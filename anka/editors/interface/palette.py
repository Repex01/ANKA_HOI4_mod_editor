"""Widget palette: every ``.gui`` element type, grouped by category.

Double-click adds the widget into the currently selected container;
"place by click" arms the canvas so the next click drops the new element
into the container under the cursor.
"""
from __future__ import annotations

from tkinter import ttk

from ...core.guitypes.schema import WIDGETS

_CATEGORY_ORDER = ("containers", "graphics", "text", "controls", "layout")


class WidgetPalette(ttk.Frame):
    def __init__(self, master, tab):
        super().__init__(master, style="Card.TFrame", padding=10)
        self.tab = tab
        self.t = tab.t
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        ttk.Label(self, text=self.t("interface.palette.title"),
                  style="CardMuted.TLabel").grid(row=0, column=0, sticky="w",
                                                 pady=(0, 4))
        self._tree = ttk.Treeview(self, show="tree", selectmode="browse",
                                  height=9)
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("cat", font=("Segoe UI Semibold", 9))

        by_cat: dict[str, list] = {}
        for spec in WIDGETS.values():
            by_cat.setdefault(spec.palette_category, []).append(spec)
        for cat in _CATEGORY_ORDER:
            specs = sorted(by_cat.get(cat, []), key=lambda s: s.type_key.lower())
            if not specs:
                continue
            cid = f"c::{cat}"
            self._tree.insert("", "end", iid=cid, open=(cat != "containers"),
                              text=self.t(f"interface.palette.{cat}"),
                              tags=("cat",))
            for spec in specs:
                self._tree.insert(cid, "end", iid=f"t::{spec.type_key}",
                                  text=f"{spec.icon} {spec.type_key}")

        self._tree.bind("<Double-Button-1>", self._double)
        row = ttk.Frame(self, style="Card.TFrame")
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(row, text="➕ " + self.t("common.add"),
                   command=self._add).pack(side="left")
        ttk.Button(row, text="🎯 " + self.t("interface.palette.place"),
                   command=self._place).pack(side="left", padx=(6, 0))

    def _selected_type(self) -> str | None:
        sel = self._tree.selection()
        if sel and sel[0].startswith("t::"):
            return sel[0][3:]
        return None

    def _double(self, event) -> str | None:
        iid = self._tree.identify_row(event.y)
        if iid.startswith("t::"):
            self.tab.palette_add(iid[3:])
            return "break"
        return None

    def _add(self) -> None:
        type_key = self._selected_type()
        if type_key:
            self.tab.palette_add(type_key)

    def _place(self) -> None:
        type_key = self._selected_type()
        if type_key:
            self.tab.palette_place(type_key)
