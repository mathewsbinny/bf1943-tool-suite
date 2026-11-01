"""navaudit1943_checks, check 6: water and shore.

From the build's cells and each grid's samples: walkable ground under more than 1.5 m of water, the wading band
(terrain 0 to WATER_DEPTH under the water: what was kept, what was not, where), and the islands on jetties, piers
and bridges.
"""
import collections
import math
from typing import TYPE_CHECKING, Any

import numpy as np

import soldier1943  # noqa: E402

from .config import PIER_RX
from .geometry import clusters_xz
from .islands import sea_level

if TYPE_CHECKING:
    from .loading import AuditContext


# ------------------------------------------------------------------------------------------------ 6: water

def water_checks(ctx: 'AuditContext') -> dict[str, Any]:
    """Walkable ground under deep water, the wading band per grid, and the islands on piers and bridges."""
    out: dict[str, Any] = {}
    nav_index = ctx['nav']
    nav = nav_index.nav
    level = sea_level(ctx)
    out['sea_level'] = round(level, 2)
    # ground under more than 1.5 m of water marked walkable (cells on terrain)
    grid = ctx['grids']['normal']
    cols = [grid.col(x, z) for x, z in nav.cell_xz]
    top = np.array([grid.top[c] if c is not None else -np.inf for c in cols])
    depth = top - nav.cell_y
    deep = depth > 1.5
    out['walkable cells under more than 1.5 m of water'] = int(deep.sum())
    out['deep walkable clusters'] = clusters_xz(nav.cell_xz[deep, 0], nav.cell_xz[deep, 1], top=20)
    wade = (depth > 0) & (depth <= 1.5)
    out['walkable cells in the wading band (0-1.5 m)'] = int(wade.sum())
    # the wading band: terrain columns 0..WATER_DEPTH under the water, as the samples see them
    band = {}
    for which, grid in ctx['grids'].items():
        s = grid.s
        col = grid.col_of()
        cx = s.min_x + (col % s.nx) * s.pitch
        cz = s.min_z + (col // s.nx) * s.pitch
        water_top = grid.top[col]                    # per surface: the water over its column
        in_band = np.isfinite(water_top) & (water_top > s.y) & (water_top - s.y <= soldier1943.WATER_DEPTH)
        sel = np.nonzero(in_band)[0]
        if which == 'normal' and 'detailed' in ctx['grids']:
            # the 0.8 m run's band outside the built detailed sectors only
            sector_x = np.floor(cx[sel] / 100.0).astype(int) - ctx['xoff']
            sector_z = np.floor(cz[sel] / 100.0).astype(int) - ctx['zoff']
            secs = np.stack([sector_x, sector_z], 1)
            detailed = np.array([tuple(v) in ctx['dset_built'] for v in secs]) if len(secs) else np.zeros(0, bool)
            sel = sel[~detailed]
        terrain_y = grid.hf.height(cx[sel], cz[sel])
        on_terrain = np.abs(s.y[sel] - terrain_y) < 0.06
        sel = sel[on_terrain]
        flat = s.normal[sel, 1] >= math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01
        clear = (s.flags[sel] & 1) == 0
        cand = sel[flat & clear]
        lost = cand[~grid.keep[cand]]
        band[grid.label] = {'band surfaces (terrain, 0-1.4 m deep)': int(len(sel)), 'flat and clear': int(len(cand)),
                            'kept': int(grid.keep[cand].sum()), 'not kept': int(len(lost)),
                            'not kept clusters': clusters_xz(cx[lost], cz[lost], top=15)}
        kept_deep = np.nonzero(grid.keep & np.isfinite(water_top) & (water_top - s.y > soldier1943.WATER_DEPTH))[0]
        band[grid.label]['kept surfaces deeper than 1.4 m'] = int(len(kept_deep))
        band[grid.label]['kept deep clusters'] = clusters_xz(cx[kept_deep], cz[kept_deep], top=15)
    out['wading band'] = band
    # jetties, piers, bridges: the build's islands on them
    piers = []
    cells_isl = np.array([area.island for area in nav.areas])[nav.cell_area]
    cell_x, cell_z, cell_y = nav.cell_xz[:, 0], nav.cell_xz[:, 1], nav.cell_y
    for box in ctx['boxes']:
        if not PIER_RX.search(box['bp']):
            continue
        lo, hi = box['min'], box['max']
        sel = (cell_x >= lo[0]) & (cell_x <= hi[0]) & (cell_z >= lo[2]) & \
              (cell_z <= hi[2]) & (cell_y >= lo[1] - 0.5) & (cell_y <= hi[1] + 0.5)
        islands = collections.Counter(cells_isl[sel].tolist())
        centre = [round((lo[0] + hi[0]) / 2, 1), round(hi[1], 1), round((lo[2] + hi[2]) / 2, 1)]
        piers.append(dict(object=box['bp'].split('/')[-1], centre=centre,
                          cells=int(sel.sum()), on_main=int(islands.get(nav_index.main, 0)),
                          other_islands={int(k): v for k, v in islands.items() if k != nav_index.main}))
    out['piers, jetties, bridges'] = piers
    return out
