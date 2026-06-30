"""Focus / idea icon generation with automatic .gfx registration.

Adding a focus icon must (1) place a DDS under ``gfx/interface/goals`` and (2) declare
a ``GFX_focus_<name>`` sprite in an ``interface/*.gfx`` file. This service does both in
one call, fulfilling ANKA's "drop a png, get a working asset" promise.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ...config.constants import (
    FOCUS_ICON_SIZE,
    GAME_DIRS,
    IDEA_ICON_SIZE,
    LEADER_PORTRAIT_SIZE,
)
from ..gfx import SpriteRegistry
from .converter import ImageConverter


class IconService:
    def __init__(self, mod_root: str | Path):
        self.mod_root = Path(mod_root)

    # --- focuses ---------------------------------------------------------
    def add_focus_icon(
        self,
        source: str | Path | Image.Image,
        focus_name: str,
        gfx_file: str = "anka_focuses.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_focus_<focus_name>`` and register it. Returns (dds, gfx)."""
        sprite = f"GFX_focus_{focus_name}"
        rel_texture = f"{GAME_DIRS.GFX_GOALS}/{focus_name}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file, FOCUS_ICON_SIZE, compressed)

    # --- ideas / national spirits ---------------------------------------
    def add_idea_icon(
        self,
        source: str | Path | Image.Image,
        idea_name: str,
        gfx_file: str = "anka_ideas.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path]:
        sprite = f"GFX_idea_{idea_name}"
        rel_texture = f"{GAME_DIRS.GFX_IDEAS}/{idea_name}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file, IDEA_ICON_SIZE, compressed)

    # --- leader portraits ------------------------------------------------
    def add_leader_portrait(
        self,
        source: str | Path | Image.Image,
        char_id: str,
        tag: str,
        gfx_file: str = "anka_portraits.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path, str]:
        """Generate ``GFX_portrait_<char_id>`` (156x210 DDS) and register it.

        Returns (dds_path, gfx_path, sprite_name)."""
        return self.add_character_portrait(source, char_id, tag, "civilian", gfx_file, compressed)

    def add_character_portrait(
        self,
        source: str | Path | Image.Image,
        char_id: str,
        tag: str,
        category: str = "civilian",
        gfx_file: str = "anka_portraits.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path, str]:
        """Generate a 156x210 portrait for a character/category and register its sprite.

        Civilian uses ``GFX_portrait_<id>``; army/navy append the category so a character
        can carry distinct civilian/military art. Returns (dds, gfx, sprite_name)."""
        suffix = "" if category == "civilian" else f"_{category}"
        sprite = f"GFX_portrait_{char_id}{suffix}"
        rel_texture = f"{GAME_DIRS.GFX_LEADERS}/{tag}/portrait_{char_id}{suffix}.dds"
        dds, gfx = self._add_icon(source, sprite, rel_texture, gfx_file, LEADER_PORTRAIT_SIZE, compressed)
        return dds, gfx, sprite

    # --- shared ----------------------------------------------------------
    def _add_icon(self, source, sprite, rel_texture, gfx_file, size, compressed):
        img = source if isinstance(source, Image.Image) else ImageConverter.load(source)
        dds_path = ImageConverter.save_dds(img, self.mod_root / rel_texture, size, compressed)
        gfx_path = self.mod_root / GAME_DIRS.INTERFACE / gfx_file
        registry = SpriteRegistry(gfx_path)
        registry.register(sprite, rel_texture).save()
        return dds_path, gfx_path
