"""Low-level image conversion built on Pillow.

The user supplies graphics in any convenient format (jpg/png/bmp/...); this layer
loads, resizes and writes them as the TGA/DDS assets HOI4 expects. Pillow 10+ writes
both uncompressed (ARGB8888) and DXT-compressed DDS natively, so no external tool is
required for the common cases.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from PIL import Image

Image.init()  # populate the SAVE registry so TGA/DDS are available


class ImageFormat(str, Enum):
    TGA = "tga"
    DDS = "dds"
    PNG = "png"


# HOI4-friendly resampling: high quality downscale for icons/flags.
_RESAMPLE = Image.Resampling.LANCZOS
# Background behind an inset (small) portrait — matches the dark tone the game
# uses behind advisor art.
_INSET_BG = (26, 24, 22, 255)


class ImageConverter:
    """Stateless conversion helpers. Each method is independently usable."""

    @staticmethod
    def load(path: str | Path) -> Image.Image:
        """Open any supported image and normalize it to RGBA."""
        img = Image.open(path)
        img.load()
        return img.convert("RGBA") if img.mode != "RGBA" else img

    @staticmethod
    def fit(img: Image.Image, size: tuple[int, int],
            crop: bool = False, keep_top: float = 1.0,
            inset: float = 0.0) -> Image.Image:
        """Resize to an exact size (HOI4 sprites are fixed-dimension, not aspect-fit).

        With ``crop=True`` the aspect ratio is preserved: the image is scaled to
        cover the target and the excess is cut away — centred horizontally and
        anchored at the top, which keeps a portrait's head in frame instead of
        squashing it. Used for character portraits; flags and icons keep the
        plain stretch, where an exact fit is what the game expects.
        """
        if keep_top < 1.0:
            # Take the upper part of the source first: the head belongs in frame.
            w, h = img.size
            img = img.crop((0, 0, w, max(1, int(round(h * keep_top)))))
        if inset > 0.0:
            # Small (advisor) portraits sit inside a border rather than filling
            # the tile edge to edge, so the picture is scaled to FIT with a dark
            # margin around it — cropping to cover makes the face far too large.
            tw, th = size
            iw, ih = max(1, int(tw * (1 - inset))), max(1, int(th * (1 - inset)))
            fitted = img.copy()
            fitted.thumbnail((iw, ih), _RESAMPLE)
            canvas = Image.new("RGBA", size, _INSET_BG)
            canvas.paste(fitted,
                         ((tw - fitted.width) // 2, (th - fitted.height) // 2),
                         fitted if fitted.mode == "RGBA" else None)
            return canvas
        if img.size == size:
            return img
        if not crop:
            return img.resize(size, _RESAMPLE)
        tw, th = size
        w, h = img.size
        scale = max(tw / w, th / h)
        new_size = (max(tw, int(round(w * scale))), max(th, int(round(h * scale))))
        scaled = img.resize(new_size, _RESAMPLE)
        left = max(0, (new_size[0] - tw) // 2)      # centre horizontally
        top = 0                                     # keep the head, cut the feet
        return scaled.crop((left, top, left + tw, top + th))

    @staticmethod
    def save_tga(img: Image.Image, dest: str | Path, size: tuple[int, int] | None = None) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = ImageConverter._prepare(img, size)
        out.save(dest, format="TGA")
        return dest

    @staticmethod
    def save_dds(
        img: Image.Image,
        dest: str | Path,
        size: tuple[int, int] | None = None,
        compressed: bool = False,
        crop: bool = False,
        keep_top: float = 1.0,
        inset: float = 0.0,
    ) -> Path:
        """Write a DDS. Uncompressed ARGB8888 by default (matches vanilla focus icons);
        `compressed=True` uses DXT5 for smaller files with an alpha channel."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = ImageConverter._prepare(img, size, crop, keep_top, inset)
        if compressed:
            out.save(dest, format="DDS", pixel_format="DXT5")
        else:
            out.save(dest, format="DDS")
        return dest

    @staticmethod
    def convert(
        src: str | Path,
        dest: str | Path,
        size: tuple[int, int] | None = None,
        fmt: ImageFormat | None = None,
        compressed: bool = False,
    ) -> Path:
        """Convert a source file to `dest`, inferring the target format from the
        destination suffix unless `fmt` is given."""
        img = ImageConverter.load(src)
        target = fmt or ImageFormat(Path(dest).suffix.lstrip(".").lower())
        if target is ImageFormat.TGA:
            return ImageConverter.save_tga(img, dest, size)
        if target is ImageFormat.DDS:
            return ImageConverter.save_dds(img, dest, size, compressed)
        out = ImageConverter._prepare(img, size)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        out.save(dest)
        return Path(dest)

    @staticmethod
    def _prepare(img: Image.Image, size: tuple[int, int] | None,
                 crop: bool = False, keep_top: float = 1.0,
                 inset: float = 0.0) -> Image.Image:
        out = img.convert("RGBA") if img.mode != "RGBA" else img
        return ImageConverter.fit(out, size, crop, keep_top, inset) if size else out
