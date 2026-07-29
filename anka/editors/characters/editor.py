"""Characters (Личности) editor — the single place to *create* characters.

Leaders, advisors, generals and admirals are all characters; here you build one with any
combination of those roles, portraits (civilian/army/navy) and a name, then save it to
``common/characters/<TAG>.txt``. Country editors only *recruit* existing characters; this
editor authors them.
"""
from __future__ import annotations

import re
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ...services.character_service import (
    ADVISOR_SLOTS,
    GENERAL_ROLES,
    PORTRAIT_SLOTS,
)
from ...services.ideology_service import IdeologyService
from ...services.trait_service import TraitService
from ...ui.widgets import ImageDropZone, ScrollableFrame
from ..base import EditorModule, EditorRegistry
from .dialogs import ItemPickerDialog, LeaderScopeDialog

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,}$")


# Sub-ideologies grouped under the four main ideologies, for sorting leaders.
_IDEOLOGY_GROUPS = {
    "fascism":    ("nazism", "fascism", "fascism_ideology", "falangism", "rexism",
                   "gen_nazism", "gen_fascism"),
    "communism":  ("marxism", "leninism", "stalinism", "marxism_leninism",
                   "anarchist_communism", "trotskyism", "gen_communism"),
    "democratic": ("conservatism", "liberalism", "socialism", "social_democracy",
                   "market_liberalism", "gen_democratic", "democratic"),
    "neutrality": ("despotism", "oligarchism", "anarchism", "moderatism",
                   "centrism", "gen_neutrality", "neutrality"),
}
_GROUP_ORDER = ("fascism", "communism", "democratic", "neutrality", "")


def _ideology_group(ideology: str) -> str:
    """Main ideology an ideology belongs to ("" when unknown / not a leader)."""
    low = (ideology or "").lower()
    if not low:
        return ""
    for group, subs in _IDEOLOGY_GROUPS.items():
        if low in subs:
            return group
    for group, subs in _IDEOLOGY_GROUPS.items():
        if any(sub in low for sub in subs):
            return group
    return ""


_ROLE_FILTER_ALL = "All roles"
_ROLE_FILTERS = (
    ("Country leader", "country_leader"),
    ("Advisor", "advisor"),
    ("Field marshal", "field_marshal"),
    ("Corps commander", "corps_commander"),
    ("Admiral", "navy_leader"),
)


@EditorRegistry.register
class CharactersEditor(EditorModule):
    id = "characters"
    name_key = "editors.characters.name"
    desc_key = "editors.characters.desc"
    order = 50

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = context.characters  # shared across editor modules
        self.ideology_service = IdeologyService(context)
        self.trait_service = TraitService(context)
        self._models = []
        self._selected = None
        self._focus_leaders: list[dict] | None = None
        self._focus_rows: dict[str, dict] = {}
        self._editing_existing = False
        self._portrait_paths: dict[tuple[str, str], Path] = {}   # (category, size)
        self._loading = False
        self._dirty = False

    # --- build -----------------------------------------------------------
    def build(self, parent) -> ttk.Widget:
        root = ttk.Frame(parent, style="TFrame")
        root.columnconfigure(0, weight=0, minsize=300)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self._build_list(root)
        self._build_form(root)
        self._search.trace_add("write", lambda *_: self._refresh_list())
        self._wire_dirty()
        self._reload()
        return root

    def _wire_dirty(self) -> None:
        form_vars = [
            self._v_name, self._v_tag, self._v_leader_ideo, self._v_leader_traits,
            self._v_adv_slot, self._v_adv_token, self._v_adv_cost, self._v_adv_traits,
            self._v_gen_role, self._v_gen_traits, self._v_adm_traits,
            self._role_leader, self._role_advisor, self._role_general, self._role_admiral,
            *self._gen_skills.values(), *self._adm_skills.values(),
        ]
        for var in form_vars:
            var.trace_add("write", lambda *_: self._mark_dirty())

    def _mark_dirty(self) -> None:
        if not self._loading:
            self._dirty = True

    def on_leave(self) -> None:
        """Persist the current character form if it was edited and has a valid id."""
        if self._dirty and _ID_RE.match(self._v_id.get().strip()):
            self._save()

    def _build_list(self, root) -> None:
        left = ttk.Frame(root, style="Card.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(3, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text=self.t("characters.list"), style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._search = tk.StringVar()
        ttk.Entry(left, textvariable=self._search).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        opts = ttk.Frame(left, style="Card.TFrame")
        opts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        opts.columnconfigure(1, weight=1)
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text=self.t("characters.vanilla"), variable=self._vanilla,
                        command=self._reload).grid(row=0, column=0, sticky="w")
        self._loc_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text=self.t("characters.loc_only"),
                        variable=self._loc_only).grid(row=1, column=0,
                                                      columnspan=3, sticky="w",
                                                      pady=(4, 0))
        self._show_focus = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text=self.t("characters.focus_made"),
                        variable=self._show_focus,
                        command=self._refresh_list).grid(row=0, column=1, sticky="w",
                                                         padx=(12, 0))
        self._role_filter = tk.StringVar(value=_ROLE_FILTER_ALL)
        role_combo = ttk.Combobox(opts, textvariable=self._role_filter, state="readonly",
                                  width=16,
                                  values=[_ROLE_FILTER_ALL] + [lbl for lbl, _ in _ROLE_FILTERS])
        role_combo.grid(row=0, column=2, sticky="e")
        role_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_list())

        self._tree = ttk.Treeview(left, columns=("roles", "ideology"),
                                  show="tree headings", selectmode="browse")
        self._tree.heading("#0", text=self.t("characters.id"))
        self._tree.heading("roles", text=self.t("characters.roles"))
        self._tree.column("roles", width=100, anchor="w")
        self._tree.heading("ideology", text=self.t("characters.ideology"))
        self._tree.column("ideology", width=110, anchor="w")
        self._tree.grid(row=3, column=0, sticky="nsew")
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        sb = ttk.Scrollbar(left, orient="vertical", command=self._tree.yview)
        sb.grid(row=3, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)

        btns = ttk.Frame(left, style="Card.TFrame")
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="➕ " + self.t("characters.new"), style="Accent.TButton",
                   command=self._new).pack(side="left")
        ttk.Button(btns, text="🗑 " + self.t("characters.delete"), command=self._delete).pack(side="left", padx=6)
        ttk.Button(btns, text="👑 " + self.t("characters.takeover"),
                   command=self._takeover_leaders).pack(side="left", padx=6)
        ttk.Button(btns, text="↺ " + self.t("characters.restore_portraits"),
                   command=self._restore_vanilla_portraits).pack(side="left", padx=6)

    def _build_form(self, root) -> None:
        outer = ttk.Frame(root, style="Card.TFrame")
        outer.grid(row=0, column=1, sticky="nsew")
        scroll = ScrollableFrame(outer, bg=self.palette.surface)
        scroll.pack(fill="both", expand=True)
        f = scroll.body
        f.configure(style="Card.TFrame")
        f.columnconfigure(1, weight=1)
        r = 0

        self._hint = ttk.Label(f, text=self.t("characters.select_hint"), style="CardMuted.TLabel")
        self._hint.grid(row=r, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8)); r += 1

        self._v_id = tk.StringVar()
        self._id_entry = self._row_entry(f, r, self.t("characters.id"), self._v_id); r += 1
        self._v_name = tk.StringVar()
        self._row_entry(f, r, self.t("characters.name"), self._v_name); r += 1

        # Localised display name: language picker + entry, auto-saved as you type
        # (debounced) for characters that already exist on disk.
        ttk.Label(f, text=self.t("characters.name_loc"), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", padx=16, pady=3)
        loc_row = ttk.Frame(f, style="Card.TFrame")
        loc_row.grid(row=r, column=1, sticky="w", padx=8, pady=3); r += 1
        from ...config.constants import HOI4_LANGUAGES
        self._v_loc_lang = tk.StringVar(
            value={"ru": "russian"}.get(self.services.settings.current.language, "english"))
        lang_combo = ttk.Combobox(loc_row, textvariable=self._v_loc_lang,
                                  values=list(HOI4_LANGUAGES), state="readonly", width=12)
        lang_combo.pack(side="left", padx=(0, 6))
        lang_combo.bind("<<ComboboxSelected>>", lambda e: self._load_loc_name())
        self._v_loc_name = tk.StringVar()
        self._loc_entry = ttk.Entry(loc_row, textvariable=self._v_loc_name, width=24)
        self._loc_entry.pack(side="left")
        self._loc_loaded = ""
        self._loc_key: str | None = None
        self._loc_job: str | None = None
        self._v_loc_name.trace_add("write", lambda *_: self._schedule_loc_save())
        self._loc_entry.bind("<FocusOut>", lambda e: self._save_loc_name())

        self._v_tag = tk.StringVar()
        self._row_entry(f, r, self.t("characters.tag"), self._v_tag, width=8); r += 1

        # Portraits
        ttk.Label(f, text=self.t("characters.portraits"), style="Heading.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 4)); r += 1
        pf = ttk.Frame(f, style="Card.TFrame")
        pf.grid(row=r, column=0, columnspan=2, sticky="w", padx=16); r += 1
        self._zones: dict[tuple[str, str], ImageDropZone] = {}
        for i, (cat, size) in enumerate(PORTRAIT_SLOTS):
            col = ttk.Frame(pf, style="Card.TFrame")
            col.grid(row=0, column=i, padx=(0, 12), sticky="s")
            loc_key = f"characters.portrait.{cat}" + ("_small" if size == "small" else "")
            ttk.Label(col, text=self.t(loc_key), style="CardMuted.TLabel").pack(anchor="w")
            preview = (65, 67) if size == "small" else (94, 126)
            zone = ImageDropZone(col, on_image=lambda p, k=(cat, size): self._on_portrait(k, p),
                                 prompt="+", preview_size=preview, palette=self.palette)
            zone.pack()
            self._zones[(cat, size)] = zone

        # Roles
        ttk.Label(f, text=self.t("characters.roles"), style="Heading.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 4)); r += 1

        # Leader
        self._role_leader = tk.BooleanVar()
        self._check(f, r, self.t("characters.role.leader"), self._role_leader); r += 1
        self._v_leader_ideo = tk.StringVar()
        self._ideo_map = {f"{g} · {s}": s for g, s in self.ideology_service.list_types()}
        self._leader_ideo_combo = self._row_combo(
            f, r, self.t("characters.leader.ideology"), self._v_leader_ideo,
            list(self._ideo_map)); r += 1
        self._v_leader_traits = tk.StringVar()
        self._trait_row(f, r, self._v_leader_traits, "leader"); r += 1

        # Advisor
        self._role_advisor = tk.BooleanVar()
        self._check(f, r, self.t("characters.role.advisor"), self._role_advisor); r += 1
        self._v_adv_slot = tk.StringVar(value=ADVISOR_SLOTS[0])
        self._row_combo(f, r, self.t("characters.advisor.slot"), self._v_adv_slot, list(ADVISOR_SLOTS)); r += 1
        self._v_adv_token = tk.StringVar()
        self._row_entry(f, r, self.t("characters.advisor.idea_token"), self._v_adv_token); r += 1
        self._v_adv_cost = tk.StringVar()
        self._row_entry(f, r, self.t("characters.advisor.cost"), self._v_adv_cost, width=8); r += 1
        self._v_adv_traits = tk.StringVar()
        self._trait_row(f, r, self._v_adv_traits, "advisor"); r += 1

        # General
        self._role_general = tk.BooleanVar()
        self._check(f, r, self.t("characters.role.general"), self._role_general); r += 1
        self._v_gen_role = tk.StringVar(value=GENERAL_ROLES[1])
        self._row_combo(f, r, self.t("characters.general.role"), self._v_gen_role, list(GENERAL_ROLES)); r += 1
        self._gen_skills = self._skill_row(f, r, ("skill", "attack", "defense", "planning", "logistics")); r += 1
        self._v_gen_traits = tk.StringVar()
        self._trait_row(f, r, self._v_gen_traits, "unit"); r += 1

        # Admiral
        self._role_admiral = tk.BooleanVar()
        self._check(f, r, self.t("characters.role.admiral"), self._role_admiral); r += 1
        self._adm_skills = self._skill_row(f, r, ("skill", "attack", "defense", "maneuvering", "coordination")); r += 1
        self._v_adm_traits = tk.StringVar()
        self._trait_row(f, r, self._v_adm_traits, "unit"); r += 1

        ttk.Button(f, text=self.t("common.save"), style="Accent.TButton",
                   command=self._save).grid(row=r, column=0, sticky="w", padx=16, pady=12); r += 1
        self._status = ttk.Label(f, text="", style="CardMuted.TLabel")
        self._status.grid(row=r, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

    # --- list ------------------------------------------------------------
    def _reload(self) -> None:
        # Shared, incrementally-maintained cache — no rescan needed for in-app edits.
        all_models = self.service.all_characters()
        if self._vanilla.get():
            self._models = sorted(all_models, key=lambda m: m.char_id)
        else:
            self._models = sorted([m for m in all_models if m.in_mod], key=lambda m: m.char_id)
        self._refresh_list()

    def _selected_role_key(self) -> str:
        label = self._role_filter.get()
        return next((key for lbl, key in _ROLE_FILTERS if lbl == label), "")

    def _refresh_list(self) -> None:
        q = self._search.get().strip().lower()
        role = self._selected_role_key()
        self._tree.delete(*self._tree.get_children())
        if not self._models:
            self._tree.insert("", "end", text=self.t("characters.none"), values=("", ""))
            return
        rows = []
        for m in self._models:
            if q and q not in m.char_id.lower() and q not in m.name.lower():
                continue
            if role and role not in m.roles:
                continue
            rows.append(m)
        # Country leaders read much better grouped by main ideology.
        show_groups = (role == "country_leader") or any(m.ideology for m in rows)
        if show_groups:
            rows.sort(key=lambda m: (_GROUP_ORDER.index(_ideology_group(m.ideology))
                                     if _ideology_group(m.ideology) in _GROUP_ORDER
                                     else len(_GROUP_ORDER),
                                     m.ideology.lower(), m.char_id.lower()))
            current = None
            for m in rows:
                grp = _ideology_group(m.ideology)
                if grp != current:
                    current = grp
                    label = self.t(f"characters.group.{grp}") if grp else self.t("characters.group.other")
                    self._tree.insert("", "end", iid=f"__grp_{grp or 'other'}",
                                      text=f"— {label} —", values=("", ""), open=True)
                self._tree.insert("", "end", iid=m.char_id, text=m.char_id,
                                  values=(m.roles_label, m.ideology))
        else:
            for m in rows:
                self._tree.insert("", "end", iid=m.char_id, text=m.char_id,
                                  values=(m.roles_label, m.ideology))
        if self._show_focus.get():
            self._append_focus_leaders(q)

    def _on_select(self, _e=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        if sel[0].startswith("__focus_"):
            # Created by a focus/event effect — there is no character to edit.
            entry = getattr(self, "_focus_rows", {}).get(sel[0])
            if entry:
                self._status.configure(
                    text=self.t("characters.focus_hint", name=entry["name"],
                                sprite=entry["sprite"], file=entry["file"]),
                    foreground=self.palette.text_muted)
            return
        if sel[0].startswith("__grp_"):
            return
        model = self.service.get(sel[0])
        if model is None:
            return
        self._selected = model
        self._editing_existing = True
        self._prefill(model)

    # --- localised name ----------------------------------------------------
    def _load_loc_name(self) -> None:
        """Fill the localized-name entry for the selected character + language."""
        if self._loc_job is not None:
            self._loc_entry.after_cancel(self._loc_job)
            self._loc_job = None
        was_loading = self._loading
        self._loading = True
        try:
            if self._loc_key is None:
                self._v_loc_name.set("")
                self._loc_loaded = ""
            else:
                value = self.service.get_name_loc(self._loc_key, self._v_loc_lang.get())
                self._v_loc_name.set(value)
                self._loc_loaded = value
        finally:
            self._loading = was_loading

    def _schedule_loc_save(self) -> None:
        """Debounced instant save: commits ~1s after the user stops typing."""
        if self._loading or self._loc_key is None:
            return
        if self._loc_job is not None:
            self._loc_entry.after_cancel(self._loc_job)
        self._loc_job = self._loc_entry.after(900, self._save_loc_name)

    def _save_loc_name(self) -> None:
        if self._loc_job is not None:
            self._loc_entry.after_cancel(self._loc_job)
            self._loc_job = None
        if self._loading or self._loc_key is None:
            return
        value = self._v_loc_name.get().strip()
        if not value or value == self._loc_loaded:
            return
        self.service.set_name_loc(self._loc_key, self._v_loc_lang.get(), value)
        self._loc_loaded = value
        if self._v_loc_lang.get() == "english":
            self._v_name.set(value)
        self._status.configure(text=self.t("characters.name_loc_saved",
                                           lang=self._v_loc_lang.get()),
                               foreground=self.palette.text_muted)

    # --- form population -------------------------------------------------
    def _new(self) -> None:
        self._loading = True
        self._selected = None
        self._editing_existing = False
        self._loc_key = None
        self._clear_form()
        self._load_loc_name()
        self._id_entry.configure(state="normal")
        self._hint.configure(text=self.t("characters.new"))
        self._loading = False
        self._dirty = False

    def _clear_form(self) -> None:
        for v in (self._v_id, self._v_name, self._v_tag, self._v_leader_ideo, self._v_leader_traits,
                  self._v_adv_token, self._v_adv_cost, self._v_adv_traits, self._v_gen_traits, self._v_adm_traits):
            v.set("")
        for b in (self._role_leader, self._role_advisor, self._role_general, self._role_admiral):
            b.set(False)
        self._portrait_paths.clear()
        for z in self._zones.values():
            z.clear()
        for d in (self._gen_skills, self._adm_skills):
            for var in d.values():
                var.set("1")
        self._unknown_ideo = None      # original ideology of an "Unknown" pick
        self._status.configure(text="")

    def _prefill(self, model) -> None:
        self._loading = True
        self._refresh_ideology_types()
        self._clear_form()
        self._id_entry.configure(state="disabled")
        self._hint.configure(text=f"{model.char_id}")
        self._v_id.set(model.char_id)
        self._v_name.set(model.name)
        self._v_tag.set(model.tag)
        self._loc_key = model.name_key
        self._load_loc_name()
        # portraits preview — one zone per (category, size) slot
        for (cat, size), zone in self._zones.items():
            sizes = model.portraits.get(cat, {})
            sprite = sizes.get(size)
            if sprite is None and size == "large":
                sprite = next((s for k, s in sizes.items() if k != "small"), None)
            path = self.service.resolve_sprite(sprite) if sprite else None
            if path:
                zone.show_image(path)
        raw = model.raw
        if raw is None:
            return
        leader = raw.get_block("country_leader")
        if leader is not None:
            self._role_leader.set(True)
            raw_ideo = leader.get_scalar("ideology", "")
            if raw_ideo and raw_ideo not in self._ideo_map.values():
                # The (sub-)ideology no longer exists — show it as unknown but
                # keep the original value so saving doesn't silently change it.
                self._unknown_ideo = raw_ideo
                self._v_leader_ideo.set(
                    f"{self.t('characters.unknown_ideology')} ({raw_ideo})")
            else:
                self._select_combo(self._v_leader_ideo, self._ideo_map, raw_ideo)
            self._v_leader_traits.set(self._traits(leader))
        adv = raw.get_block("advisor")
        if adv is not None:
            self._role_advisor.set(True)
            self._v_adv_slot.set(adv.get_scalar("slot", ADVISOR_SLOTS[0]))
            self._v_adv_token.set(adv.get_scalar("idea_token", ""))
            self._v_adv_cost.set(adv.get_scalar("cost", ""))
            self._v_adv_traits.set(self._traits(adv))
        for role in GENERAL_ROLES:
            gen = raw.get_block(role)
            if gen is not None:
                self._role_general.set(True)
                self._v_gen_role.set(role)
                self._fill_skills(self._gen_skills, gen, ("skill", "attack", "defense", "planning", "logistics"))
                self._v_gen_traits.set(self._traits(gen))
                break
        adm = raw.get_block("navy_leader")
        if adm is not None:
            self._role_admiral.set(True)
            self._fill_skills(self._adm_skills, adm, ("skill", "attack", "defense", "maneuvering", "coordination"))
            self._v_adm_traits.set(self._traits(adm))
        self._loading = False
        self._dirty = False

    # --- save / delete ---------------------------------------------------
    def _save_checksum_safe(self) -> None:
        """Save without touching common/ — keeps the checksum, keeps achievements.

        Portraits are safe: the new texture goes to gfx/ and redefines the sprite
        the base game already references, both of which the checksum ignores.
        The display name is safe too, as long as the base game stores a
        localisation key; a literal name can only be changed in
        common/characters, so that one edit is reported instead of applied.
        """
        model = self._selected
        done: list[str] = []

        # --- portraits: redefine the existing sprite ------------------------
        if self._portrait_paths and model is not None:
            replaced, missing = 0, []
            for (category, size), path in list(self._portrait_paths.items()):
                sprite = (model.portraits.get(category) or {}).get(size, "")
                if not sprite:
                    missing.append(f"{category}/{size}")
                    continue
                # Small pictures carry baked-in artwork (photo card + note), so
                # the current image is handed over as the frame to keep it.
                frame = None
                if size == "small":
                    # Deliberately the BASE GAME's artwork: resolving normally
                    # would return this mod's previous override, so repeated
                    # edits would composite into each other.
                    try:
                        frame = self.context.base_sprites.resolve(sprite)
                    except Exception:
                        frame = None
                try:
                    self.context.icons.override_portrait(
                        path, sprite, (model.tag or "").upper() or "GEN",
                        model.char_id, size, frame_from=frame)
                    replaced += 1
                except Exception as exc:
                    return self._fail_msg(str(exc))
            if replaced:
                self.context.sprites.invalidate()
                self._portrait_paths.clear()
                done.append(self.t("characters.loc_only.portraits",
                                   count=replaced))
            if missing:
                done.append(self.t("characters.loc_only.no_sprite",
                                   slots=", ".join(missing)))

        # --- display name ---------------------------------------------------
        name_changed = (self._v_loc_name.get().strip() != self._loc_loaded)
        if name_changed:
            if model is not None and model.name_is_literal:
                done.append(self.t("characters.loc_only.literal"))
            else:
                self._save_loc_name()
                done.append(self.t("characters.loc_only.name"))

        if not done:
            done.append(self.t("characters.loc_only.nothing"))
        self._dirty = False
        self._reload()
        self._status.configure(text=" · ".join(done),
                               foreground=self.palette.text_muted)

    def _save(self) -> None:
        char_id = self._v_id.get().strip()
        if not _ID_RE.match(char_id):
            return self._fail("characters.invalid_id")
        if self._loc_only.get() and self._editing_existing:
            return self._save_checksum_safe()
        if not self._editing_existing and self.service.get(char_id) is not None:
            return self._fail("characters.exists")
        tag = (self._v_tag.get().strip() or char_id.split("_")[0]).upper()
        name = self._v_name.get().strip() or char_id

        kwargs = {"portraits": dict(self._portrait_paths) or None}
        kwargs["country_leader"] = (
            {"ideology": self._ideo_map.get(self._v_leader_ideo.get(),
                                            self._unknown_ideo or "despotism"),
             "traits": self._v_leader_traits.get().split()} if self._role_leader.get() else None)
        kwargs["advisor"] = (
            {"slot": self._v_adv_slot.get(), "idea_token": self._v_adv_token.get().strip() or char_id,
             "cost": self._v_adv_cost.get().strip(), "traits": self._v_adv_traits.get().split()}
            if self._role_advisor.get() else None)
        kwargs["general"] = (
            {"role": self._v_gen_role.get(), "traits": self._v_gen_traits.get().split(),
             **self._expand_skills(self._gen_skills)}
            if self._role_general.get() else None)
        kwargs["admiral"] = (
            {"traits": self._v_adm_traits.get().split(),
             **self._expand_skills(self._adm_skills)}
            if self._role_admiral.get() else None)

        try:
            self.service.create_or_update(tag, char_id, name, **kwargs)
        except Exception as exc:
            return self._fail_msg(str(exc))
        self._portrait_paths.clear()
        self._editing_existing = True
        self._dirty = False
        self._reload()
        if self._tree.exists(char_id):
            self._tree.selection_set(char_id)
        self._status.configure(text=self.t("characters.saved", id=char_id), foreground=self.palette.text_muted)

    def _append_focus_leaders(self, query: str) -> None:
        """Read-only rows for leaders that only exist as a focus/event effect."""
        entries = self._scan_focus_leaders()
        tag = (self._v_tag.get() or "").strip().upper()
        shown = [e for e in entries
                 if (not tag or e["tag"] == tag)
                 and (not query or query in e["name"].lower()
                      or query in e["sprite"].lower())]
        if not shown:
            return
        self._tree.insert("", "end", iid="__grp_focus",
                          text=f"— {self.t('characters.focus_made')} —",
                          values=("", ""), open=True)
        for i, e in enumerate(sorted(shown, key=lambda x: (x["tag"], x["name"]))):
            label = f"{e['name']}  ({e['tag']})" if e["tag"] else e["name"]
            self._tree.insert("", "end", iid=f"__focus_{i}", text=label,
                              values=(self.t("characters.focus_role"), e["ideology"]))
        self._focus_rows = {f"__focus_{i}": e for i, e
                            in enumerate(sorted(shown, key=lambda x: (x["tag"], x["name"])))}

    def _scan_focus_leaders(self) -> list[dict]:
        """All leaders that focuses / events create, across every content root.

        These are effects, not characters, so they never appear in
        common/characters. Listed read-only so it is at least visible that they
        exist — editing them means editing the focus/event script itself.
        Scanned once per editor session (the file sweep is not cheap).
        """
        if self._focus_leaders is not None:
            return self._focus_leaders
        roots = [self.context.game_path]
        for sub in ("dlc", "integrated_dlc"):
            parent = self.context.game_path / sub
            if parent.is_dir():
                roots.extend(p for p in sorted(parent.iterdir()) if p.is_dir())
        roots.append(self.context.mod.path)

        block_re = re.compile(r"create_country_leader\s*=\s*\{(.*?)\}", re.S)
        pic_re = re.compile(r"picture\s*=\s*\"?(GFX_[A-Za-z0-9_]+)")
        name_re = re.compile(r"name\s*=\s*\"?([^\"\n}#]+)")
        ideo_re = re.compile(r"ideology\s*=\s*([A-Za-z0-9_]+)")
        tag_re = re.compile(r"GFX_portraits?_([A-Za-z]{3})_", re.I)

        out: list[dict] = []
        seen: set[str] = set()
        for root in roots:
            for folder in ("common/national_focus", "events"):
                d = root / folder
                if not d.is_dir():
                    continue
                for file in d.rglob("*.txt"):
                    try:
                        text = file.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    if "create_country_leader" not in text:
                        continue
                    for body in block_re.findall(text):
                        pic = pic_re.search(body)
                        if not pic or pic.group(1) in seen:
                            continue
                        sprite = pic.group(1)
                        seen.add(sprite)
                        tag_m = tag_re.search(sprite)
                        nm = name_re.search(body)
                        ideo = ideo_re.search(body)
                        out.append({
                            "name": (nm.group(1).strip() if nm else "?"),
                            "sprite": sprite,
                            "ideology": (ideo.group(1) if ideo else ""),
                            "tag": (tag_m.group(1).upper() if tag_m else ""),
                            "file": file.name,
                        })
        self._focus_leaders = out
        return out

    def _focus_created_leader_sprites(self, tag: str) -> list[str]:
        """Portrait sprites of leaders that focuses / events create at runtime.

        Monarchist restorations and similar paths do not define characters in
        common/characters — they call ``create_country_leader`` with a literal
        name and a ``picture``. Those leaders never appear in the character list,
        so a takeover has to pick their sprites up from the scripts instead.
        Base game, its DLC folders and the mod are all scanned; the mod wins.
        """
        roots = [self.context.game_path]
        for sub in ("dlc", "integrated_dlc"):
            parent = self.context.game_path / sub
            if parent.is_dir():
                roots.extend(p for p in sorted(parent.iterdir()) if p.is_dir())
        roots.append(self.context.mod.path)

        needle = f"_{tag}_".lower()
        found: list[str] = []
        seen: set[str] = set()
        block_re = re.compile(r"create_country_leader\s*=\s*\{(.*?)\}", re.S)
        pic_re = re.compile(r"picture\s*=\s*\"?(GFX_[A-Za-z0-9_]+)")
        for root in roots:
            for folder in ("common/national_focus", "events"):
                d = root / folder
                if not d.is_dir():
                    continue
                for file in d.rglob("*.txt"):
                    try:
                        text = file.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    if "create_country_leader" not in text:
                        continue
                    for body in block_re.findall(text):
                        m = pic_re.search(body)
                        if not m:
                            continue
                        sprite = m.group(1)
                        low = sprite.lower()
                        # Sprite names carry the tag, e.g. GFX_portrait_fra_jean_…
                        if needle in low and sprite not in seen:
                            seen.add(sprite)
                            found.append(sprite)
        return found

    def _restore_vanilla_portraits(self) -> None:
        """Drop this character's portrait overrides so the base game's art returns."""
        model = self._selected
        if model is None:
            return self._fail("characters.restore.pick_first")
        sprites = [sp for sizes in (model.portraits or {}).values()
                   for sp in sizes.values() if sp]
        if not sprites:
            return self._fail("characters.restore.none")
        if not messagebox.askyesno("ANKA", self.t("characters.restore.confirm",
                                                  name=model.name or model.char_id)):
            return
        removed, files = 0, []
        for sprite in sprites:
            try:
                n, touched = self.context.icons.restore_vanilla_sprite(sprite)
            except Exception as exc:
                return self._fail_msg(str(exc))
            removed += n
            files.extend(touched)
        if not removed:
            self._status.configure(text=self.t("characters.restore.nothing"),
                                   foreground=self.palette.text_muted)
            return
        self.context.sprites.invalidate()
        self._portrait_paths.clear()
        self._reload()
        if self._tree.exists(model.char_id):
            self._tree.selection_set(model.char_id)
        self._status.configure(text=self.t("characters.restore.done",
                                           count=removed,
                                           files=len(set(files))),
                               foreground=self.palette.text_muted)

    # ------------------------------------------------------- leader takeover
    def _takeover_leaders(self) -> None:
        """Make ONE character the face and name of every country leader of a tag.

        Whatever focus / civil war / ideology switch happens in game, the leader
        shown is always this person. Two things are written:
          * interface/zzz_anka_leader_takeover.gfx — overrides every leader's
            portrait sprite (the zzz_ prefix makes it win over DLC sprites);
          * the display name of every leader key via the localisation override.
        Focus-created leaders carry a literal name in their effect, so for those
        only the portrait can be replaced — they are reported afterwards.
        """
        src_model = self._selected
        if src_model is None:
            self._fail("characters.takeover.pick_first")
            return
        tag = (self._v_tag.get() or src_model.tag or "").strip().upper()
        if not tag:
            self._fail("characters.takeover.no_tag")
            return

        # Portrait of the chosen character (any large one will do).
        sprite = next((sp for cat in ("civilian", "army", "navy")
                       for sz, sp in (src_model.portraits.get(cat) or {}).items()
                       if sz == "large"), "")
        source_file = self.service.resolve_sprite(sprite) if sprite else None
        if source_file is None or not Path(source_file).is_file():
            self._fail("characters.takeover.no_portrait")
            return

        all_leaders = [m for m in self.service.all_characters()
                       if "country_leader" in m.roles and (m.tag or "").upper() == tag
                       and m.char_id != src_model.char_id]
        if not all_leaders:
            self._fail("characters.takeover.none")
            return

        # Scope: every ideology, or only one of the four main ideologies.
        present = [g for g in _GROUP_ORDER
                   if g and any(_ideology_group(m.ideology) == g for m in all_leaders)]
        choices = [(self.t("characters.takeover.scope_all"), "")]
        choices += [(self.t(f"characters.group.{g}"), g) for g in present]
        scope = LeaderScopeDialog(self._root_widget(), self, choices,
                                  default=_ideology_group(src_model.ideology)).result
        if scope is None:
            return
        leaders = ([m for m in all_leaders if _ideology_group(m.ideology) == scope]
                   if scope else all_leaders)
        if not leaders:
            self._fail("characters.takeover.none")
            return

        display = (self._v_loc_name.get() or src_model.name or "").strip()
        scope_label = (self.t("characters.takeover.scope_all") if not scope
                       else self.t(f"characters.group.{scope}"))
        if not messagebox.askyesno("ANKA", self.t("characters.takeover.confirm",
                                                  count=len(leaders), tag=tag,
                                                  scope=scope_label,
                                                  name=display or src_model.char_id)):
            return

        # 1) copy the portrait into the mod so the sprite path is mod-local
        rel = f"gfx/leaders/{tag}/anka_leader_takeover.dds"
        target = self.context.mod.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)

        # 2) sprite overrides for every leader portrait of this tag
        lines = ["spriteTypes = {"]
        written: set[str] = set()
        for m in leaders:
            for cat, sizes in (m.portraits or {}).items():
                for size, sp in sizes.items():
                    if sp and sp not in written:
                        written.add(sp)
                        lines.append(f'\tspriteType = {{ name = "{sp}" '
                                     f'texturefile = "{rel}" }}')
        # Leaders that only exist as a focus/event effect (monarchist paths and
        # the like) are covered too — otherwise those paths would still show the
        # original leader.
        extra = 0
        if not scope:
            for sp in self._focus_created_leader_sprites(tag):
                if sp in written:
                    continue
                written.add(sp)
                extra += 1
                lines.append(f'\tspriteType = {{ name = "{sp}" '
                             f'texturefile = "{rel}" }}')
                lines.append(f'\tspriteType = {{ name = "{sp}_small" '
                             f'texturefile = "{rel}" }}')
        lines.append("}")
        gfx = self.context.mod.path / "interface" / "zzz_anka_leader_takeover.gfx"
        gfx.parent.mkdir(parents=True, exist_ok=True)
        gfx.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # New sprite definitions on disk: refresh the resolver so the editor
        # previews the new portraits instead of the cached originals.
        self.context.sprites.invalidate()

        # 3) rename every leader
        renamed = 0
        if display:
            language = self._v_loc_lang.get() or "english"
            for m in leaders:
                if m.name_key:
                    self.service.set_name_loc(m.name_key, language, display)
                    renamed += 1

        self._reload()
        messagebox.showinfo("ANKA", self.t("characters.takeover.done",
                                           count=len(leaders), renamed=renamed,
                                           extra=extra))

    def _delete(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        model = self.service.get(sel[0])
        if model is None:
            return
        if not model.in_mod:
            return self._fail("characters.delete_vanilla")
        if not messagebox.askyesno(self.t("characters.delete"),
                                   self.t("countries.delete.confirm", tag=model.char_id)):
            return
        self.service.delete(model.char_id)
        self._selected = None
        self._reload()
        self._clear_form()
        self._status.configure(text=self.t("characters.deleted"), foreground=self.palette.text_muted)

    # --- helpers ---------------------------------------------------------
    def _on_portrait(self, slot: tuple[str, str], path: Path) -> None:
        self._portrait_paths[slot] = path
        self._dirty = True

    @staticmethod
    def _expand_skills(skill_vars) -> dict:
        """UI short keys (attack) -> script keys (attack_skill); 'skill' stays as is."""
        out = {}
        for short, var in skill_vars.items():
            full = "skill" if short == "skill" else f"{short}_skill"
            out[full] = var.get() or "1"
        return out

    def _traits(self, block) -> str:
        tb = block.get_block("traits")
        return " ".join(tb.array_values()) if tb else ""

    def _fill_skills(self, skill_vars, block, keys) -> None:
        skill_keys = ("skill", "attack_skill", "defense_skill",
                      keys[3] + "_skill", keys[4] + "_skill")
        for short, full in zip(keys, skill_keys):
            skill_vars[short].set(block.get_scalar(full, "1"))

    def _select_combo(self, var, mapping, value) -> None:
        for label, sub in mapping.items():
            if sub == value:
                var.set(label)
                return

    def _fail(self, key: str) -> None:
        self._status.configure(text=self.t(key), foreground=self.palette.danger)

    def _fail_msg(self, msg: str) -> None:
        self._status.configure(text=f"{self.t('common.error')}: {msg}", foreground=self.palette.danger)

    def _trait_row(self, parent, row, var, kind):
        """A traits entry plus a '+' button opening the dynamic trait picker."""
        ttk.Label(parent, text=self.t("characters.traits"), style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", padx=16, pady=3)
        cell = ttk.Frame(parent, style="Card.TFrame")
        cell.grid(row=row, column=1, sticky="w", padx=8, pady=3)
        ttk.Entry(cell, textvariable=var, width=30).pack(side="left")
        ttk.Button(cell, text="+", width=3,
                   command=lambda: self._pick_traits(var, kind)).pack(side="left", padx=4)

    def _trait_list(self, kind: str) -> list[str]:
        if kind == "unit":
            return self.trait_service.unit_leader_traits()
        if kind == "advisor":
            return self.trait_service.advisor_traits()
        return self.trait_service.country_leader_traits()

    def _pick_traits(self, var, kind) -> None:
        current = set(var.get().split())

        def add(selected):
            combined = list(dict.fromkeys(var.get().split() + selected))
            var.set(" ".join(combined))

        ItemPickerDialog(self._root_widget(), self, self.t("characters.traits"),
                         self._trait_list(kind), add, exclude=current)

    def _root_widget(self):
        return self._tree.winfo_toplevel()

    def _row_entry(self, parent, row, label, var, width=24):
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", padx=16, pady=3)
        e = ttk.Entry(parent, textvariable=var, width=width)
        e.grid(row=row, column=1, sticky="w", padx=8, pady=3)
        return e

    def _row_combo(self, parent, row, label, var, values):
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", padx=16, pady=3)
        combo = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=22)
        combo.grid(row=row, column=1, sticky="w", padx=8, pady=3)
        return combo

    def _refresh_ideology_types(self) -> None:
        """Sub-ideologies change when the user edits common/ideologies —
        re-read them so deleted ideologies drop out of the leader picker."""
        m = {f"{g} · {s}": s for g, s in self.ideology_service.list_types()}
        if list(m) != list(self._ideo_map):
            self._ideo_map = m
            self._leader_ideo_combo.configure(values=list(m))

    def _check(self, parent, row, label, var):
        ttk.Checkbutton(parent, text=label, variable=var, style="Card.TCheckbutton").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(8, 2))

    def _skill_row(self, parent, row, keys):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=row, column=0, columnspan=2, sticky="w", padx=16, pady=2)
        vars_: dict[str, tk.StringVar] = {}
        for i, key in enumerate(keys):
            ttk.Label(frame, text=self.t(f"characters.{key}"), style="CardMuted.TLabel").grid(
                row=0, column=i, padx=(0, 4))
            var = tk.StringVar(value="1")
            vars_[key] = var
            ttk.Entry(frame, textvariable=var, width=4).grid(row=1, column=i, padx=(0, 8))
        return vars_
