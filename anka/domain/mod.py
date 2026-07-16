"""Mod domain model.

`Mod` is metadata parsed from a ``.mod`` descriptor plus its on-disk location.
`ModContext` is the handle handed to every editor module: it bundles the mod with the
shared services (image conversion, flags, icons) and resolves content paths, so editors
never hard-code directory layout.
"""
from __future__ import annotations

import threading
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
    dependencies: list[str] = field(default_factory=list)  # names of required mods
    # Folders (relative to the mod root) this mod fully replaces instead of merging
    # with lower layers — HOI4 ``replace_path`` directives, e.g. "history/states".
    replace_paths: list[str] = field(default_factory=list)

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


def _replaces(replace_paths: list[str], rel: str) -> bool:
    """Whether a declared ``replace_path`` covers the query subpath `rel` — true when
    `rel` equals a declared folder or lives underneath one."""
    rel = rel.replace("\\", "/").strip("/")
    for raw in replace_paths:
        p = raw.replace("\\", "/").strip("/")
        if p and (rel == p or rel.startswith(p + "/")):
            return True
    return False


@dataclass
class ModContext:
    """Everything an editor module needs to operate on a mod."""

    mod: Mod
    game_path: Path                 # base game, for reading vanilla content
    # Resolved dependency mods, in load order (first listed loads first / lowest
    # priority). Empty for a stand-alone mod.
    dependencies: list[Mod] = field(default_factory=list)

    @property
    def dependency_paths(self) -> list[Path]:
        return [d.path for d in self.dependencies]

    def content(self, relative: str) -> Path:
        return self.mod.path / relative

    def game_content(self, relative: str) -> Path:
        return self.game_path / relative

    # --- content layering -------------------------------------------------
    # Content is resolved across three kinds of source, from lowest to highest
    # priority: the base game, each dependency mod (in load order), then the edited
    # mod itself. When two layers define the same file, the higher one wins — so the
    # edited mod always overrides its dependencies, which override the base game.
    #
    # A ``replace_path`` on a layer drops *every lower layer* for that subtree: e.g. a
    # total-conversion dependency declaring ``replace_path="map"`` means the base
    # game's map is ignored entirely rather than merged (which would leak vanilla
    # provinces/states). Pass the folder being read as `rel` to honour this; the
    # default empty `rel` disables replace-path filtering (full merge).
    def _layers(self) -> list[tuple[Path, list[str], bool]]:
        """(root, replace_paths, is_edited_mod) from lowest to highest priority."""
        layers: list[tuple[Path, list[str], bool]] = [(self.game_path, [], False)]
        for dep in self.dependencies:
            layers.append((dep.path, dep.replace_paths, False))
        layers.append((self.mod.path, self.mod.replace_paths, True))
        return layers

    def _effective_layers(self, rel: str) -> list[tuple[Path, bool]]:
        layers = self._layers()
        cut = 0
        if rel:
            for i, (_root, reps, _is_mod) in enumerate(layers):
                if _replaces(reps, rel):
                    cut = i          # this layer replaces everything below it
        return [(root, is_mod) for root, _reps, is_mod in layers[cut:]]

    def override_roots(self, rel: str = "") -> list[Path]:
        """Roots from lowest to highest priority. Iterate and let later roots
        overwrite earlier ones (the ``(game_path, mod.path)`` idiom, generalised)."""
        return [root for root, _is_mod in self._effective_layers(rel)]

    def search_roots(self, rel: str = "") -> list[Path]:
        """Roots from highest to lowest priority — first match wins (the
        ``(mod.path, game_path)`` idiom, generalised)."""
        return list(reversed(self.override_roots(rel)))

    def override_layers(self, rel: str = "") -> list[tuple[Path, bool]]:
        """``(root, is_edited_mod)`` from lowest to highest priority. Only the edited
        mod's own files are editable; game and dependency files are read-only here."""
        return self._effective_layers(rel)

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

    @cached_property
    def sprites(self):
        """Shared ``GFX_*`` → texture resolver. Building its map means parsing every
        ``interface/**/*.gfx`` of the game + all DLC (seconds), so every editor module
        of this mod shares one instance — and an icon registered in one editor is
        immediately resolvable in another. Warm it via `warm_sprites`."""
        from ..core.gfx import SpriteResolver
        return SpriteResolver.for_mod(self.mod.path, self.game_path,
                                      self.dependency_paths)

    def warm_sprites(self) -> threading.Event:
        """Build the shared sprite map in the background — at most once per context —
        and return the event that is set when it is ready. Call from the UI thread."""
        event: threading.Event | None = self.__dict__.get("_sprites_ready")
        if event is None:
            event = threading.Event()
            self.__dict__["_sprites_ready"] = event
            resolver = self.sprites

            def warm() -> None:
                try:
                    resolver.resolve("")
                finally:
                    event.set()

            threading.Thread(target=warm, daemon=True).start()
        return event
