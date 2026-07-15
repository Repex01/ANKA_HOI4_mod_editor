"""Tech-chain templates (ported from the focuses template system).

``templates.json`` groups templates by category; each template is a list of
*placements*: ``{id, x, y, paths: [{to, coeff}], xor: [ids], cost,
year_offset}``. Offsets are folder-position cells relative to the anchor
placement (``id == 0``), which lands on the clicked cell. ``year_offset`` is
added to the base ``start_year`` (the anchor's year, default 1936) so a yearly
research chain gets sensible ahead-of-time costs out of the box.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_TEMPLATES_FILE = Path(__file__).with_name("templates.json")


@lru_cache(maxsize=1)
def load_templates() -> dict[str, dict[str, list[dict]]]:
    try:
        with open(_TEMPLATES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def anchor_of(placements: list[dict]) -> dict:
    return next((p for p in placements if p.get("id") == 0), placements[0])


def template_cells(placements: list[dict],
                   base_cell: tuple[int, int]) -> list[tuple[int, int]]:
    """Absolute folder cells of every placement when the anchor is at `base_cell`."""
    anchor = anchor_of(placements)
    ox, oy = anchor.get("x", 0), anchor.get("y", 0)
    return [(base_cell[0] + p.get("x", 0) - ox,
             base_cell[1] + p.get("y", 0) - oy) for p in placements]
