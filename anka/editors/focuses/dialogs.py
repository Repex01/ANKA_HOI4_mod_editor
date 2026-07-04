"""Focus-specific modal dialogs. Shared dialogs live in ``editors/common``and are
re-exported here so existing imports inside the focuses package keep working."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ...core.pdx import Block, Pair
from ..common.dialogs import (  # noqa: F401 — re-exported for package-local imports
    BaseDialog,
    IconPickerDialog,
    MultiPickDialog,
    PdxPreviewDialog,
    SinglePickDialog,
    TextPromptDialog,
)

_ID_RE = re.compile(r"^\w+$")


class FocusPreviewDialog(PdxPreviewDialog):
    """Serialized view of one focus with copy-to-clipboard."""

    def __init__(self, master, editor, focus):
        super().__init__(master, editor,
                         editor.t("focuses.preview_title", id=focus.id),
                         Block([Pair(focus.kind, focus.block)]))


class NewTreeDialog(BaseDialog):
    """Create a new focus-tree file: file name, tree id, country tag, default flag."""

    def __init__(self, master, editor, tags: list[str],
                 on_submit: Callable[[str, str, str, bool], None]):
        super().__init__(master, editor, editor.t("focuses.new_tree"), (460, 300))
        self._on_submit = on_submit

        body = ttk.Frame(self, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)

        def row(r: int, key: str) -> tk.StringVar:
            ttk.Label(body, text=self.t(key), style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            ttk.Entry(body, textvariable=var).grid(row=r, column=1, sticky="ew",
                                                   padx=(10, 0), pady=4)
            return var

        self._tree_id = row(0, "focuses.tree_id")
        self._file = row(1, "focuses.file_name")

        ttk.Label(body, text=self.t("focuses.country_tag"), style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=4)
        self._all_tags = tags
        self._tag = ttk.Combobox(body, values=tags)
        self._tag.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=4)
        self._tag.bind("<KeyRelease>", self._filter_tags)

        self._default = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text=self.t("focuses.default_tree"), style="Card.TCheckbutton",
                        variable=self._default).grid(row=3, column=0, columnspan=2,
                                                     sticky="w", pady=(8, 4))
        ttk.Label(body, text=self.t("focuses.new_tree_hint"), style="CardMuted.TLabel",
                  wraplength=390, justify="left").grid(row=4, column=0, columnspan=2,
                                                       sticky="w", pady=(2, 8))
        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=5, column=0, columnspan=2, sticky="w")
        self.buttons_row(body, self.t("common.add")).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _filter_tags(self, event) -> None:
        """Type-to-search: narrow the dropdown to tags starting with the typed text."""
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = self._tag.get().strip().upper()
        matches = ([t for t in self._all_tags if t.startswith(typed)]
                   if typed else self._all_tags)
        self._tag.configure(values=matches or self._all_tags)

    def _submit(self) -> None:
        tree_id = self._tree_id.get().strip()
        file_name = self._file.get().strip() or tree_id
        tag = self._tag.get().strip().upper()
        if not _ID_RE.match(tree_id):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        self.destroy()
        self._on_submit(file_name, tree_id, tag, self._default.get())


class TreePropertiesDialog(BaseDialog):
    """Edit focus_tree-level settings + attached shared_focus references."""

    def __init__(self, master, editor, doc, shared_ids: list[str],
                 on_change: Callable[[], None]):
        super().__init__(master, editor, editor.t("focuses.tree_props"), (520, 560))
        self.doc = doc
        self._shared_ids = shared_ids
        self._on_change = on_change
        tree = doc.tree

        body = ttk.Frame(self, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(7, weight=1)

        ttk.Label(body, text=self.t("focuses.tree_id"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=3)
        self._id = tk.StringVar(value=tree.id)
        ttk.Entry(body, textvariable=self._id).grid(row=0, column=1, sticky="ew",
                                                    padx=(10, 0), pady=3)

        self._default = tk.BooleanVar(value=tree.default)
        ttk.Checkbutton(body, text=self.t("focuses.default_tree"),
                        style="Card.TCheckbutton", variable=self._default).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=3)
        self._reset = tk.BooleanVar(value=tree.reset_on_civilwar)
        ttk.Checkbutton(body, text=self.t("focuses.reset_on_civilwar"),
                        style="Card.TCheckbutton", variable=self._reset).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=3)

        self._cont = self._xy_row(body, 3, "focuses.continuous_pos",
                                  tree.continuous_focus_position)
        self._show = self._xy_row(body, 4, "focuses.initial_pos",
                                  tree.initial_show_position)

        ttk.Label(body, text=self.t("focuses.country_script"),
                  style="CardMuted.TLabel").grid(row=5, column=0, sticky="nw", pady=(10, 3))
        self._country = tk.Text(body, height=6, bg=self.palette.surface_alt,
                                fg=self.palette.text, insertbackground=self.palette.text,
                                relief="flat", font=("Consolas", 10))
        self._country.grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=(10, 3))
        self._country.insert("1.0", tree.get_country_script())

        ttk.Label(body, text=self.t("focuses.shared_refs"),
                  style="CardMuted.TLabel").grid(row=6, column=0, sticky="nw", pady=(10, 3))
        shared_frame = ttk.Frame(body, style="Card.TFrame")
        shared_frame.grid(row=6, column=1, rowspan=2, sticky="nsew", padx=(10, 0), pady=(10, 3))
        shared_frame.columnconfigure(0, weight=1)
        self._shared_list = tk.Listbox(shared_frame, height=6, bg=self.palette.surface_alt,
                                       fg=self.palette.text, relief="flat",
                                       selectbackground=self.palette.accent)
        self._shared_list.grid(row=0, column=0, sticky="nsew")
        btns = ttk.Frame(shared_frame, style="Card.TFrame")
        btns.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        ttk.Button(btns, text="➕", width=3, command=self._add_shared).pack(pady=2)
        ttk.Button(btns, text="➖", width=3, command=self._remove_shared).pack(pady=2)
        for ref in doc.tree.shared_focus_refs:
            self._shared_list.insert("end", ref)

        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=8, column=0, columnspan=2, sticky="w")
        self.buttons_row(body).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _xy_row(self, body, r: int, key: str,
                value: tuple[int, int] | None) -> tuple[tk.StringVar, tk.StringVar]:
        ttk.Label(body, text=self.t(key), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        frame = ttk.Frame(body, style="Card.TFrame")
        frame.grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        vx = tk.StringVar(value="" if value is None else str(value[0]))
        vy = tk.StringVar(value="" if value is None else str(value[1]))
        ttk.Label(frame, text="x", style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(frame, textvariable=vx, width=7).pack(side="left", padx=(2, 8))
        ttk.Label(frame, text="y", style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(frame, textvariable=vy, width=7).pack(side="left", padx=2)
        return vx, vy

    def _add_shared(self) -> None:
        existing = set(self._shared_list.get(0, "end"))
        items = [s for s in self._shared_ids if s not in existing]
        MultiPickDialog(self, self.editor, self.t("focuses.shared_refs"), items,
                        lambda picked: [self._shared_list.insert("end", p) for p in picked])

    def _remove_shared(self) -> None:
        for idx in reversed(self._shared_list.curselection()):
            self._shared_list.delete(idx)

    @staticmethod
    def _parse_xy(pair: tuple[tk.StringVar, tk.StringVar]) -> tuple[int, int] | None:
        sx, sy = pair[0].get().strip(), pair[1].get().strip()
        if not sx and not sy:
            return None
        return int(sx or 0), int(sy or 0)

    def _submit(self) -> None:
        tree = self.doc.tree
        try:
            cont = self._parse_xy(self._cont)
            show = self._parse_xy(self._show)
        except ValueError:
            self._error.configure(text=self.t("focuses.err.bad_number"))
            return
        new_id = self._id.get().strip()
        if not _ID_RE.match(new_id):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        try:
            tree.set_country_script(self._country.get("1.0", "end"))
        except Exception:
            self._error.configure(text=self.t("focuses.err.bad_script"))
            return
        tree.id = new_id
        tree.default = self._default.get()
        tree.reset_on_civilwar = self._reset.get()
        tree.continuous_focus_position = cont
        tree.initial_show_position = show
        wanted = list(self._shared_list.get(0, "end"))
        for ref in list(tree.shared_focus_refs):
            if ref not in wanted:
                tree.remove_shared_focus_ref(ref)
        for ref in wanted:
            tree.add_shared_focus_ref(ref)
        self.destroy()
        self._on_change()
