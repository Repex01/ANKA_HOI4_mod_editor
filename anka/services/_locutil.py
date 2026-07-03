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
