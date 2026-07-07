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

from ...services.adjacency_service import AdjacencyService
from ...services.building_service import BuildingService
from ...services.map_refs import ResourceService, StateCategoryService
from ...services.map_service import RENDER_MODES, MapService
from ...services.map_zones import StrategicRegionService, SupplyAreaService
from ...services.state_service import StateService
from ...services.terrain_service import TerrainService
from ...services._locutil import LocCatalog
from ..base import EditorModule, EditorRegistry
from .canvas import MapCanvas
from .inspector import ProvinceInspector, StateInspector


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
        self.map = MapService(context, state_service=self.states,
                              terrain_service=self.terrain)
        self.adjacencies = AdjacencyService(context, map_service=self.map)
        self.supply_areas = SupplyAreaService(context)
        self.strat_regions = StrategicRegionService(context)
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="state",
                              default_pattern="anka_states_l_{lang}.yml")
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
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
        self._suppress_tree_event = False
        self._tool = "select"                # select | brush | fill | picker
        self._brush_target: int | None = None
        self._last_paint: tuple[int, int] | None = None

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
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

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
        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("map.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")

        ttk.Label(bar, text=self.t("map.mode"), style="Muted.TLabel").pack(
            side="left", padx=(12, 4))
        self._mode_var = tk.StringVar(value=self.t("map.mode.provinces"))
        self._mode_by_label = {self.t(f"map.mode.{m}"): m for m in RENDER_MODES}
        combo = ttk.Combobox(bar, textvariable=self._mode_var, state="readonly",
                             width=16, values=list(self._mode_by_label))
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", self._mode_changed)
        from ...ui.widgets.tooltip import attach_help
        attach_help(combo, self.t, "map.mode", self.palette)

        ttk.Button(bar, text="⤢ " + self.t("map.fit"),
                   command=lambda: self.canvas.fit_map()).pack(side="left", padx=4)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y",
                                                   padx=6, pady=2)
        self._tool_btns: dict[str, ttk.Button] = {}
        for name, icon in (("select", "⭘"), ("brush", "🖌"),
                           ("fill", "🪣"), ("picker", "💉")):
            btn = ttk.Button(bar, text=f"{icon}",
                             command=lambda n=name: self._set_tool(n), width=3)
            btn.pack(side="left", padx=1)
            attach_help(btn, self.t, "map.tools", self.palette)
            self._tool_btns[name] = btn
        self._radius_var = tk.IntVar(value=6)
        ttk.Spinbox(bar, from_=1, to=64, width=4,
                    textvariable=self._radius_var).pack(side="left", padx=(6, 2))
        self._target_swatch = tk.Label(bar, text="  ", bd=1, relief="solid")
        self._target_swatch.pack(side="left", padx=(8, 2))
        self._target_lbl = ttk.Label(bar, text=self.t("map.no_brush"),
                                     style="Muted.TLabel")
        self._target_lbl.pack(side="left")
        ttk.Button(bar, text="➕ " + self.t("map.new_province"),
                   command=self._new_province).pack(side="left", padx=6)
        ttk.Button(bar, text="⇄ " + self.t("map.adjacencies"),
                   command=self._open_adjacencies).pack(side="left", padx=2)
        self._btn_problems = ttk.Button(bar, text="⚠", command=self._toggle_problems)
        self._btn_problems.pack(side="right", padx=(0, 4))

        self._assign_lbl = tk.Label(bar, text="🎯 " + self.t("map.assign_hint"),
                                    bd=0, padx=10, pady=3,
                                    bg=self.palette.accent,
                                    fg=self.palette.accent_text)
        self._status = ttk.Label(bar, text="", style="Muted.TLabel")
        self._status.pack(side="right", padx=8)

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
        self._tree = ttk.Treeview(panel, show="tree", selectmode="browse")
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
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
        self._placeholder = ttk.Label(panel, text=self.t("map.select_hint"),
                                      style="CardMuted.TLabel", wraplength=300,
                                      justify="left")
        self._placeholder.grid(row=0, column=0, sticky="n", pady=16)
        self._show_inspector(None)

    def _show_inspector(self, which: str | None, focus: str | None = None) -> None:
        """`which`: None (placeholder) / "province" / "state" / "both";
        `focus`: which tab to raise."""
        if which is None:
            self._nb.grid_remove()
            self._placeholder.grid()
            return
        self._placeholder.grid_remove()
        self._nb.grid()
        self._nb.tab(0, state="normal" if which in ("province", "both") else "disabled")
        self._nb.tab(1, state="normal" if which in ("state", "both") else "disabled")
        if focus == "province" and which in ("province", "both"):
            self._nb.select(0)
        elif focus == "state" and which in ("state", "both"):
            self._nb.select(1)
        elif which == "province":
            self._nb.select(0)
        elif which == "state":
            self._nb.select(1)

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

    def _map_size(self):
        return self.map.size if self.map.loaded else None

    def _render_view(self, mode, rect, scale, selected, highlight):
        return self.map.render_view(mode, rect, scale, selected=selected,
                                    highlight=highlight)

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
            if query and query not in label.lower():
                continue
            iid = f"s::{st.id}"
            self._tree.insert("", "end", iid=iid, text=label,
                              tags=() if st.in_mod else ("vanilla",))
            self._items[iid] = ("state", st.id)
        if selected and self._tree.exists(selected[0]):
            self._suppress_tree_event = True
            try:
                self._tree.selection_set(selected[0])
            finally:
                self._suppress_tree_event = False

    def _state_display_name(self, st) -> str:
        loc = self.loc.get(f"STATE_{st.id}", self.loc_language)
        return loc if loc else st.name

    def _on_tree_select(self, _event=None) -> None:
        if self._suppress_tree_event:
            return
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None or payload[0] != "state":
            return
        self.select_state(payload[1], zoom=True)

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

    def _flush_inspectors(self) -> None:
        self.state_inspector.flush_pending()
        self.province_inspector.flush_pending()

    def _tree_show_state(self, sid: int) -> None:
        iid = f"s::{sid}"
        if self._tree.exists(iid):
            self._suppress_tree_event = True
            try:
                self._tree.selection_set(iid)
                self._tree.see(iid)
            finally:
                self._suppress_tree_event = False

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

    def _map_click(self, mx: int, my: int, _event) -> None:
        if not self.map.loaded:
            return
        pid = self.map.province_at(mx, my)
        if self._assign_mode and pid is not None:
            self.assign_province_to_state(pid)
            return
        if self._tool == "picker":
            if pid is not None:
                self.set_brush_target(pid)
            return
        if self._tool == "fill":
            self._fill_at(mx, my)
            return
        if pid is None:
            self._selected_province = None
            self.canvas.set_selection(set())
            return
        self.select_province(pid)

    # ----------------------------------------------------------- paint tools
    def _set_tool(self, name: str) -> None:
        self._tool = name
        self.canvas.paint_mode = name == "brush"
        for tool, btn in self._tool_btns.items():
            btn.configure(style="Accent.TButton" if tool == name else "TButton")
        self.canvas.canvas.configure(
            cursor={"brush": "pencil", "fill": "spraycan",
                    "picker": "target"}.get(name, "crosshair"))

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

    def _paint(self, mx: int, my: int, _event) -> None:
        if not self.map.loaded or self._tool != "brush":
            return
        pid = self._brush_target
        if pid is None or pid not in self.map.by_id:
            self._last_paint = None
            return
        radius = max(1, int(self._radius_var.get() or 1))
        # Interpolate along the stroke so fast drags leave no gaps.
        points = [(mx, my)]
        if self._last_paint is not None:
            lx, ly = self._last_paint
            dist = max(abs(mx - lx), abs(my - ly))
            step = max(1, radius)
            if dist > step:
                n = dist // step
                points = [(lx + (mx - lx) * (i + 1) // (n + 1),
                           ly + (my - ly) * (i + 1) // (n + 1))
                          for i in range(int(n))] + points
        for x, y in points:
            self.map.paint_disk(x, y, radius, pid)
        self._last_paint = (mx, my)
        self.canvas.refresh()

    def _paint_end(self) -> None:
        self._last_paint = None
        if self._brush_target is not None:
            self._ensure_strategic_region(self._brush_target)

    def _fill_at(self, mx: int, my: int) -> None:
        pid = self._require_target()
        if pid is None or not self.map.loaded:
            return
        try:
            painted = self.map.flood_fill(mx, my, pid)
        except KeyError:
            return
        if painted:
            self.canvas.refresh()
            self._status.configure(
                text=self.t("map.filled_px", count=painted))
            self._ensure_strategic_region(pid)

    def _new_province(self) -> None:
        from .dialogs import NewProvinceDialog
        like = self.map.by_id.get(self._selected_province) \
            if self._selected_province is not None else None

        def submit(fields: dict) -> None:
            d = self.map.create_province(**fields)
            self._value_options.pop("province_free", None)
            self.set_brush_target(d.id)
            self._set_tool("brush")
            self._status.configure(text=self.t("map.province_created", id=d.id))

        NewProvinceDialog(self._insp_host, self, submit, like=like)

    # ------------------------------------------------------- province splitting
    def split_province_dialog(self, pid: int) -> None:
        if not self.map.loaded or self.map.area_of(pid) == 0:
            messagebox.showinfo("ANKA", self.t("map.err.no_pixels"))
            return
        from .dialogs import SplitProvinceDialog
        SplitProvinceDialog(self._insp_host, self, pid, self.apply_split)

    def apply_split(self, pid: int, labels, bbox, colors) -> None:
        ids = self.map.split_province(pid, int(labels.max()),
                                      labels=labels, bbox=bbox, colors=colors)
        new_ids = ids[1:]
        # Keep invariant "every land province belongs to exactly one state":
        # offer to put the new provinces into the source province's state.
        prov_state, _ = self.map._political_maps()
        sid = prov_state.get(pid)
        d = self.map.by_id.get(pid)
        if (sid is not None and new_ids and d is not None and d.type == "land"
                and messagebox.askyesno(
                    "ANKA", self.t("map.split_add_to_state", state=sid))):
            ref = self.states.doc_ref_for(sid)
            if ref is not None:
                if ref.is_vanilla:
                    ref = self.states.copy_to_mod(ref)
                doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
                if doc.state is not None:
                    for nid in new_ids:
                        doc.state.add_province(nid)
                    self.mark_dirty(doc)
                    self.states.refresh_info(doc)
                    if self._state_doc is not None and \
                            self._state_doc.ref.path == doc.ref.path:
                        self._state_doc = doc
                        self.state_inspector.refresh_provinces_view()
        # Strategic regions must stay consistent: new provinces inherit the
        # source province's region.
        touched = self.strat_regions.sync_provinces(
            {nid: pid for nid in new_ids}, [])
        self.map.invalidate_political()
        self._value_options.pop("province_free", None)
        self.canvas.refresh()
        self._refresh_tree()
        note = (" · " + self.t("map.strat_synced", count=len(touched))
                if touched else "")
        self._status.configure(
            text=self.t("map.split_done", count=len(ids)) + note)

    def _ensure_strategic_region(self, pid: int) -> None:
        """A drawn province must live in exactly one strategic region: if it is
        in none, inherit the majority region of its pixel neighbors."""
        if self.strat_regions.doc_for_member(pid) is not None:
            return
        if self.map.area_of(pid) == 0:
            return
        votes: dict[int, int] = {}
        for nb in self.map.neighbors_of(pid):
            doc = self.strat_regions.doc_for_member(nb)
            if doc is not None:
                votes[doc.zone_id] = votes.get(doc.zone_id, 0) + 1
        if not votes:
            return
        region = max(votes, key=votes.get)
        self.strat_regions.move_member(pid, region)
        self._status.configure(
            text=self.t("map.strat_assigned", id=pid, region=region))

    def _map_hover(self, mx, my) -> None:
        if mx is None or not self.map.loaded:
            self._status.configure(text="")
            return
        pid = self.map.province_at(mx, my)
        if pid is None:
            self._status.configure(text=f"{mx}, {my}")
            return
        d = self.map.by_id.get(pid)
        extra = f" · {d.type}/{d.terrain}" if d is not None else ""
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
        self.map.set_def(pid, **fields)
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
        prev_doc = None
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
                prev_doc.state.remove_province(pid)
                self.mark_dirty(prev_doc)
                self.states.refresh_info(prev_doc)
        doc.state.add_province(pid)
        self.mark_dirty(doc)
        self.states.refresh_info(doc)
        self.provinces_changed()
        self.state_inspector.refresh_provinces_view()

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
        if doc.ref.is_vanilla:
            return
        sid = doc.ref.state_id
        self.states.delete_doc(doc.ref)
        self._dirty.discard(str(doc.ref.path))
        self._dirty_docs.pop(str(doc.ref.path), None)
        self._state_doc = None
        self._selected_state = None
        self._show_inspector(None)
        self.map.invalidate_political()
        self.canvas.set_selection(set())
        self._refresh_tree()

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

    def state_loc_get(self, key: str) -> str:
        return self.loc.get(key, self.loc_language) or ""

    def state_loc_set(self, key: str, text: str) -> None:
        if text:
            self.loc.set(key, self.loc_language, text)

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
        if self.adjacencies.dirty:
            self.adjacencies.save()
