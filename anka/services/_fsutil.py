"""Small filesystem helpers shared by services."""
from __future__ import annotations

from pathlib import Path


def ensure_filename_case(target: Path) -> Path:
    """Make the on-disk filename exactly match `target` (case included).

    On Windows the filesystem is case-insensitive, so writing to "X.txt" when "x.txt"
    already exists keeps the *old* casing on disk. Mod files meant to override a vanilla
    file must carry the engine-matching exact name (Clausewitz compares override paths
    case-sensitively), so we rename any case-only variant to the wanted name first.
    """
    parent = target.parent
    if not parent.is_dir():
        return target
    for existing in parent.iterdir():
        if existing.name != target.name and existing.name.lower() == target.name.lower():
            try:
                existing.rename(target)
            except OSError:
                pass
            break
    return target
