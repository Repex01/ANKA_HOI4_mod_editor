"""Parse ideology groups dynamically from ``common/ideologies``.

The ruling-party ideology *group* (vanilla: democratic / communism / fascism /
neutrality, but mods add their own) determines cosmetic flag variants
``<TAG>_<group>.tga``. We read group names from the mod first, then the base game, so
custom ideologies are picked up automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, parse_file
from ..domain.mod import ModContext


@dataclass
class Ideology:
    name: str
    rgb: tuple[int, int, int] | None = None
    types: list[str] = field(default_factory=list)  # sub-ideologies (leader ideologies)


class IdeologyService:
    def __init__(self, context: ModContext):
        self.ctx = context

    def list_groups(self) -> list[Ideology]:
        groups: dict[str, Ideology] = {}
        # Game first as the baseline, then mod (mod overrides / extends).
        for root in (self.ctx.game_path, self.ctx.mod.path):
            for ideo in self._read_dir(root):
                groups[ideo.name] = ideo
        # Stable, vanilla-ish ordering with extras appended.
        preferred = ["democratic", "communism", "fascism", "neutrality"]
        ordered = [groups[n] for n in preferred if n in groups]
        ordered += [g for n, g in groups.items() if n not in preferred]
        return ordered

    def _read_dir(self, root: Path) -> list[Ideology]:
        folder = root / GAME_DIRS.IDEOLOGIES
        if not folder.is_dir():
            return []
        out: list[Ideology] = []
        for file in sorted(folder.glob("*.txt")):
            try:
                block = parse_file(file)
            except Exception:
                continue
            ideologies = block.get_block("ideologies")
            if ideologies is None:
                continue
            for pair in ideologies.pairs():
                if isinstance(pair.value, Block):
                    rgb = self._rgb(pair.value.get_block("color"))
                    types_block = pair.value.get_block("types")
                    types = [t.key for t in types_block.pairs()] if types_block else []
                    out.append(Ideology(pair.key, rgb, types))
        return out

    def list_types(self) -> list[tuple[str, str]]:
        """Flat (group, sub-ideology) pairs for leader-ideology selection."""
        pairs: list[tuple[str, str]] = []
        for group in self.list_groups():
            for sub in group.types:
                pairs.append((group.name, sub))
        return pairs

    @staticmethod
    def _rgb(node: Block | None) -> tuple[int, int, int] | None:
        if node is None:
            return None
        vals = node.array_values()
        if len(vals) >= 3:
            try:
                return tuple(int(float(v)) for v in vals[:3])  # type: ignore[return-value]
            except ValueError:
                return None
        return None
