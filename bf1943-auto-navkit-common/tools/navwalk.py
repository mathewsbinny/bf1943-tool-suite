"""NavKit: walkable surfaces from in-game collision samples, and scoring against an existing pathdata.hpd.

Rules (research/KYNAPSE_SPEC.md section 3):
  1. a sampled surface is a candidate when the soldier capsule fits and its normal is within
     GroundSlopeMax (40 deg) of vertical;
  2. surfaces lying on a ForbidPathfinding object (forbid_tris.npy from levelobjects.py) are removed;
  3. regions grow 8-connected between neighbouring samples when the height step, relative to the local
     ground plane (from both normals), is at most StepMax (0.28 m);
  4. regions are seeded on the sector perimeter lines (1.6 m inside every 100 m sector edge), on terrain
     and objects alike (terrain-only seeding cut off decks, platforms and building floors: sp_01's AI order
     positions went from 463 to 613 of DICE's 633 with object seeds; terrain_seeds_only=True restores it).

Usage:
  navwalk.py score <samples.bin> <pathdata.hpd> [forbid_tris.npy] [--step 0.28] [--slope 40]
                   scores at the 0.8 m sample grid (a 0.2 m run is subsampled)
"""
import math
import sys
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from typing import Any, cast

import numpy as np
import numpy.typing as npt

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
import navsample  # noqa: E402

type BoolArray = npt.NDArray[np.bool_]                  # one flag per surface
type FloatArray = npt.NDArray[np.floating[Any]]         # one value per surface (float32 or float64)
type Walkable = (BoolArray | tuple[BoolArray, FloatArray, FloatArray]
                 | tuple[BoolArray, FloatArray, FloatArray, BoolArray])

TERRAIN_SHAPE_VTABLE = 0x1454D14   # shape class of terrain hits (navsample version 2 hit info), build 795745
CHARACTER_SHAPE_VTABLE = 0x1483378  # shape class of soldiers standing in the level while it is sampled (their spawns):
                                    # hits 1.3-1.5 m over the floor at the spawn points (sp_006: player, Sweetwater,
                                    # Redford; sp_004_b: player), 137 soldier-sized clusters on sp_004_b; build 795745
HEADROOM_BAND = (0.5, 2.0)          # ground with the next sampled surface above it this much higher is not walkable: DICE
                                    # keeps almost none (sp_004_b and sp_01, NavKit ground cells against DICE's: 0.4-0.5 m
                                    # above 50-52 % kept, 0.5-1.2 m 0-18 %, 1.2-2.0 m 0-1 %; from 2.0 m sp_004_b 32-94 %).
                                    # Under 0.5 m: thin things (steps, rails) - kept. None to switch off. (2026-10-19)
CHARACTERS_ABSENT = True            # soldiers are not in path data: their surfaces are not floor and the ground under
                                    # and beside them is not blocked by them (as dynamic props) - before 2026-10-19 the
                                    # squad's spawn cells were left out, and in game the squad could not path to the
                                    # player (sp_004_b NavKit play test: 7 of 73 go-to requests found a path)
STEEP_TERRAIN = False              # terrain whose sampled normal is steeper than the slope limit (up to
                                    # STEEP_TERRAIN_MAX) is walkable when its slope from the heights STEEP_TERRAIN_SPAN m
                                    # either side is within the limit: DICE judges slope over a distance, not by a terrain
                                    # triangle's normal (sp_03, 0.8 m samples: 1.9 % of DICE's ground has a normal over
                                    # 40 deg, median 36.8 deg from the heights). The 0.8 m grid stepped over such bands; at
                                    # 0.2 m every column of one failed and cut valley-to-plateau routes (sp_03 detailed
                                    # build: connectivity 98.8 -> 63.2, 2026-10-22). Off: on sp_03 it raised ground
                                    # 97.3 -> 98.9 but extra ground 6.2 -> 12.0 and over-connection 19 -> 42 %, and
                                    # connectivity only 63.2 -> 63.8 - those routes were cut by something else
STEEP_TERRAIN_MAX = 55.0
LIP_WIDTH = 2                      # pitch < 0.5 only: a terrain sample steeper than the slope limit (up to
                                    # STEEP_TERRAIN_MAX) is walkable when walkable ground lies within LIP_WIDTH samples on
                                    # both opposite sides (an axis or a diagonal) with a slope within the limit between them
                                    # - a narrow lip. The 0.8 m step rule steps over such lips; at 0.2 m one cut sp_007's
                                    # north strip off (connectivity 96.2 -> 81.2, 2026-10-22). DICE at pitch 0.2 covers
                                    # 81-86 % of terrain under 40 deg and 15 % at 40-45, so a blanket rule is wrong
                                    # (STEEP_TERRAIN); of the lips this accepts DICE covers 40 % (sp_007 window: +0.9 %
                                    # ground, +1.4 % extra; width 3: +1.3 / +3.0). None to switch off
LIP_OBJECT_RISE = 0.3              # ... and on objects when the steep sample is within this of the walkable ground on
                                    # both sides: door sills and thresholds (sp_009's aircraft: a 5 cm sill with steep
                                    # sides at every hull-section seam cut the aisle at pitch 0.2), never wall tops or
                                    # ledges; None for terrain only
LIP_OBJECT_SLOPE_MAX = None        # a slope cap (deg) on object lips; None: any steepness, above STEEP_TERRAIN_MAX
                                    # only with flat floor on both sides (LIP_OBJECT_FLAT deg; 2026-10-22 evening)
LIP_OBJECT_FLAT = 15.0
ROOF_PRUNE = True                  # drop roof islands nothing reaches: a kept island (step-rule connected) that
                                    # holds no anchor (soldier spawn, AI order position, ladder end, or ground the
                                    # normal build kept - stage b), is not the largest, is mostly raised (ROOF_SHARE of
                                    # it on objects with a surface ROOF_RAISE m or more below), and has no anchored
                                    # ground within ROOF_NEAR m at its own height (ROOF_NEAR_DY). Ground-level islands
                                    # stay: DICE keeps those (all 15 levels, 2026-10-22: dropping unanchored islands
                                    # under 25 nodes cost as much DICE ground as extra it removed), but not detached
                                    # roofs and scenery tops (this rule: mean -1.31 points extra for -0.28 shared;
                                    # sp_008 -7.4 / -2.25, sp_01 -3.6 / -0.01)
ROOF_RAISE = 2.0
ROOF_SHARE = 0.5
ROOF_NEAR = 2.0
ROOF_NEAR_DY = 2.5
DYNAMIC_ABSENT_HEADROOM = True     # dynamic props do not count as a ceiling for HEADROOM_BAND (they are absent for path
                                    # data - a closed gate's leaves made the floor beside it 'low headroom')
DYNAMIC_FLOOR = 0.3                # a surface on a dynamic prop within this of walkable floor in a neighbouring column
                                    # stands for the floor under the prop (the sampler records only a prop lying on the
                                    # floor, e.g. a gate's bottom rail 0.09 m up - its columns were a hole, and at pitch
                                    # 0.2 a closed gate cut the only way through a wall: sp_003_b's monastery gate,
                                    # 2026-10-22); None to switch off
STEEP_TERRAIN_SPAN = 0.8
OUTLINE_Y_OFFSET = 0.95            # outline heights in pathdata.hpd = ground + EntityHeight / 2 (score's default)
PITCH = 0.8
SECTOR = 100.0


def _column_of(s: navsample.Samples) -> npt.NDArray[np.int_]:
    """The column index of every surface (columns are j-major: column = j * nx + i)."""
    return np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))


def coarse(f: navsample.Samples, pitch: float | None = None) -> navsample.Samples:
    """Every (pitch / f.pitch)-th column of a fine run, keeping all surfaces of the column (pitch: the target, by
    default PITCH, 0.8 m; navbuild's detailed pass asks for 0.2)."""
    pitch = pitch or PITCH
    step = int(round(pitch / f.pitch))
    nx, nz = (f.nx - 1) // step + 1, (f.nz - 1) // step + 1
    start, surfaces = [0], []
    for j in range(nz):
        for i in range(nx):
            col = (j * step) * f.nx + i * step
            surfaces.extend(range(f.start[col], f.start[col + 1]))
            start.append(len(surfaces))
    surfaces = np.array(surfaces, dtype=np.int64)
    hit = f.hit[surfaces] if f.hit is not None and len(f.hit) else None
    return navsample.Samples(pitch, f.min_x, f.min_z, nx, nz, f.y_top, f.y_bottom, f.radius, f.cap_bottom,
                             f.cap_top, f.ray_radius, np.array(start), f.y[surfaces], f.normal[surfaces],
                             f.flags[surfaces], hit)


def on_objects(s: navsample.Samples, tris: npt.NDArray[Any] | None, tol: float = 0.2) -> BoolArray:
    """Surfaces lying on one of the given world-space triangles (within tol metres vertically)."""
    out = np.zeros(len(s.y), bool)
    if tris is None or not len(tris):
        return out
    x0, x1 = s.min_x - 2, s.min_x + s.nx * s.pitch + 2
    z0, z1 = s.min_z - 2, s.min_z + s.nz * s.pitch + 2
    in_box = tris[(tris[:, :, 0].max(1) >= x0) & (tris[:, :, 0].min(1) <= x1) &
                  (tris[:, :, 2].max(1) >= z0) & (tris[:, :, 2].min(1) <= z1)].astype(float)
    y = s.y.astype(float)
    # a triangle's corners a, b, c; l1, l2 (and 1 - l1 - l2) a sample's barycentric weights in its xz projection
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in in_box:
        det = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(det) < 1e-9:
            continue
        i0 = max(0, math.ceil((min(ax, bx, cx) - s.min_x) / s.pitch))
        i1 = min(s.nx - 1, math.floor((max(ax, bx, cx) - s.min_x) / s.pitch))
        j0 = max(0, math.ceil((min(az, bz, cz) - s.min_z) / s.pitch))
        j1 = min(s.nz - 1, math.floor((max(az, bz, cz) - s.min_z) / s.pitch))
        for j in range(j0, j1 + 1):
            pz = s.min_z + j * s.pitch
            for i in range(i0, i1 + 1):
                px = s.min_x + i * s.pitch
                l1 = ((bz - cz) * (px - cx) + (cx - bx) * (pz - cz)) / det
                l2 = ((cz - az) * (px - cx) + (ax - cx) * (pz - cz)) / det
                if l1 < -1e-6 or l2 < -1e-6 or 1 - l1 - l2 < -1e-6:
                    continue
                tri_y = l1 * ay + l2 * by + (1 - l1 - l2) * cy
                col = j * s.nx + i
                for k in range(s.start[col], s.start[col + 1]):
                    if abs(y[k] - tri_y) <= tol:
                        out[k] = True
    return out


def near_characters(s: navsample.Samples, character: BoolArray, grow: float = 0.9, below: float = 2.2,
                    above: float = 0.3) -> BoolArray:
    """Surfaces a capsule test would see blocked by a soldier: within `grow` metres (horizontally) of a character hit
    (CHARACTER_SHAPE_VTABLE) and from `below` metres under to `above` metres over it."""
    out = np.zeros(len(s.y), bool)
    y = s.y.astype(float)
    column_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    radius = int(math.floor(grow / s.pitch + 1e-9))
    for k in np.nonzero(character)[0]:
        col = column_of[k]
        i, j = col % s.nx, col // s.nx
        for dj in range(-radius, radius + 1):
            for di in range(-radius, radius + 1):
                ni, nj = i + di, j + dj
                if 0 <= ni < s.nx and 0 <= nj < s.nz and math.hypot(di, dj) * s.pitch <= grow + 1e-9:
                    ncol = nj * s.nx + ni
                    for n in range(s.start[ncol], s.start[ncol + 1]):
                        if y[k] - below <= y[n] <= y[k] + above:
                            out[n] = True
    return out


def headroom(s: navsample.Samples, ignore: BoolArray | None = None) -> npt.NDArray[np.float64]:
    """Per surface: the gap to the next sampled surface above it in its column (inf when none); surfaces flagged in
    `ignore` (soldiers) do not count as above."""
    y = s.y.astype(float)
    out = np.full(len(y), np.inf)
    ignored = ignore if ignore is not None else np.zeros(len(y), bool)
    for col in range(s.nx * s.nz):
        first, end = s.start[col], s.start[col + 1]
        if end - first < 2:
            continue
        counted = [k for k in range(first, end) if not ignored[k]]
        if len(counted) < 2:
            continue
        heights = np.sort(y[counted])
        for k in counted:
            higher = heights[heights > y[k] + 0.05]
            if len(higher):
                out[k] = higher[0] - y[k]
    return out


def low_headroom(s: navsample.Samples, ignore: BoolArray | None, lo: float, hi: float) -> BoolArray:
    """Surfaces with the next surface above them between lo and hi metres higher (HEADROOM_BAND)."""
    gap = headroom(s, ignore)
    return (gap >= lo) & (gap < hi)


def near_points(s: navsample.Samples, points: Iterable[Sequence[float]], grow: float = 0.9, below: float = 0.5,
                above: float = 0.5) -> BoolArray:
    """Surfaces within `grow` metres (horizontally) of a point and from `below` under to `above` over its height."""
    out = np.zeros(len(s.y), bool)
    y = s.y.astype(float)
    radius = int(math.floor(grow / s.pitch + 1e-9))
    for px, py, pz in points:
        i0, j0 = int(round((px - s.min_x) / s.pitch)), int(round((pz - s.min_z) / s.pitch))
        for dj in range(-radius - 1, radius + 2):
            for di in range(-radius - 1, radius + 2):
                ni, nj = i0 + di, j0 + dj
                if not (0 <= ni < s.nx and 0 <= nj < s.nz):
                    continue
                if math.hypot(s.min_x + ni * s.pitch - px, s.min_z + nj * s.pitch - pz) > grow + 1e-9:
                    continue
                col = nj * s.nx + ni
                for n in range(s.start[col], s.start[col + 1]):
                    if py - below <= y[n] <= py + above:
                        out[n] = True
    return out


def near_objects(s: navsample.Samples, tris: npt.NDArray[Any] | None, grow: float = 0.5, below: float = 2.2,
                 above: float = 0.3) -> BoolArray:
    """Surfaces within `grow` metres (horizontally) of a triangle's bounding box and from `below` metres under
    to `above` metres over it - the ground a capsule test around an object would see as blocked."""
    out = np.zeros(len(s.y), bool)
    if tris is None or not len(tris):
        return out
    y = s.y.astype(float)
    for tri in tris.astype(float):
        x0, x1 = tri[:, 0].min() - grow, tri[:, 0].max() + grow
        z0, z1 = tri[:, 2].min() - grow, tri[:, 2].max() + grow
        i0 = max(0, math.ceil((x0 - s.min_x) / s.pitch))
        i1 = min(s.nx - 1, math.floor((x1 - s.min_x) / s.pitch))
        j0 = max(0, math.ceil((z0 - s.min_z) / s.pitch))
        j1 = min(s.nz - 1, math.floor((z1 - s.min_z) / s.pitch))
        y0, y1 = tri[:, 1].min() - below, tri[:, 1].max() + above
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                col = j * s.nx + i
                for k in range(s.start[col], s.start[col + 1]):
                    if y0 <= y[k] <= y1:
                        out[k] = True
    return out


def stair_slope(s: navsample.Samples, mask: BoolArray, r: int = 1, tol: float = 0.7
                ) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Slope (degrees) from neighbouring sample heights on the same layer (central differences, +-r samples),
    for the surfaces in mask; 90 where a neighbour is missing. Returns (slope, gradient x, gradient z). (Measured over
    +-0.8 m at pitch 0.2 it recognised far more stairs, but with a 0.5 m step on them it joined decks and ledges to
    streets - over-connection 2.2 -> 35.3 % on ONS_MP_002; not adopted, 2026-10-21.)"""
    y = s.y.astype(float)
    column_of = _column_of(s)
    out = np.full(len(y), 90.0)
    grad_x = np.zeros(len(y))
    grad_z = np.zeros(len(y))

    def layer(i: int, j: int, yref: float) -> float | None:
        if not (0 <= i < s.nx and 0 <= j < s.nz):
            return None
        col = j * s.nx + i
        best = None
        for k in range(s.start[col], s.start[col + 1]):
            if abs(y[k] - yref) <= tol and (best is None or abs(y[k] - yref) < abs(best - yref)):
                best = y[k]
        return best
    for k in np.nonzero(mask)[0]:
        col = column_of[k]
        i, j = col % s.nx, col // s.nx
        x_plus, x_minus = layer(i + r, j, y[k]), layer(i - r, j, y[k])
        z_plus, z_minus = layer(i, j + r, y[k]), layer(i, j - r, y[k])
        if x_plus is None and x_minus is None and z_plus is None and z_minus is None:
            continue

        # central difference, or one-sided where a neighbour is missing (narrow stairs)
        def diff(plus: float | None, minus: float | None) -> float:
            if plus is not None and minus is not None:
                return (plus - minus) / (2 * r * s.pitch)
            if plus is not None:
                return (plus - y[k]) / (r * s.pitch)
            if minus is not None:
                return (y[k] - minus) / (r * s.pitch)
            return 0.0
        grad_x[k] = diff(x_plus, x_minus)
        grad_z[k] = diff(z_plus, z_minus)
        out[k] = math.degrees(math.atan(math.hypot(grad_x[k], grad_z[k])))
    return out, grad_x, grad_z


def narrow_lips(s: navsample.Samples, base: BoolArray, cand: BoolArray, slope: float, width: int,
                rise: float | None = None, flat: float | None = None) -> BoolArray:
    """The cand surfaces (steep terrain) with a base surface (walkable) within width samples on both opposite sides
    along an axis or a diagonal - same layer, each step within 0.7 m - and a slope within the limit between those two
    (LIP_WIDTH). rise: the candidate also within this height of both of those (LIP_OBJECT_RISE); flat: both of those
    within this many degrees of level."""
    y = s.y.astype(float)
    column_of = _column_of(s)
    max_gradient = math.tan(math.radians(slope))
    out = np.zeros(len(y), bool)

    def side(i: int, j: int, yref: float, di: int, dj: int) -> tuple[int, int] | None:
        """(base surface, its distance in samples) in direction (di, dj), or None."""
        for dist in range(1, width + 1):
            ni, nj = i + di * dist, j + dj * dist
            if not (0 <= ni < s.nx and 0 <= nj < s.nz):
                return None
            col = nj * s.nx + ni
            best = None
            for n in range(s.start[col], s.start[col + 1]):
                if abs(y[n] - yref) <= 0.7 * dist and (best is None or abs(y[n] - yref) < abs(y[best] - yref)):
                    best = n
            if best is None:
                return None
            if base[best]:
                return best, dist
        return None
    for k in np.nonzero(cand)[0]:
        col = column_of[k]
        i, j = col % s.nx, col // s.nx
        for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1)):
            ahead = side(i, j, y[k], di, dj)
            if ahead is None:
                continue
            behind = side(i, j, y[k], -di, -dj)
            if behind is None:
                continue
            span = (ahead[1] + behind[1]) * s.pitch * (math.sqrt(2) if di and dj else 1)
            if rise is not None and (abs(y[k] - y[ahead[0]]) > rise or abs(y[k] - y[behind[0]]) > rise):
                continue
            if flat is not None and \
                    min(s.normal[ahead[0], 1], s.normal[behind[0], 1]) < math.cos(math.radians(flat)):
                continue
            if abs(y[ahead[0]] - y[behind[0]]) / span <= max_gradient:
                out[k] = True
                break
    return out


def matched_flags(s: navsample.Samples, other: navsample.Samples | None) -> npt.NDArray[np.uint8]:
    """Capsule flags of a second run over the same grid (e.g. a higher capsule), matched per column by height."""
    out = np.full(len(s.y), 1, dtype=np.uint8)
    if other is None:
        return out
    for col in range(s.nx * s.nz):
        other_surfaces = range(other.start[col], other.start[col + 1])
        for k in range(s.start[col], s.start[col + 1]):
            for n in other_surfaces:
                if abs(float(other.y[n]) - float(s.y[k])) < 0.05:
                    out[k] = other.flags[n]
                    break
    return out


def step_fits(dy: float, gk: Sequence[float], gn: Sequence[float], sk: bool, sn: bool, di: int, dj: int, pitch: float,
              step: float) -> bool:
    """Height change dy from surface k to its neighbour n (direction di, dj) fits the step rule: within step of the
    rise predicted by the mean local gradient - or, when one of them is a stair surface, by the stair's own
    incline (a stair sample next to a flat landing would otherwise halve the predicted rise of the last step).
    gk, gn: the two surfaces' gradients (x, z); sk, sn: whether each is a stair surface."""
    if abs(dy - pitch * ((gk[0] + gn[0]) / 2 * di + (gk[1] + gn[1]) / 2 * dj)) <= step:
        return True
    for grad, is_stair in ((gk, sk), (gn, sn)):
        if is_stair and abs(dy - pitch * (grad[0] * di + grad[1] * dj)) <= step:
            return True
    return False


LADDER_REACH = 1.5              # a ladder's ends: the walkable surface nearest the ladder within this (m, horizontally)
LADDER_FOOT = (-1.0, 1.0)       # ... at the foot: from 1 m below to 1 m above the ladder's bottom
LADDER_TOP = (-1.5, 0.5)        # ... at the top: 1.5 m below to 0.5 m above its top (its rails stick out over the deck)


def _bands(lad: dict[str, Any], reach: float) -> tuple[tuple[tuple[float, float], tuple[float, float]], float]:
    """A link's end height bands and reach: a ladder's (LADDER_FOOT, LADDER_TOP, the reach given), or a jump link's own
    (jumplinks1943: foot_band, top_band, reach - tight, so a link cannot take another floor)."""
    lo, hi = lad['lo'], lad['hi']
    foot_band, top_band = lad.get('foot_band', LADDER_FOOT), lad.get('top_band', LADDER_TOP)
    foot = (lo[1] + foot_band[0], lo[1] + foot_band[1])
    top = (hi[1] + top_band[0], hi[1] + top_band[1])
    return (foot, top), lad.get('reach', reach)


def ladder_ends(s: navsample.Samples, ok: BoolArray, keep: BoolArray, lad: dict[str, Any], reach: float = LADDER_REACH
                ) -> tuple[int | None, int | None]:
    """(foot surface, top surface) of a ladder {'lo', 'hi'} (ladders.json, from levelobjects1943.py): at each end the
    surface allowed by the rules (ok) nearest the ladder's box, within reach horizontally and the end's height band;
    kept ground first. None where there is none. A jump link (jumplinks1943) gives its own bands and reach."""
    lo, hi = lad['lo'], lad['hi']
    bands, reach = _bands(lad, reach)
    i0 = int(math.floor((lo[0] - reach - s.min_x) / s.pitch))
    i1 = int(math.ceil((hi[0] + reach - s.min_x) / s.pitch))
    j0 = int(math.floor((lo[2] - reach - s.min_z) / s.pitch))
    j1 = int(math.ceil((hi[2] + reach - s.min_z) / s.pitch))
    out = []
    for y0, y1 in bands:
        best = None
        for j in range(max(j0, 0), min(j1, s.nz - 1) + 1):
            for i in range(max(i0, 0), min(i1, s.nx - 1) + 1):
                x, z = s.min_x + i * s.pitch, s.min_z + j * s.pitch
                dist = math.hypot(max(lo[0] - x, 0, x - hi[0]), max(lo[2] - z, 0, z - hi[2]))
                if dist > reach:
                    continue
                col = j * s.nx + i
                for k in range(s.start[col], s.start[col + 1]):
                    if ok[k] and y0 <= s.y[k] <= y1:
                        key = (not keep[k], dist)
                        if best is None or key < best[0]:
                            best = (key, k)
        out.append(best[1] if best else None)
    return out[0], out[1]


LADDER_LOCAL = 3.0              # ladder_end_options: candidate ground is grouped by connection within this of the ladder


def ladder_end_options(s: navsample.Samples, keep: BoolArray, gx: FloatArray, gz: FloatArray, stair: BoolArray,
                       step: float, lad: dict[str, Any], reach: float = LADDER_REACH,
                       local: float = LADDER_LOCAL) -> tuple[list[int], list[int]]:
    """([foot surfaces], [top surfaces]) of a ladder: at each end, the kept surface nearest the ladder (within reach and
    the end's height band, as ladder_ends) of each piece of ground there that the step rule does not join near the
    ladder (within reach + local). A ladder can be stepped onto from any ground beside it: on Wake Island's towers the
    ground outside the tower's frame and the floor enclosed by it both lie within reach of the foot, and linking only
    the nearer (the enclosed floor) left the ladder out of the network."""
    lo, hi = lad['lo'], lad['hi']
    y = s.y
    bands, reach = _bands(lad, reach)
    pad = reach + local
    i0 = max(int(math.floor((lo[0] - pad - s.min_x) / s.pitch)), 0)
    i1 = min(int(math.ceil((hi[0] + pad - s.min_x) / s.pitch)), s.nx - 1)
    j0 = max(int(math.floor((lo[2] - pad - s.min_z) / s.pitch)), 0)
    j1 = min(int(math.ceil((hi[2] + pad - s.min_z) / s.pitch)), s.nz - 1)
    out = []
    for y0, y1 in bands:
        cand: list[tuple[float, int]] = []          # (distance, surface)
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                x, z = s.min_x + i * s.pitch, s.min_z + j * s.pitch
                dist = math.hypot(max(lo[0] - x, 0, x - hi[0]), max(lo[2] - z, 0, z - hi[2]))
                if dist > reach:
                    continue
                col = j * s.nx + i
                for k in range(s.start[col], s.start[col + 1]):
                    if keep[k] and y0 <= y[k] <= y1:
                        cand.append((dist, k))
        cand.sort()
        piece = {}                                  # surface -> piece id, by a flood limited to the window
        nearest = []                                # each piece's surface nearest the ladder
        for _dist, k0 in cand:
            if k0 in piece:
                continue
            piece_id = len(nearest)
            nearest.append(k0)
            piece[k0] = piece_id
            queue = deque([k0])
            while queue:
                k = queue.popleft()
                col = int(np.searchsorted(s.start, k, side='right')) - 1
                i, j = col % s.nx, col // s.nx
                for dj in (-1, 0, 1):
                    for di in (-1, 0, 1):
                        ni, nj = i + di, j + dj
                        if not (di or dj) or not (i0 <= ni <= i1 and j0 <= nj <= j1):
                            continue
                        ncol = nj * s.nx + ni
                        for n in range(s.start[ncol], s.start[ncol + 1]):
                            if n in piece or not keep[n]:
                                continue
                            if step_fits(y[n] - y[k], (gx[k], gz[k]), (gx[n], gz[n]), stair[k], stair[n], di, dj,
                                         s.pitch, step):
                                piece[n] = piece_id
                                queue.append(n)
        out.append(nearest)
    return out[0], out[1]


def walkable(s: navsample.Samples, forbid_tris: npt.NDArray[Any] | None = None, slope: float = 40.0, step: float = 0.28,
             terrain_seeds_only: bool = False, dynamic_tris: npt.NDArray[Any] | None = None,
             high_capsule: navsample.Samples | None = None, stairs: bool = True, return_gradients: bool = False,
             narrow_capsule: navsample.Samples | None = None, raised_bars: bool = False,
             stair_tris: npt.NDArray[Any] | None = None, exclude_tris: npt.NDArray[Any] | None = None,
             exclude_footprint_tris: npt.NDArray[Any] | None = None, forbid_tol: float = 0.08,
             extra_seeds: Iterable[Sequence[float]] | None = None, return_stairs: bool = False,
             force_walkable: BoolArray | None = None, force_unwalkable: BoolArray | None = None,
             unseeds: Iterable[Sequence[float]] | None = None, soldier_points: Sequence[Sequence[float]] | None = None,
             ladders: Sequence[dict[str, Any]] | None = None, anchors: Iterable[Sequence[float]] | None = None,
             headroom_band: tuple[float, float] | None = HEADROOM_BAND, lip_width: int | None = LIP_WIDTH,
             stats: dict[str, int] | None = None) -> Walkable:
    """Kept walkable surfaces (bool per surface) by rules 1-4, plus the structure rules:
      dynamic props (dynamic_tris) are absent for path data - their tops are not floor, and the ground under and
      right beside them is not blocked by them;
      stairs: object surfaces whose slope over neighbouring samples is 12..40 deg count as walkable, with the
      clearance of the high_capsule run (capsule from 0.6 m) instead of the normal one.
    Hand edits (navedits.py): force_walkable / force_unwalkable (bool per surface) override the rules before
    seeding; unseeds (points) drop the island each lies on after seeding.
    headroom_band, lip_width: HEADROOM_BAND and LIP_WIDTH unless given (None switches either rule off; a BF1943 build
    passes its soldier's band, navbuild.Settings). stats: a dict the rules' counts are added to (steep terrain
    'accepted' / 'candidates', 'lips', roof 'islands' / 'dropped' / 'surfaces'; module dicts until 2026-10-31)."""
    stats = {} if stats is None else stats
    y = s.y.astype(float)
    normal_y = np.maximum(s.normal[:, 1], 0.05)
    gx_all, gz_all = -s.normal[:, 0] / normal_y, -s.normal[:, 2] / normal_y
    stair = np.zeros(len(y), bool)
    terrain = (s.hit[:, 0] == TERRAIN_SHAPE_VTABLE) if s.hit is not None else np.ones(len(y), bool)
    clear = (s.flags & 3) == 0
    if s.hit is not None and CHARACTERS_ABSENT:
        character = s.hit[:, 0] == CHARACTER_SHAPE_VTABLE
    else:
        character = np.zeros(len(y), bool)
    if character.any():
        clear |= near_characters(s, character) & ((s.flags & 2) == 0)
    if soldier_points is not None and len(soldier_points) and CHARACTERS_ABSENT:
        # soldiers stood at their spawn points while the level was sampled; a downward ray only sees one where it hits
        # the top of his body (sp_006: Haggard's was missed, his spawn cell still blocked) - so the floor at every
        # soldier spawn point is not blocked by him either
        clear |= near_points(s, soldier_points) & ((s.flags & 2) == 0)
    if dynamic_tris is not None and len(dynamic_tris):
        clear |= near_objects(s, dynamic_tris) & ((s.flags & 2) == 0)
    if exclude_tris is not None and len(exclude_tris):
        # excluded objects do not block clearance; their upper parts are not floor
        clear |= near_objects(s, exclude_tris) & ((s.flags & 2) == 0)
    ok = clear & (s.normal[:, 1] >= math.cos(math.radians(slope)) - 0.01) & ~character
    if STEEP_TERRAIN:
        steep = terrain & clear & ~character & ~ok & (s.normal[:, 1] >= math.cos(math.radians(STEEP_TERRAIN_MAX)))
        if steep.any():
            span = max(1, int(round(STEEP_TERRAIN_SPAN / s.pitch)))       # in samples
            steep_deg, steep_gx, steep_gz = stair_slope(s, steep, r=span, tol=max(0.7, 1.2 * span * s.pitch))
            accept = steep & (steep_deg <= slope)
            ok |= accept
            # the link rule predicts steps from these gradients: the heights', not the steep normals'
            gx_all = np.where(accept, steep_gx, gx_all)
            gz_all = np.where(accept, steep_gz, gz_all)
            stats['accepted'] = stats.get('accepted', 0) + int(accept.sum())
            stats['candidates'] = stats.get('candidates', 0) + int(steep.sum())
    if lip_width and s.pitch < 0.5:
        steep_cand = clear & ~character & ~ok & (s.normal[:, 1] >= math.cos(math.radians(STEEP_TERRAIN_MAX)))
        lips = narrow_lips(s, ok, terrain & steep_cand, slope, lip_width)
        if LIP_OBJECT_RISE is not None:
            # on objects any steepness: the height limit alone keeps it to sills (an aircraft door sill's sides are
            # steeper than 55 deg - sp_009's aft cabin stayed cut off under the terrain cap)
            object_cand = ~terrain & clear & ~character & ~ok
            lips |= narrow_lips(s, ok, object_cand & steep_cand, slope, lip_width, rise=LIP_OBJECT_RISE)
            if not LIP_OBJECT_SLOPE_MAX:
                # steeper than STEEP_TERRAIN_MAX: only between flat floors (a door sill's sides; a rock mesh meeting
                # sloped terrain is not a sill - sp_007's cliff top joined the level through one, 2026-10-22)
                lips |= narrow_lips(s, ok, object_cand & ~steep_cand, slope, lip_width, rise=LIP_OBJECT_RISE,
                                    flat=LIP_OBJECT_FLAT)
        if lips.any():
            _lip_deg, lip_gx, lip_gz = stair_slope(s, lips)
            ok |= lips
            gx_all = np.where(lips, lip_gx, gx_all)
            gz_all = np.where(lips, lip_gz, gz_all)
        stats['lips'] = stats.get('lips', 0) + int(lips.sum())
    on_stair = np.zeros(len(y), bool)
    if stair_tris is not None and len(stair_tris):
        # staircase objects: surfaces on / right beside them are not blocked by the staircase itself
        on_stair = near_objects(s, stair_tris, grow=0.4, below=0.6, above=0.6) & ~terrain & ((s.flags & 2) == 0)
    if stairs:
        cand = ~terrain & ((s.flags & 2) == 0)
        stair_deg, stair_gx, stair_gz = stair_slope(s, cand)
        stair = cand & (stair_deg >= 12) & (stair_deg <= slope)
        stair_obj = on_stair & (stair_deg <= 45)
        ok |= stair_obj
        stair |= stair_obj & (stair_deg >= 12)
        high_flags = matched_flags(s, high_capsule) if high_capsule is not None else s.flags
        # landings: object surfaces within two samples of a stair surface on the same level also take the
        # high-capsule clearance (the normal capsule touches the top steps / rail ends there)
        landing = np.zeros(len(y), bool)
        column_of_landing = _column_of(s)
        for k in np.nonzero(stair)[0]:
            col = column_of_landing[k]
            i, j = col % s.nx, col // s.nx
            for dj in range(-2, 3):
                for di in range(-2, 3):
                    ni, nj = i + di, j + dj
                    if 0 <= ni < s.nx and 0 <= nj < s.nz:
                        ncol = nj * s.nx + ni
                        for n in range(s.start[ncol], s.start[ncol + 1]):
                            if cand[n] and abs(y[n] - y[k]) <= 0.9:
                                landing[n] = True
        level_landing = landing & (s.normal[:, 1] >= math.cos(math.radians(slope)) - 0.01)
        ok |= (stair | level_landing) & ((high_flags & 3) == 0)
        # on stairs the treads are flat: take the incline from the neighbour heights, not the tread normals
        gx_all = np.where(stair, stair_gx, gx_all)
        gz_all = np.where(stair, stair_gz, gz_all)
    on_forbid = on_objects(s, forbid_tris, tol=forbid_tol)
    slope_ok = s.normal[:, 1] >= math.cos(math.radians(slope)) - 0.01
    column_of_rules = _column_of(s)

    def same_layer(i: int, j: int, yref: float, tol: float = 0.7) -> list[int]:
        if not (0 <= i < s.nx and 0 <= j < s.nz):
            return []
        col = j * s.nx + i
        return [n for n in range(s.start[col], s.start[col + 1]) if abs(y[n] - yref) <= tol]
    if raised_bars:
        # a forbidden object's raised bar (barrier arm, low overhang) 0.7..1.9 m above the ground within one
        # sample does not block clearance there; the bar itself stays forbidden (on_forbid)
        bar = np.zeros(len(y), bool)
        for k in np.nonzero(on_forbid & ~terrain)[0]:
            col = column_of_rules[k]
            i, j = col % s.nx, col // s.nx
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < s.nx and 0 <= nj < s.nz):
                        continue
                    ncol = nj * s.nx + ni
                    for n in range(s.start[ncol], s.start[ncol + 1]):
                        if 0.7 <= y[k] - y[n] <= 1.9 and (s.flags[n] & 2) == 0:
                            bar[n] = True
        ok |= bar & slope_ok & ~on_forbid
    if narrow_capsule is not None:
        # narrow passages: blocked for the normal capsule, clear for the narrow one, with walkable floor on the
        # same level on opposite sides (left/right or front/back)
        narrow_flags = matched_flags(s, narrow_capsule)
        base = ok.copy()
        for k in np.nonzero(~base & ((narrow_flags & 3) == 0) & slope_ok & ((s.flags & 2) == 0) & ~on_forbid)[0]:
            col = column_of_rules[k]
            i, j = col % s.nx, col // s.nx
            for (side1, side2) in (((1, 0), (-1, 0)), ((0, 1), (0, -1)), ((1, 1), (-1, -1)), ((1, -1), (-1, 1))):
                if any(base[n] for n in same_layer(i + side1[0], j + side1[1], y[k])) and \
                        any(base[n] for n in same_layer(i + side2[0], j + side2[1], y[k])):
                    ok[k] = True
                    break
    ok &= ~on_forbid
    if dynamic_tris is not None and len(dynamic_tris):
        on_dynamic = on_objects(s, dynamic_tris)
    else:
        on_dynamic = np.zeros(len(y), bool)
    if on_dynamic.any() or (dynamic_tris is not None and len(dynamic_tris)):
        # steep hits grazing a dynamic object's vertical faces (a closed door leaf standing on a sample row: the rays
        # record its faces 0.8-1.5 m above the floor - no triangle lies under them, so on_objects misses them, and as
        # a 'ceiling' they dropped the floor in sp_009's aircraft doorways) belong to the object too
        on_dynamic |= near_objects(s, dynamic_tris, grow=0.1, below=-0.05, above=0.0) & ~slope_ok
    ok &= ~on_dynamic
    if exclude_tris is not None and len(exclude_tris):
        ok &= ~on_objects(s, exclude_tris)
    if exclude_footprint_tris is not None and len(exclude_footprint_tris):
        # the footprint of an excluded object (trunk base, fence posts and boards) stays a barrier
        ok &= ~near_objects(s, exclude_footprint_tris, grow=0.3, below=1.0, above=0.5)
    if headroom_band is not None:
        not_ceiling = (character | on_dynamic) if DYNAMIC_ABSENT_HEADROOM else character
        ok &= ~low_headroom(s, not_ceiling, *headroom_band)
    if DYNAMIC_FLOOR is not None and on_dynamic.any():
        base = ok.copy()
        for k in np.nonzero(on_dynamic & ((s.flags & 2) == 0) & slope_ok & ~on_forbid)[0]:
            col = column_of_rules[k]
            i, j = col % s.nx, col // s.nx
            # not where the column has its own floor just under the prop
            if any(not on_dynamic[n] and 0 < y[k] - y[n] <= DYNAMIC_FLOOR
                   for n in range(s.start[col], s.start[col + 1])):
                continue
            if any(base[n] for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj
                   for n in same_layer(i + di, j + dj, y[k], DYNAMIC_FLOOR)):
                ok[k] = True
    ok &= ~character                    # last, before hand edits: the stair / landing / bar rules above re-added 68 of
                                        # sp_004_b's 468 soldier surfaces as floor (2026-10-19)
    if force_walkable is not None:
        ok |= force_walkable
    if force_unwalkable is not None:
        ok &= ~force_unwalkable
    column_of = _column_of(s)
    per_i = {i for i in range(s.nx) if round((s.min_x + i * s.pitch) % SECTOR, 1) in (1.6, 98.4)}
    per_j = {j for j in range(s.nz) if round((s.min_z + j * s.pitch) % SECTOR, 1) in (1.6, 98.4)}
    keep = np.zeros(len(y), bool)
    queue = deque()
    for k in np.nonzero(ok & terrain if terrain_seeds_only else ok)[0]:
        col = column_of[k]
        if col % s.nx in per_i or col // s.nx in per_j:
            keep[k] = True
            queue.append(k)
    for (seed_x, seed_y, seed_z) in (extra_seeds or ()):
        # level seed points (soldier spawns): the walkable surface at or just below the point
        i, j = int(round((seed_x - s.min_x) / s.pitch)), int(round((seed_z - s.min_z) / s.pitch))
        if not (0 <= i < s.nx and 0 <= j < s.nz):
            continue
        col = j * s.nx + i
        cand = [n for n in range(s.start[col], s.start[col + 1]) if ok[n] and -1.5 <= y[n] - seed_y <= 0.5]
        if cand:
            n = min(cand, key=lambda n: abs(y[n] - seed_y))
            if not keep[n]:
                keep[n] = True
                queue.append(n)

    def flood() -> None:
        while queue:
            k = queue.popleft()
            col = column_of[k]
            i, j = col % s.nx, col // s.nx
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    ni, nj = i + di, j + dj
                    if not (di or dj) or not (0 <= ni < s.nx and 0 <= nj < s.nz):
                        continue
                    ncol = nj * s.nx + ni
                    for n in range(s.start[ncol], s.start[ncol + 1]):
                        if keep[n] or not ok[n]:
                            continue
                        if step_fits(y[n] - y[k], (gx_all[k], gz_all[k]), (gx_all[n], gz_all[n]), stair[k], stair[n],
                                     di, dj, s.pitch, step):
                            keep[n] = True
                            queue.append(n)
    flood()
    if ladders:
        # ladder links: a ladder whose one end stands on kept ground seeds the walkable surface at its other end (a roof
        # or deck reached only by the ladder - no step joins it, so no seed reached it); repeated until no ladder adds
        # ground (ladders in a chain). ladder_ends gives the end surfaces.
        done = set()
        changed = True
        while changed:
            changed = False
            for ladder_index, lad in enumerate(ladders):
                if ladder_index in done:
                    continue
                foot, top = ladder_ends(s, ok, keep, lad)
                foot_kept, top_kept = foot is not None and keep[foot], top is not None and keep[top]
                far = top if foot_kept and not top_kept else foot if top_kept and not foot_kept else None
                if far is None:
                    if foot_kept and top_kept:
                        done.add(ladder_index)
                    continue
                keep[far] = True
                queue.append(far)
                flood()
                done.add(ladder_index)
                changed = True
    for (seed_x, seed_y, seed_z) in (unseeds or ()):
        # drop the island the point is on: the kept surface at or just below it, and all kept surfaces it
        # connects to by the same step rule
        i, j = int(round((seed_x - s.min_x) / s.pitch)), int(round((seed_z - s.min_z) / s.pitch))
        if not (0 <= i < s.nx and 0 <= j < s.nz):
            continue
        col = j * s.nx + i
        cand = [n for n in range(s.start[col], s.start[col + 1]) if keep[n] and -1.5 <= y[n] - seed_y <= 0.5]
        if not cand:
            continue
        first = min(cand, key=lambda n: abs(y[n] - seed_y))
        drop = {first}
        queue = deque([first])
        while queue:
            k = queue.popleft()
            col = column_of[k]
            i, j = col % s.nx, col // s.nx
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    ni, nj = i + di, j + dj
                    if not (di or dj) or not (0 <= ni < s.nx and 0 <= nj < s.nz):
                        continue
                    ncol = nj * s.nx + ni
                    for n in range(s.start[ncol], s.start[ncol + 1]):
                        if n in drop or not keep[n]:
                            continue
                        if step_fits(y[n] - y[k], (gx_all[k], gz_all[k]), (gx_all[n], gz_all[n]), stair[k], stair[n],
                                     di, dj, s.pitch, step):
                            drop.add(n)
                            queue.append(n)
        keep[list(drop)] = False
    if ROOF_PRUNE:
        anchor_k = set()
        for (seed_x, seed_y, seed_z) in (anchors or ()):
            i, j = int(round((seed_x - s.min_x) / s.pitch)), int(round((seed_z - s.min_z) / s.pitch))
            if 0 <= i < s.nx and 0 <= j < s.nz:
                col = j * s.nx + i
                cand = [n for n in range(s.start[col], s.start[col + 1]) if keep[n] and -2.5 <= y[n] - seed_y <= 2.5]
                if cand:
                    anchor_k.add(min(cand, key=lambda n: abs(y[n] - seed_y)))
        for lad in (ladders or ()):
            for end in ladder_ends(s, ok, keep, lad):
                if end is not None and keep[end]:
                    anchor_k.add(int(end))
        drop = roof_islands(s, keep, gx_all, gz_all, stair, step, terrain, anchor_k, stats)
        keep &= ~drop
    if return_gradients:
        return (keep, gx_all, gz_all, stair) if return_stairs else (keep, gx_all, gz_all)
    return keep


def islands_of(s: navsample.Samples, keep: BoolArray, gx: FloatArray, gz: FloatArray, stair: BoolArray,
               step: float) -> tuple[npt.NDArray[np.int64], int]:
    """Island id per kept surface (-1 elsewhere): surfaces joined by the step rule, as walkable's flood joins them."""
    y = s.y.astype(float)
    column_of = _column_of(s)
    island = np.full(len(y), -1, np.int64)
    count = 0
    for k0 in np.nonzero(keep)[0]:
        if island[k0] >= 0:
            continue
        island[k0] = count
        queue: deque[int | np.intp] = deque([k0])
        while queue:
            k = queue.popleft()
            col = column_of[k]
            i, j = col % s.nx, col // s.nx
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    ni, nj = i + di, j + dj
                    if not (di or dj) or not (0 <= ni < s.nx and 0 <= nj < s.nz):
                        continue
                    ncol = nj * s.nx + ni
                    for n in range(s.start[ncol], s.start[ncol + 1]):
                        if island[n] >= 0 or not keep[n]:
                            continue
                        if step_fits(y[n] - y[k], (gx[k], gz[k]), (gx[n], gz[n]), stair[k], stair[n], di, dj, s.pitch,
                                     step):
                            island[n] = count
                            queue.append(n)
        count += 1
    return island, count


def roof_islands(s: navsample.Samples, keep: BoolArray, gx: FloatArray, gz: FloatArray, stair: BoolArray, step: float,
                 terrain: BoolArray, anchor_k: set[int], stats: dict[str, int]) -> BoolArray:
    """The kept surfaces ROOF_PRUNE drops (bool per surface); the counts are added to stats."""
    y = s.y.astype(float)
    island, count = islands_of(s, keep, gx, gz, stair, step)
    out = np.zeros(len(y), bool)
    if count < 2:
        return out
    column_of = _column_of(s)
    # each column's lowest surface, over the non-empty columns only (reduceat on an empty column's start would read
    # the next column's first surface, and clipping the starts cut the last column short when empty ones follow it)
    full = np.diff(s.start) > 0
    lowest = np.full(s.nx * s.nz, np.inf)
    if len(y):
        lowest[full] = np.minimum.reduceat(y, s.start[:-1][full])
    lowest = lowest[column_of]
    raised = keep & ~terrain & (y - lowest >= ROOF_RAISE)
    kept_island = island[keep]
    size = np.bincount(kept_island, minlength=count)
    raised_share = np.bincount(island[raised], minlength=count) / np.maximum(size, 1)
    anchored = np.zeros(count, bool)
    anchored[[island[k] for k in anchor_k if island[k] >= 0]] = True
    anchored[int(np.argmax(size))] = True
    cand = ~anchored & (raised_share >= ROOF_SHARE)
    if not cand.any():
        stats['islands'] = stats.get('islands', 0) + count
        return out
    # anchored ground near each candidate at its own height: buckets of ROOF_NEAR m; candidate surfaces every ~0.8 m
    anchored_k = np.nonzero(keep & anchored[np.maximum(island, 0)] & (island >= 0))[0]
    anchored_x = s.min_x + (column_of[anchored_k] % s.nx) * s.pitch
    anchored_z = s.min_z + (column_of[anchored_k] // s.nx) * s.pitch
    buckets = defaultdict(list)
    for x, z, ground_y in zip(anchored_x.tolist(), anchored_z.tolist(), y[anchored_k].tolist()):
        buckets[(int(math.floor(x / ROOF_NEAR)), int(math.floor(z / ROOF_NEAR)))].append((x, z, ground_y))
    every = max(1, int(round(0.8 / s.pitch)))
    near = np.zeros(count, bool)
    for k in np.nonzero(keep & cand[np.maximum(island, 0)] & (island >= 0))[0]:
        cand_island = island[k]
        if near[cand_island]:
            continue
        col = column_of[k]
        i, j = col % s.nx, col // s.nx
        if i % every or j % every:
            continue
        x, z = s.min_x + i * s.pitch, s.min_z + j * s.pitch
        bucket_x, bucket_z = int(math.floor(x / ROOF_NEAR)), int(math.floor(z / ROOF_NEAR))
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for (px, pz, py) in buckets.get((bucket_x + dx, bucket_z + dz), ()):
                    if abs(py - y[k]) <= ROOF_NEAR_DY and math.hypot(px - x, pz - z) <= ROOF_NEAR:
                        near[cand_island] = True
                        break
                if near[cand_island]:
                    break
            if near[cand_island]:
                break
    dropped = cand & ~near
    out = keep & (island >= 0) & dropped[np.maximum(island, 0)]
    stats['islands'] = stats.get('islands', 0) + count
    stats['dropped'] = stats.get('dropped', 0) + int(dropped.sum())
    stats['surfaces'] = stats.get('surfaces', 0) + int(out.sum())
    return out


def score(s: navsample.Samples, keep: BoolArray, hpd: str, tol: float = 0.6,
          y_offset: float = OUTLINE_Y_OFFSET) -> dict[str, float]:
    """Against the reference: DICE layers (cells fully inside an area) found exactly / within one cell, and
    kept surfaces with no reference layer at that height in the cell or its neighbours. y_offset: the reference's
    outline height above the ground."""
    reference = {col: [(lo - y_offset, hi - y_offset, cover) for lo, hi, cover in layers]
                 for col, layers in navsample.reference_cells(s, hpd).items()}
    y = s.y.astype(float)
    column_of = _column_of(s)

    def kept_near(col: int, lo: float, hi: float, radius: int) -> bool:
        i, j = col % s.nx, col // s.nx
        for dj in range(-radius, radius + 1):
            for di in range(-radius, radius + 1):
                ni, nj = i + di, j + dj
                if 0 <= ni < s.nx and 0 <= nj < s.nz:
                    ncol = nj * s.nx + ni
                    if any(keep[n] and lo - tol <= y[n] <= hi + tol for n in range(s.start[ncol], s.start[ncol + 1])):
                        return True
        return False

    full = found = near = 0
    for col, layers in reference.items():
        for lo, hi, cover in layers:
            if cover < 1:
                continue
            full += 1
            if kept_near(col, lo, hi, 0):
                found += 1
            elif kept_near(col, lo, hi, 1):
                near += 1
    extra = far = 0
    for k in np.nonzero(keep)[0]:
        col = column_of[k]
        if any(lo - tol <= y[k] <= hi + tol for lo, hi, _ in reference.get(col, [])):
            continue
        extra += 1
        i, j = col % s.nx, col // s.nx
        if not any(lo - tol <= y[k] <= hi + tol for di in (-1, 0, 1) for dj in (-1, 0, 1)
                   if 0 <= i + di < s.nx and 0 <= j + dj < s.nz
                   for lo, hi, _ in reference.get((j + dj) * s.nx + i + di, [])):
            far += 1
    within = 100 - ((full - found - near) + far) / max(full + far, 1) * 100
    per = 100.0 / max(full, 1)                   # no reference cell inside the samples' box: shares of nothing are 0
    return {'reference': full, 'found': found * per, 'found_near': (found + near) * per,
            'extra': extra * per, 'extra_far': far * per, 'within_one_cell': within}


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[1] != 'score':
        print(__doc__)
        return 1
    args = [arg for arg in argv[2:] if not arg.startswith('--')]
    opts = dict(zip(argv[2:], argv[3:]))
    f, _ = navsample.load(args[0])
    s = coarse(f) if abs(f.pitch - PITCH) > 1e-3 else f
    tris = np.load(args[2]) if len(args) > 2 else None
    keep = cast(BoolArray, walkable(s, tris, float(opts.get('--slope', 40)), float(opts.get('--step', 0.28))))
    result = score(s, keep, args[1])
    print(f"reference layers {result['reference']}: found {result['found']:.1f}% "
          f"(within 0.8 m {result['found_near']:.1f}%), extra {result['extra']:.1f}% "
          f"(far {result['extra_far']:.1f}%); agreement within 0.8 m: {result['within_one_cell']:.2f}%")
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navwalk', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
