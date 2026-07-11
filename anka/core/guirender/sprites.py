"""Sprite image decoding/slicing for the GUI renderer.

Wraps a `SpriteCatalog`: decodes textures once (Pillow reads DDS/TGA/PNG
natively), crops frame strips (``noOfFrames``), builds 9-slice scaled images
for ``corneredTileSpriteType`` (``borderSize`` + ``tilingCenter``) and reports
frame sizes to the layout solver. Unreadable/missing textures yield a
checkered placeholder instead of raising, and the miss is recorded so the
renderer can surface it as a problem.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..gfx import SpriteCatalog, SpriteDef


def placeholder(size: tuple[int, int]) -> Image.Image:
    """Magenta/black checker used for missing textures."""
    w, h = max(4, int(size[0])), max(4, int(size[1]))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 160))
    tile = 8
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if (x // tile + y // tile) % 2 == 0:
                img.paste((255, 0, 220, 160),
                          (x, y, min(x + tile, w), min(y + tile, h)))
    return img


class SpriteImageCache:
    def __init__(self, catalog: SpriteCatalog):
        self.catalog = catalog
        self._textures: dict[str, Image.Image | None] = {}   # path -> decoded
        self._frames: dict[tuple[str, int], Image.Image | None] = {}
        self.missing: set[str] = set()      # sprite names that failed to load

    # ----------------------------------------------------------------- decode
    def _decode(self, path: Path | None) -> Image.Image | None:
        if path is None:
            return None
        key = str(path).lower()
        if key in self._textures:
            return self._textures[key]
        img: Image.Image | None = None
        try:
            if path.exists():
                with Image.open(path) as im:
                    img = im.convert("RGBA")
        except Exception:
            img = None
        self._textures[key] = img
        return img

    def texture(self, name: str) -> Image.Image | None:
        d = self.catalog.get(name)
        if d is None:
            self.missing.add(name)
            return None
        img = self._decode(d.texture_path())
        if img is None:
            self.missing.add(name)
        return img

    # ----------------------------------------------------------------- frames
    def frame(self, name: str, frame: int = 1) -> Image.Image | None:
        """One frame of a (possibly multi-frame) sprite; frames are 1-based."""
        d = self.catalog.get(name)
        frames = d.view.no_of_frames() if d is not None else 1
        frame = max(1, min(frame, frames))
        key = (name, frame)
        if key in self._frames:
            return self._frames[key]
        img = self.texture(name)
        out: Image.Image | None = None
        if img is not None:
            if frames > 1:
                fw = max(1, img.width // frames)
                out = img.crop(((frame - 1) * fw, 0, frame * fw, img.height))
            else:
                out = img
        self._frames[key] = out
        return out

    def sprite_frame_size(self, name: str) -> tuple[int, int] | None:
        d = self.catalog.get(name)
        if d is None:
            return None
        # cornered tiles declare their design size explicitly
        sx, sy = d.view.get_xy("size")
        if sx and sy:
            try:
                return int(float(sx)), int(float(sy))
            except ValueError:
                pass
        img = self.texture(name)
        if img is None:
            return None
        frames = d.view.no_of_frames()
        return (img.width // frames if frames > 1 else img.width), img.height

    # ---------------------------------------------------------------- 9-slice
    def nine_slice(self, name: str, size: tuple[int, int]) -> Image.Image | None:
        """`corneredTileSpriteType` scaled to `size`: corners kept 1:1, edges
        and center tiled (or stretched when the border is zero)."""
        d = self.catalog.get(name)
        img = self.frame(name, 1)
        if img is None:
            return None
        w, h = max(1, int(size[0])), max(1, int(size[1]))
        bx_raw, by_raw = (d.view.get_xy("borderSize") if d is not None
                          else ("", ""))
        try:
            bx = int(float(bx_raw)) if bx_raw else 0
            by = int(float(by_raw)) if by_raw else 0
        except ValueError:
            bx = by = 0
        bx = min(bx, img.width // 2, w // 2)
        by = min(by, img.height // 2, h // 2)
        if bx <= 0 and by <= 0:
            return img.resize((w, h), Image.NEAREST)

        out = Image.new("RGBA", (w, h))
        sw, sh = img.width, img.height

        def piece(sx0, sy0, sx1, sy1, dx0, dy0, dx1, dy1, tile: bool) -> None:
            if sx1 <= sx0 or sy1 <= sy0 or dx1 <= dx0 or dy1 <= dy0:
                return
            part = img.crop((sx0, sy0, sx1, sy1))
            dw, dh = dx1 - dx0, dy1 - dy0
            if tile:
                for ty in range(dy0, dy1, part.height):
                    for tx in range(dx0, dx1, part.width):
                        cw = min(part.width, dx1 - tx)
                        ch = min(part.height, dy1 - ty)
                        out.paste(part.crop((0, 0, cw, ch)), (tx, ty))
            else:
                out.paste(part.resize((dw, dh), Image.NEAREST), (dx0, dy0))

        center_tiles = ((d.view.get_attr("tilingCenter") or "").lower() == "yes"
                        if d is not None else False)
        # corners
        piece(0, 0, bx, by, 0, 0, bx, by, False)
        piece(sw - bx, 0, sw, by, w - bx, 0, w, by, False)
        piece(0, sh - by, bx, sh, 0, h - by, bx, h, False)
        piece(sw - bx, sh - by, sw, sh, w - bx, h - by, w, h, False)
        # edges (tiled)
        piece(bx, 0, sw - bx, by, bx, 0, w - bx, by, True)
        piece(bx, sh - by, sw - bx, sh, bx, h - by, w - bx, h, True)
        piece(0, by, bx, sh - by, 0, by, bx, h - by, True)
        piece(sw - bx, by, sw, sh - by, w - bx, by, w, h - by, True)
        # center
        piece(bx, by, sw - bx, sh - by, bx, by, w - bx, h - by, center_tiles)
        return out

    # ------------------------------------------------------------------ misc
    def sprite_def(self, name: str) -> SpriteDef | None:
        return self.catalog.get(name)

    def invalidate(self, name: str | None = None) -> None:
        if name is None:
            self._textures.clear()
            self._frames.clear()
            self.missing.clear()
            return
        d = self.catalog.get(name)
        if d is not None:
            path = d.texture_path()
            if path is not None:
                self._textures.pop(str(path).lower(), None)
        self._frames = {k: v for k, v in self._frames.items() if k[0] != name}
        self.missing.discard(name)
