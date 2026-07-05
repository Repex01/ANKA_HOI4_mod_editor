"""Events editor.

Layout: collapsible namespace→event tree (left) · inspector (center/right) ·
collapsible problems panel (bottom) — same skeleton as the Decisions editor.

The tree aggregates every mod file under ``events/`` (a namespace may gain events
from several files); the vanilla checkbox adds base-game content read-only
(quick-scanned, parsed lazily on selection) with one-click "copy into mod". Edits
mark the owning document dirty; dirty documents are saved explicitly or on leave.
"""
from __future__ import annotations

import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...core.gfx import SpriteResolver
from ...services.event_service import (
    EVENT_KIND_LETTERS,
    EVENT_KINDS,
    Event,
    EventService,
)
from ..base import EditorModule, EditorRegistry
from ..common import BaseDialog, TextPromptDialog
from .inspector import EventInspector

_NS_RE = re.compile(r"^\w+(?:\.\w+)*$")


class NewEventDialog(BaseDialog):
    """Namespace (combobox, editable) + event kind; the id is auto-assigned."""

    def __init__(self, master, editor, namespaces: list[str], default_ns: str,
                 on_submit):
        super().__init__(master, editor, editor.t("events.new_event"), (430, 230))
        self._on_submit = on_submit

        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text=self.t("events.namespace"),
                  style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self._ns = ttk.Combobox(body, values=namespaces)
        self._ns.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        if default_ns:
            self._ns.set(default_ns)
        elif namespaces:
            self._ns.current(0)
        ttk.Label(body, text=self.t("events.kind"),
                  style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self._kind = ttk.Combobox(body, state="readonly", values=list(EVENT_KINDS))
        self._kind.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        self._kind.current(0)
        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=4, column=0, sticky="w")
        self.buttons_row(body, self.t("common.add")).grid(row=5, column=0,
                                                          sticky="ew")

    def _submit(self) -> None:
        ns = self._ns.get().strip()
        if not _NS_RE.match(ns):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        # Read widget state BEFORE destroy() — the combobox dies with the dialog.
        kind = self._kind.get()
        self.destroy()
        self._on_submit(ns, kind)


@EditorRegistry.register
class EventsEditor(EditorModule):
    id = "events"
    name_key = "editors.events.name"
    desc_key = "editors.events.desc"
    order = 30

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = EventService(context)
        self.resolver = SpriteResolver.for_mod(context.mod.path, context.game_path)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._resolver_ready = threading.Event()
        self._mod_docs: list = []               # parsed mod event documents
        self._dirty: set = set()                # document ids (paths)
        self._items: dict[str, tuple] = {}      # tree iid -> payload
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self.selected_namespace = ""

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
        self.inspector = EventInspector(self._insp_host, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(self._insp_host,
                                      text=self.t("events.select_hint"),
                                      style="Muted.TLabel", justify="center")
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
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("events.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("events.new_event"),
                   command=self._new_event).pack(side="left", padx=4)
        ttk.Button(bar, text="🗂 " + self.t("events.new_namespace"),
                   command=self._new_namespace).pack(side="left", padx=2)
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
        ttk.Checkbutton(panel, text=self.t("events.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self.reload_tree).grid(row=1, column=0, sticky="w",
                                                       pady=(0, 6))
        self._tree = ttk.Treeview(panel, show="tree", selectmode="browse")
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.tag_configure("namespace", font=("Segoe UI Semibold", 10))
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
        self._problems.heading("subject", text=self.t("events.col.event"))
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
        self._issue_targets: dict[str, str] = {}    # problems iid -> tree iid

    # ---------------------------------------------------------------- toggles
    def _toggle_list(self) -> None:
        self._list_visible = not self._list_visible
        if self._list_visible:
            self._grid_root.columnconfigure(0, minsize=300)
            self._list_panel.grid()
        else:
            # Drop the reserved column width too, or a 300px dead strip stays and
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
        self._mod_docs = [self.service.load(ref)
                          for ref in self.service.list_docs(False)]
        self._vanilla_refs = ([r for r in self.service.list_docs(True)
                               if r.is_vanilla] if self._vanilla.get() else [])
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    def _event_label(self, eid: str, kind: str, title_key: str) -> str:
        label = f"{eid} · [{EVENT_KIND_LETTERS.get(kind, '?')}]"
        loc = self.loc_get(title_key, self.loc_language) if title_key else ""
        return f"{label} · {loc}" if loc else label

    def _refresh_tree(self) -> None:
        query = self._search.get().strip().lower()
        # Rebuilding must not collapse namespaces the user expanded.
        open_ns = {iid for iid in self._tree.get_children("")
                   if self._tree.item(iid, "open")}
        self._tree.delete(*self._tree.get_children())
        self._items.clear()

        # namespace -> [(payload, label, vanilla, sort_key)], mod first
        buckets: dict[str, list[tuple]] = {}
        for doc in self._mod_docs:
            for ns in doc.namespaces():
                buckets.setdefault(ns, [])
            for e in doc.events():
                eid = e.id
                buckets.setdefault(e.namespace, []).append(
                    (("event", doc.ref, eid),
                     self._event_label(eid, e.kind, e.first_text("title")),
                     False, _sort_key(eid)))
        for ref in getattr(self, "_vanilla_refs", []):
            for ns in ref.namespaces:
                buckets.setdefault(ns, [])
            for kind, eid in ref.events:
                ns = eid.rsplit(".", 1)[0] if "." in eid else (eid or "?")
                # vanilla events are quick-scanned: guess the conventional
                # `<id>.t` title key for the label (full parse only on select)
                buckets.setdefault(ns, []).append(
                    (("event", ref, eid),
                     self._event_label(eid, kind, f"{eid}.t"),
                     True, _sort_key(eid)))

        for ns in sorted(buckets):
            rows = buckets[ns]
            rows.sort(key=lambda row: (row[2], row[3]))
            if query:
                rows = [r for r in rows if query in r[1].lower()]
                if not rows and query not in ns.lower():
                    continue
            ns_iid = f"n::{ns}"
            self._tree.insert("", "end", iid=ns_iid, text=ns,
                              open=bool(query) or ns_iid in open_ns,
                              tags=("namespace",))
            self._items[ns_iid] = ("namespace", ns)
            for payload, label, is_vanilla, _key in rows:
                iid = f"e::{payload[1].rel_file}::{payload[2]}"
                if self._tree.exists(iid):
                    continue
                self._tree.insert(ns_iid, "end", iid=iid, text=label,
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
        self.inspector.flush_pending()
        if payload[0] == "namespace":
            self.selected_namespace = payload[1]
            self._show_inspector(False)
            self._set_copy_btn(None)
            return
        _kind, ref, eid = payload
        doc = self.service.load(ref)
        event = doc.find(eid)
        self._show_inspector(event is not None)
        if event is not None:
            self.selected_namespace = event.namespace
            self.inspector.show(doc, event, not ref.is_vanilla)
        self._set_copy_btn(ref if ref.is_vanilla else None)

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    def _show_inspector(self, visible: bool) -> None:
        if visible:
            self._placeholder.grid_remove()
            self.inspector.grid()
        else:
            self.inspector.grid_remove()
            self._placeholder.grid()

    def _select_event_iid(self, rel_file: str, eid: str) -> None:
        iid = f"e::{rel_file}::{eid}"
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)             # also expands the parent namespace

    # ------------------------------------------------------------------ actions
    def _new_namespace(self) -> None:
        taken = set(self.service.namespaces(include_vanilla=True))

        def submit(ns: str) -> None:
            doc = self.service.create_namespace(ns)
            if all(d.ref.path != doc.ref.path for d in self._mod_docs):
                self._mod_docs.append(doc)
            self.selected_namespace = ns
            self.reload_tree()
            iid = f"n::{ns}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("events.new_namespace"),
                         self.t("events.namespace_id"), submit, taken=taken)

    def _new_event(self) -> None:
        namespaces = self.service.namespaces(include_vanilla=False)

        def submit(ns: str, kind: str) -> None:
            self.save_all()                 # next_id scans files on disk
            doc = self.service.mod_target_doc(ns)
            event = self.service.create_event(doc, ns, kind)
            if all(d.ref.path != doc.ref.path for d in self._mod_docs):
                self._mod_docs.append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.selected_namespace = ns
            self.reload_tree()
            self._select_event_iid(doc.ref.rel_file, event.id)

        NewEventDialog(self._tree, self, namespaces,
                       self.selected_namespace or (namespaces[0] if namespaces
                                                   else ""), submit)

    def _copy_to_mod(self) -> None:
        ref = getattr(self, "_copy_ref", None)
        if ref is None:
            return
        selected = self._tree.selection()
        new_ref = self.service.copy_to_mod(ref)
        self.reload_tree()
        if selected and selected[0].startswith("e::"):
            eid = selected[0].split("::")[-1]
            self._select_event_iid(new_ref.rel_file, eid)

    def rename_event(self, event: Event, new_id: str) -> None:
        if new_id in set(self.known_ids()):
            messagebox.showerror("ANKA", self.t("focuses.err.duplicate_id"))
            return
        doc = self.inspector.doc
        try:
            self.service.rename_event(event, new_id)
        except ValueError:
            messagebox.showerror("ANKA", self.t("focuses.err.bad_id"))
            return
        self.mark_dirty(doc)
        self.reload_tree()
        self._select_event_iid(doc.ref.rel_file, new_id)

    def duplicate_event(self) -> None:
        insp = self.inspector
        if insp.event is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        insp.flush_pending()
        self.save_all()                     # next_id scans files on disk
        new = self.service.duplicate_event(
            insp.doc, insp.event, languages=(self.loc_language, "english"))
        self.mark_dirty(insp.doc)
        self.save_all()
        self.reload_tree()
        self._select_event_iid(insp.doc.ref.rel_file, new.id)

    def delete_event(self) -> None:
        insp = self.inspector
        if insp.event is None or insp.doc is None or insp.doc.ref.is_vanilla:
            return
        if not messagebox.askyesno("ANKA", self.t("events.confirm_delete",
                                                  name=insp.event.id)):
            return
        ns = insp.event.namespace
        self.service.remove_event(insp.doc, insp.event)
        self.mark_dirty(insp.doc)
        self._show_inspector(False)
        self.reload_tree()
        # Keep the user's context: stay on the (still expanded) namespace.
        ns_iid = f"n::{ns}"
        if self._tree.exists(ns_iid):
            self._tree.selection_set(ns_iid)
            self._tree.see(ns_iid)

    def delete_namespace(self) -> None:
        """Delete the selected namespace from the mod: its ``add_namespace``
        declarations AND every mod event under it, after explicit confirmation.
        Vanilla content is never touched."""
        ns = self.selected_namespace
        if not ns:
            return
        self.inspector.flush_pending()
        self.save_all()
        in_mod = any(ns in doc.namespaces() for doc in self._mod_docs)
        count = self.service.count_mod_events(ns)
        if not in_mod and count == 0:
            messagebox.showinfo("ANKA", self.t("events.err.vanilla_namespace"))
            return
        if not messagebox.askyesno("ANKA", self.t("events.confirm_delete_namespace",
                                                  name=ns, count=count)):
            return
        self.service.remove_namespace(ns)   # saves every touched mod file itself
        self._show_inspector(False)
        self.reload_tree()
        self._validate()

    def _delete_selected(self, _event=None) -> None:
        """Delete key in the tree: route to event/namespace deletion (both confirm)."""
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        if payload[0] == "namespace":
            self.delete_namespace()
        else:
            self.delete_event()

    def import_picture(self, path, event: Event) -> str | None:
        """Convert a user image into a 355x140 event-picture DDS + sprite."""
        name = event.id.replace(".", "_")
        try:
            dds, _gfx = self.context.icons.add_event_picture(path, name)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("focuses.err.icon", error=str(exc)))
            return None
        sprite = f"GFX_report_event_{name}"
        self.resolver.add(sprite, dds)
        return sprite

    # ----------------------------------------------------------- shared protocol
    def known_ids(self) -> list[str]:
        ids = [e.id for doc in self._mod_docs for e in doc.events()]
        for ref in getattr(self, "_vanilla_refs", []):
            ids.extend(eid for _k, eid in ref.events)
        return ids

    def loc_get(self, key: str, language: str) -> str:
        value = self.service.name_of(key, language)
        return "" if value == key else value

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.service.set_loc(key, language, text)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        if vtype == "event":
            # never cached: events are created/renamed during the session
            return self.service.event_options()
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
        self._issue_targets.clear()
        errors = 0
        for i, issue in enumerate(sorted(issues, key=lambda i: i.severity)):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"events.issue.{issue.code}", detail=issue.detail)
            iid = str(i)
            self._problems.insert("", "end", iid=iid,
                                  values=("⛔" if issue.severity == "error" else "⚠",
                                          issue.subject, msg),
                                  tags=(issue.severity,))
            if issue.rel_file and issue.subject:
                self._issue_targets[iid] = f"e::{issue.rel_file}::{issue.subject}"
        label = f"⚠ {len(issues)}" if not errors else f"⛔ {errors} · ⚠ {len(issues) - errors}"
        self._btn_problems.configure(text=label)

    def _on_problem_select(self, _event=None) -> None:
        sel = self._problems.selection()
        if not sel:
            return
        target = self._issue_targets.get(sel[0])
        if target and self._tree.exists(target):
            self._tree.selection_set(target)
            self._tree.see(target)

    # ------------------------------------------------------------------ saving
    def mark_dirty(self, doc) -> None:
        if doc is not None:
            self._dirty.add(doc.ref.path)

    def save_all(self) -> None:
        self.inspector.flush_pending()
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


def _sort_key(eid: str):
    """Events inside a namespace sort numerically by the id suffix."""
    suffix = eid.rsplit(".", 1)[-1]
    return (0, int(suffix)) if suffix.isdigit() else (1, suffix)
