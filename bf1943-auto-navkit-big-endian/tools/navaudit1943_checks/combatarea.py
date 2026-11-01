"""navaudit1943_checks, check 8: walkable ground inside and outside the combat area.

From the level's combat-area outlines (CombatAreaTriggerEntityData) and the build's 0.8 m cells: the land inside,
the share of it the build (and its main network) walks, and the walkable ground outside, clustered with its islands
and objects.
"""
import collections
import math
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

import soldier1943  # noqa: E402

from .geometry import clusters_xz, in_poly, poly_area
from .islands import sea_level
from .loading import objects_at

if TYPE_CHECKING:
    from .loading import AuditContext


def _cell_keys(x: npt.NDArray[np.floating], z: npt.NDArray[np.floating]) -> set[tuple[int, int]]:
    """The 0.8 m grid columns of points (x, z)."""
    return set(zip(np.round(x / 0.8).astype(int).tolist(), np.round(z / 0.8).astype(int).tolist()))


# ------------------------------------------------------------------------------------------------ 8: playable area

def playable(ctx: 'AuditContext') -> dict[str, Any]:
    """The combat areas' land and walkable ground, and the walkable ground outside them."""
    lvl = ctx['lvl']
    nav_index = ctx['nav']
    nav = nav_index.nav
    combat = [(shape.kind, shape.points) for shape in lvl.shapes
              if lvl.entities[shape.entity].short_type == 'CombatAreaTriggerEntityData']
    areas = [points for kind, points in combat if kind == 'area']
    outer = [points for kind, points in combat if kind == 'outer area']
    out: dict[str, Any] = {'combat areas': len(areas), 'outer areas': len(outer)}
    if not areas:
        return out
    x, z = nav.cell_xz[:, 0], nav.cell_xz[:, 1]
    inside = np.zeros(len(x), bool)
    for poly in areas:
        inside |= in_poly(x, z, poly)
    hf = ctx['hf']
    lvl_sea = sea_level(ctx)
    # land a soldier can stand on: terrain at most WATER_DEPTH under the sea, on the build's own 0.8 m cell grid; the
    # walkable share counts each such column once (stacked floors and object tops are reported apart)
    land = set()
    for poly in areas:
        columns_x = np.arange(math.floor(poly[:, 0].min() / 0.8), math.ceil(poly[:, 0].max() / 0.8) + 1) * 0.8
        columns_z = np.arange(math.floor(poly[:, 2].min() / 0.8), math.ceil(poly[:, 2].max() / 0.8) + 1) * 0.8
        grid_x, grid_z = np.meshgrid(columns_x, columns_z)
        grid_x, grid_z = grid_x.ravel(), grid_z.ravel()
        ins = in_poly(grid_x, grid_z, poly) & (hf.height(grid_x, grid_z) >= lvl_sea - soldier1943.WATER_DEPTH)
        land.update(_cell_keys(grid_x[ins], grid_z[ins]))
    terrain_y = hf.height(x, z)
    on_ground = inside & (np.abs(nav.cell_y - terrain_y) < 0.3)
    walk_cols = _cell_keys(x[on_ground], z[on_ground])
    cells_isl = np.array([area.island for area in nav.areas])[nav.cell_area]
    main_cols = _cell_keys(x[on_ground & (cells_isl == nav_index.main)], z[on_ground & (cells_isl == nav_index.main)])
    out.update({
        'combat area m2': [round(poly_area(poly), 0) for poly in areas],
        'outer area m2': [round(poly_area(poly), 0) for poly in outer],
        'land inside (terrain down to the wading depth), m2': round(len(land) * 0.64, 0),
        'walkable ground inside (one per column), m2': round(len(walk_cols & land) * 0.64, 0),
        'main network ground inside, m2': round(len(main_cols & land) * 0.64, 0),
        'walkable share of the land inside': round(len(walk_cols & land) / len(land), 3) if land else None,
        'main network share of the land inside': round(len(main_cols & land) / len(land), 3) if land else None,
        'walkable on objects and stacked floors inside, m2': round(float((inside & ~on_ground).sum()) * 0.64, 0),
        'walkable outside, m2': round(float((~inside).sum()) * 0.64, 0),
        'walkable outside, clusters': clusters_xz(x[~inside], z[~inside], top=15),
    })
    if areas and outer and max(poly_area(poly) for poly in areas) < 0.05 * max(poly_area(poly) for poly in outer):
        out['note'] = ("the combat areas are small outlines inside the outer area (Coral Sea: four of about 11,500 m2, "
                       "at the carriers) - not the playable boundary; what they mean is not known")
    for cluster in out['walkable outside, clusters']:
        cx, cz = cluster['centre']
        sel = (~inside) & (np.abs(x - cx) < 50) & (np.abs(z - cz) < 50)
        if sel.any():
            cluster['islands'] = {int(k): int(v) for k, v in collections.Counter(cells_isl[sel]).most_common(3)}
            cluster['objects'] = objects_at(ctx, cx, float(np.median(nav.cell_y[sel])), cz, grow=5.0)
    return out
