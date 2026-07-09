"""Read/write division name groups — ``common/units/names_divisions/*.txt``.

Each file holds several *name groups* (``GER_armored = { ... }``). A group ties a set
of division templates (``division_types``) for some countries (``for_countries``) to
the names their divisions get:

* ``fallback_name = "%d. Panzer-Division"`` — pattern for un-named divisions (``%d`` is
  the division's number);
* ``unordered = { "..." }`` — a pool of names assigned in order to un-numbered divisions;
* ``ordered = { 1 = { "1. Panzer-Division" } 2 = { ... } }`` — a *specific* name per
  division number. This is the "ordered division name" mechanism: an OOB division with
  ``division_name = { is_name_ordered = yes name_order = 1 }`` takes entry ``1`` here.

Block-backed like the other services: unknown keys (``can_use`` triggers, …) survive
load→save untouched; edits go to the mod (copy-on-write for base/vanilla files).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs
from ._pdxview import BlockView

NAMES_DIR = "common/units/names_divisions"
_NEW_FILE = "anka_names_divisions.txt"
_GROUP_OPEN = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*\{")


def _string_array(block: Block | None) -> list[str]:
    return [s.raw for s in block.items if isinstance(s, Scalar)] if block else []


class NameGroup(BlockView):
    """A view over one ``<key> = { ... }`` name group."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def key(self) -> str:
        return self.pair.key

    @key.setter
    def key(self, value: str) -> None:
        self.pair.key = value

    @property
    def name(self) -> str:
        v = self.block.get("name")
        return v.raw if isinstance(v, Scalar) else ""

    @name.setter
    def name(self, value: str) -> None:
        if value:
            self.block.set("name", Scalar(value, quoted=True))
        else:
            self.block.remove("name")

    @property
    def fallback_name(self) -> str:
        v = self.block.get("fallback_name")
        return v.raw if isinstance(v, Scalar) else ""

    @fallback_name.setter
    def fallback_name(self, value: str) -> None:
        if value:
            self.block.set("fallback_name", Scalar(value, quoted=True))
        else:
            self.block.remove("fallback_name")

    @property
    def for_countries(self) -> list[str]:
        return _string_array(self.block.get_block("for_countries"))

    def set_for_countries(self, tags: list[str]) -> None:
        self.block.set("for_countries",
                       Block([Scalar(t) for t in tags]))

    @property
    def division_types(self) -> list[str]:
        return _string_array(self.block.get_block("division_types"))

    def set_division_types(self, types: list[str]) -> None:
        self.block.set("division_types",
                       Block([Scalar(t, quoted=True) for t in types]))

    @property
    def unordered(self) -> list[str]:
        return _string_array(self.block.get_block("unordered"))

    def set_unordered(self, names: list[str]) -> None:
        if names:
            self.block.set("unordered", Block([Scalar(n, quoted=True) for n in names]))
        else:
            self.block.remove("unordered")

    @property
    def ordered(self) -> dict[int, str]:
        """number -> name. Each ``N = { "Name" ... }`` entry: we edit the first name."""
        block = self.block.get_block("ordered")
        out: dict[int, str] = {}
        if block is None:
            return out
        for p in block.pairs():
            if p.key.isdigit() and isinstance(p.value, Block):
                names = _string_array(p.value)
                if names:
                    out[int(p.key)] = names[0]
        return out

    def set_ordered(self, mapping: dict[int, str]) -> None:
        """Replace the ordered table, preserving any extra names already stored for a
        number (only the primary/first name is edited here)."""
        old = self.block.get_block("ordered")
        extras: dict[int, list[str]] = {}
        if old is not None:
            for p in old.pairs():
                if p.key.isdigit() and isinstance(p.value, Block):
                    extras[int(p.key)] = _string_array(p.value)[1:]
        block = Block()
        for num in sorted(mapping):
            names = [mapping[num], *extras.get(num, [])]
            block.add(str(num), Block([Scalar(n, quoted=True) for n in names]))
        if mapping:
            self.block.set("ordered", block)
        else:
            self.block.remove("ordered")

    def __repr__(self) -> str:
        return f"NameGroup({self.key!r})"


@dataclass
class NamesDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    groups: list[str] = field(default_factory=list)   # quick-scan group keys

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class NamesDocument:
    ref: NamesDocRef
    root: Block

    def groups(self) -> list[NameGroup]:
        return [NameGroup(p) for p in self.root.pairs()
                if isinstance(p.value, Block)]

    def find_group(self, key: str) -> NameGroup | None:
        return next((g for g in self.groups() if g.key == key), None)


class DivisionNamesService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, NamesDocument]] = {}

    # --- listing -----------------------------------------------------------
    def list_docs(self, include_vanilla: bool = True) -> list[NamesDocRef]:
        return layered_docs(
            self.ctx, NAMES_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[NamesDocRef]:
        folder = root / NAMES_DIR
        if not folder.is_dir():
            return []
        refs: list[NamesDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            if not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            refs.append(NamesDocRef(
                rel_file=f"{NAMES_DIR}/{file.name}", source_root=root,
                is_vanilla=is_vanilla, groups=self._quick_scan(text)))
        return refs

    @staticmethod
    def _quick_scan(text: str) -> list[str]:
        keys: list[str] = []
        depth = 0
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 0:
                m = _GROUP_OPEN.match(code)
                if m:
                    keys.append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return keys

    # --- load / save -------------------------------------------------------
    def load(self, ref: NamesDocRef) -> NamesDocument:
        path = ref.path
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(path)
        if cached and cached[0] == mtime:
            doc = cached[1]
            doc.ref = ref
            return doc
        doc = NamesDocument(ref=ref, root=parse_file(path))
        self._doc_cache[path] = (mtime, doc)
        return doc

    def save(self, doc: NamesDocument) -> Path:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a base-game / dependency file")
        path = ensure_filename_case(doc.ref.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, path)
        try:
            self._doc_cache[path] = (path.stat().st_mtime, doc)
        except OSError:
            pass
        return path

    def copy_to_mod(self, ref: NamesDocRef) -> NamesDocRef:
        """Copy a base/vanilla names file into the mod (override by relative path)."""
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return NamesDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                           is_vanilla=False, edited=True, groups=list(ref.groups))

    def new_document(self, filename: str = _NEW_FILE) -> NamesDocument:
        if not filename.endswith(".txt"):
            filename += ".txt"
        rel = f"{NAMES_DIR}/{filename}"
        ref = NamesDocRef(rel_file=rel, source_root=self.ctx.mod.path, is_vanilla=False)
        if ref.path.exists():
            return self.load(ref)
        return NamesDocument(ref=ref, root=Block())

    def add_group(self, doc: NamesDocument, key: str, *, for_country: str = "",
                  division_types: list[str] | None = None) -> NameGroup:
        block = Block()
        block.set("name", Scalar(key, quoted=True))
        block.set("for_countries", Block([Scalar(for_country)] if for_country else []))
        block.set("division_types",
                  Block([Scalar(t, quoted=True) for t in (division_types or [])]))
        block.set("can_use", Block([Pair("always", Scalar("yes"))]))
        block.set("fallback_name", Scalar("%d. Division", quoted=True))
        block.set("ordered", Block())
        pair = Pair(key, block)
        doc.root.items.append(pair)
        return NameGroup(pair)

    def remove_group(self, doc: NamesDocument, group: NameGroup) -> None:
        doc.root.items = [it for it in doc.root.items if it is not group.pair]
