"""navaudit1943_checks, check 7: where each rule changed the kept ground (the rule-off masks), near-misses, ladders.

rules compares each grid's kept mask with every rule-off mask navaudit1943.py masks made (the surfaces each rule
removed or added, clustered, with the required positions near them), lists the surfaces within NEAR (20 %) of each
surface threshold (slope, headroom, water depth, terrain depth, step), and reports every ladder's ends and links.
"""
import json
import math
import os
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

import navaudit1943  # noqa: E402
import soldier1943  # noqa: E402

from .config import NEAR
from .geometry import clusters_xz
from .loading import sector_of

if TYPE_CHECKING:
    from .grids import Grid
    from .loading import AuditContext
    from .positions import CheckResult

type FloatArray = npt.NDArray[np.floating]


def _surface_xz(grid: 'Grid') -> tuple[npt.NDArray[np.int_], FloatArray, FloatArray]:
    """Every surface's column, and the column's x and z."""
    s = grid.s
    col = grid.col_of()
    return col, s.min_x + (col % s.nx) * s.pitch, s.min_z + (col // s.nx) * s.pitch


# ------------------------------------------------------------------------------------------------ 7: rules

def rules(ctx: 'AuditContext', positions: list['CheckResult']) -> dict[str, Any]:
    """Per rule-off mask, the surfaces the rule removed and added; the near-misses per grid; the ladders."""
    out: dict[str, Any] = {
        'objective floor': 'no such rule in the BF1943 pipeline: navbuild.ORDER_NODES adds nodes at AI order '
                           'positions and BF1943 levels have none (AreaOrders absent); the spawn seeds '
                           '(soldier_spawns.json) only seed the flood'}
    required = np.array([position['position'] for position in positions], float) if positions else np.zeros((0, 3))
    for _which, grid in ctx['grids'].items():
        s = grid.s
        _col, cx, cz = _surface_xz(grid)
        for variant, variant_keep in grid.variants.items():
            diff = np.nonzero(variant_keep != grid.keep)[0]
            removed = diff[variant_keep[diff] & ~grid.keep[diff]]   # walkable without the rule: the rule removed them
            added = diff[~variant_keep[diff] & grid.keep[diff]]     # walkable only with the rule: the rule added them
            record: dict[str, Any] = {'removed': int(len(removed)), 'added': int(len(added))}
            for name, sel in (('removed', removed), ('added', added)):
                clusters = clusters_xz(cx[sel], cz[sel], top=12)
                for cluster in clusters:
                    x, z = cluster['centre']
                    if len(required):
                        distance = np.hypot(required[:, 0] - x, required[:, 2] - z)
                        k = int(np.argmin(distance))
                        nearest = positions[k]
                        cluster['nearest_required'] = f"{nearest['kind']}: {nearest['name']} ({distance[k]:.0f} m)"
                record[name + ' clusters'] = clusters
                if len(required) and len(sel):
                    near = []
                    for position in positions:
                        x, y, z = position['position']
                        within = ((np.abs(cx[sel] - x) <= 20) & (np.abs(cz[sel] - z) <= 20)
                                  & (np.abs(s.y[sel] - y) <= 5))
                        if within.any():
                            near.append(f"{position['kind']}: {position['name']}: {int(within.sum())} surfaces "
                                        f"within 20 m")
                    record[name + ' near required positions'] = near
            out[f'{variant} ({grid.label})'] = record
    # near-misses: within 20 % of each threshold, on surfaces that pass every other surface test
    near_misses = {}
    for _which, grid in ctx['grids'].items():
        s = grid.s
        col, cx, cz = _surface_xz(grid)
        y = s.y.astype(float)
        clear = (s.flags & 1) == 0
        slope_deg = np.degrees(np.arccos(np.clip(s.normal[:, 1], -1, 1)))
        # clearance: the next surface up in the column (stored top down)
        above = np.full(len(y), np.inf)
        first = np.zeros(len(y), bool)
        first[s.start[:-1][np.diff(s.start) > 0]] = True
        above[1:] = np.where(first[1:], np.inf, y[:-1])
        clearance = above - y
        depth = np.where(np.isfinite(grid.top[col]), grid.top[col] - y, -np.inf)
        record = {}
        slope_max = soldier1943.SLOPE_MAX
        lo_, hi_ = slope_max * (1 - NEAR), slope_max * (1 + NEAR)
        sel = np.nonzero(clear & (slope_deg >= lo_) & (slope_deg <= hi_))[0]
        record[f'slope {lo_:.0f}-{hi_:.0f} deg (limit {slope_max:g})'] = \
            _near(grid, sel, slope_deg[sel] <= slope_max + 0.6, cx, cz)
        height = soldier1943.HEIGHT
        sel = np.nonzero(clear & (clearance >= height * (1 - NEAR)) & (clearance <= height * (1 + NEAR)))[0]
        record[f'headroom {height * (1 - NEAR):.2f}-{height * (1 + NEAR):.2f} m (limit {height:g})'] = \
            _near(grid, sel, clearance[sel] >= height, cx, cz)
        water_depth = soldier1943.WATER_DEPTH
        sel = np.nonzero(clear & (depth >= water_depth * (1 - NEAR)) & (depth <= water_depth * (1 + NEAR)))[0]
        water_band = f'{water_depth * (1 - NEAR):.2f}-{water_depth * (1 + NEAR):.2f}'
        record[f'water depth {water_band} m (limit {water_depth:g})'] = \
            _near(grid, sel, depth[sel] <= water_depth, cx, cz)
        terrain_y = grid.hf.height(cx, cz)
        under = terrain_y - y
        sel = np.nonzero(clear & (under >= 0.5 * (1 - NEAR)) & (under <= 0.5 * (1 + NEAR)))[0]
        record['under the terrain 0.40-0.60 m (limit 0.5)'] = _near(grid, sel, under[sel] <= 0.5, cx, cz)
        record['step (sills, stairs)'] = step_near(grid)
        near_misses[grid.label] = record
    out['near-misses'] = near_misses
    out['ladders'] = ladder_report(ctx)
    return out


def _near(grid: 'Grid', sel: npt.NDArray[np.intp], inside_limit: npt.NDArray[np.bool], cx: FloatArray,
          cz: FloatArray) -> dict[str, Any]:
    """One near-miss band (sel): the surfaces inside the limit and how many were kept; those just past it, clustered."""
    inside = sel[inside_limit]
    past = sel[~inside_limit]
    return {'inside the limit': int(len(inside)), 'kept of those': int(grid.keep[inside].sum()),
            'just past the limit': int(len(past)), 'past the limit, clusters': clusters_xz(cx[past], cz[past], top=8)}


def step_near(grid: 'Grid') -> dict[str, Any]:
    """Neighbouring surfaces (8 directions) on one floor whose height difference is within 20 % of the step: kept
    ground beside ground the step rule left out (dy just over STEP) and kept pairs just under it."""
    s = grid.s
    nx, nz = s.nx, s.nz
    step = soldier1943.STEP
    lo, hi = step * (1 - NEAR), step * (1 + NEAR)
    col = grid.col_of()
    kept = np.nonzero(grid.keep)[0]
    clear = (s.flags & 1) == 0
    slope_ok = s.normal[:, 1] >= math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01
    over, under = [], 0
    kept_y = s.y[kept]
    kept_col = col[kept]
    for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1), (-1, 0), (0, -1), (-1, -1), (-1, 1)):
        i = kept_col % nx + di
        j = kept_col // nx + dj
        ok = (i >= 0) & (i < nx) & (j >= 0) & (j < nz)
        neighbour = np.where(ok, j * nx + i, 0)
        first, end = s.start[neighbour], s.start[neighbour + 1]
        # the neighbour columns' surfaces, the q-th of each at a time
        for q in range(int((end - first).max()) if len(first) else 0):
            n = first + q
            valid = ok & (n < end)
            n = np.where(valid, n, 0)
            dy = s.y[n] - kept_y
            in_band = valid & (np.abs(dy) >= lo) & (np.abs(dy) <= hi)
            past = in_band & (np.abs(dy) > step) & ~grid.keep[n] & clear[n] & slope_ok[n]
            over.append(n[past])
            under += int((in_band & (np.abs(dy) <= step) & grid.keep[n]).sum())
    over = np.unique(np.concatenate(over)) if over else np.zeros(0, int)
    cx = s.min_x + (col[over] % nx) * s.pitch
    cz = s.min_z + (col[over] // nx) * s.pitch
    return {'kept pairs stepping %.2f-%.2f m' % (lo, step): under // 2,
            'left out, %.2f-%.2f m above or below kept ground' % (step, hi): int(len(over)),
            'left out, clusters': clusters_xz(cx, cz, top=12)}


def ladder_report(ctx: 'AuditContext') -> list[dict[str, Any]]:
    """Every ladder of ladders.json: the areas at its foot and top, the ladder links near it, and near-misses."""
    nav_index = ctx['nav']
    nav = nav_index.nav
    out = []
    ladders = json.load(open(os.path.join(ctx['p']['objects'], 'ladders.json'), encoding='utf-8'))
    is_ladder_edge = nav.ladder_edge if nav.ladder_edge is not None else np.zeros(len(nav.edges), bool)
    ladder_edges = nav.edges[is_ladder_edge]
    for ladder in ladders:
        lo, hi = ladder['lo'], ladder['hi']
        cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        foot, top = lo[1], hi[1]
        foot_same, _foot_any = nav_index.locate(cx, cz, foot, r=3.0, dy=1.0)
        top_same, _top_any = nav_index.locate(cx, cz, top, r=3.0, dy=1.5)
        links = 0
        for a, b in ladder_edges:
            pa, pb = nav.node_pos[a], nav.node_pos[b]
            if min(math.hypot(pa[0] - cx, pa[2] - cz), math.hypot(pb[0] - cx, pb[2] - cz)) <= 4.0:
                links += 1
        sectors = {sector_of(ctx, lo[0], lo[2]), sector_of(ctx, hi[0], hi[2])}
        foot_near_miss = bool(foot_same and navaudit1943.OBJECTIVE_REACH * 0.6 <= foot_same[0] <= 1.8)
        top_near_miss = bool(top_same and 1.2 <= top_same[0] <= 1.8)
        out.append(dict(ladder=ladder['bp'].split('/')[-1], centre=[round(cx, 1), round(foot, 1), round(cz, 1)],
                        top=round(top, 1), foot_area=nav_index.area_info(foot_same, foot),
                        top_area=nav_index.area_info(top_same, top), ladder_edges_near=links,
                        ends_in_two_sectors=len(sectors) > 1, near_miss=foot_near_miss or top_near_miss))
    return out
