"""Read/write HOI4 equipment for the Technologies editor's Equipment tab.

Sources (mod first, base game as fallback): ``common/units/equipment/*.txt`` —
each file is one ``equipments = { <id> = {...} }`` block. The ``modules/`` and
``upgrades/`` subfolders are separate content types (``equipment_modules`` /
``equipment_upgrades``) and are out of scope here — the non-recursive scan never
sees them.

The model is *block-backed* like `technology_service`: `Equipment` is a thin
view over the parsed PDX `Block`, so every key ANKA does not know about survives
a load/save round-trip. Equipment splits into archetypes (``is_archetype = yes``)
and concrete entries inheriting stats via ``archetype =`` (and superseding an
older model via ``parent =``); an absent stat on a concrete entry means
"inherited", so setters remove the key instead of writing defaults.
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
from .technology_service import Issue

# Free-form script blocks edited as validated PDX text (single occurrence).
EQUIP_SCRIPT_FIELDS = ("can_be_produced", "can_convert_from", "module_slots")

# Boolean flags that are tri-state in script: absent = engine default for
# archetypes, "inherited" for concrete equipment — so absence is meaningful
# and the UI offers unset / yes / no.
EQUIP_FLAG_FIELDS = ("active", "is_buildable", "is_convertable",
                     "one_use_only", "carrier_capable")

# Suggestion seed for the inspector's "add stat" combobox; purely UI data —
# unknown scalars still round-trip and render in the generic stats grid.
EQUIP_STATS = (
    # land / common
    "build_cost_ic", "lend_lease_cost", "manpower", "reliability",
    "maximum_speed", "defense", "breakthrough", "hardness", "armor_value",
    "soft_attack", "hard_attack", "ap_attack", "air_attack", "entrenchment",
    "recon", "max_strength", "max_organisation", "default_morale",
    "fuel_capacity", "fuel_consumption", "supply_consumption",
    # naval
    "naval_speed", "naval_range", "lg_attack", "lg_armor_piercing",
    "hg_attack", "hg_armor_piercing", "torpedo_attack", "anti_air_attack",
    "shore_bombardment", "sub_attack", "sub_detection", "sub_visibility",
    "surface_detection", "surface_visibility", "port_capacity_usage",
    "search_and_destroy_coordination", "convoy_raiding_coordination",
    # air
    "air_superiority", "air_defence", "air_range", "air_agility",
    "air_ground_attack", "air_bombing", "naval_strike_attack",
    "naval_strike_targetting", "thrust", "weight",
)

RESOURCE_NAMES = ("steel", "aluminium", "tungsten", "chromium", "rubber", "oil")

# Keys with editor-specific handling (a dedicated widget in the inspector);
# every other scalar on an equipment block lands in the generic stats grid,
# every other block in the generic script rows.
_STRUCT_SCALARS = frozenset({
    "year", "archetype", "parent", "picture", "type", "group_by",
    "interface_category", "priority", "visual_level", "is_archetype",
    *EQUIP_FLAG_FIELDS,
})
# ``type`` is scalar-or-block, hence present in both sets.
_STRUCT_BLOCKS = frozenset({
    "type", "resources", "module_slots", "module_count_limit",
    "can_be_produced", "can_convert_from",
})

ANKA_EQUIPMENT_FILE = "anka_equipment.txt"

_ID_RE = re.compile(r"^\w+$")
_NAME_TOKEN_RE = re.compile(r"([A-Za-z0-9_.@\-]+)\s*=\s*\{|\{|\}")


class Equipment:
    """A view over one ``<id> = { ... }`` block inside ``equipments``.

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

    # --- scalar helpers -------------------------------------------------------
    def _get_str(self, key: str) -> str | None:
        return self.block.get_scalar_ci(key)

    def _set_str(self, key: str, value: str | None) -> None:
        if value:
            self.block.set_ci(key, value)
        else:
            self.block.remove_ci(key)

    def _get_int(self, key: str) -> int | None:
        v = self.block.get_ci(key)
        return v.as_int() if isinstance(v, Scalar) else None

    def _set_int(self, key: str, value: int | None) -> None:
        if value is None:
            self.block.remove_ci(key)
        else:
            self.block.set_ci(key, int(value))

    # --- structural fields ------------------------------------------------------
    @property
    def year(self) -> int | None:
        return self._get_int("year")

    @year.setter
    def year(self, value: int | None) -> None:
        self._set_int("year", value)

    @property
    def priority(self) -> int | None:
        return self._get_int("priority")

    @priority.setter
    def priority(self, value: int | None) -> None:
        self._set_int("priority", value)

    @property
    def visual_level(self) -> int | None:
        return self._get_int("visual_level")

    @visual_level.setter
    def visual_level(self, value: int | None) -> None:
        self._set_int("visual_level", value)

    @property
    def picture(self) -> str | None:
        return self._get_str("picture")

    @picture.setter
    def picture(self, value: str | None) -> None:
        self._set_str("picture", value)

    @property
    def group_by(self) -> str | None:
        return self._get_str("group_by")

    @group_by.setter
    def group_by(self, value: str | None) -> None:
        self._set_str("group_by", value)

    @property
    def interface_category(self) -> str | None:
        return self._get_str("interface_category")

    @interface_category.setter
    def interface_category(self, value: str | None) -> None:
        self._set_str("interface_category", value)

    @property
    def archetype(self) -> str | None:
        return self._get_str("archetype")

    @archetype.setter
    def archetype(self, value: str | None) -> None:
        self._set_str("archetype", value)

    @property
    def parent(self) -> str | None:
        return self._get_str("parent")

    @parent.setter
    def parent(self, value: str | None) -> None:
        self._set_str("parent", value)

    @property
    def is_archetype(self) -> bool:
        v = self.block.get_ci("is_archetype")
        return v.as_bool() if isinstance(v, Scalar) else False

    @is_archetype.setter
    def is_archetype(self, value: bool) -> None:
        if value:
            self.block.set_ci("is_archetype", True)
        else:
            self.block.remove_ci("is_archetype")

    # --- tri-state flags --------------------------------------------------------
    def get_opt_flag(self, name: str) -> bool | None:
        """None = key absent = engine default (archetype) / inherited (concrete)."""
        v = self.block.get_ci(name)
        return v.as_bool() if isinstance(v, Scalar) else None

    def set_opt_flag(self, name: str, value: bool | None) -> None:
        if value is None:
            self.block.remove_ci(name)
        else:
            self.block.set_ci(name, value)

    # --- type: scalar or array block ---------------------------------------------
    @property
    def types(self) -> list[str]:
        v = self.block.get_ci("type")
        if isinstance(v, Scalar):
            return [v.raw]
        if isinstance(v, Block):
            return v.array_values()
        return []

    @types.setter
    def types(self, values: list[str]) -> None:
        values = [v for v in values if v]
        if not values:
            self.block.remove_ci("type")
        elif len(values) == 1:
            self.block.set_ci("type", values[0])
        else:
            blk = Block()
            for v in values:
                blk.add_value(v)
            self.block.set_ci("type", blk)

    # --- resources ---------------------------------------------------------------
    @property
    def resources(self) -> dict[str, int]:
        blk = self.block.get_block_ci("resources")
        if blk is None:
            return {}
        out: dict[str, int] = {}
        for pair in blk.pairs():
            if isinstance(pair.value, Scalar):
                out[pair.key] = pair.value.as_int(1) or 1
        return out

    @resources.setter
    def resources(self, values: dict[str, int]) -> None:
        if not values:
            self.block.remove_ci("resources")
            return
        blk = Block()
        for name, amount in values.items():
            blk.set(name, int(amount))
        self.block.set_ci("resources", blk)

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

    def get_repeated_script(self, key: str) -> str:
        """All ``<key> = {...}`` occurrences serialized as full statements —
        ``module_count_limit`` legitimately repeats, so it is edited as one text."""
        pairs = [it for it in self.block.items
                 if isinstance(it, Pair) and it.key.lower() == key.lower()]
        return "\n".join(dumps(Block([p]), top_level=True).rstrip("\n")
                         for p in pairs)

    def set_repeated_script(self, key: str, text: str) -> None:
        """Replace every occurrence of `key` with the statements in `text`
        (each top-level statement must be ``<key> = {...}``); raises on bad
        script. Splices at the position of the first old occurrence so the
        block keeps its shape."""
        new_pairs: list[Pair] = []
        if text.strip():
            parsed = pdx_parse(text, recover=False)
            for it in parsed.items:
                if not (isinstance(it, Pair) and it.key.lower() == key.lower()):
                    raise ValueError(
                        f"Every top-level statement must be {key} = {{...}}")
                new_pairs.append(it)
        index = next((i for i, it in enumerate(self.block.items)
                      if isinstance(it, Pair) and it.key.lower() == key.lower()),
                     None)
        self.block.items = [it for it in self.block.items
                            if not (isinstance(it, Pair)
                                    and it.key.lower() == key.lower())]
        if index is None:
            self.block.items.extend(new_pairs)
        else:
            self.block.items[index:index] = new_pairs

    # --- free-form remainder ---------------------------------------------------
    def stat_pairs(self) -> list[Pair]:
        """Flat stat pairs (``soft_attack = 3`` etc.) — every scalar pair that is
        not a structural field. Unknown/DLC stats survive untouched."""
        return [p for p in self.block.pairs()
                if isinstance(p.value, Scalar)
                and p.key.lower() not in _STRUCT_SCALARS
                and not p.key.startswith("@")]

    def extra_block_pairs(self) -> list[Pair]:
        """Block pairs without a dedicated widget (``upgrades``,
        ``allow_mission_type``, future DLC keys) — rendered as generic script rows."""
        return [p for p in self.block.pairs()
                if isinstance(p.value, Block)
                and p.key.lower() not in _STRUCT_BLOCKS]

    # --- references -----------------------------------------------------------
    def references(self, eid: str) -> bool:
        if self.archetype == eid or self.parent == eid:
            return True
        blk = self.block.get_block_ci("can_convert_from")
        return blk is not None and self._block_mentions(blk, eid)

    @classmethod
    def _block_mentions(cls, blk: Block, eid: str) -> bool:
        for it in blk.items:
            if isinstance(it, Scalar) and it.raw == eid:
                return True
            if isinstance(it, Block) and cls._block_mentions(it, eid):
                return True
            if isinstance(it, Pair):
                if isinstance(it.value, Scalar) and it.value.raw == eid:
                    return True
                if isinstance(it.value, Block) \
                        and cls._block_mentions(it.value, eid):
                    return True
        return False

    @classmethod
    def _block_replace(cls, blk: Block, old: str, new: str) -> None:
        for it in blk.items:
            if isinstance(it, Scalar) and it.raw == old:
                it.raw = new
            elif isinstance(it, Block):
                cls._block_replace(it, old, new)
            elif isinstance(it, Pair):
                if isinstance(it.value, Scalar) and it.value.raw == old:
                    it.value.raw = new
                elif isinstance(it.value, Block):
                    cls._block_replace(it.value, old, new)

    def replace_reference(self, old: str, new: str) -> None:
        if self.archetype == old:
            self.archetype = new
        if self.parent == old:
            self.parent = new
        blk = self.block.get_block_ci("can_convert_from")
        if blk is not None:
            self._block_replace(blk, old, new)

    def drop_reference(self, eid: str) -> None:
        if self.archetype == eid:
            self.archetype = None
        if self.parent == eid:
            self.parent = None

    def __repr__(self) -> str:
        return f"Equipment({self.id!r})"


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class EquipmentDocRef:
    rel_file: str                # e.g. "common/units/equipment/infantry.txt"
    source_root: Path
    is_vanilla: bool
    edited: bool = False         # vanilla file that the mod overrides
    equipment_ids: list[str] = field(default_factory=list)   # cheap-scan preview

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class EquipmentDocument:
    """One parsed ``common/units/equipment`` file, ready for editing."""

    ref: EquipmentDocRef
    root: Block
    equipments: list[Equipment] = field(default_factory=list)

    @property
    def equipments_block(self) -> Block | None:
        return self.root.get_block_ci("equipments")

    def find(self, eid: str) -> Equipment | None:
        for e in self.equipments:
            if e.id == eid:
                return e
        return None


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class EquipmentService:
    EQUIP_DIR = GAME_DIRS.EQUIPMENT

    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, EquipmentDocument]] = {}
        self._index: dict[str, EquipmentDocRef] | None = None
        self._archetypes: list[str] | None = None
        self._loc = LocCatalog(
            context.mod.path, context.game_path,
            vanilla_filter=("equipment",),
            default_pattern="anka_equipment_l_{lang}.yml",
            dep_roots=context.dependency_paths)

    # --- listing -----------------------------------------------------------
    def list_docs(self, include_vanilla: bool = False) -> list[EquipmentDocRef]:
        return layered_docs(
            self.ctx, self.EQUIP_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.name.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[EquipmentDocRef]:
        folder = root / self.EQUIP_DIR
        if not folder.is_dir():
            return []
        refs: list[EquipmentDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            refs.append(EquipmentDocRef(
                rel_file=f"{self.EQUIP_DIR}/{file.name}", source_root=root,
                is_vanilla=is_vanilla,
                equipment_ids=self._scan_equipment_ids(text)))
        return refs

    @staticmethod
    def _scan_equipment_ids(text: str) -> list[str]:
        """Cheap brace-depth scan: names of blocks directly inside ``equipments``."""
        text = re.sub(r"#[^\n]*", "", text)
        ids: list[str] = []
        stack: list[str] = []
        for m in _NAME_TOKEN_RE.finditer(text):
            name = m.group(1)
            if name is not None:
                if len(stack) == 1 and stack[0].lower() == "equipments" \
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
    def load(self, ref: EquipmentDocRef) -> EquipmentDocument:
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
    def _build_document(ref: EquipmentDocRef, root: Block) -> EquipmentDocument:
        doc = EquipmentDocument(ref=ref, root=root)
        blk = doc.equipments_block
        if blk is not None:
            doc.equipments = [Equipment(p) for p in blk.pairs()
                              if isinstance(p.value, Block)
                              and not p.key.startswith("@")]
        return doc

    def document_from_text(self, ref: EquipmentDocRef,
                           text: str) -> EquipmentDocument:
        """Rebuild a document from serialized text — used by undo/redo."""
        doc = self._build_document(ref, pdx_parse(text))
        try:
            self._doc_cache[ref.path] = (ref.path.stat().st_mtime, doc)
        except OSError:
            self._doc_cache[ref.path] = (0.0, doc)
        return doc

    def save(self, doc: EquipmentDocument) -> Path:
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

    def create_doc(self, file_name: str) -> EquipmentDocRef:
        if not file_name.endswith(".txt"):
            file_name += ".txt"
        path = self.ctx.mod.path / self.EQUIP_DIR / file_name
        if path.exists():
            raise FileExistsError(file_name)
        root = Block([Pair("equipments", Block())])
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, path)
        self._invalidate_index()
        return EquipmentDocRef(rel_file=f"{self.EQUIP_DIR}/{file_name}",
                               source_root=self.ctx.mod.path, is_vanilla=False)

    def copy_to_mod(self, ref: EquipmentDocRef) -> EquipmentDocRef:
        """Copy a vanilla equipment file into the mod (exact filename => engine
        override). Bytes are copied verbatim so vanilla comments survive until
        the first real edit."""
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        self._invalidate_index()
        return EquipmentDocRef(rel_file=ref.rel_file,
                               source_root=self.ctx.mod.path,
                               is_vanilla=False, edited=True,
                               equipment_ids=list(ref.equipment_ids))

    def delete(self, ref: EquipmentDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete vanilla files")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(ref.path, None)
        self._invalidate_index()

    def _invalidate_index(self) -> None:
        self._index = None
        self._archetypes = None

    # --- cross-file index -----------------------------------------------------
    def equipment_index(self, refresh: bool = False) -> dict[str, EquipmentDocRef]:
        """equipment id -> the doc ref that defines it (highest layer wins)."""
        if self._index is not None and not refresh:
            return self._index
        index: dict[str, EquipmentDocRef] = {}
        for ref in self.list_docs(include_vanilla=True):
            for eid in ref.equipment_ids:
                index.setdefault(eid, ref)
        self._index = index
        return index

    def find_equipment(self, eid: str) -> tuple[EquipmentDocRef, Equipment] | None:
        """Locate and load the defining document of an equipment entry."""
        ref = self.equipment_index().get(eid)
        if ref is None:
            return None
        eq = self.load(ref).find(eid)
        return (ref, eq) if eq is not None else None

    def archetypes(self, refresh: bool = False) -> list[str]:
        """Every archetype id across the content layers (cached)."""
        if self._archetypes is not None and not refresh:
            return self._archetypes
        out: list[str] = []
        for ref in self.list_docs(include_vanilla=True):
            try:
                doc = self.load(ref)
            except Exception:
                continue
            for eq in doc.equipments:
                if eq.is_archetype and eq.id not in out:
                    out.append(eq.id)
        out.sort()
        self._archetypes = out
        return out

    def concrete_ids_of(self, archetype: str) -> list[str]:
        """Concrete equipment ids belonging to `archetype` — parent options."""
        out: list[str] = []
        for ref in self.list_docs(include_vanilla=True):
            try:
                doc = self.load(ref)
            except Exception:
                continue
            for eq in doc.equipments:
                if eq.archetype == archetype and eq.id not in out:
                    out.append(eq.id)
        out.sort()
        return out

    # --- inheritance ----------------------------------------------------------
    def archetype_of(self, eq: Equipment) -> Equipment | None:
        aid = eq.archetype
        if not aid:
            return None
        found = self.find_equipment(aid)
        return found[1] if found else None

    def inherited_stats(self, eq: Equipment) -> dict[str, str]:
        """Archetype stat values the entry does not override locally —
        the inspector shows these greyed-out as inherited placeholders."""
        arch = self.archetype_of(eq)
        if arch is None:
            return {}
        local = {p.key.lower() for p in eq.stat_pairs()}
        return {p.key: p.value.raw for p in arch.stat_pairs()
                if p.key.lower() not in local}

    def inherited_value(self, eq: Equipment, key: str) -> str | None:
        """The archetype's value for a structural scalar `key` (one hop)."""
        arch = self.archetype_of(eq)
        if arch is None:
            return None
        v = arch.block.get_ci(key)
        return v.raw if isinstance(v, Scalar) else None

    # --- equipment CRUD ----------------------------------------------------------
    def add_equipment(self, doc: EquipmentDocument, eid: str, *,
                      archetype: str | None = None) -> Equipment:
        """Append a new entry: an archetype skeleton when `archetype` is None,
        else a concrete entry inheriting from `archetype`."""
        if not _ID_RE.match(eid):
            raise ValueError(f"Invalid equipment id: {eid!r}")
        if eid in self.equipment_index():
            raise ValueError(f"Duplicate equipment id: {eid}")
        blk_root = doc.equipments_block
        if blk_root is None:
            blk_root = Block()
            doc.root.add("equipments", blk_root)
        block = Block()
        pair = Pair(eid, block)
        blk_root.items.append(pair)
        eq = Equipment(pair)
        eq.year = 1936
        if archetype is None:
            eq.is_archetype = True
            eq.set_opt_flag("is_buildable", False)
            eq.types = ["infantry"]
            eq.group_by = "archetype"
            eq.interface_category = "interface_category_land"
        else:
            eq.archetype = archetype
            eq.set_opt_flag("active", True)
            eq.priority = 10
            eq.visual_level = 0
        doc.equipments.append(eq)
        self._invalidate_index()
        return eq

    def copy_stats_from(self, eq: Equipment, source_id: str) -> bool:
        """Copy the stat pairs, resources and types of `source_id` onto a
        (freshly created) entry. Identity/structure keys stay untouched."""
        found = self.find_equipment(source_id)
        if found is None:
            return False
        src = found[1]
        for pair in src.stat_pairs():
            eq.block.set_ci(pair.key, Scalar(pair.value.raw,
                                             quoted=pair.value.quoted))
        if src.resources:
            eq.resources = dict(src.resources)
        if src.types and not eq.is_archetype:
            eq.types = list(src.types)
        return True

    def duplicate_equipment(self, doc: EquipmentDocument, eq: Equipment,
                            new_id: str) -> Equipment:
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid equipment id: {new_id!r}")
        if new_id in self.equipment_index() or doc.find(new_id) is not None:
            raise ValueError(f"Duplicate equipment id: {new_id}")
        copy_root = pdx_parse(dumps(Block([Pair(eq.id, eq.block)])))
        pair = next(p for p in copy_root.pairs())
        pair.key = new_id
        dup = Equipment(pair)
        doc.equipments_block.items.append(pair)
        doc.equipments.append(dup)
        self._invalidate_index()
        return dup

    def remove_equipment(self, doc: EquipmentDocument,
                         eq: Equipment) -> list[EquipmentDocument]:
        """Delete an entry and scrub references to it in every editable document.
        Returns the list of *other* documents that were modified."""
        eid = eq.id
        blk = doc.equipments_block
        if blk is not None:
            blk.items = [it for it in blk.items
                         if not (isinstance(it, Pair) and it is eq.pair)]
        if eq in doc.equipments:
            doc.equipments.remove(eq)
        touched = self._scrub_references(doc, eid, None)
        self._invalidate_index()
        return touched

    def rename_equipment(self, doc: EquipmentDocument, eq: Equipment,
                         new_id: str) -> str:
        """Rename and rewrite references in every editable equipment document.
        Returns the old id. (Technology ``enable_equipments`` references are the
        caller's concern — the tab warns about them.)"""
        old = eq.id
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid equipment id: {new_id!r}")
        if new_id in self.equipment_index() or doc.find(new_id) is not None:
            raise ValueError(f"Duplicate equipment id: {new_id}")
        eq.id = new_id
        self._scrub_references(doc, old, new_id)
        self._invalidate_index()
        return old

    def _scrub_references(self, primary: EquipmentDocument, old: str,
                          new: str | None) -> list[EquipmentDocument]:
        for eq in primary.equipments:
            if new is None:
                eq.drop_reference(old)
            else:
                eq.replace_reference(old, new)
        touched: list[EquipmentDocument] = []
        for ref in self.list_docs(include_vanilla=False):
            if ref.path == primary.ref.path:
                continue
            try:
                other = self.load(ref)
            except Exception:
                continue
            if not any(e.references(old) for e in other.equipments):
                continue
            for eq in other.equipments:
                if new is None:
                    eq.drop_reference(old)
                else:
                    eq.replace_reference(old, new)
            touched.append(other)
        return touched

    # --- referencing entities (delete/rename safety) ----------------------------
    def referencing_ids(self, eid: str) -> list[str]:
        """Editable equipment entries that reference `eid` (archetype/parent/
        can_convert_from) — shown before deleting an archetype."""
        out: list[str] = []
        for ref in self.list_docs(include_vanilla=False):
            try:
                doc = self.load(ref)
            except Exception:
                continue
            out.extend(e.id for e in doc.equipments
                       if e.id != eid and e.references(eid))
        return out

    # --- localisation -----------------------------------------------------------
    def equipment_name(self, eid: str, language: str) -> str:
        text = self._loc.get(eid, language)
        if text is None and language != "english":
            text = self._loc.get(eid, "english")
        return text if text is not None else eid

    def has_any_name(self, eid: str, preferred: str = "english") -> bool:
        order = [preferred, "english"] + [l for l in HOI4_LANGUAGES
                                          if l not in (preferred, "english")]
        return any(self._loc.has(eid, lang) for lang in order)

    def loc_get(self, key: str, language: str) -> str | None:
        return self._loc.get(key, language)

    def loc_set(self, key: str, language: str, value: str) -> Path:
        return self._loc.set(key, language, value)

    def rename_loc(self, old: str, new: str) -> None:
        self._loc.rename(old, new)                     # <id> and <id>_desc
        self._loc.rename_key(f"{old}_short", f"{new}_short")

    # --- validation ---------------------------------------------------------
    def validate(self, docs: list[EquipmentDocument], *,
                 language: str | None = None) -> list[Issue]:
        """Data-level validation of mod-side documents. `Issue.code` maps to
        ``equipment.issue.<code>`` loc keys."""
        issues: list[Issue] = []
        index = self.equipment_index()
        archetypes = set(self.archetypes())
        for doc in docs:
            for eq in doc.equipments:
                eid = eq.id
                if not _ID_RE.match(eid or ""):
                    issues.append(Issue("error", "bad_id", eid))
                    continue
                if eq.is_archetype:
                    if not eq.types:
                        issues.append(Issue("warning", "archetype_without_type",
                                            eid))
                else:
                    arch = eq.archetype
                    if not arch:
                        issues.append(Issue("warning", "no_archetype", eid))
                    elif arch not in archetypes:
                        issues.append(Issue("error", "unknown_archetype",
                                            eid, arch))
                parent = eq.parent
                if parent and parent not in index:
                    issues.append(Issue("error", "unknown_parent", eid, parent))
                if not doc.ref.is_vanilla and language \
                        and not self.has_any_name(eid, preferred=language):
                    issues.append(Issue("warning", "missing_loc", eid))
        return issues
