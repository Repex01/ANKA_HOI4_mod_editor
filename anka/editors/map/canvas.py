"""Map viewport: a tk.Canvas showing a rendered crop of the province bitmap.

Pure view over `MapService.render_view`: it owns the camera (offset in map
pixels + zoom), converts screen↔map coordinates, and asks a background worker
thread for viewport images (latest-request-wins), so the UI never blocks on the
11.5M-pixel map. Results come back through a queue polled with ``after`` —
Tk objects are only ever touched on the main thread.

Callbacks: ``on_click(x, y, event)`` (map coords, left click), ``on_hover(x, y)``
(map coords or None when the cursor leaves the map), ``on_paint(x, y, event)``
(left-drag in paint mode — phase 4).
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageTk

ZOOM_STEPS = (0.125, 0.18, 0.25, 0.35, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)


class MapCanvas(ttk.Frame):
    def __init__(self, master, palette, *,
                 render: Callable,           # (mode, rect, scale, selected, highlight) -> PIL.Image
                 map_size: Callable[[], tuple[int, int] | None],
                 on_click: Callable[[int, int, tk.Event], None] | None = None,
                 on_hover: Callable[[int | None, int | None], None] | None = None,
                 on_paint: Callable[[int, int, tk.Event], None] | None = None,
                 on_paint_end: Callable[[], None] | None = None):
        super().__init__(master, style="TFrame")
        self.palette = palette
        self._render = render
        self._map_size = map_size
        self._on_click = on_click
        self._on_hover = on_hover
        self._on_paint = on_paint
        self._on_paint_end = on_paint_end

        self.mode = "provinces"
        self.zoom = 0.25
        self.offset = (0.0, 0.0)            # map coords of the viewport's top-left
        self.selected: set[int] = set()
        self.highlight: set[int] = set()
        self.paint_mode = False              # left-drag paints instead of panning
        self._photo: ImageTk.PhotoImage | None = None
        self._photo_key: tuple | None = None
        self._pan: tuple | None = None
        self._painting = False

        self._req_lock = threading.Condition()
        self._req: tuple | None = None
        self._req_seq = 0
        self._results: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.started = False         # started lazily on first draw
        self._poll_job: str | None = None
        self._destroyed = False

        self.canvas = tk.Canvas(self, bg=palette.bg, highlightthickness=0, bd=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        c = self.canvas
        c.bind("<Configure>", lambda e: self.refresh())
        c.bind("<ButtonPress-1>", self._press1)
        c.bind("<B1-Motion>", self._motion1)
        c.bind("<ButtonRelease-1>", self._release1)
        for btn in (2, 3):
            c.bind(f"<ButtonPress-{btn}>", self._pan_start)
            c.bind(f"<B{btn}-Motion>", self._pan_move)
            c.bind(f"<ButtonRelease-{btn}>", self._pan_end)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Shift-MouseWheel>", self._wheel_h)
        c.bind("<Control-MouseWheel>", self._wheel_zoom)
        c.bind("<Motion>", self._hover)
        c.bind("<Leave>", lambda e: self._on_hover and self._on_hover(None, None))
        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------- coordinates
    def screen_to_map(self, sx: float, sy: float) -> tuple[int, int]:
        ox, oy = self.offset
        return int(ox + sx / self.zoom), int(oy + sy / self.zoom)

    def map_to_screen(self, mx: float, my: float) -> tuple[float, float]:
        ox, oy = self.offset
        return (mx - ox) * self.zoom, (my - oy) * self.zoom

    def _clamp_offset(self) -> None:
        size = self._map_size()
        if size is None:
            return
        w, h = size
        vw = max(self.canvas.winfo_width(), 1) / self.zoom
        vh = max(self.canvas.winfo_height(), 1) / self.zoom
        ox = min(max(self.offset[0], -vw * 0.25), w - vw * 0.75)
        oy = min(max(self.offset[1], -vh * 0.25), h - vh * 0.75)
        self.offset = (ox, oy)

    # ------------------------------------------------------------------ camera
    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.refresh()

    def set_selection(self, ids: set[int], highlight: set[int] | None = None) -> None:
        self.selected = set(ids)
        self.highlight = set(highlight or ())
        self.refresh()

    def fit_map(self) -> None:
        """Zoom out so the whole map fits the widget."""
        size = self._map_size()
        if size is None:
            return
        w, h = size
        vw = max(self.canvas.winfo_width(), 32)
        vh = max(self.canvas.winfo_height(), 32)
        fit = min(vw / w, vh / h)
        self.zoom = next((z for z in reversed(ZOOM_STEPS) if z <= fit), ZOOM_STEPS[0])
        self.offset = ((w - vw / self.zoom) / 2, (h - vh / self.zoom) / 2)
        self.refresh()

    def center_on(self, mx: float, my: float, zoom: float | None = None) -> None:
        if zoom is not None:
            self.zoom = zoom
        vw = max(self.canvas.winfo_width(), 1) / self.zoom
        vh = max(self.canvas.winfo_height(), 1) / self.zoom
        self.offset = (mx - vw / 2, my - vh / 2)
        self._clamp_offset()
        self.refresh()

    def zoom_to_bbox(self, bbox: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = bbox
        bw, bh = max(x1 - x0 + 1, 1), max(y1 - y0 + 1, 1)
        vw = max(self.canvas.winfo_width(), 32)
        vh = max(self.canvas.winfo_height(), 32)
        fit = min(vw / (bw * 1.6), vh / (bh * 1.6), ZOOM_STEPS[-1])
        self.zoom = next((z for z in reversed(ZOOM_STEPS) if z <= fit), ZOOM_STEPS[0])
        self.center_on((x0 + x1) / 2, (y0 + y1) / 2)

    # ------------------------------------------------------------------ render
    def refresh(self) -> None:
        """Request an async re-render of the current viewport."""
        if self._destroyed:
            return
        vw = self.canvas.winfo_width()
        vh = self.canvas.winfo_height()
        if vw < 2 or vh < 2:
            return
        self._clamp_offset()
        ox, oy = self.offset
        x0 = max(0, int(ox))
        y0 = max(0, int(oy))
        x1 = int(ox + vw / self.zoom) + 1
        y1 = int(oy + vh / self.zoom) + 1
        req = (self.mode, (x0, y0, x1, y1), self.zoom,
               frozenset(self.selected), frozenset(self.highlight))
        with self._req_lock:
            self._req_seq += 1
            self._req = (self._req_seq, req)
            self._req_lock.notify()
        if not self._worker.started:
            self._worker.started = True
            self._worker.start()
        if self._poll_job is None:
            self._poll_job = self.after(15, self._poll)

    def _work(self) -> None:
        while True:
            with self._req_lock:
                while self._req is None:
                    self._req_lock.wait()
                seq, req = self._req
                self._req = None
            mode, rect, scale, selected, highlight = req
            try:
                img = self._render(mode, rect, scale, set(selected), set(highlight))
            except Exception:
                continue
            with self._req_lock:
                stale = self._req_seq != seq
            if not stale:
                self._results.put((seq, rect, scale, img))

    def _poll(self) -> None:
        self._poll_job = None
        if self._destroyed:
            return
        item = None
        try:
            while True:
                item = self._results.get_nowait()
        except queue.Empty:
            pass
        if item is not None:
            seq, rect, scale, img = item
            with self._req_lock:
                fresh = self._req_seq == seq
            if fresh:
                self._blit(rect, scale, img)
                self._blitted_seq = seq
        # Keep polling until the newest requested frame has been shown.
        with self._req_lock:
            latest = self._req_seq
        if getattr(self, "_blitted_seq", 0) < latest:
            self._poll_job = self.after(15, self._poll)

    def _blit(self, rect: tuple[int, int, int, int], scale: float,
              img: Image.Image) -> None:
        sx, sy = self.map_to_screen(rect[0], rect[1])
        self._photo = ImageTk.PhotoImage(img)
        c = self.canvas
        c.delete("map")
        c.create_image(int(sx), int(sy), image=self._photo, anchor="nw", tags="map")
        c.tag_lower("map")

    # ------------------------------------------------------------------ events
    def _press1(self, event) -> None:
        if self.paint_mode and self._on_paint is not None:
            self._painting = True
            mx, my = self.screen_to_map(event.x, event.y)
            self._on_paint(mx, my, event)
            return
        self._pan = ("maybe", event.x, event.y, *self.offset)

    def _motion1(self, event) -> None:
        if self._painting and self._on_paint is not None:
            mx, my = self.screen_to_map(event.x, event.y)
            self._on_paint(mx, my, event)
            return
        if self._pan is None:
            return
        kind, px, py, ox, oy = self._pan
        if kind == "maybe" and abs(event.x - px) + abs(event.y - py) < 4:
            return
        self._pan = ("pan", px, py, ox, oy)
        self.offset = (ox - (event.x - px) / self.zoom,
                       oy - (event.y - py) / self.zoom)
        self.refresh()

    def _release1(self, event) -> None:
        if self._painting:
            self._painting = False
            if self._on_paint_end is not None:
                self._on_paint_end()
            return
        pan, self._pan = self._pan, None
        if pan is not None and pan[0] == "maybe" and self._on_click is not None:
            mx, my = self.screen_to_map(event.x, event.y)
            self._on_click(mx, my, event)

    def _pan_start(self, event) -> None:
        self._pan = ("pan", event.x, event.y, *self.offset)

    def _pan_move(self, event) -> None:
        if self._pan is None:
            return
        _kind, px, py, ox, oy = self._pan
        self.offset = (ox - (event.x - px) / self.zoom,
                       oy - (event.y - py) / self.zoom)
        self.refresh()

    def _pan_end(self, _event) -> None:
        self._pan = None

    def _wheel(self, event) -> None:
        self.offset = (self.offset[0],
                       self.offset[1] - int(event.delta / 120) * 60 / self.zoom)
        self.refresh()

    def _wheel_h(self, event) -> None:
        self.offset = (self.offset[0] - int(event.delta / 120) * 60 / self.zoom,
                       self.offset[1])
        self.refresh()

    def _wheel_zoom(self, event) -> None:
        idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - self.zoom))
        idx += 1 if event.delta > 0 else -1
        new_zoom = ZOOM_STEPS[max(0, min(idx, len(ZOOM_STEPS) - 1))]
        if new_zoom == self.zoom:
            return
        # Anchor the zoom at the cursor: the map point under it must not move.
        mx, my = self.screen_to_map(event.x, event.y)
        self.zoom = new_zoom
        self.offset = (mx - event.x / self.zoom, my - event.y / self.zoom)
        self.refresh()

    def _hover(self, event) -> None:
        if self._on_hover is not None:
            mx, my = self.screen_to_map(event.x, event.y)
            self._on_hover(mx, my)

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._destroyed = True
