"""Terrain categories (``common/terrain``) for the map editor.

A province's *landscape* is its terrain category name in ``definition.csv``; the
categories themselves (name, color, is_water, movement cost) live in the
``categories = { ... }`` block of ``common/terrain/*.txt``. Parsed game-first,
mod second, so a mod can add or override categories. Lists are never hardcoded —
mods extend them freely.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.pdx import Block, Scalar, parse_file
from ..domain.mod import ModContext

TERRAIN_DIR = "common/terrain"


@dataclass(frozen=True)
class TerrainCat:
    name: str
    color: tuple[int, int, int]
    is_water: bool
    movement_cost: float


class TerrainService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._cats: dict[str, TerrainCat] | None = None

    def categories(self, refresh: bool = False) -> dict[str, TerrainCat]:
        if self._cats is None or refresh:
            self._cats = self._load()
        return self._cats

    def land_terrains(self) -> list[str]:
        return [c.name for c in self.categories().values() if not c.is_water]

    def water_terrains(self) -> list[str]:
        return [c.name for c in self.categories().values() if c.is_water]

    def color_of(self, name: str) -> tuple[int, int, int]:
        cat = self.categories().get(name)
        return cat.color if cat is not None else (128, 128, 128)

    # ------------------------------------------------------------------ load
    def _load(self) -> dict[str, TerrainCat]:
        cats: dict[str, TerrainCat] = {}
        # Game first, mod second: same-name categories are overridden by the mod.
        for root in (self.ctx.game_path, self.ctx.mod.path):
            folder = root / TERRAIN_DIR
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                categories = block.get_block("categories")
                if categories is None:
                    continue
                for pair in categories.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    cats[pair.key] = self._parse_cat(pair.key, pair.value)
        return cats

    @staticmethod
    def _parse_cat(name: str, body: Block) -> TerrainCat:
        color = (128, 128, 128)
        cblock = body.get_block("color")
        if cblock is not None:
            nums = [v.as_int() for v in cblock.array_values()
                    if isinstance(v, Scalar)]
            if len(nums) >= 3:
                color = (nums[0], nums[1], nums[2])
        is_water = False
        water = body.get("is_water")
        if isinstance(water, Scalar):
            is_water = water.as_bool()
        cost = 1.0
        mc = body.get("movement_cost")
        if isinstance(mc, Scalar):
            try:
                cost = mc.as_float()
            except (TypeError, ValueError):
                cost = 1.0
        return TerrainCat(name=name, color=color, is_water=is_water,
                          movement_cost=cost)
