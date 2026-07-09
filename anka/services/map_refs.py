"""Small map-editor reference catalogs: state categories and resources.

Both are plain "name → definition" blocks under ``common/``; parsed game-first,
mod second (mods extend/override). Kept together — each is a dozen lines and the
map editor is their only consumer.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.pdx import Block, Scalar, parse_file
from ..domain.mod import ModContext

STATE_CATEGORY_DIR = "common/state_category"
RESOURCES_DIR = "common/resources"


@dataclass(frozen=True)
class StateCategoryDef:
    name: str
    color: tuple[int, int, int]
    building_slots: int


class StateCategoryService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._defs: dict[str, StateCategoryDef] | None = None

    def categories(self, refresh: bool = False) -> dict[str, StateCategoryDef]:
        if self._defs is None or refresh:
            self._defs = self._load()
        return self._defs

    def names(self) -> list[str]:
        # Stable, gameplay-meaningful order: by building slots, then name.
        return [c.name for c in sorted(self.categories().values(),
                                       key=lambda c: (c.building_slots, c.name))]

    def _load(self) -> dict[str, StateCategoryDef]:
        defs: dict[str, StateCategoryDef] = {}
        for root in self.ctx.override_roots(STATE_CATEGORY_DIR):
            folder = root / STATE_CATEGORY_DIR
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                cats = block.get_block("state_categories")
                if cats is None:
                    continue
                for pair in cats.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    body = pair.value
                    color = (128, 128, 128)
                    cblock = body.get_block("color")
                    if cblock is not None:
                        # array_values() yields raw strings
                        nums = [int(float(v)) for v in cblock.array_values()
                                if v.replace(".", "", 1).isdigit()]
                        if len(nums) >= 3:
                            color = (nums[0], nums[1], nums[2])
                    slots = 0
                    sl = body.get("local_building_slots")
                    if isinstance(sl, Scalar):
                        try:
                            slots = sl.as_int()
                        except (TypeError, ValueError):
                            slots = 0
                    defs[pair.key] = StateCategoryDef(pair.key, color, slots)
        return defs


@dataclass(frozen=True)
class ResourceDef:
    name: str
    icon_frame: int


class ResourceService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._defs: dict[str, ResourceDef] | None = None

    def resources(self, refresh: bool = False) -> dict[str, ResourceDef]:
        if self._defs is None or refresh:
            self._defs = self._load()
        return self._defs

    def names(self) -> list[str]:
        return sorted(self.resources(), key=lambda n: self.resources()[n].icon_frame)

    def _load(self) -> dict[str, ResourceDef]:
        defs: dict[str, ResourceDef] = {}
        for root in self.ctx.override_roots(RESOURCES_DIR):
            folder = root / RESOURCES_DIR
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                res = block.get_block("resources")
                if res is None:
                    continue
                for pair in res.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    frame = 0
                    fr = pair.value.get("icon_frame")
                    if isinstance(fr, Scalar):
                        try:
                            frame = fr.as_int()
                        except (TypeError, ValueError):
                            frame = 0
                    defs[pair.key] = ResourceDef(pair.key, frame)
        return defs
