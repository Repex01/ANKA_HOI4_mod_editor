"""Read/write national focus trees for the Focuses editor.

Sources (mod first, base game as fallback): ``common/national_focus/*.txt``. A file may
carry a ``focus_tree`` and/or top-level ``shared_focus`` / ``joint_focus`` definitions.
The model is *block-backed*: `Focus` / `FocusTree` are thin views over the parsed PDX
`Block`, so every key ANKA does not know about (``shortcut``, ``inlay_window``, script
variables, comments-adjacent structure) survives a load/save round-trip untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS, HOI4_LANGUAGES
from ..core.localisation import LocFile
from ..core.pdx import Block, Pair, Scalar, dump_file, dumps, parse_file
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs

# Focus flags that are tri-state in script: absent = game default.
FOCUS_FLAG_DEFAULTS = {
    "available_if_capitulated": False,
    "cancel_if_invalid": True,
    "continue_if_invalid": False,
    "cancelable": True,
    "dynamic": False,
}

# Free-form script blocks edited as validated PDX text.
FOCUS_SCRIPT_FIELDS = (
    "available", "bypass", "cancel", "allow_branch",
    "select_effect", "completion_reward", "complete_tooltip", "ai_will_do",
)

DEFAULT_FOCUS_COST = 10.0
DAYS_PER_COST = 7

_ID_RE = re.compile(r"^\w+$")


# ---------------------------------------------------------------------------
# block-backed model
# ---------------------------------------------------------------------------

class Focus:
    """A view over one ``focus`` / ``shared_focus`` / ``joint_focus`` block."""

    def __init__(self, block: Block, kind: str = "focus"):
        self.block = block
        self.kind = kind                  # "focus" | "shared_focus" | "joint_focus"

    # --- identity ---------------------------------------------------------
    @property
    def id(self) -> str:
        return self.block.get_scalar("id", "") or ""

    @id.setter
    def id(self, value: str) -> None:
        self.block.set("id", value)

    # --- icon ---------------------------------------------------------------
    # `icon` is either a scalar sprite name or (post-NSB) a block with
    # ``value = GFX_x`` + optional trigger; several icon pairs may coexist.
    @property
    def icon(self) -> str:
        v = self.block.get("icon")
        if isinstance(v, Scalar):
            return v.raw
        if isinstance(v, Block):
            return v.get_scalar("value", "") or ""
        return ""

    @icon.setter
    def icon(self, sprite: str) -> None:
        v = self.block.get("icon")
        if isinstance(v, Block):
            v.set("value", sprite)
        else:
            self.block.set("icon", sprite)

    @property
    def has_conditional_icons(self) -> bool:
        icons = self.block.get_all("icon")
        return len(icons) > 1 or any(isinstance(i, Block) for i in icons)

    # --- layout -------------------------------------------------------------
    @property
    def x(self) -> int:
        v = self.block.get("x")
        return v.as_int(0) if isinstance(v, Scalar) else 0

    @x.setter
    def x(self, value: int) -> None:
        self.block.set("x", int(value))

    @property
    def y(self) -> int:
        v = self.block.get("y")
        return v.as_int(0) if isinstance(v, Scalar) else 0

    @y.setter
    def y(self, value: int) -> None:
        self.block.set("y", int(value))

    @property
    def relative_position_id(self) -> str | None:
        return self.block.get_scalar("relative_position_id")

    @relative_position_id.setter
    def relative_position_id(self, value: str | None) -> None:
        if value:
            self.block.set("relative_position_id", value)
        else:
            self.block.remove("relative_position_id")

    # --- basic fields ---------------------------------------------------------
    @property
    def cost(self) -> float:
        v = self.block.get("cost")
        return (v.as_float(DEFAULT_FOCUS_COST) if isinstance(v, Scalar)
                else DEFAULT_FOCUS_COST)

    @cost.setter
    def cost(self, value: float) -> None:
        value = round(float(value), 2)
        self.block.set("cost", int(value) if value == int(value) else value)

    @property
    def text(self) -> str | None:
        return self.block.get_scalar("text")

    @text.setter
    def text(self, value: str | None) -> None:
        if value:
            self.block.set("text", value)
        else:
            self.block.remove("text")

    @property
    def will_lead_to_war_with(self) -> str | None:
        return self.block.get_scalar("will_lead_to_war_with")

    @will_lead_to_war_with.setter
    def will_lead_to_war_with(self, value: str | None) -> None:
        if value:
            self.block.set("will_lead_to_war_with", value)
        else:
            self.block.remove("will_lead_to_war_with")

    # --- tri-state flags --------------------------------------------------
    def get_flag(self, name: str) -> bool:
        v = self.block.get(name)
        if isinstance(v, Scalar):
            return v.as_bool()
        return FOCUS_FLAG_DEFAULTS.get(name, False)

    def set_flag(self, name: str, value: bool) -> None:
        """Write the flag only when it differs from the game default."""
        if value == FOCUS_FLAG_DEFAULTS.get(name, False):
            self.block.remove(name)
        else:
            self.block.set(name, bool(value))

    # --- links --------------------------------------------------------------
    @property
    def prerequisites(self) -> list[list[str]]:
        """AND-list of OR-groups: each ``prerequisite`` block is one OR group."""
        groups: list[list[str]] = []
        for blk in self.block.get_all("prerequisite"):
            if isinstance(blk, Block):
                ids = [v.raw for v in blk.get_all("focus") if isinstance(v, Scalar)]
                if ids:
                    groups.append(ids)
        return groups

    @prerequisites.setter
    def prerequisites(self, groups: list[list[str]]) -> None:
        blocks = []
        for group in groups:
            blk = Block()
            for fid in group:
                blk.add("focus", fid)
            if len(blk):
                blocks.append(blk)
        self._replace_all("prerequisite", blocks)

    @property
    def mutually_exclusive(self) -> list[str]:
        ids: list[str] = []
        for blk in self.block.get_all("mutually_exclusive"):
            if isinstance(blk, Block):
                ids.extend(v.raw for v in blk.get_all("focus") if isinstance(v, Scalar))
        return ids

    @mutually_exclusive.setter
    def mutually_exclusive(self, ids: list[str]) -> None:
        if ids:
            blk = Block()
            for fid in ids:
                blk.add("focus", fid)
            self._replace_all("mutually_exclusive", [blk])
        else:
            self._replace_all("mutually_exclusive", [])

    @property
    def search_filters(self) -> list[str]:
        blk = self.block.get_block("search_filters")
        return blk.array_values() if blk is not None else []

    @search_filters.setter
    def search_filters(self, values: list[str]) -> None:
        if values:
            blk = Block()
            for v in values:
                blk.add_value(v)
            self._replace_all("search_filters", [blk])
        else:
            self.block.remove("search_filters")

    # --- script blocks as validated text ------------------------------------
    def get_script(self, key: str) -> str:
        v = self.block.get(key)
        if isinstance(v, Block):
            return dumps(v, top_level=False)
        if isinstance(v, Scalar):
            return v.raw
        return ""

    def set_script(self, key: str, text: str) -> None:
        """Parse `text` strictly and store it under `key`; raises on bad script.
        Empty text removes the key entirely."""
        if not text.strip():
            self.block.remove(key)
            return
        parsed = pdx_parse(text, recover=False)
        if len(parsed.items) == 1 and isinstance(parsed.items[0], Scalar):
            self.block.set(key, parsed.items[0])       # e.g. ``allow_branch = yes``
        else:
            self.block.set(key, Block(parsed.items))

    # --- helpers ------------------------------------------------------------
    def _replace_all(self, key: str, blocks: list[Block]) -> None:
        """Replace every ``key = {...}`` pair with `blocks`, keeping list position."""
        index = next((i for i, it in enumerate(self.block.items)
                      if isinstance(it, Pair) and it.key == key), None)
        self.block.items = [it for it in self.block.items
                            if not (isinstance(it, Pair) and it.key == key)]
        pairs = [Pair(key, blk) for blk in blocks]
        if index is None:
            self.block.items.extend(pairs)
        else:
            self.block.items[index:index] = pairs

    def references(self, fid: str) -> bool:
        """Does this focus link to `fid` via prerequisite/mutex/relative position?"""
        return (fid == self.relative_position_id
                or any(fid in g for g in self.prerequisites)
                or fid in self.mutually_exclusive)

    def replace_reference(self, old: str, new: str) -> None:
        if self.relative_position_id == old:
            self.relative_position_id = new
        groups = self.prerequisites
        if any(old in g for g in groups):
            self.prerequisites = [[new if f == old else f for f in g] for g in groups]
        mutex = self.mutually_exclusive
        if old in mutex:
            self.mutually_exclusive = [new if f == old else f for f in mutex]

    def drop_reference(self, fid: str, position: tuple[int, int] | None = None) -> None:
        """Remove every link to `fid`. If this focus was positioned relative to it,
        `position` (its resolved absolute cell) keeps it in place."""
        groups = [[f for f in g if f != fid] for g in self.prerequisites]
        self.prerequisites = [g for g in groups if g]
        mutex = [f for f in self.mutually_exclusive if f != fid]
        self.mutually_exclusive = mutex
        if self.relative_position_id == fid:
            self.relative_position_id = None
            if position is not None:
                self.x, self.y = position

    def __repr__(self) -> str:
        return f"Focus({self.id!r}, {self.kind})"


class FocusTree:
    """A view over the ``focus_tree = { ... }`` block."""

    def __init__(self, block: Block):
        self.block = block

    @property
    def id(self) -> str:
        return self.block.get_scalar("id", "") or ""

    @id.setter
    def id(self, value: str) -> None:
        self.block.set("id", value)

    @property
    def default(self) -> bool:
        v = self.block.get("default")
        return v.as_bool() if isinstance(v, Scalar) else False

    @default.setter
    def default(self, value: bool) -> None:
        self.block.set("default", bool(value))

    @property
    def reset_on_civilwar(self) -> bool:
        v = self.block.get("reset_on_civilwar")
        return v.as_bool() if isinstance(v, Scalar) else False

    @reset_on_civilwar.setter
    def reset_on_civilwar(self, value: bool) -> None:
        self.block.set("reset_on_civilwar", bool(value))

    def _get_xy(self, key: str) -> tuple[int, int] | None:
        blk = self.block.get_block(key)
        if blk is None:
            return None
        x = blk.get("x")
        y = blk.get("y")
        return (x.as_int(0) if isinstance(x, Scalar) else 0,
                y.as_int(0) if isinstance(y, Scalar) else 0)

    def _set_xy(self, key: str, value: tuple[int, int] | None) -> None:
        if value is None:
            self.block.remove(key)
            return
        blk = self.block.get_block(key)
        if blk is None:
            blk = Block()
            self.block.add(key, blk)
        blk.set("x", int(value[0]))
        blk.set("y", int(value[1]))

    @property
    def continuous_focus_position(self) -> tuple[int, int] | None:
        return self._get_xy("continuous_focus_position")

    @continuous_focus_position.setter
    def continuous_focus_position(self, value: tuple[int, int] | None) -> None:
        self._set_xy("continuous_focus_position", value)

    @property
    def initial_show_position(self) -> tuple[int, int] | None:
        return self._get_xy("initial_show_position")

    @initial_show_position.setter
    def initial_show_position(self, value: tuple[int, int] | None) -> None:
        self._set_xy("initial_show_position", value)

    def get_country_script(self) -> str:
        blk = self.block.get_block("country")
        return dumps(blk, top_level=False) if blk is not None else ""

    def set_country_script(self, text: str) -> None:
        if not text.strip():
            self.block.remove("country")
            return
        parsed = pdx_parse(text, recover=False)
        self.block.set("country", Block(parsed.items))

    @property
    def shared_focus_refs(self) -> list[str]:
        return [v.raw for v in self.block.get_all("shared_focus")
                if isinstance(v, Scalar)]

    def add_shared_focus_ref(self, fid: str) -> None:
        if fid not in self.shared_focus_refs:
            self.block.add("shared_focus", fid)

    def remove_shared_focus_ref(self, fid: str) -> None:
        self.block.items = [
            it for it in self.block.items
            if not (isinstance(it, Pair) and it.key == "shared_focus"
                    and isinstance(it.value, Scalar) and it.value.raw == fid)
        ]


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class FocusTreeRef:
    id: str                      # focus_tree id, or file stem for shared/joint files
    kind: str                    # "tree" | "shared" | "joint"
    rel_file: str                # e.g. "common/national_focus/germany.txt"
    source_root: Path
    is_vanilla: bool
    edited: bool = False         # vanilla file that the mod overrides
    country: str = ""            # best-effort tag hint from the `country` block
    focus_count: int = 0

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


@dataclass
class FocusDocument:
    """One parsed national_focus file, ready for editing."""

    ref: FocusTreeRef
    root: Block
    tree: FocusTree | None
    focuses: list[Focus] = field(default_factory=list)   # inside focus_tree
    shared: list[Focus] = field(default_factory=list)    # top-level shared_focus
    joint: list[Focus] = field(default_factory=list)     # top-level joint_focus

    def all_focuses(self) -> list[Focus]:
        return self.focuses + self.shared + self.joint

    def find(self, fid: str) -> Focus | None:
        for f in self.all_focuses():
            if f.id == fid:
                return f
        return None


@dataclass
class Issue:
    """A validation finding. `code` maps to a locale key ``focuses.issue.<code>``."""

    severity: str                # "error" | "warning"
    code: str
    focus_id: str = ""
    detail: str = ""


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

_TREE_RE = re.compile(r"^\s*focus_tree\s*=\s*\{", re.M)
_SHARED_DEF_RE = re.compile(r"^\s*shared_focus\s*=\s*\{", re.M)
_JOINT_DEF_RE = re.compile(r"^\s*joint_focus\s*=\s*\{", re.M)
_FOCUS_RE = re.compile(r"^\s*focus\s*=\s*\{", re.M)
_ID_SCAN_RE = re.compile(r"\bid\s*=\s*(\w+)")
_TAG_SCAN_RE = re.compile(r"\btag\s*=\s*(\w{2,3})\b")
_FILTER_RE = re.compile(r"\bFOCUS_FILTER_[A-Z0-9_]+\b")


class FocusService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, FocusDocument]] = {}
        self._shared_index: dict[str, tuple[FocusTreeRef, Focus]] | None = None
        self._loc_cache: dict[str, dict[str, str]] = {}       # language -> key -> value
        self._loc_files: dict[str, dict[str, Path]] = {}      # language -> key -> mod file
        self._loc_vanilla: dict[str, dict[str, Path]] = {}    # language -> key -> game file
        self._filters: list[str] | None = None

    def focus_ids(self, include_vanilla: bool = True) -> list[str]:
        """Every focus id across national_focus files (mod + game), cheap regex
        scan — for the ``complete_national_focus`` / focus-id pickers."""
        ids: list[str] = []
        seen: set[str] = set()
        # All layers (mod + dependencies, plus base game when include_vanilla).
        roots = self.ctx.override_roots(GAME_DIRS.NATIONAL_FOCUS)
        if not include_vanilla:
            roots = [r for r in roots if r != self.ctx.game_path]
        for root in roots:
            folder = root / GAME_DIRS.NATIONAL_FOCUS
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                try:
                    text = file.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                for block_re in (_FOCUS_RE, _SHARED_DEF_RE, _JOINT_DEF_RE):
                    for bm in block_re.finditer(text):
                        idm = _ID_SCAN_RE.search(text, bm.end())
                        if idm and idm.group(1) not in seen:
                            seen.add(idm.group(1))
                            ids.append(idm.group(1))
        return sorted(ids)

    # --- listing -----------------------------------------------------------
    def list_trees(self, include_vanilla: bool = False) -> list[FocusTreeRef]:
        return layered_docs(
            self.ctx, GAME_DIRS.NATIONAL_FOCUS, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.id.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[FocusTreeRef]:
        folder = root / GAME_DIRS.NATIONAL_FOCUS
        if not folder.is_dir():
            return []
        refs: list[FocusTreeRef] = []
        for file in sorted(folder.glob("*.txt")):
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            ref = self._quick_ref(file, text, root, is_vanilla)
            if ref is not None:
                refs.append(ref)
        return refs

    def _quick_ref(self, file: Path, text: str, root: Path,
                   is_vanilla: bool) -> FocusTreeRef | None:
        """Cheap regex scan for the list panel — no full parse of 80 vanilla files."""
        rel = f"{GAME_DIRS.NATIONAL_FOCUS}/{file.name}"
        tree_m = _TREE_RE.search(text)
        n_focus = len(_FOCUS_RE.findall(text))
        n_shared = len(_SHARED_DEF_RE.findall(text))
        n_joint = len(_JOINT_DEF_RE.findall(text))
        if tree_m:
            id_m = _ID_SCAN_RE.search(text, tree_m.end())
            tag_m = _TAG_SCAN_RE.search(text, tree_m.end())
            return FocusTreeRef(
                id=id_m.group(1) if id_m else file.stem,
                kind="tree", rel_file=rel, source_root=root, is_vanilla=is_vanilla,
                country=tag_m.group(1) if tag_m else "",
                focus_count=n_focus + n_shared + n_joint)
        if n_joint:
            return FocusTreeRef(id=file.stem, kind="joint", rel_file=rel,
                                source_root=root, is_vanilla=is_vanilla,
                                focus_count=n_joint)
        if n_shared:
            return FocusTreeRef(id=file.stem, kind="shared", rel_file=rel,
                                source_root=root, is_vanilla=is_vanilla,
                                focus_count=n_shared)
        return None

    # --- load / save --------------------------------------------------------
    def load(self, ref: FocusTreeRef) -> FocusDocument:
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
        root = parse_file(path)
        doc = self._build_document(ref, root)
        self._doc_cache[path] = (mtime, doc)
        return doc

    @staticmethod
    def _build_document(ref: FocusTreeRef, root: Block) -> FocusDocument:
        tree_block = root.get_block("focus_tree")
        tree = FocusTree(tree_block) if tree_block is not None else None
        doc = FocusDocument(ref=ref, root=root, tree=tree)
        if tree is not None:
            doc.focuses = [Focus(b) for b in tree_block.get_all("focus")
                           if isinstance(b, Block)]
        doc.shared = [Focus(b, "shared_focus") for b in root.get_all("shared_focus")
                      if isinstance(b, Block)]
        doc.joint = [Focus(b, "joint_focus") for b in root.get_all("joint_focus")
                     if isinstance(b, Block)]
        return doc

    def save(self, doc: FocusDocument) -> Path:
        """Write the document into the mod (vanilla documents are never written)."""
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write into the base game")
        path = ensure_filename_case(doc.ref.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, path)
        try:
            self._doc_cache[path] = (path.stat().st_mtime, doc)
        except OSError:
            pass
        self._shared_index = None      # shared definitions may have changed
        return path

    # --- create / copy / delete ---------------------------------------------
    def create_tree(self, file_name: str, tree_id: str, tag: str = "",
                    default: bool = False) -> FocusTreeRef:
        if not file_name.endswith(".txt"):
            file_name += ".txt"
        path = self.ctx.mod.path / GAME_DIRS.NATIONAL_FOCUS / file_name
        if path.exists():
            raise FileExistsError(file_name)
        tree = Block()
        tree.set("id", tree_id)
        country = Block()
        country.set("factor", 0)
        if tag:
            modifier = Block()
            modifier.set("add", 10)
            modifier.set("tag", tag)
            country.add("modifier", modifier)
        tree.add("country", country)
        tree.set("default", bool(default))
        tree.set("reset_on_civilwar", False)
        root = Block([Pair("focus_tree", tree)])
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, path)
        return FocusTreeRef(
            id=tree_id, kind="tree",
            rel_file=f"{GAME_DIRS.NATIONAL_FOCUS}/{file_name}",
            source_root=self.ctx.mod.path, is_vanilla=False, country=tag)

    def copy_to_mod(self, ref: FocusTreeRef) -> FocusTreeRef:
        """Copy a vanilla focus file into the mod (exact filename => engine override)."""
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        out = FocusTreeRef(id=ref.id, kind=ref.kind, rel_file=ref.rel_file,
                           source_root=self.ctx.mod.path, is_vanilla=False,
                           edited=True, country=ref.country,
                           focus_count=ref.focus_count)
        self._shared_index = None
        return out

    def delete(self, ref: FocusTreeRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete vanilla files")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(ref.path, None)
        self._shared_index = None

    # --- focus CRUD ----------------------------------------------------------
    def add_focus(self, doc: FocusDocument, kind: str = "focus", *,
                  fid: str, x: int = 0, y: int = 0,
                  relative_to: str | None = None,
                  prerequisite: str | None = None,
                  icon: str = "GFX_goal_unknown") -> Focus:
        block = Block()
        block.set("id", fid)
        block.set("icon", icon)
        block.set("x", x)
        block.set("y", y)
        if relative_to:
            block.set("relative_position_id", relative_to)
        if prerequisite:
            pre = Block()
            pre.add("focus", prerequisite)
            block.add("prerequisite", pre)
        block.set("cost", 10)
        block.add("completion_reward", Block())
        focus = Focus(block, kind)
        if kind == "focus":
            if doc.tree is None:
                raise ValueError("Document has no focus_tree")
            doc.tree.block.add("focus", block)
            doc.focuses.append(focus)
        elif kind == "shared_focus":
            doc.root.add("shared_focus", block)
            doc.shared.append(focus)
        else:
            doc.root.add("joint_focus", block)
            doc.joint.append(focus)
        return focus

    def duplicate_focus(self, doc: FocusDocument, focus: Focus, new_id: str) -> Focus:
        copy_root = pdx_parse(dumps(Block([Pair(focus.kind, focus.block)])))
        block = copy_root.get_block(focus.kind)
        dup = Focus(block, focus.kind)
        dup.id = new_id
        dup.x = focus.x + 2          # place next to the original
        if focus.kind == "focus":
            doc.tree.block.add("focus", block)
            doc.focuses.append(dup)
        elif focus.kind == "shared_focus":
            doc.root.add("shared_focus", block)
            doc.shared.append(dup)
        else:
            doc.root.add("joint_focus", block)
            doc.joint.append(dup)
        return dup

    def remove_focus(self, doc: FocusDocument, focus: Focus) -> None:
        """Delete a focus and scrub every reference to it in the document.
        Focuses positioned relative to it are re-anchored at absolute coords."""
        positions = self.resolved_positions(doc.all_focuses())
        fid = focus.id
        parent = doc.tree.block if focus.kind == "focus" and doc.tree else doc.root
        parent.items = [it for it in parent.items
                        if not (isinstance(it, Pair) and it.value is focus.block)]
        for lst in (doc.focuses, doc.shared, doc.joint):
            if focus in lst:
                lst.remove(focus)
        for other in doc.all_focuses():
            other.drop_reference(fid, positions.get(other.id))
        if doc.tree is not None:
            doc.tree.remove_shared_focus_ref(fid)

    def rename_focus(self, doc: FocusDocument, focus: Focus, new_id: str) -> str:
        """Rename and rewrite all references inside the document. Returns the old id."""
        old = focus.id
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid focus id: {new_id!r}")
        if doc.find(new_id) is not None:
            raise ValueError(f"Duplicate focus id: {new_id}")
        focus.id = new_id
        for other in doc.all_focuses():
            other.replace_reference(old, new_id)
        if doc.tree is not None and old in doc.tree.shared_focus_refs:
            doc.tree.remove_shared_focus_ref(old)
            doc.tree.add_shared_focus_ref(new_id)
        return old

    # --- positions -----------------------------------------------------------
    @staticmethod
    def resolved_positions(focuses: list[Focus]) -> dict[str, tuple[int, int]]:
        """Absolute grid cell of every focus, resolving `relative_position_id` chains
        (cycle-safe: a cycle falls back to the focus's own x/y)."""
        by_id = {f.id: f for f in focuses}
        memo: dict[str, tuple[int, int]] = {}

        def resolve(fid: str, stack: frozenset[str]) -> tuple[int, int]:
            if fid in memo:
                return memo[fid]
            f = by_id.get(fid)
            if f is None:
                return (0, 0)
            base = (0, 0)
            rel = f.relative_position_id
            if rel and rel != fid and rel not in stack and rel in by_id:
                base = resolve(rel, stack | {fid})
            memo[fid] = (base[0] + f.x, base[1] + f.y)
            return memo[fid]

        for f in focuses:
            if f.id:
                resolve(f.id, frozenset())
        return memo

    @staticmethod
    def move_focus(focus: Focus, new_abs: tuple[int, int],
                   positions: dict[str, tuple[int, int]]) -> None:
        """Set x/y so the focus lands on absolute cell `new_abs`, honouring its
        relative anchor if any."""
        base = (0, 0)
        rel = focus.relative_position_id
        if rel and rel in positions:
            base = positions[rel]
        focus.x = new_abs[0] - base[0]
        focus.y = new_abs[1] - base[1]

    # --- shared focuses across files ------------------------------------------
    def shared_index(self) -> dict[str, tuple[FocusTreeRef, Focus]]:
        """All shared_focus definitions across mod + game (mod wins on id clash)."""
        if self._shared_index is not None:
            return self._shared_index
        index: dict[str, tuple[FocusTreeRef, Focus]] = {}
        # list_trees puts mod refs first; iterate reversed so mod definitions are
        # written last and win on id clashes.
        for ref in reversed(self.list_trees(include_vanilla=True)):
            try:
                text = ref.path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            if not _SHARED_DEF_RE.search(text):
                continue
            try:
                doc = self.load(ref)
            except Exception:
                continue
            for f in doc.shared:
                if f.id:
                    index[f.id] = (ref, f)
        self._shared_index = index
        return index

    def attached_shared(self, doc: FocusDocument) -> list[tuple[FocusTreeRef, Focus]]:
        """Shared focuses shown with this tree: the tree's `shared_focus` roots plus
        every shared focus whose prerequisite/position chain hangs off them.
        Focuses already defined in `doc` itself are excluded."""
        if doc.tree is None:
            return []
        roots = set(doc.tree.shared_focus_refs)
        if not roots:
            return []
        index = self.shared_index()
        local_ids = {f.id for f in doc.all_focuses()}
        included: set[str] = set(r for r in roots if r in index)
        changed = True
        while changed:
            changed = False
            for fid, (_ref, focus) in index.items():
                if fid in included:
                    continue
                anchors = {focus.relative_position_id} | {
                    f for g in focus.prerequisites for f in g}
                if anchors & included:
                    included.add(fid)
                    changed = True
        return [(index[fid][0], index[fid][1])
                for fid in included if fid not in local_ids]

    # --- localisation -----------------------------------------------------------
    def focus_name(self, fid: str, language: str) -> str:
        idx = self._loc_index(language)
        text = idx.get(fid)
        if text is None and language != "english":
            text = self._loc_index("english").get(fid)
        return text if text is not None else fid

    def focus_desc(self, fid: str, language: str) -> str:
        idx = self._loc_index(language)
        text = idx.get(f"{fid}_desc")
        if text is None and language != "english":
            text = self._loc_index("english").get(f"{fid}_desc")
        return text or ""

    def has_name(self, fid: str, language: str) -> bool:
        return fid in self._loc_index(language)

    def has_any_name(self, fid: str, preferred: str = "english") -> bool:
        """Is the focus named in *any* game language? Checks the preferred language
        and english first (cheapest, most likely hits), then the rest."""
        order = [preferred, "english"] + [l for l in HOI4_LANGUAGES
                                          if l not in (preferred, "english")]
        return any(fid in self._loc_index(lang) for lang in order)

    def set_focus_loc(self, fid: str, language: str,
                      name: str | None, desc: str | None) -> None:
        """Write name/desc into the mod. Priority for the target file:
        a mod file already carrying the key; else — when the key is vanilla — a
        mod-side copy of the *original* vanilla file (same relative path => override,
        no loc key collision); else ANKA's own focus localisation file."""
        default = (self.ctx.mod.path / GAME_DIRS.LOCALISATION / language
                   / f"anka_focus_l_{language}.yml")
        self._loc_index(language)                      # ensure maps are built
        files = self._loc_files.setdefault(language, {})
        vanilla = self._loc_vanilla.setdefault(language, {})
        for key, value in ((fid, name), (f"{fid}_desc", desc)):
            if value is None:
                continue
            path = files.get(key)
            if path is None and key in vanilla:
                path = self._override_vanilla_loc(vanilla[key], language)
            if path is None:
                path = default
            loc = LocFile.load(path) if path.exists() else LocFile(language)
            loc.set(key, value)
            loc.save(path)
            files[key] = path
            self._loc_index(language)[key] = value

    def _override_vanilla_loc(self, source: Path, language: str) -> Path:
        """Copy a vanilla .yml into the mod at the same relative path and remap every
        key it defines to the copy (further edits go there, not to ANKA files)."""
        rel = source.relative_to(self.ctx.game_path)
        target = ensure_filename_case(self.ctx.mod.path / rel)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        files = self._loc_files.setdefault(language, {})
        try:
            for entry in LocFile.load(target).entries():
                files.setdefault(entry.key, target)
        except OSError:
            pass
        return target

    def rename_focus_loc(self, old: str, new: str) -> None:
        """Carry every mod-side localisation entry over to the renamed id."""
        for language, files in self._loc_files.items():
            for suffix in ("", "_desc"):
                key = old + suffix
                path = files.get(key)
                if path is None or not path.exists():
                    continue
                loc = LocFile.load(path)
                value = loc.get(key)
                if value is None:
                    continue
                loc.remove(key)
                loc.set(new + suffix, value)
                loc.save(path)
                files.pop(key, None)
                files[new + suffix] = path
                idx = self._loc_cache.get(language)
                if idx is not None:
                    idx.pop(key, None)
                    idx[new + suffix] = value

    def _loc_index(self, language: str) -> dict[str, str]:
        """key -> text for a language. Mod files are all read; vanilla files are
        filtered to focus-related names to keep memory/time sane."""
        cached = self._loc_cache.get(language)
        if cached is not None:
            return cached
        index: dict[str, str] = {}
        vanilla_files = self._loc_vanilla.setdefault(language, {})
        game_dir = self.ctx.game_path / GAME_DIRS.LOCALISATION
        if game_dir.is_dir():
            for yml in game_dir.rglob(f"*_l_{language}.yml"):
                if "focus" not in yml.name.lower():
                    continue
                before = set(index)
                self._read_loc(yml, index)
                for key in set(index) - before:
                    vanilla_files[key] = yml
        files = self._loc_files.setdefault(language, {})
        mod_dir = self.ctx.mod.path / GAME_DIRS.LOCALISATION
        if mod_dir.is_dir():
            for yml in sorted(mod_dir.rglob(f"*_l_{language}.yml")):
                try:
                    loc = LocFile.load(yml)
                except OSError:
                    continue
                for entry in loc.entries():
                    index[entry.key] = entry.value
                    files[entry.key] = yml
        self._loc_cache[language] = index
        return index

    @staticmethod
    def _read_loc(path: Path, index: dict[str, str]) -> None:
        try:
            loc = LocFile.load(path)
        except OSError:
            return
        for entry in loc.entries():
            index[entry.key] = entry.value

    # --- search filters ------------------------------------------------------
    def search_filter_values(self) -> list[str]:
        """All FOCUS_FILTER_* constants used by the game + mod (dynamic, not hardcoded)."""
        if self._filters is not None:
            return self._filters
        found: set[str] = set()
        for root in self.ctx.override_roots(GAME_DIRS.NATIONAL_FOCUS):
            folder = root / GAME_DIRS.NATIONAL_FOCUS
            if not folder.is_dir():
                continue
            for file in folder.glob("*.txt"):
                try:
                    found.update(_FILTER_RE.findall(
                        file.read_text(encoding="utf-8-sig", errors="replace")))
                except OSError:
                    continue
        self._filters = sorted(found)
        return self._filters

    # --- validation ------------------------------------------------------------
    def validate(self, doc: FocusDocument,
                 attached: list[tuple[FocusTreeRef, Focus]] | None = None,
                 language: str | None = None,
                 sprite_exists=None) -> list[Issue]:
        issues: list[Issue] = []
        local = doc.all_focuses()
        attached = attached or []
        visible = local + [f for _r, f in attached]
        by_id: dict[str, Focus] = {}

        for f in local:
            if not f.id:
                issues.append(Issue("error", "missing_id"))
            elif not _ID_RE.match(f.id):
                issues.append(Issue("error", "bad_id", f.id))
        for f in visible:
            if f.id in by_id:
                issues.append(Issue("error", "duplicate_id", f.id))
            elif f.id:
                by_id[f.id] = f

        known = set(by_id)
        for f in local:
            for group in f.prerequisites:
                for fid in group:
                    if fid not in known:
                        issues.append(Issue("warning", "broken_prerequisite",
                                            f.id, fid))
            for fid in f.mutually_exclusive:
                if fid not in known:
                    issues.append(Issue("warning", "broken_mutex", f.id, fid))
                elif fid in by_id and f.id not in by_id[fid].mutually_exclusive:
                    issues.append(Issue("warning", "one_way_mutex", f.id, fid))
            rel = f.relative_position_id
            if rel and rel not in known:
                issues.append(Issue("error", "broken_relative", f.id, rel))
            if f.cost < 0:
                issues.append(Issue("warning", "negative_cost", f.id))

        # relative_position_id cycles
        for f in local:
            seen: set[str] = set()
            cur: Focus | None = f
            while cur is not None and cur.relative_position_id:
                if cur.id in seen:
                    issues.append(Issue("error", "relative_cycle", f.id))
                    break
                seen.add(cur.id)
                cur = by_id.get(cur.relative_position_id)

        # prerequisite cycles (DFS over the AND/OR graph, edges = any prereq)
        graph = {f.id: {fid for g in f.prerequisites for fid in g if fid in known}
                 for f in visible if f.id}
        state: dict[str, int] = {}

        def dfs(node: str, path: list[str]) -> None:
            state[node] = 1
            for nxt in graph.get(node, ()):
                if state.get(nxt) == 1:
                    issues.append(Issue("error", "prerequisite_cycle", node, nxt))
                elif state.get(nxt, 0) == 0:
                    dfs(nxt, path + [nxt])
            state[node] = 2

        for fid in graph:
            if state.get(fid, 0) == 0:
                dfs(fid, [fid])

        # grid collisions & children above parents
        positions = self.resolved_positions(visible)
        cells: dict[tuple[int, int], str] = {}
        for f in local:
            pos = positions.get(f.id)
            if pos is None:
                continue
            if pos in cells:
                issues.append(Issue("warning", "cell_collision", f.id, cells[pos]))
            else:
                cells[pos] = f.id
            for group in f.prerequisites:
                for fid in group:
                    ppos = positions.get(fid)
                    if ppos is not None and ppos[1] >= pos[1]:
                        issues.append(Issue("warning", "prerequisite_below",
                                            f.id, fid))

        if sprite_exists is not None:
            for f in local:
                if f.icon and not sprite_exists(f.icon):
                    issues.append(Issue("warning", "missing_icon", f.id, f.icon))

        if language:
            for f in local:
                key = f.text or f.id      # `text` overrides the localisation key
                if f.id and not self.has_any_name(key, preferred=language):
                    issues.append(Issue("warning", "missing_loc", f.id))

        return issues
