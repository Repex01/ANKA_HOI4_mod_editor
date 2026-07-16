"""Application controller and single-window screen navigator.

ANKA uses one Tk root and swaps full-window *screens* (main menu, settings, mod list,
mod editor) rather than juggling Toplevels. The controller owns the shared services
(settings, translator, theme, mod repository) and injects them into each screen, so
screens stay thin and decoupled.
"""
from __future__ import annotations

import gc
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from . import __app_name__
from .config.constants import Paths, VERSION
from .config.settings import SettingsService
from .domain.mod import Mod
from .services.mod_repository import ModRepository
from .ui.i18n import Translator
from .ui.theme import ThemeManager
from .ui.widgets.dnd import create_root
from .ui.widgets.mousewheel import disable_form_wheel


class AnkaApp:
    MIN_SIZE = (1000, 680)

    def __init__(self):
        self.settings = SettingsService()
        self.translator = Translator(language=self.settings.current.language)
        self.repo = ModRepository(self.settings.current)

        self.root = create_root()
        # Wheel over a dropdown/spinbox must not silently change its value; the
        # few convenient exceptions opt back in via enable_form_wheel().
        disable_form_wheel(self.root)
        self.root.title(f"{__app_name__} {VERSION}")
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
        # First launch (no settings.json yet): go straight to Settings so the user
        # configures the game / mod paths before anything else.
        if self.settings.is_first_run:
            self.show_settings(first_run=True)
        else:
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
            # Variable traces registered on the Tcl interpreter would otherwise
            # keep the destroyed screen (and its services/caches) alive forever;
            # sweep them and free the old screen's memory right away.
            from .ui.varcleanup import purge_orphan_variable_traces
            purge_orphan_variable_traces()
            gc.collect()
        self._current = screen
        screen.pack(fill="both", expand=True)

    def show_main_menu(self) -> None:
        from .ui.windows.main_menu import MainMenuScreen
        self._swap(MainMenuScreen(self.root, self))

    def show_settings(self, first_run: bool = False) -> None:
        from .ui.windows.settings import SettingsScreen
        self._swap(SettingsScreen(self.root, self, first_run=first_run))

    def show_mod_list(self) -> None:
        from .ui.windows.mod_list import ModListScreen
        self._swap(ModListScreen(self.root, self))

    def show_mod_editor(self, mod: Mod) -> None:
        from .ui.windows.mod_editor import ModEditorScreen
        self.settings.add_recent_mod(mod.id)
        # Opening a mod instantiates every editor module (file scans, caches) —
        # show a loading screen with a progress bar while that happens.
        loading = self._show_loading_screen(mod.name)
        try:
            screen = ModEditorScreen(self.root, self, mod, progress=loading.step)
        except Exception:
            self.show_mod_list()      # don't leave the user stuck on the loader
            raise
        self._swap(screen)

    def _show_loading_screen(self, mod_name: str) -> "_LoadingScreen":
        screen = _LoadingScreen(self.root, self, mod_name)
        self._swap(screen)
        self.root.update()            # paint it before the heavy work starts
        return screen

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


class _LoadingScreen(ttk.Frame):
    """Full-window 'opening mod' screen: mod name + progress bar + current step."""

    def __init__(self, master, app, mod_name: str):
        super().__init__(master, style="TFrame")
        box = ttk.Frame(self, style="Card.TFrame", padding=32)
        box.place(relx=0.5, rely=0.45, anchor="center")
        ttk.Label(box, text=app.t("editor.loading", name=mod_name),
                  style="Heading.TLabel").pack(pady=(0, 14))
        self._bar = ttk.Progressbar(box, mode="determinate", maximum=100,
                                    length=380)
        self._bar.pack()
        self._status = ttk.Label(box, text="", style="CardMuted.TLabel")
        self._status.pack(pady=(10, 0))

    def step(self, done: int, total: int, label: str) -> None:
        """Progress callback for `ModEditorScreen` (runs on the Tk main thread)."""
        self._bar.configure(value=round(done / max(total, 1) * 100))
        self._status.configure(text=label)
        self.update_idletasks()
