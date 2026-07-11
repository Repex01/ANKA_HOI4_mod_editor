"""Schema-driven inspector for one ``.gui`` element.

Rebuilt on every ``show()`` from the widget's `AttrSpec` table, grouped by
section. Every commit goes through the owning tab (`commit_attr` /
`commit_background`), which records an undo command and re-renders the
canvas. The ``position`` row edits ``show_position`` transparently when the
window uses show/hide animation (see `GuiNode.position_key`). Unknown keys
are editable raw (scalar entry or script dialog), so nothing is off-limits.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.guitypes.schema import AttrKind, AttrSpec
from ...core.pdx import Block
from ..common import (IconPickerDialog, InspectorBase, PdxPreviewDialog,
                      ScriptEditorDialog, TextPromptDialog)

_SECTION_ORDER = ("common", "layout", "graphics", "text", "grid", "scroll",
                  "behavior", "sound", "animation")


class WidgetInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc = None
        self.window_index = 0
        self.node = None
        self.group: list | None = None      # multi-selection mode
        self._geo_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}

    # -------------------------------------------------------------------- show
    def show(self, doc, window_index: int, node, editable: bool) -> None:
        self.flush_pending()
        self._loading = True
        self.doc, self.window_index, self.node = doc, window_index, node
        self.group = None
        self._editable = editable
        self._geo_vars.clear()
        for child in self.body.winfo_children():
            child.destroy()
        if node is not None:
            self._build_form()
            self._set_state_all(editable)
        self._loading = False

    def show_group(self, doc, window_index: int, nodes: list,
                   editable: bool) -> None:
        """Multi-selection: one form whose edits apply to every node (the
        attribute set is the intersection of the selected widget types)."""
        self.flush_pending()
        self._loading = True
        self.doc, self.window_index = doc, window_index
        self.node = None
        self.group = list(nodes)
        self._editable = editable
        self._geo_vars.clear()
        for child in self.body.winfo_children():
            child.destroy()
        if self.group:
            self._build_group_form()
            self._set_state_all(editable)
        self._loading = False

    def update_geometry(self, rect=None) -> None:
        """Refresh position/size rows after a canvas drag (no full rebuild)."""
        if self.node is None:
            return
        self._loading = True
        try:
            for attr_name, (x_var, y_var) in self._geo_vars.items():
                key = (self.node.position_key()
                       if attr_name == "position" else attr_name)
                x, y = self.node.get_xy(key)
                x_var.set(x)
                y_var.set(y)
        finally:
            self._loading = False

    # ------------------------------------------------------------------- form
    def _build_form(self) -> None:
        node, b = self.node, self.body
        r = 0
        title = ttk.Label(b, text=(node.name or node.type_key)
                          + ("  🔒" if not self._editable else ""),
                          style="Heading.TLabel")
        title.grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        ttk.Label(b, text=f"{node.type_key} · {self.doc.ref.rel_file}",
                  style="CardMuted.TLabel", wraplength=430).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 6))
        r += 1

        if node.spec is not None and any(a.kind == AttrKind.LOC_TEXT
                                         for a in node.spec.attrs):
            r = self._language_row(r)

        if node.spec is not None:
            by_section: dict[str, list[AttrSpec]] = {}
            for attr in node.spec.attrs:
                by_section.setdefault(attr.section, []).append(attr)
            for section in _SECTION_ORDER:
                attrs = by_section.pop(section, [])
                if not attrs:
                    continue
                ttk.Label(b, text=self.t(f"interface.section.{section}"),
                          style="CardMuted.TLabel",
                          font=("Segoe UI Semibold", 9)).grid(
                    row=r, column=0, columnspan=2, sticky="w", pady=(8, 2))
                r += 1
                for attr in attrs:
                    r = self._attr_row(r, attr)
            for attrs in by_section.values():           # uncategorized specs
                for attr in attrs:
                    r = self._attr_row(r, attr)

        r = self._unknown_rows(r)

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=8)
        r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview_pdx).pack(side="left")
        self._delete_btn = ttk.Button(
            actions, text="🗑 " + self.t("interface.gfx.delete"),
            command=self._delete)
        self._delete_btn.pack(side="right")

    # ------------------------------------------------------------- group form
    def _build_group_form(self) -> None:
        nodes, b = self.group, self.body
        r = 0
        ttk.Label(b, text=self.t("interface.gui.group_title",
                                 count=len(nodes)),
                  style="Heading.TLabel").grid(row=r, column=0, columnspan=2,
                                               sticky="w")
        r += 1
        names = ", ".join((n.name or n.type_key) for n in nodes[:10])
        if len(nodes) > 10:
            names += " …"
        ttk.Label(b, text=names, style="CardMuted.TLabel", wraplength=430,
                  justify="left").grid(row=r, column=0, columnspan=2,
                                       sticky="w", pady=(0, 8))
        r += 1
        for attr in self._group_attrs(nodes):
            r = self._group_row(r, attr, nodes)
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=8)
        r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="🗑 " + self.t("interface.gfx.delete"),
                   command=self._delete_group).pack(side="right")

    @staticmethod
    def _group_attrs(nodes: list) -> list[AttrSpec]:
        """Attrs offered for group editing: present in EVERY selected type,
        excluding identity/nested-block fields."""
        specs = [n.spec for n in nodes]
        if any(s is None for s in specs):
            return []
        common = set.intersection(
            *({a.name.lower() for a in s.attrs} for s in specs))
        return [a for a in specs[0].attrs
                if a.name.lower() in common
                and a.name.lower() != "name"
                and a.kind not in (AttrKind.BACKGROUND, AttrKind.SCRIPT)]

    def _group_row(self, row: int, attr: AttrSpec, nodes: list) -> int:
        b = self.body
        name = attr.name
        kind = attr.kind

        if kind in (AttrKind.POSITION, AttrKind.SIZE):
            def read_xy(node):
                key = node.position_key() if name == "position" else name
                return node.get_xy(key)

            values = [read_xy(n) for n in nodes]
            x0, y0 = values[0] if all(v == values[0] for v in values) \
                else ("", "")
            self._label(row, name)
            cell = ttk.Frame(b, style="Card.TFrame")
            cell.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            x_var, y_var = tk.StringVar(value=x0), tk.StringVar(value=y0)

            def commit() -> None:
                if not self._guard():
                    return
                if not x_var.get().strip() and not y_var.get().strip():
                    return
                self.owner.commit_attr_group(name, "xy",
                                             (x_var.get(), y_var.get()))

            for var, tag in ((x_var, "x"), (y_var, "y")):
                ttk.Label(cell, text=tag, style="CardMuted.TLabel").pack(
                    side="left", padx=(0 if tag == "x" else 8, 2))
                e = ttk.Entry(cell, textvariable=var, width=7)
                e.pack(side="left")
                var.trace_add("write",
                              lambda *_a, c=commit: self._debounce(name, c))
                e.bind("<FocusOut>", lambda _e, c=commit: c())
            return row + 1

        values = [n.get_attr(name) for n in nodes]
        current = values[0] if all(v == values[0] for v in values) else ""

        def commit_group(v: str) -> None:
            self.owner.commit_attr_group(name, "scalar", v)

        if kind == AttrKind.SPRITE_REF:
            return self._sprite_row(row, name, current, commit_group)

        var = tk.StringVar(value=current)

        def commit() -> None:
            if self._guard():
                commit_group(var.get())

        self._label(row, name)
        if kind == AttrKind.BOOL:
            box = ttk.Combobox(b, textvariable=var, width=8, state="readonly",
                               values=("", "yes", "no"))
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit())
        elif kind == AttrKind.ENUM:
            box = ttk.Combobox(b, textvariable=var, width=18, state="readonly",
                               values=("",) + attr.enum_values)
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit())
        elif kind == AttrKind.FONT_REF:
            box = ttk.Combobox(b, textvariable=var, width=20,
                               values=tuple(self.owner.service.font_names()))
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit())
            var.trace_add("write", lambda *_a: self._debounce(name, commit))
        else:
            width = 8 if kind in (AttrKind.INT, AttrKind.FLOAT) else 26
            e = ttk.Entry(b, textvariable=var, width=width)
            e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            var.trace_add("write", lambda *_a: self._debounce(name, commit))
            e.bind("<FocusOut>", lambda _e: commit())
        return row + 1

    def _delete_group(self) -> None:
        if not self._editable or not self.group:
            return
        if not messagebox.askyesno(
                "ANKA", self.t("interface.gui.confirm_delete_group",
                               count=len(self.group))):
            return
        self.owner.delete_nodes([n.index_path() for n in self.group])

    # --------------------------------------------------------------- language
    def _language_row(self, row: int) -> int:
        b = self.body
        ttk.Label(b, text=self.t("focuses.inspector.language"),
                  style="CardMuted.TLabel").grid(row=row, column=0,
                                                 sticky="w", pady=3)
        box = ttk.Combobox(b, state="readonly", values=list(HOI4_LANGUAGES),
                           width=14)
        box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        box.set(self.owner.loc_language)

        def changed(_event=None) -> None:
            self.flush_pending()
            self.owner.set_loc_language(box.get())
            self.show(self.doc, self.window_index, self.node, self._editable)

        box.bind("<<ComboboxSelected>>", changed)
        return row + 1

    # -------------------------------------------------------------- attr rows
    def _label(self, row: int, text: str) -> None:
        ttk.Label(self.body, text=text, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)

    def _attr_row(self, row: int, attr: AttrSpec) -> int:
        node, b = self.node, self.body
        kind = attr.kind
        name = attr.name

        if kind in (AttrKind.POSITION, AttrKind.SIZE):
            read_key = node.position_key() if name == "position" else name
            self._label(row, name)
            cell = ttk.Frame(b, style="Card.TFrame")
            cell.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            x_var, y_var = tk.StringVar(), tk.StringVar()
            x0, y0 = node.get_xy(read_key)
            x_var.set(x0)
            y_var.set(y0)
            self._geo_vars[name] = (x_var, y_var)

            def commit(a=name, xv=x_var, yv=y_var) -> None:
                if not self._guard():
                    return
                self.owner.commit_attr(self.node, a, "xy",
                                       (xv.get(), yv.get()))

            for var, tag in ((x_var, "x"), (y_var, "y")):
                ttk.Label(cell, text=tag, style="CardMuted.TLabel").pack(
                    side="left", padx=(0 if tag == "x" else 8, 2))
                e = ttk.Entry(cell, textvariable=var, width=7)
                e.pack(side="left")
                var.trace_add("write",
                              lambda *_a, k=name, c=commit: self._debounce(k, c))
                e.bind("<FocusOut>", lambda _e, c=commit: c())
            return row + 1

        if kind == AttrKind.BACKGROUND:
            current = ""
            bg = node.get_attr_block(name)
            if bg is not None:
                current = (bg.get_scalar_ci("quadTextureSprite")
                           or bg.get_scalar_ci("spriteType") or "").strip('"')
            return self._sprite_row(row, name, current,
                                    lambda sprite: self.owner.commit_background(
                                        self.node, sprite))

        if kind == AttrKind.LOC_TEXT:
            return self._loc_text_row(row, attr)

        current = node.get_attr(name)
        if kind == AttrKind.SPRITE_REF:
            return self._sprite_row(
                row, name, current,
                lambda sprite, a=name: self.owner.commit_attr(
                    self.node, a, "scalar", sprite))

        var = tk.StringVar(value=current)

        def commit_scalar(a=name, v=var) -> None:
            if not self._guard():
                return
            self.owner.commit_attr(self.node, a, "scalar", v.get())

        self._label(row, name)
        if kind == AttrKind.BOOL:
            box = ttk.Combobox(b, textvariable=var, width=8, state="readonly",
                               values=("", "yes", "no"))
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit_scalar())
        elif kind == AttrKind.ENUM:
            box = ttk.Combobox(b, textvariable=var, width=18, state="readonly",
                               values=("",) + attr.enum_values)
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit_scalar())
        elif kind == AttrKind.FONT_REF:
            box = ttk.Combobox(b, textvariable=var, width=20,
                               values=tuple(self.owner.service.font_names()))
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit_scalar())
            var.trace_add("write",
                          lambda *_a: self._debounce(name, commit_scalar))
        else:
            width = 8 if kind in (AttrKind.INT, AttrKind.FLOAT) else 26
            e = ttk.Entry(b, textvariable=var, width=width)
            e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            var.trace_add("write",
                          lambda *_a: self._debounce(name, commit_scalar))
            e.bind("<FocusOut>", lambda _e: commit_scalar())
        return row + 1

    def _loc_text_row(self, row: int, attr: AttrSpec) -> int:
        """Text-valued attribute (text/buttonText/pdx_tooltip): the raw value
        may be a literal / ``[variable]`` string or a localisation key. The
        "is localization key" checkbox auto-detects (key found in loc) and,
        when on, exposes the translation for the current language. New
        elements start unchecked — the user decides whether to localize."""
        node, b = self.node, self.body
        name = attr.name
        current = node.get_attr(name)
        var = tk.StringVar(value=current)
        loc_var = tk.StringVar()
        is_key = tk.BooleanVar(value=self.owner.loc_has(current))
        syncing = [False]      # guards programmatic loc_var writes, per row

        self._label(row, name)
        e = ttk.Entry(b, textvariable=var, width=26)
        e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        row += 1

        chk = ttk.Checkbutton(b, text=self.t("interface.loc.is_key"),
                              style="Card.TCheckbutton", variable=is_key)
        chk.grid(row=row, column=1, sticky="w", padx=(8, 0))
        row += 1

        loc_cell = ttk.Frame(b, style="Card.TFrame")
        loc_cell.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(0, 3))
        ttk.Label(loc_cell, text=self.owner.loc_language,
                  style="CardMuted.TLabel").pack(side="left", padx=(0, 4))
        loc_entry = ttk.Entry(loc_cell, textvariable=loc_var, width=24)
        loc_entry.pack(side="left")
        row += 1

        def refresh_loc() -> None:
            """Pull the translation for the current key (programmatic set —
            must not trigger a write-back)."""
            syncing[0] = True
            try:
                key = var.get().strip()
                loc_var.set(self.owner.loc_get(key, self.owner.loc_language)
                            if key else "")
            finally:
                syncing[0] = False

        def commit_value(a=name) -> None:
            if not self._guard():
                return
            self.owner.commit_attr(self.node, a, "scalar", var.get())
            if is_key.get():
                refresh_loc()

        def commit_loc() -> None:
            if not self._guard() or syncing[0] or not is_key.get():
                return
            key = var.get().strip()
            if not key:
                return
            text = loc_var.get()
            if text == (self.owner.loc_get(key, self.owner.loc_language) or ""):
                return
            self.owner.loc_set(key, self.owner.loc_language, text)
            self.owner.schedule_render()

        def toggle() -> None:
            if is_key.get():
                refresh_loc()
                loc_cell.grid()
            else:
                loc_cell.grid_remove()

        chk.configure(command=toggle)
        var.trace_add("write",
                      lambda *_a: self._debounce(name, commit_value))
        e.bind("<FocusOut>", lambda _e: commit_value())
        loc_var.trace_add(
            "write",
            lambda *_a: (None if syncing[0]
                         else self._debounce(f"{name}::loc", commit_loc, 1000)))
        loc_entry.bind("<FocusOut>", lambda _e: commit_loc())

        if is_key.get():
            refresh_loc()
        else:
            loc_cell.grid_remove()
        return row

    def _sprite_row(self, row: int, name: str, current: str,
                    on_commit) -> int:
        b = self.body
        self._label(row, name)
        cell = ttk.Frame(b, style="Card.TFrame")
        cell.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        cell.columnconfigure(0, weight=1)
        var = tk.StringVar(value=current)

        def commit() -> None:
            if not self._guard():
                return
            on_commit(var.get().strip())

        e = ttk.Entry(cell, textvariable=var, width=24)
        e.grid(row=0, column=0, sticky="ew")
        var.trace_add("write", lambda *_a: self._debounce(name, commit))
        e.bind("<FocusOut>", lambda _e: commit())

        def pick() -> None:
            if not self.owner.resolver_ready():
                return

            def picked(sprite: str) -> None:
                var.set(sprite)
                commit()

            def imported(path, _keep_size: bool = False) -> None:
                """Image dropped into the picker: prompt a GFX name, convert
                and register it in the mod (anka_interface.gfx), use it."""
                if not self._editable:
                    return

                def create(sprite_name: str) -> None:
                    sprite = self.owner.import_sprite(path, sprite_name)
                    if sprite:
                        picked(sprite)
                        dialog.destroy()

                TextPromptDialog(dialog, self.owner,
                                 self.t("interface.gfx.new_sprite"),
                                 self.t("interface.gfx.name_label"), create,
                                 initial=self.owner.suggest_sprite_name(path),
                                 pattern=r"^\S+$")

            dialog = IconPickerDialog(self, self.owner, self.owner.resolver,
                                      var.get(), picked,
                                      imported if self._editable else None,
                                      prefixes=("GFX_",))

        ttk.Button(cell, text="🖼", width=3, command=pick).grid(
            row=0, column=1, padx=(4, 0))
        return row + 1

    def _unknown_rows(self, row: int) -> int:
        node, b = self.node, self.body
        unknown = node.unknown_keys()
        if not unknown:
            return row
        ttk.Label(b, text=self.t("interface.other_attrs"),
                  style="CardMuted.TLabel",
                  font=("Segoe UI Semibold", 9)).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(8, 2))
        row += 1
        for key in unknown:
            value = node.block.get_ci(key)
            self._label(row, key)
            if isinstance(value, Block):
                ttk.Button(b, text="✎", width=3,
                           command=lambda k=key: self._edit_block(k)).grid(
                    row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            else:
                var = tk.StringVar(value=node.get_attr(key))

                def commit_fn(k=key, _var=var) -> None:
                    if self._guard():
                        self.owner.commit_attr(self.node, k, "scalar",
                                               _var.get())
                e = ttk.Entry(b, textvariable=var, width=26)
                e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
                var.trace_add("write",
                              lambda *_a, k=key, c=commit_fn: self._debounce(k, c))
                e.bind("<FocusOut>", lambda _e, c=commit_fn: c())
            row += 1
        return row

    def _edit_block(self, key: str) -> None:
        node = self.node
        text = node.get_script(key)

        def submitted(new_text: str) -> None:
            self.owner.commit_attr(node, key, "script", new_text)

        ScriptEditorDialog(self, self.owner, key, text,
                           submitted if self._editable else (lambda t: None),
                           ("effect", "trigger"), node.name)

    # ----------------------------------------------------------------- actions
    def _preview_pdx(self) -> None:
        if self.node is not None:
            PdxPreviewDialog(self, self.owner, self.node.name or "element",
                             Block([self.node.pair]))

    def _delete(self) -> None:
        if self.node is None or not self._editable:
            return
        if not messagebox.askyesno(
                "ANKA", self.t("interface.gui.confirm_delete",
                               name=self.node.name or self.node.type_key)):
            return
        self.owner.delete_nodes([self.node.index_path()])
