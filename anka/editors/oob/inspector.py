"""Inspectors of the OOB editor: the division-template grid and the file view.

`TemplateInspector` is the game-like designer — 5 regiment columns (each a
top-down stack, filled cells must touch) plus a support column of up to 5 unique
companies. Add/remove are gated by the shape invariant (`can_add_regiment` /
`can_remove_regiment` in the service), so the user can never create a hole.
`FileInspector` lists the file's deployed divisions and the import action.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...core.pdx import Block, Pair
from ...services.oob_service import (
    MAX_COLS,
    MAX_ROWS,
    MAX_SUPPORT,
    DivisionTemplate,
    can_add_regiment,
    can_remove_regiment,
    experience_level_key,
)
from ...core.pdx import dumps
from ...core.pdx import parse as pdx_parse
from ..common import (InspectorBase, PdxPreviewDialog, ScriptEditorDialog,
                      SinglePickDialog, TextPromptDialog)


class TemplateInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.template: DivisionTemplate | None = None
        self.doc = None
        self._cols: list[list[str]] = []
        self._support: list[str] = []
        self._build()

    def _build(self) -> None:
        b = self.body
        r = 0
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=460)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        # name + rename
        ttk.Label(b, text=self.t("oob.template_name"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        name_row = ttk.Frame(b, style="Card.TFrame")
        name_row.grid(row=r, column=1, sticky="ew", padx=(8, 0)); r += 1
        name_row.columnconfigure(0, weight=1)
        self._name_entry = ttk.Entry(name_row, state="readonly")
        self._name_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(name_row, text="✎", width=3, command=self._rename).grid(
            row=0, column=1, padx=(4, 0))

        # division_names_group — attach the template to a name group
        ttk.Label(b, text=self.t("oob.names_group"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        self._group_var = tk.StringVar()
        self._group_combo = ttk.Combobox(b, textvariable=self._group_var)
        self._group_combo.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=3); r += 1
        self._group_var.trace_add("write",
                                  lambda *_: self._debounce("group", self._commit_group))
        self._group_combo.bind("<<ComboboxSelected>>", lambda e: self._commit_group())

        # grid host
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 4)); r += 1
        ttk.Label(head, text=self.t("oob.regiments"), style="Card.TLabel").pack(side="left")
        ttk.Label(head, text="   " + self.t("oob.support"),
                  style="CardMuted.TLabel").pack(side="right")
        self._grid_host = ttk.Frame(b, style="Card.TFrame")
        self._grid_host.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        ttk.Label(b, text=self.t("oob.shift_hint"), style="CardMuted.TLabel",
                  wraplength=460).grid(row=r, column=0, columnspan=2, sticky="w",
                                       pady=(4, 0)); r += 1

        self._status = ttk.Label(b, text="", style="CardMuted.TLabel")
        self._status.grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 0)); r += 1

        # actions
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8); r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._dup_btn = ttk.Button(actions, text="⧉ " + self.t("focuses.duplicate"),
                                   command=lambda: self.owner.duplicate_template())
        self._dup_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._del_btn = ttk.Button(actions, text="🗑 " + self.t("oob.delete_template"),
                                   command=lambda: self.owner.delete_template())
        self._del_btn.pack(side="left", fill="x", expand=True)

    # --------------------------------------------------------------------- show
    def show(self, doc, template: DivisionTemplate | None, editable: bool = True) -> None:
        self.flush_pending()
        self.doc = doc
        self.template = template
        self._editable = editable
        self._loading = True
        try:
            if template is None:
                return
            self._title.configure(text=template.name or "—")
            origin = doc.ref.rel_file if doc is not None else ""
            self._subtitle.configure(
                text=origin + ("" if editable else "  ·  🔒 " + self.t("focuses.readonly")))
            self._name_entry.configure(state="normal")
            self._name_entry.delete(0, "end")
            self._name_entry.insert(0, template.name)
            self._name_entry.configure(state="readonly")
            self._group_combo.configure(values=self.owner.division_names_groups(),
                                        state="normal" if editable else "disabled")
            self._group_var.set(template.names_group)
            self._cols = [list(c) for c in template.columns()]
            self._support = list(template.support())
            self._status.configure(text="")
            self._dup_btn.configure(state="normal" if editable else "disabled")
            self._del_btn.configure(state="normal" if editable else "disabled")
        finally:
            self._loading = False
        self._render_grid()

    # ------------------------------------------------------------------- grid
    def _cell(self, parent, text, kind: str, command=None):
        """A fixed-size grid cell. kind: filled | add | empty."""
        colors = {
            "filled": (self.palette.accent, self.palette.accent_text),
            "add": (self.palette.surface_alt, self.palette.text_muted),
            "empty": (self.palette.surface, self.palette.text_muted),
        }
        bg, fg = colors[kind]
        state = "normal" if (command and self._editable) else "disabled"
        btn = tk.Button(parent, text=text, width=9, height=2, bd=0,
                        bg=bg, fg=fg, activebackground=self.palette.surface_alt,
                        disabledforeground=fg, cursor=("hand2" if command else "arrow"),
                        font=("Segoe UI", 8), command=command or (lambda: None),
                        state=state, relief="flat", highlightthickness=1,
                        highlightbackground=self.palette.border)
        return btn

    def _render_grid(self) -> None:
        for w in self._grid_host.winfo_children():
            w.destroy()
        if self.template is None:
            return
        abbr = self._abbr
        # regiment columns
        for c in range(MAX_COLS):
            col_frame = ttk.Frame(self._grid_host, style="Card.TFrame")
            col_frame.grid(row=0, column=c, padx=2, pady=2, sticky="n")
            col = self._cols[c] if c < len(self._cols) else []
            for rr in range(MAX_ROWS):
                if rr < len(col):
                    removable = (rr == len(col) - 1
                                 and can_remove_regiment(self._cols, c))
                    cell = self._cell(col_frame, abbr(col[rr]), "filled",
                                      command=(lambda cc=c: self._remove_regiment(cc))
                                      if removable else None)
                elif rr == len(col) and c <= len(self._cols) and \
                        can_add_regiment(self._cols, c):
                    cell = self._cell(col_frame, "＋", "add",
                                      command=lambda cc=c: self._add_regiment(cc))
                    # Shift+click copies the top neighbour (or the left one if the
                    # column is empty) into the cell — regiments only. "break"
                    # suppresses the normal click (the unit picker).
                    if self._editable:
                        cell.bind("<Shift-Button-1>",
                                  lambda e, cc=c: self._shift_copy(cc) or "break")
                else:
                    cell = self._cell(col_frame, "", "empty")
                cell.pack(pady=1)
        # spacer
        ttk.Frame(self._grid_host, width=16, style="Card.TFrame").grid(row=0, column=MAX_COLS)
        # support column
        sup_frame = ttk.Frame(self._grid_host, style="Card.TFrame")
        sup_frame.grid(row=0, column=MAX_COLS + 1, padx=2, pady=2, sticky="n")
        for i in range(MAX_SUPPORT):
            if i < len(self._support):
                cell = self._cell(sup_frame, abbr(self._support[i]), "filled",
                                  command=lambda ii=i: self._remove_support(ii))
            elif i == len(self._support):
                cell = self._cell(sup_frame, "＋", "add", command=self._add_support)
            else:
                cell = self._cell(sup_frame, "", "empty")
            cell.pack(pady=1)

    def _abbr(self, unit: str) -> str:
        ut = self.owner.unit_service.get(unit)
        return (ut.abbreviation if ut and ut.abbreviation else unit)[:9]

    # ------------------------------------------------------------------ edits
    def _commit(self) -> None:
        if self.template is None:
            return
        self.template.set_columns(self._cols)
        self.template.set_support(self._support)
        self.owner.mark_dirty(self.doc)
        self._render_grid()

    def _commit_group(self) -> None:
        if self._loading or self.template is None or not self._editable:
            return
        self.template.names_group = self._group_var.get().strip()
        self.owner.mark_dirty(self.doc)

    def _add_regiment(self, col: int) -> None:
        if not self._guard():
            return
        options = [(u.label, u.name) for u in self.owner.unit_service.land_regiments()]

        def picked(unit: str) -> None:
            if col == len(self._cols):
                self._cols.append([unit])
            else:
                self._cols[col].append(unit)
            self._commit()

        SinglePickDialog(self, self.owner, self.t("oob.pick_regiment"), options, picked)

    def _remove_regiment(self, col: int) -> None:
        if not self._guard() or col >= len(self._cols):
            return
        self._cols[col].pop()
        if not self._cols[col]:
            self._cols.pop(col)
        self._commit()

    def _shift_copy(self, col: int) -> None:
        """Fill the column's add cell with its top neighbour (the unit above),
        or the left neighbour (row 0 of the previous column) when this column is
        empty. Regiments only — support has no such adjacency."""
        if not self._guard() or not can_add_regiment(self._cols, col):
            return
        col_list = self._cols[col] if col < len(self._cols) else []
        if col_list:                              # top neighbour
            unit = col_list[-1]
        elif col > 0 and self._cols[col - 1]:     # no top -> left neighbour
            unit = self._cols[col - 1][0]
        else:
            self._status.configure(text=self.t("oob.shift_copy_none"))
            return
        if col == len(self._cols):
            self._cols.append([unit])
        else:
            self._cols[col].append(unit)
        self._commit()

    def _add_support(self) -> None:
        if not self._guard() or len(self._support) >= MAX_SUPPORT:
            return
        used = set(self._support)
        options = [(u.label, u.name) for u in self.owner.unit_service.land_support()
                   if u.name not in used]
        if not options:
            self._status.configure(text=self.t("oob.no_support_left"))
            return

        def picked(unit: str) -> None:
            if unit not in self._support:
                self._support.append(unit)
            self._commit()

        SinglePickDialog(self, self.owner, self.t("oob.pick_support"), options, picked)

    def _remove_support(self, idx: int) -> None:
        if not self._guard() or idx >= len(self._support):
            return
        self._support.pop(idx)
        self._commit()

    # ------------------------------------------------------------------ actions
    def _rename(self) -> None:
        if not self._guard() or self.template is None:
            return
        template = self.template
        taken = {t.name for t in self.doc.templates()} - {template.name}
        TextPromptDialog(self, self.owner, self.t("oob.rename_template"),
                         self.t("oob.template_name"),
                         lambda new: self.owner.rename_template(template, new),
                         initial=template.name, taken=taken, pattern=r"^.+$")

    def _preview(self) -> None:
        if self.template is not None:
            PdxPreviewDialog(self, self.owner,
                             self.t("focuses.preview_title", id=self.template.name),
                             Block([self.template.pair]))


class FileInspector(InspectorBase):
    """OOB file view: deployed-division list + import action."""

    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc = None
        self._division = None
        self._build()

    def _build(self) -> None:
        b = self.body
        b.rowconfigure(4, weight=1)     # the divisions tree row expands
        r = 0
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(b, text="", style="CardMuted.TLabel", wraplength=460)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        # instant_effect — file-level effect block (equipment production, ...)
        ie_row = ttk.Frame(b, style="Card.TFrame")
        ie_row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 6)); r += 1
        self._ie_status = ttk.Label(ie_row, text="○", style="CardMuted.TLabel",
                                    width=2)
        self._ie_status.pack(side="left")
        ttk.Label(ie_row, text="instant_effect",
                  style="CardMuted.TLabel").pack(side="left")
        ttk.Button(ie_row, text="✎", width=3,
                   command=self._edit_instant_effect).pack(side="left",
                                                           padx=(6, 0))

        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 4)); r += 1
        ttk.Label(head, text=self.t("oob.divisions"), style="Card.TLabel").pack(side="left")
        self._import_btn = ttk.Button(head, text="⬇ " + self.t("oob.import_from"),
                                      command=self._import)
        self._import_btn.pack(side="right")
        self._add_btn = ttk.Button(head, text="➕ " + self.t("oob.add_division"),
                                   command=self._add_division)
        self._add_btn.pack(side="right", padx=6)

        tree_wrap = ttk.Frame(b, style="Card.TFrame")
        tree_wrap.grid(row=r, column=0, columnspan=2, sticky="nsew"); r += 1
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        self._tree = ttk.Treeview(tree_wrap, columns=("tmpl", "loc"),
                                  show="tree headings", height=10, selectmode="browse")
        self._tree.heading("#0", text=self.t("oob.division"))
        self._tree.heading("tmpl", text=self.t("oob.template"))
        self._tree.heading("loc", text=self.t("oob.location"))
        self._tree.column("#0", width=120)
        self._tree.column("tmpl", width=200)
        self._tree.column("loc", width=90, anchor="e")
        self._tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self._tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<<TreeviewSelect>>", self._on_div_select)

        # per-division edit form
        form = ttk.Frame(b, style="Card.TFrame")
        form.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 0)); r += 1
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text=self.t("oob.template"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=2)
        self._tmpl_combo = ttk.Combobox(form, state="readonly")
        self._tmpl_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)
        self._tmpl_combo.bind("<<ComboboxSelected>>", lambda e: self._commit_div())
        ttk.Label(form, text=self.t("oob.location"), style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=2)
        loc_row = ttk.Frame(form, style="Card.TFrame")
        loc_row.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)
        self._loc_var = tk.StringVar()
        loc_e = ttk.Entry(loc_row, textvariable=self._loc_var, width=14)
        loc_e.pack(side="left")
        # pick a province by id, but still allow typing one in directly
        ttk.Button(loc_row, text="…", width=2,
                   command=self._pick_province).pack(side="left", padx=(4, 0))
        self._loc_var.trace_add("write", lambda *_: self._debounce("loc", self._commit_div))
        loc_e.bind("<FocusOut>", lambda e: self._commit_div())
        ttk.Label(form, text=self.t("oob.custom_name"), style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=2)
        self._dname_var = tk.StringVar()
        self._dname_entry = ttk.Entry(form, textvariable=self._dname_var)
        self._dname_entry.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=2)
        self._dname_var.trace_add("write", lambda *_: self._debounce("dname", self._commit_div))
        self._dname_entry.bind("<FocusOut>", lambda e: self._commit_div())

        # Ordered naming: assign this division a specific number so it takes the name
        # for that number from the matching division-names group (see Division names).
        self._ordered_var = tk.BooleanVar(value=False)
        ord_row = ttk.Frame(form, style="Card.TFrame")
        ord_row.grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(ord_row, text=self.t("oob.ordered_name"),
                        style="Card.TCheckbutton", variable=self._ordered_var,
                        command=self._toggle_ordered).pack(side="left")
        ttk.Label(ord_row, text=self.t("oob.name_order"),
                  style="CardMuted.TLabel").pack(side="left", padx=(10, 4))
        self._order_var = tk.StringVar(value="1")
        self._order_spin = ttk.Spinbox(ord_row, from_=1, to=9999, width=6,
                                       textvariable=self._order_var,
                                       command=self._commit_div)
        self._order_spin.pack(side="left")
        self._order_var.trace_add("write",
                                  lambda *_: self._debounce("order", self._commit_div))

        # Starting factors (0..1). Experience additionally shows the veterancy tier.
        self._exp_var = tk.DoubleVar(value=0.0)
        self._exp_status = self._factor_row(form, 4, "oob.start_experience",
                                            self._exp_var, status=True)
        self._mp_var = tk.DoubleVar(value=1.0)
        self._factor_row(form, 5, "oob.start_manpower", self._mp_var)
        self._eq_var = tk.DoubleVar(value=1.0)
        self._factor_row(form, 6, "oob.start_equipment", self._eq_var)

        self._del_div_btn = ttk.Button(form, text="🗑 " + self.t("oob.remove_division"),
                                       command=self._remove_division)
        self._del_div_btn.grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _factor_row(self, parent, row, label_key, var, status: bool = False):
        ttk.Label(parent, text=self.t(label_key), style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=2)
        wrap = ttk.Frame(parent, style="Card.TFrame")
        wrap.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        wrap.columnconfigure(0, weight=1)
        scale = ttk.Scale(wrap, from_=0.0, to=1.0, variable=var, orient="horizontal")
        scale.grid(row=0, column=0, sticky="ew")
        val_lbl = ttk.Label(wrap, text="", style="CardMuted.TLabel", width=4)
        val_lbl.grid(row=0, column=1, padx=(6, 0))
        status_lbl = None
        if status:
            status_lbl = ttk.Label(wrap, text="", style="Card.TLabel", width=9)
            status_lbl.grid(row=0, column=2, padx=(6, 0))
        var.trace_add("write", lambda *_a: self._on_factor(var, val_lbl, status_lbl))
        return status_lbl

    def _on_factor(self, var, val_lbl, status_lbl) -> None:
        try:
            value = float(var.get())
        except (tk.TclError, ValueError):
            return
        val_lbl.configure(text=f"{value:.2f}")
        if status_lbl is not None:
            status_lbl.configure(text=self.t(f"oob.exp.{experience_level_key(value)}"))
        self._debounce("factors", self._commit_div)

    def show(self, doc, editable: bool) -> None:
        self.flush_pending()
        self.doc = doc
        self._editable = editable
        self._division = None
        self._loading = True
        try:
            self._title.configure(text=doc.ref.name)
            self._subtitle.configure(
                text=doc.ref.rel_file
                     + ("" if editable else "  ·  🔒 " + self.t("focuses.readonly")))
            names = [t.name for t in doc.templates()]
            self._tmpl_combo.configure(values=names)
            self._refresh_divisions()
            self._refresh_instant_effect()
            for w in (self._add_btn, self._import_btn):
                w.configure(state="normal" if editable else "disabled")
        finally:
            self._loading = False

    def _refresh_instant_effect(self) -> None:
        block = self.doc.root.get_block("instant_effect") if self.doc else None
        filled = block is not None and len(block.items) > 0
        self._ie_status.configure(
            text="●" if filled else "○",
            foreground=self.palette.accent if filled else self.palette.text_muted)

    def _edit_instant_effect(self) -> None:
        doc = self.doc
        if doc is None:
            return
        block = doc.root.get_block("instant_effect")
        text = dumps(block, top_level=False) if block is not None else ""

        def submitted(new_text: str) -> None:
            try:
                parsed = (pdx_parse(new_text, recover=False)
                          if new_text.strip() else None)
            except Exception:
                return
            if parsed is None or not parsed.items:
                doc.root.remove("instant_effect")
            else:
                existing = doc.root.get_block("instant_effect")
                if existing is not None:
                    existing.items = parsed.items
                else:
                    # convention: instant_effect precedes the units block
                    pair = Pair("instant_effect", Block(parsed.items))
                    index = next((i for i, it in enumerate(doc.root.items)
                                  if isinstance(it, Pair) and it.key == "units"),
                                 None)
                    if index is None:
                        doc.root.items.append(pair)
                    else:
                        doc.root.items.insert(index, pair)
            self.owner.mark_dirty(doc)
            self._refresh_instant_effect()

        ScriptEditorDialog(self, self.owner, "instant_effect", text,
                           submitted if self._editable else (lambda t: None),
                           ("effect",), doc.ref.name)

    def _refresh_divisions(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._div_index = {}
        for i, d in enumerate(self.doc.divisions()):
            iid = str(i)
            self._tree.insert("", "end", iid=iid,
                              text=d.display_name(), values=(d.template, d.location))
            self._div_index[iid] = d

    def _on_div_select(self, _e=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        self._division = self._div_index.get(sel[0])
        if self._division is None:
            return
        self._loading = True
        try:
            self._tmpl_combo.set(self._division.template)
            self._loc_var.set(self._division.location)
            self._dname_var.set(self._division.custom_name)
            ordered = self._division.is_name_ordered
            self._ordered_var.set(ordered)
            self._order_var.set(str(self._division.name_order or 1))
            self._update_ordered_state()
            self._exp_var.set(self._division.start_experience_factor)
            self._mp_var.set(self._division.start_manpower_factor)
            self._eq_var.set(self._division.start_equipment_factor)
        finally:
            self._loading = False

    def _toggle_ordered(self) -> None:
        """Ordered naming and a literal custom name are mutually exclusive."""
        self._update_ordered_state()
        self._commit_div()

    def _update_ordered_state(self) -> None:
        ordered = self._ordered_var.get()
        self._order_spin.configure(state="normal" if ordered and self._editable
                                   else "disabled")
        self._dname_entry.configure(state="disabled" if ordered else "normal")

    def _pick_province(self) -> None:
        if not self._guard() or self._division is None:
            return
        SinglePickDialog(self, self.owner, self.t("oob.pick_province"),
                         self.owner.value_options("province"),
                         lambda pid: self._loc_var.set(pid),
                         current=self._loc_var.get().strip())

    def _commit_div(self) -> None:
        if not self._guard() or self._division is None:
            return
        self._division.template = self._tmpl_combo.get()
        self._division.location = self._loc_var.get().strip()
        if self._ordered_var.get():
            try:
                order = int(self._order_var.get() or 1)
            except ValueError:
                order = 1
            self._division.set_ordered_name(order)
        else:
            self._division.set_ordered_name(None)
            self._division.custom_name = self._dname_var.get().strip()
        try:
            self._division.start_experience_factor = float(self._exp_var.get())
            self._division.start_manpower_factor = float(self._mp_var.get())
            self._division.start_equipment_factor = float(self._eq_var.get())
        except (tk.TclError, ValueError):
            pass
        self.owner.mark_dirty(self.doc)
        # refresh just the row text/values
        for iid, d in self._div_index.items():
            if d is self._division:
                self._tree.item(iid, text=d.display_name(),
                                values=(d.template, d.location))
                break

    def _add_division(self) -> None:
        if not self._guard():
            return
        names = [t.name for t in self.doc.templates()]
        if not names:
            self.owner._validate()
            return
        options = [(n, n) for n in names]

        def picked(name: str) -> None:
            self.owner.service.add_division(self.doc, name)
            self.owner.mark_dirty(self.doc)
            self._refresh_divisions()

        SinglePickDialog(self, self.owner, self.t("oob.add_division"), options, picked)

    def _remove_division(self) -> None:
        if not self._guard() or self._division is None:
            return
        self.owner.service.remove_division(self.doc, self._division)
        self.owner.mark_dirty(self.doc)
        self._division = None
        self._refresh_divisions()

    def _import(self) -> None:
        if not self._guard():
            return
        self.owner.import_from(self.doc)
