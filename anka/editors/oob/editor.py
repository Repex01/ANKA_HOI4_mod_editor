"""Order-of-battle (OOB) editor — land division templates & deployments.

Layout mirrors the Decisions/Ideas editors: a collapsible OOB-file → template
tree (left) · a template grid editor / file inspector (center) · a collapsible
problems panel (bottom). Only land OOB files (those declaring division templates)
are shown; air/naval files are ignored.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...services._locutil import LocCatalog
from ...services.oob_service import DivisionTemplate, OobService
from ...services.unit_service import UnitService
from ..base import EditorModule, EditorRegistry
from ..common import SinglePickDialog, TextPromptDialog
from .inspector import FileInspector, TemplateInspector


@EditorRegistry.register
class OobEditor(EditorModule):
    id = "oob"
    name_key = "editors.oob.name"
    desc_key = "editors.oob.desc"
    order = 100

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = OobService(context)
        self.unit_service = UnitService(context)
        self._mod_docs: list = []
        self._mod_refs: list = []
        self._vanilla_refs: list = []
        self._dirty: set = set()
        self._items: dict[str, tuple] = {}
        # script-editor owner protocol (instant_effect): loc + typed pickers
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="\x00none",
                              default_pattern="anka_oob_l_{lang}.yml",
                              dep_roots=context.dependency_paths)
        self._value_options: dict[str, list[tuple[str, str]]] = {}

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        root = ttk.Frame(parent, style="TFrame")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)
        self._build_toolbar(root)
        self._build_list_panel(root)

        center = ttk.Frame(root, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self._insp_host = ttk.Frame(center, style="TFrame")
        self._insp_host.grid(row=0, column=0, sticky="nsew")
        self._insp_host.rowconfigure(0, weight=1)
        self._insp_host.columnconfigure(0, weight=1)
        self.template_inspector = TemplateInspector(self._insp_host, self)
        self.template_inspector.grid(row=0, column=0, sticky="nsew")
        self.file_inspector = FileInspector(self._insp_host, self)
        self.file_inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(self._insp_host, text=self.t("oob.select_hint"),
                                      style="Muted.TLabel")
        self._placeholder.grid(row=0, column=0)
        self._show_inspector(None)

        self._build_problems(center)
        self.reload_tree()
        return root

    def _build_toolbar(self, root) -> None:
        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("oob.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("oob.new_template"),
                   command=self._new_template).pack(side="left", padx=4)
        ttk.Button(bar, text="🗂 " + self.t("oob.new_file"),
                   command=self._new_file).pack(side="left", padx=2)
        ttk.Button(bar, text="🏷 " + self.t("oob.names.button"),
                   command=self._open_names).pack(side="left", padx=2)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)
        self._copy_btn = ttk.Button(bar, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    command=self._copy_to_mod)
        self._btn_problems = ttk.Button(bar, text="⚠ 0", command=self._toggle_problems)
        self._btn_problems.pack(side="right")

    def _build_list_panel(self, root) -> None:
        panel = ttk.Frame(root, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)
        self._list_panel = panel
        self._list_visible = True
        self._grid_root = root
        root.columnconfigure(0, minsize=300)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("oob.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self.reload_tree).grid(row=1, column=0, sticky="w",
                                                       pady=(0, 6))
        self._tree = ttk.Treeview(panel, show="tree", selectmode="browse")
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.tag_configure("file", font=("Segoe UI Semibold", 10))
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Delete>", self._delete_selected)

    def _build_problems(self, center) -> None:
        panel = ttk.Frame(center, style="Card.TFrame", padding=(8, 6))
        panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(0, weight=1)
        self._problems_panel = panel
        self._problems = ttk.Treeview(panel, columns=("sev", "subject", "msg"),
                                      show="headings", height=5)
        self._problems.heading("sev", text="!")
        self._problems.heading("subject", text=self.t("oob.col.template"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("subject", width=220, stretch=False)
        self._problems.column("msg", width=420)
        self._problems.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._problems.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._problems.configure(yscrollcommand=sb.set)
        self._problems.tag_configure("error", foreground=self.palette.danger)
        panel.grid_remove()
        self._problems_visible = False

    # ---------------------------------------------------------------- toggles
    def _toggle_list(self) -> None:
        self._list_visible = not self._list_visible
        if self._list_visible:
            self._grid_root.columnconfigure(0, minsize=300)
            self._list_panel.grid()
        else:
            self._list_panel.grid_remove()
            self._grid_root.columnconfigure(0, minsize=0)

    def _toggle_problems(self) -> None:
        self._problems_visible = not self._problems_visible
        if self._problems_visible:
            self._validate()
            self._problems_panel.grid()
        else:
            self._problems_panel.grid_remove()

    # ------------------------------------------------------------------- tree
    def reload_tree(self, keep_selection: bool = False) -> None:
        selected = self._tree.selection() if keep_selection else ()
        self._mod_refs = self.service.list_docs(False)
        self._mod_docs = [self.service.load(r) for r in self._mod_refs]
        self._vanilla_refs = ([r for r in self.service.list_docs(True)
                               if r.is_vanilla] if self._vanilla.get() else [])
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    def _refresh_tree(self) -> None:
        query = self._search.get().strip().lower()
        open_files = {iid for iid in self._tree.get_children("")
                      if self._tree.item(iid, "open")}
        self._tree.delete(*self._tree.get_children())
        self._items.clear()

        rows: list[tuple] = []       # (rel_file, is_vanilla, [template names])
        for doc in self._mod_docs:
            rows.append((doc.ref, False, [t.name for t in doc.templates()]))
        for ref in self._vanilla_refs:
            rows.append((ref, True, list(ref.templates)))

        for ref, is_vanilla, names in sorted(rows, key=lambda r: (r[1], r[0].name.lower())):
            shown = [n for n in names if not query or query in n.lower()]
            if query and not shown and query not in ref.name.lower():
                continue
            file_iid = f"f::{ref.rel_file}"
            label = ref.name + ("  ✎" if getattr(ref, "edited", False) else "")
            self._tree.insert("", "end", iid=file_iid, text=label,
                              open=bool(query) or file_iid in open_files,
                              tags=("file",) + (("vanilla",) if is_vanilla else ()))
            self._items[file_iid] = ("file", ref)
            for name in (shown if query else names):
                iid = f"t::{ref.rel_file}::{name}"
                if self._tree.exists(iid):
                    continue
                self._tree.insert(file_iid, "end", iid=iid, text=name,
                                  tags=("vanilla",) if is_vanilla else ())
                self._items[iid] = ("template", ref, name)

    def refresh_tree_labels(self) -> None:
        self._refresh_tree()

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        self._flush_inspectors()
        if payload[0] == "file":
            ref = payload[1]
            doc = self.service.load(ref)
            self._show_inspector("file")
            self.file_inspector.show(doc, not ref.is_vanilla)
            self._set_copy_btn(ref if ref.is_vanilla else None)
            return
        _kind, ref, name = payload
        doc = self.service.load(ref)
        template = doc.find_template(name)
        self._show_inspector("template" if template is not None else None)
        if template is not None:
            self.template_inspector.show(doc, template, not ref.is_vanilla)
        self._set_copy_btn(ref if ref.is_vanilla else None)

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    def _show_inspector(self, which: str | None) -> None:
        self.template_inspector.grid_remove()
        self.file_inspector.grid_remove()
        self._placeholder.grid_remove()
        if which == "template":
            self.template_inspector.grid()
        elif which == "file":
            self.file_inspector.grid()
        else:
            self._placeholder.grid()

    def _flush_inspectors(self) -> None:
        self.template_inspector.flush_pending()
        self.file_inspector.flush_pending()

    # ------------------------------------------------------------------ helpers
    def _selected_mod_doc(self):
        """The mod document of the current selection (for adding templates)."""
        sel = self._tree.selection()
        if not sel:
            return None
        payload = self._items.get(sel[0])
        if payload is None:
            return None
        ref = payload[1]
        if ref.is_vanilla:
            return None
        return self.service.load(ref)

    # ------------------------------------------------------------------ actions
    def _new_file(self) -> None:
        taken = {r.rel_file.lower() for r in self.service.list_docs(True)}

        def submit(name: str) -> None:
            doc = self.service.new_document(name)
            if doc.ref.rel_file.lower() in taken:
                messagebox.showinfo("ANKA", self.t("oob.file_exists"))
                return
            self.service.save(doc)
            self.reload_tree()
            iid = f"f::{doc.ref.rel_file}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("oob.new_file"),
                         self.t("oob.file_name"), submit,
                         pattern=r"^[\w\-]+$")

    def _new_template(self) -> None:
        doc = self._selected_mod_doc()
        if doc is None:
            messagebox.showinfo("ANKA", self.t("oob.pick_mod_file"))
            return
        taken = {t.name for t in doc.templates()}

        def submit(name: str) -> None:
            if name in taken:
                messagebox.showerror("ANKA", self.t("oob.template_exists"))
                return
            self.service.add_template(doc, name)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            iid = f"t::{doc.ref.rel_file}::{name}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("oob.new_template"),
                         self.t("oob.template_name"), submit, pattern=r"^.+$")

    def _open_names(self) -> None:
        from .names_dialog import DivisionNamesDialog
        DivisionNamesDialog(self._tree, self)

    def division_names_groups(self) -> list[str]:
        """All name-group keys (mod + dependencies + vanilla) for the template's
        ``division_names_group`` picker."""
        from ...services.division_names_service import DivisionNamesService
        keys: set[str] = set()
        for ref in DivisionNamesService(self.context).list_docs(include_vanilla=True):
            keys.update(ref.groups)
        return sorted(keys)

    def _copy_to_mod(self) -> None:
        ref = getattr(self, "_copy_ref", None)
        if ref is None:
            return
        selected = self._tree.selection()
        new_ref = self.service.copy_to_mod(ref)
        self.reload_tree()
        if selected and selected[0].startswith("t::"):
            name = selected[0].split("::", 2)[-1]
            iid = f"t::{new_ref.rel_file}::{name}"
        else:
            iid = f"f::{new_ref.rel_file}"
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)

    # --- template operations (called by the inspector) ---------------------
    def rename_template(self, template: DivisionTemplate, new_name: str) -> None:
        doc = self.template_inspector.doc
        existing = {t.name for t in doc.templates()} - {template.name}
        if new_name in existing:
            messagebox.showerror("ANKA", self.t("oob.template_exists"))
            return
        old = template.name
        self.service.rename_template(template, new_name)
        # keep deployed divisions in this file pointing at the template
        for d in doc.divisions():
            if d.template == old:
                d.template = new_name
        self.mark_dirty(doc)
        self.reload_tree()
        iid = f"t::{doc.ref.rel_file}::{new_name}"
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)

    def duplicate_template(self) -> None:
        insp = self.template_inspector
        if insp.template is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        taken = {t.name for t in insp.doc.templates()}
        new_name = insp.template.name + " copy"
        while new_name in taken:
            new_name += " copy"
        self.service.duplicate_template(insp.doc, insp.template, new_name)
        self.mark_dirty(insp.doc)
        self.reload_tree()
        iid = f"t::{insp.doc.ref.rel_file}::{new_name}"
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)

    def delete_template(self) -> None:
        insp = self.template_inspector
        if insp.template is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        if not messagebox.askyesno("ANKA", self.t("oob.confirm_delete_template",
                                                  name=insp.template.name)):
            return
        ref = insp.doc.ref
        self.service.remove_template(insp.doc, insp.template)
        self.mark_dirty(insp.doc)
        self._show_inspector(None)
        self.reload_tree()
        fid = f"f::{ref.rel_file}"
        if self._tree.exists(fid):
            self._tree.selection_set(fid)
            self._tree.see(fid)

    def import_from(self, target_doc) -> None:
        """Open a picker of other OOB files and import their templates."""
        options = []
        for ref in self.service.list_docs(True):
            if ref.rel_file == target_doc.ref.rel_file:
                continue
            options.append((f"{ref.name}  ({len(ref.templates)})", ref.rel_file))

        def picked(rel_file: str) -> None:
            src_ref = next((r for r in self.service.list_docs(True)
                            if r.rel_file == rel_file), None)
            if src_ref is None:
                return
            n = self.service.import_templates(target_doc, self.service.load(src_ref))
            self.mark_dirty(target_doc)
            self.save_all()
            self.reload_tree()
            fid = f"f::{target_doc.ref.rel_file}"
            if self._tree.exists(fid):
                self._tree.item(fid, open=True)
                self._tree.selection_set(fid)
            messagebox.showinfo("ANKA", self.t("oob.imported", count=n))

        SinglePickDialog(self._tree, self, self.t("oob.import_from"), options, picked)

    def _delete_selected(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        if payload[0] == "template":
            self.delete_template()

    # ---------------------------------------------------------------- validation
    def _validate(self) -> None:
        known = set(self.unit_service.all_land()) or None
        issues = self.service.validate(self._mod_docs, known_units=known)
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(sorted(issues, key=lambda i: i.severity)):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"oob.issue.{issue.code}", detail=issue.detail)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error" else "⚠",
                                          issue.subject, msg),
                                  tags=(issue.severity,))
        label = f"⚠ {len(issues)}" if not errors else f"⛔ {errors} · ⚠ {len(issues) - errors}"
        self._btn_problems.configure(text=label)

    # ----------------------------------------------------------- shared protocol
    # (owner protocol of the shared script editor — used by instant_effect)
    def loc_get(self, key: str, language: str) -> str:
        return self.loc.get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.loc.set(key, language, text)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        cached = self._value_options.get(vtype)
        if cached is not None:
            return cached
        opts: list[tuple[str, str]] = []
        if vtype == "country":
            from ...services.country_service import CountryService
            for ref in CountryService(self.context).list_tags(include_vanilla=True):
                opts.append((f"{ref.tag} · {ref.name}", ref.tag))
        elif vtype == "state":
            from ...services.state_service import StateService
            for st in StateService(self.context).list_states():
                opts.append((f"{st.id} · {st.name}", str(st.id)))
        elif vtype == "idea":
            from ...services.idea_service import IdeaService
            for idea in IdeaService(self.context).list_ideas():
                opts.append((f"{idea.id} · {idea.category}", idea.id))
        elif vtype == "focus":
            from ...services.focus_service import FocusService
            for fid in FocusService(self.context).focus_ids():
                opts.append((fid, fid))
        elif vtype == "event":
            from ...services.event_service import EventService
            opts = EventService(self.context).event_options()
        elif vtype == "modifier":
            from ..effects import ScriptCatalog
            for name, mod in sorted(ScriptCatalog.modifiers().items()):
                scopes = ", ".join(mod.scopes)
                opts.append((f"{name} · {scopes}" if scopes else name, name))
        self._value_options[vtype] = opts
        return opts

    # ------------------------------------------------------------------ saving
    def mark_dirty(self, doc) -> None:
        if doc is not None:
            self._dirty.add(doc.ref.path)

    def save_all(self) -> None:
        self._flush_inspectors()
        # collect every dirty doc from the inspectors + tree
        docs = {d.ref.path: d for d in self._mod_docs}
        for insp in (self.template_inspector, self.file_inspector):
            if insp.doc is not None:
                docs[insp.doc.ref.path] = insp.doc
        for path, doc in docs.items():
            if path in self._dirty:
                try:
                    self.service.save(doc)
                    self._dirty.discard(path)
                except Exception as exc:
                    messagebox.showerror("ANKA", self.t("focuses.err.save",
                                                        error=str(exc)))
        self._validate()

    def on_leave(self) -> None:
        self.save_all()
