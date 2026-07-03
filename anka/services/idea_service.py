"""Read ideas (national spirits, laws, advisors, designers) from mod + game.

``common/ideas/*.txt`` layout::

    ideas = {
        <category> = {           # country, political_advisor, economy, ...
            <idea_id> = { ... }
            designer = yes        # category-level scalars are skipped
        }
    }

Used by pickers (e.g. ``add_ideas`` fields in the script editor), so the list is
dynamic — mods add ideas and categories freely. Mod definitions win on id clashes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, parse_file
from ..domain.mod import ModContext


@dataclass
class IdeaInfo:
    id: str
    category: str          # e.g. "country", "economy", "political_advisor"
    in_mod: bool
    file: Path


class IdeaService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._cache: dict[str, IdeaInfo] | None = None

    def list_ideas(self, refresh: bool = False) -> list[IdeaInfo]:
        if self._cache is None or refresh:
            self._cache = self._load_all()
        return sorted(self._cache.values(), key=lambda i: (i.category, i.id))

    def get(self, idea_id: str) -> IdeaInfo | None:
        if self._cache is None:
            self.list_ideas()
        return (self._cache or {}).get(idea_id)

    def _load_all(self) -> dict[str, IdeaInfo]:
        out: dict[str, IdeaInfo] = {}
        # game first, then mod — mod entries overwrite on id clash
        for root, in_mod in ((self.ctx.game_path, False), (self.ctx.mod.path, True)):
            folder = root / GAME_DIRS.IDEAS
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                try:
                    doc = parse_file(file)
                except Exception:
                    continue
                ideas = doc.get_block("ideas")
                if ideas is None:
                    continue
                for cat_pair in ideas.pairs():
                    if not isinstance(cat_pair.value, Block):
                        continue
                    for idea_pair in cat_pair.value.pairs():
                        # category-level scalars (designer = yes, law = yes, ...)
                        if not isinstance(idea_pair.value, Block):
                            continue
                        out[idea_pair.key] = IdeaInfo(
                            id=idea_pair.key, category=cat_pair.key,
                            in_mod=in_mod, file=file)
        return out
