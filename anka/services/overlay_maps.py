"""Auxiliary map bitmaps: ``heightmap.bmp`` (8-bit greyscale) and
``terrain.bmp`` (8-bit paletted "visual terrain").

Both share the province map's resolution and override rules (whole-file
copy into the mod). Arrays are numpy end-to-end like `MapService`: uint8
[H, W] plus a stamp/restore API returning changed-pixel deltas for the
undo stack.

The visual-terrain palette indices are mapped to graphical terrain names
via the top-level ``terrain = { ... }`` block of ``common/terrain/*.txt``
(each entry lists its ``terrain.bmp`` palette indices in ``color``), so
the editor can show a labelled swatch bar instead of raw indices.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ..core.pdx import Block, Scalar, parse_file
from .map_service import MapService, _flood_region

TERRAIN_DIR = "common/terrain"

_EMPTY_DELTA = (np.empty(0, np.int32), np.empty(0, np.int32),
                np.empty(0, np.uint8), np.empty(0, np.uint8))


def _brush_weights(size: int, shape: str) -> np.ndarray:
    """Weight stamp of a brush: float [d, d] in 0..1, 0 = outside the brush.
    Round brushes fall off smoothly to the rim; square brushes are flat."""
    radius = max(0, size - 1)
    d = radius * 2 + 1
    if shape == "square":
        return np.ones((d, d), dtype=np.float32)
    yy, xx = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    dist = np.sqrt(xx * xx + yy * yy)
    w = 1.0 - dist / (radius + 1.0)
    return np.clip(w, 0.0, 1.0).astype(np.float32)


def _box_blur(arr: np.ndarray, k: int) -> np.ndarray:
    """Box blur with kernel 2k+1 via cumulative sums (edges clamp)."""
    if k <= 0:
        return arr.astype(np.float32)
    padded = np.pad(arr.astype(np.float32), k, mode="edge")
    c = padded.cumsum(axis=0)
    padded = (np.vstack([c[2 * k:2 * k + 1], c[2 * k + 1:] - c[:-2 * k - 1]])
              / (2 * k + 1))
    c = padded.cumsum(axis=1)
    return (np.hstack([c[:, 2 * k:2 * k + 1], c[:, 2 * k + 1:] - c[:, :-2 * k - 1]])
            / (2 * k + 1))


class _BitmapLayer:
    """Shared plumbing: lazy load through `MapService` file resolution,
    dirty tracking, delta-based restore for undo."""

    FILENAME_KEY = ""
    DEFAULT_NAME = ""

    def __init__(self, map_service: MapService):
        self.map = map_service
        self._arr: np.ndarray | None = None
        self.dirty = False

    # ----------------------------------------------------------------- loading
    def _filename(self) -> str:
        return self.map.map_filenames().get(self.FILENAME_KEY, self.DEFAULT_NAME)

    def ensure(self) -> bool:
        """Load the bitmap once. False when the file is missing/unreadable."""
        if self._arr is not None:
            return True
        path = self.map.map_file(self._filename())
        if path is None:
            return False
        try:
            self._load(path)
        except Exception:
            return False
        return self._arr is not None

    def _load(self, path: Path) -> None:
        raise NotImplementedError

    @property
    def loaded(self) -> bool:
        return self._arr is not None

    @property
    def array(self) -> np.ndarray | None:
        return self._arr

    # ------------------------------------------------------------------ render
    def _crop(self, rect: tuple[int, int, int, int] | None):
        h, w = self._arr.shape
        if rect is None:
            rect = (0, 0, w, h)
        x0 = max(0, min(rect[0], w))
        y0 = max(0, min(rect[1], h))
        x1 = max(x0, min(rect[2], w))
        y1 = max(y0, min(rect[3], h))
        return self._arr[y0:y1, x0:x1], (x1 - x0, y1 - y0)

    @staticmethod
    def _scaled(img: Image.Image, size: tuple[int, int],
                scale: float) -> Image.Image:
        if scale == 1.0:
            return img
        tw = max(1, round(size[0] * scale))
        th = max(1, round(size[1] * scale))
        return img.resize((tw, th), Image.NEAREST)

    # ----------------------------------------------------------- undo plumbing
    def restore_pixels(self, ys: np.ndarray, xs: np.ndarray,
                       values: np.ndarray) -> None:
        if self._arr is None or len(ys) == 0:
            return
        self._arr[ys, xs] = values
        self.dirty = True

    def _footprint(self, cx: int, cy: int, size: int, shape: str):
        """Brush weights clipped to the map: (ys, xs, weights) or None."""
        h, w = self._arr.shape
        radius = max(0, size - 1)
        weights = _brush_weights(size, shape)
        x0, x1 = cx - radius, cx + radius + 1
        y0, y1 = cy - radius, cy + radius + 1
        wx0, wy0 = max(0, -x0), max(0, -y0)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x0 >= x1 or y0 >= y1:
            return None
        weights = weights[wy0:wy0 + (y1 - y0), wx0:wx0 + (x1 - x0)]
        inside = weights > 0.0
        ys, xs = np.nonzero(inside)
        return ys + y0, xs + x0, weights[inside]

    def _apply_new(self, ys: np.ndarray, xs: np.ndarray, new: np.ndarray):
        """Write `new` values, returning the changed-pixels delta."""
        old = self._arr[ys, xs]
        changed = old != new
        if not changed.any():
            return _EMPTY_DELTA
        ys, xs = ys[changed], xs[changed]
        old, new = old[changed].copy(), new[changed]
        self._arr[ys, xs] = new
        self.dirty = True
        return ys.astype(np.int32), xs.astype(np.int32), old, new

    # ------------------------------------------------------------------ saving
    def save(self) -> Path | None:
        if not self.dirty or self._arr is None:
            return None
        target = self.map.mod_map_path(self._filename())
        self._write(target)
        self.dirty = False
        return target

    def _write(self, target: Path) -> None:
        raise NotImplementedError


class HeightmapService(_BitmapLayer):
    """``heightmap.bmp``: 8-bit greyscale elevation, edited as raw 0..255.

    Saving also regenerates ``world_normal.bmp`` (the lighting normal map the
    game derives from the heightmap — usually at half resolution)."""

    FILENAME_KEY = "heightmap"
    DEFAULT_NAME = "heightmap.bmp"

    # Tangent-normal slope scale, calibrated against the vanilla pair
    # (heightmap.bmp vs world_normal.bmp at half resolution): the vanilla
    # normals are reproduced with ~2.9/255 mean error by
    # n = normalize((-s·dh/dx, +s·dh/dy, 1)) with s ≈ 0.023.
    _NORMAL_SCALE_HALF = 0.023

    def _load(self, path: Path) -> None:
        with Image.open(path) as im:
            self._arr = np.asarray(im.convert("L"), dtype=np.uint8).copy()

    def render(self, rect=None, scale: float = 1.0) -> Image.Image:
        crop, size = self._crop(rect)
        rgb = np.repeat(crop[:, :, None], 3, axis=2)
        return self._scaled(Image.fromarray(rgb, "RGB"), size, scale)

    def value_at(self, x: int, y: int) -> int | None:
        if self._arr is None:
            return None
        h, w = self._arr.shape
        if not (0 <= x < w and 0 <= y < h):
            return None
        return int(self._arr[y, x])

    def stamp(self, cx: int, cy: int, size: int, shape: str, op: str,
              strength: int = 8, level: int = 128):
        """One brush application. `op`: "raise" | "lower" | "set" | "smooth".
        Returns the changed-pixels delta ``(ys, xs, old, new)``."""
        if self._arr is None:
            return _EMPTY_DELTA
        fp = self._footprint(cx, cy, size, shape)
        if fp is None:
            return _EMPTY_DELTA
        ys, xs, weights = fp
        old = self._arr[ys, xs].astype(np.int16)
        if op in ("raise", "lower"):
            # весовое ядро даёт мягкий склон; минимум 1, чтобы слабая кисть
            # всё же действовала по всему следу
            delta = np.maximum(1, np.round(strength * weights)).astype(np.int16)
            new = old + (delta if op == "raise" else -delta)
        elif op == "set":
            new = np.full_like(old, int(level))
        elif op == "smooth":
            new = self._smoothed(ys, xs, weights, size, strength)
        else:
            return _EMPTY_DELTA
        new = np.clip(new, 0, 255).astype(np.uint8)
        return self._apply_new(ys, xs, new)

    def _smoothed(self, ys, xs, weights, size: int, strength: int) -> np.ndarray:
        """Blend brushed pixels toward a box-blurred neighborhood mean."""
        h, w = self._arr.shape
        radius = max(1, size - 1)
        y0, y1 = max(0, ys.min() - radius), min(h, ys.max() + radius + 1)
        x0, x1 = max(0, xs.min() - radius), min(w, xs.max() + radius + 1)
        blurred = _box_blur(self._arr[y0:y1, x0:x1], max(1, radius // 2))
        mean = blurred[ys - y0, xs - x0]
        old = self._arr[ys, xs].astype(np.float32)
        alpha = np.clip(strength / 16.0, 0.05, 1.0) * weights
        return np.round(old + (mean - old) * alpha).astype(np.int16)

    def _write(self, target: Path) -> None:
        Image.fromarray(self._arr, "L").save(target, format="BMP")
        self._write_world_normal()

    def world_normal_rgb(self, size: tuple[int, int]) -> np.ndarray:
        """Normal map (uint8 [th, tw, 3]) derived from the current heightmap,
        resampled to `size` = (width, height)."""
        tw, th = size
        h, w = self._arr.shape
        if w % tw == 0 and h % th == 0:          # exact ratio: mean pooling
            kx, ky = w // tw, h // th
            hs = self._arr.reshape(th, ky, tw, kx).astype(np.float32) \
                          .mean(axis=(1, 3))
        else:
            hs = np.asarray(Image.fromarray(self._arr, "L")
                            .resize((tw, th), Image.BILINEAR), dtype=np.float32)
        # scale keeps world-space slopes invariant across resolutions
        # (calibrated at ratio 2 → s = 2·S_half / ratio)
        s = self._NORMAL_SCALE_HALF * 2.0 * tw / w
        gy, gx = np.gradient(hs)
        nx, ny = -s * gx, s * gy
        inv_len = 1.0 / np.sqrt(nx * nx + ny * ny + 1.0)
        rgb = np.empty((th, tw, 3), dtype=np.uint8)
        rgb[:, :, 0] = np.clip(np.round(nx * inv_len * 127.5 + 127.5), 0, 255)
        rgb[:, :, 1] = np.clip(np.round(ny * inv_len * 127.5 + 127.5), 0, 255)
        rgb[:, :, 2] = np.clip(np.round(inv_len * 127.5 + 127.5), 0, 255)
        return rgb

    def _write_world_normal(self) -> None:
        """Regenerate ``world_normal.bmp`` in the mod at the effective file's
        resolution (half the heightmap when there is no file to match)."""
        source = self.map.map_file("world_normal.bmp")
        h, w = self._arr.shape
        if source is not None:
            try:
                with Image.open(source) as im:
                    size = im.size
            except Exception:
                size = (w // 2, h // 2)
        else:
            size = (w // 2, h // 2)
        rgb = self.world_normal_rgb(size)
        target = self.map.mod_map_path("world_normal.bmp")
        Image.fromarray(rgb, "RGB").save(target, format="BMP")


class TerrainBmpService(_BitmapLayer):
    """``terrain.bmp``: the visual landscape as palette indices."""

    FILENAME_KEY = "terrain"
    DEFAULT_NAME = "terrain.bmp"

    def __init__(self, map_service: MapService):
        super().__init__(map_service)
        self._palette: list[tuple[int, int, int]] = []
        self._index_names: dict[int, str] | None = None

    def _load(self, path: Path) -> None:
        with Image.open(path) as im:
            if im.mode != "P":               # unexpected: quantize to a palette
                im = im.convert("RGB").quantize(colors=256)
            flat = im.getpalette() or []
            self._palette = [tuple(flat[i:i + 3])
                             for i in range(0, len(flat) - 2, 3)]
            while len(self._palette) < 256:
                self._palette.append((0, 0, 0))
            self._arr = np.asarray(im, dtype=np.uint8).copy()

    @property
    def palette(self) -> list[tuple[int, int, int]]:
        return self._palette

    def render(self, rect=None, scale: float = 1.0) -> Image.Image:
        crop, size = self._crop(rect)
        lut = np.array(self._palette, dtype=np.uint8)
        return self._scaled(Image.fromarray(lut[crop], "RGB"), size, scale)

    # ------------------------------------------------------------ palette bar
    def index_names(self) -> dict[int, str]:
        """palette index → graphical terrain name, from the top-level
        ``terrain`` block of ``common/terrain/*.txt`` (game first, mod last,
        so mods override)."""
        if self._index_names is None:
            names: dict[int, str] = {}
            for root in self.map.ctx.override_roots(TERRAIN_DIR):
                folder = root / TERRAIN_DIR
                if not folder.is_dir():
                    continue
                for file in sorted(folder.glob("*.txt")):
                    try:
                        block = parse_file(file)
                    except Exception:
                        continue
                    terrain = block.get_block("terrain")
                    if terrain is None:
                        continue
                    for pair in terrain.pairs():
                        if not isinstance(pair.value, Block):
                            continue
                        tname = pair.value.get_scalar("type", "") or pair.key
                        cblock = pair.value.get_block("color")
                        if cblock is None:
                            continue
                        for v in cblock.array_values():
                            if v.isdigit():
                                names[int(v)] = tname
            self._index_names = names
        return self._index_names

    def swatches(self) -> list[tuple[int, tuple[int, int, int], str]]:
        """(index, rgb, name) for every palette index declared by a graphical
        terrain — the editor's palette bar. Sorted by index."""
        if not self.ensure():
            return []
        return [(idx, self._palette[idx], name)
                for idx, name in sorted(self.index_names().items())
                if idx < len(self._palette)]

    # ------------------------------------------------------------------- tools
    def index_at(self, x: int, y: int) -> int | None:
        if self._arr is None:
            return None
        h, w = self._arr.shape
        if not (0 <= x < w and 0 <= y < h):
            return None
        return int(self._arr[y, x])

    def stamp(self, cx: int, cy: int, size: int, shape: str, index: int):
        """Paint the brush footprint with a palette index (hard edges)."""
        if self._arr is None:
            return _EMPTY_DELTA
        fp = self._footprint(cx, cy, size, shape)
        if fp is None:
            return _EMPTY_DELTA
        ys, xs, _weights = fp
        new = np.full(len(ys), int(index), dtype=np.uint8)
        return self._apply_new(ys, xs, new)

    def flood_fill(self, x: int, y: int, index: int):
        """Fill the connected same-index region around (x, y)."""
        if self._arr is None:
            return _EMPTY_DELTA
        h, w = self._arr.shape
        if not (0 <= x < w and 0 <= y < h):
            return _EMPTY_DELTA
        seed = int(self._arr[y, x])
        if seed == int(index):
            return _EMPTY_DELTA
        region = _flood_region(self._arr == seed, y, x)
        ys, xs = np.nonzero(region)
        new = np.full(len(ys), int(index), dtype=np.uint8)
        return self._apply_new(ys.astype(np.int64), xs.astype(np.int64), new)

    def _write(self, target: Path) -> None:
        im = Image.fromarray(self._arr, "P")
        im.putpalette([c for rgb in self._palette for c in rgb])
        im.save(target, format="BMP")
