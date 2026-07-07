"""Map editor dialogs."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..common.dialogs import BaseDialog

PROVINCE_TYPES = ("land", "sea", "lake")


class NewProvinceDialog(BaseDialog):
    """Definition fields for a brand-new province (id and color are allocated
    by the service). Defaults inherit from `like` (the selected province)."""

    def __init__(self, master, editor, on_submit: Callable[[dict], None],
                 like=None):
        super().__init__(master, editor, editor.t("map.new_province"), (400, 280))
        self._on_submit = on_submit

        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text=self.t("map.type"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=3)
        self._type_var = tk.StringVar(value=like.type if like else "land")
        type_combo = ttk.Combobox(body, textvariable=self._type_var,
                                  state="readonly", values=list(PROVINCE_TYPES),
                                  width=10)
        type_combo.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        type_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_terrains())

        ttk.Label(body, text=self.t("map.terrain"), style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=3)
        self._terrain_var = tk.StringVar()
        self._terrain_combo = ttk.Combobox(body, textvariable=self._terrain_var,
                                           state="readonly", width=18)
        self._terrain_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)

        self._coastal_var = tk.BooleanVar(value=like.coastal if like else False)
        ttk.Checkbutton(body, text=self.t("map.coastal"), style="Card.TCheckbutton",
                        variable=self._coastal_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=3)

        ttk.Label(body, text=self.t("map.continent"), style="CardMuted.TLabel").grid(
            row=3, column=0, sticky="w", pady=3)
        self._cont_options = editor.continent_options()
        self._cont_var = tk.StringVar()
        cont_combo = ttk.Combobox(body, textvariable=self._cont_var,
                                  state="readonly", width=18,
                                  values=[lbl for lbl, _v in self._cont_options])
        cont_combo.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=3)
        want = like.continent if like else 1
        current = next((lbl for lbl, v in self._cont_options if v == want),
                       self._cont_options[0][0])
        self._cont_var.set(current)

        ttk.Label(body, text=self.t("map.new_province_hint"),
                  style="CardMuted.TLabel", wraplength=340,
                  justify="left").grid(row=4, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))

        self._like_terrain = like.terrain if like else None
        self._sync_terrains()
        self.buttons_row(body, self.t("common.create")).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _sync_terrains(self) -> None:
        editor = self.editor
        if self._type_var.get() == "land":
            values = editor.land_terrains()
        else:
            values = editor.water_terrains()
        self._terrain_combo.configure(values=values)
        if self._like_terrain in values:
            self._terrain_var.set(self._like_terrain)
        elif values:
            self._terrain_var.set(values[0])

    def _submit(self) -> None:
        continent = next((v for lbl, v in self._cont_options
                          if lbl == self._cont_var.get()), 0)
        fields = {
            "type": self._type_var.get(),
            "terrain": self._terrain_var.get(),
            "coastal": self._coastal_var.get(),
            "continent": continent,
        }
        self.destroy()
        self._on_submit(fields)
