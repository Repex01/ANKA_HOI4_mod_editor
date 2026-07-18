"""Application settings: typed model + JSON persistence (settings.json).

The dataclass is the single source of truth for user configuration; `SettingsService`
owns load/save and notifies listeners so UI (theme/language) can react to changes.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

from .constants import Paths

# Path-layout rules (backslashes, Steam/Documents folder shapes) are Windows
# conventions; on Linux/macOS paths are left exactly as the user typed them.
_IS_WINDOWS = os.name == "nt"


@dataclass
class Settings:
    game_path: str = ""             # Hearts of Iron IV install root
    local_mods_path: str = ""       # Documents/.../mod
    workshop_mods_path: str = ""    # steamapps/workshop/content/394360
    language: str = "en"            # UI language code (matches locales/<code>.json)
    theme: str = "dark"             # "dark" | "light"
    recent_mods: list[str] = field(default_factory=list)
    # AI integrations (reserved for future features). Stored as plain text in
    # settings.json — keep that file out of any shared repository.
    openai_api_key: str = ""
    claude_api_key: str = ""

    @property
    def paths_configured(self) -> bool:
        """Editing requires at least the game path and one mod source."""
        has_mod_source = bool(self.local_mods_path) or bool(self.workshop_mods_path)
        return bool(self.game_path) and has_mod_source

    def validate_paths(self) -> dict[str, bool]:
        """Per-path existence check for the settings UI."""
        return {
            "game_path": bool(self.game_path) and Path(self.game_path).is_dir(),
            "local_mods_path": bool(self.local_mods_path) and Path(self.local_mods_path).is_dir(),
            "workshop_mods_path": bool(self.workshop_mods_path) and Path(self.workshop_mods_path).is_dir(),
        }

    @staticmethod
    def normalize_separators(raw: str) -> str:
        """Windows: paths are kept backslashed (launcher convention).
        Other platforms: returned untouched — `/` IS the separator there."""
        return raw.replace("/", "\\") if _IS_WINDOWS else raw

    @staticmethod
    def path_format_issue(key: str, raw: str) -> str | None:
        """Layout sanity check for the three content paths — users regularly
        point fields at the wrong folder. Returns a locale suffix
        (``settings.err.<suffix>``) or None when the path looks right.
        Backslash separators are required (Windows/launcher convention).

        WINDOWS-ONLY: on Linux/macOS Steam/Paradox folder layouts differ
        (~/.steam, ~/.local/share/Paradox Interactive) and `/` is the real
        separator, so no layout check is performed there.

        Expected layouts:
        * game:      …\\SteamLibrary\\steamapps\\common\\Hearts of Iron IV
                     (never inside Documents; ``Steam\\steamapps\\…`` — the
                     default library — passes too)
        * local mods: …\\Documents\\Paradox Interactive\\Hearts of Iron IV\\mod
        * workshop:  …\\steamapps\\workshop\\content\\394360 — 394360 last
        """
        if not _IS_WINDOWS or not raw:
            return None
        if "/" in raw:
            return "slashes"
        norm = raw.rstrip("\\").lower()
        if key == "game_path":
            if "documents" in norm:
                return "game_documents"
            if "steamapps\\common\\hearts of iron iv" not in norm:
                return "game_path"
        elif key == "local_mods_path":
            if "documents\\paradox interactive\\hearts of iron iv\\mod" not in norm:
                return "local_mods"
        elif key == "workshop_mods_path":
            if "steamapps\\workshop\\content\\394360" not in norm \
                    or not norm.endswith("394360"):
                return "workshop"
        return None


class SettingsService:
    """Loads/saves `Settings` and broadcasts changes to subscribers."""

    def __init__(self, path: Path | None = None):
        self._path = path or Paths.SETTINGS_FILE
        self._first_run = not self._path.exists()   # no settings.json yet
        self._settings = self._load()
        self._listeners: list[Callable[[Settings], None]] = []

    @property
    def current(self) -> Settings:
        return self._settings

    @property
    def is_first_run(self) -> bool:
        """True when the app started without a settings.json — prompt for paths."""
        return self._first_run

    def _load(self) -> Settings:
        if not self._path.exists():
            return Settings()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return Settings()
        known = {f.name for f in fields(Settings)}
        loaded = Settings(**{k: v for k, v in raw.items() if k in known})
        # Migration (Windows only): settings saved by older ANKA versions kept
        # forward slashes; paths are backslashed now (launcher convention).
        if _IS_WINDOWS:
            for key in ("game_path", "local_mods_path", "workshop_mods_path"):
                value = getattr(loaded, key)
                if "/" in value:
                    setattr(loaded, key, value.replace("/", "\\"))
        return loaded

    def save(self, settings: Settings | None = None) -> None:
        if settings is not None:
            self._settings = settings
        self._path.write_text(
            json.dumps(asdict(self._settings), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._notify()

    def update(self, **changes) -> Settings:
        for key, value in changes.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        self.save()
        return self._settings

    def add_recent_mod(self, mod_id: str) -> None:
        recent = self._settings.recent_mods
        if mod_id in recent:
            recent.remove(mod_id)
        recent.insert(0, mod_id)
        del recent[10:]
        self.save()

    # --- observer pattern: theme/language changes propagate to the UI ---
    # Today the only subscriber is the AnkaApp instance, which lives as long as the
    # process — so a missing unsubscribe cannot leak. Anything shorter-lived
    # (screens, editors) MUST pair subscribe with unsubscribe, or the listener list
    # would keep destroyed widgets (and their Tk trees) alive and _notify would
    # touch dead widgets.
    def subscribe(self, listener: Callable[[Settings], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[Settings], None]) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self._settings)
