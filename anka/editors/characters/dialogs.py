"""Searchable multi-select picker (used for character traits)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class ItemPickerDialog(tk.Toplevel):
    """A themed, modal, searchable multi-select list. Calls `on_pick(selected)`."""

    def __init__(self, master, editor, title: str, items: list[str],
                 on_pick: Callable[[list[str]], None], exclude: set[str] | None = None):
        super().__init__(master)
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self._on_pick = on_pick
        self._items = [i for i in items if not exclude or i not in exclude]

        self.title(title)
        self.configure(bg=self.palette.bg)
        self.transient(master.winfo_toplevel())
        self.resizable(True, True)
        self.geometry("440x520")
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - 440) // 2
        y = master.winfo_rooty() + (master.winfo_height() - 520) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        from ...ui.widgets import guard_modal, fit_to_content
        guard_modal(self)           # keep taskbar restore working (Windows)
        self._build(title)
        fit_to_content(self, master.winfo_toplevel(), (440, 520))

    def _build(self, title: str) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(body, textvariable=self._search).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._tree = ttk.Treeview(body, columns=("v",), show="headings", selectmode="extended")
        self._tree.heading("v", text=title)
        self._tree.column("v", width=380, anchor="w")
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._submit())

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text=self.t("common.add"), style="Accent.TButton",
                   command=self._submit).pack(side="left")
        self._refresh()

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for item in self._items:
            if q and q not in item.lower():
                continue
            if shown >= 500:
                break
            self._tree.insert("", "end", iid=item, values=(item,))
            shown += 1

    def _submit(self) -> None:
        sel = list(self._tree.selection())
        if sel:
            self._on_pick(sel)
        self.destroy()


class LeaderScopeDialog(tk.Toplevel):
    """Ask whether a leader takeover applies to one ideology or to all of them.

    ``result`` is None when cancelled, otherwise "" for every ideology or the
    main-ideology key ("fascism" / "communism" / "democratic" / "neutrality").
    """

    def __init__(self, master, editor, choices, default=""):
        super().__init__(master)
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.result = None
        self._choices = choices            # [(label, key), ...]

        self.title(self.t("characters.takeover"))
        self.configure(bg=self.palette.bg)
        top = master.winfo_toplevel()
        self.transient(top)
        self.resizable(True, True)

        body = ttk.Frame(self, style="Card.TFrame", padding=18)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(body, text=self.t("characters.takeover.scope"),
                  style="CardMuted.TLabel").pack(anchor="w")

        self._var = tk.StringVar(value=next(
            (lbl for lbl, key in choices if key == default), choices[0][0]))
        ttk.Combobox(body, textvariable=self._var, state="readonly", width=30,
                     values=[lbl for lbl, _ in choices]).pack(anchor="w", pady=(4, 10))

        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(row, text=self.t("common.save"), style="Accent.TButton",
                   command=self._ok).pack(side="right")

        self.grab_set()
        from ...ui.widgets import guard_modal, fit_to_content
        guard_modal(self, top)
        self.bind("<Escape>", lambda e: self.destroy())
        fit_to_content(self, top, (420, 190))
        self.wait_window()

    def _ok(self) -> None:
        label = self._var.get()
        self.result = next((key for lbl, key in self._choices if lbl == label), "")
        self.destroy()
