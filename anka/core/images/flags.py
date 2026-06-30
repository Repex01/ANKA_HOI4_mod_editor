"""Country flag generation.

A HOI4 flag is three TGA files at fixed sizes under ``gfx/flags``:
    gfx/flags/<TAG>.tga           82x52   (large)
    gfx/flags/medium/<TAG>.tga    41x26
    gfx/flags/small/<TAG>.tga     10x7
Cosmetic / ideology variants use ``<TAG>_<suffix>``. The user gives a single source
image in any format and ANKA produces all three sizes.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ...config.constants import FLAG_SIZES, GAME_DIRS
from .converter import ImageConverter


class FlagService:
    """Generates the three TGA flag assets for a country tag inside a mod."""

    def __init__(self, mod_root: str | Path):
        self.mod_root = Path(mod_root)

    def flags_dir(self) -> Path:
        return self.mod_root / GAME_DIRS.GFX_FLAGS

    def asset_name(self, tag: str, suffix: str | None = None) -> str:
        return f"{tag}_{suffix}" if suffix else tag

    def generate(
        self,
        source: str | Path | Image.Image,
        tag: str,
        suffix: str | None = None,
    ) -> list[Path]:
        """Create all three flag sizes; returns the written file paths."""
        img = source if isinstance(source, Image.Image) else ImageConverter.load(source)
        name = self.asset_name(tag, suffix)
        flags = self.flags_dir()
        written: list[Path] = []
        for subfolder, size in FLAG_SIZES.items():
            dest = (flags / subfolder / f"{name}.tga") if subfolder else (flags / f"{name}.tga")
            written.append(ImageConverter.save_tga(img, dest, size))
        return written

    def existing(self, tag: str, suffix: str | None = None) -> Path | None:
        """Return the large flag path if it already exists, else None."""
        dest = self.flags_dir() / f"{self.asset_name(tag, suffix)}.tga"
        return dest if dest.exists() else None
