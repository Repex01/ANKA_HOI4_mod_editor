"""Localisation write-target resolution shared by services.

HOI4 loads *all* localisation files; two definitions of one key in different files
produce "loc key collision" warnings and load-order-dependent results. So writing a
value for a key that vanilla already defines must not add a second definition in an
ANKA file: instead the vanilla .yml is copied into the mod at the same relative path
(exact filename => the engine treats it as an override of the original) and the key is
edited inside the copy. Keys unknown to vanilla go to ANKA's own file.
"""
from __future__ import annotations

from pathlib import Path

from ..core.localisation import LocFile
from ._fsutil import ensure_filename_case

_LOC_DIR = "localisation"


def find_loc_file_with_key(root: Path, language: str, key: str,
                           name_hints: tuple[str, ...] = ()) -> Path | None:
    """First ``*_l_<language>.yml`` under `root`/localisation that defines `key`.
    `name_hints` (substrings of the file name) narrow the scan on big vanilla trees."""
    loc_dir = root / _LOC_DIR
    if not loc_dir.is_dir():
        return None
    for yml in sorted(loc_dir.rglob(f"*_l_{language}.yml")):
        if name_hints and not any(h in yml.name.lower() for h in name_hints):
            continue
        try:
            if key in LocFile.load(yml):
                return yml
        except OSError:
            continue
    return None


class LocCatalog:
    """Per-language key→value localisation index with collision-safe writes.

    Mirrors the strategy proven in `FocusService`: mod files are indexed fully,
    vanilla files are filtered by a file-name substring (memory/time cap). Writes go
    to the mod file already holding the key, else to a mod-side override copy of the
    vanilla file defining it, else to ANKA's own file (`default_pattern`).
    """

    def __init__(self, mod_root: Path, game_root: Path,
                 vanilla_filter: str, default_pattern: str):
        self.mod_root = Path(mod_root)
        self.game_root = Path(game_root)
        self._filter = vanilla_filter.lower()
        self._default = default_pattern            # e.g. "anka_decisions_l_{lang}.yml"
        self._cache: dict[str, dict[str, str]] = {}
        self._files: dict[str, dict[str, Path]] = {}      # lang -> key -> mod file
        self._vanilla: dict[str, dict[str, Path]] = {}    # lang -> key -> game file

    # --- read ---------------------------------------------------------------
    def get(self, key: str, language: str) -> str | None:
        return self._index(language).get(key)

    def has(self, key: str, language: str) -> bool:
        return key in self._index(language)

    def _index(self, language: str) -> dict[str, str]:
        cached = self._cache.get(language)
        if cached is not None:
            return cached
        index: dict[str, str] = {}
        vfiles = self._vanilla.setdefault(language, {})
        game_dir = self.game_root / _LOC_DIR
        if game_dir.is_dir():
            for yml in sorted(game_dir.rglob(f"*_l_{language}.yml")):
                if self._filter not in yml.name.lower():
                    continue
                try:
                    loc = LocFile.load(yml)
                except OSError:
                    continue
                for entry in loc.entries():
                    index[entry.key] = entry.value
                    vfiles.setdefault(entry.key, yml)
        files = self._files.setdefault(language, {})
        mod_dir = self.mod_root / _LOC_DIR
        if mod_dir.is_dir():
            for yml in sorted(mod_dir.rglob(f"*_l_{language}.yml")):
                try:
                    loc = LocFile.load(yml)
                except OSError:
                    continue
                for entry in loc.entries():
                    index[entry.key] = entry.value
                    files[entry.key] = yml
        self._cache[language] = index
        return index

    # --- write --------------------------------------------------------------
    def set(self, key: str, language: str, value: str) -> Path:
        self._index(language)
        files = self._files.setdefault(language, {})
        path = files.get(key)
        if path is None:
            vanilla = self._vanilla.get(language, {}).get(key)
            if vanilla is not None:
                path = self._override_vanilla(vanilla, language)
        if path is None:
            path = self.mod_root / _LOC_DIR / language / self._default.format(lang=language)
        loc = LocFile.load(path) if path.exists() else LocFile(language)
        loc.set(key, value)
        loc.save(path)
        files[key] = path
        self._cache[language][key] = value
        return path

    def _override_vanilla(self, source: Path, language: str) -> Path:
        """Copy a vanilla .yml into the mod (same relative path => engine override)
        and remap all its keys to the copy."""
        target = ensure_filename_case(self.mod_root / source.relative_to(self.game_root))
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        files = self._files.setdefault(language, {})
        try:
            for entry in LocFile.load(target).entries():
                files.setdefault(entry.key, target)
        except OSError:
            pass
        return target

    def rename(self, old: str, new: str) -> None:
        """Carry mod-side entries (and their _desc twins) over to a renamed id."""
        for language, files in self._files.items():
            for suffix in ("", "_desc"):
                key = old + suffix
                path = files.get(key)
                if path is None or not path.exists():
                    continue
                loc = LocFile.load(path)
                value = loc.get(key)
                if value is None:
                    continue
                loc.remove(key)
                loc.set(new + suffix, value)
                loc.save(path)
                files.pop(key, None)
                files[new + suffix] = path
                idx = self._cache.get(language)
                if idx is not None:
                    idx.pop(key, None)
                    idx[new + suffix] = value


def loc_write_target(mod_root: Path, game_root: Path, language: str, key: str,
                     default_rel: str,
                     name_hints: tuple[str, ...] = ()) -> Path:
    """The mod file `key` should be written to:

    1. a mod file that already defines the key (edit in place);
    2. else, when a *vanilla* file defines it — a copy of that file inside the mod at
       the same relative path (created here on first use), so the whole file overrides
       the original instead of colliding with it;
    3. else ANKA's own file at `default_rel` (a brand-new key).
    """
    hit = find_loc_file_with_key(mod_root, language, key)
    if hit is not None:
        return hit
    vanilla = find_loc_file_with_key(game_root, language, key, name_hints)
    if vanilla is not None:
        target = ensure_filename_case(mod_root / vanilla.relative_to(game_root))
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(vanilla.read_bytes())
        return target
    return mod_root / default_rel
