"""Modal dialogs of the Focuses editor.

All dialogs follow the app pattern: themed `tk.Toplevel`, centered over the editor,
`grab_set()`, callbacks instead of return values.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from PIL import Image, ImageTk

from ...ui.widgets import ImageDropZone

_ID_RE = re.compile(r"^\w+$")


class BaseDialog(tk.Toplevel):
    def __init__(self, master, editor, title: str, size: tuple[int, int]):
        super().__init__(master)
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.title(title)
        self.configure(bg=self.palette.bg)
        self.transient(master.winfo_toplevel())
        w, h = size
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

    def buttons_row(self, parent, submit_text: str | None = None) -> ttk.Frame:
        row = ttk.Frame(parent, style="Card.TFrame")
        ttk.Button(row, text=self.t("common.cancel"), command=self.destroy).pack(
            side="right", padx=(6, 0))
        ttk.Button(row, text=submit_text or self.t("common.save"),
                   style="Accent.TButton", command=self._submit).pack(side="right")
        return row

    def _submit(self) -> None:  # pragma: no cover - overridden
        self.destroy()


class TextPromptDialog(BaseDialog):
    """One-line prompt (used for focus ids). Validates ``\\w+`` and uniqueness."""

    def __init__(self, master, editor, title: str, label: str,
                 on_submit: Callable[[str], None], initial: str = "",
                 taken: set[str] | None = None,
                 choices_label: str = "", choices: list[tuple[str, str]] | None = None):
        super().__init__(master, editor, title, (430, 210 if choices else 165))
        self._on_submit = on_submit
        self._taken = taken or set()

        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text=label, style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self._var = tk.StringVar(value=initial)
        entry = ttk.Entry(body, textvariable=self._var)
        entry.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        entry.focus_set()
        entry.icursor("end")
        entry.bind("<Return>", lambda e: self._submit())

        self._choice = tk.StringVar()
        self._choices = choices or []
        if self._choices:
            ttk.Label(body, text=choices_label, style="CardMuted.TLabel").grid(
                row=2, column=0, sticky="w")
            combo = ttk.Combobox(body, state="readonly",
                                 values=[label for _v, label in self._choices])
            combo.grid(row=3, column=0, sticky="ew", pady=(2, 8))
            combo.current(0)
            self._combo = combo
        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=4, column=0, sticky="w")
        self.buttons_row(body, self.t("common.add")).grid(row=5, column=0, sticky="ew")

    def _submit(self) -> None:
        value = self._var.get().strip()
        if not _ID_RE.match(value):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        if value in self._taken:
            self._error.configure(text=self.t("focuses.err.duplicate_id"))
            return
        self.destroy()
        if self._choices:
            self._on_submit(value, self._choices[self._combo.current()][0])  # type: ignore[call-arg]
        else:
            self._on_submit(value)


class MultiPickDialog(BaseDialog):
    """Searchable multi-select list; calls `on_pick(selected)`."""

    def __init__(self, master, editor, title: str, items: list[str],
                 on_pick: Callable[[list[str]], None],
                 preselected: set[str] | None = None):
        super().__init__(master, editor, title, (440, 520))
        self._on_pick = on_pick
        self._items = items
        self._pre = preselected or set()

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(body, textvariable=self._search).grid(row=0, column=0, sticky="ew",
                                                        pady=(0, 8))
        self._tree = ttk.Treeview(body, columns=("v",), show="headings",
                                  selectmode="extended")
        self._tree.heading("v", text=title)
        self._tree.column("v", width=380, anchor="w")
        self._tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._submit())
        self.buttons_row(body, self.t("common.save")).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._refresh()

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for item in self._items:
            if q and q not in item.lower():
                continue
            if shown >= 800:
                break
            self._tree.insert("", "end", iid=item, values=(item,))
            if item in self._pre:
                self._tree.selection_add(item)
            shown += 1

    def _submit(self) -> None:
        picked = list(self._tree.selection())
        self.destroy()
        self._on_pick(picked)


class NewTreeDialog(BaseDialog):
    """Create a new focus-tree file: file name, tree id, country tag, default flag."""

    def __init__(self, master, editor, tags: list[str],
                 on_submit: Callable[[str, str, str, bool], None]):
        super().__init__(master, editor, editor.t("focuses.new_tree"), (460, 300))
        self._on_submit = on_submit

        body = ttk.Frame(self, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)

        def row(r: int, key: str) -> tk.StringVar:
            ttk.Label(body, text=self.t(key), style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            ttk.Entry(body, textvariable=var).grid(row=r, column=1, sticky="ew",
                                                   padx=(10, 0), pady=4)
            return var

        self._tree_id = row(0, "focuses.tree_id")
        self._file = row(1, "focuses.file_name")

        ttk.Label(body, text=self.t("focuses.country_tag"), style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=4)
        self._tag = ttk.Combobox(body, values=tags)
        self._tag.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=4)

        self._default = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text=self.t("focuses.default_tree"), style="Card.TCheckbutton",
                        variable=self._default).grid(row=3, column=0, columnspan=2,
                                                     sticky="w", pady=(8, 4))
        ttk.Label(body, text=self.t("focuses.new_tree_hint"), style="CardMuted.TLabel",
                  wraplength=390, justify="left").grid(row=4, column=0, columnspan=2,
                                                       sticky="w", pady=(2, 8))
        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=5, column=0, columnspan=2, sticky="w")
        self.buttons_row(body, self.t("common.add")).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _submit(self) -> None:
        tree_id = self._tree_id.get().strip()
        file_name = self._file.get().strip() or tree_id
        tag = self._tag.get().strip().upper()
        if not _ID_RE.match(tree_id):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        self.destroy()
        self._on_submit(file_name, tree_id, tag, self._default.get())


class TreePropertiesDialog(BaseDialog):
    """Edit focus_tree-level settings + attached shared_focus references."""

    def __init__(self, master, editor, doc, shared_ids: list[str],
                 on_change: Callable[[], None]):
        super().__init__(master, editor, editor.t("focuses.tree_props"), (520, 560))
        self.doc = doc
        self._shared_ids = shared_ids
        self._on_change = on_change
        tree = doc.tree

        body = ttk.Frame(self, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(7, weight=1)

        ttk.Label(body, text=self.t("focuses.tree_id"), style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=3)
        self._id = tk.StringVar(value=tree.id)
        ttk.Entry(body, textvariable=self._id).grid(row=0, column=1, sticky="ew",
                                                    padx=(10, 0), pady=3)

        self._default = tk.BooleanVar(value=tree.default)
        ttk.Checkbutton(body, text=self.t("focuses.default_tree"),
                        style="Card.TCheckbutton", variable=self._default).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=3)
        self._reset = tk.BooleanVar(value=tree.reset_on_civilwar)
        ttk.Checkbutton(body, text=self.t("focuses.reset_on_civilwar"),
                        style="Card.TCheckbutton", variable=self._reset).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=3)

        self._cont = self._xy_row(body, 3, "focuses.continuous_pos",
                                  tree.continuous_focus_position)
        self._show = self._xy_row(body, 4, "focuses.initial_pos",
                                  tree.initial_show_position)

        ttk.Label(body, text=self.t("focuses.country_script"),
                  style="CardMuted.TLabel").grid(row=5, column=0, sticky="nw", pady=(10, 3))
        self._country = tk.Text(body, height=6, bg=self.palette.surface_alt,
                                fg=self.palette.text, insertbackground=self.palette.text,
                                relief="flat", font=("Consolas", 10))
        self._country.grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=(10, 3))
        self._country.insert("1.0", tree.get_country_script())

        ttk.Label(body, text=self.t("focuses.shared_refs"),
                  style="CardMuted.TLabel").grid(row=6, column=0, sticky="nw", pady=(10, 3))
        shared_frame = ttk.Frame(body, style="Card.TFrame")
        shared_frame.grid(row=6, column=1, rowspan=2, sticky="nsew", padx=(10, 0), pady=(10, 3))
        shared_frame.columnconfigure(0, weight=1)
        self._shared_list = tk.Listbox(shared_frame, height=6, bg=self.palette.surface_alt,
                                       fg=self.palette.text, relief="flat",
                                       selectbackground=self.palette.accent)
        self._shared_list.grid(row=0, column=0, sticky="nsew")
        btns = ttk.Frame(shared_frame, style="Card.TFrame")
        btns.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        ttk.Button(btns, text="➕", width=3, command=self._add_shared).pack(pady=2)
        ttk.Button(btns, text="➖", width=3, command=self._remove_shared).pack(pady=2)
        for ref in doc.tree.shared_focus_refs:
            self._shared_list.insert("end", ref)

        self._error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                foreground=self.palette.danger)
        self._error.grid(row=8, column=0, columnspan=2, sticky="w")
        self.buttons_row(body).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _xy_row(self, body, r: int, key: str,
                value: tuple[int, int] | None) -> tuple[tk.StringVar, tk.StringVar]:
        ttk.Label(body, text=self.t(key), style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        frame = ttk.Frame(body, style="Card.TFrame")
        frame.grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)
        vx = tk.StringVar(value="" if value is None else str(value[0]))
        vy = tk.StringVar(value="" if value is None else str(value[1]))
        ttk.Label(frame, text="x", style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(frame, textvariable=vx, width=7).pack(side="left", padx=(2, 8))
        ttk.Label(frame, text="y", style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(frame, textvariable=vy, width=7).pack(side="left", padx=2)
        return vx, vy

    def _add_shared(self) -> None:
        existing = set(self._shared_list.get(0, "end"))
        items = [s for s in self._shared_ids if s not in existing]
        MultiPickDialog(self, self.editor, self.t("focuses.shared_refs"), items,
                        lambda picked: [self._shared_list.insert("end", p) for p in picked])

    def _remove_shared(self) -> None:
        for idx in reversed(self._shared_list.curselection()):
            self._shared_list.delete(idx)

    @staticmethod
    def _parse_xy(pair: tuple[tk.StringVar, tk.StringVar]) -> tuple[int, int] | None:
        sx, sy = pair[0].get().strip(), pair[1].get().strip()
        if not sx and not sy:
            return None
        return int(sx or 0), int(sy or 0)

    def _submit(self) -> None:
        tree = self.doc.tree
        try:
            cont = self._parse_xy(self._cont)
            show = self._parse_xy(self._show)
        except ValueError:
            self._error.configure(text=self.t("focuses.err.bad_number"))
            return
        new_id = self._id.get().strip()
        if not _ID_RE.match(new_id):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        try:
            tree.set_country_script(self._country.get("1.0", "end"))
        except Exception:
            self._error.configure(text=self.t("focuses.err.bad_script"))
            return
        tree.id = new_id
        tree.default = self._default.get()
        tree.reset_on_civilwar = self._reset.get()
        tree.continuous_focus_position = cont
        tree.initial_show_position = show
        wanted = list(self._shared_list.get(0, "end"))
        for ref in list(tree.shared_focus_refs):
            if ref not in wanted:
                tree.remove_shared_focus_ref(ref)
        for ref in wanted:
            tree.add_shared_focus_ref(ref)
        self.destroy()
        self._on_change()


class IconPickerDialog(BaseDialog):
    """Sprite gallery (game + DLC + mod focus icons) with search, plus custom import."""

    PAGE = 60
    THUMB = (64, 56)

    def __init__(self, master, editor, resolver, current: str,
                 on_pick: Callable[[str], None],
                 on_import: Callable[[object], None] | None = None):
        super().__init__(master, editor, editor.t("focuses.pick_icon"), (720, 560))
        self._resolver = resolver
        self._on_pick = on_pick
        self._photos: dict[str, ImageTk.PhotoImage] = {}
        self._names = resolver.names(prefixes=("GFX_focus_", "GFX_goal_"))
        self._shown = 0

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        top = ttk.Frame(body, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(0, weight=1)
        self._search = tk.StringVar(value="")
        self._search.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(top, textvariable=self._search).grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text=current, style="CardMuted.TLabel").grid(
            row=0, column=1, padx=(10, 0))

        canvas_wrap = ttk.Frame(body, style="Card.TFrame")
        canvas_wrap.grid(row=1, column=0, sticky="nsew")
        canvas_wrap.rowconfigure(0, weight=1)
        canvas_wrap.columnconfigure(0, weight=1)
        self._canvas = tk.Canvas(canvas_wrap, bg=self.palette.surface,
                                 highlightthickness=0, bd=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self._canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=sb.set)
        self._grid = ttk.Frame(self._canvas, style="Card.TFrame")
        self._win = self._canvas.create_window((0, 0), window=self._grid, anchor="nw")
        self._grid.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(
            self._win, width=e.width))
        self._canvas.bind("<MouseWheel>", lambda e: self._canvas.yview_scroll(
            -int(e.delta / 120), "units"))

        bottom = ttk.Frame(body, style="Card.TFrame")
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._more = ttk.Button(bottom, text=self.t("focuses.more_icons"),
                                command=lambda: self._refresh(more=True))
        self._more.pack(side="left")
        if on_import is not None:
            zone = ImageDropZone(bottom, on_import, prompt=self.t("focuses.import_icon"),
                                 preview_size=(88, 66), palette=self.palette)
            zone.pack(side="right")
        ttk.Button(bottom, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=8)
        self._refresh()

    def _refresh(self, more: bool = False) -> None:
        self._shown = self._shown + self.PAGE if more else self.PAGE
        for w in self._grid.winfo_children():
            w.destroy()
        self._photos.clear()
        q = self._search.get().strip().lower()
        matches = [n for n in self._names if q in n.lower()] if q else self._names
        cols = 6
        for i, name in enumerate(matches[: self._shown]):
            photo = self._thumb(name)
            cell = ttk.Frame(self._grid, style="Card.TFrame", padding=4)
            cell.grid(row=i // cols, column=i % cols, padx=3, pady=3)
            btn = tk.Button(cell, image=photo, bd=0, bg=self.palette.surface,
                            activebackground=self.palette.surface_alt, cursor="hand2",
                            command=lambda n=name: self._pick(n))
            btn.pack()
            short = name.removeprefix("GFX_focus_").removeprefix("GFX_goal_")
            tk.Label(cell, text=short[:14], bg=self.palette.surface,
                     fg=self.palette.text_muted, font=("Segoe UI", 8)).pack()
        self._more.configure(state="normal" if len(matches) > self._shown else "disabled")
        self._canvas.yview_moveto(0)

    def _thumb(self, name: str) -> ImageTk.PhotoImage:
        if name in self._photos:
            return self._photos[name]
        img = None
        path = self._resolver.resolve(name)
        if path is not None:
            try:
                with Image.open(path) as im:
                    img = im.convert("RGBA")
                img.thumbnail(self.THUMB, Image.LANCZOS)
            except Exception:
                img = None
        if img is None:
            img = Image.new("RGBA", self.THUMB, (0, 0, 0, 0))
        photo = ImageTk.PhotoImage(img)
        self._photos[name] = photo
        return photo

    def _pick(self, name: str) -> None:
        self.destroy()
        self._on_pick(name)
