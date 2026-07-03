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
from ..pdx import Block, Pair, Scalar
from .converter import ImageConverter

# The animated overlay every focus "shine" uses; shipped by the base game, so a mod
# .gfx may reference it by this path without bundling the texture.
_SHINE_OVERLAY = "gfx/interface/goals/shine_overlay.dds"
_SHINE_EFFECT = "gfx/FX/buttonstate.lua"


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
        """Generate ``GFX_focus_<focus_name>`` and register it. Returns (dds, gfx).

        Alongside the base sprite a ``GFX_focus_<name>_shine`` is registered in
        ``anka_focuses_shine.gfx`` (mirroring vanilla ``goals_shine.gfx``) — without
        it the in-game "focus available" glow animation is missing."""
        sprite = f"GFX_focus_{focus_name}"
        rel_texture = f"{GAME_DIRS.GFX_GOALS}/{focus_name}.dds"
        result = self._add_icon(source, sprite, rel_texture, gfx_file,
                                FOCUS_ICON_SIZE, compressed)
        self._register_shine(sprite, rel_texture)
        return result

    def _register_shine(self, sprite: str, rel_texture: str,
                        gfx_file: str = "anka_focuses_shine.gfx") -> Path:
        """Write the two-pass scrolling shine animation for a focus icon."""
        def animation(rotation: float) -> Block:
            anim = Block()
            anim.add("animationmaskfile", Scalar(rel_texture, quoted=True))
            anim.add("animationtexturefile", Scalar(_SHINE_OVERLAY, quoted=True))
            anim.add("animationrotation", Scalar(f"{rotation:.1f}"))
            anim.add("animationlooping", Scalar("no"))
            anim.add("animationtime", Scalar("0.75"))
            anim.add("animationdelay", Scalar("0"))
            anim.add("animationblendmode", Scalar("add", quoted=True))
            anim.add("animationtype", Scalar("scrolling", quoted=True))
            anim.add("animationrotationoffset",
                     Block([Pair("x", Scalar("0.0")), Pair("y", Scalar("0.0"))]))
            anim.add("animationtexturescale",
                     Block([Pair("x", Scalar("1.0")), Pair("y", Scalar("1.0"))]))
            return anim

        shine = Block()
        shine.add("name", Scalar(f"{sprite}_shine", quoted=True))
        shine.add("texturefile", Scalar(rel_texture, quoted=True))
        shine.add("effectFile", Scalar(_SHINE_EFFECT, quoted=True))
        shine.add("animation", animation(-90.0))
        shine.add("animation", animation(90.0))
        shine.add("legacy_lazy_load", Scalar("no"))

        gfx_path = self.mod_root / GAME_DIRS.INTERFACE / gfx_file
        registry = SpriteRegistry(gfx_path)
        registry.register_sprite(shine)
        return registry.save()

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
