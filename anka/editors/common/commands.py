"""Generic undo/redo building blocks — Command pattern.

Extracted from the map editor so every editor can share the same history
machinery. Commands are *recorded already executed*: the editor performs the
action through its services, then pushes the command; ``undo()``/``redo()``
re-drive the same APIs. `touches` tells the owning editor which caches/views
to refresh after a history step.
"""
from __future__ import annotations


class Command:
    label_key = "common.cmd.generic"
    touches: frozenset = frozenset()

    def undo(self, editor) -> None:
        raise NotImplementedError

    def redo(self, editor) -> None:
        raise NotImplementedError


class CompoundCommand(Command):
    """Children are redone in order and undone in reverse."""

    def __init__(self, children: list[Command], label_key: str):
        self.children = children
        self.label_key = label_key

    @property
    def touches(self) -> frozenset:  # type: ignore[override]
        out: frozenset = frozenset()
        for child in self.children:
            out |= child.touches
        return out

    def undo(self, editor) -> None:
        for child in reversed(self.children):
            child.undo(editor)

    def redo(self, editor) -> None:
        for child in self.children:
            child.redo(editor)


class CommandStack:
    """Classic two-stack undo history; commands enter already executed."""

    def __init__(self, limit: int = 40):
        self._limit = limit
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def record(self, command: Command) -> None:
        self._undo.append(command)
        del self._undo[:-self._limit]
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, editor) -> Command | None:
        if not self._undo:
            return None
        command = self._undo.pop()
        command.undo(editor)
        self._redo.append(command)
        return command

    def redo(self, editor) -> Command | None:
        if not self._redo:
            return None
        command = self._redo.pop()
        command.redo(editor)
        self._undo.append(command)
        return command

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
