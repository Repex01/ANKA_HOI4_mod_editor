"""Countries editor.

Left: searchable country list (+ "new country"). Right: a tabbed detail panel —
General (tag/culture/color → colors.txt + definition file), Flags (base + one drop zone
per dynamically-parsed ideology), Names (per-language localisation), Territory (capital +
owned states with conflict highlighting), Politics (stability / war support / manpower).

The editor talks to the domain only through services (`CountryService`, `StateService`,
`IdeologyService`) and the mod's flag service, never touching file layout directly.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, messagebox, ttk

from ...services.country_service import CountryRef, CountryService
from ...services.ideology_service import IdeologyService
from ...services.state_service import StateService
from ...services.technology_service import TechnologyService
from ...ui.widgets import ImageDropZone, ScrollableFrame
from ..base import EditorModule, EditorRegistry
from ..common import ScriptEditorDialog
from .dialogs import (
    CharacterPickerDialog,
    CoresEditDialog,
    ImportTechDialog,
    NewCosmeticTagDialog,
    NewCountryDialog,
    StatePickerDialog,
    TechPickerDialog,
)


@EditorRegistry.register
class CountriesEditor(EditorModule):
    id = "countries"
    name_key = "editors.countries.name"
    desc_key = "editors.countries.desc"
    order = 10

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = CountryService(context)
        self.state_service = StateService(context)
        self.ideology_service = IdeologyService(context)
        self.character_service = context.characters  # shared across editor modules
        self.tech_service = TechnologyService(context)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        self._effects_loc_cat = None
        self._tech_selection: list[tuple[str, str]] = []
        self._tech_count_cache: dict[str, int] = {}
        self._loading = False          # suppress dirty-tracking while populating fields
        self._dirty_name = False       # unsaved localisation-tab edits
        self._dirty_politics = False   # unsaved politics-tab edits
        self._loc_ctx: tuple[str, str | None] = ("english", None)  # loaded lang+ideology
        self._refs: list[CountryRef] = []
        self._selected: CountryRef | None = None
        self._color: tuple[int, int, int] | None = None
        self._flag_zones: dict[str | None, ImageDropZone] = {}

    # --- build -----------------------------------------------------------
    def build(self, parent) -> ttk.Widget:
        root = ttk.Frame(parent, style="TFrame")
        root.columnconfigure(0, weight=0, minsize=300)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self._build_list(root)
        self._build_details(root)
        self._search.trace_add("write", lambda *_: self._refresh_list())
        self._wire_dirty()
        self._reload()
        return root

    def _wire_dirty(self) -> None:
        """Mark the editor dirty when any auto-savable form field changes, so
        switching country / leaving the editor persists real edits but never
        rewrites untouched countries. Politics and localisation are tracked
        separately so an edit in one never rewrites the other's files."""
        politics_vars = [
            self._pol_ruling, self._pol_last_election, self._pol_freq, self._pol_elections,
            self._pol_research, self._pol_stability, self._pol_war, self._pol_manpower,
            *self._pop_vars.values(), *self._oob_vars.values(),
        ]
        for var in politics_vars:
            var.trace_add("write", lambda *_: self._mark_dirty("politics"))
        for var in (self._loc_name, self._loc_def, self._loc_adj,
                    self._loc_party, self._loc_party_long, self._loc_party_desc):
            var.trace_add("write", lambda *_: self._mark_dirty("name"))

    def _mark_dirty(self, kind: str) -> None:
        if self._loading:
            return
        if kind == "politics":
            self._dirty_politics = True
        else:
            self._dirty_name = True

    def on_leave(self) -> None:
        """Auto-save pending edits for the selected country when leaving the editor."""
        self._save_all_pending()

    def _build_list(self, root) -> None:
        left = ttk.Frame(root, style="Card.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(4, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text=self.t("countries.list"), style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        # Countries vs. cosmetic tags — the latter only uses General/Flags/Names.
        # Chip-style switch: the selected chip shows a thin white outline
        # (no radio indicator dot).
        mode_row = ttk.Frame(left, style="Card.TFrame")
        mode_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self._list_mode = tk.StringVar(value="countries")
        self._mode_chips: dict[str, tk.Label] = {}
        for value, key in (("countries", "countries.mode.countries"),
                           ("cosmetic", "countries.mode.cosmetic")):
            chip = tk.Label(mode_row, text=self.t(key), bd=0, padx=10, pady=4,
                            bg=self.palette.surface_alt, fg=self.palette.text,
                            cursor="hand2", highlightthickness=1,
                            highlightbackground=self.palette.surface_alt)
            chip.pack(side="left", padx=(0, 8))
            chip.bind("<Button-1>", lambda e, v=value: self._set_list_mode(v))
            self._mode_chips[value] = chip
        self._sync_mode_chips()

        self._search = tk.StringVar()
        entry = ttk.Entry(left, textvariable=self._search)
        entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._placeholder(entry, self.t("modlist.search"))

        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text=self.t("countries.vanilla"), variable=self._vanilla,
                        command=self._reload).grid(row=3, column=0, sticky="w", pady=(0, 6))

        self._tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self._tree.grid(row=4, column=0, sticky="nsew")
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        sb = ttk.Scrollbar(left, orient="vertical", command=self._tree.yview)
        sb.grid(row=4, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)

        self._add_btn = ttk.Button(left, text="➕  " + self.t("countries.add"),
                                   style="Accent.TButton", command=self._new_country)
        self._add_btn.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    @property
    def _cosmetic_mode(self) -> bool:
        return self._list_mode.get() == "cosmetic"

    def _set_list_mode(self, value: str) -> None:
        if value == self._list_mode.get():
            return
        self._list_mode.set(value)
        self._sync_mode_chips()
        self._list_mode_changed()

    def _sync_mode_chips(self) -> None:
        selected = self._list_mode.get()
        for value, chip in self._mode_chips.items():
            chip.configure(highlightbackground=(
                "#ffffff" if value == selected else self.palette.surface_alt))

    def _list_mode_changed(self) -> None:
        self._save_all_pending()
        self._selected = None
        add_key = "countries.add_cosmetic" if self._cosmetic_mode else "countries.add"
        self._add_btn.configure(text="➕  " + self.t(add_key))
        self._update_tab_states()
        self._reload()
        self._g_title.configure(text=self.t("editor.select_module"))
        self._color_btn.configure(state="disabled")
        self._delete_btn.configure(state="disabled")

    def _update_tab_states(self) -> None:
        """In cosmetic mode only General / Flags / Names are meaningful."""
        state = "disabled" if self._cosmetic_mode else "normal"
        for idx in self._country_only_tabs:
            self._nb.tab(idx, state=state)
        if self._cosmetic_mode and self._nb.index("current") in self._country_only_tabs:
            self._nb.select(0)

    def _build_details(self, root) -> None:
        self._nb = ttk.Notebook(root)
        self._nb.grid(row=0, column=1, sticky="nsew")
        self._build_tab_general()
        self._build_tab_flags()
        self._build_tab_localisation()
        self._build_tab_territory()
        self._build_tab_politics()
        self._build_tab_technology()
        self._build_tab_characters()
        self._build_tab_effects()
        # Tab indices that make no sense for cosmetic tags (everything except
        # General / Flags / Names).
        self._country_only_tabs = tuple(range(3, len(self._nb.tabs())))

    # --- tab: general ----------------------------------------------------
    def _build_tab_general(self) -> None:
        tab = ttk.Frame(self._nb, style="Card.TFrame", padding=18)
        self._nb.add(tab, text=self.t("countries.tabs.general"))
        tab.columnconfigure(1, weight=1)

        self._g_title = ttk.Label(tab, text=self.t("editor.select_module"), style="Heading.TLabel")
        self._g_title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

        self._row(tab, 1, self.t("countries.tag"), "_g_tag")

        ttk.Label(tab, text=self.t("countries.graphical_culture"),
                  style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self._g_culture_var = tk.StringVar()
        self._g_culture_combo = ttk.Combobox(
            tab, textvariable=self._g_culture_var, state="disabled", width=26,
            values=self.service.list_graphical_cultures())
        self._g_culture_combo.grid(row=2, column=1, sticky="w", padx=8)
        self._g_culture_combo.bind("<<ComboboxSelected>>", self._culture_changed)

        ttk.Label(tab, text=self.t("countries.color"), style="CardMuted.TLabel").grid(
            row=3, column=0, sticky="w", pady=4)
        crow = ttk.Frame(tab, style="Card.TFrame")
        crow.grid(row=3, column=1, sticky="w", padx=8)
        self._swatch = tk.Canvas(crow, width=30, height=20, highlightthickness=1,
                                 highlightbackground=self.palette.border, bd=0)
        self._swatch.pack(side="left")
        self._color_btn = ttk.Button(crow, text="…", width=4, command=self._pick_color, state="disabled")
        self._color_btn.pack(side="left", padx=6)
        self._g_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._g_status.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        tab.rowconfigure(5, weight=1)
        self._delete_btn = ttk.Button(tab, text="🗑  " + self.t("countries.delete"),
                                      command=self._delete_country, state="disabled")
        self._delete_btn.grid(row=6, column=0, sticky="w", pady=(10, 0))

    # --- tab: flags ------------------------------------------------------
    def _build_tab_flags(self) -> None:
        tab = ttk.Frame(self._nb, style="Card.TFrame")
        self._nb.add(tab, text=self.t("countries.tabs.flags"))
        scroll = ScrollableFrame(tab, bg=self.palette.surface)
        scroll.pack(fill="both", expand=True)
        body = scroll.body
        body.configure(style="Card.TFrame")

        ttk.Label(body, text=self.t("countries.base_flag"), style="Heading.TLabel").pack(
            anchor="w", padx=16, pady=(14, 2))
        self._flag_zones[None] = ImageDropZone(
            body, on_image=lambda p: self._on_flag(p, None),
            prompt=self.t("countries.flag.drop"), preview_size=(164, 104), palette=self.palette)
        self._flag_zones[None].pack(anchor="w", padx=16, pady=(0, 10))

        ttk.Label(body, text=self.t("countries.flag.ideology_hint"), style="CardMuted.TLabel",
                  wraplength=520, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

        for ideo in self.ideology_service.list_groups():
            ttk.Label(body, text=ideo.name, style="Card.TLabel").pack(anchor="w", padx=16, pady=(6, 0))
            zone = ImageDropZone(
                body, on_image=lambda p, s=ideo.name: self._on_flag(p, s),
                prompt=self.t("countries.flag.drop"), preview_size=(120, 76), palette=self.palette)
            zone.pack(anchor="w", padx=16, pady=(0, 6))
            self._flag_zones[ideo.name] = zone

        self._flag_status = ttk.Label(body, text="", style="CardMuted.TLabel")
        self._flag_status.pack(anchor="w", padx=16, pady=(6, 12))

    # --- tab: localisation ----------------------------------------------
    def _build_tab_localisation(self) -> None:
        from ...config.constants import HOI4_LANGUAGES
        tab = ttk.Frame(self._nb, style="Card.TFrame", padding=18)
        self._nb.add(tab, text=self.t("countries.tabs.localisation"))
        tab.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(tab, text=self.t("countries.name_language"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=4)
        self._loc_lang = tk.StringVar(value="english")
        ttk.Combobox(tab, textvariable=self._loc_lang, values=list(HOI4_LANGUAGES), state="readonly",
                     width=16).grid(row=r, column=1, sticky="w", padx=8); r += 1

        ttk.Label(tab, text=self.t("countries.name_ideology"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=4)
        self._loc_base_label = self.t("countries.name_base")
        self._loc_ideology = tk.StringVar(value=self._loc_base_label)
        ideo_values = [self._loc_base_label] + [g.name for g in self.ideology_service.list_groups()]
        ttk.Combobox(tab, textvariable=self._loc_ideology, values=ideo_values, state="readonly",
                     width=16).grid(row=r, column=1, sticky="w", padx=8); r += 1

        # --- Country name section ---
        ttk.Label(tab, text=self.t("countries.section.country_name"), style="Heading.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(12, 4)); r += 1
        self._loc_name = tk.StringVar()
        self._loc_row(tab, r, self.t("countries.name_value"), self._loc_name); r += 1
        self._loc_def = tk.StringVar()
        self._loc_row(tab, r, self.t("countries.name_definite"), self._loc_def); r += 1
        self._loc_adj = tk.StringVar()
        self._loc_row(tab, r, self.t("countries.name_adjective"), self._loc_adj); r += 1

        # Spacer + separator between the country-name and party sections.
        ttk.Separator(tab, orient="horizontal").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=(16, 4)); r += 1

        # --- Party section (optional) ---
        ttk.Label(tab, text=self.t("countries.section.party"), style="Heading.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(4, 0)); r += 1
        ttk.Label(tab, text=self.t("countries.party_optional"), style="CardMuted.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 4)); r += 1
        self._loc_party = tk.StringVar()
        self._loc_party_entry = self._loc_row(tab, r, self.t("countries.party_name"), self._loc_party); r += 1
        self._loc_party_long = tk.StringVar()
        self._loc_party_long_entry = self._loc_row(tab, r, self.t("countries.party_long"), self._loc_party_long); r += 1
        self._loc_party_desc = tk.StringVar()
        self._loc_party_desc_entry = self._loc_row(tab, r, self.t("countries.party_desc"), self._loc_party_desc); r += 1

        ttk.Label(tab, text=self.t("countries.autosave_hint"), style="CardMuted.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(12, 2)); r += 1
        ttk.Label(tab, text=self.t("countries.existing_names"), style="CardMuted.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(8, 2)); r += 1
        self._loc_existing = ttk.Label(tab, text="", style="Card.TLabel", justify="left", wraplength=460)
        self._loc_existing.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self._loc_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._loc_status.grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Switching language/ideology first flushes pending edits for the PREVIOUS
        # combination, so nothing typed is ever lost (auto-save, no Save button).
        self._loc_lang.trace_add("write", lambda *_: self._loc_context_changed())
        self._loc_ideology.trace_add("write", lambda *_: self._loc_context_changed())

    def _loc_row(self, parent, row, label, var) -> ttk.Entry:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", padx=8)
        return entry

    # --- tab: territory --------------------------------------------------
    def _build_tab_territory(self) -> None:
        tab = ttk.Frame(self._nb, style="Card.TFrame", padding=18)
        self._nb.add(tab, text=self.t("countries.tabs.territory"))
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)

        ttk.Label(tab, text=self.t("countries.capital"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=4)
        cap_row = ttk.Frame(tab, style="Card.TFrame")
        cap_row.grid(row=0, column=1, sticky="w", padx=8)
        self._capital = tk.StringVar()
        # Capital must be one of the country's owned states (mapping label -> id).
        self._capital_options: dict[str, int] = {}
        self._capital_combo = ttk.Combobox(cap_row, textvariable=self._capital, state="readonly", width=24)
        self._capital_combo.pack(side="left")
        # Auto-save: picking a capital persists it immediately.
        self._capital_combo.bind("<<ComboboxSelected>>", lambda e: self._save_capital())

        head = ttk.Frame(tab, style="Card.TFrame")
        head.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        ttk.Label(head, text=self.t("countries.owned_states"), style="Heading.TLabel").pack(side="left")
        ttk.Button(head, text="➕ " + self.t("countries.add_state"),
                   command=self._add_state).pack(side="right")

        self._states_tree = ttk.Treeview(tab, columns=("name", "cores", "provinces"),
                                         show="headings", selectmode="extended")
        self._states_tree.heading("name", text=self.t("countries.owned_states"))
        self._states_tree.heading("cores", text=self.t("countries.column_cores"))
        self._states_tree.heading("provinces", text=self.t("countries.column_provinces"))
        self._states_tree.column("name", width=190)
        self._states_tree.column("cores", width=130)
        self._states_tree.column("provinces", width=230)
        self._states_tree.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self._states_tree.bind("<Double-1>", lambda e: self._edit_state_cores())
        sb = ttk.Scrollbar(tab, orient="vertical", command=self._states_tree.yview)
        sb.grid(row=2, column=2, sticky="ns")
        self._states_tree.configure(yscrollcommand=sb.set)
        self._terr_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._terr_status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(tab, text=self.t("countries.cores_hint"), style="CardMuted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))

    # --- tab: politics ---------------------------------------------------
    def _build_tab_politics(self) -> None:
        outer = ttk.Frame(self._nb, style="Card.TFrame")
        self._nb.add(outer, text=self.t("countries.tabs.politics"))
        scroll = ScrollableFrame(outer, bg=self.palette.surface)
        scroll.pack(fill="both", expand=True)
        tab = scroll.body
        tab.configure(style="Card.TFrame")
        tab.columnconfigure(1, weight=1)
        row = 0

        # Ruling party & elections
        ttk.Label(tab, text=self.t("countries.section.politics"), style="Heading.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 6)); row += 1

        self._pol_ruling = tk.StringVar()
        self._groups = [g.name for g in self.ideology_service.list_groups()]
        self._combo_field(tab, row, self.t("countries.ruling_party"), self._pol_ruling, self._groups); row += 1
        self._pol_last_election = tk.StringVar()
        self._entry_field(tab, row, self.t("countries.last_election"), self._pol_last_election); row += 1
        self._pol_freq = tk.StringVar()
        self._entry_field(tab, row, self.t("countries.election_frequency"), self._pol_freq); row += 1
        self._pol_elections = tk.BooleanVar(value=False)
        ttk.Checkbutton(tab, text=self.t("countries.elections_allowed"), variable=self._pol_elections,
                        style="Card.TCheckbutton").grid(row=row, column=0, columnspan=2, sticky="w", padx=16, pady=4)
        row += 1

        # Popularities (dynamic ideologies)
        ttk.Label(tab, text=self.t("countries.section.popularities"), style="Heading.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4)); row += 1
        self._pop_vars: dict[str, tk.StringVar] = {}
        pop_frame = ttk.Frame(tab, style="Card.TFrame")
        pop_frame.grid(row=row, column=0, columnspan=2, sticky="w", padx=16); row += 1
        for i, group in enumerate(self._groups):
            var = tk.StringVar()
            self._pop_vars[group] = var
            ttk.Label(pop_frame, text=group, style="CardMuted.TLabel").grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(pop_frame, textvariable=var, width=6)
            e.grid(row=i, column=1, sticky="w", padx=8)
            var.trace_add("write", lambda *_: self._update_pop_sum())
        self._pop_sum = ttk.Label(pop_frame, text="", style="CardMuted.TLabel")
        self._pop_sum.grid(row=len(self._groups), column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Misc: stability / war support / manpower / OOB
        ttk.Label(tab, text=self.t("countries.section.misc"), style="Heading.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4)); row += 1
        self._pol_research = tk.StringVar()
        self._pol_stability = tk.StringVar()
        self._pol_war = tk.StringVar()
        self._pol_manpower = tk.StringVar()
        self._entry_field(tab, row, self.t("countries.research_slots"), self._pol_research); row += 1
        self._entry_field(tab, row, self.t("countries.stability"), self._pol_stability); row += 1
        self._entry_field(tab, row, self.t("countries.war_support"), self._pol_war); row += 1
        self._entry_field(tab, row, self.t("countries.manpower"), self._pol_manpower); row += 1

        # Order of battle: land / naval / air, each from the existing OOB files.
        self._oob_none_label = self.t("countries.oob_none")
        oob_values = [self._oob_none_label] + self.service.list_oob_files()
        self._oob_vars: dict[str, tk.StringVar] = {}
        self._oob_combos: dict[str, ttk.Combobox] = {}
        for kind in ("land", "naval", "air"):
            var = tk.StringVar()
            self._oob_vars[kind] = var
            self._oob_combos[kind] = self._combo_field(
                tab, row, self.t(f"countries.oob_{kind}"), var, oob_values, width=28); row += 1
        self._pol_oob = self._oob_vars["land"]  # back-compat alias

        ttk.Label(tab, text=self.t("countries.autosave_hint"), style="CardMuted.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 2)); row += 1
        self._pol_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._pol_status.grid(row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

    # --- tab: technology -------------------------------------------------
    def _build_tab_technology(self) -> None:
        tab = ttk.Frame(self._nb, style="Card.TFrame", padding=18)
        self._nb.add(tab, text=self.t("countries.tabs.technology"))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        head = ttk.Frame(tab, style="Card.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(head, text=self.t("countries.tech.list"), style="Heading.TLabel").pack(side="left")
        ttk.Button(head, text="➕ " + self.t("countries.tech.add"), command=self._add_tech).pack(side="right")
        ttk.Button(head, text="⬇ " + self.t("countries.tech.import"),
                   command=self._import_tech).pack(side="right", padx=6)

        self._tech_tree = ttk.Treeview(tab, columns=("t",), show="headings", selectmode="extended")
        self._tech_tree.heading("t", text=self.t("countries.tech.list"))
        self._tech_tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tab, orient="vertical", command=self._tech_tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tech_tree.configure(yscrollcommand=sb.set)

        actions = ttk.Frame(tab, style="Card.TFrame")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="🗑 " + self.t("countries.tech.remove"), command=self._remove_tech).pack(side="left")
        self._tech_status = ttk.Label(actions, text="", style="CardMuted.TLabel")
        self._tech_status.pack(side="left", padx=10)

    # --- tab: additional effects ----------------------------------------
    def _build_tab_effects(self) -> None:
        tab = ttk.Frame(self._nb, style="Card.TFrame", padding=18)
        self._nb.add(tab, text=self.t("countries.tabs.effects"))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)

        head = ttk.Frame(tab, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text=self.t("countries.effects.title"),
                  style="Heading.TLabel").pack(side="left")
        self._effects_edit_btn = ttk.Button(
            head, text="✎ " + self.t("countries.effects.edit"),
            style="Accent.TButton", command=self._edit_effects, state="disabled")
        self._effects_edit_btn.pack(side="right")

        ttk.Label(tab, text=self.t("countries.effects.hint"), style="CardMuted.TLabel",
                  wraplength=560, justify="left").grid(row=1, column=0, sticky="w",
                                                       pady=(6, 8))
        ttk.Label(tab, text=self.t("countries.effects.preview"),
                  style="CardMuted.TLabel").grid(row=2, column=0, sticky="w")
        wrap = ttk.Frame(tab, style="Card.TFrame")
        wrap.grid(row=3, column=0, sticky="nsew", pady=(2, 0))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self._effects_text = tk.Text(wrap, wrap="none", relief="flat",
                                     bg=self.palette.surface_alt, fg=self.palette.text,
                                     font=("Consolas", 10), tabs="24",
                                     state="disabled", height=10)
        self._effects_text.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self._effects_text.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb = ttk.Scrollbar(wrap, orient="horizontal", command=self._effects_text.xview)
        xsb.grid(row=1, column=0, sticky="ew")
        self._effects_text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self._effects_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._effects_status.grid(row=4, column=0, sticky="w", pady=(8, 0))

    def _refresh_effects_preview(self) -> None:
        text = ""
        if self._selected is not None:
            text = self.service.get_history_text(self._selected.tag)
        self._effects_text.configure(state="normal")
        self._effects_text.delete("1.0", "end")
        self._effects_text.insert("1.0", text)
        self._effects_text.configure(state="disabled")

    def _edit_effects(self) -> None:
        if self._selected is None:
            return
        # Persist pending edits from the other tabs so the editor reads current
        # on-disk state (tech / politics / names share the same history file).
        self._save_all_pending()
        tag, name = self._selected.tag, self._selected.name
        current = self.service.get_history_text(tag)

        def submitted(text: str) -> None:
            try:
                self.service.set_history_text(tag, name, text)
            except Exception as exc:
                self._effects_status.configure(
                    text=f"{self.t('common.error')}: {exc}",
                    foreground=self.palette.danger)
                return
            # Re-read every derived tab from disk so e.g. set_technology edits
            # made here show up in the Technology tab.
            if self._selected is not None and self._selected.tag == tag:
                self._populate(self._selected)
            self._effects_status.configure(text=self.t("countries.effects.saved"),
                                           foreground=self.palette.text_muted)

        ScriptEditorDialog(self._nb, self, self.t("countries.effects.title"),
                           current, submitted, ("effect", "trigger"), tag)

    def _save_all_pending(self) -> None:
        """Flush unsaved edits to disk — on country switch, on leaving the editor,
        and before the effects editor reads the history file. Technology and
        territory changes are saved immediately when made, so only the free-form
        localisation / politics fields need flushing here."""
        if self._selected is None:
            return
        if self._dirty_name:
            self._save_name()
            self._dirty_name = False
        if self._dirty_politics and not self._selected.cosmetic:
            self._save_politics()
            self._dirty_politics = False

    # --- script-editor owner protocol (for the effects tab) -------------
    @property
    def _effects_loc(self):
        if self._effects_loc_cat is None:
            from ...services._locutil import LocCatalog
            self._effects_loc_cat = LocCatalog(
                self.context.mod.path, self.context.game_path,
                vanilla_filter="anka_country_effects",
                default_pattern="anka_country_effects_l_{lang}.yml",
                dep_roots=self.context.dependency_paths)
        return self._effects_loc_cat

    def loc_get(self, key: str, language: str) -> str:
        return self._effects_loc.get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self._effects_loc.set(key, language, text)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        if vtype == "event":
            from ...services.event_service import EventService
            if getattr(self, "_event_service", None) is None:
                self._event_service = EventService(self.context)
            return self._event_service.event_options()
        cached = self._value_options.get(vtype)
        if cached is not None:
            return cached
        opts: list[tuple[str, str]] = []
        if vtype == "country":
            for ref in self.service.list_tags(include_vanilla=True):
                opts.append((f"{ref.tag} · {ref.name}", ref.tag))
        elif vtype == "state":
            for st in self.state_service.list_states():
                opts.append((f"{st.id} · {st.name}", str(st.id)))
        elif vtype == "idea":
            from ...services.idea_service import IdeaService
            if getattr(self, "_idea_service", None) is None:
                self._idea_service = IdeaService(self.context)
            for idea in self._idea_service.list_ideas():
                opts.append((f"{idea.id} · {idea.category}", idea.id))
        elif vtype == "focus":
            from ...services.focus_service import FocusService
            for fid in FocusService(self.context).focus_ids():
                opts.append((fid, fid))
        elif vtype == "modifier":
            from ..effects import ScriptCatalog
            for name, mod in sorted(ScriptCatalog.modifiers().items()):
                scopes = ", ".join(mod.scopes)
                opts.append((f"{name} · {scopes}" if scopes else name, name))
        self._value_options[vtype] = opts
        return opts

    # --- tab: characters (assign existing) ------------------------------
    def _build_tab_characters(self) -> None:
        tab = ttk.Frame(self._nb, style="Card.TFrame", padding=18)
        self._nb.add(tab, text=self.t("countries.tabs.characters"))
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=0, minsize=220)
        tab.rowconfigure(1, weight=1)

        head = ttk.Frame(tab, style="Card.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(head, text=self.t("countries.char.recruited"), style="Heading.TLabel").pack(side="left")
        ttk.Button(head, text="➕ " + self.t("countries.char.add"),
                   command=self._add_character).pack(side="right")
        ttk.Button(head, text="🗑 " + self.t("countries.char.remove"),
                   command=self._remove_character).pack(side="right", padx=6)

        self._chars_tree = ttk.Treeview(tab, columns=("roles",), show="tree headings", selectmode="browse")
        self._chars_tree.heading("#0", text=self.t("countries.char.id_name"))
        self._chars_tree.heading("roles", text=self.t("countries.char.roles"))
        self._chars_tree.column("#0", width=300)
        self._chars_tree.column("roles", width=150, anchor="w")
        self._chars_tree.grid(row=1, column=0, sticky="nsew")
        self._chars_tree.bind("<<TreeviewSelect>>", self._on_character_select)
        sb = ttk.Scrollbar(tab, orient="vertical", command=self._chars_tree.yview)
        sb.grid(row=1, column=0, sticky="nse")
        self._chars_tree.configure(yscrollcommand=sb.set)

        # Portrait gallery for the selected character — shows ALL portraits
        # (leader/commander/advisor, every category & size).
        side = ttk.Frame(tab, style="Card.TFrame")
        side.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        side.rowconfigure(1, weight=1)
        side.columnconfigure(0, weight=1)
        self._char_name_lbl = ttk.Label(side, text="", style="Heading.TLabel", wraplength=210)
        self._char_name_lbl.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._gallery = ScrollableFrame(side, bg=self.palette.surface)
        self._gallery.grid(row=1, column=0, sticky="nsew")
        self._gallery.body.configure(style="Card.TFrame")
        self._portrait_photos: list = []  # keep PhotoImage refs alive

        self._chars_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._chars_status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # --- data flow -------------------------------------------------------
    def _reload(self) -> None:
        if self._cosmetic_mode:
            self._refs = self.service.list_cosmetic_tags(include_vanilla=self._vanilla.get())
        else:
            self._refs = self.service.list_tags(include_vanilla=self._vanilla.get())
        self._refresh_list()

    def _refresh_list(self) -> None:
        query = (self._search.get() or "").strip().lower()
        if query == self.t("modlist.search").lower():
            query = ""
        self._tree.delete(*self._tree.get_children())
        for ref in self._refs:
            if query and query not in ref.tag.lower() and query not in ref.name.lower():
                continue
            marker = "  ✎" if ref.edited else ("  ·" if ref.is_vanilla else "")
            self._tree.insert("", "end", iid=ref.tag, text=f"{ref.tag}   {ref.name}{marker}")

    def _on_select(self, _e=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        ref = next((r for r in self._refs if r.tag == sel[0]), None)
        if ref is None or ref is self._selected:
            return
        # Auto-save: flush pending edits of the previously selected entry first.
        self._save_all_pending()
        self._selected = ref
        self._populate(ref)

    def _populate(self, ref: CountryRef) -> None:
        self._loading = True
        if ref.cosmetic:
            self._populate_cosmetic(ref)
            return
        self._g_title.configure(text=f"{ref.tag} — {ref.name}")
        self._g_tag.configure(text=ref.tag)
        self._g_culture_var.set(self.service.graphical_culture(ref) or "")
        self._g_culture_combo.configure(state="readonly")
        self._set_color(self.service.get_color(ref).rgb)
        self._color_btn.configure(state="normal")
        self._g_status.configure(text="")

        # flags
        for suffix, zone in self._flag_zones.items():
            existing = self.service.flag_path(ref.tag, suffix)
            zone.show_image(existing) if existing else zone.clear()
        self._flag_status.configure(text="")

        # names
        self._names = self.service.get_names(ref.tag)
        self._load_localisation()
        self._render_existing_names()

        # history (capital + misc)
        hist = self.service.get_history(ref.tag)
        self._pol_research.set(hist.get("set_research_slots", ""))
        self._pol_stability.set(hist.get("set_stability", ""))
        self._pol_war.set(hist.get("set_war_support", ""))
        self._pol_manpower.set(hist.get("add_manpower", ""))

        # set_politics block
        politics = self.service.get_politics(ref.tag)
        self._pol_ruling.set(politics.get("ruling_party", ""))
        self._pol_last_election.set(politics.get("last_election", ""))
        self._pol_freq.set(politics.get("election_frequency", ""))
        self._pol_elections.set((politics.get("elections_allowed", "no") == "yes"))

        # popularities (dynamic)
        pops = self.service.get_popularities(ref.tag)
        for group, var in self._pop_vars.items():
            var.set(str(pops.get(group, "")))
        self._update_pop_sum()

        # OOB (land / naval / air) — refresh the file list so OOBs created in the
        # OOB editor this session show up without reopening the mod.
        oob_values = [self._oob_none_label] + self.service.list_oob_files()
        for kind, var in self._oob_vars.items():
            self._oob_combos[kind].configure(values=oob_values)
            var.set(self.service.get_oob(ref.tag, kind) or self._oob_none_label)

        # technologies (tech, condition) entries
        self._tech_selection = self.service.get_technology_entries(ref.tag)
        self._refresh_techs()

        # additional effects (raw history block)
        self._refresh_effects_preview()
        self._effects_edit_btn.configure(state="normal")
        self._effects_status.configure(text="")

        self._refresh_owned_states(current_capital=hist.get("capital", ""))
        self._refresh_characters()

        # Deletion is only allowed for mod-defined countries.
        self._delete_btn.configure(state=("disabled" if ref.is_vanilla else "normal"))

        self._loading = False
        self._dirty_name = False
        self._dirty_politics = False

    def _populate_cosmetic(self, ref: CountryRef) -> None:
        """Cosmetic tags only use General (tag + color), Flags and Names."""
        self._g_title.configure(text=ref.tag)
        self._g_tag.configure(text=ref.tag)
        self._g_culture_var.set("")
        self._g_culture_combo.configure(state="disabled")
        self._set_color(self.service.get_cosmetic_color(ref.tag).rgb)
        self._color_btn.configure(state="normal")
        self._g_status.configure(text="")

        for suffix, zone in self._flag_zones.items():
            existing = self.service.flag_path(ref.tag, suffix)
            zone.show_image(existing) if existing else zone.clear()
        self._flag_status.configure(text="")

        self._names = self.service.get_names(ref.tag)
        self._load_localisation()
        self._render_existing_names()

        self._delete_btn.configure(state=("disabled" if ref.is_vanilla else "normal"))
        self._loading = False
        self._dirty_name = False
        self._dirty_politics = False

    # --- general actions -------------------------------------------------
    def _pick_color(self) -> None:
        if not self._selected:
            return
        initial = "#%02x%02x%02x" % (self._color or (120, 120, 120))
        rgb, _ = colorchooser.askcolor(color=initial, title=self.t("countries.color"))
        if rgb is None:
            return
        rgb_int = tuple(int(c) for c in rgb)
        self._set_color(rgb_int)
        try:
            if self._selected.cosmetic:
                written = [self.service.set_cosmetic_color(self._selected.tag, rgb_int)]
            else:
                written = self.service.set_color(self._selected.tag, rgb_int, ref=self._selected)
            self._g_status.configure(text=f"{self.t('settings.saved')} ({len(written)})",
                                     foreground=self.palette.text_muted)
        except Exception as exc:
            self._g_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)

    def _culture_changed(self, _event=None) -> None:
        """Auto-save: applying a graphical culture writes the definition file."""
        if not self._selected or self._selected.cosmetic or self._loading:
            return
        culture = self._g_culture_var.get().strip()
        if not culture or culture == self.service.graphical_culture(self._selected):
            return
        try:
            self.service.set_graphical_culture(self._selected, culture)
            self._g_status.configure(text=self.t("settings.saved"),
                                     foreground=self.palette.text_muted)
        except Exception as exc:
            self._g_status.configure(text=f"{self.t('common.error')}: {exc}",
                                     foreground=self.palette.danger)

    # --- flag actions ----------------------------------------------------
    def _on_flag(self, path: Path, suffix: str | None) -> None:
        if not self._selected:
            return
        try:
            written = self.context.flags.generate(path, self._selected.tag, suffix)
            self._flag_status.configure(
                text=self.t("countries.flag.generated", count=len(written)),
                foreground=self.palette.text_muted)
        except Exception as exc:
            self._flag_status.configure(text=f"{self.t('common.error')}: {exc}",
                                        foreground=self.palette.danger)

    # --- name actions ----------------------------------------------------
    def _current_ideology(self) -> str | None:
        value = self._loc_ideology.get()
        return None if value == self._loc_base_label else value

    def _loc_context_changed(self) -> None:
        """Language/ideology combo changed: flush pending edits for the previous
        combination, then load the new one."""
        if self._selected is not None and self._dirty_name:
            lang, ideology = self._loc_ctx
            self._save_name(language=lang, ideology=ideology)
            self._dirty_name = False
        self._load_localisation()

    def _load_localisation(self) -> None:
        if not self._selected:
            return
        ideology = self._current_ideology()
        loc = self.service.get_localisation(self._selected.tag, self._loc_lang.get(), ideology)
        was_loading, self._loading = self._loading, True   # loading, not user edits
        try:
            self._loc_name.set(loc.get("name", ""))
            self._loc_def.set(loc.get("definite", ""))
            self._loc_adj.set(loc.get("adjective", ""))
            self._loc_party.set(loc.get("party", ""))
            self._loc_party_long.set(loc.get("party_long", ""))
            self._loc_party_desc.set(loc.get("party_desc", ""))
        finally:
            self._loading = was_loading
        self._loc_ctx = (self._loc_lang.get(), ideology)

    def _render_existing_names(self) -> None:
        names = getattr(self, "_names", {})
        text = "  ".join(f"{lang}: {value}" for lang, value in names.items()) or self.t("common.none")
        self._loc_existing.configure(text=text)

    def _save_name(self, language: str | None = None,
                   ideology: str | None | bool = False) -> None:
        """Persist the name/party fields (auto-saved: on country switch, on
        language/ideology switch, and on leaving the editor). `language`/`ideology`
        override the combo values when flushing edits for a previous combination."""
        if not self._selected:
            return
        name = self._loc_name.get().strip()
        if not name:
            return
        if ideology is False:
            ideology = self._current_ideology()
        if language is None:
            language = self._loc_lang.get()
        party = (self._loc_party.get().strip(), self._loc_party_long.get().strip(),
                 self._loc_party_desc.get().strip())
        # Party names require an ideology; warn but still save the country name.
        if any(party) and not ideology:
            self._loc_status.configure(text=self.t("countries.party_needs_ideology"),
                                       foreground=self.palette.danger)
        try:
            self.service.set_localisation(
                self._selected.tag, language, ideology,
                name, self._loc_def.get().strip(), self._loc_adj.get().strip(),
                party=party[0] if ideology else "",
                party_long=party[1] if ideology else "",
                party_desc=party[2] if ideology else "")
            self._names = self.service.get_names(self._selected.tag)
            self._render_existing_names()
            if not (any(party) and not ideology):
                self._loc_status.configure(text=self.t("countries.name_saved"),
                                           foreground=self.palette.text_muted)
        except Exception as exc:
            self._loc_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)

    # --- territory actions ----------------------------------------------
    def _refresh_owned_states(self, current_capital: str | None = None) -> None:
        self._states_tree.delete(*self._states_tree.get_children())
        self._capital_options = {}
        if not self._selected:
            self._capital_combo.configure(values=[])
            self._capital.set("")
            return
        owned = [s for s in self.state_service.list_states() if s.owner == self._selected.tag]
        if not owned:
            self._states_tree.insert("", "end", values=(self.t("countries.no_owned_states"), "", ""))
        for s in owned:
            label = f"{s.id} · {s.name}"
            self._states_tree.insert(
                "", "end", iid=str(s.id),
                values=(label, " ".join(s.cores) or "—",
                        ", ".join(str(p) for p in s.provinces) or "—"))
            self._capital_options[label] = s.id

        # Capital combobox: only owned states are selectable.
        self._capital_combo.configure(values=list(self._capital_options))
        if current_capital is None:
            current_capital = ""
        match = next((lbl for lbl, sid in self._capital_options.items() if str(sid) == str(current_capital)), "")
        self._capital.set(match)

    def _save_capital(self) -> None:
        if not self._selected:
            return
        label = self._capital.get()
        state_id = self._capital_options.get(label)
        if state_id is None:
            self._terr_status.configure(text=self.t("countries.capital_owned_only"),
                                        foreground=self.palette.danger)
            return
        try:
            self.service.set_history_values(self._selected.tag, self._selected.name, capital=state_id)
            self._terr_status.configure(text=self.t("countries.capital_saved"),
                                        foreground=self.palette.text_muted)
        except Exception as exc:
            self._terr_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)

    def _add_state(self) -> None:
        if not self._selected:
            return
        StatePickerDialog(self._nb, self, self._selected.tag)

    def _edit_state_cores(self) -> None:
        """Double-click on owned states: chip editor of add_core_of for every
        selected state at once (click a chip to remove, ➕ to add)."""
        if not self._selected:
            return
        state_ids = [int(iid) for iid in self._states_tree.selection() if iid.isdigit()]
        if not state_ids:
            return
        CoresEditDialog(self._nb, self, state_ids)

    def notify_cores_changed(self, state_ids: list[int]) -> None:
        """Called by CoresEditDialog after each live edit: refresh table + status."""
        keep_capital = self._capital_options.get(self._capital.get())
        self._refresh_owned_states(current_capital=keep_capital)
        self._terr_status.configure(
            text=self.t("countries.cores_saved",
                        ids=", ".join(str(i) for i in state_ids)),
            foreground=self.palette.text_muted)

    def assign_state(self, state_id: int) -> None:
        """Backwards-compatible single-state entry point."""
        self.assign_states([state_id])

    def assign_states(self, state_ids: list[int], core: bool = True,
                      exclusive_core: bool = False) -> None:
        """Called by the picker dialog after one or more states are chosen."""
        if not self._selected:
            return
        keep_capital = self._capital_options.get(self._capital.get())
        conflicts: list[str] = []
        added: list[int] = []
        for state_id in state_ids:
            conflict = self.state_service.owner_conflict(state_id, self._selected.tag)
            try:
                self.state_service.set_owner(state_id, self._selected.tag,
                                             core=core, exclusive_core=exclusive_core)
            except Exception as exc:
                self._terr_status.configure(text=f"{self.t('common.error')}: {exc}",
                                            foreground=self.palette.danger)
                return
            added.append(state_id)
            if conflict:
                conflicts.append(f"{state_id}: {conflict}")
        self._refresh_owned_states(current_capital=keep_capital)
        if conflicts:
            self._terr_status.configure(
                text=self.t("countries.state_conflict", owner=", ".join(conflicts)),
                foreground=self.palette.danger)
        else:
            self._terr_status.configure(
                text=self.t("countries.state_added",
                            id=", ".join(str(i) for i in added)),
                foreground=self.palette.text_muted)

    # --- politics actions ------------------------------------------------
    def _save_politics(self) -> None:
        if not self._selected or self._selected.cosmetic:
            return
        tag, name = self._selected.tag, self._selected.name
        try:
            # scalar misc values
            self.service.set_history_values(
                tag, name,
                set_research_slots=self._pol_research.get().strip(),
                set_stability=self._pol_stability.get().strip(),
                set_war_support=self._pol_war.get().strip(),
                add_manpower=self._pol_manpower.get().strip(),
            )
            # set_politics block
            if self._pol_ruling.get():
                self.service.set_politics(
                    tag, name,
                    ruling_party=self._pol_ruling.get(),
                    last_election=self._pol_last_election.get().strip() or "1936.1.1",
                    election_frequency=self._pol_freq.get().strip() or "48",
                    elections_allowed=self._pol_elections.get(),
                )
            # popularities
            pops = {}
            for group, var in self._pop_vars.items():
                value = var.get().strip()
                if value:
                    try:
                        pops[group] = int(value)
                    except ValueError:
                        pass
            if pops:
                self.service.set_popularities(tag, name, pops)
            # OOB (land / naval / air)
            for kind, var in self._oob_vars.items():
                value = var.get()
                self.service.set_oob(tag, name, "" if value == self._oob_none_label else value, kind)
        except Exception as exc:
            self._pol_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)
            return
        self._pol_status.configure(text=self.t("countries.politics_saved"), foreground=self.palette.text_muted)

    # --- technology actions ----------------------------------------------
    def _refresh_techs(self) -> None:
        self._tech_tree.delete(*self._tech_tree.get_children())
        if not self._tech_selection:
            self._tech_tree.insert("", "end", values=(self.t("countries.tech.none"),))
            return
        for tech, cond in self._tech_selection:
            label = tech + (f"   [{self._cond_label(cond)}]" if cond else "")
            self._tech_tree.insert("", "end", iid=f"{tech}|{cond}", values=(label,))

    @staticmethod
    def _cond_label(cond: str) -> str:
        kind, _, dlc = cond.partition(":")
        return (f"DLC: {dlc}" if kind == "dlc" else f"no DLC: {dlc}") if dlc else ""

    def _add_tech(self) -> None:
        if not self._selected:
            return
        TechPickerDialog(self._nb, self, {t for t, _ in self._tech_selection})

    def add_technologies(self, techs: list[str]) -> None:
        """Manual additions from the picker are unconditional (no DLC gate)."""
        existing = set(self._tech_selection)
        for tech in techs:
            if (tech, "") not in existing:
                self._tech_selection.append((tech, ""))
        self._refresh_techs()
        self._save_tech()

    def _remove_tech(self) -> None:
        changed = False
        for iid in self._tech_tree.selection():
            tech, _, cond = iid.partition("|")
            if (tech, cond) in self._tech_selection:
                self._tech_selection.remove((tech, cond))
                changed = True
        self._refresh_techs()
        if changed:
            self._save_tech()

    def _save_tech(self) -> None:
        if not self._selected or self._selected.cosmetic:
            return
        try:
            self.service.set_technology_entries(self._selected.tag, self._selected.name, self._tech_selection)
            self._tech_status.configure(text=self.t("countries.tech.saved"), foreground=self.palette.text_muted)
        except Exception as exc:
            self._tech_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)

    def tech_count(self, tag: str) -> int:
        """Cached count of a tag's starting technologies (for the import dialog)."""
        if tag not in self._tech_count_cache:
            self._tech_count_cache[tag] = len(self.service.get_technologies(tag))
        return self._tech_count_cache[tag]

    def _import_tech(self) -> None:
        if not self._selected:
            return
        ImportTechDialog(self._nb, self)

    def import_technologies(self, source_tag: str) -> None:
        """Merge another country's starting techs (with their DLC conditions)."""
        imported = self.service.get_technology_entries(source_tag)
        existing = set(self._tech_selection)
        for entry in imported:
            if entry not in existing:
                self._tech_selection.append(entry)
        self._refresh_techs()
        self._save_tech()
        self._tech_status.configure(
            text=self.t("countries.tech.imported", tag=source_tag, count=len(imported)),
            foreground=self.palette.text_muted)

    # --- character actions (assign existing) ----------------------------
    def _refresh_characters(self) -> None:
        self._chars_tree.delete(*self._chars_tree.get_children())
        self._clear_portrait()
        if not self._selected:
            return
        recruited = self.character_service.recruited_by_tag(self._selected.tag)
        if not recruited:
            self._chars_tree.insert("", "end", text=self.t("countries.char.none"), values=("",))
            return
        for c in recruited:
            label = c.char_id + (f"  ({c.name})" if c.name and c.name != c.char_id else "")
            self._chars_tree.insert("", "end", iid=c.char_id, text=label, values=(c.roles_label,))

    def _on_character_select(self, _e=None) -> None:
        sel = self._chars_tree.selection()
        if not sel:
            return
        model = self.character_service.get(sel[0])
        if model is None:
            return
        self._char_name_lbl.configure(text=model.name)
        self._show_portraits(model)

    def _show_portraits(self, model) -> None:
        from PIL import Image, ImageTk
        self._clear_portrait()
        self._char_name_lbl.configure(text=model.name)
        body = self._gallery.body
        sprites = model.sprites()
        if not sprites:
            ttk.Label(body, text=self.t("common.none"), style="CardMuted.TLabel").grid(
                row=0, column=0, padx=6, pady=6)
            return
        for i, (cat, size, sprite) in enumerate(sprites):
            cell = ttk.Frame(body, style="Card.TFrame")
            cell.grid(row=i // 2, column=i % 2, padx=6, pady=6, sticky="n")
            path = self.character_service.resolve_sprite(sprite)
            canvas = tk.Canvas(cell, width=84, height=112, highlightthickness=1,
                               highlightbackground=self.palette.border,
                               bg=self.palette.surface_alt, bd=0)
            canvas.pack()
            if path:
                try:
                    img = Image.open(path)
                    img.thumbnail((82, 110), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self._portrait_photos.append(photo)
                    canvas.create_image(42, 56, image=photo)
                except Exception:
                    canvas.create_text(42, 56, text="✕", fill=self.palette.text_muted)
            else:
                canvas.create_text(42, 56, text="?", fill=self.palette.text_muted)
            ttk.Label(cell, text=f"{cat}/{size}", style="CardMuted.TLabel").pack()

    def _clear_portrait(self) -> None:
        if hasattr(self, "_gallery"):
            for child in self._gallery.body.winfo_children():
                child.destroy()
            self._char_name_lbl.configure(text="")
            self._portrait_photos = []

    def _add_character(self) -> None:
        if not self._selected:
            return
        # The character service is shared across editor modules, so characters created
        # in the Characters editor are already in the cache — no rescan needed.
        CharacterPickerDialog(self._nb, self)

    def assign_character(self, char_id: str) -> None:
        """Recruit an existing (unrecruited) character into the current country."""
        if not self._selected:
            return
        try:
            self.service.add_recruit_character(self._selected.tag, self._selected.name, char_id)
        except Exception as exc:
            self._chars_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)
            return
        self.character_service.mark_recruited(char_id, self._selected.tag)  # O(1) cache update
        self._refresh_characters()
        self._chars_status.configure(text=self.t("countries.char.added", id=char_id),
                                     foreground=self.palette.text_muted)

    def _remove_character(self) -> None:
        sel = self._chars_tree.selection()
        if not sel or not self._selected:
            return
        char_id = sel[0]
        try:
            self.service.remove_recruit_character(self._selected.tag, self._selected.name, char_id)
        except Exception as exc:
            self._chars_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)
            return
        self.character_service.mark_unrecruited(char_id, self._selected.tag)  # O(1) cache update
        self._refresh_characters()
        self._chars_status.configure(text=self.t("countries.char.removed", id=char_id),
                                     foreground=self.palette.text_muted)

    # --- delete ----------------------------------------------------------
    def _delete_country(self) -> None:
        if not self._selected or self._selected.is_vanilla:
            return
        ref = self._selected
        title_key = "countries.delete_cosmetic" if ref.cosmetic else "countries.delete"
        if not messagebox.askyesno(self.t(title_key),
                                   self.t("countries.delete.confirm", tag=ref.tag)):
            return
        try:
            if ref.cosmetic:
                touched = self.service.delete_cosmetic_tag(ref.tag)
            else:
                touched = self.service.delete_country(ref)
        except Exception as exc:
            self._g_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)
            return
        self._selected = None
        if not ref.cosmetic:
            self.state_service.list_states(refresh=True)
        self._reload()
        self._g_title.configure(text=self.t("editor.select_module"))
        self._g_status.configure(text=self.t("countries.deleted", tag=ref.tag, count=len(touched)),
                                 foreground=self.palette.text_muted)
        self._delete_btn.configure(state="disabled")

    # --- new country / cosmetic tag --------------------------------------
    def _new_country(self) -> None:
        if self._cosmetic_mode:
            existing = {r.tag for r in self.service.list_cosmetic_tags(include_vanilla=True)}
            NewCosmeticTagDialog(self._nb, self, existing)
            return
        existing = {r.tag for r in self.service.list_tags(include_vanilla=True)}
        NewCountryDialog(self._nb, self, existing)

    def after_country_created(self, tag: str) -> None:
        self._vanilla.set(False)
        self._reload()
        if self._tree.exists(tag):
            self._tree.selection_set(tag)
            self._tree.see(tag)
            self._on_select()  # populate synchronously (don't wait for the virtual event)
        self._g_status.configure(text=self.t("countries.created", tag=tag), foreground=self.palette.text_muted)

    # --- helpers ---------------------------------------------------------
    def _row(self, parent, row: int, label: str, attr: str) -> None:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        value = ttk.Label(parent, text="—", style="Card.TLabel")
        value.grid(row=row, column=1, sticky="w", padx=8)
        setattr(self, attr, value)

    def _field(self, parent, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=var, width=16).grid(row=row, column=1, sticky="w", padx=8)

    def _entry_field(self, parent, row: int, label: str, var: tk.StringVar, width: int = 16) -> None:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", padx=16, pady=4)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky="w", padx=8, pady=4)

    def _combo_field(self, parent, row: int, label: str, var: tk.StringVar,
                     values: list[str], width: int = 18) -> ttk.Combobox:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", padx=16, pady=4)
        combo = ttk.Combobox(parent, textvariable=var, values=values, state="readonly",
                             width=width)
        combo.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        return combo

    def _update_pop_sum(self) -> None:
        total = 0
        for var in self._pop_vars.values():
            try:
                total += int(var.get() or 0)
            except ValueError:
                pass
        color = self.palette.text_muted if total == 100 else self.palette.danger
        self._pop_sum.configure(text=self.t("countries.popularities_sum", sum=total), foreground=color)

    def _set_color(self, rgb) -> None:
        self._color = tuple(rgb) if rgb else None
        self._swatch.configure(bg="#%02x%02x%02x" % (self._color or (60, 63, 76)))

    def _placeholder(self, entry: ttk.Entry, text: str) -> None:
        entry.insert(0, text)
        entry.bind("<FocusIn>", lambda e: entry.delete(0, "end") if entry.get() == text else None)
