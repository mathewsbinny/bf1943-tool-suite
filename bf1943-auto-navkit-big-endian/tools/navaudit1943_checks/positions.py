"""navaudit1943_checks, checks 1 and 5: the required positions, their floors and layers, the capture areas' share.

required_positions lists what a soldier must reach (capture areas, bases, soldier and vehicle spawns, repair stations,
hot zones) from the spawn map and the level; check_position judges one against the build (walkable ground on its own
floor within reach, or a vehicle's entry points), with the sample column under it (check 5's layers); capture_share
measures how much of a capture area's land the main network covers.
"""
import collections
import math
from typing import TYPE_CHECKING, Any, cast

import numpy as np

import navaudit1943  # noqa: E402
import soldier1943  # noqa: E402

from .config import AIRBORNE, AIRCRAFT, BOAT_REACH, BOATS, GROUND, MOUNT_DROP, STATIONARY, USABLE_SHARE, VEHICLE_REACH
from .geometry import in_poly, poly_area
from .loading import grid_at, objects_at

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .grids import Grid
    from .loading import AuditContext

type Position = dict[str, Any]          # a required position: {kind, name, team, position, ...}
type CheckResult = dict[str, Any]       # check_position's record of a position
type EntryPoint = dict[str, Any]        # {'point': [x, y, z], 'radius', 'order'}
type Point3 = tuple[float, float, float]


def _at_origin(position: Position) -> bool:
    """Whether a position lies at the origin (an unused placeholder)."""
    return all(abs(v) < 1e-6 for v in position['position'])


# ------------------------------------------------------------------------------------------------ 1 + 5: positions

def required_positions(ctx: 'AuditContext') -> list[Position]:
    """[{kind, name, team, position, ...}] from the spawn map and the level (BF1943 data only)."""
    out = []
    level_map, lvl = ctx['map'], ctx['lvl']
    hot = [shape for shape in lvl.shapes
           if lvl.entities[shape.entity].short_type == 'HotZoneEntityData' and shape.kind == 'area']
    for objective in level_map['objectives']:
        pos = objective['position']
        poly = None
        for shape in hot:
            if in_poly([pos[0]], [pos[2]], shape.points)[0]:
                poly = shape.points
                break
        out.append(dict(kind=objective['role'], name=objective['name'], team=objective.get('initial_owner'),
                        position=pos, capture_radius=objective.get('capture_radius'), capture_area=poly))
        for spawn in objective['spawns']:
            if spawn['kind'] == 'soldier':
                alternates = [a for a in spawn.get('alternates', []) if isinstance(a, list)]
                for k, point in enumerate([spawn['position']] + alternates):
                    out.append(dict(kind='soldier spawn', name=spawn['name'] + (f' alt {k}' if k else ''),
                                    team=spawn.get('team'), position=point, objective=objective['name']))
            else:
                out.append(dict(kind='vehicle spawn', name=spawn['name'], vehicle=spawn.get('vehicle'),
                                team=spawn.get('team'), position=spawn['position'], objective=objective['name'],
                                guid=spawn.get('guid')))
    for spawn in level_map.get('unowned_soldier_spawns', []):
        out.append(dict(kind='soldier spawn', name=spawn['name'], team=spawn.get('team'), position=spawn['position'],
                        objective=None))
    for spawn in level_map.get('unowned_vehicle_spawns', []):
        if spawn.get('vehicle') in (None, 'null') and _at_origin(spawn):
            continue                     # unused templates at the origin with no vehicle (Coral Sea)
        out.append(dict(kind='vehicle spawn', name=spawn['name'], vehicle=spawn.get('vehicle'), team=spawn.get('team'),
                        position=spawn['position'], objective=None, guid=spawn.get('guid')))
    for entity in lvl.entities:
        if entity.short_type == 'RepairStationEntityData' and entity.position is not None:
            outlines = [shape for shape in lvl.shapes if shape.entity == entity.index]
            out.append(dict(kind='repair station', name=f'repair {entity.index}', team=entity.team,
                            position=[float(v) for v in entity.position],
                            capture_area=outlines[0].points if outlines else None))
    hot_zones_used = {id(position['capture_area']) for position in out
                      if position['kind'] in ('flag', 'base') and position.get('capture_area') is not None}
    for shape in hot:
        if id(shape.points) not in hot_zones_used:
            centre = shape.points.mean(0)
            out.append(dict(kind='hot zone without a flag', name=f'hotzone {shape.entity}', team=None,
                            position=[float(centre[0]), float(centre[1]), float(centre[2])],
                            capture_area=shape.points))
    for position in out:
        if position['kind'] == 'vehicle spawn':
            vehicle = position.get('vehicle') or ''
            position['class'] = ('ground vehicle' if vehicle in GROUND else 'boat' if vehicle in BOATS
                          else 'aircraft' if vehicle in AIRCRAFT
                          else 'stationary weapon' if vehicle in STATIONARY else 'other')
            position['entries'] = vehicle_entries(ctx, position.get('guid'))
    # entities left at the origin are unused placeholders (Coral Sea: 3 flags, 2 soldier spawns, 4 repair stations,
    # 16 vehicle spawns), not positions in the level: listed apart, not checked
    ctx['placeholders'] = [{k: v for k, v in position.items() if k != 'capture_area'} for position in out
                           if _at_origin(position)]
    return [position for position in out if not _at_origin(position)]


def floor_of(grid: 'Grid', x: float, y: float, z: float) -> tuple[int | None, int | None, float | None]:
    """The surface a position stands on: the highest at most 0.5 m above it (spawns sit on or just above their
    floor), else the lowest surface of the column. (column, surface index, height); (None, None, None) off the grid,
    (column, None, None) in a column without surfaces."""
    c = grid.col(x, z)
    if c is None:
        return None, None, None
    indices = list(grid.surfaces(c))
    if not indices:
        return c, None, None
    below = [n for n in indices if grid.s.y[n] <= y + 0.5]
    n = max(below, key=lambda n: grid.s.y[n]) if below else min(indices, key=lambda n: grid.s.y[n])
    # a position under the terrain (Wake Island's Beach alternate 3: 0.92 m under a stony slope, inside a boulder's
    # buried part) stands on the terrain over it, not on what is buried under it
    cx, cz = grid.centre(c)
    terrain_y = float(grid.hf.height(cx, cz))
    if y < terrain_y:
        on_terrain = [m for m in indices if abs(grid.s.y[m] - terrain_y) < 0.06]
        if on_terrain:
            n = on_terrain[0]
    return c, n, float(grid.s.y[n])


def nearest_cell_3d(ctx: 'AuditContext', point: 'Sequence[float]', r: float = 8.0,
                    main_only: bool = False) -> tuple[float, int] | tuple[None, None]:
    """(distance, cell) of the build's walkable cell nearest a point, measured to a standing soldier's middle (ground +
    HEIGHT / 2); cells within r horizontally (and on the main network with main_only). Cells are the 0.8 m display
    grid: about 0.4 m coarse."""
    nav = ctx['nav'].nav
    x, y, z = point
    near = (np.abs(nav.cell_xz[:, 0] - x) <= r) & (np.abs(nav.cell_xz[:, 1] - z) <= r)
    if main_only:
        if '_cell_island' not in ctx:
            ctx['_cell_island'] = np.array([area.island for area in nav.areas])[nav.cell_area]
        near &= ctx['_cell_island'] == ctx['nav'].main
    selected = np.nonzero(near)[0]
    if not len(selected):
        return None, None
    distance = np.sqrt((nav.cell_xz[selected, 0] - x) ** 2 + (nav.cell_xz[selected, 1] - z) ** 2
                       + (nav.cell_y[selected] + soldier1943.HEIGHT / 2 - y) ** 2)
    k = int(np.argmin(distance))
    return float(distance[k]), int(selected[k])


def fine_entry(ctx: 'AuditContext',
               entries: 'Iterable[EntryPoint]') -> tuple[float, Point3, dict[str, Any]] | None:
    """(distance, point, area info) of the nearest kept sample surface within an entry point's radius (to a standing
    soldier's middle) that lies inside a built area of its floor; main-network ground first. None when there is none."""
    nav = ctx['nav']
    best = None                         # ((not main, distance), distance, point, area info)
    for entry in entries:
        x, y, z = entry['point']
        grid = grid_at(ctx, x, z)
        s = grid.s
        reach_cells = int(math.ceil(entry['radius'] / s.pitch))
        ci, cj = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        candidates = []
        for j in range(cj - reach_cells, cj + reach_cells + 1):
            for i in range(ci - reach_cells, ci + reach_cells + 1):
                if not (0 <= i < s.nx and 0 <= j < s.nz):
                    continue
                for k in grid.surfaces(j * s.nx + i):
                    if grid.keep[k]:
                        px, py, pz = s.min_x + i * s.pitch, float(s.y[k]), s.min_z + j * s.pitch
                        distance = math.sqrt((px - x) ** 2 + (pz - z) ** 2
                                             + (py + soldier1943.HEIGHT / 2 - y) ** 2)
                        if distance <= entry['radius']:
                            candidates.append((distance, (px, py, pz)))
        for distance, (px, py, pz) in sorted(candidates):
            same, _ = nav.locate(px, pz, py, r=0.5)
            if same is not None and same[0] <= 0.05:
                info = nav.area_info(same, py)
                key = (not info['main'], distance)
                if best is None or key < best[0]:
                    best = (key, distance, (px, py, pz), info)
                break
    return None if best is None else best[1:]


def vehicle_entries(ctx: 'AuditContext', spawn_guid: str | None) -> list[EntryPoint]:
    """The entry points of the vehicle a spawn makes, in the world: [{'point', 'radius', 'order'}] from the vehicle
    blueprint's PlayerEntryComponentData (Transform, EntryRadius, EntryOrderNumber), placed by the spawn's transform.
    [] when the blueprint has none. Entry points of parts that move (turrets) are taken at their rest transform."""
    from bfbc2core import dbx
    cache = ctx.setdefault('_entries', {})
    entity = ctx['entity_by_guid'].get((spawn_guid or '').lower())
    if entity is None or entity.matrix is None:
        return []
    vehicle = entity.element.child('Vehicle')
    ref = vehicle.attr('ref') if vehicle is not None else None
    if not ref or ref == 'null':
        return []
    part = ref.rsplit('/', 1)[0]                # the vehicle blueprint's partition
    if part not in cache:
        out = []
        data = ctx['g'].data(part + '.dbx')
        if data:
            for inst in dbx.parse(data).instances:
                if 'PlayerEntryComponentData' not in (inst.attr('type') or ''):
                    continue
                fields = {(child.attr('name') or child.tag): child for child in inst.children or ()}
                try:
                    matrix = [float(t) for t in dbx.value(fields['Transform']).split('/')]
                    radius = float(dbx.value(fields['EntryRadius']))
                    order_field = fields.get('EntryOrderNumber')
                    order = int(dbx.value(order_field)) if order_field is not None else 0
                except (KeyError, ValueError, TypeError):
                    continue
                out.append((np.array(matrix[12:15]), radius, order))
        cache[part] = out
    spawn_matrix = np.asarray(entity.matrix, float)
    return [{'point': (local @ spawn_matrix[:3, :3] + spawn_matrix[3, :3]).tolist(), 'radius': radius, 'order': order}
            for local, radius, order in cache[part]]


def check_position(ctx: 'AuditContext', position: Position) -> CheckResult:
    """One required position judged against the build: status ('ok', 'ok (not main network)', 'fail', 'not
    required', 'info (base marker)'), the reason, the nearest areas, the sample column under it."""
    x, y, z = position['position']
    grid = grid_at(ctx, x, z)
    c, _surface, floor_y = floor_of(grid, x, y, z)
    res = {k: v for k, v in position.items() if k != 'capture_area'}
    res['grid'] = grid.label
    if floor_y is None:
        res.update(status='fail', reason='no sampled surface under the position', floor_y=None)
        return res
    res['floor_y'] = round(floor_y, 2)
    res['height_above_floor'] = round(y - floor_y, 2)
    reach = navaudit1943.OBJECTIVE_REACH
    floor_for_nav = floor_y
    # a floor is in a column: c is not None from here on
    if position.get('class') == 'boat' and grid.top[cast(int, c)] > floor_y + 0.3:
        # an LCVP in the water: boarded from ground within BOAT_REACH that is at most WATER_DEPTH under the water
        reach = BOAT_REACH
        floor_for_nav = float(grid.top[cast(int, c)]) - soldier1943.WATER_DEPTH / 2
        res['afloat'] = True
    nav = ctx['nav']
    dy = navaudit1943.FLOOR_DY if not res.get('afloat') else soldier1943.WATER_DEPTH / 2 + 0.6
    same, any_floor = nav.locate(x, z, floor_for_nav, r=30.0, dy=dy)
    res['nearest_same_floor'] = nav.area_info(same, floor_y)
    res['nearest_any_floor'] = nav.area_info(any_floor, floor_y)
    col = grid.describe(cast(int, c), floor_y - navaudit1943.LAYER_WINDOW, floor_y + navaudit1943.LAYER_WINDOW,
                        ctx['mm'])
    res['column'] = col
    res['objects'] = objects_at(ctx, x, floor_y, z)
    if position.get('class') == 'aircraft' and y - floor_y > AIRBORNE:
        res.update(status='not required',
                   reason=f'an aircraft spawned {y - floor_y:.1f} m above its floor (in the air)')
        return res
    if position['kind'] == 'base':
        # a base cannot be captured: its soldier spawns are the requirement, its marker is reported only
        res['status'] = 'info (base marker)'
        res['reason'] = (f'marker {y - floor_y:.1f} m above the surface under it; nearest area on any floor '
                         f'{any_floor[0]:.1f} m away, {any_floor[2] - floor_y:+.1f} m' if any_floor
                         else 'no area within 30 m')
        return res
    if position['kind'] == 'vehicle spawn' and position.get('entries'):
        # entered from walkable ground within the vehicle's own EntryRadius of one of its entry points (the game's
        # PlayerEntryComponentData); Wake Island's air-raid bunker is entered from outside its sealed room
        best = None                             # (distance, cell, entry point)
        for main_only in (True, False):             # main-network ground within the radius first: a bot must reach it
            best = None
            for entry in position['entries']:
                distance, cell = nearest_cell_3d(ctx, entry['point'], main_only=main_only)
                if cell is not None and (best is None or distance - entry['radius'] < best[0] - best[2]['radius']):
                    best = (cast(float, distance), cell, entry)     # a cell has its distance
            if best is not None and best[0] <= best[2]['radius']:
                break
        if best is not None:
            distance, cell, entry = best
            island = nav.nav.areas[nav.nav.cell_area[cell]].island
            nearest_walkable = [round(float(nav.nav.cell_xz[cell, 0]), 2), round(float(nav.nav.cell_y[cell]), 2),
                                round(float(nav.nav.cell_xz[cell, 1]), 2)]
            res['entry'] = {'point': [round(v, 2) for v in entry['point']], 'radius': entry['radius'],
                            'seat': entry['order'], 'nearest_walkable': nearest_walkable,
                            'distance': round(distance, 2), 'island': int(island), 'main': island == nav.main}
            if distance <= entry['radius']:
                res['status'] = 'ok' if island == nav.main else 'ok (not main network)'
                res['note'] = (f"entered within its EntryRadius {entry['radius']:g} m: walkable ground "
                               f"{distance:.2f} m from the entry point")
                return res
            # the display cells are 0.8 m apart: for a small radius (mounted MGs: 1 m), the kept samples themselves,
            # each confirmed inside a built area
            fine = fine_entry(ctx, position['entries'])
            if fine is not None:
                fine_distance, point, info = fine
                res['entry'].update(nearest_walkable=[round(v, 2) for v in point], distance=round(fine_distance, 2),
                                    island=info['island'], main=info['main'])
                res['status'] = 'ok' if info['main'] else 'ok (not main network)'
                res['note'] = (f"entered within its EntryRadius {res['entry']['radius']:g} m: walkable ground "
                               f"{fine_distance:.2f} m from the entry point (kept samples)")
                return res
            res['status'] = 'fail'
            res['reason'] = (f"no walkable ground within its EntryRadius {entry['radius']:g} m of an entry point "
                             f"(nearest {distance:.2f} m)")
            return res
    if position.get('class') == 'stationary weapon':
        # the gunner stands at the gun, on its floor or below it: a window mount's gun sits in the aperture 1.3-1.6 m
        # over the bunker floor (Wake Island's beach and airfield bunkers, mil_bunker_t01v01)
        gunner_y = floor_y - (MOUNT_DROP - navaudit1943.FLOOR_DY) / 2
        gunner_same, _ = nav.locate(x, z, gunner_y, r=30.0, dy=(MOUNT_DROP + navaudit1943.FLOOR_DY) / 2)
        objective_reach = navaudit1943.OBJECTIVE_REACH
        if gunner_same is not None and gunner_same[0] <= objective_reach and (same is None or
                                                                             same[0] > objective_reach):
            res['gunner_floor'] = nav.area_info(gunner_same, floor_y)
            res['status'] = 'ok' if res['gunner_floor']['main'] else 'ok (not main network)'
            res['note'] = (f"gunner's floor {gunner_same[2] - floor_y:+.2f} m below the mount, "
                           f"{gunner_same[0]:.1f} m away")
            return res
    vehicle = position['kind'] == 'vehicle spawn' and position.get('class') != 'stationary weapon'
    if vehicle and not res.get('afloat'):
        best_3d = None                          # the nearest area's 3-D distance
        for hit in (same, any_floor):
            if hit is not None:
                distance_3d = math.hypot(hit[0], hit[2] - floor_y)
                best_3d = distance_3d if best_3d is None else min(best_3d, distance_3d)
        res['walkable_3d_distance'] = None if best_3d is None else round(best_3d, 2)
        if best_3d is not None and best_3d <= VEHICLE_REACH:
            same_in_reach = same is not None and math.hypot(same[0], same[2] - floor_y) <= VEHICLE_REACH
            info = res['nearest_same_floor'] if same_in_reach else res['nearest_any_floor']
            # an area within reach (same or any_floor) has its record
            on_main = info['main']  # pyright: ignore[reportOptionalSubscript]
            res['status'] = 'ok' if on_main else 'ok (not main network)'
            return res
        reach = VEHICLE_REACH
    if same is not None and same[0] <= reach:
        res['status'] = 'ok'
        if same[0] > 0:
            res['note'] = f'on the floor of an area {same[0]:.1f} m away'
        # same is not None: its record
        same_main = res['nearest_same_floor']['main']  # pyright: ignore[reportOptionalSubscript]
        if not same_main and position['kind'] not in ('repair station',):
            res['status'] = 'ok (not main network)'
        return res
    res['status'] = 'fail'
    floor = next((surface for surface in col['surfaces'] if abs(surface['y'] - round(floor_y, 2)) < 0.011), None)
    why = []
    if same is not None:
        why.append(f'nearest area on its floor is {same[0]:.1f} m away (reach {reach:g} m)')
    else:
        why.append('no area on its floor within 30 m')
    if any_floor is not None and any_floor[0] <= reach:
        why.append(f'an area on another floor is {any_floor[0]:.1f} m away, {any_floor[2] - floor_y:+.2f} m')
    if floor is not None:
        if floor['kept']:
            why.append('its floor surface is kept walkable, but no area covers it (lost when areas were built)')
        else:
            why.append('floor surface not walkable: ' + '; '.join(floor['why_not']))
    res['reason'] = '; '.join(why)
    return res


def capture_share(ctx: 'AuditContext', position: Position) -> dict[str, Any] | None:
    """Walkable share of a capture area (or repair station): its land, and how much of it the main network covers."""
    poly = position.get('capture_area')
    if poly is None:
        radius = position.get('capture_radius') or 0
        if radius <= 0:
            return None
        angle = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        centre = position['position']
        poly = np.stack([centre[0] + radius * np.cos(angle), np.full(48, centre[1]),
                         centre[2] + radius * np.sin(angle)], 1)
        src = f'capture radius {radius:g} m'
    else:
        src = 'HotZone outline'
    x0, z0 = poly[:, 0].min(), poly[:, 2].min()
    x1, z1 = poly[:, 0].max(), poly[:, 2].max()
    step = 0.8
    grid_x, grid_z = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(z0, z1 + step, step))
    grid_x, grid_z = grid_x.ravel(), grid_z.ravel()
    ins = in_poly(grid_x, grid_z, poly)
    grid_x, grid_z = grid_x[ins], grid_z[ins]
    terrain_y = ctx['hf'].height(grid_x, grid_z)
    grid = ctx['grids']['normal']
    columns = (grid.col(a, b) for a, b in zip(grid_x, grid_z))
    tops = np.array([grid.top[c] if c is not None else -np.inf for c in columns])
    land = terrain_y >= tops - soldier1943.WATER_DEPTH
    nav = ctx['nav']
    cx, cz = nav.nav.cell_xz[:, 0], nav.nav.cell_xz[:, 1]
    near = (cx >= x0) & (cx <= x1) & (cz >= z0) & (cz <= z1)
    near_cells = np.nonzero(near)[0]
    inside = near_cells[in_poly(cx[near_cells], cz[near_cells], poly)]
    islands = np.array([nav.nav.areas[a].island for a in nav.nav.cell_area[inside]], int)
    main = int((islands == nav.main).sum())
    land_m2 = float(land.sum()) * step * step
    area = poly_area(poly)
    other_islands = collections.Counter(islands[islands != nav.main]).most_common(5)
    out: dict[str, Any] = {'source': src, 'area_m2': round(area, 0), 'land_m2': round(land_m2, 0),
                           'walkable_m2': round(len(inside) * 0.64, 0), 'main_network_m2': round(main * 0.64, 0),
                           'other_islands': {int(k): int(v) for k, v in other_islands}}
    if position['kind'] == 'flag' or land_m2 >= 0.25 * area:
        # on land: the main network's share of the land (a flag must be held from the network the bots walk)
        out['share'] = round(main * 0.64 / land_m2, 3) if land_m2 else None
        out['share_of'] = 'land, main network'
    else:
        # over water (a carrier's deck): the share of the outline covered by walkable ground on any island
        out['share'] = round(len(inside) * 0.64 / area, 3) if area else None
        out['share_of'] = 'outline, any island (over water)'
    if position['kind'] == 'base':
        out['status'] = 'info (base)'
    else:
        out['status'] = 'ok' if out['share'] is not None and out['share'] >= USABLE_SHARE else 'fail'
    return out

