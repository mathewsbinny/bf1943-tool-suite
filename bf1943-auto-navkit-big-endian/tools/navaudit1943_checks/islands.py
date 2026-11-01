"""navaudit1943_checks, check 2: the landmass, the islands' classes and reachability.

landmass labels the land a soldier can wade to; classify_islands names each island of the build (main, vehicle-only
zone, separate landmass, raised surface, gap candidate) from where its cells lie; reachability floods the route graph
from every infantry spawn to every capture area and back.
"""
import collections
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

import navaudit1943  # noqa: E402
import soldier1943  # noqa: E402

from .config import BOAT_REACH
from .geometry import in_poly, label
from .loading import objects_at

if TYPE_CHECKING:
    from .loading import AuditContext
    from .positions import CheckResult

type Landmass = tuple[npt.NDArray[np.int_], float, float, float]    # (label grid, x0, z0, cell)


# ------------------------------------------------------------------------------------------------ 2: reachability

def landmass(ctx: 'AuditContext', cell: float = 4.0) -> Landmass:
    """Land a soldier can wade to: terrain at most WATER_DEPTH under the water, labelled into connected pieces on a
    4 m grid. Returns (label grid, x0, z0, cell)."""
    hf = ctx['hf']
    x0, z0 = float(hf.min[0]), float(hf.min[2])
    n = int((hf.max[0] - hf.min[0]) / cell)
    grid_x, grid_z = np.meshgrid(x0 + (np.arange(n) + 0.5) * cell, z0 + (np.arange(n) + 0.5) * cell)
    terrain_y = hf.height(grid_x, grid_z)
    level = sea_level(ctx)
    land = terrain_y >= level - soldier1943.WATER_DEPTH
    labels, _count = label(land)
    return labels, x0, z0, cell


def sea_level(ctx: 'AuditContext') -> float:
    """The water mesh's median height; without one, navkit.json's (soldier1943.sea_level)."""
    water = ctx['water']
    if water is None or not len(water[0]):
        # no water mesh: navkit.json's sea level, or without water the exporters' (2026-10-30: float(None) raised, then
        # -1e9 put the audit's sea elsewhere than the export's)
        return soldier1943.sea_level(ctx['st'], ctx['hf'])
    return float(np.median(water[0][:, 1]))


def landmass_at(landmass_: Landmass, x: npt.ArrayLike, z: npt.ArrayLike) -> npt.NDArray[np.int_]:
    """The landmass label at each (x, z)."""
    labels, x0, z0, cell = landmass_
    i = np.clip(((np.asarray(x) - x0) / cell).astype(int), 0, labels.shape[1] - 1)
    j = np.clip(((np.asarray(z) - z0) / cell).astype(int), 0, labels.shape[0] - 1)
    return labels[j, i]


def classify_islands(ctx: 'AuditContext', landmass_: Landmass, positions: list['CheckResult']) -> list[dict[str, Any]]:
    """Every island of the build with its kind (main, vehicle-only zone, separate landmass, raised surface, gap
    candidate), its extent and the required positions on it."""
    nav_index = ctx['nav']
    nav = nav_index.nav
    hf = ctx['hf']
    cells_isl = np.array([area.island for area in nav.areas])[nav.cell_area]
    cell_terrain = hf.height(nav.cell_xz[:, 0], nav.cell_xz[:, 1])
    cell_landmass = landmass_at(landmass_, nav.cell_xz[:, 0], nav.cell_xz[:, 1])
    main_landmass = collections.Counter(cell_landmass[cells_isl == nav_index.main]).most_common(1)[0][0]
    level = sea_level(ctx)
    required = collections.defaultdict(list)
    for position in positions:
        area = position.get('nearest_same_floor')
        reach = max(navaudit1943.OBJECTIVE_REACH, BOAT_REACH if position.get('afloat') else 0)
        if area and area['distance'] <= reach and area['island'] >= 0:
            required[area['island']].append(f"{position['kind']}: {position['name']}")
    node_pos = nav.node_pos
    node_terrain = hf.height(node_pos[:, 0], node_pos[:, 2])
    node_landmass = landmass_at(landmass_, node_pos[:, 0], node_pos[:, 2])
    out = []
    for k, island in enumerate(nav.islands):
        sel = cells_isl == k
        if sel.any():
            xs, zs, ys = nav.cell_xz[sel, 0], nav.cell_xz[sel, 1], nav.cell_y[sel]
            terrain_y, landmass_label = cell_terrain[sel], cell_landmass[sel]
        else:
            # areas too narrow to hold a 0.8 m cell: the island's route nodes stand for it
            nodes = nav.node_island == k
            xs, zs, ys = node_pos[nodes, 0], node_pos[nodes, 2], nav_index.node_ground[nodes]
            terrain_y, landmass_label = node_terrain[nodes], node_landmass[nodes]
        above_terrain = ys - terrain_y
        on_main_land = float((landmass_label == main_landmass).mean())
        over_water = float((terrain_y < level - soldier1943.WATER_DEPTH).mean())
        raised = float((above_terrain > 1.5).mean())
        record: dict[str, Any] = dict(island=k, nodes=island.nodes, cells=int(sel.sum()),
                                      area_m2=round(float(sel.sum()) * 0.64, 0),
                                      centre=[round(float(xs.mean()), 1), round(float(ys.mean()), 1),
                                              round(float(zs.mean()), 1)],
                                      bbox=[round(float(xs.min()), 1), round(float(zs.min()), 1),
                                            round(float(xs.max()), 1), round(float(zs.max()), 1)],
                                      on_main_landmass=round(on_main_land, 2), over_deep_water=round(over_water, 2),
                                      raised_share=round(raised, 2),
                                      mean_height_above_terrain=round(float(above_terrain.mean()), 1),
                                      required=required.get(k, []))
        cx, cy, cz = record['centre']
        record['objects'] = objects_at(ctx, cx, cy, cz, grow=2.0)
        if k == nav_index.main:
            record['kind'] = 'main'
        elif over_water > 0.5:
            record['kind'] = 'vehicle-only zone'
            record['linked_by'] = vehicle_link(ctx, record, positions)
        elif on_main_land < 0.5:
            record['kind'] = 'separate landmass (reef or islet: boat, or swimming)'
            record['linked_by'] = vehicle_link(ctx, record, positions)
        elif raised > 0.8 and not record['required']:
            record['kind'] = 'raised surface, no access'
        else:
            record['kind'] = 'gap candidate'
        out.append(record)
    return out


def vehicle_link(ctx: 'AuditContext', isl: dict[str, Any], positions: list['CheckResult']) -> list[str]:
    """What would join a vehicle-only zone to the rest: the vehicle spawns on or beside it."""
    x0, z0, x1, z1 = isl['bbox']
    spawns = [spawn for spawn in positions if spawn['kind'] == 'vehicle spawn'
              and x0 - 10 <= spawn['position'][0] <= x1 + 10 and z0 - 10 <= spawn['position'][2] <= z1 + 10]
    kinds = collections.Counter(position.get('class') for position in spawns)
    out = []
    if kinds.get('boat'):
        out.append(f"boat (landing craft spawns: {kinds['boat']})")
    if kinds.get('aircraft'):
        out.append(f"plane (aircraft spawns: {kinds['aircraft']})")
    if not out:
        out.append('no vehicle spawn on it: a boat landing or a parachute drop from a plane')
    return out


def reachability(ctx: 'AuditContext', positions: list['CheckResult']) -> dict[str, Any]:
    """Every infantry spawn joined to the route graph, and whether it reaches each flag's capture area and back."""
    nav = ctx['nav']
    flags = [position for position in positions if position['kind'] == 'flag']
    targets = {}                        # flag name -> the route nodes in its capture area
    for flag in flags:
        poly = flag.get('_poly')
        nodes = nav.nav.node_pos
        if poly is not None:
            ins = in_poly(nodes[:, 0], nodes[:, 2], poly)
        else:
            radius = max(flag.get('capture_radius') or 0, 5.0)
            ins = np.hypot(nodes[:, 0] - flag['position'][0], nodes[:, 2] - flag['position'][2]) <= radius
        targets[flag['name']] = np.nonzero(ins)[0]
    rows = []
    for position in positions:
        if position['kind'] != 'soldier spawn':
            continue
        area = position.get('nearest_same_floor')
        if position.get('floor_y') is None:
            rows.append(dict(spawn=position['name'], joined=False, reason='no floor'))
            continue
        x, _, z = position['position']
        node = nav.join(x, z, position['floor_y'], island=area['island'] if area else None)
        if node is None:
            rows.append(dict(spawn=position['name'], objective=position.get('objective'), joined=False,
                             reason='no route node within 15 m on its floor'))
            continue
        forward, backward = nav.reach(node), nav.reach(node, backward=True)
        res = dict(spawn=position['name'], objective=position.get('objective'), joined=True, node=node,
                   island=int(nav.nav.node_island[node]), main=int(nav.nav.node_island[node]) == nav.main,
                   to={}, back={})
        for name, target in targets.items():
            res['to'][name] = bool(len(target) and forward[target].any())
            res['back'][name] = bool(len(target) and backward[target].any())
        rows.append(res)
    return {'targets': {k: int(len(v)) for k, v in targets.items()}, 'spawns': rows}
