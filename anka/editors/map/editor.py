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

from ...services.building_service import BuildingService
from ...services.map_refs import ResourceService, StateCategoryService
from ...services.map_service import RENDER_MODES, MapService
from ...services.state_service import StateService
from ...services.terrain_service import TerrainService
from ...services._locutil import LocCatalog
from ..base import EditorModule, EditorRegistry
from .canvas import MapCanvas
from .inspector import StateInspector


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
        self.state_inspector = StateInspector(panel, self)
        self.state_inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(panel, text=self.t("map.select_hint"),
                                      style="CardMuted.TLabel", wraplength=300,
                                      justify="left")
        self._placeholder.grid(row=0, column=0, sticky="n", pady=16)
        self._show_inspector(None)

    def _show_inspector(self, which: str | None) -> None:
        self.state_inspector.grid_remove()
        self._placeholder.grid_remove()
        if which == "state":
            self.state_inspector.grid()
        else:
            self._placeholder.grid()

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
        self.state_inspector.flush_pending()
        self._selected_state = state_id
        self._set_assign_mode(False)
        st = self.states.get(state_id)
        provinces = set(st.provinces) if st else set()
        keep = ({self._selected_province}
                if self._selected_province in provinces else set())
        self.canvas.set_selection(keep, highlight=provinces)
        if zoom and provinces and self.map.loaded:
            bbox = self._state_bbox(provinces)
            if bbox is not None:
                self.canvas.zoom_to_bbox(bbox)
        self._open_state_doc(state_id)

    def _open_state_doc(self, state_id: int) -> None:
        ref = self.states.doc_ref_for(state_id)
        if ref is None:
            self._state_doc = None
            self._show_inspector(None)
            return
        # A dirty (unsaved) document must keep its in-memory edits.
        doc = self._dirty_docs.get(str(ref.path)) or self.states.load(ref)
        self._state_doc = doc
        self._show_inspector("state")
        self.state_inspector.show(doc, editable=not ref.is_vanilla)

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
        if pid is None:
            self._selected_province = None
            self.canvas.set_selection(set())
            return
        self._selected_province = pid
        prov_state, _owners = self.map._political_maps()
        sid = prov_state.get(pid)
        highlight = set()
        if sid is not None:
            st = self.states.get(sid)
            if st is not None:
                highlight = set(st.provinces)
        self.canvas.set_selection({pid}, highlight=highlight)
        if sid is not None and sid != self._selected_state:
            self._selected_state = sid
            iid = f"s::{sid}"
            if self._tree.exists(iid):
                self._suppress_tree_event = True
                try:
                    self._tree.selection_set(iid)
                    self._tree.see(iid)
                finally:
                    self._suppress_tree_event = False
            self.state_inspector.flush_pending()
            self._open_state_doc(sid)
        elif sid is None:
            self._selected_state = None
            self._state_doc = None
            self._show_inspector(None)

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
        bbox = self.map.bbox_of(pid)
        if bbox is not None:
            self.canvas.zoom_to_bbox(bbox)
        self._selected_province = pid
        keep_highlight = set(self.canvas.highlight)
        self.canvas.set_selection({pid}, highlight=keep_highlight)

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
        if d.type != "land":
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
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", self.t("focuses.err.save", error=str(exc)))
            return
        if written:
            messagebox.showinfo("ANKA", self.t("map.saved_nudge_warning"))

    def on_leave(self) -> None:
        self.state_inspector.flush_pending()
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
