"""Main menu: logo + Edit mod / Settings / Exit.

"Edit mod" is disabled until the required paths are configured (per the spec), with an
inline hint pointing the user to settings.
"""
from __future__ import annotations

from tkinter import ttk

from PIL import Image, ImageTk

from ...config.constants import Paths
from ..theme import FONT_TITLE


class MainMenuScreen(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="TFrame")
        self.app = app
        self._logo_ref = None
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
        if not configured:
            edit.state(["disabled"])
            ttk.Label(center, text=t("menu.paths_required"), style="Muted.TLabel").pack(pady=(2, 6))

        ttk.Button(center, text=t("menu.settings"), style="Menu.TButton",
                   width=26, command=self.app.show_settings).pack(pady=6, ipadx=4)
        ttk.Button(center, text=t("menu.exit"), style="Menu.TButton",
                   width=26, command=self.app.root.destroy).pack(pady=6, ipadx=4)

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
