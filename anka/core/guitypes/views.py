"""Typed views over parsed interface PDX blocks.

`GuiNode` wraps one widget pair (``iconType = { ... }``) inside a window tree;
`GuiDoc` wraps a whole ``guiTypes`` document; `SpriteView` one ``.gfx`` entry;
`SguiView` one scripted-GUI entry. Views never copy: they read and write the
underlying `Block` in place (case-insensitively, preserving on-file key casing
via `Block.set_ci`), so unknown keys and duplicate entries survive untouched.
"""
from __future__ import annotations

from ..pdx import Block, Pair, Scalar, dumps
from ..pdx import parse as pdx_parse
from .schema import (
    AttrKind,
    SpriteKindSpec,
    WidgetSpec,
    sprite_kind,
    widget_spec,
)

# Kinds whose values vanilla conventionally writes quoted.
_QUOTED_KINDS = frozenset({
    AttrKind.SPRITE_REF, AttrKind.FONT_REF, AttrKind.LOC_TEXT,
    AttrKind.TEXTURE, AttrKind.SOUND_REF, AttrKind.STR,
})


def _unquote(raw: str | None) -> str:
    return raw.strip('"') if raw else ""


def _scalar_for(raw: str, kind: AttrKind | None, existing: Scalar | None) -> Scalar:
    """Build the scalar for a write, preserving the existing quoting style when
    the key is already on file, else following the kind's convention."""
    if existing is not None:
        return Scalar(raw, quoted=existing.quoted)
    if kind in _QUOTED_KINDS or any(c.isspace() for c in raw):
        return Scalar(raw, quoted=True)
    return Scalar(raw)


def _get_pair_ci(block: Block, key: str) -> Pair | None:
    low = key.lower()
    for pair in block.pairs():
        if pair.key.lower() == low:
            return pair
    return None


class _CIAttrs:
    """Shared case-insensitive attribute access over ``self.block``."""

    block: Block

    def _kind_of(self, name: str) -> AttrKind | None:
        return None

    def get_attr(self, name: str) -> str:
        v = self.block.get_ci(name)
        return _unquote(v.raw) if isinstance(v, Scalar) else ""

    def get_attr_block(self, name: str) -> Block | None:
        return self.block.get_block_ci(name)

    def set_attr(self, name: str, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            self.block.remove_ci(name)
            return
        pair = _get_pair_ci(self.block, name)
        existing = pair.value if pair and isinstance(pair.value, Scalar) else None
        self.block.set_ci(name, _scalar_for(raw, self._kind_of(name), existing))

    def get_script(self, name: str) -> str:
        v = self.block.get_ci(name)
        if isinstance(v, Block):
            return dumps(v, top_level=False)
        if isinstance(v, Scalar):
            return v.raw
        return ""

    def set_script(self, name: str, text: str) -> None:
        if not text.strip():
            self.block.remove_ci(name)
            return
        parsed = pdx_parse(text, recover=False)
        if len(parsed.items) == 1 and isinstance(parsed.items[0], Scalar):
            self.block.set_ci(name, parsed.items[0])
        else:
            self.block.set_ci(name, Block(parsed.items))

    # --- {x= y=} / {width= height=} vector helpers -------------------------
    def get_xy(self, name: str) -> tuple[str, str]:
        """Raw components of a vector block; supports both axis spellings.
        Returns ("", "") when the block is absent."""
        block = self.block.get_block_ci(name)
        if block is None:
            return "", ""
        x = block.get_scalar_ci("x") or block.get_scalar_ci("width") or ""
        y = block.get_scalar_ci("y") or block.get_scalar_ci("height") or ""
        return _unquote(x), _unquote(y)

    def set_xy(self, name: str, x: str, y: str) -> None:
        """Write a vector block, preserving the existing axis-key spelling."""
        x, y = x.strip(), y.strip()
        if not x and not y:
            self.block.remove_ci(name)
            return
        block = self.block.get_block_ci(name)
        if block is None:
            block = Block()
            self.block.set_ci(name, block)
        x_key = "width" if block.has_ci("width") else "x"
        y_key = "height" if block.has_ci("height") else "y"
        if x:
            block.set_ci(x_key, Scalar(x))
        else:
            block.remove_ci(x_key)
        if y:
            block.set_ci(y_key, Scalar(y))
        else:
            block.remove_ci(y_key)


class GuiNode(_CIAttrs):
    """One widget element: a live view over its ``<type> = { ... }`` pair."""

    def __init__(self, pair: Pair, parent: "GuiNode | None" = None):
        self.pair = pair
        self.parent = parent
        self.type_key = pair.key
        self.spec: WidgetSpec | None = widget_spec(pair.key)

    @property
    def block(self) -> Block:  # type: ignore[override]
        return self.pair.value

    @property
    def name(self) -> str:
        return self.get_attr("name")

    def _kind_of(self, name: str) -> AttrKind | None:
        if self.spec is None:
            return None
        low = name.lower()
        for attr in self.spec.attrs:
            if attr.name.lower() == low:
                return attr.kind
        return None

    @property
    def is_container(self) -> bool:
        return bool(self.spec and self.spec.container)

    def children(self) -> list["GuiNode"]:
        out: list[GuiNode] = []
        for item in self.block.items:
            if (isinstance(item, Pair) and isinstance(item.value, Block)
                    and widget_spec(item.key) is not None):
                out.append(GuiNode(item, self))
        return out

    def child_index(self) -> int:
        """Index among the parent's widget children (not raw block items)."""
        if self.parent is None:
            return 0
        for i, child in enumerate(self.parent.children()):
            if child.pair is self.pair:
                return i
        return -1

    def index_path(self) -> tuple[int, ...]:
        node, path = self, []
        while node.parent is not None:
            path.append(node.child_index())
            node = node.parent
        return tuple(reversed(path))

    # --- geometry -----------------------------------------------------------
    def position_key(self) -> str:
        """Vanilla windows animated with show/hide keep their *hidden* offset
        in ``position`` and the on-screen one in ``show_position``; the editor
        displays and edits the shown state."""
        return "show_position" if self.block.has_ci("show_position") else "position"

    def get_position(self) -> tuple[float, float]:
        x, y = self.get_xy(self.position_key())
        return _to_float(x), _to_float(y)

    def set_position(self, x: float, y: float) -> None:
        self.set_xy(self.position_key(), _fmt_num(x), _fmt_num(y))

    def get_size_raw(self) -> tuple[str, str]:
        return self.get_xy("size")

    def set_size_raw(self, w: str, h: str) -> None:
        self.set_xy("size", w, h)

    @property
    def orientation(self) -> str:
        return (self.get_attr("orientation") or "upper_left").lower()

    @property
    def origo(self) -> str:
        return (self.get_attr("origo") or "upper_left").lower()

    # --- misc ----------------------------------------------------------------
    def snapshot(self) -> str:
        """Serialized ``<type> = { ... }`` text (for delete/create undo)."""
        return dumps(Block([self.pair]), top_level=True)

    def unknown_keys(self) -> list[str]:
        if self.spec is None:
            return []
        known = {a.name.lower() for a in self.spec.attrs}
        known.update(w.lower() for w in ("background", "backGround"))
        out: list[str] = []
        for item in self.block.items:
            if not isinstance(item, Pair):
                continue
            low = item.key.lower()
            if low in known or widget_spec(item.key) is not None:
                continue
            if low not in out:
                out.append(item.key)
        return out

    def __repr__(self) -> str:
        return f"GuiNode({self.type_key} {self.name!r})"


class GuiDoc:
    """A parsed ``.gui`` document: ``guiTypes = { <windows> }``."""

    def __init__(self, ref, root: Block):
        self.ref = ref
        self.root = root

    def gui_types(self, create: bool = False) -> Block | None:
        block = self.root.get_block_ci("guiTypes")
        if block is None and create:
            block = Block()
            self.root.add("guiTypes", block)
        return block

    def windows(self) -> list[GuiNode]:
        block = self.gui_types()
        if block is None:
            return []
        return [GuiNode(p) for p in block.pairs()
                if isinstance(p.value, Block) and widget_spec(p.key) is not None]

    def find(self, window_index: int, path: tuple[int, ...] = ()) -> GuiNode | None:
        windows = self.windows()
        if window_index >= len(windows):
            return None
        node = windows[window_index]
        for i in path:
            kids = node.children()
            if i >= len(kids):
                return None
            node = kids[i]
        return node


class SpriteView(_CIAttrs):
    """One ``.gfx`` entry (``spriteType = { ... }`` and friends)."""

    def __init__(self, pair: Pair):
        self.pair = pair
        self.type_key = pair.key
        self.spec: SpriteKindSpec | None = sprite_kind(pair.key)

    @property
    def block(self) -> Block:  # type: ignore[override]
        return self.pair.value

    @property
    def name(self) -> str:
        return self.get_attr("name")

    def _kind_of(self, name: str) -> AttrKind | None:
        if self.spec is None:
            return None
        low = name.lower()
        for attr in self.spec.attrs:
            if attr.name.lower() == low:
                return attr.kind
        return None

    @property
    def texture(self) -> str:
        """The primary texture path (any of the spellings across kinds)."""
        for key in ("texturefile", "textureFile", "texture", "textureFile1"):
            value = self.get_attr(key)
            if value:
                return value
        return ""

    def animations(self) -> list[Block]:
        return [v for v in self.block.get_all_ci("animation")
                if isinstance(v, Block)]

    def add_animation(self) -> Block:
        block = Block()
        self.block.add("animation", block)
        return block

    def remove_animation(self, anim: Block) -> None:
        self.block.items = [
            it for it in self.block.items
            if not (isinstance(it, Pair) and it.value is anim)
        ]

    def no_of_frames(self) -> int:
        try:
            return max(1, int(float(self.get_attr("noOfFrames") or "1")))
        except ValueError:
            return 1

    def snapshot(self) -> str:
        return dumps(Block([self.pair]), top_level=True)

    def __repr__(self) -> str:
        return f"SpriteView({self.type_key} {self.name!r})"


class SguiView(_CIAttrs):
    """One scripted-GUI entry: ``<name> = { ... }`` inside ``scripted_gui``."""

    def __init__(self, pair: Pair, parent: Block):
        self.pair = pair
        self.parent = parent

    @property
    def block(self) -> Block:  # type: ignore[override]
        return self.pair.value

    @property
    def name(self) -> str:
        return self.pair.key

    def snapshot(self) -> str:
        return dumps(Block([self.pair]), top_level=True)

    def __repr__(self) -> str:
        return f"SguiView({self.name!r})"


def _to_float(raw: str) -> float:
    try:
        return float(raw.rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"
