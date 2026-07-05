"""Runtime model of the HOI4 script catalog (effects + triggers).

The JSON databases next to this module are generated from the game's official script
documentation (see `generate.py`). At startup they are parsed once into `Effect` /
`Trigger` instances; UI code (script editors, snippet pickers) works only with these
objects — never with raw JSON — so the storage format can evolve freely.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).parent


@dataclass(frozen=True)
class ScriptItem:
    """One documented script keyword (an effect or a trigger)."""

    name: str
    scopes: tuple[str, ...] = ()        # e.g. ("COUNTRY", "STATE") or ("any",)
    targets: tuple[str, ...] = ()       # scopes this keyword may be targeted at
    description: str = ""
    example: str = ""                   # verbatim example from the docs ("" if none)
    display: str = ""                   # picker label override (e.g. "x [SHORT]")

    kind: str = field(default="", repr=False)

    @property
    def title(self) -> str:
        return self.display or self.name

    def snippet(self) -> str:
        """Ready-to-insert script text: the documented example when available,
        otherwise a minimal ``name = `` stub the user completes in place."""
        if self.example.strip():
            return self.example.strip("\n")
        return f"{self.name} = "

    def supports_scope(self, scope: str) -> bool:
        if not self.scopes or "any" in self.scopes:
            return True
        return scope.upper() in self.scopes


class Effect(ScriptItem):
    def __init__(self, **kw):
        super().__init__(kind="effect", **kw)


class Trigger(ScriptItem):
    def __init__(self, **kw):
        super().__init__(kind="trigger", **kw)


class Modifier(ScriptItem):
    """A static modifier (e.g. ``stability_factor``). `scopes` holds its categories
    (country/army/air/...), `description` the value format ("Number with 1 …")."""

    def __init__(self, **kw):
        super().__init__(kind="modifier", **kw)


def _load(filename: str, cls) -> dict[str, ScriptItem]:
    path = _DATA_DIR / filename
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, ScriptItem] = {}
    for row in data.get("items", []):
        item = cls(
            name=row.get("name", ""),
            scopes=tuple(row.get("scopes", [])),
            targets=tuple(row.get("targets", [])),
            description=row.get("desc", ""),
            example=row.get("example", ""),
        )
        if item.name:
            out[item.name] = item
    return out


# Effects that fire an event. Each gets TWO picker entries: the documented full
# form ([LONG], every optional field) and a minimal ``{ id = ... }`` one ([SHORT]).
_EVENT_EFFECT_NAMES = ("country_event", "news_event", "state_event",
                       "unit_leader_event", "operative_leader_event")
_EXAMPLE_ID_RE = re.compile(r"id\s*=\s*([\w.]+)")


def _add_event_effect_variants(items: dict[str, Effect]) -> dict[str, Effect]:
    for name in _EVENT_EFFECT_NAMES:
        full = items.get(name)
        if full is None:
            continue
        m = _EXAMPLE_ID_RE.search(full.example)
        sample = m.group(1) if m else "my_namespace.1"
        items[name] = Effect(
            name=name, scopes=full.scopes, targets=full.targets,
            description=full.description, example=full.example,
            display=f"{name} [LONG]")
        # dict key differs so both survive; `name` stays the real script keyword
        items[f"{name} [SHORT]"] = Effect(
            name=name, scopes=full.scopes, targets=full.targets,
            description=full.description,
            example=f"{name} = {{\n\tid = {sample}\n}}",
            display=f"{name} [SHORT]")
    return items


class ScriptCatalog:
    """Facade over the effect/trigger databases (loaded once per process)."""

    @staticmethod
    @lru_cache(maxsize=1)
    def effects() -> dict[str, Effect]:
        return _add_event_effect_variants(
            _load("effects.json", Effect))  # type: ignore[arg-type,return-value]

    @staticmethod
    @lru_cache(maxsize=1)
    def triggers() -> dict[str, Trigger]:
        return _load("triggers.json", Trigger)  # type: ignore[return-value]

    @staticmethod
    @lru_cache(maxsize=1)
    def modifiers() -> dict[str, Modifier]:
        return _load("modifiers.json", Modifier)  # type: ignore[return-value]

    # --- queries ----------------------------------------------------------
    @classmethod
    def items(cls, kind: str) -> dict[str, ScriptItem]:
        return {"effect": cls.effects, "trigger": cls.triggers,
                "modifier": cls.modifiers}[kind]()

    @classmethod
    def find(cls, name: str) -> ScriptItem | None:
        return cls.effects().get(name) or cls.triggers().get(name)

    @classmethod
    def search(cls, query: str, kind: str, scope: str | None = None) -> list[ScriptItem]:
        """Substring search over names (and descriptions for 3+ char queries)."""
        q = query.strip().lower()
        pool = cls.items(kind).values()
        if scope:
            pool = [it for it in pool if it.supports_scope(scope)]
        if not q:
            return sorted(pool, key=lambda it: it.name)
        by_name = [it for it in pool if q in it.name.lower()]
        if len(q) >= 3:
            named = {it.name for it in by_name}
            by_desc = [it for it in pool
                       if it.name not in named and q in it.description.lower()]
        else:
            by_desc = []
        # Name hits first (prefix matches before infix), then description hits.
        by_name.sort(key=lambda it: (not it.name.lower().startswith(q), it.name))
        return by_name + sorted(by_desc, key=lambda it: it.name)

    @classmethod
    def scopes(cls, kind: str) -> list[str]:
        seen: set[str] = set()
        for item in cls.items(kind).values():
            seen.update(item.scopes)
        return sorted(seen)
