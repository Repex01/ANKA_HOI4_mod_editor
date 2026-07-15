"""Hierarchy panel: ``.gui`` files → windows → element tree.

Mod files are listed eagerly; vanilla files (opt-in, dimmed) expand lazily —
453 vanilla documents are only parsed when opened. Selection, reorder,
add/delete/duplicate intents go to the tab through callbacks; payloads mirror
the established ``(kind, ...)`` tuples of other ANKA editors.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

IndexPath = tuple[int, ...]

_LAZY = "::lazy::"


def _path_str(path: IndexPath) -> str:
    return ".".join(str(i) for i in path)


class HierarchyPanel(ttk.Frame):
    def __init__(self, master, tab, *,
                 on_select: Callable[[object], None],
                 on_context: Callable[[tk.Event, object], None],
                 on_multi_select: Callable[[object, int, list], None] | None = None):
        super().__init__(master, style="Card.TFrame", padding=10)
        self.tab = tab
        self.t = tab.t
        self.on_select = on_select
        self.on_context = on_context
        self.on_multi_select = on_multi_select
        self.items: dict[str, tuple] = {}
        self._suppress = False          # guard against feedback while syncing

        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(self, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text=self.t("interface.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self.tab.reload_docs).grid(
            row=1, column=0, sticky="w", pady=(0, 6))
        self._tree = ttk.Treeview(self, show="tree", selectmode="extended")
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla",
                                 foreground=self.tab.palette.text_muted)
        self._tree.tag_configure("file", font=("Segoe UI Semibold", 10))
        self._tree.bind("<<TreeviewSelect>>", self._select)
        self._tree.bind("<<TreeviewOpen>>", self._lazy_open)
        self._tree.bind("<Button-3>", self._context)
        self._tree.bind("<Delete>", self._delete_key)

    @property
    def show_vanilla(self) -> bool:
        return self._vanilla.get()

    # ---------------------------------------------------------------- content
    def refresh(self) -> None:
        """Rebuild from the tab's doc lists, keeping open/selected state."""
        tree = self._tree
        open_iids = {iid for iid in self._all_iids() if tree.item(iid, "open")}
        selected = tree.selection()
        tree.delete(*tree.get_children())
        self.items.clear()
        query = self._search.get().strip().lower()

        for doc in self.tab.mod_docs:
            self._add_file(doc.ref, doc, False, open_iids, query)
        for ref in self.tab.vanilla_refs:
            self._add_file(ref, None, True, open_iids, query)

        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])
            tree.see(selected[0])

    def _all_iids(self, parent: str = "") -> list[str]:
        out = []
        for iid in self._tree.get_children(parent):
            out.append(iid)
            out.extend(self._all_iids(iid))
        return out

    def _matches(self, ref, query: str) -> bool:
        if not query:
            return True
        if query in ref.rel_file.lower():
            return True
        return any(query in n.lower() for n in ref.names)

    def _add_file(self, ref, doc, is_vanilla: bool, open_iids: set[str],
                  query: str) -> None:
        if not self._matches(ref, query):
            return
        iid = f"f::{ref.rel_file}::{int(is_vanilla)}"
        label = ref.rel_file[len("interface/"):] \
            if ref.rel_file.startswith("interface/") else ref.rel_file
        tags = ("file",) + (("vanilla",) if is_vanilla else ())
        self._tree.insert("", "end", iid=iid, text=label,
                          open=iid in open_iids or bool(query), tags=tags)
        self.items[iid] = ("file", ref, doc)
        if doc is None:
            # vanilla: lazy — a dummy child shows the expander
            if ref.names:
                self._tree.insert(iid, "end", iid=iid + _LAZY, text="…")
        else:
            self._fill_windows(iid, doc, is_vanilla, open_iids, query)

    def _fill_windows(self, file_iid: str, doc, is_vanilla: bool,
                      open_iids: set[str], query: str) -> None:
        for wi, window in enumerate(doc.windows()):
            name = window.name or f"<{window.type_key}>"
            if query and query not in name.lower() \
                    and query not in doc.ref.rel_file.lower():
                continue
            wiid = f"w::{doc.ref.rel_file}::{int(is_vanilla)}::{wi}"
            tags = ("vanilla",) if is_vanilla else ()
            self._tree.insert(file_iid, "end", iid=wiid,
                              text=f"▣ {name}",
                              open=wiid in open_iids, tags=tags)
            self.items[wiid] = ("window", doc, wi)
            self._fill_nodes(wiid, doc, wi, window, (), is_vanilla, open_iids)

    def _fill_nodes(self, parent_iid: str, doc, wi: int, node, path: IndexPath,
                    is_vanilla: bool, open_iids: set[str]) -> None:
        for i, child in enumerate(node.children()):
            cpath = path + (i,)
            glyph = child.spec.icon if child.spec else "▢"
            label = f"{glyph} {child.name or child.type_key}"
            ciid = (f"n::{doc.ref.rel_file}::{int(is_vanilla)}::{wi}"
                    f"::{_path_str(cpath)}")
            tags = ("vanilla",) if is_vanilla else ()
            self._tree.insert(parent_iid, "end", iid=ciid, text=label,
                              open=ciid in open_iids, tags=tags)
            self.items[ciid] = ("node", doc, wi, cpath)
            self._fill_nodes(ciid, doc, wi, child, cpath, is_vanilla,
                             open_iids)

    # ---------------------------------------------------------------- events
    def _lazy_open(self, _event=None) -> None:
        iid = self._tree.focus()
        payload = self.items.get(iid)
        if payload is None or payload[0] != "file" or payload[2] is not None:
            return
        lazy_iid = iid + _LAZY
        if not self._tree.exists(lazy_iid):
            return
        self._tree.delete(lazy_iid)
        ref = payload[1]
        doc = self.tab.load_doc(ref)
        if doc is None:
            return
        self.items[iid] = ("file", ref, doc)
        self._fill_windows(iid, doc, True, set(), "")

    def _select(self, _event=None) -> None:
        if self._suppress:              # programmatic sync from the canvas
            return
        sel = self._tree.selection()
        if not sel:
            return
        # Multiple element nodes of the same window → group selection.
        nodes = [self.items.get(iid) for iid in sel]
        nodes = [p for p in nodes if p is not None and p[0] == "node"]
        if (self.on_multi_select is not None and len(sel) > 1
                and len(nodes) == len(sel)):
            doc, wi = nodes[0][1], nodes[0][2]
            if all(p[1] is doc and p[2] == wi for p in nodes):
                self.on_multi_select(doc, wi, [p[3] for p in nodes])
                return
        payload = self.items.get(self._tree.focus() or sel[0]) \
            or self.items.get(sel[0])
        if payload is not None:
            self.on_select(payload)

    def _context(self, event) -> None:
        iid = self._tree.identify_row(event.y)
        if iid:
            self._tree.selection_set(iid)
            payload = self.items.get(iid)
            if payload is not None:
                self.on_context(event, payload)

    def _delete_key(self, _event=None) -> None:
        paths = [p[3] for iid in self._tree.selection()
                 if (p := self.items.get(iid)) is not None and p[0] == "node"]
        if paths:
            self.tab.delete_nodes(paths)
            return
        windows = [(p[1], p[2]) for iid in self._tree.selection()
                   if (p := self.items.get(iid)) is not None
                   and p[0] == "window"]
        if windows:
            doc, wi = windows[0]
            self.tab.delete_window(doc, wi)

    # ------------------------------------------------------------- selection
    def select_node(self, doc, wi: int, path: IndexPath | None) -> None:
        is_vanilla = int(doc.ref.is_vanilla)
        if path is None:
            iid = f"w::{doc.ref.rel_file}::{is_vanilla}::{wi}"
        else:
            iid = (f"n::{doc.ref.rel_file}::{is_vanilla}::{wi}"
                   f"::{_path_str(path)}")
        if self._tree.exists(iid):
            self._suppress = True
            try:
                self._tree.selection_set(iid)
                self._tree.see(iid)
            finally:
                self._suppress = False

    def select_nodes(self, doc, wi: int, paths: list[IndexPath]) -> None:
        """Mirror the canvas selection in the tree (single or multiple nodes)."""
        is_vanilla = int(doc.ref.is_vanilla)
        iids = [iid for path in paths
                if self._tree.exists(
                    iid := f"n::{doc.ref.rel_file}::{is_vanilla}::{wi}"
                           f"::{_path_str(path)}")]
        self._suppress = True
        try:
            self._tree.selection_set(iids)      # empty list clears the selection
            if iids:
                self._tree.see(iids[0])
        finally:
            self._suppress = False
