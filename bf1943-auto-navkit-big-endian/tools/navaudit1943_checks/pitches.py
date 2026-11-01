"""navaudit1943_checks, check 4: resolution (0.8 m against 0.2 m walkable ground in the detailed sectors).

In each built detailed sector, the kept ground layer of the 0.8 m run is compared with the 0.2 m run's under it:
the places wider than a cell row that only one of them keeps (with their sample columns and the objects there), and
the pieces of ground one run joins and the other separates.
"""
import collections
import math
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from .geometry import label
from .loading import objects_at

if TYPE_CHECKING:
    from .grids import Grid
    from .loading import AuditContext

type FloatArray = npt.NDArray[np.floating]


def _sector_name(sector_x: int, sector_z: int) -> str:
    """A sector's id as the report writes it (XXZZ in hex)."""
    return f'{((sector_x & 0xFF) << 8) | (sector_z & 0xFF):04x}'


def _centre_of(x: FloatArray, z: FloatArray, sel: npt.NDArray[np.bool]) -> list[float]:
    """The mean (x, z) of the selected cells, rounded to 0.1 m."""
    return [round(float(x[sel].mean()), 1), round(float(z[sel].mean()), 1)]


# ------------------------------------------------------------------------------------------------ 4: resolution

def ground_heights(grid: 'Grid', cols: npt.NDArray[np.integer]
                   ) -> tuple[FloatArray, npt.NDArray[np.int_], FloatArray]:
    """Per column (of cols): the kept surface nearest the terrain within 1 m (NaN when none), the number of kept
    surfaces, and the terrain height."""
    s = grid.s
    first, end = s.start[cols], s.start[cols + 1]
    counts = end - first
    owner = np.repeat(np.arange(len(cols)), counts)                     # each listed surface's place in cols
    within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)   # its index in its column
    surfaces = (np.repeat(first, counts) + within).astype(np.int64)
    x = s.min_x + (cols % s.nx) * s.pitch
    z = s.min_z + (cols // s.nx) * s.pitch
    terrain_y = grid.hf.height(x, z)
    y = s.y[surfaces]
    kept = grid.keep[surfaces]
    to_terrain = np.abs(y - terrain_y[owner])
    heights = np.full(len(cols), np.nan)
    ok = np.nonzero(kept & (to_terrain <= 1.0))[0]
    if len(ok):
        order = ok[np.lexsort((to_terrain[ok], owner[ok]))]           # by column, nearest the terrain first
        firsts = order[np.r_[True, owner[order][1:] != owner[order][:-1]]]
        heights[owner[firsts]] = y[firsts]
    kept_count = np.bincount(owner[kept], minlength=len(cols))
    return heights, kept_count, terrain_y


def resolution(ctx: 'AuditContext') -> dict[str, Any]:
    """0.8 m against 0.2 m walkable ground in every built detailed sector (skipped without a detailed mask)."""
    if 'detailed' not in ctx['grids']:
        return {'skipped': 'no detailed mask'}
    grid8, grid2 = ctx['grids']['normal'], ctx['grids']['detailed']
    s8, s2 = grid8.s, grid2.s
    out: dict[str, Any] = {'sectors': []}
    diffs8, diffs2, conn = [], [], []
    for (sector_x, sector_z) in sorted(ctx['dset_built']):
        # the sector's 0.8 m columns
        bx = (sector_x + ctx['xoff']) * 100.0
        bz = (sector_z + ctx['zoff']) * 100.0
        i0 = int(math.ceil((bx - s8.min_x) / s8.pitch - 1e-6))
        i1 = int(math.floor((bx + 100 - s8.min_x) / s8.pitch - 1e-6))
        j0 = int(math.ceil((bz - s8.min_z) / s8.pitch - 1e-6))
        j1 = int(math.floor((bz + 100 - s8.min_z) / s8.pitch - 1e-6))
        # a sector at the sampled box's edge reaches past it (the box is the land plus a margin)
        i0, i1, j0, j1 = max(i0, 0), min(i1, s8.nx - 1), max(j0, 0), min(j1, s8.nz - 1)
        if i0 > i1 or j0 > j1:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        cols8 = (jj * s8.nx + ii).ravel()
        h8, _n8, terrain8 = ground_heights(grid8, cols8)
        x8 = s8.min_x + ii.ravel() * s8.pitch
        z8 = s8.min_z + jj.ravel() * s8.pitch
        # over the 4 x 4 0.2 m columns around each 0.8 m one: the share with ground (any2), and with ground that
        # agrees with the 0.8 m height within 0.3 m or where the 0.8 m run has none (agree)
        agree = np.zeros(len(cols8))
        any2 = np.zeros(len(cols8))
        for di in (-2, -1, 0, 1):
            for dj in (-2, -1, 0, 1):
                i2 = np.round((x8 - s2.min_x) / s2.pitch).astype(int) + di
                j2 = np.round((z8 - s2.min_z) / s2.pitch).astype(int) + dj
                ok = (i2 >= 0) & (i2 < s2.nx) & (j2 >= 0) & (j2 < s2.nz)
                cols2 = np.where(ok, j2 * s2.nx + i2, 0)
                h2, _n2, _ = ground_heights(grid2, cols2)
                h2 = np.where(ok, h2, np.nan)
                any2 += ~np.isnan(h2)
                agree += ~np.isnan(h2) & (np.isnan(h8) | (np.abs(h2 - np.nan_to_num(h8)) <= 0.3))
        agree /= 16.0
        any2 /= 16.0
        only8 = ~np.isnan(h8) & (agree < 0.25)
        only2 = np.isnan(h8) & (any2 >= 0.75)
        rows, columns = jj.shape
        for _name, mask, store in (('0.8 m only', only8, diffs8), ('0.2 m only', only2, diffs2)):
            mask_2d = mask.reshape(rows, columns)
            # more than a cell row: what survives removing one row from every side
            core = mask_2d.copy()
            core[1:, :] &= mask_2d[:-1, :]
            core[:-1, :] &= mask_2d[1:, :]
            core[:, 1:] &= mask_2d[:, :-1]
            core[:, :-1] &= mask_2d[:, 1:]
            if not core.any():
                continue
            labels, count = label(core)
            for k in range(count):
                sel = (labels == k).ravel()
                cx, cz = float(x8[sel].mean()), float(z8[sel].mean())
                store.append(dict(sector=_sector_name(sector_x, sector_z), centre=[round(cx, 1), round(cz, 1)],
                                  core_cells=int(sel.sum()), core_m2=round(float(sel.sum()) * 0.64, 1),
                                  terrain_y=round(float(terrain8[sel].mean()), 1)))
        # connectivity of the ground layer: pieces one run joins and the other separates
        ground8 = (~np.isnan(h8)).reshape(rows, columns)
        ground2 = (any2 >= 0.5).reshape(rows, columns)
        labels8, count8 = label(ground8)
        labels2, count2 = label(ground2)
        both = ground8 & ground2
        pairs = set(zip(labels8[both].tolist(), labels2[both].tolist()))
        by2 = collections.defaultdict(set)      # 0.2 m piece -> the 0.8 m pieces it overlaps
        by8 = collections.defaultdict(set)      # 0.8 m piece -> the 0.2 m pieces it overlaps
        for piece8, piece2 in pairs:
            by2[piece2].add(piece8)
            by8[piece8].add(piece2)
        size8 = np.bincount(labels8[ground8], minlength=count8)
        size2 = np.bincount(labels2[ground2], minlength=count2)
        for piece2, pieces8 in by2.items():
            big = [piece8 for piece8 in pieces8 if size8[piece8] >= 25]
            if len(big) > 1:
                sel = (labels2 == piece2).ravel()
                conn.append(dict(sector=_sector_name(sector_x, sector_z), kind='0.2 m joins pieces 0.8 m separates',
                                 pieces=len(big), centre=_centre_of(x8, z8, sel)))
        for piece8, pieces2 in by8.items():
            big = [piece2 for piece2 in pieces2 if size2[piece2] >= 25]
            if len(big) > 1:
                sel = (labels8 == piece8).ravel()
                conn.append(dict(sector=_sector_name(sector_x, sector_z), kind='0.8 m joins pieces 0.2 m separates',
                                 pieces=len(big), centre=_centre_of(x8, z8, sel)))
        out['sectors'].append(dict(sector=_sector_name(sector_x, sector_z), ground_08=int(ground8.sum()),
                                   ground_02=int(ground2.sum()), only08=int(only8.sum()), only02=int(only2.sum())))
    for places in (diffs8, diffs2):
        for place in places:
            x, z = place['centre']
            low, high = place['terrain_y'] - 1.5, place['terrain_y'] + 2.5
            place['objects'] = objects_at(ctx, x, place['terrain_y'], z, grow=1.0)
            c8 = grid8.col(x, z)
            place['column_08'] = grid8.describe(c8, low, high, ctx['mm'])['surfaces'] if c8 is not None else None
            c2 = grid2.col(x, z)
            place['column_02'] = grid2.describe(c2, low, high, ctx['mm'])['surfaces'] if c2 is not None else None
        places.sort(key=lambda place: -place['core_cells'])
    out['walkable at 0.8 m, not at 0.2 m'] = diffs8
    out['walkable at 0.2 m, not at 0.8 m'] = diffs2
    out['connectivity'] = conn
    return out
