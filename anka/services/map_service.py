"""Province map model: ``provinces.bmp`` + ``definition.csv`` (mod-first).

Working data is numpy end-to-end — the map is 5632×2048 (11.5M pixels), so any
per-pixel Python loop is banned. Pixels are kept as ``codes: uint32[H, W]``
(``r<<16 | g<<8 | b``) plus a dense province index ``inv: int32[H, W]``
(pixel → position in ``_ucodes``), which makes every render mode a small
"dense index → RGB" LUT applied with one ``np.take``.

``definition.csv`` is loaded line-preserving: untouched rows are written back
byte-identical (vanilla line endings included); only edited/added rows are
re-serialized. Saving always targets the mod (copy-on-write override of the
whole file — that is how map files override in HOI4).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from .mapgen_colors import iter_colors, load_pool

MAP_DIR = "map"

# Fallback colors for the render LUTs.
_ORPHAN_RGB = (255, 0, 220)        # bmp color with no definition.csv row
_SEA_RGB = (38, 57, 87)            # sea/lake in owner & state modes
_NEUTRAL_RGB = (150, 150, 150)     # land without owner
_NO_STATE_RGB = (90, 90, 90)       # land not in any state (state mode)

RENDER_MODES = ("provinces", "terrain", "owner", "state")


def encode_rgb(r: int, g: int, b: int) -> int:
    return (r << 16) | (g << 8) | b


def decode_rgb(code: int) -> tuple[int, int, int]:
    return (code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF


def _flood_region(mask: np.ndarray, sy: int, sx: int) -> np.ndarray:
    """Connected component of True cells containing (sy, sx) — scanline stack
    fill; segment extents are found with numpy, not per-pixel Python steps."""
    h, w = mask.shape
    region = np.zeros_like(mask)
    if not (0 <= sy < h and 0 <= sx < w) or not mask[sy, sx]:
        return region
    stack = [(sy, sx)]
    while stack:
        y, x = stack.pop()
        if region[y, x] or not mask[y, x]:
            continue
        blocked = ~mask[y]
        left = np.nonzero(blocked[:x + 1])[0]
        x0 = int(left[-1]) + 1 if len(left) else 0
        right = np.nonzero(blocked[x:])[0]
        x1 = x + int(right[0]) - 1 if len(right) else w - 1
        region[y, x0:x1 + 1] = True
        for ny in (y - 1, y + 1):
            if 0 <= ny < h:
                candidates = mask[ny, x0:x1 + 1] & ~region[ny, x0:x1 + 1]
                if candidates.any():
                    xs = np.nonzero(candidates)[0]
                    run_starts = xs[np.r_[True, np.diff(xs) > 1]]
                    for s in run_starts:
                        stack.append((ny, x0 + int(s)))
    return region


def state_color(state_id: int) -> tuple[int, int, int]:
    """Deterministic, well-spread color for a state id (state render mode)."""
    import colorsys
    h = (state_id * 0.6180339887498949) % 1.0
    s = 0.42 + (state_id * 7 % 5) * 0.09
    v = 0.62 + (state_id * 13 % 4) * 0.09
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


@dataclass
class SplitResult:
    """Outcome of `apply_split_area`, per cluster label (index = label - 1)."""

    ids: list[int]                        # province id of each cluster
    donors: list[int]                     # majority source province per cluster
    new_defs: list["ProvinceDef"]         # freshly created definitions
    deltas: list[tuple]                   # (ys, xs, old_codes, new_code) chunks
    orphaned: list[int]                   # sources left with 0 pixels, not reused


@dataclass
class ProvinceDef:
    id: int
    r: int
    g: int
    b: int
    type: str                      # "land" | "sea" | "lake"
    coastal: bool
    terrain: str
    continent: int
    line_idx: int = -1             # row in the csv line list
    dirty: bool = False            # row must be re-serialized on save

    @property
    def color(self) -> tuple[int, int, int]:
        return self.r, self.g, self.b

    @property
    def code(self) -> int:
        return encode_rgb(self.r, self.g, self.b)

    def csv_row(self) -> str:
        coastal = "true" if self.coastal else "false"
        return (f"{self.id};{self.r};{self.g};{self.b};{self.type};"
                f"{coastal};{self.terrain};{self.continent}")


class MapService:
    """Read/render/mutate the province map of `context`'s mod (vanilla fallback)."""

    def __init__(self, context: ModContext, *, state_service=None,
                 terrain_service=None):
        self.ctx = context
        self._state_service = state_service
        self._terrain_service = terrain_service

        self._filenames: dict[str, str] | None = None
        self._defs: list[ProvinceDef] | None = None
        self.by_id: dict[int, ProvinceDef] = {}
        self.by_code: dict[int, ProvinceDef] = {}
        self._csv_lines: list[str] = []
        self._csv_newline = "\r\n"
        self._csv_trailing_newline = True
        self._csv_source: Path | None = None

        self._codes: np.ndarray | None = None      # uint32 [H, W]
        self._inv: np.ndarray | None = None        # int32  [H, W] dense index
        self._ucodes: list[int] = []                # dense index -> code
        self._uidx: dict[int, int] = {}             # code -> dense index
        self._bmp_source: Path | None = None

        self._luts: dict[str, np.ndarray] = {}      # mode -> uint8 [n, 3]
        self._bboxes: dict[int, tuple[int, int, int, int] | None] = {}
        self._political: tuple[dict[int, int], dict[int, str]] | None = None
        self._tag_colors: dict[str, tuple[int, int, int]] | None = None

        self.dirty_bmp = False
        self.dirty_csv = False

    # ------------------------------------------------------------- file layout
    def map_filenames(self) -> dict[str, str]:
        """File names declared in ``default.map`` (definitions, provinces, ...)."""
        if self._filenames is None:
            names = {"definitions": "definition.csv", "provinces": "provinces.bmp",
                     "adjacencies": "adjacencies.csv", "continent": "continent.txt",
                     "adjacency_rules": "adjacency_rules.txt"}
            path = self.map_file("default.map")
            if path is not None:
                from ..core.pdx import Scalar, parse_file
                try:
                    block = parse_file(path)
                    for pair in block.pairs():
                        if isinstance(pair.value, Scalar) and pair.value.raw:
                            names[pair.key] = pair.value.raw
                except Exception:
                    pass
            self._filenames = names
        return self._filenames

    def map_file(self, name: str) -> Path | None:
        """Resolve ``map/<name>`` mod-first, then the base game."""
        for root in self.ctx.search_roots(MAP_DIR):
            candidate = root / MAP_DIR / name
            if candidate.is_file():
                return candidate
        return None

    def mod_map_path(self, name: str) -> Path:
        target = self.ctx.mod.path / MAP_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return ensure_filename_case(target)

    # ------------------------------------------------------------- definitions
    @property
    def defs(self) -> list[ProvinceDef]:
        if self._defs is None:
            self._load_definitions()
        return self._defs

    def _load_definitions(self) -> None:
        path = self.map_file(self.map_filenames()["definitions"])
        if path is None:
            raise FileNotFoundError("definition.csv not found in mod or game")
        raw = path.read_bytes()
        self._csv_source = path
        self._csv_newline = "\r\n" if b"\r\n" in raw[:4096] else "\n"
        self._csv_trailing_newline = raw.endswith((b"\n", b"\r"))
        text = raw.decode("utf-8", errors="replace")
        self._csv_lines = text.splitlines()
        defs: list[ProvinceDef] = []
        self.by_id = {}
        self.by_code = {}
        for idx, line in enumerate(self._csv_lines):
            parts = line.split(";")
            if len(parts) < 8:
                continue
            try:
                pid = int(parts[0])
                r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
                continent = int(parts[7])
            except ValueError:
                continue
            d = ProvinceDef(id=pid, r=r, g=g, b=b, type=parts[4],
                            coastal=parts[5].strip().lower() == "true",
                            terrain=parts[6], continent=continent, line_idx=idx)
            defs.append(d)
            self.by_id[pid] = d
            if pid != 0:                     # province 0 shares (0,0,0) with "no province"
                self.by_code[d.code] = d
        self._defs = defs

    def set_def(self, pid: int, **fields) -> ProvinceDef:
        """Edit definition fields of a province (type/coastal/terrain/continent)."""
        self.defs                                 # ensure loaded
        d = self.by_id[pid]
        for key, value in fields.items():
            if not hasattr(d, key):
                raise AttributeError(f"ProvinceDef has no field {key!r}")
            setattr(d, key, value)
        d.dirty = True
        self.dirty_csv = True
        self._luts.clear()                    # terrain/type feed the render LUTs
        return d

    def continents(self) -> list[str]:
        """Continent names from ``continent.txt`` (1-based ids in definition.csv)."""
        path = self.map_file(self.map_filenames().get("continent", "continent.txt"))
        if path is None:
            return []
        from ..core.pdx import Scalar, parse_file
        try:
            block = parse_file(path)
        except Exception:
            return []
        cont = block.get_block("continents")
        if cont is None:
            return []
        return list(cont.array_values())      # raw strings already

    # ------------------------------------------------------------------ bitmap
    def ensure_bitmap(self) -> None:
        """Load ``provinces.bmp`` into `codes` and build the dense index (heavy:
        ~1–2 s on the vanilla map; call from a worker thread)."""
        if self._codes is not None:
            return
        self.defs                                   # definitions feed by_code
        path = self.map_file(self.map_filenames()["provinces"])
        if path is None:
            raise FileNotFoundError("provinces.bmp not found in mod or game")
        self._bmp_source = path
        with Image.open(path) as im:
            rgb = np.asarray(im.convert("RGB"), dtype=np.uint32)
        codes = (rgb[:, :, 0] << 16) | (rgb[:, :, 1] << 8) | rgb[:, :, 2]
        codes = np.ascontiguousarray(codes, dtype=np.uint32)
        uniq, inv = np.unique(codes.ravel(), return_inverse=True)
        self._codes = codes
        self._inv = inv.astype(np.int32).reshape(codes.shape)
        self._ucodes = [int(c) for c in uniq]
        self._uidx = {c: i for i, c in enumerate(self._ucodes)}
        self._luts.clear()
        self._bboxes.clear()

    @property
    def loaded(self) -> bool:
        return self._codes is not None

    @property
    def size(self) -> tuple[int, int]:
        """(width, height) of the map."""
        self.ensure_bitmap()
        h, w = self._codes.shape
        return w, h

    @property
    def codes(self) -> np.ndarray:
        self.ensure_bitmap()
        return self._codes

    def province_at(self, x: int, y: int) -> int | None:
        self.ensure_bitmap()
        h, w = self._codes.shape
        if not (0 <= x < w and 0 <= y < h):
            return None
        d = self.by_code.get(int(self._codes[y, x]))
        return d.id if d is not None else None

    def mask_of(self, pid: int) -> np.ndarray | None:
        """Full-size boolean mask of a province (None if it has no pixels)."""
        self.ensure_bitmap()
        d = self.by_id.get(pid)
        if d is None:
            return None
        idx = self._uidx.get(d.code)
        if idx is None:
            return None
        return self._inv == idx

    def bbox_of(self, pid: int) -> tuple[int, int, int, int] | None:
        """(x0, y0, x1, y1) inclusive bounds of a province; cached."""
        if pid in self._bboxes:
            return self._bboxes[pid]
        mask = self.mask_of(pid)
        bbox = None
        if mask is not None:
            rows = np.any(mask, axis=1)
            cols = np.any(mask, axis=0)
            if rows.any():
                y0, y1 = np.argmax(rows), len(rows) - 1 - np.argmax(rows[::-1])
                x0, x1 = np.argmax(cols), len(cols) - 1 - np.argmax(cols[::-1])
                bbox = (int(x0), int(y0), int(x1), int(y1))
        self._bboxes[pid] = bbox
        return bbox

    def area_of(self, pid: int) -> int:
        """Pixel count of a province."""
        self.ensure_bitmap()
        d = self.by_id.get(pid)
        idx = self._uidx.get(d.code) if d is not None else None
        if idx is None:
            return 0
        return int(np.count_nonzero(self._inv == idx))

    def neighbors_of(self, pid: int) -> list[int]:
        """Province ids pixel-adjacent to `pid` (4-connectivity)."""
        self.ensure_bitmap()
        bbox = self.bbox_of(pid)
        d = self.by_id.get(pid)
        if bbox is None or d is None:
            return []
        x0, y0, x1, y1 = bbox
        h, w = self._codes.shape
        cx0, cy0 = max(0, x0 - 1), max(0, y0 - 1)
        cx1, cy1 = min(w, x1 + 2), min(h, y1 + 2)
        crop = self._codes[cy0:cy1, cx0:cx1]
        mask = crop == d.code
        neighbor = np.zeros_like(mask)
        neighbor[1:, :] |= mask[:-1, :]
        neighbor[:-1, :] |= mask[1:, :]
        neighbor[:, 1:] |= mask[:, :-1]
        neighbor[:, :-1] |= mask[:, 1:]
        neighbor &= ~mask
        out = []
        for code in np.unique(crop[neighbor]):
            nd = self.by_code.get(int(code))
            if nd is not None and nd.id != pid:
                out.append(nd.id)
        return sorted(out)

    # ---------------------------------------------------------------- political
    def _political_maps(self) -> tuple[dict[int, int], dict[int, str]]:
        """(province → state id, state id → owner tag) from history/states."""
        if self._political is None:
            if self._state_service is None:
                from .state_service import StateService
                self._state_service = StateService(self.ctx)
            prov_state: dict[int, int] = {}
            state_owner: dict[int, str] = {}
            for st in self._state_service.list_states():
                state_owner[st.id] = st.owner or ""
                for p in st.provinces:
                    prov_state[p] = st.id
            self._political = (prov_state, state_owner)
        return self._political

    def _country_colors(self) -> dict[str, tuple[int, int, int]]:
        """tag → map color. ``colors.txt`` is parsed once (mod copy *replaces*
        vanilla wholesale); tags missing there fall back to the ``color`` block
        of their own definition file. `CountryService.get_color` would re-parse
        colors.txt per tag — far too slow for a full-map LUT."""
        if self._tag_colors is None:
            from .country_service import CountryService
            from ..core.pdx import Block, parse_file
            from ..config.constants import GAME_DIRS
            svc = CountryService(self.ctx)
            colors: dict[str, tuple[int, int, int]] = {}
            # colors.txt across every layer, lowest→highest priority so a higher
            # layer (dependency, then the edited mod) overrides a tag's color.
            for root in self.ctx.override_roots(GAME_DIRS.COUNTRY_COLORS):
                block = svc._colors_block(root)
                if block is None:
                    continue
                for pair in block.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    rgb = (svc._color_from(pair.value.get_block("color"))
                           or svc._color_from(pair.value.get_block("color_ui")))
                    if rgb is not None:
                        colors[pair.key] = rgb
            for ref in svc.list_tags(include_vanilla=True):
                if ref.tag in colors:
                    continue
                try:
                    cblock = parse_file(ref.country_file)
                    rgb = svc._color_from(cblock.get_block("color"))
                except Exception:
                    rgb = None
                if rgb is not None:
                    colors[ref.tag] = rgb
            self._tag_colors = colors
        return self._tag_colors

    def invalidate_political(self) -> None:
        """State ownership / country colors changed → owner & state LUTs stale."""
        self._political = None
        self._tag_colors = None
        self._luts.pop("owner", None)
        self._luts.pop("state", None)

    # ------------------------------------------------------------------ render
    def _terrain_colors(self) -> dict[str, tuple[int, int, int]]:
        if self._terrain_service is None:
            from .terrain_service import TerrainService
            self._terrain_service = TerrainService(self.ctx)
        return {name: cat.color
                for name, cat in self._terrain_service.categories().items()}

    def _lut(self, mode: str) -> np.ndarray:
        lut = self._luts.get(mode)
        if lut is not None and len(lut) == len(self._ucodes):
            return lut
        n = len(self._ucodes)
        lut = np.zeros((n, 3), dtype=np.uint8)
        if mode == "provinces":
            arr = np.array(self._ucodes, dtype=np.uint32)
            lut[:, 0] = (arr >> 16) & 0xFF
            lut[:, 1] = (arr >> 8) & 0xFF
            lut[:, 2] = arr & 0xFF
        elif mode == "terrain":
            tcolors = self._terrain_colors()
            for i, code in enumerate(self._ucodes):
                d = self.by_code.get(code)
                lut[i] = (_ORPHAN_RGB if d is None
                          else tcolors.get(d.terrain, (128, 128, 128)))
        elif mode == "owner":
            prov_state, state_owner = self._political_maps()
            tag_colors = self._country_colors()
            for i, code in enumerate(self._ucodes):
                d = self.by_code.get(code)
                if d is None:
                    lut[i] = _ORPHAN_RGB
                elif d.type != "land":
                    lut[i] = _SEA_RGB
                else:
                    tag = state_owner.get(prov_state.get(d.id, -1), "")
                    lut[i] = tag_colors.get(tag, _NEUTRAL_RGB)
        elif mode == "state":
            prov_state, _ = self._political_maps()
            for i, code in enumerate(self._ucodes):
                d = self.by_code.get(code)
                if d is None:
                    lut[i] = _ORPHAN_RGB
                elif d.type != "land":
                    lut[i] = _SEA_RGB
                else:
                    sid = prov_state.get(d.id)
                    lut[i] = state_color(sid) if sid is not None else _NO_STATE_RGB
        else:
            raise ValueError(f"Unknown render mode {mode!r}")
        self._luts[mode] = lut
        return lut

    def render_view(self, mode: str, rect: tuple[int, int, int, int] | None = None,
                    scale: float = 1.0,
                    selected: set[int] | None = None,
                    highlight: set[int] | None = None) -> Image.Image:
        """Render a map rectangle: `rect` = (x0, y0, x1, y1) exclusive on the
        right/bottom (None = whole map), scaled by `scale` (NEAREST).
        `selected` provinces are brightened; `highlight` gets a lighter tint."""
        self.ensure_bitmap()
        h, w = self._codes.shape
        if rect is None:
            rect = (0, 0, w, h)
        x0 = max(0, min(rect[0], w))
        y0 = max(0, min(rect[1], h))
        x1 = max(x0, min(rect[2], w))
        y1 = max(y0, min(rect[3], h))
        inv_crop = self._inv[y0:y1, x0:x1]
        rgb = self._lut(mode)[inv_crop]        # (h, w, 3) uint8
        for ids, strength in ((highlight, 0.30), (selected, 0.55)):
            if ids:
                sel_mask = self._ids_mask(inv_crop, ids)
                if sel_mask is not None:
                    area = rgb[sel_mask].astype(np.uint16)
                    rgb[sel_mask] = ((area * (1 - strength))
                                     + int(255 * strength)).astype(np.uint8)
        img = Image.fromarray(rgb, "RGB")
        if scale != 1.0:
            tw = max(1, round((x1 - x0) * scale))
            th = max(1, round((y1 - y0) * scale))
            img = img.resize((tw, th), Image.NEAREST)
        return img

    def _ids_mask(self, inv_crop: np.ndarray, ids: set[int]) -> np.ndarray | None:
        idxs = [self._uidx[d.code] for pid in ids
                if (d := self.by_id.get(pid)) is not None and d.code in self._uidx]
        if not idxs:
            return None
        mask = inv_crop == idxs[0]
        for i in idxs[1:]:
            mask |= inv_crop == i
        return mask

    # --------------------------------------------------------------- mutations
    def free_colors(self, n: int) -> list[tuple[int, int, int]]:
        """`n` unique colors not used by any definition row nor any bmp pixel."""
        occupied = {(d.r, d.g, d.b) for d in self.defs}
        occupied.add((0, 0, 0))
        if self._codes is not None:
            occupied.update(decode_rgb(c) for c in self._ucodes)
        out: list[tuple[int, int, int]] = []
        for rgb in load_pool():
            if len(out) >= n:
                return out
            if rgb not in occupied:
                occupied.add(rgb)
                out.append(rgb)
        budget = max(n * 200, 100_000)         # pool exhausted: deterministic fallback
        for rgb in iter_colors():
            budget -= 1
            if len(out) >= n or budget <= 0:
                break
            if rgb not in occupied:
                occupied.add(rgb)
                out.append(rgb)
        if len(out) < n:
            raise RuntimeError("Ran out of free province colors")
        return out

    def create_province(self, type: str = "land", terrain: str = "plains",
                        continent: int = 1, coastal: bool = False,
                        color: tuple[int, int, int] | None = None) -> ProvinceDef:
        """New definition row with a fresh id + free color. Pixels are painted
        separately with `set_pixels`."""
        if color is None:
            color = self.free_colors(1)[0]
        code = encode_rgb(*color)
        if code in self.by_code or color == (0, 0, 0):
            raise ValueError(f"Color {color} is already taken")
        pid = max(self.by_id, default=0) + 1
        d = ProvinceDef(id=pid, r=color[0], g=color[1], b=color[2], type=type,
                        coastal=coastal, terrain=terrain, continent=continent,
                        line_idx=len(self._csv_lines), dirty=True)
        self.defs.append(d)
        self._csv_lines.append("")            # placeholder, re-serialized on save
        self.by_id[pid] = d
        self.by_code[code] = d
        self.dirty_csv = True
        return d

    def restore_def(self, *, id: int, color: tuple[int, int, int], type: str,
                    terrain: str, continent: int, coastal: bool) -> ProvinceDef:
        """Re-insert a definition row with an exact id + color (redo of a
        removed/undone `create_province`)."""
        if id in self.by_id:
            return self.by_id[id]
        code = encode_rgb(*color)
        if code in self.by_code or color == (0, 0, 0):
            raise ValueError(f"Color {color} is already taken")
        d = ProvinceDef(id=id, r=color[0], g=color[1], b=color[2], type=type,
                        coastal=coastal, terrain=terrain, continent=continent,
                        line_idx=len(self._csv_lines), dirty=True)
        self.defs.append(d)
        self._csv_lines.append("")            # placeholder, re-serialized on save
        self.by_id[id] = d
        self.by_code[code] = d
        self.dirty_csv = True
        return d

    def remove_province(self, pid: int) -> None:
        """Drop a definition row (caller ensures the province has no pixels)."""
        d = self.by_id.pop(pid, None)
        if d is None:
            return
        self.by_code.pop(d.code, None)
        self._defs.remove(d)
        if 0 <= d.line_idx < len(self._csv_lines):
            del self._csv_lines[d.line_idx]
            for other in self._defs:
                if other.line_idx > d.line_idx:
                    other.line_idx -= 1
        self._bboxes.pop(pid, None)
        self.dirty_csv = True
        self._luts.clear()

    def set_pixels(self, ys: np.ndarray, xs: np.ndarray, pid: int):
        """Paint pixels (map coords) with province `pid`. Incremental: only the
        touched provinces' bboxes are invalidated, LUT rows stay valid.

        Returns the *actually changed* pixels as ``(ys, xs, old_codes)`` — the
        delta the undo stack needs to revert the operation."""
        self.ensure_bitmap()
        d = self.by_id.get(pid)
        if d is None:
            raise KeyError(f"Province {pid} not defined")
        empty = (np.empty(0, np.int32), np.empty(0, np.int32),
                 np.empty(0, np.uint32))
        if len(ys) == 0:
            return empty
        code = d.code
        old = self._codes[ys, xs]
        changed = old != code
        if not changed.any():
            return empty
        ys = np.asarray(ys)[changed].astype(np.int32)
        xs = np.asarray(xs)[changed].astype(np.int32)
        old = old[changed].copy()
        idx = self._uidx.get(code)
        if idx is None:
            idx = len(self._ucodes)
            self._ucodes.append(code)
            self._uidx[code] = idx
            self._luts.clear()                # cached LUT lengths are now stale
        self._codes[ys, xs] = code
        self._inv[ys, xs] = idx
        self._bboxes.pop(pid, None)
        for old_code in np.unique(old):
            old_def = self.by_code.get(int(old_code))
            if old_def is not None:
                self._bboxes.pop(old_def.id, None)
        self.dirty_bmp = True
        return ys, xs, old

    def restore_pixels(self, ys: np.ndarray, xs: np.ndarray, codes) -> None:
        """Write raw color codes back (undo/redo path). `codes` is a uint32
        array or a single code applied to every pixel."""
        self.ensure_bitmap()
        if len(ys) == 0:
            return
        codes_arr = np.asarray(codes, dtype=np.uint32)
        if codes_arr.ndim == 0:
            codes_arr = np.full(len(ys), int(codes_arr), dtype=np.uint32)
        overwritten = np.unique(self._codes[ys, xs])
        uniq = np.unique(codes_arr)
        for c in uniq:
            ci = int(c)
            if ci not in self._uidx:
                self._uidx[ci] = len(self._ucodes)
                self._ucodes.append(ci)
                self._luts.clear()
        idx_of = np.array([self._uidx[int(c)] for c in uniq], dtype=np.int32)
        self._codes[ys, xs] = codes_arr
        self._inv[ys, xs] = idx_of[np.searchsorted(uniq, codes_arr)]
        for code in np.concatenate([overwritten, uniq]):
            d = self.by_code.get(int(code))
            if d is not None:
                self._bboxes.pop(d.id, None)
        self.dirty_bmp = True

    def set_mask(self, mask: np.ndarray, pid: int) -> None:
        ys, xs = np.nonzero(mask)
        self.set_pixels(ys, xs, pid)

    def paint_disk(self, cx: int, cy: int, radius: int, pid: int):
        """Brush stamp: paint a disk of `radius` (map px) with province `pid`.
        Returns the changed-pixels delta of `set_pixels`."""
        self.ensure_bitmap()
        h, w = self._codes.shape
        x0, x1 = max(0, cx - radius), min(w, cx + radius + 1)
        y0, y1 = max(0, cy - radius), min(h, cy + radius + 1)
        if x0 >= x1 or y0 >= y1:
            return (np.empty(0, np.int32), np.empty(0, np.int32),
                    np.empty(0, np.uint32))
        yy, xx = np.ogrid[y0 - cy:y1 - cy, x0 - cx:x1 - cx]
        disk = (xx * xx + yy * yy) <= radius * radius
        ys, xs = np.nonzero(disk)
        return self.set_pixels(ys + y0, xs + x0, pid)

    def flood_fill(self, x: int, y: int, pid: int):
        """Fill the connected same-color region around (x, y) with province
        `pid` (scanline BFS on numpy rows — no per-pixel Python loops).
        Returns the changed-pixels delta of `set_pixels`."""
        self.ensure_bitmap()
        empty = (np.empty(0, np.int32), np.empty(0, np.int32),
                 np.empty(0, np.uint32))
        h, w = self._codes.shape
        if not (0 <= x < w and 0 <= y < h):
            return empty
        seed_code = int(self._codes[y, x])
        target = self.by_id.get(pid)
        if target is None:
            raise KeyError(f"Province {pid} not defined")
        if seed_code == target.code:
            return empty
        # Work on the bbox of the seed color to keep rows short.
        seed_def = self.by_code.get(seed_code)
        bbox = self.bbox_of(seed_def.id) if seed_def is not None else None
        if bbox is None:
            mask_full = self._codes == seed_code
            rows = np.any(mask_full, axis=1)
            cols = np.any(mask_full, axis=0)
            bbox = (int(np.argmax(cols)), int(np.argmax(rows)),
                    int(len(cols) - 1 - np.argmax(cols[::-1])),
                    int(len(rows) - 1 - np.argmax(rows[::-1])))
        bx0, by0, bx1, by1 = bbox
        mask = self._codes[by0:by1 + 1, bx0:bx1 + 1] == seed_code
        region = _flood_region(mask, y - by0, x - bx0)
        ys, xs = np.nonzero(region)
        return self.set_pixels(ys + by0, xs + bx0, pid)

    # ------------------------------------------------------- province splitting
    def preview_split_area(self, pids: list[int], k: int, *,
                           seed: int | None = None, strategy: str = "organic",
                           smooth_passes: int = 2, on_progress=None):
        """Compute split labels for the union of several provinces without
        touching the map. Returns (labels int32 on the bbox slice, bbox)."""
        from .region_gen import split_region
        self.ensure_bitmap()
        codes = []
        boxes = []
        for pid in pids:
            d = self.by_id.get(pid)
            box = self.bbox_of(pid) if d is not None else None
            if d is None or box is None:
                continue
            codes.append(d.code)
            boxes.append(box)
        if not boxes:
            raise ValueError("No pixels in the selected provinces")
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
        x0, y0, x1, y1 = bbox
        crop = self._codes[y0:y1 + 1, x0:x1 + 1]
        mask = np.isin(crop, np.array(codes, dtype=np.uint32))
        labels = split_region(mask, k, seed=seed, strategy=strategy,
                              smooth_passes=smooth_passes,
                              on_progress=on_progress)
        return labels, bbox

    def preview_split(self, pid: int, k: int, **kwargs):
        """Single-province convenience wrapper of `preview_split_area`."""
        return self.preview_split_area([pid], k, **kwargs)

    def apply_split_area(self, pids: list[int], labels: np.ndarray,
                         bbox: tuple[int, int, int, int],
                         colors: list[tuple[int, int, int]] | None = None):
        """Apply previewed split labels to the map (phase 4b, generalized to a
        multi-province area).

        Each cluster's *donor* is the source province that contributed most of
        its pixels; a donor id is reused ("eaten") by the first cluster it
        dominates, other clusters become new provinces inheriting the donor's
        definition. Returns a `SplitResult` with everything the undo stack and
        the state/region sync need."""
        self.ensure_bitmap()
        x0, y0, x1, y1 = bbox
        n = int(labels.max())
        old_crop = self._codes[y0:y1 + 1, x0:x1 + 1].copy()
        if colors is None:
            colors = self.free_colors(n)
        pid_set = set(pids)
        ids: list[int] = []
        donors: list[int] = []
        new_defs: list[ProvinceDef] = []
        used: set[int] = set()
        color_i = 0
        label_pixels: list[tuple[np.ndarray, np.ndarray]] = []
        for label in range(1, n + 1):
            lys, lxs = np.nonzero(labels == label)
            label_pixels.append((lys, lxs))
            vals, counts = np.unique(old_crop[lys, lxs], return_counts=True)
            donor = None
            for v in vals[np.argsort(-counts)]:
                dd = self.by_code.get(int(v))
                if dd is not None:
                    donor = dd
                    break
            if donor is None:                     # orphan colors only — fall back
                donor = self.by_id[pids[0]]
            donors.append(donor.id)
            if donor.id in pid_set and donor.id not in used:
                ids.append(donor.id)
                used.add(donor.id)
            else:
                nd = self.create_province(type=donor.type, terrain=donor.terrain,
                                          continent=donor.continent,
                                          coastal=donor.coastal,
                                          color=colors[color_i])
                color_i += 1
                ids.append(nd.id)
                new_defs.append(nd)
        deltas: list[tuple] = []                  # (ys, xs, old, new_code)
        for (lys, lxs), pid_l in zip(label_pixels, ids):
            dys, dxs, dold = self.set_pixels(lys + y0, lxs + x0, pid_l)
            if len(dys):
                deltas.append((dys, dxs, dold, self.by_id[pid_l].code))
        for pid in pids:
            self._bboxes.pop(pid, None)
        orphaned = [p for p in pids if p not in used and self.area_of(p) == 0]
        return SplitResult(ids=ids, donors=donors, new_defs=new_defs,
                           deltas=deltas, orphaned=orphaned)

    def split_province(self, pid: int, k: int, *, seed: int | None = None,
                       strategy: str = "organic", smooth_passes: int = 2,
                       colors: list[tuple[int, int, int]] | None = None,
                       labels: np.ndarray | None = None,
                       bbox: tuple[int, int, int, int] | None = None,
                       on_progress=None) -> list[int]:
        """Split one province into `k` connected provinces; cluster 1 keeps the
        source id. Kept as the simple single-province entry point."""
        if labels is None or bbox is None:
            labels, bbox = self.preview_split(pid, k, seed=seed,
                                              strategy=strategy,
                                              smooth_passes=smooth_passes,
                                              on_progress=on_progress)
        return self.apply_split_area([pid], labels, bbox, colors).ids

    # ------------------------------------------------------------------ saving
    def save(self) -> list[Path]:
        """Write dirty bmp/csv into the mod (whole-file override). Returns the
        written paths."""
        written: list[Path] = []
        if self.dirty_csv or self.dirty_bmp:
            self.defs                                   # ensure loaded
        if self.dirty_csv:
            for d in self.defs:
                if d.dirty and 0 <= d.line_idx < len(self._csv_lines):
                    self._csv_lines[d.line_idx] = d.csv_row()
                    d.dirty = False
            target = self.mod_map_path(self.map_filenames()["definitions"])
            text = self._csv_newline.join(self._csv_lines)
            if self._csv_trailing_newline and text:
                text += self._csv_newline
            target.write_bytes(text.encode("utf-8"))
            self._csv_source = target
            self.dirty_csv = False
            written.append(target)
        if self.dirty_bmp:
            self.ensure_bitmap()
            rgb = np.empty((*self._codes.shape, 3), dtype=np.uint8)
            rgb[:, :, 0] = (self._codes >> 16) & 0xFF
            rgb[:, :, 1] = (self._codes >> 8) & 0xFF
            rgb[:, :, 2] = self._codes & 0xFF
            target = self.mod_map_path(self.map_filenames()["provinces"])
            # Pillow writes uncompressed 24-bit BMP for RGB — the format HOI4 needs.
            Image.fromarray(rgb, "RGB").save(target, format="BMP")
            self._bmp_source = target
            self.dirty_bmp = False
            written.append(target)
        return written
