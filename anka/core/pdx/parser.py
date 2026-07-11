"""Parse Paradox script text into the `nodes` DOM using lark (LALR).

The parser is tolerant by design: HOI4 files are large and occasionally irregular,
and ANKA mostly reads them to extract data or regenerates whole blocks, so exotic
constructs (inline `@[ ... ]` math) degrade into bare tokens rather than failing.
"""
from __future__ import annotations

import functools
from pathlib import Path

from lark import Lark, Transformer, Token, v_args
from lark.exceptions import UnexpectedInput

from .nodes import Block, Pair, Scalar

_GRAMMAR_FILE = Path(__file__).with_name("grammar.lark")


@v_args(inline=False)
class _TreeBuilder(Transformer):
    """Lower the lark parse tree into ANKA's node model."""

    def STRING(self, tok: Token) -> Scalar:
        return Scalar(str(tok)[1:-1], quoted=True)

    def TOKEN(self, tok: Token) -> Scalar:
        return Scalar(str(tok))

    def tagged(self, children) -> Block:
        tag_scalar, block = children
        block.tag = tag_scalar.raw
        return block

    def block(self, children) -> Block:
        return Block(list(children))

    def pair(self, children) -> Pair:
        key, op, value = children
        return Pair(key.raw, value, op=str(op))

    def start(self, children) -> Block:
        return Block(list(children))


@functools.lru_cache(maxsize=1)
def _parser() -> Lark:
    return Lark(
        _GRAMMAR_FILE.read_text(encoding="utf-8"),
        parser="lalr",
        transformer=_TreeBuilder(),
        maybe_placeholders=False,
    )


def parse(text: str, *, recover: bool = True) -> Block:
    """Parse PDX source text and return the document as a root `Block`.

    Some vanilla/community files have unbalanced braces that the game tolerates.
    When `recover` is set, a failed strict parse is retried once on a brace-balanced
    copy of the source rather than raising.
    """
    # Strip a UTF-8 BOM if present; HOI4 files frequently carry one.
    if text and text[0] == "﻿":
        text = text[1:]
    try:
        return _parser().parse(text)
    except UnexpectedInput:
        if not recover:
            raise
        return _parser().parse(_balance_braces(text))


def _balance_braces(text: str) -> str:
    """Repair stray/missing braces while respecting comments and strings.

    Removes closers that would drop depth below zero and appends any closers still
    open at end-of-file — the same leniency HOI4's own parser applies.
    """
    out: list[str] = []
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(text[i:j])
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1   # skip escaped chars (\" \\)
            j = n if j >= n else j + 1
            out.append(text[i:j])
            i = j
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                i += 1  # drop stray closer
                continue
            depth -= 1
        out.append(ch)
        i += 1
    if depth > 0:
        out.append("\n" + "}" * depth)
    return "".join(out)


def parse_file(path: str | Path, encoding: str = "utf-8-sig") -> Block:
    return parse(Path(path).read_text(encoding=encoding, errors="replace"))
