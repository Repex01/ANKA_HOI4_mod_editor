"""A vertically scrollable frame. Place children inside `.body`."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .mousewheel import bind_wheel, unbind_wheel_all, wheel_steps


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, bg: str = "#282a36", autohide: bool = False,
                 width: int | None = None, **kwargs):
        super().__init__(master, **kwargs)
        self._autohide = autohide
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        if width is not None:
            # Override the Canvas default request (~376px) so the frame does
            # not inflate a weight-0 grid column beyond its minsize.
            self._canvas.configure(width=width)
        self._scroll = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self.body = ttk.Frame(self._canvas)

        self._win = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._canvas.configure(yscrollcommand=self._scroll.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        if not autohide:
            self._scroll.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<Enter>", lambda e: self._bind_wheel())
        self._canvas.bind("<Leave>", lambda e: self._unbind_wheel())

    def set_bg(self, color: str) -> None:
        self._canvas.configure(bg=color)

    def _on_body_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._update_bar()

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)
        self._update_bar()

    def _update_bar(self) -> None:
        """autohide mode: show the scrollbar only when the content overflows."""
        if not self._autohide:
            return
        need = self.body.winfo_reqheight() > self._canvas.winfo_height()
        if need and not self._scroll.winfo_ismapped():
            self._scroll.pack(side="right", fill="y")
        elif not need and self._scroll.winfo_ismapped():
            self._scroll.pack_forget()

    def _bind_wheel(self) -> None:
        bind_wheel(self._canvas, self._on_wheel, bind_all=True)

    def _unbind_wheel(self) -> None:
        unbind_wheel_all(self._canvas)

    def _on_wheel(self, event) -> None:
        steps = wheel_steps(event)
        if steps:
            self._canvas.yview_scroll(-steps, "units")
