"""Inspectors of the Ideas editor: the idea form and the category form.

Both are dumb forms over block-backed models (`IdeaDef` / idea_tags
`CategoryDef` + file flags): values load on `show()`, edits commit back instantly
(debounced for typed fields) through the owning editor, which tracks dirty
documents and saves them on demand / on_leave.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.pdx import Block, Pair
from ...services.idea_service import (
    CATEGORY_FILE_FLAGS,
    IDEA_EFFECT_FIELDS,
    IDEA_FLAG_DEFAULTS,
    IDEA_SCRIPT_FIELDS,
    LEDGER_VALUES,
    IdeaDef,
)
from ...ui.widgets.tooltip import attach_help
from ..common import (
    IconPickerDialog,
    InspectorBase,
    MultiPickDialog,
    PdxPreviewDialog,
    ScriptEditorDialog,
    TextPromptDialog,
)

_ICON_PREFIX = "GFX_idea_"
# Raw numeric/enum idea fields shown as entries.
_NUM_FIELDS = ("cost", "removal_cost", "level")


def _script_kinds(name: str) -> tuple[str, ...]:
    """Block kinds each idea script field offers. Effect fields mix effects and
    triggers; modifier fields (modifier/targeted_modifier) get the modifiers
    section automatically via the editor's _MODIFIER_PARENTS root-key list."""
    if name in IDEA_EFFECT_FIELDS:
        return ("effect", "trigger")
    return ("trigger",)


class IdeaInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.idea: IdeaDef | None = None
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
        self._id_entry = ttk.Entry(id_row, state="readonly", font=("Consolas", 10))
        self._id_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(id_row, text="✎", width=3, command=self._rename).grid(row=0, column=1, padx=(4, 0))

        # category (move)
        ttk.Label(b, text=self.t("ideas.category"), style="CardMuted.TLabel").grid(
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

        # name override key (loc key when set)
        name_label = ttk.Label(b, text="name", style="CardMuted.TLabel")
        name_label.grid(row=r, column=0, sticky="w", pady=3)
        attach_help(name_label, self.t, "idea.name", self.palette)
        self._namekey_var = tk.StringVar()
        namekey_entry = ttk.Entry(b, textvariable=self._namekey_var)
        namekey_entry.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._namekey_var.trace_add("write", lambda *_: self._debounce("namekey", self._commit_namekey))
        namekey_entry.bind("<FocusOut>", lambda e: self._commit_namekey())

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

        # numeric fields + ledger + flags
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6); r += 1
        self._num_vars: dict[str, tk.StringVar] = {}
        for field_name in _NUM_FIELDS:
            label = ttk.Label(b, text=field_name, style="CardMuted.TLabel")
            label.grid(row=r, column=0, sticky="w", pady=3)
            attach_help(label, self.t, f"idea.{field_name}", self.palette)
            var = tk.StringVar()
            entry = ttk.Entry(b, textvariable=var, width=18)
            entry.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
            self._num_vars[field_name] = var
            var.trace_add("write", lambda *_, n=field_name: self._debounce(
                f"num:{n}", lambda n=n: self._commit_number(n)))
            entry.bind("<FocusOut>", lambda e, n=field_name: self._commit_number(n))

        led_label = ttk.Label(b, text="ledger", style="CardMuted.TLabel")
        led_label.grid(row=r, column=0, sticky="w", pady=3)
        attach_help(led_label, self.t, "idea.ledger", self.palette)
        self._ledger = ttk.Combobox(b, state="readonly", values=list(LEDGER_VALUES), width=14)
        self._ledger.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
        self._ledger.bind("<<ComboboxSelected>>", lambda e: self._commit_ledger())

        self._flags: dict[str, tk.BooleanVar] = {}
        for name in IDEA_FLAG_DEFAULTS:
            var = tk.BooleanVar()
            self._flags[name] = var
            cb = ttk.Checkbutton(b, text=name, style="Card.TCheckbutton", variable=var,
                                 command=lambda n=name: self._commit_flag(n))
            cb.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
            attach_help(cb, self.t, f"idea.{name}", self.palette)

        # traits
        tr_label = ttk.Label(b, text="traits", style="CardMuted.TLabel")
        tr_label.grid(row=r, column=0, sticky="nw", pady=3)
        attach_help(tr_label, self.t, "idea.traits", self.palette)
        tr_row = ttk.Frame(b, style="Card.TFrame")
        tr_row.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        tr_row.columnconfigure(0, weight=1)
        self._traits_label = ttk.Label(tr_row, text="—", style="CardMuted.TLabel", wraplength=240)
        self._traits_label.grid(row=0, column=0, sticky="w")
        ttk.Button(tr_row, text="…", width=3, command=self._pick_traits).grid(row=0, column=1)

        # scripts
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 4)); r += 1
        ttk.Label(head, text=self.t("focuses.inspector.scripts"),
                  style="Card.TLabel").pack(side="left")
        ttk.Button(head, text="👁 " + self.t("focuses.preview"),
                   command=self._preview).pack(side="right")
        self._script_status: dict[str, ttk.Label] = {}
        for field_name in IDEA_SCRIPT_FIELDS:
            row = ttk.Frame(b, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1); r += 1
            status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
            status.pack(side="left")
            label = ttk.Label(row, text=field_name, style="CardMuted.TLabel")
            label.pack(side="left")
            attach_help(label, self.t, f"idea.{field_name}", self.palette)
            self._script_status[field_name] = status
            ttk.Button(row, text="✎", width=3,
                       command=lambda n=field_name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        self._dup_btn = ttk.Button(actions, text="⧉ " + self.t("focuses.duplicate"),
                                   command=lambda: self.owner.duplicate_idea())
        self._dup_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._del_btn = ttk.Button(actions, text="🗑 " + self.t("ideas.delete"),
                                   command=lambda: self.owner.delete_idea())
        self._del_btn.pack(side="left", fill="x", expand=True)

    # --------------------------------------------------------------------- show
    def show(self, doc, idea: IdeaDef | None, editable: bool = True) -> None:
        self.flush_pending()
        self.doc = doc
        self.idea = idea
        self._editable = editable
        self._loading = True
        try:
            if idea is None:
                return
            self._title.configure(text=idea.id)
            origin = doc.ref.rel_file if doc is not None else ""
            self._subtitle.configure(
                text=origin + ("" if editable else "  ·  🔒 " + self.t("focuses.readonly")))
            self._id_entry.configure(state="normal")
            self._id_entry.delete(0, "end")
            self._id_entry.insert(0, idea.id)
            self._id_entry.configure(state="readonly")
            self._category.configure(values=self.owner.category_options())
            self._category.set(idea.category)
            lang = self.owner.loc_language
            self._lang.set(lang)
            key = idea.name_key
            self._loaded_name = self.owner.service.name_of(key, lang)
            self._loaded_desc = self.owner.service.desc_of(key, lang)
            self._name_var.set("" if self._loaded_name == key else self._loaded_name)
            self._desc.delete("1.0", "end")
            self._desc.insert("1.0", self._loaded_desc)
            self._namekey_var.set(idea.get_raw("name"))
            for name, var in self._num_vars.items():
                var.set(idea.get_raw(name))
            self._ledger.set(idea.get_raw("ledger"))
            for name, var in self._flags.items():
                var.set(idea.get_flag(name))
            self._refresh_icon()
            self._refresh_traits()
            self._refresh_scripts()
            self._set_state_all(editable)
            self._lang.configure(state="readonly")
            self._category.configure(state="readonly")
        finally:
            self._loading = False

    def _refresh_icon(self) -> None:
        idea = self.idea
        sprite = idea.sprite_name() if idea else ""
        self._icon_photo = self._icon_preview(sprite, size=(63, 50))
        self._icon_btn.configure(image=self._icon_photo)
        # show the stored short picture, or the implicit default for empty
        shown = (idea.picture if idea and idea.picture
                 else (self.t("ideas.default_picture") if idea else "")) or "—"
        self._icon_name.configure(text=shown)

    def _refresh_traits(self) -> None:
        traits = self.idea.traits if self.idea else []
        self._traits_label.configure(text=", ".join(traits) if traits else "—")

    def _refresh_scripts(self) -> None:
        idea = self.idea
        for name, label in self._script_status.items():
            filled = bool(idea and idea.get_script(name).strip())
            label.configure(text="●" if filled else "○",
                            foreground=self.palette.accent if filled
                            else self.palette.text_muted)

    # ------------------------------------------------------------------ commits
    def _commit_number(self, name: str) -> None:
        if not self._guard() or self.idea is None:
            return
        value = self._num_vars[name].get().strip()
        if value != self.idea.get_raw(name):
            self.idea.set_raw(name, value)
            self.owner.mark_dirty(self.doc)

    def _commit_ledger(self) -> None:
        if not self._guard() or self.idea is None:
            return
        self.idea.set_raw("ledger", self._ledger.get().strip())
        self.owner.mark_dirty(self.doc)

    def _commit_flag(self, name: str) -> None:
        if not self._guard() or self.idea is None:
            return
        self.idea.set_flag(name, self._flags[name].get())
        self.owner.mark_dirty(self.doc)

    def _commit_namekey(self) -> None:
        if not self._guard() or self.idea is None:
            return
        value = self._namekey_var.get().strip()
        if value != self.idea.get_raw("name"):
            self.idea.set_raw("name", value)
            self.owner.mark_dirty(self.doc)
            # loc key changed: reload name/desc for the new key
            self.show(self.doc, self.idea, self._editable)

    def _commit_loc(self) -> None:
        if not self._guard() or self.idea is None:
            return
        key = self.idea.name_key
        lang = self._lang.get()
        name = self._name_var.get().strip()
        desc = self._desc.get("1.0", "end").strip()
        cur_name = self.owner.service.name_of(key, lang)
        cur_desc = self.owner.service.desc_of(key, lang)
        write_name = name if name and name != cur_name else None
        write_desc = desc if desc != cur_desc and (desc or cur_desc) else None
        if write_name is not None or write_desc is not None:
            self.owner.service.set_loc(key, lang, write_name, write_desc)
            self.owner.refresh_tree_labels()

    def _commit_category(self) -> None:
        if not self._guard() or self.idea is None:
            return
        new_cat = self._category.get()
        if new_cat and new_cat != self.idea.category:
            self.owner.service.move_idea(self.doc, self.idea, new_cat)
            self.owner.mark_dirty(self.doc)
            self.owner.reload_tree(keep_selection=True)

    def _on_language(self, _event=None) -> None:
        self.owner.loc_language = self._lang.get()
        if self.idea is not None:
            self.show(self.doc, self.idea, self._editable)

    # ------------------------------------------------------------------ actions
    def _rename(self) -> None:
        if not self._guard() or self.idea is None:
            return
        idea = self.idea
        TextPromptDialog(self, self.owner, self.t("ideas.rename"),
                         self.t("focuses.rename_label"),
                         lambda new_id: self.owner.rename_idea(idea, new_id),
                         initial=idea.id,
                         taken=set(self.owner.known_ids()) - {idea.id})

    def _pick_icon(self) -> None:
        if not self._guard() or self.idea is None:
            return
        idea = self.idea

        def picked(sprite: str) -> None:
            picture = sprite
            if picture.startswith(_ICON_PREFIX):
                picture = picture[len(_ICON_PREFIX):]
            idea.picture = picture
            self.owner.mark_dirty(self.doc)
            self._refresh_icon()

        def imported(path) -> None:
            sprite = self.owner.import_icon(path, idea)
            if sprite:
                picked(sprite)

        IconPickerDialog(self, self.owner, self.owner.resolver, idea.picture,
                         picked, imported, prefixes=(_ICON_PREFIX,))

    def _pick_traits(self) -> None:
        if not self._guard() or self.idea is None:
            return
        idea = self.idea

        def picked(selected: list[str]) -> None:
            # keep unknown traits already on the idea (don't silently drop them)
            known = set(self.owner.trait_options())
            preserved = [t for t in idea.traits if t not in known]
            idea.traits = list(dict.fromkeys(selected + preserved))
            self.owner.mark_dirty(self.doc)
            self._refresh_traits()

        MultiPickDialog(self, self.owner, self.t("ideas.pick_traits"),
                        self.owner.trait_options(), picked,
                        preselected=set(idea.traits))

    def _edit_script(self, name: str) -> None:
        idea = self.idea
        if idea is None:
            return
        kinds = _script_kinds(name)
        if not self._editable:
            ScriptEditorDialog(self, self.owner, name, idea.get_script(name),
                               lambda text: None, kinds, idea.id)
            return

        def submitted(text: str) -> None:
            try:
                idea.set_script(name, text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self._refresh_scripts()

        ScriptEditorDialog(self, self.owner, name, idea.get_script(name),
                           submitted, kinds, idea.id)

    def _preview(self) -> None:
        if self.idea is not None:
            self.flush_pending()
            PdxPreviewDialog(self, self.owner,
                             self.t("focuses.preview_title", id=self.idea.id),
                             Block([Pair("ideas", Block([Pair(self.idea.category,
                                    Block([self.idea.pair]))]))]))


class IdeaCategoryInspector(InspectorBase):
    """Category-node form: idea_tags definition (read-only for vanilla), the
    file-flags (law/designer/use_list_view) written into every mod file that
    holds the category, plus the category-name localisation."""

    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.key = ""            # the file key (slot or category name)
        self._build()

    def _build(self) -> None:
        b = self.body
        r = 0
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=420)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        # localisation of the category name
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

        # file flags
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6); r += 1
        ttk.Label(b, text=self.t("ideas.cat.file_flags"), style="Card.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 3)); r += 1
        self._flags: dict[str, tk.BooleanVar] = {}
        for name in CATEGORY_FILE_FLAGS:
            var = tk.BooleanVar()
            self._flags[name] = var
            cb = ttk.Checkbutton(b, text=name, style="Card.TCheckbutton", variable=var,
                                 command=lambda n=name: self._commit_flag(n))
            cb.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
            attach_help(cb, self.t, f"idea.cat_{name}", self.palette)

        # idea_tags definition (read-only view)
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6); r += 1
        self._def_head = ttk.Label(b, text=self.t("ideas.cat.definition"), style="Card.TLabel")
        self._def_head.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 3)); r += 1
        self._def_info = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=420,
                                   justify="left")
        self._def_info.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        self._del_btn = ttk.Button(b, text="🗑 " + self.t("ideas.delete_category"),
                                   command=lambda: self.owner.delete_category())
        self._del_btn.grid(row=r, column=0, columnspan=2, sticky="ew")

    def show(self, key: str) -> None:
        self.flush_pending()
        self.key = key
        self._loading = True
        try:
            self._title.configure(text=key or "—")
            svc = self.owner.service
            defs = svc.category_defs()
            cdef = defs.get(key)
            owner_name = key if cdef is not None else svc.slot_owner(key)
            if cdef is None and owner_name:
                cdef = defs.get(owner_name)
            self._subtitle.configure(
                text=self.t("ideas.cat.subtitle", owner=owner_name or key))
            self._lang.set(self.owner.loc_language)
            self._reload_loc()
            # file flags: union across mod docs that hold this key
            flags: dict[str, bool] = {}
            for doc in self.owner._mod_docs:
                for name, on in doc.category_flags(key).items():
                    flags[name] = flags.get(name, False) or on
            for name, var in self._flags.items():
                var.set(flags.get(name, False))
            # definition read-out
            if cdef is not None:
                slots = cdef.slots()
                cslots = cdef.character_slots()
                lines = [
                    f"type = {cdef.type or '—'}",
                    f"cost = {cdef.cost or '—'}   removal_cost = {cdef.removal_cost or '—'}",
                    f"ledger = {cdef.ledger or '—'}   politics_tab = {'yes' if cdef.politics_tab else 'no'}",
                    f"hidden = {'yes' if cdef.hidden else 'no'}",
                    f"slot: {', '.join(slots) or '—'}",
                    f"character_slot: {', '.join(cslots) or '—'}",
                ]
                self._def_info.configure(text="\n".join(lines))
            else:
                self._def_info.configure(text=self.t("ideas.cat.no_def"))
            self._set_state_all(True)
            self._lang.configure(state="readonly")
        finally:
            self._loading = False

    def _reload_loc(self) -> None:
        if not self.key:
            return
        was = self._loading
        self._loading = True
        value = self.owner.service.name_of(self.key, self._lang.get())
        self._name_var.set("" if value == self.key else value)
        self._loading = was

    def _commit_loc(self) -> None:
        if self._loading or not self.key:
            return
        text = self._name_var.get().strip()
        if not text:
            return
        if text != self.owner.service.name_of(self.key, self._lang.get()):
            self.owner.service.set_loc(self.key, self._lang.get(), text, None)
            self.owner.refresh_tree_labels()

    def _commit_flag(self, name: str) -> None:
        if self._loading or not self.key:
            return
        value = self._flags[name].get()
        wrote = False
        for doc in self.owner._mod_docs:
            if doc.category_pair(self.key) is not None:
                doc.set_category_flag(self.key, name, value)
                self.owner.mark_dirty(doc)
                wrote = True
        if wrote:
            self.owner.refresh_tree_labels()
