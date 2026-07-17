"""Main menu: logo + Edit mod / Settings / Exit, author links and a hover-hint
area.

"Edit mod" is disabled until the required paths are configured (per the spec),
with an inline hint pointing the user to settings. Below the main buttons sit
three square link buttons (YouTube / Ko-fi / GitHub); hovering any button shows
its description in a fixed-height hint area (empty when nothing is hovered, so
the layout never jumps).
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

from ...config.constants import Paths
from ..theme import FONT_TITLE

_LINKS = (
    ("youtube.png", "menu.hint.youtube",
     "https://www.youtube.com/@-veselator2599"),
    ("kofi.png", "menu.hint.kofi", "https://ko-fi.com/veselatorl"),
    ("github.png", "menu.hint.github",
     "https://github.com/Veselator/ANKA_HOI4_mod_editor"),
)
_LINK_ICON_SIZE = (36, 36)


class MainMenuScreen(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="TFrame")
        self.app = app
        self._logo_ref = None
        self._icon_refs: list[ImageTk.PhotoImage] = []
        self._build()

    def _build(self) -> None:
        t, p = self.app.t, self.app.palette
        center = ttk.Frame(self, style="TFrame")
        center.place(relx=0.5, rely=0.5, anchor="center")

        self._place_logo(center, t)

        ttk.Label(center, text=t("app.subtitle"), style="Muted.TLabel").pack(pady=(0, 28))

        configured = self.app.settings.current.paths_configured

        edit = ttk.Button(center, text=t("menu.edit_mod"), style="Menu.TButton",
                          width=26, command=self.app.show_mod_list)
        edit.pack(pady=6, ipadx=4)
        self._bind_hint(edit, "menu.hint.edit_mod")
        if not configured:
            edit.state(["disabled"])
            ttk.Label(center, text=t("menu.paths_required"), style="Muted.TLabel").pack(pady=(2, 6))

        settings = ttk.Button(center, text=t("menu.settings"), style="Menu.TButton",
                              width=26, command=self.app.show_settings)
        settings.pack(pady=6, ipadx=4)
        self._bind_hint(settings, "menu.hint.settings")
        contact = ttk.Button(center, text=t("menu.contact"), style="Menu.TButton",
                             width=26, command=lambda: webbrowser.open(
                                 "https://forms.gle/QciUiKJmpSjsEKgY8"))
        contact.pack(pady=6, ipadx=4)
        self._bind_hint(contact, "menu.hint.contact")
        quit_btn = ttk.Button(center, text=t("menu.exit"), style="Menu.TButton",
                              width=26, command=self.app.root.destroy)
        quit_btn.pack(pady=6, ipadx=4)
        self._bind_hint(quit_btn, "menu.hint.exit")

        self._place_links(center, p)
        self._place_hint_area(center, p)

    def _place_links(self, parent, p) -> None:
        """Square author-link buttons: YouTube / Ko-fi / GitHub."""
        row = ttk.Frame(parent, style="TFrame")
        row.pack(pady=(18, 0))
        for file_name, hint_key, url in _LINKS:
            photo = self._load_icon(Paths.IMAGES / file_name)
            if photo is None:
                continue
            self._icon_refs.append(photo)
            btn = tk.Button(row, image=photo, bd=0, relief="flat",
                            cursor="hand2", bg=p.bg, activebackground=p.surface,
                            width=_LINK_ICON_SIZE[0] + 12,
                            height=_LINK_ICON_SIZE[1] + 12,
                            command=lambda u=url: webbrowser.open(u))
            btn.pack(side="left", padx=6)
            self._bind_hint(btn, hint_key)

    def _load_icon(self, path) -> ImageTk.PhotoImage | None:
        try:
            img = Image.open(path).convert("RGBA")
            img.thumbnail(_LINK_ICON_SIZE, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _place_hint_area(self, parent, p) -> None:
        """Fixed-height hover-hint area (empty until a button is hovered)."""
        box = ttk.Frame(parent, style="TFrame", height=64, width=440)
        box.pack(pady=(12, 0))
        box.pack_propagate(False)
        self._hint = ttk.Label(box, text="", style="Muted.TLabel",
                               wraplength=420, justify="center",
                               anchor="center")
        self._hint.pack(fill="both", expand=True)

    def _bind_hint(self, widget, key: str) -> None:
        widget.bind("<Enter>", lambda _e: self._hint.configure(
            text=self.app.t(key)), add=True)
        widget.bind("<Leave>", lambda _e: self._hint.configure(text=""),
                    add=True)

    def _place_logo(self, parent, t) -> None:
        if Paths.LOGO.exists():
            try:
                img = Image.open(Paths.LOGO)
                img.thumbnail((360, 200), Image.Resampling.LANCZOS)
                self._logo_ref = ImageTk.PhotoImage(img)
                ttk.Label(parent, image=self._logo_ref, style="TLabel").pack(pady=(0, 16))
                return
            except Exception:
                pass
        ttk.Label(parent, text="ANKA", font=FONT_TITLE, style="Title.TLabel").pack(pady=(0, 16))
