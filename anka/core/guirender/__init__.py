"""Pillow-based WYSIWYG renderer for HOI4 ``.gui`` windows — tk-free.

`layout` solves element rectangles (orientation/origo/percent/negative
offsets) and inverts drags back into ``position`` values; `sprites` decodes
and slices ``.gfx`` textures; `text` approximates HOI4 bitmap fonts with
system truetype fonts; `renderer` composes a window into one RGBA frame plus
a hit-test map.
"""
from .layout import Rect, LayoutSolver
from .sprites import SpriteImageCache
from .text import FontProvider
from .renderer import GuiRenderer, RenderProblem, RenderResult

__all__ = [
    "Rect", "LayoutSolver", "SpriteImageCache", "FontProvider",
    "GuiRenderer", "RenderProblem", "RenderResult",
]
