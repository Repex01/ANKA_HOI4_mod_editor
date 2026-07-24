"""Modal dialogs for the Countries editor: create-country and state picker."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import colorchooser, ttk

# HOI4 tags start with a letter and may contain digits afterwards (dynamic tags
# like ``D01``); 2–3 characters, never leading with a digit.
_TAG_RE = re.compile(r"^[A-Z][A-Z0-9]{1,2}$")


class _Dialog(tk.Toplevel):
    """Themed, centered, modal Toplevel base."""

    def __init__(self, master, editor, title: str, size: tuple[int, int]):
        super().__init__(master)
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.title(title)
        self.configure(bg=self.palette.bg)
        self.transient(master.winfo_toplevel())
        self._master_ref = master
        self._base_size = size
        # Resizable as a safety net: UI scaling can make content exceed the
        # hardcoded size, and a fixed non-resizable window would clip it.
        self.resizable(True, True)
        w, h = size
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        from ...ui.widgets import guard_modal
        guard_modal(self)           # keep taskbar restore working (Windows)
        # Once the subclass has built its widgets, grow to fit so nothing is
        # clipped when tk scaling enlarges the content.
        self.after_idle(self._fit_to_content)

    def _fit_to_content(self) -> None:
        try:
            self.update_idletasks()
            base_w, base_h = self._base_size
            w = max(base_w, self.winfo_reqwidth())
            h = max(base_h, self.winfo_reqheight())
            m = self._master_ref
            x = m.winfo_rootx() + (m.winfo_width() - w) // 2
            y = m.winfo_rooty() + (m.winfo_height() - h) // 2
            self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass


class NewCountryDialog(_Dialog):
    def __init__(self, master, editor, existing_tags: set[str]):
        super().__init__(master, editor, editor.t("countries.new.title"), (380, 400))
        self._existing = existing_tags
        self._rgb = (128, 128, 128)
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=20)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(body, text=self.t("countries.new.tag"), style="CardMuted.TLabel").pack(anchor="w")
        self._tag = tk.StringVar()
        ttk.Entry(body, textvariable=self._tag, width=12).pack(anchor="w", pady=(2, 10))

        ttk.Label(body, text=self.t("countries.new.name"), style="CardMuted.TLabel").pack(anchor="w")
        # NB: not ``self._name`` — that is tk.Toplevel's own widget-name attribute;
        # assigning a StringVar to it makes destroy() raise (unhashable StringVar).
        self._name_var = tk.StringVar()
        ttk.Entry(body, textvariable=self._name_var, width=28).pack(anchor="w", pady=(2, 10))

        ttk.Label(body, text=self.t("countries.graphical_culture"),
                  style="CardMuted.TLabel").pack(anchor="w")
        cultures = self.editor.service.list_graphical_cultures()
        default = ("western_european_gfx" if "western_european_gfx" in cultures
                   else cultures[0])
        self._culture = tk.StringVar(value=default)
        ttk.Combobox(body, textvariable=self._culture, values=cultures,
                     state="readonly", width=26).pack(anchor="w", pady=(2, 10))

        ttk.Label(body, text=self.t("countries.color"), style="CardMuted.TLabel").pack(anchor="w")
        crow = ttk.Frame(body, style="Card.TFrame")
        crow.pack(anchor="w", pady=(2, 10))
        self._swatch = tk.Canvas(crow, width=30, height=20, highlightthickness=1,
                                 highlightbackground=self.palette.border, bg="#808080", bd=0)
        self._swatch.pack(side="left")
        ttk.Button(crow, text="…", width=4, command=self._pick_color).pack(side="left", padx=6)

        self._error = ttk.Label(body, text="", style="CardMuted.TLabel")
        self._error.pack(anchor="w", pady=(4, 8))

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.pack(anchor="e", side="bottom")
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text=self.t("common.create"), style="Accent.TButton",
                   command=self._submit).pack(side="left")

    def _pick_color(self) -> None:
        rgb, _ = colorchooser.askcolor(color="#%02x%02x%02x" % self._rgb, parent=self)
        if rgb:
            self._rgb = tuple(int(c) for c in rgb)
            self._swatch.configure(bg="#%02x%02x%02x" % self._rgb)

    def _submit(self) -> None:
        tag = self._tag.get().strip().upper()
        if not _TAG_RE.match(tag):
            return self._fail("countries.new.invalid_tag")
        if tag in self._existing:
            return self._fail("countries.new.exists")
        name = self._name_var.get().strip() or tag
        try:
            self.editor.service.create_country(
                tag, name, self._rgb, graphical_culture=self._culture.get())
        except Exception as exc:
            self._error.configure(text=str(exc), foreground=self.palette.danger)
            return
        self.destroy()
        self.editor.after_country_created(tag)

    def _fail(self, key: str) -> None:
        self._error.configure(text=self.t(key), foreground=self.palette.danger)


class NewCosmeticTagDialog(_Dialog):
    """Create a cosmetic tag: id + display name + map color (cosmetic.txt entry)."""

    _COSMETIC_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

    def __init__(self, master, editor, existing_tags: set[str]):
        super().__init__(master, editor, editor.t("countries.cosmetic.new_title"), (440, 430))
        self._existing = existing_tags
        self._rgb = (128, 128, 128)
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=20)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(body, text=self.t("countries.cosmetic.tag"), style="CardMuted.TLabel").pack(anchor="w")
        self._tag = tk.StringVar()
        ttk.Entry(body, textvariable=self._tag, width=28).pack(anchor="w", pady=(2, 2))
        ttk.Label(body, text=self.t("countries.cosmetic.tag_hint"),
                  style="CardMuted.TLabel", wraplength=330, justify="left").pack(anchor="w", pady=(0, 10))

        ttk.Label(body, text=self.t("countries.new.name"), style="CardMuted.TLabel").pack(anchor="w")
        self._name_var = tk.StringVar()
        ttk.Entry(body, textvariable=self._name_var, width=28).pack(anchor="w", pady=(2, 10))

        ttk.Label(body, text=self.t("countries.color"), style="CardMuted.TLabel").pack(anchor="w")
        crow = ttk.Frame(body, style="Card.TFrame")
        crow.pack(anchor="w", pady=(2, 10))
        self._swatch = tk.Canvas(crow, width=30, height=20, highlightthickness=1,
                                 highlightbackground=self.palette.border, bg="#808080", bd=0)
        self._swatch.pack(side="left")
        ttk.Button(crow, text="…", width=4, command=self._pick_color).pack(side="left", padx=6)

        self._error = ttk.Label(body, text="", style="CardMuted.TLabel")
        self._error.pack(anchor="w", pady=(4, 8))

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.pack(anchor="e", side="bottom")
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text=self.t("common.create"), style="Accent.TButton",
                   command=self._submit).pack(side="left")

    def _pick_color(self) -> None:
        rgb, _ = colorchooser.askcolor(color="#%02x%02x%02x" % self._rgb, parent=self)
        if rgb:
            self._rgb = tuple(int(c) for c in rgb)
            self._swatch.configure(bg="#%02x%02x%02x" % self._rgb)

    def _submit(self) -> None:
        tag = self._tag.get().strip()
        if not self._COSMETIC_RE.match(tag):
            return self._fail("countries.cosmetic.invalid_tag")
        if tag in self._existing:
            return self._fail("countries.new.exists")
        name = self._name_var.get().strip()
        try:
            self.editor.service.create_cosmetic_tag(tag, name, self._rgb)
        except Exception as exc:
            self._error.configure(text=str(exc), foreground=self.palette.danger)
            return
        self.destroy()
        self.editor.after_country_created(tag)

    def _fail(self, key: str) -> None:
        self._error.configure(text=self.t(key), foreground=self.palette.danger)


class StatePickerDialog(_Dialog):
    """Searchable multi-select state list. Owner column flags conflicts; selection is
    never blocked. Core checkboxes control add_core_of for the added states."""

    def __init__(self, master, editor, current_tag: str):
        super().__init__(master, editor, editor.t("countries.state_picker"), (460, 590))
        self._tag = current_tag
        self._states = editor.state_service.list_states()
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(body, textvariable=self._search).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._tree = ttk.Treeview(body, columns=("name", "owner"), show="headings",
                                  selectmode="extended")
        self._tree.heading("name", text=self.t("countries.name_value"))
        self._tree.heading("owner", text=self.t("countries.column_owner"))
        self._tree.column("name", width=240)
        self._tree.column("owner", width=90, anchor="center")
        self._tree.tag_configure("conflict", foreground=self.palette.danger)
        self._tree.tag_configure("own", foreground=self.palette.text_muted)
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._submit())

        # Core options: "national provinces" unlocks "strip other cores".
        self._core = tk.BooleanVar(value=True)
        self._core_only = tk.BooleanVar(value=False)
        core_cb = ttk.Checkbutton(body, text=self.t("countries.state_core"),
                                  style="Card.TCheckbutton", variable=self._core,
                                  command=self._core_toggled)
        core_cb.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._core_only_cb = ttk.Checkbutton(
            body, text=self.t("countries.state_core_only"),
            style="Card.TCheckbutton", variable=self._core_only)
        self._core_only_cb.grid(row=3, column=0, columnspan=2, sticky="w", padx=(20, 0))
        self._core_toggled()

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text=self.t("common.add"), style="Accent.TButton",
                   command=self._submit).pack(side="left")
        self._refresh()

    def _core_toggled(self) -> None:
        if self._core.get():
            self._core_only_cb.configure(state="normal")
        else:
            self._core_only.set(False)
            self._core_only_cb.configure(state="disabled")

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for s in self._states:
            if q and q not in str(s.id) and q not in s.name.lower() and q not in (s.owner or "").lower():
                continue
            if shown >= 400:  # keep the tree responsive; narrow via search
                break
            tags = ()
            if s.owner == self._tag:
                tags = ("own",)
            elif s.owner:
                tags = ("conflict",)
            self._tree.insert("", "end", iid=str(s.id), values=(f"{s.id} · {s.name}", s.owner or "—"), tags=tags)
            shown += 1

    def _submit(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        state_ids = [int(iid) for iid in sel]
        core = self._core.get()
        core_only = self._core_only.get()
        self.destroy()
        self.editor.assign_states(state_ids, core=core, exclusive_core=core_only)


class TagPickerDialog(_Dialog):
    """Searchable multi-select of country tags with preselection — used to edit a
    state's ``add_core_of`` list. Calls `on_pick(sorted_selected_tags)`."""

    def __init__(self, master, editor, title: str, tags: list[tuple[str, str]],
                 on_pick, preselected: set[str] | None = None):
        super().__init__(master, editor, title, (420, 520))
        self._tags = tags                      # (tag, display name)
        self._on_pick = on_pick
        self._selected: set[str] = set(preselected or ())
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        entry = ttk.Entry(body, textvariable=self._search)
        entry.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        entry.focus_set()

        self._tree = ttk.Treeview(body, columns=("tag", "name"), show="headings",
                                  selectmode="extended")
        self._tree.heading("tag", text=self.t("countries.tag"))
        self._tree.heading("name", text=self.t("countries.name"))
        self._tree.column("tag", width=70, anchor="center")
        self._tree.column("name", width=250)
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<<TreeviewSelect>>", self._sync)

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(
            side="left", padx=6)
        ttk.Button(btns, text=self.t("common.save"), style="Accent.TButton",
                   command=self._submit).pack(side="left")
        self._refresh()

    def _sync(self, _event=None) -> None:
        """Fold the visible selection into the persistent set, so choices made
        before/after a search are all kept."""
        visible = set(self._tree.get_children())
        self._selected = (self._selected - visible) | set(self._tree.selection())

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for tag, name in self._tags:
            if q and q not in tag.lower() and q not in name.lower():
                continue
            if shown >= 400:
                break
            self._tree.insert("", "end", iid=tag, values=(tag, name))
            if tag in self._selected:
                self._tree.selection_add(tag)
            shown += 1

    def _submit(self) -> None:
        self._sync()
        result = sorted(self._selected)
        self.destroy()
        self._on_pick(result)


class CoresEditDialog(_Dialog):
    """Chip-style editor of ``add_core_of`` for one or several states.

    Each core tag is a chip: clicking it removes the tag from every selected state.
    The ➕ chip opens the tag picker to add one/many tags to every selected state.
    Changes apply immediately (state files are mod-copied as usual)."""

    def __init__(self, master, editor, state_ids: list[int]):
        super().__init__(master, editor, editor.t("countries.cores_title"), (440, 320))
        self._state_ids = state_ids
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(2, weight=1)
        body.columnconfigure(0, weight=1)

        states = [self.editor.state_service.get(sid) for sid in self._state_ids]
        names = ", ".join(f"{s.id} · {s.name}" for s in states if s)
        ttk.Label(body, text=names, style="Card.TLabel", wraplength=390,
                  justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=self.t("countries.cores_click_hint"),
                  style="CardMuted.TLabel", wraplength=390, justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 8))

        self._chips = ttk.Frame(body, style="Card.TFrame")
        self._chips.grid(row=2, column=0, sticky="nsew")

        ttk.Button(body, text=self.t("common.close"), command=self.destroy).grid(
            row=3, column=0, sticky="e", pady=(10, 0))
        self._render_chips()

    def _states(self):
        return [s for s in (self.editor.state_service.get(sid)
                            for sid in self._state_ids) if s is not None]

    def _render_chips(self) -> None:
        for w in self._chips.winfo_children():
            w.destroy()
        states = self._states()
        total = len(states)
        counts: dict[str, int] = {}
        for s in states:
            for tag in s.cores:
                counts[tag] = counts.get(tag, 0) + 1

        col = row = 0
        max_cols = 4
        for tag in sorted(counts):
            partial = f" ({counts[tag]}/{total})" if counts[tag] < total else ""
            chip = tk.Button(
                self._chips, text=f"{tag}{partial}  ✕", bd=0, padx=10, pady=5,
                bg=self.palette.surface_alt, fg=self.palette.text,
                activebackground=self.palette.danger,
                activeforeground=self.palette.accent_text, cursor="hand2",
                command=lambda t=tag: self._remove(t))
            chip.grid(row=row, column=col, padx=3, pady=3, sticky="w")
            col += 1
            if col >= max_cols:
                col, row = 0, row + 1
        add = tk.Button(self._chips, text="➕", bd=0, padx=12, pady=5,
                        bg=self.palette.accent, fg=self.palette.accent_text,
                        activebackground=self.palette.accent_hover,
                        activeforeground=self.palette.accent_text, cursor="hand2",
                        command=self._add)
        add.grid(row=row, column=col, padx=3, pady=3, sticky="w")

    def _remove(self, tag: str) -> None:
        for s in self._states():
            if tag in s.cores:
                self.editor.state_service.set_cores(
                    s.id, [t for t in s.cores if t != tag])
        self._render_chips()
        self.editor.notify_cores_changed(self._state_ids)

    def _add(self) -> None:
        tags = [(r.tag, r.name)
                for r in self.editor.service.list_tags(include_vanilla=True)]

        def picked(new_tags: list[str]) -> None:
            for s in self._states():
                merged = list(s.cores) + [t for t in new_tags if t not in s.cores]
                self.editor.state_service.set_cores(s.id, merged)
            self._render_chips()
            self.editor.notify_cores_changed(self._state_ids)

        TagPickerDialog(self, self.editor, self.t("countries.cores_add"), tags, picked)


class TechPickerDialog(_Dialog):
    """Searchable, multi-select technology picker. Excludes already-selected techs."""

    def __init__(self, master, editor, already: set[str]):
        super().__init__(master, editor, editor.t("countries.tech.picker"), (440, 520))
        self._techs = [t for t in editor.tech_service.list_techs() if t not in already]
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(body, textvariable=self._search).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._tree = ttk.Treeview(body, columns=("t",), show="headings", selectmode="extended")
        self._tree.heading("t", text=self.t("countries.tech.list"))
        self._tree.column("t", width=380, anchor="w")
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._submit())

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text=self.t("common.add"), style="Accent.TButton",
                   command=self._submit).pack(side="left")
        self._refresh()

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for tech in self._techs:
            if q and q not in tech.lower():
                continue
            if shown >= 400:
                break
            self._tree.insert("", "end", iid=tech, values=(tech,))
            shown += 1

    def _submit(self) -> None:
        sel = list(self._tree.selection())
        if not sel:
            return
        self.destroy()
        self.editor.add_technologies(sel)


class ImportTechDialog(_Dialog):
    """Pick a country to copy its starting technologies from. Shows tech counts."""

    def __init__(self, master, editor):
        super().__init__(master, editor, editor.t("countries.tech.import"), (440, 520))
        self._refs = editor.service.list_tags(include_vanilla=True)
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(body, textvariable=self._search).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._tree = ttk.Treeview(body, columns=("name", "count"), show="headings", selectmode="browse")
        self._tree.heading("name", text=self.t("countries.name_value"))
        self._tree.heading("count", text=self.t("countries.tech.count"))
        self._tree.column("name", width=300, anchor="w")
        self._tree.column("count", width=80, anchor="center")
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._submit())

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text=self.t("countries.tech.import_btn"), style="Accent.TButton",
                   command=self._submit).pack(side="left")
        self._refresh()

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for ref in self._refs:
            if q and q not in ref.tag.lower() and q not in ref.name.lower():
                continue
            if shown >= 150:
                break
            count = self.editor.tech_count(ref.tag)
            self._tree.insert("", "end", iid=ref.tag, values=(f"{ref.tag} · {ref.name}", count))
            shown += 1

    def _submit(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        self.destroy()
        self.editor.import_technologies(sel[0])


class CharacterPickerDialog(_Dialog):
    """Assign an *existing, unrecruited* character to the current country."""

    def __init__(self, master, editor):
        super().__init__(master, editor, editor.t("countries.char.picker"), (480, 520))
        self._chars = editor.character_service.available_characters()
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(body, textvariable=self._search).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._tree = ttk.Treeview(body, columns=("name", "roles"), show="headings", selectmode="browse")
        self._tree.heading("name", text=self.t("countries.char.id_name"))
        self._tree.heading("roles", text=self.t("countries.char.roles"))
        self._tree.column("name", width=280, anchor="w")
        self._tree.column("roles", width=140, anchor="w")
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._submit())

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text=self.t("common.cancel"), command=self.destroy).pack(side="left", padx=6)
        ttk.Button(btns, text=self.t("common.add"), style="Accent.TButton",
                   command=self._submit).pack(side="left")
        self._refresh()

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for c in self._chars:
            if q and q not in c.char_id.lower() and q not in c.name.lower():
                continue
            if shown >= 300:
                break
            label = f"{c.char_id}" + (f"  ({c.name})" if c.name and c.name != c.char_id else "")
            self._tree.insert("", "end", iid=c.char_id, values=(label, c.roles_label))
            shown += 1

    def _submit(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        self.destroy()
        self.editor.assign_character(sel[0])
