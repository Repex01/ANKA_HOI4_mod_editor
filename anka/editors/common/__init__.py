"""Shared building blocks for editor modules.

Editors that need script editing / pickers implement a small owner protocol:
``t``, ``palette``, ``loc_language``, ``value_options(vtype)`` and
``loc_get(key, lang)`` / ``loc_set(key, lang, text)`` — see `FocusesEditor` and
`DecisionsEditor` for reference implementations.
"""
from .block_editor import BlockPickerDialog, BlockTreeEditor, node_from_catalog
from .dialogs import (
    BaseDialog,
    IconPickerDialog,
    MultiPickDialog,
    PdxPreviewDialog,
    SinglePickDialog,
    TextPromptDialog,
)
from .script_editor import ScriptEditorDialog

__all__ = [
    "BaseDialog", "TextPromptDialog", "MultiPickDialog", "SinglePickDialog",
    "IconPickerDialog", "PdxPreviewDialog",
    "BlockTreeEditor", "BlockPickerDialog", "node_from_catalog",
    "ScriptEditorDialog",
]
