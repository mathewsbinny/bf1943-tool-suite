"""navaudit1943_checks, check 6: water and shore."""
import collections
import math

import numpy as np

import soldier1943  # noqa: E402

from .config import PIER_RX
from .geometry import clusters_xz
from .islands import sea_level


# ------------------------------------------------------------------------------------------------ 6: water

def water_checks(C):
    out = {}
    nv = C['nav']
    nav = nv.nav
    level = sea_level(C)
    out['sea_level'] = round(level, 2)
    # ground under more than 1.5 m of water marked walkable (cells on terrain)
    G = C['grids']['normal']
    cols = [G.col(x, z) for x, z in nav.cell_xz]
    top = np.array([G.top[c] if c is not None else -np.inf for c in cols])
    depth = top - nav.cell_y
    deep = depth > 1.5
    out['walkable cells under more than 1.5 m of water'] = int(deep.sum())
    out['deep walkable clusters'] = clusters_xz(nav.cell_xz[deep, 0], nav.cell_xz[deep, 1], top=20)
    wade = (depth > 0) & (depth <= 1.5)
    out['walkable cells in the wading band (0-1.5 m)'] = int(wade.sum())
    # the wading band: terrain columns 0..WATER_DEPTH under the water, as the samples see them
    res = {}
    for which, G in C['grids'].items():
        s = G.s
        col = G.col_of()
        cx = s.min_x + (col % s.nx) * s.pitch
        cz = s.min_z + (col // s.nx) * s.pitch
        sel = np.nonzero(np.isfinite(G.top[col]) & (G.top[col] > s.y) & (G.top[col] - s.y <= soldier1943.WATER_DEPTH))[0]
        if which == 'normal' and 'detailed' in C['grids']:
            secs = np.stack([np.floor(cx[sel] / 100.0).astype(int) - C['xoff'], np.floor(cz[sel] / 100.0).astype(int) - C['zoff']], 1)
            det = np.array([tuple(v) in C['dset_built'] for v in secs]) if len(secs) else np.zeros(0, bool)
            sel = sel[~det]
        t = G.hf.height(cx[sel], cz[sel])
        terr = np.abs(s.y[sel] - t) < 0.06
        sel = sel[terr]
        flat = s.normal[sel, 1] >= math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01
        clear = (s.flags[sel] & 1) == 0
        cand = sel[flat & clear]
        lost = cand[~G.keep[cand]]
        res[G.label] = {'band surfaces (terrain, 0-1.4 m deep)': int(len(sel)), 'flat and clear': int(len(cand)),
                        'kept': int(G.keep[cand].sum()), 'not kept': int(len(lost)),
                        'not kept clusters': clusters_xz(cx[lost], cz[lost], top=15)}
        kept_deep = np.nonzero(G.keep & np.isfinite(G.top[col]) & (G.top[col] - s.y > soldier1943.WATER_DEPTH))[0]
        res[G.label]['kept surfaces deeper than 1.4 m'] = int(len(kept_deep))
        res[G.label]['kept deep clusters'] = clusters_xz(cx[kept_deep], cz[kept_deep], top=15)
    out['wading band'] = res
    # jetties, piers, bridges: the build's islands on them
    piers = []
    cells_isl = np.array([a.island for a in nav.areas])[nav.cell_area]
    for b in C['boxes']:
        if not PIER_RX.search(b['bp']):
            continue
        lo, hi = b['min'], b['max']
        sel = (nav.cell_xz[:, 0] >= lo[0]) & (nav.cell_xz[:, 0] <= hi[0]) & (nav.cell_xz[:, 1] >= lo[2]) & \
              (nav.cell_xz[:, 1] <= hi[2]) & (nav.cell_y >= lo[1] - 0.5) & (nav.cell_y <= hi[1] + 0.5)
        isl = collections.Counter(cells_isl[sel].tolist())
        piers.append(dict(object=b['bp'].split('/')[-1], centre=[round((lo[0] + hi[0]) / 2, 1), round(hi[1], 1), round((lo[2] + hi[2]) / 2, 1)],
                          cells=int(sel.sum()), on_main=int(isl.get(nv.main, 0)),
                          other_islands={int(k): v for k, v in isl.items() if k != nv.main}))
    out['piers, jetties, bridges'] = piers
    return out
