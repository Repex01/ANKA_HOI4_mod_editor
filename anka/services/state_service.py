"""States: light ownership info for the Countries editor + a block-backed
document model for the Map editor.

A HOI4 *region* is a state defined in ``history/states/<id>-<name>.txt``; ownership is
the ``owner`` key inside its ``history`` block. Assigning a state to a country edits
that key — copying the vanilla file into the mod first if needed, so the base game stays
untouched. The service also reports the current owner so the UI can flag conflicts
(already owned by someone else) without blocking the action.

The map editor works through `StateDocRef` / `StateDocument` / `StateDef`
(the Ref/Document/BlockView pattern of the other editors): every state lives in
its own file, unknown history effects survive load→save untouched. The legacy
`StateInfo` API above stays intact — the Countries editor depends on it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case as _ensure_filename_case
from ._pdxview import BlockView

_STATE_ID_RE = re.compile(r"^[ \t]*id[ \t]*=[ \t]*(\d+)", re.M)


@dataclass
class StateInfo:
    id: int
    name: str
    owner: str | None
    file: Path
    in_mod: bool
    provinces: list[int] = field(default_factory=list)
    cores: list[str] = field(default_factory=list)   # add_core_of tags


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
        for root, in_mod in self.ctx.override_layers(GAME_DIRS.HISTORY_STATES):
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
        cores = ([v.raw for v in history.get_all("add_core_of") if isinstance(v, Scalar)]
                 if history else [])
        prov_block = state.get_block("provinces")
        provinces = [int(v) for v in (prov_block.array_values() if prov_block else []) if v.isdigit()]
        return StateInfo(
            id=sid_int,
            name=names.get(sid_int, f"STATE_{sid_int}"),
            owner=owner,
            file=file,
            in_mod=in_mod,
            provinces=provinces,
            cores=cores,
        )

    def _state_names(self) -> dict[int, str]:
        if self._names is not None:
            return self._names
        from ..core.localisation import LocFile
        names: dict[int, str] = {}
        for root in self.ctx.override_roots(GAME_DIRS.LOCALISATION):
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
    def set_owner(self, state_id: int, tag: str, *,
                  core: bool = True, exclusive_core: bool = False) -> StateInfo:
        """Set a state's owner, copying the vanilla file into the mod if needed.

        `core` also adds ``add_core_of = tag`` (the state becomes national territory
        of the owner); `exclusive_core` additionally strips every other country's
        ``add_core_of`` from the state."""
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
        if core and exclusive_core:
            history.items = [
                it for it in history.items
                if not (isinstance(it, Pair) and it.key == "add_core_of"
                        and isinstance(it.value, Scalar) and it.value.raw != tag)
            ]
        if core:
            cores = [v.raw for v in history.get_all("add_core_of")
                     if isinstance(v, Scalar)]
            if tag not in cores:
                history.add("add_core_of", Scalar(tag))
        target = _ensure_filename_case(target)  # match vanilla casing for override
        dump_file(block, target)

        # Refresh cache entry.
        cores = [v.raw for v in history.get_all("add_core_of") if isinstance(v, Scalar)]
        updated = StateInfo(info.id, info.name, tag, target, True, info.provinces, cores)
        if self._cache is not None:
            self._cache[state_id] = updated
        return updated

    def set_cores(self, state_id: int, tags: list[str]) -> StateInfo:
        """Replace the state's ``add_core_of`` list (mod copy created if needed)."""
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
        # Replace in place: keep the position of the first add_core_of pair.
        index = next((i for i, it in enumerate(history.items)
                      if isinstance(it, Pair) and it.key == "add_core_of"), None)
        history.items = [it for it in history.items
                         if not (isinstance(it, Pair) and it.key == "add_core_of")]
        pairs = [Pair("add_core_of", Scalar(t)) for t in tags]
        if index is None:
            history.items.extend(pairs)
        else:
            history.items[index:index] = pairs
        target = _ensure_filename_case(target)
        dump_file(block, target)

        updated = StateInfo(info.id, info.name, info.owner, target, True,
                            info.provinces, list(tags))
        if self._cache is not None:
            self._cache[state_id] = updated
        return updated

    def owner_conflict(self, state_id: int, tag: str) -> str | None:
        """Return the current owner if it exists and differs from `tag`, else None."""
        info = self.get(state_id)
        if info and info.owner and info.owner != tag:
            return info.owner
        return None

    def invalidate(self) -> None:
        """Drop the `StateInfo` cache (map editor edited state documents)."""
        self._cache = None

    # ================== block-backed documents (map editor) ==================
    def list_docs(self, include_vanilla: bool = True) -> list["StateDocRef"]:
        """Refs of every state file across all layers (base game → dependencies →
        edited mod). A higher layer's file overrides a lower one with the same name
        (case-insensitive); the edited mod's own files are editable, everything below
        (dependencies included) is read-only. Dependency files are always listed (the
        active base); only pure base-game files honour `include_vanilla`."""
        layers = self.ctx.override_layers(GAME_DIRS.HISTORY_STATES)   # low→high
        # Names present strictly below each layer, so an override can be flagged.
        below: dict[Path, set[str]] = {}
        acc: set[str] = set()
        names: dict[Path, set[str]] = {}
        for root, _is_mod in layers:
            d = root / GAME_DIRS.HISTORY_STATES
            names[root] = {f.name.lower() for f in d.glob("*.txt")} if d.is_dir() else set()
            below[root] = set(acc)
            acc |= names[root]

        refs: list[StateDocRef] = []
        seen: set[str] = set()
        for root, is_mod in reversed(layers):                        # high→low
            if root == self.ctx.game_path and not include_vanilla:
                continue
            folder = root / GAME_DIRS.HISTORY_STATES
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                low = file.name.lower()
                if low in seen:                # overridden by a higher layer
                    continue
                seen.add(low)
                rel = f"{GAME_DIRS.HISTORY_STATES}/{file.name}"
                refs.append(StateDocRef(rel_file=rel, source_root=root,
                                        is_vanilla=not is_mod,
                                        edited=is_mod and low in below[root],
                                        state_id=self._quick_state_id(file)))
        return refs

    @staticmethod
    def _quick_state_id(file: Path) -> int | None:
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return None
        m = _STATE_ID_RE.search(text)
        return int(m.group(1)) if m else None

    def doc_ref_for(self, state_id: int) -> "StateDocRef | None":
        for ref in self.list_docs(include_vanilla=True):
            if ref.state_id == state_id:
                return ref
        return None

    def load(self, ref: "StateDocRef") -> "StateDocument":
        """Parse (or reuse the cached parse of) a state file."""
        if not hasattr(self, "_doc_cache"):
            self._doc_cache: dict[str, tuple[float, StateDocument]] = {}
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        root = parse_file(ref.path)
        doc = StateDocument(ref, root)
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save_doc(self, doc: "StateDocument") -> None:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a vanilla state file")
        target = _ensure_filename_case(doc.ref.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        if hasattr(self, "_doc_cache"):
            key = str(doc.ref.path).lower()
            try:
                self._doc_cache[key] = (target.stat().st_mtime, doc)
            except OSError:
                self._doc_cache.pop(key, None)
        self.refresh_info(doc)

    def refresh_info(self, doc: "StateDocument") -> None:
        """Sync the light `StateInfo` cache with an (edited) document — much
        cheaper than `invalidate()`, which would re-parse all 1000+ files."""
        st = doc.state
        if st is None:
            return
        if self._cache is None:
            self.list_states()
        names = self._state_names()
        info = StateInfo(st.id, names.get(st.id, f"STATE_{st.id}"),
                         st.owner or None, doc.ref.path,
                         not doc.ref.is_vanilla, st.provinces, st.cores)
        if self._cache is not None:
            self._cache[st.id] = info

    def copy_to_mod(self, ref: "StateDocRef") -> "StateDocRef":
        """Byte-exact copy of a vanilla state file into the mod (override)."""
        if not ref.is_vanilla:
            return ref
        target = self.ctx.mod.path / ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = _ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return StateDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                           is_vanilla=False, edited=True, state_id=ref.state_id)

    def next_state_id(self) -> int:
        """Smallest free state id above every existing one (across all layers)."""
        ids = [r.state_id for r in self.list_docs(include_vanilla=True)
               if r.state_id is not None]
        return (max(ids) + 1) if ids else 1

    def create_state(self, *, name: str = "", owner: str = "",
                     category: str = "rural",
                     provinces: "list[int] | None" = None) -> "StateDocRef":
        """Create a brand-new, editable state file in the mod and return its ref.

        The id is auto-allocated (`next_state_id`); the localised *name text* is left to
        the caller (it writes ``STATE_<id>`` via the loc catalog). Provinces may be
        seeded now or added later on the map."""
        sid = self.next_state_id()
        root = build_state_root(sid, name, owner, category, provinces or [])
        ref = self.write_state_root(sid, root, name=name)
        if self._cache is not None:                # keep the tree in sync immediately
            self._cache[sid] = StateInfo(sid, name or f"STATE_{sid}", owner or None,
                                         ref.path, True, list(provinces or []),
                                         [owner] if owner else [])
        return ref

    def write_state_root(self, state_id: int, root: "Block", name: str = "",
                         rel_file: "str | None" = None) -> "StateDocRef":
        """Write a state document into the mod. `rel_file` forces an exact relative path
        (used to overwrite an original file at the same name so HOI4 overrides it);
        otherwise the path is ``history/states/<id>-<slug>.txt``."""
        if rel_file is not None:
            rel = rel_file
        else:
            slug = re.sub(r"[^0-9A-Za-z_-]+", "_", name).strip("_") or f"STATE_{state_id}"
            rel = f"{GAME_DIRS.HISTORY_STATES}/{state_id}-{slug}.txt"
        target = _ensure_filename_case(self.ctx.mod.path / rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, target)
        return StateDocRef(rel_file=rel, source_root=self.ctx.mod.path,
                           is_vanilla=False, state_id=state_id)

    def delete_doc(self, ref: "StateDocRef") -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a vanilla state file")
        if ref.path.exists():
            ref.path.unlink()
        if hasattr(self, "_doc_cache"):
            self._doc_cache.pop(str(ref.path).lower(), None)
        if self._cache is not None and ref.state_id is not None:
            self._cache.pop(ref.state_id, None)

    def find_province_state(self, province_id: int,
                            exclude_state: int | None = None) -> "StateDocRef | None":
        """The (other) state currently containing a province — conflict check
        when assigning provinces on the map."""
        for ref in self.list_docs(include_vanilla=True):
            if ref.state_id is None or ref.state_id == exclude_state:
                continue
            doc = self.load(ref)
            state = doc.state
            if state is not None and province_id in state.provinces:
                return ref
        return None


@dataclass
class StateDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    state_id: int | None = None

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class StateDocument:
    def __init__(self, ref: StateDocRef, root: Block):
        self.ref = ref
        self.root = root

    @property
    def state(self) -> "StateDef | None":
        block = self.root.get_block("state")
        return StateDef(block) if block is not None else None


class StateDef(BlockView):
    """Thin view over one ``state = { ... }`` block. The ``history`` block and
    any unknown effects inside it are preserved untouched."""

    FLAG_DEFAULTS = {"impassable": False}

    def __init__(self, block: Block):
        self.block = block

    # --- plain fields -------------------------------------------------------
    @property
    def id(self) -> int:
        v = self.block.get("id")
        return v.as_int(0) if isinstance(v, Scalar) else 0

    @property
    def name_key(self) -> str:
        return self.get_raw("name")

    def set_name_key(self, key: str) -> None:
        self.block.set("name", Scalar(key, quoted=True))

    @property
    def manpower(self) -> int:
        v = self.block.get("manpower")
        return v.as_int(0) if isinstance(v, Scalar) else 0

    def set_manpower(self, value: int) -> None:
        self.block.set("manpower", Scalar(str(int(value))))

    @property
    def state_category(self) -> str:
        return self.get_raw("state_category")

    def set_state_category(self, name: str) -> None:
        self.set_raw("state_category", name)

    @property
    def local_supplies(self) -> float:
        v = self.block.get("local_supplies")
        return (v.as_float(0.0) or 0.0) if isinstance(v, Scalar) else 0.0

    def set_local_supplies(self, value: float) -> None:
        if value:
            self.block.set("local_supplies", Scalar(f"{value:g}"))
        else:
            self.block.remove("local_supplies")

    @property
    def impassable(self) -> bool:
        return self.get_flag("impassable")

    def set_impassable(self, value: bool) -> None:
        self.set_flag("impassable", value)

    # --- provinces ------------------------------------------------------------
    @property
    def provinces(self) -> list[int]:
        block = self.block.get_block("provinces")
        if block is None:
            return []
        # array_values() yields raw strings
        return [int(v) for v in block.array_values() if v.isdigit()]

    def set_provinces(self, ids: list[int]) -> None:
        block = self.block.get_block("provinces")
        if block is None:
            block = Block()
            self.block.set("provinces", block)
        block.items = [Scalar(str(i)) for i in ids]

    def add_province(self, pid: int) -> None:
        ids = self.provinces
        if pid not in ids:
            ids.append(pid)
            self.set_provinces(ids)

    def remove_province(self, pid: int) -> None:
        ids = self.provinces
        if pid in ids:
            ids.remove(pid)
            self.set_provinces(ids)

    # --- history --------------------------------------------------------------
    def _history(self, create: bool = False) -> Block | None:
        h = self.block.get_block("history")
        if h is None and create:
            h = Block()
            self.block.set("history", h)
        return h

    @property
    def owner(self) -> str:
        h = self._history()
        return h.get_scalar("owner", "") if h is not None else ""

    def set_owner(self, tag: str) -> None:
        h = self._history(create=True)
        if tag:
            h.set("owner", Scalar(tag))
        else:
            h.remove("owner")

    @property
    def controller(self) -> str:
        h = self._history()
        return h.get_scalar("controller", "") if h is not None else ""

    def set_controller(self, tag: str) -> None:
        h = self._history(create=True)
        if tag:
            h.set("controller", Scalar(tag))
        else:
            h.remove("controller")

    @property
    def cores(self) -> list[str]:
        h = self._history()
        if h is None:
            return []
        return [v.raw for v in h.get_all("add_core_of") if isinstance(v, Scalar)]

    def set_cores(self, tags: list[str]) -> None:
        """Replace ``add_core_of`` pairs keeping the first one's position."""
        h = self._history(create=True)
        index = next((i for i, it in enumerate(h.items)
                      if isinstance(it, Pair) and it.key == "add_core_of"), None)
        h.items = [it for it in h.items
                   if not (isinstance(it, Pair) and it.key == "add_core_of")]
        pairs = [Pair("add_core_of", Scalar(t)) for t in tags]
        if index is None:
            h.items.extend(pairs)
        else:
            h.items[index:index] = pairs

    # --- victory points ---------------------------------------------------------
    @property
    def victory_points(self) -> list[tuple[int, int]]:
        h = self._history()
        if h is None:
            return []
        out: list[tuple[int, int]] = []
        for v in h.get_all("victory_points"):
            if isinstance(v, Block):
                nums = [int(s) for s in v.array_values()
                        if s.lstrip("-").isdigit()]
                for i in range(0, len(nums) - 1, 2):
                    out.append((nums[i], nums[i + 1]))
        return out

    def set_victory_points(self, points: list[tuple[int, int]]) -> None:
        h = self._history(create=True)
        index = next((i for i, it in enumerate(h.items)
                      if isinstance(it, Pair) and it.key == "victory_points"), None)
        h.items = [it for it in h.items
                   if not (isinstance(it, Pair) and it.key == "victory_points")]
        pairs = [Pair("victory_points",
                      Block([Scalar(str(p)), Scalar(str(v))]))
                 for p, v in points]
        if index is None:
            h.items.extend(pairs)
        else:
            h.items[index:index] = pairs

    # --- resources ---------------------------------------------------------------
    @property
    def resources(self) -> dict[str, float]:
        block = self.block.get_block("resources")
        if block is None:
            return {}
        out: dict[str, float] = {}
        for pair in block.pairs():
            if isinstance(pair.value, Scalar):
                val = pair.value.as_float()
                if val is not None:
                    out[pair.key] = val
        return out

    def set_resource(self, name: str, amount: float) -> None:
        block = self.block.get_block("resources")
        if amount <= 0:
            if block is not None:
                block.remove(name)
                if not len(block.items):
                    self.block.remove("resources")
            return
        if block is None:
            block = Block()
            self.block.set("resources", block)
        text = f"{amount:g}"
        block.set(name, Scalar(text))

    # --- buildings ------------------------------------------------------------------
    def _buildings(self, create: bool = False) -> Block | None:
        h = self._history(create=create)
        if h is None:
            return None
        b = h.get_block("buildings")
        if b is None and create:
            b = Block()
            h.set("buildings", b)
        return b

    @property
    def state_buildings(self) -> dict[str, int]:
        b = self._buildings()
        if b is None:
            return {}
        out: dict[str, int] = {}
        for pair in b.pairs():
            if isinstance(pair.value, Scalar) and not pair.key.isdigit():
                lvl = pair.value.as_int()
                if lvl is not None:
                    out[pair.key] = lvl
        return out

    def set_state_building(self, name: str, level: int) -> None:
        b = self._buildings(create=level > 0)
        if b is None:
            return
        if level > 0:
            b.set(name, Scalar(str(int(level))))
        else:
            b.remove(name)

    @property
    def province_buildings(self) -> dict[int, dict[str, int]]:
        b = self._buildings()
        if b is None:
            return {}
        out: dict[int, dict[str, int]] = {}
        for pair in b.pairs():
            if isinstance(pair.value, Block) and pair.key.isdigit():
                inner: dict[str, int] = {}
                for p2 in pair.value.pairs():
                    if isinstance(p2.value, Scalar):
                        lvl = p2.value.as_int()
                        if lvl is not None:
                            inner[p2.key] = lvl
                out[int(pair.key)] = inner
        return out

    def set_province_building(self, province: int, name: str, level: int) -> None:
        b = self._buildings(create=level > 0)
        if b is None:
            return
        key = str(province)
        prov_block = None
        for pair in b.pairs():
            if pair.key == key and isinstance(pair.value, Block):
                prov_block = pair.value
                break
        if level > 0:
            if prov_block is None:
                prov_block = Block()
                b.add(key, prov_block)
            prov_block.set(name, Scalar(str(int(level))))
        elif prov_block is not None:
            prov_block.remove(name)
            if not len(prov_block.items):
                b.items = [it for it in b.items
                           if not (isinstance(it, Pair) and it.key == key
                                   and it.value is prov_block)]


def build_state_root(state_id: int, name: str, owner: str, category: str,
                     provinces: "list[int]") -> Block:
    """Build a minimal ``state = { ... }`` document Block (id, name, manpower 0,
    category, history owner/core, provinces). Shared by create_state and generation."""
    state = Block()
    state.add("id", Scalar(str(state_id)))
    state.add("name", Scalar(name or f"STATE_{state_id}", quoted=True))
    state.add("manpower", Scalar("0"))
    if category:
        state.add("state_category", Scalar(category))
    history = Block()
    if owner:
        history.set("owner", Scalar(owner))
        history.add("add_core_of", Scalar(owner))
    state.add("history", history)
    state.add("provinces", Block([Scalar(str(p)) for p in provinces]))
    root = Block()
    root.add("state", state)
    return root


# State-level industry buildings split proportionally when carving out a new state
# (population = manpower, industry = these factories, plus every resource).
INDUSTRY_BUILDINGS = ("industrial_complex", "arms_factory", "dockyard")


def split_state_assets(src: "StateDef", dst: "StateDef", taken: int, total: int) -> None:
    """Move a proportional share (``taken/total`` of the source, floored) of manpower,
    resources and industry from `src` into `dst`. The source keeps the remainder, so it
    effectively rounds *up* — matching how carving N provinces out of a state works.

    Infrastructure is the exception: it is a state-wide quality level, not a stockpile,
    so it is *copied* to the new state (kept identical to the source) rather than split.
    """
    if total <= 0 or taken <= 0:
        return
    mp = src.manpower
    move = mp * taken // total
    if move:
        src.set_manpower(mp - move)
        dst.set_manpower(dst.manpower + move)
    for name, amount in list(src.resources.items()):
        amt = int(amount)
        m = amt * taken // total
        if m:
            dst.set_resource(name, int(dst.resources.get(name, 0)) + m)
            src.set_resource(name, amt - m)
    src_b = src.state_buildings
    for building in INDUSTRY_BUILDINGS:
        lvl = int(src_b.get(building, 0))
        m = lvl * taken // total
        if m:
            dst.set_state_building(building,
                                   int(dst.state_buildings.get(building, 0)) + m)
            src.set_state_building(building, lvl - m)
    # Copy infrastructure unchanged (source keeps its level; new state matches it).
    infra = int(src_b.get("infrastructure", 0))
    if infra:
        dst.set_state_building("infrastructure",
                               max(infra, int(dst.state_buildings.get("infrastructure", 0))))


def transfer_victory_points(src: "StateDef", dst: "StateDef", pids: set[int]) -> None:
    """Move every victory point whose province is in `pids` from `src` to `dst`."""
    move = [(p, v) for p, v in src.victory_points if p in pids]
    if move:
        src.set_victory_points([(p, v) for p, v in src.victory_points if p not in pids])
        dst.set_victory_points(dst.victory_points + move)
