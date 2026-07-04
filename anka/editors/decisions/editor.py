"""Decisions editor.

Layout: collapsible category→decision tree (left) · inspector (center/right) ·
collapsible problems panel (bottom). No canvas — decisions have no spatial layout.

The tree aggregates every mod file under ``common/decisions`` (a category may gain
decisions from several files); the vanilla checkbox adds base-game content read-only
(quick-scanned, parsed lazily on selection) with one-click "copy into mod". Edits mark
the owning document dirty; dirty documents are saved explicitly or on leave.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...core.gfx import SpriteResolver
from ...services.decision_service import Decision, DecisionDocRef, DecisionService
from ..base import EditorModule, EditorRegistry
from ..common import TextPromptDialog
from .inspector import CategoryInspector, DecisionInspector


@EditorRegistry.register
class DecisionsEditor(EditorModule):
    id = "decisions"
    name_key = "editors.decisions.name"
    desc_key = "editors.decisions.desc"
    order = 60

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = DecisionService(context)
        self.resolver = SpriteResolver.for_mod(context.mod.path, context.game_path)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._resolver_ready = threading.Event()
        self._mod_docs: list = []               # parsed mod decision documents
        self._dirty: set = set()                # document ids (paths)
        self._items: dict[str, tuple] = {}      # tree iid -> payload
        self._selection: tuple | None = None
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self.selected_category_name = ""

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        threading.Thread(target=self._warm_resolver, daemon=True).start()
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
        self.decision_inspector = DecisionInspector(self._insp_host, self)
        self.decision_inspector.grid(row=0, column=0, sticky="nsew")
        self.category_inspector = CategoryInspector(self._insp_host, self)
        self.category_inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(self._insp_host,
                                      text=self.t("decisions.select_hint"),
                                      style="Muted.TLabel")
        self._placeholder.grid(row=0, column=0)
        self._show_inspector(None)

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
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("decisions.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("decisions.new_decision"),
                   command=self._new_decision).pack(side="left", padx=4)
        ttk.Button(bar, text="🗂 " + self.t("decisions.new_category"),
                   command=self._new_category).pack(side="left", padx=2)
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
        root.columnconfigure(0, minsize=280)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("decisions.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self.reload_tree).grid(row=1, column=0, sticky="w",
                                                       pady=(0, 6))
        self._tree = ttk.Treeview(panel, show="tree", selectmode="browse")
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.tag_configure("category", font=("Segoe UI Semibold", 10))
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
        self._problems.heading("subject", text=self.t("decisions.col.decision"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("subject", width=240, stretch=False)
        self._problems.column("msg", width=400)
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
            self._grid_root.columnconfigure(0, minsize=280)
            self._list_panel.grid()
        else:
            # Drop the reserved column width too, or a 280px dead strip stays and
            # the inspector never claims the freed space.
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
        self._category_cache = None
        self._mod_docs = [self.service.load(ref)
                          for ref in self.service.list_docs(False, "decisions")]
        self._vanilla_refs = ([r for r in self.service.list_docs(True, "decisions")
                               if r.is_vanilla] if self._vanilla.get() else [])
        # Defined categories must show up even with zero decisions in them —
        # otherwise a freshly created category would be invisible/unselectable.
        self._def_cats_mod = {name
                              for ref in self.service.list_docs(False, "categories")
                              for name in ref.categories}
        self._def_cats_vanilla = ({name
                                   for ref in self.service.list_docs(True, "categories")
                                   if ref.is_vanilla for name in ref.categories}
                                  if self._vanilla.get() else set())
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    def _refresh_tree(self) -> None:
        query = self._search.get().strip().lower()
        # Rebuilding must not collapse categories the user expanded.
        open_cats = {iid for iid in self._tree.get_children("")
                     if self._tree.item(iid, "open")}
        self._tree.delete(*self._tree.get_children())
        self._items.clear()

        # category -> [(payload, label, vanilla)], preserving mod-first order
        buckets: dict[str, list[tuple]] = {}
        for name in getattr(self, "_def_cats_mod", set()):
            buckets.setdefault(name, [])
        for doc in self._mod_docs:
            for d in doc.decisions():
                buckets.setdefault(d.category, []).append(
                    (("decision", doc.ref, d.id), d.id, False))
        for name in getattr(self, "_def_cats_vanilla", set()):
            buckets.setdefault(name, [])
        for ref in getattr(self, "_vanilla_refs", []):
            for cat, ids in ref.categories.items():
                for did in ids:
                    buckets.setdefault(cat, []).append(
                        (("decision", ref, did), did, True))

        for cat in sorted(buckets):
            rows = buckets[cat]
            if query:
                rows = [r for r in rows if query in r[1].lower()]
                if not rows and query not in cat.lower():
                    continue
            cat_iid = f"c::{cat}"
            label = self.service.name_of(cat, self.loc_language)
            text = cat if label == cat else f"{cat}  ·  {label}"
            self._tree.insert("", "end", iid=cat_iid, text=text,
                              open=bool(query) or cat_iid in open_cats,
                              tags=("category",))
            self._items[cat_iid] = ("category", cat)
            for payload, label, is_vanilla in rows:
                iid = f"d::{payload[1].rel_file}::{payload[2]}"
                if self._tree.exists(iid):
                    continue
                self._tree.insert(cat_iid, "end", iid=iid, text=label,
                                  tags=("vanilla",) if is_vanilla else ())
                self._items[iid] = payload

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
        if payload[0] == "category":
            self.selected_category_name = payload[1]
            hit = self.service.find_category_def(payload[1])
            if hit is None:
                self._show_inspector("category")
                self.category_inspector.show(None, None, False)
            else:
                ref, cat = hit
                doc = self.service.load(ref)
                cat = doc.find_category(payload[1])
                self._show_inspector("category")
                self.category_inspector.show(doc, cat, not ref.is_vanilla)
                self._set_copy_btn(ref if ref.is_vanilla else None)
            return
        _kind, ref, did = payload
        doc = self.service.load(ref)
        decision = doc.find(did)
        self._show_inspector("decision" if decision is not None else None)
        if decision is not None:
            self.decision_inspector.show(doc, decision, not ref.is_vanilla)
        self._set_copy_btn(ref if ref.is_vanilla else None)

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    def _show_inspector(self, which: str | None) -> None:
        self.decision_inspector.grid_remove()
        self.category_inspector.grid_remove()
        self._placeholder.grid_remove()
        if which == "decision":
            self.decision_inspector.grid()
        elif which == "category":
            self.category_inspector.grid()
        else:
            self._placeholder.grid()

    def _flush_inspectors(self) -> None:
        self.decision_inspector.flush_pending()
        self.category_inspector.flush_pending()

    # ------------------------------------------------------------------ actions
    def _new_category(self) -> None:
        taken = set(self.service.category_names())

        def submit(name: str) -> None:
            self.service.create_category(name)
            self.reload_tree()
            iid = f"c::{name}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("decisions.new_category"),
                         self.t("decisions.category_id"), submit, taken=taken)

    def _new_decision(self) -> None:
        categories = self.service.category_names()
        if not categories:
            messagebox.showinfo("ANKA", self.t("decisions.no_categories"))
            return
        taken = set(self.known_ids())
        default_cat = self.selected_category_name or categories[0]
        choices = [(c, c) for c in categories]
        choices.sort(key=lambda cv: cv[0] != default_cat)

        def submit(did: str, category: str) -> None:
            doc = self.service.mod_target_doc(category)
            self.service.add_decision(doc, category, did)
            if all(d.ref.path != doc.ref.path for d in self._mod_docs):
                self._mod_docs.append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            iid = f"d::{doc.ref.rel_file}::{did}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("decisions.new_decision"),
                         self.t("decisions.decision_id"), submit, taken=taken,
                         choices_label=self.t("decisions.category"), choices=choices)

    def _copy_to_mod(self) -> None:
        ref = getattr(self, "_copy_ref", None)
        if ref is None:
            return
        selected = self._tree.selection()
        new_ref = self.service.copy_to_mod(ref)
        self.reload_tree()
        if selected:
            old = selected[0]
            if old.startswith("d::"):
                did = old.split("::")[-1]
                iid = f"d::{new_ref.rel_file}::{did}"
                if self._tree.exists(iid):
                    self._tree.selection_set(iid)
                    self._tree.see(iid)

    def rename_decision(self, decision: Decision, new_id: str) -> None:
        if new_id in set(self.known_ids()):
            messagebox.showerror("ANKA", self.t("focuses.err.duplicate_id"))
            return
        doc = self.decision_inspector.doc
        self.service.rename_decision(decision, new_id)
        self.mark_dirty(doc)
        self.reload_tree()
        iid = f"d::{doc.ref.rel_file}::{new_id}"
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)

    def duplicate_decision(self) -> None:
        insp = self.decision_inspector
        if insp.decision is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        taken = set(self.known_ids())
        new_id = insp.decision.id + "_copy"
        while new_id in taken:
            new_id += "_copy"
        self.service.duplicate_decision(insp.doc, insp.decision, new_id)
        self.mark_dirty(insp.doc)
        self.reload_tree()
        iid = f"d::{insp.doc.ref.rel_file}::{new_id}"
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)         # also expands the parent category

    def delete_decision(self) -> None:
        insp = self.decision_inspector
        if insp.decision is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        if not messagebox.askyesno("ANKA", self.t("decisions.confirm_delete",
                                                  name=insp.decision.id)):
            return
        category = insp.decision.category
        self.service.remove_decision(insp.doc, insp.decision)
        self.mark_dirty(insp.doc)
        self._show_inspector(None)
        self.reload_tree()
        # Keep the user's context: stay on the (still expanded) category.
        cat_iid = f"c::{category}"
        if self._tree.exists(cat_iid):
            self._tree.selection_set(cat_iid)
            self._tree.see(cat_iid)

    def delete_category(self) -> None:
        """Delete the selected category from the mod (definition + its mod decisions),
        after explicit confirmation. Vanilla categories are refused."""
        name = self.selected_category_name
        if not name:
            return
        in_mod_defs = name in getattr(self, "_def_cats_mod", set())
        count = self.service.count_mod_decisions(name)
        if not in_mod_defs and count == 0:
            messagebox.showinfo("ANKA", self.t("decisions.err.vanilla_category"))
            return
        if not messagebox.askyesno("ANKA", self.t("decisions.confirm_delete_category",
                                                  name=name, count=count)):
            return
        self._flush_inspectors()
        self.service.remove_category(name)   # saves every touched mod file itself
        self._show_inspector(None)
        self.reload_tree()
        self._validate()

    def _delete_selected(self, _event=None) -> None:
        """Delete key in the tree: route to decision/category deletion (both confirm)."""
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        if payload[0] == "category":
            self.delete_category()
        else:
            self.delete_decision()

    def import_icon(self, path, decision: Decision) -> str | None:
        try:
            dds, _gfx = self.context.icons.add_decision_icon(path, decision.id)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("focuses.err.icon", error=str(exc)))
            return None
        sprite = f"GFX_decision_{decision.id}"
        self.resolver.add(sprite, dds)
        return sprite

    # ----------------------------------------------------------- shared protocol
    def known_ids(self) -> list[str]:
        ids = [d.id for doc in self._mod_docs for d in doc.decisions()]
        for ref in getattr(self, "_vanilla_refs", []):
            for cat_ids in ref.categories.values():
                ids.extend(cat_ids)
        return ids

    def category_options(self) -> list[str]:
        if getattr(self, "_category_cache", None) is None:
            self._category_cache = self.service.category_names()
        return self._category_cache

    def loc_get(self, key: str, language: str) -> str:
        value = self.service.name_of(key, language)
        return "" if value == key else value

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.service.set_loc(key, language, text, None)

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
        elif vtype == "modifier":
            from ..effects import ScriptCatalog
            for name, mod in sorted(ScriptCatalog.modifiers().items()):
                scopes = ", ".join(mod.scopes)
                opts.append((f"{name} · {scopes}" if scopes else name, name))
        self._value_options[vtype] = opts
        return opts

    # ---------------------------------------------------------------- validation
    def _validate(self) -> None:
        sprite_exists = ((lambda s: self.resolver.resolve(s) is not None)
                         if self.resolver_ready() else None)
        issues = self.service.validate(self._mod_docs, language=self.loc_language,
                                       sprite_exists=sprite_exists)
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(sorted(issues, key=lambda i: i.severity)):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"decisions.issue.{issue.code}", detail=issue.detail)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error" else "⚠",
                                          issue.subject, msg),
                                  tags=(issue.severity,))
        label = f"⚠ {len(issues)}" if not errors else f"⛔ {errors} · ⚠ {len(issues) - errors}"
        self._btn_problems.configure(text=label)

    # ------------------------------------------------------------------ saving
    def mark_dirty(self, doc) -> None:
        if doc is not None:
            self._dirty.add(doc.ref.path)

    def save_all(self) -> None:
        self._flush_inspectors()
        for doc in self._mod_docs:
            if doc.ref.path in self._dirty:
                try:
                    self.service.save(doc)
                    self._dirty.discard(doc.ref.path)
                except Exception as exc:
                    messagebox.showerror("ANKA", self.t("focuses.err.save",
                                                        error=str(exc)))
        # category documents are edited through their own inspector
        cat_doc = self.category_inspector.doc
        if cat_doc is not None and cat_doc.ref.path in self._dirty:
            try:
                self.service.save(cat_doc)
                self._dirty.discard(cat_doc.ref.path)
            except Exception as exc:
                messagebox.showerror("ANKA", self.t("focuses.err.save", error=str(exc)))
        self._validate()

    def on_leave(self) -> None:
        self.save_all()
