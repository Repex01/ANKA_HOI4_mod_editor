"""Sprite registration in HOI4 ``.gfx`` files.

Icons (focuses, ideas, events) must be declared as ``SpriteType`` entries inside a
``spriteTypes = { ... }`` block under ``interface/``. This service reads an existing
.gfx via the PDX parser, adds/updates a sprite idempotently, and writes it back —
creating the file if needed. It is reused by every editor that introduces graphics.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .fspath import resolve_ci
from .guitypes.views import SpriteView
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

    def register_sprite(self, sprite: Block) -> "SpriteRegistry":
        """Add a fully-built SpriteType block (replaces an existing one by name).
        Used for complex sprites (shine animations) that `register` can't express."""
        name = (sprite.get_scalar("name") or "").strip('"')
        existing = self.find(name)
        if existing is not None:
            for pair in self._sprite_types().pairs():
                if pair.value is existing:
                    pair.value = sprite
                    break
        else:
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
        # One instance is shared by every editor module of a mod (see
        # ModContext.sprites), so the lazy build must not run twice when the
        # warm-up thread and a UI call race on a cold map.
        self._build_lock = threading.Lock()

    @classmethod
    def for_mod(cls, mod_path: Path, game_path: Path,
                deps: list[Path] | None = None) -> "SpriteResolver":
        """Build the standard root list: DLC folders, then game, then dependency mods
        (in load order), then the edited mod (highest priority)."""
        roots: list[Path] = []
        for dlc_parent in ("dlc", "integrated_dlc"):
            parent = game_path / dlc_parent
            if parent.is_dir():
                roots.extend(sorted(p for p in parent.iterdir() if p.is_dir()))
        roots.append(game_path)
        roots.extend(deps or [])
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

    def _mapping(self) -> dict[str, Path]:
        if self._map is None:
            with self._build_lock:
                if self._map is None:
                    self._map = self._build()
        return self._map

    def invalidate(self) -> None:
        """Drop the cached sprite map so the next lookup re-reads the .gfx files.

        The map is built once and shared by every editor module, so writing a new
        portrait (new .dds + new spriteType entry) would otherwise keep resolving
        to the previously cached texture — the editor showed the old image while
        the game already used the new one.
        """
        with self._build_lock:
            self._map = None

    def resolve(self, sprite_name: str) -> Path | None:
        path = self._mapping().get(sprite_name)
        if not path:
            return None
        # Case-insensitive: vanilla .gfx texture paths regularly mismatch the
        # on-disk case, which only case-sensitive filesystems (Linux) notice.
        return resolve_ci(path)

    def add(self, name: str, texture: Path) -> None:
        """Record a sprite created at runtime without a full rescan."""
        self._mapping()[name] = Path(texture)

    def names(self, prefixes: tuple[str, ...] = ()) -> list[str]:
        """All known sprite names, optionally filtered by prefix (case-insensitive)."""
        lows = tuple(p.lower() for p in prefixes)
        return sorted(n for n in self._mapping()
                      if not lows or n.lower().startswith(lows))


class SpriteDef:
    """One catalog entry: a full sprite definition anchored to its content root
    (texture paths inside a .gfx are relative to the root that ships it)."""

    __slots__ = ("view", "root")

    def __init__(self, view: SpriteView, root: Path):
        self.view = view
        self.root = root

    @property
    def name(self) -> str:
        return self.view.name

    @property
    def kind(self) -> str:
        return self.view.type_key.lower()

    def texture_path(self, key: str = "") -> Path | None:
        raw = self.view.get_attr(key) if key else self.view.texture
        if not raw:
            return None
        path = self.root / raw.replace("\\", "/").replace("//", "/")
        return resolve_ci(path) or path


class SpriteCatalog:
    """Full sprite-definition lookup across content roots (the renderer's and
    the .gui editor's read side). Unlike `SpriteResolver` — which only maps
    ``spriteType`` names to texture files — the catalog keeps every entry kind
    (cornered tiles, frame animations, progress bars, ...) with its whole
    block, so 9-slice borders, frame counts and two-texture bars are available.
    Built lazily; later roots override earlier ones (DLC → game → deps → mod).
    """

    def __init__(self, content_roots: list[Path]):
        self._roots = [Path(r) for r in content_roots]
        self._map: dict[str, SpriteDef] | None = None

    @classmethod
    def for_mod(cls, mod_path: Path, game_path: Path,
                deps: list[Path] | None = None) -> "SpriteCatalog":
        roots: list[Path] = []
        for dlc_parent in ("dlc", "integrated_dlc"):
            parent = game_path / dlc_parent
            if parent.is_dir():
                roots.extend(sorted(p for p in parent.iterdir() if p.is_dir()))
        roots.append(game_path)
        roots.extend(deps or [])
        roots.append(mod_path)
        return cls(roots)

    def _build(self) -> dict[str, SpriteDef]:
        from .guitypes.schema import SPRITE_KINDS
        mapping: dict[str, SpriteDef] = {}
        for root in self._roots:
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
                for pair in sprites.pairs():
                    if (pair.key.lower() not in SPRITE_KINDS
                            or not isinstance(pair.value, Block)):
                        continue
                    view = SpriteView(pair)
                    if view.name:
                        mapping[view.name] = SpriteDef(view, root)
        return mapping

    def _mapping(self) -> dict[str, SpriteDef]:
        if self._map is None:
            self._map = self._build()
        return self._map

    def get(self, name: str) -> SpriteDef | None:
        return self._mapping().get(name)

    def names(self, prefixes: tuple[str, ...] = ()) -> list[str]:
        lows = tuple(p.lower() for p in prefixes)
        return sorted(n for n in self._mapping()
                      if not lows or n.lower().startswith(lows))

    def add(self, view: SpriteView, root: Path) -> None:
        """Record a sprite created/edited at runtime without a full rescan."""
        if view.name:
            self._mapping()[view.name] = SpriteDef(view, root)

    def invalidate(self) -> None:
        self._map = None

    def warm(self) -> None:
        self._mapping()
