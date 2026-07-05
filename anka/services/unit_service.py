"""Land sub-unit catalogue for the OOB (order of battle) editor.

Reads ``common/units/*.txt`` (game + mod), each defining
``sub_units = { <name> = { group type map_icon_category abbreviation ... } }``.
A sub-unit is a *support company* when ``group = support`` and a line *regiment*
otherwise; sea/air units (ships, planes) are filtered out by icon category. The
result feeds the two palettes of the division-template editor.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.pdx import Block, Scalar, parse_file
from ..domain.mod import ModContext

# Icon categories that mark a sub-unit as naval (excluded from the land editor).
_SEA_ICONS = {"ship", "transport", "uboat"}


@dataclass(frozen=True)
class UnitType:
    name: str            # sub_unit key, e.g. "infantry", "engineer"
    abbreviation: str    # short badge, e.g. "INF"
    is_support: bool
    map_icon: str

    @property
    def label(self) -> str:
        return f"{self.name}" + (f" · {self.abbreviation}" if self.abbreviation else "")


class UnitService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._cache: dict[str, UnitType] | None = None

    def _load(self) -> dict[str, UnitType]:
        out: dict[str, UnitType] = {}
        # game first, then mod (mod overrides on name clash)
        for root in (self.ctx.game_path, self.ctx.mod.path):
            folder = root / "common/units"
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                try:
                    doc = parse_file(file)
                except Exception:
                    continue
                subs = doc.get_block("sub_units")
                if subs is None:
                    continue
                for pair in subs.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    b = pair.value
                    group = (b.get_scalar("group") or "").strip()
                    map_icon = (b.get_scalar("map_icon_category") or "").strip()
                    # land only: must have a group and a non-sea, non-empty icon
                    if not group or not map_icon or map_icon in _SEA_ICONS:
                        continue
                    out[pair.key] = UnitType(
                        name=pair.key,
                        abbreviation=(b.get_scalar("abbreviation") or "").strip('"'),
                        is_support=(group == "support"),
                        map_icon=map_icon)
        return out

    def all_land(self, refresh: bool = False) -> dict[str, UnitType]:
        if self._cache is None or refresh:
            self._cache = self._load()
        return self._cache

    def land_regiments(self) -> list[UnitType]:
        return sorted((u for u in self.all_land().values() if not u.is_support),
                      key=lambda u: u.name)

    def land_support(self) -> list[UnitType]:
        return sorted((u for u in self.all_land().values() if u.is_support),
                      key=lambda u: u.name)

    def get(self, name: str) -> UnitType | None:
        return self.all_land().get(name)
