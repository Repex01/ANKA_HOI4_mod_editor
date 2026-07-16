"""Mod editor shell: a sidebar of registered editor modules + a content area.

The shell knows nothing about concrete editors — it iterates `EditorRegistry.all()`,
instantiates each with a shared `ModContext`/`EditorServices`, and mounts the selected
one lazily. New editors appear here automatically once registered.
"""
from __future__ import annotations

from pathlib import Path
from tkinter import ttk

from ...domain.mod import Mod, ModContext
from ...editors import EditorRegistry, EditorServices
from ..widgets.scrollable import ScrollableFrame


class ModEditorScreen(ttk.Frame):
    def __init__(self, master, app, mod: Mod, progress=None):
        """`progress(done, total, label)` — optional loading-screen callback,
        called before each editor module is instantiated and before the first
        module's UI is built (the two slow phases of opening a mod)."""
        super().__init__(master, style="TFrame")
        self.app = app
        self.mod = mod
        self.context = ModContext(
            mod=mod,
            game_path=Path(app.settings.current.game_path),
            dependencies=app.repo.resolve_dependencies(mod),
        )
        self.services = EditorServices(t=app.t, theme=app.theme, settings=app.settings)

        classes = EditorRegistry.all()
        total = len(classes) + 1                 # +1 for building the first editor UI
        self._modules = []
        for i, cls in enumerate(classes):
            if progress is not None:
                progress(i, total, app.t(cls.name_key))
            self._modules.append(cls(self.context, self.services))
        self._built: dict[str, ttk.Widget] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._active: str | None = None
        if progress is not None:
            progress(total - 1, total, "")
        self._build()

    def _build(self) -> None:
        t = self.app.t
        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=20, pady=(16, 8))
        ttk.Button(header, text="‹ " + t("common.back"), command=self.app.show_mod_list).pack(side="left")
        ttk.Label(header, text=t("editor.title", name=self.mod.name),
                  style="Title.TLabel").pack(side="left", padx=16)
        ttk.Button(header, text="📂 " + t("editor.open_folder"),
                   command=self._open_mod_folder).pack(side="right")

        body = ttk.Frame(self, style="TFrame")
        body.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        body.columnconfigure(0, weight=0, minsize=150)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(body, style="Sidebar.TFrame", padding=(8, 12))
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ttk.Label(sidebar, text=t("editor.modules"), style="CardMuted.TLabel").pack(
            anchor="w", padx=8, pady=(0, 6))
        # Scrolls when the module list outgrows the window height.
        scroll = ScrollableFrame(sidebar, bg=self.app.palette.surface,
                                 autohide=True, width=150)
        scroll.pack(fill="both", expand=True)
        scroll.body.configure(style="Sidebar.TFrame")
        for module in self._modules:
            label = module.title + ("" if module.implemented else "  ·")
            btn = ttk.Button(scroll.body, text=label, style="Sidebar.TButton",
                             command=lambda m=module: self._select(m))
            btn.pack(fill="x", pady=2)
            self._buttons[module.id] = btn

        self._content = ttk.Frame(body, style="TFrame")
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.rowconfigure(0, weight=1)
        self._content.columnconfigure(0, weight=1)

        if self._modules:
            self._select(self._modules[0])

    def _open_mod_folder(self) -> None:
        """Open the mod's content root in the system file manager."""
        import os
        import subprocess
        import sys
        path = str(self.mod.path)
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError:
            pass

    def _active_module(self):
        return next((m for m in self._modules if m.id == self._active), None)

    def on_leave(self) -> None:
        """Flush the active editor module's pending edits when leaving the editor."""
        self._flush_active()

    def _flush_active(self) -> None:
        active = self._active_module()
        if active is not None:
            hook = getattr(active, "on_leave", None)
            if callable(hook):
                try:
                    hook()
                except Exception:
                    pass

    def _select(self, module) -> None:
        if self._active == module.id:
            return
        self._flush_active()  # auto-save the module we're leaving
        for w in self._built.values():
            w.grid_remove()
        widget = self._built.get(module.id)
        if widget is None:
            widget = module.build(self._content)
            widget.grid(row=0, column=0, sticky="nsew")
            self._built[module.id] = widget
        else:
            widget.grid()
        self._active = module.id
