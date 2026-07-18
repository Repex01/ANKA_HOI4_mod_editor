"""WYSIWYG canvas for the GUI designer (pure view, FocusCanvas pattern).

Shows the Pillow-rendered frame as one bitmap; selection, hover, resize
handles, snap guides and drag ghosts are tk vector overlays on top. All
intents go up through callbacks — the tab owns the document, the commands and
the re-render. World coordinates = simulated-screen pixels; the canvas only
scales by zoom and pans.

Interactions: click select (Ctrl toggles, Alt cycles overlapping stack),
drag move (snap to sibling/parent edges, Alt on drop = reparent into the
container under the cursor), 8-handle resize, drag empty space / middle
button to pan, Ctrl+wheel cursor-anchored zoom, arrows nudge (Shift = ×10),
Delete, right-click context menu, armed click-to-place for palette items.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageTk

from ...core.guirender import Rect
from ...ui.widgets.mousewheel import bind_wheel, wheel_steps

ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
_MARGIN = 48
_HANDLE = 4          # half-size of a resize handle, px (screen)
_SNAP = 6            # snap distance, px (screen)

IndexPath = tuple[int, ...]


class GuiDesignCanvas(ttk.Frame):
    def __init__(self, master, palette, *,
                 on_select: Callable[[list[IndexPath]], None],
                 on_move: Callable[[list[IndexPath], tuple[float, float]], None],
                 on_resize: Callable[[IndexPath, Rect], None],
                 on_create: Callable[[str, IndexPath, tuple[float, float]], None],
                 on_reparent: Callable[[list[IndexPath], IndexPath], None],
                 on_context: Callable[[tk.Event, IndexPath | None], None],
                 on_delete: Callable[[list[IndexPath]], None],
                 on_zoom: Callable[[float], None] = lambda z: None):
        super().__init__(master, style="Card.TFrame")
        self.palette = palette
        self.on_select = on_select
        self.on_move = on_move
        self.on_resize = on_resize
        self.on_create = on_create
        self.on_reparent = on_reparent
        self.on_context = on_context
        self.on_delete = on_delete
        self.on_zoom = on_zoom

        self.zoom = 1.0
        self._resolution = (1920, 1080)
        self._image: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._photo_zoom = 0.0
        self._rects: dict[IndexPath, Rect] = {}
        self._order: list[IndexPath] = []          # paint order (bottom→top)
        self._containers: set[IndexPath] = set()
        self._selection: list[IndexPath] = []
        self._editable = True
        self._hover: IndexPath | None = None
        self._drag: dict | None = None
        self._place_type: str | None = None

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        c = tk.Canvas(self, bg=palette.bg, highlightthickness=0, bd=0)
        c.grid(row=0, column=0, sticky="nsew")
        self.canvas = c
        sx = ttk.Scrollbar(self, orient="horizontal", command=c.xview)
        sy = ttk.Scrollbar(self, orient="vertical", command=c.yview)
        sx.grid(row=1, column=0, sticky="ew")
        sy.grid(row=0, column=1, sticky="ns")
        c.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)

        c.bind("<ButtonPress-1>", self._press_1)
        c.bind("<B1-Motion>", self._motion_1)
        c.bind("<ButtonRelease-1>", self._release_1)
        c.bind("<Motion>", self._hover_motion)
        c.bind("<ButtonPress-2>", lambda e: c.scan_mark(e.x, e.y))
        c.bind("<B2-Motion>", lambda e: c.scan_dragto(e.x, e.y, gain=1))
        c.bind("<Button-3>", self._context)
        bind_wheel(c, self._wheel)
        bind_wheel(c, self._wheel_h, "Shift")
        bind_wheel(c, self._wheel_zoom, "Control")
        c.bind("<Delete>", lambda e: self.on_delete(list(self._selection)))
        c.bind("<Escape>", self._escape)
        for key, d in (("Left", (-1, 0)), ("Right", (1, 0)),
                       ("Up", (0, -1)), ("Down", (0, 1))):
            c.bind(f"<{key}>",
                   lambda e, dd=d: self._nudge(dd, 1))
            c.bind(f"<Shift-{key}>",
                   lambda e, dd=d: self._nudge(dd, 10))
        c.bind("<ButtonPress-1>", lambda e: c.focus_set(), add="+")

    # ------------------------------------------------------------------ scene
    def set_scene(self, image: Image.Image | None,
                  rects: dict[IndexPath, Rect],
                  containers: set[IndexPath],
                  resolution: tuple[int, int],
                  selection: list[IndexPath],
                  editable: bool) -> None:
        self._image = image
        self._photo_zoom = 0.0                       # force re-scale
        self._rects = dict(rects)
        self._order = list(rects.keys())
        self._containers = set(containers)
        self._resolution = resolution
        self._selection = list(selection)
        self._editable = editable
        self._redraw()

    def set_selection(self, selection: list[IndexPath]) -> None:
        self._selection = list(selection)
        self._draw_overlays()

    def begin_place(self, type_key: str) -> None:
        """Arm click-to-place creation for a palette widget type."""
        if not self._editable:
            return
        self._place_type = type_key
        self.canvas.configure(cursor="crosshair")

    # ------------------------------------------------------------- transforms
    def _w2c(self, x: float, y: float) -> tuple[float, float]:
        return x * self.zoom, y * self.zoom

    def _c2w(self, event) -> tuple[float, float]:
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        return cx / self.zoom, cy / self.zoom

    # ---------------------------------------------------------------- drawing
    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        rw, rh = self._resolution
        zw, zh = rw * self.zoom, rh * self.zoom
        c.configure(scrollregion=(-_MARGIN, -_MARGIN,
                                  zw + _MARGIN, zh + _MARGIN))
        # screen backdrop + border
        c.create_rectangle(0, 0, zw, zh, fill=self.palette.surface_alt,
                           outline=self.palette.border, tags="backdrop")
        if self._image is not None:
            if self._photo_zoom != self.zoom:
                img = self._image
                if self.zoom != 1.0:
                    img = img.resize((max(1, int(img.width * self.zoom)),
                                      max(1, int(img.height * self.zoom))),
                                     Image.NEAREST if self.zoom >= 1
                                     else Image.BILINEAR)
                self._photo = ImageTk.PhotoImage(img)
                self._photo_zoom = self.zoom
            c.create_image(0, 0, image=self._photo, anchor="nw", tags="frame")
        self._draw_overlays()

    def _draw_overlays(self) -> None:
        c = self.canvas
        c.delete("ov")
        accent = self.palette.accent
        if self._hover is not None and self._hover not in self._selection:
            r = self._rects.get(self._hover)
            if r is not None:
                x0, y0 = self._w2c(r.x, r.y)
                x1, y1 = self._w2c(r.x2, r.y2)
                c.create_rectangle(x0, y0, x1, y1, outline=accent,
                                   dash=(3, 3), tags="ov")
        for path in self._selection:
            r = self._rects.get(path)
            if r is None:
                continue
            x0, y0 = self._w2c(r.x, r.y)
            x1, y1 = self._w2c(r.x2, r.y2)
            c.create_rectangle(x0, y0, x1, y1, outline=accent, width=2,
                               tags="ov")
        if len(self._selection) == 1 and self._editable:
            r = self._rects.get(self._selection[0])
            if r is not None:
                for hx, hy, _tag in self._handles(r):
                    c.create_rectangle(hx - _HANDLE, hy - _HANDLE,
                                       hx + _HANDLE, hy + _HANDLE,
                                       fill=accent, outline="", tags="ov")

    def _handles(self, r: Rect) -> list[tuple[float, float, str]]:
        x0, y0 = self._w2c(r.x, r.y)
        x1, y1 = self._w2c(r.x2, r.y2)
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        return [(x0, y0, "nw"), (xm, y0, "n"), (x1, y0, "ne"),
                (x0, ym, "w"), (x1, ym, "e"),
                (x0, y1, "sw"), (xm, y1, "s"), (x1, y1, "se")]

    # -------------------------------------------------------------- hit tests
    def _hit(self, wx: float, wy: float,
             containers_only: bool = False) -> IndexPath | None:
        for path in reversed(self._order):
            if containers_only and path not in self._containers:
                continue
            r = self._rects.get(path)
            if r is not None and r.contains(wx, wy):
                return path
        return None

    def _hit_stack(self, wx: float, wy: float) -> list[IndexPath]:
        return [p for p in reversed(self._order)
                if (r := self._rects.get(p)) is not None
                and r.contains(wx, wy)]

    def _hit_handle(self, event) -> str | None:
        if len(self._selection) != 1 or not self._editable:
            return None
        r = self._rects.get(self._selection[0])
        if r is None:
            return None
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        for hx, hy, tag in self._handles(r):
            if abs(cx - hx) <= _HANDLE + 2 and abs(cy - hy) <= _HANDLE + 2:
                return tag
        return None

    # ------------------------------------------------------------------ mouse
    def _press_1(self, event) -> None:
        wx, wy = self._c2w(event)
        if self._place_type is not None:
            target = self._hit(wx, wy, containers_only=True) or ()
            type_key, self._place_type = self._place_type, None
            self.canvas.configure(cursor="")
            self.on_create(type_key, target, (wx, wy))
            return
        handle = self._hit_handle(event)
        if handle is not None:
            self._drag = {"kind": "resize", "handle": handle,
                          "start": (wx, wy),
                          "rect0": self._rects[self._selection[0]],
                          "moved": False}
            return
        hit = self._hit(wx, wy)
        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)
        alt = bool(event.state & 0x20000)
        if hit is None:
            if self._selection:
                self.on_select([])
            self.canvas.scan_mark(event.x, event.y)
            self._drag = {"kind": "pan"}
            return
        if alt:
            stack = self._hit_stack(wx, wy)
            if self._selection and self._selection[0] in stack:
                i = stack.index(self._selection[0])
                hit = stack[(i + 1) % len(stack)]
            self.on_select([hit])
            return
        if ctrl or shift:
            # additive multi-select: Ctrl toggles, Shift only adds
            sel = list(self._selection)
            if hit in sel:
                if ctrl:
                    sel.remove(hit)
            else:
                sel.append(hit)
            self.on_select(sel)
            return
        if hit not in self._selection:
            self.on_select([hit])
        if self._editable:
            rects = {p: self._rects[p] for p in (self._selection or [hit])
                     if p in self._rects}
            self._drag = {"kind": "move", "start": (wx, wy),
                          "rects0": rects, "moved": False, "delta": (0, 0)}

    def _motion_1(self, event) -> None:
        if self._drag is None:
            return
        kind = self._drag["kind"]
        if kind == "pan":
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            return
        wx, wy = self._c2w(event)
        sx, sy = self._drag["start"]
        dx, dy = wx - sx, wy - sy
        if abs(dx) < 1 and abs(dy) < 1 and not self._drag["moved"]:
            return
        self._drag["moved"] = True
        c = self.canvas
        c.delete("ghost")
        if kind == "move":
            dx, dy = self._snap_delta(dx, dy)
            self._drag["delta"] = (dx, dy)
            for r in self._drag["rects0"].values():
                x0, y0 = self._w2c(r.x + dx, r.y + dy)
                x1, y1 = self._w2c(r.x2 + dx, r.y2 + dy)
                c.create_rectangle(x0, y0, x1, y1,
                                   outline=self.palette.accent,
                                   dash=(4, 2), tags="ghost")
        elif kind == "resize":
            rect = self._resize_rect(self._drag["rect0"],
                                     self._drag["handle"], dx, dy)
            self._drag["rect"] = rect
            x0, y0 = self._w2c(rect.x, rect.y)
            x1, y1 = self._w2c(rect.x2, rect.y2)
            c.create_rectangle(x0, y0, x1, y1, outline=self.palette.accent,
                               dash=(4, 2), tags="ghost")

    def _release_1(self, event) -> None:
        drag, self._drag = self._drag, None
        self.canvas.delete("ghost")
        self.canvas.delete("guide")
        if drag is None or drag["kind"] == "pan" or not drag.get("moved"):
            return
        if drag["kind"] == "move":
            dx, dy = drag["delta"]
            alt = bool(event.state & 0x20000)
            if alt:
                wx, wy = self._c2w(event)
                target = self._hit_container_excluding(
                    wx, wy, set(drag["rects0"].keys()))
                self.on_reparent(list(drag["rects0"].keys()), target or ())
                return
            if dx or dy:
                self.on_move(list(drag["rects0"].keys()), (dx, dy))
        elif drag["kind"] == "resize" and "rect" in drag:
            self.on_resize(self._selection[0], drag["rect"])

    def _hit_container_excluding(self, wx: float, wy: float,
                                 exclude: set[IndexPath]) -> IndexPath | None:
        for path in reversed(self._order):
            if path not in self._containers:
                continue
            if any(path == e or path[:len(e)] == e for e in exclude):
                continue
            r = self._rects.get(path)
            if r is not None and r.contains(wx, wy):
                return path
        return None

    def _hover_motion(self, event) -> None:
        if self._drag is not None:
            return
        handle = self._hit_handle(event)
        if handle is not None:
            cursors = {"nw": "size_nw_se", "se": "size_nw_se",
                       "ne": "size_ne_sw", "sw": "size_ne_sw",
                       "n": "size_ns", "s": "size_ns",
                       "w": "size_we", "e": "size_we"}
            self.canvas.configure(cursor=cursors.get(handle, ""))
            return
        if self._place_type is None:
            self.canvas.configure(cursor="")
        wx, wy = self._c2w(event)
        hover = self._hit(wx, wy)
        if hover != self._hover:
            self._hover = hover
            self._draw_overlays()

    def _context(self, event) -> None:
        wx, wy = self._c2w(event)
        self.on_context(event, self._hit(wx, wy))

    def _escape(self, _event) -> None:
        if self._place_type is not None:
            self._place_type = None
            self.canvas.configure(cursor="")
        self._drag = None
        self.canvas.delete("ghost")
        self.canvas.delete("guide")

    def _nudge(self, d: tuple[int, int], factor: int) -> None:
        if self._selection and self._editable:
            self.on_move(list(self._selection),
                         (d[0] * factor, d[1] * factor))

    # --------------------------------------------------------------- snapping
    snap_enabled = True

    def _snap_delta(self, dx: float, dy: float) -> tuple[float, float]:
        dx, dy = round(dx), round(dy)
        if not self.snap_enabled or not self._drag:
            return dx, dy
        rects0 = self._drag["rects0"]
        primary_path = next(iter(rects0))
        r = rects0[primary_path]
        moved = Rect(r.x + dx, r.y + dy, r.w, r.h)
        tol = _SNAP / self.zoom
        cand_x: list[float] = [0.0, float(self._resolution[0])]
        cand_y: list[float] = [0.0, float(self._resolution[1])]
        parent = primary_path[:-1]
        for path, rect in self._rects.items():
            if path in rects0:
                continue
            if path[:-1] == parent or path == parent:
                cand_x += [rect.x, rect.x2, (rect.x + rect.x2) / 2]
                cand_y += [rect.y, rect.y2, (rect.y + rect.y2) / 2]
        best_dx, best_dy = dx, dy
        guides: list[tuple[str, float]] = []
        for edge in (moved.x, moved.x2, (moved.x + moved.x2) / 2):
            for cand in cand_x:
                if abs(edge - cand) <= tol:
                    best_dx = dx + (cand - edge)
                    guides.append(("v", cand))
                    break
            else:
                continue
            break
        for edge in (moved.y, moved.y2, (moved.y + moved.y2) / 2):
            for cand in cand_y:
                if abs(edge - cand) <= tol:
                    best_dy = dy + (cand - edge)
                    guides.append(("h", cand))
                    break
            else:
                continue
            break
        self.canvas.delete("guide")
        rw, rh = self._resolution
        for axis, value in guides:
            if axis == "v":
                x, _ = self._w2c(value, 0)
                self.canvas.create_line(x, 0, x, rh * self.zoom,
                                        fill=self.palette.accent,
                                        dash=(2, 4), tags="guide")
            else:
                _, y = self._w2c(0, value)
                self.canvas.create_line(0, y, rw * self.zoom, y,
                                        fill=self.palette.accent,
                                        dash=(2, 4), tags="guide")
        return best_dx, best_dy

    def _resize_rect(self, r: Rect, handle: str, dx: float,
                     dy: float) -> Rect:
        x0, y0, x1, y1 = r.x, r.y, r.x2, r.y2
        if "w" in handle:
            x0 = min(x0 + dx, x1 - 1)
        if "e" in handle:
            x1 = max(x1 + dx, x0 + 1)
        if "n" in handle:
            y0 = min(y0 + dy, y1 - 1)
        if "s" in handle:
            y1 = max(y1 + dy, y0 + 1)
        return Rect(round(x0), round(y0), round(x1 - x0), round(y1 - y0))

    # ------------------------------------------------------------------ wheel
    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-1 if wheel_steps(event) > 0 else 1, "units")

    def _wheel_h(self, event) -> None:
        self.canvas.xview_scroll(-1 if wheel_steps(event) > 0 else 1, "units")

    def _wheel_zoom(self, event) -> None:
        index = min(range(len(ZOOM_STEPS)),
                    key=lambda i: abs(ZOOM_STEPS[i] - self.zoom))
        index += 1 if wheel_steps(event) > 0 else -1
        index = max(0, min(len(ZOOM_STEPS) - 1, index))
        self.set_zoom(ZOOM_STEPS[index], event)

    def set_zoom(self, zoom: float, event=None) -> None:
        if zoom == self.zoom:
            return
        c = self.canvas
        if event is not None:
            wx = c.canvasx(event.x) / self.zoom
            wy = c.canvasy(event.y) / self.zoom
        else:
            wx = (c.canvasx(0) + c.winfo_width() / 2) / self.zoom
            wy = (c.canvasy(0) + c.winfo_height() / 2) / self.zoom
        self.zoom = zoom
        self._redraw()
        # keep the anchor point under the cursor / view center
        ax, ay = self._w2c(wx, wy)
        px = event.x if event is not None else c.winfo_width() / 2
        py = event.y if event is not None else c.winfo_height() / 2
        rw, rh = self._resolution
        span_x = rw * zoom + 2 * _MARGIN
        span_y = rh * zoom + 2 * _MARGIN
        c.xview_moveto((ax - px + _MARGIN) / span_x)
        c.yview_moveto((ay - py + _MARGIN) / span_y)
        self.on_zoom(zoom)
