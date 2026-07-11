"""Inspectors of the Decisions editor: the decision form and the category form.

Both are dumb forms over block-backed models (`Decision` / `CategoryDef`): values are
loaded on `show()`, edits commit back instantly (debounced for typed fields) through
the owning editor, which tracks dirty documents and saves them on demand/on_leave.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...config.constants import HOI4_LANGUAGES
from ...services.decision_service import (
    CATEGORY_SCRIPT_FIELDS,
    DECISION_EFFECT_FIELDS,
    DECISION_FLAG_DEFAULTS,
    DECISION_SCRIPT_FIELDS,
    DECISION_TRIGGER_FIELDS,
    Decision,
)
from ..common import (
    IconPickerDialog,
    InspectorBase,
    PdxPreviewDialog,
    ScriptEditorDialog,
    SinglePickDialog,
    TextPromptDialog,
)
from ...core.pdx import Block, Pair
from ...ui.widgets.tooltip import attach_help

# Block kinds each script field offers in the visual editor. Condition fields are
# trigger-only; effect fields mix both; modifier fields get the modifiers section
# automatically (their root key is in the editor's _MODIFIER_PARENTS list).
def _script_kinds(name: str) -> tuple[str, ...]:
    if name in DECISION_EFFECT_FIELDS:
        return ("effect", "trigger")
    return ("trigger",)


# Raw-value fields, grouped into inspector sections. `war_with_*` take country tags
# (picker offered), the rest are numbers/variables/loc keys.
_MAIN_FIELDS = ("cost", "custom_cost_text", "days_remove", "days_re_enable", "priority")
_MISSION_FIELDS = ("days_mission_timeout", "ai_hint_pp_cost")
_WAR_FIELDS = ("war_with_on_remove", "war_with_on_complete", "war_with_on_timeout")
_MAIN_FLAGS = ("fire_only_once", "cancel_if_not_visible", "targets_dynamic")
_MISSION_FLAGS = ("selectable_mission", "is_good", "fixed_random_seed")


class DecisionInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.decision: Decision | None = None
        self.doc = None
        self._loaded_name = ""
        self._loaded_desc = ""
        self._build()

    def _build(self) -> None:
        b = self.body
        r = 0
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=420)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        # id + rename
        ttk.Label(b, text="ID", style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        id_row = ttk.Frame(b, style="Card.TFrame")
        id_row.grid(row=r, column=1, sticky="ew", padx=(8, 0)); r += 1
        id_row.columnconfigure(0, weight=1)
        self._id_entry = ttk.Entry(id_row, state="readonly")
        self._id_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(id_row, text="✎", width=3, command=self._rename).grid(row=0, column=1, padx=(4, 0))

        # category
        ttk.Label(b, text=self.t("decisions.category"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        self._category = ttk.Combobox(b, state="readonly")
        self._category.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._category.bind("<<ComboboxSelected>>", lambda e: self._commit_category())

        # localisation
        ttk.Label(b, text=self.t("focuses.inspector.language"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        self._lang = ttk.Combobox(b, state="readonly", values=list(HOI4_LANGUAGES), width=14)
        self._lang.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
        self._lang.set(self.owner.loc_language)
        self._lang.bind("<<ComboboxSelected>>", self._on_language)

        ttk.Label(b, text=self.t("focuses.inspector.name"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        self._name_var = tk.StringVar()
        name_entry = ttk.Entry(b, textvariable=self._name_var)
        name_entry.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._name_var.trace_add("write", lambda *_: self._debounce("loc", self._commit_loc, 1200))
        name_entry.bind("<FocusOut>", lambda e: self._commit_loc())

        ttk.Label(b, text=self.t("focuses.inspector.desc"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="nw", pady=3)
        self._desc = tk.Text(b, height=3, wrap="word", bg=self.palette.surface_alt,
                             fg=self.palette.text, insertbackground=self.palette.text,
                             relief="flat", font=("Segoe UI", 10))
        self._desc.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._desc.bind("<KeyRelease>", lambda e: self._debounce("loc", self._commit_loc, 1200))
        self._desc.bind("<FocusOut>", lambda e: self._commit_loc())

        # icon
        ttk.Label(b, text=self.t("focuses.inspector.icon"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        icon_row = ttk.Frame(b, style="Card.TFrame")
        icon_row.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._icon_btn = tk.Button(icon_row, bd=0, bg=self.palette.surface_alt,
                                   activebackground=self.palette.surface,
                                   cursor="hand2", command=self._pick_icon)
        self._icon_btn.pack(side="left")
        self._icon_name = ttk.Label(icon_row, text="", style="CardMuted.TLabel", wraplength=250)
        self._icon_name.pack(side="left", padx=(8, 0))

        # sections: main / mission / war (see module constants)
        self._num_vars: dict[str, tk.StringVar] = {}
        self._flags: dict[str, tk.BooleanVar] = {}

        def fields_section(header_key: str, raw_fields, flags, war=False):
            nonlocal r
            ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6); r += 1
            head = ttk.Label(b, text=self.t(header_key), style="Card.TLabel")
            head.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 3)); r += 1
            attach_help(head, self.t, header_key.replace("decisions.section.",
                                                         "decisions."), self.palette)
            for field_name in raw_fields:
                label = ttk.Label(b, text=field_name, style="CardMuted.TLabel")
                label.grid(row=r, column=0, sticky="w", pady=3)
                attach_help(label, self.t, f"script.{field_name}", self.palette)
                row_frame = ttk.Frame(b, style="Card.TFrame")
                row_frame.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
                var = tk.StringVar()
                entry = ttk.Entry(row_frame, textvariable=var, width=18)
                entry.pack(side="left")
                self._num_vars[field_name] = var
                var.trace_add("write", lambda *_, n=field_name: self._debounce(
                    f"num:{n}", lambda n=n: self._commit_number(n)))
                entry.bind("<FocusOut>", lambda e, n=field_name: self._commit_number(n))
                if war:
                    ttk.Button(row_frame, text="…", width=2,
                               command=lambda v=var: self._pick_country(v)).pack(
                        side="left", padx=2)
            for name in flags:
                var = tk.BooleanVar()
                self._flags[name] = var
                cb = ttk.Checkbutton(b, text=name, style="Card.TCheckbutton",
                                     variable=var,
                                     command=lambda n=name: self._commit_flag(n))
                cb.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
                attach_help(cb, self.t, f"script.{name}", self.palette)

        fields_section("decisions.section.main", _MAIN_FIELDS, _MAIN_FLAGS)
        fields_section("decisions.section.mission", _MISSION_FIELDS, _MISSION_FLAGS)
        fields_section("decisions.section.war", _WAR_FIELDS, (), war=True)

        # scripts
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 4)); r += 1
        ttk.Label(head, text=self.t("focuses.inspector.scripts"),
                  style="Card.TLabel").pack(side="left")
        ttk.Button(head, text="👁 " + self.t("focuses.preview"),
                   command=self._preview).pack(side="right")
        self._script_status: dict[str, ttk.Label] = {}
        for field_name in DECISION_SCRIPT_FIELDS:
            row = ttk.Frame(b, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1); r += 1
            status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
            status.pack(side="left")
            label = ttk.Label(row, text=field_name, style="CardMuted.TLabel")
            label.pack(side="left")
            attach_help(label, self.t, f"script.{field_name}", self.palette)
            self._script_status[field_name] = status
            # the edit button hugs the label instead of the far-right edge
            ttk.Button(row, text="✎", width=3,
                       command=lambda n=field_name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="⧉ " + self.t("focuses.duplicate"),
                   command=lambda: self.owner.duplicate_decision()).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(actions, text="🗑 " + self.t("decisions.delete"),
                   command=lambda: self.owner.delete_decision()).pack(
            side="left", fill="x", expand=True)

    def _pick_country(self, var: tk.StringVar) -> None:
        if not self._guard():
            return
        SinglePickDialog(self, self.owner, self.t("focuses.pick.country"),
                         self.owner.value_options("country"),
                         lambda value: var.set(value), current=var.get().strip())

    # --------------------------------------------------------------------- show
    def show(self, doc, decision: Decision | None, editable: bool = True) -> None:
        self.flush_pending()
        self.doc = doc
        self.decision = decision
        self._editable = editable
        self._loading = True
        try:
            if decision is None:
                return
            self._title.configure(text=decision.id)
            origin = doc.ref.rel_file if doc is not None else ""
            self._subtitle.configure(
                text=origin + ("" if editable else "  ·  🔒 " + self.t("focuses.readonly")))
            self._id_entry.configure(state="normal")
            self._id_entry.delete(0, "end")
            self._id_entry.insert(0, decision.id)
            self._id_entry.configure(state="readonly")
            self._category.configure(values=self.owner.category_options())
            self._category.set(decision.category)
            lang = self.owner.loc_language
            self._lang.set(lang)
            key = decision.name_key
            self._loaded_name = self.owner.service.name_of(key, lang)
            self._loaded_desc = self.owner.service.desc_of(key, lang)
            self._name_var.set("" if self._loaded_name == key else self._loaded_name)
            self._desc.configure(state="normal")   # a disabled Text ignores edits
            self._desc.delete("1.0", "end")
            self._desc.insert("1.0", self._loaded_desc.replace("\\n", "\n"))
            for name, var in self._num_vars.items():
                var.set(decision.get_raw(name))
            for name, var in self._flags.items():
                var.set(decision.get_flag(name))
            self._refresh_icon()
            self._refresh_scripts()
            self._set_state_all(editable)
            self._lang.configure(state="readonly")
            self._id_entry.configure(state="readonly")
        finally:
            self._loading = False

    def _refresh_icon(self) -> None:
        d = self.decision
        sprite = d.sprite_name() if d else ""
        self._icon_photo = self._icon_preview(sprite)
        self._icon_btn.configure(image=self._icon_photo)
        self._icon_name.configure(text=(d.icon if d else "") or "—")

    def _refresh_scripts(self) -> None:
        d = self.decision
        for name, label in self._script_status.items():
            filled = bool(d and d.get_script(name).strip())
            label.configure(text="●" if filled else "○",
                            foreground=self.palette.accent if filled
                            else self.palette.text_muted)

    # ------------------------------------------------------------------ commits
    def _commit_number(self, name: str) -> None:
        if not self._guard() or self.decision is None:
            return
        value = self._num_vars[name].get().strip()
        if value != self.decision.get_raw(name):
            self.decision.set_raw(name, value)
            self.owner.mark_dirty(self.doc)

    def _commit_flag(self, name: str) -> None:
        if not self._guard() or self.decision is None:
            return
        self.decision.set_flag(name, self._flags[name].get())
        self.owner.mark_dirty(self.doc)

    def _commit_loc(self) -> None:
        if not self._guard() or self.decision is None:
            return
        key = self.decision.name_key
        lang = self._lang.get()
        name = self._name_var.get().strip()
        desc = self._desc.get("1.0", "end").strip()
        cur_name = self.owner.service.name_of(key, lang)
        # loc stores line breaks as literal \n; the Text widget holds real ones
        cur_desc = self.owner.service.desc_of(key, lang).replace("\\n", "\n")
        write_name = name if name and name != cur_name else None
        write_desc = desc if desc != cur_desc and (desc or cur_desc) else None
        if write_name is not None or write_desc is not None:
            self.owner.service.set_loc(key, lang, write_name, write_desc)
            self.owner.refresh_tree_labels()

    def _commit_category(self) -> None:
        if not self._guard() or self.decision is None:
            return
        new_cat = self._category.get()
        if new_cat and new_cat != self.decision.category:
            self.owner.service.move_decision(self.doc, self.decision, new_cat)
            self.owner.mark_dirty(self.doc)
            self.owner.reload_tree(keep_selection=True)

    def _on_language(self, _event=None) -> None:
        self.owner.loc_language = self._lang.get()
        if self.decision is not None:
            self.show(self.doc, self.decision, self._editable)

    # ------------------------------------------------------------------ actions
    def _rename(self) -> None:
        if not self._guard() or self.decision is None:
            return
        decision = self.decision
        TextPromptDialog(self, self.owner, self.t("decisions.rename"),
                         self.t("focuses.rename_label"),
                         lambda new_id: self.owner.rename_decision(decision, new_id),
                         initial=decision.id,
                         taken=set(self.owner.known_ids()) - {decision.id})

    def _pick_icon(self) -> None:
        if not self._guard() or self.decision is None:
            return
        decision = self.decision

        def picked(sprite: str) -> None:
            # store short names the vanilla way (strip the decision prefix)
            icon = sprite
            if icon.startswith("GFX_decision_"):
                icon = icon[len("GFX_decision_"):]
            decision.icon = icon
            self.owner.mark_dirty(self.doc)
            self._refresh_icon()

        def imported(path, keep_size: bool = False) -> None:
            sprite = self.owner.import_icon(path, decision, keep_size=keep_size)
            if sprite:
                picked(sprite)

        IconPickerDialog(self, self.owner, self.owner.resolver, decision.icon,
                         picked, imported, prefixes=("GFX_decision_",))

    def _edit_script(self, name: str) -> None:
        decision = self.decision
        if decision is None:
            return
        kinds = _script_kinds(name)
        if not self._editable:
            ScriptEditorDialog(self, self.owner, name, decision.get_script(name),
                               lambda text: None, kinds, decision.id)
            return

        def submitted(text: str) -> None:
            try:
                decision.set_script(name, text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self._refresh_scripts()

        ScriptEditorDialog(self, self.owner, name, decision.get_script(name),
                           submitted, kinds, decision.id)

    def _preview(self) -> None:
        if self.decision is not None:
            PdxPreviewDialog(self, self.owner,
                             self.t("focuses.preview_title", id=self.decision.id),
                             Block([Pair(self.decision.category,
                                         Block([self.decision.pair]))]))


class CategoryInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.category = None          # CategoryDef | None
        self.doc = None
        self._build()

    def _build(self) -> None:
        b = self.body
        r = 0
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=420)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1
        self._no_def = ttk.Label(b, text=self.t("decisions.no_def"),
                                 style="CardMuted.TLabel", wraplength=420)

        # localisation
        ttk.Label(b, text=self.t("focuses.inspector.language"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        self._lang = ttk.Combobox(b, state="readonly", values=list(HOI4_LANGUAGES), width=14)
        self._lang.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
        self._lang.bind("<<ComboboxSelected>>", lambda e: self._reload_loc())

        ttk.Label(b, text=self.t("focuses.inspector.name"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        self._name_var = tk.StringVar()
        entry = ttk.Entry(b, textvariable=self._name_var)
        entry.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._name_var.trace_add("write", lambda *_: self._debounce("loc", self._commit_loc, 1200))
        entry.bind("<FocusOut>", lambda e: self._commit_loc())

        ttk.Label(b, text=self.t("focuses.inspector.desc"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="nw", pady=3)
        self._desc = tk.Text(b, height=3, wrap="word", bg=self.palette.surface_alt,
                             fg=self.palette.text, insertbackground=self.palette.text,
                             relief="flat", font=("Segoe UI", 10))
        self._desc.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._desc.bind("<KeyRelease>", lambda e: self._debounce("loc", self._commit_loc, 1200))
        self._desc.bind("<FocusOut>", lambda e: self._commit_loc())

        # icon / picture
        ttk.Label(b, text=self.t("focuses.inspector.icon"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        icon_row = ttk.Frame(b, style="Card.TFrame")
        icon_row.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._icon_btn = tk.Button(icon_row, bd=0, bg=self.palette.surface_alt,
                                   activebackground=self.palette.surface,
                                   cursor="hand2", command=self._pick_icon)
        self._icon_btn.pack(side="left")
        self._icon_name = ttk.Label(icon_row, text="", style="CardMuted.TLabel", wraplength=250)
        self._icon_name.pack(side="left", padx=(8, 0))

        # picture: a selectable sprite (vanilla stores the full GFX name)
        pic_label = ttk.Label(b, text="picture", style="CardMuted.TLabel")
        pic_label.grid(row=r, column=0, sticky="w", pady=3)
        attach_help(pic_label, self.t, "decisions.picture", self.palette)
        pic_row = ttk.Frame(b, style="Card.TFrame")
        pic_row.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._picture_btn = tk.Button(pic_row, bd=0, bg=self.palette.surface_alt,
                                      activebackground=self.palette.surface,
                                      cursor="hand2", command=self._pick_picture)
        self._picture_btn.pack(side="left")
        self._picture_name = ttk.Label(pic_row, text="", style="CardMuted.TLabel",
                                       wraplength=220)
        self._picture_name.pack(side="left", padx=(8, 4))
        ttk.Button(pic_row, text="✕", width=2,
                   command=self._clear_picture).pack(side="left")

        self._priority_var = self._entry_row(r, "priority", "priority", self._commit_priority); r += 1

        # scripts
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        self._script_status: dict[str, ttk.Label] = {}
        for field_name in CATEGORY_SCRIPT_FIELDS:
            row = ttk.Frame(b, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1); r += 1
            status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
            status.pack(side="left")
            label = ttk.Label(row, text=field_name, style="CardMuted.TLabel")
            label.pack(side="left")
            attach_help(label, self.t, f"script.{field_name}", self.palette)
            self._script_status[field_name] = status
            ttk.Button(row, text="✎", width=3,
                       command=lambda n=field_name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        ttk.Button(b, text="🗑 " + self.t("decisions.delete_category"),
                   command=lambda: self.owner.delete_category()).grid(
            row=r, column=0, columnspan=2, sticky="ew")

    def show(self, doc, category, editable: bool) -> None:
        self.flush_pending()
        self.doc = doc
        self.category = category
        self._editable = editable and category is not None
        self._loading = True
        try:
            name = category.name if category is not None else self.owner.selected_category_name
            self._title.configure(text=name or "—")
            if category is None:
                self._subtitle.configure(text=self.t("decisions.no_def"))
            else:
                origin = doc.ref.rel_file if doc is not None else ""
                self._subtitle.configure(
                    text=origin + ("" if editable else "  ·  🔒 " + self.t("focuses.readonly")))
            self._lang.set(self.owner.loc_language)
            self._reload_loc()
            self._icon_photo = self._icon_preview(
                category.sprite_name() if category else "")
            self._icon_btn.configure(image=self._icon_photo)
            self._icon_name.configure(text=(category.icon if category else "") or "—")
            self._refresh_picture()
            self._priority_var.set(category.get_raw("priority") if category else "")
            for fname, label in self._script_status.items():
                filled = bool(category and category.get_script(fname).strip())
                label.configure(text="●" if filled else "○",
                                foreground=self.palette.accent if filled
                                else self.palette.text_muted)
            self._set_state_all(self._editable)
            self._lang.configure(state="readonly")
        finally:
            self._loading = False

    def _reload_loc(self) -> None:
        name = self.category.name if self.category is not None \
            else self.owner.selected_category_name
        if not name:
            return
        was = self._loading
        self._loading = True
        value = self.owner.service.name_of(name, self._lang.get())
        self._name_var.set("" if value == name else value)
        desc = self.owner.service.desc_of(name, self._lang.get())
        self._desc.configure(state="normal")   # a disabled Text ignores edits
        self._desc.delete("1.0", "end")
        self._desc.insert("1.0", desc.replace("\\n", "\n"))
        self._loading = was

    def _commit_loc(self) -> None:
        if self._loading:
            return
        name = self.category.name if self.category is not None \
            else self.owner.selected_category_name
        if not name:
            return
        lang = self._lang.get()
        text = self._name_var.get().strip()
        desc = self._desc.get("1.0", "end").strip()
        cur_name = self.owner.service.name_of(name, lang)
        # loc stores line breaks as literal \n; the Text widget holds real ones
        cur_desc = self.owner.service.desc_of(name, lang).replace("\\n", "\n")
        write_name = text if text and text != cur_name else None
        write_desc = desc if desc != cur_desc and (desc or cur_desc) else None
        if write_name is not None or write_desc is not None:
            self.owner.service.set_loc(name, lang, write_name, write_desc)
            self.owner.refresh_tree_labels()

    def _refresh_picture(self) -> None:
        sprite = self.category.get_raw("picture") if self.category else ""
        self._picture_photo = self._icon_preview(sprite, size=(140, 60))
        self._picture_btn.configure(image=self._picture_photo)
        self._picture_name.configure(text=sprite or "—")

    def _pick_picture(self) -> None:
        if not self._guard() or self.category is None:
            return
        category = self.category

        def picked(sprite: str) -> None:
            category.set_raw("picture", sprite)   # pictures keep the full GFX name
            self.owner.mark_dirty(self.doc)
            self._refresh_picture()

        def imported(path, _keep_size: bool = False) -> None:
            sprite = self.owner.import_category_picture(path, category.name)
            if sprite:
                picked(sprite)

        IconPickerDialog(self, self.owner, self.owner.resolver,
                         category.get_raw("picture"), picked, imported,
                         prefixes=("GFX_decision_cat_",))

    def _clear_picture(self) -> None:
        if not self._guard() or self.category is None:
            return
        self.category.set_raw("picture", "")
        self.owner.mark_dirty(self.doc)
        self._refresh_picture()

    def _commit_priority(self) -> None:
        if not self._guard() or self.category is None:
            return
        self.category.set_raw("priority", self._priority_var.get())
        self.owner.mark_dirty(self.doc)

    def _pick_icon(self) -> None:
        if not self._guard() or self.category is None:
            return
        category = self.category

        def picked(sprite: str) -> None:
            icon = sprite
            if icon.startswith("GFX_decision_category_"):
                icon = icon[len("GFX_decision_category_"):]
            category.icon = icon
            self.owner.mark_dirty(self.doc)
            self._icon_btn.configure(image=self._icon_preview(category.sprite_name()))
            self._icon_name.configure(text=category.icon or "—")

        def imported(path, keep_size: bool = False) -> None:
            sprite = self.owner.import_category_icon(path, category.name,
                                                     keep_size=keep_size)
            if sprite:
                picked(sprite)

        IconPickerDialog(self, self.owner, self.owner.resolver, category.icon,
                         picked, imported, prefixes=("GFX_decision_category_",))

    def _edit_script(self, name: str) -> None:
        category = self.category
        if category is None:
            return

        def submitted(text: str) -> None:
            if not self._editable:
                return
            try:
                category.set_script(name, text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self.show(self.doc, category, self._editable)

        ScriptEditorDialog(self, self.owner, name, category.get_script(name),
                           submitted, ("trigger",), category.name)
