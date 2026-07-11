"""Inspectors of the Traits editor: a country-leader trait form and a
unit-leader trait form.

Both are dumb forms over a block-backed `TraitDef`: values load on ``show()`` and
edits commit back instantly (debounced for typed fields) through the owning
editor, which tracks dirty documents and saves them. A shared base
(`_TraitInspectorBase`) supplies the header (id + rename + localisation), the
generic field/script row builders and the footer (preview / duplicate / delete);
each family subclass only lays out its own fields.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.pdx import Block, Pair
from ...services.trait_service import (
    COUNTRY_SCRIPT_FIELDS,
    UNIT_EFFECT_FIELDS,
    UNIT_MODIFIER_FIELDS,
    UNIT_NUM_FIELDS,
    UNIT_SKILL_FIELDS,
    UNIT_STR_FIELDS,
    UNIT_TRAIT_TYPES,
    UNIT_TRIGGER_FIELDS,
    UNIT_TYPES,
)
from ..common import (
    InspectorBase,
    PdxPreviewDialog,
    ScriptEditorDialog,
    TextPromptDialog,
)

# Which picker catalog each script field offers.
_MODIFIER_KINDS = ("modifier",)
_TRIGGER_KINDS = ("trigger",)
_EFFECT_KINDS = ("effect", "trigger")

_UNIT_SCRIPT_KINDS: dict[str, tuple[str, ...]] = {}
for _f in UNIT_MODIFIER_FIELDS:
    _UNIT_SCRIPT_KINDS[_f] = _MODIFIER_KINDS
for _f in UNIT_TRIGGER_FIELDS:
    _UNIT_SCRIPT_KINDS[_f] = _TRIGGER_KINDS
for _f in UNIT_EFFECT_FIELDS:
    _UNIT_SCRIPT_KINDS[_f] = _EFFECT_KINDS
for _f in ("sub_unit_modifiers", "trait_xp_factor", "ai_will_do",
           "new_commander_weight", "parent"):
    _UNIT_SCRIPT_KINDS[_f] = _TRIGGER_KINDS

_COUNTRY_SCRIPT_KINDS: dict[str, tuple[str, ...]] = {
    "ai_will_do": _TRIGGER_KINDS,
    "targeted_modifier": _MODIFIER_KINDS,
    "equipment_bonus": _MODIFIER_KINDS,
}


class _TraitInspectorBase(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.trait = None
        self.doc = None
        self._entry_vars: dict[str, tk.StringVar] = {}
        self._combo_widgets: dict[str, ttk.Combobox] = {}
        self._bool_vars: dict[str, tk.BooleanVar] = {}
        self._bool_defaults: dict[str, bool] = {}
        self._script_status: dict[str, ttk.Label] = {}
        self._loaded_name = ""
        self._loaded_desc = ""
        r = self._build_header(0)
        r = self._build_body(r)
        self._build_footer(r)

    # ------------------------------------------------------------------ header
    def _build_header(self, r: int) -> int:
        b = self.body
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=420)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        ttk.Label(b, text="ID", style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        id_row = ttk.Frame(b, style="Card.TFrame")
        id_row.grid(row=r, column=1, sticky="ew", padx=(8, 0)); r += 1
        id_row.columnconfigure(0, weight=1)
        self._id_entry = ttk.Entry(id_row, state="readonly", font=("Consolas", 10))
        self._id_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(id_row, text="✎", width=3, command=self._rename).grid(
            row=0, column=1, padx=(4, 0))

        ttk.Label(b, text=self.t("focuses.inspector.language"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        self._lang = ttk.Combobox(b, state="readonly", values=list(HOI4_LANGUAGES),
                                  width=14)
        self._lang.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
        self._lang.set(self.owner.loc_language)
        self._lang.bind("<<ComboboxSelected>>", self._on_language)

        ttk.Label(b, text=self.t("focuses.inspector.name"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        self._name_var = tk.StringVar()
        name_entry = ttk.Entry(b, textvariable=self._name_var)
        name_entry.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._name_var.trace_add("write",
                                 lambda *_: self._debounce("loc", self._commit_loc, 1200))
        name_entry.bind("<FocusOut>", lambda e: self._commit_loc())

        ttk.Label(b, text=self.t("focuses.inspector.desc"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="nw", pady=3)
        self._desc = tk.Text(b, height=3, wrap="word", bg=self.palette.surface_alt,
                             fg=self.palette.text, insertbackground=self.palette.text,
                             relief="flat", font=("Segoe UI", 10))
        self._desc.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._desc.bind("<KeyRelease>",
                        lambda e: self._debounce("loc", self._commit_loc, 1200))
        self._desc.bind("<FocusOut>", lambda e: self._commit_loc())

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6); r += 1
        return r

    # ------------------------------------------------------------- row builders
    def _entry_field(self, r: int, key: str, width: int = 18,
                     label: str | None = None) -> int:
        ttk.Label(self.body, text=label or key, style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        var = tk.StringVar()
        entry = ttk.Entry(self.body, textvariable=var, width=width)
        entry.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
        self._entry_vars[key] = var
        var.trace_add("write", lambda *_, k=key: self._debounce(
            f"e:{k}", lambda k=k: self._commit_entry(k)))
        entry.bind("<FocusOut>", lambda e, k=key: self._commit_entry(k))
        return r + 1

    def _combo_field(self, r: int, key: str, values: tuple[str, ...],
                     label: str | None = None) -> int:
        ttk.Label(self.body, text=label or key, style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        combo = ttk.Combobox(self.body, state="readonly", values=("",) + values,
                             width=22)
        combo.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
        combo.bind("<<ComboboxSelected>>", lambda e, k=key: self._commit_combo(k))
        self._combo_widgets[key] = combo
        return r + 1

    def _bool_field(self, r: int, key: str, default: bool,
                    label: str | None = None) -> int:
        var = tk.BooleanVar()
        self._bool_vars[key] = var
        self._bool_defaults[key] = default
        ttk.Checkbutton(self.body, text=label or key, style="Card.TCheckbutton",
                        variable=var,
                        command=lambda k=key: self._commit_bool(k)).grid(
            row=r, column=0, columnspan=2, sticky="w"); r += 1
        return r

    def _script_row(self, r: int, key: str, kinds: tuple[str, ...],
                    label: str | None = None) -> int:
        row = ttk.Frame(self.body, style="Card.TFrame")
        row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
        status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
        status.pack(side="left")
        ttk.Label(row, text=label or key, style="CardMuted.TLabel").pack(side="left")
        self._script_status[key] = status
        ttk.Button(row, text="✎", width=3,
                   command=lambda k=key, kd=kinds: self._edit_script(k, kd)).pack(
            side="left", padx=(6, 0))
        return r + 1

    def _section(self, r: int, text: str) -> int:
        ttk.Separator(self.body).grid(row=r, column=0, columnspan=2, sticky="ew",
                                      pady=6); r += 1
        ttk.Label(self.body, text=text, style="Card.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 3)); r += 1
        return r

    # ------------------------------------------------------------------ footer
    def _build_footer(self, r: int) -> None:
        b = self.body
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview).pack(side="left")
        self._dup_btn = ttk.Button(actions, text="⧉ " + self.t("focuses.duplicate"),
                                   command=lambda: self.owner.duplicate_trait(
                                       self.trait, self.doc))
        self._dup_btn.pack(side="right", padx=(4, 0))
        self._del_btn = ttk.Button(actions, text="🗑 " + self.t("traits.delete"),
                                   command=lambda: self.owner.delete_trait(
                                       self.trait, self.doc))
        self._del_btn.pack(side="right")

    # --------------------------------------------------------------------- show
    def show(self, doc, trait, editable: bool = True) -> None:
        self.flush_pending()
        self.doc = doc
        self.trait = trait
        self._editable = editable
        self._loading = True
        try:
            if trait is None:
                return
            self._title.configure(text=trait.id)
            origin = doc.ref.rel_file if doc is not None else ""
            self._subtitle.configure(
                text=origin + ("" if editable
                               else "  ·  🔒 " + self.t("focuses.readonly")))
            self._id_entry.configure(state="normal")
            self._id_entry.delete(0, "end")
            self._id_entry.insert(0, trait.id)
            self._id_entry.configure(state="readonly")
            lang = self.owner.loc_language
            self._lang.set(lang)
            key = trait.loc_key
            self._loaded_name = self.owner.service.name_of(key, lang)
            self._loaded_desc = self.owner.service.desc_of(key, lang)
            self._name_var.set("" if self._loaded_name == key else self._loaded_name)
            self._desc.configure(state="normal")   # a disabled Text ignores edits
            self._desc.delete("1.0", "end")
            self._desc.insert("1.0", self._loaded_desc.replace("\\n", "\n"))
            for k, var in self._entry_vars.items():
                var.set(trait.get_raw(k))
            for k, combo in self._combo_widgets.items():
                combo.set(trait.get_raw(k))
            for k, var in self._bool_vars.items():
                var.set(trait.get_flag(k, self._bool_defaults[k]))
            self._load_extra()
            self._refresh_scripts()
            self._set_state_all(editable)
            self._lang.configure(state="readonly")
            for combo in self._combo_widgets.values():
                combo.configure(state="readonly" if editable else "disabled")
        finally:
            self._loading = False

    def _load_extra(self) -> None:
        """Subclass hook for fields not covered by the generic maps."""

    def _refresh_scripts(self) -> None:
        for key, status in self._script_status.items():
            filled = bool(self.trait and self.trait.has_script(key)
                          and self.trait.get_script(key).strip())
            status.configure(text="●" if filled else "○",
                             foreground=self.palette.accent if filled
                             else self.palette.text_muted)

    # ------------------------------------------------------------------ commits
    def _commit_entry(self, key: str) -> None:
        if not self._guard() or self.trait is None:
            return
        value = self._entry_vars[key].get().strip()
        if value != self.trait.get_raw(key):
            self.trait.set_raw(key, value)
            self.owner.mark_dirty(self.doc)

    def _commit_combo(self, key: str) -> None:
        if not self._guard() or self.trait is None:
            return
        self.trait.set_raw(key, self._combo_widgets[key].get().strip())
        self.owner.mark_dirty(self.doc)

    def _commit_bool(self, key: str) -> None:
        if not self._guard() or self.trait is None:
            return
        value = self._bool_vars[key].get()
        if value == self._bool_defaults[key]:
            self.trait.block.remove(key)
        else:
            self.trait.set_raw(key, "yes" if value else "no")
        self.owner.mark_dirty(self.doc)

    def _commit_loc(self) -> None:
        if not self._guard() or self.trait is None:
            return
        key = self.trait.loc_key
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

    def _on_language(self, _event=None) -> None:
        self.owner.loc_language = self._lang.get()
        if self.trait is not None:
            self.show(self.doc, self.trait, self._editable)

    # ------------------------------------------------------------------ actions
    def _rename(self) -> None:
        if not self._guard() or self.trait is None:
            return
        trait = self.trait
        doc = self.doc
        TextPromptDialog(self, self.owner, self.t("traits.rename"),
                         self.t("focuses.rename_label"),
                         lambda new_id: self.owner.rename_trait(trait, doc, new_id),
                         initial=trait.id,
                         taken=self.owner.known_ids(trait.family) - {trait.id})

    def _edit_script(self, key: str, kinds: tuple[str, ...]) -> None:
        trait = self.trait
        if trait is None:
            return
        text = trait.get_script(key)
        if not self._editable:
            ScriptEditorDialog(self, self.owner, key, text, lambda t: None,
                               kinds, trait.id)
            return

        def submitted(new_text: str) -> None:
            try:
                trait.set_script(key, new_text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self._refresh_scripts()

        ScriptEditorDialog(self, self.owner, key, text, submitted, kinds, trait.id)

    def _preview(self) -> None:
        if self.trait is None:
            return
        self.flush_pending()
        PdxPreviewDialog(self, self.owner,
                         self.t("focuses.preview_title", id=self.trait.id),
                         Block([Pair("leader_traits", Block([self.trait.pair]))]))

    # subclasses implement the middle
    def _build_body(self, r: int) -> int:  # pragma: no cover - overridden
        raise NotImplementedError


class CountryTraitInspector(_TraitInspectorBase):
    """Country-leader / advisor / idea trait: a few special keys plus the flat
    country modifiers edited together as one block."""

    def _build_body(self, r: int) -> int:
        r = self._bool_field(r, "random", default=True, label="random")
        r = self._entry_field(r, "sprite", width=8, label="sprite")

        # flat country modifiers (top-level pairs that are not special keys)
        r = self._section(r, self.t("traits.country_modifiers"))
        row = ttk.Frame(self.body, style="Card.TFrame")
        row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1); r += 1
        row.columnconfigure(1, weight=1)
        self._mod_status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
        self._mod_status.pack(side="left")
        self._mod_edit_btn = ttk.Button(row, text="✎ " + self.t("traits.edit_modifiers"),
                                        command=self._edit_country_modifiers)
        self._mod_edit_btn.pack(side="left")
        self._mod_summary = ttk.Label(self.body, text="—", style="CardMuted.TLabel",
                                      wraplength=380, justify="left")
        self._mod_summary.grid(row=r, column=0, columnspan=2, sticky="w",
                               padx=(24, 0)); r += 1

        r = self._section(r, self.t("traits.scripts"))
        for key in COUNTRY_SCRIPT_FIELDS:
            r = self._script_row(r, key, _COUNTRY_SCRIPT_KINDS[key])
        return r

    def _load_extra(self) -> None:
        text = self.trait.country_modifiers_text() if self.trait else ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            summary = ", ".join(lines[:4]) + (" …" if len(lines) > 4 else "")
        else:
            summary = "—"
        self._mod_summary.configure(text=summary)
        self._mod_status.configure(text="●" if lines else "○",
                                   foreground=self.palette.accent if lines
                                   else self.palette.text_muted)

    def _edit_country_modifiers(self) -> None:
        trait = self.trait
        if trait is None:
            return
        text = trait.country_modifiers_text()
        if not self._editable:
            ScriptEditorDialog(self, self.owner, "modifiers", text, lambda t: None,
                               _MODIFIER_KINDS, trait.id)
            return

        def submitted(new_text: str) -> None:
            try:
                trait.set_country_modifiers_text(new_text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self._load_extra()

        ScriptEditorDialog(self, self.owner, "modifiers", text, submitted,
                           _MODIFIER_KINDS, trait.id)


class UnitTraitInspector(_TraitInspectorBase):
    """Unit-leader (general / field-marshal / admiral) trait: type, trait_type,
    skill bonuses (flat × factor), tree wiring and scoped modifier/effect blocks."""

    def _build_body(self, r: int) -> int:
        r = self._combo_field(r, "type", UNIT_TYPES, label="type")
        r = self._combo_field(r, "trait_type", UNIT_TRAIT_TYPES, label="trait_type")

        r = self._section(r, self.t("traits.skills"))
        for skill in UNIT_SKILL_FIELDS:
            r = self._skill_pair_row(r, skill)

        r = self._section(r, self.t("traits.properties"))
        for key in UNIT_NUM_FIELDS:
            r = self._entry_field(r, key, width=10, label=key)
        for key in UNIT_STR_FIELDS:
            r = self._entry_field(r, key, width=22, label=key)

        r = self._section(r, self.t("traits.modifiers"))
        for key in UNIT_MODIFIER_FIELDS:
            r = self._script_row(r, key, _UNIT_SCRIPT_KINDS[key])

        r = self._section(r, self.t("traits.scripts"))
        for key in ("allowed", "prerequisites", "gain_xp", "gain_xp_leader",
                    "on_add", "on_remove", "daily_effect", "ai_will_do",
                    "new_commander_weight", "parent", "sub_unit_modifiers",
                    "trait_xp_factor"):
            r = self._script_row(r, key, _UNIT_SCRIPT_KINDS[key])
        return r

    def _skill_pair_row(self, r: int, skill: str) -> int:
        ttk.Label(self.body, text=skill, style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        fr = ttk.Frame(self.body, style="Card.TFrame")
        fr.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
        for suffix in ("", "_factor"):
            key = skill + suffix
            var = tk.StringVar()
            entry = ttk.Entry(fr, textvariable=var, width=7)
            entry.pack(side="left")
            if suffix == "":
                ttk.Label(fr, text="×", style="CardMuted.TLabel").pack(
                    side="left", padx=4)
            self._entry_vars[key] = var
            var.trace_add("write", lambda *_, k=key: self._debounce(
                f"e:{k}", lambda k=k: self._commit_entry(k)))
            entry.bind("<FocusOut>", lambda e, k=key: self._commit_entry(k))
        return r + 1
