"""Static paths and HOI4 layout constants.

`Paths` resolves application resources relative to the project root so the app
works regardless of the current working directory. `GAME_DIRS` describes where,
*inside a mod*, each kind of content lives — used by editors to read/generate files.
"""
from __future__ import annotations

from pathlib import Path

# Project root = parent of the `anka` package directory.
_ROOT = Path(__file__).resolve().parents[2]


class Paths:
    ROOT: Path = _ROOT
    IMAGES: Path = _ROOT / "images"
    LOCALES: Path = _ROOT / "locales"
    SETTINGS_FILE: Path = _ROOT / "settings.json"

    # Standard app images (with graceful fallback handled by the loader).
    LOGO: Path = IMAGES / "New_logo.png"
    LOGO_SMALL: Path = IMAGES / "logo_small.png"
    NO_LOGO: Path = IMAGES / "no_logo.png"


class GAME_DIRS:
    """Relative paths inside a HOI4 mod (or the base game) for each content type."""

    DESCRIPTOR = "descriptor.mod"
    COUNTRY_TAGS = "common/country_tags"
    COUNTRY_TAGS_FILE = "common/country_tags/00_countries.txt"
    COUNTRY_COLORS = "common/countries/colors.txt"
    COUNTRIES = "common/countries"
    IDEOLOGIES = "common/ideologies"
    NATIONAL_FOCUS = "common/national_focus"
    IDEAS = "common/ideas"
    HISTORY_COUNTRIES = "history/countries"
    HISTORY_STATES = "history/states"
    CHARACTERS = "common/characters"
    DECISIONS = "common/decisions"
    DECISION_CATEGORIES = "common/decisions/categories"
    DYNAMIC_MODIFIERS = "common/dynamic_modifiers"
    EVENTS = "events"
    LOCALISATION = "localisation"
    INTERFACE = "interface"
    GFX_FLAGS = "gfx/flags"
    GFX_GOALS = "gfx/interface/goals"        # focus icons
    GFX_IDEAS = "gfx/interface/ideas"
    GFX_EVENTS = "gfx/event_pictures"
    GFX_LEADERS = "gfx/leaders"              # country-leader portraits


# HOI4 flag dimensions (TGA, RGBA). Order matters: (folder-relative path, size).
FLAG_SIZES = {
    "": (82, 52),          # large flag at gfx/flags/<TAG>.tga
    "medium": (41, 26),
    "small": (10, 7),
}

# Focus / idea icon canonical size for generated DDS sprites.
FOCUS_ICON_SIZE = (100, 88)
IDEA_ICON_SIZE = (63, 50)
LEADER_PORTRAIT_SIZE = (156, 210)   # large country-leader portrait

SUPPORTED_IMPORT_FORMATS = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".dds", ".gif", ".webp")

# Localisation languages HOI4 ships with (folder + ``_l_<lang>`` filename suffix).
HOI4_LANGUAGES = (
    "english", "russian", "german", "french", "spanish", "polish",
    "portuguese", "japanese", "simp_chinese", "korean", "braz_por", "turkish",
)
