"""Map editor: province bitmap viewport + state/province inspectors.

Layout: states tree (left, collapsible) · map canvas (center) · inspector
(right). The heavy bitmap (5632×2048) is loaded in a background thread; the
canvas renders viewport crops asynchronously, so the UI stays responsive.

Phase 1 scope: read-only viewing — render modes (provinces / terrain / owner /
state), click-to-select with state highlight, zoom-to-state from the tree,
cursor coordinate + province indicator.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...services.map_service import RENDER_MODES, MapService
from ...services.state_service import StateService
from ...services.terrain_service import TerrainService
from ..base import EditorModule, EditorRegistry
from .canvas import MapCanvas


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
        self.map = MapService(context, state_service=self.states,
                              terrain_service=self.terrain)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._items: dict[str, tuple] = {}
        self._selected_province: int | None = None
        self._selected_state: int | None = None
        self._load_state = "idle"           # idle | loading | ready | error
        self._load_error = ""

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
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self._loading_lbl = ttk.Label(center, text=self.t("map.loading"),
                                      style="Muted.TLabel")
        self._loading_lbl.grid(row=0, column=0)

        self._build_inspector_panel(root)
        self._start_loading()
        return root

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

        ttk.Button(bar, text="⤢ " + self.t("map.fit"),
                   command=lambda: self.canvas.fit_map()).pack(side="left", padx=4)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)

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
        panel = ttk.Frame(root, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=2, sticky="nsew", padx=(10, 0))
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        self._grid_root.columnconfigure(2, minsize=300)
        self._insp_host = panel
        self._placeholder = ttk.Label(panel, text=self.t("map.select_hint"),
                                      style="CardMuted.TLabel", wraplength=260,
                                      justify="left")
        self._placeholder.grid(row=0, column=0, sticky="n", pady=16)
        self._info = ttk.Label(panel, text="", style="Card.TLabel",
                               wraplength=260, justify="left", anchor="nw")

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

    def _refresh_tree(self) -> None:
        if not hasattr(self, "_tree"):
            return
        query = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        self._items.clear()
        for st in self.states.list_states():
            label = f"{st.id} · {st.name}"
            if st.owner:
                label += f" · {st.owner}"
            if query and query not in label.lower():
                continue
            iid = f"s::{st.id}"
            self._tree.insert("", "end", iid=iid, text=label,
                              tags=() if st.in_mod else ("vanilla",))
            self._items[iid] = ("state", st.id)

    def _on_tree_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None or payload[0] != "state":
            return
        self.select_state(payload[1], zoom=True)

    # --------------------------------------------------------------- selection
    def select_state(self, state_id: int, zoom: bool = False) -> None:
        self._selected_state = state_id
        st = self.states.get(state_id)
        provinces = set(st.provinces) if st else set()
        keep = ({self._selected_province}
                if self._selected_province in provinces else set())
        self.canvas.set_selection(keep, highlight=provinces)
        if zoom and provinces and self.map.loaded:
            bbox = self._state_bbox(provinces)
            if bbox is not None:
                self.canvas.zoom_to_bbox(bbox)
        self._show_info()

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
        if pid is None:
            self._selected_province = None
            self.canvas.set_selection(set())
            self._show_info()
            return
        self._selected_province = pid
        prov_state, _owners = self.map._political_maps()
        sid = prov_state.get(pid)
        self._selected_state = sid
        highlight = set()
        if sid is not None:
            st = self.states.get(sid)
            if st is not None:
                highlight = set(st.provinces)
            iid = f"s::{sid}"
            if self._tree.exists(iid):
                self._tree.selection_remove(*self._tree.selection())
                # избегаем рекурсии select → zoom: только показать строку
                self._tree.see(iid)
                self._tree.item(iid, tags=self._tree.item(iid, "tags"))
        self.canvas.set_selection({pid}, highlight=highlight)
        self._show_info()

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

    # --------------------------------------------------------------- inspector
    def _show_info(self) -> None:
        lines = []
        pid = self._selected_province
        if pid is not None:
            d = self.map.by_id.get(pid)
            if d is not None:
                coastal = self.t("common.yes") if d.coastal else self.t("common.no")
                lines.append(f"{self.t('map.province')} #{d.id}")
                lines.append(f"RGB: {d.r}, {d.g}, {d.b}")
                lines.append(f"{self.t('map.type')}: {d.type}")
                lines.append(f"{self.t('map.terrain')}: {d.terrain}")
                lines.append(f"{self.t('map.coastal')}: {coastal}")
                lines.append(f"{self.t('map.continent')}: {d.continent}")
        sid = self._selected_state
        if sid is not None:
            st = self.states.get(sid)
            if st is not None:
                if lines:
                    lines.append("")
                lines.append(f"{self.t('map.state')} {st.id} · {st.name}")
                if st.owner:
                    lines.append(f"{self.t('map.owner')}: {st.owner}")
                lines.append(f"{self.t('map.provinces_count')}: {len(st.provinces)}")
        if lines:
            self._placeholder.grid_remove()
            self._info.configure(text="\n".join(lines))
            self._info.grid(row=0, column=0, sticky="new")
        else:
            self._info.grid_remove()
            self._placeholder.grid()

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
        try:
            written = self.map.save()
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", self.t("focuses.err.save", error=str(exc)))
            return
        if written:
            messagebox.showinfo("ANKA", self.t("map.saved_nudge_warning"))

    def on_leave(self) -> None:
        if self.map.dirty_bmp or self.map.dirty_csv:
            self.map.save()
