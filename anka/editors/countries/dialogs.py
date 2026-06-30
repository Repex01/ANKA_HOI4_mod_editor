"""Modal dialogs for the Countries editor: create-country and state picker."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import colorchooser, ttk

_TAG_RE = re.compile(r"^[A-Z]{2,3}$")


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
        self.resizable(False, False)
        w, h = size
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()


class NewCountryDialog(_Dialog):
    def __init__(self, master, editor, existing_tags: set[str]):
        super().__init__(master, editor, editor.t("countries.new.title"), (380, 320))
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
        self._name = tk.StringVar()
        ttk.Entry(body, textvariable=self._name, width=28).pack(anchor="w", pady=(2, 10))

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
        name = self._name.get().strip() or tag
        try:
            self.editor.service.create_country(tag, name, self._rgb)
        except Exception as exc:
            self._error.configure(text=str(exc), foreground=self.palette.danger)
            return
        self.destroy()
        self.editor.after_country_created(tag)

    def _fail(self, key: str) -> None:
        self._error.configure(text=self.t(key), foreground=self.palette.danger)


class StatePickerDialog(_Dialog):
    """Searchable state list. Owner column flags conflicts; selection never blocked."""

    def __init__(self, master, editor, current_tag: str):
        super().__init__(master, editor, editor.t("countries.state_picker"), (460, 520))
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

        self._tree = ttk.Treeview(body, columns=("name", "owner"), show="headings", selectmode="browse")
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
        state_id = int(sel[0])
        self.destroy()
        self.editor.assign_state(state_id)


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
