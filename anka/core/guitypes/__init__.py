"""HOI4 interface (.gfx / .gui / scripted_guis) format layer — tk-free.

`schema` declares every widget/sprite type and attribute a modder can use
(driving inspector forms, palettes and validation); `views` wraps parsed PDX
blocks into typed objects (`GuiNode`, `GuiDoc`, `SpriteView`, `SguiView`).
"""
from .schema import (
    AttrKind,
    AttrSpec,
    WidgetSpec,
    SpriteKindSpec,
    WIDGETS,
    SPRITE_KINDS,
    ANIMATION_ATTRS,
    ORIENTATIONS,
    TEXT_FORMATS,
    CONTEXT_TYPES,
    PARENT_WINDOW_TOKENS,
    SGUI_SCALARS,
    SGUI_SCRIPTS,
    widget_spec,
    sprite_kind,
)
from .views import GuiNode, GuiDoc, SpriteView, SguiView

__all__ = [
    "AttrKind", "AttrSpec", "WidgetSpec", "SpriteKindSpec",
    "WIDGETS", "SPRITE_KINDS", "ANIMATION_ATTRS",
    "ORIENTATIONS", "TEXT_FORMATS", "CONTEXT_TYPES", "PARENT_WINDOW_TOKENS",
    "SGUI_SCALARS", "SGUI_SCRIPTS",
    "widget_spec", "sprite_kind",
    "GuiNode", "GuiDoc", "SpriteView", "SguiView",
]
