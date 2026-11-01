"""navaudit1943_checks, check 2: the landmass, the islands' classes and reachability."""
import collections

import numpy as np

import navaudit1943 as A  # noqa: E402
import soldier1943  # noqa: E402

from .config import BOAT_REACH
from .geometry import in_poly, label
from .loading import objects_at


# ------------------------------------------------------------------------------------------------ 2: reachability

def landmass(C, cell=4.0):
    """Land a soldier can wade to: terrain at most WATER_DEPTH under the water, labelled into connected pieces on a
    4 m grid. Returns (label grid, x0, z0, cell)."""
    hf = C['hf']
    x0, z0 = float(hf.min[0]), float(hf.min[2])
    n = int((hf.max[0] - hf.min[0]) / cell)
    X, Z = np.meshgrid(x0 + (np.arange(n) + 0.5) * cell, z0 + (np.arange(n) + 0.5) * cell)
    T = hf.height(X, Z)
    level = sea_level(C)
    land = T >= level - soldier1943.WATER_DEPTH
    lab, k = label(land)
    return lab, x0, z0, cell


def sea_level(C):
    w = C['water']
    if w is None or not len(w[0]):
        # no water mesh: navkit.json's sea level, or without water the exporters' (2026-10-30: float(None) raised, then
        # -1e9 put the audit's sea elsewhere than the export's)
        return soldier1943.sea_level(C['st'], C['hf'])
    return float(np.median(w[0][:, 1]))


def landmass_at(LM, x, z):
    lab, x0, z0, cell = LM
    i = np.clip(((np.asarray(x) - x0) / cell).astype(int), 0, lab.shape[1] - 1)
    j = np.clip(((np.asarray(z) - z0) / cell).astype(int), 0, lab.shape[0] - 1)
    return lab[j, i]


def classify_islands(C, LM, positions):
    nv = C['nav']
    nav = nv.nav
    hf = C['hf']
    cells_isl = np.array([a.island for a in nav.areas])[nav.cell_area]
    t = hf.height(nav.cell_xz[:, 0], nav.cell_xz[:, 1])
    lm = landmass_at(LM, nav.cell_xz[:, 0], nav.cell_xz[:, 1])
    main_lm = collections.Counter(lm[cells_isl == nv.main]).most_common(1)[0][0]
    level = sea_level(C)
    req = collections.defaultdict(list)
    for r in positions:
        a = r.get('nearest_same_floor')
        if a and a['distance'] <= max(A.OBJECTIVE_REACH, BOAT_REACH if r.get('afloat') else 0) and a['island'] >= 0:
            req[a['island']].append(f"{r['kind']}: {r['name']}")
    npos = nav.node_pos
    nt = hf.height(npos[:, 0], npos[:, 2])
    nlm = landmass_at(LM, npos[:, 0], npos[:, 2])
    out = []
    for k, isl in enumerate(nav.islands):
        sel = cells_isl == k
        if sel.any():
            xs, zs, ys = nav.cell_xz[sel, 0], nav.cell_xz[sel, 1], nav.cell_y[sel]
            tt, ll = t[sel], lm[sel]
        else:
            # areas too narrow to hold a 0.8 m cell: the island's route nodes stand for it
            ns = nav.node_island == k
            xs, zs, ys = npos[ns, 0], npos[ns, 2], nv.node_ground[ns]
            tt, ll = nt[ns], nlm[ns]
        e = ys - tt
        on_main_land = float((ll == main_lm).mean())
        over_water = float((tt < level - soldier1943.WATER_DEPTH).mean())
        raised = float((e > 1.5).mean())
        d = dict(island=k, nodes=isl.nodes, cells=int(sel.sum()), area_m2=round(float(sel.sum()) * 0.64, 0),
                 centre=[round(float(xs.mean()), 1), round(float(ys.mean()), 1), round(float(zs.mean()), 1)],
                 bbox=[round(float(xs.min()), 1), round(float(zs.min()), 1), round(float(xs.max()), 1), round(float(zs.max()), 1)],
                 on_main_landmass=round(on_main_land, 2), over_deep_water=round(over_water, 2),
                 raised_share=round(raised, 2), mean_height_above_terrain=round(float(e.mean()), 1),
                 required=req.get(k, []))
        cx, cy, cz = d['centre']
        d['objects'] = objects_at(C, cx, cy, cz, grow=2.0)
        if k == nv.main:
            d['kind'] = 'main'
        elif over_water > 0.5:
            d['kind'] = 'vehicle-only zone'
            d['linked_by'] = vehicle_link(C, d, positions)
        elif on_main_land < 0.5:
            d['kind'] = 'separate landmass (reef or islet: boat, or swimming)'
            d['linked_by'] = vehicle_link(C, d, positions)
        elif raised > 0.8 and not d['required']:
            d['kind'] = 'raised surface, no access'
        else:
            d['kind'] = 'gap candidate'
        out.append(d)
    return out


def vehicle_link(C, isl, positions):
    """What would join a vehicle-only zone to the rest: the vehicle spawns on or beside it."""
    x0, z0, x1, z1 = isl['bbox']
    vs = [r for r in positions if r['kind'] == 'vehicle spawn' and x0 - 10 <= r['position'][0] <= x1 + 10
          and z0 - 10 <= r['position'][2] <= z1 + 10]
    kinds = collections.Counter(r.get('class') for r in vs)
    out = []
    if kinds.get('boat'):
        out.append(f"boat (landing craft spawns: {kinds['boat']})")
    if kinds.get('aircraft'):
        out.append(f"plane (aircraft spawns: {kinds['aircraft']})")
    if not out:
        out.append('no vehicle spawn on it: a boat landing or a parachute drop from a plane')
    return out


def reachability(C, positions):
    nv = C['nav']
    flags = [r for r in positions if r['kind'] == 'flag']
    targets = {}
    for f in flags:
        poly = f.get('_poly')
        P = nv.nav.node_pos
        if poly is not None:
            ins = in_poly(P[:, 0], P[:, 2], poly)
        else:
            rad = max(f.get('capture_radius') or 0, 5.0)
            ins = np.hypot(P[:, 0] - f['position'][0], P[:, 2] - f['position'][2]) <= rad
        targets[f['name']] = np.nonzero(ins)[0]
    rows = []
    for r in positions:
        if r['kind'] != 'soldier spawn':
            continue
        a = r.get('nearest_same_floor')
        if r.get('floor_y') is None:
            rows.append(dict(spawn=r['name'], joined=False, reason='no floor'))
            continue
        x, _, z = r['position']
        n = nv.join(x, z, r['floor_y'], island=a['island'] if a else None)
        if n is None:
            rows.append(dict(spawn=r['name'], objective=r.get('objective'), joined=False,
                             reason='no route node within 15 m on its floor'))
            continue
        fw, bw = nv.reach(n), nv.reach(n, backward=True)
        res = dict(spawn=r['name'], objective=r.get('objective'), joined=True, node=n,
                   island=int(nv.nav.node_island[n]), main=int(nv.nav.node_island[n]) == nv.main, to={}, back={})
        for name, tg in targets.items():
            res['to'][name] = bool(len(tg) and fw[tg].any())
            res['back'][name] = bool(len(tg) and bw[tg].any())
        rows.append(res)
    return {'targets': {k: int(len(v)) for k, v in targets.items()}, 'spawns': rows}
