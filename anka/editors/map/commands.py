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

from ..common.commands import CommandStack, CompoundCommand  # noqa: F401 (re-export)
from ..common.commands import Command as _BaseCommand

TOUCH_PIXELS = "pixels"
TOUCH_DEFS = "defs"
TOUCH_STATES = "states"
TOUCH_ZONES = "zones"


class Command(_BaseCommand):
    label_key = "map.cmd.generic"


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


class LayerPixelsCommand(Command):
    """A batch of repainted pixels on an auxiliary bitmap layer (heightmap /
    visual terrain). `service_attr` names the editor attribute holding the
    layer service. A stroke may touch the same pixel several times (numpy
    fancy assignment gives no order guarantee for duplicates), so the pixels
    are deduplicated up front: undo keeps each pixel's FIRST old value, redo
    its LAST new value."""

    touches = frozenset({TOUCH_PIXELS})

    def __init__(self, service_attr: str, label_key: str, ys: np.ndarray,
                 xs: np.ndarray, old: np.ndarray, new: np.ndarray):
        self.service_attr = service_attr
        self.label_key = label_key
        ys = np.asarray(ys, dtype=np.int64)
        xs = np.asarray(xs, dtype=np.int64)
        old = np.asarray(old, dtype=np.uint8)
        new = np.asarray(new, dtype=np.uint8)
        key = (ys << 32) | xs
        _, first = np.unique(key, return_index=True)
        _, last_rev = np.unique(key[::-1], return_index=True)
        last = len(key) - 1 - last_rev
        self.ys = ys[first].astype(np.int32)
        self.xs = xs[first].astype(np.int32)
        self.old = old[first]
        # `first` and `last` are both sorted by key, so the rows line up.
        self.new = new[last]

    def __len__(self) -> int:
        return len(self.ys)

    def undo(self, editor) -> None:
        service = getattr(editor, self.service_attr)
        service.restore_pixels(self.ys, self.xs, self.old)

    def redo(self, editor) -> None:
        service = getattr(editor, self.service_attr)
        service.restore_pixels(self.ys, self.xs, self.new)


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


class StateDocsCommand(Command):
    """Full before/after file snapshots of every state document a bulk operation
    creates, deletes or edits (generate states). Reliable where the field/province
    commands can't express file creation & deletion; each snapshot maps a file path
    to its text (``None`` = the file is absent)."""

    label_key = "map.cmd.gen_states"
    touches = frozenset({TOUCH_STATES})

    def __init__(self, before: dict, after: dict):
        self.before = dict(before)
        self.after = dict(after)

    def undo(self, editor) -> None:
        self._apply(editor, self.before)

    def redo(self, editor) -> None:
        self._apply(editor, self.after)

    def _apply(self, editor, snapshot: dict) -> None:
        from pathlib import Path
        for path_str, text in snapshot.items():
            p = Path(path_str)
            if text is None:
                p.unlink(missing_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            editor._dirty.discard(path_str)
            editor._dirty_docs.pop(path_str, None)
        # State caches now point at stale content — drop them so the tree and
        # inspector re-read from disk on the refresh that follows.
        editor.states.invalidate()
        cache = getattr(editor.states, "_doc_cache", None)
        if cache is not None:
            cache.clear()
        editor._state_doc = None
        editor._selected_state = None
