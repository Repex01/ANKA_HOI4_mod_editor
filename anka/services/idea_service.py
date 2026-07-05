"""Read/write ideas (national spirits, laws, advisors, designers, spirits).

Source: ``common/ideas/*.txt`` — files shaped as::

    ideas = {
        <category> = {           # country, hidden_ideas, economy, tank_manufacturer...
            <idea_id> = { ... }
            law = yes            # category-level scalar flags (kept, not lost)
        }
    }

The key of a *category block* is either a category name (``country``,
``hidden_ideas``) or a *slot* name (``economy``, ``tank_manufacturer``, ...) —
categories that declare slots are addressed in idea files by slot. One category
is assembled additively from many files; one file may hold several categories.

Category *definitions* live in ``common/idea_tags/*.txt``
(``idea_categories = { <name> = { type slot character_slot cost ... } }`` plus a
``slot_ledgers`` block).

Like decisions/events, the model is *block-backed*: `IdeaDef` / `CategoryDef` are
thin views over parsed PDX nodes, so unknown keys survive load/save untouched.

The light ``IdeaInfo`` / ``list_ideas`` / ``get`` API (used by other editors'
pickers) is preserved; it now reads the quick-scan instead of a full parse.
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
from ._locutil import LocCatalog
from ._pdxview import BlockView

# Script blocks on an idea, grouped by what the visual editor should offer.
IDEA_TRIGGER_FIELDS = ("allowed", "allowed_civil_war", "visible", "available",
                       "allowed_to_remove", "cancel", "do_effect")
IDEA_EFFECT_FIELDS = ("on_add", "on_remove")
IDEA_OTHER_FIELDS = ("ai_will_do", "modifier", "targeted_modifier",
                     "equipment_bonus", "research_bonus", "rule")
IDEA_SCRIPT_FIELDS = IDEA_TRIGGER_FIELDS + IDEA_EFFECT_FIELDS + IDEA_OTHER_FIELDS

# Tri-state boolean flags on an idea (absent = game default). cancel_if_invalid
# defaults to yes in vanilla (only ``= no`` is ever written), confirmed on the
# base-game ideas: 31 ``= no``, zero ``= yes``.
IDEA_FLAG_DEFAULTS = {"default": False, "cancel_if_invalid": True}

# ``ledger`` combobox values ("" = key absent).
LEDGER_VALUES = ("", "army", "air", "navy", "military", "civilian", "all", "hidden")

# Category-level scalar flags that live *inside* a category block in an ideas file
# (not in idea_tags). They must survive aggregation and be editable.
CATEGORY_FILE_FLAGS = ("law", "designer", "use_list_view")

# Default picture (short name) for the national-spirit creation template.
_DEFAULT_PICTURE = "generic_production_bonus"

_ID_RE = re.compile(r"^\w+$")
_NEW_FILE = "anka_ideas.txt"
_NEW_TAGS_FILE = "anka_idea_tags.txt"


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

class IdeaDef(BlockView):
    """A view over one ``<idea_id> = { ... }`` pair inside a category block."""

    FLAG_DEFAULTS = IDEA_FLAG_DEFAULTS

    def __init__(self, pair: Pair, category: str):
        self.pair = pair
        self.category = category

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
    def name_key(self) -> str:
        """Localisation key: explicit ``name = X`` override or the id itself."""
        return self.get_raw("name") or self.id

    @property
    def has_name_override(self) -> bool:
        return bool(self.get_raw("name"))

    # picture: short name stored the vanilla way (``picture = generic_x``).
    @property
    def picture(self) -> str:
        return self.get_raw("picture")

    @picture.setter
    def picture(self, value: str) -> None:
        self.set_raw("picture", value)

    def sprite_name(self) -> str:
        """Resolve the picture field into a sprite name: a short name gets the
        ``GFX_idea_`` prefix, a full ``GFX_`` name passes through, and an absent
        picture falls back to the engine default ``GFX_idea_<id>``."""
        pic = self.picture
        if not pic:
            return f"GFX_idea_{self.id}"
        if pic.startswith("GFX_"):
            return pic
        return f"GFX_idea_{pic}"

    # traits: ``traits = { a b c }`` array of bare scalars.
    @property
    def traits(self) -> list[str]:
        v = self.block.get("traits")
        return v.array_values() if isinstance(v, Block) else []

    @traits.setter
    def traits(self, values: list[str]) -> None:
        values = [t.strip() for t in values if t.strip()]
        if not values:
            self.block.remove("traits")
            return
        block = Block()
        for t in values:
            block.add_value(Scalar(t))
        self.block.set("traits", block)

    def __repr__(self) -> str:
        return f"IdeaDef({self.id!r} in {self.category!r})"


class CategoryDef(BlockView):
    """A view over one ``<name> = { ... }`` pair of ``idea_categories``."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def name(self) -> str:
        return self.pair.key

    def slots(self) -> list[str]:
        return [v.raw for v in self.block.get_all("slot") if isinstance(v, Scalar)]

    def character_slots(self) -> list[str]:
        return [v.raw for v in self.block.get_all("character_slot")
                if isinstance(v, Scalar)]

    @property
    def type(self) -> str:
        return self.get_raw("type")

    @property
    def cost(self) -> str:
        return self.get_raw("cost")

    @property
    def removal_cost(self) -> str:
        return self.get_raw("removal_cost")

    @property
    def ledger(self) -> str:
        return self.get_raw("ledger")

    @property
    def hidden(self) -> bool:
        return self.get_flag("hidden")

    @property
    def politics_tab(self) -> bool:
        # vanilla writes ``politics_tab = no`` on many categories; default is
        # yes for slot-less national-spirit style categories.
        v = self.block.get("politics_tab")
        if isinstance(v, Scalar):
            return v.as_bool()
        return not (self.slots() or self.character_slots())

    def __repr__(self) -> str:
        return f"CategoryDef({self.name!r})"


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class IdeaDocRef:
    rel_file: str                     # e.g. "common/ideas/SOV.txt"
    source_root: Path
    is_vanilla: bool
    edited: bool = False              # vanilla file overridden by the mod
    categories: dict[str, list[str]] = field(default_factory=dict)   # quick scan

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class IdeaDocument:
    ref: IdeaDocRef
    root: Block

    def ideas_block(self) -> Block | None:
        return self.root.get_block("ideas")

    def category_blocks(self) -> list[Pair]:
        ideas = self.ideas_block()
        if ideas is None:
            return []
        return [p for p in ideas.pairs() if isinstance(p.value, Block)]

    def ideas(self) -> list[IdeaDef]:
        out: list[IdeaDef] = []
        for cat in self.category_blocks():
            for p in cat.value.pairs():
                if isinstance(p.value, Block):
                    out.append(IdeaDef(p, cat.key))
        return out

    def find(self, idea_id: str) -> IdeaDef | None:
        for idea in self.ideas():
            if idea.id == idea_id:
                return idea
        return None

    def category_pair(self, category: str) -> Pair | None:
        for p in self.category_blocks():
            if p.key == category:
                return p
        return None

    def category_flags(self, category: str) -> dict[str, bool]:
        pair = self.category_pair(category)
        flags: dict[str, bool] = {}
        if pair is not None:
            for name in CATEGORY_FILE_FLAGS:
                v = pair.value.get(name)
                if isinstance(v, Scalar):
                    flags[name] = v.as_bool()
        return flags

    def set_category_flag(self, category: str, name: str, value: bool) -> None:
        """Write a category-level flag (``law``/``designer``/``use_list_view``)
        into the category block, creating the block if needed."""
        ideas = self.ideas_block()
        if ideas is None:
            ideas = Block()
            self.root.set("ideas", ideas)
        pair = self.category_pair(category)
        if pair is None:
            pair = Pair(category, Block())
            ideas.items.append(pair)
        if value:
            pair.value.set(name, Scalar("yes"))
        else:
            pair.value.remove(name)


@dataclass
class Issue:
    severity: str        # "error" | "warning"
    code: str            # locale key suffix: ideas.issue.<code>
    subject: str = ""    # idea / category id
    detail: str = ""
    rel_file: str = ""   # owning document (for tree navigation)


# ---------------------------------------------------------------------------
# light picker API (preserved signatures)
# ---------------------------------------------------------------------------

@dataclass
class IdeaInfo:
    id: str
    category: str          # e.g. "country", "economy", "political_advisor"
    in_mod: bool
    file: Path


# idea ids may contain dots and hyphens (e.g. AST_vickers-ruwolt_organization).
_KEY_OPEN_RE = re.compile(r"^\s*([\w.\-]+)\s*=\s*\{")


class IdeaService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, IdeaDocument]] = {}
        self._info_cache: dict[str, IdeaInfo] | None = None
        self._cat_defs_cache: dict[str, CategoryDef] | None = None
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="idea",
                              default_pattern="anka_ideas_l_{lang}.yml")

    # --- listing -----------------------------------------------------------
    def list_docs(self, include_vanilla: bool = False) -> list[IdeaDocRef]:
        mod_refs = self._scan_dir(self.ctx.mod.path, False)
        vanilla_refs = self._scan_dir(self.ctx.game_path, True)
        mod_files = {r.rel_file.lower() for r in mod_refs}
        for ref in mod_refs:
            if any(v.rel_file.lower() == ref.rel_file.lower() for v in vanilla_refs):
                ref.edited = True
        out = list(mod_refs)
        if include_vanilla:
            out.extend(v for v in vanilla_refs if v.rel_file.lower() not in mod_files)
        return sorted(out, key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[IdeaDocRef]:
        folder = root / GAME_DIRS.IDEAS
        if not folder.is_dir():
            return []
        refs: list[IdeaDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            if not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            refs.append(IdeaDocRef(
                rel_file=f"{GAME_DIRS.IDEAS}/{file.name}", source_root=root,
                is_vanilla=is_vanilla, categories=self._quick_scan(text)))
        return refs

    @staticmethod
    def _quick_scan(text: str) -> dict[str, list[str]]:
        """Cheap category -> idea-ids map for the tree/pickers (no full parse).

        Depth 0 opens ``ideas``; depth 1 keys are categories; depth 2 keys are
        ideas. Category-level scalar flags (``law = yes``) have no ``{`` and are
        skipped. Depth is brace-counted — vanilla indentation is inconsistent."""
        out: dict[str, list[str]] = {}
        depth = 0
        current: str | None = None
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            m = _KEY_OPEN_RE.match(code)
            if m:
                if depth == 1:
                    current = m.group(1)
                    out.setdefault(current, [])
                elif depth == 2 and current is not None:
                    out[current].append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
            if depth < 1:          # left the ideas block: forget the category
                current = None
        return out

    # --- load / save --------------------------------------------------------
    def load(self, ref: IdeaDocRef) -> IdeaDocument:
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
        doc = IdeaDocument(ref=ref, root=parse_file(path))
        self._doc_cache[path] = (mtime, doc)
        return doc

    def save(self, doc: IdeaDocument) -> Path:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write into the base game")
        path = ensure_filename_case(doc.ref.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, path)
        try:
            self._doc_cache[path] = (path.stat().st_mtime, doc)
        except OSError:
            pass
        self._info_cache = None
        return path

    def copy_to_mod(self, ref: IdeaDocRef) -> IdeaDocRef:
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        return IdeaDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                          is_vanilla=False, edited=True,
                          categories={k: list(v) for k, v in ref.categories.items()})

    def delete(self, ref: IdeaDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete vanilla files")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(ref.path, None)
        self._info_cache = None

    # --- category definitions (idea_tags) -----------------------------------
    def list_category_docs(self, include_vanilla: bool = False) -> list[IdeaDocRef]:
        mod_refs = self._scan_tag_dir(self.ctx.mod.path, False)
        vanilla_refs = self._scan_tag_dir(self.ctx.game_path, True)
        mod_files = {r.rel_file.lower() for r in mod_refs}
        for ref in mod_refs:
            if any(v.rel_file.lower() == ref.rel_file.lower() for v in vanilla_refs):
                ref.edited = True
        out = list(mod_refs)
        if include_vanilla:
            out.extend(v for v in vanilla_refs if v.rel_file.lower() not in mod_files)
        return sorted(out, key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    def _scan_tag_dir(self, root: Path, is_vanilla: bool) -> list[IdeaDocRef]:
        folder = root / GAME_DIRS.IDEA_TAGS
        if not folder.is_dir():
            return []
        refs: list[IdeaDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            if not file.is_file():
                continue
            refs.append(IdeaDocRef(
                rel_file=f"{GAME_DIRS.IDEA_TAGS}/{file.name}", source_root=root,
                is_vanilla=is_vanilla))
        return refs

    def category_defs(self, include_vanilla: bool = True) -> dict[str, CategoryDef]:
        """name -> CategoryDef across idea_tags (mod overrides vanilla)."""
        defs: dict[str, CategoryDef] = {}
        for ref in sorted(self.list_category_docs(include_vanilla),
                          key=lambda r: (not r.is_vanilla,)):  # vanilla first
            try:
                root = parse_file(ref.path)
            except Exception:
                continue
            cats = root.get_block("idea_categories")
            if cats is None:
                continue
            for p in cats.pairs():
                if isinstance(p.value, Block):
                    defs[p.key] = CategoryDef(p)
        return defs

    def slot_ledgers(self, include_vanilla: bool = True) -> dict[str, str]:
        """slot name -> ledger, from the ``slot_ledgers`` blocks."""
        out: dict[str, str] = {}
        for ref in sorted(self.list_category_docs(include_vanilla),
                          key=lambda r: (not r.is_vanilla,)):
            try:
                root = parse_file(ref.path)
            except Exception:
                continue
            block = root.get_block("slot_ledgers")
            if block is None:
                continue
            for p in block.pairs():
                if isinstance(p.value, Scalar):
                    out[p.key] = p.value.raw
        return out

    def _slot_owner_map(self, include_vanilla: bool = True) -> dict[str, str]:
        """slot / character_slot name -> owning category name."""
        owners: dict[str, str] = {}
        for name, cdef in self.category_defs(include_vanilla).items():
            for slot in cdef.slots() + cdef.character_slots():
                owners.setdefault(slot, name)
        return owners

    def slot_owner(self, key: str, include_vanilla: bool = True) -> str | None:
        return self._slot_owner_map(include_vanilla).get(key)

    def known_category_keys(self, include_vanilla: bool = True) -> set[str]:
        """Keys allowed as category blocks in an ideas file: slot-less category
        names (country, hidden_ideas), every slot / character_slot, plus keys
        actually seen in idea files (mods may be 'dirty')."""
        keys: set[str] = set()
        defs = self.category_defs(include_vanilla)
        for name, cdef in defs.items():
            slots = cdef.slots() + cdef.character_slots()
            if not slots:
                keys.add(name)
            keys.update(slots)
        for ref in self.list_docs(include_vanilla):
            keys.update(ref.categories)
        return keys

    # --- category creation / removal ----------------------------------------
    def create_category(self, name: str, *, kind: str = "", cost: str = "",
                        removal_cost: str = "", ledger: str = "",
                        hidden: bool = False, politics_tab: bool = True,
                        slots: tuple[str, ...] = (),
                        character_slots: tuple[str, ...] = (),
                        law: bool = False, designer: bool = False,
                        use_list_view: bool = False,
                        icon_source: str | Path | None = None) -> str:
        """Create a full idea category: the ``idea_tags`` definition, an empty
        materialized category block in the mod's ideas file (so it shows in the
        tree), and — for a politics-tab category — its GUI/GFX assets (empty-slot
        sprites, an extra category-icon frame, a widened politics-view grid).

        GUI artifacts are additive and are NOT rolled back on ``remove_category``.
        """
        slots = tuple(s.strip() for s in slots if s.strip())
        character_slots = tuple(s.strip() for s in character_slots if s.strip())

        # 1. idea_tags definition (repeating slot/character_slot pairs)
        path = self.ctx.mod.path / GAME_DIRS.IDEA_TAGS / _NEW_TAGS_FILE
        root = parse_file(path) if path.exists() else Block()
        cats = root.get_block("idea_categories")
        if cats is None:
            cats = Block()
            root.set("idea_categories", cats)
        block = Block()
        if kind:
            block.set("type", Scalar(kind))
        if cost != "":
            block.set("cost", Scalar(str(cost)))
        if removal_cost != "":
            block.set("removal_cost", Scalar(str(removal_cost)))
        if ledger:
            block.set("ledger", Scalar(ledger))
        if hidden:
            block.set("hidden", Scalar("yes"))
        if not politics_tab:                 # default is yes; only write the override
            block.set("politics_tab", Scalar("no"))
        for slot in slots:
            block.add("slot", Scalar(slot))
        for slot in character_slots:
            block.add("character_slot", Scalar(slot))
        cats.remove(name)                    # replace any prior mod definition
        cats.add(name, block)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, path)
        self._doc_cache.pop(path, None)
        self._cat_defs_cache = None

        # 2. materialize the category (+ file flags) in the mod ideas file
        doc = self.mod_target_doc(name)
        self.ensure_category(doc, name)
        for flag, value in (("law", law), ("designer", designer),
                            ("use_list_view", use_list_view)):
            if value:
                doc.set_category_flag(name, flag, True)
        self.save(doc)

        # 3. GUI / GFX assets
        from ._ideagui import IdeaGuiAssets
        assets = IdeaGuiAssets(self.ctx)
        assets.add_slot_sprites(list(slots) + list(character_slots))
        if politics_tab:
            assets.add_category_frame(icon_source)
            assets.widen_ideas_grid(len(slots) + len(character_slots))
        return name

    def count_mod_ideas(self, category: str) -> int:
        """How many mod ideas live in `category` (for delete confirmation)."""
        return sum(len(ref.categories.get(category, ()))
                   for ref in self.list_docs(False))

    def remove_category(self, name: str) -> int:
        """Delete a category from the mod: its ``idea_tags`` definition AND every
        mod idea block under it. Returns the number of ideas removed. Vanilla
        files and GUI/GFX artifacts (frames, widened grid) are left untouched."""
        removed = 0
        for ref in self.list_category_docs(False):
            try:
                root = parse_file(ref.path)
            except Exception:
                continue
            cats = root.get_block("idea_categories")
            if cats is None or cats.get(name) is None:
                continue
            cats.remove(name)
            dump_file(root, ensure_filename_case(ref.path))
            self._doc_cache.pop(ref.path, None)
        for ref in self.list_docs(False):
            if name not in ref.categories:
                continue
            doc = self.load(ref)
            ideas = doc.ideas_block()
            if ideas is None:
                continue
            for pair in list(ideas.pairs()):
                if pair.key == name and isinstance(pair.value, Block):
                    removed += sum(1 for p in pair.value.pairs()
                                   if isinstance(p.value, Block))
                    ideas.items.remove(pair)
            self.save(doc)
        self._cat_defs_cache = None
        return removed

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

    # --- idea CRUD ----------------------------------------------------------
    def mod_target_doc(self, category: str) -> IdeaDocument:
        """The mod document new ideas of `category` should go to: the first mod
        file already containing that category, else ANKA's ideas file."""
        for ref in self.list_docs(False):
            if category in ref.categories:
                return self.load(ref)
        rel = f"{GAME_DIRS.IDEAS}/{_NEW_FILE}"
        ref = IdeaDocRef(rel_file=rel, source_root=self.ctx.mod.path,
                         is_vanilla=False)
        if ref.path.exists():
            return self.load(ref)
        return IdeaDocument(ref=ref, root=Block([Pair("ideas", Block())]))

    def _category_block(self, doc: IdeaDocument, category: str) -> Block:
        ideas = doc.ideas_block()
        if ideas is None:
            ideas = Block()
            doc.root.set("ideas", ideas)
        pair = doc.category_pair(category)
        if pair is None:
            pair = Pair(category, Block())
            ideas.items.append(pair)
        return pair.value

    def ensure_category(self, doc: IdeaDocument, category: str) -> None:
        """Materialize an (empty) category block so it shows up in the tree."""
        self._category_block(doc, category)

    def add_idea(self, doc: IdeaDocument, category: str, idea_id: str) -> IdeaDef:
        cat_block = self._category_block(doc, category)
        block = Block()
        block.set("allowed", Block([Pair("always", Scalar("yes"))]))
        owner = self.slot_owner(category)
        if owner is None:
            # national-spirit / hidden-idea style top-level category
            block.set("removal_cost", Scalar("-1"))
            block.set("picture", Scalar(_DEFAULT_PICTURE))
            block.set("modifier", Block())
        else:
            # slot idea: inherit the category cost, no removal_cost
            cdef = self.category_defs().get(owner)
            cost = cdef.cost if cdef else ""
            if cost:
                block.set("cost", Scalar(cost))
            block.set("picture", Scalar(_DEFAULT_PICTURE))
        pair = Pair(idea_id, block)
        cat_block.items.append(pair)
        self._info_cache = None
        return IdeaDef(pair, category)

    def remove_idea(self, doc: IdeaDocument, idea: IdeaDef) -> None:
        for cat in doc.category_blocks():
            if idea.pair in cat.value.items:
                cat.value.items.remove(idea.pair)
                self._info_cache = None
                return

    def duplicate_idea(self, doc: IdeaDocument, idea: IdeaDef,
                       new_id: str) -> IdeaDef:
        copy_root = pdx_parse(dumps(Block([Pair(idea.id, idea.block)])))
        pair = next(p for p in copy_root.pairs())
        pair.key = new_id
        for cat in doc.category_blocks():
            if idea.pair in cat.value.items:
                cat.value.items.append(pair)
                self._info_cache = None
                return IdeaDef(pair, cat.key)
        raise ValueError("idea not in document")

    def move_idea(self, doc: IdeaDocument, idea: IdeaDef,
                  new_category: str) -> IdeaDef:
        """Move an idea to another category (within the same document)."""
        self.remove_idea(doc, idea)
        cat_block = self._category_block(doc, new_category)
        cat_block.items.append(idea.pair)
        idea.category = new_category
        self._info_cache = None
        return idea

    def rename_idea(self, idea: IdeaDef, new_id: str) -> str:
        """Change the id (the pair key). Carry derived loc keys unless the idea
        has an explicit ``name =`` override (then the loc key is not the id)."""
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid id: {new_id!r}")
        old = idea.id
        carry_loc = not idea.has_name_override
        idea.id = new_id
        if carry_loc:
            self.loc.rename(old, new_id)     # also carries the _desc twin
        self._info_cache = None
        return old

    # --- light picker API ---------------------------------------------------
    def list_ideas(self, refresh: bool = False) -> list[IdeaInfo]:
        if self._info_cache is None or refresh:
            self._info_cache = self._load_infos()
        return sorted(self._info_cache.values(), key=lambda i: (i.category, i.id))

    def get(self, idea_id: str) -> IdeaInfo | None:
        if self._info_cache is None:
            self.list_ideas()
        return (self._info_cache or {}).get(idea_id)

    def _load_infos(self) -> dict[str, IdeaInfo]:
        out: dict[str, IdeaInfo] = {}
        for ref in self.list_docs(include_vanilla=True):
            in_mod = not ref.is_vanilla
            for category, ids in ref.categories.items():
                for idea_id in ids:
                    # mod entries overwrite vanilla on id clash (mod refs come
                    # first from list_docs, so only fill if absent or mod wins)
                    prev = out.get(idea_id)
                    if prev is None or (in_mod and not prev.in_mod):
                        out[idea_id] = IdeaInfo(id=idea_id, category=category,
                                                in_mod=in_mod, file=ref.path)
        return out

    # --- validation ---------------------------------------------------------
    def validate(self, docs: list[IdeaDocument], language: str | None = None,
                 sprite_exists=None, trait_names: set[str] | None = None
                 ) -> list[Issue]:
        issues: list[Issue] = []
        seen: dict[str, str] = {}
        known_keys = self.known_category_keys(include_vanilla=True)
        for doc in docs:
            rel = doc.ref.rel_file
            # law categories: at least one default idea expected (mod categories)
            law_flag_cats: dict[str, bool] = {}
            for cat in doc.category_blocks():
                if doc.category_flags(cat.key).get("law"):
                    law_flag_cats[cat.key] = False
            for idea in doc.ideas():
                iid = idea.id
                if not _ID_RE.match(iid):
                    issues.append(Issue("error", "bad_id", iid, rel_file=rel))
                if iid in seen:
                    issues.append(Issue("error", "duplicate_id", iid, seen[iid],
                                        rel_file=rel))
                else:
                    seen[iid] = rel
                if idea.category not in known_keys:
                    issues.append(Issue("error", "unknown_category", iid,
                                        idea.category, rel_file=rel))
                if sprite_exists is not None:
                    sprite = idea.sprite_name()
                    if sprite and not sprite_exists(sprite):
                        issues.append(Issue("warning", "missing_sprite", iid,
                                            sprite, rel_file=rel))
                if language and not self.has_any_name(idea.name_key,
                                                      preferred=language):
                    issues.append(Issue("warning", "missing_loc", iid,
                                        rel_file=rel))
                if idea.category in law_flag_cats and idea.get_flag("default"):
                    law_flag_cats[idea.category] = True
                cost = idea.get_raw("cost")
                if cost and cost.lstrip("-").isdigit() and int(cost) > 0 \
                        and idea.get_raw("removal_cost") == "-1":
                    issues.append(Issue("warning", "removal_cost_minus_one",
                                        iid, rel_file=rel))
                if trait_names is not None:
                    for tr in idea.traits:
                        if tr not in trait_names:
                            issues.append(Issue("warning", "traits_unknown", iid,
                                                tr, rel_file=rel))
            for cat, has_default in law_flag_cats.items():
                if not has_default:
                    issues.append(Issue("warning", "law_no_default", cat,
                                        rel_file=rel))
        return issues
