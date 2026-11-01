"""NavKit's builder (navbuild), input assembly: a stage's sampling runs brought to its grid (to_pitch, same_grid), the
water and reference-extent rules (water_tops, deep_water, outside_bounds), a sample column's sector (column_sectors),
the walkable surfaces with their step-rule connectivity (Level), and stage_level, which puts them together for one
stage (the first part of stage() until 2026-10-31)."""
import copy
import math
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import numpy.typing as npt

import navsample
import navwalk

from .config import BFBC2, CLIP_TO_REFERENCE, LINK_RULE, MAX_WATER_DEPTH, PITCH, BuildError, grid_at

if TYPE_CHECKING:
    from .config import Grid, Ladder, Log, Points, Settings, Water


def water_tops(s: navsample.Samples, vertices: npt.ArrayLike | None, triangles: npt.NDArray[Any]
               ) -> npt.NDArray[np.float64]:
    """The water surface height over every sample column (-inf where there is no water). The columns are a regular
    grid, so each water triangle only visits the columns its bounding box covers."""
    nx, nz, p = s.nx, s.nz, s.pitch
    top = np.full((nz, nx), -np.inf)
    if vertices is None or not len(triangles):
        return top.ravel()
    tri = np.asarray(vertices, dtype=np.float64)[np.asarray(triangles)]
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tri:
        i0 = max(0, int(math.floor((min(ax, bx, cx) - s.min_x) / p)))
        i1 = min(nx - 1, int(math.ceil((max(ax, bx, cx) - s.min_x) / p)))
        j0 = max(0, int(math.floor((min(az, bz, cz) - s.min_z) / p)))
        j1 = min(nz - 1, int(math.ceil((max(az, bz, cz) - s.min_z) / p)))
        if i0 > i1 or j0 > j1:
            continue
        d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(d) < 1e-12:
            continue
        X = s.min_x + np.arange(i0, i1 + 1) * p
        Z = s.min_z + np.arange(j0, j1 + 1) * p
        X, Z = np.meshgrid(X, Z)
        l1 = ((bz - cz) * (X - cx) + (cx - bx) * (Z - cz)) / d
        l2 = ((cz - az) * (X - cx) + (ax - cx) * (Z - cz)) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if inside.any():
            h = np.where(inside, l1 * ay + l2 * by + l3 * cy, -np.inf)
            sub = top[j0:j1 + 1, i0:i1 + 1]
            np.maximum(sub, h, out=sub)
    return top.ravel()


def outside_bounds(s: navsample.Samples, bounds: Sequence[float]) -> npt.NDArray[np.bool_]:
    """Surfaces whose column lies outside bounds (minX, minZ, maxX, maxZ) - bool per surface (CLIP_TO_REFERENCE)."""
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x, z = s.min_x + (col % s.nx) * s.pitch, s.min_z + (col // s.nx) * s.pitch
    e = 1e-3                                             # edges inclusive (float noise of the sample grid)
    return (x < bounds[0] - e) | (x > bounds[2] + e) | (z < bounds[1] - e) | (z > bounds[3] + e)


def deep_water(s: navsample.Samples, water: 'Water | None', limit: float = MAX_WATER_DEPTH) -> npt.NDArray[np.bool_]:
    """Surfaces more than limit metres under the level's water (bool per surface). water: (vertices, triangles)."""
    if water is None:
        return np.zeros(len(s.y), dtype=bool)
    top = water_tops(s, water[0], water[1])
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    return (top[col] - s.y.astype(np.float64)) > limit


class Level:
    """Kept walkable surfaces on one stage's sample grid, with the step-rule connectivity. grid: the stage's Grid
    (grid_at; the normal grid by default); settings: the build's Settings, which the area, outline and node rules
    read."""

    def __init__(self, s: navsample.Samples, keep: npt.NDArray[np.bool_], step: float,
                 gx: navwalk.FloatArray | None = None, gz: navwalk.FloatArray | None = None,
                 stair: npt.NDArray[np.bool_] | None = None, grid: 'Grid | None' = None,
                 settings: 'Settings' = BFBC2) -> None:
        self.grid = grid if grid is not None else grid_at(PITCH, settings)
        self.settings = settings
        self.s, self.keep, self.step = s, keep, step
        self.stair = stair if stair is not None else np.zeros(len(s.y), bool)
        self.y = s.y.astype(float)
        ny = np.maximum(s.normal[:, 1], 0.05)
        self.gx = gx if gx is not None else -s.normal[:, 0] / ny
        self.gz = gz if gz is not None else -s.normal[:, 2] / ny
        self.col_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
        self.terrain = (s.hit[:, 0] == navwalk.TERRAIN_SHAPE_VTABLE) if s.hit is not None else np.ones(len(s.y), bool)

    def on_grid(self, grid: 'Grid') -> 'Level':
        """The same surfaces with another Grid's step rule (a shallow copy; merge_detailed)."""
        other = copy.copy(self)
        other.grid = grid
        return other

    def surfaces(self, gi: int, gj: int) -> list[int] | range:
        if not (0 <= gi < self.s.nx and 0 <= gj < self.s.nz):
            return range(0)
        c = gj * self.s.nx + gi
        return [k for k in range(self.s.start[c], self.s.start[c + 1]) if self.keep[k]]

    def connected(self, k: int, n: int, di: int, dj: int, rule: str | None = None) -> bool:
        """Surface n (direction di, dj from k) links to k by rule ('slope', 'raw', 'terrain'; LINK_RULE if None)."""
        rule = rule or LINK_RULE
        if rule == 'slope' or (rule == 'terrain' and not (self.terrain[k] and self.terrain[n])):
            return navwalk.step_fits(self.y[n] - self.y[k], (self.gx[k], self.gz[k]), (self.gx[n], self.gz[n]),
                                     self.stair[k], self.stair[n], di, dj, self.grid.pitch, self.step)
        y = self.y
        step = self.grid.link_step
        if not (di and dj):
            return abs(y[n] - y[k]) <= step
        # diagonal: through either side column, over any surface there (walkable or not)
        c = self.col_of[k]
        gi, gj = c % self.s.nx, c // self.s.nx
        for a, b in ((gi + di, gj), (gi, gj + dj)):
            if 0 <= a < self.s.nx and 0 <= b < self.s.nz:
                m0 = b * self.s.nx + a
                for m in range(self.s.start[m0], self.s.start[m0 + 1]):
                    if abs(y[m] - y[k]) <= step and abs(y[n] - y[m]) <= step:
                        return True
        return False

    def neighbour(self, k: int, gi: int, gj: int, di: int, dj: int, prefer: 'set[int] | dict[int, Any] | None' = None,
                  rule: str | None = None) -> int | None:
        """The kept surface in column (gi+di, gj+dj) connected to surface k, or None. With prefer (a set of
        surfaces, e.g. those of k's own area), a connected surface in that set is returned first."""
        best, bd = None, 9e9
        for n in self.surfaces(gi + di, gj + dj):
            if self.connected(k, n, di, dj, rule):
                if prefer is not None and n in prefer:
                    return n
                d = abs(self.y[n] - self.y[k])
                if d < bd:
                    best, bd = n, d
        return best


def to_pitch(f: navsample.Samples, pitch: float, what: str) -> navsample.Samples:
    # a sampling run on the build's grid: as it is at the build's pitch, coarsened from a finer one (navwalk.coarse),
    # an error otherwise (a run coarser than the build, or one coarse() cannot bring to this pitch)
    if abs(f.pitch - pitch) <= 1e-3:
        return f
    ratio = pitch / f.pitch
    if ratio < 1 or abs(ratio - round(ratio)) > 1e-3:
        raise BuildError(f"{what}: pitch {f.pitch:g} cannot be brought to the build's {pitch:g}")
    return navwalk.coarse(f, pitch)


def same_grid(s: navsample.Samples, f: navsample.Samples, what: str) -> navsample.Samples:
    if (f.nx, f.nz) != (s.nx, s.nz) or abs(f.min_x - s.min_x) > 1e-3 or abs(f.min_z - s.min_z) > 1e-3 \
            or abs(f.pitch - s.pitch) > 1e-6:
        raise BuildError(f"{what}: grid {f.nx} x {f.nz} at ({f.min_x:g}, {f.min_z:g}) pitch {f.pitch:g} is not the main "
                         f"run's {s.nx} x {s.nz} at ({s.min_x:g}, {s.min_z:g}) pitch {s.pitch:g} - sample both with one box")
    return f


def column_sectors(s: navsample.Samples, cols: npt.ArrayLike, xoff: int, zoff: int, settings: 'Settings', pitch: float
                   ) -> tuple[list[int], list[int]]:
    """(X list, Y list): the sectors of sample columns cols of s, less the sector offsets. With the settings'
    column_sectors_cm (NavKit's default) from whole centimetres (navsample.column_sectors, as detailed.py counts);
    without, from the float position floor((min + i x pitch) / 100) at the pitch given - BF1943's builds before
    2026-10-31, which took the stage's grid pitch (a Python float) in stage() and the samples' own float32 pitch in
    merge_detailed's seeds (on a sector line the two disagree: Wake Island at 0.2 m, 29,969 columns against 17,632)."""
    cols = np.asarray(cols, dtype=np.int64)
    if settings.column_sectors_cm:
        X, Z = navsample.column_sectors(s, cols)
        return (X - xoff).tolist(), (Z - zoff).tolist()
    X, Y = [], []
    for c in cols.tolist():
        x, z = s.min_x + (c % s.nx) * pitch, s.min_z + (c // s.nx) * pitch
        X.append(int(math.floor(x / 100.0)) - xoff)
        Y.append(int(math.floor(z / 100.0)) - zoff)
    return X, Y


def stage_level(samples_path: str, high_path: str | None, grid: 'Grid', settings: 'Settings',
                tris_path: str | None = None, step: float = 0.28, log: 'Log' = print, dynamic_path: str | None = None,
                narrow_path: str | None = None, stairs_path: str | None = None, exclude_path: str | None = None,
                exclude_foot_path: str | None = None, seeds: 'Points | None' = None,
                edits: Sequence[dict[str, Any]] | None = None, water: 'Water | None' = None,
                max_water_depth: float | None = MAX_WATER_DEPTH, bounds: Sequence[float] | None = None,
                order_points: 'Points | None' = None, seed_points: 'Points | None' = None,
                ladders: 'list[Ladder] | None' = None, t0: float | None = None
                ) -> tuple['Level', list[tuple[int, int]]]:
    """A stage's input assembly (stage()'s first part until 2026-10-31): its sampling runs brought to the grid's pitch,
    the hand edits, water and reference-extent rules, the level's objects, the walkable surfaces (navwalk.walkable)
    and the ladder links' end surfaces. grid, settings: the stage's Grid and the build's Settings; the rest as
    stage(). Returns (level, ladder_pairs): the stage's Level and [(foot surface, top surface)]."""
    t0 = t0 or time.time()
    pitch = grid.pitch
    f, _ = navsample.load(samples_path)
    s = to_pitch(f, pitch, 'samples')
    hand = {}
    soldiers = list(seeds or [])                 # the level's soldier spawn points (navwalk.CHARACTERS_ABSENT)
    if edits:
        import navedits
        e = navedits.edits_for(s, edits)
        hand = {'force_walkable': e['force_walkable'] if e['surfaces_walkable'] else None,
                'force_unwalkable': e['force_unwalkable'] if e['surfaces_unwalkable'] else None,
                'unseeds': e['unseeds']}
        seeds = list(seeds or []) + e['seeds']
        log(f'hand edits: {e["counts"]}; surfaces forced walkable {e["surfaces_walkable"]}, unwalkable {e["surfaces_unwalkable"]}')
    if water is not None and max_water_depth is not None:
        deep = deep_water(s, water, max_water_depth)
        if deep.any():
            fw, fu = hand.get('force_walkable'), hand.get('force_unwalkable')
            if fw is not None:
                deep &= ~fw                      # a hand edit forcing ground walkable wins over the water rule
            hand['force_unwalkable'] = deep if fu is None else (fu | deep)
        log(f'water: {int(deep.sum())} surfaces more than {max_water_depth} m under water left out')
    if bounds is not None and CLIP_TO_REFERENCE:
        outside = outside_bounds(s, bounds)
        fw, fu = hand.get('force_walkable'), hand.get('force_unwalkable')
        if fw is not None:
            outside &= ~fw                       # a hand edit forcing ground walkable wins here too
        hand['force_unwalkable'] = outside if fu is None else (fu | outside)
        log(f'reference extent x {bounds[0]:.1f}..{bounds[2]:.1f}, z {bounds[1]:.1f}..{bounds[3]:.1f}: '
            f'{int(outside.sum())} surfaces outside left out')
    tris = np.load(tris_path) if tris_path else None
    dyn = np.load(dynamic_path) if dynamic_path else None
    # the clearance runs are matched to the main run column by column (navwalk.matched_flags): same grid, or an error
    hi = same_grid(s, to_pitch(navsample.load(high_path)[0], pitch, 'high-capsule run'), 'high-capsule run') if high_path else None
    narrow = same_grid(s, to_pitch(navsample.load(narrow_path)[0], pitch, 'narrow run'), 'narrow run') if narrow_path else None
    st = np.load(stairs_path) if stairs_path else None
    ex = np.load(exclude_path) if exclude_path else None
    exf = np.load(exclude_foot_path) if exclude_foot_path else None
    walk_stats = {}                              # navwalk's counts, for the log (roof islands dropped)
    keep, gx, gz, stair = cast(tuple[navwalk.BoolArray, navwalk.FloatArray, navwalk.FloatArray, navwalk.BoolArray],
                               navwalk.walkable(s, tris, slope=settings.slope_max, step=step, dynamic_tris=dyn,
                                    high_capsule=hi, headroom_band=settings.headroom_band, stats=walk_stats,
                                    return_gradients=True,
                                    return_stairs=True,
                                    narrow_capsule=narrow, stair_tris=st, exclude_tris=ex, exclude_footprint_tris=exf,
                                    extra_seeds=list(seeds or []) + list(seed_points or []), soldier_points=soldiers,
                                    ladders=ladders, **hand,
                                    # roof pruning anchors: soldier spawns, AI order positions, and (stage b) the ground
                                    # the normal build kept - stage b drops only roofs that build did not have
                                    anchors=list(seeds or []) + list(order_points or []) + list(seed_points or [])))
    level = Level(s, keep, step, gx, gz, stair, grid=grid, settings=settings)
    log(f'{len(s.y)} surfaces, {int(keep.sum())} walkable kept ({time.time() - t0:.0f} s)'
        + (f'; roof islands dropped {walk_stats.get("dropped", 0)} ({walk_stats.get("surfaces", 0)} surfaces)'
           if navwalk.ROOF_PRUNE else ''))
    # ladder links: the kept surfaces at each ladder's foot and top (navwalk.ladder_ends), or with the settings'
    # ladder_all_ground one per piece of ground within reach of each end (navwalk.ladder_end_options)
    ladder_pairs = []
    for lad in (ladders or ()):
        if settings.ladder_all_ground:
            feet, tops = navwalk.ladder_end_options(s, keep, gx, gz, stair, step, lad)
            ladder_pairs += [(int(f_), int(t_)) for f_ in feet for t_ in tops]
        else:
            f_, t_ = navwalk.ladder_ends(s, keep, keep, lad)
            if f_ is not None and t_ is not None and f_ != t_:
                ladder_pairs.append((int(f_), int(t_)))
    return level, ladder_pairs
