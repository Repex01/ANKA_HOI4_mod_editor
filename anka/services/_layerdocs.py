"""Shared helper for dependency-aware ``list_docs`` implementations.

Editors list content files as *document refs* merged across the content layers
(base game → dependency mods → the edited mod). A higher layer's file overrides a
lower one with the same relative path (HOI4 file-override semantics); the edited mod's
own files are editable, everything below (dependencies and base game) is read-only.

Each service supplies its own ``scan(root, is_vanilla) -> list[ref]`` (the cheap
per-file scan it already had); this helper only orchestrates the layering so the merge
logic lives in one place. With no dependencies AND no ``replace_path`` the layer list
is just ``[game, mod]``, so the result is identical to the old mod+vanilla merge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


def layered_docs(ctx, rel: str, scan: Callable[[Path, bool], list],
                 *, include_vanilla: bool,
                 rel_key: Callable[[object], str] = lambda r: r.rel_file,
                 set_edited: Callable[[object], None] | None = None,
                 sort_key: Callable[[object], object] | None = None) -> list:
    layers = ctx.override_layers(rel)                 # low → high priority
    scanned: dict[Path, list] = {}
    below: dict[Path, set[str]] = {}
    acc: set[str] = set()
    for root, is_mod in layers:
        refs = scan(root, not is_mod)
        scanned[root] = refs
        below[root] = set(acc)
        acc |= {rel_key(r).lower() for r in refs}

    out: list = []
    seen: set[str] = set()
    for root, is_mod in reversed(layers):             # high → low (higher overrides)
        # Dependency layers are always listed (the active base this mod builds on);
        # only pure base-game files honour `include_vanilla`.
        if root == ctx.game_path and not include_vanilla:
            continue
        for ref in scanned.get(root, ()):
            key = rel_key(ref).lower()
            if key in seen:
                continue
            seen.add(key)
            if is_mod and set_edited is not None and key in below[root]:
                set_edited(ref)
            out.append(ref)
    if sort_key is not None:
        out.sort(key=sort_key)
    return out
