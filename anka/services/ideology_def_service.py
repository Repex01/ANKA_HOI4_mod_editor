"""Ideology *definitions* (``common/ideologies``) — full read/write model.

This complements the lightweight `IdeologyService` (which only reads ideology
*groups* for flag-variant / leader-ideology pickers): here every ideology is an
editable document, mirroring `DynamicModifierService`.

File shape: a single ``ideologies = { <name> = { ... } }`` wrapper holding one
block per ideology. Inside an ideology:

* ``color = { R G B }`` — political pie / map colour;
* ``types = { <variant> = { can_be_randomly_selected color } }`` — leader sub-types;
* ``dynamic_faction_names = { "LOC_KEY" ... }`` — faction-name pools;
* ``rules = { ... }`` — governmental capability flags (yes/no);
* ``modifiers = { key = value ... }`` — ideology gameplay modifiers;
* ``faction_modifiers = { ... }`` — modifiers applied to a faction led by it;
* scalar flags/values (``can_be_boosted``, ``war_impact_on_world_tension`` …);
* ``ai_<behaviour> = yes`` — which base AI behaviour the ideology mimics.

Layered `list_docs` (base game → dependencies → mod, with file-override
semantics) gives submod support for free; views mutate the parsed `Block` in
place so unknown keys survive a round-trip untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, Pair, Scalar, dump_file, dumps, parse_file
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs

IDEOLOGY_DIR = GAME_DIRS.IDEOLOGIES
DEFAULT_REL_FILE = f"{IDEOLOGY_DIR}/anka_ideologies.txt"
CONTAINER_KEY = "ideologies"

_KEY_RE = re.compile(r"^([A-Za-z_][\w.]*)[ \t]*=[ \t]*\{")

# The governmental capability flags an ideology's `rules` block may hold.
RULE_KEYS: tuple[str, ...] = (
    "can_create_collaboration_government",
    "can_declare_war_on_same_ideology",
    "can_force_government",
    "can_send_volunteers",
    "can_puppet",
    "can_lower_tension",
    "can_only_justify_war_on_threat_country",
    "can_guarantee_other_ideologies",
)

# Boolean top-level flags.
BOOL_KEYS: tuple[str, ...] = (
    "can_be_boosted",
    "can_collaborate",
    "can_host_government_in_exile",
)

# Numeric top-level fields.
NUM_KEYS: tuple[str, ...] = (
    "war_impact_on_world_tension",
    "faction_impact_on_world_tension",
    "ai_ideology_wanted_units_factor",
    "ai_give_core_state_control_threshold",
)

# The base AI behaviours an ideology can mimic (written as ``ai_<x> = yes``).
AI_BEHAVIOURS: tuple[str, ...] = ("democratic", "communist", "fascist", "neutral")

# Sub-blocks and keys handled explicitly (everything else is preserved but not
# surfaced as a first-class field).
_STRUCTURAL_KEYS = frozenset(
    {"color", "types", "dynamic_faction_names", "rules", "modifiers",
     "faction_modifiers"}
    | set(BOOL_KEYS) | set(NUM_KEYS) | {f"ai_{b}" for b in AI_BEHAVIOURS}
)


@dataclass
class Issue:
    severity: str            # "error" | "warning"
    code: str
    subject: str
    detail: str = ""
    rel_file: str = ""


@dataclass
class IdeologyDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    names: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class IdeologyTypeDef:
    """View over one ``<variant> = { ... }`` pair inside ``types``."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def name(self) -> str:
        return self.pair.key

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def can_be_randomly_selected(self) -> bool:
        v = self.block.get_scalar_ci("can_be_randomly_selected")
        # in-engine default is yes when the key is absent
        return (v or "yes").strip('"').lower() != "no"

    def set_can_be_randomly_selected(self, value: bool) -> None:
        if value:
            self.block.remove_ci("can_be_randomly_selected")   # yes is the default
        else:
            self.block.set_ci("can_be_randomly_selected", Scalar("no"))

    # --- optional colour override -------------------------------------------
    @property
    def has_color(self) -> bool:
        return self.block.get_block_ci("color") is not None

    @property
    def color(self) -> tuple[str, str, str]:
        blk = self.block.get_block_ci("color")
        vals = blk.array_values() if blk is not None else []
        return tuple((vals + ["", "", ""])[:3])            # type: ignore[return-value]

    def set_color(self, r: str, g: str, b: str) -> None:
        parts = [p.strip() for p in (r, g, b)]
        if not any(parts):
            self.block.remove_ci("color")
            return
        blk = Block()
        for p in parts:
            blk.add_value(p or "0")
        self.block.set_ci("color", blk)

    def clear_color(self) -> None:
        self.block.remove_ci("color")


class IdeologyDef:
    """View over one ``<name> = { ... }`` ideology pair."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def name(self) -> str:
        return self.pair.key

    @property
    def block(self) -> Block:
        return self.pair.value

    # --- helpers -------------------------------------------------------------
    def _sub_block(self, key: str, create: bool = False) -> Block | None:
        blk = self.block.get_block_ci(key)
        if blk is None and create:
            blk = Block()
            self.block.set_ci(key, blk)
        return blk

    # --- colour --------------------------------------------------------------
    @property
    def color(self) -> tuple[str, str, str]:
        blk = self.block.get_block_ci("color")
        vals = blk.array_values() if blk is not None else []
        return tuple((vals + ["", "", ""])[:3])            # type: ignore[return-value]

    def set_color(self, r: str, g: str, b: str) -> None:
        parts = [p.strip() for p in (r, g, b)]
        if not any(parts):
            self.block.remove_ci("color")
            return
        blk = Block()
        for p in parts:
            blk.add_value(p or "0")
        self.block.set_ci("color", blk)

    # --- scalar flags / numbers ---------------------------------------------
    def get_scalar(self, key: str) -> str:
        v = self.block.get_scalar_ci(key)
        return (v or "").strip('"')

    def set_scalar(self, key: str, value: str) -> None:
        value = value.strip()
        if value:
            self.block.set_ci(key, Scalar(value))
        else:
            self.block.remove_ci(key)

    def get_bool(self, key: str) -> bool:
        return self.get_scalar(key).lower() == "yes"

    def set_bool(self, key: str, value: bool) -> None:
        # explicit yes/no round-trips (both are meaningful vs. engine defaults)
        self.block.set_ci(key, Scalar("yes" if value else "no"))

    # --- AI behaviour --------------------------------------------------------
    @property
    def ai_behaviour(self) -> str:
        for beh in AI_BEHAVIOURS:
            if self.get_scalar(f"ai_{beh}").lower() == "yes":
                return beh
        return ""

    def set_ai_behaviour(self, behaviour: str) -> None:
        for beh in AI_BEHAVIOURS:
            self.block.remove_ci(f"ai_{beh}")
        if behaviour in AI_BEHAVIOURS:
            self.block.set_ci(f"ai_{behaviour}", Scalar("yes"))

    # --- rules ---------------------------------------------------------------
    def get_rule(self, key: str) -> str:
        """"" (unset) / "yes" / "no"."""
        blk = self.block.get_block_ci("rules")
        if blk is None:
            return ""
        v = blk.get_scalar_ci(key)
        return (v or "").strip('"').lower()

    def set_rule(self, key: str, value: str) -> None:
        value = value.strip().lower()
        if value not in ("yes", "no"):
            blk = self.block.get_block_ci("rules")
            if blk is not None:
                blk.remove_ci(key)
                if not blk.items:
                    self.block.remove_ci("rules")
            return
        self._sub_block("rules", create=True).set_ci(key, Scalar(value))

    def extra_rules(self) -> list[str]:
        """Rule keys present in the block that are not part of the known
        `RULE_KEYS` set (custom / undocumented rules), in file order."""
        blk = self.block.get_block_ci("rules")
        if blk is None:
            return []
        known = {k.lower() for k in RULE_KEYS}
        out: list[str] = []
        for pair in blk.pairs():
            if pair.key.lower() not in known and pair.key not in out:
                out.append(pair.key)
        return out

    # --- modifier lists ------------------------------------------------------
    def modifiers(self, key: str = "modifiers") -> list[Pair]:
        blk = self.block.get_block_ci(key)
        if blk is None:
            return []
        return [p for p in blk.pairs() if isinstance(p.value, Scalar)]

    def add_modifier(self, mod_key: str, value: str = "0",
                     key: str = "modifiers") -> Pair:
        pair = Pair(mod_key, Scalar(value))
        self._sub_block(key, create=True).items.append(pair)
        return pair

    def remove_modifier(self, pair: Pair, key: str = "modifiers") -> None:
        blk = self.block.get_block_ci(key)
        if blk is None:
            return
        blk.items = [it for it in blk.items if it is not pair]
        if not blk.items:
            self.block.remove_ci(key)

    # --- dynamic faction names ----------------------------------------------
    def faction_names(self) -> list[str]:
        blk = self.block.get_block_ci("dynamic_faction_names")
        return blk.array_values() if blk is not None else []

    def set_faction_names(self, names: list[str]) -> None:
        names = [n.strip() for n in names if n.strip()]
        if not names:
            self.block.remove_ci("dynamic_faction_names")
            return
        blk = Block()
        for n in names:
            blk.items.append(Scalar(n, quoted=True))
        self.block.set_ci("dynamic_faction_names", blk)

    # --- types ---------------------------------------------------------------
    def types(self) -> list[IdeologyTypeDef]:
        blk = self.block.get_block_ci("types")
        if blk is None:
            return []
        return [IdeologyTypeDef(p) for p in blk.pairs()
                if isinstance(p.value, Block)]

    def add_type(self, name: str) -> IdeologyTypeDef:
        pair = Pair(name, Block())
        self._sub_block("types", create=True).items.append(pair)
        return IdeologyTypeDef(pair)

    def remove_type(self, itype: IdeologyTypeDef) -> None:
        blk = self.block.get_block_ci("types")
        if blk is None:
            return
        blk.items = [it for it in blk.items if it is not itype.pair]
        if not blk.items:
            self.block.remove_ci("types")

    # --- misc ----------------------------------------------------------------
    def other_keys(self) -> list[str]:
        """Top-level keys not surfaced as a first-class field (still preserved)."""
        out: list[str] = []
        for pair in self.block.pairs():
            if pair.key.lower() not in _STRUCTURAL_KEYS and pair.key not in out:
                out.append(pair.key)
        return out

    def get_script(self, key: str) -> str:
        v = self.block.get_ci(key)
        return dumps(v, top_level=False) if isinstance(v, Block) else ""


class IdeologyDoc:
    def __init__(self, ref: IdeologyDocRef, root: Block):
        self.ref = ref
        self.root = root

    def container(self, create: bool = False) -> Block | None:
        blk = self.root.get_block_ci(CONTAINER_KEY)
        if blk is None and create:
            blk = Block()
            self.root.add(CONTAINER_KEY, blk)
        return blk

    def entries(self) -> list[IdeologyDef]:
        blk = self.container()
        if blk is None:
            return []
        return [IdeologyDef(p) for p in blk.pairs() if isinstance(p.value, Block)]

    def find(self, name: str) -> IdeologyDef | None:
        low = name.lower()
        return next((e for e in self.entries() if e.name.lower() == low), None)


class IdeologyDefService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, IdeologyDoc]] = {}

    # -------------------------------------------------------------------- scan
    def list_docs(self, include_vanilla: bool = True) -> list[IdeologyDocRef]:
        return layered_docs(
            self.ctx, IDEOLOGY_DIR, self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[IdeologyDocRef]:
        folder = root / IDEOLOGY_DIR
        if not folder.is_dir():
            return []
        return [IdeologyDocRef(rel_file=f"{IDEOLOGY_DIR}/{file.name}",
                               source_root=root, is_vanilla=is_vanilla,
                               names=self._quick_scan(file))
                for file in sorted(folder.glob("*.txt"))]

    @staticmethod
    def _quick_scan(file: Path) -> list[str]:
        """Ideology names live at depth 1 (inside the ``ideologies`` wrapper)."""
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        names: list[str] = []
        depth = 0
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 1:
                m = _KEY_RE.match(code.strip())
                if m:
                    names.append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return names

    # -------------------------------------------------------------------- load
    def load(self, ref: IdeologyDocRef) -> IdeologyDoc:
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        doc = IdeologyDoc(ref, parse_file(ref.path))
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save(self, doc: IdeologyDoc) -> None:
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

    def copy_to_mod(self, ref: IdeologyDocRef) -> IdeologyDocRef:
        if not ref.is_vanilla:
            return ref
        target = self.ctx.mod.path / ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return IdeologyDocRef(rel_file=ref.rel_file,
                              source_root=self.ctx.mod.path, is_vanilla=False,
                              edited=True, names=list(ref.names))

    def delete_file(self, ref: IdeologyDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a vanilla file")
        if ref.path.exists():
            ref.path.unlink()
        self._doc_cache.pop(str(ref.path).lower(), None)

    # -------------------------------------------------------------------- CRUD
    def create_doc(self, filename: str) -> IdeologyDoc:
        if not filename.endswith(".txt"):
            filename += ".txt"
        ref = IdeologyDocRef(rel_file=f"{IDEOLOGY_DIR}/{filename}",
                             source_root=self.ctx.mod.path, is_vanilla=False)
        doc = IdeologyDoc(ref, Block())
        doc.container(create=True)
        self._doc_cache[str(ref.path).lower()] = (0.0, doc)
        return doc

    def mod_target_doc(self) -> IdeologyDoc:
        for ref in self.list_docs(include_vanilla=False):
            try:
                return self.load(ref)
            except Exception:
                continue
        ref = IdeologyDocRef(rel_file=DEFAULT_REL_FILE,
                             source_root=self.ctx.mod.path, is_vanilla=False)
        doc = IdeologyDoc(ref, Block())
        doc.container(create=True)
        return doc

    def add_entry(self, doc: IdeologyDoc, name: str) -> IdeologyDef:
        """Create a new ideology pre-filled with sensible, valid defaults."""
        seed = pdx_parse(
            "color = { 127 127 127 }\n"
            "rules = {\n"
            "    can_force_government = yes\n"
            "    can_send_volunteers = no\n"
            "    can_puppet = no\n"
            "}\n"
            "war_impact_on_world_tension = 0.5\n"
            "faction_impact_on_world_tension = 0.5\n"
            "ai_neutral = yes\n",
            recover=False)
        pair = Pair(name, Block(seed.items))
        doc.container(create=True).items.append(pair)
        return IdeologyDef(pair)

    def remove_entry(self, doc: IdeologyDoc, entry: IdeologyDef) -> None:
        blk = doc.container()
        if blk is not None:
            blk.items = [it for it in blk.items if it is not entry.pair]

    def rename_entry(self, entry: IdeologyDef, new_name: str) -> None:
        entry.pair.key = new_name

    def duplicate_entry(self, doc: IdeologyDoc, entry: IdeologyDef,
                        new_name: str) -> IdeologyDef:
        clone = pdx_parse(dumps(entry.block, top_level=False), recover=False)
        pair = Pair(new_name, Block(clone.items))
        doc.container(create=True).items.append(pair)
        return IdeologyDef(pair)

    # -------------------------------------------------------------- validation
    def validate(self, docs: list[IdeologyDoc],
                 known_modifiers: dict | set | None = None) -> list[Issue]:
        issues: list[Issue] = []
        seen: dict[str, str] = {}
        for doc in docs:
            rel = doc.ref.rel_file
            for entry in doc.entries():
                name = entry.name
                if name in seen:
                    issues.append(Issue("error", "duplicate_name", name,
                                        detail=seen[name], rel_file=rel))
                else:
                    seen[name] = rel
                r, g, b = entry.color
                if not (r and g and b):
                    issues.append(Issue("warning", "no_color", name, rel_file=rel))
                elif not all(_is_byte(c) for c in (r, g, b)):
                    issues.append(Issue("warning", "bad_color", name,
                                        detail=f"{r} {g} {b}", rel_file=rel))
                if not entry.ai_behaviour:
                    issues.append(Issue("warning", "no_ai", name, rel_file=rel))
                if known_modifiers is not None:
                    for group in ("modifiers", "faction_modifiers"):
                        for pair in entry.modifiers(group):
                            if pair.key not in known_modifiers:
                                issues.append(Issue("warning", "unknown_modifier",
                                                    name, detail=pair.key,
                                                    rel_file=rel))
        return issues


def _is_byte(raw: str) -> bool:
    try:
        return 0 <= int(float(raw)) <= 255
    except (ValueError, TypeError):
        return False
