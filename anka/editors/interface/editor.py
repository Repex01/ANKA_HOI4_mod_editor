"""Interface editor — HOI4 GUI modding in three tabs.

* **GUI designer** (`gui_tab`): WYSIWYG canvas over ``interface/*.gui`` windows.
* **Sprites** (`gfx_tab`): every ``.gfx`` entry kind with schema-driven forms.
* **Scripted GUIs** (`sgui_tab`): ``common/scripted_guis`` logic bound to windows.

The module hosts the shared `InterfaceService` (documents, sprite catalog,
fonts, window index) and fans save/leave out to whichever tabs were built.
Tabs are created lazily on first visit; the sprite catalog is warmed on a
background thread at build time.
"""
from __future__ import annotations

import threading
from tkinter import ttk

from ...services._locutil import LocCatalog
from ...services.interface_service import InterfaceService
from ..base import EditorModule, EditorRegistry


@EditorRegistry.register
class InterfaceEditor(EditorModule):
    id = "interface"
    name_key = "editors.interface.name"
    desc_key = "editors.interface.desc"
    order = 85

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = InterfaceService(context)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        # Widget text/tooltips reference loc keys from anywhere in vanilla, so
        # the whole vanilla index is needed (filter "" matches every file);
        # it is warmed on the background thread together with the catalogs.
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="",
                              default_pattern="anka_interface_l_{lang}.yml",
                              dep_roots=context.dependency_paths)
        self._ready = threading.Event()
        self._tabs: dict[str, object] = {}

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        threading.Thread(target=self._warm, daemon=True).start()
        self._nb = ttk.Notebook(parent)
        self._pages: dict[str, ttk.Frame] = {}
        for key in ("gui", "gfx", "sgui"):
            page = ttk.Frame(self._nb, style="TFrame", padding=(0, 8, 0, 0))
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            self._nb.add(page, text=self.t(f"interface.tab.{key}"))
            self._pages[key] = page
        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._build_tab("gui")
        return self._nb

    def _warm(self) -> None:
        try:
            self.service.warm()
            self.loc.get("CLOSE", self.loc_language)   # build the loc index
        except Exception:
            pass
        finally:
            self._ready.set()

    def resolver_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def resolver(self):
        return self.service.resolver()

    def _on_tab_changed(self, _event=None) -> None:
        index = self._nb.index(self._nb.select())
        key = ("gui", "gfx", "sgui")[index]
        for tab in self._tabs.values():
            tab.flush_pending()
        self._build_tab(key)

    def _build_tab(self, key: str) -> None:
        if key in self._tabs:
            return
        page = self._pages[key]
        if key == "gfx":
            from .gfx_tab import SpritesTab
            tab = SpritesTab(page, self)
        elif key == "gui":
            from .gui_tab import GuiDesignerTab
            tab = GuiDesignerTab(page, self)
        else:
            from .sgui_tab import ScriptedGuiTab
            tab = ScriptedGuiTab(page, self)
        tab.grid(row=0, column=0, sticky="nsew")
        self._tabs[key] = tab

    def open_in_designer(self, window_name: str) -> None:
        """Jump to the GUI tab with the named window loaded (used by the
        scripted-GUIs inspector)."""
        info = self.service.window_index().get(window_name)
        if info is None:
            return
        self._build_tab("gui")
        self._nb.select(0)
        tab = self._tabs.get("gui")
        if tab is not None:
            tab.open_by_ref(info.ref, info.window_index)

    # ----------------------------------------------------------- owner protocol
    def loc_get(self, key: str, language: str) -> str:
        return self.loc.get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.loc.set(key, language, text)

    def loc_has(self, key: str, language: str) -> bool:
        """Is `key` a known localisation key (in `language`, else english)?"""
        if not key or not self._ready.is_set():
            return False
        return (self.loc.has(key, language)
                or (language != "english" and self.loc.has(key, "english")))

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        return []

    # ------------------------------------------------------------------ saving
    def save_all(self) -> None:
        for tab in self._tabs.values():
            tab.save_all()

    def on_leave(self) -> None:
        self.save_all()
