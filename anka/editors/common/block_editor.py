"""Scratch-like recursive editor for PDX script blocks.

Renders a `Block` as nested visual rows: leaf ``key = value`` pairs become label +
entry, block pairs become bordered containers with their own children and their own
[+] button, and every level ends with a [+] that opens the block picker (logic
containers, the full effect/trigger catalog, or a free-form key). Values are edited
in place (writing straight into the PDX nodes); structural changes re-render.

``custom_effect_tooltip`` (scalar) and ``custom_trigger_tooltip`` (block with a
``tooltip`` key, per the HOI4 docs) get a third field on their loc-key row: the
localized tooltip text for the current language, saved through the owner's
loc protocol (collision-safe, per language). Game rules (``rule = { ... }``)
render as strict yes/no switches.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ...core.pdx import Block, Pair, Scalar
from ...core.pdx import parse as pdx_parse
from ..effects import ScriptCatalog
from .dialogs import BaseDialog, SinglePickDialog

from collections import deque # stack

_OPS = ("=", "<", ">", "<=", ">=", "!=")

# Logic / flow containers offered by the picker, per script kind.
_CONTAINERS: dict[str, tuple[str, ...]] = {
    "trigger": ("AND", "OR", "NOT", "hidden_trigger"),
    "effect": ("if", "limit", "else", "random_list", "hidden_effect"),
}
_TOOLTIP_KEYS = ("custom_effect_tooltip", "custom_trigger_tooltip",
                 "custom_modifier_tooltip")

# Game rules (idea/character `rule = { ... }` blocks): fixed yes/no switches.
# Source: hoi4 wiki, Idea modding → game rules.
_RULE_PARENTS = ("rule",)
_GAME_RULES = (
    "can_access_market", "can_be_spymaster", "can_boost_other_ideologies",
    "can_boost_own_ideology", "can_create_collaboration_government",
    "can_create_factions", "can_declare_war_on_same_ideology",
    "can_declare_war_without_wargoal_when_in_war", "can_decline_call_to_war",
    "can_force_government", "can_generate_female_aces",
    "can_generate_female_country_leaders", "can_generate_female_unit_leaders",
    "can_guarantee_other_ideologies", "can_join_factions",
    "can_join_factions_not_allowed_diplomacy", "can_join_opposite_factions",
    "can_lower_tension", "can_not_build_buildings", "can_not_declare_war",
    "can_occupy_non_war", "can_only_justify_war_on_threat_country",
    "can_puppet", "can_send_volunteers", "can_use_kamikaze_pilots",
    "contributes_operatives", "units_deployed_to_overlord",
)

# ---------------------------------------------------------------------------
# Typed values: instead of free typing, known fields get a "…" picker button
# (searchable list of country tags, states by id+name, ideas, modifiers).
# ---------------------------------------------------------------------------
_KEY_TYPES = {                       # by parameter key inside blocks
    "tag": "country", "target": "country", "country": "country",
    "original_tag": "country", "exile": "country", "owner": "country",
    "controller": "country", "targeted_alliance": "country", "enemy": "country",
    "state": "state",
    "idea": "idea",
}
# Effects that fire an event: their ``id`` field (block form) and their scalar
# form (``country_event = ns.1``) both take an event id — picker offered.
_EVENT_EFFECT_KEYS = ("country_event", "news_event", "state_event",
                      "unit_leader_event", "operative_leader_event")
_VALUE_TYPES = {                     # by effect/trigger name in scalar form
    "country_event": "event", "news_event": "event", "state_event": "event",
    "unit_leader_event": "event", "operative_leader_event": "event",
    "country_exists": "country", "has_war_with": "country",
    "has_war_together_with": "country", "is_ally_with": "country",
    "is_puppet_of": "country", "is_subject_of": "country", "puppet": "country",
    "end_puppet": "country", "give_guarantee": "country", "add_to_faction": "country",
    "is_in_faction_with": "country", "is_neighbor_of": "country",
    "is_owned_by": "country", "is_controlled_by": "country", "is_core_of": "country",
    "is_claimed_by": "country", "white_peace": "country", "declare_war_on": "country",
    "has_defensive_war_with": "country", "has_offensive_war_with": "country",
    "has_wargoal_against": "country", "send_embargo": "country",
    "inherit_technology": "country",
    "owns_state": "state", "controls_state": "state", "add_state_core": "state",
    "add_state_claim": "state", "remove_state_core": "state",
    "remove_state_claim": "state", "transfer_state": "state", "set_capital": "state",
    "has_full_control_of_state": "state",
    "add_ideas": "idea", "remove_ideas": "idea", "has_idea": "idea",
    "show_ideas_tooltip": "idea",
    "complete_national_focus": "focus", "unlock_national_focus": "focus",
    "focus": "focus", "has_completed_focus": "focus",
}
_LIST_ITEM_TYPES = {                 # bare scalars inside ``<key> = { a b c }``
    "add_ideas": "idea", "remove_ideas": "idea", "show_ideas_tooltip": "idea",
    "prioritize": "state", "states": "state", "seller_tags": "country",
}
# Blocks whose child KEYS may be static modifiers (idea/state modifier blocks; the
# picker *adds* a modifiers section there rather than replacing effects/triggers).
_MODIFIER_PARENTS = ("modifier", "modifiers", "hidden_modifier", "targeted_modifier",
                     "state_modifier")


# Effects that must be a block with specific sub-keys — the game docs are terse
# and their catalog `example` is often empty, so a scalar fallback would be wrong.
# Inserted pre-filled with sensible defaults; `target` gets a country picker
# (see _KEY_TYPES). Keep these accurate to the HOI4 script API.
_BLOCK_EFFECT_TEMPLATES = {
    "declare_war_on": "declare_war_on = { target = FROM type = annex_everything }",
    "annex_country": "annex_country = { target = FROM transfer_troops = yes }",
    "create_wargoal": "create_wargoal = { type = annex_everything target = FROM }",
    "start_civil_war": ("start_civil_war = { ideology = communism "
                        "ruling_party = communism size = 0.5 }"),
    "become_exiled_in": "become_exiled_in = { target = FROM legitimacy = 50 }",
    # NB: puppet stays scalar by default (``puppet = TAG`` is idiomatic); the block
    # form is valid too but we don't force it.
    "set_autonomy": ("set_autonomy = { target = FROM "
                     "autonomy_state = autonomy_puppet freedom_level = 0.5 }"),
    "release_autonomy": ("release_autonomy = { target = FROM "
                         "autonomy_state = autonomy_puppet freedom_level = 0.5 }"),
    "diplomatic_relation": ("diplomatic_relation = { country = FROM "
                            "relation = military_access active = yes }"),
    "add_to_war": ("add_to_war = { targeted_alliance = FROM enemy = FROM "
                   "hostility_reason = asked_to_join }"),
    "add_opinion_modifier": ("add_opinion_modifier = { target = FROM "
                             "modifier = opinion_modifier_id }"),
    "remove_opinion_modifier": ("remove_opinion_modifier = { target = FROM "
                                "modifier = opinion_modifier_id }"),
    "set_politics": "set_politics = { ruling_party = communism elections_allowed = yes }",
    "add_dynamic_modifier": "add_dynamic_modifier = { modifier = my_dynamic_modifier }",
    "remove_dynamic_modifier": ("remove_dynamic_modifier = "
                                "{ modifier = my_dynamic_modifier }"),
}

# Optional sub-keys offered as one-click buttons next to a block's [+]
# ("suggested fields"): (key, default value). Hidden once the key is present.
_SUGGESTED_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "add_dynamic_modifier": (("days", "14"), ("scope", "ROOT")),
}

# Effects whose ``modifier`` field names a *dynamic* modifier (picker over
# common/dynamic_modifiers), and whose ``scope`` is a country.
_DYNAMIC_MODIFIER_EFFECTS = ("add_dynamic_modifier", "remove_dynamic_modifier",
                             "force_update_dynamic_modifier")


def value_type_of(parent_key: str, key: str) -> str | None:
    if key == "id" and parent_key in _EVENT_EFFECT_KEYS:
        return "event"
    if parent_key in _DYNAMIC_MODIFIER_EFFECTS:
        if key == "modifier":
            return "dynamic_modifier"
        if key == "scope":
            return "country"
    return _KEY_TYPES.get(key) or _VALUE_TYPES.get(key)


# Scope-iterator effects (``every_country``, ``random_owned_state``, … and their
# ``global_`` variants) are always blocks — ``every_country = { ... }`` — but the
# catalog ships no example for most, so without this they'd wrongly insert as a
# scalar leaf. Matched by the vanilla naming convention.
_SCOPE_ITERATOR_PREFIXES = ("every_", "random_", "global_every_", "global_random_")


def _is_scope_iterator(item) -> bool:
    return (getattr(item, "kind", "") == "effect"
            and item.name.startswith(_SCOPE_ITERATOR_PREFIXES))


def node_from_catalog(item) -> Pair:
    """Build an insertable Pair from a catalog entry — the documented example when it
    parses (instant pre-filled form), otherwise an empty ``name = `` leaf.

    List-form effects (``add_ideas = { a b }`` and friends) insert as an empty
    block; block-form effects (declare_war_on, annex_country, ...) insert a
    pre-filled skeleton so their required sub-keys (target/type) are present;
    scope-iterator effects (every_/random_) insert as an empty block."""
    if item.name in _LIST_ITEM_TYPES:
        return Pair(item.name, Block())
    template = _BLOCK_EFFECT_TEMPLATES.get(item.name)
    if template:
        try:
            root = pdx_parse(template, recover=False)
            for pair in root.pairs():
                if pair.key == item.name:
                    return pair
        except Exception:
            pass
    if item.example:
        try:
            root = pdx_parse(item.example, recover=False)
            for pair in root.pairs():
                if pair.key == item.name:
                    return pair
        except Exception:
            pass
    if _is_scope_iterator(item):
        return Pair(item.name, Block())
    return Pair(item.name, Scalar(""))


class BlockTreeEditor(ttk.Frame):
    def __init__(self, master, owner, root_block: Block, kinds: tuple[str, ...],
                 focus_id: str = "", root_key: str = "",
                 on_change: Callable[[], None] | None = None):
        super().__init__(master, style="Card.TFrame")
        self.owner = owner                    # FocusesEditor (service, loc_language, t)
        self.t = owner.t
        self.palette = owner.palette
        self.root_block = root_block
        self.kinds = kinds
        self.focus_id = focus_id
        self.root_key = root_key              # the script field being edited
        self._on_change = on_change or (lambda: None)

        self._hovered_stack = deque()

        self._canvas = tk.Canvas(self, bg=self.palette.surface, highlightthickness=0, bd=0)
        self._sb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._body = ttk.Frame(self._canvas, style="Card.TFrame")
        self._win = self._canvas.create_window((0, 0), window=self._body, anchor="nw")
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._sb.pack(side="right", fill="y")
        self._body.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(
            self._win, width=e.width))
        self._canvas.bind("<Enter>", lambda e: self._canvas.bind_all(
            "<MouseWheel>", self._wheel))
        self._canvas.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))
        self.render()

    def _wheel(self, event) -> None:
        self._canvas.yview_scroll(-int(event.delta / 120), "units")

    # ------------------------------------------------------------------ render
    def render(self) -> None:
        y = self._canvas.yview()
        # Every box is about to be destroyed; drop the hover stack so a rebuild under
        # a stationary cursor can't re-highlight (or dim) an already-dead widget.
        self._hovered_stack.clear()
        for w in self._body.winfo_children():
            w.destroy()
        self._render_block(self.root_block, self._body, depth=0,
                           parent_key=self.root_key, kinds=self.kinds)
        self._body.update_idletasks()
        self._canvas.yview_moveto(y[0])

    def _render_block(self, block: Block, parent: ttk.Frame, depth: int,
                      parent_key: str = "",
                      kinds: tuple[str, ...] | None = None) -> None:
        kinds = self.kinds if kinds is None else kinds
        for item in list(block.items):
            self._render_item(block, item, parent, depth, parent_key, kinds)
        self._add_button(block, parent, parent_key, kinds)

    def _render_item(self, owner_block: Block, item, parent, depth: int,
                     parent_key: str = "",
                     kinds: tuple[str, ...] | None = None) -> None:
        kinds = self.kinds if kinds is None else kinds
        if isinstance(item, Pair) and isinstance(item.value, Block):
            self._render_container(owner_block, item, parent, depth, kinds)
        elif isinstance(item, Pair):
            self._render_leaf(owner_block, item, parent, parent_key, kinds)
        elif isinstance(item, Scalar):
            self._render_bare(owner_block, item, parent, parent_key)
        elif isinstance(item, Block):                 # rare: anonymous block
            self._render_container(owner_block, item, parent, depth, kinds)

    def _row(self, parent) -> ttk.Frame:
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=1, anchor="w")
        return row

    def _del_btn(self, row, owner_block: Block, item) -> None:
        ttk.Button(row, text="✕", width=2,
                   command=lambda: self._remove(owner_block, item)).pack(side="right", padx=2)

    def _remove(self, owner_block: Block, item) -> None:
        if item in owner_block.items:
            owner_block.items.remove(item)
        self.render()
        self._on_change()

    def _key_widget(self, row, pair: Pair) -> None:
        """Known keywords render as labels; unknown keys stay editable."""
        known = (ScriptCatalog.find(pair.key) is not None
                 or pair.key in ScriptCatalog.modifiers()
                 or pair.key in _GAME_RULES
                 or pair.key in _CONTAINERS["trigger"] + _CONTAINERS["effect"]
                 or pair.key in _TOOLTIP_KEYS
                 or pair.key in ("limit", "else_if", "tooltip", "factor", "add",
                                 "base", "modifier", "rule"))
        if known:
            ttk.Label(row, text=pair.key, style="Card.TLabel").pack(side="left")
        else:
            var = tk.StringVar(value=pair.key)
            entry = ttk.Entry(row, textvariable=var, width=max(8, len(pair.key) + 2))
            entry.pack(side="left")
            var.trace_add("write", lambda *_: (setattr(pair, "key", var.get().strip()),
                                               self._on_change()))

    def _op_editable(self, pair: Pair, kinds: tuple[str, ...]) -> bool:
        """Whether this leaf may use comparison operators. Only triggers can:
        effects, static modifiers and game rules are always plain
        ``key = value`` assignments.

        * Effect-only context  -> never editable.
        * Modifier / rule keys -> never editable, wherever they appear.
        * Trigger-only context -> editable (e.g. inside ``limit``).
        * Mixed context        -> decide per key: triggers (and unknown keys,
          which may be scripted-variable comparisons) are editable.
        """
        if "trigger" not in kinds:
            return False
        if pair.key in ScriptCatalog.modifiers() or pair.key in _GAME_RULES \
                or pair.key == "tooltip":
            return False
        if kinds == ("trigger",):
            return True
        item = ScriptCatalog.find(pair.key)
        return item is None or item.kind != "effect"

    def _render_leaf(self, owner_block: Block, pair: Pair, parent,
                     parent_key: str = "",
                     kinds: tuple[str, ...] | None = None) -> None:
        kinds = self.kinds if kinds is None else kinds
        row = self._row(parent)
        self._key_widget(row, pair)

        # Comparison operators (<, >, <=, >=, !=) are only meaningful for triggers.
        # Effects are always plain assignments, so their operator is a fixed "="
        # label the user cannot change (see hoi4 wiki: Triggers vs Effects).
        if self._op_editable(pair, kinds):
            op_var = tk.StringVar(value=pair.op)
            op = ttk.Combobox(row, textvariable=op_var, values=_OPS, width=3,
                              state="readonly")
            op.pack(side="left", padx=3)
            op_var.trace_add("write", lambda *_: (setattr(pair, "op", op_var.get()),
                                                  self._on_change()))
        else:
            if pair.op != "=":
                pair.op = "="          # normalise any stray operator on an effect
            ttk.Label(row, text="=", style="Card.TLabel").pack(side="left", padx=3)

        scalar = pair.value
        val_var = tk.StringVar(value=scalar.raw)
        is_rule = (pair.key in _GAME_RULES or parent_key in _RULE_PARENTS)
        if is_rule:
            # game rules are strict yes/no switches
            box = ttk.Combobox(row, textvariable=val_var, width=5,
                               state="readonly", values=("yes", "no"))
            box.pack(side="left")
        else:
            entry = ttk.Entry(row, textvariable=val_var, width=24)
            entry.pack(side="left")

        def value_changed(*_a) -> None:
            text = val_var.get()
            scalar.raw = text
            scalar.quoted = scalar.quoted or (" " in text)
            self._on_change()
        val_var.trace_add("write", value_changed)

        vtype = value_type_of(parent_key, pair.key)
        if vtype is not None and not is_rule:
            self._picker_button(row, vtype, val_var)
        if pair.key in _TOOLTIP_KEYS or (pair.key == "tooltip"
                                         and parent_key == "custom_trigger_tooltip"):
            self._tooltip_loc_field(row, val_var)
        self._del_btn(row, owner_block, pair)

    def _picker_button(self, row, vtype: str, val_var: tk.StringVar) -> None:
        """The '…' button next to typed values: searchable pick list instead of
        hand-typing tags / state ids / idea ids / modifier names."""
        def open_picker() -> None:
            if vtype == "dynamic_modifier":
                # resolved here (not per-editor value_options) so every script
                # editor gets the picker without its own service wiring
                from ...services.dynamic_modifier_service import \
                    DynamicModifierService
                options = DynamicModifierService.options(self.owner.context)
            else:
                options = self.owner.value_options(vtype)
            SinglePickDialog(self, self.owner, self.t(f"focuses.pick.{vtype}"),
                             options,
                             lambda value: val_var.set(value),
                             current=val_var.get().strip())
        ttk.Button(row, text="…", width=2, command=open_picker).pack(side="left", padx=2)

    def _tooltip_loc_field(self, row, key_var: tk.StringVar) -> None:
        """Extra field on custom_*_tooltip rows: the localized text behind the key.
        Reads/writes through the owner's `loc_get`/`loc_set` protocol methods."""
        lang = self.owner.loc_language
        ttk.Label(row, text="📝", style="CardMuted.TLabel").pack(side="left", padx=(8, 2))
        text_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=text_var, width=30)
        entry.pack(side="left")

        loading = {"on": False}

        def load(*_a) -> None:
            loading["on"] = True
            key = key_var.get().strip()
            text_var.set(self.owner.loc_get(key, lang) if key else "")
            loading["on"] = False

        job: list = [None]

        def save() -> None:
            job[0] = None
            key = key_var.get().strip()
            text = text_var.get().strip()
            if key and text:
                self.owner.loc_set(key, lang, text)

        def schedule(*_a) -> None:
            if loading["on"]:
                return
            if job[0] is not None:
                entry.after_cancel(job[0])
            job[0] = entry.after(900, save)

        load()
        text_var.trace_add("write", schedule)
        entry.bind("<FocusOut>", lambda e: save())
        key_var.trace_add("write", load)

    def _render_bare(self, owner_block: Block, scalar: Scalar, parent,
                     parent_key: str = "") -> None:
        row = self._row(parent)
        var = tk.StringVar(value=scalar.raw)
        ttk.Entry(row, textvariable=var, width=20).pack(side="left")
        var.trace_add("write", lambda *_: (setattr(scalar, "raw", var.get()),
                                           self._on_change()))
        vtype = _LIST_ITEM_TYPES.get(parent_key)
        if vtype is not None:
            self._picker_button(row, vtype, var)
        self._del_btn(row, owner_block, scalar)

    def _render_container(self, owner_block: Block, item, parent, depth: int,
                          kinds: tuple[str, ...] | None = None) -> None:
        kinds = self.kinds if kinds is None else kinds
        pair = item if isinstance(item, Pair) else None
        block = item.value if pair is not None else item
        box = tk.Frame(parent, bg=self.palette.surface,
                       highlightbackground=self.palette.border,
                       highlightcolor=self.palette.border, highlightthickness=1)
        box.pack(fill="x", pady=2, padx=(depth and 0, 0), anchor="w")
        self._add_hover(box)
        head = ttk.Frame(box, style="Card.TFrame")
        head.pack(fill="x", padx=4, pady=(3, 0))
        if pair is not None:
            self._key_widget(head, pair)
            ttk.Label(head, text="= {", style="CardMuted.TLabel").pack(side="left", padx=4)
        ttk.Button(head, text="✕", width=2,
                   command=lambda: self._remove(owner_block, item)).pack(side="right", padx=2)
        body = ttk.Frame(box, style="Card.TFrame")
        body.pack(fill="x", padx=(18, 4), pady=(0, 4))
        self._render_block(block, body, depth + 1,
                           parent_key=pair.key if pair is not None else "",
                           kinds=self._child_kinds(pair, kinds))

    def _child_kinds(self, pair: Pair | None, kinds: tuple[str, ...]) -> tuple[str, ...]:
        """Narrow the allowed script kinds when descending into a known container, so
        the operator rule (triggers only) reaches parameter sub-keys the catalog does
        not list on their own (``type``, ``ideology``, ``instant_build`` …).

        A ``limit`` / logic-trigger block holds trigger conditions only — never
        effects — even inside an effect script; a known effect/trigger keyword forces
        its whole subtree to that kind. Unknown containers keep the parent's kinds.
        """
        if pair is None:
            return kinds
        key = pair.key
        if key == "limit" or key in _CONTAINERS["trigger"]:
            return ("trigger",)
        if key in _CONTAINERS["effect"]:
            return ("effect",)
        item = ScriptCatalog.find(key)
        if item is not None and item.kind in ("effect", "trigger"):
            return (item.kind,)
        return kinds

    def _add_hover(self, box: tk.Frame) -> None:
        """Highlight only the innermost container under the pointer (accent border, a
        touch thicker). A stack tracks the nesting so leaving a child restores its
        parent's highlight; `render()` clears the stack when boxes are destroyed."""
        def enter(_e=None) -> None:
            if self._hovered_stack:
                self._set_border(self._hovered_stack[-1], hot=False)
            self._hovered_stack.append(box)
            self._set_border(box, hot=True)

        def leave(_e=None) -> None:
            # Unwind to (and including) this box: children entered after it may have
            # already been popped, so guard against it no longer being on the stack.
            if box in self._hovered_stack:
                while self._hovered_stack:
                    if self._set_border(self._hovered_stack.pop(), hot=False) is box:
                        break
            if self._hovered_stack:
                self._set_border(self._hovered_stack[-1], hot=True)

        box.bind("<Enter>", enter, add="+")
        box.bind("<Leave>", leave, add="+")

    def _set_border(self, box: tk.Frame, *, hot: bool) -> tk.Frame:
        """Colour a box's border, tolerating boxes destroyed by a re-render."""
        if box.winfo_exists():
            colour = self.palette.accent if hot else self.palette.border
            box.configure(highlightbackground=colour, highlightcolor=colour,
                          highlightthickness=2 if hot else 1)
        return box

    def _add_button(self, block: Block, parent, parent_key: str = "",
                    kinds: tuple[str, ...] | None = None) -> None:
        kinds = self.kinds if kinds is None else kinds
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=1, anchor="w")
        # Suggested optional sub-keys (e.g. days / scope on add_dynamic_modifier):
        # one click inserts the field pre-filled; hidden once present.
        for key, default in _SUGGESTED_FIELDS.get(parent_key, ()):
            if not block.has(key):
                ttk.Button(row, text=f"➕ {key}", width=len(key) + 3,
                           command=lambda k=key, d=default: self._add_suggested(
                               block, k, d)).pack(side="left", padx=(0, 4))
        ttk.Button(row, text="➕", width=3,
                   command=lambda: self._pick_new(block, parent_key, kinds)).pack(side="left")
        # Inside a list-form effect (add_ideas = { a b }, prioritize = {...}, ...)
        # offer a one-click "add item" that appends a bare value with a picker.
        list_type = _LIST_ITEM_TYPES.get(parent_key)
        if list_type is not None:
            ttk.Button(row, text="➕ " + self.t(f"focuses.pick.{list_type}"),
                       command=lambda: self._add_bare(block)).pack(side="left", padx=4)

    def _add_suggested(self, block: Block, key: str, default: str) -> None:
        block.items.append(Pair(key, Scalar(default)))
        self.render()
        self._on_change()

    def _add_bare(self, block: Block) -> None:
        block.items.append(Scalar(""))
        self.render()
        self._on_change()

    # ------------------------------------------------------------------ picker
    def _pick_new(self, block: Block, parent_key: str = "",
                  kinds: tuple[str, ...] | None = None) -> None:
        BlockPickerDialog(self, self.owner, self.kinds if kinds is None else kinds,
                          lambda node: self._insert(block, node),
                          with_modifiers=parent_key in _MODIFIER_PARENTS,
                          with_rules=parent_key in _RULE_PARENTS)

    def _insert(self, block: Block, node) -> None:
        block.items.append(node)
        self.render()
        self._on_change()


class BlockPickerDialog(BaseDialog):
    """Searchable picker: logic containers, special blocks, the effect/trigger
    catalog and free-form entries. Returns a ready PDX node via `on_pick`."""

    def __init__(self, master, editor, kinds: tuple[str, ...],
                 on_pick: Callable[[object], None], with_modifiers: bool = False,
                 with_rules: bool = False):
        super().__init__(master, editor, editor.t("focuses.blocks.add"), (520, 540))
        self._kinds = kinds
        self._on_pick = on_pick
        self._with_modifiers = with_modifiers
        self._with_rules = with_rules

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=3)
        body.rowconfigure(2, weight=2)
        body.columnconfigure(0, weight=1)

        self._query = tk.StringVar()
        self._query.trace_add("write", lambda *_: self._refresh())
        entry = ttk.Entry(body, textvariable=self._query)
        entry.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._submit())

        self._list = tk.Listbox(body, bg=self.palette.surface_alt, fg=self.palette.text,
                                relief="flat", selectbackground=self.palette.accent,
                                font=("Consolas", 10), exportselection=False)
        self._list.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._list.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._list.configure(yscrollcommand=sb.set)
        self._list.bind("<Double-1>", lambda e: self._submit())
        self._list.bind("<<ListboxSelect>>", self._show_doc)

        self._doc = tk.Text(body, height=7, wrap="word", state="disabled",
                            bg=self.palette.surface, fg=self.palette.text_muted,
                            relief="flat", font=("Consolas", 9))
        self._doc.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(6, 0))

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text=self.t("common.add"), style="Accent.TButton",
                   command=self._submit).pack(side="right")

        self._entries: list[tuple[str, object]] = []
        self._refresh()

    # entry payloads: ("container", key) | ("tooltip", key) | ("catalog", item)
    #                 | ("custom_leaf", None) | ("custom_block", None)
    def _refresh(self) -> None:
        q = self._query.get().strip().lower()
        t = self.t
        entries: list[tuple[str, object]] = []
        for kind in ("trigger", "effect"):
            if kind not in self._kinds:
                continue
            for key in _CONTAINERS[kind]:
                if not q or q in key.lower():
                    entries.append((f"{{}}  {key}", ("container", key)))
        if "effect" in self._kinds and (not q or q in "custom_effect_tooltip"):
            entries.append(("📝  custom_effect_tooltip", ("tooltip", "custom_effect_tooltip")))
        if "trigger" in self._kinds and (not q or q in "custom_trigger_tooltip"):
            entries.append(("📝  custom_trigger_tooltip", ("tooltip", "custom_trigger_tooltip")))
        if (self._with_modifiers or "modifier" in self._kinds) \
                and (not q or q in "custom_modifier_tooltip"):
            entries.append(("📝  custom_modifier_tooltip",
                            ("tooltip", "custom_modifier_tooltip")))
        entries.append(("✏  " + t("focuses.blocks.custom_leaf"), ("custom_leaf", None)))
        entries.append(("✏  " + t("focuses.blocks.custom_block"), ("custom_block", None)))
        if self._with_rules:
            for rule in _GAME_RULES:
                if not q or q in rule:
                    entries.append((f"📏  {rule}", ("rule", rule)))
        badge = {"effect": "⚡", "trigger": "❔", "modifier": "📊"}
        kinds = (("modifier",) if self._with_modifiers else ()) + tuple(self._kinds)
        for kind in kinds:
            for item in ScriptCatalog.search(q, kind)[:250]:
                entries.append((f"{badge[kind]} {item.title}", ("catalog", item)))
        self._entries = entries
        self._list.delete(0, "end")
        for label, _p in entries:
            self._list.insert("end", label)

    def _selected(self):
        sel = self._list.curselection()
        return self._entries[sel[0]][1] if sel else None

    def _show_doc(self, _event=None) -> None:
        payload = self._selected()
        self._doc.configure(state="normal")
        self._doc.delete("1.0", "end")
        if payload and payload[0] == "catalog":
            item = payload[1]
            parts = [item.title]
            if item.scopes:
                parts.append(f"{self.t('focuses.script.scope')}: {', '.join(item.scopes)}")
            if item.description:
                parts.append("")
                parts.append(item.description)
            if item.example:
                parts.append("")
                parts.append(item.example)
            self._doc.insert("1.0", "\n".join(parts))
        self._doc.configure(state="disabled")

    def _submit(self) -> None:
        payload = self._selected()
        if payload is None:
            # allow typing a raw key and pressing Enter with nothing selected
            text = self._query.get().strip()
            if text.replace("_", "").isalnum():
                payload = ("typed", text)
            else:
                return
        kind, data = payload
        typed = self._query.get().strip()
        if kind == "container":
            # `if` (and else_if) is meaningless without a `limit` holding the
            # conditions — pre-create it so the structure is always valid.
            inner = (Block([Pair("limit", Block())])
                     if data in ("if", "else_if") else Block())
            node = Pair(data, inner)
        elif kind == "tooltip":
            master = self.master
            fid = getattr(master, "focus_id", "") or "my_focus"
            if data == "custom_trigger_tooltip":
                # block form per the HOI4 docs: the loc key sits in `tooltip`
                node = Pair(data, Block([Pair("tooltip", Scalar(f"{fid}_tt"))]))
            else:
                node = Pair(data, Scalar(f"{fid}_tt"))
        elif kind == "rule":
            node = Pair(data, Scalar("yes"))
        elif kind == "catalog":
            node = node_from_catalog(data)
        elif kind == "custom_block":
            node = Pair(typed if typed.replace("_", "").isalnum() else "key", Block())
        else:  # custom_leaf / typed
            key = data if kind == "typed" else (
                typed if typed.replace("_", "").isalnum() else "key")
            node = Pair(key, Scalar(""))
        self.destroy()
        self._on_pick(node)
