"""navaudit1943_checks, checks 1 and 5: the required positions, their floors and layers, the capture areas' share."""
import collections
import math

import numpy as np

import navaudit1943 as A  # noqa: E402
import soldier1943  # noqa: E402

from .config import AIRBORNE, AIRCRAFT, BOAT_REACH, BOATS, GROUND, MOUNT_DROP, STATIONARY, USABLE_SHARE, VEHICLE_REACH
from .geometry import in_poly, poly_area
from .loading import grid_at, objects_at


# ------------------------------------------------------------------------------------------------ 1 + 5: positions

def required_positions(C):
    """[{kind, name, team, position, ...}] from the spawn map and the level (BF1943 data only)."""
    out = []
    m, lvl = C['map'], C['lvl']
    hot = [s for s in lvl.shapes if lvl.entities[s.entity].short_type == 'HotZoneEntityData' and s.kind == 'area']
    for o in m['objectives']:
        pos = o['position']
        poly = None
        for s in hot:
            if in_poly([pos[0]], [pos[2]], s.points)[0]:
                poly = s.points
                break
        out.append(dict(kind=o['role'], name=o['name'], team=o.get('initial_owner'), position=pos,
                        capture_radius=o.get('capture_radius'), capture_area=poly))
        for s in o['spawns']:
            if s['kind'] == 'soldier':
                for k, q in enumerate([s['position']] + [a for a in s.get('alternates', []) if isinstance(a, list)]):
                    out.append(dict(kind='soldier spawn', name=s['name'] + (f' alt {k}' if k else ''), team=s.get('team'),
                                    position=q, objective=o['name']))
            else:
                out.append(dict(kind='vehicle spawn', name=s['name'], vehicle=s.get('vehicle'), team=s.get('team'),
                                position=s['position'], objective=o['name'], guid=s.get('guid')))
    for s in m.get('unowned_soldier_spawns', []):
        out.append(dict(kind='soldier spawn', name=s['name'], team=s.get('team'), position=s['position'], objective=None))
    for s in m.get('unowned_vehicle_spawns', []):
        if s.get('vehicle') in (None, 'null') and all(abs(v) < 1e-6 for v in s['position']):
            continue                     # unused templates at the origin with no vehicle (Coral Sea)
        out.append(dict(kind='vehicle spawn', name=s['name'], vehicle=s.get('vehicle'), team=s.get('team'),
                        position=s['position'], objective=None, guid=s.get('guid')))
    for e in lvl.entities:
        if e.short_type == 'RepairStationEntityData' and e.position is not None:
            sh = [s for s in lvl.shapes if s.entity == e.index]
            out.append(dict(kind='repair station', name=f'repair {e.index}', team=e.team, position=[float(v) for v in e.position],
                            capture_area=sh[0].points if sh else None))
    hz_used = {id(r['capture_area']) for r in out if r['kind'] in ('flag', 'base') and r.get('capture_area') is not None}
    for s in hot:
        if id(s.points) not in hz_used:
            c = s.points.mean(0)
            out.append(dict(kind='hot zone without a flag', name=f'hotzone {s.entity}', team=None,
                            position=[float(c[0]), float(c[1]), float(c[2])], capture_area=s.points))
    for r in out:
        if r['kind'] == 'vehicle spawn':
            v = r.get('vehicle') or ''
            r['class'] = ('ground vehicle' if v in GROUND else 'boat' if v in BOATS else 'aircraft' if v in AIRCRAFT
                          else 'stationary weapon' if v in STATIONARY else 'other')
            r['entries'] = vehicle_entries(C, r.get('guid'))
    # entities left at the origin are unused placeholders (Coral Sea: 3 flags, 2 soldier spawns, 4 repair stations,
    # 16 vehicle spawns), not positions in the level: listed apart, not checked
    C['placeholders'] = [{k: v for k, v in r.items() if k != 'capture_area'} for r in out
                         if all(abs(v) < 1e-6 for v in r['position'])]
    return [r for r in out if not all(abs(v) < 1e-6 for v in r['position'])]


def floor_of(G, x, y, z):
    """The surface a position stands on: the highest at most 0.5 m above it (spawns sit on or just above their
    floor), else the lowest surface of the column. (surface index, height) or (None, None)."""
    c = G.col(x, z)
    if c is None:
        return None, None, None
    idx = list(G.surfaces(c))
    if not idx:
        return c, None, None
    below = [n for n in idx if G.s.y[n] <= y + 0.5]
    n = max(below, key=lambda n: G.s.y[n]) if below else min(idx, key=lambda n: G.s.y[n])
    # a position under the terrain (Wake Island's Beach alternate 3: 0.92 m under a stony slope, inside a boulder's
    # buried part) stands on the terrain over it, not on what is buried under it
    cx, cz = G.centre(c)
    t = float(G.hf.height(cx, cz))
    if y < t:
        on_t = [m for m in idx if abs(G.s.y[m] - t) < 0.06]
        if on_t:
            n = on_t[0]
    return c, n, float(G.s.y[n])


def nearest_cell_3d(C, p, r=8.0, main_only=False):
    """(distance, cell) of the build's walkable cell nearest a point, measured to a standing soldier's middle (ground +
    HEIGHT / 2); cells within r horizontally (and on the main network with main_only). Cells are the 0.8 m display
    grid: about 0.4 m coarse."""
    nav = C['nav'].nav
    x, y, z = p
    near = (np.abs(nav.cell_xz[:, 0] - x) <= r) & (np.abs(nav.cell_xz[:, 1] - z) <= r)
    if main_only:
        if '_cell_island' not in C:
            C['_cell_island'] = np.array([a.island for a in nav.areas])[nav.cell_area]
        near &= C['_cell_island'] == C['nav'].main
    sel = np.nonzero(near)[0]
    if not len(sel):
        return None, None
    d = np.sqrt((nav.cell_xz[sel, 0] - x) ** 2 + (nav.cell_xz[sel, 1] - z) ** 2
                + (nav.cell_y[sel] + soldier1943.HEIGHT / 2 - y) ** 2)
    k = int(np.argmin(d))
    return float(d[k]), int(sel[k])


def fine_entry(C, entries):
    """(distance, point, area info) of the nearest kept sample surface within an entry point's radius (to a standing
    soldier's middle) that lies inside a built area of its floor; main-network ground first. None when there is none."""
    nv = C['nav']
    best = None
    for ent in entries:
        x, y, z = ent['point']
        G = grid_at(C, x, z)
        s = G.s
        n = int(math.ceil(ent['radius'] / s.pitch))
        ci, cj = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        cand = []
        for j in range(cj - n, cj + n + 1):
            for i in range(ci - n, ci + n + 1):
                if not (0 <= i < s.nx and 0 <= j < s.nz):
                    continue
                for k in G.surfaces(j * s.nx + i):
                    if G.keep[k]:
                        px, py, pz = s.min_x + i * s.pitch, float(s.y[k]), s.min_z + j * s.pitch
                        d = math.sqrt((px - x) ** 2 + (pz - z) ** 2 + (py + soldier1943.HEIGHT / 2 - y) ** 2)
                        if d <= ent['radius']:
                            cand.append((d, (px, py, pz)))
        for d, (px, py, pz) in sorted(cand):
            same, _ = nv.locate(px, pz, py, r=0.5)
            if same is not None and same[0] <= 0.05:
                info = nv.area_info(same, py)
                key = (not info['main'], d)
                if best is None or key < best[0]:
                    best = (key, d, (px, py, pz), info)
                break
    return None if best is None else best[1:]


def vehicle_entries(C, spawn_guid):
    """The entry points of the vehicle a spawn makes, in the world: [{'point', 'radius', 'order'}] from the vehicle
    blueprint's PlayerEntryComponentData (Transform, EntryRadius, EntryOrderNumber), placed by the spawn's transform.
    [] when the blueprint has none. Entry points of parts that move (turrets) are taken at their rest transform."""
    from bfbc2core import dbx
    cache = C.setdefault('_entries', {})
    e = C['entity_by_guid'].get((spawn_guid or '').lower())
    if e is None or e.matrix is None:
        return []
    v = e.element.child('Vehicle')
    ref = v.attr('ref') if v is not None else None
    if not ref or ref == 'null':
        return []
    part = ref.rsplit('/', 1)[0]
    if part not in cache:
        out = []
        data = C['g'].data(part + '.dbx')
        if data:
            for inst in dbx.parse(data).instances:
                if 'PlayerEntryComponentData' not in (inst.attr('type') or ''):
                    continue
                vals = {(c.attr('name') or c.tag): c for c in inst.children or ()}
                try:
                    m = [float(t) for t in dbx.value(vals['Transform']).split('/')]
                    radius = float(dbx.value(vals['EntryRadius']))
                    order = int(dbx.value(vals.get('EntryOrderNumber'))) if vals.get('EntryOrderNumber') is not None else 0
                except (KeyError, ValueError, TypeError):
                    continue
                out.append((np.array(m[12:15]), radius, order))
        cache[part] = out
    M = np.asarray(e.matrix, float)
    return [{'point': (local @ M[:3, :3] + M[3, :3]).tolist(), 'radius': radius, 'order': order}
            for local, radius, order in cache[part]]


def check_position(C, r):
    x, y, z = r['position']
    G = grid_at(C, x, z)
    c, n, fy = floor_of(G, x, y, z)
    res = {k: v for k, v in r.items() if k != 'capture_area'}
    res['grid'] = G.label
    if fy is None:
        res.update(status='fail', reason='no sampled surface under the position', floor_y=None)
        return res
    res['floor_y'] = round(fy, 2)
    res['height_above_floor'] = round(y - fy, 2)
    reach = A.OBJECTIVE_REACH
    floor_for_nav = fy
    if r.get('class') == 'boat' and G.top[c] > fy + 0.3:
        # an LCVP in the water: boarded from ground within BOAT_REACH that is at most WATER_DEPTH under the water
        reach = BOAT_REACH
        floor_for_nav = float(G.top[c]) - soldier1943.WATER_DEPTH / 2
        res['afloat'] = True
    nv = C['nav']
    dy = A.FLOOR_DY if not res.get('afloat') else soldier1943.WATER_DEPTH / 2 + 0.6
    same, anyf = nv.locate(x, z, floor_for_nav, r=30.0, dy=dy)
    res['nearest_same_floor'] = nv.area_info(same, fy)
    res['nearest_any_floor'] = nv.area_info(anyf, fy)
    col = G.describe(c, fy - A.LAYER_WINDOW, fy + A.LAYER_WINDOW, C['mm'])
    res['column'] = col
    res['objects'] = objects_at(C, x, fy, z)
    if r.get('class') == 'aircraft' and y - fy > AIRBORNE:
        res.update(status='not required', reason=f'an aircraft spawned {y - fy:.1f} m above its floor (in the air)')
        return res
    if r['kind'] == 'base':
        # a base cannot be captured: its soldier spawns are the requirement, its marker is reported only
        res['status'] = 'info (base marker)'
        res['reason'] = (f'marker {y - fy:.1f} m above the surface under it; nearest area on any floor '
                         f'{anyf[0]:.1f} m away, {anyf[2] - fy:+.1f} m' if anyf else 'no area within 30 m')
        return res
    if r['kind'] == 'vehicle spawn' and r.get('entries'):
        # entered from walkable ground within the vehicle's own EntryRadius of one of its entry points (the game's
        # PlayerEntryComponentData); Wake Island's air-raid bunker is entered from outside its sealed room
        best = None
        for main_only in (True, False):             # main-network ground within the radius first: a bot must reach it
            best = None
            for ent in r['entries']:
                d, k = nearest_cell_3d(C, ent['point'], main_only=main_only)
                if k is not None and (best is None or d - ent['radius'] < best[0] - best[2]['radius']):
                    best = (d, k, ent)
            if best is not None and best[0] <= best[2]['radius']:
                break
        if best is not None:
            d, k, ent = best
            isl = nv.nav.areas[nv.nav.cell_area[k]].island
            res['entry'] = {'point': [round(v, 2) for v in ent['point']], 'radius': ent['radius'],
                            'seat': ent['order'], 'nearest_walkable': [round(float(nv.nav.cell_xz[k, 0]), 2),
                                                                      round(float(nv.nav.cell_y[k]), 2),
                                                                      round(float(nv.nav.cell_xz[k, 1]), 2)],
                            'distance': round(d, 2), 'island': int(isl), 'main': isl == nv.main}
            if d <= ent['radius']:
                res['status'] = 'ok' if isl == nv.main else 'ok (not main network)'
                res['note'] = f"entered within its EntryRadius {ent['radius']:g} m: walkable ground {d:.2f} m from the entry point"
                return res
            # the display cells are 0.8 m apart: for a small radius (mounted MGs: 1 m), the kept samples themselves,
            # each confirmed inside a built area
            fine = fine_entry(C, r['entries'])
            if fine is not None:
                d2, pt, info = fine
                res['entry'].update(nearest_walkable=[round(v, 2) for v in pt], distance=round(d2, 2),
                                    island=info['island'], main=info['main'])
                res['status'] = 'ok' if info['main'] else 'ok (not main network)'
                res['note'] = (f"entered within its EntryRadius {res['entry']['radius']:g} m: walkable ground {d2:.2f} m "
                               f"from the entry point (kept samples)")
                return res
            res['status'] = 'fail'
            res['reason'] = (f"no walkable ground within its EntryRadius {ent['radius']:g} m of an entry point "
                             f"(nearest {d:.2f} m)")
            return res
    if r.get('class') == 'stationary weapon':
        # the gunner stands at the gun, on its floor or below it: a window mount's gun sits in the aperture 1.3-1.6 m
        # over the bunker floor (Wake Island's beach and airfield bunkers, mil_bunker_t01v01)
        g_same, _ = nv.locate(x, z, fy - (MOUNT_DROP - A.FLOOR_DY) / 2, r=30.0, dy=(MOUNT_DROP + A.FLOOR_DY) / 2)
        if g_same is not None and g_same[0] <= A.OBJECTIVE_REACH and (same is None or same[0] > A.OBJECTIVE_REACH):
            res['gunner_floor'] = nv.area_info(g_same, fy)
            res['status'] = 'ok' if res['gunner_floor']['main'] else 'ok (not main network)'
            res['note'] = f"gunner's floor {g_same[2] - fy:+.2f} m below the mount, {g_same[0]:.1f} m away"
            return res
    vehicle = r['kind'] == 'vehicle spawn' and r.get('class') != 'stationary weapon'
    if vehicle and not res.get('afloat'):
        best = None
        for rec in (same, anyf):
            if rec is not None:
                d3 = math.hypot(rec[0], rec[2] - fy)
                best = d3 if best is None else min(best, d3)
        res['walkable_3d_distance'] = None if best is None else round(best, 2)
        if best is not None and best <= VEHICLE_REACH:
            info = res['nearest_same_floor'] if same is not None and math.hypot(same[0], same[2] - fy) <= VEHICLE_REACH \
                else res['nearest_any_floor']
            res['status'] = 'ok' if info['main'] else 'ok (not main network)'
            return res
        reach = VEHICLE_REACH
    if same is not None and same[0] <= reach:
        res['status'] = 'ok'
        if same[0] > 0:
            res['note'] = f'on the floor of an area {same[0]:.1f} m away'
        if not res['nearest_same_floor']['main'] and r['kind'] not in ('repair station',):
            res['status'] = 'ok (not main network)'
        return res
    res['status'] = 'fail'
    floor = next((s_ for s_ in col['surfaces'] if abs(s_['y'] - round(fy, 2)) < 0.011), None)
    why = []
    if same is not None:
        why.append(f'nearest area on its floor is {same[0]:.1f} m away (reach {reach:g} m)')
    else:
        why.append('no area on its floor within 30 m')
    if anyf is not None and anyf[0] <= reach:
        why.append(f'an area on another floor is {anyf[0]:.1f} m away, {anyf[2] - fy:+.2f} m')
    if floor is not None:
        if floor['kept']:
            why.append('its floor surface is kept walkable, but no area covers it (lost when areas were built)')
        else:
            why.append('floor surface not walkable: ' + '; '.join(floor['why_not']))
    res['reason'] = '; '.join(why)
    return res


def capture_share(C, r):
    """Walkable share of a capture area (or repair station): its land, and how much of it the main network covers."""
    poly = r.get('capture_area')
    if poly is None:
        rad = r.get('capture_radius') or 0
        if rad <= 0:
            return None
        t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        poly = np.stack([r['position'][0] + rad * np.cos(t), np.full(48, r['position'][1]), r['position'][2] + rad * np.sin(t)], 1)
        src = f'capture radius {rad:g} m'
    else:
        src = 'HotZone outline'
    x0, z0 = poly[:, 0].min(), poly[:, 2].min()
    x1, z1 = poly[:, 0].max(), poly[:, 2].max()
    step = 0.8
    X, Z = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(z0, z1 + step, step))
    X, Z = X.ravel(), Z.ravel()
    ins = in_poly(X, Z, poly)
    X, Z = X[ins], Z[ins]
    t = C['hf'].height(X, Z)
    G = C['grids']['normal']
    tops = np.array([G.top[c] if c is not None else -np.inf for c in (G.col(a, b) for a, b in zip(X, Z))])
    land = t >= tops - soldier1943.WATER_DEPTH
    nv = C['nav']
    cx, cz = nv.nav.cell_xz[:, 0], nv.nav.cell_xz[:, 1]
    near = (cx >= x0) & (cx <= x1) & (cz >= z0) & (cz <= z1)
    idx = np.nonzero(near)[0]
    inside = idx[in_poly(cx[idx], cz[idx], poly)]
    isl = np.array([nv.nav.areas[a].island for a in nv.nav.cell_area[inside]], int)
    main = int((isl == nv.main).sum())
    land_m2 = float(land.sum()) * step * step
    area = poly_area(poly)
    out = {'source': src, 'area_m2': round(area, 0), 'land_m2': round(land_m2, 0),
           'walkable_m2': round(len(inside) * 0.64, 0), 'main_network_m2': round(main * 0.64, 0),
           'other_islands': {int(k): int(v) for k, v in collections.Counter(isl[isl != nv.main]).most_common(5)}}
    if r['kind'] == 'flag' or land_m2 >= 0.25 * area:
        # on land: the main network's share of the land (a flag must be held from the network the bots walk)
        out['share'] = round(main * 0.64 / land_m2, 3) if land_m2 else None
        out['share_of'] = 'land, main network'
    else:
        # over water (a carrier's deck): the share of the outline covered by walkable ground on any island
        out['share'] = round(len(inside) * 0.64 / area, 3) if area else None
        out['share_of'] = 'outline, any island (over water)'
    if r['kind'] == 'base':
        out['status'] = 'info (base)'
    else:
        out['status'] = 'ok' if out['share'] is not None and out['share'] >= USABLE_SHARE else 'fail'
    return out
