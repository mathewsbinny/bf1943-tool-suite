"""NavKit (big-endian): collision samples of a BF1943 level computed from its data, without the game.

BFBC2's NavKit samples a level in game, through OPFOR's [dev] navsample option (DICE's physics queries). BF1943 has
no such sampler, so this computes the same columns from the level's terrain height field and the world-space
collision written by levelobjects1943.py (all_tris.npy), and writes them in navsample's format (version 1), which
the navbuild package reads unchanged:

  per column (pitch 0.8 m, j-major): every surface a downward ray meets - the terrain, and each upward-facing
  collision triangle over the column (height from its plane, normal from its winding or the terrain gradient);
  per surface: BLOCKED when the soldier capsule meets collision: a triangle within the radius whose height range there
  reaches from step height to head height over the surface (a wall: its whole height; a floor or slope beside the
  column: its plane height +- slope x radius), or a non-wall triangle over the column itself between just above the
  surface and head height (a ceiling, a slab, a platform: collision is two-sided, so undersides are surfaces too);
  also when the surface lies more than UNDERGROUND under the terrain (a model's buried foundations).

Usage: navsample1943.py <level dir> <heightfield.res> <out samples.bin> [--step 0.37] [--radius 0.35]
                        [--height 1.8] [--pitch 0.8] [--margin 20] [--sea-level 215] [--sectors=XOFF,ZOFF:XXZZ,...]
  The capsule defaults are BF1943's soldier (soldier1943.py); NavKit's high-capsule run uses --step 0.6.
  <level dir> holds levelobjects1943.py's files; the sampling box is the collision's extent plus the land (terrain
  above the lowest collision), with a margin, on whole multiples of the pitch.

Differences from in-game sampling (approximations, to be measured against BFBC2 levels): a vertical ray instead of
the game's sphere cast, and a capsule test on the triangles' height ranges instead of an exact capsule-triangle
distance.
"""
import argparse
import math
import os
import struct
import sys
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import numpy.typing as npt

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
from bfbc2core import terrain  # noqa: E402

if TYPE_CHECKING:
    from bfbc2core.terrain import Heightfield

type Log = Callable[[str], object]
type FloatArray = npt.NDArray[np.float64]
type BoolArray = npt.NDArray[np.bool]
type Box = tuple[npt.NDArray[np.floating], npt.NDArray[np.floating]]     # (min (x, z), max (x, z))
type Sectors = tuple[tuple[int, int], Sequence[tuple[int, int]]]         # ((xoff, zoff), [(X, Z)])

HEADER = struct.Struct('<4sI3f2i6fI')
BLOCKED = 1
TILE = 32                        # columns per tile side
UP_MIN = 0.05                    # a triangle whose normal has y above this is a floor a downward ray meets
FLOOR_MIN = 0.3                  # flatter than this (normal y): height range from its plane; steeper: a wall
MERGE = 0.05                     # surfaces closer than this in one column are one surface
OVERHEAD_MIN = 0.05              # a triangle over the column this far above a surface leaves no headroom there
UNDERGROUND = 0.5                # a surface this far under the terrain is buried (a model's foundations: Wake's guard
                                 # towers stand 3.9 m deep), not floor; the margin is for the heightfield's own error


def normals(tris: npt.NDArray[np.floating]) -> tuple[npt.NDArray[np.float32], BoolArray]:
    """Unit normals of triangles (N, 3, 3), each turned to face up, and whether each triangle has an area."""
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    normal = normal / np.maximum(length, 1e-12)
    flip = normal[:, 1] < 0                  # both windings occur: a floor is the side facing up
    normal[flip] *= -1
    return normal.astype(np.float32), length[:, 0] > 1e-9


def tile_bins(tris: npt.NDArray[np.floating], x0: float, z0: float, pitch: float, ntx: int, ntz: int,
              pad: float) -> dict[int, npt.NDArray[np.intp]]:
    """{tile index: triangle indices} for every tile a triangle's bounding box (grown by pad) touches."""
    lo, hi = tris[:, :, [0, 2]].min(1) - pad, tris[:, :, [0, 2]].max(1) + pad
    size = TILE * pitch
    tile_lo = np.floor((lo - [x0, z0]) / size).astype(np.int64)        # each triangle's first and last tile (x, z)
    tile_hi = np.floor((hi - [x0, z0]) / size).astype(np.int64)
    tile_lo = np.clip(tile_lo, 0, [ntx - 1, ntz - 1])
    tile_hi = np.clip(tile_hi, 0, [ntx - 1, ntz - 1])
    keep = (hi[:, 0] >= x0) & (hi[:, 1] >= z0) & (lo[:, 0] <= x0 + ntx * size) & (lo[:, 1] <= z0 + ntz * size)
    bins: dict[int, list[npt.NDArray[np.intp]]] = {}
    idx = np.nonzero(keep)[0]
    span = (tile_hi[idx, 0] - tile_lo[idx, 0] + 1) * (tile_hi[idx, 1] - tile_lo[idx, 1] + 1)
    one = idx[span == 1]                     # most triangles touch one tile: grouped in one go
    key = tile_lo[one, 1] * ntx + tile_lo[one, 0]
    order = np.argsort(key, kind='stable')
    keys, starts = np.unique(key[order], return_index=True)
    for k, group in zip(keys, np.split(one[order], starts[1:])):
        bins.setdefault(int(k), []).append(group)
    for i in idx[span > 1]:                  # the rest, tile by tile
        for tz in range(tile_lo[i, 1], tile_hi[i, 1] + 1):
            for tx in range(tile_lo[i, 0], tile_hi[i, 0] + 1):
                bins.setdefault(int(tz * ntx + tx), []).append(np.array([i]))
    return {t: np.concatenate(v) for t, v in bins.items()}


FLAT_FROM_ABOVE = 1e-6           # a triangle whose sides meet at under about 0.06 deg seen from above (sin^2 of the
                                 # angle) is a line there: a vertical wall


def inside(tris_xz: npt.NDArray[np.floating], px: npt.ArrayLike, pz: npt.ArrayLike
           ) -> tuple[FloatArray, FloatArray, FloatArray, BoolArray]:
    """Barycentric coordinates (u, v, w) of points (P,) in triangles (N, 3, 2) -> (N, P) each, and the inside mask.
    A triangle that is a line seen from above (a vertical wall) contains no point: its edges still count in dist2d.
    That is judged relative to its size, in float64: float32 rounding at level coordinates leaves such a wall's
    determinant far above any fixed epsilon, and the coordinates then put far-away points inside it."""
    tris_xz = tris_xz.astype(np.float64)
    # the corners a, b, c and the points p; the usual dot-product form of the barycentric coordinates
    a, b, c = tris_xz[:, 0, None, :], tris_xz[:, 1, None, :], tris_xz[:, 2, None, :]
    p = np.stack([px, pz], -1).astype(np.float64)[None]
    v0, v1, v2 = b - a, c - a, p - a
    d00 = (v0 * v0).sum(-1)
    d01 = (v0 * v1).sum(-1)
    d11 = (v1 * v1).sum(-1)
    d20 = (v2 * v0).sum(-1)
    d21 = (v2 * v1).sum(-1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) <= FLAT_FROM_ABOVE * d00 * d11, np.nan, den)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    u = 1 - v - w
    return u, v, w, (u >= -1e-6) & (v >= -1e-6) & (w >= -1e-6)


def dist2d(tris_xz: npt.NDArray[np.floating], px: npt.ArrayLike, pz: npt.ArrayLike) -> FloatArray:
    """Distance from points (P,) to triangles (N, 3, 2) in the horizontal plane -> (N, P)."""
    _, _, _, ins = inside(tris_xz, px, pz)
    p = np.stack([px, pz], -1)[None]
    best = None
    for k in range(3):
        # edge k from corner a to corner b; t: the nearest point's position along it (0 at a, 1 at b)
        a, b = tris_xz[:, k, None, :], tris_xz[:, (k + 1) % 3, None, :]
        ab = b - a
        t = np.clip(((p - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
        d = np.linalg.norm(p - (a + t[..., None] * ab), axis=-1)
        best = d if best is None else np.minimum(best, d)
    return np.where(ins, 0.0, cast(FloatArray, best))        # set by the loop's first edge


ISLAND_FOOTPRINT = 100.0          # m2: an object rising above the sea and at least this big is ground (rock islets,
                                 # wrecks, piers); buoys are a few m2


def box_from_level(level_dir: str, hf: 'Heightfield', sea_level: float, log: Log = print) -> Box | None:
    """(min (x, z), max (x, z)) of what can be walked on: the land (terrain above sea_level - 1.5 m, every islet
    included), the whole collision box of each object holding a soldier spawn (the carriers, whatever their length),
    and objects rising above the sea with a footprint of at least ISLAND_FOOTPRINT. None when there is none of these
    (2026-10-30 review: no terrain above the sea raised ValueError on an empty array)."""
    import json
    land = hf.heights > sea_level - 1.5
    cx, cz = hf.cell                                 # the height field's cell size
    if land.any():
        jj, ii = np.nonzero(land)
        lo = np.array([hf.min[0] + ii.min() * cx, hf.min[2] + jj.min() * cz])
        hi = np.array([hf.min[0] + (ii.max() + 1) * cx, hf.min[2] + (jj.max() + 1) * cz])
        log(f'box: land x {lo[0]:.0f}..{hi[0]:.0f}, z {lo[1]:.0f}..{hi[1]:.0f}')
    else:
        lo, hi = np.array([np.inf, np.inf]), np.array([-np.inf, -np.inf])
        log(f'box: no terrain above the sea ({sea_level:g} m)')
    with open(os.path.join(level_dir, 'soldier_spawns.json'), encoding='utf-8') as f:
        spawns = json.load(f)
    with open(os.path.join(level_dir, 'object_boxes.json'), encoding='utf-8') as f:
        boxes = json.load(f)
    for box in boxes:
        bmin, bmax = np.array(box['min'])[[0, 2]], np.array(box['max'])[[0, 2]]
        holds = any(bmin[0] - 5 <= spawn[0] <= bmax[0] + 5 and bmin[1] - 5 <= spawn[2] <= bmax[1] + 5
                    for spawn in spawns)
        islet = box['max'][1] > sea_level + 0.5 and box['footprint'] >= ISLAND_FOOTPRINT
        if (holds or islet) and (bmin < lo).any() | (bmax > hi).any():
            log(f'box: + {box["bp"].rsplit("/", 1)[-1]} ({"holds a spawn" if holds else "islet"}, '
                f'x {bmin[0]:.0f}..{bmax[0]:.0f}, z {bmin[1]:.0f}..{bmax[1]:.0f})')
            lo, hi = np.minimum(lo, bmin), np.maximum(hi, bmax)
    return (lo, hi) if (lo <= hi).all() else None


def extent_box(tris: npt.NDArray[np.floating], hf: 'Heightfield') -> Box:
    """(min (x, z), max (x, z)): the collision's extent and the land (terrain above the lowest collision; all of it
    without collision). ValueError when there is neither (2026-10-30 review: an empty collision raised on an empty
    array)."""
    lo, hi = np.array([np.inf, np.inf]), np.array([-np.inf, -np.inf])
    if len(tris):
        lo, hi = tris[:, :, [0, 2]].reshape(-1, 2).min(0), tris[:, :, [0, 2]].reshape(-1, 2).max(0)
    land = hf.heights > tris[:, :, 1].min() if len(tris) else np.ones_like(hf.heights, bool)
    if land.any():
        jj, ii = np.nonzero(land)
        cx, cz = hf.cell                             # the height field's cell size
        lo = np.minimum(lo, [hf.min[0] + ii.min() * cx, hf.min[2] + jj.min() * cz])
        hi = np.maximum(hi, [hf.min[0] + ii.max() * cx, hf.min[2] + jj.max() * cz])
    if not (lo <= hi).all():
        raise ValueError('nothing to sample: no collision and no terrain')
    return lo, hi


# BF1943's soldier (soldier1943.py): the capsule each surface is tested with
from soldier1943 import HEIGHT, RADIUS, STEP, STEP_HEIGHT, STEP_TOLERANCE  # noqa: E402,F401


def sample(level_dir: str, hf_path: str, out: str, step: float = STEP, radius: float = RADIUS, height: float = HEIGHT,
           pitch: float = 0.8, margin: float = 20.0, sea_level: float | None = None, sectors: Sectors | None = None,
           log: Log = print) -> str:
    """sectors: None, or (offsets (xoff, zoff), [(X, Z)]) - sample only tiles touching those 100 m sectors (plus 1 m,
    the builder's window overlap); the rest of the grid is written as empty columns (the detailed run at pitch 0.2)."""
    t_start = time.time()
    triangles = np.load(os.path.join(level_dir, 'all_tris.npy')).astype(np.float64)
    hf = terrain.read(hf_path)
    tri_normal, has_area = normals(triangles)
    triangles, tri_normal = triangles[has_area], tri_normal[has_area]
    box = None
    if sea_level is not None and os.path.isfile(os.path.join(level_dir, 'object_boxes.json')):
        box = box_from_level(level_dir, hf, sea_level, log)
    if box is None:
        # the collision's extent, and the land (terrain above the lowest collision)
        box = extent_box(triangles, hf)
    lo, hi = box
    lo = np.maximum(lo - margin, [hf.min[0], hf.min[2]])
    hi = np.minimum(hi + margin, [hf.max[0] - pitch, hf.max[2] - pitch])
    x0, z0 = math.floor(lo[0] / pitch) * pitch, math.floor(lo[1] / pitch) * pitch
    nx, nz = int((hi[0] - x0) / pitch) + 1, int((hi[1] - z0) / pitch) + 1
    ntx, ntz = -(-nx // TILE), -(-nz // TILE)            # tiles per side, rounded up
    bins = tile_bins(triangles, x0, z0, pitch, ntx, ntz, radius)
    log(f'{len(triangles)} triangles; box x {x0:.1f}..{x0 + nx * pitch:.1f}, z {z0:.1f}..{z0 + nz * pitch:.1f}: '
        f'{nx} x {nz} columns, {ntx * ntz} tiles, {len(bins)} with collision')
    up = tri_normal[:, 1] > UP_MIN
    steep = tri_normal[:, 1] <= FLOOR_MIN
    slope = np.sqrt(np.maximum(1 - tri_normal[:, 1] ** 2, 0)) / np.maximum(tri_normal[:, 1], 0.05)   # rise per metre
    tri_lo, tri_hi = triangles[:, :, 1].min(1), triangles[:, :, 1].max(1)
    y_top, y_bottom = -1e9, 1e9
    out_file = open(out, 'wb')
    out_file.write(bytes(HEADER.size))             # rewritten at the end, when the height range is known
    surfaces = blocked_count = 0
    wanted = None                                  # the chosen sectors' boxes (x0, z0, x1, z1), 1 m wider
    if sectors:
        (x_offset, z_offset), ids = sectors
        wanted = [((X + x_offset) * 100.0 - 1, (Z + z_offset) * 100.0 - 1,
                   (X + x_offset) * 100.0 + 101, (Z + z_offset) * 100.0 + 101)
                  for X, Z in ids]
    skipped = 0
    for tz in range(ntz):
        rows = min(TILE, nz - tz * TILE)
        band: list[list[Any]] = [[] for _ in range(rows * nx)]   # this band of rows: columns j-major, written when done
        for tx in range(ntx):
            i0, j0 = tx * TILE, tz * TILE
            if wanted is not None:
                bx0, bz0 = x0 + i0 * pitch, z0 + j0 * pitch
                bx1, bz1 = bx0 + TILE * pitch, bz0 + TILE * pitch
                if not any(bx1 >= wx0 and bx0 <= wx1 and bz1 >= wz0 and bz0 <= wz1 for wx0, wz0, wx1, wz1 in wanted):
                    skipped += 1                    # its columns stay empty
                    continue
            ii, jj = np.meshgrid(np.arange(i0, min(i0 + TILE, nx)), np.arange(j0, min(j0 + TILE, nz)))
            ii, jj = ii.ravel(), jj.ravel()
            px, pz = x0 + ii * pitch, z0 + jj * pitch
            # the terrain surface: its height and its normal from the height field's gradient
            terrain_y = hf.height(px, pz)
            grad_x = (hf.height(px + 0.5, pz) - hf.height(px - 0.5, pz))
            grad_z = (hf.height(px, pz + 0.5) - hf.height(px, pz - 0.5))
            terrain_normal = np.stack([-grad_x, np.ones_like(grad_x), -grad_z], 1)
            terrain_normal /= np.linalg.norm(terrain_normal, axis=1, keepdims=True)
            # per column of the tile: its surfaces [(height, normal)], the terrain first
            column_surfaces = [[(float(terrain_y[k]), terrain_normal[k])] for k in range(len(px))]
            tris = bins.get(tz * ntx + tx)
            pairs = []                                      # (column, low, high, over) of collision near each column
            if tris is not None:
                for chunk in np.array_split(tris, max(1, len(tris) // 1500)):
                    chunk_xz = triangles[chunk][:, :, [0, 2]]
                    u, v, w, ins = inside(chunk_xz, px, pz)
                    # (triangle, column): the triangle's plane height at the column
                    corner_y = triangles[chunk][:, :, 1]
                    plane_y = u * corner_y[:, 0, None] + v * corner_y[:, 1, None] + w * corner_y[:, 2, None]
                    floor_hit = ins & up[chunk][:, None]       # steep ones too: the slope rule rejects them later
                    for tri, col in zip(*np.nonzero(floor_hit)):
                        column_surfaces[col].append((float(plane_y[tri, col]), tri_normal[chunk][tri]))
                    distance = dist2d(chunk_xz, px, pz)
                    near = distance <= radius
                    if near.any():
                        tri, col = np.nonzero(near)
                        tri_steep = steep[chunk][tri]
                        # a floor: its plane height at the column, extended past its edge for a column beside it (the
                        # barycentric height), within a radius' rise. Its whole height range would make a ramp's next
                        # triangle block the ramp itself (Wake's storeroom stair, 2026-10-23). NaN for a triangle that
                        # is a line seen from above: its range then.
                        y_near = plane_y[tri, col]
                        plane_lo = np.where(np.isnan(y_near), tri_lo[chunk][tri], y_near - slope[chunk][tri] * radius)
                        plane_hi = np.where(np.isnan(y_near), tri_hi[chunk][tri], y_near + slope[chunk][tri] * radius)
                        low = np.where(tri_steep, tri_lo[chunk][tri], np.maximum(plane_lo, tri_lo[chunk][tri]))
                        high = np.where(tri_steep, tri_hi[chunk][tri], np.minimum(plane_hi, tri_hi[chunk][tri]))
                        # a triangle over the column itself (not a wall): its exact height there; it blocks from just
                        # above the surface, not from step height - a soldier steps up onto a ledge beside him, but
                        # cannot stand under a ceiling, a slab or a platform (collision is two-sided: its underside is a
                        # surface too, and must not be standable)
                        over = ins[tri, col] & ~tri_steep
                        low = np.where(over, plane_y[tri, col], low)
                        high = np.where(over, plane_y[tri, col], high)
                        pairs.append(np.stack([col, low, high, over], 1))
            near_pairs = np.concatenate(pairs) if pairs else np.zeros((0, 4))
            # merge each column's surfaces (top down), then the capsule test
            for k in range(len(px)):
                top_down = sorted(column_surfaces[k], key=lambda surface: -surface[0])
                merged = []
                for y, normal in top_down:
                    if not merged or merged[-1][0] - y > MERGE:
                        merged.append((y, normal))
                column_surfaces[k] = merged
            starts = np.zeros(0, np.int64)                  # each column's run of pairs (with pairs)
            if len(near_pairs):
                order = np.argsort(near_pairs[:, 0], kind='stable')
                near_pairs = near_pairs[order]
                starts = np.searchsorted(near_pairs[:, 0], np.arange(len(px) + 1))
            for k in range(len(px)):
                record = []                                 # (height, normal, flags) per surface
                near_lo = near_hi = None
                if len(near_pairs) and starts[k + 1] > starts[k]:
                    run = near_pairs[starts[k]:starts[k + 1]]
                    near_lo, near_hi = run[:, 1], run[:, 2]
                    near_over = run[:, 3] > 0
                for y, normal in column_surfaces[k]:
                    if near_lo is not None:
                        # near_over is set with near_lo
                        floor_of_band = np.where(near_over,  # pyright: ignore[reportPossiblyUnboundVariable]
                                                 y + OVERHEAD_MIN, y + step)
                        blocked = bool(((near_lo < y + height) & (near_hi > floor_of_band)).any())
                    else:
                        blocked = False
                    blocked |= y < float(terrain_y[k]) - UNDERGROUND
                    blocked_count += blocked
                    record.append((y, normal, BLOCKED if blocked else 0))
                    y_top, y_bottom = max(y_top, y), min(y_bottom, y)
                band[(jj[k] - tz * TILE) * nx + ii[k]] = record
                surfaces += len(record)
        out_bytes = bytearray()
        for record in band:
            out_bytes.append(min(len(record), 255))
            for y, normal, flags in record[:255]:
                q = np.clip(np.round(np.asarray(normal) * 127), -127, 127).astype(int)    # the normal as signed bytes
                out_bytes += struct.pack('<f3bB', y, int(q[0]), int(q[1]), int(q[2]), flags)
        out_file.write(out_bytes)
        log(f'  row {tz + 1}/{ntz}: {surfaces} surfaces, {blocked_count} blocked ({time.time() - t_start:.0f} s)')
    out_file.seek(0)
    out_file.write(HEADER.pack(b'ONS1', 1, pitch, x0, z0, nx, nz, y_top + 2, y_bottom - 2, radius, step, height,
                               0.0, 0))
    out_file.close()
    log(f'{out}: {nx * nz} columns, {surfaces} surfaces, {blocked_count} blocked, {skipped} tiles outside the '
        f'chosen sectors left empty, {time.time() - t_start:.0f} s')
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='navsample1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('level_dir')
    parser.add_argument('heightfield')
    parser.add_argument('out')
    parser.add_argument('--step', type=float, default=STEP)
    parser.add_argument('--radius', type=float, default=RADIUS)
    parser.add_argument('--height', type=float, default=HEIGHT)
    parser.add_argument('--pitch', type=float, default=0.8)
    parser.add_argument('--margin', type=float, default=20.0)
    parser.add_argument('--sea-level', type=float,
                        help='the level water height (core water.read_mesh): limits the box to land')
    parser.add_argument('--sectors', help='XOffset,ZOffset:XXZZ,XXZZ,... (hex sector ids, as detailed1943.py prints '
                                          'them): sample only those sectors')
    args = parser.parse_args(argv[1:])
    sectors = None
    if args.sectors:
        offsets, ids = args.sectors.split(':')
        x_offset, z_offset = (int(v) for v in offsets.split(','))
        sector_ids = [(int(s[:2], 16), int(s[2:], 16)) for s in ids.replace(' ', ',').split(',') if s]
        sectors = ((x_offset, z_offset), sector_ids)
    sample(args.level_dir, args.heightfield, args.out, args.step, args.radius, args.height, args.pitch, args.margin,
           args.sea_level, sectors)
    return 0

if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navsample1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
