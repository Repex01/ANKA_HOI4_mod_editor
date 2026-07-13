"""Pure layout solver for HOI4 GUI elements.

Semantics (per wiki + vanilla usage):

* ``orientation`` picks the anchor point on the **parent** rect
  (``upper_left`` default). ``position`` offsets from that anchor; the axes
  always run right/down, which is why right/bottom-anchored elements use
  negative offsets.
* ``origo`` (or ``centerposition = yes``) shifts by the element's **own**
  size so ``position`` marks that corner/center instead of the top-left.
* sizes take pixels or percentages of the parent (``100%``; ``%%`` is an
  escaped percent seen in vanilla — same meaning).
* missing sizes fall back: sprite frame size → maxWidth/maxHeight (text) →
  parent size (containers).

`position_for_rect` is the exact inverse used by canvas dragging; keeping
both here makes the pair unit-testable without any UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x2 and self.y <= py < self.y2

    def intersects(self, other: "Rect") -> bool:
        return not (other.x >= self.x2 or other.x2 <= self.x
                    or other.y >= self.y2 or other.y2 <= self.y)


class MetricsProvider(Protocol):
    def sprite_frame_size(self, name: str) -> tuple[int, int] | None: ...
    def measure_text(self, text: str, font_name: str) -> tuple[int, int]: ...


# Anchor factors per orientation: (fx, fy) of the parent rect.
_ANCHORS: dict[str, tuple[float, float]] = {
    "upper_left": (0.0, 0.0), "upper_right": (1.0, 0.0),
    "lower_left": (0.0, 1.0), "lower_right": (1.0, 1.0),
    "center": (0.5, 0.5), "center_middle": (0.5, 0.5),
    "center_up": (0.5, 0.0), "center_down": (0.5, 1.0),
    "center_left": (0.0, 0.5), "center_right": (1.0, 0.5),
    "left": (0.0, 0.5), "right": (1.0, 0.5),
    "up": (0.5, 0.0), "down": (0.5, 1.0),
    "center_lower": (0.5, 1.0), "bottom_left": (0.0, 1.0),
}


def parse_dim(raw: str, parent_dim: float) -> float | None:
    """One size component: pixels, or a percentage of the parent dimension."""
    raw = (raw or "").strip().strip('"')
    if not raw:
        return None
    if raw.endswith("%"):
        try:
            return float(raw.rstrip("%")) / 100.0 * parent_dim
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


def anchor_point(orientation: str, parent: Rect) -> tuple[float, float]:
    fx, fy = _ANCHORS.get(orientation.lower().strip('"'), (0.0, 0.0))
    return parent.x + fx * parent.w, parent.y + fy * parent.h


def origo_shift(origo: str, w: float, h: float) -> tuple[float, float]:
    fx, fy = _ANCHORS.get(origo.lower().strip('"'), (0.0, 0.0))
    return fx * w, fy * h


class LayoutSolver:
    def __init__(self, metrics: MetricsProvider):
        self.metrics = metrics

    # ------------------------------------------------------------------- size
    def solve_size(self, node, parent: Rect) -> tuple[float, float]:
        if (node.get_attr("fullScreen") or "").lower() == "yes":
            return parent.w, parent.h
        type_low = node.type_key.lower()
        # iconType is sized by its sprite (× scale) and instantTextBoxType by its
        # maxWidth/maxHeight clip box — both ignore any `size` on the element.
        if type_low == "icontype":
            sw, sh = self._sprite_size(node)
            if sw or sh:
                return (sw or 24.0), (sh or 24.0)
        elif type_low == "instanttextboxtype":
            tw, th = self._text_bounds(node)
            if tw or th:
                return (tw or 24.0), (th or 24.0)
        w_raw, h_raw = node.get_size_raw()
        w = parse_dim(w_raw, parent.w)
        h = parse_dim(h_raw, parent.h)
        if w is not None and h is not None:
            return w, h

        sw, sh = self._sprite_size(node)
        tw, th = self._text_bounds(node)
        type_low = node.type_key.lower()
        if type_low == "gridboxtype":
            gw, gh = self._grid_size(node, parent)
            return (w if w is not None else gw), (h if h is not None else gh)
        if w is None:
            w = sw if sw else (tw if tw else (parent.w if node.is_container else 24.0))
        if h is None:
            h = sh if sh else (th if th else (parent.h if node.is_container else 24.0))
        return w, h

    def _sprite_size(self, node) -> tuple[float, float]:
        sprite = node.get_attr("spriteType") or node.get_attr("quadTextureSprite")
        if not sprite:
            return 0.0, 0.0
        size = self.metrics.sprite_frame_size(sprite)
        if size is None:
            return 0.0, 0.0
        scale = _to_float(node.get_attr("scale"), 1.0) or 1.0
        return size[0] * scale, size[1] * scale

    def _text_bounds(self, node) -> tuple[float, float]:
        mw = _to_float(node.get_attr("maxWidth"), 0.0)
        mh = _to_float(node.get_attr("maxHeight"), 0.0)
        return mw, mh

    def _grid_size(self, node, parent: Rect) -> tuple[float, float]:
        node_slot = node.get_xy("slotsize")
        slot_w = parse_dim(node_slot[0], parent.w) or 24.0
        slot_h = parse_dim(node_slot[1], parent.h) or 24.0
        cols = int(_to_float(node.get_attr("max_slots_horizontal"), 0.0) or 0)
        rows = int(_to_float(node.get_attr("max_slots_vertical"), 0.0) or 0)
        if cols <= 0:
            cols = max(1, int(parent.w // slot_w)) if rows else 4
        if rows <= 0:
            rows = 3
        return slot_w * cols, slot_h * rows

    # ------------------------------------------------------------------- rect
    def solve_rect(self, node, parent: Rect) -> Rect:
        w, h = self.solve_size(node, parent)
        if (node.get_attr("fullScreen") or "").lower() == "yes":
            return Rect(parent.x, parent.y, w, h)
        ax, ay = anchor_point(node.orientation, parent)
        px, py = node.get_position()
        origo = node.origo
        if (node.get_attr("centerposition") or "").lower() == "yes":
            origo = "center"
        dx, dy = origo_shift(origo, w, h)
        return Rect(ax + px - dx, ay + py - dy, w, h)

    def position_for_rect(self, node, target_x: float, target_y: float,
                          parent: Rect) -> tuple[float, float]:
        """Inverse of `solve_rect`: the ``position`` values that place the
        node's top-left at (target_x, target_y)."""
        w, h = self.solve_size(node, parent)
        ax, ay = anchor_point(node.orientation, parent)
        origo = node.origo
        if (node.get_attr("centerposition") or "").lower() == "yes":
            origo = "center"
        dx, dy = origo_shift(origo, w, h)
        return target_x + dx - ax, target_y + dy - ay

    # ------------------------------------------------------------------- tree
    def solve_tree(self, window, resolution: tuple[int, int],
                   *, on_rect: Callable[[tuple[int, ...], Rect], None]) -> None:
        """Walk the window tree, reporting the absolute rect of every node
        (index-path keyed) through `on_rect`."""
        screen = Rect(0, 0, float(resolution[0]), float(resolution[1]))

        def walk(node, parent: Rect, path: tuple[int, ...]) -> None:
            rect = self.solve_rect(node, parent)
            on_rect(path, rect)
            for i, child in enumerate(node.children()):
                walk(child, rect, path + (i,))

        walk(window, screen, ())


def _to_float(raw: str, default: float) -> float:
    try:
        return float(str(raw).strip().strip('"'))
    except (ValueError, AttributeError):
        return default
