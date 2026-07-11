"""Sprites tab: browse and edit every ``.gfx`` entry across content layers.

Layout mirrors the established editor skeleton: toolbar · collapsible
file→sprite tree (left) · schema-driven sprite inspector (center) · problems
panel (bottom). Vanilla files are read-only with one-click copy-to-mod; new
sprites go into an existing mod .gfx (else ``interface/anka_sprites.gfx``).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...core.guitypes.schema import SPRITE_KINDS
from ..common import SinglePickDialog, TextPromptDialog
from .gfx_inspector import SpriteInspector

from ...services.interface_service import DEFAULT_SPRITES_FILE as DEFAULT_GFX_FILE


class SpritesTab(ttk.Frame):
    def __init__(self, master, editor):
        super().__init__(master, style="TFrame")
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.context = editor.context
        self.service = editor.service
        self.loc_language = editor.loc_language
        self._mod_docs: list = []
        self._vanilla_refs: list = []
        self._dirty: set[str] = set()
        self._items: dict[str, tuple] = {}
        self._copy_ref = None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_toolbar()
        self._build_list_panel()
        self._build_center()
        self.reload_tree()

    # ----------------------------------------------------------- owner protocol
    @property
    def resolver(self):
        return self.editor.resolver

    def resolver_ready(self) -> bool:
        return self.editor.resolver_ready()

    def loc_get(self, key: str, language: str) -> str:
        return self.editor.loc_get(key, language)

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.editor.loc_set(key, language, text)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        return self.editor.value_options(vtype)

    # ------------------------------------------------------------------- build
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("interface.gfx.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("interface.gfx.new_sprite"),
                   command=self._new_sprite).pack(side="left", padx=4)
        ttk.Button(bar, text="🗎 " + self.t("interface.gfx.new_file"),
                   command=self._new_file).pack(side="left", padx=4)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)
        self._copy_btn = ttk.Button(bar, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    command=self._copy_to_mod)
        self._btn_problems = ttk.Button(bar, text="⚠ 0",
                                        command=self._toggle_problems)
        self._btn_problems.pack(side="right")

    def _build_list_panel(self) -> None:
        panel = ttk.Frame(self, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)
        self._list_panel = panel
        self._list_visible = True
        self.columnconfigure(0, minsize=320)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("interface.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self.reload_tree).grid(row=1, column=0,
                                                       sticky="w", pady=(0, 6))
        self._tree = ttk.Treeview(panel, show="tree", selectmode="browse")
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.tag_configure("file", font=("Segoe UI Semibold", 10))
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Delete>", self._delete_selected)

    def _build_center(self) -> None:
        center = ttk.Frame(self, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        host = ttk.Frame(center, style="TFrame")
        host.grid(row=0, column=0, sticky="nsew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.inspector = SpriteInspector(host, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(host, text=self.t("interface.gfx.select_hint"),
                                      style="Muted.TLabel")
        self._placeholder.grid(row=0, column=0)
        self._show_inspector(False)

        panel = ttk.Frame(center, style="Card.TFrame", padding=(8, 6))
        panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(0, weight=1)
        self._problems_panel = panel
        self._problems = ttk.Treeview(panel, columns=("sev", "subject", "msg"),
                                      show="headings", height=4)
        self._problems.heading("sev", text="!")
        self._problems.heading("subject", text=self.t("interface.gfx.col.sprite"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("subject", width=260, stretch=False)
        self._problems.column("msg", width=420)
        self._problems.grid(row=0, column=0, sticky="ew")
        self._problems.tag_configure("error", foreground=self.palette.danger)
        panel.grid_remove()
        self._problems_visible = False

    # ---------------------------------------------------------------- toggles
    def _toggle_list(self) -> None:
        self._list_visible = not self._list_visible
        if self._list_visible:
            self.columnconfigure(0, minsize=320)
            self._list_panel.grid()
        else:
            self._list_panel.grid_remove()
            self.columnconfigure(0, minsize=0)

    def _toggle_problems(self) -> None:
        self._problems_visible = not self._problems_visible
        if self._problems_visible:
            self._validate()
            self._problems_panel.grid()
        else:
            self._problems_panel.grid_remove()

    def _show_inspector(self, visible: bool) -> None:
        if visible:
            self._placeholder.grid_remove()
            self.inspector.grid()
        else:
            self.inspector.grid_remove()
            self._placeholder.grid()

    # ------------------------------------------------------------------- tree
    def reload_tree(self, keep_selection: bool = False) -> None:
        selected = self._tree.selection() if keep_selection else ()
        self._mod_docs = []
        for ref in self.service.list_gfx_docs(include_vanilla=False):
            try:
                self._mod_docs.append(self.service.load_gfx(ref))
            except Exception:
                continue
        self._vanilla_refs = ([r for r in self.service.list_gfx_docs(True)
                               if r.is_vanilla]
                              if self._vanilla.get() else [])
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    def _refresh_tree(self) -> None:
        query = self._search.get().strip().lower()
        open_files = {iid for iid in self._tree.get_children("")
                      if self._tree.item(iid, "open")}
        self._tree.delete(*self._tree.get_children())
        self._items.clear()

        def add_file(rel_file: str, names: list[str], is_vanilla: bool,
                     payload) -> None:
            rows = [(i, name) for i, name in enumerate(names)
                    if not query or query in name.lower()]
            if not rows and query and query not in rel_file.lower():
                return
            if not rows and not query and is_vanilla:
                return          # empty mod files stay visible (fresh files)
            file_iid = f"f::{rel_file}"
            self._tree.insert("", "end", iid=file_iid,
                              text=rel_file[len("interface/"):]
                              if rel_file.startswith("interface/") else rel_file,
                              open=bool(query) or file_iid in open_files,
                              tags=("file",) + (("vanilla",) if is_vanilla else ()))
            self._items[file_iid] = ("file", payload, is_vanilla)
            for index, name in rows:
                iid = f"e::{rel_file}::{index}"
                self._tree.insert(file_iid, "end", iid=iid, text=name,
                                  tags=("vanilla",) if is_vanilla else ())
                self._items[iid] = ("entry", payload, index)

        for doc in self._mod_docs:
            add_file(doc.ref.rel_file, [v.name or v.type_key
                                        for v in doc.entries()], False, doc)
        for ref in self._vanilla_refs:
            add_file(ref.rel_file, ref.names, True, ref)

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        self.inspector.flush_pending()
        if payload[0] == "file":
            ref = payload[1] if payload[2] else None
            self._set_copy_btn(ref)
            self._show_inspector(False)
            return
        _kind, doc_or_ref, index = payload
        is_vanilla = not hasattr(doc_or_ref, "entries")
        try:
            doc = (self.service.load_gfx(doc_or_ref) if is_vanilla else doc_or_ref)
        except Exception:
            self._show_inspector(False)
            return
        entries = doc.entries()
        if index >= len(entries):
            self._show_inspector(False)
            return
        self._show_inspector(True)
        self.inspector.show(doc, entries[index],
                            editable=not doc.ref.is_vanilla)
        self._set_copy_btn(doc.ref if doc.ref.is_vanilla else None)

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    # ----------------------------------------------------------------- actions
    def _mod_target_doc(self):
        for doc in self._mod_docs:
            return doc
        ref = self.service.create_doc(DEFAULT_GFX_FILE, "spriteTypes")
        doc = self.service.load_gfx(ref)
        self._mod_docs.append(doc)
        return doc

    def _new_sprite(self) -> None:
        kinds = [(spec.type_key, spec.type_key) for spec in
                 sorted(SPRITE_KINDS.values(), key=lambda s: s.type_key.lower())]

        def kind_picked(kind: str) -> None:
            TextPromptDialog(self._tree, self,
                             self.t("interface.gfx.new_sprite"),
                             self.t("interface.gfx.name_label"),
                             lambda name: create(kind, name),
                             initial="GFX_", pattern=r"^\S+$")

        def create(kind: str, name: str) -> None:
            doc = self._mod_target_doc()
            doc.add_entry(kind, name)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            index = len(doc.entries()) - 1
            iid = f"e::{doc.ref.rel_file}::{index}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        SinglePickDialog(self._tree, self, self.t("interface.gfx.pick_kind"),
                         kinds, kind_picked)

    def _new_file(self) -> None:
        def create(name: str) -> None:
            rel = f"interface/{name}.gfx" if not name.endswith(".gfx") \
                else f"interface/{name}"
            ref = self.service.create_doc(rel, "spriteTypes")
            doc = self.service.load_gfx(ref)
            self._mod_docs.append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()

        TextPromptDialog(self._tree, self, self.t("interface.gfx.new_file"),
                         self.t("interface.gfx.file_label"), create,
                         pattern=r"^[\w./-]+$")

    def _copy_to_mod(self) -> None:
        if self._copy_ref is None:
            return
        selected = self._tree.selection()
        new_ref = self.service.copy_to_mod(self._copy_ref)
        self.reload_tree()
        if selected and selected[0].startswith("e::"):
            index = selected[0].split("::")[-1]
            iid = f"e::{new_ref.rel_file}::{index}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

    def delete_sprite(self, doc, view) -> None:
        doc.remove_entry(view)
        self.mark_dirty(doc)
        self._show_inspector(False)
        self.reload_tree(keep_selection=False)
        file_iid = f"f::{doc.ref.rel_file}"
        if self._tree.exists(file_iid):
            self._tree.selection_set(file_iid)

    def _delete_selected(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None or payload[0] != "entry":
            return
        _kind, doc, index = payload
        if not hasattr(doc, "entries"):
            return
        entries = doc.entries()
        if index < len(entries):
            view = entries[index]
            if messagebox.askyesno("ANKA", self.t("interface.gfx.confirm_delete",
                                                  name=view.name)):
                self.delete_sprite(doc, view)

    # ---------------------------------------------------------------- validation
    def _validate(self) -> None:
        issues = self.service.validate_gfx(self._mod_docs)
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(issues):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"interface.gfx.issue.{issue.code}", detail=issue.detail)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error"
                                          else "⚠", issue.subject, msg),
                                  tags=(issue.severity,))
        n = len(issues)
        label = f"⚠ {n}" if not errors else f"⛔ {errors} · ⚠ {n - errors}"
        self._btn_problems.configure(text=label)

    # ------------------------------------------------------------------ saving
    def mark_dirty(self, doc) -> None:
        if doc is not None:
            self._dirty.add(str(doc.ref.path))

    def flush_pending(self) -> None:
        self.inspector.flush_pending()

    def save_all(self) -> None:
        self.inspector.flush_pending()
        for doc in self._mod_docs:
            if str(doc.ref.path) in self._dirty:
                try:
                    self.service.save(doc)
                    self._dirty.discard(str(doc.ref.path))
                except Exception as exc:                  # noqa: BLE001
                    messagebox.showerror("ANKA", self.t("focuses.err.save",
                                                        error=str(exc)))
        self._validate()
