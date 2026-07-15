"""Inspector of one ideology (``common/ideologies``).

A dumb form over the `IdeologyDef` block-view, rebuilt on every ``show()`` so
the dynamic lists (faction names, leader types, modifiers) stay simple. Fields:
localized name · colour (RGB + swatch) · AI behaviour · boolean flags & numeric
values · the ``rules`` capability flags (tri-state) · ``dynamic_faction_names``
· ``types`` (leader sub-ideologies) · ``modifiers`` and ``faction_modifiers``
(catalog-picked). Edits commit straight into the parsed block through the owning
editor, which tracks dirty docs and saves on demand / on_leave.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.pdx import Block, Pair
from ...services.ideology_def_service import (
    AI_BEHAVIOURS,
    BOOL_KEYS,
    NUM_KEYS,
    RULE_KEYS,
)
from ..common import (
    InspectorBase,
    PdxPreviewDialog,
    SinglePickDialog,
    TextPromptDialog,
)

_RULE_STATES = ("—", "yes", "no")


def _hex_of(components) -> str:
    """Clamped ``#rrggbb`` for three RGB components — each either a `StringVar`
    or a plain string (0 on error)."""
    out = []
    for c in components:
        raw = c.get() if hasattr(c, "get") else c
        try:
            out.append(max(0, min(255, int(float(raw or 0)))))
        except (ValueError, TypeError):
            out.append(0)
    return "#%02x%02x%02x" % tuple(out)


class IdeologyInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc = None
        self.entry = None
        self._swatch = None

    # -------------------------------------------------------------------- show
    def show(self, doc, entry, editable: bool) -> None:
        self.flush_pending()
        self._loading = True
        self.doc, self.entry, self._editable = doc, entry, editable
        for child in self.body.winfo_children():
            child.destroy()
        if entry is not None:
            self._build_form()
            self._set_state_all(editable)
        self._loading = False

    def _rebuild(self) -> None:
        self.show(self.doc, self.entry, self._editable)

    # ------------------------------------------------------------------- build
    def _build_form(self) -> None:
        entry, b = self.entry, self.body
        r = 0
        ttk.Label(b, text=entry.name + ("  🔒" if not self._editable else ""),
                  style="Heading.TLabel").grid(row=r, column=0, columnspan=2,
                                               sticky="w")
        r += 1
        ttk.Label(b, text=self.doc.ref.rel_file, style="CardMuted.TLabel",
                  wraplength=430).grid(row=r, column=0, columnspan=2, sticky="w",
                                       pady=(0, 6))
        r += 1

        r = self._loc_section(r)
        r = self._color_section(r)
        r = self._core_section(r)
        r = self._rules_section(r)
        r = self._faction_names_section(r)
        r = self._types_section(r)
        r = self._modifier_section(r, "modifiers", "ideology.modifiers")
        r = self._modifier_section(r, "faction_modifiers",
                                   "ideology.faction_modifiers")
        r = self._other_section(r)
        self._actions_section(r)

    # ------------------------------------------------------------ localisation
    def _loc_section(self, r: int) -> int:
        b = self.body
        ttk.Label(b, text=self.t("focuses.inspector.language"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w",
                                                 pady=3)
        self._lang = ttk.Combobox(b, state="readonly",
                                  values=list(HOI4_LANGUAGES), width=14)
        self._lang.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
        self._lang.set(self.owner.loc_language)
        self._lang.bind("<<ComboboxSelected>>", self._on_language)
        r += 1

        ttk.Label(b, text=self.t("focuses.inspector.name"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w",
                                                 pady=3)
        self._name_var = tk.StringVar()
        name_entry = ttk.Entry(b, textvariable=self._name_var)
        name_entry.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3)
        self._name_var.trace_add(
            "write", lambda *_: self._debounce("loc", self._commit_loc, 1200))
        name_entry.bind("<FocusOut>", lambda e: self._commit_loc())
        r += 1

        ttk.Label(b, text=self.t("ideology.desc"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="nw",
                                                 pady=3)
        self._desc_text = tk.Text(b, height=3, wrap="word", relief="flat",
                                  bg=self.palette.surface_alt,
                                  fg=self.palette.text,
                                  insertbackground=self.palette.text,
                                  font=("Segoe UI", 9))
        self._desc_text.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3)
        self._desc_text.bind(
            "<KeyRelease>",
            lambda e: self._debounce("loc_desc", self._commit_desc, 1200))
        self._desc_text.bind("<FocusOut>", lambda e: self._commit_desc())
        r += 1

        # ideology (group) icon — GFX_ideology_<name>_group
        self._group_photo = self._icon_preview(
            self.owner.ideology_sprite(self.entry.name, group=True), (72, 72))
        ttk.Label(b, image=self._group_photo).grid(row=r, column=0, pady=(4, 0))
        if self._editable:
            ttk.Button(b, text="🖼 " + self.t("ideology.pick_icon"),
                       command=lambda: self._pick_icon(self.entry.name,
                                                       group=True)).grid(
                row=r, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        r += 1
        self._reload_loc()
        return r

    def _reload_loc(self) -> None:
        lang = self._lang.get()
        self._name_var.set(self.owner.loc_get(self.entry.name, lang))
        self._desc_text.delete("1.0", "end")
        self._desc_text.insert(
            "1.0", self.owner.loc_get(f"{self.entry.name}_desc", lang))

    def _commit_loc(self) -> None:
        if not self._guard() or self.entry is None:
            return
        key, lang = self.entry.name, self._lang.get()
        name = self._name_var.get().strip()
        if name and name != self.owner.loc_get(key, lang):
            self.owner.loc_set(key, lang, name)
            self.owner.refresh_tree_labels()

    def _commit_desc(self) -> None:
        if not self._guard() or self.entry is None:
            return
        key, lang = f"{self.entry.name}_desc", self._lang.get()
        text = self._desc_text.get("1.0", "end").strip()
        if text and text != self.owner.loc_get(key, lang):
            self.owner.loc_set(key, lang, text)

    def _pick_icon(self, name: str, *, group: bool) -> None:
        """Gallery picker + custom import for ideology / sub-ideology icons."""
        if not self._editable or not self.owner.resolver_ready():
            return
        from ..common.dialogs import IconPickerDialog
        target = self.owner.ideology_sprite(name, group=group)

        def picked(sprite: str) -> None:
            if self.owner.reuse_ideology_icon(sprite, name, group=group):
                self._rebuild()

        def imported(path, _keep_size: bool = False) -> None:
            if self.owner.import_ideology_icon(path, name, group=group):
                dialog.destroy()
                self._rebuild()

        dialog = IconPickerDialog(self, self.owner, self.owner.resolver,
                                  target, picked, imported,
                                  prefixes=("GFX_ideology_",))

    def _on_language(self, _event=None) -> None:
        self.flush_pending()
        self.owner.loc_language = self._lang.get()
        # rebuild so per-faction-name translations reload in the new language
        self._rebuild()

    # -------------------------------------------------------------------- color
    def _color_section(self, r: int) -> int:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1
        ttk.Label(b, text=self.t("ideology.color"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w",
                                                 pady=3)
        cell = ttk.Frame(b, style="Card.TFrame")
        cell.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
        self._rgb_vars: list[tk.StringVar] = []
        r0, g0, b0 = self.entry.color
        for init, tag in ((r0, "R"), (g0, "G"), (b0, "B")):
            ttk.Label(cell, text=tag, style="CardMuted.TLabel").pack(
                side="left", padx=(0 if tag == "R" else 6, 2))
            var = tk.StringVar(value=init)
            e = ttk.Entry(cell, textvariable=var, width=5)
            e.pack(side="left")
            var.trace_add("write",
                          lambda *_a: self._debounce("color", self._commit_color))
            e.bind("<FocusOut>", lambda _e: self._commit_color())
            self._rgb_vars.append(var)
        self._swatch = tk.Button(cell, width=3, relief="solid", borderwidth=1,
                                 cursor="hand2", command=self._pick_color)
        self._swatch.pack(side="left", padx=(8, 4))
        ttk.Button(cell, text="🎨", width=3, command=self._pick_color).pack(side="left")
        self._refresh_swatch()
        return r + 1

    def _refresh_swatch(self) -> None:
        if self._swatch is None:
            return
        self._swatch.configure(bg=_hex_of(self._rgb_vars))

    def _pick_color(self) -> None:
        if not self._guard() or self.entry is None:
            return
        rgb, _hexv = colorchooser.askcolor(color=_hex_of(self._rgb_vars),
                                           title=self.t("ideology.color"),
                                           parent=self)
        if rgb is None:
            return
        for var, comp in zip(self._rgb_vars, rgb):
            var.set(str(int(comp)))
        self._commit_color()

    def _commit_color(self) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_color(*[v.get() for v in self._rgb_vars])
        self._refresh_swatch()
        self.owner.mark_dirty(self.doc)

    # --------------------------------------------------------------- core scalars
    def _core_section(self, r: int) -> int:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1

        # AI behaviour
        ttk.Label(b, text=self.t("ideology.ai_behaviour"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w",
                                                 pady=3)
        self._ai = ttk.Combobox(b, state="readonly", width=14,
                                values=("",) + AI_BEHAVIOURS)
        self._ai.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
        self._ai.set(self.entry.ai_behaviour)
        self._ai.bind("<<ComboboxSelected>>", lambda e: self._commit_ai())
        r += 1

        # numeric fields
        self._num_vars: dict[str, tk.StringVar] = {}
        for key in NUM_KEYS:
            ttk.Label(b, text=key, style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=self.entry.get_scalar(key))
            e = ttk.Entry(b, textvariable=var, width=10)
            e.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
            self._num_vars[key] = var
            var.trace_add("write", lambda *_a, k=key:
                          self._debounce(f"num:{k}", lambda k=k: self._commit_num(k)))
            e.bind("<FocusOut>", lambda _e, k=key: self._commit_num(k))
            r += 1

        # boolean flags
        self._bool_vars: dict[str, tk.BooleanVar] = {}
        for key in BOOL_KEYS:
            var = tk.BooleanVar(value=self.entry.get_bool(key))
            self._bool_vars[key] = var
            ttk.Checkbutton(b, text=key, style="Card.TCheckbutton", variable=var,
                            command=lambda k=key: self._commit_bool(k)).grid(
                row=r, column=0, columnspan=2, sticky="w")
            r += 1
        return r

    def _commit_ai(self) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_ai_behaviour(self._ai.get())
        self.owner.mark_dirty(self.doc)

    def _commit_num(self, key: str) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_scalar(key, self._num_vars[key].get())
        self.owner.mark_dirty(self.doc)

    def _commit_bool(self, key: str) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_bool(key, self._bool_vars[key].get())
        self.owner.mark_dirty(self.doc)

    # -------------------------------------------------------------------- rules
    def _rules_section(self, r: int) -> int:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 3))
        ttk.Label(head, text=self.t("ideology.rules"), style="Card.TLabel",
                  font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Button(head, text="➕", width=3, command=self._add_rule).pack(side="right")
        r += 1
        # the known rules are always listed (tri-state, "—" = not written)
        self._rule_boxes: dict[str, ttk.Combobox] = {}
        for key in RULE_KEYS:
            ttk.Label(b, text=key, style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=2)
            box = ttk.Combobox(b, state="readonly", width=6, values=_RULE_STATES)
            cur = self.entry.get_rule(key)
            box.set(cur if cur in ("yes", "no") else "—")
            box.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=2)
            box.bind("<<ComboboxSelected>>", lambda e, k=key: self._commit_rule(k))
            self._rule_boxes[key] = box
            r += 1
        # custom / undocumented rules: value + remove button
        for key in self.entry.extra_rules():
            line = ttk.Frame(b, style="Card.TFrame")
            line.grid(row=r, column=0, columnspan=2, sticky="ew", pady=2)
            ttk.Label(line, text=key, style="CardMuted.TLabel").pack(
                side="left", padx=(0, 6))
            box = ttk.Combobox(line, state="readonly", width=6, values=("yes", "no"))
            box.set(self.entry.get_rule(key) or "yes")
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>",
                     lambda e, k=key, bx=box: self._commit_extra_rule(k, bx))
            ttk.Button(line, text="✕", width=2,
                       command=lambda k=key: self._remove_rule(k)).pack(
                side="right", padx=2)
            r += 1
        return r

    def _commit_rule(self, key: str) -> None:
        if not self._guard() or self.entry is None:
            return
        value = self._rule_boxes[key].get()
        self.entry.set_rule(key, "" if value == "—" else value)
        self.owner.mark_dirty(self.doc)

    def _commit_extra_rule(self, key: str, box: ttk.Combobox) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_rule(key, box.get())
        self.owner.mark_dirty(self.doc)

    def _add_rule(self) -> None:
        if not self._editable or self.entry is None:
            return
        taken = {k.lower() for k in RULE_KEYS} | {
            k.lower() for k in self.entry.extra_rules()}

        def create(key: str) -> None:
            if key.lower() in taken:
                return
            self.entry.set_rule(key, "yes")
            self.owner.mark_dirty(self.doc)
            self._rebuild()

        TextPromptDialog(self, self.owner, self.t("ideology.add_rule"),
                         self.t("ideology.rule_key"), create, pattern=r"^\w+$")

    def _remove_rule(self, key: str) -> None:
        if not self._editable or self.entry is None:
            return
        self.entry.set_rule(key, "")
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    # ------------------------------------------------------- dynamic faction names
    def _faction_names_section(self, r: int) -> int:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=self.t("ideology.faction_names"), style="Card.TLabel",
                  font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Button(head, text="➕", width=3,
                   command=self._add_faction_name).pack(side="right")
        r += 1
        lang = self._lang.get()
        ttk.Label(b, text=self.t("ideology.faction_names_hint", lang=lang),
                  style="CardMuted.TLabel", wraplength=430, justify="left").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 2))
        r += 1
        self._fname_vars: list[tk.StringVar] = []
        names = self.entry.faction_names()
        for i, name in enumerate(names):
            line = ttk.Frame(b, style="Card.TFrame")
            line.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
            line.columnconfigure(0, weight=3)
            line.columnconfigure(1, weight=4)
            key_var = tk.StringVar(value=name)
            self._fname_vars.append(key_var)
            key_e = ttk.Entry(line, textvariable=key_var)
            key_e.grid(row=0, column=0, sticky="ew", padx=(10, 4))
            key_var.trace_add("write",
                              lambda *_a: self._debounce("fnames",
                                                         self._commit_faction_names))
            key_e.bind("<FocusOut>", lambda _e: self._commit_faction_names())
            # translation into the currently selected language
            loc_var = tk.StringVar(value=self.owner.loc_get(name, lang))
            loc_e = ttk.Entry(line, textvariable=loc_var)
            loc_e.grid(row=0, column=1, sticky="ew", padx=(0, 4))
            loc_var.trace_add(
                "write", lambda *_a, kv=key_var, lv=loc_var:
                self._debounce(f"floc:{id(kv)}",
                               lambda kv=kv, lv=lv: self._commit_faction_loc(kv, lv),
                               1200))
            loc_e.bind("<FocusOut>",
                       lambda _e, kv=key_var, lv=loc_var:
                       self._commit_faction_loc(kv, lv))
            ttk.Button(line, text="✕", width=2,
                       command=lambda idx=i: self._remove_faction_name(idx)).grid(
                row=0, column=2, padx=2)
            r += 1
        return r

    def _commit_faction_names(self) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_faction_names([v.get() for v in self._fname_vars])
        self.owner.mark_dirty(self.doc)

    def _commit_faction_loc(self, key_var: tk.StringVar,
                            loc_var: tk.StringVar) -> None:
        if not self._guard():
            return
        key = key_var.get().strip()
        text = loc_var.get().strip()
        if not key:
            return
        lang = self._lang.get()
        if text and text != self.owner.loc_get(key, lang):
            self.owner.loc_set(key, lang, text)

    def _add_faction_name(self) -> None:
        if not self._editable or self.entry is None:
            return
        self.entry.set_faction_names(self.entry.faction_names() + ["FACTION_NAME"])
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    def _remove_faction_name(self, index: int) -> None:
        if not self._editable or self.entry is None:
            return
        names = [v.get() for v in self._fname_vars]
        if 0 <= index < len(names):
            del names[index]
            self.entry.set_faction_names(names)
            self.owner.mark_dirty(self.doc)
            self._rebuild()

    # -------------------------------------------------------------------- types
    def _types_section(self, r: int) -> int:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=self.t("ideology.types"), style="Card.TLabel",
                  font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Button(head, text="➕", width=3,
                   command=self._add_type).pack(side="right")
        r += 1
        lang = self._lang.get()
        self._type_photos: list = []
        for itype in self.entry.types():
            line = ttk.Frame(b, style="Card.TFrame")
            line.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4, 1))
            ttk.Label(line, text=itype.name, style="Card.TLabel").pack(
                side="left", padx=(10, 6))
            var = tk.BooleanVar(value=itype.can_be_randomly_selected)
            ttk.Checkbutton(line, text=self.t("ideology.randomly_selected"),
                            style="Card.TCheckbutton", variable=var,
                            command=lambda it=itype, v=var:
                            self._commit_type_random(it, v)).pack(side="left")
            ttk.Button(line, text="🗑", width=3,
                       command=lambda it=itype: self._remove_type(it)).pack(
                side="right", padx=2)
            # optional colour override
            if itype.has_color:
                ttk.Button(line, text="✕", width=2,
                           command=lambda it=itype: self._clear_type_color(it)).pack(
                    side="right", padx=(2, 6))
                sw = tk.Button(line, width=2, relief="solid", borderwidth=1,
                               cursor="hand2",
                               command=lambda it=itype: self._pick_type_color(it))
                sw.configure(bg=_hex_of(itype.color))
                sw.pack(side="right")
            else:
                ttk.Button(line, text="🎨 " + self.t("ideology.set_color"),
                           command=lambda it=itype: self._pick_type_color(it)).pack(
                    side="right")
            r += 1
            r = self._type_detail_row(r, itype, lang)
        return r

    def _type_detail_row(self, r: int, itype, lang: str) -> int:
        """Sub-ideology extras: icon (``GFX_ideology_<type>``) + localized
        name/description in the currently selected language."""
        b = self.body
        detail = ttk.Frame(b, style="Card.TFrame")
        detail.grid(row=r, column=0, columnspan=2, sticky="ew",
                    pady=(0, 3), padx=(24, 0))
        detail.columnconfigure(1, weight=1)
        photo = self._icon_preview(
            self.owner.ideology_sprite(itype.name, group=False), (36, 36))
        self._type_photos.append(photo)
        icon_lbl = ttk.Label(detail, image=photo)
        icon_lbl.grid(row=0, column=0, rowspan=2, padx=(0, 8))
        if self._editable:
            icon_lbl.configure(cursor="hand2")
            icon_lbl.bind("<Button-1>",
                          lambda _e, n=itype.name: self._pick_icon(n, group=False))

        def loc_row(row: int, suffix: str, label_key: str) -> None:
            key = itype.name + suffix
            ttk.Label(detail, text=self.t(label_key),
                      style="CardMuted.TLabel").grid(row=row, column=1,
                                                     sticky="w")
            var = tk.StringVar(value=self.owner.loc_get(key, lang))
            entry = ttk.Entry(detail, textvariable=var)
            entry.grid(row=row, column=2, sticky="ew", padx=(6, 0), pady=1)

            def commit(k=key, v=var) -> None:
                if not self._guard():
                    return
                text = v.get().strip()
                if text and text != self.owner.loc_get(k, self._lang.get()):
                    self.owner.loc_set(k, self._lang.get(), text)

            var.trace_add("write", lambda *_a, k=key, c=commit:
                          self._debounce(f"tloc:{k}", c, 1200))
            entry.bind("<FocusOut>", lambda _e, c=commit: c())

        detail.columnconfigure(2, weight=3)
        loc_row(0, "", "focuses.inspector.name")
        loc_row(1, "_desc", "ideology.desc")
        if self._editable:
            ttk.Button(detail, text="🖼", width=3,
                       command=lambda n=itype.name: self._pick_icon(
                           n, group=False)).grid(row=0, column=3, rowspan=2,
                                                 padx=(6, 0))
        return r + 1

    def _commit_type_random(self, itype, var: tk.BooleanVar) -> None:
        if not self._guard():
            return
        itype.set_can_be_randomly_selected(var.get())
        self.owner.mark_dirty(self.doc)

    def _pick_type_color(self, itype) -> None:
        if not self._guard():
            return
        init = _hex_of(itype.color) if itype.has_color else "#808080"
        rgb, _hexv = colorchooser.askcolor(color=init, parent=self,
                                           title=self.t("ideology.color"))
        if rgb is None:
            return
        itype.set_color(*[str(int(c)) for c in rgb])
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    def _clear_type_color(self, itype) -> None:
        if not self._guard():
            return
        itype.clear_color()
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    def _add_type(self) -> None:
        if not self._editable or self.entry is None:
            return
        taken = {t.name for t in self.entry.types()}

        def create(name: str) -> None:
            self.entry.add_type(name)
            self.owner.mark_dirty(self.doc)
            self._rebuild()

        TextPromptDialog(self, self.owner, self.t("ideology.add_type"),
                         self.t("ideology.type_name"), create,
                         taken=taken, pattern=r"^\w+$")

    def _remove_type(self, itype) -> None:
        if not self._editable or self.entry is None:
            return
        self.entry.remove_type(itype)
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    # ---------------------------------------------------------------- modifiers
    def _modifier_section(self, r: int, group: str, title_key: str) -> int:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=self.t(title_key), style="Card.TLabel",
                  font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Button(head, text="➕", width=3,
                   command=lambda g=group: self._add_modifier(g)).pack(side="right")
        r += 1
        for pair in self.entry.modifiers(group):
            r = self._modifier_row(r, pair, group)
        return r

    def _modifier_row(self, r: int, pair: Pair, group: str) -> int:
        b = self.body
        line = ttk.Frame(b, style="Card.TFrame")
        line.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
        ttk.Label(line, text=pair.key, style="Card.TLabel").pack(
            side="left", padx=(10, 4))
        var = tk.StringVar(value=pair.value.raw)

        def commit(p=pair, v=var) -> None:
            if not self._guard():
                return
            p.value.raw = v.get().strip()
            self.owner.mark_dirty(self.doc)

        e = ttk.Entry(line, textvariable=var, width=12)
        e.pack(side="left")
        var.trace_add("write",
                      lambda *_a, k=pair.key: self._debounce(f"{group}:{k}", commit))
        e.bind("<FocusOut>", lambda _e: commit())
        ttk.Button(line, text="✕", width=2,
                   command=lambda p=pair, g=group: self._remove_modifier(p, g)).pack(
            side="right", padx=2)
        return r + 1

    def _add_modifier(self, group: str) -> None:
        if not self._editable or self.entry is None:
            return
        options = list(self.owner.value_options("modifier"))
        options.append((f"✏ {self.t('ideology.custom_key')}", "__custom__"))

        def picked(value: str) -> None:
            if value == "__custom__":
                TextPromptDialog(self, self.owner, self.t("ideology.add_modifier"),
                                 self.t("ideology.key_label"), add, pattern=r"^\w+$")
            else:
                add(value)

        def add(key: str) -> None:
            self.entry.add_modifier(key, "0", key=group)
            self.owner.mark_dirty(self.doc)
            self._rebuild()

        SinglePickDialog(self, self.owner, self.t("ideology.add_modifier"),
                         options, picked)

    def _remove_modifier(self, pair: Pair, group: str) -> None:
        if not self._editable or self.entry is None:
            return
        self.entry.remove_modifier(pair, key=group)
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    # -------------------------------------------------------------------- other
    def _other_section(self, r: int) -> int:
        extras = self.entry.other_keys()
        if not extras:
            return r
        b = self.body
        ttk.Label(b, text=self.t("ideology.other_attrs") + ": " + ", ".join(extras),
                  style="CardMuted.TLabel", wraplength=430, justify="left").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))
        return r + 1

    # ------------------------------------------------------------------ actions
    def _actions_section(self, r: int) -> None:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8)
        r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview_pdx).pack(side="left")
        self._rename_btn = ttk.Button(actions, text="✎ " + self.t("ideology.rename"),
                                      command=self._rename)
        self._rename_btn.pack(side="left", padx=(4, 0))
        self._dup_btn = ttk.Button(actions, text="⧉ " + self.t("focuses.duplicate"),
                                   command=lambda: self.owner.duplicate_entry(
                                       self.doc, self.entry))
        self._dup_btn.pack(side="left", padx=(4, 0))
        self._delete_btn = ttk.Button(actions, text="🗑 " + self.t("ideology.delete"),
                                      command=self._delete)
        self._delete_btn.pack(side="right")

    def _rename(self) -> None:
        if not self._guard() or self.entry is None:
            return
        entry, doc = self.entry, self.doc
        TextPromptDialog(self, self.owner, self.t("ideology.rename"),
                         self.t("focuses.rename_label"),
                         lambda new: self.owner.rename_entry(doc, entry, new),
                         initial=entry.name,
                         taken=set(self.owner.known_names()) - {entry.name},
                         pattern=r"^\w+$")

    def _preview_pdx(self) -> None:
        if self.entry is not None:
            self.flush_pending()
            PdxPreviewDialog(self, self.owner, self.entry.name,
                             Block([Pair("ideologies", Block([self.entry.pair]))]))

    def _delete(self) -> None:
        if self.entry is None or not self._editable:
            return
        if not messagebox.askyesno("ANKA", self.t("ideology.confirm_delete",
                                                  name=self.entry.name)):
            return
        self.owner.delete_entry(self.doc, self.entry)
