"""Traits editor — country-leader / advisor traits and unit-leader (general /
admiral) traits, the two ``leader_traits = { ... }`` families.

Layout mirrors the Ideas / On-actions editors: a collapsible family → trait
tree (left) · a family-aware trait inspector (center) · a collapsible problems
panel (bottom). The two families live in different folders and have very
different shapes, so each gets its own inspector; the tree keeps them under two
root nodes.

Content is merged across the layers (base game → dependency mods → edited mod),
so submods work: a dependency's traits show read-only, and the edited mod
overrides files of the same name. Vanilla base-game traits are opt-in (there are
~2000 of them) and always searchable; "copy to mod" clones a read-only file for
editing.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...services.trait_service import (
    COUNTRY_FAMILY,
    UNIT_FAMILY,
    TraitDef,
    TraitService,
)
from ..base import EditorModule, EditorRegistry
from ..common import TextPromptDialog
from .inspector import CountryTraitInspector, UnitTraitInspector

_FAMILIES = (COUNTRY_FAMILY, UNIT_FAMILY)


@EditorRegistry.register
class TraitsEditor(EditorModule):
    id = "traits"
    name_key = "editors.traits.name"
    desc_key = "editors.traits.desc"
    order = 45

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = TraitService(context)
        self.resolver = context.sprites
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._resolver_ready = context.warm_sprites()
        self._mod_docs: dict[str, list] = {f: [] for f in _FAMILIES}
        self._vanilla_refs: dict[str, list] = {f: [] for f in _FAMILIES}
        self._dirty: set = set()
        self._items: dict[str, tuple] = {}
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self._trait_cache: dict[str, list[str]] = {}

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
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
        self._insp_host = ttk.Frame(center, style="TFrame")
        self._insp_host.grid(row=0, column=0, sticky="nsew")
        self._insp_host.rowconfigure(0, weight=1)
        self._insp_host.columnconfigure(0, weight=1)
        self.country_inspector = CountryTraitInspector(self._insp_host, self)
        self.country_inspector.grid(row=0, column=0, sticky="nsew")
        self.unit_inspector = UnitTraitInspector(self._insp_host, self)
        self.unit_inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(self._insp_host,
                                      text=self.t("traits.select_hint"),
                                      style="Muted.TLabel")
        self._placeholder.grid(row=0, column=0)
        self._show_inspector(None)

        self._build_problems(center)
        self.reload_tree()
        return root

    def resolver_ready(self) -> bool:
        return self._resolver_ready.is_set()

    def _build_toolbar(self, root) -> None:
        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("traits.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("traits.new_country"),
                   command=lambda: self._new_trait(COUNTRY_FAMILY)).pack(
            side="left", padx=(4, 2))
        ttk.Button(bar, text="➕ " + self.t("traits.new_unit"),
                   command=lambda: self._new_trait(UNIT_FAMILY)).pack(side="left")
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
        root.columnconfigure(0, minsize=300)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("traits.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self.reload_tree).grid(row=1, column=0, sticky="w",
                                                       pady=(0, 6))
        self._tree = ttk.Treeview(panel, show="tree", selectmode="browse")
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.tag_configure("family", font=("Segoe UI Semibold", 10))
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
        self._problems.heading("subject", text=self.t("traits.col.trait"))
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
        self._trait_cache.clear()
        for family in _FAMILIES:
            self._mod_docs[family] = []
            for ref in self.service.list_docs(family, include_vanilla=False):
                try:
                    self._mod_docs[family].append(self.service.load(ref))
                except Exception:
                    continue
            self._vanilla_refs[family] = (
                [r for r in self.service.list_docs(family, include_vanilla=True)
                 if r.is_vanilla] if self._vanilla.get() else [])
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    def _refresh_tree(self) -> None:
        query = self._search.get().strip().lower()
        open_fams = {iid for iid in self._tree.get_children("")
                     if self._tree.item(iid, "open")}
        self._tree.delete(*self._tree.get_children())
        self._items.clear()

        for family in _FAMILIES:
            # (payload, id, is_vanilla), mod first then vanilla
            rows: list[tuple] = []
            seen: set[str] = set()
            for doc in self._mod_docs[family]:
                # dependency files are listed here too (read-only, is_vanilla).
                is_ro = doc.ref.is_vanilla
                for trait in doc.traits():
                    rows.append((("trait", family, doc.ref, trait.id), trait.id, is_ro))
                    seen.add(trait.id)
            for ref in self._vanilla_refs[family]:
                for tid in ref.names:
                    if tid in seen:
                        continue
                    rows.append((("trait", family, ref, tid), tid, True))

            shown = rows
            if query:
                shown = [r for r in rows if query in r[1].lower()
                         or query in self._label_for(family, r[1]).lower()]
                if not shown:
                    continue
            fam_iid = f"fam::{family}"
            label = f"{self.t('traits.family.' + family)}  ·  {len(rows)}"
            self._tree.insert("", "end", iid=fam_iid, text=label,
                              open=bool(query) or fam_iid in open_fams
                              or not open_fams,
                              tags=("family",))
            self._items[fam_iid] = ("family", family)
            for payload, tid, is_vanilla in shown:
                iid = f"t::{family}::{payload[2].rel_file}::{tid}"
                if self._tree.exists(iid):
                    continue
                name = self._label_for(family, tid)
                text = tid if name == tid else f"{tid}  ·  {name}"
                self._tree.insert(fam_iid, "end", iid=iid, text=text,
                                  tags=("vanilla",) if is_vanilla else ())
                self._items[iid] = payload

    def _label_for(self, family: str, trait_id: str) -> str:
        loc_key = trait_id if family == COUNTRY_FAMILY else f"trait_{trait_id}"
        return self.service.name_of(loc_key, self.loc_language)

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
        if payload[0] == "family":
            self._show_inspector(None)
            self._set_copy_btn(None)
            return
        _kind, family, ref, tid = payload
        doc = self.service.load(ref)
        trait = doc.find(tid)
        if trait is None:
            self._show_inspector(None)
            return
        editable = not ref.is_vanilla
        self._show_inspector(family)
        insp = self._inspector_for(family)
        insp.show(doc, trait, editable)
        self._set_copy_btn(ref if ref.is_vanilla else None)

    def _inspector_for(self, family: str):
        return (self.country_inspector if family == COUNTRY_FAMILY
                else self.unit_inspector)

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    def _show_inspector(self, family: str | None) -> None:
        self.country_inspector.grid_remove()
        self.unit_inspector.grid_remove()
        self._placeholder.grid_remove()
        if family == COUNTRY_FAMILY:
            self.country_inspector.grid()
        elif family == UNIT_FAMILY:
            self.unit_inspector.grid()
        else:
            self._placeholder.grid()

    def _flush_inspectors(self) -> None:
        self.country_inspector.flush_pending()
        self.unit_inspector.flush_pending()

    # ------------------------------------------------------------------ actions
    def _new_trait(self, family: str) -> None:
        taken = self.known_ids(family)

        def submit(trait_id: str) -> None:
            doc = self.service.mod_target_doc(family)
            self.service.add_trait(doc, trait_id)
            if all(d.ref.path != doc.ref.path for d in self._mod_docs[family]):
                self._mod_docs[family].append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            node = f"t::{family}::{doc.ref.rel_file}::{trait_id}"
            if self._tree.exists(node):
                self._tree.selection_set(node)
                self._tree.see(node)

        TextPromptDialog(self._tree, self, self.t("traits.new_" + (
            "country" if family == COUNTRY_FAMILY else "unit")),
            self.t("traits.trait_id"), submit, taken=taken)

    def _copy_to_mod(self) -> None:
        ref = getattr(self, "_copy_ref", None)
        if ref is None:
            return
        selected = self._tree.selection()
        new_ref = self.service.copy_to_mod(ref)
        self.reload_tree()
        if selected and selected[0].startswith("t::"):
            tid = selected[0].split("::")[-1]
            node = f"t::{ref.family}::{new_ref.rel_file}::{tid}"
            if self._tree.exists(node):
                self._tree.selection_set(node)
                self._tree.see(node)

    def rename_trait(self, trait: TraitDef, doc, new_id: str) -> None:
        if new_id in self.known_ids(trait.family):
            messagebox.showerror("ANKA", self.t("focuses.err.duplicate_id"))
            return
        self.service.rename_trait(trait, new_id)
        self.mark_dirty(doc)
        self.save_all()
        self.reload_tree()
        node = f"t::{trait.family}::{doc.ref.rel_file}::{new_id}"
        if self._tree.exists(node):
            self._tree.selection_set(node)
            self._tree.see(node)

    def duplicate_trait(self, trait: TraitDef, doc) -> None:
        if doc.ref.is_vanilla:
            return
        taken = self.known_ids(trait.family)
        new_id = trait.id + "_copy"
        while new_id in taken:
            new_id += "_copy"
        old_loc = trait.loc_key
        new_trait = self.service.duplicate_trait(doc, trait, new_id)
        for lang in ("english", self.loc_language):
            name = self.service.loc.get(old_loc, lang)
            desc = self.service.loc.get(f"{old_loc}_desc", lang)
            if name is not None or desc is not None:
                self.service.set_loc(new_trait.loc_key, lang, name, desc)
        self.mark_dirty(doc)
        self.save_all()
        self.reload_tree()
        node = f"t::{trait.family}::{doc.ref.rel_file}::{new_id}"
        if self._tree.exists(node):
            self._tree.selection_set(node)
            self._tree.see(node)

    def delete_trait(self, trait: TraitDef, doc) -> None:
        if doc.ref.is_vanilla:
            return
        if not messagebox.askyesno("ANKA", self.t("traits.confirm_delete",
                                                  name=trait.id)):
            return
        family = trait.family
        self.service.remove_trait(doc, trait)
        self.mark_dirty(doc)
        self.save_all()
        self._show_inspector(None)
        self.reload_tree()
        fam_iid = f"fam::{family}"
        if self._tree.exists(fam_iid):
            self._tree.selection_set(fam_iid)

    def _delete_selected(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None or payload[0] != "trait":
            return
        _kind, _family, ref, tid = payload
        if ref.is_vanilla:
            return
        doc = self.service.load(ref)
        trait = doc.find(tid)
        if trait is not None:
            self.delete_trait(trait, doc)

    # ----------------------------------------------------------- shared protocol
    def known_ids(self, family: str) -> set[str]:
        ids = {t.id for doc in self._mod_docs[family] for t in doc.traits()}
        for ref in self._vanilla_refs[family]:
            ids.update(ref.names)
        ids |= self.service.known_ids(family)
        return ids

    def trait_options(self, family: str) -> list[str]:
        cached = self._trait_cache.get(family)
        if cached is None:
            cached = sorted(self.service.known_ids(family))
            self._trait_cache[family] = cached
        return cached

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
        docs = [d for f in _FAMILIES for d in self._mod_docs[f]]
        issues = self.service.validate(docs, language=self.loc_language)
        self._problems.delete(*self._problems.get_children())
        self._problem_index = {}
        errors = 0
        for i, issue in enumerate(sorted(issues, key=lambda i: i.severity)):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"traits.issue.{issue.code}", detail=issue.detail)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error" else "⚠",
                                          issue.subject, msg),
                                  tags=(issue.severity,))
            self._problem_index[str(i)] = issue
        n = len(issues)
        label = f"⚠ {n}" if not errors else f"⛔ {errors} · ⚠ {n - errors}"
        self._btn_problems.configure(text=label)

    def _on_problem_select(self, _event=None) -> None:
        sel = self._problems.selection()
        if not sel:
            return
        issue = getattr(self, "_problem_index", {}).get(sel[0])
        if issue is None or not issue.rel_file:
            return
        for family in _FAMILIES:
            node = f"t::{family}::{issue.rel_file}::{issue.subject}"
            if self._tree.exists(node):
                self._tree.see(node)
                self._tree.selection_set(node)
                return

    # ------------------------------------------------------------------ saving
    def mark_dirty(self, doc) -> None:
        if doc is not None:
            self._dirty.add(str(doc.ref.path))

    def save_all(self) -> None:
        self._flush_inspectors()
        for family in _FAMILIES:
            for doc in self._mod_docs[family]:
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
