"""Equipment inspector: a scrollable form over one block-backed `Equipment`.

Sections: identity (rename / archetype / parent) · general (year, type,
interface, tri-state flags) · stats (local overrides + greyed-out values
inherited from the archetype) · resources · script blocks · localisation.
Values load on ``show()``; edits commit straight back through the model
(debounced for typed fields) and notify the owning tab, which tracks dirty
documents. An absent field on concrete equipment means "inherited", so empty
input removes the pair instead of writing a zero.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...services.equipment_service import (
    EQUIP_FLAG_FIELDS,
    EQUIP_SCRIPT_FIELDS,
    EQUIP_STATS,
    RESOURCE_NAMES,
    Equipment,
    EquipmentDocument,
)
from ..common import TextPromptDialog
from ..common.inspector_base import InspectorBase
from ..common.script_editor import ScriptEditorDialog

_INTERFACE_CATEGORIES = (
    "interface_category_land", "interface_category_armor",
    "interface_category_air", "interface_category_naval",
    "interface_category_capital_ships", "interface_category_screen_ships",
    "interface_category_other_ships", "interface_category_other",
)
_SCRIPT_KINDS = {
    "can_be_produced": ("trigger",),
    "can_convert_from": ("trigger",),
    "module_slots": ("trigger",),
}
_TRI_UNSET = "—"


class EquipmentInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.eq: Equipment | None = None
        self.doc: EquipmentDocument | None = None
        self._sections: list[ttk.Widget] = []
        self._placeholder = ttk.Label(self.body,
                                      text=self.t("equipment.select_hint"),
                                      style="CardMuted.TLabel")
        self._placeholder.grid(row=0, column=0, columnspan=2, pady=24)

    # ------------------------------------------------------------------ show
    def show(self, doc: EquipmentDocument | None,
             eq: Equipment | None, editable: bool = True) -> None:
        self.flush_pending()
        self._loading = True
        try:
            self.doc = doc
            self.eq = eq
            self._editable = editable and eq is not None
            for w in self._sections:
                w.destroy()
            self._sections = []
            if eq is None or doc is None:
                self._placeholder.grid()
                return
            self._placeholder.grid_remove()
            self._build_form()
            if not self._editable:
                self._set_state_all(False)
        finally:
            self._loading = False

    def refresh(self) -> None:
        if self.eq is not None and self.doc is not None:
            self.show(self.doc, self.eq, self._editable)

    def _touch(self) -> None:
        self.owner.mark_dirty(self.doc)

    # ------------------------------------------------------------------ form
    def _build_form(self) -> None:
        self._build_identity()
        self._build_general()
        self._build_visual()
        self._build_stats()
        self._build_resources()
        self._build_scripts()
        self._build_loc()

    def _section(self, title: str) -> ttk.Labelframe:
        frame = ttk.Labelframe(self.body, text=title, style="Card.TLabelframe",
                               padding=(8, 6))
        row = len(self._sections) + 1
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        self._sections.append(frame)
        return frame

    def _row_entry(self, parent, row: int, label: str, value: str,
                   commit, width: int = 16) -> tk.StringVar:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=value)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        var.trace_add("write",
                      lambda *_: self._debounce(f"{id(parent)}:{row}:{label}",
                                                lambda: self._guarded(commit, var)))
        entry.bind("<FocusOut>", lambda e: self._guarded(commit, var))
        return var

    def _guarded(self, commit, var) -> None:
        if self._guard():
            commit(var.get())

    # --- identity -------------------------------------------------------------
    def _build_identity(self) -> None:
        t = self.t
        eq = self.eq
        sec = self._section(t("equipment.section.identity"))
        head = ttk.Frame(sec, style="Card.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        head.columnconfigure(0, weight=1)
        title = ("★ " if eq.is_archetype else "") + eq.id
        ttk.Label(head, text=title, style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Button(head, text=t("equipment.rename"),
                   command=self._rename).grid(row=0, column=1, sticky="e")

        self._arch_var = tk.BooleanVar(value=eq.is_archetype)
        ttk.Checkbutton(sec, text=t("equipment.field.is_archetype"),
                        style="Card.TCheckbutton", variable=self._arch_var,
                        command=self._commit_is_archetype).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=2)

        if not eq.is_archetype:
            ttk.Label(sec, text=t("equipment.field.archetype"),
                      style="CardMuted.TLabel").grid(row=2, column=0,
                                                     sticky="w", pady=2)
            self._archetype_var = tk.StringVar(value=eq.archetype or "")
            combo = ttk.Combobox(sec, textvariable=self._archetype_var,
                                 values=self.owner.service.archetypes(),
                                 width=28)
            combo.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=2)
            combo.bind("<<ComboboxSelected>>", lambda e: self._commit_archetype())
            combo.bind("<FocusOut>", lambda e: self._commit_archetype())

            ttk.Label(sec, text=t("equipment.field.parent"),
                      style="CardMuted.TLabel").grid(row=3, column=0,
                                                     sticky="w", pady=2)
            parents = (self.owner.service.concrete_ids_of(eq.archetype)
                       if eq.archetype else [])
            self._parent_var = tk.StringVar(value=eq.parent or "")
            combo = ttk.Combobox(sec, textvariable=self._parent_var,
                                 values=[p for p in parents if p != eq.id],
                                 width=28)
            combo.grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=2)
            combo.bind("<<ComboboxSelected>>", lambda e: self._commit_parent())
            combo.bind("<FocusOut>", lambda e: self._commit_parent())

    def _rename(self) -> None:
        if not self._editable:
            return
        taken = set(self.owner.service.equipment_index())

        def apply(new_id: str) -> None:
            self.owner.rename_equipment(self.doc, self.eq, new_id)

        TextPromptDialog(self, self.owner.editor, self.t("equipment.rename"),
                         self.t("equipment.name_label"), apply,
                         initial=self.eq.id, taken=taken - {self.eq.id})

    def _commit_is_archetype(self) -> None:
        if not self._guard():
            return
        self.eq.is_archetype = self._arch_var.get()
        self._touch()
        self.refresh()

    def _commit_archetype(self) -> None:
        if not self._guard():
            return
        value = self._archetype_var.get().strip()
        if value == (self.eq.archetype or ""):
            return
        self.eq.archetype = value or None
        self._touch()
        self.refresh()                          # parent options + inherited stats

    def _commit_parent(self) -> None:
        if not self._guard():
            return
        value = self._parent_var.get().strip()
        if value == (self.eq.parent or ""):
            return
        self.eq.parent = value or None
        self._touch()

    # --- general -----------------------------------------------------------------
    def _build_general(self) -> None:
        t = self.t
        eq = self.eq
        sec = self._section(t("equipment.section.general"))
        row = 0

        def int_committer(attr):
            def commit(text: str) -> None:
                text = text.strip()
                try:
                    value = int(text) if text else None
                except ValueError:
                    return
                if getattr(eq, attr) != value:
                    setattr(eq, attr, value)
                    self._touch()
            return commit

        for attr in ("year", "priority", "visual_level"):
            value = getattr(eq, attr)
            self._row_entry(sec, row, t(f"equipment.field.{attr}"),
                            "" if value is None else str(value),
                            int_committer(attr), width=10)
            row += 1

        def picture_commit(text: str) -> None:
            if (eq.picture or "") != text.strip():
                eq.picture = text.strip() or None
                self._touch()

        self._row_entry(sec, row, t("equipment.field.picture"),
                        eq.picture or "", picture_commit, width=28)
        row += 1

        def types_commit(text: str) -> None:
            values = [v for v in text.replace(",", " ").split() if v]
            if values != eq.types:
                eq.types = values
                self._touch()

        self._row_entry(sec, row, t("equipment.field.type"),
                        " ".join(eq.types), types_commit, width=28)
        row += 1

        row = self._combo_row(sec, row, t("equipment.field.interface_category"),
                              eq.interface_category or "",
                              _INTERFACE_CATEGORIES, "interface_category")
        row = self._combo_row(sec, row, t("equipment.field.group_by"),
                              eq.group_by or "", ("archetype", "type"),
                              "group_by")

        for flag in EQUIP_FLAG_FIELDS:
            row = self._tri_row(sec, row, flag)

    def _combo_row(self, sec, row: int, label: str, value: str,
                   options: tuple, attr: str) -> int:
        ttk.Label(sec, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=value)
        combo = ttk.Combobox(sec, textvariable=var, values=list(options),
                             width=28)
        combo.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)

        def commit(_event=None) -> None:
            if not self._guard():
                return
            text = var.get().strip()
            if (getattr(self.eq, attr) or "") != text:
                setattr(self.eq, attr, text or None)
                self._touch()

        combo.bind("<<ComboboxSelected>>", commit)
        combo.bind("<FocusOut>", commit)
        return row + 1

    def _tri_row(self, sec, row: int, flag: str) -> int:
        ttk.Label(sec, text=self.t(f"equipment.field.{flag}"),
                  style="CardMuted.TLabel").grid(row=row, column=0,
                                                 sticky="w", pady=2)
        current = self.eq.get_opt_flag(flag)
        var = tk.StringVar(value=_TRI_UNSET if current is None
                           else ("yes" if current else "no"))
        combo = ttk.Combobox(sec, textvariable=var, state="readonly",
                             values=(_TRI_UNSET, "yes", "no"), width=8)
        combo.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)

        def commit(_event=None) -> None:
            if not self._guard():
                return
            text = var.get()
            value = None if text == _TRI_UNSET else text == "yes"
            if self.eq.get_opt_flag(flag) != value:
                self.eq.set_opt_flag(flag, value)
                self._touch()

        combo.bind("<<ComboboxSelected>>", commit)
        return row + 1

    # --- visual ----------------------------------------------------------------
    def _sprite_name(self) -> str:
        """The sprite the game shows for this entry: ``GFX_<picture>_medium``
        (falling back to the id, then the inherited archetype picture)."""
        eq = self.eq
        picture = (eq.picture
                   or self.owner.service.inherited_value(eq, "picture")
                   or eq.id)
        return f"GFX_{picture}_medium"

    def _build_visual(self) -> None:
        t = self.t
        sec = self._section(t("equipment.section.visual"))
        sprite = self._sprite_name()
        self._visual_photo = self._icon_preview(sprite, (120, 60))
        ttk.Label(sec, image=self._visual_photo).grid(row=0, column=0,
                                                      pady=(2, 0))
        ttk.Label(sec, text=sprite, style="CardMuted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        if self._editable:
            row = ttk.Frame(sec, style="Card.TFrame")
            row.grid(row=0, column=1, sticky="w", padx=(8, 0))
            ttk.Button(row, text="🖼 " + t("equipment.pick_icon"),
                       command=self._pick_visual).pack(side="left")

    def _pick_visual(self) -> None:
        """Gallery picker + custom import for the equipment picture sprite."""
        if self.eq is None or not self.owner.resolver_ready():
            return
        eq = self.eq
        from ..common.dialogs import IconPickerDialog

        def target_picture() -> str:
            """Importing/picking always writes an explicit picture key so the
            sprite lookup stays deterministic."""
            if not eq.picture:
                eq.picture = eq.id
                self._touch()
            return eq.picture

        def picked(sprite: str) -> None:
            if self.owner.reuse_equipment_sprite(target_picture(), sprite):
                self._touch()
                self.refresh()

        def imported(path, _keep_size: bool = False) -> None:
            if self.owner.import_equipment_icon(path, target_picture()):
                dialog.destroy()
                self._touch()
                self.refresh()

        dialog = IconPickerDialog(self, self.owner.editor, self.owner.resolver,
                                  self._sprite_name(), picked, imported,
                                  prefixes=("GFX_",))

    # --- stats ---------------------------------------------------------------
    def _build_stats(self) -> None:
        t = self.t
        eq = self.eq
        sec = self._section(t("equipment.section.stats"))
        row = 0
        local_keys = set()
        for pair in eq.stat_pairs():
            local_keys.add(pair.key.lower())
            self._stat_row(sec, row, pair.key, pair.value.raw, inherited=False)
            row += 1
        # Values inherited from the archetype and not overridden locally: shown
        # greyed-out; typing a value creates a local override, clearing an
        # override falls back to inherited (via refresh).
        for key, value in sorted(self.owner.service.inherited_stats(eq).items()):
            if key.lower() in local_keys:
                continue
            self._stat_row(sec, row, key, value, inherited=True)
            row += 1

        add = ttk.Frame(sec, style="Card.TFrame")
        add.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        add.columnconfigure(0, weight=1)
        self._new_stat_var = tk.StringVar()
        combo = ttk.Combobox(add, textvariable=self._new_stat_var,
                             values=[s for s in EQUIP_STATS
                                     if s not in local_keys], width=26)
        combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(add, text="➕ " + t("equipment.add_stat"), width=14,
                   command=self._add_stat).grid(row=0, column=1, padx=(6, 0))

    def _stat_row(self, sec, row: int, key: str, value: str,
                  inherited: bool) -> None:
        label = ttk.Label(sec, text=key,
                          style="CardMuted.TLabel" if inherited
                          else "Card.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=1)
        var = tk.StringVar(value=value)
        entry = ttk.Entry(sec, textvariable=var, width=12)
        if inherited:
            entry.configure(foreground=self.palette.text_muted)
        entry.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=1)

        def commit() -> None:
            if not self._guard():
                return
            text = var.get().strip()
            if inherited:
                if text and text != value:
                    self.eq.block.set_ci(key, _scalar_text(text))
                    self._touch()
                    self.refresh()
                return
            if not text:
                self.eq.block.remove_ci(key)
                self._touch()
                self.refresh()                  # may fall back to inherited
            elif text != value:
                self.eq.block.set_ci(key, _scalar_text(text))
                self._touch()

        var.trace_add("write",
                      lambda *_: self._debounce(f"stat:{key}", commit))
        entry.bind("<FocusOut>", lambda e: commit())
        if not inherited:
            ttk.Button(sec, text="✕", width=3,
                       command=lambda: self._remove_stat(key)).grid(
                row=row, column=2, padx=(4, 0), pady=1)

    def _remove_stat(self, key: str) -> None:
        if not self._guard():
            return
        self.eq.block.remove_ci(key)
        self._touch()
        self.refresh()

    def _add_stat(self) -> None:
        if not self._guard():
            return
        key = self._new_stat_var.get().strip()
        if not key or self.eq.block.has_ci(key):
            return
        inherited = self.owner.service.inherited_stats(self.eq)
        self.eq.block.set_ci(key, _scalar_text(inherited.get(key, "0")))
        self._touch()
        self.refresh()

    # --- resources ---------------------------------------------------------------
    def _build_resources(self) -> None:
        t = self.t
        sec = self._section(t("equipment.section.resources"))
        self._res_rows: list[tuple[tk.StringVar, tk.StringVar]] = []
        row = 0
        for name, amount in self.eq.resources.items():
            self._resource_row(sec, row, name, str(amount))
            row += 1
        ttk.Button(sec, text="➕ " + t("equipment.add_resource"),
                   command=lambda: (self._resource_row(sec, len(self._res_rows),
                                                       "", ""))).grid(
            row=99, column=0, sticky="w", pady=(6, 0))

    def _resource_row(self, sec, row: int, name: str, amount: str) -> None:
        name_var = tk.StringVar(value=name)
        combo = ttk.Combobox(sec, textvariable=name_var,
                             values=list(RESOURCE_NAMES), width=14)
        combo.grid(row=row, column=0, sticky="w", pady=1)
        amount_var = tk.StringVar(value=amount)
        entry = ttk.Entry(sec, textvariable=amount_var, width=6)
        entry.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=1)
        self._res_rows.append((name_var, amount_var))

        def commit(_event=None) -> None:
            if self._guard():
                self._commit_resources()

        for widget, var in ((combo, name_var), (entry, amount_var)):
            var.trace_add("write", lambda *_: self._debounce("resources",
                                                             self._commit_resources))
            widget.bind("<FocusOut>", commit)
        combo.bind("<<ComboboxSelected>>", commit)
        ttk.Button(sec, text="✕", width=3,
                   command=lambda: self._remove_resource(name_var)).grid(
            row=row, column=2, padx=(4, 0), pady=1)

    def _remove_resource(self, name_var: tk.StringVar) -> None:
        if not self._guard():
            return
        self._res_rows = [(n, a) for n, a in self._res_rows if n is not name_var]
        self._commit_resources()
        self.refresh()

    def _commit_resources(self) -> None:
        if not self._guard():
            return
        out: dict[str, int] = {}
        for name_var, amount_var in self._res_rows:
            name = name_var.get().strip()
            if not name:
                continue
            try:
                out[name] = int(amount_var.get().strip() or "0")
            except ValueError:
                continue
        if out != self.eq.resources:
            self.eq.resources = out
            self._touch()

    # --- script blocks --------------------------------------------------------
    def _build_scripts(self) -> None:
        t = self.t
        sec = self._section(t("equipment.section.scripts"))
        row = 0
        for key in EQUIP_SCRIPT_FIELDS:
            self._script_row(sec, row, key,
                             lambda k=key: self.eq.get_script(k),
                             lambda text, k=key: self.eq.set_script(k, text))
            row += 1
        self._script_row(sec, row, "module_count_limit",
                         lambda: self.eq.get_repeated_script(
                             "module_count_limit"),
                         lambda text: self.eq.set_repeated_script(
                             "module_count_limit", text))
        row += 1
        for pair in self.eq.extra_block_pairs():
            key = pair.key
            self._script_row(sec, row, key,
                             lambda k=key: self.eq.get_script(k),
                             lambda text, k=key: self.eq.set_script(k, text))
            row += 1

    def _script_row(self, sec, row: int, key: str, getter, setter) -> None:
        label = self.t(f"equipment.script.{key}") \
            if key in (*EQUIP_SCRIPT_FIELDS, "module_count_limit") else key
        ttk.Label(sec, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=2)
        text = getter()
        preview = text.strip().splitlines()[0][:38] if text.strip() else "—"
        ttk.Label(sec, text=preview, style="CardMuted.TLabel").grid(
            row=row, column=1, sticky="w", padx=(8, 0), pady=2)

        def edit() -> None:
            if not self._editable:
                return

            def submit(new_text: str) -> None:
                try:
                    setter(new_text)
                except Exception as exc:                  # noqa: BLE001
                    messagebox.showerror("ANKA",
                                         self.t("equipment.err.bad_script",
                                                error=str(exc)))
                    return
                self._touch()
                self.refresh()

            ScriptEditorDialog(self, self.owner.editor, label, getter(),
                               submit, kinds=_SCRIPT_KINDS.get(key,
                                                               ("trigger",)))

        ttk.Button(sec, text="✎", width=3, command=edit).grid(
            row=row, column=2, padx=(4, 0), pady=2)

    # --- localisation --------------------------------------------------------
    def _build_loc(self) -> None:
        t = self.t
        eq = self.eq
        sec = self._section(t("equipment.section.loc"))
        row = 0
        for lang in self.owner.languages():
            ttk.Label(sec, text=lang, style="CardTitle.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w",
                pady=(6 if row else 0, 2))
            row += 1
            for suffix, label_key in (("", "equipment.loc.name"),
                                      ("_short", "equipment.loc.short"),
                                      ("_desc", "equipment.loc.desc")):
                key = eq.id + suffix

                def committer(k=key, lg=lang):
                    def commit(text: str) -> None:
                        if text != (self.owner.loc_get(k, lg) or ""):
                            self.owner.loc_set(k, lg, text)
                    return commit

                self._row_entry(sec, row, t(label_key),
                                self.owner.loc_get(key, lang) or "",
                                committer(), width=30)
                row += 1


def _scalar_text(text: str):
    """Normalize numeric input for storage; keep raw text for the rest."""
    try:
        value = float(text)
    except ValueError:
        return text
    return int(value) if value == int(value) else value
