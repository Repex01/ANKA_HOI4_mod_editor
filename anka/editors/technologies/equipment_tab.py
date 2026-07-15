"""Equipment tab: browse and edit ``common/units/equipment`` across content layers.

Layout mirrors the established editor skeleton (see `SpritesTab`): toolbar ·
collapsible file→equipment tree (left) · block-backed inspector (right) ·
problems panel (bottom). Vanilla files are read-only with one-click
copy-to-mod; new entries go into an existing mod file (else
``anka_equipment.txt``). Archetypes are marked with ★ in the tree.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...services.equipment_service import (
    ANKA_EQUIPMENT_FILE,
    EquipmentService,
)
from ..common import SinglePickDialog, TextPromptDialog
from .equipment_inspector import EquipmentInspector


class EquipmentTab(ttk.Frame):
    def __init__(self, master, editor):
        super().__init__(master, style="TFrame")
        self.editor = editor                  # TechnologiesEditor (owner protocol)
        self.t = editor.t
        self.palette = editor.palette
        self.context = editor.context
        self.loc_language = editor.loc_language
        self.service = EquipmentService(editor.context)
        self.tech_service = editor.service    # languages() + enable_equipments scan
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
    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        return self.editor.value_options(vtype)

    @property
    def resolver(self):
        return self.editor.resolver

    def resolver_ready(self) -> bool:
        return self.editor.resolver_ready()

    def import_equipment_icon(self, path, picture: str) -> str | None:
        """Convert an image into ``GFX_<picture>_medium`` (DDS + registration)."""
        try:
            dds, _gfx = self.context.icons.add_equipment_icon(path, picture)
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", self.t("technologies.err.icon",
                                                error=str(exc)))
            return None
        sprite = f"GFX_{picture}_medium"
        self.editor.interface.resolver().add(sprite, dds)
        return sprite

    def reuse_equipment_sprite(self, picture: str, source: str) -> str | None:
        """Point ``GFX_<picture>_medium`` at an existing sprite's texture."""
        return self.editor.reuse_sprite(f"GFX_{picture}_medium", source)

    def loc_get(self, key: str, language: str) -> str:
        return self.service.loc_get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.service.loc_set(key, language, text)

    def languages(self) -> list[str]:
        return self.tech_service.languages()

    # ------------------------------------------------------------------- build
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("equipment.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("equipment.new_equipment"),
                   command=self._new_equipment).pack(side="left", padx=4)
        ttk.Button(bar, text="🗎 " + self.t("equipment.new_file"),
                   command=self._new_file).pack(side="left", padx=4)
        ttk.Button(bar, text="🗑 " + self.t("equipment.delete"),
                   command=self._delete_selected).pack(side="left", padx=4)
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
        ttk.Checkbutton(panel, text=self.t("equipment.show_vanilla"),
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
        self._tree.bind("<Button-3>", self._tree_context)
        self._menu: tk.Menu | None = None

    def _build_center(self) -> None:
        center = ttk.Frame(self, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        host = ttk.Frame(center, style="TFrame")
        host.grid(row=0, column=0, sticky="nsew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.inspector = EquipmentInspector(host, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(host, text=self.t("equipment.select_hint"),
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
        self._problems.heading("subject", text=self.t("equipment.col.equipment"))
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
        # Dependency-mod files are is_vanilla=True even with the checkbox off
        # (layered_docs always lists them): read-only base files, never
        # editable mod docs — and never twice (duplicate iids break the tree).
        refs = self.service.list_docs(include_vanilla=self._vanilla.get())
        self._mod_docs = []
        for ref in refs:
            if ref.is_vanilla:
                continue
            try:
                self._mod_docs.append(self.service.load(ref))
            except Exception:
                continue
        self._vanilla_refs = [r for r in refs if r.is_vanilla]
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    _REL_PREFIX = "common/units/equipment/"

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
                              text=rel_file[len(self._REL_PREFIX):]
                              if rel_file.startswith(self._REL_PREFIX)
                              else rel_file,
                              open=bool(query) or file_iid in open_files,
                              tags=("file",) + (("vanilla",) if is_vanilla else ()))
            self._items[file_iid] = ("file", payload, is_vanilla)
            for index, name in rows:
                iid = f"e::{rel_file}::{index}"
                self._tree.insert(file_iid, "end", iid=iid, text=name,
                                  tags=("vanilla",) if is_vanilla else ())
                self._items[iid] = ("entry", payload, index)

        for doc in self._mod_docs:
            add_file(doc.ref.rel_file,
                     [("★ " if e.is_archetype else "") + e.id
                      for e in doc.equipments], False, doc)
        for ref in self._vanilla_refs:
            add_file(ref.rel_file, ref.equipment_ids, True, ref)

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
        is_vanilla = not hasattr(doc_or_ref, "equipments")
        try:
            doc = (self.service.load(doc_or_ref) if is_vanilla else doc_or_ref)
        except Exception:
            self._show_inspector(False)
            return
        entries = doc.equipments
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
        ref = self.service.create_doc(ANKA_EQUIPMENT_FILE)
        doc = self.service.load(ref)
        self._mod_docs.append(doc)
        return doc

    def _new_equipment(self) -> None:
        kinds = [(self.t("equipment.kind.archetype"), "archetype"),
                 (self.t("equipment.kind.equipment"), "equipment")]

        def kind_picked(kind: str) -> None:
            if kind == "archetype":
                ask_id(None)
            else:
                options = [(a, a) for a in self.service.archetypes()]
                SinglePickDialog(self._tree, self.editor,
                                 self.t("equipment.pick_archetype"),
                                 options, ask_id)

        def ask_id(archetype: str | None) -> None:
            TextPromptDialog(self._tree, self.editor,
                             self.t("equipment.new_equipment"),
                             self.t("equipment.name_label"),
                             lambda eid: ask_copy_source(archetype, eid),
                             taken=set(self.service.equipment_index()))

        def ask_copy_source(archetype: str | None, eid: str) -> None:
            """Optionally seed the new entry with the stats of an existing
            entry of the same category (same archetype / other archetypes)."""
            sources = (self.service.archetypes() if archetype is None
                       else self.service.concrete_ids_of(archetype))
            if not sources:
                create(archetype, eid, None)
                return
            options = [("— " + self.t("equipment.copy_stats_none"), "")]
            options += [(s, s) for s in sources]
            SinglePickDialog(self._tree, self.editor,
                             self.t("equipment.copy_stats_from"),
                             options,
                             lambda source: create(archetype, eid,
                                                   source or None))

        def create(archetype: str | None, eid: str,
                   copy_from: str | None) -> None:
            doc = self._mod_target_doc()
            try:
                eq = self.service.add_equipment(doc, eid, archetype=archetype)
            except ValueError as exc:
                messagebox.showerror("ANKA", str(exc))
                return
            if copy_from:
                self.service.copy_stats_from(eq, copy_from)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            index = len(doc.equipments) - 1
            iid = f"e::{doc.ref.rel_file}::{index}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        SinglePickDialog(self._tree, self.editor,
                         self.t("equipment.pick_kind"), kinds, kind_picked)

    def _new_file(self) -> None:
        def create(name: str) -> None:
            try:
                ref = self.service.create_doc(name)
            except FileExistsError:
                messagebox.showerror("ANKA", self.t("equipment.err.file_exists",
                                                    name=name))
                return
            doc = self.service.load(ref)
            self._mod_docs.append(doc)
            self.reload_tree()
            file_iid = f"f::{ref.rel_file}"
            if self._tree.exists(file_iid):
                self._tree.selection_set(file_iid)

        TextPromptDialog(self._tree, self.editor, self.t("equipment.new_file"),
                         self.t("equipment.file_label"), create,
                         pattern=r"^[\w.-]+$")

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
        else:
            file_iid = f"f::{new_ref.rel_file}"
            if self._tree.exists(file_iid):
                self._tree.selection_set(file_iid)

    def _tree_context(self, event) -> None:
        """Right-click on an editable (mod) equipment entry: delete."""
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        self._tree.selection_set(iid)
        payload = self._items.get(iid)
        if payload is None or payload[0] != "entry" \
                or not hasattr(payload[1], "equipments"):
            return                            # vanilla entries are read-only
        if self._menu is not None:
            self._menu.destroy()
        menu = tk.Menu(self._tree, **self.editor._menu_colors())
        self._menu = menu
        menu.add_command(label="🗑 " + self.t("equipment.delete"),
                         command=self._delete_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _delete_selected(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None or payload[0] != "entry":
            return
        _kind, doc, index = payload
        if not hasattr(doc, "equipments"):
            return                           # vanilla entries are read-only
        entries = doc.equipments
        if index >= len(entries):
            return
        eq = entries[index]
        used_by = self.service.referencing_ids(eq.id) if eq.is_archetype else []
        message = self.t("equipment.confirm_delete", name=eq.id)
        if used_by:
            message += "\n" + self.t("equipment.warn.referenced",
                                     ids=", ".join(used_by[:8]))
        if not messagebox.askyesno("ANKA", message):
            return
        touched = self.service.remove_equipment(doc, eq)
        self.mark_dirty(doc)
        for other in touched:
            self.mark_dirty(other)
        self._show_inspector(False)
        self.reload_tree(keep_selection=False)
        file_iid = f"f::{doc.ref.rel_file}"
        if self._tree.exists(file_iid):
            self._tree.selection_set(file_iid)

    def rename_equipment(self, doc, eq, new_id: str) -> None:
        """Rename an entry: service rename + loc carry-over + a warning when
        technologies still unlock the old id (v1 warns, does not rewrite —
        those documents belong to the tech tab's undo history)."""
        old = eq.id
        try:
            self.service.rename_equipment(doc, eq, new_id)
        except ValueError as exc:
            messagebox.showerror("ANKA", str(exc))
            return
        self.service.rename_loc(old, new_id)
        self.mark_dirty(doc)
        self.save_all()
        count = self._tech_reference_count(old)
        if count:
            messagebox.showwarning("ANKA", self.t("equipment.warn.tech_refs",
                                                  count=count, id=old))
        self.reload_tree(keep_selection=True)
        self.inspector.show(doc, eq, editable=True)

    def _tech_reference_count(self, eid: str) -> int:
        count = 0
        try:
            for ref in self.tech_service.list_docs(include_vanilla=False):
                tech_doc = self.tech_service.load(ref)
                count += sum(1 for tech in tech_doc.techs
                             if eid in tech.enable_equipments)
        except Exception:
            return 0
        return count

    # ---------------------------------------------------------------- validation
    def _validate(self) -> None:
        issues = self.service.validate(self._mod_docs,
                                       language=self.loc_language)
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(issues):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"equipment.issue.{issue.code}", detail=issue.detail)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error"
                                          else "⚠", issue.tech_id, msg),
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
                    messagebox.showerror("ANKA", self.t("equipment.err.save",
                                                        error=str(exc)))
        if self._problems_visible:
            self._validate()
