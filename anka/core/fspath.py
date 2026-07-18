"""Cross-platform filesystem path helpers.

HOI4 content references files case-insensitively (a Windows heritage): vanilla
``.gfx`` files carry ``texturefile`` entries whose spelling does not match the
file on disk (``sociaL_democracy``, ``NOR_nasjonal...`` vs actual lowercase),
and mods made on Windows are worse. On Windows the mismatch is invisible; on a
case-sensitive filesystem (Linux) a naive ``Path.exists()`` misses and icons,
portraits or map bitmaps silently disappear from the editor.

`resolve_ci` returns an existing path equal to the requested one up to case,
correcting each component against the real directory listing when the exact
spelling is absent.
"""
from __future__ import annotations

import os
from pathlib import Path

# Successful case-corrections only — a correction can only become stale if the
# corrected file is deleted, which the .exists() re-check below catches.
_hits: dict[str, Path] = {}


def resolve_ci(path: Path) -> Path | None:
    """An existing path equal to `path` ignoring case, or None.

    Fast path: when `path` exists as spelled (always the case on Windows/macOS
    case-insensitive filesystems) it is returned unchanged, so callers can use
    this unconditionally instead of ``.exists()`` checks.
    """
    if path.exists():
        return path
    key = str(path)
    hit = _hits.get(key)
    if hit is not None:
        if hit.exists():
            return hit
        del _hits[key]
    parent = path.parent
    if parent == path:                     # drive/fs root itself is missing
        return None
    parent = resolve_ci(parent)
    if parent is None:
        return None
    try:
        names = os.listdir(parent)
    except OSError:
        return None
    low = path.name.lower()
    for name in names:
        if name.lower() == low:
            found = parent / name
            _hits[key] = found
            return found
    return None
