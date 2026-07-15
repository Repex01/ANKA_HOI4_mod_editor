"""Undo/redo commands for the GUI designer.

Nodes are addressed by ``(doc, window_index, child-index path)`` and
re-resolved through the tab at undo/redo time (the `GuiNode` wrappers are
transient). Structural commands snapshot the serialized pair text and
re-parse it, which reproduces the exact element including unknown keys.
All commands are recorded already executed (see `common.commands`).
"""
from __future__ import annotations

from ...core.pdx import Pair, parse as pdx_parse
from ..common.commands import Command

TOUCH_RENDER = "render"
TOUCH_TREE = "tree"


def _widget_items_index(parent_block, child_index: int) -> int:
    """Raw ``items`` index where the `child_index`-th widget child sits (or
    where it should be inserted): widget children are interleaved with
    attribute pairs."""
    from ...core.guitypes.schema import widget_spec
    from ...core.pdx import Block
    seen = 0
    for i, item in enumerate(parent_block.items):
        if (isinstance(item, Pair) and isinstance(item.value, Block)
                and widget_spec(item.key) is not None):
            if seen == child_index:
                return i
            seen += 1
    return len(parent_block.items)


class _NodeCommand(Command):
    touches = frozenset({TOUCH_RENDER})

    def __init__(self, doc, window_index: int, path: tuple[int, ...]):
        self.doc = doc
        self.window_index = window_index
        self.path = tuple(path)

    def _node(self, tab):
        return self.doc.find(self.window_index, self.path)


class SetAttrCommand(_NodeCommand):
    """One attribute swap; `kind` picks the writer: scalar / xy / script."""

    label_key = "interface.cmd.set_attr"

    def __init__(self, doc, window_index, path, attr: str, kind: str,
                 old, new):
        super().__init__(doc, window_index, path)
        self.attr = attr
        self.kind = kind
        self.old = old
        self.new = new
        if attr.lower() == "name":
            self.touches = frozenset({TOUCH_RENDER, TOUCH_TREE})

    def _apply(self, tab, value) -> None:
        node = self._node(tab)
        if node is None:
            return
        if self.kind == "xy":
            node.set_xy(self.attr, value[0], value[1])
        elif self.kind == "script":
            node.set_script(self.attr, value)
        else:
            node.set_attr(self.attr, value)
        tab.mark_dirty(self.doc)

    def undo(self, tab) -> None:
        self._apply(tab, self.old)

    def redo(self, tab) -> None:
        self._apply(tab, self.new)


class CreateNodeCommand(_NodeCommand):
    """`path` addresses the *parent* (``()`` = the window itself); `index` is
    the widget-child slot; `snapshot` is the serialized ``type = { ... }``."""

    label_key = "interface.cmd.create"
    touches = frozenset({TOUCH_RENDER, TOUCH_TREE})

    def __init__(self, doc, window_index, parent_path, index: int,
                 snapshot: str):
        super().__init__(doc, window_index, parent_path)
        self.index = index
        self.snapshot = snapshot

    def redo(self, tab) -> None:
        parent = self._node(tab)
        if parent is None:
            return
        parsed = pdx_parse(self.snapshot, recover=False)
        pair = next((it for it in parsed.items if isinstance(it, Pair)), None)
        if pair is None:
            return
        parent.block.items.insert(
            _widget_items_index(parent.block, self.index), pair)
        tab.mark_dirty(self.doc)

    def undo(self, tab) -> None:
        parent = self._node(tab)
        if parent is None:
            return
        kids = parent.children()
        if self.index < len(kids):
            parent.block.items.remove(kids[self.index].pair)
            tab.mark_dirty(self.doc)


class DeleteNodeCommand(_NodeCommand):
    label_key = "interface.cmd.delete"
    touches = frozenset({TOUCH_RENDER, TOUCH_TREE})

    def __init__(self, doc, window_index, parent_path, index: int,
                 snapshot: str):
        super().__init__(doc, window_index, parent_path)
        self.index = index
        self.snapshot = snapshot
        self._create = CreateNodeCommand(doc, window_index, parent_path,
                                         index, snapshot)

    def redo(self, tab) -> None:
        self._create.undo(tab)

    def undo(self, tab) -> None:
        self._create.redo(tab)


class ReorderCommand(_NodeCommand):
    """Move a widget child between z-order slots within the same parent."""

    label_key = "interface.cmd.reorder"
    touches = frozenset({TOUCH_RENDER, TOUCH_TREE})

    def __init__(self, doc, window_index, parent_path, old_index: int,
                 new_index: int):
        super().__init__(doc, window_index, parent_path)
        self.old_index = old_index
        self.new_index = new_index

    def _move(self, tab, src: int, dst: int) -> None:
        parent = self._node(tab)
        if parent is None:
            return
        kids = parent.children()
        if src >= len(kids):
            return
        pair = kids[src].pair
        parent.block.items.remove(pair)
        parent.block.items.insert(_widget_items_index(parent.block, dst), pair)
        tab.mark_dirty(self.doc)

    def redo(self, tab) -> None:
        self._move(tab, self.old_index, self.new_index)

    def undo(self, tab) -> None:
        self._move(tab, self.new_index, self.old_index)


class CreateWindowCommand(Command):
    """A new top-level window appended to ``guiTypes``."""

    label_key = "interface.cmd.create_window"
    touches = frozenset({TOUCH_RENDER, TOUCH_TREE})

    def __init__(self, doc, snapshot: str):
        self.doc = doc
        self.snapshot = snapshot

    def redo(self, tab) -> None:
        parsed = pdx_parse(self.snapshot, recover=False)
        pair = next((it for it in parsed.items if isinstance(it, Pair)), None)
        if pair is not None:
            self.doc.gui_types(create=True).items.append(pair)
            tab.mark_dirty(self.doc)

    def undo(self, tab) -> None:
        windows = self.doc.windows()
        if windows:
            self.doc.gui_types().items.remove(windows[-1].pair)
            tab.mark_dirty(self.doc)


class DeleteWindowCommand(Command):
    """Remove the `window_index`-th top-level window; undo re-parses the
    snapshot and splices it back into the same window slot."""

    label_key = "interface.cmd.delete_window"
    touches = frozenset({TOUCH_RENDER, TOUCH_TREE})

    def __init__(self, doc, window_index: int, snapshot: str):
        self.doc = doc
        self.window_index = window_index
        self.snapshot = snapshot

    def redo(self, tab) -> None:
        windows = self.doc.windows()
        if self.window_index < len(windows):
            self.doc.gui_types().items.remove(windows[self.window_index].pair)
            tab.mark_dirty(self.doc)

    def undo(self, tab) -> None:
        parsed = pdx_parse(self.snapshot, recover=False)
        pair = next((it for it in parsed.items if isinstance(it, Pair)), None)
        if pair is None:
            return
        gt = self.doc.gui_types(create=True)
        windows = self.doc.windows()
        if self.window_index < len(windows):
            raw_index = gt.items.index(windows[self.window_index].pair)
        else:
            raw_index = len(gt.items)
        gt.items.insert(raw_index, pair)
        tab.mark_dirty(self.doc)
