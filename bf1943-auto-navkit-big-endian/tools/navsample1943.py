"""NavKit (big-endian): collision samples of a BF1943 level computed from its data, without the game.

BFBC2's NavKit samples a level in game, through OPFOR's [dev] navsample option (DICE's physics queries). BF1943 has
no such sampler, so this computes the same columns from the level's terrain height field and the world-space
collision written by levelobjects1943.py (all_tris.npy), and writes them in navsample's format (version 1), which
navbuild.py reads unchanged:

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

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'core')]
from bfbc2core import terrain  # noqa: E402

HEADER = struct.Struct('<4sI3f2i6fI')
BLOCKED = 1
TILE = 32                        # columns per tile side
UP_MIN = 0.05                    # a triangle whose normal has y above this is a floor a downward ray meets
FLOOR_MIN = 0.3                  # flatter than this (normal y): height range from its plane; steeper: a wall
MERGE = 0.05                     # surfaces closer than this in one column are one surface
OVERHEAD_MIN = 0.05              # a triangle over the column this far above a surface leaves no headroom there
UNDERGROUND = 0.5                # a surface this far under the terrain is buried (a model's foundations: Wake's guard
                                 # towers stand 3.9 m deep), not floor; the margin is for the heightfield's own error


def normals(T):
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.maximum(length, 1e-12)
    flip = n[:, 1] < 0                       # both windings occur: a floor is the side facing up
    n[flip] *= -1
    return n.astype(np.float32), length[:, 0] > 1e-9


def tile_bins(T, x0, z0, pitch, ntx, ntz, pad):
    """{tile index: triangle indices} for every tile a triangle's bounding box (grown by pad) touches."""
    lo, hi = T[:, :, [0, 2]].min(1) - pad, T[:, :, [0, 2]].max(1) + pad
    size = TILE * pitch
    t0 = np.floor((lo - [x0, z0]) / size).astype(np.int64)
    t1 = np.floor((hi - [x0, z0]) / size).astype(np.int64)
    t0 = np.clip(t0, 0, [ntx - 1, ntz - 1]); t1 = np.clip(t1, 0, [ntx - 1, ntz - 1])
    keep = (hi[:, 0] >= x0) & (hi[:, 1] >= z0) & (lo[:, 0] <= x0 + ntx * size) & (lo[:, 1] <= z0 + ntz * size)
    bins = {}
    idx = np.nonzero(keep)[0]
    span = (t1[idx, 0] - t0[idx, 0] + 1) * (t1[idx, 1] - t0[idx, 1] + 1)
    one = idx[span == 1]                     # most triangles touch one tile: grouped in one go
    key = t0[one, 1] * ntx + t0[one, 0]
    order = np.argsort(key, kind='stable')
    keys, starts = np.unique(key[order], return_index=True)
    for k, group in zip(keys, np.split(one[order], starts[1:])):
        bins.setdefault(int(k), []).append(group)
    for i in idx[span > 1]:                  # the rest, tile by tile
        for tz in range(t0[i, 1], t1[i, 1] + 1):
            for tx in range(t0[i, 0], t1[i, 0] + 1):
                bins.setdefault(int(tz * ntx + tx), []).append(np.array([i]))
    return {t: np.concatenate(v) for t, v in bins.items()}


FLAT_FROM_ABOVE = 1e-6           # a triangle whose sides meet at under about 0.06 deg seen from above (sin^2 of the
                                 # angle) is a line there: a vertical wall


def inside(T2, px, pz):
    """Barycentric coordinates (u, v, w) of points (P,) in triangles (N, 3, 2) -> (N, P) each, and the inside mask.
    A triangle that is a line seen from above (a vertical wall) contains no point: its edges still count in dist2d.
    That is judged relative to its size, in float64: float32 rounding at level coordinates leaves such a wall's
    determinant far above any fixed epsilon, and the coordinates then put far-away points inside it."""
    T2 = T2.astype(np.float64)
    a, b, c = T2[:, 0, None, :], T2[:, 1, None, :], T2[:, 2, None, :]
    p = np.stack([px, pz], -1).astype(np.float64)[None]
    v0, v1, v2 = b - a, c - a, p - a
    d00 = (v0 * v0).sum(-1); d01 = (v0 * v1).sum(-1); d11 = (v1 * v1).sum(-1)
    d20 = (v2 * v0).sum(-1); d21 = (v2 * v1).sum(-1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) <= FLAT_FROM_ABOVE * d00 * d11, np.nan, den)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    u = 1 - v - w
    return u, v, w, (u >= -1e-6) & (v >= -1e-6) & (w >= -1e-6)


def dist2d(T2, px, pz):
    """Distance from points (P,) to triangles (N, 3, 2) in the horizontal plane -> (N, P)."""
    _, _, _, ins = inside(T2, px, pz)
    p = np.stack([px, pz], -1)[None]
    best = None
    for k in range(3):
        a, b = T2[:, k, None, :], T2[:, (k + 1) % 3, None, :]
        ab = b - a
        t = np.clip(((p - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
        d = np.linalg.norm(p - (a + t[..., None] * ab), axis=-1)
        best = d if best is None else np.minimum(best, d)
    return np.where(ins, 0.0, best)


ISLAND_FOOTPRINT = 100.0          # m2: an object rising above the sea and at least this big is ground (rock islets,
                                 # wrecks, piers); buoys are a few m2


def box_from_level(level_dir, hf, sea_level, log=print):
    """(min (x, z), max (x, z)) of what can be walked on: the land (terrain above sea_level - 1.5 m, every islet
    included), the whole collision box of each object holding a soldier spawn (the carriers, whatever their length),
    and objects rising above the sea with a footprint of at least ISLAND_FOOTPRINT."""
    import json
    land = hf.heights > sea_level - 1.5
    jj, ii = np.nonzero(land)
    cx, cz = hf.cell
    lo = np.array([hf.min[0] + ii.min() * cx, hf.min[2] + jj.min() * cz])
    hi = np.array([hf.min[0] + (ii.max() + 1) * cx, hf.min[2] + (jj.max() + 1) * cz])
    log(f'box: land x {lo[0]:.0f}..{hi[0]:.0f}, z {lo[1]:.0f}..{hi[1]:.0f}')
    spawns = json.load(open(os.path.join(level_dir, 'soldier_spawns.json')))
    for b in json.load(open(os.path.join(level_dir, 'object_boxes.json'))):
        bmin, bmax = np.array(b['min'])[[0, 2]], np.array(b['max'])[[0, 2]]
        holds = any(bmin[0] - 5 <= s[0] <= bmax[0] + 5 and bmin[1] - 5 <= s[2] <= bmax[1] + 5 for s in spawns)
        islet = b['max'][1] > sea_level + 0.5 and b['footprint'] >= ISLAND_FOOTPRINT
        if (holds or islet) and (bmin < lo).any() | (bmax > hi).any():
            log(f'box: + {b["bp"].rsplit("/", 1)[-1]} ({"holds a spawn" if holds else "islet"}, '
                f'x {bmin[0]:.0f}..{bmax[0]:.0f}, z {bmin[1]:.0f}..{bmax[1]:.0f})')
            lo, hi = np.minimum(lo, bmin), np.maximum(hi, bmax)
    return lo, hi


# BF1943's soldier (soldier1943.py): the capsule each surface is tested with
from soldier1943 import HEIGHT, RADIUS, STEP, STEP_HEIGHT, STEP_TOLERANCE  # noqa: E402,F401


def sample(level_dir, hf_path, out, step=STEP, radius=RADIUS, height=HEIGHT, pitch=0.8, margin=20.0, sea_level=None,
           sectors=None, log=print):
    """sectors: None, or (offsets (xoff, zoff), [(X, Z)]) - sample only tiles touching those 100 m sectors (plus 1 m,
    the builder's window overlap); the rest of the grid is written as empty columns (the detailed run at pitch 0.2)."""
    t_start = time.time()
    T = np.load(os.path.join(level_dir, 'all_tris.npy')).astype(np.float64)
    hf = terrain.read(hf_path)
    N, ok = normals(T)
    T, N = T[ok], N[ok]
    if sea_level is not None and os.path.isfile(os.path.join(level_dir, 'object_boxes.json')):
        lo, hi = box_from_level(level_dir, hf, sea_level, log)
    else:
        # the collision's extent, and the land (terrain above the lowest collision)
        lo, hi = T[:, :, [0, 2]].reshape(-1, 2).min(0), T[:, :, [0, 2]].reshape(-1, 2).max(0)
        floor = T[:, :, 1].min()
        land = hf.heights > floor
        if land.any():
            jj, ii = np.nonzero(land)
            cx, cz = hf.cell
            lo = np.minimum(lo, [hf.min[0] + ii.min() * cx, hf.min[2] + jj.min() * cz])
            hi = np.maximum(hi, [hf.min[0] + ii.max() * cx, hf.min[2] + jj.max() * cz])
    lo = np.maximum(lo - margin, [hf.min[0], hf.min[2]])
    hi = np.minimum(hi + margin, [hf.max[0] - pitch, hf.max[2] - pitch])
    x0, z0 = math.floor(lo[0] / pitch) * pitch, math.floor(lo[1] / pitch) * pitch
    nx, nz = int((hi[0] - x0) / pitch) + 1, int((hi[1] - z0) / pitch) + 1
    ntx, ntz = -(-nx // TILE), -(-nz // TILE)
    bins = tile_bins(T, x0, z0, pitch, ntx, ntz, radius)
    log(f'{len(T)} triangles; box x {x0:.1f}..{x0 + nx * pitch:.1f}, z {z0:.1f}..{z0 + nz * pitch:.1f}: '
        f'{nx} x {nz} columns, {ntx * ntz} tiles, {len(bins)} with collision')
    up = N[:, 1] > UP_MIN
    steep = N[:, 1] <= FLOOR_MIN
    slope = np.sqrt(np.maximum(1 - N[:, 1] ** 2, 0)) / np.maximum(N[:, 1], 0.05)   # height change per metre
    tri_lo, tri_hi = T[:, :, 1].min(1), T[:, :, 1].max(1)
    y_top, y_bottom = -1e9, 1e9
    f = open(out, 'wb')
    f.write(bytes(HEADER.size))                    # rewritten at the end, when the height range is known
    surfaces = blocked_count = 0
    wanted = None
    if sectors:
        (xo, zo), ids = sectors
        wanted = [((X + xo) * 100.0 - 1, (Z + zo) * 100.0 - 1, (X + xo) * 100.0 + 101, (Z + zo) * 100.0 + 101) for X, Z in ids]
    skipped = 0
    for tz in range(ntz):
        rows = min(TILE, nz - tz * TILE)
        band = [[] for _ in range(rows * nx)]       # this band of rows: columns j-major, written when it is done
        for tx in range(ntx):
            i0, j0 = tx * TILE, tz * TILE
            if wanted is not None:
                bx0, bz0 = x0 + i0 * pitch, z0 + j0 * pitch
                bx1, bz1 = bx0 + TILE * pitch, bz0 + TILE * pitch
                if not any(bx1 >= a and bx0 <= c and bz1 >= b and bz0 <= d for a, b, c, d in wanted):
                    skipped += 1                    # its columns stay empty
                    continue
            ii, jj = np.meshgrid(np.arange(i0, min(i0 + TILE, nx)), np.arange(j0, min(j0 + TILE, nz)))
            ii, jj = ii.ravel(), jj.ravel()
            px, pz = x0 + ii * pitch, z0 + jj * pitch
            # the terrain surface
            ty = hf.height(px, pz)
            gx = (hf.height(px + 0.5, pz) - hf.height(px - 0.5, pz))
            gz = (hf.height(px, pz + 0.5) - hf.height(px, pz - 0.5))
            tn = np.stack([-gx, np.ones_like(gx), -gz], 1)
            tn /= np.linalg.norm(tn, axis=1, keepdims=True)
            S = [[(float(ty[k]), tn[k])] for k in range(len(px))]
            tris = bins.get(tz * ntx + tx)
            pairs = []                                      # (column, low, high) of collision near each column
            if tris is not None:
                for chunk in np.array_split(tris, max(1, len(tris) // 1500)):
                    T2 = T[chunk][:, :, [0, 2]]
                    u, v, w, ins = inside(T2, px, pz)
                    Y = u * T[chunk][:, 0, 1, None] + v * T[chunk][:, 1, 1, None] + w * T[chunk][:, 2, 1, None]
                    floor_hit = ins & up[chunk][:, None]       # steep ones too: the slope rule rejects them later
                    for a, c in zip(*np.nonzero(floor_hit)):
                        S[c].append((float(Y[a, c]), N[chunk][a]))
                    d = dist2d(T2, px, pz)
                    near = d <= radius
                    if near.any():
                        a, c = np.nonzero(near)
                        st = steep[chunk][a]
                        # a floor: its plane height at the column, extended past its edge for a column beside it (the
                        # barycentric height), within a radius' rise. Its whole height range would make a ramp's next
                        # triangle block the ramp itself (Wake's storeroom stair, 2026-10-23). NaN for a triangle that
                        # is a line seen from above: its range then.
                        yc = Y[a, c]
                        flo = np.where(np.isnan(yc), tri_lo[chunk][a], yc - slope[chunk][a] * radius)
                        fhi = np.where(np.isnan(yc), tri_hi[chunk][a], yc + slope[chunk][a] * radius)
                        low = np.where(st, tri_lo[chunk][a], np.maximum(flo, tri_lo[chunk][a]))
                        high = np.where(st, tri_hi[chunk][a], np.minimum(fhi, tri_hi[chunk][a]))
                        # a triangle over the column itself (not a wall): its exact height there; it blocks from just
                        # above the surface, not from step height - a soldier steps up onto a ledge beside him, but
                        # cannot stand under a ceiling, a slab or a platform (collision is two-sided: its underside is a
                        # surface too, and must not be standable)
                        over = ins[a, c] & ~st
                        low = np.where(over, Y[a, c], low)
                        high = np.where(over, Y[a, c], high)
                        pairs.append(np.stack([c, low, high, over], 1))
            P = np.concatenate(pairs) if pairs else np.zeros((0, 4))
            # merge each column's surfaces (top down), then the capsule test
            for k in range(len(px)):
                s = sorted(S[k], key=lambda e: -e[0])
                merged = []
                for y, n in s:
                    if not merged or merged[-1][0] - y > MERGE:
                        merged.append((y, n))
                S[k] = merged
            if len(P):
                order = np.argsort(P[:, 0], kind='stable')
                P = P[order]
                starts = np.searchsorted(P[:, 0], np.arange(len(px) + 1))
            for k in range(len(px)):
                rec = []
                near_lo = near_hi = None
                if len(P) and starts[k + 1] > starts[k]:
                    near_lo, near_hi = P[starts[k]:starts[k + 1], 1], P[starts[k]:starts[k + 1], 2]
                    near_over = P[starts[k]:starts[k + 1], 3] > 0
                for y, n in S[k]:
                    floor_of_band = np.where(near_over, y + OVERHEAD_MIN, y + step) if near_lo is not None else None
                    blocked = near_lo is not None and bool(((near_lo < y + height) & (near_hi > floor_of_band)).any())
                    blocked |= y < float(ty[k]) - UNDERGROUND
                    blocked_count += blocked
                    rec.append((y, n, BLOCKED if blocked else 0))
                    y_top, y_bottom = max(y_top, y), min(y_bottom, y)
                band[(jj[k] - tz * TILE) * nx + ii[k]] = rec
                surfaces += len(rec)
        out_bytes = bytearray()
        for rec in band:
            out_bytes.append(min(len(rec), 255))
            for y, n, flags in rec[:255]:
                q = np.clip(np.round(np.asarray(n) * 127), -127, 127).astype(int)
                out_bytes += struct.pack('<f3bB', y, int(q[0]), int(q[1]), int(q[2]), flags)
        f.write(out_bytes)
        log(f'  row {tz + 1}/{ntz}: {surfaces} surfaces, {blocked_count} blocked ({time.time() - t_start:.0f} s)')
    f.seek(0)
    f.write(HEADER.pack(b'ONS1', 1, pitch, x0, z0, nx, nz, y_top + 2, y_bottom - 2, radius, step, height, 0.0, 0))
    f.close()
    log(f'{out}: {nx * nz} columns, {surfaces} surfaces, {blocked_count} blocked, {skipped} tiles outside the '
        f'chosen sectors left empty, {time.time() - t_start:.0f} s')
    return out


def main(argv):
    p = argparse.ArgumentParser(prog='navsample1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('level_dir')
    p.add_argument('heightfield')
    p.add_argument('out')
    p.add_argument('--step', type=float, default=STEP)
    p.add_argument('--radius', type=float, default=RADIUS)
    p.add_argument('--height', type=float, default=HEIGHT)
    p.add_argument('--pitch', type=float, default=0.8)
    p.add_argument('--margin', type=float, default=20.0)
    p.add_argument('--sea-level', type=float, help='the level water height (core water.read_mesh): limits the box to land')
    p.add_argument('--sectors', help='XOffset,ZOffset:XXZZ,XXZZ,... (hex sector ids, as detailed1943.py prints them): '
                                     'sample only those sectors')
    a = p.parse_args(argv[1:])
    sectors = None
    if a.sectors:
        offs, ids = a.sectors.split(':')
        xo, zo = (int(v) for v in offs.split(','))
        sectors = ((xo, zo), [(int(s[:2], 16), int(s[2:], 16)) for s in ids.replace(' ', ',').split(',') if s])
    sample(a.level_dir, a.heightfield, a.out, a.step, a.radius, a.height, a.pitch, a.margin, a.sea_level, sectors)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navsample1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
