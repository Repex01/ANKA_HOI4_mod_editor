"""Auto-generation of N provinces inside an arbitrary mask.

Port of the proven ``F:/Python/TestFilling`` prototype (Cluster growth +
majority smoothing), adapted to the map editor:

* growth is confined to a boolean *mask* (the selected region), on the bbox
  slice — never the whole 5632×2048 map;
* K seeds are spread by greedy max-min distance, so provinces come out
  comparable in size;
* ``smooth`` is a **vectorized** majority filter over labels (the prototype's
  double Python loop is far too slow at map scale);
* clusters are kept connected afterwards (smoothing can pinch off islands —
  minor components are reassigned to a neighboring cluster);
* deterministic under a ``seed``.

The result is a ``labels`` array (0 = outside mask, 1..K = cluster) — the
caller (`MapService.split_province`) maps labels to real province ids/colors.
"""
from __future__ import annotations

import random
from typing import Callable

import numpy as np

_NEIGHBORS = ((0, -1), (1, 0), (0, 1), (-1, 0))     # clockwise from "up"


# ------------------------------------------------------------------ seeding
def _seedable_mask(mask: np.ndarray, k: int) -> np.ndarray:
    """Mask restricted to components big enough to host a cluster seed.

    Greedy max-min seeding otherwise loves remote islands (they maximize
    distance), turning every islet into its own tiny province. Components far
    smaller than a fair cluster share are excluded — their pixels are later
    attached to the nearest grown cluster by `_fill_leftovers`."""
    comps = _components(mask)
    if len(comps) <= 1:
        return mask
    total = int(mask.sum())
    threshold = max(16, total // (k * 8))
    big = [c for c in comps if int(c.sum()) >= threshold]
    if not big:
        return mask
    out = np.zeros_like(mask)
    for c in big:
        out |= c
    return out


def spread_seeds(mask: np.ndarray, k: int,
                 rng: random.Random) -> list[tuple[int, int]]:
    """K points inside `mask`, spread by greedy max-min distance (first one
    random). Returns (y, x) tuples."""
    coords = np.argwhere(mask)
    if len(coords) == 0:
        return []
    k = min(k, len(coords))
    first = coords[rng.randrange(len(coords))]
    seeds = [(int(first[0]), int(first[1]))]
    dist = np.full(len(coords), np.inf)
    while len(seeds) < k:
        sy, sx = seeds[-1]
        d = (coords[:, 0] - sy) ** 2 + (coords[:, 1] - sx) ** 2
        dist = np.minimum(dist, d)
        nxt = coords[int(np.argmax(dist))]
        seeds.append((int(nxt[0]), int(nxt[1])))
    return seeds


# ------------------------------------------------------------------- growth
class _Grower:
    """One growing cluster over a shared `labels` array (prototype's Cluster:
    incremental candidate border, stale entries dropped lazily)."""

    __slots__ = ("labels", "mask", "label", "border", "size")

    def __init__(self, labels: np.ndarray, mask: np.ndarray, label: int):
        self.labels = labels
        self.mask = mask
        self.label = label
        self.border: set[tuple[int, int]] = set()
        self.size = 0

    def claim(self, y: int, x: int) -> None:
        self.labels[y, x] = self.label
        self.size += 1
        self.border.discard((y, x))
        h, w = self.labels.shape
        for dx, dy in _NEIGHBORS:
            ny, nx = y + dy, x + dx
            if (0 <= ny < h and 0 <= nx < w and self.mask[ny, nx]
                    and self.labels[ny, nx] == 0):
                self.border.add((ny, nx))

    def _refresh(self) -> None:
        self.border = {(y, x) for y, x in self.border if self.labels[y, x] == 0}

    def grow_random(self, count: int, rng: random.Random) -> bool:
        """Organic growth: claim `count` random border pixels."""
        self._refresh()
        if not self.border:
            return False
        for _ in range(count):
            if not self.border:
                break
            y, x = rng.choice(tuple(self.border))
            self.claim(y, x)
        return True

    def grow_weighted(self, count: int, rng: random.Random) -> bool:
        """Smoother borders: candidates weighted by how many of their
        4-neighbors already belong to this cluster."""
        self._refresh()
        if not self.border:
            return False
        for _ in range(count):
            if not self.border:
                break
            candidates = tuple(self.border)
            weights = [self._own_neighbors(y, x) for y, x in candidates]
            y, x = rng.choices(candidates, weights=weights, k=1)[0]
            self.claim(y, x)
        return True

    def _own_neighbors(self, y: int, x: int) -> int:
        h, w = self.labels.shape
        n = 0
        for dx, dy in _NEIGHBORS:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and self.labels[ny, nx] == self.label:
                n += 1
        return n


def _shift_labels(labels: np.ndarray, shift: int, axis: int) -> np.ndarray:
    """`labels` moved one step along `axis` (vacated edge zeroed)."""
    src = np.roll(labels, shift, axis=axis)
    if shift == 1:
        if axis == 0:
            src[0, :] = 0
        else:
            src[:, 0] = 0
    else:
        if axis == 0:
            src[-1, :] = 0
        else:
            src[:, -1] = 0
    return src


def _fill_leftovers(labels: np.ndarray, mask: np.ndarray) -> None:
    """Assign unreachable/unclaimed mask pixels to an adjacent cluster by
    repeated one-step dilation. Mask parts cluster growth can never reach —
    offshore islands separated by out-of-mask gaps — are then flooded with a
    wavefront over the WHOLE grid (labels cross the gaps), so every island
    joins its nearest generated cluster instead of being dumped into #1."""
    h, w = labels.shape
    for _ in range(h + w):
        empty = mask & (labels == 0)
        if not empty.any():
            return
        changed = False
        for shift in (1, -1):
            for axis in (0, 1):
                src = _shift_labels(labels, shift, axis)
                take = empty & (labels == 0) & (src > 0)
                if take.any():
                    labels[take] = src[take]
                    changed = True
        if not changed:
            break
    empty = mask & (labels == 0)
    if not empty.any():
        return
    # Nearest-cluster flood: spread labels across non-mask pixels too; the
    # first label to reach an island pixel is the closest cluster. Sources
    # are frozen per iteration (synchronous BFS), so the wavefront advances
    # one pixel everywhere per step and distances stay honest.
    field = labels.copy()
    for _ in range(h + w):
        if not (empty & (field == 0)).any():
            break
        zero = field == 0
        base = field.copy()
        grown = False
        for shift in (1, -1):
            for axis in (0, 1):
                src = _shift_labels(base, shift, axis)
                take = zero & (src > 0)
                if take.any():
                    field[take] = src[take]
                    zero &= ~take
                    grown = True
        if not grown:
            break
    reached = empty & (field > 0)
    labels[reached] = field[reached]
    still = empty & (labels == 0)
    if still.any():                       # no labels anywhere at all — cover
        labels[still] = 1


# ---------------------------------------------------------------- smoothing
def smooth_labels(labels: np.ndarray, mask: np.ndarray, k: int,
                  passes: int = 2, radius: int = 1) -> np.ndarray:
    """Vectorized majority filter: each in-mask pixel takes the most frequent
    label in its (2r+1)² window (only in-mask labeled neighbors vote); ties
    keep the current label — stable borders don't jitter."""
    if passes <= 0 or k < 2:
        return labels
    h, w = labels.shape
    offsets = [(dy, dx) for dy in range(-radius, radius + 1)
               for dx in range(-radius, radius + 1)]
    for _ in range(passes):
        counts = np.zeros((k + 1, h, w), dtype=np.uint16)
        for dy, dx in offsets:
            shifted = np.zeros_like(labels)
            ys0, ys1 = max(0, dy), min(h, h + dy)
            xs0, xs1 = max(0, dx), min(w, w + dx)
            shifted[ys0:ys1, xs0:xs1] = labels[ys0 - dy:ys1 - dy,
                                               xs0 - dx:xs1 - dx]
            for lbl in range(1, k + 1):
                counts[lbl] += shifted == lbl
        best = counts[1:].argmax(axis=0).astype(np.int32) + 1
        best_count = counts[1:].max(axis=0)
        cur = labels
        cur_count = np.take_along_axis(
            counts, cur[np.newaxis].clip(0, k), axis=0)[0]
        keep = cur_count >= best_count                # tie → keep current
        new = np.where(keep, cur, best)
        labels = np.where(mask & (cur > 0), new, labels).astype(np.int32)
    return labels


# ------------------------------------------------------------- connectivity
def _components(mask: np.ndarray) -> list[np.ndarray]:
    """Connected components (4-conn) of a boolean mask, as boolean arrays."""
    from .map_service import _flood_region
    remaining = mask.copy()
    out = []
    while True:
        ys, xs = np.nonzero(remaining)
        if len(ys) == 0:
            return out
        comp = _flood_region(remaining, int(ys[0]), int(xs[0]))
        out.append(comp)
        remaining &= ~comp


def enforce_connectivity(labels: np.ndarray, mask: np.ndarray, k: int) -> None:
    """Smoothing may pinch off small islands; give every label one connected
    body by reassigning minor components to the dominant neighboring label."""
    for lbl in range(1, k + 1):
        comps = _components(labels == lbl)
        if len(comps) <= 1:
            continue
        comps.sort(key=lambda c: int(c.sum()), reverse=True)
        for comp in comps[1:]:
            neighbor = np.zeros_like(comp)
            neighbor[1:, :] |= comp[:-1, :]
            neighbor[:-1, :] |= comp[1:, :]
            neighbor[:, 1:] |= comp[:, :-1]
            neighbor[:, :-1] |= comp[:, 1:]
            neighbor &= ~comp & mask & (labels != lbl) & (labels > 0)
            votes = labels[neighbor]
            if len(votes):
                labels[comp] = int(np.bincount(votes).argmax())
            # else: island of the mask itself — keep the label, it IS connected
            # within its own mask part.


# ------------------------------------------------------------------- driver
def split_region(mask: np.ndarray, k: int, *, seed: int | None = None,
                 strategy: str = "organic", smooth_passes: int = 2,
                 step_min: int = 1, step_max: int = 5,
                 max_iterations: int = 200_000,
                 barrier: np.ndarray | None = None,
                 on_progress: Callable[[int, int], None] | None = None) -> np.ndarray:
    """Split the True area of `mask` into `k` connected clusters.

    Returns int32 `labels` (same shape): 0 outside, 1..k inside. `strategy`:
    "organic" (random border growth) or "smooth" (weighted → even borders).
    Deterministic for a given `seed`.

    `barrier` (bool, same shape) marks pixels growth may not enter or cross —
    e.g. river pixels, so every cluster stays on one bank. Barrier pixels
    inside `mask` are attached to the nearest grown cluster at the end (a thin
    river gets split down its middle between the two banks).
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    labels = np.zeros(mask.shape, dtype=np.int32)
    total = int(mask.sum())
    if total == 0:
        return labels
    grow_mask = mask
    if barrier is not None:
        grow_mask = mask & ~barrier
        if not grow_mask.any():          # selection is ALL barrier — ignore it
            grow_mask = mask
    rng = random.Random(seed)
    seeds = spread_seeds(_seedable_mask(grow_mask, k), k, rng)
    growers = [_Grower(labels, grow_mask, i + 1) for i in range(len(seeds))]
    for g, (sy, sx) in zip(growers, seeds):
        g.claim(sy, sx)

    grow = (_Grower.grow_weighted if strategy == "smooth"
            else _Grower.grow_random)
    iterations = 0
    while iterations < max_iterations:
        changed = False
        for g in growers:
            if grow(g, rng.randint(step_min, step_max), rng):
                changed = True
        iterations += 1
        if on_progress is not None and iterations % 50 == 0:
            on_progress(sum(g.size for g in growers), total)
        if not changed:
            break

    _fill_leftovers(labels, grow_mask)
    labels = smooth_labels(labels, grow_mask, len(growers),
                           passes=smooth_passes)
    # Full mask last: barrier pixels inside the selection join the nearest
    # cluster (each bank keeps its own side — growth never crossed).
    _fill_leftovers(labels, mask)
    enforce_connectivity(labels, mask, len(growers))
    if on_progress is not None:
        on_progress(total, total)
    return labels
