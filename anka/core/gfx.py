"""Sprite registration in HOI4 ``.gfx`` files.

Icons (focuses, ideas, events) must be declared as ``SpriteType`` entries inside a
``spriteTypes = { ... }`` block under ``interface/``. This service reads an existing
.gfx via the PDX parser, adds/updates a sprite idempotently, and writes it back —
creating the file if needed. It is reused by every editor that introduces graphics.
"""
from __future__ import annotations

from pathlib import Path

from .pdx import Block, Pair, Scalar, dump_file, parse_file


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

    Builds one map by scanning every ``interface/*.gfx`` in the given roots (mod first so
    it overrides the game). Cached after the first lookup. Used to preview portraits /
    icons that are referenced only by sprite name in script.
    """

    def __init__(self, roots: list[Path]):
        self._roots = [Path(r) for r in roots]
        self._map: dict[str, Path] | None = None

    def _build(self) -> dict[str, Path]:
        mapping: dict[str, Path] = {}
        # Game first, mod last so the mod wins on name clashes.
        for root in reversed(self._roots):
            interface = root / "interface"
            if not interface.is_dir():
                continue
            for gfx in interface.glob("*.gfx"):
                try:
                    block = parse_file(gfx)
                except Exception:
                    continue
                sprites = block.get_block("spriteTypes")
                if sprites is None:
                    continue
                for sprite in sprites.get_all("SpriteType"):
                    if not isinstance(sprite, Block):
                        continue
                    name = (sprite.get_scalar("name", "") or "").strip('"')
                    texture = (sprite.get_scalar("texturefile", "") or "").strip('"')
                    if name and texture:
                        mapping[name] = root / texture.replace("\\", "/")
        return mapping

    def resolve(self, sprite_name: str) -> Path | None:
        if self._map is None:
            self._map = self._build()
        path = self._map.get(sprite_name)
        return path if (path and path.exists()) else None
