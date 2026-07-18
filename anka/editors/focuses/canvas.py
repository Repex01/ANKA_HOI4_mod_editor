"""Focus tree canvas: game-like grid rendering + direct manipulation.

Pure view: it receives a display model (`CanvasNode` list + link lists) and emits
user intents through callbacks (`on_select`, `on_move`, `on_create`, `on_link`,
`on_context`, `on_open`). All PDX/domain logic stays in the editor/service.

Interactions: wheel = scroll, Shift+wheel = horizontal, Ctrl+wheel = zoom (anchored
at the cursor), left-drag on a focus = move (grid-snapped, multi-selection aware),
left-drag on empty = pan, double-click on empty cell = create focus, right-click =
context menu, link mode = click a target focus after choosing "add link" in the menu.
A collapsible minimap sits in the bottom-right corner.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from ...ui.widgets.mousewheel import bind_wheel, wheel_steps

# Base (zoom = 1.0) cell metrics; the HOI4 grid is wider than tall.
CELL_W = 108
CELL_H = 126
ICON_W = 76
ICON_H = 60
_MARGIN_CELLS = 2
ZOOM_STEPS = (0.3, 0.4, 0.5, 0.65, 0.8, 1.0, 1.2, 1.45)
_DRAG_THRESHOLD = 6  # px before a press becomes a drag


@dataclass
class CanvasNode:
    fid: str
    pos: tuple[int, int]              # absolute grid cell
    icon: str                         # GFX sprite name
    name: str
    kind: str = "focus"               # focus | shared_focus | joint_focus
    editable: bool = True
    days: int = 70
    has_issues: bool = False


@dataclass
class CanvasModel:
    nodes: list[CanvasNode] = field(default_factory=list)
    prereqs: list[tuple[str, str, bool]] = field(default_factory=list)  # child, parent, is_or
    mutexes: list[tuple[str, str]] = field(default_factory=list)


class FocusCanvas(ttk.Frame):
    def __init__(self, master, palette, *,
                 resolve_icon: Callable[[str], Path | None],
                 on_select: Callable[[list[str]], None],
                 on_move: Callable[[list[str], tuple[int, int]], None],
                 on_create: Callable[[tuple[int, int]], None],
                 on_link: Callable[[str, str, str], None],
                 on_context: Callable[[tk.Event, str | None, tuple[int, int]], None],
                 on_open: Callable[[str], None] | None = None,
                 on_delete: Callable[[list[str]], None] | None = None,
                 on_link_end: Callable[[], None] | None = None,
                 link_hints: dict[str, str] | None = None,
                 icons_ready: Callable[[], bool] | None = None):
        super().__init__(master, style="TFrame")
        self.palette = palette
        self._resolve_icon = resolve_icon
        self._icons_ready = icons_ready
        self._on_delete = on_delete
        self._on_link_end = on_link_end
        self._link_hints = link_hints or {}
        self._on_select = on_select
        self._on_move = on_move
        self._on_create = on_create
        self._on_link = on_link
        self._on_context = on_context
        self._on_open = on_open

        self.model = CanvasModel()
        self.zoom = 1.0
        self.show_grid = True
        self.selection: list[str] = []
        self._origin = (0, 0)              # cell offset so all coords are positive
        self._by_id: dict[str, CanvasNode] = {}
        self._img_cache: dict[tuple[str, float], ImageTk.PhotoImage] = {}
        self._pil_cache: dict[str, Image.Image] = {}
        self._icon_queue: list[str] = []      # sprites awaiting async load
        self._queued: set[str] = set()
        self._pump_job: str | None = None
        self._link_mode: tuple[str, str] | None = None    # (source fid, kind)
        self._link_multi: str | None = None               # None | "or" | "and"
        self._press: dict | None = None
        self._ghosts: list[int] = []
        self._preview_ids: list[int] = []      # translucent template-placement cells

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

        # Bottom-left status pill, shown only while picking prerequisites.
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
        bind_wheel(c, self._wheel)
        bind_wheel(c, self._wheel_h, "Shift")
        bind_wheel(c, self._wheel_zoom, "Control")
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
        self._by_id = {n.fid: n for n in model.nodes}
        self.render()
        if anchor is not None:
            self._restore_view(anchor)
        else:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
            self.minimap.refresh_viewport()

    def set_selection(self, fids: list[str], notify: bool = False) -> None:
        self.selection = [f for f in fids if f in self._by_id]
        self._apply_selection()
        if notify:
            self._on_select(list(self.selection))

    # ---------------------------------------------------------------- mapping
    def _cell_px(self) -> tuple[float, float]:
        return CELL_W * self.zoom, CELL_H * self.zoom

    def _cell_to_px(self, cell: tuple[int, int]) -> tuple[float, float]:
        """Top-left pixel of a grid cell."""
        cw, ch = self._cell_px()
        return (cell[0] + self._origin[0]) * cw, (cell[1] + self._origin[1]) * ch

    def _px_to_cell(self, px: float, py: float) -> tuple[int, int]:
        cw, ch = self._cell_px()
        return int(px // cw) - self._origin[0], int(py // ch) - self._origin[1]

    def _bounds(self) -> tuple[int, int, int, int]:
        xs = [n.pos[0] for n in self.model.nodes] or [0]
        ys = [n.pos[1] for n in self.model.nodes] or [0]
        return min(xs), min(ys), max(xs), max(ys)

    def _view_world(self) -> tuple[float, float]:
        """Grid coordinates currently at the viewport's top-left corner."""
        cw, ch = self._cell_px()
        return (self.canvas.canvasx(0) / cw - self._origin[0],
                self.canvas.canvasy(0) / ch - self._origin[1])

    def _restore_view(self, world: tuple[float, float]) -> None:
        cw, ch = self._cell_px()
        x = (world[0] + self._origin[0]) * cw
        y = (world[1] + self._origin[1]) * ch
        sr = [float(v) for v in (self.canvas.cget("scrollregion").split() or [0, 0, 1, 1])]
        w = max(sr[2] - sr[0], 1)
        h = max(sr[3] - sr[1], 1)
        self.canvas.xview_moveto(max(0.0, x / w))
        self.canvas.yview_moveto(max(0.0, y / h))
        self.minimap.refresh_viewport()

    def center_on(self, fid: str) -> None:
        node = self._by_id.get(fid)
        if node is None:
            return
        cw, ch = self._cell_px()
        px, py = self._cell_to_px(node.pos)
        vw = max(self.canvas.winfo_width(), 1)
        vh = max(self.canvas.winfo_height(), 1)
        sr = [float(v) for v in self.canvas.cget("scrollregion").split()]
        self.canvas.xview_moveto(max(0.0, (px + cw / 2 - vw / 2) / max(sr[2], 1)))
        self.canvas.yview_moveto(max(0.0, (py + ch / 2 - vh / 2) / max(sr[3], 1)))
        self.minimap.refresh_viewport()

    # ------------------------------------------------------ template preview
    def preview_cells(self, cells: list[tuple[int, int]],
                      anchor: tuple[int, int] | None = None) -> None:
        """Draw translucent phantom focus tiles at `cells` (template hover preview);
        the `anchor` cell (the one that will sit under the cursor) is highlighted."""
        self.clear_preview()
        c = self.canvas
        cw, ch = self._cell_px()
        px, py = cw * 0.12, ch * 0.10
        for cell in cells:
            x0, y0 = self._cell_to_px(cell)
            hot = cell == anchor
            self._preview_ids.append(c.create_rectangle(
                x0 + px, y0 + py, x0 + cw - px, y0 + ch - py,
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
        self._origin = (_MARGIN_CELLS - min_x, _MARGIN_CELLS - min_y)
        cw, ch = self._cell_px()
        width = (max_x - min_x + 2 * _MARGIN_CELLS + 1) * cw
        height = (max_y - min_y + 2 * _MARGIN_CELLS + 1) * ch
        c.configure(scrollregion=(0, 0, width, height))

        if self.show_grid:
            self._draw_grid(width, height)
        for child, parent, is_or in self.model.prereqs:
            self._draw_prereq(child, parent, is_or)
        for a, b in self.model.mutexes:
            self._draw_mutex(a, b)
        for node in self.model.nodes:
            self._draw_node(node)
        self._apply_selection()
        self.minimap.render(self)
        self._schedule_pump()

    def set_grid(self, visible: bool) -> None:
        if visible != self.show_grid:
            self.show_grid = visible
            self.render()

    def _draw_grid(self, width: float, height: float) -> None:
        """Faint cell borders under everything else."""
        c = self.canvas
        cw, ch = self._cell_px()
        color = self.palette.surface_alt if self.palette.is_dark else self.palette.border
        x = 0.0
        while x <= width + 1:
            c.create_line(x, 0, x, height, fill=color, width=1, tags="grid")
            x += cw
        y = 0.0
        while y <= height + 1:
            c.create_line(0, y, width, y, fill=color, width=1, tags="grid")
            y += ch
        c.tag_lower("grid")

    def _node_rect(self, node: CanvasNode) -> tuple[float, float, float, float]:
        px, py = self._cell_to_px(node.pos)
        cw, ch = self._cell_px()
        pad_x = cw * 0.06
        return px + pad_x, py + ch * 0.04, px + cw - pad_x, py + ch * 0.96

    def _draw_node(self, node: CanvasNode) -> None:
        c = self.canvas
        p = self.palette
        x0, y0, x1, y1 = self._node_rect(node)
        tags = ("node", f"f:{node.fid}")

        fill = p.surface if node.editable else p.surface_alt
        outline = {"shared_focus": "#3f8f5f", "joint_focus": "#b08a3e"}.get(node.kind, p.border)
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline,
                           width=1, tags=tags)

        icon = self._photo(node.icon)
        cx = (x0 + x1) / 2
        icon_y = y0 + (y1 - y0) * 0.06
        # Anchor text below a fixed icon box so async icon swaps don't shift labels.
        text_top = icon_y + int(ICON_H * self.zoom) + 3
        c.create_image(cx, icon_y, image=icon, anchor="n",
                       tags=tags + (f"img:{node.icon}",))

        size = max(7, round(8.4 * self.zoom))
        c.create_text(cx, text_top, text=node.name, anchor="n",
                      width=(x1 - x0) - 6, justify="center",
                      font=("Segoe UI", size),
                      fill=p.text if node.editable else p.text_muted, tags=tags)

        if node.kind != "focus":
            badge = "S" if node.kind == "shared_focus" else "J"
            c.create_text(x0 + 5, y0 + 3, text=badge, anchor="nw",
                          font=("Segoe UI Semibold", size), fill=outline, tags=tags)
        if not node.editable:
            c.create_text(x1 - 5, y0 + 3, text="🔒", anchor="ne",
                          font=("Segoe UI", size), tags=tags)
        if node.has_issues:
            r = max(3, 4 * self.zoom)
            c.create_oval(x1 - 2 * r - 3, y1 - 2 * r - 3, x1 - 3, y1 - 3,
                          fill=p.danger, outline="", tags=tags)

    def _photo(self, sprite: str) -> ImageTk.PhotoImage:
        """PhotoImage for a sprite at the current zoom. Unloaded sprites get a
        placeholder immediately and are queued for async loading (`_pump_icons`
        patches the canvas items in place once the texture is read)."""
        pil = self._pil_cache.get(sprite)
        if pil is None:
            if sprite and sprite not in self._queued:
                self._queued.add(sprite)
                self._icon_queue.append(sprite)
            key = ("~placeholder~", self.zoom)
            if key not in self._img_cache:
                self._img_cache[key] = ImageTk.PhotoImage(
                    self._fit(self._placeholder()))
            return self._img_cache[key]
        key = (sprite, self.zoom)
        if key not in self._img_cache:
            self._img_cache[key] = ImageTk.PhotoImage(self._fit(pil))
        return self._img_cache[key]

    def _fit(self, pil: Image.Image) -> Image.Image:
        w = max(8, int(ICON_W * self.zoom))
        h = max(8, int(ICON_H * self.zoom))
        scale = min(w / pil.width, h / pil.height)
        return pil.resize((max(1, int(pil.width * scale)),
                           max(1, int(pil.height * scale))), Image.LANCZOS)

    def invalidate_icon(self, sprite: str) -> None:
        self._pil_cache.pop(sprite, None)
        self._queued.discard(sprite)
        self._img_cache = {k: v for k, v in self._img_cache.items() if k[0] != sprite}

    def _schedule_pump(self, delay: int = 15) -> None:
        if self._pump_job is None and self._icon_queue:
            self._pump_job = self.after(delay, self._pump_icons)

    def _pump_icons(self) -> None:
        self._pump_job = None
        if not self._icon_queue:
            return
        if self._icons_ready is not None and not self._icons_ready():
            self._schedule_pump(200)      # sprite map still building in background
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
            self._pil_cache[sprite] = pil if pil is not None else self._placeholder()
            items = self.canvas.find_withtag(f"img:{sprite}")
            if items:
                photo = self._photo(sprite)
                for item in items:
                    self.canvas.itemconfigure(item, image=photo)
        self._schedule_pump()

    def _placeholder(self) -> Image.Image:
        img = Image.new("RGBA", (ICON_W, ICON_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((2, 2, ICON_W - 3, ICON_H - 3), radius=8,
                            outline=self.palette.text_muted, width=2)
        d.text((ICON_W // 2, ICON_H // 2), "?", anchor="mm",
               fill=self.palette.text_muted)
        return img

    def _anchor_points(self, fid: str) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        """(top, bottom, center) pixel anchors of a node."""
        node = self._by_id[fid]
        x0, y0, x1, y1 = self._node_rect(node)
        cx = (x0 + x1) / 2
        return (cx, y0), (cx, y1), (cx, (y0 + y1) / 2)

    def _draw_prereq(self, child: str, parent: str, is_or: bool) -> None:
        if child not in self._by_id or parent not in self._by_id:
            return
        (ct, _cb, _cc) = self._anchor_points(child)
        (_pt, pb, _pc) = self._anchor_points(parent)
        mid_y = pb[1] + max(6.0, (ct[1] - pb[1]) * 0.4)
        points = [pb[0], pb[1], pb[0], mid_y, ct[0], mid_y, ct[0], ct[1]]
        kw = {"dash": (5, 4)} if is_or else {}
        self.canvas.create_line(
            *points, fill=self.palette.text_muted, width=max(1, round(1.4 * self.zoom)),
            tags=("link", f"ln:{child}", f"ln:{parent}"), **kw)

    def _draw_mutex(self, a: str, b: str) -> None:
        if a not in self._by_id or b not in self._by_id:
            return
        (_at, _ab, ac) = self._anchor_points(a)
        (_bt, _bb, bc) = self._anchor_points(b)
        r = max(2.5, 3.5 * self.zoom)
        color = self.palette.danger
        self.canvas.create_line(ac[0], ac[1], bc[0], bc[1], fill=color,
                                dash=(3, 5), width=max(1, round(1.4 * self.zoom)),
                                tags=("link", f"ln:{a}", f"ln:{b}"))
        for cx, cy in (ac, bc):
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                    fill=color, outline="",
                                    tags=("link", f"ln:{a}", f"ln:{b}"))

    def _apply_selection(self) -> None:
        c = self.canvas
        c.delete("selbox")
        c.itemconfigure("link", width=max(1, round(1.4 * self.zoom)))
        for fid in self.selection:
            node = self._by_id.get(fid)
            if node is None:
                continue
            x0, y0, x1, y1 = self._node_rect(node)
            c.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2,
                               outline=self.palette.accent, width=2, tags="selbox")
            c.itemconfigure(f"ln:{fid}", width=max(2, round(2.2 * self.zoom)))
            c.tag_raise(f"ln:{fid}")
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

    def _event_cell(self, event) -> tuple[int, int]:
        return self._px_to_cell(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def _press_1(self, event) -> None:
        self.canvas.focus_set()
        fid = self._hit_node(event)
        if self._link_mode is not None:
            src, kind = self._link_mode
            if fid is None or fid == src:
                self.cancel_link_mode()          # clicked away: abort picking
                return
            shift = bool(event.state & 0x0001)
            ctrl = bool(event.state & 0x0004)
            if kind == "prerequisite" and (self._link_multi or shift or ctrl):
                # Multi-pick: Shift accumulates one OR group, Ctrl adds AND groups;
                # the mode ends when the modifier key is released (or Esc).
                if self._link_multi is None:
                    self._link_multi = "or" if shift else "and"
                    self._show_link_status()
                self._on_link(src, fid,
                              "prerequisite_or" if self._link_multi == "or"
                              else "prerequisite")
                return
            self.cancel_link_mode()
            self._on_link(src, fid, kind)
            return
        if fid is None:
            self.set_selection([], notify=True)
            self.canvas.scan_mark(event.x, event.y)
            self._press = {"pan": True}
            return
        if fid not in self.selection:
            add = bool(event.state & 0x0004)          # Ctrl held
            self.set_selection(self.selection + [fid] if add else [fid], notify=True)
        self._press = {"pan": False, "fid": fid, "x": event.x, "y": event.y,
                       "cell": self._event_cell(event), "dragging": False}

    def _motion_1(self, event) -> None:
        if self._press is None:
            return
        if self._press.get("pan"):
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            self.minimap.refresh_viewport()
            return
        node = self._by_id.get(self._press["fid"])
        if node is None or not node.editable:
            return
        if not self._press["dragging"]:
            if abs(event.x - self._press["x"]) + abs(event.y - self._press["y"]) < _DRAG_THRESHOLD:
                return
            self._press["dragging"] = True
        delta = self._drag_delta(event)
        self._show_ghosts(delta)

    def _drag_delta(self, event, press: dict | None = None) -> tuple[int, int]:
        cell = self._event_cell(event)
        start = (press or self._press)["cell"]
        return cell[0] - start[0], cell[1] - start[1]

    def _show_ghosts(self, delta: tuple[int, int]) -> None:
        c = self.canvas
        for g in self._ghosts:
            c.delete(g)
        self._ghosts = []
        for fid in (self.selection or [self._press["fid"]]):
            node = self._by_id.get(fid)
            if node is None or not node.editable:
                continue
            x0, y0, x1, y1 = self._node_rect(CanvasNode(
                fid=fid, pos=(node.pos[0] + delta[0], node.pos[1] + delta[1]),
                icon="", name=""))
            self._ghosts.append(c.create_rectangle(
                x0, y0, x1, y1, outline=self.palette.accent, dash=(4, 3), width=2))

    def _release_1(self, event) -> None:
        press, self._press = self._press, None
        for g in self._ghosts:
            self.canvas.delete(g)
        self._ghosts = []
        if not press or press.get("pan") or not press.get("dragging"):
            return
        delta = self._drag_delta(event, press)   # self._press is already cleared
        if delta != (0, 0):
            ids = [f for f in (self.selection or [press["fid"]])
                   if (n := self._by_id.get(f)) and n.editable]
            if ids:
                self._on_move(ids, delta)

    def _double_1(self, event) -> None:
        fid = self._hit_node(event)
        if fid is None:
            self._on_create(self._event_cell(event))
        elif self._on_open is not None:
            self._on_open(fid)

    def _context(self, event) -> None:
        fid = self._hit_node(event)
        if fid is not None and fid not in self.selection:
            self.set_selection([fid], notify=True)
        self._on_context(event, fid, self._event_cell(event))

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-wheel_steps(event) * 2, "units")
        self.minimap.refresh_viewport()

    def _wheel_h(self, event) -> None:
        self.canvas.xview_scroll(-wheel_steps(event) * 2, "units")
        self.minimap.refresh_viewport()

    def _wheel_zoom(self, event) -> None:
        idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - self.zoom))
        idx += 1 if wheel_steps(event) > 0 else -1
        self.set_zoom(ZOOM_STEPS[max(0, min(idx, len(ZOOM_STEPS) - 1))], event)

    def set_zoom(self, zoom: float, event=None) -> None:
        if zoom == self.zoom:
            return
        c = self.canvas
        cw, ch = self._cell_px()
        if event is not None:
            wx = c.canvasx(event.x) / cw
            wy = c.canvasy(event.y) / ch
            sx, sy = event.x, event.y
        else:
            wx = c.canvasx(c.winfo_width() / 2) / cw
            wy = c.canvasy(c.winfo_height() / 2) / ch
            sx, sy = c.winfo_width() / 2, c.winfo_height() / 2
        self.zoom = zoom
        self._img_cache.clear()
        self.render()
        cw, ch = self._cell_px()
        sr = [float(v) for v in c.cget("scrollregion").split()]
        c.xview_moveto(max(0.0, (wx * cw - sx) / max(sr[2], 1)))
        c.yview_moveto(max(0.0, (wy * ch - sy) / max(sr[3], 1)))
        self.minimap.refresh_viewport()

    def _delete_key(self, _event) -> None:
        if self._on_delete is not None and self.selection:
            self._on_delete(list(self.selection))

    # --------------------------------------------------------------- link mode
    def start_link_mode(self, source_fid: str, kind: str) -> None:
        """kind: 'prerequisite' | 'mutually_exclusive'"""
        self._link_mode = (source_fid, kind)
        self._link_multi = None
        self.canvas.configure(cursor="crosshair")
        if kind == "prerequisite":
            self._show_link_status()
        self.canvas.focus_set()      # so modifier KeyRelease events reach us

    def cancel_link_mode(self) -> None:
        was_active = self._link_mode is not None
        self._link_mode = None
        self._link_multi = None
        self.canvas.configure(cursor="")
        self._link_status.place_forget()
        if was_active and self._on_link_end is not None:
            self._on_link_end()

    def _end_multi(self, which: str) -> None:
        """Releasing the modifier that started a multi-pick commits & exits."""
        if self._link_mode is not None and self._link_multi == which:
            self.cancel_link_mode()

    def _show_link_status(self) -> None:
        text = self._link_hints.get(self._link_multi or "single", "")
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

    def __init__(self, owner: FocusCanvas, palette):
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

    def render(self, owner: FocusCanvas) -> None:
        if not self.visible:
            return
        m = self.map
        m.delete("all")
        nodes = owner.model.nodes
        if not nodes:
            return
        min_x, min_y, max_x, max_y = owner._bounds()
        span_x = max(max_x - min_x + 1, 1)
        span_y = max(max_y - min_y + 1, 1)
        self._scale = min((self.W - 8) / span_x, (self.H - 8) / span_y)
        self._off = (min_x, min_y)
        s = self._scale
        colors = {"focus": self.palette.text_muted,
                  "shared_focus": "#3f8f5f", "joint_focus": "#b08a3e"}
        for n in nodes:
            x = 4 + (n.pos[0] - min_x) * s
            y = 4 + (n.pos[1] - min_y) * s
            r = max(1.2, s * 0.32)
            m.create_rectangle(x - r, y - r, x + r, y + r,
                               fill=colors.get(n.kind, self.palette.text_muted),
                               outline="")
        self.refresh_viewport()

    def refresh_viewport(self) -> None:
        if not self.visible:
            return
        owner = self.owner
        m = self.map
        m.delete("vp")
        if not owner.model.nodes:
            return
        cw, ch = owner._cell_px()
        c = owner.canvas
        x0 = c.canvasx(0) / cw - owner._origin[0]
        y0 = c.canvasy(0) / ch - owner._origin[1]
        x1 = x0 + max(c.winfo_width(), 1) / cw
        y1 = y0 + max(c.winfo_height(), 1) / ch
        s = self._scale
        m.create_rectangle(4 + (x0 - self._off[0]) * s, 4 + (y0 - self._off[1]) * s,
                           4 + (x1 - self._off[0]) * s, 4 + (y1 - self._off[1]) * s,
                           outline=self.palette.accent, width=1, tags="vp")

    def _jump(self, event) -> None:
        owner = self.owner
        if not owner.model.nodes or self._scale <= 0:
            return
        cell_x = (event.x - 4) / self._scale + self._off[0]
        cell_y = (event.y - 4) / self._scale + self._off[1]
        cw, ch = owner._cell_px()
        c = owner.canvas
        sr = [float(v) for v in c.cget("scrollregion").split()]
        px = (cell_x + owner._origin[0]) * cw - c.winfo_width() / 2
        py = (cell_y + owner._origin[1]) * ch - c.winfo_height() / 2
        c.xview_moveto(max(0.0, px / max(sr[2], 1)))
        c.yview_moveto(max(0.0, py / max(sr[3], 1)))
        self.refresh_viewport()

    # scrollbar callbacks keep the viewport rect live
