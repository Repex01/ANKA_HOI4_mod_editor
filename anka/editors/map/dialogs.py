"""Map editor dialogs."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

import numpy as np
from PIL import Image, ImageTk

from ...services.map_service import MIN_PROVINCE_AREA
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


class NewStateDialog(BaseDialog):
    """Metadata for a brand-new state (id is allocated by the service). If provinces
    are selected on the map they seed the state; otherwise it starts empty and the
    editor turns on assign mode so the user can paint provinces in."""

    def __init__(self, master, editor, on_submit: Callable[[dict], None],
                 seed_count: int = 0, split_resources: bool = True):
        super().__init__(master, editor, editor.t("map.new_state"), (410, 300))
        self._on_submit = on_submit

        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text=self.t("map.state_name"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=3)
        self._name_var = tk.StringVar()
        name_entry = ttk.Entry(body, textvariable=self._name_var, width=22)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        name_entry.focus_set()

        ttk.Label(body, text=self.t("map.owner"), style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=3)
        self._owner_var = tk.StringVar()
        ttk.Entry(body, textvariable=self._owner_var, width=8).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=3)

        ttk.Label(body, text=self.t("map.category"), style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=3)
        cats = editor.state_categories()
        self._cat_var = tk.StringVar(value="rural" if "rural" in cats
                                     else (cats[0] if cats else "rural"))
        ttk.Combobox(body, textvariable=self._cat_var, state="readonly",
                     values=cats, width=18).grid(row=2, column=1, sticky="w",
                                                 padx=(8, 0), pady=3)

        self._split_var = tk.BooleanVar(value=split_resources)
        ttk.Checkbutton(body, text=self.t("map.split_resources"),
                        style="Card.TCheckbutton", variable=self._split_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        hint = (self.t("map.new_state_seed", count=seed_count) if seed_count
                else self.t("map.new_state_empty"))
        ttk.Label(body, text=hint, style="CardMuted.TLabel", wraplength=360,
                  justify="left").grid(row=4, column=0, columnspan=2, sticky="w",
                                       pady=(8, 0))
        self.buttons_row(body, self.t("common.create")).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _submit(self) -> None:
        fields = {
            "name": self._name_var.get().strip(),
            "owner": self._owner_var.get().strip().upper(),
            "category": self._cat_var.get().strip(),
            "split_resources": self._split_var.get(),
        }
        self.destroy()
        self._on_submit(fields)


class GenerateStatesDialog(BaseDialog):
    """Partition the selected land provinces into N new states, with a live preview
    (each cluster a colour) before applying."""

    PREVIEW = (460, 260)

    def __init__(self, master, editor, provs: list[int],
                 on_apply: Callable[[list[int], list, dict], None],
                 split_resources: bool = True, total_states: int = 0,
                 selected_states: int = 0):
        super().__init__(master, editor, editor.t("map.gen_states"), (520, 690))
        self.resizable(True, True)
        self._provs = list(provs)
        self._on_apply = on_apply
        self._groups: list[list[int]] | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._state = "idle"

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(8, weight=1)

        ttk.Label(body, text=self.t("map.gen_states_stats", total=total_states,
                                    selected=selected_states),
                  style="CardMuted.TLabel").grid(row=0, column=0, columnspan=2,
                                                 sticky="w", pady=(0, 6))

        row0 = ttk.Frame(body, style="Card.TFrame")
        row0.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(row0, text=self.t("map.gen_states_count"),
                  style="Card.TLabel").pack(side="left")
        self._count_var = tk.IntVar(value=min(2, len(provs)))
        ttk.Spinbox(row0, from_=1, to=max(1, len(provs)), width=5,
                    textvariable=self._count_var).pack(side="left", padx=(4, 12))
        ttk.Label(row0, text="seed:", style="Card.TLabel").pack(side="left")
        self._seed_var = tk.StringVar(value="0")
        ttk.Entry(row0, textvariable=self._seed_var, width=8).pack(side="left", padx=4)

        row1 = ttk.Frame(body, style="Card.TFrame")
        row1.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(row1, text=self.t("map.owner"), style="Card.TLabel").pack(side="left")
        self._owner_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self._owner_var, width=6).pack(side="left",
                                                                    padx=(4, 12))
        ttk.Label(row1, text=self.t("map.category"),
                  style="Card.TLabel").pack(side="left")
        cats = editor.state_categories()
        self._cat_var = tk.StringVar(value="rural" if "rural" in cats
                                     else (cats[0] if cats else "rural"))
        ttk.Combobox(row1, textvariable=self._cat_var, state="readonly", width=14,
                     values=cats).pack(side="left", padx=4)

        self._split_var = tk.BooleanVar(value=split_resources)
        ttk.Checkbutton(body, text=self.t("map.split_resources"),
                        style="Card.TCheckbutton", variable=self._split_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._borders_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text=self.t("map.gen_within_borders"),
                        style="Card.TCheckbutton", variable=self._borders_var).grid(
            row=4, column=0, columnspan=2, sticky="w")
        self._owners_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text=self.t("map.gen_keep_owners"),
                        style="Card.TCheckbutton", variable=self._owners_var).grid(
            row=5, column=0, columnspan=2, sticky="w")
        self._match_cat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text=self.t("map.gen_match_categories"),
                        style="Card.TCheckbutton", variable=self._match_cat_var).grid(
            row=6, column=0, columnspan=2, sticky="w")
        self._cores_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text=self.t("map.gen_original_cores"),
                        style="Card.TCheckbutton", variable=self._cores_var).grid(
            row=7, column=0, columnspan=2, sticky="w")

        self._canvas = tk.Canvas(body, width=self.PREVIEW[0], height=self.PREVIEW[1],
                                 bg=self.palette.surface_alt, highlightthickness=0,
                                 bd=0)
        self._canvas.grid(row=8, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        self._progress = ttk.Label(body, text=self.t("map.gen_states_hint",
                                                     count=len(provs)),
                                   style="CardMuted.TLabel", wraplength=460)
        self._progress.grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 2))

        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(10, 0))
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
        raw = self._seed_var.get().strip()
        if raw:
            try:
                seed = int(raw)
            except ValueError:
                seed = 0
        else:
            import random
            seed = random.randrange(1_000_000)
        self._seed_var.set(str(seed))
        n = max(1, min(int(self._count_var.get() or 1), len(self._provs)))
        borders = self._borders_var.get()
        self._state = "working"
        self._apply_btn.configure(state="disabled")
        self._gen_btn.configure(state="disabled")
        self._progress.configure(text=self.t("map.split_working"))

        def work():
            try:
                self._result = self.editor.partition_provinces(
                    self._provs, n, seed, within_borders=borders)
            except Exception as exc:                           # noqa: BLE001
                self._result = exc
            self._state = "done"

        threading.Thread(target=work, daemon=True).start()
        self._poll()

    def _poll(self) -> None:
        if self._state == "working":
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
        self._groups = result
        self._progress.configure(text=self.t("map.gen_states_ready",
                                             count=len(self._groups)))
        self._apply_btn.configure(state="normal")
        self._draw_preview()
        # The clustering is deterministic per seed — auto-increment so a repeat click
        # produces a fresh variant (the exact seed used stays reproducible).
        try:
            self._seed_var.set(str(int(self._seed_var.get() or "0") + 1))
        except ValueError:
            self._seed_var.set("0")

    def _draw_preview(self) -> None:
        if not self._groups:
            return
        img = self.editor.generate_states_preview(self._provs, self._groups)
        if img is None:
            return
        w, h = img.size
        cw = max(self._canvas.winfo_width(), self.PREVIEW[0])
        ch = max(self._canvas.winfo_height(), self.PREVIEW[1])
        scale = min(cw / w, ch / h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                         Image.NEAREST)
        self._photo = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        self._canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")

    def _apply(self) -> None:
        if not self._groups:
            return
        fields = {
            "owner": self._owner_var.get().strip().upper(),
            "category": self._cat_var.get().strip(),
            "split_resources": self._split_var.get(),
            "match_categories": self._match_cat_var.get(),
            "original_cores": self._cores_var.get(),
            "keep_owners": self._owners_var.get(),
        }
        groups = self._groups
        self.destroy()
        self._on_apply(self._provs, groups, fields)


class AdjacencyDialog(BaseDialog):
    """Table over ``map/adjacencies.csv``: straits, canals, impassable borders.
    Edits go to the service (saved with the editor's 💾)."""

    def __init__(self, master, editor):
        super().__init__(master, editor, editor.t("map.adjacencies"), (760, 480))
        self.resizable(True, True)
        self.service = editor.adjacencies

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        cols = ("from", "to", "type", "through", "rule", "comment")
        self._tree = ttk.Treeview(body, columns=cols, show="headings")
        headers = {"from": "From", "to": "To", "type": "Type",
                   "through": "Through", "rule": "Rule",
                   "comment": self.t("map.comment")}
        widths = {"from": 70, "to": 70, "type": 90, "through": 70,
                  "rule": 150, "comment": 220}
        for c in cols:
            self._tree.heading(c, text=headers[c])
            self._tree.column(c, width=widths[c],
                              stretch=(c == "comment"))
        self._tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._edit())

        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(bar, text="➕ " + self.t("common.add"),
                   command=self._add).pack(side="left")
        ttk.Button(bar, text="✏", width=3, command=self._edit).pack(
            side="left", padx=4)
        ttk.Button(bar, text="➖", width=3, command=self._remove).pack(side="left")
        ttk.Button(bar, text=self.t("common.close"),
                   command=self.destroy).pack(side="right")
        self._refresh()

    def _refresh(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for i, row in enumerate(self.service.rows):
            self._tree.insert("", "end", iid=str(i), values=(
                row.from_id, row.to_id, row.type or "—", row.through,
                row.rule or "", row.comment))

    def _selected_row(self):
        sel = self._tree.selection()
        if not sel:
            return None
        try:
            return self.service.rows[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def _add(self) -> None:
        AdjacencyRowDialog(self, self.editor, None, self._row_submitted)

    def _edit(self) -> None:
        row = self._selected_row()
        if row is not None:
            AdjacencyRowDialog(self, self.editor, row, self._row_submitted)

    def _row_submitted(self, row, fields: dict) -> None:
        if row is None:
            self.service.add(**fields)
        else:
            self.service.edit(row, **fields)
        self._refresh()

    def _remove(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        from tkinter import messagebox
        if messagebox.askyesno("ANKA", self.t("map.confirm_remove_adjacency",
                                              a=row.from_id, b=row.to_id),
                               parent=self):
            self.service.remove(row)
            self._refresh()


class AdjacencyRowDialog(BaseDialog):
    def __init__(self, master, editor, row, on_submit):
        super().__init__(master, editor, editor.t("map.adjacency_row"),
                         (420, 360))
        self._row = row
        self._on_submit = on_submit

        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)

        def entry(r, label, value):
            ttk.Label(body, text=label, style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(value))
            ttk.Entry(body, textvariable=var, width=12).grid(
                row=r, column=1, sticky="w", padx=(8, 0), pady=3)
            return var

        self._from_var = entry(0, "From", row.from_id if row else "")
        self._to_var = entry(1, "To", row.to_id if row else "")
        ttk.Label(body, text="Type", style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=3)
        self._type_var = tk.StringVar(value=(row.type if row else "sea") or "—")
        ttk.Combobox(body, textvariable=self._type_var, state="readonly",
                     values=["sea", "impassable", "—"], width=12).grid(
            row=2, column=1, sticky="w", padx=(8, 0), pady=3)
        self._through_var = entry(3, "Through", row.through if row else -1)
        ttk.Label(body, text="Rule", style="CardMuted.TLabel").grid(
            row=4, column=0, sticky="w", pady=3)
        self._rule_var = tk.StringVar(value=(row.rule if row else "") or "—")
        ttk.Combobox(body, textvariable=self._rule_var, state="readonly",
                     values=["—"] + editor.adjacencies.rule_names(),
                     width=22).grid(row=4, column=1, sticky="w",
                                    padx=(8, 0), pady=3)
        self._comment_var = entry(5, self.t("map.comment"),
                                  row.comment if row else "")
        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=6, column=0, columnspan=2, sticky="w")
        self.buttons_row(body, self.t("common.save")).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _submit(self) -> None:
        try:
            fields = {
                "from_id": int(self._from_var.get()),
                "to_id": int(self._to_var.get()),
                "through": int(self._through_var.get() or "-1"),
            }
        except ValueError:
            self._error.configure(text=self.t("map.err.bad_number"))
            return
        type_value = self._type_var.get()
        fields["type"] = "" if type_value == "—" else type_value
        rule = self._rule_var.get()
        fields["rule"] = "" if rule == "—" else rule
        fields["comment"] = self._comment_var.get()
        row = self._row
        self.destroy()
        self._on_submit(row, fields)


class SplitProvinceDialog(BaseDialog):
    """Split a region (one province or a multi-selection) into K generated
    provinces (phase 4b): parameters, background generation with the ported
    cluster-growth algorithm, a shape preview, then apply."""

    PREVIEW = (460, 300)

    def __init__(self, master, editor, pids: list[int],
                 on_apply: Callable[[list[int], np.ndarray, tuple, list], None]):
        title = (editor.t("map.split_title", id=pids[0]) if len(pids) == 1
                 else editor.t("map.split_area_title", count=len(pids)))
        super().__init__(master, editor, title, (520, 550))
        self.resizable(True, True)
        self._pids = list(pids)
        self._on_apply = on_apply
        self._labels = None
        self._bbox = None
        self._colors: list[tuple[int, int, int]] = []
        self._photo: ImageTk.PhotoImage | None = None
        self._state = "idle"          # idle | working | done

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(5, weight=1)

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

        self._borders_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text=self.t("map.split_within_borders"),
                        style="Card.TCheckbutton",
                        variable=self._borders_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._rivers_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text=self.t("map.split_avoid_rivers"),
                        style="Card.TCheckbutton",
                        variable=self._rivers_var).grid(
            row=3, column=0, columnspan=2, sticky="w")

        self._progress = ttk.Label(body, text="", style="CardMuted.TLabel")
        self._progress.grid(row=4, column=0, columnspan=2, sticky="w",
                            pady=(6, 2))

        self._canvas = tk.Canvas(body, width=self.PREVIEW[0],
                                 height=self.PREVIEW[1],
                                 bg=self.palette.surface_alt,
                                 highlightthickness=0, bd=0)
        self._canvas.grid(row=5, column=0, columnspan=2, sticky="nsew")

        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
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
        area = sum(editor.map.area_of(p) for p in self._pids)
        if area > _SPLIT_SOFT_CAP:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "ANKA", self.t("map.split_big", count=area), parent=self):
                return
        raw_seed = self._seed_var.get().strip()
        if raw_seed:
            try:
                seed = int(raw_seed)
            except ValueError:
                seed = 0
        else:
            import random
            seed = random.randrange(1_000_000)   # пустое поле = случайный вариант
        self._seed_var.set(str(seed))            # показать, чтобы можно было воспроизвести
        k = max(2, int(self._k_var.get() or 2))
        strategy = self._strategies.get(self._strategy_var.get(), "organic")
        smooth = max(0, int(self._smooth_var.get() or 0))
        within = self._borders_var.get()
        avoid_rivers = self._rivers_var.get()
        self._state = "working"
        self._apply_btn.configure(state="disabled")
        self._gen_btn.configure(state="disabled")
        self._progress.configure(text=self.t("map.split_working"))
        self._prog_val = (0, max(1, area))

        def on_progress(done, total):
            self._prog_val = (done, total)

        def work():
            try:
                labels, bbox = editor.map.preview_split_area(
                    self._pids, k, seed=seed, strategy=strategy,
                    smooth_passes=smooth, within_states=within,
                    avoid_rivers=avoid_rivers, on_progress=on_progress)
                # Up to `k` new colors may be needed (a donor id is reused per
                # cluster at most once); unused ones are simply not taken.
                colors = editor.map.free_colors(int(labels.max()))
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
        # Генератор детерминирован: не сдвинув seed, повторное «Сгенерировать»
        # выдало бы в точности тот же результат. Автоинкремент даёт новый
        # вариант на каждый клик, а конкретное значение остаётся воспроизводимым.
        try:
            self._seed_var.set(str(int(self._seed_var.get() or "0") + 1))
        except ValueError:
            self._seed_var.set("0")

    def _draw_preview(self) -> None:
        labels, bbox, colors = self._labels, self._bbox, self._colors
        if labels is None:
            return
        d = self.editor.map.by_id[self._pids[0]]
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
        # Reject a split that would produce provinces below the minimum area — report
        # it here in the generation window (each cluster becomes one province).
        counts = np.bincount(self._labels.ravel())
        tiny = sum(1 for i in range(1, len(counts))
                   if 0 < counts[i] < MIN_PROVINCE_AREA)
        if tiny:
            from tkinter import messagebox
            messagebox.showerror("ANKA", self.t("map.err.tiny_split",
                                                min=MIN_PROVINCE_AREA, count=tiny),
                                 parent=self)
            return
        labels, bbox, colors = self._labels, self._bbox, self._colors
        pids = self._pids
        self.destroy()
        self._on_apply(pids, labels, bbox, colors)
