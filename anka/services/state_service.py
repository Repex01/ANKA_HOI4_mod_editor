"""State (region) ownership for the Countries editor.

A HOI4 *region* is a state defined in ``history/states/<id>-<name>.txt``; ownership is
the ``owner`` key inside its ``history`` block. Assigning a state to a country edits
that key — copying the vanilla file into the mod first if needed, so the base game stays
untouched. The service also reports the current owner so the UI can flag conflicts
(already owned by someone else) without blocking the action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ..domain.mod import ModContext


@dataclass
class StateInfo:
    id: int
    name: str
    owner: str | None
    file: Path
    in_mod: bool
    provinces: list[int] = field(default_factory=list)


class StateService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._cache: dict[int, StateInfo] | None = None
        self._names: dict[int, str] | None = None

    # --- listing ---------------------------------------------------------
    def list_states(self, refresh: bool = False) -> list[StateInfo]:
        if self._cache is None or refresh:
            self._cache = self._load_all()
        return sorted(self._cache.values(), key=lambda s: s.id)

    def get(self, state_id: int) -> StateInfo | None:
        if self._cache is None:
            self.list_states()
        return self._cache.get(state_id) if self._cache else None

    def _load_all(self) -> dict[int, StateInfo]:
        names = self._state_names()
        states: dict[int, StateInfo] = {}
        # Game first, mod second so mod overrides win.
        for root, in_mod in ((self.ctx.game_path, False), (self.ctx.mod.path, True)):
            folder = root / GAME_DIRS.HISTORY_STATES
            if not folder.is_dir():
                continue
            for file in folder.glob("*.txt"):
                info = self._parse_state(file, in_mod, names)
                if info is not None:
                    states[info.id] = info
        return states

    def _parse_state(self, file: Path, in_mod: bool, names: dict[int, str]) -> StateInfo | None:
        try:
            block = parse_file(file)
        except Exception:
            return None
        state = block.get_block("state")
        if state is None:
            return None
        sid = state.get_scalar("id")
        if sid is None:
            return None
        try:
            sid_int = int(sid)
        except ValueError:
            return None
        history = state.get_block("history")
        owner = history.get_scalar("owner") if history else None
        prov_block = state.get_block("provinces")
        provinces = [int(v) for v in (prov_block.array_values() if prov_block else []) if v.isdigit()]
        return StateInfo(
            id=sid_int,
            name=names.get(sid_int, f"STATE_{sid_int}"),
            owner=owner,
            file=file,
            in_mod=in_mod,
            provinces=provinces,
        )

    def _state_names(self) -> dict[int, str]:
        if self._names is not None:
            return self._names
        from ..core.localisation import LocFile
        names: dict[int, str] = {}
        for root in (self.ctx.game_path, self.ctx.mod.path):
            loc_dir = root / GAME_DIRS.LOCALISATION
            if not loc_dir.is_dir():
                continue
            for yml in loc_dir.glob("**/*state_names*l_english.yml"):
                try:
                    loc = LocFile.load(yml)
                except Exception:
                    continue
                for entry in loc.entries():
                    if entry.key.startswith("STATE_"):
                        suffix = entry.key[len("STATE_"):]
                        if suffix.isdigit():
                            names[int(suffix)] = entry.value
        self._names = names
        return names

    # --- mutation --------------------------------------------------------
    def set_owner(self, state_id: int, tag: str) -> StateInfo:
        """Set a state's owner, copying the vanilla file into the mod if needed."""
        info = self.get(state_id)
        if info is None:
            raise KeyError(f"State {state_id} not found")
        target = info.file
        if not info.in_mod:
            target = self.ctx.mod.path / GAME_DIRS.HISTORY_STATES / info.file.name
            target.parent.mkdir(parents=True, exist_ok=True)

        block = parse_file(info.file)
        state = block.get_block("state")
        history = state.get_block("history")
        if history is None:
            history = Block()
            state.items.insert(0, Pair("history", history))
        history.set("owner", Scalar(tag))
        if not history.has("add_core_of"):
            history.add("add_core_of", Scalar(tag))
        dump_file(block, target)

        # Refresh cache entry.
        updated = StateInfo(info.id, info.name, tag, target, True, info.provinces)
        if self._cache is not None:
            self._cache[state_id] = updated
        return updated

    def owner_conflict(self, state_id: int, tag: str) -> str | None:
        """Return the current owner if it exists and differs from `tag`, else None."""
        info = self.get(state_id)
        if info and info.owner and info.owner != tag:
            return info.owner
        return None
