"""Dynamic modifiers editor (``common/dynamic_modifiers``).

Standard skeleton: collapsible file → modifier tree (left) · entry inspector
(center) · problems panel (bottom). Vanilla is read-only with one-click
copy-to-mod; new entries go to the selected mod file (else
``anka_dynamic_modifiers.txt``).
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...core.gfx import SpriteResolver
from ...services._locutil import LocCatalog
from ...services.dynamic_modifier_service import DynamicModifierService
from ..base import EditorModule, EditorRegistry
from ..common import TextPromptDialog
from .inspector import DynamicModifierInspector


@EditorRegistry.register
class DynamicModifiersEditor(EditorModule):
    id = "dynamic_modifiers"
    name_key = "editors.dynamic_modifiers.name"
    desc_key = "editors.dynamic_modifiers.desc"
    order = 70

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = DynamicModifierService(context)
        self.resolver = SpriteResolver.for_mod(context.mod.path, context.game_path,
                                               context.dependency_paths)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        # Vanilla names live in files like dynamic_modifiers_l_english.yml.
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="modifier",
                              default_pattern="anka_dynamic_modifiers_l_{lang}.yml",
                              dep_roots=context.dependency_paths)
        self._resolver_ready = threading.Event()
        self._mod_docs: list = []
        self._vanilla_refs: list = []
        self._dirty: set = set()
        self._items: dict[str, tuple] = {}
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self._copy_ref = None

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        threading.Thread(target=self._warm_resolver, daemon=True).start()
        root = ttk.Frame(parent, style="TFrame")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)
        self._grid_root = root

        self._build_toolbar(root)
        self._build_list_panel(root)

        center = ttk.Frame(root, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        host = ttk.Frame(center, style="TFrame")
        host.grid(row=0, column=0, sticky="nsew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.inspector = DynamicModifierInspector(host, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(host,
                                      text=self.t("dyn_mod.select_hint"),
                                      style="Muted.TLabel")
        self._placeholder.grid(row=0, column=0)
        self._show_inspector(False)

        self._build_problems(center)
        self.reload_tree()
        return root

    def _warm_resolver(self) -> None:
        try:
            self.resolver.resolve("")
        finally:
            self._resolver_ready.set()

    def resolver_ready(self) -> bool:
        return self._resolver_ready.is_set()

    def _build_toolbar(self, root) -> None:
        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("dyn_mod.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("dyn_mod.new_entry"),
                   command=self._new_entry).pack(side="left", padx=4)
        ttk.Button(bar, text="🗎 " + self.t("dyn_mod.new_file"),
                   command=self._new_file).pack(side="left", padx=2)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)
        self._copy_btn = ttk.Button(bar, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    command=self._copy_to_mod)
        self._btn_problems = ttk.Button(bar, text="⚠ 0",
                                        command=self._toggle_problems)
        self._btn_problems.pack(side="right")

    def _build_list_panel(self, root) -> None:
        panel = ttk.Frame(root, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)
        self._list_panel = panel
        self._list_visible = True
        root.columnconfigure(0, minsize=300)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("dyn_mod.show_vanilla"),
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

    def _build_problems(self, center) -> None:
        panel = ttk.Frame(center, style="Card.TFrame", padding=(8, 6))
        panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(0, weight=1)
        self._problems_panel = panel
        self._problems = ttk.Treeview(panel, columns=("sev", "subject", "msg"),
                                      show="headings", height=4)
        self._problems.heading("sev", text="!")
        self._problems.heading("subject", text=self.t("dyn_mod.col.modifier"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("subject", width=240, stretch=False)
        self._problems.column("msg", width=400)
        self._problems.grid(row=0, column=0, sticky="ew")
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
        for ref in self.service.list_docs(include_vanilla=False):
            try:
                self._mod_docs.append(self.service.load(ref))
            except Exception:
                continue
        self._vanilla_refs = ([r for r in self.service.list_docs(True)
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
                return
            file_iid = f"f::{rel_file}"
            self._tree.insert("", "end", iid=file_iid,
                              text=rel_file.rsplit("/", 1)[-1],
                              open=bool(query) or file_iid in open_files,
                              tags=("file",) + (("vanilla",) if is_vanilla
                                                else ()))
            self._items[file_iid] = ("file", payload, is_vanilla)
            for index, name in rows:
                iid = f"e::{rel_file}::{index}"
                self._tree.insert(file_iid, "end", iid=iid, text=name,
                                  tags=("vanilla",) if is_vanilla else ())
                self._items[iid] = ("entry", payload, index)

        for doc in self._mod_docs:
            add_file(doc.ref.rel_file, [e.name for e in doc.entries()],
                     False, doc)
        for ref in self._vanilla_refs:
            add_file(ref.rel_file, ref.names, True, ref)

    def refresh_tree_labels(self) -> None:
        self._refresh_tree()

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        self.inspector.flush_pending()
        if payload[0] == "file":
            self._set_copy_btn(payload[1] if payload[2] else None)
            self._show_inspector(False)
            return
        _kind, doc_or_ref, index = payload
        is_vanilla = not hasattr(doc_or_ref, "entries")
        try:
            doc = self.service.load(doc_or_ref) if is_vanilla else doc_or_ref
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

    # ------------------------------------------------------------------ actions
    def _target_doc(self):
        sel = self._tree.selection()
        payload = self._items.get(sel[0]) if sel else None
        if payload is not None:
            doc = payload[1]
            if hasattr(doc, "entries") and not doc.ref.is_vanilla:
                return doc
        return self.service.mod_target_doc()

    def _new_entry(self) -> None:
        def create(name: str) -> None:
            doc = self._target_doc()
            self.service.add_entry(doc, name)
            if all(d.ref.path != doc.ref.path for d in self._mod_docs):
                self._mod_docs.append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            index = len(doc.entries()) - 1
            iid = f"e::{doc.ref.rel_file}::{index}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("dyn_mod.new_entry"),
                         self.t("dyn_mod.name_label"), create,
                         pattern=r"^\w+$")

    def _new_file(self) -> None:
        def create(name: str) -> None:
            doc = self.service.create_doc(name)
            if any(d.ref.path == doc.ref.path for d in self._mod_docs):
                return
            self._mod_docs.append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            iid = f"f::{doc.ref.rel_file}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("dyn_mod.new_file"),
                         self.t("dyn_mod.file_label"), create,
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

    def delete_entry(self, doc, entry) -> None:
        self.service.remove_entry(doc, entry)
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
            entry = entries[index]
            if messagebox.askyesno("ANKA", self.t("dyn_mod.confirm_delete",
                                                  name=entry.name)):
                self.delete_entry(doc, entry)

    # ----------------------------------------------------------- shared protocol
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

    # ---------------------------------------------------------------- validation
    def _validate(self) -> None:
        from ..effects import ScriptCatalog
        issues = self.service.validate(self._mod_docs,
                                       known=ScriptCatalog.modifiers())
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(issues):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"dyn_mod.issue.{issue.code}", detail=issue.detail)
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

    def on_leave(self) -> None:
        self.save_all()
