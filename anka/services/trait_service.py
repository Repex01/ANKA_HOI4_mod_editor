"""Dynamically parse character traits from the game/mod.

Two trait families, defined as ``leader_traits = { <trait> = {...} }``:
  * **country-leader / advisor traits** — ``common/country_leader/*.txt`` (this file also
    holds advisor traits like ``captain_of_industry``);
  * **unit-leader traits** (generals & admirals) — ``common/unit_leader/*.txt``.
Results are cached per service instance and feed the trait pickers in the editor.
"""
from __future__ import annotations

from ..core.pdx import parse_file
from ..domain.mod import ModContext


class TraitService:
    COUNTRY_LEADER_DIR = "common/country_leader"
    UNIT_LEADER_DIR = "common/unit_leader"

    def __init__(self, context: ModContext):
        self.ctx = context
        self._country: list[str] | None = None
        self._unit: list[str] | None = None

    def country_leader_traits(self) -> list[str]:
        if self._country is None:
            self._country = self._scan(self.COUNTRY_LEADER_DIR)
        return self._country

    def unit_leader_traits(self) -> list[str]:
        if self._unit is None:
            self._unit = self._scan(self.UNIT_LEADER_DIR)
        return self._unit

    # Advisor traits live alongside country-leader traits.
    advisor_traits = country_leader_traits

    def _scan(self, rel_dir: str) -> list[str]:
        names: set[str] = set()
        for root in self.ctx.override_roots(rel_dir):
            folder = root / rel_dir
            if not folder.is_dir():
                continue
            for file in folder.glob("*.txt"):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                traits = block.get_block("leader_traits")
                if traits is None:
                    continue
                names.update(p.key for p in traits.pairs())
        return sorted(names)
