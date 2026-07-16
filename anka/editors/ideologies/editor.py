"""Ideologies editor (``common/ideologies``).

Standard editor skeleton (shared with Dynamic modifiers / Ideas): collapsible
file → ideology tree (left) · ideology inspector (center) · problems panel
(bottom). Content is merged across the layers — base game → dependency submods →
the edited mod — via the service's layered ``list_docs``; only the mod's own
files are editable, everything below is read-only with one-click copy-to-mod.
New ideologies go into the selected mod file (else ``anka_ideologies.txt``).
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...services._locutil import LocCatalog
from ...services.ideology_def_service import IdeologyDefService
from ..base import EditorModule, EditorRegistry
from ..common import TextPromptDialog
from .inspector import IdeologyInspector


@EditorRegistry.register
class IdeologiesEditor(EditorModule):
    id = "ideologies"
    name_key = "editors.ideologies.name"
    desc_key = "editors.ideologies.desc"
    order = 90

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = IdeologyDefService(context)
        self.resolver = context.sprites
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        # Ideology + faction names live in loc files; the whole vanilla index is
        # relevant (empty filter matches every file), warmed off-thread.
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="",
                              default_pattern="anka_ideologies_l_{lang}.yml",
                              dep_roots=context.dependency_paths)
        self._resolver_ready = context.warm_sprites()
        self._mod_docs: list = []
        self._vanilla_refs: list = []
        self._dirty: set = set()
        self._items: dict[str, tuple] = {}
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self._copy_ref = None

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        threading.Thread(target=self._warm_loc, daemon=True).start()
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
        self.inspector = IdeologyInspector(host, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(host, text=self.t("ideology.select_hint"),
                                      style="Muted.TLabel")
        self._placeholder.grid(row=0, column=0)
        self._show_inspector(False)

        self._build_problems(center)
        self.reload_tree()
        return root

    def _warm_loc(self) -> None:
        # The sprite map is warmed by the shared per-mod resolver (ModContext);
        # only the vanilla loc index (first .get is the slow one) is preloaded here.
        self.loc.get("CLOSE", self.loc_language)

    def resolver_ready(self) -> bool:
        return self._resolver_ready.is_set()

    def _build_toolbar(self, root) -> None:
        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("ideology.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("ideology.new_entry"),
                   command=self._new_entry).pack(side="left", padx=4)
        ttk.Button(bar, text="🗎 " + self.t("ideology.new_file"),
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
        ttk.Checkbutton(panel, text=self.t("ideology.show_vanilla"),
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
        self._problems.heading("subject", text=self.t("ideology.col.ideology"))
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
                label = self.loc_get(name, self.loc_language)
                shown = name if not label or label == name else f"{name}  ·  {label}"
                self._tree.insert(file_iid, "end", iid=iid, text=shown,
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
        taken = set(self.known_names())

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

        TextPromptDialog(self._tree, self, self.t("ideology.new_entry"),
                         self.t("ideology.name_label"), create,
                         taken=taken, pattern=r"^\w+$")

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

        TextPromptDialog(self._tree, self, self.t("ideology.new_file"),
                         self.t("ideology.file_label"), create,
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

    def rename_entry(self, doc, entry, new_name: str) -> None:
        if new_name in set(self.known_names()):
            messagebox.showerror("ANKA", self.t("focuses.err.duplicate_id"))
            return
        self.service.rename_entry(entry, new_name)
        self.mark_dirty(doc)
        self.reload_tree()
        index = next((i for i, e in enumerate(doc.entries())
                      if e.name == new_name), None)
        if index is not None:
            iid = f"e::{doc.ref.rel_file}::{index}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

    def duplicate_entry(self, doc, entry) -> None:
        if doc is None or doc.ref.is_vanilla:
            return
        taken = set(self.known_names())
        new_name = entry.name + "_copy"
        while new_name in taken:
            new_name += "_copy"
        self.service.duplicate_entry(doc, entry, new_name)
        self.mark_dirty(doc)
        self.reload_tree()
        index = next((i for i, e in enumerate(doc.entries())
                      if e.name == new_name), None)
        if index is not None:
            iid = f"e::{doc.ref.rel_file}::{index}"
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
        if not hasattr(doc, "entries") or doc.ref.is_vanilla:
            return
        entries = doc.entries()
        if index < len(entries):
            entry = entries[index]
            if messagebox.askyesno("ANKA", self.t("ideology.confirm_delete",
                                                  name=entry.name)):
                self.delete_entry(doc, entry)

    # ------------------------------------------------------------------- icons
    def ideology_sprite(self, name: str, *, group: bool) -> str:
        return f"GFX_ideology_{name}_group" if group else f"GFX_ideology_{name}"

    def import_ideology_icon(self, path, name: str, *, group: bool) -> str | None:
        """Convert an image into the ideology (group) / sub-ideology (type)
        icon sprite: DDS under gfx/interface/ideologies + .gfx registration."""
        try:
            if group:
                dds, _gfx = self.context.icons.add_ideology_group_icon(path, name)
            else:
                dds, _gfx = self.context.icons.add_ideology_type_icon(path, name)
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", self.t("ideology.err.icon",
                                                error=str(exc)))
            return None
        sprite = self.ideology_sprite(name, group=group)
        self.resolver.add(sprite, dds)
        return sprite

    def reuse_ideology_icon(self, source_sprite: str, name: str, *,
                            group: bool) -> str | None:
        """Gallery pick: bake the picked sprite's texture into this ideology's
        own icon (copied into the mod, so it keeps working stand-alone)."""
        path = self.resolver.resolve(source_sprite)
        if path is None:
            return None
        return self.import_ideology_icon(path, name, group=group)

    # ----------------------------------------------------------- shared protocol
    def known_names(self) -> list[str]:
        names = [e.name for doc in self._mod_docs for e in doc.entries()]
        for ref in self._vanilla_refs:
            names.extend(ref.names)
        return names

    def loc_get(self, key: str, language: str) -> str:
        return self.loc.get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.loc.set(key, language, text)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        cached = self._value_options.get(vtype)
        if cached is not None:
            return cached
        opts: list[tuple[str, str]] = []
        if vtype == "modifier":
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
                                       known_modifiers=ScriptCatalog.modifiers())
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(sorted(issues, key=lambda x: x.severity)):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"ideology.issue.{issue.code}", detail=issue.detail)
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
