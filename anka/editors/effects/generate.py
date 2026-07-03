"""Generate ``effects.json`` / ``triggers.json`` from the game's own documentation.

HOI4 ships machine-readable script docs (``<game>/documentation/*.md``) that are always
in sync with the installed game version — a better source than the wiki. Re-run this
after a game update::

    python -m anka.editors.effects.generate ["D:/path/to/Hearts of Iron IV"]

Without an argument the game path is taken from the project's ``settings.json``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_DATA_DIR = Path(__file__).parent

# Section headers that are structure, not keyword entries.
_NON_ENTRY = re.compile(
    r"^(table of content|effects for scope .*|triggers for scope .*|modifiers for scope .*)$",
    re.I)
_HEADER = re.compile(r"^## +(.+?) *$")
_SCOPES = re.compile(r"^\* +Supported Scopes: *(.*)$")
_TARGETS = re.compile(r"^\* +Supported Targets: *(.*)$")
_MOD_CATEGORIES = re.compile(r"^\* +\*\*Categories\*\*: *(.*)$")


def _split_list(raw: str) -> list[str]:
    parts = [p for p in re.split(r"[,\s]+", raw.strip()) if p]
    return [] if parts in ([], ["none"]) else parts


def parse_documentation(md_text: str) -> list[dict]:
    """Parse one documentation file into catalog rows.

    Entry layout in the docs::

        ## keyword_name
        * Supported Scopes: COUNTRY STATE
        * Supported Targets: none
        ```
        free-form description
        Example:
        keyword_name = { ... }
        ```
    """
    entries: dict[str, dict] = {}
    current: dict | None = None
    fence_lines: list[str] | None = None

    for line in md_text.splitlines():
        m = _HEADER.match(line)
        if m and fence_lines is None:
            name = m.group(1).strip()
            if _NON_ENTRY.match(name) or not re.fullmatch(r"[a-zA-Z0-9_.]+", name):
                current = None
                continue
            # Rare duplicate sections: merge scopes/targets, keep first text.
            current = entries.setdefault(
                name, {"name": name, "scopes": [], "targets": [], "desc": "", "example": ""})
            continue
        if current is None:
            continue

        if fence_lines is None:
            m = _SCOPES.match(line)
            if m:
                for s in _split_list(m.group(1)):
                    if s not in current["scopes"]:
                        current["scopes"].append(s)
                continue
            m = _TARGETS.match(line)
            if m:
                for s in _split_list(m.group(1)):
                    if s not in current["targets"]:
                        current["targets"].append(s)
                continue
            if line.strip().startswith("```"):
                fence_lines = []
            continue

        if line.strip().startswith("```"):        # closing fence
            text = "\n".join(fence_lines).strip("\n")
            desc, _, example = text.partition("\nExample:")
            if not _:
                desc, _, example = text.partition("Example:")
            if not current["desc"]:
                current["desc"] = desc.strip()
                current["example"] = example.strip("\n").strip()
            fence_lines = None
            continue
        fence_lines.append(line)

    return sorted(entries.values(), key=lambda e: e["name"])


def parse_modifiers_documentation(md_text: str) -> list[dict]:
    """Parse ``modifiers_documentation.md``. Entry layout::

        ## modifier_name
        * Number with 1 decimal places
        * **Categories**: army, country

    Stored in the common catalog row shape: categories -> ``scopes``, the value
    format line -> ``desc`` (so the runtime loader is shared with effects/triggers).
    """
    entries: dict[str, dict] = {}
    current: dict | None = None
    for line in md_text.splitlines():
        m = _HEADER.match(line)
        if m:
            name = m.group(1).strip()
            if _NON_ENTRY.match(name) or not re.fullmatch(r"[a-zA-Z0-9_.]+", name):
                current = None
                continue
            current = entries.setdefault(
                name, {"name": name, "scopes": [], "targets": [], "desc": "", "example": ""})
            continue
        if current is None:
            continue
        m = _MOD_CATEGORIES.match(line)
        if m:
            for cat in _split_list(m.group(1)):
                if cat not in current["scopes"]:
                    current["scopes"].append(cat)
            continue
        stripped = line.strip()
        if stripped.startswith("*") and not current["desc"]:
            current["desc"] = stripped.lstrip("* ").strip()
    return sorted(entries.values(), key=lambda e: e["name"])


def generate(game_path: Path) -> None:
    docs = game_path / "documentation"
    for src, dst, parser in (
            ("effects_documentation.md", "effects.json", parse_documentation),
            ("triggers_documentation.md", "triggers.json", parse_documentation),
            ("modifiers_documentation.md", "modifiers.json", parse_modifiers_documentation)):
        md = (docs / src).read_text(encoding="utf-8-sig", errors="replace")
        items = parser(md)
        payload = {"source": src, "count": len(items), "items": items}
        (_DATA_DIR / dst).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{dst}: {len(items)} entries")


def _default_game_path() -> Path:
    settings = _DATA_DIR.parents[2] / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    return Path(data["game_path"])


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else _default_game_path())
