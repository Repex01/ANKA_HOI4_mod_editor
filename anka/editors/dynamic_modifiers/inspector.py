"""Inspector of one dynamic modifier.

Form: localized name/description (per language) · icon (sprite picker with
image import) · ``attacker_modifier`` flag · ``enable`` / ``remove_trigger``
trigger scripts · the modifier list itself — one row per static modifier
(value is a number or a variable name), added through the searchable
modifier-catalog picker.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.pdx import Block, Pair
from ..common import (IconPickerDialog, InspectorBase, PdxPreviewDialog,
                      ScriptEditorDialog, SinglePickDialog, TextPromptDialog)


class DynamicModifierInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc = None
        self.entry = None
        self._icon_photo = None
        self._mod_rows: list = []

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

    def _build_form(self) -> None:
        entry, b = self.entry, self.body
        r = 0
        ttk.Label(b, text=entry.name + ("  🔒" if not self._editable else ""),
                  style="Heading.TLabel").grid(row=r, column=0, columnspan=2,
                                               sticky="w")
        r += 1
        ttk.Label(b, text=self.doc.ref.rel_file, style="CardMuted.TLabel",
                  wraplength=430).grid(row=r, column=0, columnspan=2,
                                       sticky="w", pady=(0, 6))
        r += 1

        # --- localisation ----------------------------------------------------
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

        ttk.Label(b, text=self.t("focuses.inspector.desc"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="nw",
                                                 pady=3)
        self._desc = tk.Text(b, height=3, wrap="word",
                             bg=self.palette.surface_alt,
                             fg=self.palette.text,
                             insertbackground=self.palette.text,
                             relief="flat", font=("Segoe UI", 10))
        self._desc.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3)
        self._desc.bind("<KeyRelease>",
                        lambda e: self._debounce("loc", self._commit_loc, 1200))
        self._desc.bind("<FocusOut>", lambda e: self._commit_loc())
        r += 1
        self._reload_loc()

        # --- icon -------------------------------------------------------------
        ttk.Label(b, text=self.t("focuses.inspector.icon"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w",
                                                 pady=3)
        icon_row = ttk.Frame(b, style="Card.TFrame")
        icon_row.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3)
        self._icon_btn = tk.Button(icon_row, bd=0, bg=self.palette.surface_alt,
                                   activebackground=self.palette.surface,
                                   cursor="hand2", command=self._pick_icon)
        self._icon_btn.pack(side="left")
        self._icon_name = ttk.Label(icon_row, text="",
                                    style="CardMuted.TLabel", wraplength=230)
        self._icon_name.pack(side="left", padx=(8, 4))
        ttk.Button(icon_row, text="✕", width=2,
                   command=self._clear_icon).pack(side="left")
        self._refresh_icon()
        r += 1

        # --- attacker_modifier -------------------------------------------------
        self._attacker_var = tk.BooleanVar(value=entry.attacker_modifier)
        ttk.Checkbutton(b, text="attacker_modifier",
                        style="Card.TCheckbutton",
                        variable=self._attacker_var,
                        command=self._commit_attacker).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=3)
        r += 1

        # --- trigger scripts ---------------------------------------------------
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=6)
        r += 1
        for name in ("enable", "remove_trigger"):
            line = ttk.Frame(b, style="Card.TFrame")
            line.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
            filled = bool(entry.get_script(name).strip())
            ttk.Label(line, text="●" if filled else "○",
                      style="CardMuted.TLabel", width=2).pack(side="left")
            ttk.Label(line, text=name, style="CardMuted.TLabel").pack(
                side="left")
            ttk.Button(line, text="✎", width=3,
                       command=lambda n=name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))
            r += 1

        # --- modifiers ----------------------------------------------------------
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=6)
        r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=self.t("dyn_mod.modifiers"),
                  style="CardMuted.TLabel",
                  font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Button(head, text="➕", width=3,
                   command=self._add_modifier).pack(side="right")
        r += 1
        for pair in entry.modifiers():
            r = self._modifier_row(r, pair)

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=8)
        r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview_pdx).pack(side="left")
        self._delete_btn = ttk.Button(
            actions, text="🗑 " + self.t("dyn_mod.delete"),
            command=self._delete)
        self._delete_btn.pack(side="right")

    # ------------------------------------------------------------- loc fields
    def _reload_loc(self) -> None:
        entry = self.entry
        lang = self._lang.get()
        name_value = self.owner.loc_get(entry.name, lang)
        self._name_var.set(name_value)
        desc = self.owner.loc_get(f"{entry.name}_desc", lang)
        self._desc.configure(state="normal")
        self._desc.delete("1.0", "end")
        self._desc.insert("1.0", desc.replace("\\n", "\n"))

    def _commit_loc(self) -> None:
        if not self._guard() or self.entry is None:
            return
        key = self.entry.name
        lang = self._lang.get()
        name = self._name_var.get().strip()
        desc = self._desc.get("1.0", "end").strip()
        cur_name = self.owner.loc_get(key, lang)
        cur_desc = self.owner.loc_get(f"{key}_desc", lang).replace("\\n", "\n")
        if name and name != cur_name:
            self.owner.loc_set(key, lang, name)
            self.owner.refresh_tree_labels()
        if desc != cur_desc and (desc or cur_desc):
            self.owner.loc_set(f"{key}_desc", lang, desc)

    def _on_language(self, _event=None) -> None:
        self.flush_pending()
        self.owner.loc_language = self._lang.get()
        was = self._loading
        self._loading = True
        self._reload_loc()
        self._loading = was

    # ------------------------------------------------------------------- icon
    def _refresh_icon(self) -> None:
        sprite = self.entry.icon if self.entry else ""
        self._icon_photo = self._icon_preview(sprite, size=(63, 50))
        self._icon_btn.configure(image=self._icon_photo)
        self._icon_name.configure(text=sprite or "—")

    def _pick_icon(self) -> None:
        if not self._guard() or self.entry is None:
            return
        entry = self.entry

        def picked(sprite: str) -> None:
            entry.set_icon(sprite)
            self.owner.mark_dirty(self.doc)
            self._refresh_icon()

        def imported(path, keep_size: bool = False) -> None:
            try:
                dds, _gfx = self.owner.context.icons.add_idea_icon(
                    path, entry.name, resize=not keep_size)
            except Exception as exc:                      # noqa: BLE001
                messagebox.showerror("ANKA", str(exc))
                return
            sprite = f"GFX_idea_{entry.name}"
            self.owner.resolver.add(sprite, dds)
            picked(sprite)

        IconPickerDialog(self, self.owner, self.owner.resolver, entry.icon,
                         picked, imported, prefixes=("GFX_",))

    def _clear_icon(self) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_icon("")
        self.owner.mark_dirty(self.doc)
        self._refresh_icon()

    # ---------------------------------------------------------------- scripts
    def _commit_attacker(self) -> None:
        if not self._guard() or self.entry is None:
            return
        self.entry.set_attacker_modifier(self._attacker_var.get())
        self.owner.mark_dirty(self.doc)

    def _edit_script(self, name: str) -> None:
        entry = self.entry

        def submitted(text: str) -> None:
            try:
                entry.set_script(name, text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self._rebuild()

        ScriptEditorDialog(self, self.owner, name, entry.get_script(name),
                           submitted if self._editable else (lambda t: None),
                           ("trigger",), entry.name)

    # -------------------------------------------------------------- modifiers
    def _modifier_row(self, row: int, pair: Pair) -> int:
        b = self.body
        line = ttk.Frame(b, style="Card.TFrame")
        line.grid(row=row, column=0, columnspan=2, sticky="ew", pady=1)
        ttk.Label(line, text=pair.key, style="Card.TLabel").pack(
            side="left", padx=(10, 4))
        var = tk.StringVar(value=pair.value.raw)

        def commit(p=pair, v=var) -> None:
            if not self._guard():
                return
            p.value.raw = v.get().strip()
            self.owner.mark_dirty(self.doc)

        e = ttk.Entry(line, textvariable=var, width=14)
        e.pack(side="left")
        var.trace_add("write",
                      lambda *_a, k=pair.key: self._debounce(k, commit))
        e.bind("<FocusOut>", lambda _e: commit())
        ttk.Button(line, text="✕", width=2,
                   command=lambda p=pair: self._remove_modifier(p)).pack(
            side="right", padx=2)
        return row + 1

    def _add_modifier(self) -> None:
        if not self._editable or self.entry is None:
            return
        options = list(self.owner.value_options("modifier"))
        options.append((f"✏ {self.t('dyn_mod.custom_key')}", "__custom__"))

        def picked(value: str) -> None:
            if value == "__custom__":
                TextPromptDialog(self, self.owner,
                                 self.t("dyn_mod.add_modifier"),
                                 self.t("dyn_mod.key_label"), add,
                                 pattern=r"^\w+$")
            else:
                add(value)

        def add(key: str) -> None:
            self.entry.add_modifier(key, "0")
            self.owner.mark_dirty(self.doc)
            self._rebuild()

        SinglePickDialog(self, self.owner, self.t("dyn_mod.add_modifier"),
                         options, picked)

    def _remove_modifier(self, pair: Pair) -> None:
        if not self._editable or self.entry is None:
            return
        self.entry.remove_modifier(pair)
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    # ----------------------------------------------------------------- actions
    def _preview_pdx(self) -> None:
        if self.entry is not None:
            PdxPreviewDialog(self, self.owner, self.entry.name,
                             Block([self.entry.pair]))

    def _delete(self) -> None:
        if self.entry is None or not self._editable:
            return
        if not messagebox.askyesno("ANKA", self.t("dyn_mod.confirm_delete",
                                                  name=self.entry.name)):
            return
        self.owner.delete_entry(self.doc, self.entry)
