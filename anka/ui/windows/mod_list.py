"""Mod list screen: search, sort, filter, preview, then open the editor."""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from ...domain.mod import Mod
from ..widgets import enable_form_wheel


class ModListScreen(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="TFrame")
        self.app = app
        self._all: list[Mod] = []
        self._shown: list[Mod] = []
        self._selected: Mod | None = None
        self._thumb_ref = None
        self._build()
        self._load()

    # --- layout ----------------------------------------------------------
    def _build(self) -> None:
        t = self.app.t
        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=24, pady=(20, 8))
        ttk.Button(header, text="‹ " + t("common.back"), command=self.app.show_main_menu).pack(side="left")
        ttk.Label(header, text=t("modlist.title"), style="Title.TLabel").pack(side="left", padx=16)
        ttk.Button(header, text="➕ " + t("modlist.new"), style="Accent.TButton",
                   command=self._new_mod).pack(side="left", padx=(16, 0))
        self._count = ttk.Label(header, text="", style="Muted.TLabel")
        self._count.pack(side="right")

        body = ttk.Frame(self, style="TFrame")
        body.pack(fill="both", expand=True, padx=24, pady=12)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_list(body, t)
        self._build_details(body, t)

        # Attach reactive traces only after all widgets/state exist.
        for var in (self._search, self._sort, self._filter):
            var.trace_add("write", lambda *_: self._refresh())

    def _build_list(self, body, t) -> None:
        left = ttk.Frame(body, style="Card.TFrame", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        controls = ttk.Frame(left, style="Card.TFrame")
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        controls.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        search = ttk.Entry(controls, textvariable=self._search)
        search.grid(row=0, column=0, sticky="ew")
        self._placeholder(search, t("modlist.search"))

        self._sort = tk.StringVar(value=t("modlist.sort.name"))
        self._sort_map = {
            t("modlist.sort.name"): "name",
            t("modlist.sort.date"): "date",
            t("modlist.sort.source"): "source",
        }
        sort_cb = ttk.Combobox(controls, textvariable=self._sort,
                               values=list(self._sort_map),
                               state="readonly", width=18)
        sort_cb.grid(row=0, column=1, padx=(8, 0))
        enable_form_wheel(sort_cb)          # wheel is handy in the mod picker

        self._filter = tk.StringVar(value=t("modlist.filter.all"))
        self._filter_map = {
            t("modlist.filter.all"): "all",
            t("modlist.filter.local"): "local",
            t("modlist.filter.workshop"): "workshop",
        }
        filter_cb = ttk.Combobox(controls, textvariable=self._filter,
                                 values=list(self._filter_map),
                                 state="readonly", width=14)
        filter_cb.grid(row=0, column=2, padx=(8, 0))
        enable_form_wheel(filter_cb)

        self._tree = ttk.Treeview(left, columns=("source",), show="tree headings",
                                  selectmode="browse")
        self._tree.heading("#0", text="")
        self._tree.heading("source", text="")
        self._tree.column("source", width=90, anchor="e", stretch=False)
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Double-1>", self._on_double)

    def _on_double(self, event) -> None:
        """Open the row under the cursor, resolving its selection first so a
        double-click on a not-yet-selected mod still opens the right one."""
        row = self._tree.identify_row(event.y)
        if not row:
            return
        self._tree.selection_set(row)
        self._on_select()
        self._open()

    def _build_details(self, body, t) -> None:
        right = ttk.Frame(body, style="Card.TFrame", padding=18)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        self._thumb = tk.Canvas(right, width=200, height=130, highlightthickness=1,
                                highlightbackground=self.app.palette.border,
                                bg=self.app.palette.surface_alt, bd=0)
        self._thumb.grid(row=0, column=0, pady=(0, 12))

        # not ``self._name`` — that shadows the Frame's own Tk widget-name attribute.
        self._name_lbl = ttk.Label(right, text="—", style="Heading.TLabel", wraplength=320)
        self._name_lbl.grid(row=1, column=0, sticky="w")
        self._meta = ttk.Label(right, text="", style="CardMuted.TLabel", wraplength=320, justify="left")
        self._meta.grid(row=2, column=0, sticky="w", pady=(2, 10))

        ttk.Label(right, text=t("modlist.tags"), style="CardMuted.TLabel").grid(row=3, column=0, sticky="nw")
        self._tags = ttk.Label(right, text="", style="Card.TLabel", wraplength=320, justify="left")
        self._tags.grid(row=4, column=0, sticky="nw", pady=(0, 12))

        self._edit = ttk.Button(right, text=t("modlist.edit"), style="Accent.TButton",
                               command=self._open)
        self._edit.grid(row=5, column=0, sticky="ew")
        self._edit.state(["disabled"])

    # --- data ------------------------------------------------------------
    def _load(self) -> None:
        self._all = self.app.repo.list_mods()
        self._refresh()

    def _refresh(self) -> None:
        query = (self._search.get() or "").strip().lower()
        if query == self.app.t("modlist.search").lower():
            query = ""
        flt = self._filter_map.get(self._filter.get(), "all")

        mods = self._all
        if flt == "local":
            mods = [m for m in mods if m.is_local]
        elif flt == "workshop":
            mods = [m for m in mods if not m.is_local]
        if query:
            mods = [m for m in mods if query in m.name.lower() or query in m.id.lower()]

        key = self._sort_map.get(self._sort.get(), "name")
        if key == "name":
            mods = sorted(mods, key=lambda m: m.name.lower())
        elif key == "date":
            mods = sorted(mods, key=lambda m: m.modified_at, reverse=True)
        elif key == "source":
            mods = sorted(mods, key=lambda m: (m.is_local, m.name.lower()))

        self._shown = mods
        self._tree.delete(*self._tree.get_children())
        for m in mods:
            src = self.app.t("modlist.source.local") if m.is_local else self.app.t("modlist.source.workshop")
            self._tree.insert("", "end", iid=m.id, text=m.name, values=(src,))
        self._count.configure(text=self.app.t("modlist.count", count=len(mods)))

    def _on_select(self, _e=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        mod = next((m for m in self._shown if m.id == sel[0]), None)
        if mod is None:
            return
        self._selected = mod
        self._name_lbl.configure(text=mod.name)
        source = self.app.t("modlist.source.local") if mod.is_local else self.app.t("modlist.source.workshop")
        meta = [f"{source}"]
        if mod.version:
            meta.append(f"v{mod.version}")
        if mod.supported_version:
            meta.append(f"HOI4 {mod.supported_version}")
        self._meta.configure(text="   ·   ".join(meta))
        self._tags.configure(text=", ".join(mod.tags) if mod.tags else self.app.t("common.none"))
        self._show_thumb(mod)
        self._edit.state(["!disabled"])

    def _show_thumb(self, mod: Mod) -> None:
        self._thumb.delete("all")
        path = mod.thumbnail_path
        if path:
            try:
                img = Image.open(path)
                img.thumbnail((200, 130), Image.Resampling.LANCZOS)
                self._thumb_ref = ImageTk.PhotoImage(img)
                self._thumb.create_image(100, 65, image=self._thumb_ref)
                return
            except Exception:
                pass
        self._thumb.create_text(100, 65, text="no image", fill=self.app.palette.text_muted)

    def _open(self) -> None:
        if self._selected:
            self.app.show_mod_editor(self._selected)

    # ------------------------------------------------------------ new mod
    def _new_mod(self) -> None:
        """Create an empty local mod (folder + descriptor.mod + launcher .mod)."""
        t = self.app.t
        root = (self.app.settings.current.local_mods_path or "").strip()
        if not root or not Path(root).is_dir():
            messagebox.showerror("ANKA", t("modlist.new.no_path"))
            return
        NewModDialog(self, self.app, Path(root), self._after_create)

    def _after_create(self, mod_dir: Path) -> None:
        self.app.repo = type(self.app.repo)(self.app.settings.current)
        self._load()
        messagebox.showinfo("ANKA", self.app.t("modlist.new.done", path=str(mod_dir)))

    def _placeholder(self, entry: ttk.Entry, text: str) -> None:
        entry.insert(0, text)

        def on_in(_e):
            if entry.get() == text:
                entry.delete(0, "end")

        def on_out(_e):
            if not entry.get():
                entry.insert(0, text)

        entry.bind("<FocusIn>", on_in)
        entry.bind("<FocusOut>", on_out)


class NewModDialog(tk.Toplevel):
    """Name / folder / supported version -> writes an empty, valid mod."""

    def __init__(self, master, app, mods_root: Path, on_done):
        super().__init__(master)
        self.app = app
        self.t = app.t
        self._root_dir = mods_root
        self._on_done = on_done
        self.title(self.t("modlist.new"))
        self.configure(bg=app.palette.bg)
        top = master.winfo_toplevel()
        self.transient(top)
        self.resizable(True, True)

        body = ttk.Frame(self, style="Card.TFrame", padding=18)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text=self.t("modlist.new.name"),
                  style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self._name = tk.StringVar()
        ttk.Entry(body, textvariable=self._name, width=32).grid(
            row=0, column=1, sticky="ew", pady=4)

        ttk.Label(body, text=self.t("modlist.new.folder"),
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self._folder = tk.StringVar()
        ttk.Entry(body, textvariable=self._folder, width=32).grid(
            row=1, column=1, sticky="ew", pady=4)

        ttk.Label(body, text=self.t("modlist.new.version"),
                  style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self._version = tk.StringVar(value="1.19.*")
        ttk.Entry(body, textvariable=self._version, width=14).grid(
            row=2, column=1, sticky="w", pady=4)

        # Suggest a folder name while typing, until the user edits it themselves.
        self._folder_touched = False
        self._folder.trace_add("write", self._mark_touched)
        self._name.trace_add("write", self._suggest_folder)

        row = ttk.Frame(body, style="Card.TFrame")
        row.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(row, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(row, text=self.t("common.save"), style="Accent.TButton",
                   command=self._create).pack(side="right")

        self.grab_set()
        from ...ui.widgets import guard_modal, fit_to_content
        guard_modal(self, top)
        self.bind("<Escape>", lambda e: self.destroy())
        fit_to_content(self, top, (460, 240))

    def _mark_touched(self, *_):
        if self.focus_get() is not None:
            self._folder_touched = True

    def _suggest_folder(self, *_):
        if not self._folder_touched:
            self._folder.set(_slug(self._name.get()))

    def _create(self) -> None:
        name = self._name.get().strip()
        folder = _slug(self._folder.get() or name)
        version = self._version.get().strip() or "1.19.*"
        if not name or not folder:
            messagebox.showerror("ANKA", self.t("modlist.new.err_name"), parent=self)
            return
        target = self._root_dir / folder
        if target.exists():
            messagebox.showerror("ANKA", self.t("modlist.new.err_exists",
                                                folder=folder), parent=self)
            return
        try:
            for sub in ("interface", "common", "localisation/english", "gfx"):
                (target / sub).mkdir(parents=True, exist_ok=True)
            body = (f'name="{name}"\n'
                    f'tags={{\n\t"Graphics"\n}}\n'
                    f'supported_version="{version}"\n'
                    f'version="0.1"\n')
            # descriptor.mod lives inside the mod; the launcher .mod adds `path`.
            (target / "descriptor.mod").write_text(body, encoding="utf-8")
            (self._root_dir / f"{folder}.mod").write_text(
                body + f'path="mod/{folder}"\n', encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("ANKA", str(exc), parent=self)
            return
        self.destroy()
        self._on_done(target)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", (text or "").strip().lower()).strip("_")
    return slug or "new_mod"
