"""Scripted localisation (``common/scripted_localisation``) — dynamic loc keys.

File shape: a flat sequence of ``defined_text = { name = <id> text = { ... } }``
blocks (see the game's ``00_scripted_localisation.txt``). Each ``defined_text``
binds a *name* (referenced from localisation values as ``[name]``) to an ordered
list of ``text`` alternatives; the first whose ``trigger`` passes supplies the
``localization_key`` that is printed (``random_list`` picks weighted at random).

Unlike scripted GUIs, entries are top-level pairs (no wrapper object). The service
mirrors ``ScriptedGuiService``: layered listing across base game → dependency mods →
edited mod, cheap name scans for the tree, collision-safe mod-side writes, and
one-click copy-to-mod for vanilla files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs

SLOC_DIR = "common/scripted_localisation"
DEFAULT_REL_FILE = f"{SLOC_DIR}/anka_scripted_localisation.txt"

_DEFINED_RE = re.compile(r"^[ \t]*defined_text[ \t]*=[ \t]*\{", re.IGNORECASE)
_NAME_RE = re.compile(r"^[ \t]*name[ \t]*=[ \t]*\"?([\w.]+)", re.IGNORECASE)


@dataclass
class SlocDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    names: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class DefinedTextView:
    """A single ``defined_text`` entry: its `Pair` inside the document root."""

    def __init__(self, pair: Pair, parent: Block):
        self.pair = pair
        self.parent = parent

    @property
    def block(self) -> Block:
        return self.pair.value if isinstance(self.pair.value, Block) else Block()

    @property
    def name(self) -> str:
        return (self.block.get_scalar_ci("name") or "").strip('"')

    def set_name(self, name: str) -> None:
        self.block.set_ci("name", Scalar(name))

    def loc_keys(self) -> list[str]:
        """Every ``localization_key`` referenced anywhere inside the entry
        (across all ``text`` / ``random_list`` alternatives), de-duplicated in
        first-seen order."""
        out: list[str] = []
        seen: set[str] = set()

        def walk(block: Block) -> None:
            for pair in block.pairs():
                if (pair.key.lower() == "localization_key"
                        and isinstance(pair.value, Scalar)):
                    key = pair.value.raw
                    if key not in seen:
                        seen.add(key)
                        out.append(key)
                elif isinstance(pair.value, Block):
                    walk(pair.value)

        walk(self.block)
        return out


class SlocDocument:
    def __init__(self, ref: SlocDocRef, root: Block):
        self.ref = ref
        self.root = root

    def entries(self) -> list[DefinedTextView]:
        return [DefinedTextView(pair, self.root)
                for pair in self.root.pairs()
                if pair.key.lower() == "defined_text"
                and isinstance(pair.value, Block)]


class ScriptedLocService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, SlocDocument]] = {}

    # -------------------------------------------------------------------- scan
    def list_docs(self, include_vanilla: bool = True) -> list[SlocDocRef]:
        return layered_docs(
            self.ctx, SLOC_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[SlocDocRef]:
        folder = root / SLOC_DIR
        if not folder.is_dir():
            return []
        return [SlocDocRef(rel_file=f"{SLOC_DIR}/{file.name}",
                           source_root=root, is_vanilla=is_vanilla,
                           names=self._quick_scan(file))
                for file in sorted(folder.glob("*.txt"))]

    @staticmethod
    def _quick_scan(file: Path) -> list[str]:
        """`name` of every top-level ``defined_text`` — a cheap line scan that
        avoids parsing large vanilla files just to populate the tree."""
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        names: list[str] = []
        depth = 0
        in_defined = False
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 0 and _DEFINED_RE.match(code):
                in_defined = True
            elif depth == 1 and in_defined:
                m = _NAME_RE.match(code)
                if m:
                    names.append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth <= 0:
                depth = 0
                in_defined = False
        return names

    # -------------------------------------------------------------------- load
    def load(self, ref: SlocDocRef) -> SlocDocument:
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        doc = SlocDocument(ref, parse_file(ref.path))
        self._doc_cache[key] = (mtime, doc)
        return doc

    def all_names(self, include_vanilla: bool = True) -> list[str]:
        """Sorted, de-duplicated ``defined_text`` names across every layer —
        the picker source for the interface "SL" button."""
        seen: set[str] = set()
        out: list[str] = []
        for ref in self.list_docs(include_vanilla=include_vanilla):
            for name in ref.names:
                if name and name not in seen:
                    seen.add(name)
                    out.append(name)
        out.sort(key=str.lower)
        return out

    def save(self, doc: SlocDocument) -> None:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a vanilla scripted_localisation")
        target = ensure_filename_case(doc.ref.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        try:
            self._doc_cache[str(doc.ref.path).lower()] = \
                (target.stat().st_mtime, doc)
        except OSError:
            pass
        doc.ref.names = [e.name for e in doc.entries()]

    def copy_to_mod(self, ref: SlocDocRef) -> SlocDocRef:
        if not ref.is_vanilla:
            return ref
        target = self.ctx.mod.path / ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return SlocDocRef(rel_file=ref.rel_file,
                          source_root=self.ctx.mod.path, is_vanilla=False,
                          edited=True, names=list(ref.names))

    def create_doc(self, rel_file: str) -> SlocDocRef:
        """Create an empty mod-side file (idempotent) and return its ref."""
        if not rel_file.startswith(SLOC_DIR):
            rel_file = f"{SLOC_DIR}/{rel_file}"
        if not rel_file.endswith(".txt"):
            rel_file += ".txt"
        target = ensure_filename_case(self.ctx.mod.path / rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            dump_file(Block(), target)
        return SlocDocRef(rel_file=rel_file, source_root=self.ctx.mod.path,
                          is_vanilla=False, edited=True)

    def delete_file(self, ref: SlocDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a vanilla file")
        if ref.path.exists():
            ref.path.unlink()
        self._doc_cache.pop(str(ref.path).lower(), None)

    # -------------------------------------------------------------------- CRUD
    def mod_target_doc(self) -> SlocDocument:
        for ref in self.list_docs(include_vanilla=False):
            try:
                return self.load(ref)
            except Exception:
                continue
        ref = SlocDocRef(rel_file=DEFAULT_REL_FILE,
                         source_root=self.ctx.mod.path, is_vanilla=False)
        return SlocDocument(ref, Block())

    def add_entry(self, doc: SlocDocument, name: str) -> DefinedTextView:
        block = Block()
        block.add("name", Scalar(name))
        text = Block()
        text.add("localization_key", Scalar(f"{name}_text"))
        block.add("text", text)
        pair = Pair("defined_text", block)
        doc.root.items.append(pair)
        return DefinedTextView(pair, doc.root)

    def remove_entry(self, doc: SlocDocument, entry: DefinedTextView) -> None:
        doc.root.items = [it for it in doc.root.items if it is not entry.pair]

    def replace_entry(self, doc: SlocDocument, entry: DefinedTextView,
                      new_pair: Pair) -> DefinedTextView:
        """Swap one entry's `defined_text` pair in place (used by the inline
        script editor when the raw text is re-parsed)."""
        for i, item in enumerate(doc.root.items):
            if item is entry.pair:
                doc.root.items[i] = new_pair
                return DefinedTextView(new_pair, doc.root)
        doc.root.items.append(new_pair)
        return DefinedTextView(new_pair, doc.root)
