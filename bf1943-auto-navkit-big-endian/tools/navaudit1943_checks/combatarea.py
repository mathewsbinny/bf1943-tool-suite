"""navaudit1943_checks, check 8: walkable ground inside and outside the combat area."""
import collections
import math

import numpy as np

import soldier1943  # noqa: E402

from .geometry import clusters_xz, in_poly, poly_area
from .islands import sea_level
from .loading import objects_at


# ------------------------------------------------------------------------------------------------ 8: playable area

def playable(C):
    lvl = C['lvl']
    nv = C['nav']
    nav = nv.nav
    combat = [(s.kind, s.points) for s in lvl.shapes if lvl.entities[s.entity].short_type == 'CombatAreaTriggerEntityData']
    areas = [p for k, p in combat if k == 'area']
    outer = [p for k, p in combat if k == 'outer area']
    out = {'combat areas': len(areas), 'outer areas': len(outer)}
    if not areas:
        return out
    x, z = nav.cell_xz[:, 0], nav.cell_xz[:, 1]
    inside = np.zeros(len(x), bool)
    for p in areas:
        inside |= in_poly(x, z, p)
    hf = C['hf']
    lvl_sea = sea_level(C)
    # land a soldier can stand on: terrain at most WATER_DEPTH under the sea, on the build's own 0.8 m cell grid; the
    # walkable share counts each such column once (stacked floors and object tops are reported apart)
    land = set()
    for p in areas:
        X, Z = np.meshgrid(np.arange(math.floor(p[:, 0].min() / 0.8), math.ceil(p[:, 0].max() / 0.8) + 1) * 0.8,
                           np.arange(math.floor(p[:, 2].min() / 0.8), math.ceil(p[:, 2].max() / 0.8) + 1) * 0.8)
        X, Z = X.ravel(), Z.ravel()
        ins = in_poly(X, Z, p) & (hf.height(X, Z) >= lvl_sea - soldier1943.WATER_DEPTH)
        land.update(zip(np.round(X[ins] / 0.8).astype(int).tolist(), np.round(Z[ins] / 0.8).astype(int).tolist()))
    t = hf.height(x, z)
    on_ground = inside & (np.abs(nav.cell_y - t) < 0.3)
    walk_cols = set(zip(np.round(x[on_ground] / 0.8).astype(int).tolist(), np.round(z[on_ground] / 0.8).astype(int).tolist()))
    cells_isl = np.array([a.island for a in nav.areas])[nav.cell_area]
    main_cols = set(zip(np.round(x[on_ground & (cells_isl == nv.main)] / 0.8).astype(int).tolist(),
                        np.round(z[on_ground & (cells_isl == nv.main)] / 0.8).astype(int).tolist()))
    out.update({
        'combat area m2': [round(poly_area(p), 0) for p in areas],
        'outer area m2': [round(poly_area(p), 0) for p in outer],
        'land inside (terrain down to the wading depth), m2': round(len(land) * 0.64, 0),
        'walkable ground inside (one per column), m2': round(len(walk_cols & land) * 0.64, 0),
        'main network ground inside, m2': round(len(main_cols & land) * 0.64, 0),
        'walkable share of the land inside': round(len(walk_cols & land) / len(land), 3) if land else None,
        'main network share of the land inside': round(len(main_cols & land) / len(land), 3) if land else None,
        'walkable on objects and stacked floors inside, m2': round(float((inside & ~on_ground).sum()) * 0.64, 0),
        'walkable outside, m2': round(float((~inside).sum()) * 0.64, 0),
        'walkable outside, clusters': clusters_xz(x[~inside], z[~inside], top=15),
    })
    if areas and outer and max(poly_area(p) for p in areas) < 0.05 * max(poly_area(p) for p in outer):
        out['note'] = ("the combat areas are small outlines inside the outer area (Coral Sea: four of about 11,500 m2, "
                       "at the carriers) - not the playable boundary; what they mean is not known")
    for c_ in out['walkable outside, clusters']:
        cx, cz = c_['centre']
        sel = (~inside) & (np.abs(x - cx) < 50) & (np.abs(z - cz) < 50)
        if sel.any():
            c_['islands'] = {int(k): int(v) for k, v in collections.Counter(cells_isl[sel]).most_common(3)}
            c_['objects'] = objects_at(C, cx, float(np.median(nav.cell_y[sel])), cz, grow=5.0)
    return out
