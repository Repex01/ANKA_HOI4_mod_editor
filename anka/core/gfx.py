"""Sprite registration in HOI4 ``.gfx`` files.

Icons (focuses, ideas, events) must be declared as ``SpriteType`` entries inside a
``spriteTypes = { ... }`` block under ``interface/``. This service reads an existing
.gfx via the PDX parser, adds/updates a sprite idempotently, and writes it back —
creating the file if needed. It is reused by every editor that introduces graphics.
"""
from __future__ import annotations

from pathlib import Path

from .pdx import Block, Pair, Scalar, dump_file, parse_file


def _get_block_ci(block: Block, key: str):
    """Case-insensitive child-block lookup (HOI4 keys are case-insensitive)."""
    low = key.lower()
    for pair in block.pairs():
        if pair.key.lower() == low and isinstance(pair.value, Block):
            return pair.value
    return None


def _get_scalar_ci(block: Block, key: str) -> str | None:
    low = key.lower()
    for pair in block.pairs():
        if pair.key.lower() == low and isinstance(pair.value, Scalar):
            return pair.value.raw
    return None


class SpriteRegistry:
    """Idempotent SpriteType management for a single .gfx file."""

    def __init__(self, gfx_path: str | Path):
        self.path = Path(gfx_path)
        self.root = self._load()

    def _load(self) -> Block:
        if self.path.exists():
            return parse_file(self.path)
        return Block([Pair("spriteTypes", Block())])

    def _sprite_types(self) -> Block:
        block = self.root.get_block("spriteTypes")
        if block is None:
            block = Block()
            self.root.add("spriteTypes", block)
        return block

    def has(self, name: str) -> bool:
        return self.find(name) is not None

    def find(self, name: str) -> Block | None:
        for sprite in self._sprite_types().get_all("SpriteType"):
            if isinstance(sprite, Block) and sprite.get_scalar("name", "").strip('"') == name:
                return sprite
        return None

    def register(self, name: str, texturefile: str, **extra: str) -> "SpriteRegistry":
        """Add the sprite, or update its texture path if it already exists."""
        existing = self.find(name)
        if existing is not None:
            existing.set("texturefile", Scalar(texturefile, quoted=True))
        else:
            sprite = Block()
            sprite.add("name", Scalar(name, quoted=True))
            sprite.add("texturefile", Scalar(texturefile, quoted=True))
            for key, value in extra.items():
                sprite.add(key, Scalar.of(value))
            self._sprite_types().add("SpriteType", sprite)
        return self

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(self.root, self.path)
        return self.path


class SpriteResolver:
    """Resolve a ``GFX_*`` sprite name to its on-disk texture.

    Sprites are declared in ``interface/**/*.gfx`` across several *content roots* — the
    mod, the base game, and every DLC folder (``dlc/*`` and ``integrated_dlc/*``). Each
    DLC ships its own ``interface`` and its ``texturefile`` paths are relative to that
    DLC's root, so the resolver records, per root, where to anchor the texture. The map is
    built once (lazily) and cached. Roots are scanned low→high priority; later roots win,
    so the mod overrides the game which overrides DLC.
    """

    def __init__(self, content_roots: list[Path]):
        self._roots = [Path(r) for r in content_roots]
        self._map: dict[str, Path] | None = None

    @classmethod
    def for_mod(cls, mod_path: Path, game_path: Path) -> "SpriteResolver":
        """Build the standard root list: DLC folders, then game, then mod (highest)."""
        roots: list[Path] = []
        for dlc_parent in ("dlc", "integrated_dlc"):
            parent = game_path / dlc_parent
            if parent.is_dir():
                roots.extend(sorted(p for p in parent.iterdir() if p.is_dir()))
        roots.append(game_path)
        roots.append(mod_path)
        return cls(roots)

    def _build(self) -> dict[str, Path]:
        mapping: dict[str, Path] = {}
        for root in self._roots:  # later roots (game, mod) override earlier DLC
            interface = root / "interface"
            if not interface.is_dir():
                continue
            for gfx in interface.rglob("*.gfx"):
                try:
                    block = parse_file(gfx)
                except Exception:
                    continue
                sprites = _get_block_ci(block, "spriteTypes")
                if sprites is None:
                    continue
                # HOI4 is case-insensitive: entries appear as both `SpriteType` and
                # `spriteType` (the latter dominates the leader-portrait files).
                for pair in sprites.pairs():
                    if pair.key.lower() != "spritetype" or not isinstance(pair.value, Block):
                        continue
                    name = (_get_scalar_ci(pair.value, "name") or "").strip('"')
                    texture = (_get_scalar_ci(pair.value, "texturefile") or "").strip('"')
                    if name and texture:
                        mapping[name] = root / texture.replace("\\", "/")
        return mapping

    def resolve(self, sprite_name: str) -> Path | None:
        if self._map is None:
            self._map = self._build()
        path = self._map.get(sprite_name)
        return path if (path and path.exists()) else None
