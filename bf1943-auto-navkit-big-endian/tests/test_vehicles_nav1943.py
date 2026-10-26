"""NavKit's vehicle navigation (groundnav1943, seanav1943, rsnvgrid1943, core rsnvtrip) on made-up data (no game
data):

  1. Ground: a flat 32 x 20 m yard with a 0.45 m kerb across it and a wall with a 6 m gap. The jeep's rules (step
     0.3 m) keep the kerb as a border (3 pieces with the wall), the tank's (0.6 m) drive over it; the disc keeps off the yard's edge; the
     route graph has exactly the drivable grid's components, every edge a straight drivable line; the world written
     by rsnvgrid1943 validates, every node lies on its cells, and the reference search finds the tank's way through
     the gap and none for the jeep.
  2. Sea grid helpers: dilation, the distance field, the straight cell line, a triangle crossing the waterline band.
  3. Trips (rsnvtrip): a soldier world of two islands (a deck and a beach) and a vehicle world between them with a
     board and an alight transfer: the plan rides (its cost the sum of its legs, the ride scaled), its legs' A* costs
     equal the Dijkstra legs; a goal on the deck walks; a transfer whose stored soldier node is stale is resolved
     again by position; a team without a board transfer gets no trip.
  4. The class table: every class's clearance at least its half-width, and the [O] sizes as read from the game
     (vehicleclasses1943.CLASSES) self-consistent.

  python -B test_vehicles_nav1943.py
"""
import math
import os
import sys
import tempfile

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), 'tools'), os.path.join(SUITE, 'core')]
import groundnav1943 as gn  # noqa: E402
import rsnvgrid1943 as rg  # noqa: E402
import seanav1943 as sn  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import rsnv, rsnvtrip  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


UP = (0.0, 1.0, 0.0)


def yard():
    """40 x 25 columns of 0.8 m at y 10; from i = 20 the ground is 0.45 m higher (the kerb); at j = 12 a wall (blocked
    samples) across, with a gap at i = 30..37."""
    nx, nz = 40, 25
    cols = []
    for j in range(nz):
        for i in range(nx):
            y = 10.45 if i >= 20 else 10.0
            blocked = 1 if (j == 12 and not 30 <= i <= 37) else 0
            cols.append([(y, UP, blocked)])
    return gn.Grid.from_columns(0.8, 0.0, 0.0, nx, nz, cols)


def ground_tests(tmp):
    g = yard()
    out = {}
    for cls in ('jeep', 'tank'):
        ok = gn.drivable(g, cls, sea_level=0.0, log=lambda *a: None)
        table, pairs = gn.link_table(g, ok, vc.value(cls, 'step'))
        labels = gn.components(g.n, pairs)
        comps = set(labels[ok].tolist())
        nodes, edges = gn.build_graph(g, ok, table, labels, 6, log=lambda *a: None)
        idx = {n: k for k, n in enumerate(nodes)}
        glab = gn.components(len(nodes), np.array([(idx[a], idx[b]) for a, b in edges], np.int64).reshape(-1, 2))
        out[cls] = (g, ok, table, labels, comps, nodes, edges, glab)
        rc = int(math.floor((vc.value(cls, 'radius') - 0.4) / 0.8 + 1e-9))
        edge_ok = not ok[(g.i < rc) | (g.j < rc) | (g.i > g.nx - 1 - rc) | (g.j > g.nz - 1 - rc)].any()
        check(edge_ok, f'{cls}: the disc ({vc.value(cls, "radius")} m) keeps {rc} cells off the yard\'s edge')
        check(len(set(glab.tolist())) == len(comps),
              f'{cls}: the route graph has the drivable grid\'s {len(comps)} components ({len(nodes)} nodes, {len(edges)} edges)')
        straight = all(gn.grid_line(g, table, a, b) is not None for a, b in edges[:400])
        check(straight, f'{cls}: every edge is a straight drivable line')
    jeep_c, tank_c = out['jeep'][4], out['tank'][4]
    check(len(jeep_c) == 3, f'jeep: the 0.45 m kerb and the wall cut the yard into 3 pieces ({len(jeep_c)})')
    check(len(tank_c) == 1, f'tank: over the kerb and through the gap, one piece ({len(tank_c)})')
    # the RSNV world of the tank
    g, ok, table, labels, comps, nodes, edges, _ = out['tank']
    ks = np.nonzero(ok)[0]
    local = np.full(g.n, -1, np.int64)
    local[ks] = np.arange(len(ks))
    links = {(int(local[k]), di, dj): int(local[n]) for (k, di, dj), n in table.items() if (di, dj) in rg.STRAIGHT}
    cs = rg.CellSet(g.pitch, g.x0, g.z0, g.i[ks], g.j[ks], g.y[ks], links, name='yard')
    node_list = [(0, int(local[n])) for n in nodes]
    nid = {n: k for k, n in enumerate(nodes)}
    edge_list = [(nid[a], nid[b], rsnv.EDGE_WALK, None, True) for a, b in edges]
    path = os.path.join(tmp, 'yard.tank.rsnv')
    header = {'sea_level': 0.0, 'radius': 1.8, 'height': 2.9, 'step': 0.6, 'water_depth': 1.2, 'sha256': '0' * 8}
    stats = rg.write_world('yard', 'tank', [cs], node_list, edge_list, [], path, (0, 0), header, {}, log=lambda *a: None)
    w = rsnv.load(path)
    check(not w.validate(), f'the tank\'s yard world validates ({stats["nodes"]} nodes, {stats["areas"]} areas, '
                            f'{stats["tight edges"]} tight edges)')
    off = sum(1 for n in range(len(w.node_pos)) if (lambda a, c: c is None or not w.walkable(a, *c))(
        w.areas[int(w.node_area[n])], w.areas[int(w.node_area[n])].cell(w.node_pos[n][0], w.node_pos[n][2])))
    check(off == 0, f'every node lies on its own cells ({off} off)')
    lo = int(np.argmin(w.node_pos[:, 2] + w.node_pos[:, 0] * 0.01))     # near z = 0, low x
    hi = int(np.argmax(w.node_pos[:, 2] - w.node_pos[:, 0] * 0.01))     # near z = max, low x: behind the wall
    st, cost, p, _e = rsnv.search(w, lo, hi)
    xs = [w.node_pos[n][0] for n in p]
    check(st == rsnv.DONE and max(xs) >= 30 * 0.8 - 2, f'the tank\'s route around the wall goes through the gap '
                                                       f'(x up to {max(xs):.1f} m, {cost / 16:.1f} m)')
    los = all(w.los(tuple(float(np.float32(v)) for v in w.node_pos[a]), tuple(float(np.float32(v)) for v in w.node_pos[b]))
              for a, b in zip(p, p[1:]))
    check(los, 'the runtime\'s line of sight holds along the route')


def sea_tests():
    m = np.zeros((9, 9), bool)
    m[4, 4] = True
    d = sn.dilate(m, 2.0)
    check(int(d.sum()) == 13, f'dilate by 2 cells: a disc of 13 cells ({int(d.sum())})')
    water = np.ones((9, 9), bool)
    water[:, 0] = False
    df = sn.distance_field(water, 6)
    check(df[4, 1] == 1 and df[4, 4] == 4 and df[4, 8] == 6, f'distance field from the shore ({df[4, 1]}, {df[4, 4]}, {df[4, 8]})')
    nav = np.ones((5, 5), bool)
    nav[2, 2] = False
    check(sn.cell_line(nav, (0, 0), (4, 0)) and not sn.cell_line(nav, (0, 2), (4, 2)),
          'a straight cell line passes open water and not an island')
    T = np.array([[[0, 9.5, 0], [4, 10.5, 0], [0, 10.5, 4]], [[0, 20, 0], [4, 20, 0], [0, 20, 4]]], np.float32)
    band, n = sn.rasterise_band(T, 9.0, 12.0, -1.0, -1.0, 8, 8, 1.0)
    check(n == 1 and band.any() and band[1, 1], f'a triangle crossing the waterline band blocks its cells; one above does not ({n})')


def flat_world(path, cls, cells_x, nodes_x, z=0.0, y=10.0, island_split=None, offm=()):
    """A strip of cells (x indices cells_x, 2 m pitch, j 0..2) and nodes at x positions; edges between neighbours
    except across island_split (a gap)."""
    ii, jj = [], []
    for i in cells_x:
        for j in range(3):
            ii.append(i)
            jj.append(j)
    idx = {(i, j): k for k, (i, j) in enumerate(zip(ii, jj))}
    links = {}
    for (i, j), k in idx.items():
        for di, dj in rg.STRAIGHT:
            n = idx.get((i + di, j + dj))
            if n is not None and not (island_split and min(i, i + di) < island_split <= max(i, i + di)):
                links[(k, di, dj)] = n
    cs = rg.CellSet(2.0, 0.0, z, ii, jj, [y] * len(ii), links, name=cls)
    node_list = [(0, idx[(int(round(x / 2.0)), 1)]) for x in nodes_x]
    edges = []
    for a in range(len(nodes_x) - 1):
        if island_split and nodes_x[a] / 2.0 < island_split <= nodes_x[a + 1] / 2.0:
            continue
        edges.append((a, a + 1, rsnv.EDGE_WALK, None, True))
    header = {'sea_level': 0.0, 'radius': 0.35, 'height': 1.8, 'step': 0.37, 'water_depth': 1.4, 'sha256': '0' * 8}
    rg.write_world('trip', cls, [cs], node_list, edges, list(offm), path, (0, 0), header, {}, flag_tight=False,
                   log=lambda *a: None)
    return rsnv.load(path)


def trip_tests(tmp):
    # soldier world: a deck (x 0..20) and a beach (x 80..120), no way between
    sx = [0, 4, 8, 12, 16, 20, 80, 84, 88, 92, 96, 100, 104, 108, 112, 116, 120]
    S = flat_world(os.path.join(tmp, 'trip.rsnv'), 'jeep', list(range(0, 11)) + list(range(40, 61)), sx, island_split=30)
    deck = [n for n in range(len(S.node_pos)) if S.node_pos[n][0] <= 20]
    beach = [n for n in range(len(S.node_pos)) if S.node_pos[n][0] >= 80]
    check(len(set(S.node_island[deck].tolist())) == 1 and S.node_island[deck[0]] != S.node_island[beach[0]],
          'the soldier world: a deck and a beach, two islands')
    # vehicle world: x 20..80 (a boat), board at its first node (soldier node 5, x 20), alight at its last (x 80)
    vx = [20, 30, 40, 50, 60, 70, 80]
    board = (rsnvtrip.OFFM_BOARD, 1, 3, 0, 20.0, 10.0, 0.0 + 2.0, 0.0, 8.0, 5)
    alight = (rsnvtrip.OFFM_ALIGHT, 3, 3, 6, 80.0, 10.0, 2.0, 0.0, 1.0, 6)
    V = flat_world(os.path.join(tmp, 'trip.lcvp.rsnv'), 'lcvp', list(range(10, 41)), vx, offm=[board, alight])
    start, goal = deck[0], beach[-1]
    t = rsnvtrip.plan(S, V, start, goal, team=1)
    want = t['walk1'] + rsnvtrip.DEFAULTS['board_cost'] + (t['ride'] * rsnvtrip.DEFAULTS['vehicle_scale'] >> 8) + \
        rsnvtrip.DEFAULTS['alight_cost'] + t['walk2']
    check(t['mode'] == 'vehicle' and t['cost'] == want and t['ride'] == 60 * 16,
          f'deck -> beach rides the vehicle: cost {t["cost"]} = legs {t.get("walk1")} + {t.get("ride")} + {t.get("walk2")}')
    legs = rsnvtrip.legs(S, V, t, start, goal)
    check([l[2] for l in legs] == [t['walk1'], t['ride'], t['walk2']] and all(l[1] == rsnv.DONE for l in legs),
          f'the legs\' A* costs equal the plan\'s ({[l[2] for l in legs]})')
    w = rsnvtrip.plan(S, V, deck[0], deck[-1], team=1)
    check(w['mode'] == 'walk', f'deck -> deck walks ({w["mode"]})')
    n = rsnvtrip.plan(S, V, start, goal, team=2)
    check(n['mode'] is None, 'a team without a board transfer gets no trip')
    stale = list(board)
    stale[9] = 0                       # a soldier node far from the entry point: resolved again by position
    check(rsnvtrip.resolve(S, tuple(stale)) == 5, f'a stale soldier node is resolved again by position '
                                                   f'({rsnvtrip.resolve(S, tuple(stale))})')


def class_tests():
    ok = all(vc.value(c, 'radius') >= vc.value(c, 'half_width') for c in vc.CLASSES)
    check(ok, 'every class\'s clearance radius is at least its half-width')
    check(vc.value('tank', 'step') > vc.value('jeep', 'step') and vc.value('tank', 'slope') > vc.value('jeep', 'slope'),
          'tanks climb more than jeeps (step, slope)')
    check(set(vc.CLASS_IDS) == set(vc.CLASSES) and len(set(vc.CLASS_IDS.values())) == len(vc.CLASS_IDS),
          'every class has its own world id')


def main():
    tmp = tempfile.mkdtemp(prefix='vehnav_')
    ground_tests(tmp)
    sea_tests()
    trip_tests(tmp)
    class_tests()
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
