"""Paradox-script (PDX / Clausewitz) parsing and serialization.

The public surface is intentionally small:

    from anka.core.pdx import parse, dumps, Block, Pair, Scalar

`parse(text) -> Block` yields a DOM-like tree; `dumps(block) -> str` serializes it
back. Editors manipulate the tree through the convenience API on `Block`.
"""
from .nodes import Block, Node, Pair, Scalar
from .parser import parse, parse_file
from .writer import dump_file, dumps

__all__ = [
    "Block", "Node", "Pair", "Scalar",
    "parse", "parse_file", "dumps", "dump_file",
]
