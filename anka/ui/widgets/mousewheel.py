"""Guard against accidental mouse-wheel edits of form fields.

`ttk.Combobox` and `ttk.Spinbox` change their value when the wheel is rolled over
them (a class-level binding). In a long scrollable inspector that's a hazard: the
user scrolls the page and silently flips a dropdown or a number. `disable_form_wheel`
neutralises those class bindings app-wide — the wheel scrolls the page instead of
touching the field — while `enable_form_wheel` opts a specific widget back in for the
few places wheel editing is genuinely convenient (mod list, map brush size / mode).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_WHEEL_SEQS = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def disable_form_wheel(root) -> None:
    """Remove the value-changing wheel behaviour from every combobox and spinbox.

    The behaviour lives entirely in the ``TCombobox`` / ``TSpinbox`` class bindings,
    so replacing them with a no-op (that does *not* return "break") both stops the
    value change and lets the event fall through to the page-scroll handler."""
    noop = lambda _e: None                                   # noqa: E731
    for cls in ("TCombobox", "TSpinbox"):
        for seq in _WHEEL_SEQS:
            root.bind_class(cls, seq, noop)


def _wheel_delta(event) -> int:
    """+1 for a wheel-up notch, -1 for wheel-down (Windows/X11)."""
    if getattr(event, "num", 0) == 4:
        return 1
    if getattr(event, "num", 0) == 5:
        return -1
    return 1 if getattr(event, "delta", 0) > 0 else -1


def enable_form_wheel(widget) -> None:
    """Re-enable wheel value change on one combobox/spinbox (up = next / larger)."""
    def handler(event):
        delta = _wheel_delta(event)
        if isinstance(widget, ttk.Spinbox):
            _step_spinbox(widget, delta)
        else:                                                # ttk.Combobox
            values = widget.cget("values")
            if values:
                idx = widget.current()
                idx = 0 if idx < 0 else idx
                idx = min(len(values) - 1, max(0, idx - delta))   # up = previous
                widget.current(idx)
                widget.event_generate("<<ComboboxSelected>>")
        return "break"

    for seq in _WHEEL_SEQS:
        widget.bind(seq, handler)


def _cget_float(widget, option: str, default: float) -> float:
    try:
        return float(widget.cget(option))
    except (ValueError, tk.TclError):
        return default


def _step_spinbox(widget: ttk.Spinbox, delta: int) -> None:
    """Increment/decrement a spinbox by its `increment`, clamped to from_/to.
    Manual because ttk.Spinbox exposes no `invoke` command in some Tk builds."""
    try:
        cur = float(widget.get())
    except (ValueError, tk.TclError):
        cur = _cget_float(widget, "from", 0.0)
    inc = _cget_float(widget, "increment", 1.0) or 1.0
    lo = _cget_float(widget, "from", float("-inf"))
    hi = _cget_float(widget, "to", float("inf"))
    new = max(lo, min(hi, cur + delta * inc))
    widget.set(int(new) if float(new).is_integer() else round(new, 6))
