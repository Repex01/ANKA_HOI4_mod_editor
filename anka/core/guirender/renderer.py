"""Compose a HOI4 window into one RGBA frame (the WYSIWYG picture).

Walks a `GuiNode` window tree, solves every element's absolute rect at the
simulated resolution, draws sprites/text/ghosts in definition order (later on
top, like the engine), honours container clipping via sub-images, and returns
the frame plus an index-path→rect map for canvas hit-testing and a list of
render problems (missing sprites/textures). Pure Pillow — no tk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image, ImageDraw

from .layout import LayoutSolver, Rect
from .sprites import SpriteImageCache, placeholder
from .text import FontProvider

_COLOR_CODE_RE = re.compile("§.")

_TEXT_COLOR = (235, 232, 224, 255)
_GHOST = (120, 170, 255, 110)
_GHOST_FILL = (120, 170, 255, 18)
_UNKNOWN = (255, 170, 60, 130)


@dataclass
class RenderProblem:
    path: tuple[int, ...]
    code: str           # missing_sprite | bad_texture
    detail: str = ""


@dataclass
class RenderResult:
    image: Image.Image
    rects: dict[tuple[int, ...], Rect] = field(default_factory=dict)
    problems: list[RenderProblem] = field(default_factory=list)


class GuiRenderer:
    """Also serves as the solver's `MetricsProvider`."""

    def __init__(self, sprites: SpriteImageCache, fonts: FontProvider,
                 loc_get: Callable[[str], str | None] | None = None):
        self.sprites = sprites
        self.fonts = fonts
        self.loc_get = loc_get
        self.solver = LayoutSolver(self)

    # ------------------------------------------------------- MetricsProvider
    def sprite_frame_size(self, name: str) -> tuple[int, int] | None:
        return self.sprites.sprite_frame_size(name)

    def measure_text(self, text: str, font_name: str) -> tuple[int, int]:
        return self.fonts.measure_text(text, font_name)

    # ------------------------------------------------------------------ main
    def render_window(self, window, resolution: tuple[int, int], *,
                      ghost_boxes: bool = True) -> RenderResult:
        result = RenderResult(
            image=Image.new("RGBA", (int(resolution[0]), int(resolution[1])),
                            (0, 0, 0, 0)))
        self._ghosts = ghost_boxes
        screen = Rect(0, 0, float(resolution[0]), float(resolution[1]))
        self._paint(window, screen, (), result.image, (0.0, 0.0), result)
        return result

    # ----------------------------------------------------------------- paint
    def _paint(self, node, parent: Rect, path: tuple[int, ...],
               canvas: Image.Image, origin: tuple[float, float],
               result: RenderResult) -> None:
        rect = self.solver.solve_rect(node, parent)
        result.rects[path] = rect
        type_low = node.type_key.lower()
        local = Rect(rect.x - origin[0], rect.y - origin[1], rect.w, rect.h)

        if type_low in ("containerwindowtype", "windowtype"):
            self._paint_container(node, rect, local, path, canvas, origin, result)
            return

        draw_op = _DISPATCH.get(type_low)
        if draw_op is not None:
            draw_op(self, node, local, path, canvas, result)
        elif node.spec is None:
            self._outline(canvas, local, _UNKNOWN, node.type_key)
        # widgets with children (scrollbars, dropdowns, listboxes)
        for i, child in enumerate(node.children()):
            self._paint(child, rect, path + (i,), canvas, origin, result)

    def _paint_container(self, node, rect: Rect, local: Rect,
                         path: tuple[int, ...], canvas: Image.Image,
                         origin: tuple[float, float],
                         result: RenderResult) -> None:
        self._draw_background(node, local, path, canvas, result)

        clip = ((node.get_attr("clipping") or "yes").lower() != "no"
                and bool(node.get_size_raw()[0] or node.get_size_raw()[1]))
        if clip and rect.w >= 1 and rect.h >= 1:
            sub = Image.new("RGBA", (max(1, int(rect.w)), max(1, int(rect.h))),
                            (0, 0, 0, 0))
            for i, child in enumerate(node.children()):
                self._paint(child, rect, path + (i,), sub, (rect.x, rect.y),
                            result)
            canvas.alpha_composite(sub, (int(local.x), int(local.y)))
        else:
            for i, child in enumerate(node.children()):
                self._paint(child, rect, path + (i,), canvas, origin, result)

    # ------------------------------------------------------------ primitives
    def _sprite_image(self, name: str, size: tuple[float, float],
                      frame: int, path: tuple[int, ...],
                      result: RenderResult) -> Image.Image | None:
        """Sprite scaled/sliced for a target size; records problems."""
        if not name:
            return None
        d = self.sprites.sprite_def(name)
        if d is None:
            result.problems.append(RenderProblem(path, "missing_sprite", name))
            return placeholder((size[0] or 24, size[1] or 24))
        w, h = max(1, int(size[0])), max(1, int(size[1]))
        if d.kind == "corneredtilespritetype":
            img = self.sprites.nine_slice(name, (w, h))
        else:
            img = self.sprites.frame(name, frame)
            if img is not None and (img.width, img.height) != (w, h):
                img = img.resize((w, h), Image.LANCZOS)
        if img is None:
            result.problems.append(RenderProblem(path, "bad_texture", name))
            return placeholder((w, h))
        return img

    def _draw_background(self, node, local: Rect, path: tuple[int, ...],
                         canvas: Image.Image, result: RenderResult) -> None:
        bg = (node.get_attr_block("background")
              or node.get_attr_block("backGround"))
        if bg is None:
            return
        name = (bg.get_scalar_ci("quadTextureSprite")
                or bg.get_scalar_ci("spriteType") or "").strip('"')
        if not name:
            return
        img = self._sprite_image(name, (local.w, local.h), 1, path, result)
        if img is not None:
            canvas.alpha_composite(img, (int(local.x), int(local.y)))

    def _outline(self, canvas: Image.Image, local: Rect,
                 color: tuple[int, int, int, int], label: str = "",
                 fill: tuple[int, int, int, int] | None = None) -> None:
        if local.w < 1 or local.h < 1:
            return
        draw = ImageDraw.Draw(canvas)
        box = (int(local.x), int(local.y),
               int(local.x + local.w) - 1, int(local.y + local.h) - 1)
        if fill is not None:
            draw.rectangle(box, outline=color, fill=fill)
        else:
            draw.rectangle(box, outline=color)
        if label:
            font = self.fonts.get("arial_10", 1.0)
            draw.text((box[0] + 3, box[1] + 2), label, font=font, fill=color)

    def _resolve_text(self, raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        if raw.startswith("["):
            return raw                      # scripted loc — show the source
        if self.loc_get is not None:
            loc = self.loc_get(raw)
            if loc:
                raw = loc
        return _COLOR_CODE_RE.sub("", raw).replace("§!", "")

    def _draw_text(self, canvas: Image.Image, local: Rect, text: str,
                   font_name: str, fmt: str, *, valign: str = "top") -> None:
        text = self._resolve_text(text)
        if not text:
            return
        font = self.fonts.get(font_name or "arial_14")
        draw = ImageDraw.Draw(canvas)
        max_w = int(local.w) if local.w > 4 else 10_000
        lines = _wrap(draw, text, font, max_w)
        line_h = self.fonts.size_of(font_name or "") + 3
        total_h = line_h * len(lines)
        y = local.y
        if valign == "center":
            y = local.y + (local.h - total_h) / 2
        elif valign == "bottom":
            y = local.y + local.h - total_h
        fmt = (fmt or "left").lower().strip('"')
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            lw = box[2] - box[0]
            x = local.x
            if fmt in ("center", "centre"):
                x = local.x + (local.w - lw) / 2
            elif fmt == "right":
                x = local.x + local.w - lw
            draw.text((x, y), line, font=font, fill=_TEXT_COLOR)
            y += line_h

    # ----------------------------------------------------------- widget draws
    def _draw_icon(self, node, local, path, canvas, result) -> None:
        name = node.get_attr("spriteType") or node.get_attr("quadTextureSprite")
        frame = int(_num(node.get_attr("frame"), 1))
        if not name:
            if self._ghosts:
                self._outline(canvas, local, _GHOST, "icon")
            return
        img = self._sprite_image(name, (local.w, local.h), frame, path, result)
        if img is not None:
            canvas.alpha_composite(img, (int(local.x), int(local.y)))

    def _draw_button(self, node, local, path, canvas, result) -> None:
        name = node.get_attr("quadTextureSprite") or node.get_attr("spriteType")
        frame = int(_num(node.get_attr("frame"), 1))
        if name:
            img = self._sprite_image(name, (local.w, local.h), frame, path,
                                     result)
            if img is not None:
                canvas.alpha_composite(img, (int(local.x), int(local.y)))
        elif self._ghosts:
            self._outline(canvas, local, _GHOST, "btn", _GHOST_FILL)
        text = node.get_attr("buttonText")
        if text:
            self._draw_text(canvas, local, text,
                            node.get_attr("buttonFont") or "hoi_18b",
                            node.get_attr("format") or "center",
                            valign="center")

    def _draw_textbox(self, node, local, path, canvas, result) -> None:
        self._draw_text(canvas, local, node.get_attr("text"),
                        node.get_attr("font"), node.get_attr("format"))

    def _draw_editbox(self, node, local, path, canvas, result) -> None:
        self._outline(canvas, local, (200, 200, 200, 90), "",
                      (255, 255, 255, 14))
        self._draw_text(canvas, local, node.get_attr("text"),
                        node.get_attr("font"), node.get_attr("format"),
                        valign="center")

    def _draw_gridbox(self, node, local, path, canvas, result) -> None:
        if not self._ghosts:
            return
        sx, sy = node.get_xy("slotsize")
        slot_w = _num(sx, 24.0)
        slot_h = _num(sy, 24.0)
        if slot_w <= 2 or slot_h <= 2:
            self._outline(canvas, local, _GHOST, "grid")
            return
        draw = ImageDraw.Draw(canvas)
        y = local.y
        while y < local.y2 - 1:
            x = local.x
            while x < local.x2 - 1:
                draw.rectangle((int(x), int(y),
                                int(min(x + slot_w, local.x2)) - 1,
                                int(min(y + slot_h, local.y2)) - 1),
                               outline=_GHOST)
                x += slot_w
            y += slot_h
        font = self.fonts.get("arial_10")
        draw.text((int(local.x) + 3, int(local.y) + 2),
                  node.name or "grid", font=font, fill=_GHOST)

    def _draw_listbox(self, node, local, path, canvas, result) -> None:
        if self._ghosts:
            self._outline(canvas, local, _GHOST, node.name or "list",
                          _GHOST_FILL)

    def _draw_overlapping(self, node, local, path, canvas, result) -> None:
        if self._ghosts:
            self._outline(canvas, local, _GHOST, node.name or "overlap")

    def _draw_position(self, node, local, path, canvas, result) -> None:
        if not self._ghosts:
            return
        draw = ImageDraw.Draw(canvas)
        x, y = int(local.x), int(local.y)
        draw.line((x - 4, y, x + 4, y), fill=_GHOST)
        draw.line((x, y - 4, x, y + 4), fill=_GHOST)


def _num(raw: str, default: float) -> float:
    try:
        return float(str(raw).strip().strip('"'))
    except (ValueError, AttributeError):
        return default


def _wrap(draw: ImageDraw.ImageDraw, text: str, font,
          max_width: int) -> list[str]:
    lines: list[str] = []
    for para in text.replace("\\n", "\n").split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for word in words[1:]:
            probe = cur + " " + word
            box = draw.textbbox((0, 0), probe, font=font)
            if box[2] - box[0] <= max_width:
                cur = probe
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines or [""]


_DISPATCH = {
    "icontype": GuiRenderer._draw_icon,
    "buttontype": GuiRenderer._draw_button,
    "guibuttontype": GuiRenderer._draw_button,
    "checkboxtype": GuiRenderer._draw_button,
    "instanttextboxtype": GuiRenderer._draw_textbox,
    "textboxtype": GuiRenderer._draw_textbox,
    "editboxtype": GuiRenderer._draw_editbox,
    "gridboxtype": GuiRenderer._draw_gridbox,
    "listboxtype": GuiRenderer._draw_listbox,
    "smoothlistboxtype": GuiRenderer._draw_listbox,
    "overlappingelementsboxtype": GuiRenderer._draw_overlapping,
    "positiontype": GuiRenderer._draw_position,
}
