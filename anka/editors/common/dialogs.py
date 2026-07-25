"""Shared modal dialogs for editor modules (focuses, decisions, ...).

All dialogs follow the app pattern: themed `tk.Toplevel`, centered over the main
window, `grab_set()`, callbacks instead of return values. The `editor` handed in is
any editor module (provides `t`, `palette`); richer dialogs (icon gallery) also use
its sprite resolver.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageTk

from ...core.pdx import dumps
from ...ui.widgets import ImageDropZone
from ...ui.widgets.mousewheel import bind_wheel, wheel_steps

_ID_RE = re.compile(r"^\w+$")


class BaseDialog(tk.Toplevel):
    def __init__(self, master, editor, title: str, size: tuple[int, int]):
        super().__init__(master)
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.title(title)
        self.configure(bg=self.palette.bg)
        top = master.winfo_toplevel()
        self.transient(top)
        w, h = size
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        # Center over the application window, not over `master` (which may be a
        # narrow side panel — that used to shove dialogs to the screen edge).
        x = top.winfo_rootx() + (top.winfo_width() - w) // 2
        y = top.winfo_rooty() + (top.winfo_height() - h) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        from ...ui.widgets import guard_modal, fit_to_content
        guard_modal(self, top)      # keep taskbar restore working (Windows)
        self.bind("<Escape>", lambda e: self.destroy())
        fit_to_content(self, top, size)   # never clip UI-scaled content

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
    """One-line prompt (ids). Validates ``\\w+`` and uniqueness; optional kind choice."""

    def __init__(self, master, editor, title: str, label: str,
                 on_submit: Callable[[str], None], initial: str = "",
                 taken: set[str] | None = None,
                 choices_label: str = "", choices: list[tuple[str, str]] | None = None,
                 pattern: str | None = None):
        super().__init__(master, editor, title, (430, 210 if choices else 165))
        self._on_submit = on_submit
        self._taken = taken or set()
        self._pattern = re.compile(pattern) if pattern else _ID_RE

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

    def set_choice(self, value: str) -> None:
        for i, (v, _label) in enumerate(self._choices):
            if v == value:
                self._combo.current(i)
                return

    def _submit(self) -> None:
        value = self._var.get().strip()
        if not self._pattern.match(value):
            self._error.configure(text=self.t("focuses.err.bad_id"))
            return
        if value in self._taken:
            self._error.configure(text=self.t("focuses.err.duplicate_id"))
            return
        # Read widget state BEFORE destroy() — the combobox dies with the dialog.
        choice = self._choices[self._combo.current()][0] if self._choices else None
        self.destroy()
        if choice is not None:
            self._on_submit(value, choice)  # type: ignore[call-arg]
        else:
            self._on_submit(value)


class MultiPickDialog(BaseDialog):
    """Searchable multi-select list; calls `on_pick(selected_values)`.

    `items` may be plain strings or ``(display, value)`` pairs — the list shows
    the display text but selection returns the values. Shift/Ctrl extend the
    selection, and "Select all" / "Clear" act on the currently shown rows."""

    def __init__(self, master, editor, title: str,
                 items: "list[str] | list[tuple[str, str]]",
                 on_pick: Callable[[list[str]], None],
                 preselected: set[str] | None = None):
        super().__init__(master, editor, title, (440, 540))
        self._on_pick = on_pick
        self._options = [(it, it) if isinstance(it, str) else it for it in items]
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

        tools = ttk.Frame(body, style="Card.TFrame")
        tools.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(tools, text=self.t("common.select_all"),
                   command=self._select_all).pack(side="left")
        ttk.Button(tools, text=self.t("common.clear_selection"),
                   command=self._clear).pack(side="left", padx=6)
        self.buttons_row(body, self.t("common.save")).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._refresh()

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        shown = 0
        for display, value in self._options:
            if q and q not in display.lower():
                continue
            if shown >= 800:
                break
            self._tree.insert("", "end", iid=value, values=(display,))
            if value in self._pre:
                self._tree.selection_add(value)
            shown += 1

    def _select_all(self) -> None:
        self._tree.selection_set(self._tree.get_children())

    def _clear(self) -> None:
        self._tree.selection_remove(self._tree.selection())

    def _submit(self) -> None:
        picked = list(self._tree.selection())
        self.destroy()
        self._on_pick(picked)


class SinglePickDialog(BaseDialog):
    """Searchable single-select picker over (display, value) options.
    The search matches the whole display string — so states are found both by id
    and by name, countries by tag and name, etc."""

    def __init__(self, master, editor, title: str,
                 options: list[tuple[str, str]],
                 on_pick: Callable[[str], None], current: str = ""):
        super().__init__(master, editor, title, (440, 520))
        self._options = options
        self._on_pick = on_pick
        self._shown: list[tuple[str, str]] = []

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh())
        entry = ttk.Entry(body, textvariable=self._search)
        entry.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._submit())

        self._list = tk.Listbox(body, bg=self.palette.surface_alt, fg=self.palette.text,
                                relief="flat", selectbackground=self.palette.accent,
                                font=("Consolas", 10), exportselection=False)
        self._list.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(body, orient="vertical", command=self._list.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._list.configure(yscrollcommand=sb.set)
        self._list.bind("<Double-1>", lambda e: self._submit())

        row = ttk.Frame(body, style="Card.TFrame")
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(row, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(row, text=self.t("common.ok"), style="Accent.TButton",
                   command=self._submit).pack(side="right")

        self._current = current
        self._refresh()

    def _refresh(self) -> None:
        q = self._search.get().strip().lower()
        self._list.delete(0, "end")
        self._shown = []
        for display, value in self._options:
            if q and q not in display.lower():
                continue
            if len(self._shown) >= 1000:
                break
            self._shown.append((display, value))
            self._list.insert("end", display)
            if value == self._current:
                self._list.selection_set("end")
                self._list.see("end")

    def _submit(self) -> None:
        sel = self._list.curselection()
        if not sel:
            return
        value = self._shown[sel[0]][1]
        self.destroy()
        self._on_pick(value)


class PdxPreviewDialog(BaseDialog):
    """Read-only serialized view of a PDX node — exactly what will be written to the
    file — with one-click copy to clipboard."""

    def __init__(self, master, editor, title: str, node):
        super().__init__(master, editor, title, (640, 560))
        self.resizable(True, True)
        script = dumps(node).rstrip("\n")

        body = ttk.Frame(self, style="Card.TFrame", padding=10)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        text = tk.Text(body, wrap="none", bg=self.palette.surface_alt,
                       fg=self.palette.text, relief="flat", font=("Consolas", 10),
                       tabs="24")
        text.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb = ttk.Scrollbar(body, orient="horizontal", command=text.xview)
        xsb.grid(row=1, column=0, sticky="ew")
        text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        text.insert("1.0", script)
        text.configure(state="disabled")

        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._copied = ttk.Label(bar, text="", style="CardMuted.TLabel")
        self._copied.pack(side="left")
        ttk.Button(bar, text=self.t("common.close"),
                   command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="📋 " + self.t("focuses.script.copy"),
                   style="Accent.TButton",
                   command=lambda: self._copy(script)).pack(side="right")

    def _copy(self, script: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(script)
        self._copied.configure(text="✓ " + self.t("focuses.script.copied"))


class IconPickerDialog(BaseDialog):
    """Sprite gallery (game + DLC + mod) with infinite scroll, search and optional
    custom-image import. `prefixes` selects the sprite family (focus icons, decision
    icons, ...); `*_shine` duplicates are always filtered out."""

    PAGE = 60
    THUMB = (64, 56)

    def __init__(self, master, editor, resolver, current: str,
                 on_pick: Callable[[str], None],
                 on_import: Callable[[object, bool], None] | None = None,
                 prefixes: tuple[str, ...] = ("GFX_focus_", "GFX_goal_")):
        super().__init__(master, editor, editor.t("focuses.pick_icon"), (720, 560))
        self._resolver = resolver
        self._on_pick = on_pick
        self._photos: dict[str, ImageTk.PhotoImage] = {}
        # Shine sprites duplicate their base icon (same texture, animation on top) —
        # showing them would double every icon in the gallery.
        self._names = [n for n in resolver.names(prefixes=prefixes)
                       if not n.lower().endswith("_shine")]
        self._matches: list[str] = []
        self._shown = 0
        self._appending = False

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
        self._sb = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self._canvas.yview)
        self._sb.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=self._on_scroll)
        self._grid = ttk.Frame(self._canvas, style="Card.TFrame")
        self._win = self._canvas.create_window((0, 0), window=self._grid, anchor="nw")
        self._grid.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(
            self._win, width=e.width))
        bind_wheel(self._canvas, lambda e: (s := wheel_steps(e)) and
                   self._canvas.yview_scroll(-s, "units"))

        bottom = ttk.Frame(body, style="Card.TFrame")
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._counter = ttk.Label(bottom, text="", style="CardMuted.TLabel")
        self._counter.pack(side="left")
        if on_import is not None:
            # "No resize" keeps the source image at its original dimensions instead of
            # the standard fixed icon size. The flag is passed as on_import's 2nd arg.
            self._keep_size = tk.BooleanVar(value=False)
            zone = ImageDropZone(bottom,
                                 lambda p: on_import(p, self._keep_size.get()),
                                 prompt=self.t("focuses.import_icon"),
                                 preview_size=(88, 66), palette=self.palette)
            zone.pack(side="right")
            ttk.Checkbutton(bottom, text=self.t("focuses.import_no_resize"),
                            style="Card.TCheckbutton",
                            variable=self._keep_size).pack(side="right", padx=8)
        ttk.Button(bottom, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=8)
        self._refresh()

    def _refresh(self) -> None:
        """Search changed: rebuild the gallery from scratch (first page only —
        further pages stream in as the user scrolls, see `_on_scroll`)."""
        for w in self._grid.winfo_children():
            w.destroy()
        self._photos.clear()
        q = self._search.get().strip().lower()
        self._matches = [n for n in self._names if q in n.lower()] if q else list(self._names)
        self._shown = 0
        self._canvas.yview_moveto(0)
        self._append_page()

    def _append_page(self) -> None:
        """Render the next batch of thumbnails (infinite scroll)."""
        if self._appending or self._shown >= len(self._matches):
            return
        self._appending = True
        try:
            cols = 6
            page = self._matches[self._shown: self._shown + self.PAGE]
            for offset, name in enumerate(page):
                i = self._shown + offset
                photo = self._thumb(name)
                cell = ttk.Frame(self._grid, style="Card.TFrame", padding=4)
                cell.grid(row=i // cols, column=i % cols, padx=3, pady=3)
                btn = tk.Button(cell, image=photo, bd=0, bg=self.palette.surface,
                                activebackground=self.palette.surface_alt, cursor="hand2",
                                command=lambda n=name: self._pick(n))
                btn.pack()
                short = name
                for prefix in ("GFX_focus_", "GFX_goal_", "GFX_decision_", "GFX_"):
                    if short.startswith(prefix):
                        short = short[len(prefix):]
                        break
                tk.Label(cell, text=short[:14], bg=self.palette.surface,
                         fg=self.palette.text_muted, font=("Segoe UI", 8)).pack()
            self._shown += len(page)
            self._counter.configure(text=f"{self._shown} / {len(self._matches)}")
        finally:
            self._appending = False

    def _on_scroll(self, first: str, last: str) -> None:
        """Scrollbar proxy: when the view reaches the bottom, stream the next page.
        Also self-feeds until the content overflows the viewport."""
        self._sb.set(first, last)
        if float(last) > 0.9 and self._shown < len(self._matches):
            # Defer: appending mid-scroll-callback confuses the canvas geometry.
            self.after_idle(self._append_page)

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
