"""Enumerate technologies from ``common/technologies`` (game + mod).

Used by the Countries editor's starting-tech picker. Technologies are the keys of the
``technologies = { <tech> = { ... } }`` block; categories/dummy entries without a real
definition are skipped. Results are cached per service instance.
"""
from __future__ import annotations

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, parse_file
from ..domain.mod import ModContext


class TechnologyService:
    TECH_DIR = "common/technologies"

    def __init__(self, context: ModContext):
        self.ctx = context
        self._cache: list[str] | None = None

    def list_techs(self, refresh: bool = False) -> list[str]:
        if self._cache is None or refresh:
            names: set[str] = set()
            for root in self.ctx.override_roots(self.TECH_DIR):
                folder = root / self.TECH_DIR
                if not folder.is_dir():
                    continue
                for file in folder.glob("*.txt"):
                    try:
                        block = parse_file(file)
                    except Exception:
                        continue
                    techs = block.get_block("technologies")
                    if techs is None:
                        continue
                    for pair in techs.pairs():
                        if isinstance(pair.value, Block):
                            names.add(pair.key)
            self._cache = sorted(names)
        return self._cache
