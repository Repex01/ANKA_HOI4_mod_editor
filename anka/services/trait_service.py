"""Read/write character *traits* — the two ``leader_traits = { ... }`` families.

Two families share the container block but live in different folders and have
very different shapes:

* **country-leader / advisor / idea traits** — ``common/country_leader/*.txt``.
  A trait block holds a few special keys (``random``, ``sprite``, ``ai_will_do``,
  ``targeted_modifier``, ``equipment_bonus``) and, *flat at the top level*, the
  static modifiers applied to the country (``political_power_factor = 0.25`` …).
  Localisation key is the trait id itself (``dictator`` → ``dictator`` /
  ``dictator_desc``).

* **unit-leader traits** (generals, field marshals, admirals) —
  ``common/unit_leader/*.txt``. A rich structure: ``type`` / ``trait_type``,
  flat and multiplicative skill bonuses, several scoped ``*_modifier`` blocks,
  ``sub_unit_modifiers``, trait-tree wiring (``parent`` / ``mutually_exclusive``
  / ``gui_row`` …), lifecycle effects (``on_add`` / ``on_remove`` /
  ``daily_effect``) and more. Localisation key is ``trait_<id>``.

Like the ideas / on-actions services, the model is *block-backed*: `TraitDef` is a
thin view over a parsed PDX pair, so unknown keys survive load/save untouched.
Documents are merged across the content layers (base game → dependency mods →
edited mod) via `layered_docs`, so **submods are fully supported**: a dependency's
traits appear read-only and the edited mod overrides files of the same name.

The light ``country_leader_traits`` / ``unit_leader_traits`` / ``advisor_traits``
lists (used by the Characters and Ideas editors' pickers) are preserved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.pdx import Block, Pair, Scalar, dump_file, dumps, parse_file
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs
from ._locutil import LocCatalog

# --- family layout ----------------------------------------------------------
COUNTRY_FAMILY = "country"
UNIT_FAMILY = "unit"
FAMILY_DIRS = {
    COUNTRY_FAMILY: "common/country_leader",
    UNIT_FAMILY: "common/unit_leader",
}
_NEW_FILE = {
    COUNTRY_FAMILY: "anka_country_leader_traits.txt",
    UNIT_FAMILY: "anka_unit_leader_traits.txt",
}

# --- field vocabularies (drive the inspector forms) -------------------------
# Country-leader trait: keys handled by dedicated widgets. Every OTHER top-level
# pair is a flat country modifier, edited together as one "modifiers" block.
COUNTRY_SPECIAL_KEYS = {"random", "sprite", "name", "ai_will_do",
                        "targeted_modifier", "equipment_bonus"}
COUNTRY_SCRIPT_FIELDS = ("ai_will_do", "targeted_modifier", "equipment_bonus")

UNIT_TYPES = ("all", "land", "navy", "corps_commander", "field_marshal", "operative")
UNIT_TRAIT_TYPES = ("basic_trait", "personality_trait", "assignable_trait",
                    "basic_terrain_trait", "assignable_terrain_trait",
                    "status_trait", "exile")
UNIT_SKILL_FIELDS = ("attack_skill", "defense_skill", "logistics_skill",
                     "planning_skill", "maneuvering_skill", "coordination_skill")
UNIT_SKILL_FACTOR_FIELDS = tuple(f"{s}_factor" for s in UNIT_SKILL_FIELDS)
UNIT_NUM_FIELDS = ("cost", "gui_row", "gui_column", "gain_xp_on_spotting")
UNIT_STR_FIELDS = ("enable_ability", "mutually_exclusive")
UNIT_TRIGGER_FIELDS = ("allowed", "prerequisites", "gain_xp", "gain_xp_leader")
UNIT_EFFECT_FIELDS = ("on_add", "on_remove", "daily_effect")
UNIT_MODIFIER_FIELDS = ("modifier", "non_shared_modifier", "corps_commander_modifier",
                        "field_marshal_modifier")
UNIT_OTHER_SCRIPT_FIELDS = ("sub_unit_modifiers", "trait_xp_factor",
                            "ai_will_do", "new_commander_weight", "parent")
UNIT_SCRIPT_FIELDS = (UNIT_MODIFIER_FIELDS + UNIT_TRIGGER_FIELDS
                      + UNIT_EFFECT_FIELDS + UNIT_OTHER_SCRIPT_FIELDS)

_ID_RE = re.compile(r"^\w+$")
# trait ids may carry dots / hyphens like idea ids (e.g. AST_foo-bar).
_KEY_OPEN_RE = re.compile(r"^\s*([\w.\-]+)\s*=\s*\{")


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

class TraitDef:
    """A view over one ``<trait_id> = { ... }`` pair inside ``leader_traits``."""

    def __init__(self, pair: Pair, family: str):
        self.pair = pair
        self.family = family

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def id(self) -> str:
        return self.pair.key

    @id.setter
    def id(self, value: str) -> None:
        self.pair.key = value

    @property
    def loc_key(self) -> str:
        """Localisation key: the id for country traits, ``trait_<id>`` for unit."""
        return self.id if self.family == COUNTRY_FAMILY else f"trait_{self.id}"

    # --- generic scalar accessors ------------------------------------------
    def get_raw(self, key: str) -> str:
        v = self.block.get(key)
        return v.raw if isinstance(v, Scalar) else ""

    def set_raw(self, key: str, value: str) -> None:
        value = value.strip()
        if value:
            self.block.set(key, Scalar(value))
        else:
            self.block.remove(key)

    def get_flag(self, key: str, default: bool = False) -> bool:
        v = self.block.get(key)
        return v.as_bool() if isinstance(v, Scalar) else default

    def get_script(self, key: str) -> str:
        v = self.block.get(key)
        if isinstance(v, Block):
            return dumps(v, top_level=False)
        if isinstance(v, Scalar):
            return v.raw
        return ""

    def set_script(self, key: str, text: str) -> None:
        """Parse `text` strictly and store under `key`; empty removes the key."""
        if not text.strip():
            self.block.remove(key)
            return
        parsed = pdx_parse(text, recover=False)
        if len(parsed.items) == 1 and isinstance(parsed.items[0], Scalar):
            self.block.set(key, parsed.items[0])
        else:
            self.block.set(key, Block(parsed.items))

    def has_script(self, key: str) -> bool:
        return self.block.get(key) is not None

    # --- country: flat modifiers block -------------------------------------
    def country_modifiers_text(self) -> str:
        """Every top-level pair that is not a special key, serialized as a block —
        the flat country modifiers (``political_power_factor = 0.25`` …)."""
        pairs = [p for p in self.block.pairs()
                 if p.key not in COUNTRY_SPECIAL_KEYS]
        return dumps(Block(list(pairs)), top_level=False) if pairs else ""

    def set_country_modifiers_text(self, text: str) -> None:
        """Replace all non-special top-level pairs with the parsed modifier lines,
        keeping the special keys (random/sprite/ai_will_do/…) in place."""
        kept = [it for it in self.block.items
                if not (isinstance(it, Pair) and it.key not in COUNTRY_SPECIAL_KEYS)]
        new_items: list = []
        if text.strip():
            parsed = pdx_parse(text, recover=False)
            new_items = list(parsed.items)
        self.block.items = kept + new_items

    def __repr__(self) -> str:
        return f"TraitDef({self.id!r}, {self.family})"


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class TraitDocRef:
    rel_file: str                     # e.g. "common/country_leader/00_traits.txt"
    source_root: Path
    is_vanilla: bool
    family: str
    edited: bool = False              # a lower-layer file overridden by the mod
    names: list[str] = field(default_factory=list)   # quick-scan trait ids

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class TraitDocument:
    ref: TraitDocRef
    root: Block

    def leader_traits(self, create: bool = False) -> Block | None:
        block = self.root.get_block("leader_traits")
        if block is None and create:
            block = Block()
            self.root.set("leader_traits", block)
        return block

    def traits(self) -> list[TraitDef]:
        block = self.leader_traits()
        if block is None:
            return []
        return [TraitDef(p, self.ref.family) for p in block.pairs()
                if isinstance(p.value, Block)]

    def find(self, trait_id: str) -> TraitDef | None:
        for trait in self.traits():
            if trait.id == trait_id:
                return trait
        return None


@dataclass
class Issue:
    severity: str        # "error" | "warning"
    code: str            # locale key suffix: traits.issue.<code>
    subject: str = ""    # trait id
    detail: str = ""
    rel_file: str = ""


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class TraitService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, TraitDocument]] = {}
        # legacy quick lists (used by other editors' pickers)
        self._country: list[str] | None = None
        self._unit: list[str] | None = None
        # trait names/tooltips localise into traits_l_<lang>.yml; ANKA's own file
        # holds brand-new keys. Shared for both families (loc key already encodes
        # the family: bare id vs. trait_<id>).
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="trait",
                              default_pattern="anka_traits_l_{lang}.yml",
                              dep_roots=context.dependency_paths)

    # --- legacy picker API (list[str]) -------------------------------------
    def country_leader_traits(self) -> list[str]:
        if self._country is None:
            self._country = self._scan_names(COUNTRY_FAMILY)
        return self._country

    def unit_leader_traits(self) -> list[str]:
        if self._unit is None:
            self._unit = self._scan_names(UNIT_FAMILY)
        return self._unit

    # Advisor traits live alongside country-leader traits.
    advisor_traits = country_leader_traits

    def _scan_names(self, family: str) -> list[str]:
        names: set[str] = set()
        for ref in self.list_docs(family, include_vanilla=True):
            names.update(ref.names)
        return sorted(names)

    # --- listing -----------------------------------------------------------
    def list_docs(self, family: str,
                  include_vanilla: bool = False) -> list[TraitDocRef]:
        rel = FAMILY_DIRS[family]
        return layered_docs(
            self.ctx, rel,
            lambda root, is_vanilla: self._scan_dir(root, is_vanilla, family, rel),
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool, family: str,
                  rel: str) -> list[TraitDocRef]:
        folder = root / rel
        if not folder.is_dir():
            return []
        refs: list[TraitDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            if not file.is_file():
                continue
            refs.append(TraitDocRef(
                rel_file=f"{rel}/{file.name}", source_root=root,
                is_vanilla=is_vanilla, family=family,
                names=self._quick_scan(file)))
        return refs

    @staticmethod
    def _quick_scan(file: Path) -> list[str]:
        """Trait ids = keys opening a block at depth 1 inside ``leader_traits``.
        Brace counting (vanilla mixes tabs/spaces and has top-level ``@vars``)."""
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        names: list[str] = []
        depth = 0
        seen_lt = False
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            m = _KEY_OPEN_RE.match(code)
            if m and depth == 0 and m.group(1) == "leader_traits":
                seen_lt = True
            elif m and depth == 1 and seen_lt:
                names.append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return names

    # --- load / save --------------------------------------------------------
    def load(self, ref: TraitDocRef) -> TraitDocument:
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            doc = cached[1]
            doc.ref = ref
            return doc
        doc = TraitDocument(ref=ref, root=parse_file(ref.path))
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save(self, doc: TraitDocument) -> Path:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write into a read-only trait file")
        path = ensure_filename_case(doc.ref.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, path)
        try:
            self._doc_cache[str(path).lower()] = (path.stat().st_mtime, doc)
        except OSError:
            pass
        doc.ref.names = [t.id for t in doc.traits()]
        self._invalidate_names(doc.ref.family)
        return path

    def copy_to_mod(self, ref: TraitDocRef) -> TraitDocRef:
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return TraitDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                           is_vanilla=False, family=ref.family, edited=True,
                           names=list(ref.names))

    def delete_file(self, ref: TraitDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a read-only trait file")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(str(ref.path).lower(), None)
        self._invalidate_names(ref.family)

    def _invalidate_names(self, family: str) -> None:
        if family == COUNTRY_FAMILY:
            self._country = None
        else:
            self._unit = None

    # --- localisation -------------------------------------------------------
    def name_of(self, key: str, language: str) -> str:
        text = self.loc.get(key, language)
        if text is None and language != "english":
            text = self.loc.get(key, "english")
        return text if text is not None else key

    def desc_of(self, key: str, language: str) -> str:
        text = self.loc.get(f"{key}_desc", language)
        if text is None and language != "english":
            text = self.loc.get(f"{key}_desc", "english")
        return text or ""

    def set_loc(self, key: str, language: str,
                name: str | None, desc: str | None) -> None:
        if name is not None:
            self.loc.set(key, language, name)
        if desc is not None:
            self.loc.set(f"{key}_desc", language, desc)

    def has_any_name(self, key: str, preferred: str = "english") -> bool:
        from ..config.constants import HOI4_LANGUAGES
        order = [preferred, "english"] + [l for l in HOI4_LANGUAGES
                                          if l not in (preferred, "english")]
        return any(self.loc.has(key, lang) for lang in order)

    # --- trait CRUD ---------------------------------------------------------
    def mod_target_doc(self, family: str) -> TraitDocument:
        """The mod document new traits of `family` should go to: the first
        *editable* mod file of that family, else ANKA's own file. Dependency
        files (listed but read-only) are skipped."""
        for ref in self.list_docs(family, include_vanilla=False):
            if ref.is_vanilla or ref.source_root != self.ctx.mod.path:
                continue
            try:
                return self.load(ref)
            except Exception:
                continue
        rel = f"{FAMILY_DIRS[family]}/{_NEW_FILE[family]}"
        ref = TraitDocRef(rel_file=rel, source_root=self.ctx.mod.path,
                          is_vanilla=False, family=family)
        if ref.path.exists():
            return self.load(ref)
        return TraitDocument(ref=ref, root=Block([Pair("leader_traits", Block())]))

    def add_trait(self, doc: TraitDocument, trait_id: str) -> TraitDef:
        block = doc.leader_traits(create=True)
        body = Block()
        if doc.ref.family == COUNTRY_FAMILY:
            body.set("random", Scalar("no"))
            body.set("ai_will_do", Block([Pair("factor", Scalar("1"))]))
        else:
            body.set("type", Scalar("land"))
            body.set("trait_type", Scalar("personality_trait"))
            body.set("modifier", Block())
        pair = Pair(trait_id, body)
        block.items.append(pair)
        return TraitDef(pair, doc.ref.family)

    def remove_trait(self, doc: TraitDocument, trait: TraitDef) -> None:
        block = doc.leader_traits()
        if block is not None:
            block.items = [it for it in block.items if it is not trait.pair]

    def duplicate_trait(self, doc: TraitDocument, trait: TraitDef,
                        new_id: str) -> TraitDef:
        copy_root = pdx_parse(dumps(Block([Pair(trait.id, trait.block)])))
        pair = next(p for p in copy_root.pairs())
        pair.key = new_id
        block = doc.leader_traits(create=True)
        block.items.append(pair)
        return TraitDef(pair, doc.ref.family)

    def rename_trait(self, trait: TraitDef, new_id: str) -> str:
        """Change the id (pair key) and carry its localisation keys."""
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid id: {new_id!r}")
        old_loc = trait.loc_key
        trait.id = new_id
        new_loc = trait.loc_key
        if old_loc != new_loc:
            self.loc.rename(old_loc, new_loc)     # carries the _desc twin too
        return new_id

    def known_ids(self, family: str) -> set[str]:
        return set(self._scan_names(family))

    # --- validation ---------------------------------------------------------
    def validate(self, docs: list[TraitDocument], language: str | None = None,
                 known_modifiers: set[str] | None = None) -> list[Issue]:
        issues: list[Issue] = []
        seen: dict[str, str] = {}
        for doc in docs:
            rel = doc.ref.rel_file
            for trait in doc.traits():
                tid = trait.id
                if not _ID_RE.match(tid):
                    issues.append(Issue("error", "bad_id", tid, rel_file=rel))
                if tid in seen:
                    issues.append(Issue("error", "duplicate_id", tid, seen[tid],
                                        rel_file=rel))
                else:
                    seen[tid] = rel
                if language and not self.has_any_name(trait.loc_key,
                                                      preferred=language):
                    issues.append(Issue("warning", "missing_loc", tid,
                                        rel_file=rel))
                if doc.ref.family == UNIT_FAMILY:
                    ttype = trait.get_raw("type")
                    if ttype and ttype not in UNIT_TYPES:
                        issues.append(Issue("warning", "bad_type", tid, ttype,
                                            rel_file=rel))
                    tt = trait.get_raw("trait_type")
                    if tt and tt not in UNIT_TRAIT_TYPES:
                        issues.append(Issue("warning", "bad_trait_type", tid, tt,
                                            rel_file=rel))
        return issues
