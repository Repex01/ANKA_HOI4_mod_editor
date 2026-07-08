"""Undo/redo for the map editor — Command pattern.

Every user operation that mutates the map is captured as a `Command` holding
just enough state to revert and replay itself (pixel deltas, old/new definition
fields, membership snapshots). Commands are *recorded already executed* — the
editor performs the action through the services as before, then pushes the
command onto the `CommandStack`; ``undo()``/``redo()`` re-drive the same
service APIs. `touches` tells the editor which caches/views to refresh.

Covered: brush strokes, flood fill, province definition edits, new-province
creation, state↔province assignment, province splitting (compound), and the
strategic-region membership that rides along with those operations. Inspector
form fields (manpower, VP, buildings, ...) are ordinary text edits and are NOT
on the stack.
"""
from __future__ import annotations

import numpy as np

TOUCH_PIXELS = "pixels"
TOUCH_DEFS = "defs"
TOUCH_STATES = "states"
TOUCH_ZONES = "zones"


class Command:
    label_key = "map.cmd.generic"
    touches: frozenset = frozenset()

    def undo(self, editor) -> None:
        raise NotImplementedError

    def redo(self, editor) -> None:
        raise NotImplementedError


class PixelsCommand(Command):
    """A batch of repainted pixels. `old_codes` is per-pixel; `new_codes` is a
    per-pixel array or one code (uniform brush/fill)."""

    label_key = "map.cmd.paint"
    touches = frozenset({TOUCH_PIXELS})

    def __init__(self, ys: np.ndarray, xs: np.ndarray,
                 old_codes, new_codes):
        self.ys = np.asarray(ys, dtype=np.int32)
        self.xs = np.asarray(xs, dtype=np.int32)
        # 0-d arrays (uniform code) are fine — restore_pixels broadcasts them.
        self.old_codes = np.asarray(old_codes, dtype=np.uint32)
        self.new_codes = np.asarray(new_codes, dtype=np.uint32)

    def __len__(self) -> int:
        return len(self.ys)

    def undo(self, editor) -> None:
        editor.map.restore_pixels(self.ys, self.xs, self.old_codes)

    def redo(self, editor) -> None:
        editor.map.restore_pixels(self.ys, self.xs, self.new_codes)


class SetDefCommand(Command):
    """definition.csv field edit (type/terrain/coastal/continent)."""

    label_key = "map.cmd.def"
    touches = frozenset({TOUCH_DEFS})

    def __init__(self, pid: int, old: dict, new: dict):
        self.pid = pid
        self.old = old
        self.new = new

    def undo(self, editor) -> None:
        editor.map.set_def(self.pid, **self.old)

    def redo(self, editor) -> None:
        editor.map.set_def(self.pid, **self.new)


class CreateDefCommand(Command):
    """A new definition row (id and color are fixed, so redo re-creates the
    exact same province the pixel commands refer to)."""

    label_key = "map.cmd.create"
    touches = frozenset({TOUCH_DEFS})

    def __init__(self, d):
        self.fields = dict(id=d.id, color=d.color, type=d.type,
                           terrain=d.terrain, continent=d.continent,
                           coastal=d.coastal)

    def undo(self, editor) -> None:
        editor.map.remove_province(self.fields["id"])

    def redo(self, editor) -> None:
        editor.map.restore_def(**self.fields)


class StateProvincesCommand(Command):
    """Snapshot swap of one state's ``provinces`` list."""

    label_key = "map.cmd.assign"
    touches = frozenset({TOUCH_STATES})

    def __init__(self, state_id: int, old: list[int], new: list[int]):
        self.state_id = state_id
        self.old = list(old)
        self.new = list(new)

    def undo(self, editor) -> None:
        self._apply(editor, self.old)

    def redo(self, editor) -> None:
        self._apply(editor, self.new)

    def _apply(self, editor, provinces: list[int]) -> None:
        ref = editor.states.doc_ref_for(self.state_id)
        if ref is None:
            return
        if ref.is_vanilla:      # the do-path always went through a mod copy
            ref = editor.states.copy_to_mod(ref)
        doc = editor._dirty_docs.get(str(ref.path)) or editor.states.load(ref)
        if doc.state is None:
            return
        doc.state.set_provinces(list(provinces))
        editor.mark_dirty(doc)
        editor.states.refresh_info(doc)


class ZoneMembersCommand(Command):
    """Snapshot swap of one zone's member list (strategic region / supply
    area). `service_attr` names the editor attribute holding the service."""

    label_key = "map.cmd.region"
    touches = frozenset({TOUCH_ZONES})

    def __init__(self, service_attr: str, zone_id: int,
                 old: list[int], new: list[int]):
        self.service_attr = service_attr
        self.zone_id = zone_id
        self.old = list(old)
        self.new = list(new)

    def undo(self, editor) -> None:
        self._apply(editor, self.old)

    def redo(self, editor) -> None:
        self._apply(editor, self.new)

    def _apply(self, editor, members: list[int]) -> None:
        service = getattr(editor, self.service_attr)
        doc = service.doc_for_zone(self.zone_id)
        if doc is None:
            return
        doc = service.to_mod(doc)
        doc.set_members(list(members))
        service.save_doc(doc)


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
