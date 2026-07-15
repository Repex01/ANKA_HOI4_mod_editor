"""Technology tree canvas: game-like per-folder rendering + direct manipulation.

Pure view (forked from the focuses canvas): it receives a display model
(`CanvasNode` list + link lists + the folder's gridboxes/labels/item geometry) and
emits user intents through callbacks. Unlike the focus canvas the world space is
*pixels of the folder container* (the .gui coordinate system): every node carries a
precomputed pixel anchor, and drag snapping happens in the cells of the node's own
gridbox (each gridbox has its own origin, slot size and axis format).

Interactions: wheel = scroll, Shift+wheel = horizontal, Ctrl+wheel = zoom (anchored
at the cursor), left-drag on a tech = move (cell-snapped, multi-selection aware),
left-drag on empty = pan, double-click on empty = create tech, right-click = context
menu, link mode = click a target tech after choosing "add link" in the menu.
A collapsible minimap sits in the bottom-right corner.
"""
from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from ...services.tech_gui_service import GridBoxInfo, ItemLayout, TextLabel

_MARGIN_PX = 90
ZOOM_STEPS = (0.3, 0.4, 0.5, 0.65, 0.8, 1.0, 1.2, 1.45)
_DRAG_THRESHOLD = 6  # px before a press becomes a drag
_FONT_PX_RE = re.compile(r"(\d+)")

# Link kinds understood by the renderer.
LINK_PATH = "path"
LINK_PATH_IGNORED = "path_ignored"
LINK_DEP = "dependency"
LINK_XOR = "xor"


@dataclass
class CanvasNode:
    tid: str
    px: tuple[float, float]           # slot anchor, folder-container pixels
    cell: tuple[int, int]             # folder position (for status/tooltips)
    gridbox: str                      # owning gridbox name ("" = unresolved)
    icon: str                         # GFX sprite name
    name: str
    small: bool = False
    doctrine: bool = False
    editable: bool = True
    has_issues: bool = False
    sub_count: int = 0                # sub_technologies badges


@dataclass
class CanvasModel:
    nodes: list[CanvasNode] = field(default_factory=list)
    # child, parent, kind (LINK_*), label ("" or e.g. "×2")
    links: list[tuple[str, str, str, str]] = field(default_factory=list)
    gridboxes: dict[str, GridBoxInfo] = field(default_factory=dict)
    labels: list[TextLabel] = field(default_factory=list)
    item: ItemLayout = field(default_factory=ItemLayout)
    small_item: ItemLayout | None = None
    layout_mode: bool = False         # gridbox origins draggable


class TechCanvas(ttk.Frame):
    def __init__(self, master, palette, *,
                 resolve_icon: Callable[[str], Path | None],
                 on_select: Callable[[list[str]], None],
                 on_move: Callable[[list[str], tuple[int, int]], None],
                 on_create: Callable[[str | None, tuple[int, int], tuple[float, float]], None],
                 on_link: Callable[[str, str, str], None],
                 on_context: Callable[[tk.Event, str | None, str | None, tuple[float, float]], None],
                 on_open: Callable[[str], None] | None = None,
                 on_delete: Callable[[list[str]], None] | None = None,
                 on_link_end: Callable[[], None] | None = None,
                 on_gridbox_move: Callable[[str, tuple[float, float]], None] | None = None,
                 link_hints: dict[str, str] | None = None,
                 icons_ready: Callable[[], bool] | None = None,
                 icon_frames: Callable[[str], int] | None = None):
        super().__init__(master, style="TFrame")
        self.palette = palette
        self._resolve_icon = resolve_icon
        self._icons_ready = icons_ready
        self._icon_frames = icon_frames
        self._on_delete = on_delete
        self._on_link_end = on_link_end
        self._on_gridbox_move = on_gridbox_move
        self._link_hints = link_hints or {}
        self._on_select = on_select
        self._on_move = on_move
        self._on_create = on_create
        self._on_link = on_link
        self._on_context = on_context
        self._on_open = on_open

        self.model = CanvasModel()
        self.zoom = 1.0
        self.show_boxes = True             # gridbox outlines
        self.selection: list[str] = []
        self._origin = (0.0, 0.0)          # px offset so all coords are positive
        self._by_id: dict[str, CanvasNode] = {}
        self._box_rects: dict[str, tuple[float, float, float, float]] = {}
        self._img_cache: dict[tuple[str, float], ImageTk.PhotoImage] = {}
        self._pil_cache: dict[str, Image.Image] = {}
        self._icon_queue: list[str] = []
        self._queued: set[str] = set()
        self._pump_job: str | None = None
        self._link_mode: tuple[str, str] | None = None    # (source tid, kind)
        self._link_multi: str | None = None
        self._press: dict | None = None
        self._ghosts: list[int] = []
        self._preview_ids: list[int] = []

        self.canvas = tk.Canvas(self, bg=palette.bg, highlightthickness=0, bd=0)
        self._hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self._vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self._on_xscroll, yscrollcommand=self._on_yscroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._vbar.grid(row=0, column=1, sticky="ns")
        self._hbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.minimap = _Minimap(self, palette)

        self._link_status = tk.Label(self, bd=0, padx=10, pady=5,
                                     bg=palette.accent, fg=palette.accent_text,
                                     font=("Segoe UI", 9))

        c = self.canvas
        c.configure(takefocus=1)
        c.bind("<ButtonPress-1>", self._press_1)
        c.bind("<B1-Motion>", self._motion_1)
        c.bind("<ButtonRelease-1>", self._release_1)
        c.bind("<Double-Button-1>", self._double_1)
        c.bind("<ButtonPress-2>", lambda e: c.scan_mark(e.x, e.y))
        c.bind("<B2-Motion>", lambda e: (c.scan_dragto(e.x, e.y, gain=1),
                                         self.minimap.refresh_viewport()))
        c.bind("<Button-3>", self._context)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Shift-MouseWheel>", self._wheel_h)
        c.bind("<Control-MouseWheel>", self._wheel_zoom)
        c.bind("<Escape>", lambda e: self.cancel_link_mode())
        c.bind("<Delete>", self._delete_key)
        for key in ("Shift_L", "Shift_R"):
            c.bind(f"<KeyRelease-{key}>", lambda e: self._end_multi("or"))
        for key in ("Control_L", "Control_R"):
            c.bind(f"<KeyRelease-{key}>", lambda e: self._end_multi("and"))
        c.bind("<Configure>", lambda e: self.minimap.refresh_viewport())

    def _on_xscroll(self, *args) -> None:
        self._hbar.set(*args)
        self.minimap.refresh_viewport()

    def _on_yscroll(self, *args) -> None:
        self._vbar.set(*args)
        self.minimap.refresh_viewport()

    # ------------------------------------------------------------------ model
    def set_model(self, model: CanvasModel, keep_view: bool = True) -> None:
        anchor = self._view_world() if keep_view else None
        self.model = model
        self._by_id = {n.tid: n for n in model.nodes}
        self.render()
        if anchor is not None:
            self._restore_view(anchor)
        else:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
            self.minimap.refresh_viewport()

    def set_selection(self, tids: list[str], notify: bool = False) -> None:
        self.selection = [t for t in tids if t in self._by_id]
        self._apply_selection()
        if notify:
            self._on_select(list(self.selection))

    # ---------------------------------------------------------------- mapping
    def _world_to_screen(self, px: tuple[float, float]) -> tuple[float, float]:
        return ((px[0] + self._origin[0]) * self.zoom,
                (px[1] + self._origin[1]) * self.zoom)

    def _screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return sx / self.zoom - self._origin[0], sy / self.zoom - self._origin[1]

    def _item_layout(self, node: CanvasNode) -> ItemLayout:
        if node.small and self.model.small_item is not None:
            return self.model.small_item
        return self.model.item

    def _node_rect(self, node: CanvasNode) -> tuple[float, float, float, float]:
        """Screen rectangle of a node's item box."""
        layout = self._item_layout(node)
        x, y = self._world_to_screen((node.px[0] + layout.offset[0],
                                      node.px[1] + layout.offset[1]))
        return (x, y, x + layout.size[0] * self.zoom, y + layout.size[1] * self.zoom)

    def _bounds(self) -> tuple[float, float, float, float]:
        """World-pixel extent of everything drawable."""
        xs: list[float] = []
        ys: list[float] = []
        for n in self.model.nodes:
            layout = self._item_layout(n)
            xs += [n.px[0] + layout.offset[0],
                   n.px[0] + layout.offset[0] + layout.size[0]]
            ys += [n.px[1] + layout.offset[1],
                   n.px[1] + layout.offset[1] + layout.size[1]]
        for gb in self.model.gridboxes.values():
            xs.append(gb.origin[0])
            ys.append(gb.origin[1])
        for lb in self.model.labels:
            xs.append(lb.pos[0])
            ys.append(lb.pos[1])
        if not xs:
            xs, ys = [0.0], [0.0]
        return min(xs), min(ys), max(xs), max(ys)

    def _view_world(self) -> tuple[float, float]:
        return self._screen_to_world(self.canvas.canvasx(0), self.canvas.canvasy(0))

    def _restore_view(self, world: tuple[float, float]) -> None:
        x, y = self._world_to_screen(world)
        sr = [float(v) for v in (self.canvas.cget("scrollregion").split() or [0, 0, 1, 1])]
        self.canvas.xview_moveto(max(0.0, x / max(sr[2] - sr[0], 1)))
        self.canvas.yview_moveto(max(0.0, y / max(sr[3] - sr[1], 1)))
        self.minimap.refresh_viewport()

    def center_on(self, tid: str) -> None:
        node = self._by_id.get(tid)
        if node is None:
            return
        x, y = self._world_to_screen(node.px)
        vw = max(self.canvas.winfo_width(), 1)
        vh = max(self.canvas.winfo_height(), 1)
        sr = [float(v) for v in self.canvas.cget("scrollregion").split()]
        self.canvas.xview_moveto(max(0.0, (x - vw / 2) / max(sr[2], 1)))
        self.canvas.yview_moveto(max(0.0, (y - vh / 2) / max(sr[3], 1)))
        self.minimap.refresh_viewport()

    # ------------------------------------------------------ template preview
    def preview_px(self, points: list[tuple[float, float]],
                   anchor: tuple[float, float] | None = None) -> None:
        """Translucent phantom tiles at world-pixel anchors (template preview)."""
        self.clear_preview()
        c = self.canvas
        layout = self.model.item
        for px in points:
            x, y = self._world_to_screen((px[0] + layout.offset[0],
                                          px[1] + layout.offset[1]))
            hot = px == anchor
            self._preview_ids.append(c.create_rectangle(
                x, y, x + layout.size[0] * self.zoom, y + layout.size[1] * self.zoom,
                fill=self.palette.accent,
                stipple="gray50" if hot else "gray25",
                outline=self.palette.accent, width=2,
                dash=() if hot else (4, 3)))

    def clear_preview(self) -> None:
        for item in self._preview_ids:
            self.canvas.delete(item)
        self._preview_ids = []

    # --------------------------------------------------------------- rendering
    def render(self) -> None:
        c = self.canvas
        c.delete("all")
        self._ghosts = []
        min_x, min_y, max_x, max_y = self._bounds()
        self._origin = (_MARGIN_PX - min_x, _MARGIN_PX - min_y)
        width = (max_x - min_x + 2 * _MARGIN_PX) * self.zoom
        height = (max_y - min_y + 2 * _MARGIN_PX) * self.zoom
        c.configure(scrollregion=(0, 0, width, height))

        if self.show_boxes:
            self._draw_gridboxes()
        for lb in self.model.labels:
            self._draw_label(lb)
        for child, parent, kind, label in self.model.links:
            self._draw_link(child, parent, kind, label)
        for node in self.model.nodes:
            self._draw_node(node)
        self._apply_selection()
        self.minimap.render(self)
        self._schedule_pump()

    def set_boxes(self, visible: bool) -> None:
        if visible != self.show_boxes:
            self.show_boxes = visible
            self.render()

    def _gridbox_extent(self, name: str) -> tuple[float, float, float, float]:
        """World rect of a gridbox: its origin plus the cells of its techs."""
        gb = self.model.gridboxes[name]
        xs = [gb.origin[0]]
        ys = [gb.origin[1]]
        w, h = gb.slotsize
        for n in self.model.nodes:
            if n.gridbox == name:
                xs += [n.px[0], n.px[0] + w]
                ys += [n.px[1], n.px[1] + h]
        return min(xs) - 8, min(ys) - 8, max(xs) + 8, max(ys) + 8

    def _draw_gridboxes(self) -> None:
        c = self.canvas
        p = self.palette
        self._box_rects = {}
        color = p.surface_alt if p.is_dark else p.border
        for name in self.model.gridboxes:
            wx0, wy0, wx1, wy1 = self._gridbox_extent(name)
            self._box_rects[name] = (wx0, wy0, wx1, wy1)
            x0, y0 = self._world_to_screen((wx0, wy0))
            x1, y1 = self._world_to_screen((wx1, wy1))
            active = self.model.layout_mode
            c.create_rectangle(x0, y0, x1, y1,
                               outline=p.accent if active else color,
                               dash=(2, 4), width=1,
                               tags=("gbox", f"gb:{name}"))
            size = max(7, round(8 * self.zoom))
            c.create_text(x0 + 4, y0 + 2, text=name, anchor="nw",
                          font=("Segoe UI", size),
                          fill=p.accent if active else p.text_muted,
                          tags=("gbox", f"gb:{name}"))
            # origin marker (the 0,0 slot corner)
            ox, oy = self._world_to_screen(self.model.gridboxes[name].origin)
            r = max(2, 3 * self.zoom)
            c.create_oval(ox - r, oy - r, ox + r, oy + r,
                          fill=p.accent if active else color, outline="",
                          tags=("gbox", f"gb:{name}"))

    def _draw_label(self, lb: TextLabel) -> None:
        m = _FONT_PX_RE.search(lb.font or "")
        base = int(m.group(1)) if m else 16
        size = max(7, round(base * 0.55 * self.zoom))
        x, y = self._world_to_screen(lb.pos)
        self.canvas.create_text(x, y, text=lb.text, anchor="nw",
                                font=("Segoe UI Semibold", size),
                                fill=self.palette.text_muted, tags="label")

    def _draw_node(self, node: CanvasNode) -> None:
        c = self.canvas
        p = self.palette
        x0, y0, x1, y1 = self._node_rect(node)
        tags = ("node", f"f:{node.tid}")

        fill = p.surface if node.editable else p.surface_alt
        outline = "#b08a3e" if node.doctrine else p.border
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline,
                           width=1, tags=tags)

        layout = self._item_layout(node)
        # The GUI's Icon element carries `centerposition = yes` on the big item
        # (position = its centre) but not on the small one (position = top-left),
        # so the anchor must follow the element, or small icons spill up-left.
        anchor = "center"
        if layout.icon_pos is not None:
            icx = x0 + layout.icon_pos[0] * self.zoom
            icy = y0 + layout.icon_pos[1] * self.zoom
            if not layout.icon_center:
                anchor = "nw"
        else:
            icx = (x0 + x1) / 2
            icy = (y0 + y1) / 2 + (y1 - y0) * 0.12
        icon = self._photo(node.icon, self._icon_box(layout))
        c.create_image(icx, icy, image=icon, anchor=anchor,
                       tags=tags + (f"img:{node.icon}",))

        size = max(7, round(8.4 * self.zoom))
        c.create_text(x0 + 4 * self.zoom, y0 + 2 * self.zoom, text=node.name,
                      anchor="nw", width=(x1 - x0) - 8, justify="left",
                      font=("Segoe UI", size),
                      fill=p.text if node.editable else p.text_muted, tags=tags)

        if node.sub_count:
            chip = max(8, round(11 * self.zoom))
            for i in range(min(node.sub_count, 3)):
                cx1 = x1 - 3 - i * (chip + 2)
                c.create_rectangle(cx1 - chip, y1 - 3 - chip, cx1, y1 - 3,
                                   fill=p.surface_alt, outline=outline, tags=tags)
        if not node.editable:
            c.create_text(x1 - 5, y0 + 3, text="🔒", anchor="ne",
                          font=("Segoe UI", size), tags=tags)
        if node.has_issues:
            r = max(3, 4 * self.zoom)
            c.create_oval(x0 + 3, y1 - 2 * r - 3, x0 + 3 + 2 * r, y1 - 3,
                          fill=p.danger, outline="", tags=tags)

    def _photo(self, sprite: str,
               box: tuple[float, float]) -> ImageTk.PhotoImage:
        pil = self._pil_cache.get(sprite)
        if pil is None:
            if sprite and sprite not in self._queued:
                self._queued.add(sprite)
                self._icon_queue.append(sprite)
            key = ("~placeholder~", self.zoom, box)
            if key not in self._img_cache:
                self._img_cache[key] = ImageTk.PhotoImage(
                    self._fit(self._placeholder(), box))
            return self._img_cache[key]
        key = (sprite, self.zoom, box)
        if key not in self._img_cache:
            self._img_cache[key] = ImageTk.PhotoImage(self._fit(pil, box))
        return self._img_cache[key]

    _ICON_FALLBACK_BOX = (100.0, 52.0)

    def _node_of_item(self, item: int) -> CanvasNode | None:
        """The node a canvas item belongs to (items carry an ``f:<tid>`` tag)."""
        for tag in self.canvas.gettags(item):
            if tag.startswith("f:"):
                return self._by_id.get(tag[2:])
        return None

    def _icon_box(self, layout: ItemLayout) -> tuple[float, float]:
        """Room an icon may occupy inside its item (world px) — only a cap for
        oversized art; normal icons draw at their native size (see `_fit`)."""
        w, h = layout.size
        if layout.icon_pos is not None and not layout.icon_center:
            # top-left anchored icon (small item): keep its inset as the margin
            ix, iy = layout.icon_pos
            return (max(8.0, w - 2 * ix), max(8.0, h - 2 * iy))
        return (max(8.0, w), max(8.0, h))

    def _fit(self, pil: Image.Image,
             box: tuple[float, float]) -> Image.Image:
        """HOI4 draws an iconType at its sprite's *native* size, so do the same
        (× zoom) — that keeps square art square instead of squashing it into a
        guessed box. Only art that would spill out of its item is scaled down."""
        factor = min(1.0, box[0] / pil.width, box[1] / pil.height)
        w = max(1, int(pil.width * factor * self.zoom))
        h = max(1, int(pil.height * factor * self.zoom))
        return pil.resize((w, h), Image.LANCZOS)

    def invalidate_icon(self, sprite: str) -> None:
        self._pil_cache.pop(sprite, None)
        self._queued.discard(sprite)
        self._img_cache = {k: v for k, v in self._img_cache.items() if k[0] != sprite}

    def destroy(self) -> None:
        """Cancel the background icon pump before teardown: a job that fires
        after the widget is gone raises TclError out of the Tk callback and can
        leave the window hanging on exit."""
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except tk.TclError:
                pass
            self._pump_job = None
        self._icon_queue.clear()
        super().destroy()

    def _schedule_pump(self, delay: int = 15) -> None:
        if self._pump_job is None and self._icon_queue and self.winfo_exists():
            self._pump_job = self.after(delay, self._pump_icons)

    def _pump_icons(self) -> None:
        self._pump_job = None
        if not self._icon_queue or not self.winfo_exists():
            return
        if self._icons_ready is not None and not self._icons_ready():
            self._schedule_pump(200)
            return
        for sprite in self._icon_queue[:12]:
            del self._icon_queue[:1]
            pil = None
            path = self._resolve_icon(sprite)
            if path is not None:
                try:
                    with Image.open(path) as im:
                        pil = im.convert("RGBA")
                except Exception:
                    pil = None
            # Frame strips (noOfFrames > 1) store frames side by side; the game
            # draws only one frame, so crop the first — otherwise square icons
            # render as wide rectangles.
            if pil is not None and self._icon_frames is not None:
                try:
                    frames = max(1, int(self._icon_frames(sprite)))
                except Exception:
                    frames = 1
                if frames > 1:
                    pil = pil.crop((0, 0, max(1, pil.width // frames), pil.height))
            self._pil_cache[sprite] = pil if pil is not None else self._placeholder()
            # Each item is sized by its own node's layout (big vs small item), so
            # the photo must be built per item, not once for the sprite.
            try:
                for item in self.canvas.find_withtag(f"img:{sprite}"):
                    node = self._node_of_item(item)
                    box = (self._icon_box(self._item_layout(node))
                           if node is not None else self._ICON_FALLBACK_BOX)
                    self.canvas.itemconfigure(item, image=self._photo(sprite, box))
            except tk.TclError:
                return                      # widget torn down mid-pump
        self._schedule_pump()

    def _placeholder(self) -> Image.Image:
        w, h = 72, 40
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((2, 2, w - 3, h - 3), radius=6,
                            outline=self.palette.text_muted, width=2)
        d.text((w // 2, h // 2), "?", anchor="mm", fill=self.palette.text_muted)
        return img

    # --------------------------------------------------------------- links
    def _edge_anchors(self, child: str, parent: str
                      ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """Start (on parent box) and end (on child box) anchor points, picking the
        facing edges so LEFT-format (horizontal) and UP-format trees both read well."""
        if child not in self._by_id or parent not in self._by_id:
            return None
        cx0, cy0, cx1, cy1 = self._node_rect(self._by_id[child])
        px0, py0, px1, py1 = self._node_rect(self._by_id[parent])
        pcx, pcy = (px0 + px1) / 2, (py0 + py1) / 2
        ccx, ccy = (cx0 + cx1) / 2, (cy0 + cy1) / 2
        if abs(ccx - pcx) >= abs(ccy - pcy):        # mostly horizontal
            if ccx >= pcx:
                return (px1, pcy), (cx0, ccy)
            return (px0, pcy), (cx1, ccy)
        if ccy >= pcy:                              # child below
            return (pcx, py1), (ccx, cy0)
        return (pcx, py0), (ccx, cy1)

    def _draw_link(self, child: str, parent: str, kind: str, label: str) -> None:
        anchors = self._edge_anchors(child, parent)
        if anchors is None:
            return
        (sx, sy), (ex, ey) = anchors
        p = self.palette
        width = max(1, round(1.4 * self.zoom))
        tags = ("link", f"ln:{child}", f"ln:{parent}")
        if kind == LINK_XOR:
            self.canvas.create_line(sx, sy, ex, ey, fill=p.danger, dash=(3, 5),
                                    width=width, tags=tags)
            r = max(2.5, 3.5 * self.zoom)
            for cx, cy in ((sx, sy), (ex, ey)):
                self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                        fill=p.danger, outline="", tags=tags)
            return
        if abs(ex - sx) >= abs(ey - sy):
            mid = sx + (ex - sx) * 0.5
            points = [sx, sy, mid, sy, mid, ey, ex, ey]
        else:
            mid = sy + (ey - sy) * 0.5
            points = [sx, sy, sx, mid, ex, mid, ex, ey]
        kw = {}
        color = p.text_muted
        if kind == LINK_DEP:
            kw["dash"] = (5, 4)
        elif kind == LINK_PATH_IGNORED:
            kw["dash"] = (2, 4)
        self.canvas.create_line(*points, fill=color, width=width,
                                arrow=tk.LAST if kind != LINK_DEP else tk.NONE,
                                arrowshape=(7, 9, 3), tags=tags, **kw)
        if label:
            size = max(6, round(7.5 * self.zoom))
            self.canvas.create_text((sx + ex) / 2, (sy + ey) / 2 - 6 * self.zoom,
                                    text=label, font=("Segoe UI", size),
                                    fill=p.text_muted, tags=tags)

    def _apply_selection(self) -> None:
        c = self.canvas
        c.delete("selbox")
        c.itemconfigure("link", width=max(1, round(1.4 * self.zoom)))
        for tid in self.selection:
            node = self._by_id.get(tid)
            if node is None:
                continue
            x0, y0, x1, y1 = self._node_rect(node)
            c.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2,
                               outline=self.palette.accent, width=2, tags="selbox")
            c.itemconfigure(f"ln:{tid}", width=max(2, round(2.2 * self.zoom)))
            c.tag_raise(f"ln:{tid}")
        c.tag_raise("node")
        c.tag_raise("selbox")

    # ------------------------------------------------------------------ events
    def _hit_node(self, event) -> str | None:
        c = self.canvas
        for item in c.find_overlapping(c.canvasx(event.x) - 1, c.canvasy(event.y) - 1,
                                       c.canvasx(event.x) + 1, c.canvasy(event.y) + 1):
            for tag in c.gettags(item):
                if tag.startswith("f:"):
                    return tag[2:]
        return None

    def _hit_gridbox(self, event) -> str | None:
        """Gridbox whose extent contains the point (topmost = smallest)."""
        wx, wy = self._event_world(event)
        best: tuple[float, str] | None = None
        for name, (x0, y0, x1, y1) in self._box_rects.items():
            if x0 <= wx <= x1 and y0 <= wy <= y1:
                area = (x1 - x0) * (y1 - y0)
                if best is None or area < best[0]:
                    best = (area, name)
        return best[1] if best else None

    def _nearest_gridbox(self, wx: float, wy: float) -> str | None:
        best: tuple[float, str] | None = None
        for name, gb in self.model.gridboxes.items():
            x0, y0, x1, y1 = self._box_rects.get(
                name, (gb.origin[0], gb.origin[1], gb.origin[0], gb.origin[1]))
            dx = max(x0 - wx, 0, wx - x1)
            dy = max(y0 - wy, 0, wy - y1)
            d = dx * dx + dy * dy
            if best is None or d < best[0]:
                best = (d, name)
        return best[1] if best else None

    def _event_world(self, event) -> tuple[float, float]:
        return self._screen_to_world(self.canvas.canvasx(event.x),
                                     self.canvas.canvasy(event.y))

    def _press_1(self, event) -> None:
        self.canvas.focus_set()
        tid = self._hit_node(event)
        if self._link_mode is not None:
            src, kind = self._link_mode
            if tid is None or tid == src:
                self.cancel_link_mode()
                return
            shift = bool(event.state & 0x0001)
            ctrl = bool(event.state & 0x0004)
            if kind in (LINK_PATH, LINK_DEP) and (self._link_multi or shift or ctrl):
                if self._link_multi is None:
                    self._link_multi = "or" if shift else "and"
                    self._show_link_status()
                self._on_link(src, tid, kind)
                return
            self.cancel_link_mode()
            self._on_link(src, tid, kind)
            return
        if tid is None:
            if self.model.layout_mode and self._on_gridbox_move is not None:
                name = self._hit_gridbox(event)
                if name is not None:
                    self._press = {"pan": False, "gridbox": name,
                                   "x": event.x, "y": event.y,
                                   "world": self._event_world(event),
                                   "dragging": False}
                    return
            self.set_selection([], notify=True)
            self.canvas.scan_mark(event.x, event.y)
            self._press = {"pan": True}
            return
        if tid not in self.selection:
            add = bool(event.state & 0x0004)
            self.set_selection(self.selection + [tid] if add else [tid], notify=True)
        self._press = {"pan": False, "tid": tid, "x": event.x, "y": event.y,
                       "world": self._event_world(event), "dragging": False}

    def _motion_1(self, event) -> None:
        if self._press is None:
            return
        if self._press.get("pan"):
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            self.minimap.refresh_viewport()
            return
        if not self._press["dragging"]:
            if abs(event.x - self._press["x"]) + abs(event.y - self._press["y"]) < _DRAG_THRESHOLD:
                return
            self._press["dragging"] = True
        if "gridbox" in self._press:
            self._show_gridbox_ghost(event)
            return
        node = self._by_id.get(self._press["tid"])
        if node is None or not node.editable:
            return
        delta = self._drag_delta(event)
        if delta is not None:
            self._show_ghosts(delta)

    def _drag_delta(self, event, press: dict | None = None) -> tuple[int, int] | None:
        """Cell delta of the drag, in the pressed node's own gridbox."""
        press = press or self._press
        node = self._by_id.get(press["tid"])
        if node is None:
            return None
        gb = self.model.gridboxes.get(node.gridbox)
        if gb is None:
            return None
        wx, wy = self._event_world(event)
        return gb.delta_cells((wx - press["world"][0], wy - press["world"][1]))

    def _cells_to_px_delta(self, gridbox: str, delta: tuple[int, int]) -> tuple[float, float]:
        gb = self.model.gridboxes.get(gridbox)
        if gb is None:
            return (0.0, 0.0)
        w, h = gb.slotsize
        if gb.fmt == "LEFT":
            return delta[1] * w, delta[0] * h
        return delta[0] * w, delta[1] * h

    def _show_ghosts(self, delta: tuple[int, int]) -> None:
        c = self.canvas
        for g in self._ghosts:
            c.delete(g)
        self._ghosts = []
        for tid in (self.selection or [self._press["tid"]]):
            node = self._by_id.get(tid)
            if node is None or not node.editable:
                continue
            dx, dy = self._cells_to_px_delta(node.gridbox, delta)
            ghost = CanvasNode(tid=tid, px=(node.px[0] + dx, node.px[1] + dy),
                               cell=node.cell, gridbox=node.gridbox, icon="",
                               name="", small=node.small)
            x0, y0, x1, y1 = self._node_rect(ghost)
            self._ghosts.append(c.create_rectangle(
                x0, y0, x1, y1, outline=self.palette.accent, dash=(4, 3), width=2))

    def _show_gridbox_ghost(self, event) -> None:
        c = self.canvas
        for g in self._ghosts:
            c.delete(g)
        self._ghosts = []
        name = self._press["gridbox"]
        rect = self._box_rects.get(name)
        if rect is None:
            return
        wx, wy = self._event_world(event)
        dx, dy = wx - self._press["world"][0], wy - self._press["world"][1]
        x0, y0 = self._world_to_screen((rect[0] + dx, rect[1] + dy))
        x1, y1 = self._world_to_screen((rect[2] + dx, rect[3] + dy))
        self._ghosts.append(c.create_rectangle(
            x0, y0, x1, y1, outline=self.palette.accent, dash=(4, 3), width=2))

    def _release_1(self, event) -> None:
        press, self._press = self._press, None
        for g in self._ghosts:
            self.canvas.delete(g)
        self._ghosts = []
        if not press or press.get("pan") or not press.get("dragging"):
            return
        if "gridbox" in press:
            wx, wy = self._event_world(event)
            delta = (wx - press["world"][0], wy - press["world"][1])
            if delta != (0.0, 0.0) and self._on_gridbox_move is not None:
                self._on_gridbox_move(press["gridbox"], delta)
            return
        delta = self._drag_delta(event, press)
        if delta and delta != (0, 0):
            ids = [t for t in (self.selection or [press["tid"]])
                   if (n := self._by_id.get(t)) and n.editable]
            if ids:
                self._on_move(ids, delta)

    def _double_1(self, event) -> None:
        tid = self._hit_node(event)
        if tid is None:
            wx, wy = self._event_world(event)
            name = self._hit_gridbox(event) or self._nearest_gridbox(wx, wy)
            cell = (0, 0)
            if name is not None:
                gb = self.model.gridboxes[name]
                cell = gb.px_to_cell((wx, wy))
            self._on_create(name, cell, (wx, wy))
        elif self._on_open is not None:
            self._on_open(tid)

    def _context(self, event) -> None:
        tid = self._hit_node(event)
        if tid is not None and tid not in self.selection:
            self.set_selection([tid], notify=True)
        self._on_context(event, tid, self._hit_gridbox(event),
                         self._event_world(event))

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-int(event.delta / 120) * 2, "units")
        self.minimap.refresh_viewport()

    def _wheel_h(self, event) -> None:
        self.canvas.xview_scroll(-int(event.delta / 120) * 2, "units")
        self.minimap.refresh_viewport()

    def _wheel_zoom(self, event) -> None:
        idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - self.zoom))
        idx += 1 if event.delta > 0 else -1
        self.set_zoom(ZOOM_STEPS[max(0, min(idx, len(ZOOM_STEPS) - 1))], event)

    def set_zoom(self, zoom: float, event=None) -> None:
        if zoom == self.zoom:
            return
        c = self.canvas
        if event is not None:
            world = self._screen_to_world(c.canvasx(event.x), c.canvasy(event.y))
            sx, sy = event.x, event.y
        else:
            world = self._screen_to_world(c.canvasx(c.winfo_width() / 2),
                                          c.canvasy(c.winfo_height() / 2))
            sx, sy = c.winfo_width() / 2, c.winfo_height() / 2
        self.zoom = zoom
        self._img_cache.clear()
        self.render()
        x, y = self._world_to_screen(world)
        sr = [float(v) for v in c.cget("scrollregion").split()]
        c.xview_moveto(max(0.0, (x - sx) / max(sr[2], 1)))
        c.yview_moveto(max(0.0, (y - sy) / max(sr[3], 1)))
        self.minimap.refresh_viewport()

    def _delete_key(self, _event) -> None:
        if self._on_delete is not None and self.selection:
            self._on_delete(list(self.selection))

    # --------------------------------------------------------------- link mode
    def start_link_mode(self, source_tid: str, kind: str) -> None:
        """kind: LINK_PATH | LINK_DEP | LINK_XOR"""
        self._link_mode = (source_tid, kind)
        self._link_multi = None
        self.canvas.configure(cursor="crosshair")
        self._show_link_status()
        self.canvas.focus_set()

    def cancel_link_mode(self) -> None:
        was_active = self._link_mode is not None
        self._link_mode = None
        self._link_multi = None
        self.canvas.configure(cursor="")
        self._link_status.place_forget()
        if was_active and self._on_link_end is not None:
            self._on_link_end()

    def _end_multi(self, which: str) -> None:
        if self._link_mode is not None and self._link_multi == which:
            self.cancel_link_mode()

    def _show_link_status(self) -> None:
        key = self._link_mode[1] if self._link_mode else "single"
        text = self._link_hints.get(self._link_multi or key,
                                    self._link_hints.get("single", ""))
        if not text:
            return
        self._link_status.configure(text=text)
        self._link_status.place(in_=self.canvas, x=10, rely=1.0, y=-10, anchor="sw")
        self._link_status.lift()

    @property
    def link_mode(self) -> tuple[str, str] | None:
        return self._link_mode


class _Minimap:
    """Collapsible overview map overlaid on the canvas' bottom-right corner."""

    W, H = 190, 150

    def __init__(self, owner: TechCanvas, palette):
        self.owner = owner
        self.palette = palette
        self.visible = True
        self._scale = 1.0
        self._off = (0.0, 0.0)

        self.frame = ttk.Frame(owner, style="Card.TFrame")
        self.toggle = tk.Label(owner, text="◱", cursor="hand2", bd=0,
                               bg=palette.surface_alt, fg=palette.text_muted,
                               font=("Segoe UI", 10), padx=6, pady=2)
        self.map = tk.Canvas(self.frame, width=self.W, height=self.H,
                             bg=palette.surface, highlightthickness=1,
                             highlightbackground=palette.border, bd=0)
        self.map.pack()
        self.map.bind("<Button-1>", self._jump)
        self.map.bind("<B1-Motion>", self._jump)
        self.toggle.bind("<Button-1>", lambda e: self.set_visible(not self.visible))
        self._place()

    def _place(self) -> None:
        self.toggle.place(relx=1.0, rely=1.0, x=-4, y=-4, anchor="se")
        if self.visible:
            self.frame.place(relx=1.0, rely=1.0, x=-4, y=-28, anchor="se")
        else:
            self.frame.place_forget()

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        self.toggle.configure(fg=self.palette.text if visible else self.palette.text_muted)
        self._place()
        if visible:
            self.render(self.owner)

    def render(self, owner: TechCanvas) -> None:
        if not self.visible:
            return
        m = self.map
        m.delete("all")
        nodes = owner.model.nodes
        if not nodes:
            return
        min_x, min_y, max_x, max_y = owner._bounds()
        span_x = max(max_x - min_x, 1)
        span_y = max(max_y - min_y, 1)
        self._scale = min((self.W - 8) / span_x, (self.H - 8) / span_y)
        self._off = (min_x, min_y)
        s = self._scale
        for n in nodes:
            x = 4 + (n.px[0] - min_x) * s
            y = 4 + (n.px[1] - min_y) * s
            r = max(1.2, s * 24)
            color = "#b08a3e" if n.doctrine else self.palette.text_muted
            m.create_rectangle(x - r, y - r, x + r, y + r, fill=color, outline="")
        self.refresh_viewport()

    def refresh_viewport(self) -> None:
        if not self.visible:
            return
        owner = self.owner
        m = self.map
        m.delete("vp")
        if not owner.model.nodes:
            return
        c = owner.canvas
        x0, y0 = owner._screen_to_world(c.canvasx(0), c.canvasy(0))
        x1 = x0 + max(c.winfo_width(), 1) / owner.zoom
        y1 = y0 + max(c.winfo_height(), 1) / owner.zoom
        s = self._scale
        m.create_rectangle(4 + (x0 - self._off[0]) * s, 4 + (y0 - self._off[1]) * s,
                           4 + (x1 - self._off[0]) * s, 4 + (y1 - self._off[1]) * s,
                           outline=self.palette.accent, width=1, tags="vp")

    def _jump(self, event) -> None:
        owner = self.owner
        if not owner.model.nodes or self._scale <= 0:
            return
        wx = (event.x - 4) / self._scale + self._off[0]
        wy = (event.y - 4) / self._scale + self._off[1]
        c = owner.canvas
        sr = [float(v) for v in c.cget("scrollregion").split()]
        x, y = owner._world_to_screen((wx, wy))
        c.xview_moveto(max(0.0, (x - c.winfo_width() / 2) / max(sr[2], 1)))
        c.yview_moveto(max(0.0, (y - c.winfo_height() / 2) / max(sr[3], 1)))
        self.refresh_viewport()
