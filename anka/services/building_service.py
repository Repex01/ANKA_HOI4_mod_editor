"""Building definitions (``common/buildings``) for the map editor's state inspector.

The scope discriminator is the ``level_cap`` block: ``province_max`` makes a
building *provincial* (placed per province inside a state's ``buildings`` block,
e.g. ``3838 = { naval_base = 3 }``), otherwise it is a *state* building
(``infrastructure = 2``). ``only_costal`` (game spelling!) restricts a building
to coastal provinces. Never hardcode the list — mods add buildings.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.pdx import Block, Scalar, parse_file
from ..domain.mod import ModContext

BUILDINGS_DIR = "common/buildings"


@dataclass(frozen=True)
class BuildingDef:
    name: str
    scope: str                  # "state" | "province"
    max_level: int
    only_coastal: bool
    shares_slots: bool
    icon_frame: int


class BuildingService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._defs: dict[str, BuildingDef] | None = None

    def buildings(self, refresh: bool = False) -> dict[str, BuildingDef]:
        if self._defs is None or refresh:
            self._defs = self._load()
        return self._defs

    def state_buildings(self) -> list[BuildingDef]:
        return [b for b in self.buildings().values() if b.scope == "state"]

    def province_buildings(self) -> list[BuildingDef]:
        return [b for b in self.buildings().values() if b.scope == "province"]

    def get(self, name: str) -> BuildingDef | None:
        return self.buildings().get(name)

    # ------------------------------------------------------------------ load
    def _load(self) -> dict[str, BuildingDef]:
        defs: dict[str, BuildingDef] = {}
        for root in (self.ctx.game_path, self.ctx.mod.path):
            folder = root / BUILDINGS_DIR
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                buildings = block.get_block("buildings")
                if buildings is None:
                    continue
                for pair in buildings.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    defs[pair.key] = self._parse_building(pair.key, pair.value)
        return defs

    @staticmethod
    def _parse_building(name: str, body: Block) -> BuildingDef:
        scope = "state"
        max_level = 15
        shares = False
        cap = body.get_block("level_cap")
        if cap is not None:
            pmax = cap.get("province_max")
            smax = cap.get("state_max")
            if isinstance(pmax, Scalar):
                scope = "province"
                max_level = _to_int(pmax, max_level)
            elif isinstance(smax, Scalar):
                max_level = _to_int(smax, max_level)
            sh = cap.get("shares_slots")
            if isinstance(sh, Scalar):
                shares = sh.as_bool()
        ml = body.get("max_level")
        if isinstance(ml, Scalar):
            max_level = _to_int(ml, max_level)
        coastal = False
        oc = body.get("only_costal")     # sic — the game key is misspelled
        if isinstance(oc, Scalar):
            coastal = oc.as_bool()
        frame = 0
        fr = body.get("icon_frame")
        if isinstance(fr, Scalar):
            frame = _to_int(fr, 0)
        return BuildingDef(name=name, scope=scope, max_level=max_level,
                           only_coastal=coastal, shares_slots=shares,
                           icon_frame=frame)


def _to_int(scalar: Scalar, default: int) -> int:
    try:
        return scalar.as_int()
    except (TypeError, ValueError):
        return default
