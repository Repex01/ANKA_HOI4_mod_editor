"""Map editor dialogs."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

import numpy as np
from PIL import Image, ImageTk

from ..common.dialogs import BaseDialog

PROVINCE_TYPES = ("land", "sea", "lake")
_SPLIT_SOFT_CAP = 400_000        # px; larger selections need a confirmation


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


class SplitProvinceDialog(BaseDialog):
    """Split one province into K generated provinces (phase 4b): parameters,
    background generation with the ported cluster-growth algorithm, a preview
    in the exact future colors, then apply."""

    PREVIEW = (460, 300)

    def __init__(self, master, editor, pid: int,
                 on_apply: Callable[[int, np.ndarray, tuple, list], None]):
        super().__init__(master, editor,
                         editor.t("map.split_title", id=pid), (520, 520))
        self.resizable(True, True)
        self._pid = pid
        self._on_apply = on_apply
        self._labels = None
        self._bbox = None
        self._colors: list[tuple[int, int, int]] = []
        self._photo: ImageTk.PhotoImage | None = None
        self._state = "idle"          # idle | working | done

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(3, weight=1)

        row0 = ttk.Frame(body, style="Card.TFrame")
        row0.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(row0, text="K:", style="Card.TLabel").pack(side="left")
        self._k_var = tk.IntVar(value=4)
        ttk.Spinbox(row0, from_=2, to=64, width=4,
                    textvariable=self._k_var).pack(side="left", padx=(4, 12))
        ttk.Label(row0, text="seed:", style="Card.TLabel").pack(side="left")
        self._seed_var = tk.StringVar(value="0")
        ttk.Entry(row0, textvariable=self._seed_var, width=8).pack(
            side="left", padx=(4, 12))

        row1 = ttk.Frame(body, style="Card.TFrame")
        row1.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(row1, text=self.t("map.split_strategy"),
                  style="Card.TLabel").pack(side="left")
        self._strategy_var = tk.StringVar(value=self.t("map.split_organic"))
        self._strategies = {self.t("map.split_organic"): "organic",
                            self.t("map.split_even"): "smooth"}
        ttk.Combobox(row1, textvariable=self._strategy_var, state="readonly",
                     width=16, values=list(self._strategies)).pack(
            side="left", padx=(4, 12))
        ttk.Label(row1, text=self.t("map.split_smooth"),
                  style="Card.TLabel").pack(side="left")
        self._smooth_var = tk.IntVar(value=2)
        ttk.Spinbox(row1, from_=0, to=6, width=4,
                    textvariable=self._smooth_var).pack(side="left", padx=4)

        self._progress = ttk.Label(body, text="", style="CardMuted.TLabel")
        self._progress.grid(row=2, column=0, columnspan=2, sticky="w",
                            pady=(6, 2))

        self._canvas = tk.Canvas(body, width=self.PREVIEW[0],
                                 height=self.PREVIEW[1],
                                 bg=self.palette.surface_alt,
                                 highlightthickness=0, bd=0)
        self._canvas.grid(row=3, column=0, columnspan=2, sticky="nsew")

        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(bar, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=(6, 0))
        self._apply_btn = ttk.Button(bar, text=self.t("common.apply"),
                                     style="Accent.TButton", state="disabled",
                                     command=self._apply)
        self._apply_btn.pack(side="right")
        self._gen_btn = ttk.Button(bar, text="⚡ " + self.t("map.split_generate"),
                                   command=self._generate)
        self._gen_btn.pack(side="left")

    # ---------------------------------------------------------------- generate
    def _generate(self) -> None:
        if self._state == "working":
            return
        editor = self.editor
        area = editor.map.area_of(self._pid)
        if area > _SPLIT_SOFT_CAP:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "ANKA", self.t("map.split_big", count=area), parent=self):
                return
        try:
            seed = int(self._seed_var.get().strip() or "0")
        except ValueError:
            seed = 0
        k = max(2, int(self._k_var.get() or 2))
        strategy = self._strategies.get(self._strategy_var.get(), "organic")
        smooth = max(0, int(self._smooth_var.get() or 0))
        self._state = "working"
        self._apply_btn.configure(state="disabled")
        self._gen_btn.configure(state="disabled")
        self._progress.configure(text=self.t("map.split_working"))
        self._prog_val = (0, max(1, area))

        def on_progress(done, total):
            self._prog_val = (done, total)

        def work():
            try:
                labels, bbox = editor.map.preview_split(
                    self._pid, k, seed=seed, strategy=strategy,
                    smooth_passes=smooth, on_progress=on_progress)
                colors = editor.map.free_colors(int(labels.max()) - 1)
                self._result = (labels, bbox, colors)
            except Exception as exc:                       # noqa: BLE001
                self._result = exc
            self._state = "done"

        threading.Thread(target=work, daemon=True).start()
        self._poll()

    def _poll(self) -> None:
        if self._state == "working":
            done, total = self._prog_val
            self._progress.configure(
                text=f"{self.t('map.split_working')} {done * 100 // total}%")
            self.after(120, self._poll)
            return
        if self._state != "done":
            return
        self._state = "idle"
        self._gen_btn.configure(state="normal")
        result = self._result
        if isinstance(result, Exception):
            self._progress.configure(text=str(result))
            return
        self._labels, self._bbox, self._colors = result
        self._progress.configure(text=self.t("map.split_ready"))
        self._apply_btn.configure(state="normal")
        self._draw_preview()

    def _draw_preview(self) -> None:
        labels, bbox, colors = self._labels, self._bbox, self._colors
        if labels is None:
            return
        d = self.editor.map.by_id[self._pid]
        h, w = labels.shape
        palette = [(45, 47, 56), d.color] + list(colors)
        lut = np.array(palette, dtype=np.uint8)
        rgb = lut[np.clip(labels, 0, len(palette) - 1)]
        img = Image.fromarray(rgb, "RGB")
        cw = max(self._canvas.winfo_width(), self.PREVIEW[0])
        ch = max(self._canvas.winfo_height(), self.PREVIEW[1])
        scale = min(cw / w, ch / h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                         Image.NEAREST)
        self._photo = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        self._canvas.create_image(cw // 2, ch // 2, image=self._photo,
                                  anchor="center")

    def _apply(self) -> None:
        if self._labels is None:
            return
        labels, bbox, colors = self._labels, self._bbox, self._colors
        pid = self._pid
        self.destroy()
        self._on_apply(pid, labels, bbox, colors)
