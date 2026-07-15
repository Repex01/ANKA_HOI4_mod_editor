"""User-saved script blocks ("Save block" in the block editors).

A tiny personal library of ready-made PDX snippets: any container (or a whole
edited script) can be saved under a name from the block editor's context menu,
and later re-inserted from the effect/trigger picker, where saved entries are
listed with a ``[BLOCK]`` prefix. Stored as JSON next to ``settings.json`` so
the library survives across mods and sessions.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config.constants import Paths

_FILE: Path = Paths.ROOT / "saved_blocks.json"
_cache: list[dict] | None = None


def _load() -> list[dict]:
    global _cache
    if _cache is None:
        try:
            with open(_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = [b for b in data.get("blocks", [])
                      if isinstance(b, dict) and b.get("name") and b.get("text")]
        except (OSError, json.JSONDecodeError):
            _cache = []
    return _cache


def _save() -> None:
    try:
        with open(_FILE, "w", encoding="utf-8") as fh:
            json.dump({"blocks": _cache or []}, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def all_blocks() -> list[dict]:
    """Every saved block: ``{"name": str, "text": str}`` (insertion order)."""
    return list(_load())


def get(name: str) -> dict | None:
    return next((b for b in _load() if b["name"] == name), None)


def add(name: str, text: str) -> None:
    """Save (or overwrite) a named snippet."""
    blocks = _load()
    existing = next((b for b in blocks if b["name"] == name), None)
    if existing is not None:
        existing["text"] = text
    else:
        blocks.append({"name": name, "text": text})
    _save()


def remove(name: str) -> bool:
    global _cache
    blocks = _load()
    kept = [b for b in blocks if b["name"] != name]
    if len(kept) == len(blocks):
        return False
    _cache = kept
    _save()
    return True
