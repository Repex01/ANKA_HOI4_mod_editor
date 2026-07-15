"""Keyboard-layout-independent hotkey binding.

Tk matches key bindings by *keysym*, so ``<Control-z>`` never fires while a
non-latin keyboard layout (e.g. Russian) is active — the physical Z key then
produces ``Cyrillic_ya``. On Windows ``event.keycode`` carries the virtual-key
code of the *physical* key regardless of layout, so every hotkey is bound
twice: the plain keysym sequence (latin layouts — Tk picks the more specific
binding, so nothing fires twice) plus one shared ``<Control-KeyPress>``
fallback per widget that dispatches on keycode.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

# Windows virtual-key codes for letters equal ord() of the uppercase letter;
# X11 keycodes differ, but there the keysym binding usually works — the
# fallback additionally checks a Cyrillic keysym map for common layouts.
_RU_TO_LAT = dict(zip("йцукенгшщзхъфывапролджэячсмитьбю",
                      "qwertyuiop[]asdfghjkl;'zxcvbnm,."))

_DISPATCH_ATTR = "_anka_hotkey_dispatch"


def _dispatch_table(widget) -> dict[tuple[str, bool], Callable]:
    """One ``<Control-KeyPress>`` handler per widget, shared by all hotkeys."""
    table = getattr(widget, _DISPATCH_ATTR, None)
    if table is None:
        table = {}
        setattr(widget, _DISPATCH_ATTR, table)

        def handler(event, _table=table):
            letter = _physical_letter(event)
            if letter is None:
                return None
            shift = bool(event.state & 0x0001)
            callback = _table.get((letter, shift))
            if callback is None:
                return None
            return callback(event)

        widget.bind("<Control-KeyPress>", handler, add=True)
    return table


def _physical_letter(event: tk.Event) -> str | None:
    """The latin letter of the pressed physical key, layout-independent."""
    keysym = (event.keysym or "").lower()
    if len(keysym) == 1 and "a" <= keysym <= "z":
        return keysym
    mapped = _RU_TO_LAT.get(keysym)
    if mapped and mapped.isalpha():
        return mapped
    # Windows: virtual-key codes of letters are 0x41..0x5A
    keycode = getattr(event, "keycode", 0) or 0
    if 0x41 <= keycode <= 0x5A:
        return chr(keycode).lower()
    return None


def bind_ctrl(widget, letter: str, callback, *, shift: bool = False) -> None:
    """Bind Ctrl+<letter> (optionally +Shift) so it fires on any keyboard
    layout. `callback` receives the event and its return value is passed to
    Tk ("break" stops further handling)."""
    letter = letter.lower()
    sequence = (f"<Control-Shift-{letter.upper()}>" if shift
                else f"<Control-{letter}>")
    widget.bind(sequence, callback)
    _dispatch_table(widget)[(letter, shift)] = callback
