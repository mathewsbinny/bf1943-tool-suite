"""Trips across navigation worlds: a soldier who walks to a vehicle, rides it and walks on (operation-rising-sun
research/NAVIGATION_RUNTIME_DESIGN.md, section 4.2: "the brain layer plans the whole trip as one route over ISLD
transfers"). The Python reference of rsnav's rsn_trip_plan (rsnav_vehicle.c), which must give the same answer.

    S = rsnv.load('wake_island_s.rsnv'); V = rsnv.load('wake_island_s.lcvp.rsnv')
    t = plan(S, V, start, goal, team=1)        # {'mode': 'walk' | 'vehicle' | None, 'cost', legs, transfers}

The worlds are separate RSNV files (NavKit's groundnav1943 / seanav1943 / airnav1943 write the vehicle worlds). A
vehicle world's OFFM records are its transfers:
  kind BOARD_VEHICLE (1): soldier -> vehicle; node: the vehicle world's node; x, y, z: the entry point; radius: its
    EntryRadius; aux: the soldier node the exporter resolved;
  kind ALIGHT_ZONE (2) and DROP (4, a parachute): vehicle -> soldier; x, y, z: the soldier node's position; aux: that
    node;
  kind LCVP_LAUNCH (3): information only (the deck lane's end; the graph has the drop edge itself).
A transfer's soldier node is checked against the soldier world in use (it may have been rebuilt since): kept when it
lies within the reach of x, y, z (BOARD: radius + 2 m horizontally and 3.5 m vertically; the others: 2 m and 2 m),
else resolved again: BOARD by the same rule (the nearest node within the reach, horizontally, lowest index on a tie);
ALIGHT and DROP (x, y, z: a soldier cell, the unload point) the nearest node within IN_SIGHT_REACH horizontally and
IN_SIGHT_DY vertically that the point has line of sight to (rsnv.World.los, the runtime's rule), nearest first (then
the lowest index), at most IN_SIGHT_TRIES of them (2026-10-29: a rebuilt soldier world moves its nodes; the point does
not move).

The plan, exactly (rsn_trip_plan does the same in the same order):
  1. dS_from: Dijkstra over the soldier world from start; dS_to: Dijkstra over its reversed edges to goal (both
     with the soldier filter's costs, rsnv.edge_cost);
  2. the direct walk costs dS_from[goal];
  3. for each BOARD transfer in OFFM order whose team mask has the team and whose two nodes exist, with
     dS_from[board] reached: dV = Dijkstra over the vehicle world from its node (the vehicle filter); for each
     ALIGHT / DROP transfer in OFFM order with dV and dS_to reached: total = dS_from + board_cost +
     floor(dV x vehicle_scale / 256) + alight_cost + dS_to; the least total wins, the first on a tie;
  4. the walk wins when it costs no more than the best ride. Costs are in 1/16 m of soldier walking: vehicle_scale
     turns the vehicle's metres into the soldier's (a craft at 22 m/s against a soldier at 5 m/s [E]: 58).
"""
import heapq

import numpy as np

from . import rsnv

OFFM_BOARD, OFFM_ALIGHT, OFFM_LAUNCH, OFFM_DROP, OFFM_MOUNT = 1, 2, 3, 4, 5
INF = (1 << 63) - 1
SOLDIER = {'water_scale': (256, 384, 768), 'kind_mask': ((1 << 32) - 1) & ~(1 << rsnv.EDGE_BOARD) & ~(1 << rsnv.EDGE_ALIGHT),
           'tight_extra': 800}
VEHICLE = {'water_scale': (256, 256, 256), 'kind_mask': (1 << 32) - 1, 'tight_extra': 0}
DEFAULTS = {'vehicle_scale': 58, 'board_cost': 160, 'alight_cost': 160}


def soldier_node(S, x, y, z, reach, dy):
    """The node nearest (x, z) horizontally within reach whose height is within dy of y; lowest index on a tie.
    Element-wise IEEE double operations (dx * dx + dz * dz, then the square root), as rsnav computes them."""
    P = S.node_pos
    ddx, ddz = P[:, 0] - x, P[:, 2] - z
    d = np.sqrt(ddx * ddx + ddz * ddz)
    ok = (d <= reach) & (np.abs(P[:, 1] - y) <= dy)
    if not ok.any():
        return None
    k = np.nonzero(ok)[0]
    return int(k[np.argmin(d[k])])


IN_SIGHT_REACH = 6.0          # m: an unload point's soldier node lies within this, horizontally
IN_SIGHT_DY = 2.0
IN_SIGHT_TRIES = 32


def in_sight_node(S, x, y, z, reach=IN_SIGHT_REACH, dy=IN_SIGHT_DY, tries=IN_SIGHT_TRIES):
    """The node nearest (x, z) within reach and dy that (x, y, z) has line of sight to, or None (see the module)."""
    P = S.node_pos
    ddx, ddz = P[:, 0] - x, P[:, 2] - z
    d = np.sqrt(ddx * ddx + ddz * ddz)
    ok = np.nonzero((d <= reach) & (np.abs(P[:, 1] - y) <= dy))[0]
    if not len(ok):
        return None
    order = ok[np.lexsort((ok, d[ok]))][:tries]
    a = (float(np.float32(x)), float(np.float32(y)), float(np.float32(z)))
    for n in order.tolist():
        b = tuple(float(np.float32(v)) for v in P[n])
        if S.los(a, b):
            return int(n)
    return None


def reach_of(rec):
    return (float(rec[8]) + 2.0, 3.5) if rec[0] == OFFM_BOARD else (2.0, 2.0)


def resolve(S, rec):
    """The soldier node of a transfer in the soldier world S, or None (see the module's description)."""
    x, y, z = (float(np.float32(v)) for v in rec[4:7])
    reach, dy = reach_of(rec)
    aux = int(rec[9])
    if aux < len(S.node_pos):
        p = S.node_pos[aux]
        ddx, ddz = float(p[0]) - x, float(p[2]) - z
        if (ddx * ddx + ddz * ddz) ** 0.5 <= reach and abs(float(p[1]) - y) <= dy:
            return aux
    if rec[0] in (OFFM_ALIGHT, OFFM_DROP):
        return in_sight_node(S, x, y, z)
    return soldier_node(S, x, y, z, reach, dy)


def transfers(S, V):
    """[(OFFM index, kind, team mask, vehicle node or None, soldier node or None)] of V's transfers."""
    out = []
    for k, r in enumerate(V.offmesh):
        if r[0] not in (OFFM_BOARD, OFFM_ALIGHT, OFFM_DROP):
            continue
        vn = None if r[3] == 0xFFFFFFFF or r[3] >= len(V.node_pos) else int(r[3])
        out.append((k, int(r[0]), int(r[1]), vn, resolve(S, r)))
    return out


def _cost(w, n, k, f):
    if f.get('kind_mask') is not None and not (f['kind_mask'] >> int(w.edge_kind[k]) & 1):
        return None
    return rsnv.edge_cost(w, n, k, f['water_scale'], None, None, None, f.get('tight_extra', 0))


def dijkstra(w, source, f, reverse=False):
    """Costs from source (to source with reverse) over w's edges with filter f; INF where unreached."""
    n = len(w.node_pos)
    dist = np.full(n, INF, np.int64)
    fe, tgt = w.node_first_edge, w.edge_target
    if reverse:
        if not hasattr(w, '_rev'):
            src = np.repeat(np.arange(n), np.diff(fe))
            order = np.argsort(tgt, kind='stable')
            w._rev = (np.searchsorted(tgt[order], np.arange(n + 1)), src[order], order)
        rfirst, rsrc, redge = w._rev
    dist[source] = 0
    heap = [(0, source)]
    done = np.zeros(n, bool)
    while heap:
        d, u = heapq.heappop(heap)
        if done[u]:
            continue
        done[u] = True
        if reverse:
            items = ((int(rsrc[t]), int(redge[t])) for t in range(rfirst[u], rfirst[u + 1]))
            for v, k in items:
                c = _cost(w, v, k, f)
                if c is not None and d + c < dist[v]:
                    dist[v] = d + c
                    heapq.heappush(heap, (d + c, v))
        else:
            for k in range(fe[u], fe[u + 1]):
                v = int(tgt[k])
                c = _cost(w, u, k, f)
                if c is not None and d + c < dist[v]:
                    dist[v] = d + c
                    heapq.heappush(heap, (d + c, v))
    return dist


def plan(S, V, start, goal, team, params=None, fs=None, fv=None):
    """The best trip from soldier node start to soldier node goal (see the module's description)."""
    p = dict(DEFAULTS, **(params or {}))
    fs, fv = fs or SOLDIER, fv or VEHICLE
    d_from = dijkstra(S, start, fs)
    d_to = dijkstra(S, goal, fs, reverse=True)
    best = None
    if d_from[goal] < INF:
        best = (int(d_from[goal]), None, None)
    tr = transfers(S, V)
    boards = [t for t in tr if t[1] == OFFM_BOARD and t[2] & team and t[3] is not None and t[4] is not None]
    alights = [t for t in tr if t[1] in (OFFM_ALIGHT, OFFM_DROP) and t[3] is not None and t[4] is not None]
    ride_best = None
    for b in boards:
        if d_from[b[4]] >= INF:
            continue
        dv = dijkstra(V, b[3], fv)
        for a in alights:
            if dv[a[3]] >= INF or d_to[a[4]] >= INF:
                continue
            total = int(d_from[b[4]]) + p['board_cost'] + (int(dv[a[3]]) * p['vehicle_scale'] >> 8) + p['alight_cost'] \
                + int(d_to[a[4]])
            if ride_best is None or total < ride_best[0]:
                ride_best = (total, b, a, int(d_from[b[4]]), int(dv[a[3]]), int(d_to[a[4]]))
    if ride_best is not None and (best is None or ride_best[0] < best[0]):
        total, b, a, c1, c2, c3 = ride_best
        return {'mode': 'vehicle', 'cost': total, 'board': b[0], 'alight': a[0], 'soldier board': b[4],
                'vehicle board': b[3], 'vehicle alight': a[3], 'soldier alight': a[4], 'walk1': c1, 'ride': c2,
                'walk2': c3}
    if best is not None:
        return {'mode': 'walk', 'cost': best[0]}
    return {'mode': None, 'cost': None}


def legs(S, V, t, start, goal, fs=None, fv=None):
    """The trip's paths by rsnv.search (A*, the runtime's search): [(world 'S' or 'V', status, cost, [nodes])] - walk
    to the vehicle, ride, walk on; or the one walk."""
    fs, fv = fs or SOLDIER, fv or VEHICLE

    def run(w, a, b, f):
        st, c, path, _e = rsnv.search(w, a, b, kind_mask=f['kind_mask'], water_scale=f['water_scale'],
                                      tight_extra=f.get('tight_extra', 0))
        return st, c, path
    if t['mode'] == 'walk':
        return [('S', *run(S, start, goal, fs))]
    if t['mode'] != 'vehicle':
        return []
    return [('S', *run(S, start, t['soldier board'], fs)), ('V', *run(V, t['vehicle board'], t['vehicle alight'], fv)),
            ('S', *run(S, t['soldier alight'], goal, fs))]
