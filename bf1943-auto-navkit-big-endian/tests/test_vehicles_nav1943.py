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
  4. Heading-aware planning (rsnvhybrid): the primitive tables are deterministic; in a 4 m wide dead-end strip a car
     (4.5 m turning radius) turns back only by reversing (a plan with reverse moves, every pose on the strip's cells);
     round an L corner 5 m wide it drives forward only; a start a little off the cells is snapped onto them; a goal
     outside the corridor is not reached.
  5. Plane take-off lanes (airnav1943.choose_lane) on a made-up airfield: a clear lane stays the spawn heading; a
     tree on the centre line turns it a few degrees; a hangar roof over the spawn starts it outside; a field ending
     in a drop ahead turns the plane on the ground; a plateau too small for any roll keeps the clear heading, flagged.
  6. The class table: every class's clearance at least its half-width, and the [O] sizes as read from the game
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
import airnav1943 as an  # noqa: E402
import groundnav1943 as gn  # noqa: E402
import rsnvgrid1943 as rg  # noqa: E402
import seanav1943 as sn  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import rsnv, rsnvhybrid, rsnvtrip  # noqa: E402

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
    import math as _m
    src = np.repeat(np.arange(len(w.node_pos)), np.diff(w.node_first_edge))
    p32 = w.node_pos
    ln = [_m.sqrt(((p32[b][0] - p32[a][0]) ** 2 + (p32[b][1] - p32[a][1]) ** 2) + (p32[b][2] - p32[a][2]) ** 2)
          for a, b in zip(src.tolist(), w.edge_target.tolist())]
    adm = all(int(c) >= 16.0 * l * w.h_scale for c, l in zip(w.edge_cost.tolist(), ln))
    from bfbc2core import rsnvtrip as _rt
    dij = _rt.dijkstra(w, lo, _rt.VEHICLE)[hi]
    check(w.h_scale_q > 0 and adm and cost == dij,
          f'admissible: heuristic scale {w.h_scale:.6f} in the header, every edge costs at least 16 x scale x its length, '
          f'A* finds the Dijkstra optimum ({cost} = {int(dij)})')
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


def strip_world(path, cells):
    """A world of the given 0.8 m cells (i, j) at y 10 (the car's lattice), one area set."""
    cells = sorted(cells)
    idx = {c: k for k, c in enumerate(cells)}
    links = {}
    for (i, j), k in idx.items():
        for di, dj in rg.STRAIGHT:
            n = idx.get((i + di, j + dj))
            if n is not None:
                links[(k, di, dj)] = n
    cs = rg.CellSet(0.8, 0.0, 0.0, [c[0] for c in cells], [c[1] for c in cells], [10.0] * len(cells), links, name='strip')
    header = {'sea_level': 0.0, 'radius': 1.5, 'height': 2.0, 'step': 0.3, 'water_depth': 0.8, 'sha256': '0' * 8}
    rg.write_world('strip', 'jeep', [cs], [(0, 0)], [], [], path, (0, 0), header, {}, flag_tight=False, log=lambda *a: None)
    return rsnv.load(path)


def hybrid_tests(tmp):
    T = rsnvhybrid.table('car')
    check(T == rsnvhybrid.table('car') and len(T['prims']) == 16 and all(len(l) == 6 for l in T['prims']),
          'the car\'s primitive table: deterministic, 6 moves (straight, two arcs; forward and back) for each of 16 headings')
    # a dead-end strip 5 cells (4 m) wide, 40 cells (32 m) long, along +z from (10, 10)
    W = strip_world(os.path.join(tmp, 'strip.jeep.rsnv'), [(i, j) for i in range(10, 15) for j in range(10, 50)])
    corr = [(9.6, 8.0), (9.6, 40.0)]
    r = rsnvhybrid.plan(W, T, (12, 20, 0), (12, 20, 8), corr, 10.0, {'max_expansions': 20000, 'htol': 1, 'corridor': 12.0})
    rev = sum(1 for q in r['poses'] if q[3] < 0)
    onc = all(10 <= q[0] <= 14 and 10 <= q[1] <= 49 for q in r['poses'])
    check(r['status'] == 'done' and rev > 0 and onc,
          f'a U-turn in a 4 m strip: done by reversing ({rev} reverse moves of {len(r["poses"]) - 1}, cost {r["cost"] / 16:.1f} m, '
          f'{r["expansions"]} expansions), every pose on the strip')
    # an L: 7 cells wide (5.6 m) north, then east
    L = [(i, j) for i in range(10, 17) for j in range(10, 45)] + [(i, j) for i in range(17, 60) for j in range(38, 45)]
    WL = strip_world(os.path.join(tmp, 'ell.jeep.rsnv'), L)
    corrL = [(10.8, 8.0), (10.8, 33.0), (40.0, 33.0)]
    rL = rsnvhybrid.plan(WL, T, (13, 14, 0), (50, 41, 4), corrL, 10.0, {'max_expansions': 20000, 'corridor': 12.0})
    check(rL['status'] == 'done' and all(q[3] > 0 for q in rL['poses']),
          f'round an L corner 5.6 m wide: forward only ({rL["status"]}, {len(rL["poses"])} poses, {rL["expansions"]} expansions)')
    rs = rsnvhybrid.plan(WL, T, (9, 14, 0), (50, 41, 4), corrL, 10.0, {'max_expansions': 20000, 'corridor': 12.0})
    check(rs['status'] == 'done' and rs['poses'][0][0] == 10, f'a start one cell off the cells is snapped onto them '
                                                                 f'(starts at I = {rs["poses"][0][0] if rs["poses"] else None})')
    ro = rsnvhybrid.plan(WL, T, (13, 14, 0), (50, 41, 4), [(10.8, 8.0), (10.8, 33.0)], 10.0,
                         {'max_expansions': 4000, 'corridor': 6.0})
    check(ro['status'] != 'done', f'a goal outside the corridor is not reached ({ro["status"]})')


def lane_tests():
    def field(trees=(), roof=None, drop_at=None):
        nx = nz = 100                                   # 400 x 400 m of 4 m cells from (-200, -200), ground at 0
        top = np.zeros((nz, nx))
        for x, z, h in trees:
            top[int((z + 200) // 4), int((x + 200) // 4)] = h
        if roof:
            (xa, za, xb, zb, h) = roof
            top[int((za + 200) // 4):int((zb + 200) // 4) + 1, int((xa + 200) // 4):int((xb + 200) // 4) + 1] = h
        if drop_at is not None:
            top[int((drop_at + 200) // 4):, :] = -10.0
        return (-200.0, -200.0, top)
    p0 = np.array([0.0, 1.5, -150.0])                   # a plane 1.5 m over the field, facing +z
    c = an.choose_lane(field(), p0, 0.0)
    check(not c['straight blocked'] and c['offset'] == 0.0 and c['length'] == 120.0, 'a clear lane: the spawn heading, 120 m')
    c = an.choose_lane(field(trees=[(0.0, -90.0, 8.0)]), p0, 0.0)
    check(c['clear'] and c['straight blocked'] and c['offset'] != 0.0 and abs(c['offset']) <= 20 and c['start'] == 0.0,
          f'a tree on the centre line 60 m out: the lane turns {c["offset"]:+.1f} deg')
    c = an.choose_lane(field(roof=(-12.0, -160.0, 12.0, -144.0, 5.0)), p0, 0.0)
    check(c['clear'] and c['start'] >= 6.0, f'a hangar roof over the spawn: the lane starts {c["start"]:g} m ahead')
    c = an.choose_lane(field(drop_at=-90.0), p0, 0.0)
    check(c['clear'] and c['carried'] and abs(c['offset']) > 20 and 'turns' in c['source'],
          f'a field ending in a drop ahead before lift-off: the plane turns {c["offset"]:+.1f} deg on the ground first')
    x0, z0, top = field()
    I, J = np.meshgrid(np.arange(100), np.arange(100))
    top[np.hypot(x0 + I * 4 + 2 - p0[0], z0 + J * 4 + 2 - p0[2]) > 60.0] = -10.0     # a plateau 60 m across
    c = an.choose_lane((x0, z0, top), p0, 0.0)
    check(c['clear'] and not c['carried'] and 'in-game check' in c['source'],
          'a plateau too small for any roll: the clear heading kept, flagged for an in-game check')


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
    hybrid_tests(tmp)
    lane_tests()
    class_tests()
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
