"""Shared block-backed view helper for the editor services.

`BlockView` bundles the scalar / tri-state-flag / script accessors that every
block-backed model (`Decision`, `Event`, `Option`, `IdeaDef`, `CategoryDef`, ...)
needs over a parsed PDX `Block`. Subclasses expose a ``block`` property and set
``FLAG_DEFAULTS`` (the per-flag game default: a flag equal to its default is
written as *absence*, so only meaningful values reach the file).

Note on ``Block.__bool__``: a `Block` is always truthy (see ``core.pdx.nodes``),
so probing a block's presence with ``if self.block.get(k):`` is wrong for empty
blocks — the accessors here test node *type* (``isinstance(v, Block/Scalar)``)
instead.
"""
from __future__ import annotations

from ..core.pdx import Block, Scalar, dumps
from ..core.pdx import parse as pdx_parse


class BlockView:
    """Shared scalar/flag/script accessors over a PDX block."""

    block: Block
    FLAG_DEFAULTS: dict[str, bool] = {}

    def get_raw(self, key: str) -> str:
        v = self.block.get(key)
        return v.raw if isinstance(v, Scalar) else ""

    def set_raw(self, key: str, value: str) -> None:
        value = value.strip()
        if value:
            self.block.set(key, Scalar(value))
        else:
            self.block.remove(key)

    def get_flag(self, name: str) -> bool:
        v = self.block.get(name)
        if isinstance(v, Scalar):
            return v.as_bool()
        return self.FLAG_DEFAULTS.get(name, False)

    def set_flag(self, name: str, value: bool) -> None:
        if value == self.FLAG_DEFAULTS.get(name, False):
            self.block.remove(name)
        else:
            self.block.set(name, bool(value))

    def get_script(self, key: str) -> str:
        v = self.block.get(key)
        if isinstance(v, Block):
            return dumps(v, top_level=False)
        if isinstance(v, Scalar):
            return v.raw
        return ""

    def set_script(self, key: str, text: str) -> None:
        """Parse `text` strictly and store under `key`; empty removes the key."""
        if not text.strip():
            self.block.remove(key)
            return
        parsed = pdx_parse(text, recover=False)
        if len(parsed.items) == 1 and isinstance(parsed.items[0], Scalar):
            self.block.set(key, parsed.items[0])
        else:
            self.block.set(key, Block(parsed.items))
