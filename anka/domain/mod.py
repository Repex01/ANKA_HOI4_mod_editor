"""Mod domain model.

`Mod` is metadata parsed from a ``.mod`` descriptor plus its on-disk location.
`ModContext` is the handle handed to every editor module: it bundles the mod with the
shared services (image conversion, flags, icons) and resolves content paths, so editors
never hard-code directory layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path


@dataclass
class Mod:
    id: str                         # workshop id or local folder name (unique)
    name: str
    path: Path                      # content root (where common/, events/, gfx/ live)
    descriptor_path: Path | None = None
    picture: str = ""               # thumbnail file relative to `path`
    tags: list[str] = field(default_factory=list)
    version: str = ""
    supported_version: str = ""
    remote_file_id: str = ""
    is_local: bool = True

    @property
    def thumbnail_path(self) -> Path | None:
        if self.picture:
            candidate = self.path / self.picture
            if candidate.exists():
                return candidate
        for name in ("thumbnail.png", "thumbnail.jpg"):
            candidate = self.path / name
            if candidate.exists():
                return candidate
        return None

    @property
    def modified_at(self) -> datetime:
        """Last-modified time of the descriptor (proxy for 'release/update date')."""
        target = self.descriptor_path or self.path
        try:
            return datetime.fromtimestamp(target.stat().st_mtime)
        except OSError:
            return datetime.min

    def has(self, relative: str) -> bool:
        return (self.path / relative).exists()


@dataclass
class ModContext:
    """Everything an editor module needs to operate on a mod."""

    mod: Mod
    game_path: Path                 # base game, for reading vanilla content

    def content(self, relative: str) -> Path:
        return self.mod.path / relative

    def game_content(self, relative: str) -> Path:
        return self.game_path / relative

    @cached_property
    def flags(self):
        from ..core.images import FlagService
        return FlagService(self.mod.path)

    @cached_property
    def icons(self):
        from ..core.images import IconService
        return IconService(self.mod.path)

    @cached_property
    def characters(self):
        """Shared character service: one cache across all editor modules of this mod,
        so a character created in one editor is immediately visible in another."""
        from ..services.character_service import CharacterService
        return CharacterService(self)
