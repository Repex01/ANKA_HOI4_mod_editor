"""Map editor: province bitmap viewport + state/province inspectors.

Layout: states tree (left, collapsible) · map canvas (center) · inspector
(right). The heavy bitmap (5632×2048) is loaded in a background thread; the
canvas renders viewport crops asynchronously, so the UI stays responsive.

Phase 1: read-only viewing — render modes (provinces / terrain / owner /
state), click-to-select with state highlight, zoom-to-state, cursor indicator.
Phase 2: block-backed state inspector (owner/controller, category, manpower,
cores, VP, resources, buildings, province membership incl. map-click assign).
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

from ...services.adjacency_service import AdjacencyService
from ...services.building_service import BuildingService
from ...services.map_refs import ResourceService, StateCategoryService
from ...services.map_service import RENDER_MODES, MapService
from ...services.map_zones import StrategicRegionService, SupplyAreaService
from ...services.overlay_maps import HeightmapService, TerrainBmpService
from ...services.state_service import StateService
from ...services.terrain_service import TerrainService
from ...services._locutil import LocCatalog
from ..base import EditorModule, EditorRegistry
from ...ui.widgets import enable_form_wheel
from .canvas import MapCanvas
from .commands import (TOUCH_DEFS, TOUCH_STATES, TOUCH_ZONES, CommandStack,
                       CompoundCommand, CreateDefCommand, LayerPixelsCommand,
                       PixelsCommand, SetDefCommand, StateDocsCommand,
                       StateProvincesCommand, ZoneMembersCommand)
from .inspector import BatchStateInspector, ProvinceInspector, StateInspector

# Tools in toolbar / hotkey order (1–5); every render mode has its own tool set.
_TOOLS = ("select", "state", "brush", "fill", "picker")
_TOOL_ICONS = {"select": "⭘", "state": "⭘⭘", "brush": "🖌",
               "fill": "🪣", "picker": "💉"}
_HEIGHT_TOOLS = ("raise", "lower", "set", "smooth")
_HEIGHT_ICONS = {"raise": "▲", "lower": "▼", "set": "▬", "smooth": "≈"}
_VISUAL_TOOLS = ("vbrush", "vfill", "vpicker")
_VISUAL_ICONS = {"vbrush": "🖌", "vfill": "🪣", "vpicker": "💉"}
_REGION_TOOLS = ("rselect", "rassign")
_REGION_ICONS = {"rselect": "⭘", "rassign": "🎯"}

# Every mode in F-hotkey / combobox order: the province LUT modes rendered by
# MapService, then the bitmap-layer editors (heightmap / visual terrain).
ALL_MODES = (*RENDER_MODES, "height", "visual")
# Modes that use the province tool set (select/state/brush/fill/picker).
_PROVINCE_MODES = ("provinces", "terrain", "owner", "state")


def _tool_group(mode: str) -> str:
    if mode == "height":
        return "height"
    if mode == "visual":
        return "visual"
    if mode == "strat_region":
        return "region"
    return "prov"


@EditorRegistry.register
class MapEditor(EditorModule):
    id = "map"
    name_key = "editors.map.name"
    desc_key = "editors.map.desc"
    order = 110

    def __init__(self, context, services):
        super().__init__(context, services)
        self.states = StateService(context)
        self.terrain = TerrainService(context)
        self.buildings = BuildingService(context)
        self.state_cats = StateCategoryService(context)
        self.resources = ResourceService(context)
        self.strat_regions = StrategicRegionService(context)
        self.map = MapService(context, state_service=self.states,
                              terrain_service=self.terrain,
                              strat_service=self.strat_regions)
        self.heightmap = HeightmapService(self.map)
        self.terrain_bmp = TerrainBmpService(self.map)
        self.adjacencies = AdjacencyService(context, map_service=self.map)
        self.supply_areas = SupplyAreaService(context)
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="state",
                              default_pattern="anka_states_l_{lang}.yml",
                              dep_roots=context.dependency_paths)
        # Victory-point names (key VICTORY_POINTS_<province>) live in their own
        # vanilla files (victory_points_l_*.yml), so they need a separate catalog.
        self.vp_loc = LocCatalog(context.mod.path, context.game_path,
                                 vanilla_filter="victory",
                                 default_pattern="anka_victory_points_l_{lang}.yml",
                                 dep_roots=context.dependency_paths)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        # Victory-point names are edited per language, chosen in the inspector; starts
        # at the app language but can be switched to localise VPs for any language.
        self.vp_language = self.loc_language
        self._items: dict[str, tuple] = {}
        self._selected_province: int | None = None
        self._selected_state: int | None = None
        self._state_doc = None
        self._dirty: set = set()             # paths of dirty state documents
        self._dirty_docs: dict = {}          # path -> StateDocument
        self._assign_mode = False
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self._load_state = "idle"            # idle | loading | ready | error
        self._load_error = ""
        self._tree_sel_guard: str | None = None   # iid of a programmatic tree select
        self._tool = "select"                # select | brush | fill | picker
        self._brush_target: int | None = None
        self._last_paint: tuple[int, int] | None = None
        self.commands = CommandStack(limit=40)
        self._stroke_delta: list[tuple] = []   # (ys, xs, old) chunks of a stroke
        self._multi_sel: set[int] = set()      # Shift-click / rubber-band picks
        self._mode = "provinces"               # current render/editing mode
        self._height_tool = "raise"            # raise | lower | set | smooth
        self._visual_tool = "vbrush"           # vbrush | vfill | vpicker
        self._region_tool = "rselect"          # rselect | rassign
        self._visual_index: int | None = None  # selected terrain.bmp palette index
        self._sel_region: int | None = None    # selected strategic region id
        self._layer_stroke: list[tuple] = []   # (ys, xs, old, new) chunks
        self._palette_built = False

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        root = ttk.Frame(parent, style="TFrame")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)
        self._grid_root = root

        self._build_toolbar(root)
        self._build_states_panel(root)

        center = ttk.Frame(root, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)

        self.canvas = MapCanvas(
            center, self.palette,
            render=self._render_view,
            map_size=self._map_size,
            on_click=self._map_click,
            on_hover=self._map_hover,
            on_paint=self._paint,
            on_paint_end=self._paint_end,
            on_rect_select=self._rect_select,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        from ...ui.hotkeys import bind_ctrl
        for widget in (self.canvas.canvas, self._tree):
            bind_ctrl(widget, "z", lambda e: (self.undo(), "break")[1])
            bind_ctrl(widget, "y", lambda e: (self.redo(), "break")[1])
            # F1–F7 switch render mode, 1–5 switch the active mode's tool (only
            # while the map/tree has focus, so typing digits in inspector
            # fields is unaffected).
            for i, mode in enumerate(ALL_MODES):
                widget.bind(f"<F{i + 1}>",
                            lambda e, m=mode: (self._set_mode(m), "break")[1])
            for i in range(len(_TOOLS)):
                widget.bind(f"<Key-{i + 1}>",
                            lambda e, n=i: (self._tool_hotkey(n), "break")[1])
        self.canvas.canvas.bind("<Escape>",
                                lambda e: self.clear_multi_selection())
        # Grab keyboard focus when the pointer enters the map so the mode/tool
        # hotkeys fire without first having to click (they only reach a focused
        # widget). Guard: don't steal focus from a text field being edited.
        self.canvas.canvas.bind("<Enter>", self._focus_map, add="+")

        # Selected-province counter, pinned to the map's bottom-right corner.
        self._sel_count = tk.Label(self.canvas, text="", bd=0, padx=8, pady=2,
                                   bg=self.palette.surface, fg=self.palette.text_muted,
                                   font=("Segoe UI", 9))
        self._sel_count.place(relx=1.0, rely=1.0, anchor="se", x=-4, y=-4)
        self._update_sel_count()
        # Cursor coordinates, pinned to the screen's bottom-left corner. Origin (0,0)
        # is the map's top-left (image coordinates).
        self._coord_lbl = tk.Label(self.canvas, text="", bd=0, padx=8, pady=2,
                                   bg=self.palette.surface, fg=self.palette.text_muted,
                                   font=("Segoe UI", 9))
        self._coord_lbl.place(relx=0.0, rely=1.0, anchor="sw", x=4, y=-4)

        self._loading_lbl = ttk.Label(center, text=self.t("map.loading"),
                                      style="Muted.TLabel")
        self._loading_lbl.grid(row=0, column=0)

        self._build_problems(center)
        self._build_inspector_panel(root)
        self._start_loading()
        return root

    def _build_problems(self, center) -> None:
        panel = ttk.Frame(center, style="Card.TFrame", padding=(8, 6))
        panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(0, weight=1)
        self._problems_panel = panel
        self._problems = ttk.Treeview(panel, columns=("sev", "subject", "msg"),
                                      show="headings", height=5)
        self._problems.heading("sev", text="!")
        self._problems.heading("subject", text=self.t("map.col.subject"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("subject", width=140, stretch=False)
        self._problems.column("msg", width=440)
        self._problems.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._problems.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._problems.configure(yscrollcommand=sb.set)
        self._problems.tag_configure("error", foreground=self.palette.danger)
        self._problems.bind("<<TreeviewSelect>>", self._on_problem_select)
        panel.grid_remove()
        self._problems_visible = False
        self._issues: list = []

    def _toggle_problems(self) -> None:
        self._problems_visible = not self._problems_visible
        if self._problems_visible:
            self._validate()
            self._problems_panel.grid()
        else:
            self._problems_panel.grid_remove()

    def _validate(self) -> None:
        from ...services.map_validation import validate
        if not self.map.loaded:
            return
        try:
            self._issues = validate(self.map, self.states, self.buildings,
                                    strategic_regions=self.strat_regions,
                                    supply_areas=self.supply_areas)
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", str(exc))
            return
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(self._issues):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"map.issue.{issue.code}", **issue.detail)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error"
                                          else "⚠", issue.subject, msg),
                                  tags=(issue.severity,))
        n = len(self._issues)
        label = f"⚠ {n}" if not errors else f"⛔ {errors} · ⚠ {n - errors}"
        self._btn_problems.configure(text=label)

    def _on_problem_select(self, _event=None) -> None:
        sel = self._problems.selection()
        if not sel:
            return
        try:
            issue = self._issues[int(sel[0])]
        except (ValueError, IndexError):
            return
        if issue.target_kind == "province" and issue.target_id in self.map.by_id:
            if self.map.area_of(issue.target_id):
                self.select_province(issue.target_id, zoom=True)
            else:
                self.select_province(issue.target_id)
        elif issue.target_kind == "state":
            self.select_state(issue.target_id, zoom=True)

    def _build_toolbar(self, root) -> None:
        # The bar lives inside a canvas so a horizontal scrollbar can appear
        # when the window is too narrow to show every control.
        outer = ttk.Frame(root, style="TFrame")
        outer.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        outer.columnconfigure(0, weight=1)
        clip = tk.Canvas(outer, highlightthickness=0, bd=0, bg=self.palette.bg)
        clip.grid(row=0, column=0, sticky="ew")
        hbar = ttk.Scrollbar(outer, orient="horizontal", command=clip.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        hbar.grid_remove()
        clip.configure(xscrollcommand=hbar.set)
        bar = ttk.Frame(clip, style="TFrame")
        bar_win = clip.create_window(0, 0, window=bar, anchor="nw")

        def _sync_toolbar(_event=None) -> None:
            req_w, req_h = bar.winfo_reqwidth(), bar.winfo_reqheight()
            if clip.winfo_height() != req_h:
                clip.configure(height=req_h)
            clip.configure(scrollregion=(0, 0, req_w, req_h))
            clip_w = clip.winfo_width()
            if req_w > clip_w:                      # overflow: scroll natural width
                clip.itemconfigure(bar_win, width=req_w)
                hbar.grid()
            else:                                   # fits: stretch so side="right"
                clip.itemconfigure(bar_win, width=clip_w)   # widgets stay pinned
                hbar.grid_remove()
                clip.xview_moveto(0)

        bar.bind("<Configure>", _sync_toolbar)
        clip.bind("<Configure>", _sync_toolbar)
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("map.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")

        ttk.Label(bar, text=self.t("map.mode"), style="Muted.TLabel").pack(
            side="left", padx=(12, 4))
        # Every mode label carries its F-hotkey, e.g. "Provinces (F1)".
        self._mode_by_label = {f"{self.t(f'map.mode.{m}')} (F{i + 1})": m
                               for i, m in enumerate(ALL_MODES)}
        self._label_by_mode = {m: lbl for lbl, m in self._mode_by_label.items()}
        self._mode_var = tk.StringVar(value=self._label_by_mode["provinces"])
        combo = ttk.Combobox(bar, textvariable=self._mode_var, state="readonly",
                             width=22, values=list(self._mode_by_label))
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", self._mode_changed)
        enable_form_wheel(combo)          # map-mode wheel is convenient here
        from ...ui.widgets.tooltip import attach_help
        attach_help(combo, self.t, "map.mode", self.palette)

        self._rivers_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=self.t("map.rivers"), style="Card.TCheckbutton",
                        variable=self._rivers_var,
                        command=lambda: self.canvas.set_rivers(self._rivers_var.get())
                        ).pack(side="left", padx=(8, 0))

        ttk.Button(bar, text="⤢ " + self.t("map.fit"),
                   command=lambda: self.canvas.fit_map()).pack(side="left", padx=4)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)
        self._undo_btn = ttk.Button(bar, text="↶", width=3, command=self.undo,
                                    state="disabled")
        self._undo_btn.pack(side="left", padx=(4, 1))
        self._redo_btn = ttk.Button(bar, text="↷", width=3, command=self.redo,
                                    state="disabled")
        self._redo_btn.pack(side="left", padx=1)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y",
                                                   padx=6, pady=2)
        # Brush size — one variable shared by every paint tool; each tool group
        # shows its own labelled spinbox for it.
        self._radius_var = tk.IntVar(value=6)
        # Brush shape — used by the height / visual terrain brushes.
        self._shape_by_label = {self.t("map.shape.round"): "round",
                                self.t("map.shape.square"): "square"}
        self._shape_var = tk.StringVar(value=self.t("map.shape.round"))

        # Per-mode tool groups: only the active mode's frame is shown.
        area = ttk.Frame(bar, style="TFrame")
        area.pack(side="left", fill="y")
        self._tool_frames = {
            "prov": self._build_prov_tools(area, attach_help),
            "height": self._build_height_tools(area),
            "visual": self._build_visual_tools(area),
            "region": self._build_region_tools(area),
        }
        self._tool_frames["prov"].pack(side="left")

        self._btn_problems = ttk.Button(bar, text="⚠", command=self._toggle_problems)
        self._btn_problems.pack(side="right", padx=(0, 4))

        self._assign_lbl = tk.Label(bar, text="🎯 " + self.t("map.assign_hint"),
                                    bd=0, padx=10, pady=3,
                                    bg=self.palette.accent,
                                    fg=self.palette.accent_text)
        self._status = ttk.Label(bar, text="", style="Muted.TLabel")
        self._status.pack(side="right", padx=8)

    # ------------------------------------------------------- per-mode tool bars
    def _build_prov_tools(self, parent, attach_help) -> ttk.Frame:
        frame = ttk.Frame(parent, style="TFrame")
        self._tool_btns: dict[str, ttk.Button] = {}
        for i, name in enumerate(_TOOLS):
            btn = ttk.Button(frame, text=f"{_TOOL_ICONS[name]} {i + 1}",
                             command=lambda n=name: self._set_tool(n), width=5)
            btn.pack(side="left", padx=1)
            attach_help(btn, self.t, "map.tools", self.palette)
            self._tool_btns[name] = btn
        self._size_spin(frame)
        self._ground_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text=self.t("map.ground_only"),
                        style="Card.TCheckbutton",
                        variable=self._ground_only).pack(side="left", padx=(8, 0))
        self._target_swatch = tk.Label(frame, text="  ", bd=1, relief="solid")
        self._target_swatch.pack(side="left", padx=(8, 2))
        self._target_lbl = ttk.Label(frame, text=self.t("map.no_brush"),
                                     style="Muted.TLabel")
        self._target_lbl.pack(side="left")
        # Stack the create / generate pairs into two-row columns to save width.
        new_col = ttk.Frame(frame, style="TFrame")
        new_col.pack(side="left", padx=(6, 2))
        ttk.Button(new_col, text="➕ " + self.t("map.new_province"), width=13,
                   command=self._new_province).pack(fill="x")
        ttk.Button(new_col, text="🗺 " + self.t("map.new_state"), width=13,
                   command=self._new_state).pack(fill="x", pady=(2, 0))
        gen_col = ttk.Frame(frame, style="TFrame")
        gen_col.pack(side="left", padx=2)
        split_btn = ttk.Button(gen_col, text="⚡ " + self.t("map.split_area"),
                               width=14, command=self._split_selection)
        split_btn.pack(fill="x")
        attach_help(split_btn, self.t, "map.split", self.palette)
        gen_btn = ttk.Button(gen_col, text="⚡ " + self.t("map.gen_states"),
                             width=14, command=self._generate_states)
        gen_btn.pack(fill="x", pady=(2, 0))
        attach_help(gen_btn, self.t, "map.gen_states_help", self.palette)
        ttk.Button(frame, text="⇄ " + self.t("map.adjacencies"),
                   command=self._open_adjacencies).pack(side="left", padx=2)
        # One-shot: turn the mod into a full history/states replacement.
        self._replace_btn = ttk.Button(frame, text="🔒 " + self.t("map.make_replace"),
                                       command=self._make_replace_path)
        if not self._states_replaced():
            self._replace_btn.pack(side="left", padx=2)
        attach_help(self._replace_btn, self.t, "map.make_replace", self.palette)
        return frame

    def _size_spin(self, parent) -> None:
        """Labelled brush-size spinbox bound to the shared size variable."""
        ttk.Label(parent, text=self.t("map.brush_size"),
                  style="Muted.TLabel").pack(side="left", padx=(8, 2))
        spin = ttk.Spinbox(parent, from_=1, to=64, width=4,
                           textvariable=self._radius_var)
        spin.pack(side="left")
        enable_form_wheel(spin)                 # brush size wheel is convenient

    def _shape_combo(self, parent) -> ttk.Combobox:
        combo = ttk.Combobox(parent, textvariable=self._shape_var,
                             state="readonly", width=9,
                             values=list(self._shape_by_label))
        enable_form_wheel(combo)
        return combo

    def _brush_shape(self) -> str:
        return self._shape_by_label.get(self._shape_var.get(), "round")

    def _build_height_tools(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, style="TFrame")
        self._htool_btns: dict[str, ttk.Button] = {}
        for i, name in enumerate(_HEIGHT_TOOLS):
            btn = ttk.Button(frame, width=11,
                             text=f"{_HEIGHT_ICONS[name]} "
                                  f"{self.t(f'map.htool.{name}')} {i + 1}",
                             command=lambda n=name: self._set_height_tool(n))
            btn.pack(side="left", padx=1)
            self._htool_btns[name] = btn
        self._shape_combo(frame).pack(side="left", padx=(8, 0))
        self._size_spin(frame)
        ttk.Label(frame, text=self.t("map.strength"),
                  style="Muted.TLabel").pack(side="left", padx=(8, 2))
        self._strength_var = tk.IntVar(value=8)
        strength = ttk.Spinbox(frame, from_=1, to=64, width=4,
                               textvariable=self._strength_var)
        strength.pack(side="left")
        enable_form_wheel(strength)
        ttk.Label(frame, text=self.t("map.level"),
                  style="Muted.TLabel").pack(side="left", padx=(8, 2))
        self._level_var = tk.IntVar(value=128)
        level = ttk.Spinbox(frame, from_=0, to=255, width=5,
                            textvariable=self._level_var)
        level.pack(side="left")
        enable_form_wheel(level)
        self._height_lbl = ttk.Label(frame, text="", style="Muted.TLabel")
        self._height_lbl.pack(side="left", padx=(10, 0))
        return frame

    def _build_visual_tools(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, style="TFrame")
        self._vtool_btns: dict[str, ttk.Button] = {}
        for i, name in enumerate(_VISUAL_TOOLS):
            btn = ttk.Button(frame, text=f"{_VISUAL_ICONS[name]} {i + 1}",
                             command=lambda n=name: self._set_visual_tool(n),
                             width=5)
            btn.pack(side="left", padx=1)
            self._vtool_btns[name] = btn
        self._shape_combo(frame).pack(side="left", padx=(8, 4))
        self._size_spin(frame)
        # Palette bar (two rows of index swatches), filled once terrain.bmp and
        # the graphical-terrain declarations are loaded.
        self._palette_frame = ttk.Frame(frame, style="TFrame")
        self._palette_frame.pack(side="left", padx=(6, 4))
        self._visual_lbl = ttk.Label(frame, text=self.t("map.no_terrain_index"),
                                     style="Muted.TLabel")
        self._visual_lbl.pack(side="left", padx=(4, 0))
        return frame

    def _build_region_tools(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, style="TFrame")
        self._rtool_btns: dict[str, ttk.Button] = {}
        for i, name in enumerate(_REGION_TOOLS):
            btn = ttk.Button(frame, width=12,
                             text=f"{_REGION_ICONS[name]} "
                                  f"{self.t(f'map.rtool.{name}')} {i + 1}",
                             command=lambda n=name: self._set_region_tool(n))
            btn.pack(side="left", padx=1)
            self._rtool_btns[name] = btn
        ttk.Button(frame, text="➕ " + self.t("map.new_region"),
                   command=self._new_region).pack(side="left", padx=(8, 0))
        self._region_lbl = ttk.Label(frame, text=self.t("map.no_region_sel"),
                                     style="Muted.TLabel")
        self._region_lbl.pack(side="left", padx=(10, 0))
        return frame

    def _populate_palette(self) -> None:
        """Fill the visual-terrain palette bar with labelled index swatches."""
        if self._palette_built:
            return
        swatches = self.terrain_bmp.swatches()
        if not swatches:
            return
        self._palette_built = True
        self._swatch_widgets: dict[int, tk.Label] = {}
        for i, (index, rgb, name) in enumerate(swatches):
            luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
            sw = tk.Label(self._palette_frame, text=str(index), width=3,
                          bg=f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
                          fg="#000000" if luma > 128 else "#ffffff",
                          bd=1, relief="solid", cursor="hand2",
                          font=("Segoe UI", 7))
            sw.grid(row=i % 2, column=i // 2, padx=1, pady=1)
            sw.bind("<Button-1>",
                    lambda e, idx=index: self._select_visual_index(idx))
            self._swatch_widgets[index] = sw
        first = swatches[0][0]
        self._select_visual_index(first)

    def _select_visual_index(self, index: int) -> None:
        self._visual_index = index
        for idx, sw in getattr(self, "_swatch_widgets", {}).items():
            sw.configure(bd=3 if idx == index else 1,
                         relief="ridge" if idx == index else "solid")
        name = self.terrain_bmp.index_names().get(index, "?")
        self._visual_lbl.configure(text=f"{index} · {name}")

    # ------------------------------------------------------------ tool switching
    def _tool_hotkey(self, n: int) -> None:
        """Number key 1..5 → n-th tool of the active mode's tool set."""
        group = _tool_group(self._mode)
        tools = {"prov": _TOOLS, "height": _HEIGHT_TOOLS,
                 "visual": _VISUAL_TOOLS, "region": _REGION_TOOLS}[group]
        if n >= len(tools):
            return
        {"prov": self._set_tool, "height": self._set_height_tool,
         "visual": self._set_visual_tool,
         "region": self._set_region_tool}[group](tools[n])

    def _set_height_tool(self, name: str) -> None:
        self._height_tool = name
        for tool, btn in self._htool_btns.items():
            btn.configure(style="Accent.TButton" if tool == name else "TButton")
        self._apply_paint_state()

    def _set_visual_tool(self, name: str) -> None:
        self._visual_tool = name
        for tool, btn in self._vtool_btns.items():
            btn.configure(style="Accent.TButton" if tool == name else "TButton")
        self._apply_paint_state()

    def _set_region_tool(self, name: str) -> None:
        self._region_tool = name
        for tool, btn in self._rtool_btns.items():
            btn.configure(style="Accent.TButton" if tool == name else "TButton")
        self._apply_paint_state()

    def _apply_paint_state(self) -> None:
        """Sync the canvas drag behaviour + cursor with the active mode/tool."""
        mode = self._mode
        if mode == "height":
            self.canvas.paint_mode = True
            cursor = "dotbox"
        elif mode == "visual":
            self.canvas.paint_mode = self._visual_tool == "vbrush"
            cursor = {"vbrush": "pencil", "vfill": "spraycan",
                      "vpicker": "target"}[self._visual_tool]
        elif mode == "strat_region":
            self.canvas.paint_mode = False
            cursor = {"rselect": "hand2", "rassign": "crosshair"}[self._region_tool]
        else:
            self.canvas.paint_mode = self._tool == "brush"
            cursor = {"brush": "pencil", "fill": "spraycan", "picker": "target",
                      "state": "hand2"}.get(self._tool, "crosshair")
        self.canvas.canvas.configure(cursor=cursor)

    def _build_states_panel(self, root) -> None:
        panel = ttk.Frame(root, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)
        self._list_panel = panel
        self._list_visible = True
        self._grid_root.columnconfigure(0, minsize=260)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._tree = ttk.Treeview(panel, show="tree", selectmode="extended")
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        # Empty states are free indices waiting to be reused — flag them red.
        self._tree.tag_configure("empty", foreground=self.palette.danger)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def _build_inspector_panel(self, root) -> None:
        panel = ttk.Frame(root, style="Card.TFrame", padding=(6, 6))
        panel.grid(row=1, column=2, sticky="nsew", padx=(10, 0))
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        self._grid_root.columnconfigure(2, minsize=340)
        self._insp_host = panel
        self._nb = ttk.Notebook(panel)
        self.province_inspector = ProvinceInspector(self._nb, self)
        self.state_inspector = StateInspector(self._nb, self)
        self._nb.add(self.province_inspector, text=self.t("map.province"))
        self._nb.add(self.state_inspector, text=self.t("map.state"))
        self._nb.grid(row=0, column=0, sticky="nsew")
        self.batch_inspector = BatchStateInspector(panel, self)
        self.batch_inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(panel, text=self.t("map.select_hint"),
                                      style="CardMuted.TLabel", wraplength=300,
                                      justify="left")
        self._placeholder.grid(row=0, column=0, sticky="n", pady=16)
        self._show_inspector(None)

    def _show_inspector(self, which: str | None, focus: str | None = None) -> None:
        """`which`: None (placeholder) / "province" / "state" / "both" / "batch";
        `focus`: which tab to raise."""
        if which != "batch":
            self.batch_inspector.grid_remove()
        if which is None:
            self._nb.grid_remove()
            self._placeholder.grid()
            return
        if which == "batch":
            self._nb.grid_remove()
            self._placeholder.grid_remove()
            self.batch_inspector.grid()
            return
        # Remember the tab the user was on so a new selection keeps it (only falling
        # back to `focus` / the sole available tab when that tab isn't available now).
        try:
            prev = {0: "province", 1: "state"}.get(self._nb.index(self._nb.select()))
        except Exception:
            prev = None
        self._placeholder.grid_remove()
        self._nb.grid()
        avail = {"province": which in ("province", "both"),
                 "state": which in ("state", "both")}
        self._nb.tab(0, state="normal" if avail["province"] else "disabled")
        self._nb.tab(1, state="normal" if avail["state"] else "disabled")
        if prev and avail.get(prev):
            target = prev                      # preserve the current tab
        elif focus and avail.get(focus):
            target = focus
        else:
            target = "province" if avail["province"] else "state"
        self._nb.select(0 if target == "province" else 1)

    # ----------------------------------------------------------------- loading
    def _start_loading(self) -> None:
        if self._load_state != "idle":
            return
        self._load_state = "loading"

        def work():
            try:
                self.map.ensure_bitmap()
                self.map.defs
                self.states.list_states()
                # Auxiliary layers are optional — a missing heightmap/terrain
                # bmp only disables its mode, never the whole editor.
                self.heightmap.ensure()
                self.terrain_bmp.ensure()
                self._load_state = "ready"
            except Exception as exc:                      # noqa: BLE001
                self._load_error = str(exc)
                self._load_state = "error"

        threading.Thread(target=work, daemon=True).start()
        self._poll_loading()

    def _poll_loading(self) -> None:
        if self._load_state == "loading":
            self._loading_lbl.after(120, self._poll_loading)
            return
        if self._load_state == "error":
            self._loading_lbl.configure(
                text=self.t("map.load_error", error=self._load_error))
            return
        self._loading_lbl.grid_remove()
        self.canvas.fit_map()
        self.reload_tree()
        self.canvas.canvas.focus_set()      # hotkeys work right after loading
        self._update_sel_count()            # show the total province count
        if self.terrain_bmp.loaded:
            self._populate_palette()

    def _focus_map(self, _event=None) -> None:
        """Give the map keyboard focus unless a text entry is currently being edited."""
        try:
            focused = self.canvas.canvas.focus_get()
        except KeyError:
            focused = None
        if not isinstance(focused, (tk.Entry, ttk.Entry, tk.Text, ttk.Spinbox)):
            self.canvas.canvas.focus_set()

    def _map_size(self):
        return self.map.size if self.map.loaded else None

    def _render_view(self, mode, rect, scale, selected, highlight, rivers=False):
        if mode in ("height", "visual"):
            layer = self.heightmap if mode == "height" else self.terrain_bmp
            if layer.ensure():
                return layer.render(rect, scale)
            return self._missing_layer_image(rect, scale)
        return self.map.render_view(mode, rect, scale, selected=selected,
                                    highlight=highlight, rivers=rivers)

    def _missing_layer_image(self, rect, scale):
        from PIL import Image
        w = max(1, round((rect[2] - rect[0]) * scale))
        h = max(1, round((rect[3] - rect[1]) * scale))
        return Image.new("RGB", (w, h), (45, 47, 56))

    # -------------------------------------------------------------------- tree
    def reload_tree(self) -> None:
        self._refresh_tree()

    def refresh_tree_labels(self) -> None:
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        if not hasattr(self, "_tree"):
            return
        query = self._search.get().strip().lower()
        selected = self._tree.selection()
        self._tree.delete(*self._tree.get_children())
        self._items.clear()
        for st in self.states.list_states():
            label = f"{st.id} · {self._state_display_name(st)}"
            if st.owner:
                label += f" · {st.owner}"
            if not st.provinces:
                label += " · " + self.t("map.free_index")
            if query and query not in label.lower():
                continue
            iid = f"s::{st.id}"
            tags: tuple = ()
            if not st.provinces:
                tags = ("empty",)          # red — free index, reused first
            elif not st.in_mod:
                tags = ("vanilla",)
            self._tree.insert("", "end", iid=iid, text=label, tags=tags)
            self._items[iid] = ("state", st.id)
        keep = [i for i in selected if self._tree.exists(i)]
        if keep:
            self._tree_sel_guard = keep[0]
            self._tree.selection_set(keep)

    def _state_display_name(self, st) -> str:
        loc = self.loc.get(f"STATE_{st.id}", self.loc_language)
        return loc if loc else st.name

    def _on_tree_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        if len(sel) == 1 and getattr(self, "_tree_sel_guard", None) == sel[0]:
            self._tree_sel_guard = None       # programmatic echo — swallow once
            return
        state_ids = [self._items[i][1] for i in sel
                     if self._items.get(i) and self._items[i][0] == "state"]
        if len(state_ids) >= 2:               # Shift/Ctrl multi-select → batch editing
            self._show_batch_states(sorted(state_ids))
        elif len(state_ids) == 1:
            self.select_state(state_ids[0], zoom=True)

    def _states_of_provinces(self, pids) -> set[int]:
        prov_state, _ = self.map._political_maps()
        return {sid for p in pids if (sid := prov_state.get(p)) is not None}

    def _show_batch_states(self, state_ids: list[int]) -> None:
        """Highlight every province of the given states and show the batch editor."""
        self._flush_inspectors()
        self._selected_province = None
        self._state_doc = None
        provinces: set[int] = set()
        for sid in state_ids:
            st = self.states.get(sid)
            if st is not None:
                provinces |= set(st.provinces)
        self.canvas.set_selection(set(), highlight=provinces)
        self.batch_inspector.show(state_ids)
        self._show_inspector("batch")

    def _batch_edit_states(self, state_ids: list[int], mutate) -> int:
        """Apply `mutate(state_def)` to every state in `state_ids` at once (undoable).
        Vanilla states are copied into the mod first. Returns how many were changed."""
        if not state_ids:
            return 0
        refs_by_id = {r.state_id: r
                      for r in self.states.list_docs(include_vanilla=True)}
        affected = {str(self.context.mod.path / refs_by_id[sid].rel_file)
                    for sid in state_ids if sid in refs_by_id}
        before = self._snapshot_paths(affected)
        changed = 0
        for sid in state_ids:
            ref = refs_by_id.get(sid)
            if ref is None:
                continue
            if ref.is_vanilla:
                ref = self.states.copy_to_mod(ref)
            doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
            if doc.state is None:
                continue
            mutate(doc.state)
            self.states.save_doc(doc)
            self._dirty.discard(str(ref.path))
            self._dirty_docs.pop(str(ref.path), None)
            changed += 1
        self.states.invalidate()
        cache = getattr(self.states, "_doc_cache", None)
        if cache is not None:
            cache.clear()
        after = self._snapshot_paths(affected)
        for p in affected:
            before.setdefault(p, None)
        self._record(StateDocsCommand(before, after))
        self.map.invalidate_political()
        self._refresh_tree()
        self.canvas.refresh()
        return changed

    def batch_apply_states(self, state_ids: list[int], owner: str | None = None,
                           controller: str | None = None,
                           category: str | None = None) -> None:
        """Set the given non-None fields on every selected state at once."""
        def mutate(st) -> None:
            if owner is not None:
                st.set_owner(owner)
            if controller is not None:
                st.set_controller(controller)
            if category is not None:
                st.set_state_category(category)
        n = self._batch_edit_states(state_ids, mutate)
        self._status.configure(text=self.t("map.batch_applied", count=n))

    def batch_rename_states(self, state_ids: list[int], base: str,
                            language: str) -> None:
        """Localise every selected state as ``<base>_<n>`` (ascending id order;
        a single state gets `base` verbatim). Writes the value under each
        state's own name key (usually ``STATE_<id>``), so every script/GUI
        reference — which points at the key, never the display text — stays
        valid; nothing else needs rewriting."""
        ids = sorted(state_ids)
        refs_by_id = {r.state_id: r
                      for r in self.states.list_docs(include_vanilla=True)}
        renamed = 0
        for n, sid in enumerate(ids, start=1):
            key = f"STATE_{sid}"
            ref = refs_by_id.get(sid)
            if ref is not None:
                try:
                    doc = self.states.load(ref)
                    if doc.state is not None and doc.state.name_key:
                        key = doc.state.name_key
                except Exception:
                    pass
            value = base if len(ids) == 1 else f"{base}_{n}"
            self.loc.set(key, language, value)
            renamed += 1
        self.states.invalidate()
        self._refresh_tree()
        self.canvas.refresh()
        self._status.configure(text=self.t("map.batch_applied", count=renamed))

    def batch_cores(self, state_ids: list[int], add: str | None = None,
                    remove: str | None = None) -> None:
        """Add or remove a national core (``add_core_of``) on every selected state."""
        def mutate(st) -> None:
            cores = list(st.cores)
            if remove:
                cores = [c for c in cores if c != remove]
            if add and add not in cores:
                cores.append(add)
            st.set_cores(cores)
        n = self._batch_edit_states(state_ids, mutate)
        self._status.configure(text=self.t("map.batch_applied", count=n))

    # --------------------------------------------------------------- selection
    def select_state(self, state_id: int, zoom: bool = False) -> None:
        self._flush_inspectors()
        self._selected_state = state_id
        self._set_assign_mode(False)
        st = self.states.get(state_id)
        provinces = set(st.provinces) if st else set()
        if self._selected_province not in provinces:
            self._selected_province = None
        keep = ({self._selected_province}
                if self._selected_province in provinces else set())
        self.canvas.set_selection(keep, highlight=provinces)
        if zoom and provinces and self.map.loaded:
            bbox = self._state_bbox(provinces)
            if bbox is not None:
                self.canvas.zoom_to_bbox(bbox)
        self._open_state_doc(state_id)
        self._sync_inspectors(focus="state")

    def select_province(self, pid: int, zoom: bool = False) -> None:
        """Select a province (and its state, if any) programmatically."""
        self._flush_inspectors()
        self._selected_province = pid
        prov_state, _owners = self.map._political_maps()
        sid = prov_state.get(pid)
        self._selected_state = sid
        highlight = set()
        if sid is not None:
            st = self.states.get(sid)
            if st is not None:
                highlight = set(st.provinces)
            self._open_state_doc(sid)
            self._tree_show_state(sid)
        else:
            self._state_doc = None
        self.canvas.set_selection({pid}, highlight=highlight)
        if zoom:
            bbox = self.map.bbox_of(pid)
            if bbox is not None:
                self.canvas.zoom_to_bbox(bbox)
        self._sync_inspectors(focus="province")
        self._update_sel_count()

    def _flush_inspectors(self) -> None:
        self.state_inspector.flush_pending()
        self.province_inspector.flush_pending()

    def _tree_show_state(self, sid: int) -> None:
        """Reflect a state in the tree WITHOUT triggering select_state:
        `selection_set` delivers <<TreeviewSelect>> through the event queue,
        so a boolean reset right after the call would be cleared long before
        the event arrives — guard by the exact iid instead."""
        iid = f"s::{sid}"
        if self._tree.exists(iid):
            if self._tree.selection() != (iid,):
                self._tree_sel_guard = iid
                self._tree.selection_set(iid)
            self._tree.see(iid)

    def _open_state_doc(self, state_id: int) -> None:
        ref = self.states.doc_ref_for(state_id)
        if ref is None:
            self._state_doc = None
            return
        # A dirty (unsaved) document must keep its in-memory edits.
        doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
        self._state_doc = doc

    def _sync_inspectors(self, focus: str | None = None) -> None:
        """Refresh both inspector tabs from the current selection."""
        has_prov = (self._selected_province is not None
                    and self._selected_province in self.map.by_id)
        has_state = self._state_doc is not None and self._state_doc.state is not None
        if has_prov:
            self.province_inspector.show(self._selected_province)
        if has_state:
            self.state_inspector.show(self._state_doc,
                                      editable=not self._state_doc.ref.is_vanilla)
        which = ("both" if has_prov and has_state
                 else "province" if has_prov
                 else "state" if has_state else None)
        self._show_inspector(which, focus=focus)

    def _state_bbox(self, provinces: set[int]):
        boxes = [b for p in provinces if (b := self.map.bbox_of(p)) is not None]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _selectable(self, pid: int) -> bool:
        """With "select only ground" on, sea/lake provinces can't be selected."""
        if not self._ground_only.get():
            return True
        d = self.map.by_id.get(pid)
        return d is not None and d.type == "land"

    def _map_click(self, mx: int, my: int, event) -> None:
        if not self.map.loaded:
            return
        if self._mode == "height":
            return                       # height tools are drag-only
        if self._mode == "visual":
            self._visual_click(mx, my)
            return
        if self._mode == "strat_region":
            self._region_click(mx, my)
            return
        pid = self.map.province_at(mx, my)
        if self._assign_mode and pid is not None:
            self.assign_province_to_state(pid)
            return
        if self._tool == "state":
            if pid is not None:
                shift = bool(getattr(event, "state", 0) & 0x0001)
                self._select_state_provinces(pid, add=shift)
            return
        shift = bool(getattr(event, "state", 0) & 0x0001)
        if shift and self._tool == "select" and pid is not None:
            if self._selectable(pid):
                self._toggle_multi(pid)
            return
        if self._tool == "picker":
            if pid is not None:
                self.set_brush_target(pid)
            return
        if self._tool == "fill":
            self._fill_at(mx, my)
            return
        self._multi_sel.clear()
        if pid is None or not self._selectable(pid):
            self._selected_province = None
            self.canvas.set_selection(set())
            self._update_sel_count()
            return
        self.select_province(pid)

    def _select_state_provinces(self, pid: int, add: bool = False) -> None:
        """State tool: select the clicked province's whole state. A plain click selects
        only that state; Shift-click (`add`) toggles it in the multi-selection — a
        second Shift-click on the same state deselects it."""
        prov_state, _ = self.map._political_maps()
        sid = prov_state.get(pid)
        if sid is None:
            self._status.configure(text=self.t("map.no_state"))
            return
        st = self.states.get(sid)
        provinces = {p for p in (st.provinces if st else [pid])
                     if self._selectable(p)}
        if not provinces:
            return
        self._selected_province = None
        if add:
            current = set(self._multi_sel)
            if provinces <= current:      # already selected → Shift-click deselects
                current -= provinces
            else:
                current |= provinces
            self._multi_sel = current
        else:
            self._multi_sel = provinces   # plain click: select only this state
        self._show_multi_selection()

    # -------------------------------------------------------- multi-selection
    def _toggle_multi(self, pid: int) -> None:
        """Shift+click: grow/shrink the multi-selection for area generation."""
        if not self._multi_sel and self._selected_province is not None:
            self._multi_sel.add(self._selected_province)
        if pid in self._multi_sel:
            self._multi_sel.discard(pid)
        else:
            self._multi_sel.add(pid)
        self._show_multi_selection()

    def _rect_select(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Shift+drag rubber band: add every province in the rect."""
        if not self.map.loaded:
            return
        w, h = self.map.size
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w - 1, x1), min(h - 1, y1)
        if x0 > x1 or y0 > y1:
            return
        hit: set[int] = set()
        for code in np.unique(self.map.codes[y0:y1 + 1, x0:x1 + 1]):
            d = self.map.by_code.get(int(code))
            if d is not None and d.id != 0 and self._selectable(d.id):
                hit.add(d.id)
        # State tool: a rubber band selects whole states, not loose provinces.
        if self._tool == "state":
            hit = self._expand_to_states(hit)
        self._multi_sel |= hit
        self._show_multi_selection()

    def _expand_to_states(self, pids: set[int]) -> set[int]:
        """Grow a set of provinces to every province of the states they belong to
        (respecting 'select only ground'). Provinces with no state are kept as-is."""
        prov_state, _ = self.map._political_maps()
        out: set[int] = set()
        seen: set[int] = set()
        for p in pids:
            sid = prov_state.get(p)
            if sid is None:
                out.add(p)
                continue
            if sid in seen:
                continue
            seen.add(sid)
            st = self.states.get(sid)
            provinces = set(st.provinces) if st else {p}
            out |= {q for q in provinces if self._selectable(q)}
        return out

    def _show_multi_selection(self) -> None:
        self.canvas.set_selection(set(self._multi_sel))
        self._update_sel_count()
        if self._multi_sel:
            area = sum(self.map.area_of(p) for p in self._multi_sel)
            self._status.configure(text=self.t("map.multi_selected",
                                               count=len(self._multi_sel),
                                               area=area))
            # State tool: one whole state → its inspector; ≥2 → batch editing.
            if self._tool == "state":
                sids = sorted(self._states_of_provinces(self._multi_sel))
                if len(sids) >= 2:
                    self.batch_inspector.show(sids)
                    self._show_inspector("batch")
                elif len(sids) == 1:
                    self._selected_state = sids[0]
                    self._open_state_doc(sids[0])
                    self._tree_show_state(sids[0])
                    self._sync_inspectors(focus="state")
        else:
            self._status.configure(text="")

    def clear_multi_selection(self) -> None:
        if self._multi_sel:
            self._multi_sel.clear()
            keep = ({self._selected_province}
                    if self._selected_province is not None else set())
            self.canvas.set_selection(keep, highlight=self.canvas.highlight)
            self._status.configure(text="")
            self._update_sel_count()

    def _update_sel_count(self) -> None:
        """Bottom-right counter: total provinces normally; 'selected / total' while a
        multi-selection is active."""
        if not hasattr(self, "_sel_count"):
            return
        total = sum(1 for pid in self.map.by_id if pid != 0) if self.map.loaded else 0
        if self._multi_sel:
            text = self.t("map.selected_count",
                          count=len(self._multi_sel), total=total)
        else:
            text = self.t("map.province_total", total=total)
        self._sel_count.configure(text=text)

    # ----------------------------------------------------------- paint tools
    def _set_tool(self, name: str) -> None:
        self._tool = name
        for tool, btn in self._tool_btns.items():
            btn.configure(style="Accent.TButton" if tool == name else "TButton")
        self._apply_paint_state()

    def set_brush_target(self, pid: int) -> None:
        d = self.map.by_id.get(pid)
        if d is None:
            return
        self._brush_target = pid
        self._target_swatch.configure(bg=f"#{d.r:02x}{d.g:02x}{d.b:02x}")
        self._target_lbl.configure(text=f"#{pid} · {d.type}/{d.terrain}")

    def _require_target(self) -> int | None:
        if self._brush_target is None or self._brush_target not in self.map.by_id:
            messagebox.showinfo("ANKA", self.t("map.err.no_brush"))
            return None
        return self._brush_target

    # ----------------------------------------------- height / visual / regions
    def _visual_click(self, mx: int, my: int) -> None:
        if not self.terrain_bmp.ensure():
            return
        if self._visual_tool == "vpicker":
            idx = self.terrain_bmp.index_at(mx, my)
            if idx is not None:
                if idx in getattr(self, "_swatch_widgets", {}):
                    self._select_visual_index(idx)
                else:
                    self._visual_index = idx
                    name = self.terrain_bmp.index_names().get(idx, "?")
                    self._visual_lbl.configure(text=f"{idx} · {name}")
        elif self._visual_tool == "vfill":
            if self._visual_index is None:
                return
            ys, xs, old, new = self.terrain_bmp.flood_fill(mx, my,
                                                           self._visual_index)
            if len(ys):
                self._record(LayerPixelsCommand("terrain_bmp", "map.cmd.visual",
                                                ys, xs, old, new))
                self.canvas.refresh()
                self._status.configure(text=self.t("map.filled_px",
                                                   count=len(ys)))

    def _region_click(self, mx: int, my: int) -> None:
        pid = self.map.province_at(mx, my)
        if pid is None:
            return
        if self._region_tool == "rselect":
            doc = self.strat_regions.doc_for_member(pid)
            if doc is None:
                self._sel_region = None
                self._region_lbl.configure(text=self.t("map.no_region"))
                self.canvas.set_selection(set())
                return
            self._select_region(doc)
        else:                                   # rassign
            if self._sel_region is None:
                self._status.configure(text=self.t("map.no_region_sel"))
                return
            self._assign_to_region(pid, self._sel_region)

    def _select_region(self, doc) -> None:
        self._sel_region = doc.zone_id
        members = list(doc.members)
        self._region_lbl.configure(
            text=self.t("map.region_info", id=doc.zone_id,
                        name=doc.name or f"REGION_{doc.zone_id}",
                        count=len(members)))
        self.canvas.set_selection(set(), highlight=set(members))

    def _assign_to_region(self, pid: int, region: int) -> None:
        """Move the clicked province into the selected strategic region —
        together with its whole state: HOI4 requires every province of a state
        to share one strategic region, so a lone province must never switch
        region on its own. Stateless (sea/lake) provinces move individually.
        Undoable as one command."""
        if self.strat_regions.doc_for_zone(region) is None:
            return
        prov_state, _ = self.map._political_maps()
        sid = prov_state.get(pid)
        pids = [pid]
        if sid is not None:
            st = self.states.get(sid)
            if st is not None and st.provinces:
                pids = list(st.provinces)
        member_of = self.strat_regions.member_map()
        pids = [p for p in pids if member_of.get(p) != region]
        if not pids:
            return
        # Snapshot every affected zone's member list before the move.
        affected = {region}
        affected |= {member_of[p] for p in pids if p in member_of}
        before = {}
        for zid in affected:
            doc = self.strat_regions.doc_for_zone(zid)
            if doc is not None:
                before[zid] = list(doc.members)
        for p in pids:
            self.strat_regions.move_member(p, region)
        children = []
        for zid, old in before.items():
            doc = self.strat_regions.doc_for_zone(zid)
            new = list(doc.members) if doc is not None else old
            if new != old:
                children.append(ZoneMembersCommand("strat_regions", zid,
                                                   old, new))
        if children:
            self._record(children[0] if len(children) == 1
                         else CompoundCommand(children, "map.cmd.region"))
        self.map.invalidate_regions()
        self.canvas.refresh()
        target = self.strat_regions.doc_for_zone(region)
        if target is not None:
            self._select_region(target)
        if sid is not None:
            self._status.configure(text=self.t("map.region_assigned_state",
                                               state=sid, count=len(pids),
                                               region=region))
        else:
            self._status.configure(text=self.t("map.region_assigned",
                                               id=pid, region=region))

    def _new_region(self) -> None:
        doc = self.strat_regions.create_region()
        self.map.invalidate_regions()
        self._select_region(doc)
        self.canvas.refresh()
        self._status.configure(text=self.t("map.region_created",
                                           id=doc.zone_id))

    def _paint_layer(self, mx: int, my: int) -> None:
        """Drag-paint on the heightmap / visual terrain layer."""
        layer = self.heightmap if self._mode == "height" else self.terrain_bmp
        if not layer.ensure():
            return
        size = max(1, int(self._radius_var.get() or 1))
        shape = self._brush_shape()
        # Interpolate along the stroke so fast drags leave no gaps.
        points = [(mx, my)]
        if self._last_paint is not None:
            lx, ly = self._last_paint
            dist = max(abs(mx - lx), abs(my - ly))
            step = max(1, size)
            if dist > step:
                n = dist // step
                points = [(lx + (mx - lx) * (i + 1) // (n + 1),
                           ly + (my - ly) * (i + 1) // (n + 1))
                          for i in range(int(n))] + points
        for x, y in points:
            if self._mode == "height":
                delta = self.heightmap.stamp(
                    x, y, size, shape, self._height_tool,
                    strength=max(1, int(self._strength_var.get() or 1)),
                    level=max(0, min(255, int(self._level_var.get() or 0))))
            else:
                if self._visual_index is None:
                    return
                delta = self.terrain_bmp.stamp(x, y, size, shape,
                                               self._visual_index)
            if len(delta[0]):
                self._layer_stroke.append(delta)
        self._last_paint = (mx, my)
        self.canvas.refresh()

    def _end_layer_stroke(self) -> None:
        self._last_paint = None
        chunks, self._layer_stroke = self._layer_stroke, []
        if not chunks:
            return
        ys = np.concatenate([c[0] for c in chunks])
        xs = np.concatenate([c[1] for c in chunks])
        old = np.concatenate([c[2] for c in chunks])
        new = np.concatenate([c[3] for c in chunks])
        attr, label = (("heightmap", "map.cmd.height") if self._mode == "height"
                       else ("terrain_bmp", "map.cmd.visual"))
        self._record(LayerPixelsCommand(attr, label, ys, xs, old, new))

    def _paint(self, mx: int, my: int, _event) -> None:
        if self._mode in ("height", "visual"):
            self._paint_layer(mx, my)
            return
        if not self.map.loaded or self._tool != "brush":
            return
        pid = self._brush_target
        if pid is None or pid not in self.map.by_id:
            self._last_paint = None
            return
        # Spinbox value is the brush *size*: size 1 paints a single pixel (radius 0),
        # size 2 a small plus, etc. The disk radius is therefore size - 1.
        size = max(1, int(self._radius_var.get() or 1))
        radius = size - 1
        # Interpolate along the stroke so fast drags leave no gaps.
        points = [(mx, my)]
        if self._last_paint is not None:
            lx, ly = self._last_paint
            dist = max(abs(mx - lx), abs(my - ly))
            step = max(1, size)
            if dist > step:
                n = dist // step
                points = [(lx + (mx - lx) * (i + 1) // (n + 1),
                           ly + (my - ly) * (i + 1) // (n + 1))
                          for i in range(int(n))] + points
        for x, y in points:
            delta = self.map.paint_disk(x, y, radius, pid)
            if len(delta[0]):
                self._stroke_delta.append(delta)
        self._last_paint = (mx, my)
        self.canvas.refresh()

    def _paint_end(self) -> None:
        if self._mode in ("height", "visual"):
            self._end_layer_stroke()
            return
        self._last_paint = None
        chunks, self._stroke_delta = self._stroke_delta, []
        pid = self._brush_target
        children = []
        if chunks and pid is not None and pid in self.map.by_id:
            ys = np.concatenate([c[0] for c in chunks])
            xs = np.concatenate([c[1] for c in chunks])
            old = np.concatenate([c[2] for c in chunks])
            children.append(PixelsCommand(ys, xs, old, self.map.by_id[pid].code))
        if pid is not None:
            region_cmd = self._ensure_strategic_region(pid)
            if region_cmd is not None:
                children.append(region_cmd)
        if len(children) == 1:
            self._record(children[0])
        elif children:
            self._record(CompoundCommand(children, "map.cmd.paint"))

    def _fill_at(self, mx: int, my: int) -> None:
        pid = self._require_target()
        if pid is None or not self.map.loaded:
            return
        try:
            ys, xs, old = self.map.flood_fill(mx, my, pid)
        except KeyError:
            return
        if len(ys):
            self.canvas.refresh()
            self._status.configure(
                text=self.t("map.filled_px", count=len(ys)))
            children = [PixelsCommand(ys, xs, old, self.map.by_id[pid].code)]
            region_cmd = self._ensure_strategic_region(pid)
            if region_cmd is not None:
                children.append(region_cmd)
            self._record(children[0] if len(children) == 1
                         else CompoundCommand(children, "map.cmd.fill"))

    def _new_province(self) -> None:
        from .dialogs import NewProvinceDialog
        like = self.map.by_id.get(self._selected_province) \
            if self._selected_province is not None else None

        def submit(fields: dict) -> None:
            d = self.map.create_province(**fields)
            self._record(CreateDefCommand(d))
            self._value_options.pop("province_free", None)
            self.set_brush_target(d.id)
            self._set_tool("brush")
            self._status.configure(text=self.t("map.province_created", id=d.id))

        NewProvinceDialog(self._insp_host, self, submit, like=like)

    def _new_state(self) -> None:
        if not self.map.loaded:
            return
        from .dialogs import NewStateDialog
        # Land provinces currently selected seed the new state; sea/lake excluded.
        sel = set(self._multi_sel)
        if self._selected_province is not None:
            sel.add(self._selected_province)
        seed = sorted(p for p in sel
                      if (d := self.map.by_id.get(p)) is not None and d.type == "land")

        def submit(fields: dict) -> None:
            self.split_resources_pref = fields["split_resources"]
            ref = self._create_state_reusing(name=fields["name"],
                                             owner=fields["owner"],
                                             category=fields["category"],
                                             provinces=seed)
            sid = ref.state_id
            doc = self.states.load(ref)
            self.mark_dirty(doc)
            if fields["name"]:
                self.state_loc_set(f"STATE_{sid}", fields["name"])
            emptied: list[int] = []
            if seed:
                emptied = self._absorb_provinces(
                    doc, seed, split_resources=fields["split_resources"])
            self.map.invalidate_political()
            self._value_options.pop("province_free", None)
            self._refresh_tree()
            self.select_state(sid, zoom=bool(seed))
            if not seed:                      # no provinces yet — let the user paint them
                self._set_assign_mode(True)
            self._status.configure(text=self.t("map.state_created", id=sid))
            if emptied:
                self.handle_emptied_states(emptied)

        NewStateDialog(self._insp_host, self, submit, seed_count=len(seed),
                       split_resources=getattr(self, "split_resources_pref", True))

    def _create_state_reusing(self, *, name: str, owner: str, category: str,
                              provinces: list[int]):
        """Create a state, taking the smallest FREE INDEX (empty state) first —
        its file is overwritten in place, so the id sequence never grows a
        hole. Falls back to a brand-new id when no state is empty."""
        free = self.free_state_ids()
        if not free:
            return self.states.create_state(name=name, owner=owner,
                                            category=category,
                                            provinces=provinces)
        from ...services.state_service import build_state_root
        fid = free[0]
        ref0 = self.states.doc_ref_for(fid)
        mod_path = str(self.context.mod.path / ref0.rel_file)
        before = self._snapshot_paths([mod_path])
        root = build_state_root(fid, "", owner, category, provinces)
        ref = self.states.write_state_root(fid, root, rel_file=ref0.rel_file)
        after = self._snapshot_paths([mod_path])
        self._record(StateDocsCommand(before, after))
        self.states.invalidate()
        cache = getattr(self.states, "_doc_cache", None)
        if cache is not None:
            cache.clear()
        return ref

    def _absorb_provinces(self, new_doc, pids: list[int], *,
                          split_resources: bool) -> list[int]:
        """Move `pids` into `new_doc`'s state, removing them from their previous
        states (a land province belongs to exactly one state). Optionally split each
        source's manpower / industry / resources proportionally, and always carry over
        any victory points sitting on the moved provinces. Vanilla donors are copied
        into the mod first."""
        from ...services.state_service import (split_state_assets,
                                               transfer_victory_points)
        new_state = new_doc.state
        if new_state is None:
            return []
        pid_set = set(pids)
        by_state: dict[int, list[int]] = {}
        for st in self.states.list_states():
            if st.id == new_state.id:
                continue
            overlap = [p for p in st.provinces if p in pid_set]
            if overlap:
                by_state[st.id] = overlap
        for sid, taken in by_state.items():
            ref = self.states.doc_ref_for(sid)
            if ref is None:
                continue
            if ref.is_vanilla:
                ref = self.states.copy_to_mod(ref)
            src_doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
            src = src_doc.state
            if src is None:
                continue
            if split_resources:
                split_state_assets(src, new_state, len(taken), len(src.provinces))
            transfer_victory_points(src, new_state, pid_set)
            for p in taken:
                src.remove_province(p)
            self.mark_dirty(src_doc)
            self.states.refresh_info(src_doc)
        self.mark_dirty(new_doc)
        self.states.refresh_info(new_doc)
        # Donors the seeding emptied completely — the caller offers to delete them.
        return [sid for sid in by_state
                if (st := self.states.get(sid)) is not None and not st.provinces]

    def _generate_states(self) -> None:
        """Auto-partition the selected land provinces into N new states (the state-
        level analogue of 'generate area': clusters provinces instead of pixels)."""
        if not self.map.loaded:
            return
        sel = set(self._multi_sel)
        if self._selected_province is not None:
            sel.add(self._selected_province)
        provs = sorted(p for p in sel
                       if (d := self.map.by_id.get(p)) is not None and d.type == "land")
        if len(provs) < 2:
            messagebox.showinfo("ANKA", self.t("map.gen_states_need_selection"))
            return
        prov_state, _ = self.map._political_maps()
        selected_states = len({prov_state.get(p) for p in provs
                               if prov_state.get(p) is not None})
        from .dialogs import GenerateStatesDialog
        GenerateStatesDialog(
            self._insp_host, self, provs, self._apply_generate_states,
            split_resources=getattr(self, "split_resources_pref", True),
            total_states=len(self.states.list_states()),
            selected_states=selected_states)

    def _apply_generate_states(self, provs: list[int], groups: list[list[int]],
                               fields: dict) -> None:
        """Create one new state per cluster, undoably. States the selection fully
        empties are reused: the new clusters take their ids first (then contiguous new
        ids), so state numbering stays gap-free — HOI4 errors on id gaps."""
        from ...services.state_service import (StateDef, build_state_root,
                                               split_state_assets,
                                               transfer_victory_points)
        self.split_resources_pref = fields["split_resources"]
        prov_set = set(provs)
        prov_state, _ = self.map._political_maps()          # province → original state
        all_states = self.states.list_states()
        old_max = max((st.id for st in all_states), default=0)

        # Identify the source states (those overlapping the selection) and the eventual
        # mod path of each; note which the selection empties completely (freed ids).
        refs_by_id = {r.state_id: r
                      for r in self.states.list_docs(include_vanilla=True)}
        source_ids: list[int] = []
        freed_ids: list[int] = []
        src_rel: dict[int, str] = {}          # source id -> its original relative path
        src_provs: dict[int, set] = {}        # source id -> its original provinces
        affected: set[str] = set()
        for st in all_states:
            if not any(p in prov_set for p in st.provinces):
                continue
            ref = refs_by_id.get(st.id)
            if ref is None:
                continue
            source_ids.append(st.id)
            src_rel[st.id] = ref.rel_file
            src_provs[st.id] = set(st.provinces)
            affected.add(str(self.context.mod.path / ref.rel_file))
            if set(st.provinces) <= prov_set:
                freed_ids.append(st.id)
        freed_ids.sort()
        # Pre-existing empty states (free indices) join the reuse pool too —
        # their files are overwritten in place, keeping the id range compact.
        already_empty: list[int] = []
        for st in all_states:
            if st.provinces or st.id in src_rel:
                continue
            ref = refs_by_id.get(st.id)
            if ref is None:
                continue
            already_empty.append(st.id)
            src_rel[st.id] = ref.rel_file
            affected.add(str(self.context.mod.path / ref.rel_file))
        already_empty.sort()
        # Snapshot BEFORE copying vanilla sources into the mod, so undo of a vanilla
        # source removes its mod override entirely rather than leaving a copy.
        before = self._snapshot_paths(affected)

        # Now load each source's mod-side doc (copying vanilla in), its category & cores.
        source_docs: dict[int, object] = {}
        src_category: dict[int, str] = {}
        src_cores: dict[int, list] = {}
        src_owner: dict[int, str] = {}
        for sid in source_ids:
            ref = refs_by_id[sid]
            if ref.is_vanilla:
                ref = self.states.copy_to_mod(ref)
            doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
            source_docs[sid] = doc
            if doc.state is not None:
                src_category[sid] = doc.state.state_category
                src_cores[sid] = list(doc.state.cores)
                src_owner[sid] = doc.state.owner

        # Assign ids: a freed id goes to the cluster that inherits the most of that
        # state's original provinces (greedy by overlap), so the id — and thus the
        # state's file/name — stays where the original state was instead of jumping
        # across the map. Remaining clusters get fresh contiguous ids.
        n = len(groups)
        group_sets = [set(g) for g in groups]
        overlaps = sorted(
            ((len(group_sets[gi] & src_provs[sid]), gi, sid)
             for gi in range(n) for sid in freed_ids
             if group_sets[gi] & src_provs[sid]),
            reverse=True)
        gi_to_id: dict[int, int] = {}
        used_freed: set[int] = set()
        for _ov, gi, sid in overlaps:
            if gi in gi_to_id or sid in used_freed:
                continue
            gi_to_id[gi] = sid
            used_freed.add(sid)
        # Remaining clusters: freed-but-unmatched ids and pre-existing free
        # indices first (smallest first), only then brand-new contiguous ids.
        free_pool = sorted((set(freed_ids) - used_freed) | set(already_empty))
        final_ids: list[int] = []
        next_new = old_max + 1
        for gi in range(n):
            if gi in gi_to_id:
                final_ids.append(gi_to_id[gi])
            elif free_pool:
                final_ids.append(free_pool.pop(0))
            else:
                final_ids.append(next_new)
                next_new += 1
        match = fields.get("match_categories", False)
        keep_cores = fields.get("original_cores", True)
        keep_owners = fields.get("keep_owners", True)

        # Phase 1 — build each new state and pull its share of assets from the sources.
        new_roots: list = []
        for gi, group in enumerate(groups):
            fid = final_ids[gi]
            dom = self._dominant_source_id(group, prov_state)
            cat = (src_category.get(dom, fields["category"]) if match
                   else fields["category"])
            # "Keep owners": inherit the dominant source state's owner tag so
            # generated states stay with their current countries.
            owner = (src_owner.get(dom) or fields["owner"]) if keep_owners \
                else fields["owner"]
            root = build_state_root(fid, "", owner, cat, group)
            new_state = StateDef(root.get_block("state"))
            if keep_cores and src_cores.get(dom):
                new_state.set_cores(src_cores[dom])   # inherit original national cores
            by_src: dict[int, list[int]] = {}
            for p in group:
                by_src.setdefault(prov_state.get(p), []).append(p)
            for sid, taken in by_src.items():
                sdoc = source_docs.get(sid)
                if sdoc is None or sdoc.state is None:
                    continue
                src = sdoc.state
                if fields["split_resources"]:
                    split_state_assets(src, new_state, len(taken), len(src.provinces))
                transfer_victory_points(src, new_state, set(taken))
                for p in taken:
                    src.remove_province(p)
            new_roots.append((fid, root))

        # Phase 2 — write to disk. A reused id keeps its source's original filename so
        # HOI4 overrides that state; new ids get an auto-generated name.
        reused = set(final_ids)
        for sid, sdoc in source_docs.items():
            path = str(sdoc.ref.path)
            if sdoc.state is not None and sdoc.state.provinces:
                self.states.save_doc(sdoc)        # partially emptied → keep reduced
            elif sid not in reused:
                # Emptied & id not reused → keep as an empty placeholder (free
                # index): deleting the file would punch a hole in the id range.
                name_key = (sdoc.state.name_key if sdoc.state else "") \
                    or f"STATE_{sid}"
                category = (sdoc.state.state_category if sdoc.state else "") \
                    or "rural"
                self.states.write_state_root(
                    sid, build_state_root(sid, name_key, "", category, []),
                    rel_file=sdoc.ref.rel_file)
            # emptied & id reused → left as-is; the new write below overwrites it
            self._dirty.discard(path)
            self._dirty_docs.pop(path, None)
        for fid, root in new_roots:
            ref = self.states.write_state_root(fid, root, rel_file=src_rel.get(fid))
            affected.add(str(ref.path))
        self.states.invalidate()
        cache = getattr(self.states, "_doc_cache", None)
        if cache is not None:
            cache.clear()

        after = self._snapshot_paths(affected)
        for p in affected:
            before.setdefault(p, None)
        self._record(StateDocsCommand(before, after))

        self.map.invalidate_political()
        self._value_options.pop("province_free", None)
        self._multi_sel.clear()
        self._state_doc = None
        self._selected_state = None
        self._refresh_tree()
        if final_ids:
            self.select_state(final_ids[0], zoom=True)
        self._status.configure(text=self.t("map.states_created", count=n))

    def _dominant_source_id(self, group: list[int], prov_state: dict) -> int | None:
        """The original state contributing the most provinces to this cluster — used to
        inherit its category / national cores when the options are on."""
        counts: dict[int, int] = {}
        for p in group:
            sid = prov_state.get(p)
            if sid is not None:
                counts[sid] = counts.get(sid, 0) + 1
        return max(counts, key=counts.get) if counts else None

    def _snapshot_paths(self, paths) -> dict:
        from pathlib import Path
        out: dict = {}
        for p in paths:
            pp = Path(p)
            out[str(pp)] = pp.read_text(encoding="utf-8") if pp.exists() else None
        return out

    def _flush_state_paths(self, paths) -> None:
        for p in list(paths):
            doc = self._dirty_docs.get(p)
            if doc is not None:
                try:
                    self.states.save_doc(doc)
                except Exception:                              # noqa: BLE001
                    pass
                self._dirty.discard(p)
                self._dirty_docs.pop(p, None)

    def partition_provinces(self, provs: list[int], n: int, seed: int = 0,
                            within_borders: bool = False) -> list[list[int]]:
        """Cluster `provs` into `n` groups. With `within_borders` the clusters
        are additionally refined along existing state borders, so no generated
        state ever spans two current states.

        A generated state must never span two strategic regions (HOI4 requires a state's
        provinces to share one), so clusters are always refined along region borders."""
        region_of = self.strat_regions.member_map()
        state_of: dict[int, int] | None = None
        if within_borders:
            prov_state, _ = self.map._political_maps()
            state_of = prov_state
        return self._cluster_pixels(provs, n, seed, region_of, state_of)

    def _cluster_pixels(self, provs: list[int], n: int, seed: int,
                        region_of: dict[int, int],
                        state_of: dict[int, int] | None = None) -> list[list[int]]:
        """Grow `n` regions over the provinces' pixels, then assign each province
        wholesale to the cluster owning most of its pixels. Groups are keyed by
        (cluster, strategic region[, state]) so a cluster spanning two regions —
        or, with `state_of`, two existing states — is split accordingly."""
        from ...services.region_gen import split_region
        bbox = self._state_bbox(set(provs))
        if bbox is not None and n > 1:
            x0, y0, x1, y1 = bbox
            codes = self.map.codes[y0:y1 + 1, x0:x1 + 1]
            sel_codes = [self.map.by_id[p].code for p in provs]
            mask = np.isin(codes, sel_codes)
            labels = split_region(mask, n, seed=seed)
        else:
            codes = labels = None
        groups: dict[tuple, list[int]] = {}
        for p in provs:
            if labels is not None:
                pm = (codes == self.map.by_id[p].code) & (labels > 0)
                lab = int(np.bincount(labels[pm].ravel()).argmax()) if pm.any() else 1
            else:
                lab = 1
            key = (lab, region_of.get(p),
                   state_of.get(p) if state_of is not None else None)
            groups.setdefault(key, []).append(p)
        return [g for _key, g in sorted(
            groups.items(),
            key=lambda kv: (kv[0][0], kv[0][1] is None, kv[0][1] or 0,
                            kv[0][2] is None, kv[0][2] or 0))
            if g]

    def generate_states_preview(self, provs: list[int], groups: list[list[int]]):
        """Build a PIL preview image of the province clustering (each group a colour)."""
        from PIL import Image
        bbox = self._state_bbox(set(provs))
        if bbox is None:
            return None
        x0, y0, x1, y1 = bbox
        codes = self.map.codes[y0:y1 + 1, x0:x1 + 1]
        colors = self.map.free_colors(len(groups)) or [(200, 80, 80)]
        rgb = np.full((codes.shape[0], codes.shape[1], 3), (45, 47, 56), np.uint8)
        for gi, group in enumerate(groups):
            codeset = [self.map.by_id[p].code for p in group]
            rgb[np.isin(codes, codeset)] = colors[gi % len(colors)]
        return Image.fromarray(rgb, "RGB")

    # ------------------------------------------------------------ replace_path
    def _states_replaced(self) -> bool:
        """True once the mod declares ``replace_path="history/states"`` — the
        vanilla state files no longer load, so deletion can be physical."""
        from ...domain.mod import _replaces
        return _replaces(self.context.mod.replace_paths, "history/states")

    def _make_replace_path(self) -> None:
        """One-shot, irreversible: copy every remaining vanilla state into the
        mod, then declare ``replace_path="history/states"`` in BOTH descriptors
        (in-folder descriptor.mod + the launcher-side .mod)."""
        if self._states_replaced():
            return
        vanilla = [r for r in self.states.list_docs(include_vanilla=True)
                   if r.is_vanilla]
        if not messagebox.askyesno(
                "ANKA", self.t("map.confirm_make_replace", count=len(vanilla))):
            return
        try:
            for ref in vanilla:
                self.states.copy_to_mod(ref)
            from ...services.mod_repository import ModRepository
            repo = ModRepository(self.services.settings.current)
            written = repo.add_replace_paths(self.context.mod,
                                             ["history/states"])
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", str(exc))
            return
        # The undo history predates the switch — replaying it could resurrect
        # pre-replace file layouts.
        self.commands.clear()
        self._update_undo_buttons()
        self.states.invalidate()
        cache = getattr(self.states, "_doc_cache", None)
        if cache is not None:
            cache.clear()
        self.map.invalidate_political()
        self._value_options.pop("province_free", None)
        self._refresh_tree()
        self.canvas.refresh()
        self._replace_btn.pack_forget()
        messagebox.showinfo(
            "ANKA", self.t("map.replace_done", count=len(vanilla),
                           files="\n".join(str(w) for w in written)))

    # ------------------------------------------------- state emptying (delete)
    # HOI4 crashes when state ids are not contiguous, so states are never
    # physically deleted: "deleting" strips one down to an empty placeholder
    # that keeps its id — a FREE INDEX (red in the tree) that new states and
    # the generators reuse first. Works for vanilla states too (the empty
    # placeholder becomes a mod override with the same filename).
    def free_state_ids(self) -> list[int]:
        """Ids of empty states — free indices for new states, smallest first."""
        return sorted(st.id for st in self.states.list_states()
                      if not st.provinces)

    def _empty_state_files(self, sids) -> list[int]:
        """"Delete" states, one undoable command. With ``replace_path`` active
        the files are removed for real (vanilla can't leak back); otherwise
        they are rewritten as empty placeholders (id + name key + category
        kept) so the id numbering never gets a hole."""
        if self._states_replaced():
            return self._hard_delete_states(sids)
        from ...services.state_service import build_state_root
        refs: dict[int, object] = {}
        for sid in sorted(set(sids)):
            ref = self.states.doc_ref_for(sid)
            if ref is not None:
                refs[sid] = ref
        if not refs:
            return []
        mod_paths = {sid: str(self.context.mod.path / ref.rel_file)
                     for sid, ref in refs.items()}
        before = self._snapshot_paths(mod_paths.values())
        for sid, ref in refs.items():
            try:
                doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
                st = doc.state
            except Exception:                              # noqa: BLE001
                st = None
            name_key = (st.name_key if st is not None else "") or f"STATE_{sid}"
            category = (st.state_category if st is not None else "") or "rural"
            root = build_state_root(sid, name_key, "", category, [])
            self.states.write_state_root(sid, root, rel_file=ref.rel_file)
            for p in (str(ref.path), mod_paths[sid]):
                self._dirty.discard(p)
                self._dirty_docs.pop(p, None)
        after = self._snapshot_paths(mod_paths.values())
        self._record(StateDocsCommand(before, after))
        self.states.invalidate()
        cache = getattr(self.states, "_doc_cache", None)
        if cache is not None:
            cache.clear()
        self.map.invalidate_political()
        self._value_options.pop("province_free", None)
        self._refresh_tree()
        self.canvas.refresh()
        return sorted(refs)

    def _hard_delete_states(self, sids) -> list[int]:
        """replace_path mode: physically delete the states' files (undoable)."""
        refs: dict[int, object] = {}
        for sid in sorted(set(sids)):
            ref = self.states.doc_ref_for(sid)
            if ref is not None and not ref.is_vanilla:
                refs[sid] = ref
        if not refs:
            return []
        paths = [str(ref.path) for ref in refs.values()]
        before = self._snapshot_paths(paths)
        for sid, ref in refs.items():
            self.states.delete_doc(ref)
            self._dirty.discard(str(ref.path))
            self._dirty_docs.pop(str(ref.path), None)
            if self._selected_state == sid:
                self._selected_state = None
                self._state_doc = None
        self._record(StateDocsCommand(before, {p: None for p in paths}))
        self.states.invalidate()
        self.map.invalidate_political()
        self._value_options.pop("province_free", None)
        self._refresh_tree()
        self.canvas.refresh()
        if self._state_doc is None:
            self.canvas.set_selection(set())
            self._show_inspector(None)
        return sorted(refs)

    def handle_emptied_states(self, sids) -> None:
        """After provinces moved away: normalize states left empty into clean
        placeholders (drops leftover VPs/buildings) and point out the freed
        indices in the status bar — they show red in the tree. With
        replace_path active, offer real deletion instead."""
        empty = sorted({sid for sid in sids
                        if (st := self.states.get(sid)) is not None
                        and not st.provinces})
        if not empty:
            return
        if self._states_replaced():
            if not messagebox.askyesno(
                    "ANKA", self.t("map.confirm_delete_empty",
                                   ids=", ".join(map(str, empty)))):
                return
            done = self._empty_state_files(empty)
            self._status.configure(
                text=self.t("map.states_deleted_hard", count=len(done)))
            return
        self._empty_state_files(empty)
        self._status.configure(
            text=self.t("map.states_now_free",
                        ids=", ".join(map(str, empty))))

    def batch_delete_states(self, state_ids: list[int]) -> None:
        """Batch inspector's delete button: empty/delete every selected state."""
        done = self._empty_state_files(state_ids)
        self._multi_sel.clear()
        self.canvas.set_selection(set())
        self._show_inspector(None)
        self._update_sel_count()
        if not done:
            return
        if self._states_replaced():
            self._status.configure(
                text=self.t("map.states_deleted_hard", count=len(done)))
        else:
            self._status.configure(
                text=self.t("map.states_now_free",
                            ids=", ".join(map(str, done))))

    # ------------------------------------------------------- province splitting
    def split_province_dialog(self, pid: int) -> None:
        self._open_split_dialog([pid])

    def _split_selection(self) -> None:
        """Toolbar entry point: split the multi-selection (or the single
        selected province)."""
        pids = sorted(self._multi_sel)
        if not pids and self._selected_province is not None:
            pids = [self._selected_province]
        if not pids:
            messagebox.showinfo("ANKA", self.t("map.err.no_selection"))
            return
        self._open_split_dialog(pids)

    def _open_split_dialog(self, pids: list[int]) -> None:
        pids = [p for p in pids if p in self.map.by_id]
        if not self.map.loaded or not pids \
                or all(self.map.area_of(p) == 0 for p in pids):
            messagebox.showinfo("ANKA", self.t("map.err.no_pixels"))
            return
        from .dialogs import SplitProvinceDialog
        SplitProvinceDialog(self._insp_host, self, pids, self.apply_split)

    def apply_split(self, pids: list[int], labels, bbox, colors) -> None:
        """Apply previewed split labels over one or several source provinces.

        Each cluster's donor (majority pixel contributor) defines the new
        province's definition fields, target state and strategic region."""
        pids = [p for p in pids if p in self.map.by_id]
        if not pids:
            return
        # Strategic-region snapshots BEFORE the sync (for the undo commands).
        region_old: dict[int, list[int]] = {}
        for pid in pids:
            doc = self.strat_regions.doc_for_member(pid)
            if doc is not None and doc.zone_id not in region_old:
                region_old[doc.zone_id] = list(doc.members)

        result = self.map.apply_split_area(pids, labels, bbox, colors)
        new_ids = [d.id for d in result.new_defs]
        donor_of = {pid_l: donor for pid_l, donor
                    in zip(result.ids, result.donors) if pid_l in new_ids}

        children: list = [CreateDefCommand(d) for d in result.new_defs]
        if result.deltas:
            ys = np.concatenate([c[0] for c in result.deltas])
            xs = np.concatenate([c[1] for c in result.deltas])
            old = np.concatenate([c[2] for c in result.deltas])
            new = np.concatenate([np.full(len(c[0]), c[3], dtype=np.uint32)
                                  for c in result.deltas])
            children.append(PixelsCommand(ys, xs, old, new))

        # Keep invariant "every land province belongs to exactly one state":
        # offer to add each new province to its donor's state.
        prov_state, _ = self.map._political_maps()
        by_state: dict[int, list[int]] = {}
        for nid, donor in donor_of.items():
            sid = prov_state.get(donor)
            if sid is not None and self.map.by_id[nid].type == "land":
                by_state.setdefault(sid, []).append(nid)
        if by_state and messagebox.askyesno(
                "ANKA", self.t("map.split_add_to_state",
                               state=", ".join(str(s) for s in sorted(by_state)))):
            for sid, nids in sorted(by_state.items()):
                ref = self.states.doc_ref_for(sid)
                if ref is None:
                    continue
                if ref.is_vanilla:
                    ref = self.states.copy_to_mod(ref)
                doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
                if doc.state is None:
                    continue
                old_provinces = list(doc.state.provinces)
                for nid in nids:
                    doc.state.add_province(nid)
                children.append(StateProvincesCommand(
                    sid, old_provinces, list(doc.state.provinces)))
                self.mark_dirty(doc)
                self.states.refresh_info(doc)
                if self._state_doc is not None and \
                        self._state_doc.ref.path == doc.ref.path:
                    self._state_doc = doc
                    self.state_inspector.refresh_provinces_view()
        # Strategic regions must stay consistent: new provinces inherit their
        # donor's region.
        touched = self.strat_regions.sync_provinces(donor_of, [])
        for zone_doc in touched:
            if zone_doc.zone_id in region_old:
                children.append(ZoneMembersCommand(
                    "strat_regions", zone_doc.zone_id,
                    region_old[zone_doc.zone_id], list(zone_doc.members)))
        self._record(CompoundCommand(children, "map.cmd.split"))
        self._multi_sel.clear()
        self.map.invalidate_political()
        self._value_options.pop("province_free", None)
        self.canvas.refresh()
        self._refresh_tree()
        note = (" · " + self.t("map.strat_synced", count=len(touched))
                if touched else "")
        if result.orphaned:
            note += " · " + self.t("map.split_orphaned",
                                   ids=", ".join(map(str, result.orphaned)))
        self._status.configure(
            text=self.t("map.split_done", count=len(result.ids)) + note)

    def _ensure_strategic_region(self, pid: int):
        """A drawn province must live in exactly one strategic region: if it is
        in none, inherit the majority region of its pixel neighbors.
        Returns the recorded-state `ZoneMembersCommand` (already executed) or
        None when nothing had to change."""
        if self.strat_regions.doc_for_member(pid) is not None:
            return None
        if self.map.area_of(pid) == 0:
            return None
        votes: dict[int, int] = {}
        for nb in self.map.neighbors_of(pid):
            doc = self.strat_regions.doc_for_member(nb)
            if doc is not None:
                votes[doc.zone_id] = votes.get(doc.zone_id, 0) + 1
        if not votes:
            return None
        region = max(votes, key=votes.get)
        target = self.strat_regions.doc_for_zone(region)
        old_members = list(target.members) if target is not None else []
        self.strat_regions.move_member(pid, region)
        self.map.invalidate_regions()
        self._status.configure(
            text=self.t("map.strat_assigned", id=pid, region=region))
        target = self.strat_regions.doc_for_zone(region)
        return ZoneMembersCommand("strat_regions", region, old_members,
                                  list(target.members) if target else old_members)

    def _map_hover(self, mx, my) -> None:
        if mx is None or not self.map.loaded:
            self._status.configure(text="")
            self._coord_lbl.configure(text="")
            return
        # Top-left origin (image coordinates): (0,0) is the map's top-left corner.
        self._coord_lbl.configure(text=f"{mx}, {my}")
        if self._mode == "height":
            value = self.heightmap.value_at(mx, my)
            self._status.configure(
                text=f"{mx}, {my}" + (f" · h={value}" if value is not None else ""))
            return
        if self._mode == "visual":
            idx = self.terrain_bmp.index_at(mx, my)
            if idx is not None:
                name = self.terrain_bmp.index_names().get(idx, "?")
                self._status.configure(text=f"{mx}, {my} · {idx} · {name}")
            else:
                self._status.configure(text=f"{mx}, {my}")
            return
        pid = self.map.province_at(mx, my)
        if pid is None:
            self._status.configure(text=f"{mx}, {my}")
            return
        d = self.map.by_id.get(pid)
        extra = f" · {d.type}/{d.terrain}" if d is not None else ""
        if self._mode == "strat_region":
            doc = self.strat_regions.doc_for_member(pid)
            extra += (f" · {doc.name or doc.zone_id}" if doc is not None
                      else f" · {self.t('map.no_region')}")
        self._status.configure(text=f"{mx}, {my} · #{pid}{extra}")

    def goto_province(self, pid: int) -> None:
        """Zoom to a province keeping the state context (no tab switch)."""
        bbox = self.map.bbox_of(pid)
        if bbox is not None:
            self.canvas.zoom_to_bbox(bbox)
        self._selected_province = pid
        keep_highlight = set(self.canvas.highlight)
        self.canvas.set_selection({pid}, highlight=keep_highlight)
        self.province_inspector.show(pid)
        self._sync_inspectors()

    def goto_state_of_province(self, pid: int) -> None:
        prov_state, _ = self.map._political_maps()
        sid = prov_state.get(pid)
        if sid is not None:
            self._open_state_doc(sid)
            self._selected_state = sid
            self._tree_show_state(sid)
            self._sync_inspectors(focus="state")

    # -------------------------------------------------- province definition edit
    def set_province_def(self, pid: int, **fields) -> None:
        d = self.map.by_id.get(pid)
        if d is None:
            return
        old = {key: getattr(d, key) for key in fields}
        if old == fields:
            return
        self.map.set_def(pid, **fields)
        commands = [SetDefCommand(pid, old, dict(fields))]
        if "type" in fields:
            # a type flip changes who counts as coastal — the province itself
            # and every neighbor (sea neighbors make land coastal; land makes
            # sea coastal; lake never is)
            for q, was, now in self.map.recompute_coastal_around(pid):
                commands.append(SetDefCommand(q, {"coastal": was},
                                              {"coastal": now}))
        self._record(commands[0] if len(commands) == 1
                     else CompoundCommand(commands, "map.cmd.def"))
        self.canvas.refresh()          # terrain/type feed the render LUTs

    def land_terrains(self) -> list[str]:
        return self.terrain.land_terrains()

    def water_terrains(self) -> list[str]:
        return self.terrain.water_terrains()

    def continent_options(self) -> list[tuple[str, int]]:
        opts = [("0 · —", 0)]
        for i, name in enumerate(self.map.continents(), start=1):
            opts.append((f"{i} · {name}", i))
        return opts

    def province_facts(self, pid: int) -> str:
        area = self.map.area_of(pid)
        bbox = self.map.bbox_of(pid)
        lines = [f"{self.t('map.area')}: {area} px"]
        if bbox is not None:
            lines.append(f"bbox: {bbox[0]},{bbox[1]} – {bbox[2]},{bbox[3]}")
        return "\n".join(lines)

    def province_neighbors(self, pid: int) -> list[int]:
        return self.map.neighbors_of(pid)

    # ------------------------------------------------------- province assigning
    def toggle_assign_mode(self) -> None:
        self._set_assign_mode(not self._assign_mode)

    def _set_assign_mode(self, active: bool) -> None:
        if active == self._assign_mode:
            return
        self._assign_mode = active
        self.state_inspector.set_assign_active(active)
        if active:
            self._assign_lbl.pack(side="left", padx=10)
        else:
            self._assign_lbl.pack_forget()

    def assign_province_to_state(self, pid: int) -> None:
        """Add province `pid` to the inspected state (mod copy first if needed),
        removing it from its previous state after confirmation."""
        doc = self._state_doc
        if doc is None or doc.state is None:
            return
        if doc.ref.is_vanilla:
            messagebox.showinfo("ANKA", self.t("map.err.assign_vanilla"))
            return
        d = self.map.by_id.get(pid)
        if d is None:
            return
        if d.type == "sea":       # lakes occur inside vanilla states, sea never
            messagebox.showinfo("ANKA", self.t("map.err.sea_in_state"))
            return
        if pid in doc.state.provinces:
            return
        # Conflict: province already belongs to another state.
        prev = next((st for st in self.states.list_states()
                     if pid in st.provinces), None)
        children: list = []
        if prev is not None:
            if not messagebox.askyesno(
                    "ANKA", self.t("map.confirm_move_province",
                                   id=pid, state=prev.id)):
                return
            prev_ref = self.states.doc_ref_for(prev.id)
            if prev_ref is None:
                return
            if prev_ref.is_vanilla:
                prev_ref = self.states.copy_to_mod(prev_ref)
            prev_doc = (self._dirty_docs.get(str(prev_ref.path))
                        or self.states.load(prev_ref))
            if prev_doc.state is not None:
                old_prev = list(prev_doc.state.provinces)
                prev_doc.state.remove_province(pid)
                children.append(StateProvincesCommand(
                    prev.id, old_prev, list(prev_doc.state.provinces)))
                self.mark_dirty(prev_doc)
                self.states.refresh_info(prev_doc)
        old_target = list(doc.state.provinces)
        doc.state.add_province(pid)
        children.append(StateProvincesCommand(
            doc.state.id, old_target, list(doc.state.provinces)))
        self._record(children[0] if len(children) == 1
                     else CompoundCommand(children, "map.cmd.assign"))
        self.mark_dirty(doc)
        self.states.refresh_info(doc)
        self.provinces_changed()
        self.state_inspector.refresh_provinces_view()
        # Merging states province by province may leave the donor empty —
        # offer to delete it right away (and warn about the id gap).
        if prev is not None:
            self.handle_emptied_states([prev.id])

    def provinces_changed(self) -> None:
        """State membership changed: refresh political layers, highlight, tree."""
        doc = self._state_doc
        if doc is not None and doc.state is not None:
            self.states.refresh_info(doc)
        self.map.invalidate_political()
        self._value_options.pop("province_free", None)
        if doc is not None and doc.state is not None:
            provinces = set(doc.state.provinces)
            keep = ({self._selected_province}
                    if self._selected_province in provinces else set())
            self.canvas.set_selection(keep, highlight=provinces)
        else:
            self.canvas.refresh()
        self._refresh_tree()

    def political_changed(self) -> None:
        """Owner/controller changed: recolor owner mode + tree labels."""
        doc = self._state_doc
        if doc is not None and doc.state is not None:
            self.states.refresh_info(doc)
        self.map.invalidate_political()
        self.canvas.refresh()
        self._refresh_tree()

    # -------------------------------------------------------- state id reassign
    def reassign_state_id(self, doc, new_id: int) -> bool:
        """Re-number a mod state and migrate everything keyed by the id:
        the file name, the ``STATE_<id>`` localisation (all languages) and
        state-id references inside the mod's map folder. Used to close id
        gaps, which crash the game."""
        from ...config.constants import HOI4_LANGUAGES
        state = doc.state
        if state is None:
            return False
        old_id = state.id
        old_path = str(doc.ref.path)
        old_key = state.name_key or f"STATE_{old_id}"
        conventional = old_key == f"STATE_{old_id}"
        if conventional:
            state.set_name_key(f"STATE_{new_id}")
        try:
            self.states.change_state_id(doc, new_id)
        except (ValueError, PermissionError) as exc:
            if conventional:
                state.set_name_key(old_key)
            messagebox.showerror("ANKA", str(exc))
            return False

        if conventional:
            new_key = f"STATE_{new_id}"
            captured: dict[str, str] = {}
            for lang in HOI4_LANGUAGES:
                value = self.loc.get(old_key, lang)
                if value:
                    captured[lang] = value
            self.loc.rename_key(old_key, new_key)   # moves mod-side entries
            for lang, value in captured.items():
                if not self.loc.has(new_key, lang):  # vanilla-sourced → copy
                    self.loc.set(new_key, lang, value)

        changed = self._rewrite_map_state_ids(old_id, new_id)

        # the renamed file was already written; stale dirty flags would
        # resurrect the old path on save_all
        self._dirty.discard(old_path)
        self._dirty_docs.pop(old_path, None)
        # command history refers to the old id — replaying it would corrupt
        self.commands.clear()
        self._update_undo_buttons()
        self.political_changed()
        self._open_state_doc(new_id)
        if changed:
            messagebox.showinfo(
                "ANKA", self.t("map.reassign_map_files",
                               files=", ".join(changed)))
        return True

    def _rewrite_map_state_ids(self, old_id: int, new_id: int) -> list[str]:
        """Rewrite state-id references in the map folder (``buildings.txt``
        CSV rows; ``airports.txt`` / ``rocketsites.txt`` in older layouts).
        The effective file is copied into the mod when it comes from a lower
        layer. Returns the rel paths that changed."""
        import re as _re
        changed: list[str] = []
        for filename in ("buildings.txt", "airports.txt", "rocketsites.txt"):
            rel = f"map/{filename}"
            source = next((root / rel for root in self.context.search_roots(rel)
                           if (root / rel).exists()), None)
            if source is None:
                continue
            try:
                text = source.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            if filename == "buildings.txt":
                pattern = _re.compile(rf"^{old_id};", _re.M)
                new_text = pattern.sub(f"{new_id};", text)
            else:
                pattern = _re.compile(rf"^(\s*){old_id}(\s*=)", _re.M)
                new_text = pattern.sub(rf"\g<1>{new_id}\g<2>", text)
            if new_text == text:
                continue
            target = self.context.mod.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding="utf-8")
            changed.append(rel)
        return changed

    # ------------------------------------------------------------ state actions
    def copy_state_to_mod(self) -> None:
        doc = self._state_doc
        if doc is None or not doc.ref.is_vanilla:
            return
        new_ref = self.states.copy_to_mod(doc.ref)
        sid = new_ref.state_id or (doc.state.id if doc.state else None)
        self._refresh_tree()
        if sid is not None:
            self._open_state_doc(sid)
            self._sync_inspectors(focus="state")

    def delete_state(self, doc) -> None:
        """"Delete" a state: physical removal with replace_path active,
        otherwise emptying into a free index (see above)."""
        sid = doc.ref.state_id or (doc.state.id if doc.state else None)
        if sid is None:
            return
        done = self._empty_state_files([sid])
        if not done:
            return
        if self._states_replaced():
            self._status.configure(
                text=self.t("map.states_deleted_hard", count=len(done)))
            return
        self._status.configure(
            text=self.t("map.states_now_free", ids=str(sid)))
        self.canvas.set_selection(set())
        self._open_state_doc(sid)
        self._sync_inspectors(focus="state")

    def delete_confirm_key(self) -> str:
        """Which confirmation text fits the current deletion semantics."""
        return ("map.confirm_delete_state_replace" if self._states_replaced()
                else "map.confirm_delete_state")

    def batch_delete_confirm_key(self) -> str:
        return ("map.confirm_batch_delete_replace" if self._states_replaced()
                else "map.confirm_batch_delete")

    # ------------------------------------------------------------- undo / redo
    def _record(self, command) -> None:
        """Push an already-executed command onto the history."""
        self.commands.record(command)
        self._update_undo_buttons()

    def push_provinces_change(self, state_id: int, old: list[int],
                              new: list[int]) -> None:
        """Inspector hook: record a provinces-list edit it already applied."""
        self._record(StateProvincesCommand(state_id, old, new))

    def undo(self) -> None:
        self._history_step(undo=True)

    def redo(self) -> None:
        self._history_step(undo=False)

    def _history_step(self, undo: bool) -> None:
        if not self.map.loaded:
            return
        self._flush_inspectors()      # pending debounced edits must land first
        command = (self.commands.undo(self) if undo
                   else self.commands.redo(self))
        if command is None:
            return
        touches = command.touches
        if TOUCH_STATES in touches or TOUCH_DEFS in touches:
            self.map.invalidate_political()
            self._value_options.pop("province_free", None)
            self._refresh_tree()
        if TOUCH_ZONES in touches:
            self.map.invalidate_regions()
        self.canvas.refresh()
        self._sync_inspectors()
        self.state_inspector.refresh_provinces_view()
        self._update_undo_buttons()
        arrow = "↶" if undo else "↷"
        self._status.configure(text=f"{arrow} {self.t(command.label_key)}")

    def _update_undo_buttons(self) -> None:
        self._undo_btn.configure(
            state="normal" if self.commands.can_undo() else "disabled")
        self._redo_btn.configure(
            state="normal" if self.commands.can_redo() else "disabled")

    # ----------------------------------------------------------- owner protocol
    def mark_dirty(self, doc) -> None:
        if doc is not None:
            self._dirty.add(str(doc.ref.path))
            self._dirty_docs[str(doc.ref.path)] = doc

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
            for st in self.states.list_states():
                opts.append((f"{st.id} · {st.name}", str(st.id)))
        elif vtype == "province_free":
            assigned: dict[int, int] = {}
            for st in self.states.list_states():
                for p in st.provinces:
                    assigned[p] = st.id
            for d in self.map.defs:
                if d.type != "land" or d.id == 0:
                    continue
                owner = assigned.get(d.id)
                suffix = (f" · {self.t('map.state')} {owner}" if owner is not None
                          else f" · {self.t('map.no_state')}")
                opts.append((f"{d.id} · {d.terrain}{suffix}", str(d.id)))
        self._value_options[vtype] = opts
        return opts

    # owner protocol of the shared script editor (state history editing)
    def loc_get(self, key: str, language: str) -> str:
        return self.loc.get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.loc.set(key, language, text)

    def state_loc_get(self, key: str) -> str:
        return self.loc.get(key, self.loc_language) or ""

    def state_loc_set(self, key: str, text: str) -> None:
        if text:
            self.loc.set(key, self.loc_language, text)

    def vp_loc_get(self, province: int) -> str:
        return self.vp_loc.get(f"VICTORY_POINTS_{province}", self.vp_language) or ""

    def vp_loc_set(self, province: int, text: str) -> None:
        if text:
            self.vp_loc.set(f"VICTORY_POINTS_{province}", self.vp_language, text)

    def state_categories(self) -> list[str]:
        return self.state_cats.names()

    def resource_names(self) -> list[str]:
        return self.resources.names()

    def state_building_defs(self):
        return self.buildings.state_buildings()

    def province_building_defs(self):
        return self.buildings.province_buildings()

    def province_def(self, pid: int):
        return self.map.by_id.get(pid)

    # -------------------------------------------------------------- supply areas
    def supply_area_label(self, state_id: int) -> str:
        doc = self.supply_areas.doc_for_member(state_id)
        if doc is None:
            return self.t("map.no_supply_area")
        return (f"{doc.zone_id} · {doc.name or f'SUPPLYAREA_{doc.zone_id}'}"
                f" · value {self.supply_areas.value_of(doc)}")

    def pick_supply_area(self, state_id: int, on_done) -> None:
        from ..common.dialogs import SinglePickDialog
        options: list[tuple[str, str]] = []
        for ref in self.supply_areas.list_docs():
            try:
                doc = self.supply_areas.load(ref)
            except Exception:
                continue
            options.append((f"{doc.zone_id} · {doc.name} · "
                            f"value {self.supply_areas.value_of(doc)}",
                            str(doc.zone_id)))
        options.sort(key=lambda o: int(o[1]))
        options.append((f"➕ {self.t('map.new_supply_area')}", "new"))

        def picked(value: str) -> None:
            if value == "new":
                area = self.supply_areas.create_area(self.supply_areas.free_id())
                area_id = area.zone_id
            else:
                area_id = int(value)
            self.supply_areas.move_member(state_id, area_id)
            on_done()

        SinglePickDialog(self._insp_host, self, self.t("map.pick_supply_area"),
                         options, picked)

    def set_supply_area_value(self, state_id: int, value: int) -> None:
        doc = self.supply_areas.doc_for_member(state_id)
        if doc is not None:
            self.supply_areas.set_value(doc, value)

    # -------------------------------------------------------------- adjacencies
    def _open_adjacencies(self) -> None:
        from .dialogs import AdjacencyDialog
        AdjacencyDialog(self._insp_host, self)

    # ----------------------------------------------------------------- toggles
    def _toggle_list(self) -> None:
        self._list_visible = not self._list_visible
        if self._list_visible:
            self._grid_root.columnconfigure(0, minsize=260)
            self._list_panel.grid()
        else:
            self._list_panel.grid_remove()
            self._grid_root.columnconfigure(0, minsize=0)

    def _mode_changed(self, _event=None) -> None:
        mode = self._mode_by_label.get(self._mode_var.get(), "provinces")
        self._set_mode(mode)

    def _set_mode(self, mode: str) -> None:
        """Switch render/editing mode (F1–F7 hotkeys / combobox), swapping the
        toolbar's tool set along with the rendering."""
        self._mode_var.set(self._label_by_mode.get(mode, mode))
        prev_group = _tool_group(self._mode)
        self._mode = mode
        group = _tool_group(mode)
        if group != prev_group:
            self._tool_frames[prev_group].pack_forget()
            self._tool_frames[group].pack(side="left")
        if mode == "visual":
            # Load lazily on first use (background loading may have skipped it).
            if self.terrain_bmp.ensure():
                self._populate_palette()
        elif mode == "height":
            self.heightmap.ensure()
        self._apply_paint_state()
        self.canvas.set_mode(mode)

    # ------------------------------------------------------------------ saving
    def save_all(self) -> None:
        self.state_inspector.flush_pending()
        for path in list(self._dirty):
            doc = self._dirty_docs.get(path)
            if doc is None:
                self._dirty.discard(path)
                continue
            try:
                self.states.save_doc(doc)
                self._dirty.discard(path)
                self._dirty_docs.pop(path, None)
            except Exception as exc:                      # noqa: BLE001
                messagebox.showerror("ANKA", self.t("focuses.err.save",
                                                    error=str(exc)))
        try:
            written = self.map.save()
            for layer in (self.heightmap, self.terrain_bmp):
                path = layer.save()
                if path is not None:
                    written.append(path)
            if self.adjacencies.dirty:
                self.adjacencies.save()
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", self.t("focuses.err.save", error=str(exc)))
            return
        if self._problems_visible:
            self._validate()
        if written:
            messagebox.showinfo("ANKA", self.t("map.saved_nudge_warning"))

    def on_leave(self) -> None:
        self.state_inspector.flush_pending()
        self.province_inspector.flush_pending()
        for path in list(self._dirty):
            doc = self._dirty_docs.get(path)
            if doc is not None:
                try:
                    self.states.save_doc(doc)
                except Exception:
                    pass
            self._dirty.discard(path)
            self._dirty_docs.pop(path, None)
        if self.map.dirty_bmp or self.map.dirty_csv:
            self.map.save()
        for layer in (self.heightmap, self.terrain_bmp):
            if layer.dirty:
                try:
                    layer.save()
                except Exception:                          # noqa: BLE001
                    pass
        if self.adjacencies.dirty:
            self.adjacencies.save()
