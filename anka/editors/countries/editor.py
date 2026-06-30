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
from .dialogs import (
    CharacterPickerDialog,
    ImportTechDialog,
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
        self._tech_selection: list[tuple[str, str]] = []
        self._tech_count_cache: dict[str, int] = {}
        self._loading = False      # suppress dirty-tracking while populating fields
        self._dirty = False        # any unsaved edits for the selected country?
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
        """Mark the editor dirty when any auto-savable form field changes, so leaving
        the editor persists real edits but never rewrites untouched countries."""
        form_vars = [
            self._pol_ruling, self._pol_last_election, self._pol_freq, self._pol_elections,
            self._pol_research, self._pol_stability, self._pol_war, self._pol_manpower, self._pol_oob,
            self._loc_name, self._loc_def, self._loc_adj,
            *self._pop_vars.values(),
        ]
        for var in form_vars:
            var.trace_add("write", lambda *_: self._mark_dirty())

    def _mark_dirty(self) -> None:
        if not self._loading:
            self._dirty = True

    def on_leave(self) -> None:
        """Auto-save pending edits for the selected country when leaving the editor."""
        if not self._dirty or self._selected is None:
            return
        self._save_politics()
        self._save_tech()
        self._save_name()
        self._dirty = False

    def _build_list(self, root) -> None:
        left = ttk.Frame(root, style="Card.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(3, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text=self.t("countries.list"), style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self._search = tk.StringVar()
        entry = ttk.Entry(left, textvariable=self._search)
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._placeholder(entry, self.t("modlist.search"))

        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text=self.t("countries.vanilla"), variable=self._vanilla,
                        command=self._reload).grid(row=2, column=0, sticky="w", pady=(0, 6))

        self._tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self._tree.grid(row=3, column=0, sticky="nsew")
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        sb = ttk.Scrollbar(left, orient="vertical", command=self._tree.yview)
        sb.grid(row=3, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)

        ttk.Button(left, text="➕  " + self.t("countries.add"), style="Accent.TButton",
                   command=self._new_country).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

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

    # --- tab: general ----------------------------------------------------
    def _build_tab_general(self) -> None:
        tab = ttk.Frame(self._nb, style="Card.TFrame", padding=18)
        self._nb.add(tab, text=self.t("countries.tabs.general"))
        tab.columnconfigure(1, weight=1)

        self._g_title = ttk.Label(tab, text=self.t("editor.select_module"), style="Heading.TLabel")
        self._g_title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

        self._row(tab, 1, self.t("countries.tag"), "_g_tag")
        self._row(tab, 2, self.t("countries.graphical_culture"), "_g_culture")

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

        ttk.Label(tab, text=self.t("countries.name_language"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=4)
        self._loc_lang = tk.StringVar(value="english")
        ttk.Combobox(tab, textvariable=self._loc_lang, values=list(HOI4_LANGUAGES), state="readonly",
                     width=16).grid(row=0, column=1, sticky="w", padx=8)

        # Ideology variant: base (no ideology) or a ruling-party cosmetic name.
        ttk.Label(tab, text=self.t("countries.name_ideology"), style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=4)
        self._loc_base_label = self.t("countries.name_base")
        self._loc_ideology = tk.StringVar(value=self._loc_base_label)
        ideo_values = [self._loc_base_label] + [g.name for g in self.ideology_service.list_groups()]
        ttk.Combobox(tab, textvariable=self._loc_ideology, values=ideo_values, state="readonly",
                     width=16).grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(tab, text=self.t("countries.name_value"), style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=4)
        self._loc_name = tk.StringVar()
        ttk.Entry(tab, textvariable=self._loc_name).grid(row=2, column=1, sticky="ew", padx=8)

        ttk.Label(tab, text=self.t("countries.name_definite"), style="CardMuted.TLabel").grid(
            row=3, column=0, sticky="w", pady=4)
        self._loc_def = tk.StringVar()
        ttk.Entry(tab, textvariable=self._loc_def).grid(row=3, column=1, sticky="ew", padx=8)

        ttk.Label(tab, text=self.t("countries.name_adjective"), style="CardMuted.TLabel").grid(
            row=4, column=0, sticky="w", pady=4)
        self._loc_adj = tk.StringVar()
        ttk.Entry(tab, textvariable=self._loc_adj).grid(row=4, column=1, sticky="ew", padx=8)

        ttk.Button(tab, text=self.t("common.save"), style="Accent.TButton",
                   command=self._save_name).grid(row=5, column=1, sticky="w", padx=8, pady=10)

        ttk.Label(tab, text=self.t("countries.existing_names"), style="CardMuted.TLabel").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 2))
        self._loc_existing = ttk.Label(tab, text="", style="Card.TLabel", justify="left", wraplength=460)
        self._loc_existing.grid(row=7, column=0, columnspan=2, sticky="w")
        self._loc_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._loc_status.grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Reload fields whenever language or ideology variant changes.
        self._loc_lang.trace_add("write", lambda *_: self._load_localisation())
        self._loc_ideology.trace_add("write", lambda *_: self._load_localisation())

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
        ttk.Button(cap_row, text=self.t("common.save"), command=self._save_capital).pack(side="left", padx=6)

        head = ttk.Frame(tab, style="Card.TFrame")
        head.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        ttk.Label(head, text=self.t("countries.owned_states"), style="Heading.TLabel").pack(side="left")
        ttk.Button(head, text="➕ " + self.t("countries.add_state"),
                   command=self._add_state).pack(side="right")

        self._states_tree = ttk.Treeview(tab, columns=("name",), show="headings", selectmode="browse")
        self._states_tree.heading("name", text=self.t("countries.owned_states"))
        self._states_tree.grid(row=2, column=0, columnspan=2, sticky="nsew")
        sb = ttk.Scrollbar(tab, orient="vertical", command=self._states_tree.yview)
        sb.grid(row=2, column=2, sticky="ns")
        self._states_tree.configure(yscrollcommand=sb.set)
        self._terr_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._terr_status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

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

        self._pol_oob = tk.StringVar()
        oob_values = [self.t("countries.oob_none")] + self.service.list_oob_files()
        self._oob_none_label = self.t("countries.oob_none")
        self._combo_field(tab, row, self.t("countries.oob"), self._pol_oob, oob_values, width=28); row += 1

        ttk.Button(tab, text=self.t("common.save"), style="Accent.TButton",
                   command=self._save_politics).grid(row=row, column=0, sticky="w", padx=16, pady=12); row += 1
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
        ttk.Button(actions, text=self.t("common.save"), style="Accent.TButton",
                   command=self._save_tech).pack(side="left", padx=8)
        self._tech_status = ttk.Label(actions, text="", style="CardMuted.TLabel")
        self._tech_status.pack(side="left", padx=10)

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

        # Portrait preview panel for the selected character.
        side = ttk.Frame(tab, style="Card.TFrame")
        side.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        self._char_name_lbl = ttk.Label(side, text="", style="Heading.TLabel", wraplength=200)
        self._char_name_lbl.pack(anchor="w", pady=(0, 8))
        self._portrait_canvas = tk.Canvas(side, width=160, height=210, highlightthickness=1,
                                          highlightbackground=self.palette.border,
                                          bg=self.palette.surface_alt, bd=0)
        self._portrait_canvas.pack(anchor="w")
        self._portrait_caption = ttk.Label(side, text="", style="CardMuted.TLabel", wraplength=200)
        self._portrait_caption.pack(anchor="w", pady=(4, 0))
        self._portrait_photos: list = []  # keep refs

        self._chars_status = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self._chars_status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # --- data flow -------------------------------------------------------
    def _reload(self) -> None:
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
            label = f"{ref.tag}   {ref.name}" + ("" if not ref.is_vanilla else "  ·")
            self._tree.insert("", "end", iid=ref.tag, text=label)

    def _on_select(self, _e=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        ref = next((r for r in self._refs if r.tag == sel[0]), None)
        if ref is None:
            return
        self._selected = ref
        self._populate(ref)

    def _populate(self, ref: CountryRef) -> None:
        self._loading = True
        self._g_title.configure(text=f"{ref.tag} — {ref.name}")
        self._g_tag.configure(text=ref.tag)
        self._g_culture.configure(text=self.service.graphical_culture(ref) or "—")
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

        # OOB
        oob = self.service.get_oob(ref.tag)
        self._pol_oob.set(oob or self._oob_none_label)

        # technologies (tech, condition) entries
        self._tech_selection = self.service.get_technology_entries(ref.tag)
        self._refresh_techs()

        self._refresh_owned_states(current_capital=hist.get("capital", ""))
        self._refresh_characters()

        # Deletion is only allowed for mod-defined countries.
        self._delete_btn.configure(state=("disabled" if ref.is_vanilla else "normal"))

        self._loading = False
        self._dirty = False

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
            written = self.service.set_color(self._selected.tag, rgb_int, ref=self._selected)
            self._g_status.configure(text=f"{self.t('settings.saved')} ({len(written)})",
                                     foreground=self.palette.text_muted)
        except Exception as exc:
            self._g_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)

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

    def _load_localisation(self) -> None:
        if not self._selected:
            return
        loc = self.service.get_localisation(self._selected.tag, self._loc_lang.get(),
                                            self._current_ideology())
        self._loc_name.set(loc.get("name", ""))
        self._loc_def.set(loc.get("definite", ""))
        self._loc_adj.set(loc.get("adjective", ""))

    def _render_existing_names(self) -> None:
        names = getattr(self, "_names", {})
        text = "  ".join(f"{lang}: {value}" for lang, value in names.items()) or self.t("common.none")
        self._loc_existing.configure(text=text)

    def _save_name(self) -> None:
        if not self._selected:
            return
        name = self._loc_name.get().strip()
        if not name:
            return
        try:
            self.service.set_localisation(
                self._selected.tag, self._loc_lang.get(), self._current_ideology(),
                name, self._loc_def.get().strip(), self._loc_adj.get().strip())
            self._names = self.service.get_names(self._selected.tag)
            self._render_existing_names()
            self._loc_status.configure(text=self.t("countries.name_saved"), foreground=self.palette.text_muted)
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
            self._states_tree.insert("", "end", values=(self.t("countries.no_owned_states"),))
        for s in owned:
            label = f"{s.id} · {s.name}"
            self._states_tree.insert("", "end", iid=str(s.id), values=(label,))
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

    def assign_state(self, state_id: int) -> None:
        """Called by the picker dialog after a state is chosen."""
        if not self._selected:
            return
        conflict = self.state_service.owner_conflict(state_id, self._selected.tag)
        keep_capital = self._capital_options.get(self._capital.get())
        try:
            self.state_service.set_owner(state_id, self._selected.tag)
        except Exception as exc:
            self._terr_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)
            return
        self._refresh_owned_states(current_capital=keep_capital)
        if conflict:
            self._terr_status.configure(
                text=self.t("countries.state_conflict", owner=conflict), foreground=self.palette.danger)
        else:
            self._terr_status.configure(
                text=self.t("countries.state_added", id=state_id), foreground=self.palette.text_muted)

    # --- politics actions ------------------------------------------------
    def _save_politics(self) -> None:
        if not self._selected:
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
            # OOB
            oob = self._pol_oob.get()
            self.service.set_oob(tag, name, "" if oob == self._oob_none_label else oob)
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
        self._dirty = True
        self._refresh_techs()

    def _remove_tech(self) -> None:
        for iid in self._tech_tree.selection():
            tech, _, cond = iid.partition("|")
            if (tech, cond) in self._tech_selection:
                self._tech_selection.remove((tech, cond))
        self._dirty = True
        self._refresh_techs()

    def _save_tech(self) -> None:
        if not self._selected:
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
        self._dirty = True
        self._refresh_techs()
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
        self._portrait_canvas.delete("all")
        self._portrait_photos = []
        sprites = model.sprites()
        # Show the first resolvable portrait large; list the rest as captions.
        drawn = False
        captions = []
        for cat, size, sprite in sprites:
            path = self.character_service.resolve_sprite(sprite)
            captions.append(f"{cat}/{size}: {sprite}" + ("" if path else "  (?)"))
            if not drawn and path:
                try:
                    img = Image.open(path); img.thumbnail((158, 208), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self._portrait_photos.append(photo)
                    self._portrait_canvas.create_image(80, 105, image=photo)
                    drawn = True
                except Exception:
                    pass
        if not drawn:
            self._portrait_canvas.create_text(80, 105, text="—", fill=self.palette.text_muted)
        self._portrait_caption.configure(text="\n".join(captions) if captions else self.t("common.none"))

    def _clear_portrait(self) -> None:
        if hasattr(self, "_portrait_canvas"):
            self._portrait_canvas.delete("all")
            self._char_name_lbl.configure(text="")
            self._portrait_caption.configure(text="")

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
        if not messagebox.askyesno(self.t("countries.delete"),
                                   self.t("countries.delete.confirm", tag=ref.tag)):
            return
        try:
            touched = self.service.delete_country(ref)
        except Exception as exc:
            self._g_status.configure(text=f"{self.t('common.error')}: {exc}", foreground=self.palette.danger)
            return
        self._selected = None
        self.state_service.list_states(refresh=True)
        self._reload()
        self._g_title.configure(text=self.t("editor.select_module"))
        self._g_status.configure(text=self.t("countries.deleted", tag=ref.tag, count=len(touched)),
                                 foreground=self.palette.text_muted)
        self._delete_btn.configure(state="disabled")

    # --- new country -----------------------------------------------------
    def _new_country(self) -> None:
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
                     values: list[str], width: int = 18) -> None:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", padx=16, pady=4)
        ttk.Combobox(parent, textvariable=var, values=values, state="readonly",
                     width=width).grid(row=row, column=1, sticky="w", padx=8, pady=4)

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
