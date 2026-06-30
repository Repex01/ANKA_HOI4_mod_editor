"""Editor module contract + registry — the extension point of ANKA.

A new feature (countries, focuses, events, ...) is a subclass of `EditorModule`
decorated with `@EditorRegistry.register`. The mod-editor window asks the registry for
all modules, shows them in a sidebar, and lazily calls `build()` to mount each editor's
UI. Modules depend only on the abstractions handed to them (`ModContext`, `EditorServices`),
never on each other or on the window — so adding one never touches existing code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from tkinter import ttk
from typing import ClassVar

from ..config.settings import SettingsService
from ..domain.mod import ModContext
from ..ui.i18n import Translator
from ..ui.theme import Palette, ThemeManager


@dataclass
class EditorServices:
    """Shared, UI-facing services injected into every editor module."""

    t: Translator
    theme: ThemeManager
    settings: SettingsService


class EditorModule(ABC):
    # --- metadata (override in subclasses) ---
    id: ClassVar[str] = ""
    name_key: ClassVar[str] = ""
    desc_key: ClassVar[str] = ""
    order: ClassVar[int] = 100
    implemented: ClassVar[bool] = True

    def __init__(self, context: ModContext, services: EditorServices):
        self.context = context
        self.services = services

    # convenience accessors -------------------------------------------------
    @property
    def t(self) -> Translator:
        return self.services.t

    @property
    def palette(self) -> Palette:
        return self.services.theme.palette

    @property
    def title(self) -> str:
        return self.t(self.name_key)

    @property
    def description(self) -> str:
        return self.t(self.desc_key)

    @abstractmethod
    def build(self, parent) -> ttk.Widget:
        """Construct and return this editor's root widget inside `parent`."""
        raise NotImplementedError


class EditorRegistry:
    """Global, ordered registry of editor module classes."""

    _modules: list[type[EditorModule]] = []

    @classmethod
    def register(cls, module: type[EditorModule]) -> type[EditorModule]:
        if not module.id:
            raise ValueError(f"{module.__name__} must define a non-empty `id`")
        if any(m.id == module.id for m in cls._modules):
            raise ValueError(f"Duplicate editor id: {module.id!r}")
        cls._modules.append(module)
        return module

    @classmethod
    def all(cls) -> list[type[EditorModule]]:
        return sorted(cls._modules, key=lambda m: (m.order, m.id))
