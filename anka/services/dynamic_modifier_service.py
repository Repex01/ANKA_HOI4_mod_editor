"""Dynamic modifiers (``common/dynamic_modifiers``) — scriptable modifier sets.

File shape: top-level ``<name> = { ... }`` entries. Special keys: ``icon``
(a GFX sprite), ``enable`` / ``remove_trigger`` (trigger blocks),
``attacker_modifier`` (flag). Every other pair is a static modifier whose
value is a number or a variable name (resolved at runtime by the engine).
They are attached in script via ``add_dynamic_modifier``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs

DYNMOD_DIR = GAME_DIRS.DYNAMIC_MODIFIERS
DEFAULT_REL_FILE = f"{DYNMOD_DIR}/anka_dynamic_modifiers.txt"

_KEY_RE = re.compile(r"^([A-Za-z_][\w.]*)[ \t]*=[ \t]*\{")

# Keys of an entry that are NOT modifiers.
SPECIAL_KEYS = ("icon", "enable", "remove_trigger", "attacker_modifier")


@dataclass
class Issue:
    severity: str            # "error" | "warning"
    code: str
    subject: str
    detail: str = ""
    rel_file: str = ""


@dataclass
class DynModDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    names: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class DynamicModifier:
    """View over one ``<name> = { ... }`` pair."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def name(self) -> str:
        return self.pair.key

    @property
    def block(self) -> Block:
        return self.pair.value

    # --- special fields ------------------------------------------------------
    @property
    def icon(self) -> str:
        return (self.block.get_scalar("icon") or "").strip('"')

    def set_icon(self, sprite: str) -> None:
        sprite = sprite.strip()
        if sprite:
            self.block.set("icon", Scalar(sprite))
        else:
            self.block.remove("icon")

    @property
    def attacker_modifier(self) -> bool:
        v = self.block.get_scalar("attacker_modifier")
        return (v or "").strip('"').lower() == "yes"

    def set_attacker_modifier(self, value: bool) -> None:
        if value:
            self.block.set("attacker_modifier", "yes")
        else:
            self.block.remove("attacker_modifier")

    def get_script(self, key: str) -> str:
        from ..core.pdx import dumps
        v = self.block.get(key)
        return dumps(v, top_level=False) if isinstance(v, Block) else ""

    def set_script(self, key: str, text: str) -> None:
        from ..core.pdx import parse as pdx_parse
        if not text.strip():
            self.block.remove(key)
            return
        parsed = pdx_parse(text, recover=False)
        self.block.set(key, Block(parsed.items))

    # --- modifiers -----------------------------------------------------------
    def modifiers(self) -> list[Pair]:
        """Modifier pairs in file order (everything except the special keys)."""
        return [p for p in self.block.pairs()
                if p.key.lower() not in SPECIAL_KEYS
                and isinstance(p.value, Scalar)]

    def add_modifier(self, key: str, value: str = "0") -> Pair:
        pair = Pair(key, Scalar(value))
        self.block.items.append(pair)
        return pair

    def remove_modifier(self, pair: Pair) -> None:
        self.block.items = [it for it in self.block.items if it is not pair]

    @property
    def is_empty(self) -> bool:
        return not len(self.block.items)


class DynModDocument:
    def __init__(self, ref: DynModDocRef, root: Block):
        self.ref = ref
        self.root = root

    def entries(self) -> list[DynamicModifier]:
        return [DynamicModifier(p) for p in self.root.pairs()
                if isinstance(p.value, Block)]

    def find(self, name: str) -> DynamicModifier | None:
        return next((e for e in self.entries() if e.name == name), None)


class DynamicModifierService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, DynModDocument]] = {}

    @classmethod
    def options(cls, context: ModContext) -> list[tuple[str, str]]:
        """(display, value) choices of every known dynamic modifier (mod +
        dependencies + vanilla) — used by the script editors' pickers."""
        seen: dict[str, str] = {}
        for ref in cls(context).list_docs(include_vanilla=True):
            for name in ref.names:
                seen.setdefault(name, ref.rel_file.rsplit("/", 1)[-1])
        return [(f"{name} · {src}", name)
                for name, src in sorted(seen.items())]

    # -------------------------------------------------------------------- scan
    def list_docs(self, include_vanilla: bool = True) -> list[DynModDocRef]:
        return layered_docs(
            self.ctx, DYNMOD_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[DynModDocRef]:
        folder = root / DYNMOD_DIR
        if not folder.is_dir():
            return []
        return [DynModDocRef(rel_file=f"{DYNMOD_DIR}/{file.name}",
                             source_root=root, is_vanilla=is_vanilla,
                             names=self._quick_scan(file))
                for file in sorted(folder.glob("*.txt"))]

    @staticmethod
    def _quick_scan(file: Path) -> list[str]:
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        names: list[str] = []
        depth = 0
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 0:
                m = _KEY_RE.match(code)
                if m:
                    names.append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return names

    # -------------------------------------------------------------------- load
    def load(self, ref: DynModDocRef) -> DynModDocument:
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        doc = DynModDocument(ref, parse_file(ref.path))
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save(self, doc: DynModDocument) -> None:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a vanilla file")
        target = ensure_filename_case(doc.ref.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        try:
            self._doc_cache[str(doc.ref.path).lower()] = \
                (target.stat().st_mtime, doc)
        except OSError:
            pass
        doc.ref.names = [e.name for e in doc.entries()]

    def copy_to_mod(self, ref: DynModDocRef) -> DynModDocRef:
        if not ref.is_vanilla:
            return ref
        target = self.ctx.mod.path / ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return DynModDocRef(rel_file=ref.rel_file,
                            source_root=self.ctx.mod.path, is_vanilla=False,
                            edited=True, names=list(ref.names))

    def delete_file(self, ref: DynModDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a vanilla file")
        if ref.path.exists():
            ref.path.unlink()
        self._doc_cache.pop(str(ref.path).lower(), None)

    # -------------------------------------------------------------------- CRUD
    def create_doc(self, filename: str) -> DynModDocument:
        if not filename.endswith(".txt"):
            filename += ".txt"
        ref = DynModDocRef(rel_file=f"{DYNMOD_DIR}/{filename}",
                           source_root=self.ctx.mod.path, is_vanilla=False)
        doc = DynModDocument(ref, Block())
        self._doc_cache[str(ref.path).lower()] = (0.0, doc)
        return doc

    def mod_target_doc(self) -> DynModDocument:
        for ref in self.list_docs(include_vanilla=False):
            try:
                return self.load(ref)
            except Exception:
                continue
        ref = DynModDocRef(rel_file=DEFAULT_REL_FILE,
                           source_root=self.ctx.mod.path, is_vanilla=False)
        return DynModDocument(ref, Block())

    def add_entry(self, doc: DynModDocument, name: str) -> DynamicModifier:
        pair = Pair(name, Block())
        doc.root.items.append(pair)
        return DynamicModifier(pair)

    def remove_entry(self, doc: DynModDocument, entry: DynamicModifier) -> None:
        doc.root.items = [it for it in doc.root.items if it is not entry.pair]

    # -------------------------------------------------------------- validation
    def validate(self, docs: list[DynModDocument],
                 known: dict | set) -> list[Issue]:
        """`known` = the modifier catalog names (supplied by the editor layer
        so the service stays UI-free)."""
        issues: list[Issue] = []
        for doc in docs:
            rel = doc.ref.rel_file
            for entry in doc.entries():
                if entry.is_empty:
                    issues.append(Issue("warning", "empty_entry", entry.name,
                                        rel_file=rel))
                for pair in entry.modifiers():
                    if pair.key not in known \
                            and pair.key != "custom_modifier_tooltip":
                        issues.append(Issue("warning", "unknown_modifier",
                                            entry.name, detail=pair.key,
                                            rel_file=rel))
        return issues
