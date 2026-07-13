"""Focus-layout templates — data + geometry helpers for the "Add template here"
insertion system.

`templates.json` groups templates by category; each template is a list of focus
placements ``{id, x, y, prerequisites, exclusive}``. The placement with ``id == 0``
is the *anchor*: it lands on the cell the user clicked, every other focus is offset
relative to it. ``prerequisites`` is a (possibly nested) ``and``/``or`` structure of
template-local ids; ``prereq_groups`` flattens it to HOI4's model — a list of OR
groups that are AND-ed together (each ``prerequisite = { focus … }`` block).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).parent / "templates.json"


@lru_cache(maxsize=1)
def load_templates() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return {}


def _anchor(placements: list[dict]) -> dict | None:
    if not placements:
        return None
    return next((p for p in placements if p.get("id") == 0), placements[0])


def template_cells(placements: list[dict],
                   base_cell: tuple[int, int]) -> list[tuple[int, int]]:
    """Absolute grid cells for a template anchored so ``id == 0`` is at base_cell."""
    anchor = _anchor(placements)
    if anchor is None:
        return []
    ox, oy = anchor.get("x", 0), anchor.get("y", 0)
    return [(base_cell[0] + p.get("x", 0) - ox, base_cell[1] + p.get("y", 0) - oy)
            for p in placements]


def prereq_groups(prereq: dict, id_map: dict[int, str]) -> list[list[str]]:
    """Flatten a template ``prerequisites`` dict into resolved OR-groups.

    ``and``: each entry is a separate prerequisite block (AND). ``or``: one block
    holding all entries (OR). Entries may themselves be nested ``and``/``or`` dicts,
    which are recursed into — so multiple, nested blocks are supported."""
    groups: list[list[str]] = []

    def walk(node) -> None:
        if not isinstance(node, dict):
            return
        for item in node.get("and", ()):
            if isinstance(item, int):
                if item in id_map:
                    groups.append([id_map[item]])
            elif isinstance(item, dict):
                walk(item)
        or_items = node.get("or")
        if or_items:
            grp: list[str] = []
            for entry in or_items:
                if isinstance(entry, int):
                    if entry in id_map:
                        grp.append(id_map[entry])
                elif isinstance(entry, dict):
                    walk(entry)
            if grp:
                groups.append(grp)

    walk(prereq)
    return groups
