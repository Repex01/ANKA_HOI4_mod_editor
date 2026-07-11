"""State & province inspectors for the map editor.

Dumb forms over block-backed models: values load in ``show()``, edits commit
instantly (debounced for typed fields) and mark the owner dirty. Vanilla states
are read-only until copied into the mod; province definition edits are always
allowed — saving writes a whole-file override of ``definition.csv`` into the mod.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...config.constants import HOI4_LANGUAGES
from ...services.state_service import StateDocument
from ..common.dialogs import PdxPreviewDialog, SinglePickDialog
from ..common.inspector_base import InspectorBase

PROVINCE_TYPES = ("land", "sea", "lake")


class ProvinceInspector(InspectorBase):
    """definition.csv row of one province: type / coastal / terrain / continent
    (phase 3) + read-only geometry facts (area, bbox, neighbors)."""

    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.pid: int | None = None
        self._build()

    def _build(self) -> None:
        b = self.body
        row = 0
        self._title_lbl = ttk.Label(b, text="", style="Heading.TLabel")
        self._title_lbl.grid(row=row, column=0, columnspan=2, sticky="w",
                             pady=(0, 6))
        row += 1
        self._rgb_lbl = ttk.Label(b, text="", style="CardMuted.TLabel")
        self._rgb_lbl.grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        ttk.Label(b, text=self.t("map.type"), style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        self._type_var = tk.StringVar()
        self._type_combo = ttk.Combobox(b, textvariable=self._type_var,
                                        state="readonly", width=10,
                                        values=list(PROVINCE_TYPES))
        self._type_combo.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        self._type_combo.bind("<<ComboboxSelected>>", lambda e: self._commit_type())
        row += 1

        ttk.Label(b, text=self.t("map.terrain"), style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        self._terrain_var = tk.StringVar()
        self._terrain_combo = ttk.Combobox(b, textvariable=self._terrain_var,
                                           state="readonly", width=18)
        self._terrain_combo.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        self._terrain_combo.bind("<<ComboboxSelected>>",
                                 lambda e: self._commit_terrain())
        row += 1

        self._coastal_var = tk.BooleanVar()
        ttk.Checkbutton(b, text=self.t("map.coastal"), style="Card.TCheckbutton",
                        variable=self._coastal_var,
                        command=self._commit_coastal).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=3)
        row += 1

        ttk.Label(b, text=self.t("map.continent"), style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        self._cont_var = tk.StringVar()
        self._cont_combo = ttk.Combobox(b, textvariable=self._cont_var,
                                        state="readonly", width=18)
        self._cont_combo.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        self._cont_combo.bind("<<ComboboxSelected>>",
                              lambda e: self._commit_continent())
        row += 1

        self._facts_lbl = ttk.Label(b, text="", style="CardMuted.TLabel",
                                    justify="left")
        self._facts_lbl.grid(row=row, column=0, columnspan=2, sticky="w",
                             pady=(8, 3))
        row += 1

        ttk.Label(b, text=self.t("map.neighbors"), style="Heading.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(8, 3))
        row += 1
        self._nb_list = tk.Listbox(b, height=6, bg=self.palette.surface_alt,
                                   fg=self.palette.text, relief="flat",
                                   selectbackground=self.palette.accent,
                                   exportselection=False, font=("Consolas", 10))
        self._nb_list.grid(row=row, column=0, columnspan=2, sticky="ew")
        self._nb_list.bind("<Double-1>", self._goto_neighbor)
        self._neighbors: list[int] = []
        row += 1

        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._state_btn = ttk.Button(actions, text="🗺 " + self.t("map.goto_state"),
                                     command=self._goto_state)
        self._state_btn.pack(side="left")
        split_btn = ttk.Button(actions, text="⚡ " + self.t("map.split_button"),
                               command=self._split)
        split_btn.pack(side="left", padx=(6, 0))
        from ...ui.widgets.tooltip import attach_help
        attach_help(split_btn, self.t, "map.split", self.palette)

    # -------------------------------------------------------------------- show
    def show(self, pid: int | None) -> None:
        self.flush_pending()
        self._loading = True
        self.pid = pid
        d = self.owner.province_def(pid) if pid is not None else None
        if d is None:
            self._loading = False
            return
        self._title_lbl.configure(text=f"{self.t('map.province')} #{d.id}")
        self._rgb_lbl.configure(text=f"RGB {d.r}, {d.g}, {d.b}")
        self._type_var.set(d.type)
        self._set_terrain_choices(d.type)
        self._terrain_var.set(d.terrain)
        self._coastal_var.set(d.coastal)
        conts = self.owner.continent_options()
        self._cont_combo.configure(values=[label for label, _v in conts])
        current = next((label for label, v in conts if v == d.continent),
                       str(d.continent))
        self._cont_var.set(current)
        self._facts_lbl.configure(text=self.owner.province_facts(d.id))
        self._neighbors = self.owner.province_neighbors(d.id)
        self._nb_list.delete(0, "end")
        for n in self._neighbors:
            nd = self.owner.province_def(n)
            extra = f"  {nd.type}/{nd.terrain}" if nd is not None else ""
            self._nb_list.insert("end", f"{n}{extra}")
        is_land = d.type == "land"
        self._state_btn.configure(state="normal" if is_land else "disabled")
        self._loading = False

    def _set_terrain_choices(self, ptype: str) -> None:
        if ptype == "land":
            values = self.owner.land_terrains()
        else:
            values = self.owner.water_terrains()
        self._terrain_combo.configure(values=values)

    # ----------------------------------------------------------------- commits
    def _commit_type(self) -> None:
        if self._loading or self.pid is None:
            return
        new_type = self._type_var.get()
        d = self.owner.province_def(self.pid)
        if d is None or d.type == new_type:
            return
        fields = {"type": new_type}
        # Keep the terrain consistent with the new type.
        terrains = (self.owner.land_terrains() if new_type == "land"
                    else self.owner.water_terrains())
        if d.terrain not in terrains and terrains:
            fields["terrain"] = terrains[0]
        self.owner.set_province_def(self.pid, **fields)
        self._set_terrain_choices(new_type)
        self._terrain_var.set(fields.get("terrain", d.terrain))

    def _commit_terrain(self) -> None:
        if self._loading or self.pid is None:
            return
        d = self.owner.province_def(self.pid)
        value = self._terrain_var.get()
        if d is not None and d.terrain != value:
            self.owner.set_province_def(self.pid, terrain=value)

    def _commit_coastal(self) -> None:
        if self._loading or self.pid is None:
            return
        d = self.owner.province_def(self.pid)
        value = self._coastal_var.get()
        if d is not None and d.coastal != value:
            self.owner.set_province_def(self.pid, coastal=value)

    def _commit_continent(self) -> None:
        if self._loading or self.pid is None:
            return
        label = self._cont_var.get()
        value = next((v for lbl, v in self.owner.continent_options()
                      if lbl == label), None)
        d = self.owner.province_def(self.pid)
        if value is not None and d is not None and d.continent != value:
            self.owner.set_province_def(self.pid, continent=value)

    # ------------------------------------------------------------------- misc
    def _goto_neighbor(self, _event=None) -> None:
        sel = self._nb_list.curselection()
        if sel:
            self.owner.select_province(self._neighbors[sel[0]], zoom=True)

    def _goto_state(self) -> None:
        if self.pid is not None:
            self.owner.goto_state_of_province(self.pid)

    def _split(self) -> None:
        if self.pid is not None:
            self.owner.split_province_dialog(self.pid)


class StateInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc: StateDocument | None = None
        self.state = None
        self._core_chips: list = []
        self._vp_rows: list = []
        self._res_rows: list = []
        self._bld_vars: dict[str, tk.StringVar] = {}
        self._pb_vars: dict[str, tk.StringVar] = {}
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        b = self.body
        row = 0

        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        head.columnconfigure(0, weight=1)
        self._title_lbl = ttk.Label(head, text="", style="Heading.TLabel")
        self._title_lbl.grid(row=0, column=0, sticky="w")
        self._copy_btn = ttk.Button(head, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    style="Accent.TButton", command=self._copy_to_mod)
        self._file_lbl = ttk.Label(head, text="", style="CardMuted.TLabel")
        self._file_lbl.grid(row=1, column=0, columnspan=2, sticky="w")
        row += 1

        self._name_var = self._entry_row_wide(row, self.t("map.state_name"),
                                              "name", self._commit_name)
        row += 1

        self._owner_var = self._pick_row(row, self.t("map.owner"), "owner")
        row += 1
        self._controller_var = self._pick_row(row, self.t("map.controller"),
                                              "controller")
        row += 1

        ttk.Label(b, text=self.t("map.category"), style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        self._cat_var = tk.StringVar()
        self._cat_combo = ttk.Combobox(b, textvariable=self._cat_var,
                                       state="readonly", width=18)
        self._cat_combo.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        self._cat_combo.bind("<<ComboboxSelected>>", lambda e: self._commit_category())
        row += 1

        self._manpower_var = self._entry_row(row, self.t("map.manpower"),
                                             "manpower", self._commit_manpower)
        row += 1
        self._supplies_var = self._entry_row(row, self.t("map.local_supplies"),
                                             "supplies", self._commit_supplies)
        row += 1

        self._impassable_var = tk.BooleanVar()
        chk = ttk.Checkbutton(b, text=self.t("map.impassable"),
                              style="Card.TCheckbutton",
                              variable=self._impassable_var,
                              command=self._commit_impassable)
        chk.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
        row += 1

        ttk.Label(b, text=self.t("map.supply_area"),
                  style="CardMuted.TLabel").grid(row=row, column=0,
                                                 sticky="w", pady=3)
        sa = ttk.Frame(b, style="Card.TFrame")
        sa.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        self._supply_lbl = ttk.Label(sa, text="", style="Card.TLabel")
        self._supply_lbl.pack(side="left")
        self._supply_btn = ttk.Button(sa, text="…", width=3,
                                      command=self._pick_supply_area)
        self._supply_btn.pack(side="left", padx=(6, 0))
        row += 1

        row = self._section(row, self.t("map.cores"))
        self._cores_frame = ttk.Frame(b, style="Card.TFrame")
        self._cores_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        row = self._section(row, self.t("map.victory_points"))
        self._vp_frame = ttk.Frame(b, style="Card.TFrame")
        self._vp_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        row = self._section(row, self.t("map.resources"))
        self._res_frame = ttk.Frame(b, style="Card.TFrame")
        self._res_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        row = self._section(row, self.t("map.provinces"))
        prov = ttk.Frame(b, style="Card.TFrame")
        prov.grid(row=row, column=0, columnspan=2, sticky="ew")
        prov.columnconfigure(0, weight=1)
        self._prov_list = tk.Listbox(prov, height=6, bg=self.palette.surface_alt,
                                     fg=self.palette.text, relief="flat",
                                     selectbackground=self.palette.accent,
                                     exportselection=False, font=("Consolas", 10))
        self._prov_list.grid(row=0, column=0, rowspan=3, sticky="nsew")
        self._prov_list.bind("<Double-1>", self._goto_province)
        ttk.Button(prov, text="➕", width=3, command=self._add_province_dialog).grid(
            row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(prov, text="➖", width=3, command=self._remove_province).grid(
            row=1, column=1, sticky="ew", padx=(6, 0), pady=2)
        self._assign_btn = ttk.Button(prov, text="🎯", width=3,
                                      command=self._toggle_assign)
        self._assign_btn.grid(row=2, column=1, sticky="ew", padx=(6, 0))
        row += 1

        row = self._section(row, self.t("map.state_buildings"))
        self._bld_frame = ttk.Frame(b, style="Card.TFrame")
        self._bld_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        row = self._section(row, self.t("map.province_buildings"))
        pbf = ttk.Frame(b, style="Card.TFrame")
        pbf.grid(row=row, column=0, columnspan=2, sticky="ew")
        pbf.columnconfigure(1, weight=1)
        ttk.Label(pbf, text=self.t("map.province"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w")
        self._pb_prov_var = tk.StringVar()
        self._pb_combo = ttk.Combobox(pbf, textvariable=self._pb_prov_var,
                                      state="readonly", width=12)
        self._pb_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._pb_combo.bind("<<ComboboxSelected>>",
                            lambda e: self._refresh_province_buildings())
        self._pb_frame = ttk.Frame(b, style="Card.TFrame")
        row += 1
        self._pb_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="👁 " + self.t("map.preview"),
                   command=self._preview).pack(side="left")
        self._delete_btn = ttk.Button(actions, text="🗑 " + self.t("map.delete_state"),
                                      command=self._delete)
        self._delete_btn.pack(side="right")

    def _section(self, row: int, title: str) -> int:
        ttk.Label(self.body, text=title, style="Heading.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(10, 3))
        return row + 1

    def _entry_row_wide(self, row: int, label: str, key: str, commit) -> tk.StringVar:
        ttk.Label(self.body, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar()
        entry = ttk.Entry(self.body, textvariable=var, width=26)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        var.trace_add("write", lambda *_: self._debounce(key, commit))
        entry.bind("<FocusOut>", lambda e: commit())
        return var

    def _pick_row(self, row: int, label: str, which: str) -> tk.StringVar:
        ttk.Label(self.body, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        wrap = ttk.Frame(self.body, style="Card.TFrame")
        wrap.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        var = tk.StringVar()
        entry = ttk.Entry(wrap, textvariable=var, width=8)
        entry.pack(side="left")
        var.trace_add("write", lambda *_: self._debounce(
            which, lambda: self._commit_tag(which)))
        entry.bind("<FocusOut>", lambda e: self._commit_tag(which))
        ttk.Button(wrap, text="…", width=3,
                   command=lambda: self._pick_tag(which)).pack(side="left", padx=(4, 0))
        return var

    # -------------------------------------------------------------------- show
    def show(self, doc: StateDocument | None, editable: bool) -> None:
        self.flush_pending()
        self._loading = True
        self.doc = doc
        self.state = doc.state if doc is not None else None
        self._editable = editable
        st = self.state
        if st is None:
            self._loading = False
            return
        name = self.owner.state_loc_get(st.name_key or f"STATE_{st.id}")
        self._title_lbl.configure(
            text=f"{self.t('map.state')} {st.id}" + ("  🔒" if not editable else ""))
        self._file_lbl.configure(text=self.doc.ref.rel_file)
        if editable:
            self._copy_btn.grid_remove()
        else:
            self._copy_btn.grid(row=0, column=1, sticky="e")
        self._name_var.set(name)
        self._owner_var.set(st.owner)
        self._controller_var.set(st.controller)
        cats = self.owner.state_categories()
        self._cat_combo.configure(values=cats)
        self._cat_var.set(st.state_category)
        self._manpower_var.set(str(st.manpower))
        self._supplies_var.set(f"{st.local_supplies:g}")
        self._impassable_var.set(st.impassable)
        self._supply_lbl.configure(text=self.owner.supply_area_label(st.id))
        self._refresh_cores()
        self._refresh_vps()
        self._refresh_resources()
        self._refresh_provinces()
        self._refresh_state_buildings()
        provinces = [str(p) for p in st.provinces]
        self._pb_combo.configure(values=provinces)
        self._pb_prov_var.set(provinces[0] if provinces else "")
        self._refresh_province_buildings()
        self._set_state_all(editable)
        self._delete_btn.configure(state="normal" if editable else "disabled")
        # _set_state_all disables EVERY button on a vanilla state — but these
        # two must stay clickable: copying to mod is exactly the read-only
        # escape hatch, and the supply area lives in zone files, not this one.
        self._copy_btn.configure(state="normal")
        self._supply_btn.configure(state="normal")
        self._loading = False

    def refresh_provinces_view(self) -> None:
        """Called by the editor after map-driven province changes."""
        if self.state is not None:
            self._refresh_provinces()
            provinces = [str(p) for p in self.state.provinces]
            self._pb_combo.configure(values=provinces)
            if self._pb_prov_var.get() not in provinces:
                self._pb_prov_var.set(provinces[0] if provinces else "")
                self._refresh_province_buildings()

    # ----------------------------------------------------------------- commits
    def _dirty(self) -> None:
        self.owner.mark_dirty(self.doc)

    def _commit_name(self) -> None:
        if not self._guard() or self.state is None:
            return
        key = self.state.name_key or f"STATE_{self.state.id}"
        if not self.state.name_key:
            self.state.set_name_key(key)
            self._dirty()
        self.owner.state_loc_set(key, self._name_var.get().strip())
        self.owner.refresh_tree_labels()

    def _commit_tag(self, which: str) -> None:
        if not self._guard() or self.state is None:
            return
        value = self._owner_var.get() if which == "owner" else self._controller_var.get()
        value = value.strip().upper()
        current = self.state.owner if which == "owner" else self.state.controller
        if value == current:
            return
        if which == "owner":
            self.state.set_owner(value)
        else:
            self.state.set_controller(value)
        self._dirty()
        self.owner.political_changed()

    def _pick_tag(self, which: str) -> None:
        if not self._editable:
            return
        options = [("—", "")] + self.owner.value_options("country")

        def picked(tag: str) -> None:
            var = self._owner_var if which == "owner" else self._controller_var
            var.set(tag)
            self._commit_tag(which)

        SinglePickDialog(self, self.owner, self.t("map.pick_country"),
                         options, picked)

    def _commit_category(self) -> None:
        if not self._guard() or self.state is None:
            return
        self.state.set_state_category(self._cat_var.get())
        self._dirty()

    def _commit_manpower(self) -> None:
        if not self._guard() or self.state is None:
            return
        try:
            value = int(self._manpower_var.get().strip() or "0")
        except ValueError:
            return
        if value != self.state.manpower:
            self.state.set_manpower(value)
            self._dirty()

    def _commit_supplies(self) -> None:
        if not self._guard() or self.state is None:
            return
        try:
            value = float(self._supplies_var.get().strip() or "0")
        except ValueError:
            return
        if value != self.state.local_supplies:
            self.state.set_local_supplies(value)
            self._dirty()

    def _commit_impassable(self) -> None:
        if not self._guard() or self.state is None:
            return
        self.state.set_impassable(self._impassable_var.get())
        self._dirty()

    def _pick_supply_area(self) -> None:
        if self.state is None:
            return
        sid = self.state.id

        def done() -> None:
            self._supply_lbl.configure(text=self.owner.supply_area_label(sid))

        self.owner.pick_supply_area(sid, done)

    # -------------------------------------------------------------------- cores
    def _refresh_cores(self) -> None:
        for w in self._cores_frame.winfo_children():
            w.destroy()
        st = self.state
        if st is None:
            return
        for tag in st.cores:
            chip = ttk.Frame(self._cores_frame, style="Card.TFrame")
            chip.pack(side="left", padx=(0, 4), pady=2)
            ttk.Label(chip, text=tag, style="Card.TLabel").pack(side="left")
            if self._editable:
                btn = tk.Label(chip, text="✕", cursor="hand2",
                               bg=self.palette.surface, fg=self.palette.text_muted)
                btn.pack(side="left", padx=(2, 0))
                btn.bind("<Button-1>", lambda e, t=tag: self._remove_core(t))
        if self._editable:
            ttk.Button(self._cores_frame, text="＋", width=3,
                       command=self._add_core).pack(side="left", pady=2)

    def _remove_core(self, tag: str) -> None:
        if not self._editable or self.state is None:
            return
        cores = self.state.cores
        cores.remove(tag)
        self.state.set_cores(cores)
        self._dirty()
        self._refresh_cores()

    def _add_core(self) -> None:
        if self.state is None:
            return

        def picked(tag: str) -> None:
            cores = self.state.cores
            if tag and tag not in cores:
                cores.append(tag)
                self.state.set_cores(cores)
                self._dirty()
                self._refresh_cores()

        SinglePickDialog(self, self.owner, self.t("map.pick_country"),
                         self.owner.value_options("country"), picked)

    # ---------------------------------------------------------------------- VP
    def _refresh_vps(self) -> None:
        for w in self._vp_frame.winfo_children():
            w.destroy()
        st = self.state
        if st is None:
            return
        # Language selector: VP names are localised per language independently.
        langrow = ttk.Frame(self._vp_frame, style="Card.TFrame")
        langrow.pack(fill="x", pady=(0, 3))
        ttk.Label(langrow, text=self.t("map.vp_language"),
                  style="CardMuted.TLabel").pack(side="left")
        self._vp_lang_var = tk.StringVar(value=self.owner.vp_language)
        lang_combo = ttk.Combobox(langrow, textvariable=self._vp_lang_var,
                                  state="readonly", width=12,
                                  values=list(HOI4_LANGUAGES))
        lang_combo.pack(side="left", padx=6)
        lang_combo.bind("<<ComboboxSelected>>", lambda e: self._change_vp_language())
        for i, (prov, val) in enumerate(st.victory_points):
            rowf = ttk.Frame(self._vp_frame, style="Card.TFrame")
            rowf.pack(fill="x", pady=1)
            ttk.Label(rowf, text=str(prov), style="Card.TLabel",
                      width=6).pack(side="left")
            var = tk.StringVar(value=str(val))
            spin = ttk.Spinbox(rowf, from_=1, to=50, width=4, textvariable=var,
                               command=lambda i=i: None)
            spin.pack(side="left", padx=(4, 0))
            var.trace_add("write", lambda *_a, i=i, v=var: self._debounce(
                f"vp{i}", lambda: self._commit_vp(i, v)))
            # Victory-point name (localisation VICTORY_POINTS_<province>). It is a loc
            # edit written straight to the mod, so it is allowed even on a read-only state.
            name_var = tk.StringVar(value=self.owner.vp_loc_get(prov))
            ttk.Entry(rowf, textvariable=name_var, width=13).pack(
                side="left", padx=(4, 0))
            name_var.trace_add("write", lambda *_a, p=prov, v=name_var: self._debounce(
                f"vpn{p}", lambda: self.owner.vp_loc_set(p, v.get())))
            if self._editable:
                btn = tk.Label(rowf, text="✕", cursor="hand2",
                               bg=self.palette.surface, fg=self.palette.text_muted)
                btn.pack(side="left", padx=(4, 0))
                btn.bind("<Button-1>", lambda e, i=i: self._remove_vp(i))
        if self._editable:
            ttk.Button(self._vp_frame, text="＋ " + self.t("map.add_vp"),
                       command=self._add_vp).pack(anchor="w", pady=2)

    def _commit_vp(self, index: int, var: tk.StringVar) -> None:
        if not self._guard() or self.state is None:
            return
        try:
            value = int(var.get())
        except ValueError:
            return
        points = self.state.victory_points
        if 0 <= index < len(points) and points[index][1] != value:
            points[index] = (points[index][0], value)
            self.state.set_victory_points(points)
            self._dirty()

    def _remove_vp(self, index: int) -> None:
        if not self._editable or self.state is None:
            return
        points = self.state.victory_points
        if 0 <= index < len(points):
            del points[index]
            self.state.set_victory_points(points)
            self._dirty()
            self._refresh_vps()

    def _add_vp(self) -> None:
        st = self.state
        if st is None:
            return
        taken = {p for p, _ in st.victory_points}
        options = [(str(p), str(p)) for p in st.provinces if p not in taken]
        if not options:
            return

        def picked(prov: str) -> None:
            points = st.victory_points
            points.append((int(prov), 1))
            st.set_victory_points(points)
            self._dirty()
            self._refresh_vps()

        SinglePickDialog(self, self.owner, self.t("map.pick_province"),
                         options, picked)

    def _change_vp_language(self) -> None:
        """Switch the language VP names are read/written in, then reload the rows."""
        self.flush_pending()          # land any pending name edit in the old language
        self.owner.vp_language = self._vp_lang_var.get()
        self._refresh_vps()

    # --------------------------------------------------------------- resources
    def _refresh_resources(self) -> None:
        for w in self._res_frame.winfo_children():
            w.destroy()
        st = self.state
        if st is None:
            return
        for name, amount in st.resources.items():
            rowf = ttk.Frame(self._res_frame, style="Card.TFrame")
            rowf.pack(fill="x", pady=1)
            ttk.Label(rowf, text=name, style="Card.TLabel", width=12).pack(side="left")
            var = tk.StringVar(value=f"{amount:g}")
            entry = ttk.Entry(rowf, textvariable=var, width=7)
            entry.pack(side="left", padx=6)
            var.trace_add("write", lambda *_a, n=name, v=var: self._debounce(
                f"res_{n}", lambda: self._commit_resource(n, v)))
            if self._editable:
                btn = tk.Label(rowf, text="✕", cursor="hand2",
                               bg=self.palette.surface, fg=self.palette.text_muted)
                btn.pack(side="left")
                btn.bind("<Button-1>", lambda e, n=name: self._remove_resource(n))
        if self._editable:
            ttk.Button(self._res_frame, text="＋ " + self.t("map.add_resource"),
                       command=self._add_resource).pack(anchor="w", pady=2)

    def _commit_resource(self, name: str, var: tk.StringVar) -> None:
        if not self._guard() or self.state is None:
            return
        try:
            amount = float(var.get())
        except ValueError:
            return
        if amount > 0 and self.state.resources.get(name) != amount:
            self.state.set_resource(name, amount)
            self._dirty()

    def _remove_resource(self, name: str) -> None:
        if not self._editable or self.state is None:
            return
        self.state.set_resource(name, 0)
        self._dirty()
        self._refresh_resources()

    def _add_resource(self) -> None:
        st = self.state
        if st is None:
            return
        existing = set(st.resources)
        options = [(n, n) for n in self.owner.resource_names() if n not in existing]
        if not options:
            return

        def picked(name: str) -> None:
            st.set_resource(name, 1)
            self._dirty()
            self._refresh_resources()

        SinglePickDialog(self, self.owner, self.t("map.pick_resource"),
                         options, picked)

    # --------------------------------------------------------------- provinces
    def _refresh_provinces(self) -> None:
        self._prov_list.delete(0, "end")
        st = self.state
        if st is None:
            return
        for pid in st.provinces:
            d = self.owner.province_def(pid)
            extra = f"  {d.type}/{d.terrain}" if d is not None else ""
            self._prov_list.insert("end", f"{pid}{extra}")

    def _goto_province(self, _event=None) -> None:
        sel = self._prov_list.curselection()
        if sel and self.state is not None:
            pid = self.state.provinces[sel[0]]
            self.owner.goto_province(pid)

    def _add_province_dialog(self) -> None:
        if not self._editable or self.state is None:
            return
        options = self.owner.value_options("province_free")

        def picked(value: str) -> None:
            self.owner.assign_province_to_state(int(value))

        SinglePickDialog(self, self.owner, self.t("map.pick_province"),
                         options, picked)

    def _remove_province(self) -> None:
        if not self._editable or self.state is None:
            return
        sel = self._prov_list.curselection()
        if not sel:
            return
        pid = self.state.provinces[sel[0]]
        if not messagebox.askyesno("ANKA", self.t("map.confirm_remove_province",
                                                  id=pid)):
            return
        old = list(self.state.provinces)
        self.state.remove_province(pid)
        self.owner.push_provinces_change(self.state.id, old,
                                         list(self.state.provinces))
        self._dirty()
        self.owner.provinces_changed()
        self._refresh_provinces()

    def _toggle_assign(self) -> None:
        if not self._editable:
            return
        self.owner.toggle_assign_mode()

    def set_assign_active(self, active: bool) -> None:
        self._assign_btn.configure(style="Accent.TButton" if active else "TButton")

    # --------------------------------------------------------------- buildings
    def _refresh_state_buildings(self) -> None:
        for w in self._bld_frame.winfo_children():
            w.destroy()
        self._bld_vars.clear()
        st = self.state
        if st is None:
            return
        levels = st.state_buildings
        for bdef in self.owner.state_building_defs():
            rowf = ttk.Frame(self._bld_frame, style="Card.TFrame")
            rowf.pack(fill="x", pady=1)
            ttk.Label(rowf, text=bdef.name, style="Card.TLabel",
                      width=22).pack(side="left")
            var = tk.StringVar(value=str(levels.get(bdef.name, 0)))
            spin = ttk.Spinbox(rowf, from_=0, to=bdef.max_level, width=5,
                               textvariable=var)
            spin.pack(side="left", padx=6)
            ttk.Label(rowf, text=f"≤{bdef.max_level}",
                      style="CardMuted.TLabel").pack(side="left")
            var.trace_add("write", lambda *_a, n=bdef.name, v=var: self._debounce(
                f"bld_{n}", lambda: self._commit_state_building(n, v)))
            self._bld_vars[bdef.name] = var

    def _commit_state_building(self, name: str, var: tk.StringVar) -> None:
        if not self._guard() or self.state is None:
            return
        try:
            level = int(var.get() or "0")
        except ValueError:
            return
        if self.state.state_buildings.get(name, 0) != level:
            self.state.set_state_building(name, level)
            self._dirty()

    def _refresh_province_buildings(self) -> None:
        for w in self._pb_frame.winfo_children():
            w.destroy()
        self._pb_vars.clear()
        st = self.state
        raw = self._pb_prov_var.get()
        if st is None or not raw:
            return
        pid = int(raw)
        d = self.owner.province_def(pid)
        coastal = d.coastal if d is not None else False
        levels = st.province_buildings.get(pid, {})
        for bdef in self.owner.province_building_defs():
            if bdef.only_coastal and not coastal:
                continue
            rowf = ttk.Frame(self._pb_frame, style="Card.TFrame")
            rowf.pack(fill="x", pady=1)
            ttk.Label(rowf, text=bdef.name, style="Card.TLabel",
                      width=22).pack(side="left")
            var = tk.StringVar(value=str(levels.get(bdef.name, 0)))
            spin = ttk.Spinbox(rowf, from_=0, to=bdef.max_level, width=5,
                               textvariable=var)
            spin.pack(side="left", padx=6)
            ttk.Label(rowf, text=f"≤{bdef.max_level}",
                      style="CardMuted.TLabel").pack(side="left")
            var.trace_add("write",
                          lambda *_a, n=bdef.name, v=var, p=pid: self._debounce(
                              f"pb_{p}_{n}",
                              lambda: self._commit_province_building(p, n, v)))
            self._pb_vars[bdef.name] = var
        if not self._editable:
            self._set_state_all(False)

    def _commit_province_building(self, pid: int, name: str,
                                  var: tk.StringVar) -> None:
        if not self._guard() or self.state is None:
            return
        try:
            level = int(var.get() or "0")
        except ValueError:
            return
        if self.state.province_buildings.get(pid, {}).get(name, 0) != level:
            self.state.set_province_building(pid, name, level)
            self._dirty()

    # ------------------------------------------------------------------ actions
    def _preview(self) -> None:
        if self.doc is not None:
            PdxPreviewDialog(self, self.owner, self.doc.ref.rel_file, self.doc.root)

    def _copy_to_mod(self) -> None:
        self.owner.copy_state_to_mod()

    def _delete(self) -> None:
        if self.doc is None or self.doc.ref.is_vanilla:
            return
        if not messagebox.askyesno("ANKA", self.t("map.confirm_delete_state",
                                                  id=self.state.id if self.state else "?")):
            return
        self.owner.delete_state(self.doc)


class BatchStateInspector(InspectorBase):
    """Edit shared fields (owner / controller / category) across many states at once —
    shown when a multi-selection of states is active. Empty fields are left untouched."""

    KEEP = "— keep —"

    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.state_ids: list[int] = []
        self._build()

    def _build(self) -> None:
        b = self.body
        r = 0
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 2)); r += 1
        ttk.Label(b, text=self.t("map.batch_hint"), style="CardMuted.TLabel",
                  wraplength=300, justify="left").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 8)); r += 1

        self._owner_var = self._pick_row(r, self.t("map.owner")); r += 1
        self._ctrl_var = self._pick_row(r, self.t("map.controller")); r += 1

        ttk.Label(b, text=self.t("map.category"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        self._cat_var = tk.StringVar(value=self.KEEP)
        ttk.Combobox(b, textvariable=self._cat_var, state="readonly", width=18,
                     values=[self.KEEP] + self.owner.state_categories()).grid(
            row=r, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=3); r += 1

        ttk.Button(b, text=self.t("map.batch_apply"), style="Accent.TButton",
                   command=self._apply).grid(row=r, column=0, columnspan=3,
                                             sticky="ew", pady=(10, 0)); r += 1

        # Cores act immediately (a core is a set membership, not a single value):
        # type/pick a tag, then add it to or remove it from every selected state.
        ttk.Separator(b).grid(row=r, column=0, columnspan=3, sticky="ew", pady=8); r += 1
        ttk.Label(b, text=self.t("map.cores"), style="Heading.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 3)); r += 1
        core_row = ttk.Frame(b, style="Card.TFrame")
        core_row.grid(row=r, column=0, columnspan=3, sticky="ew")
        self._core_var = tk.StringVar()
        ttk.Entry(core_row, textvariable=self._core_var, width=6).pack(side="left")
        ttk.Button(core_row, text="…", width=3,
                   command=lambda: self._pick_country(self._core_var)).pack(side="left",
                                                                            padx=(4, 0))
        ttk.Button(core_row, text="＋ " + self.t("map.batch_add_core"),
                   command=lambda: self._core_op(add=True)).pack(side="left", padx=(8, 0))
        ttk.Button(core_row, text="－ " + self.t("map.batch_remove_core"),
                   command=lambda: self._core_op(add=False)).pack(side="left", padx=4)

    def _pick_row(self, row: int, label: str) -> tk.StringVar:
        b = self.body
        ttk.Label(b, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar()
        ttk.Entry(b, textvariable=var, width=8).grid(
            row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        ttk.Button(b, text="…", width=3,
                   command=lambda: self._pick_country(var)).grid(
            row=row, column=2, sticky="w", padx=(4, 0))
        return var

    def _pick_country(self, var: tk.StringVar) -> None:
        SinglePickDialog(self, self.owner, self.t("map.owner"),
                         self.owner.value_options("country"),
                         lambda v: var.set(v), current=var.get().strip())

    def show(self, state_ids: list[int]) -> None:
        self.state_ids = list(state_ids)
        self._title.configure(text=self.t("map.batch_title", count=len(state_ids)))
        self._owner_var.set("")
        self._ctrl_var.set("")
        self._cat_var.set(self.KEEP)

    def _apply(self) -> None:
        if not self.state_ids:
            return
        owner = self._owner_var.get().strip().upper() or None
        ctrl = self._ctrl_var.get().strip().upper() or None
        cat = self._cat_var.get()
        cat = None if cat == self.KEEP else cat
        if owner is None and ctrl is None and cat is None:
            return
        self.owner.batch_apply_states(self.state_ids, owner, ctrl, cat)

    def _core_op(self, add: bool) -> None:
        tag = self._core_var.get().strip().upper()
        if not tag or not self.state_ids:
            return
        if add:
            self.owner.batch_cores(self.state_ids, add=tag)
        else:
            self.owner.batch_cores(self.state_ids, remove=tag)
