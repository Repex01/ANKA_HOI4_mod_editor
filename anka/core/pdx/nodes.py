"""DOM-like node model for Paradox script.

A document is a `Block`. A block holds an ordered list of *items*, where each item
is either a `Pair` (``key = value``) or a bare `Scalar`/`Block` (array element).
Duplicate keys are preserved (HOI4 relies on repeated keys, e.g. several `option`s),
so lookups distinguish "first" (`get`) from "all" (`get_all`).
"""
from __future__ import annotations

from typing import Iterator, Optional, Union

Item = Union["Pair", "Scalar", "Block"]
Value = Union["Scalar", "Block"]


class Node:
    """Base marker for all tree elements."""


class Scalar(Node):
    """A leaf value: bareword, number, date, boolean, or quoted string.

    `raw` is the source text without surrounding quotes; `quoted` records whether it
    was a string literal so serialization round-trips faithfully.
    """

    __slots__ = ("raw", "quoted")

    def __init__(self, raw: str, quoted: bool = False):
        self.raw = str(raw)
        self.quoted = quoted

    # --- typed views -----------------------------------------------------
    @property
    def value(self) -> str:
        return self.raw

    def as_int(self, default: int | None = None) -> int | None:
        try:
            return int(self.raw)
        except (ValueError, TypeError):
            return default

    def as_float(self, default: float | None = None) -> float | None:
        try:
            return float(self.raw)
        except (ValueError, TypeError):
            return default

    def as_bool(self) -> bool:
        return self.raw.lower() == "yes"

    @classmethod
    def of(cls, value) -> "Scalar":
        """Build a scalar from a Python value, quoting strings with spaces."""
        if isinstance(value, bool):
            return cls("yes" if value else "no")
        if isinstance(value, (int, float)):
            return cls(repr(value) if isinstance(value, float) else str(value))
        text = str(value)
        needs_quotes = (text == "") or any(c.isspace() for c in text)
        return cls(text, quoted=needs_quotes)

    def __repr__(self) -> str:
        return f"Scalar({self.raw!r}{', quoted' if self.quoted else ''})"


class Pair(Node):
    """A ``key <op> value`` statement. `op` is usually '=' but may be a comparator."""

    __slots__ = ("key", "value", "op")

    def __init__(self, key: str, value: Value, op: str = "="):
        self.key = key
        self.value = value
        self.op = op

    def __repr__(self) -> str:
        return f"Pair({self.key!r} {self.op} {self.value!r})"


class Block(Node):
    """An ordered collection of items, optionally tagged (e.g. ``rgb { ... }``)."""

    __slots__ = ("items", "tag")

    def __init__(self, items: Optional[list[Item]] = None, tag: str | None = None):
        self.items: list[Item] = items if items is not None else []
        self.tag = tag  # type prefix such as "rgb" / "hsv", or None

    # --- read API --------------------------------------------------------
    def pairs(self) -> Iterator[Pair]:
        for item in self.items:
            if isinstance(item, Pair):
                yield item

    def get(self, key: str) -> Optional[Value]:
        for pair in self.pairs():
            if pair.key == key:
                return pair.value
        return None

    def get_all(self, key: str) -> list[Value]:
        return [p.value for p in self.pairs() if p.key == key]

    def get_scalar(self, key: str, default=None):
        v = self.get(key)
        return v.raw if isinstance(v, Scalar) else default

    def get_block(self, key: str) -> Optional["Block"]:
        v = self.get(key)
        return v if isinstance(v, Block) else None

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def array_values(self) -> list[str]:
        """Bare scalar elements, e.g. the contents of ``traits = { a b c }``."""
        return [it.raw for it in self.items if isinstance(it, Scalar)]

    # --- write API -------------------------------------------------------
    def set(self, key: str, value: Value | str | int | float | bool) -> "Block":
        """Replace the first pair with `key`, or append one if absent."""
        node = value if isinstance(value, Node) else Scalar.of(value)
        for pair in self.pairs():
            if pair.key == key:
                pair.value = node
                return self
        self.items.append(Pair(key, node))
        return self

    def add(self, key: str, value: Value | str | int | float | bool) -> "Block":
        node = value if isinstance(value, Node) else Scalar.of(value)
        self.items.append(Pair(key, node))
        return self

    def add_value(self, value: str | int | float | bool) -> "Block":
        self.items.append(value if isinstance(value, Node) else Scalar.of(value))
        return self

    def remove(self, key: str) -> "Block":
        self.items = [
            it for it in self.items if not (isinstance(it, Pair) and it.key == key)
        ]
        return self

    def __iter__(self) -> Iterator[Item]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        # A Block node always *exists*; truthiness must not depend on emptiness, or
        # ``existing = parent.get_block(k) or Block()`` would silently detach an empty
        # block. Use ``len()`` / ``.items`` to test for contents.
        return True

    def __repr__(self) -> str:
        tag = f"{self.tag} " if self.tag else ""
        return f"Block({tag}{len(self.items)} items)"
