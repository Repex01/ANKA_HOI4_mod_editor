"""HOI4 localisation files.

Despite the ``.yml`` extension this is *not* YAML. The format is::

    l_english:
     KEY:0 "Value with $variables$"
     OTHER_KEY:1 "Another"

Files must be UTF-8 **with BOM** or the game silently drops them. Order is preserved
on write so diffs stay clean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# key : optional version, then a quoted value (which may itself contain quotes/braces)
_ENTRY_RE = re.compile(r'^\s*([\w.\-]+):\s*(\d*)\s*"(.*)"\s*$')
_HEADER_RE = re.compile(r"^\s*l_(\w+):\s*$")


@dataclass
class LocEntry:
    key: str
    value: str
    version: int = 0


class LocFile:
    """An ordered collection of localisation entries for one language."""

    def __init__(self, language: str = "english"):
        self.language = language
        self._entries: dict[str, LocEntry] = {}  # insertion-ordered

    # --- access ----------------------------------------------------------
    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str, default: str | None = None) -> str | None:
        entry = self._entries.get(key)
        return entry.value if entry else default

    def entries(self) -> list[LocEntry]:
        return list(self._entries.values())

    def set(self, key: str, value: str, version: int = 0) -> "LocFile":
        if key in self._entries:
            self._entries[key].value = value
        else:
            self._entries[key] = LocEntry(key, value, version)
        return self

    def remove(self, key: str) -> "LocFile":
        self._entries.pop(key, None)
        return self

    # --- io --------------------------------------------------------------
    @classmethod
    def parse(cls, text: str, language: str | None = None) -> "LocFile":
        loc = cls(language or "english")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            header = _HEADER_RE.match(line)
            if header:
                loc.language = header.group(1)
                continue
            m = _ENTRY_RE.match(line)
            if m:
                key, ver, value = m.group(1), m.group(2), m.group(3)
                loc._entries[key] = LocEntry(key, value, int(ver) if ver else 0)
        return loc

    @classmethod
    def load(cls, path: str | Path) -> "LocFile":
        path = Path(path)
        loc = cls.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        # Infer language from filename suffix when the header is missing/odd.
        m = re.search(r"_l_(\w+)\.yml$", path.name)
        if m:
            loc.language = m.group(1)
        return loc

    def dumps(self) -> str:
        lines = [f"l_{self.language}:"]
        for e in self._entries.values():
            lines.append(f' {e.key}:{e.version} "{e.value}"')
        return "\n".join(lines) + "\n"

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig => BOM, which HOI4 requires for localisation files.
        path.write_text(self.dumps(), encoding="utf-8-sig")
        return path
