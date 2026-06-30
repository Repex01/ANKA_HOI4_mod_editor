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


class ImageConverter:
    """Stateless conversion helpers. Each method is independently usable."""

    @staticmethod
    def load(path: str | Path) -> Image.Image:
        """Open any supported image and normalize it to RGBA."""
        img = Image.open(path)
        img.load()
        return img.convert("RGBA") if img.mode != "RGBA" else img

    @staticmethod
    def fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
        """Resize to an exact size (HOI4 sprites are fixed-dimension, not aspect-fit)."""
        return img if img.size == size else img.resize(size, _RESAMPLE)

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
    ) -> Path:
        """Write a DDS. Uncompressed ARGB8888 by default (matches vanilla focus icons);
        `compressed=True` uses DXT5 for smaller files with an alpha channel."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = ImageConverter._prepare(img, size)
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
    def _prepare(img: Image.Image, size: tuple[int, int] | None) -> Image.Image:
        out = img.convert("RGBA") if img.mode != "RGBA" else img
        return ImageConverter.fit(out, size) if size else out
