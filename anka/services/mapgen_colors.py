"""Deterministic pool of free province colors.

New provinces need RGB colors that are unique across the whole map (the engine's
only hard constraint; ``(0,0,0)`` is reserved for province 0). This module owns
one deterministic generator — a golden-ratio hue walk over HSV tiers, so
consecutive colors are visually distinct and the sequence is stable between
runs — and the offline packaging of its output into a data file:

    python -m anka.services.mapgen_colors

builds ``anka/config/data/free_province_colors.bin`` (packed uint8 r,g,b
triples) from the *vanilla* ``definition.csv`` (colors already used by the base
game are skipped). At runtime `MapService.free_colors` reads the pool and
filters out colors occupied by the current mod; if the pool runs dry it falls
back to `iter_colors` directly, skipping everything occupied.
"""
from __future__ import annotations

import colorsys
from pathlib import Path
from typing import Iterable, Iterator

from ..config.constants import Paths

POOL_FILE = Path(__file__).resolve().parents[1] / "config" / "data" / "free_province_colors.bin"
POOL_SIZE = 30000

# Irrational step per HSV channel: the walk fills the whole cube quasi-uniformly
# (a Kronecker/Weyl sequence), so consecutive colors are visually distinct and
# the supply of distinct 24-bit values is practically unlimited.
_H_STEP = 0.6180339887498949          # 1/φ
_S_STEP = 0.41421356237309515         # √2 − 1
_V_STEP = 0.7320508075688772          # √3 − 1


def iter_colors(seed: int = 0) -> Iterator[tuple[int, int, int]]:
    """Endless deterministic sequence of RGB colors (никогда не выдаёт (0,0,0)).

    Occasional duplicates are possible after quantization to 24-bit; callers
    dedupe against their own "seen/occupied" set.
    """
    i = seed
    while True:
        h = (i * _H_STEP) % 1.0
        s = 0.35 + 0.65 * ((i * _S_STEP) % 1.0)
        v = 0.30 + 0.70 * ((i * _V_STEP) % 1.0)
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        rgb = (int(r * 255), int(g * 255), int(b * 255))
        if rgb != (0, 0, 0):
            yield rgb
        i += 1


def generate_free_colors(count: int,
                         occupied: set[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """First `count` colors of the deterministic walk not in `occupied`."""
    out: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int, int]] = set(occupied)
    budget = max(count * 200, 100_000)     # hard stop: never loop forever
    for rgb in iter_colors():
        budget -= 1
        if budget <= 0:
            break
        if rgb in seen:
            continue
        seen.add(rgb)
        out.append(rgb)
        if len(out) >= count:
            break
    return out


def load_pool(path: Path | None = None) -> list[tuple[int, int, int]]:
    """Read the packed pool file; empty list when it is missing/corrupt."""
    path = path or POOL_FILE
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    n = len(raw) // 3
    return [(raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]) for i in range(n)]


def save_pool(colors: Iterable[tuple[int, int, int]], path: Path | None = None) -> Path:
    path = path or POOL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(c for rgb in colors for c in rgb))
    return path


def _vanilla_colors(game_path: Path) -> set[tuple[int, int, int]]:
    occupied: set[tuple[int, int, int]] = set()
    csv = game_path / "map" / "definition.csv"
    for line in csv.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(";")
        if len(parts) >= 4:
            try:
                occupied.add((int(parts[1]), int(parts[2]), int(parts[3])))
            except ValueError:
                continue
    return occupied


def main() -> None:
    import json
    settings = json.loads(Paths.SETTINGS_FILE.read_text(encoding="utf-8"))
    game_path = Path(settings["game_path"])
    occupied = _vanilla_colors(game_path)
    colors = generate_free_colors(POOL_SIZE, occupied)
    path = save_pool(colors)
    print(f"Wrote {len(colors)} colors to {path} "
          f"(skipped {len(occupied)} vanilla colors)")


if __name__ == "__main__":
    main()
