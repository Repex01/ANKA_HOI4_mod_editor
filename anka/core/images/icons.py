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
from PIL import ImageFilter

from .converter import ImageConverter

# How much of a photo the small (advisor) portrait keeps, measured from the top.
# The game draws its own frame around these, so no template art is needed — only
# a tighter crop than the large portrait.
_SMALL_KEEP_TOP = 0.70
# Share of the tile left as a margin around a small portrait. The game shows
# advisor art inside a border, not edge to edge, so filling the tile makes the
# face look far too big next to the vanilla ministers.
_SMALL_INSET = 0.18

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
        # Portraits keep their aspect ratio (centre-crop) — stretching a photo to
        # 156x210 squashes the face; icons/flags below still use the plain resize.
        # The small portrait is nearly square and shows head and shoulders, so it
        # is taken from the upper part of the same photo.
        small = size == "small"
        dds, gfx = self._add_icon(source, sprite, rel_texture, gfx_file, dims,
                                  compressed, crop=True,
                                  keep_top=_SMALL_KEEP_TOP if small else 1.0,
                                  inset=_SMALL_INSET if small else 0.0)
        return dds, gfx, sprite

    @staticmethod
    def compose_into_frame(source, frame_path) -> Image.Image:
        """Put a photo behind a frame image, showing through its opening.

        A small-portrait frame is the base game's artwork — photo card, border
        and paper note — with the middle left transparent. The picture goes
        behind it and shows through that opening; the frame itself is never
        touched, so the result keeps the original look exactly.

        The opening is found by flooding outwards from the centre across
        transparent pixels, which distinguishes it from the transparent area
        *around* the card.
        """
        frame = ImageConverter.load(frame_path).convert("RGBA")
        photo = (source if isinstance(source, Image.Image)
                 else ImageConverter.load(source)).convert("RGBA")
        w, h = frame.size
        alpha = frame.split()[3].load()

        # Mark the transparent area that touches the image border — that is the
        # space *around* the card. Whatever transparency is left over is the
        # opening. Flooding from the centre would fail whenever something opaque
        # sits there, which is exactly the case when a note covers the middle.
        outside = Image.new("L", (w, h), 0)
        op = outside.load()
        stack = []
        for x in range(w):
            for y in (0, h - 1):
                if alpha[x, y] <= 40 and not op[x, y]:
                    op[x, y] = 255
                    stack.append((x, y))
        for y in range(h):
            for x in (0, w - 1):
                if alpha[x, y] <= 40 and not op[x, y]:
                    op[x, y] = 255
                    stack.append((x, y))
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and not op[nx, ny] \
                        and alpha[nx, ny] <= 40:
                    op[nx, ny] = 255
                    stack.append((nx, ny))

        hole = Image.new("L", (w, h), 0)
        hp = hole.load()
        for y in range(h):
            for x in range(w):
                if alpha[x, y] <= 40 and not op[x, y]:
                    hp[x, y] = 255

        box = hole.getbbox()
        if box is None:
            return ImageConverter.fit(photo, (w, h), crop=True,
                                      keep_top=_SMALL_KEEP_TOP)
        bw, bh = box[2] - box[0], box[3] - box[1]

        # Head and shoulders, then cover the opening without distorting.
        if _SMALL_KEEP_TOP < 1.0:
            photo = photo.crop((0, 0, photo.width,
                                max(1, int(photo.height * _SMALL_KEEP_TOP))))
        scale = max(bw / photo.width, bh / photo.height)
        scaled = photo.resize((max(1, round(photo.width * scale)),
                               max(1, round(photo.height * scale))),
                              Image.Resampling.LANCZOS)
        left = max(0, (scaled.width - bw) // 2)
        scaled = scaled.crop((left, 0, left + bw, bh))

        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.paste(scaled, (box[0], box[1]))
        out.paste(layer, (0, 0), hole)            # picture only inside the opening
        out.alpha_composite(frame)                # frame stays exactly as it is
        return out

    def override_portrait(
        self,
        source,
        sprite: str,
        tag: str,
        char_id: str,
        size: str = "large",
        gfx_file: str = "zzz_anka_portrait_overrides.gfx",
        frame_from=None,
    ) -> tuple[Path, Path, str]:
        """Point an EXISTING sprite at a new texture, without touching common/.

        The regular portrait import registers ``GFX_portrait_<char_id>`` and then
        has to write that name into the character definition — which changes the
        game checksum and disables achievements. Redefining the sprite the base
        game already references achieves the same visual result while only
        touching gfx/ and interface/, both of which the checksum ignores. The
        ``zzz_`` filename makes the override win over DLC definitions.
        """
        suffix = "_small" if size == "small" else ""
        rel_texture = (f"{GAME_DIRS.GFX_LEADERS}/{tag}/"
                       f"anka_{char_id}{suffix}.dds")
        dims = SMALL_PORTRAIT_SIZE if size == "small" else LEADER_PORTRAIT_SIZE
        small = size == "small"
        if small and frame_from:
            # Reuse the existing artwork: the composite already has the right
            # size, so no further cropping or insetting.
            composed = self.compose_into_frame(source, frame_from)
            dds, gfx = self._add_icon(composed, sprite, rel_texture, gfx_file,
                                      None, False)
            return dds, gfx, sprite
        dds, gfx = self._add_icon(source, sprite, rel_texture, gfx_file, dims,
                                  False, crop=True,
                                  keep_top=_SMALL_KEEP_TOP if small else 1.0,
                                  inset=_SMALL_INSET if small else 0.0)
        return dds, gfx, sprite

    def restore_vanilla_sprite(self, sprite: str) -> tuple[int, list[str]]:
        """Undo a portrait override: drop the mod's redefinition of `sprite`.

        Every .gfx in the mod is searched, the matching SpriteType removed, and
        the texture it pointed at deleted when no other sprite still uses it and
        the file lives inside the mod. A .gfx left without sprites is removed as
        well, so an undone override leaves no trace and the base game's
        definition applies again.

        Returns (number of definitions removed, names of files touched).
        """
        interface = self.mod_root / GAME_DIRS.INTERFACE
        if not interface.is_dir():
            return 0, []

        textures: list[Path] = []
        removed = 0
        touched: list[str] = []
        for gfx_path in sorted(interface.rglob("*.gfx")):
            registry = SpriteRegistry(gfx_path)
            entry = registry.find(sprite)
            if entry is None:
                continue
            texture = (entry.get_scalar("texturefile") or "").strip('"')
            if texture:
                textures.append(self.mod_root / texture.replace("\\", "/"))
            if registry.unregister(sprite):
                removed += 1
                touched.append(gfx_path.name)
                if registry.is_empty():
                    gfx_path.unlink(missing_ok=True)
                else:
                    registry.save()

        # Delete textures that nothing in the mod references any more.
        still_used: set[str] = set()
        for gfx_path in sorted(interface.rglob("*.gfx")):
            try:
                text = gfx_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            still_used.add(text)
        for tex in textures:
            try:
                rel = tex.relative_to(self.mod_root).as_posix()
            except ValueError:
                continue                      # outside the mod: never touch it
            if any(rel in text for text in still_used):
                continue
            tex.unlink(missing_ok=True)
        return removed, touched

    # --- shared ----------------------------------------------------------
    def _add_icon(self, source, sprite, rel_texture, gfx_file, size, compressed,
                  crop: bool = False, keep_top: float = 1.0,
                  inset: float = 0.0):
        img = source if isinstance(source, Image.Image) else ImageConverter.load(source)
        dds_path = ImageConverter.save_dds(img, self.mod_root / rel_texture, size,
                                           compressed, crop, keep_top, inset)
        gfx_path = self.mod_root / GAME_DIRS.INTERFACE / gfx_file
        registry = SpriteRegistry(gfx_path)
        registry.register(sprite, rel_texture).save()
        return dds_path, gfx_path
