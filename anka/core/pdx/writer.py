"""Serialize the `nodes` DOM back to Paradox-script text.

Formatting mirrors HOI4 conventions: tab indentation, ``key = value`` with spaces,
short scalar-only blocks kept on one line, everything else expanded with one
statement per line.
"""
from __future__ import annotations

from pathlib import Path

from .nodes import Block, Pair, Scalar

_INDENT = "\t"
_INLINE_LIMIT = 5  # scalar-only blocks up to this many items stay on one line


def dumps(node: Block, *, top_level: bool = True) -> str:
    """Serialize a root `Block` to a string (no trailing newline trimming)."""
    if not isinstance(node, Block):
        raise TypeError("dumps expects a Block root node")
    lines: list[str] = []
    for item in node.items:
        lines.append(_render_item(item, 0))
    text = "\n".join(lines)
    return text + "\n" if text and top_level else text


def dump_file(node: Block, path: str | Path, encoding: str = "utf-8") -> None:
    """Write a script file (.txt/.gfx). HOI4 script must be UTF-8 **without** a BOM —
    a leading BOM makes the engine choke on the first token (``Unexpected token: ﻿…``).
    Localisation .yml is the opposite and is handled by `LocFile.save` (BOM required)."""
    Path(path).write_text(dumps(node), encoding=encoding)


# --- internals -----------------------------------------------------------

def _render_scalar(s: Scalar) -> str:
    return f'"{s.raw}"' if s.quoted else s.raw


def _render_block(block: Block, depth: int) -> str:
    tag = f"{block.tag} " if block.tag else ""
    if not block.items:
        return tag + "{}"

    scalar_only = all(isinstance(it, Scalar) for it in block.items)
    if scalar_only and len(block.items) <= _INLINE_LIMIT:
        inner = " ".join(_render_scalar(it) for it in block.items)
        return f"{tag}{{ {inner} }}"

    pad = _INDENT * (depth + 1)
    inner_lines = [pad + _render_item(it, depth + 1) for it in block.items]
    close_pad = _INDENT * depth
    return tag + "{\n" + "\n".join(inner_lines) + "\n" + close_pad + "}"


def _render_value(value, depth: int) -> str:
    if isinstance(value, Scalar):
        return _render_scalar(value)
    if isinstance(value, Block):
        return _render_block(value, depth)
    raise TypeError(f"Unexpected value node: {value!r}")


def _render_item(item, depth: int) -> str:
    if isinstance(item, Pair):
        return f"{item.key} {item.op} {_render_value(item.value, depth)}"
    if isinstance(item, Scalar):
        return _render_scalar(item)
    if isinstance(item, Block):
        return _render_block(item, depth)
    raise TypeError(f"Unexpected item node: {item!r}")
