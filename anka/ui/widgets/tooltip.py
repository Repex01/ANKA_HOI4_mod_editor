"""Hover tooltips + a locale-driven help system.

`Tooltip` is a plain themed hover popup for any widget. `attach_help` is the
extensible layer editors use: help texts live in the locale files under ``help.*``
keys (e.g. ``help.script.available``), so adding a hint anywhere in the app is just
adding a locale entry — no code changes. Missing keys attach nothing.
"""
from __future__ import annotations

import tkinter as tk


class Tooltip:
    """Delayed hover popup. `text` may be a string or a zero-arg callable
    (evaluated at show time, so dynamic hints stay fresh)."""

    DELAY_MS = 500
    WRAP = 360

    def __init__(self, widget, text, palette=None):
        self.widget = widget
        self._text = text
        self._palette = palette
        self._tip: tk.Toplevel | None = None
        self._job: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._job = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        self._job = None
        text = self._text() if callable(self._text) else self._text
        if not text or self._tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        p = self._palette
        bg = p.surface_alt if p else "#31333f"
        fg = p.text if p else "#e8e8ec"
        border = p.border if p else "#3c3f4c"
        frame = tk.Frame(tip, bg=border, padx=1, pady=1)
        frame.pack()
        tk.Label(frame, text=text, bg=bg, fg=fg, justify="left",
                 wraplength=self.WRAP, padx=8, pady=5,
                 font=("Segoe UI", 9)).pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


def attach_help(widget, t, topic: str, palette=None) -> Tooltip | None:
    """Attach a locale-driven tooltip: looks up ``help.<topic>`` via the translator.
    Returns None (attaches nothing) when the key has no translation — so new topics
    are enabled purely by adding locale entries."""
    key = f"help.{topic}"
    text = t(key)
    if text == key:            # translator fallback: key itself => no help defined
        return None
    return Tooltip(widget, text, palette)
