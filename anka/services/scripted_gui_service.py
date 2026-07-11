"""Scripted GUIs (``common/scripted_guis``) — window-bound game logic.

File shape: ``scripted_gui = { <name> = { ... } }`` (several wrappers per file
are legal). Entries bind a ``.gui`` window (``window_name``) to effects,
triggers, properties and dynamic lists; the authoritative schema lives in the
game's ``_documentation.md`` and is mirrored by ``guitypes.schema``.
Validation cross-checks entries against the interface service's window index
(element names referenced by handlers must exist in the bound window).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.guitypes import SguiView
from ..core.guitypes.schema import (CONTEXT_TYPES, PARENT_WINDOW_TOKENS,
                                    SGUI_CLICK_MODIFIERS)
from ..core.pdx import Block, Pair, dump_file, parse_file
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs

SGUI_DIR = "common/scripted_guis"
DEFAULT_REL_FILE = f"{SGUI_DIR}/anka_scripted_gui.txt"

_KEY_RE = re.compile(r"^[ \t]*([A-Za-z_][\w.]*)[ \t]*=[ \t]*\{")

# effects/triggers key suffixes: <element>[_right|_alt|_control|_shift]*_click
# / _click_enabled / _visible
_HANDLER_RE = re.compile(
    r"^(?P<element>.+?)"
    r"(?P<mods>(?:_(?:" + "|".join(SGUI_CLICK_MODIFIERS) + r"))*)"
    r"_(?P<kind>click_enabled|click|visible)$")


@dataclass
class Issue:
    severity: str
    code: str
    subject: str
    detail: str = ""
    rel_file: str = ""


@dataclass
class SguiDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    names: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class SguiDocument:
    def __init__(self, ref: SguiDocRef, root: Block):
        self.ref = ref
        self.root = root

    def wrappers(self, create: bool = False) -> list[Block]:
        out = [v for v in self.root.get_all_ci("scripted_gui")
               if isinstance(v, Block)]
        if not out and create:
            block = Block()
            self.root.add("scripted_gui", block)
            out = [block]
        return out

    def entries(self) -> list[SguiView]:
        out: list[SguiView] = []
        for wrapper in self.wrappers():
            for pair in wrapper.pairs():
                if isinstance(pair.value, Block):
                    out.append(SguiView(pair, wrapper))
        return out


def parse_handler_key(key: str) -> tuple[str, list[str], str] | None:
    """``my_btn_shift_right_click`` → (element, [modifiers], kind)."""
    m = _HANDLER_RE.match(key)
    if m is None:
        return None
    mods = [p for p in m.group("mods").split("_") if p]
    return m.group("element"), mods, m.group("kind")


def build_handler_key(element: str, modifiers: list[str], kind: str) -> str:
    mods = "".join(f"_{m}" for m in SGUI_CLICK_MODIFIERS if m in modifiers)
    return f"{element}{mods}_{kind}"


class ScriptedGuiService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, SguiDocument]] = {}

    # -------------------------------------------------------------------- scan
    def list_docs(self, include_vanilla: bool = True) -> list[SguiDocRef]:
        return layered_docs(
            self.ctx, SGUI_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[SguiDocRef]:
        folder = root / SGUI_DIR
        if not folder.is_dir():
            return []
        return [SguiDocRef(rel_file=f"{SGUI_DIR}/{file.name}",
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
        in_wrapper = False
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 0:
                m = _KEY_RE.match(code)
                in_wrapper = bool(m and m.group(1).lower() == "scripted_gui")
            elif depth == 1 and in_wrapper:
                m = _KEY_RE.match(code)
                if m:
                    names.append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return names

    # -------------------------------------------------------------------- load
    def load(self, ref: SguiDocRef) -> SguiDocument:
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        doc = SguiDocument(ref, parse_file(ref.path))
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save(self, doc: SguiDocument) -> None:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a vanilla scripted_gui")
        target = ensure_filename_case(doc.ref.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        try:
            self._doc_cache[str(doc.ref.path).lower()] = \
                (target.stat().st_mtime, doc)
        except OSError:
            pass
        doc.ref.names = [e.name for e in doc.entries()]

    def copy_to_mod(self, ref: SguiDocRef) -> SguiDocRef:
        if not ref.is_vanilla:
            return ref
        target = self.ctx.mod.path / ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return SguiDocRef(rel_file=ref.rel_file,
                          source_root=self.ctx.mod.path, is_vanilla=False,
                          edited=True, names=list(ref.names))

    def delete_file(self, ref: SguiDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a vanilla file")
        if ref.path.exists():
            ref.path.unlink()
        self._doc_cache.pop(str(ref.path).lower(), None)

    # -------------------------------------------------------------------- CRUD
    def mod_target_doc(self) -> SguiDocument:
        for ref in self.list_docs(include_vanilla=False):
            try:
                return self.load(ref)
            except Exception:
                continue
        ref = SguiDocRef(rel_file=DEFAULT_REL_FILE,
                         source_root=self.ctx.mod.path, is_vanilla=False)
        root = Block()
        root.add("scripted_gui", Block())
        return SguiDocument(ref, root)

    def add_entry(self, doc: SguiDocument, name: str) -> SguiView:
        wrapper = doc.wrappers(create=True)[0]
        block = Block()
        block.add("context_type", "player_context")
        pair = Pair(name, block)
        wrapper.items.append(pair)
        return SguiView(pair, wrapper)

    def remove_entry(self, doc: SguiDocument, entry: SguiView) -> None:
        entry.parent.items = [it for it in entry.parent.items
                              if it is not entry.pair]

    # -------------------------------------------------------------- validation
    def validate(self, docs: list[SguiDocument],
                 window_index: dict) -> list[Issue]:
        issues: list[Issue] = []
        for doc in docs:
            rel = doc.ref.rel_file
            for entry in doc.entries():
                self._validate_entry(entry, rel, window_index, issues)
        return issues

    def _validate_entry(self, entry: SguiView, rel: str, window_index: dict,
                        issues: list[Issue]) -> None:
        name = entry.name
        context = entry.get_attr("context_type")
        if context and context not in CONTEXT_TYPES:
            issues.append(Issue("error", "bad_context", name,
                                detail=context, rel_file=rel))
        token = entry.get_attr("parent_window_token")
        if token and token not in PARENT_WINDOW_TOKENS:
            issues.append(Issue("warning", "bad_token", name,
                                detail=token, rel_file=rel))
        window_name = entry.get_attr("window_name")
        if not window_name:
            issues.append(Issue("warning", "no_window", name, rel_file=rel))
            return
        info = window_index.get(window_name)
        if info is None:
            issues.append(Issue("error", "window_not_found", name,
                                detail=window_name, rel_file=rel))
            return
        elements = set(info.elements) | {window_name}

        def check_refs(block_key: str, element_from_key) -> None:
            block = entry.get_attr_block(block_key)
            if block is None:
                return
            for pair in block.pairs():
                element = element_from_key(pair.key)
                if element is None:
                    issues.append(Issue("warning", "bad_handler_key",
                                        name, detail=pair.key, rel_file=rel))
                elif element not in elements:
                    issues.append(Issue(
                        "warning", "element_not_found", name,
                        detail=f"{block_key}: {pair.key} → {element}",
                        rel_file=rel))

        check_refs("effects",
                   lambda k: (parse_handler_key(k) or (None,))[0])
        check_refs("triggers",
                   lambda k: (parse_handler_key(k) or (None,))[0])
        check_refs("properties", lambda k: k)
        check_refs("dynamic_lists", lambda k: k)
