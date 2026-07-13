"""Scan mod (+ dependency) script for the flags and variables it already uses.

The script editors offer these as pick lists so a modder can reuse an existing
country/global/state flag or a scripted variable instead of retyping it (and
still type a brand-new name straight into the field to create one). Purely a
convenience index built by cheap regex over the text — no PDX parse — cached per
process. Base-game files are intentionally *not* scanned: the list would be huge
and dominated by names irrelevant to the mod being edited; the mod and the
submods it builds on are what matter.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..domain.mod import ModContext

# Folders worth scanning (where scripted effects/triggers live).
_SCAN_DIRS = ("common", "events", "history", "scripted")

_FLAG_RES: dict[str, re.Pattern] = {
    # both scalar (``set_country_flag = foo``) and block (``= { flag = foo … }``)
    "country_flag": re.compile(
        r"(?:set|has|clr|modify|remove)_country_flag\s*=\s*"
        r"(?:\{\s*flag\s*=\s*)?([A-Za-z_]\w*)"),
    "global_flag": re.compile(
        r"(?:set|has|clr|modify)_global_flag\s*=\s*"
        r"(?:\{\s*flag\s*=\s*)?([A-Za-z_]\w*)"),
    "state_flag": re.compile(
        r"(?:set|has|clr|modify|remove)_state_flag\s*=\s*"
        r"(?:\{\s*flag\s*=\s*)?([A-Za-z_]\w*)"),
}
# ``var = my_var`` (in set_variable/check_variable/… blocks) and the shorthand
# ``set_variable = { my_var = 3 }``.
_VAR_RE = re.compile(r"\bvar\s*=\s*([A-Za-z_][\w.]*)")
_VAR_SHORT_RE = re.compile(
    r"(?:set|add_to|subtract_from|multiply|divide|clamp)_variable\s*=\s*"
    r"\{\s*([A-Za-z_][\w.]*)\s*[=<>]")

VAR_FLAG_TYPES = ("country_flag", "global_flag", "state_flag", "variable")


class ScriptVarsService:
    def __init__(self, context: ModContext):
        self.ctx = context

    def options(self, vtype: str) -> list[tuple[str, str]]:
        """(display, value) rows of every known flag/variable of ``vtype``."""
        found = self._scan().get(vtype, ())
        return [(name, name) for name in found]

    # -------------------------------------------------------------------- scan
    def _scan(self) -> dict[str, tuple[str, ...]]:
        # Dependencies don't change during a session -> cached. The mod itself is
        # rescanned every time so a flag/variable the modder just created and saved
        # in the script editor shows up in the pick list without restarting.
        buckets: dict[str, set[str]] = {t: set() for t in VAR_FLAG_TYPES}
        dep = _scan_roots(tuple(self.ctx.dependency_paths))
        mod = _scan_root(self.ctx.mod.path)
        for source in (dep, mod):
            for vtype, names in source.items():
                buckets[vtype].update(names)
        return {t: tuple(sorted(v)) for t, v in buckets.items()}

    @staticmethod
    def invalidate() -> None:
        """Drop the dependency-scan cache (rarely needed — deps are static)."""
        _scan_roots.cache_clear()


def _scan_root(root: Path) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, set[str]] = {t: set() for t in VAR_FLAG_TYPES}
    for sub in _SCAN_DIRS:
        folder = root / sub
        if not folder.is_dir():
            continue
        for file in folder.rglob("*.txt"):
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for vtype, pattern in _FLAG_RES.items():
                buckets[vtype].update(pattern.findall(text))
            buckets["variable"].update(_VAR_RE.findall(text))
            buckets["variable"].update(_VAR_SHORT_RE.findall(text))
    return {t: tuple(sorted(v)) for t, v in buckets.items()}


@lru_cache(maxsize=8)
def _scan_roots(roots: tuple[Path, ...]) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, set[str]] = {t: set() for t in VAR_FLAG_TYPES}
    for root in roots:
        for vtype, names in _scan_root(root).items():
            buckets[vtype].update(names)
    return {t: tuple(sorted(v)) for t, v in buckets.items()}
