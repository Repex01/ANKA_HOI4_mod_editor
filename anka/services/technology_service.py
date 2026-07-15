"""Read/write HOI4 technologies for the Technologies editor.

Sources (mod first, base game as fallback): ``common/technologies/*.txt`` — each file
is one ``technologies = { @consts, <tech_id> = {...} }`` block; ``common/technology_tags``
declares the category whitelist and the folder tabs. The model is *block-backed*:
`Technology` / `FolderAssignment` / `TechPath` are thin views over the parsed PDX
`Block`, so every key ANKA does not know about survives a load/save round-trip.

Positions: a tech's tree placement lives in repeated ``folder = { name position }``
blocks whose x/y may reference file-local ``@constants`` (kept on write when the value
still matches — see `FolderAssignment.set_cell`). The pixel grid those cells map onto
is defined per folder in the interface ``.gui`` files (see `tech_gui_service`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS, HOI4_LANGUAGES
from ..core.pdx import Block, Pair, Scalar, dump_file, dumps, parse_file
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs
from ._locutil import LocCatalog

# Free-form script blocks edited as validated PDX text.
TECH_SCRIPT_FIELDS = (
    "allow", "allow_branch", "ai_will_do", "ai_research_weights",
    "on_research_complete", "on_research_complete_limit",
)

# Boolean flags that are tri-state in script: absent = game default (False).
TECH_FLAG_FIELDS = (
    "doctrine", "show_effect_as_desc", "force_use_small_tech_layout",
    "show_equipment_icon", "is_special_project_tech",
)

# Keys with editor-specific handling; everything else on a tech block is either a
# flat country modifier (scalar) or a unit/category stat block (block).
_STRUCT_SCALARS = frozenset({
    "research_cost", "start_year", "doctrine", "doctrine_name", "desc",
    "enable_tactic", "xp_research_type", "xp_boost_cost", "xp_research_bonus",
    "xp_unlock_cost", "show_effect_as_desc", "force_use_small_tech_layout",
    "show_equipment_icon", "is_special_project_tech", "sub_tech_index",
})
_STRUCT_BLOCKS = frozenset({
    "folder", "path", "dependencies", "xor", "allow", "allow_branch",
    "categories", "enable_equipments", "enable_equipment_modules",
    "enable_subunits", "enable_building", "sub_technologies",
    "ai_will_do", "ai_research_weights", "on_research_complete",
    "on_research_complete_limit", "special_project_specialization",
})

MAX_SUBTECHS = 3               # NDefines.NTechnology.MAX_SUBTECHS
DEFAULT_RESEARCH_COST = 1.0

_ID_RE = re.compile(r"^\w+$")
_NAME_TOKEN_RE = re.compile(r"([A-Za-z0-9_.@\-]+)\s*=\s*\{|\{|\}")


def _resolve_coord(raw: str | None, consts: dict[str, int]) -> int:
    """A folder-position coordinate: literal int or an ``@constant`` reference."""
    if raw is None:
        return 0
    raw = raw.strip()
    if raw.startswith("@"):
        return consts.get(raw, 0)
    try:
        return int(float(raw))
    except ValueError:
        return 0


class TechPath:
    """A view over one ``path = { leads_to_tech = X ... }`` block."""

    def __init__(self, block: Block):
        self.block = block

    @property
    def leads_to_tech(self) -> str:
        return self.block.get_scalar_ci("leads_to_tech", "") or ""

    @leads_to_tech.setter
    def leads_to_tech(self, value: str) -> None:
        self.block.set_ci("leads_to_tech", value)

    @property
    def research_cost_coeff(self) -> float | None:
        v = self.block.get_ci("research_cost_coeff")
        return v.as_float() if isinstance(v, Scalar) else None

    @research_cost_coeff.setter
    def research_cost_coeff(self, value: float | None) -> None:
        if value is None:
            self.block.remove_ci("research_cost_coeff")
        else:
            value = round(float(value), 3)
            self.block.set_ci("research_cost_coeff",
                              int(value) if value == int(value) else value)

    @property
    def ignore_for_layout(self) -> bool:
        v = self.block.get_ci("ignore_for_layout")
        return v.as_bool() if isinstance(v, Scalar) else False

    @ignore_for_layout.setter
    def ignore_for_layout(self, value: bool) -> None:
        if value:
            self.block.set_ci("ignore_for_layout", True)
        else:
            self.block.remove_ci("ignore_for_layout")


class FolderAssignment:
    """A view over one ``folder = { name = X position = { x y } }`` block."""

    def __init__(self, block: Block):
        self.block = block

    @property
    def name(self) -> str:
        return self.block.get_scalar_ci("name", "") or ""

    @name.setter
    def name(self, value: str) -> None:
        self.block.set_ci("name", value)

    def _position(self) -> Block | None:
        return self.block.get_block_ci("position")

    @property
    def raw_x(self) -> str | None:
        pos = self._position()
        return pos.get_scalar_ci("x") if pos is not None else None

    @property
    def raw_y(self) -> str | None:
        pos = self._position()
        return pos.get_scalar_ci("y") if pos is not None else None

    def cell(self, consts: dict[str, int]) -> tuple[int, int]:
        return (_resolve_coord(self.raw_x, consts),
                _resolve_coord(self.raw_y, consts))

    def set_cell(self, x: int, y: int, doc: "TechDocument | None" = None) -> None:
        """Write the cell, snapping back to an ``@constant`` when the axis already
        used one and the target value has a matching constant in the document."""
        pos = self._position()
        if pos is None:
            pos = Block()
            self.block.set_ci("position", pos)
        consts = doc.consts if doc is not None else {}
        by_value = doc.const_by_value if doc is not None else {}
        for key, raw, value in (("x", self.raw_x, int(x)), ("y", self.raw_y, int(y))):
            if raw is not None and _resolve_coord(raw, consts) == value:
                continue                       # untouched axis round-trips verbatim
            if raw is not None and raw.strip().startswith("@") and value in by_value:
                pos.set_ci(key, Scalar(by_value[value]))
            else:
                pos.set_ci(key, value)


class Technology:
    """A view over one ``<tech_id> = { ... }`` block inside ``technologies``.

    Holds the owning `Pair` so renaming edits the key in place.
    """

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    # --- identity -----------------------------------------------------------
    @property
    def id(self) -> str:
        return self.pair.key

    @id.setter
    def id(self, value: str) -> None:
        self.pair.key = value

    # --- basic fields ---------------------------------------------------------
    @property
    def research_cost(self) -> float:
        v = self.block.get_ci("research_cost")
        return (v.as_float(DEFAULT_RESEARCH_COST) if isinstance(v, Scalar)
                else DEFAULT_RESEARCH_COST)

    @research_cost.setter
    def research_cost(self, value: float) -> None:
        value = round(float(value), 2)
        self.block.set_ci("research_cost",
                          int(value) if value == int(value) else value)

    @property
    def start_year(self) -> int | None:
        v = self.block.get_ci("start_year")
        return v.as_int() if isinstance(v, Scalar) else None

    @start_year.setter
    def start_year(self, value: int | None) -> None:
        if value is None:
            self.block.remove_ci("start_year")
        else:
            self.block.set_ci("start_year", int(value))

    @property
    def desc(self) -> str | None:
        return self.block.get_scalar_ci("desc")

    @desc.setter
    def desc(self, value: str | None) -> None:
        if value:
            self.block.set_ci("desc", value)
        else:
            self.block.remove_ci("desc")

    @property
    def doctrine_name(self) -> str | None:
        return self.block.get_scalar_ci("doctrine_name")

    @doctrine_name.setter
    def doctrine_name(self, value: str | None) -> None:
        if value:
            self.block.set_ci("doctrine_name", value)
        else:
            self.block.remove_ci("doctrine_name")

    @property
    def enable_tactic(self) -> str | None:
        return self.block.get_scalar_ci("enable_tactic")

    @enable_tactic.setter
    def enable_tactic(self, value: str | None) -> None:
        if value:
            self.block.set_ci("enable_tactic", value)
        else:
            self.block.remove_ci("enable_tactic")

    @property
    def sub_tech_index(self) -> int | None:
        v = self.block.get_ci("sub_tech_index")
        return v.as_int() if isinstance(v, Scalar) else None

    @sub_tech_index.setter
    def sub_tech_index(self, value: int | None) -> None:
        if value is None:
            self.block.remove_ci("sub_tech_index")
        else:
            self.block.set_ci("sub_tech_index", int(value))

    # --- XP fields (absent = not set) ----------------------------------------
    @property
    def xp_research_type(self) -> str | None:
        return self.block.get_scalar_ci("xp_research_type")

    @xp_research_type.setter
    def xp_research_type(self, value: str | None) -> None:
        if value:
            self.block.set_ci("xp_research_type", value)
        else:
            self.block.remove_ci("xp_research_type")

    def _get_num(self, key: str) -> float | None:
        v = self.block.get_ci(key)
        return v.as_float() if isinstance(v, Scalar) else None

    def _set_num(self, key: str, value: float | None) -> None:
        if value is None:
            self.block.remove_ci(key)
        else:
            value = round(float(value), 3)
            self.block.set_ci(key, int(value) if value == int(value) else value)

    @property
    def xp_boost_cost(self) -> float | None:
        return self._get_num("xp_boost_cost")

    @xp_boost_cost.setter
    def xp_boost_cost(self, value: float | None) -> None:
        self._set_num("xp_boost_cost", value)

    @property
    def xp_research_bonus(self) -> float | None:
        return self._get_num("xp_research_bonus")

    @xp_research_bonus.setter
    def xp_research_bonus(self, value: float | None) -> None:
        self._set_num("xp_research_bonus", value)

    @property
    def xp_unlock_cost(self) -> float | None:
        return self._get_num("xp_unlock_cost")

    @xp_unlock_cost.setter
    def xp_unlock_cost(self, value: float | None) -> None:
        self._set_num("xp_unlock_cost", value)

    # --- tri-state flags --------------------------------------------------
    def get_flag(self, name: str) -> bool:
        v = self.block.get_ci(name)
        return v.as_bool() if isinstance(v, Scalar) else False

    def set_flag(self, name: str, value: bool) -> None:
        """Write the flag only when it differs from the game default (absent=no)."""
        if value:
            self.block.set_ci(name, True)
        else:
            self.block.remove_ci(name)

    @property
    def doctrine(self) -> bool:
        return self.get_flag("doctrine")

    @doctrine.setter
    def doctrine(self, value: bool) -> None:
        self.set_flag("doctrine", value)

    # --- placement ------------------------------------------------------------
    @property
    def folders(self) -> list[FolderAssignment]:
        return [FolderAssignment(b) for b in self.block.get_all_ci("folder")
                if isinstance(b, Block)]

    def folder_in(self, folder_id: str) -> FolderAssignment | None:
        for fa in self.folders:
            if fa.name == folder_id:
                return fa
        return None

    def add_folder(self, folder_id: str, x: int = 0, y: int = 0) -> FolderAssignment:
        blk = Block()
        blk.set("name", folder_id)
        pos = Block()
        pos.set("x", int(x))
        pos.set("y", int(y))
        blk.set("position", pos)
        self.block.add("folder", blk)
        return FolderAssignment(blk)

    def remove_folder(self, folder_id: str) -> None:
        self.block.items = [
            it for it in self.block.items
            if not (isinstance(it, Pair) and it.key.lower() == "folder"
                    and isinstance(it.value, Block)
                    and FolderAssignment(it.value).name == folder_id)
        ]

    # --- links ------------------------------------------------------------------
    @property
    def paths(self) -> list[TechPath]:
        return [TechPath(b) for b in self.block.get_all_ci("path")
                if isinstance(b, Block)]

    def path_to(self, tid: str) -> TechPath | None:
        for p in self.paths:
            if p.leads_to_tech == tid:
                return p
        return None

    def add_path(self, target: str, coeff: float | None = 1) -> TechPath:
        blk = Block()
        blk.set("leads_to_tech", target)
        if coeff is not None:
            blk.set("research_cost_coeff",
                    int(coeff) if coeff == int(coeff) else coeff)
        self.block.add("path", blk)
        return TechPath(blk)

    def remove_path(self, target: str) -> None:
        self.block.items = [
            it for it in self.block.items
            if not (isinstance(it, Pair) and it.key.lower() == "path"
                    and isinstance(it.value, Block)
                    and TechPath(it.value).leads_to_tech == target)
        ]

    @property
    def dependencies(self) -> dict[str, int]:
        blk = self.block.get_block_ci("dependencies")
        if blk is None:
            return {}
        out: dict[str, int] = {}
        for pair in blk.pairs():
            if isinstance(pair.value, Scalar):
                out[pair.key] = pair.value.as_int(1) or 1
        return out

    @dependencies.setter
    def dependencies(self, deps: dict[str, int]) -> None:
        if not deps:
            self.block.remove_ci("dependencies")
            return
        blk = Block()
        for tid, level in deps.items():
            blk.set(tid, int(level))
        self.block.set_ci("dependencies", blk)

    @property
    def xor(self) -> list[str]:
        blk = self.block.get_block_ci("xor")
        return blk.array_values() if blk is not None else []

    @xor.setter
    def xor(self, ids: list[str]) -> None:
        if not ids:
            self.block.remove_ci("xor")
            return
        blk = Block()
        for tid in ids:
            blk.add_value(tid)
        # vanilla spells it uppercase; set_ci keeps existing casing on update
        self.block.set_ci("XOR", blk)

    # --- simple array blocks ------------------------------------------------
    def _get_array(self, key: str) -> list[str]:
        blk = self.block.get_block_ci(key)
        return blk.array_values() if blk is not None else []

    def _set_array(self, key: str, values: list[str]) -> None:
        if not values:
            self.block.remove_ci(key)
            return
        blk = Block()
        for v in values:
            blk.add_value(v)
        self.block.set_ci(key, blk)

    @property
    def categories(self) -> list[str]:
        return self._get_array("categories")

    @categories.setter
    def categories(self, values: list[str]) -> None:
        self._set_array("categories", values)

    @property
    def enable_equipments(self) -> list[str]:
        return self._get_array("enable_equipments")

    @enable_equipments.setter
    def enable_equipments(self, values: list[str]) -> None:
        self._set_array("enable_equipments", values)

    @property
    def enable_equipment_modules(self) -> list[str]:
        return self._get_array("enable_equipment_modules")

    @enable_equipment_modules.setter
    def enable_equipment_modules(self, values: list[str]) -> None:
        self._set_array("enable_equipment_modules", values)

    @property
    def enable_subunits(self) -> list[str]:
        return self._get_array("enable_subunits")

    @enable_subunits.setter
    def enable_subunits(self, values: list[str]) -> None:
        self._set_array("enable_subunits", values)

    @property
    def sub_technologies(self) -> list[str]:
        return self._get_array("sub_technologies")

    @sub_technologies.setter
    def sub_technologies(self, values: list[str]) -> None:
        self._set_array("sub_technologies", values)

    @property
    def special_project_specialization(self) -> list[str]:
        return self._get_array("special_project_specialization")

    @special_project_specialization.setter
    def special_project_specialization(self, values: list[str]) -> None:
        self._set_array("special_project_specialization", values)

    @property
    def enable_buildings(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for blk in self.block.get_all_ci("enable_building"):
            if isinstance(blk, Block):
                building = blk.get_scalar_ci("building", "")
                level = blk.get_ci("level")
                out.append((building or "",
                            level.as_int(1) if isinstance(level, Scalar) else 1))
        return out

    @enable_buildings.setter
    def enable_buildings(self, values: list[tuple[str, int]]) -> None:
        index = next((i for i, it in enumerate(self.block.items)
                      if isinstance(it, Pair)
                      and it.key.lower() == "enable_building"), None)
        self.block.items = [it for it in self.block.items
                            if not (isinstance(it, Pair)
                                    and it.key.lower() == "enable_building")]
        pairs = []
        for building, level in values:
            blk = Block()
            blk.set("building", building)
            blk.set("level", int(level))
            pairs.append(Pair("enable_building", blk))
        if index is None:
            self.block.items.extend(pairs)
        else:
            self.block.items[index:index] = pairs

    # --- script blocks as validated text ------------------------------------
    def get_script(self, key: str) -> str:
        v = self.block.get_ci(key)
        if isinstance(v, Block):
            return dumps(v, top_level=False)
        if isinstance(v, Scalar):
            return v.raw
        return ""

    def set_script(self, key: str, text: str) -> None:
        """Parse `text` strictly and store it under `key`; raises on bad script.
        Empty text removes the key entirely."""
        if not text.strip():
            self.block.remove_ci(key)
            return
        parsed = pdx_parse(text, recover=False)
        if len(parsed.items) == 1 and isinstance(parsed.items[0], Scalar):
            self.block.set_ci(key, parsed.items[0])
        else:
            self.block.set_ci(key, Block(parsed.items))

    # --- free-form remainder ---------------------------------------------------
    def modifier_pairs(self) -> list[Pair]:
        """Flat country-modifier pairs (``research_speed_factor = 0.03`` etc.) —
        every scalar pair that is not a structural field."""
        return [p for p in self.block.pairs()
                if isinstance(p.value, Scalar)
                and p.key.lower() not in _STRUCT_SCALARS]

    def stat_blocks(self) -> list[Pair]:
        """Unit/category stat blocks (``category_light_infantry = {...}``,
        ``destroyer = {...}``) — every block pair that is not structural."""
        return [p for p in self.block.pairs()
                if isinstance(p.value, Block)
                and p.key.lower() not in _STRUCT_BLOCKS]

    # --- references -----------------------------------------------------------
    def references(self, tid: str) -> bool:
        return (any(p.leads_to_tech == tid for p in self.paths)
                or tid in self.dependencies
                or tid in self.xor
                or tid in self.sub_technologies)

    def replace_reference(self, old: str, new: str) -> None:
        for p in self.paths:
            if p.leads_to_tech == old:
                p.leads_to_tech = new
        deps = self.dependencies
        if old in deps:
            self.dependencies = {new if k == old else k: v for k, v in deps.items()}
        xor = self.xor
        if old in xor:
            self.xor = [new if t == old else t for t in xor]
        subs = self.sub_technologies
        if old in subs:
            self.sub_technologies = [new if t == old else t for t in subs]

    def drop_reference(self, tid: str) -> None:
        self.remove_path(tid)
        deps = self.dependencies
        if tid in deps:
            deps.pop(tid)
            self.dependencies = deps
        xor = self.xor
        if tid in xor:
            self.xor = [t for t in xor if t != tid]
        subs = self.sub_technologies
        if tid in subs:
            self.sub_technologies = [t for t in subs if t != tid]

    def __repr__(self) -> str:
        return f"Technology({self.id!r})"


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class TechDocRef:
    rel_file: str                # e.g. "common/technologies/infantry.txt"
    source_root: Path
    is_vanilla: bool
    edited: bool = False         # vanilla file that the mod overrides
    tech_ids: list[str] = field(default_factory=list)   # cheap-scan preview

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class TechDocument:
    """One parsed ``common/technologies`` file, ready for editing."""

    ref: TechDocRef
    root: Block
    techs: list[Technology] = field(default_factory=list)

    @property
    def techs_block(self) -> Block | None:
        return self.root.get_block_ci("technologies")

    @property
    def consts(self) -> dict[str, int]:
        """File-local ``@name = value`` constants (inside ``technologies``)."""
        blk = self.techs_block
        if blk is None:
            return {}
        out: dict[str, int] = {}
        for pair in blk.pairs():
            if pair.key.startswith("@") and isinstance(pair.value, Scalar):
                v = pair.value.as_int()
                if v is None:
                    f = pair.value.as_float()
                    v = int(f) if f is not None else None
                if v is not None:
                    out[pair.key] = v
        return out

    @property
    def const_by_value(self) -> dict[int, str]:
        """First constant per value — used to snap positions back to ``@consts``."""
        out: dict[int, str] = {}
        for name, value in self.consts.items():
            out.setdefault(value, name)
        return out

    def all_techs(self) -> list[Technology]:
        return list(self.techs)

    def find(self, tid: str) -> Technology | None:
        for t in self.techs:
            if t.id == tid:
                return t
        return None


@dataclass
class TechFolderDef:
    """One folder tab from ``common/technology_tags`` (read view + source block)."""

    id: str
    ledger: str = ""
    doctrine: bool = False
    available: str = ""          # trigger script text ("" = always)
    is_vanilla: bool = True
    block: Block | None = None
    source: Path | None = None   # the technology_tags file declaring it


@dataclass
class Issue:
    """A validation finding. `code` maps to ``technologies.issue.<code>``."""

    severity: str                # "error" | "warning" | "info"
    code: str
    tech_id: str = ""
    detail: str = ""
    folder_id: str = ""


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class TechnologyService:
    TECH_DIR = GAME_DIRS.TECHNOLOGIES
    TAGS_DIR = GAME_DIRS.TECHNOLOGY_TAGS

    def __init__(self, context: ModContext):
        self.ctx = context
        self._cache: list[str] | None = None          # legacy list_techs cache
        self._doc_cache: dict[Path, tuple[float, TechDocument]] = {}
        self._index: dict[str, TechDocRef] | None = None
        self._tags_cache: tuple[list[str], dict[str, TechFolderDef]] | None = None
        self._ai_categories: set[str] | None = None
        self._loc = LocCatalog(
            context.mod.path, context.game_path,
            # "equipment" is needed too: techs that unlock equipment often have no
            # loc key of their own and the game shows the equipment's name instead.
            vanilla_filter=("research", "modifiers", "equipment"),
            default_pattern="anka_technologies_l_{lang}.yml",
            dep_roots=context.dependency_paths)

    # --- legacy API (Countries editor) ---------------------------------------
    def list_techs(self, refresh: bool = False) -> list[str]:
        """Every tech id across the content layers (engine override semantics)."""
        if self._cache is None or refresh:
            self._cache = sorted(self.tech_index(refresh=refresh))
        return self._cache

    # --- listing -----------------------------------------------------------
    def list_docs(self, include_vanilla: bool = False) -> list[TechDocRef]:
        return layered_docs(
            self.ctx, self.TECH_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.name.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[TechDocRef]:
        folder = root / self.TECH_DIR
        if not folder.is_dir():
            return []
        refs: list[TechDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            refs.append(TechDocRef(
                rel_file=f"{self.TECH_DIR}/{file.name}", source_root=root,
                is_vanilla=is_vanilla, tech_ids=self._scan_tech_ids(text)))
        return refs

    @staticmethod
    def _scan_tech_ids(text: str) -> list[str]:
        """Cheap brace-depth scan: names of blocks directly inside ``technologies``."""
        text = re.sub(r"#[^\n]*", "", text)
        ids: list[str] = []
        stack: list[str] = []
        for m in _NAME_TOKEN_RE.finditer(text):
            name = m.group(1)
            if name is not None:
                if len(stack) == 1 and stack[0].lower() == "technologies" \
                        and not name.startswith("@"):
                    ids.append(name)
                stack.append(name)
            elif m.group(0) == "{":
                stack.append("")               # anonymous block
            else:
                if stack:
                    stack.pop()
        return ids

    # --- load / save --------------------------------------------------------
    def load(self, ref: TechDocRef) -> TechDocument:
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
    def _build_document(ref: TechDocRef, root: Block) -> TechDocument:
        doc = TechDocument(ref=ref, root=root)
        techs_block = doc.techs_block
        if techs_block is not None:
            doc.techs = [Technology(p) for p in techs_block.pairs()
                         if isinstance(p.value, Block)
                         and not p.key.startswith("@")]
        return doc

    def document_from_text(self, ref: TechDocRef, text: str) -> TechDocument:
        """Rebuild a document from serialized text — used by undo/redo."""
        doc = self._build_document(ref, pdx_parse(text))
        try:
            self._doc_cache[ref.path] = (ref.path.stat().st_mtime, doc)
        except OSError:
            self._doc_cache[ref.path] = (0.0, doc)
        return doc

    def save(self, doc: TechDocument) -> Path:
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
        self._invalidate_index()
        return path

    def create_doc(self, file_name: str) -> TechDocRef:
        if not file_name.endswith(".txt"):
            file_name += ".txt"
        path = self.ctx.mod.path / self.TECH_DIR / file_name
        if path.exists():
            raise FileExistsError(file_name)
        root = Block([Pair("technologies", Block())])
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, path)
        self._invalidate_index()
        return TechDocRef(rel_file=f"{self.TECH_DIR}/{file_name}",
                          source_root=self.ctx.mod.path, is_vanilla=False)

    def copy_to_mod(self, ref: TechDocRef) -> TechDocRef:
        """Copy a vanilla tech file into the mod (exact filename => engine override)."""
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        self._invalidate_index()
        return TechDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                          is_vanilla=False, edited=True,
                          tech_ids=list(ref.tech_ids))

    def delete(self, ref: TechDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete vanilla files")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(ref.path, None)
        self._invalidate_index()

    def _invalidate_index(self) -> None:
        self._index = None
        self._cache = None

    # --- cross-file index -----------------------------------------------------
    def tech_index(self, refresh: bool = False) -> dict[str, TechDocRef]:
        """tech id -> the doc ref that defines it (highest layer wins)."""
        if self._index is not None and not refresh:
            return self._index
        index: dict[str, TechDocRef] = {}
        for ref in self.list_docs(include_vanilla=True):
            for tid in ref.tech_ids:
                index.setdefault(tid, ref)
        self._index = index
        return index

    def find_tech(self, tid: str) -> tuple[TechDocRef, Technology] | None:
        """Locate and load the defining document of a tech."""
        ref = self.tech_index().get(tid)
        if ref is None:
            return None
        tech = self.load(ref).find(tid)
        return (ref, tech) if tech is not None else None

    def techs_in_folder(self, folder_id: str,
                        include_vanilla: bool = True) -> list[tuple[TechDocRef, Technology]]:
        out: list[tuple[TechDocRef, Technology]] = []
        for ref in self.list_docs(include_vanilla=include_vanilla):
            try:
                doc = self.load(ref)
            except Exception:
                continue
            for tech in doc.techs:
                if tech.folder_in(folder_id) is not None:
                    out.append((ref, tech))
        return out

    # --- tech CRUD ----------------------------------------------------------
    def add_tech(self, doc: TechDocument, tid: str, *,
                 folder: str | None = None, cell: tuple[int, int] = (0, 0),
                 doctrine: bool = False) -> Technology:
        if not _ID_RE.match(tid):
            raise ValueError(f"Invalid technology id: {tid!r}")
        if tid in self.tech_index():
            raise ValueError(f"Duplicate technology id: {tid}")
        techs_block = doc.techs_block
        if techs_block is None:
            techs_block = Block()
            doc.root.add("technologies", techs_block)
        block = Block()
        pair = Pair(tid, block)
        techs_block.items.append(pair)
        tech = Technology(pair)
        tech.research_cost = 1
        tech.start_year = 1936
        if doctrine:
            tech.doctrine = True
            tech.xp_unlock_cost = 100
        if folder:
            tech.add_folder(folder, *cell)
        doc.techs.append(tech)
        self._invalidate_index()
        return tech

    def duplicate_tech(self, doc: TechDocument, tech: Technology,
                       new_id: str) -> Technology:
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid technology id: {new_id!r}")
        if new_id in self.tech_index() or doc.find(new_id) is not None:
            raise ValueError(f"Duplicate technology id: {new_id}")
        copy_root = pdx_parse(dumps(Block([Pair(tech.id, tech.block)])))
        pair = next(p for p in copy_root.pairs())
        pair.key = new_id
        dup = Technology(pair)
        for fa in dup.folders:                 # place next to the original
            x, y = fa.cell(doc.consts)
            fa.set_cell(x, y + 2, doc)
        techs_block = doc.techs_block
        techs_block.items.append(pair)
        doc.techs.append(dup)
        self._invalidate_index()
        return dup

    def paste_tech(self, doc: TechDocument, pair: Pair, *,
                   folder: str | None = None) -> Technology:
        """Insert a copied ``<id> = {...}`` pair under a fresh unique id.
        When `folder` is given the tech is (re)assigned to it: an existing
        assignment shifts two rows down (paste next to the source), a missing
        one is created at the origin."""
        taken = set(self.tech_index())
        new_id = pair.key if pair.key not in taken else pair.key + "_copy"
        while new_id in taken or doc.find(new_id) is not None:
            new_id += "_copy"
        copy_root = pdx_parse(dumps(Block([pair])))
        copy_pair = next(p for p in copy_root.pairs())
        copy_pair.key = new_id
        tech = Technology(copy_pair)
        if folder:
            fa = tech.folder_in(folder)
            if fa is None:
                tech.add_folder(folder, 0, 0)
            else:
                x, y = fa.cell(doc.consts)
                fa.set_cell(x, y + 2, doc)
        techs_block = doc.techs_block
        if techs_block is None:
            techs_block = Block()
            doc.root.add("technologies", techs_block)
        techs_block.items.append(copy_pair)
        doc.techs.append(tech)
        self._invalidate_index()
        return tech

    def remove_tech(self, doc: TechDocument, tech: Technology) -> list[TechDocument]:
        """Delete a tech and scrub references to it in every editable document.
        Returns the list of *other* documents that were modified."""
        tid = tech.id
        techs_block = doc.techs_block
        if techs_block is not None:
            techs_block.items = [it for it in techs_block.items
                                 if not (isinstance(it, Pair) and it is tech.pair)]
        if tech in doc.techs:
            doc.techs.remove(tech)
        touched = self._scrub_references(doc, tid, None)
        self._invalidate_index()
        return touched

    def rename_tech(self, doc: TechDocument, tech: Technology, new_id: str) -> str:
        """Rename and rewrite references in every editable document.
        Returns the old id."""
        old = tech.id
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid technology id: {new_id!r}")
        if new_id in self.tech_index() or doc.find(new_id) is not None:
            raise ValueError(f"Duplicate technology id: {new_id}")
        tech.id = new_id
        self._scrub_references(doc, old, new_id)
        self._invalidate_index()
        return old

    def _scrub_references(self, primary: TechDocument, old: str,
                          new: str | None) -> list[TechDocument]:
        """Replace (or drop when `new` is None) references to `old` in the primary
        document and every other *editable* (non-vanilla) document."""
        for tech in primary.techs:
            if new is None:
                tech.drop_reference(old)
            else:
                tech.replace_reference(old, new)
        touched: list[TechDocument] = []
        for ref in self.list_docs(include_vanilla=False):
            if ref.path == primary.ref.path:
                continue
            try:
                other = self.load(ref)
            except Exception:
                continue
            if not any(t.references(old) for t in other.techs):
                continue
            for tech in other.techs:
                if new is None:
                    tech.drop_reference(old)
                else:
                    tech.replace_reference(old, new)
            touched.append(other)
        return touched

    # --- technology_tags -------------------------------------------------------
    ANKA_TAGS_FILE = "anka_technology.txt"

    def _tags(self) -> tuple[list[str], dict[str, TechFolderDef]]:
        if self._tags_cache is not None:
            return self._tags_cache
        categories: list[str] = []
        seen_cat: set[str] = set()
        folders: dict[str, TechFolderDef] = {}
        for root in self.ctx.override_roots(self.TAGS_DIR):
            folder = root / self.TAGS_DIR
            if not folder.is_dir():
                continue
            is_vanilla = root == self.ctx.game_path
            for file in sorted(folder.glob("*.txt")):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                cats = block.get_block_ci("technology_categories")
                if cats is not None:
                    for name in cats.array_values():
                        if name not in seen_cat:
                            seen_cat.add(name)
                            categories.append(name)
                fol = block.get_block_ci("technology_folders")
                if fol is not None:
                    for pair in fol.pairs():
                        if not isinstance(pair.value, Block):
                            continue
                        avail = pair.value.get_block_ci("available")
                        doct = pair.value.get_ci("doctrine")
                        folders[pair.key] = TechFolderDef(
                            id=pair.key,
                            ledger=pair.value.get_scalar_ci("ledger", "") or "",
                            doctrine=(doct.as_bool()
                                      if isinstance(doct, Scalar) else False),
                            available=(dumps(avail, top_level=False)
                                       if avail is not None else ""),
                            is_vanilla=is_vanilla,
                            block=pair.value,
                            source=file)
        self._tags_cache = (categories, folders)
        return self._tags_cache

    def categories(self, refresh: bool = False) -> list[str]:
        if refresh:
            self._tags_cache = None
        return self._tags()[0]

    def folders(self, refresh: bool = False) -> dict[str, TechFolderDef]:
        if refresh:
            self._tags_cache = None
        return self._tags()[1]

    def _mod_tags_root(self) -> tuple[Path, Block]:
        path = self.ctx.mod.path / self.TAGS_DIR / self.ANKA_TAGS_FILE
        if path.exists():
            return path, parse_file(path)
        return path, Block()

    def add_category(self, name: str) -> None:
        if not _ID_RE.match(name):
            raise ValueError(f"Invalid category name: {name!r}")
        if name in self.categories():
            return
        path, root = self._mod_tags_root()
        cats = root.get_block_ci("technology_categories")
        if cats is None:
            cats = Block()
            root.add("technology_categories", cats)
        cats.add_value(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, path)
        self._tags_cache = None

    def add_folder(self, folder_id: str, *, ledger: str = "army",
                   doctrine: bool = False, available: str = "") -> None:
        if not _ID_RE.match(folder_id):
            raise ValueError(f"Invalid folder id: {folder_id!r}")
        if folder_id in self.folders():
            raise ValueError(f"Duplicate folder id: {folder_id}")
        path, root = self._mod_tags_root()
        fol = root.get_block_ci("technology_folders")
        if fol is None:
            fol = Block()
            root.add("technology_folders", fol)
        blk = Block()
        blk.set("ledger", ledger)
        if doctrine:
            blk.set("doctrine", True)
        if available.strip():
            parsed = pdx_parse(available, recover=False)
            blk.set("available", Block(parsed.items))
        fol.add(folder_id, blk)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, path)
        self._tags_cache = None

    def remove_folder(self, folder_id: str) -> None:
        """Delete a mod-side folder definition from its technology_tags file."""
        fdef = self.folders().get(folder_id)
        if fdef is None:
            raise KeyError(folder_id)
        if fdef.is_vanilla or fdef.source is None:
            raise PermissionError("Folder is defined in a read-only layer")
        root = parse_file(fdef.source)
        fol = root.get_block_ci("technology_folders")
        if fol is not None:
            fol.items = [
                it for it in fol.items
                if not (isinstance(it, Pair)
                        and it.key.lower() == folder_id.lower())]
        dump_file(root, fdef.source)
        self._tags_cache = None

    def remove_folder_assignments(self, folder_id: str) -> list[TechDocument]:
        """Strip ``folder = { name = <folder_id> ... }`` from every tech in
        every editable document. Returns the touched documents."""
        touched: list[TechDocument] = []
        for ref in self.list_docs(include_vanilla=False):
            try:
                doc = self.load(ref)
            except Exception:
                continue
            changed = False
            for tech in doc.techs:
                if tech.folder_in(folder_id) is not None:
                    tech.remove_folder(folder_id)
                    changed = True
            if changed:
                touched.append(doc)
        return touched

    def update_folder(self, folder_id: str, *, ledger: str | None = None,
                      doctrine: bool | None = None,
                      available: str | None = None) -> None:
        """Edit a folder definition in place. Only mod-side (non-vanilla)
        definitions are writable; the file is re-parsed so on-disk formatting
        of the untouched parts survives."""
        fdef = self.folders().get(folder_id)
        if fdef is None:
            raise KeyError(folder_id)
        if fdef.is_vanilla or fdef.source is None:
            raise PermissionError("Folder is defined in a read-only layer")
        root = parse_file(fdef.source)
        fol = root.get_block_ci("technology_folders")
        blk = fol.get_block_ci(folder_id) if fol is not None else None
        if blk is None:
            raise KeyError(folder_id)
        if ledger is not None:
            blk.set_ci("ledger", ledger)
        if doctrine is not None:
            if doctrine:
                blk.set_ci("doctrine", True)
            else:
                blk.remove_ci("doctrine")
        if available is not None:
            if available.strip():
                parsed = pdx_parse(available, recover=False)
                blk.set_ci("available", Block(parsed.items))
            else:
                blk.remove_ci("available")
        dump_file(root, fdef.source)
        self._tags_cache = None

    # --- ai_focuses hint --------------------------------------------------------
    def ai_focus_categories(self, refresh: bool = False) -> set[str]:
        """Categories referenced by any ``common/ai_focuses`` research block —
        a tech whose categories never appear here is ignored by AI research."""
        if self._ai_categories is not None and not refresh:
            return self._ai_categories
        found: set[str] = set()
        for root in self.ctx.override_roots(GAME_DIRS.AI_FOCUSES):
            folder = root / GAME_DIRS.AI_FOCUSES
            if not folder.is_dir():
                continue
            for file in folder.glob("*.txt"):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                for pair in block.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    research = pair.value.get_block_ci("research")
                    if research is None:
                        continue
                    found.update(p.key for p in research.pairs())
        self._ai_categories = found
        return found

    # --- localisation -----------------------------------------------------------
    def languages(self) -> list[str]:
        """Languages present in the mod's localisation dir, ordered like HOI4's
        canonical list; falls back to english when the mod has none yet."""
        present: set[str] = set()
        for root in (self.ctx.mod.path, *self.ctx.dependency_paths):
            loc_dir = root / GAME_DIRS.LOCALISATION
            if not loc_dir.is_dir():
                continue
            for child in loc_dir.iterdir():
                if child.is_dir() and child.name in HOI4_LANGUAGES:
                    present.add(child.name)
        out = [lang for lang in HOI4_LANGUAGES if lang in present]
        return out or ["english"]

    def tech_name(self, tid: str, language: str,
                  tech: "Technology | None" = None) -> str:
        """Display name for a tech. Many vanilla techs (``infantry_weapons1``,
        ``improved_infantry_weapons``, …) define no loc key of their own — the game
        shows the name of the equipment they unlock, so fall back to that before
        giving up and showing the raw id."""
        text = self._loc.get(tid, language)
        if text is None and language != "english":
            text = self._loc.get(tid, "english")
        if text is None and tech is not None:
            text = self._unlocked_name(tech, language)
        return text if text is not None else tid

    def _unlocked_name(self, tech: "Technology", language: str) -> str | None:
        for key in (*tech.enable_equipments, *tech.enable_equipment_modules):
            text = self._loc.get(key, language)
            if text is None and language != "english":
                text = self._loc.get(key, "english")
            if text:
                return text
        return None

    def tech_desc(self, tid: str, language: str) -> str:
        text = self._loc.get(f"{tid}_desc", language)
        if text is None and language != "english":
            text = self._loc.get(f"{tid}_desc", "english")
        return text or ""

    def has_name(self, tid: str, language: str) -> bool:
        return self._loc.has(tid, language)

    def has_any_name(self, tid: str, preferred: str = "english") -> bool:
        order = [preferred, "english"] + [l for l in HOI4_LANGUAGES
                                          if l not in (preferred, "english")]
        return any(self._loc.has(tid, lang) for lang in order)

    def set_tech_loc(self, tid: str, language: str,
                     name: str | None, desc: str | None) -> None:
        if name is not None:
            self._loc.set(tid, language, name)
        if desc is not None:
            self._loc.set(f"{tid}_desc", language, desc)

    def rename_tech_loc(self, old: str, new: str) -> None:
        self._loc.rename(old, new)

    def loc_get(self, key: str, language: str) -> str | None:
        return self._loc.get(key, language)

    def loc_set(self, key: str, language: str, value: str) -> Path:
        return self._loc.set(key, language, value)

    # --- validation ---------------------------------------------------------
    def validate(self, techs: list[tuple[TechDocRef, Technology]], *,
                 language: str | None = None,
                 sprite_exists=None) -> list[Issue]:
        """Data-level validation of the given techs (usually one folder's worth).
        GUI-level rules (gridboxes, collisions) are added by the editor, which
        owns the layout. Loc/icon checks are skipped for vanilla-defined techs."""
        issues: list[Issue] = []
        index = self.tech_index()
        known_cats = set(self.categories())
        ai_cats = self.ai_focus_categories()
        folder_defs = self.folders()
        in_set = {t.id: t for _r, t in techs}

        for ref, tech in techs:
            tid = tech.id
            if not _ID_RE.match(tid or ""):
                issues.append(Issue("error", "bad_id", tid))
                continue

            for path in tech.paths:
                target = path.leads_to_tech
                if not target:
                    # vanilla ships empty path{} blocks (motorised_infantry) —
                    # the game ignores them, so this is only a warning
                    issues.append(Issue("warning", "empty_path", tid))
                elif target not in index:
                    issues.append(Issue("warning", "broken_path", tid, target))
            for dep in tech.dependencies:
                if dep not in index:
                    issues.append(Issue("warning", "broken_dependency", tid, dep))
            for other in tech.xor:
                if other not in index:
                    issues.append(Issue("warning", "broken_xor", tid, other))
                else:
                    twin = in_set.get(other)
                    if twin is None:
                        located = self.find_tech(other)
                        twin = located[1] if located else None
                    if twin is not None and tid not in twin.xor:
                        issues.append(Issue("warning", "one_way_xor", tid, other))
            for sub in tech.sub_technologies:
                if sub not in index:
                    issues.append(Issue("warning", "broken_subtech", tid, sub))
            if len(tech.sub_technologies) > MAX_SUBTECHS:
                issues.append(Issue("error", "too_many_subtechs", tid,
                                    str(len(tech.sub_technologies))))

            doc = self.load(ref)
            for fa in tech.folders:
                if fa.name not in folder_defs:
                    issues.append(Issue("error", "unknown_folder", tid, fa.name))
                for raw in (fa.raw_x, fa.raw_y):
                    if raw and raw.strip().startswith("@") \
                            and raw.strip() not in doc.consts:
                        issues.append(Issue("error", "unknown_const", tid, raw))
                if fa.name in folder_defs:
                    fdef = folder_defs[fa.name]
                    if tech.doctrine and not fdef.doctrine:
                        issues.append(Issue("warning", "doctrine_in_tech_folder",
                                            tid, fa.name))
                    elif not tech.doctrine and fdef.doctrine:
                        issues.append(Issue("warning", "tech_in_doctrine_folder",
                                            tid, fa.name))

            cats = tech.categories
            for cat in cats:
                if cat not in known_cats:
                    issues.append(Issue("error", "unknown_category", tid, cat))
            if cats and ai_cats and not any(c in ai_cats for c in cats):
                issues.append(Issue("info", "category_no_ai", tid,
                                    ", ".join(cats[:3])))

            if not ref.is_vanilla:
                if language and not self.has_any_name(tid, preferred=language):
                    issues.append(Issue("warning", "missing_loc", tid))
                if sprite_exists is not None and not tech.enable_equipments \
                        and not sprite_exists(f"GFX_{tid}_medium"):
                    issues.append(Issue("warning", "missing_icon", tid,
                                        f"GFX_{tid}_medium"))

        # path cycles among the given techs (DFS)
        graph = {tid: {p.leads_to_tech for p in t.paths if p.leads_to_tech in in_set}
                 for tid, t in in_set.items()}
        state: dict[str, int] = {}

        def dfs(node: str) -> None:
            state[node] = 1
            for nxt in graph.get(node, ()):
                if state.get(nxt) == 1:
                    issues.append(Issue("error", "path_cycle", node, nxt))
                elif state.get(nxt, 0) == 0:
                    dfs(nxt)
            state[node] = 2

        for tid in graph:
            if state.get(tid, 0) == 0:
                dfs(tid)
        return issues
