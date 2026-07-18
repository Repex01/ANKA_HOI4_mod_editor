"""Focus / idea icon generation with automatic .gfx registration.

Adding a focus icon must (1) place a DDS under ``gfx/interface/goals`` and (2) declare
a ``GFX_focus_<name>`` sprite in an ``interface/*.gfx`` file. This service does both in
one call, fulfilling ANKA's "drop a png, get a working asset" promise.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ...config.constants import (
    DECISION_ICON_SIZE,
    FOCUS_ICON_SIZE,
    GAME_DIRS,
    IDEA_ICON_SIZE,
    LEADER_PORTRAIT_SIZE,
    SMALL_PORTRAIT_SIZE,
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
        resize: bool = True,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_focus_<focus_name>`` and register it. Returns (dds, gfx).

        With ``resize=False`` the source image is written at its original dimensions
        instead of the standard focus-icon size.

        Alongside the base sprite a ``GFX_focus_<name>_shine`` is registered in
        ``anka_focuses_shine.gfx`` (mirroring vanilla ``goals_shine.gfx``) — without
        it the in-game "focus available" glow animation is missing."""
        sprite = f"GFX_focus_{focus_name}"
        rel_texture = f"{GAME_DIRS.GFX_GOALS}/{focus_name}.dds"
        result = self._add_icon(source, sprite, rel_texture, gfx_file,
                                FOCUS_ICON_SIZE if resize else None, compressed)
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

    # --- technologies -------------------------------------------------------
    def add_tech_icon(
        self,
        source: str | Path | Image.Image,
        tech_id: str,
        gfx_file: str = "anka_technologies.gfx",
        compressed: bool = False,
        max_size: tuple[int, int] | None = (120, 60),
    ) -> tuple[Path, Path]:
        """Generate ``GFX_<tech_id>_medium`` and register it. Returns (dds, gfx).

        Vanilla tech icons have no canonical size (103×50, 94×45, 143×55 …), so
        the source dimensions are kept; ``max_size`` only downscales oversized
        images, preserving aspect ratio."""
        img = source if isinstance(source, Image.Image) else ImageConverter.load(source)
        if max_size is not None and (img.width > max_size[0] or img.height > max_size[1]):
            img = img.copy()
            img.thumbnail(max_size, Image.LANCZOS)
        sprite = f"GFX_{tech_id}_medium"
        rel_texture = f"{GAME_DIRS.GFX_TECHNOLOGIES}/{tech_id}_medium.dds"
        return self._add_icon(img, sprite, rel_texture, gfx_file, None, compressed)

    def add_tech_tab_icon(
        self,
        source_normal: str | Path | Image.Image,
        folder_id: str,
        source_active: str | Path | Image.Image | None = None,
        gfx_file: str = "anka_technologies.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path]:
        """Generate the two-frame ``GFX_<folder_id>_tab`` sprite (frame 1 = folder
        open, frame 2 = closed, side by side). When no ``source_active`` is given,
        the second frame is a darkened copy of the first. Returns (dds, gfx)."""
        normal = (source_normal if isinstance(source_normal, Image.Image)
                  else ImageConverter.load(source_normal)).convert("RGBA")
        if source_active is None:
            from PIL import ImageEnhance
            active = ImageEnhance.Brightness(normal).enhance(0.6)
        else:
            active = (source_active if isinstance(source_active, Image.Image)
                      else ImageConverter.load(source_active)).convert("RGBA")
            if active.size != normal.size:
                active = active.resize(normal.size, Image.LANCZOS)
        strip = Image.new("RGBA", (normal.width * 2, normal.height), (0, 0, 0, 0))
        strip.paste(normal, (0, 0))
        strip.paste(active, (normal.width, 0))
        sprite = f"GFX_{folder_id}_tab"
        rel_texture = f"{GAME_DIRS.GFX_TECHNOLOGIES}/{folder_id}_tab.dds"
        dds_path = ImageConverter.save_dds(strip, self.mod_root / rel_texture,
                                           None, compressed)
        gfx_path = self.mod_root / GAME_DIRS.INTERFACE / gfx_file
        registry = SpriteRegistry(gfx_path)
        registry.register(sprite, rel_texture, noOfFrames=2).save()
        return dds_path, gfx_path

    # --- equipment -----------------------------------------------------------
    def add_equipment_icon(
        self,
        source: str | Path | Image.Image,
        picture: str,
        gfx_file: str = "anka_equipment.gfx",
        compressed: bool = False,
        max_size: tuple[int, int] | None = (120, 60),
    ) -> tuple[Path, Path]:
        """Generate ``GFX_<picture>_medium`` — the sprite the game shows for
        equipment whose ``picture = <picture>`` (or whose id matches). Vanilla
        equipment art has no canonical size, so the source dimensions are kept;
        ``max_size`` only downscales oversized images. Returns (dds, gfx)."""
        img = source if isinstance(source, Image.Image) else ImageConverter.load(source)
        if max_size is not None and (img.width > max_size[0] or img.height > max_size[1]):
            img = img.copy()
            img.thumbnail(max_size, Image.LANCZOS)
        sprite = f"GFX_{picture}_medium"
        rel_texture = f"{GAME_DIRS.GFX_ARCHETYPES}/{picture}.dds"
        return self._add_icon(img, sprite, rel_texture, gfx_file, None, compressed)

    # --- ideologies -----------------------------------------------------------
    def add_ideology_group_icon(
        self,
        source: str | Path | Image.Image,
        ideology: str,
        gfx_file: str = "anka_ideologies.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_ideology_<ideology>_group`` (politics-view group icon).
        The source dimensions are kept. Returns (dds, gfx)."""
        sprite = f"GFX_ideology_{ideology}_group"
        rel_texture = f"{GAME_DIRS.GFX_IDEOLOGIES}/{ideology}_group.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file,
                              None, compressed)

    def add_ideology_type_icon(
        self,
        source: str | Path | Image.Image,
        itype: str,
        gfx_file: str = "anka_ideologies.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_ideology_<type>`` (leader sub-ideology icon).
        The source dimensions are kept. Returns (dds, gfx)."""
        sprite = f"GFX_ideology_{itype}"
        rel_texture = f"{GAME_DIRS.GFX_IDEOLOGIES}/{itype}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file,
                              None, compressed)

    # --- decisions --------------------------------------------------------
    def add_decision_icon(
        self,
        source: str | Path | Image.Image,
        decision_name: str,
        gfx_file: str = "anka_decisions.gfx",
        compressed: bool = False,
        resize: bool = True,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_decision_<decision_name>`` and register it (no shine —
        decisions have none). ``resize=False`` keeps the source dimensions."""
        sprite = f"GFX_decision_{decision_name}"
        rel_texture = f"{GAME_DIRS.GFX_DECISIONS}/{decision_name}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file,
                              DECISION_ICON_SIZE if resize else None, compressed)

    def add_decision_category_icon(
        self,
        source: str | Path | Image.Image,
        category_name: str,
        gfx_file: str = "anka_decisions.gfx",
        compressed: bool = False,
        resize: bool = True,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_decision_category_<name>`` (the small tab icon)."""
        sprite = f"GFX_decision_category_{category_name}"
        rel_texture = f"{GAME_DIRS.GFX_DECISIONS}/category_{category_name}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file,
                              DECISION_ICON_SIZE if resize else None, compressed)

    def add_decision_category_picture(
        self,
        source: str | Path | Image.Image,
        category_name: str,
        gfx_file: str = "anka_decisions.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_decision_cat_<name>`` (the wide category banner).
        The source dimensions are kept — vanilla banners vary."""
        sprite = f"GFX_decision_cat_{category_name}"
        rel_texture = f"{GAME_DIRS.GFX_DECISIONS}/cat_{category_name}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file,
                              None, compressed)

    # --- event pictures ----------------------------------------------------
    def add_event_picture(
        self,
        source: str | Path | Image.Image,
        name: str,
        gfx_file: str = "anka_events.gfx",
        compressed: bool = False,
    ) -> tuple[Path, Path]:
        """Generate ``GFX_report_event_<name>`` (DDS under gfx/event_pictures)
        and register it. The original image size is kept — vanilla event
        pictures vary (355×140 is only the typical window size) and the game
        renders any dimensions. Returns (dds, gfx)."""
        sprite = f"GFX_report_event_{name}"
        rel_texture = f"{GAME_DIRS.GFX_EVENTS}/{name}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file,
                              None, compressed)

    # --- ideas / national spirits ---------------------------------------
    def add_idea_icon(
        self,
        source: str | Path | Image.Image,
        idea_name: str,
        gfx_file: str = "anka_ideas.gfx",
        compressed: bool = False,
        resize: bool = True,
    ) -> tuple[Path, Path]:
        sprite = f"GFX_idea_{idea_name}"
        rel_texture = f"{GAME_DIRS.GFX_IDEAS}/{idea_name}.dds"
        return self._add_icon(source, sprite, rel_texture, gfx_file,
                              IDEA_ICON_SIZE if resize else None, compressed)

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
        size: str = "large",
    ) -> tuple[Path, Path, str]:
        """Generate a portrait for a character/category and register its sprite.

        Civilian uses ``GFX_portrait_<id>``; army/navy append the category so a character
        can carry distinct civilian/military art. ``size="large"`` is the 156x210 leader
        portrait; ``size="small"`` is the 65x67 advisor portrait (vanilla standard),
        registered as ``GFX_portrait_<id>[_<category>]_small``.
        Returns (dds, gfx, sprite_name)."""
        suffix = "" if category == "civilian" else f"_{category}"
        if size == "small":
            suffix += "_small"
        sprite = f"GFX_portrait_{char_id}{suffix}"
        rel_texture = f"{GAME_DIRS.GFX_LEADERS}/{tag}/portrait_{char_id}{suffix}.dds"
        dims = SMALL_PORTRAIT_SIZE if size == "small" else LEADER_PORTRAIT_SIZE
        dds, gfx = self._add_icon(source, sprite, rel_texture, gfx_file, dims, compressed)
        return dds, gfx, sprite

    # --- shared ----------------------------------------------------------
    def _add_icon(self, source, sprite, rel_texture, gfx_file, size, compressed):
        img = source if isinstance(source, Image.Image) else ImageConverter.load(source)
        dds_path = ImageConverter.save_dds(img, self.mod_root / rel_texture, size, compressed)
        gfx_path = self.mod_root / GAME_DIRS.INTERFACE / gfx_file
        registry = SpriteRegistry(gfx_path)
        registry.register(sprite, rel_texture).save()
        return dds_path, gfx_path
