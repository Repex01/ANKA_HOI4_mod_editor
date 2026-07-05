"""Placeholder editors for sections not yet implemented.

They are real, registered modules so the sidebar, navigation and architecture are
exercised end-to-end; each renders a friendly "under construction" panel. Replacing a
stub with a full editor is a localized change: swap the class body, keep the metadata.
"""
from __future__ import annotations

from tkinter import ttk

from .base import EditorModule, EditorRegistry


class WipEditor(EditorModule):
    implemented = False

    def build(self, parent) -> ttk.Widget:
        frame = ttk.Frame(parent, style="TFrame")
        ttk.Label(frame, text=self.title, style="Title.TLabel").pack(anchor="w", padx=24, pady=(24, 4))
        ttk.Label(frame, text=self.description, style="Muted.TLabel",
                  wraplength=520, justify="left").pack(anchor="w", padx=24)
        badge = ttk.Label(frame, text=f"🛠  {self.t('editors.wip')}", style="Muted.TLabel")
        badge.pack(anchor="w", padx=24, pady=28)
        return frame


@EditorRegistry.register
class DynamicModifiersEditor(WipEditor):
    id = "dynamic_modifiers"
    name_key = "editors.dynamic_modifiers.name"
    desc_key = "editors.dynamic_modifiers.desc"
    order = 70


@EditorRegistry.register
class LocalisationEditor(WipEditor):
    id = "localisation"
    name_key = "editors.localisation.name"
    desc_key = "editors.localisation.desc"
    order = 80
