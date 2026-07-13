"""Read/write decisions for the Decisions editor.

Sources (mod first, base game as fallback):
  * ``common/decisions/*.txt`` — files of ``<category> = { <decision> = {...} }``;
    the same category may gain decisions from several files (additive).
  * ``common/decisions/categories/*.txt`` — category definitions (icon, picture,
    priority, allowed/visible/available).

Like `FocusService`, the model is *block-backed*: `Decision` / `CategoryDef` are thin
views over parsed PDX nodes, so unknown keys survive load/save round-trips untouched.
A decision's id is the *pair key* inside its category block.
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
from ._locutil import LocCatalog
from ._pdxview import BlockView

# Tri-state boolean flags on a decision (absent = game default).
DECISION_FLAG_DEFAULTS = {
    "fire_only_once": False,
    "is_good": False,
    "selectable_mission": False,
    "cancel_if_not_visible": False,
    "targets_dynamic": False,
    "state_target": False,         # target a map state rather than a country
    "fixed_random_seed": True,     # missions: no = reroll random effects on restart
}

# Script fields grouped by what the visual editor should offer.
DECISION_TRIGGER_FIELDS = ("allowed", "visible", "available", "activation",
                           "target_trigger", "target_root_trigger",
                           "cancel_trigger", "remove_trigger",
                           "custom_cost_trigger")
DECISION_EFFECT_FIELDS = ("complete_effect", "remove_effect",
                          "timeout_effect", "cancel_effect")
DECISION_MODIFIER_FIELDS = ("modifier", "targeted_modifier")
DECISION_OTHER_FIELDS = ("ai_will_do", "targets", "highlight_states")
DECISION_SCRIPT_FIELDS = (DECISION_TRIGGER_FIELDS + DECISION_EFFECT_FIELDS
                          + DECISION_MODIFIER_FIELDS + DECISION_OTHER_FIELDS)

# on_map_mode is a plain enum (not a script block), edited as a General dropdown.
ON_MAP_MODE_VALUES = ("map_only", "decision_view_only", "map_and_decisions_view")

CATEGORY_SCRIPT_FIELDS = ("allowed", "visible", "available")

_ID_RE = re.compile(r"^\w+$")


class _BlockView(BlockView):
    """Decision-flavoured `BlockView` (shared helper lives in ``_pdxview``)."""

    FLAG_DEFAULTS = DECISION_FLAG_DEFAULTS


class Decision(_BlockView):
    """A view over one ``<id> = { ... }`` pair inside a category block."""

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

    # icon: scalar (short "generic_research" or full "GFX_decision_x") or a
    # conditional block with ``key = <name>`` + trigger.
    @property
    def icon(self) -> str:
        v = self.block.get("icon")
        if isinstance(v, Scalar):
            return v.raw
        if isinstance(v, Block):
            return v.get_scalar("key", "") or ""
        return ""

    @icon.setter
    def icon(self, value: str) -> None:
        v = self.block.get("icon")
        if isinstance(v, Block):
            v.set("key", value)
        elif value:
            self.block.set("icon", value)
        else:
            self.block.remove("icon")

    @property
    def name_key(self) -> str:
        """The localisation key: explicit ``name = X`` override or the id."""
        return self.get_raw("name") or self.id

    def sprite_name(self) -> str:
        """Resolve the icon field into a sprite name (short names get the
        ``GFX_decision_`` prefix, full GFX_ names pass through)."""
        icon = self.icon
        if not icon or icon.startswith("GFX_"):
            return icon
        return f"GFX_decision_{icon}"

    def __repr__(self) -> str:
        return f"Decision({self.id!r} in {self.category!r})"


class CategoryDef(_BlockView):
    """A view over one category definition in common/decisions/categories."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def name(self) -> str:
        return self.pair.key

    @property
    def icon(self) -> str:
        v = self.block.get("icon")
        if isinstance(v, Scalar):
            return v.raw
        if isinstance(v, Block):                      # conditional icons
            first = v.get_scalar("key", "")
            return first or ""
        return ""

    @icon.setter
    def icon(self, value: str) -> None:
        self.set_raw("icon", value)

    def sprite_name(self) -> str:
        icon = self.icon
        if not icon or icon.startswith("GFX_"):
            return icon
        return f"GFX_decision_category_{icon}"


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class DecisionDocRef:
    rel_file: str                     # e.g. "common/decisions/AFG.txt"
    source_root: Path
    is_vanilla: bool
    kind: str                         # "decisions" | "categories"
    edited: bool = False              # vanilla file overridden by the mod
    categories: dict[str, list[str]] = field(default_factory=dict)  # quick scan

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class DecisionDocument:
    ref: DecisionDocRef
    root: Block

    def category_blocks(self) -> list[Pair]:
        return [p for p in self.root.pairs() if isinstance(p.value, Block)]

    def decisions(self) -> list[Decision]:
        out: list[Decision] = []
        for cat in self.category_blocks():
            for p in cat.value.pairs():
                if isinstance(p.value, Block):
                    out.append(Decision(p, cat.key))
        return out

    def find(self, decision_id: str) -> Decision | None:
        for d in self.decisions():
            if d.id == decision_id:
                return d
        return None

    def category_defs(self) -> list[CategoryDef]:
        """Only meaningful for kind == 'categories' documents."""
        return [CategoryDef(p) for p in self.root.pairs() if isinstance(p.value, Block)]

    def find_category(self, name: str) -> CategoryDef | None:
        for c in self.category_defs():
            if c.name == name:
                return c
        return None


@dataclass
class Issue:
    severity: str        # "error" | "warning"
    code: str            # locale key suffix: decisions.issue.<code>
    subject: str = ""    # decision/category id
    detail: str = ""


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

# ids may contain dots (vanilla debug decisions like ``DEBUG_x.3``)
_KEY_OPEN_RE = re.compile(r"^\s*([\w.]+)\s*=\s*\{")

_NEW_FILE = "anka_decisions.txt"
_NEW_CAT_FILE = "anka_decision_categories.txt"


class DecisionService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, DecisionDocument]] = {}
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="decision",
                              default_pattern="anka_decisions_l_{lang}.yml",
                              dep_roots=context.dependency_paths)

    # --- listing -----------------------------------------------------------
    def list_docs(self, include_vanilla: bool = False,
                  kind: str = "decisions") -> list[DecisionDocRef]:
        folder_rel = (GAME_DIRS.DECISIONS if kind == "decisions"
                      else GAME_DIRS.DECISION_CATEGORIES)
        return layered_docs(
            self.ctx, folder_rel,
            lambda root, is_vanilla: self._scan_dir(root, folder_rel, is_vanilla, kind),
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    def _scan_dir(self, root: Path, folder_rel: str, is_vanilla: bool,
                  kind: str) -> list[DecisionDocRef]:
        folder = root / folder_rel
        if not folder.is_dir():
            return []
        refs: list[DecisionDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            # the categories subfolder lives inside common/decisions — skip dirs
            if not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            refs.append(DecisionDocRef(
                rel_file=f"{folder_rel}/{file.name}", source_root=root,
                is_vanilla=is_vanilla, kind=kind,
                categories=self._quick_scan(text)))
        return refs

    @staticmethod
    def _quick_scan(text: str) -> dict[str, list[str]]:
        """Cheap category -> decision-ids map for the list panel (no full parse).

        Depth is tracked by brace counting — vanilla indentation is too inconsistent
        (tabs, double tabs, spaces) to rely on."""
        out: dict[str, list[str]] = {}
        depth = 0
        current: str | None = None
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            m = _KEY_OPEN_RE.match(code)
            if m:
                if depth == 0:
                    current = m.group(1)
                    out.setdefault(current, [])
                elif depth == 1 and current is not None:
                    out[current].append(m.group(1))
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return out

    # --- load / save --------------------------------------------------------
    def load(self, ref: DecisionDocRef) -> DecisionDocument:
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
        doc = DecisionDocument(ref=ref, root=parse_file(path))
        self._doc_cache[path] = (mtime, doc)
        return doc

    def save(self, doc: DecisionDocument) -> Path:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write into the base game")
        path = ensure_filename_case(doc.ref.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, path)
        try:
            self._doc_cache[path] = (path.stat().st_mtime, doc)
        except OSError:
            pass
        return path

    def copy_to_mod(self, ref: DecisionDocRef) -> DecisionDocRef:
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        return DecisionDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                              is_vanilla=False, kind=ref.kind, edited=True,
                              categories=dict(ref.categories))

    def delete(self, ref: DecisionDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete vanilla files")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(ref.path, None)

    # --- category definitions ------------------------------------------------
    def category_names(self, include_vanilla: bool = True) -> list[str]:
        """All known category names: definitions + categories used in decisions."""
        names: set[str] = set()
        for ref in self.list_docs(include_vanilla, kind="categories"):
            names.update(ref.categories)
        for ref in self.list_docs(include_vanilla, kind="decisions"):
            names.update(ref.categories)
        return sorted(names)

    def find_category_def(self, name: str,
                          include_vanilla: bool = True
                          ) -> tuple[DecisionDocRef, CategoryDef] | None:
        """Locate a category definition (mod first, then vanilla)."""
        for ref in self.list_docs(include_vanilla, kind="categories"):
            if name not in ref.categories:
                continue
            doc = self.load(ref)
            cat = doc.find_category(name)
            if cat is not None:
                return ref, cat
        return None

    def create_category(self, name: str, icon: str = "generic_research") -> DecisionDocRef:
        """Add a category definition to ANKA's category file in the mod."""
        path = self.ctx.mod.path / GAME_DIRS.DECISION_CATEGORIES / _NEW_CAT_FILE
        root = parse_file(path) if path.exists() else Block()
        block = Block()
        block.set("icon", icon)
        root.add(name, block)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, path)
        self._doc_cache.pop(path, None)
        rel = f"{GAME_DIRS.DECISION_CATEGORIES}/{_NEW_CAT_FILE}"
        return DecisionDocRef(rel_file=rel, source_root=self.ctx.mod.path,
                              is_vanilla=False, kind="categories",
                              categories={name: []})

    def count_mod_decisions(self, category: str) -> int:
        """How many mod decisions live in `category` (for delete confirmation)."""
        return sum(len(ref.categories.get(category, ()))
                   for ref in self.list_docs(False, kind="decisions"))

    def remove_category(self, name: str) -> int:
        """Delete a category from the mod: its definition(s) AND every mod decision
        block under it. Returns the number of decisions removed. Vanilla files are
        never touched — the category may still exist in the base game."""
        removed = 0
        for ref in self.list_docs(False, kind="categories"):
            if name not in ref.categories:
                continue
            doc = self.load(ref)
            before = len(doc.root.items)
            doc.root.items = [it for it in doc.root.items
                              if not (isinstance(it, Pair) and it.key == name)]
            if len(doc.root.items) != before:
                self.save(doc)
        for ref in self.list_docs(False, kind="decisions"):
            if name not in ref.categories:
                continue
            doc = self.load(ref)
            for pair in list(doc.root.pairs()):
                if pair.key == name and isinstance(pair.value, Block):
                    removed += sum(1 for p in pair.value.pairs()
                                   if isinstance(p.value, Block))
                    doc.root.items.remove(pair)
            self.save(doc)
        return removed

    # --- decision CRUD --------------------------------------------------------
    def mod_target_doc(self, category: str) -> DecisionDocument:
        """The mod document new decisions of `category` should go to: the first mod
        file already containing that category, else ANKA's decisions file."""
        for ref in self.list_docs(False, kind="decisions"):
            if category in ref.categories:
                return self.load(ref)
        path = self.ctx.mod.path / GAME_DIRS.DECISIONS / _NEW_FILE
        if path.exists():
            ref = DecisionDocRef(rel_file=f"{GAME_DIRS.DECISIONS}/{_NEW_FILE}",
                                 source_root=self.ctx.mod.path, is_vanilla=False,
                                 kind="decisions")
            return self.load(ref)
        ref = DecisionDocRef(rel_file=f"{GAME_DIRS.DECISIONS}/{_NEW_FILE}",
                             source_root=self.ctx.mod.path, is_vanilla=False,
                             kind="decisions")
        return DecisionDocument(ref=ref, root=Block())

    def add_decision(self, doc: DecisionDocument, category: str, did: str,
                     icon: str = "generic_research") -> Decision:
        cat_block = None
        for p in doc.root.pairs():
            if p.key == category and isinstance(p.value, Block):
                cat_block = p.value
                break
        if cat_block is None:
            cat_block = Block()
            doc.root.add(category, cat_block)
        block = Block()
        block.set("icon", icon)
        block.set("cost", 50)
        block.add("complete_effect", Block())
        pair = Pair(did, block)
        cat_block.items.append(pair)
        return Decision(pair, category)

    def remove_decision(self, doc: DecisionDocument, decision: Decision) -> None:
        for cat in doc.category_blocks():
            if decision.pair in cat.value.items:
                cat.value.items.remove(decision.pair)
                return

    def duplicate_decision(self, doc: DecisionDocument, decision: Decision,
                           new_id: str) -> Decision:
        copy_root = pdx_parse(dumps(Block([Pair(decision.id, decision.block)])))
        pair = next(p for p in copy_root.pairs())
        pair.key = new_id
        for cat in doc.category_blocks():
            if decision.pair in cat.value.items:
                cat.value.items.append(pair)
                return Decision(pair, cat.key)
        raise ValueError("decision not in document")

    def find_decision(self, decision_id: str
                      ) -> tuple[DecisionDocument, Decision] | None:
        """Locate a live `Decision` (and its document) by id across every layer
        (mod + dependencies + vanilla). Used to base a new decision on an old one."""
        for ref in self.list_docs(include_vanilla=True, kind="decisions"):
            try:
                doc = self.load(ref)
            except Exception:
                continue
            hit = doc.find(decision_id)
            if hit is not None:
                return doc, hit
        return None

    def create_decision_from(self, doc: DecisionDocument, category: str,
                             new_id: str, source: Decision) -> Decision:
        """Create ``new_id`` in ``doc``/``category`` as a deep copy of ``source``.
        The explicit ``name =`` override, if any, is dropped so the copy derives its
        own loc keys from ``new_id`` (localisation text is copied by the editor)."""
        copy_root = pdx_parse(dumps(Block([Pair(source.id, source.block)])))
        pair = next(p for p in copy_root.pairs())
        pair.key = new_id
        new = Decision(pair, category)
        new.set_raw("name", "")            # use id-derived loc keys, not the source's
        cat_block = None
        for p in doc.root.pairs():
            if p.key == category and isinstance(p.value, Block):
                cat_block = p.value
                break
        if cat_block is None:
            cat_block = Block()
            doc.root.add(category, cat_block)
        cat_block.items.append(pair)
        return new

    def move_decision(self, doc: DecisionDocument, decision: Decision,
                      new_category: str) -> Decision:
        """Move a decision to another category (within the same document)."""
        self.remove_decision(doc, decision)
        cat_block = None
        for p in doc.root.pairs():
            if p.key == new_category and isinstance(p.value, Block):
                cat_block = p.value
                break
        if cat_block is None:
            cat_block = Block()
            doc.root.add(new_category, cat_block)
        cat_block.items.append(decision.pair)
        decision.category = new_category
        return decision

    def rename_decision(self, decision: Decision, new_id: str) -> str:
        if not _ID_RE.match(new_id):
            raise ValueError(f"Invalid id: {new_id!r}")
        old = decision.id
        decision.id = new_id
        self.loc.rename(old, new_id)
        return old

    # --- localisation ----------------------------------------------------------
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

    # --- validation ---------------------------------------------------------------
    def validate(self, docs: list[DecisionDocument], language: str | None = None,
                 sprite_exists=None) -> list[Issue]:
        issues: list[Issue] = []
        seen: dict[str, str] = {}
        # Only *defined* categories count — a category used in a decisions file but
        # never defined under decisions/categories will not show up in game.
        known_categories = {name
                            for ref in self.list_docs(True, kind="categories")
                            for name in ref.categories}
        for doc in docs:
            for decision in doc.decisions():
                did = decision.id
                if not _ID_RE.match(did):
                    issues.append(Issue("error", "bad_id", did))
                if did in seen:
                    issues.append(Issue("error", "duplicate_id", did, seen[did]))
                else:
                    seen[did] = decision.category
                if decision.category not in known_categories:
                    issues.append(Issue("error", "unknown_category", did,
                                        decision.category))
                if sprite_exists is not None:
                    sprite = decision.sprite_name()
                    if sprite and not sprite_exists(sprite):
                        issues.append(Issue("warning", "missing_icon", did, sprite))
                if language and not self.has_any_name(decision.name_key,
                                                      preferred=language):
                    issues.append(Issue("warning", "missing_loc", did))
                if decision.get_script("target_trigger") and \
                        not decision.get_script("targets") and \
                        not decision.get_flag("targets_dynamic"):
                    issues.append(Issue("warning", "targets_missing", did))
                if decision.get_flag("selectable_mission") and \
                        not decision.get_raw("days_mission_timeout"):
                    issues.append(Issue("warning", "mission_no_timeout", did))
        return issues
