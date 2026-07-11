"""On-actions (``common/on_actions``) — engine hooks firing effects.

File shape: one top-level ``on_actions = { ... }`` block whose keys are hook
names (``on_startup``, ``on_daily``, ``on_capitulation``, dynamic variants like
``on_daily_TAG``). A hook body holds repeatable ``effect = { ... }`` blocks and
optionally ``random_events = { <weight> = <event_id> }``. Files are additive:
the same hook may appear in many files (and even twice in one) — the engine
runs them all, so the tree groups entries per file.

Hook names are never hardcoded: `known_names` collects them from the vanilla
files (mods may target modded hooks / TAG variants freely).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.pdx import Block, Pair, dump_file, dumps, parse_file
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs

ON_ACTIONS_DIR = "common/on_actions"
DEFAULT_REL_FILE = f"{ON_ACTIONS_DIR}/anka_on_actions.txt"

_KEY_RE = re.compile(r"^[ \t]*([A-Za-z_][\w.]*)[ \t]*=[ \t]*\{")
# on_action tokens inside backticks in _documentation.md.
_DOC_TOKEN_RE = re.compile(r"`(on_\w+)`")
# A concrete per-country hook: base (lowercase snake_case) + trailing all-caps TAG.
_TAG_SUFFIX_RE = re.compile(r"^(on_\w+?)_([A-Z][A-Z0-9]{1,3})$")


@dataclass
class Issue:
    severity: str            # "error" | "warning"
    code: str
    subject: str
    detail: str = ""
    rel_file: str = ""


@dataclass
class OnActionDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    names: list[str] = field(default_factory=list)   # quick-scan hook names

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class OnActionEntry:
    """Thin view over one ``<name> = { ... }`` pair inside ``on_actions``."""

    def __init__(self, name: str, pair: Pair, parent: Block):
        self.name = name
        self.pair = pair
        self.parent = parent

    @property
    def block(self) -> Block:
        return self.pair.value

    # --- effect (repeatable in vanilla; we read merged, write one block) -----
    @property
    def effect_text(self) -> str:
        parts = [dumps(v, top_level=False) for v in self.block.get_all("effect")
                 if isinstance(v, Block)]
        return "\n".join(p for p in parts if p.strip())

    def set_effect_text(self, text: str) -> None:
        """Replace ALL ``effect`` blocks with one parsed block (multiple blocks
        in one hook are semantically a plain concatenation, so merging is
        loss-free); empty text removes them."""
        self._set_merged("effect", text)

    @property
    def random_events_text(self) -> str:
        v = self.block.get("random_events")
        return dumps(v, top_level=False) if isinstance(v, Block) else ""

    def set_random_events_text(self, text: str) -> None:
        self._set_merged("random_events", text)

    def _set_merged(self, key: str, text: str) -> None:
        index = next((i for i, it in enumerate(self.block.items)
                      if isinstance(it, Pair) and it.key == key), None)
        self.block.items = [it for it in self.block.items
                            if not (isinstance(it, Pair) and it.key == key)]
        if not text.strip():
            return
        parsed = pdx_parse(text, recover=False)
        pair = Pair(key, Block(parsed.items))
        if index is None:
            self.block.items.append(pair)
        else:
            self.block.items.insert(index, pair)

    @property
    def extra_keys(self) -> list[str]:
        """Keys other than effect/random_events (shown read-only, preserved)."""
        return sorted({p.key for p in self.block.pairs()
                       if p.key not in ("effect", "random_events")})

    @property
    def is_empty(self) -> bool:
        return not len(self.block.items)


class OnActionDocument:
    def __init__(self, ref: OnActionDocRef, root: Block):
        self.ref = ref
        self.root = root

    def on_actions(self, create: bool = False) -> Block | None:
        block = self.root.get_block("on_actions")
        if block is None and create:
            block = Block()
            self.root.set("on_actions", block)
        return block

    def entries(self) -> list[OnActionEntry]:
        block = self.on_actions()
        if block is None:
            return []
        return [OnActionEntry(p.key, p, block) for p in block.pairs()
                if isinstance(p.value, Block)]

    def find(self, name: str, index: int = 0) -> OnActionEntry | None:
        hits = [e for e in self.entries() if e.name == name]
        return hits[index] if index < len(hits) else None


class OnActionService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, OnActionDocument]] = {}

    # -------------------------------------------------------------------- scan
    def list_docs(self, include_vanilla: bool = True) -> list[OnActionDocRef]:
        return layered_docs(
            self.ctx, ON_ACTIONS_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[OnActionDocRef]:
        folder = root / ON_ACTIONS_DIR
        if not folder.is_dir():
            return []
        return [OnActionDocRef(rel_file=f"{ON_ACTIONS_DIR}/{file.name}",
                               source_root=root, is_vanilla=is_vanilla,
                               names=self._quick_scan(file))
                for file in sorted(folder.glob("*.txt"))]

    @staticmethod
    def _quick_scan(file: Path) -> list[str]:
        """Hook names = keys at depth 1 inside the ``on_actions`` block.
        Brace counting, not indentation (vanilla mixes tabs and spaces)."""
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        names: list[str] = []
        depth = 0
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 1:
                m = _KEY_RE.match(code)
                if m and m.group(1) != "on_actions":
                    names.append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return names

    def known_actions(self) -> list[tuple[str, bool]]:
        """Every canonical on_action as ``(base_name, supports_tag)``.

        Sources, in order of authority:

        * the game's ``common/on_actions/_documentation.md`` — it lists both the
          base (``on_monthly``) and a ``_TAG`` marker (``on_monthly_TAG``) when a
          per-country variant exists;
        * base names actually seen in vanilla files but missing from the docs.

        Concrete per-country hooks scanned from vanilla files (``on_monthly_AFG``,
        ``on_daily_GER`` …) are **folded** into their base — they only mark it as
        tag-supporting and are never listed as separate entries. ``supports_tag``
        means the picker should offer an ``<base>_TAG`` variant that prompts for a
        country tag."""
        bases: set[str] = set()
        tag_supported: set[str] = set()

        doc = self.ctx.game_path / ON_ACTIONS_DIR / "_documentation.md"
        if doc.is_file():
            try:
                text = doc.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for token in _DOC_TOKEN_RE.findall(text):
                if token.endswith("_TAG"):
                    base = token[:-4]
                    bases.add(base)
                    tag_supported.add(base)
                else:
                    bases.add(token)

        for ref in self.list_docs(include_vanilla=True):
            if not ref.is_vanilla:
                continue
            for name in ref.names:
                if name.endswith("_TAG"):
                    base = name[:-4]
                    bases.add(base)
                    tag_supported.add(base)
                    continue
                # base names are snake_case lowercase, so a trailing all-caps
                # segment is a concrete country TAG (AFG, GER, D01, …) — fold it.
                m = _TAG_SUFFIX_RE.match(name)
                if m:
                    bases.add(m.group(1))
                    tag_supported.add(m.group(1))
                else:
                    bases.add(name)

        return sorted((base, base in tag_supported) for base in bases)

    def known_names(self) -> list[str]:
        """Canonical base hook names only (no per-country ``_TAG`` variants) — used
        for validation and the 'unknown hook' check."""
        return [base for base, _ in self.known_actions()]

    # -------------------------------------------------------------------- load
    def load(self, ref: OnActionDocRef) -> OnActionDocument:
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        doc = OnActionDocument(ref, parse_file(ref.path))
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save(self, doc: OnActionDocument) -> None:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a vanilla on_actions file")
        target = ensure_filename_case(doc.ref.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        try:
            self._doc_cache[str(doc.ref.path).lower()] = \
                (target.stat().st_mtime, doc)
        except OSError:
            pass
        doc.ref.names = [e.name for e in doc.entries()]

    def copy_to_mod(self, ref: OnActionDocRef) -> OnActionDocRef:
        if not ref.is_vanilla:
            return ref
        target = self.ctx.mod.path / ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return OnActionDocRef(rel_file=ref.rel_file,
                              source_root=self.ctx.mod.path, is_vanilla=False,
                              edited=True, names=list(ref.names))

    def delete_file(self, ref: OnActionDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a vanilla file")
        if ref.path.exists():
            ref.path.unlink()
        self._doc_cache.pop(str(ref.path).lower(), None)

    # -------------------------------------------------------------------- CRUD
    def mod_target_doc(self) -> OnActionDocument:
        """First mod on_actions file, else a fresh ``anka_on_actions.txt``."""
        for ref in self.list_docs(include_vanilla=False):
            try:
                return self.load(ref)
            except Exception:
                continue
        ref = OnActionDocRef(rel_file=DEFAULT_REL_FILE,
                             source_root=self.ctx.mod.path, is_vanilla=False)
        root = Block()
        root.set("on_actions", Block())
        doc = OnActionDocument(ref, root)
        return doc

    def create_doc(self, filename: str) -> OnActionDocument:
        """A fresh mod-side on_actions file (saved on the next save_all)."""
        if not filename.endswith(".txt"):
            filename += ".txt"
        ref = OnActionDocRef(rel_file=f"{ON_ACTIONS_DIR}/{filename}",
                             source_root=self.ctx.mod.path, is_vanilla=False)
        root = Block()
        root.set("on_actions", Block())
        doc = OnActionDocument(ref, root)
        self._doc_cache[str(ref.path).lower()] = (0.0, doc)
        return doc

    def add_entry(self, doc: OnActionDocument, name: str) -> OnActionEntry:
        block = doc.on_actions(create=True)
        pair = Pair(name, Block())
        block.items.append(pair)
        return OnActionEntry(name, pair, block)

    def remove_entry(self, doc: OnActionDocument, entry: OnActionEntry) -> None:
        block = doc.on_actions()
        if block is not None:
            block.items = [it for it in block.items if it is not entry.pair]

    # -------------------------------------------------------------- validation
    def validate(self, docs: list[OnActionDocument],
                 known: set[str] | None = None) -> list[Issue]:
        issues: list[Issue] = []
        if known is None:
            known = set(self.known_names())
        for doc in docs:
            for entry in doc.entries():
                if entry.is_empty:
                    issues.append(Issue("warning", "empty_entry", entry.name,
                                        rel_file=doc.ref.rel_file))
                if entry.name in known:
                    continue
                # Dynamic variants (`on_daily_GER`) extend a known base name.
                if any(entry.name.startswith(base + "_") for base in known):
                    continue
                issues.append(Issue("warning", "unknown_name", entry.name,
                                    rel_file=doc.ref.rel_file))
        return issues
