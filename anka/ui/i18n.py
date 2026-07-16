"""Interface translation.

Flat dotted-key JSON files live in ``locales/<code>.json``. `Translator.t` looks up a
key, falls back to the fallback language, then to the key itself, and applies
``str.format`` for interpolation. Language changes notify subscribers so open windows
can rebuild their labels.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ..config.constants import Paths


class Translator:
    def __init__(self, language: str = "en", fallback: str = "en"):
        self._dir = Paths.LOCALES
        self.fallback = fallback
        self._fallback_map: dict[str, str] = self._load(fallback)
        self._map: dict[str, str] = {}
        self._listeners: list[Callable[[str], None]] = []
        self.language = language
        self.set_language(language, notify=False)

    def _load(self, code: str) -> dict[str, str]:
        path = self._dir / f"{code}.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def available_languages(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def set_language(self, code: str, notify: bool = True) -> None:
        self.language = code
        self._map = self._load(code)
        if notify:
            for listener in self._listeners:
                listener(code)

    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def t(self, key: str, **kwargs) -> str:
        text = self._map.get(key) or self._fallback_map.get(key) or key
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return text
        return text

    __call__ = t
