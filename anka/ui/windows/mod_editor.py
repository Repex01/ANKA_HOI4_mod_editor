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


class ModEditorScreen(ttk.Frame):
    def __init__(self, master, app, mod: Mod):
        super().__init__(master, style="TFrame")
        self.app = app
        self.mod = mod
        self.context = ModContext(mod=mod, game_path=Path(app.settings.current.game_path))
        self.services = EditorServices(t=app.t, theme=app.theme, settings=app.settings)

        self._modules = [cls(self.context, self.services) for cls in EditorRegistry.all()]
        self._built: dict[str, ttk.Widget] = {}
        self._buttons: dict[str, ttk.Button] = {}
        self._active: str | None = None
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
        body.columnconfigure(0, weight=0, minsize=210)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(body, style="Sidebar.TFrame", padding=(8, 12))
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ttk.Label(sidebar, text=t("editor.modules"), style="CardMuted.TLabel").pack(
            anchor="w", padx=8, pady=(0, 6))
        for module in self._modules:
            label = module.title + ("" if module.implemented else "  ·")
            btn = ttk.Button(sidebar, text=label, style="Sidebar.TButton",
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
