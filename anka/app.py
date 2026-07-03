"""Application controller and single-window screen navigator.

ANKA uses one Tk root and swaps full-window *screens* (main menu, settings, mod list,
mod editor) rather than juggling Toplevels. The controller owns the shared services
(settings, translator, theme, mod repository) and injects them into each screen, so
screens stay thin and decoupled.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from . import __app_name__, __version__
from .config.constants import Paths
from .config.settings import SettingsService
from .domain.mod import Mod
from .services.mod_repository import ModRepository
from .ui.i18n import Translator
from .ui.theme import ThemeManager
from .ui.widgets.dnd import create_root


class AnkaApp:
    MIN_SIZE = (1000, 680)

    def __init__(self):
        self.settings = SettingsService()
        self.translator = Translator(language=self.settings.current.language)
        self.repo = ModRepository(self.settings.current)

        self.root = create_root()
        self.root.title(f"{__app_name__} {__version__}")
        self.root.minsize(*self.MIN_SIZE)
        self._center(*self.MIN_SIZE)      # sane restore-size before maximizing
        self._maximize()
        self._icon_ref = None
        self._load_icon()

        self.theme = ThemeManager(self.root)
        self.theme.apply(self.settings.current.theme)
        self.root.configure(bg=self.theme.palette.bg)

        self._current: ttk.Frame | None = None
        self.settings.subscribe(self._on_settings_changed)

    # --- lifecycle -------------------------------------------------------
    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_main_menu()
        self.root.mainloop()

    def _on_close(self) -> None:
        if self._current is not None:
            on_leave = getattr(self._current, "on_leave", None)
            if callable(on_leave):
                try:
                    on_leave()
                except Exception:
                    pass
        self.root.destroy()

    def _on_settings_changed(self, settings) -> None:
        # Re-apply look & language, refresh the repo's settings reference, rebuild.
        self.translator.set_language(settings.language, notify=False)
        self.theme.apply(settings.theme)
        self.root.configure(bg=self.theme.palette.bg)
        self.repo = ModRepository(settings)

    # --- navigation ------------------------------------------------------
    def _swap(self, screen: ttk.Frame) -> None:
        if self._current is not None:
            # Auto-save any pending edits before leaving the current screen.
            on_leave = getattr(self._current, "on_leave", None)
            if callable(on_leave):
                try:
                    on_leave()
                except Exception:
                    pass
            self._current.destroy()
        self._current = screen
        screen.pack(fill="both", expand=True)

    def show_main_menu(self) -> None:
        from .ui.windows.main_menu import MainMenuScreen
        self._swap(MainMenuScreen(self.root, self))

    def show_settings(self) -> None:
        from .ui.windows.settings import SettingsScreen
        self._swap(SettingsScreen(self.root, self))

    def show_mod_list(self) -> None:
        from .ui.windows.mod_list import ModListScreen
        self._swap(ModListScreen(self.root, self))

    def show_mod_editor(self, mod: Mod) -> None:
        from .ui.windows.mod_editor import ModEditorScreen
        self.settings.add_recent_mod(mod.id)
        self._swap(ModEditorScreen(self.root, self, mod))

    # --- helpers ---------------------------------------------------------
    @property
    def t(self) -> Translator:
        return self.translator

    @property
    def palette(self):
        return self.theme.palette

    def _maximize(self) -> None:
        """Start maximized ("zoomed" on Windows; X11 uses the -zoomed attribute)."""
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                pass                      # WM without maximize support: keep centered

    def _center(self, w: int, h: int) -> None:
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{max(0, y - 20)}")

    def _load_icon(self) -> None:
        if Paths.LOGO_SMALL.exists():
            try:
                self._icon_ref = ImageTk.PhotoImage(Image.open(Paths.LOGO_SMALL))
                self.root.iconphoto(True, self._icon_ref)
            except Exception:
                pass
