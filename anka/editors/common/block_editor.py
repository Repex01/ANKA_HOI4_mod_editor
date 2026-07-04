"""Scratch-like recursive editor for PDX script blocks.

Renders a `Block` as nested visual rows: leaf ``key = value`` pairs become label +
entry, block pairs become bordered containers with their own children and their own
[+] button, and every level ends with a [+] that opens the block picker (logic
containers, the full effect/trigger catalog, or a free-form key). Values are edited
in place (writing straight into the PDX nodes); structural changes re-render.

``custom_effect_tooltip`` / ``custom_trigger_tooltip`` scalar pairs get a third field:
the localized tooltip text for the current language, saved through the focus service
(collision-safe, per language).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ...core.pdx import Block, Pair, Scalar
from ...core.pdx import parse as pdx_parse
from ..effects import ScriptCatalog
from .dialogs import BaseDialog, SinglePickDialog

_OPS = ("=", "<", ">", "<=", ">=", "!=")

# Logic / flow containers offered by the picker, per script kind.
_CONTAINERS: dict[str, tuple[str, ...]] = {
    "trigger": ("AND", "OR", "NOT", "hidden_trigger"),
    "effect": ("if", "limit", "else", "random_list", "hidden_effect"),
}
_TOOLTIP_KEYS = ("custom_effect_tooltip", "custom_trigger_tooltip")

# ---------------------------------------------------------------------------
# Typed values: instead of free typing, known fields get a "…" picker button
# (searchable list of country tags, states by id+name, ideas, modifiers).
# ---------------------------------------------------------------------------
_KEY_TYPES = {                       # by parameter key inside blocks
    "tag": "country", "target": "country", "country": "country",
    "original_tag": "country", "exile": "country", "owner": "country",
    "controller": "country",
    "state": "state",
    "idea": "idea",
}
_VALUE_TYPES = {                     # by effect/trigger name in scalar form
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
}
_LIST_ITEM_TYPES = {                 # bare scalars inside ``<key> = { a b c }``
    "add_ideas": "idea", "remove_ideas": "idea", "show_ideas_tooltip": "idea",
    "prioritize": "state", "states": "state", "seller_tags": "country",
}
# Blocks whose child KEYS may be static modifiers (idea/state modifier blocks; the
# picker *adds* a modifiers section there rather than replacing effects/triggers).
_MODIFIER_PARENTS = ("modifier", "modifiers", "hidden_modifier", "targeted_modifier",
                     "state_modifier")


def value_type_of(parent_key: str, key: str) -> str | None:
    return _KEY_TYPES.get(key) or _VALUE_TYPES.get(key)


def node_from_catalog(item) -> Pair:
    """Build an insertable Pair from a catalog entry — the documented example when it
    parses (instant pre-filled form), otherwise an empty ``name = `` leaf."""
    if item.example:
        try:
            root = pdx_parse(item.example, recover=False)
            for pair in root.pairs():
                if pair.key == item.name:
                    return pair
        except Exception:
            pass
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
        for w in self._body.winfo_children():
            w.destroy()
        self._render_block(self.root_block, self._body, depth=0,
                           parent_key=self.root_key)
        self._body.update_idletasks()
        self._canvas.yview_moveto(y[0])

    def _render_block(self, block: Block, parent: ttk.Frame, depth: int,
                      parent_key: str = "") -> None:
        for item in list(block.items):
            self._render_item(block, item, parent, depth, parent_key)
        self._add_button(block, parent, parent_key)

    def _render_item(self, owner_block: Block, item, parent, depth: int,
                     parent_key: str = "") -> None:
        if isinstance(item, Pair) and isinstance(item.value, Block):
            self._render_container(owner_block, item, parent, depth)
        elif isinstance(item, Pair):
            self._render_leaf(owner_block, item, parent, parent_key)
        elif isinstance(item, Scalar):
            self._render_bare(owner_block, item, parent, parent_key)
        elif isinstance(item, Block):                 # rare: anonymous block
            self._render_container(owner_block, item, parent, depth)

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
                 or pair.key in _CONTAINERS["trigger"] + _CONTAINERS["effect"]
                 or pair.key in _TOOLTIP_KEYS
                 or pair.key in ("limit", "else_if", "tooltip", "factor", "add",
                                 "base", "modifier"))
        if known:
            ttk.Label(row, text=pair.key, style="Card.TLabel").pack(side="left")
        else:
            var = tk.StringVar(value=pair.key)
            entry = ttk.Entry(row, textvariable=var, width=max(8, len(pair.key) + 2))
            entry.pack(side="left")
            var.trace_add("write", lambda *_: (setattr(pair, "key", var.get().strip()),
                                               self._on_change()))

    def _render_leaf(self, owner_block: Block, pair: Pair, parent,
                     parent_key: str = "") -> None:
        row = self._row(parent)
        self._key_widget(row, pair)

        op_var = tk.StringVar(value=pair.op)
        op = ttk.Combobox(row, textvariable=op_var, values=_OPS, width=3, state="readonly")
        op.pack(side="left", padx=3)
        op_var.trace_add("write", lambda *_: (setattr(pair, "op", op_var.get()),
                                              self._on_change()))

        scalar = pair.value
        val_var = tk.StringVar(value=scalar.raw)
        entry = ttk.Entry(row, textvariable=val_var, width=24)
        entry.pack(side="left")

        def value_changed(*_a) -> None:
            text = val_var.get()
            scalar.raw = text
            scalar.quoted = scalar.quoted or (" " in text)
            self._on_change()
        val_var.trace_add("write", value_changed)

        vtype = value_type_of(parent_key, pair.key)
        if vtype is not None:
            self._picker_button(row, vtype, val_var)
        if pair.key in _TOOLTIP_KEYS:
            self._tooltip_loc_field(row, val_var)
        self._del_btn(row, owner_block, pair)

    def _picker_button(self, row, vtype: str, val_var: tk.StringVar) -> None:
        """The '…' button next to typed values: searchable pick list instead of
        hand-typing tags / state ids / idea ids / modifier names."""
        def open_picker() -> None:
            SinglePickDialog(self, self.owner, self.t(f"focuses.pick.{vtype}"),
                             self.owner.value_options(vtype),
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

    def _render_container(self, owner_block: Block, item, parent, depth: int) -> None:
        pair = item if isinstance(item, Pair) else None
        block = item.value if pair is not None else item
        box = tk.Frame(parent, bg=self.palette.surface,
                       highlightbackground=self.palette.border, highlightthickness=1)
        box.pack(fill="x", pady=2, padx=(depth and 0, 0), anchor="w")
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
                           parent_key=pair.key if pair is not None else "")

    def _add_button(self, block: Block, parent, parent_key: str = "") -> None:
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=1, anchor="w")
        ttk.Button(row, text="➕", width=3,
                   command=lambda: self._pick_new(block, parent_key)).pack(side="left")

    # ------------------------------------------------------------------ picker
    def _pick_new(self, block: Block, parent_key: str = "") -> None:
        BlockPickerDialog(self, self.owner, self.kinds,
                          lambda node: self._insert(block, node),
                          with_modifiers=parent_key in _MODIFIER_PARENTS)

    def _insert(self, block: Block, node) -> None:
        block.items.append(node)
        self.render()
        self._on_change()


class BlockPickerDialog(BaseDialog):
    """Searchable picker: logic containers, special blocks, the effect/trigger
    catalog and free-form entries. Returns a ready PDX node via `on_pick`."""

    def __init__(self, master, editor, kinds: tuple[str, ...],
                 on_pick: Callable[[object], None], with_modifiers: bool = False):
        super().__init__(master, editor, editor.t("focuses.blocks.add"), (520, 540))
        self._kinds = kinds
        self._on_pick = on_pick
        self._with_modifiers = with_modifiers

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
        entries.append(("✏  " + t("focuses.blocks.custom_leaf"), ("custom_leaf", None)))
        entries.append(("✏  " + t("focuses.blocks.custom_block"), ("custom_block", None)))
        badge = {"effect": "⚡", "trigger": "❔", "modifier": "📊"}
        kinds = (("modifier",) if self._with_modifiers else ()) + tuple(self._kinds)
        for kind in kinds:
            for item in ScriptCatalog.search(q, kind)[:250]:
                entries.append((f"{badge[kind]} {item.name}", ("catalog", item)))
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
            parts = [item.name]
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
            node = Pair(data, Block())
        elif kind == "tooltip":
            master = self.master
            fid = getattr(master, "focus_id", "") or "my_focus"
            node = Pair(data, Scalar(f"{fid}_tt"))
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
