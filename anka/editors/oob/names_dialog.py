"""Division-names manager: edit ``common/units/names_divisions`` name groups and,
crucially, their ``ordered`` table (division number → name) that backs the OOB
editor's "ordered name" toggle.

Launched from the OOB toolbar. Base-game / dependency files are read-only until copied
into the mod (whole-file override), matching every other editor.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...services.division_names_service import DivisionNamesService, NameGroup
from ..common.dialogs import BaseDialog, TextPromptDialog


class DivisionNamesDialog(BaseDialog):
    def __init__(self, master, editor):
        super().__init__(master, editor, editor.t("oob.names.title"), (860, 600))
        self.resizable(True, True)
        self.service = DivisionNamesService(editor.context)
        self._dirty: set = set()
        self._group: NameGroup | None = None
        self._doc = None
        self._ref = None
        self._items: dict[str, tuple] = {}
        self._loading = False

        outer = ttk.Frame(self, style="Card.TFrame", padding=10)
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(1, weight=1)

        bar = ttk.Frame(outer, style="Card.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Button(bar, text="🗂 " + self.t("oob.names.new_file"),
                   command=self._new_file).pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("oob.names.new_group"),
                   command=self._new_group).pack(side="left", padx=4)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self._save).pack(side="left", padx=4)
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text=self.t("oob.show_vanilla"), style="Card.TCheckbutton",
                        variable=self._vanilla, command=self._reload).pack(side="left",
                                                                           padx=8)

        # left: file → group tree
        left = ttk.Frame(outer, style="Card.TFrame")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        self._tree = ttk.Treeview(left, show="tree", selectmode="browse", height=20)
        self._tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self._tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.tag_configure("file", font=("Segoe UI Semibold", 10))
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        self._form = ttk.Frame(outer, style="Card.TFrame")
        self._form.grid(row=1, column=1, sticky="nsew")
        self._form.columnconfigure(1, weight=1)
        self._build_form()
        self._reload()

    # ------------------------------------------------------------------- form
    def _build_form(self) -> None:
        f = self._form
        r = 0
        self._title = ttk.Label(f, text=self.t("oob.names.select_hint"),
                                style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(f, text="", style="CardMuted.TLabel", wraplength=460)
        self._subtitle.grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 8)); r += 1
        self._copy_btn = ttk.Button(f, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    command=self._copy_to_mod)

        self._name_var = self._field(f, r, "oob.names.group_name"); r += 1
        self._fc_var = self._field(f, r, "oob.names.for_countries"); r += 1
        self._dt_var = self._field(f, r, "oob.names.division_types"); r += 1
        self._fb_var = self._field(f, r, "oob.names.fallback"); r += 1

        ttk.Label(f, text=self.t("oob.names.ordered"), style="Card.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(8, 2)); r += 1
        ord_wrap = ttk.Frame(f, style="Card.TFrame")
        ord_wrap.grid(row=r, column=0, columnspan=3, sticky="nsew"); r += 1
        f.rowconfigure(r - 1, weight=1)
        ord_wrap.rowconfigure(0, weight=1)
        ord_wrap.columnconfigure(0, weight=1)
        self._ord_tree = ttk.Treeview(ord_wrap, columns=("num", "name"),
                                      show="headings", height=8, selectmode="browse")
        self._ord_tree.heading("num", text="#")
        self._ord_tree.heading("name", text=self.t("oob.names.name"))
        self._ord_tree.column("num", width=50, anchor="center", stretch=False)
        self._ord_tree.column("name", width=340)
        self._ord_tree.grid(row=0, column=0, sticky="nsew")
        osb = ttk.Scrollbar(ord_wrap, orient="vertical", command=self._ord_tree.yview)
        osb.grid(row=0, column=1, sticky="ns")
        self._ord_tree.configure(yscrollcommand=osb.set)
        self._ord_tree.bind("<Double-1>", lambda e: self._edit_ordered())

        addrow = ttk.Frame(f, style="Card.TFrame")
        addrow.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(4, 0)); r += 1
        ttk.Label(addrow, text="#", style="CardMuted.TLabel").pack(side="left")
        self._num_var = tk.StringVar(value="1")
        ttk.Spinbox(addrow, from_=1, to=9999, width=6,
                    textvariable=self._num_var).pack(side="left", padx=(2, 6))
        self._new_name_var = tk.StringVar()
        ttk.Entry(addrow, textvariable=self._new_name_var).pack(
            side="left", fill="x", expand=True)
        ttk.Button(addrow, text=self.t("oob.names.set"),
                   command=self._set_ordered).pack(side="left", padx=(6, 0))
        ttk.Button(addrow, text="✕", width=3,
                   command=self._remove_ordered).pack(side="left", padx=(4, 0))

    def _field(self, parent, row, key) -> tk.StringVar:
        ttk.Label(parent, text=self.t(key), style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=2)
        var.trace_add("write", lambda *_: self._commit())
        return var

    # ------------------------------------------------------------------- tree
    def _reload(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._items.clear()
        refs = self.service.list_docs(include_vanilla=self._vanilla.get())
        for ref in refs:
            fid = f"f::{ref.rel_file}"
            label = ref.name + ("  ✎" if ref.edited else "")
            self._tree.insert("", "end", iid=fid, text=label, open=not ref.is_vanilla,
                              tags=("file",) + (("vanilla",) if ref.is_vanilla else ()))
            self._items[fid] = ("file", ref)
            for key in ref.groups:
                gid = f"g::{ref.rel_file}::{key}"
                self._tree.insert(fid, "end", iid=gid, text=key,
                                  tags=("vanilla",) if ref.is_vanilla else ())
                self._items[gid] = ("group", ref, key)

    def _on_select(self, _e=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        if payload[0] == "file":
            # Track the selected file so "New group" targets it (not a fresh file).
            _kind, ref = payload
            self._ref = ref
            self._doc = self.service.load(ref) if not ref.is_vanilla else None
            self._group = None
            self._title.configure(text=self.t("oob.names.select_hint"))
            self._subtitle.configure(text=ref.rel_file)
            self._copy_btn.grid_remove()
            return
        _kind, ref, key = payload
        self._ref = ref
        self._doc = self.service.load(ref)
        self._group = self._doc.find_group(key)
        self._load_group()

    def _load_group(self) -> None:
        g = self._group
        if g is None:
            return
        editable = not self._ref.is_vanilla
        self._editable = editable
        self._loading = True
        try:
            self._title.configure(text=g.key)
            self._subtitle.configure(
                text=self._ref.rel_file
                     + ("" if editable else "  ·  🔒 " + self.t("focuses.readonly")))
            self._name_var.set(g.name)
            self._fc_var.set(" ".join(g.for_countries))
            self._dt_var.set(" ".join(g.division_types))
            self._fb_var.set(g.fallback_name)
            self._refresh_ordered()
            self._set_form_state(editable)
            if editable:
                self._copy_btn.grid_remove()
            else:
                self._copy_btn.grid(row=1, column=2, sticky="e")
        finally:
            self._loading = False

    def _set_form_state(self, editable: bool) -> None:
        state = "normal" if editable else "disabled"
        for child in self._form.winfo_children():
            for w in (child, *child.winfo_children()):
                if isinstance(w, (ttk.Entry, ttk.Spinbox, ttk.Button)):
                    # keep the copy button usable on read-only groups
                    if w is self._copy_btn:
                        continue
                    try:
                        w.configure(state=state)
                    except tk.TclError:
                        pass

    def _refresh_ordered(self) -> None:
        self._ord_tree.delete(*self._ord_tree.get_children())
        if self._group is None:
            return
        for num in sorted(self._group.ordered):
            self._ord_tree.insert("", "end", iid=str(num),
                                  values=(num, self._group.ordered[num]))

    # ------------------------------------------------------------------ edits
    def _commit(self) -> None:
        if self._loading or self._group is None or not getattr(self, "_editable", False):
            return
        g = self._group
        g.name = self._name_var.get().strip()
        g.set_for_countries(self._fc_var.get().split())
        g.set_division_types(self._dt_var.get().split())
        g.fallback_name = self._fb_var.get().strip()
        self._mark_dirty()

    def _set_ordered(self) -> None:
        if self._group is None or not getattr(self, "_editable", False):
            return
        try:
            num = int(self._num_var.get())
        except ValueError:
            return
        name = self._new_name_var.get().strip()
        if not name:
            return
        mapping = self._group.ordered
        mapping[num] = name
        self._group.set_ordered(mapping)
        self._new_name_var.set("")
        self._mark_dirty()
        self._refresh_ordered()
        if self._ord_tree.exists(str(num)):
            self._ord_tree.selection_set(str(num))

    def _remove_ordered(self) -> None:
        if self._group is None or not getattr(self, "_editable", False):
            return
        sel = self._ord_tree.selection()
        if not sel:
            return
        mapping = self._group.ordered
        mapping.pop(int(sel[0]), None)
        self._group.set_ordered(mapping)
        self._mark_dirty()
        self._refresh_ordered()

    def _edit_ordered(self) -> None:
        sel = self._ord_tree.selection()
        if not sel or self._group is None or not getattr(self, "_editable", False):
            return
        num = int(sel[0])
        self._num_var.set(str(num))
        self._new_name_var.set(self._group.ordered.get(num, ""))

    def _mark_dirty(self) -> None:
        if self._doc is not None:
            self._dirty.add(self._doc.ref.path)

    # ------------------------------------------------------------------ actions
    def _copy_to_mod(self) -> None:
        if self._ref is None or not self._ref.is_vanilla:
            return
        key = self._group.key if self._group else None
        new_ref = self.service.copy_to_mod(self._ref)
        self._reload()
        gid = f"g::{new_ref.rel_file}::{key}"
        if key and self._tree.exists(gid):
            self._tree.selection_set(gid)
            self._tree.see(gid)

    def _new_file(self) -> None:
        def submit(name: str) -> None:
            doc = self.service.new_document(name)
            self.service.save(doc)
            self._reload()
            fid = f"f::{doc.ref.rel_file}"
            if self._tree.exists(fid):
                self._tree.selection_set(fid)

        TextPromptDialog(self, self.editor, self.t("oob.names.new_file"),
                         self.t("oob.file_name"), submit, pattern=r"^[\w\-]+$")

    def _new_group(self) -> None:
        # target the currently selected mod file, else a fresh ANKA file
        ref = self._ref if (self._ref and not self._ref.is_vanilla) else None
        doc = self.service.load(ref) if ref else self.service.new_document()

        def submit(key: str) -> None:
            if doc.find_group(key) is not None:
                messagebox.showerror("ANKA", self.t("oob.names.group_exists"))
                return
            self.service.add_group(doc, key)
            self.service.save(doc)
            self._dirty.discard(doc.ref.path)
            self._reload()
            gid = f"g::{doc.ref.rel_file}::{key}"
            fid = f"f::{doc.ref.rel_file}"
            if self._tree.exists(fid):
                self._tree.item(fid, open=True)
            if self._tree.exists(gid):
                self._tree.selection_set(gid)
                self._tree.see(gid)

        TextPromptDialog(self, self.editor, self.t("oob.names.new_group"),
                         self.t("oob.names.group_key"), submit, pattern=r"^[A-Za-z_]\w*$")

    def _save(self) -> None:
        for ref in self.service.list_docs(include_vanilla=False):
            if ref.path in self._dirty:
                try:
                    self.service.save(self.service.load(ref))
                    self._dirty.discard(ref.path)
                except Exception as exc:                       # noqa: BLE001
                    messagebox.showerror("ANKA", self.t("focuses.err.save",
                                                        error=str(exc)))
        self._reload()

    def destroy(self) -> None:                                # auto-save on close
        try:
            self._save()
        except Exception:
            pass
        super().destroy()
