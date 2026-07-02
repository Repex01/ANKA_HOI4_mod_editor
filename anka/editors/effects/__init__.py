"""HOI4 script catalog: every game effect and trigger as data.

Effects/triggers are shared by many content types (focuses, events, decisions, ...),
so they live here as a standalone, editor-agnostic catalog. The source of truth is the
game's own ``documentation/effects_documentation.md`` / ``triggers_documentation.md``
(always in sync with the installed game version), converted to ``effects.json`` /
``triggers.json`` by ``generate.py`` and loaded at runtime into `Effect` / `Trigger`
instances via `ScriptCatalog`.
"""
from .catalog import Effect, ScriptCatalog, ScriptItem, Trigger

__all__ = ["Effect", "Trigger", "ScriptItem", "ScriptCatalog"]
