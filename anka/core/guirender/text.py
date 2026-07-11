"""Approximate HOI4 bitmap fonts with system truetype fonts.

By design (user decision) the editor does not rasterize the game's BMFont
atlases; it maps a font name like ``hoi_18mbs`` / ``garamond_14`` to a system
font at the size embedded in the name. Metrics are therefore approximate —
the designer marks text sizing as such in the UI.
"""
from __future__ import annotations

import re

from PIL import Image, ImageDraw, ImageFont

_SIZE_RE = re.compile(r"(\d{2}|\d)")

# Family hints by font-name prefix; everything falls back to Arial.
_SERIF_PREFIXES = ("garamond", "vic", "typewriter")

_CANDIDATES_SANS = ("arialbd.ttf", "arial.ttf")
_CANDIDATES_SERIF = ("georgia.ttf", "times.ttf", "arial.ttf")


class FontProvider:
    DEFAULT_SIZE = 14

    def __init__(self):
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._measurer = Image.new("RGBA", (1, 1))

    def size_of(self, font_name: str) -> int:
        m = _SIZE_RE.search(font_name or "")
        if not m:
            return self.DEFAULT_SIZE
        size = int(m.group(1))
        return size if 6 <= size <= 60 else self.DEFAULT_SIZE

    def get(self, font_name: str, scale: float = 1.0) -> ImageFont.FreeTypeFont:
        low = (font_name or "").lower()
        size = max(6, int(self.size_of(low) * scale))
        bold = "_bold" in low or low.endswith("b") or "header" in low
        key = (("serif" if low.startswith(_SERIF_PREFIXES) else "sans")
               + ("-b" if bold else ""), size)
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        names = (_CANDIDATES_SERIF if key[0].startswith("serif")
                 else _CANDIDATES_SANS)
        if not bold and names[0].endswith("bd.ttf"):
            names = names[1:]
        font: ImageFont.FreeTypeFont | None = None
        for name in names:
            try:
                font = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        self._fonts[key] = font
        return font

    def measure_text(self, text: str, font_name: str) -> tuple[int, int]:
        if not text:
            return 0, 0
        font = self.get(font_name)
        draw = ImageDraw.Draw(self._measurer)
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
