"""A drop zone that accepts an image via OS drag&drop or click-to-browse.

Shows a live preview of the chosen image and invokes `on_image(path)` so the owning
editor can convert/save it. Works with or without tkinterdnd2 (see `dnd`).
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

from PIL import Image, ImageTk

from ...config.constants import SUPPORTED_IMPORT_FORMATS
from .dnd import register_file_drop


class ImageDropZone(ttk.Frame):
    def __init__(
        self,
        master,
        on_image: Callable[[Path], None],
        *,
        prompt: str = "Drop an image here",
        preview_size: tuple[int, int] = (160, 120),
        palette=None,
        **kwargs,
    ):
        super().__init__(master, style="Card.TFrame", **kwargs)
        self._on_image = on_image
        self._prompt = prompt
        self._preview_size = preview_size
        self._palette = palette
        self._photo: ImageTk.PhotoImage | None = None

        border = palette.border if palette else "#3c3f4c"
        bg = palette.surface_alt if palette else "#31333f"
        self._canvas = tk.Canvas(
            self, width=preview_size[0], height=preview_size[1],
            highlightthickness=2, highlightbackground=border, bg=bg, bd=0,
            cursor="hand2",
        )
        self._canvas.pack(padx=4, pady=4)
        self._label = ttk.Label(self, text=prompt, style="CardMuted.TLabel",
                                 justify="center", anchor="center")
        self._label.pack(pady=(0, 4))

        self._draw_placeholder()
        self._canvas.bind("<Button-1>", lambda e: self.browse())
        self._dnd_ok = register_file_drop(self._canvas, self._handle_drop)

    # --- public ----------------------------------------------------------
    def browse(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in SUPPORTED_IMPORT_FORMATS)
        path = filedialog.askopenfilename(
            title=self._prompt,
            filetypes=[("Images", patterns), ("All files", "*.*")],
        )
        if path:
            self._accept(Path(path))

    def show_image(self, path: str | Path) -> None:
        """Render a preview without firing the callback (e.g. existing flag)."""
        self._render_preview(Path(path))

    def clear(self) -> None:
        self._photo = None
        self._draw_placeholder()

    # --- internals -------------------------------------------------------
    def _handle_drop(self, paths: list[str]) -> None:
        for raw in paths:
            p = Path(raw)
            if p.suffix.lower() in SUPPORTED_IMPORT_FORMATS:
                self._accept(p)
                return

    def _accept(self, path: Path) -> None:
        if path.suffix.lower() not in SUPPORTED_IMPORT_FORMATS:
            return
        self._render_preview(path)
        self._on_image(path)

    def _render_preview(self, path: Path) -> None:
        try:
            img = Image.open(path)
            img.thumbnail(self._preview_size, Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self._canvas.delete("all")
            self._canvas.create_image(
                self._preview_size[0] // 2, self._preview_size[1] // 2,
                image=self._photo,
            )
        except Exception:
            self._draw_placeholder(error=True)

    def _draw_placeholder(self, error: bool = False) -> None:
        self._canvas.delete("all")
        w, h = self._preview_size
        muted = (self._palette.text_muted if self._palette else "#9aa0ad")
        self._canvas.create_text(
            w // 2, h // 2, text=("✕" if error else "+"),
            fill=muted, font=("Segoe UI", 28),
        )
