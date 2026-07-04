"""Compatibility shim: the visual block editor moved to ``editors/common``."""
from ..common.block_editor import (  # noqa: F401
    _CONTAINERS,
    _LIST_ITEM_TYPES,
    _MODIFIER_PARENTS,
    _TOOLTIP_KEYS,
    BlockPickerDialog,
    BlockTreeEditor,
    node_from_catalog,
    value_type_of,
)
