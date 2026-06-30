"""Thin abstraction over tkinterdnd2 so the app degrades gracefully without it.

If tkinterdnd2 is installed, `create_root` returns a DnD-capable Tk root and
`register_file_drop` wires a widget to receive OS file drops. If not, the app still
runs — drop zones simply fall back to click-to-browse.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    DND_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


def create_root() -> tk.Tk:
    """Create the application root, DnD-enabled when possible."""
    if DND_AVAILABLE:
        return TkinterDnD.Tk()
    return tk.Tk()


def _parse_drop_paths(data: str) -> list[str]:
    """tkinterdnd2 delivers a brace/space-delimited string of paths."""
    paths: list[str] = []
    token = ""
    in_brace = False
    for ch in data:
        if ch == "{":
            in_brace = True
            token = ""
        elif ch == "}":
            in_brace = False
            paths.append(token)
            token = ""
        elif ch == " " and not in_brace:
            if token:
                paths.append(token)
            token = ""
        else:
            token += ch
    if token:
        paths.append(token)
    return paths


def register_file_drop(widget: tk.Widget, on_drop: Callable[[list[str]], None]) -> bool:
    """Register `widget` as a file drop target. Returns False if DnD is unavailable."""
    if not DND_AVAILABLE:
        return False
    try:
        widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
        widget.dnd_bind("<<Drop>>", lambda e: on_drop(_parse_drop_paths(e.data)))  # type: ignore[attr-defined]
        return True
    except Exception:
        return False
