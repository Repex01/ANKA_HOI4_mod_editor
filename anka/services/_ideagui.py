"""GUI / GFX wiring for creating a new idea category (Ideas editor, step 2).

Creating a *politics-tab* category is more than an ``idea_tags`` entry: the
country-politics view must render it. This module materializes the graphical
side inside the mod (never referencing vanilla textures from a mod ``.gfx`` —
texture paths resolve relative to the content root that owns the ``.gfx``):

* empty-slot sprites ``GFX_idea_slot_<slot>`` — a byte copy of a vanilla slot
  DDS per new slot, registered in ``interface/anka_ideas.gfx``;
* the category icon strip ``gfx/interface/idea_categories.dds`` — rebuilt with
  one extra frame appended on the right (Pillow), and an override
  ``GFX_idea_categories`` sprite with ``noOfFrames`` bumped by one. The engine
  picks a category's frame from the order of politics-tab categories, so a newly
  added category takes the last (new) frame;
* ``interface/countrypoliticsview.gui`` — the per-category ``ideas_grid`` slot
  row (``max_slots = { x = 7 ... }`` in vanilla) widened when a category brings
  more slots than fit.

All operations read the mod's own copy first (so several categories accumulate
on the already-overridden texture / gui) and fall back to vanilla.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from ..config.constants import GAME_DIRS
from ..core.gfx import SpriteRegistry
from ..core.images.converter import ImageConverter
from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ._fsutil import ensure_filename_case

_GFX_FILE = "anka_ideas.gfx"                       # under interface/
_CATEGORIES_TEX = "gfx/interface/idea_categories.dds"
_POLITICS_GUI = "interface/countrypoliticsview.gui"
_POLITICS_GFX = "interface/countrypoliticsview.gfx"
_SLOT_TEMPLATE = "gfx/interface/idea_slot_political_advisor.dds"
_VANILLA_CATEGORY_FRAMES = 6                        # noOfFrames of GFX_idea_categories
_VANILLA_GRID_X = 7                                 # max_slots.x of the ideas_grid


class IdeaGuiAssets:
    """Materialize the graphical side of a new idea category inside the mod."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.mod = Path(ctx.mod.path)
        self.game = Path(ctx.game_path)

    def _gfx_registry(self) -> SpriteRegistry:
        return SpriteRegistry(self.mod / GAME_DIRS.INTERFACE / _GFX_FILE)

    # --- empty-slot sprites --------------------------------------------------
    def add_slot_sprites(self, slots: list[str]) -> list[str]:
        """Copy a vanilla slot DDS into the mod for each new slot and register
        ``GFX_idea_slot_<slot>``. Returns the sprite names created."""
        template = self.game / _SLOT_TEMPLATE
        created: list[str] = []
        reg = self._gfx_registry()
        for slot in slots:
            slot = slot.strip()
            if not slot:
                continue
            sprite = f"GFX_idea_slot_{slot}"
            rel = f"gfx/interface/idea_slot_{slot}.dds"
            dest = self.mod / rel
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                if template.exists():
                    shutil.copyfile(template, dest)
                else:                       # no vanilla template: blank 63x63
                    ImageConverter.save_dds(Image.new("RGBA", (63, 63), (0, 0, 0, 0)),
                                            dest)
            reg.register(sprite, rel)
            created.append(sprite)
        if created:
            reg.save()
        return created

    # --- category icon strip -------------------------------------------------
    def _current_category_texture(self) -> tuple[Path, int]:
        """(texture path, frame count) of the category strip to extend — the
        mod's own override if present, else the vanilla texture."""
        mod_tex = self.mod / _CATEGORIES_TEX
        reg = self._gfx_registry()
        existing = reg.find("GFX_idea_categories")
        if mod_tex.exists() and existing is not None:
            frames = _int(existing.get_scalar("noOfFrames"), _VANILLA_CATEGORY_FRAMES)
            return mod_tex, frames
        return self.game / _CATEGORIES_TEX, _VANILLA_CATEGORY_FRAMES

    def add_category_frame(self, icon_source: str | Path | None) -> int:
        """Append one frame to the category strip (user icon or a copy of the
        last frame) and register the override sprite. Returns the new frame
        count (= the 1-based frame index of the new category)."""
        source_tex, frames = self._current_category_texture()
        with Image.open(source_tex) as im:
            strip = im.convert("RGBA")
        width, height = strip.size
        frame_w = max(1, width // max(1, frames))
        if icon_source is not None:
            with Image.open(icon_source) as im:
                new_frame = im.convert("RGBA").resize((frame_w, height), Image.LANCZOS)
        else:
            new_frame = strip.crop((width - frame_w, 0, width, height))
        canvas = Image.new("RGBA", (width + frame_w, height), (0, 0, 0, 0))
        canvas.paste(strip, (0, 0))
        canvas.paste(new_frame, (width, 0))
        dest = self.mod / _CATEGORIES_TEX
        ImageConverter.save_dds(canvas, dest)
        new_frames = frames + 1
        reg = self._gfx_registry()
        reg.register("GFX_idea_categories", _CATEGORIES_TEX)
        sprite = reg.find("GFX_idea_categories")
        if sprite is not None:
            sprite.set("noOfFrames", Scalar(str(new_frames)))
        reg.save()
        return new_frames

    # --- politics view grid --------------------------------------------------
    def widen_ideas_grid(self, extra_slots: int) -> int | None:
        """Widen the per-category ``ideas_grid`` slot row by `extra_slots`
        columns. Copies the vanilla ``.gui`` into the mod on first use. Returns
        the new ``max_slots.x`` (or None if nothing had to change)."""
        if extra_slots <= 0:
            return None
        mod_gui = self.mod / _POLITICS_GUI
        source = mod_gui if mod_gui.exists() else self.game / _POLITICS_GUI
        if not source.exists():
            return None
        root = parse_file(source)
        grid = _find_ideas_grid(root)
        if grid is None:
            return None
        max_slots = _get_block_ci(grid, "max_slots")
        if max_slots is None:
            return None
        current = _int(max_slots.get_scalar("x"), _VANILLA_GRID_X)
        new_x = current + extra_slots
        max_slots.set("x", Scalar(str(new_x)))
        target = ensure_filename_case(mod_gui)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, target)
        return new_x


# --- helpers ---------------------------------------------------------------
def _int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _get_block_ci(block: Block, key: str) -> Block | None:
    low = key.lower()
    for pair in block.pairs():
        if pair.key.lower() == low and isinstance(pair.value, Block):
            return pair.value
    return None


def _find_ideas_grid(node: Block) -> Block | None:
    """Depth-first search for the ``gridBoxType`` named ``ideas_grid`` that owns
    a ``max_slots`` block (case-insensitive keys — .gui mixes ``gridBoxType`` /
    ``gridboxtype``). The other ``ideas_grid`` uses ``max_slots_horizontal``."""
    for pair in node.pairs():
        if not isinstance(pair.value, Block):
            continue
        if pair.key.lower() == "gridboxtype":
            name = (pair.value.get_scalar("name") or "").strip('"')
            if name == "ideas_grid" and _get_block_ci(pair.value, "max_slots") is not None:
                return pair.value
        found = _find_ideas_grid(pair.value)
        if found is not None:
            return found
    return None
