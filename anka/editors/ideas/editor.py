"""Ideas / national-spirits editor.

Layout mirrors the Decisions editor: a collapsible category→idea tree (left) ·
idea / category inspector (center) · collapsible problems panel (bottom).

Tree categories are the *file keys* used inside ``ideas = { ... }`` — a category
name (``country``, ``hidden_ideas``) or a slot (``economy``,
``tank_manufacturer``, ...). The mod files are parsed in full; vanilla content is
quick-scanned and added read-only with one-click "copy into mod". Creating a
category goes through `NewCategoryDialog` → `IdeaService.create_category`, which
also wires the politics-view GUI/GFX.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...core.gfx import SpriteResolver
from ...services.idea_service import (
    CATEGORY_FILE_FLAGS,
    IdeaDef,
    IdeaService,
    LEDGER_VALUES,
)
from ..base import EditorModule, EditorRegistry
from ..common import BaseDialog, SinglePickDialog, TextPromptDialog
from ...ui.widgets import ImageDropZone
from .inspector import IdeaCategoryInspector, IdeaInspector


@EditorRegistry.register
class IdeasEditor(EditorModule):
    id = "ideas"
    name_key = "editors.ideas.name"
    desc_key = "editors.ideas.desc"
    order = 40

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = IdeaService(context)
        self.resolver = SpriteResolver.for_mod(context.mod.path, context.game_path,
                                               context.dependency_paths)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._resolver_ready = threading.Event()
        self._mod_docs: list = []
        self._mod_refs: list = []
        self._vanilla_refs: list = []
        self._dirty: set = set()
        self._items: dict[str, tuple] = {}
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self._trait_cache: list[str] | None = None
        self._cat_key_cache: list[str] | None = None
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
        self.idea_inspector = IdeaInspector(self._insp_host, self)
        self.idea_inspector.grid(row=0, column=0, sticky="nsew")
        self.category_inspector = IdeaCategoryInspector(self._insp_host, self)
        self.category_inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(self._insp_host,
                                      text=self.t("ideas.select_hint"),
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
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("ideas.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("ideas.new_idea"),
                   command=self._new_idea).pack(side="left", padx=4)
        ttk.Button(bar, text="🗂 " + self.t("ideas.new_category"),
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
        root.columnconfigure(0, minsize=300)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("ideas.show_vanilla"),
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
        self._problems.heading("subject", text=self.t("ideas.col.idea"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("subject", width=240, stretch=False)
        self._problems.column("msg", width=400)
        self._problems.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._problems.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._problems.configure(yscrollcommand=sb.set)
        self._problems.tag_configure("error", foreground=self.palette.danger)
        self._problems.bind("<<TreeviewSelect>>", self._on_problem_select)
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
        self._cat_key_cache = None
        self._mod_refs = self.service.list_docs(False)
        self._mod_docs = [self.service.load(r) for r in self._mod_refs]
        self._vanilla_refs = ([r for r in self.service.list_docs(True)
                               if r.is_vanilla] if self._vanilla.get() else [])
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    def category_file_flags(self, key: str) -> set:
        """Union of file flags (law/designer/use_list_view) on a category key
        across every visible doc (mod + shown vanilla) — from the quick scan."""
        flags: set = set()
        for ref in self._mod_refs + self._vanilla_refs:
            flags |= ref.cat_flags.get(key, set())
        return flags

    def _category_badge(self, key: str) -> str:
        """`[law/designer · N слотов]`-style badge for a category node."""
        flags = self.category_file_flags(key)
        parts = [f for f in CATEGORY_FILE_FLAGS if f in flags]
        cdef = self.service.category_defs().get(key)
        if cdef is None:
            owner = self.service.slot_owner(key)
            cdef = self.service.category_defs().get(owner) if owner else None
        if cdef is not None:
            n = len(cdef.slots()) + len(cdef.character_slots())
            if n:
                parts.append(self.t("ideas.badge.slots", count=n))
        return "  ·  [" + ", ".join(parts) + "]" if parts else ""

    def _refresh_tree(self) -> None:
        query = self._search.get().strip().lower()
        open_cats = {iid for iid in self._tree.get_children("")
                     if self._tree.item(iid, "open")}
        self._tree.delete(*self._tree.get_children())
        self._items.clear()

        # category -> [(payload, label, vanilla)], mod-first (materialized empty
        # categories still show: quick-scan lists their key with an empty list).
        buckets: dict[str, list[tuple]] = {}
        for ref in self._mod_refs:
            for cat in ref.categories:
                buckets.setdefault(cat, [])
        for doc in self._mod_docs:
            for idea in doc.ideas():
                buckets.setdefault(idea.category, []).append(
                    (("idea", doc.ref, idea.id), idea.id, False))
        for ref in self._vanilla_refs:
            for cat, ids in ref.categories.items():
                for iid in ids:
                    buckets.setdefault(cat, []).append(
                        (("idea", ref, iid), iid, True))

        for cat in sorted(buckets):
            rows = buckets[cat]
            if query:
                rows = [r for r in rows if query in r[1].lower()]
                if not rows and query not in cat.lower():
                    continue
            cat_iid = f"c::{cat}"
            label = self.service.name_of(cat, self.loc_language)
            text = cat if label == cat else f"{cat}  ·  {label}"
            text += self._category_badge(cat)
            self._tree.insert("", "end", iid=cat_iid, text=text,
                              open=bool(query) or cat_iid in open_cats,
                              tags=("category",))
            self._items[cat_iid] = ("category", cat)
            for payload, lbl, is_vanilla in rows:
                iid = f"i::{payload[1].rel_file}::{payload[2]}"
                if self._tree.exists(iid):
                    continue
                name = self.service.name_of(lbl, self.loc_language)
                shown = lbl if name == lbl else f"{lbl}  ·  {name}"
                self._tree.insert(cat_iid, "end", iid=iid, text=shown,
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
            self._show_inspector("category")
            self.category_inspector.show(payload[1])
            self._set_copy_btn(None)
            return
        _kind, ref, iid = payload
        doc = self.service.load(ref)
        idea = doc.find(iid)
        self._show_inspector("idea" if idea is not None else None)
        if idea is not None:
            self.idea_inspector.show(doc, idea, not ref.is_vanilla)
        self._set_copy_btn(ref if ref.is_vanilla else None)

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    def _show_inspector(self, which: str | None) -> None:
        self.idea_inspector.grid_remove()
        self.category_inspector.grid_remove()
        self._placeholder.grid_remove()
        if which == "idea":
            self.idea_inspector.grid()
        elif which == "category":
            self.category_inspector.grid()
        else:
            self._placeholder.grid()

    def _flush_inspectors(self) -> None:
        self.idea_inspector.flush_pending()
        self.category_inspector.flush_pending()

    # ------------------------------------------------------------------ actions
    def _new_category(self) -> None:
        taken = set(self.service.known_category_keys())

        def submit(params: dict) -> None:
            key = self.service.create_category(**params)
            self.reload_tree()
            iid = f"c::{key}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        NewCategoryDialog(self._tree, self, submit, taken)

    def _new_idea(self) -> None:
        categories = self.category_options()
        if not categories:
            messagebox.showinfo("ANKA", self.t("ideas.no_categories"))
            return
        taken = set(self.known_ids())
        default_cat = self.selected_category_name or categories[0]
        choices = [(c, c) for c in categories]
        choices.sort(key=lambda cv: cv[0] != default_cat)
        base_options = [(f"{i.id}  ·  {i.category}", i.id)
                        for i in self.service.list_ideas()]

        def submit(iid: str, category: str, base_id: str) -> None:
            doc = self.service.mod_target_doc(category)
            if base_id:
                self._create_from(doc, category, iid, base_id)
            else:
                self.service.add_idea(doc, category, iid)
            if all(d.ref.path != doc.ref.path for d in self._mod_docs):
                self._mod_docs.append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            node = f"i::{doc.ref.rel_file}::{iid}"
            if self._tree.exists(node):
                self._tree.selection_set(node)
                self._tree.see(node)

        NewIdeaDialog(self._tree, self, submit, taken, choices, base_options)

    def _create_from(self, doc, category: str, new_id: str, base_id: str) -> None:
        """Clone an existing idea into ``new_id`` and copy its localisation onto
        the new id's keys (english + the current UI language)."""
        found = self.service.find_idea_def(base_id)
        src_key = found[1].name_key if found else base_id
        self.service.create_idea_from(doc, category, new_id, base_id)
        for lang in dict.fromkeys(("english", self.loc_language)):
            name = self.service.loc.get(src_key, lang)
            desc = self.service.loc.get(f"{src_key}_desc", lang)
            if name is not None or desc is not None:
                self.service.set_loc(new_id, lang, name, desc)

    def _copy_to_mod(self) -> None:
        ref = getattr(self, "_copy_ref", None)
        if ref is None:
            return
        selected = self._tree.selection()
        new_ref = self.service.copy_to_mod(ref)
        self.reload_tree()
        if selected and selected[0].startswith("i::"):
            iid = selected[0].split("::")[-1]
            node = f"i::{new_ref.rel_file}::{iid}"
            if self._tree.exists(node):
                self._tree.selection_set(node)
                self._tree.see(node)

    def rename_idea(self, idea: IdeaDef, new_id: str) -> None:
        if new_id in set(self.known_ids()):
            messagebox.showerror("ANKA", self.t("focuses.err.duplicate_id"))
            return
        doc = self.idea_inspector.doc
        self.service.rename_idea(idea, new_id)
        self.mark_dirty(doc)
        self.reload_tree()
        node = f"i::{doc.ref.rel_file}::{new_id}"
        if self._tree.exists(node):
            self._tree.selection_set(node)
            self._tree.see(node)

    def duplicate_idea(self) -> None:
        insp = self.idea_inspector
        if insp.idea is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        taken = set(self.known_ids())
        new_id = insp.idea.id + "_copy"
        while new_id in taken:
            new_id += "_copy"
        old_key = insp.idea.name_key
        had_override = insp.idea.has_name_override
        new_idea = self.service.duplicate_idea(insp.doc, insp.idea, new_id)
        # copy localisation to the duplicate unless it uses a shared name override
        if not had_override:
            for lang in ("english", self.loc_language):
                name = self.service.loc.get(old_key, lang)
                desc = self.service.loc.get(f"{old_key}_desc", lang)
                self.service.set_loc(new_id, lang, name, desc)
        self.mark_dirty(insp.doc)
        self.reload_tree()
        node = f"i::{insp.doc.ref.rel_file}::{new_id}"
        if self._tree.exists(node):
            self._tree.selection_set(node)
            self._tree.see(node)

    def delete_idea(self) -> None:
        insp = self.idea_inspector
        if insp.idea is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        if not messagebox.askyesno("ANKA", self.t("ideas.confirm_delete",
                                                  name=insp.idea.id)):
            return
        category = insp.idea.category
        self.service.remove_idea(insp.doc, insp.idea)
        self.mark_dirty(insp.doc)
        self._show_inspector(None)
        self.reload_tree()
        cat_iid = f"c::{category}"
        if self._tree.exists(cat_iid):
            self._tree.selection_set(cat_iid)
            self._tree.see(cat_iid)

    def delete_category(self) -> None:
        name = self.selected_category_name
        if not name:
            return
        # only mod-defined categories / categories with mod ideas are deletable
        cdef = self.service.category_defs(include_vanilla=False)
        owner = name if name in cdef else self.service.slot_owner(name)
        count = self.service.count_mod_ideas(name)
        if (owner not in cdef) and count == 0:
            messagebox.showinfo("ANKA", self.t("ideas.err.vanilla_category"))
            return
        target = owner if owner in cdef else name
        if not messagebox.askyesno("ANKA", self.t("ideas.confirm_delete_category",
                                                  name=target, count=count)):
            return
        self._flush_inspectors()
        self.service.remove_category(target)
        self._show_inspector(None)
        self.reload_tree()
        self._validate()

    def _delete_selected(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        if payload[0] == "category":
            self.delete_category()
        else:
            self.delete_idea()

    def import_icon(self, path, idea: IdeaDef, keep_size: bool = False) -> str | None:
        try:
            dds, _gfx = self.context.icons.add_idea_icon(path, idea.id,
                                                         resize=not keep_size)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("focuses.err.icon", error=str(exc)))
            return None
        sprite = f"GFX_idea_{idea.id}"
        self.resolver.add(sprite, dds)
        return sprite

    # ----------------------------------------------------------- shared protocol
    def known_ids(self) -> list[str]:
        ids = [idea.id for doc in self._mod_docs for idea in doc.ideas()]
        for ref in self._vanilla_refs:
            for cat_ids in ref.categories.values():
                ids.extend(cat_ids)
        return ids

    def category_options(self) -> list[str]:
        if self._cat_key_cache is None:
            self._cat_key_cache = sorted(self.service.known_category_keys())
        return self._cat_key_cache

    def trait_options(self) -> list[str]:
        if self._trait_cache is None:
            from ...services.trait_service import TraitService
            ts = TraitService(self.context)
            self._trait_cache = sorted(set(ts.country_leader_traits())
                                       | set(ts.unit_leader_traits()))
        return self._trait_cache

    def loc_get(self, key: str, language: str) -> str:
        value = self.service.name_of(key, language)
        return "" if value == key else value

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.service.set_loc(key, language, text, None)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        if vtype == "event":
            from ...services.event_service import EventService
            if getattr(self, "_event_service", None) is None:
                self._event_service = EventService(self.context)
            return self._event_service.event_options()
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
            for idea in self.service.list_ideas():
                opts.append((f"{idea.id} · {idea.category}", idea.id))
        elif vtype == "focus":
            from ...services.focus_service import FocusService
            for fid in FocusService(self.context).focus_ids():
                opts.append((fid, fid))
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
        trait_names = set(self.trait_options()) or None
        issues = self.service.validate(self._mod_docs, language=self.loc_language,
                                       sprite_exists=sprite_exists,
                                       trait_names=trait_names)
        self._problems.delete(*self._problems.get_children())
        self._problem_index = {}
        errors = 0
        for i, issue in enumerate(sorted(issues, key=lambda i: i.severity)):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"ideas.issue.{issue.code}", detail=issue.detail)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error" else "⚠",
                                          issue.subject, msg),
                                  tags=(issue.severity,))
            self._problem_index[str(i)] = issue
        label = f"⚠ {len(issues)}" if not errors else f"⛔ {errors} · ⚠ {len(issues) - errors}"
        self._btn_problems.configure(text=label)

    def _on_problem_select(self, _event=None) -> None:
        sel = self._problems.selection()
        if not sel:
            return
        issue = getattr(self, "_problem_index", {}).get(sel[0])
        if issue is None or not issue.rel_file:
            return
        node = f"i::{issue.rel_file}::{issue.subject}"
        if self._tree.exists(node):
            self._tree.see(node)
            self._tree.selection_set(node)

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
        self._validate()

    def on_leave(self) -> None:
        self.save_all()


class NewIdeaDialog(BaseDialog):
    """New-idea form: id, target category, and an optional *base idea* to clone.

    Picking a base copies that idea's whole block; the new id gets its own,
    fresh localisation keys (the source's ``name =`` override is dropped)."""

    def __init__(self, master, editor, on_submit, taken: set[str],
                 categories: list[tuple[str, str]],
                 base_options: list[tuple[str, str]]):
        super().__init__(master, editor, editor.t("ideas.new_idea"), (440, 300))
        self._on_submit = on_submit
        self._taken = taken
        self._categories = categories
        self._base_options = base_options
        self._base_id = ""
        import re
        self._id_re = re.compile(r"^\w+$")
        t = self.t

        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text=t("ideas.idea_id"), style="Card.TLabel").grid(
            row=0, column=0, sticky="w")
        self._id_var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self._id_var)
        entry.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._submit())

        ttk.Label(body, text=t("ideas.category"), style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w")
        self._cat = ttk.Combobox(body, state="readonly",
                                 values=[label for _v, label in categories])
        self._cat.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        self._cat.current(0)

        ttk.Label(body, text=t("ideas.base_idea"), style="CardMuted.TLabel").grid(
            row=4, column=0, sticky="w")
        base_row = ttk.Frame(body, style="Card.TFrame")
        base_row.grid(row=5, column=0, sticky="ew", pady=(2, 8))
        base_row.columnconfigure(0, weight=1)
        self._base_label = ttk.Label(base_row, text=t("ideas.base_none"),
                                     style="Card.TLabel")
        self._base_label.grid(row=0, column=0, sticky="w")
        ttk.Button(base_row, text=t("ideas.base_choose"), width=10,
                   command=self._pick_base).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(base_row, text="✕", width=2,
                   command=self._clear_base).grid(row=0, column=2, padx=(4, 0))

        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=6, column=0, sticky="w")
        self.buttons_row(body, t("common.add")).grid(row=7, column=0, sticky="ew")

    def _pick_base(self) -> None:
        SinglePickDialog(self, self.editor, self.t("ideas.base_idea"),
                         self._base_options, self._set_base, current=self._base_id)

    def _set_base(self, value: str) -> None:
        self._base_id = value
        self._base_label.configure(text=value or self.t("ideas.base_none"))

    def _clear_base(self) -> None:
        self._set_base("")

    def _submit(self) -> None:
        iid = self._id_var.get().strip()
        if not self._id_re.match(iid):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        if iid in self._taken:
            self._error.configure(text=self.t("focuses.err.duplicate_id"))
            return
        category = self._categories[self._cat.current()][0]
        base_id = self._base_id
        self.destroy()
        self._on_submit(iid, category, base_id)


class NewCategoryDialog(BaseDialog):
    """Rich category-creation form: name, cost/removal_cost, ledger, flags,
    editable slot / character-slot lists, and an optional icon."""

    def __init__(self, master, editor, on_submit, taken: set[str]):
        super().__init__(master, editor, editor.t("ideas.new_category"), (520, 640))
        self.resizable(False, True)
        self._on_submit = on_submit
        self._taken = taken
        self._icon_path = None

        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)
        t = self.t
        r = 0

        def entry_row(label_key, default=""):
            nonlocal r
            ttk.Label(body, text=t(label_key), style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=default)
            ttk.Entry(body, textvariable=var).grid(row=r, column=1, sticky="ew",
                                                   padx=(8, 0), pady=3)
            r += 1
            return var

        self._name_var = entry_row("ideas.category_id")
        self._cost = entry_row("ideas.cat.cost", "150")
        self._removal = entry_row("ideas.cat.removal_cost", "0")

        ttk.Label(body, text="ledger", style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        self._ledger = ttk.Combobox(body, state="readonly", values=list(LEDGER_VALUES),
                                    width=14)
        self._ledger.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3)
        self._ledger.set("civilian"); r += 1

        self._flags = {}
        for name in ("hidden", "politics_tab", "law", "designer", "use_list_view"):
            var = tk.BooleanVar(value=(name == "politics_tab"))
            self._flags[name] = var
            ttk.Checkbutton(body, text=t(f"ideas.cat.{name}"), style="Card.TCheckbutton",
                            variable=var).grid(row=r, column=0, columnspan=2,
                                               sticky="w"); r += 1

        self._slots = self._slot_list(body, r, "ideas.cat.slots"); r += 1
        self._cslots = self._slot_list(body, r, "ideas.cat.character_slots"); r += 1

        ttk.Label(body, text=t("ideas.cat.icon"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=(8, 3))
        zone = ImageDropZone(body, self._set_icon, prompt=t("focuses.import_icon"),
                             preview_size=(48, 33), palette=self.palette)
        zone.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=(8, 3)); r += 1

        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self.buttons_row(body, t("common.create")).grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _slot_list(self, body, row, label_key):
        frame = ttk.Frame(body, style="Card.TFrame")
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=self.t(label_key), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w")
        lb = tk.Listbox(frame, height=3, bg=self.palette.surface_alt,
                        fg=self.palette.text, relief="flat",
                        selectbackground=self.palette.accent, exportselection=False)
        lb.grid(row=1, column=0, sticky="ew")
        btns = ttk.Frame(frame, style="Card.TFrame")
        btns.grid(row=1, column=1, sticky="n", padx=(4, 0))
        entry = ttk.Entry(frame, width=20)
        entry.grid(row=2, column=0, sticky="w", pady=(2, 0))

        def add():
            v = entry.get().strip()
            if v:
                lb.insert("end", v)
                entry.delete(0, "end")

        def remove():
            for i in reversed(lb.curselection()):
                lb.delete(i)

        ttk.Button(btns, text="＋", width=3, command=add).pack(pady=1)
        ttk.Button(btns, text="－", width=3, command=remove).pack(pady=1)
        entry.bind("<Return>", lambda e: add())
        return lb

    def _set_icon(self, path) -> None:
        self._icon_path = path

    def _submit(self) -> None:
        name = self._name_var.get().strip()
        import re
        if not re.match(r"^\w+$", name):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        if name in self._taken:
            self._error.configure(text=self.t("focuses.err.duplicate_id"))
            return
        params = dict(
            name=name,
            cost=self._cost.get().strip(),
            removal_cost=self._removal.get().strip(),
            ledger=self._ledger.get().strip(),
            hidden=self._flags["hidden"].get(),
            politics_tab=self._flags["politics_tab"].get(),
            slots=tuple(self._slots.get(0, "end")),
            character_slots=tuple(self._cslots.get(0, "end")),
            law=self._flags["law"].get(),
            designer=self._flags["designer"].get(),
            use_list_view=self._flags["use_list_view"].get(),
            icon_source=self._icon_path,
        )
        self.destroy()
        self._on_submit(params)
