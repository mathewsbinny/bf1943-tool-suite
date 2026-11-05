r"""Trips across navigation worlds (rsnvtrip): the plan, its legs and transfers on two made-up worlds

rsnvtrip on two copies of the test_rsnv.py tiny world (no game data): the soldier world with its walking costs made
100 times dearer, and a vehicle world whose OFFM records board at soldier node 0 and alight at node 1. The plan rides
when that is cheaper (board cost, the ride scaled by vehicle_scale / 256, alight cost), walks when the walk costs no
more (a tie walks), walks when the team may not board, and has no mode when neither reaches; legs gives each part's
search; transfers keeps boards, alights and drops (not launch points), with the vehicle node only when it exists;
reach_of, soldier_node and in_sight_node keep their reach and order rules; the soldier and vehicle filters and the
defaults are the documented ones.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import fuzzcheck
import numpy as np

from bfbc2core import rsnv, rsnvtrip

RSNV = fuzzcheck.fixtures('core/tests/test_rsnv.py')
_RAW = []
BOARD = (rsnvtrip.OFFM_BOARD, 1, 3, 0, 0.0, 10.0, 0.0, 0.0, 1.0, 0)
ALIGHT = (rsnvtrip.OFFM_ALIGHT, 1, 3, 1, 2.4, 10.0, 0.0, 0.0, 1.0, 1)


def world():
    if not _RAW:
        import os
        import tempfile
        sections, header = RSNV.tiny()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'tiny.rsnv')
            rsnv.write(path, sections, header)
            _RAW.append(open(path, 'rb').read())
    w = rsnv.load(_RAW[0])
    for name in ('edge_kind', 'edge_cost', 'node_flags'):
        setattr(w, name, np.array(getattr(w, name)))
    return w


def worlds(offmesh=(BOARD, ALIGHT)):
    S, V = world(), world()
    S.edge_cost = S.edge_cost * 100                 # walking 0 -> 1 costs 3800; the ride 160 + 38 x 58 / 256 + 160
    V.offmesh = list(offmesh)
    return S, V


def test_constants():
    assert (rsnvtrip.OFFM_BOARD, rsnvtrip.OFFM_ALIGHT, rsnvtrip.OFFM_LAUNCH, rsnvtrip.OFFM_DROP,
            rsnvtrip.OFFM_MOUNT) == (1, 2, 3, 4, 5)
    assert rsnvtrip.INF == (1 << 63) - 1
    assert rsnvtrip.SOLDIER == {'water_scale': (256, 384, 768), 'tight_extra': 800,
                                'kind_mask': 0xFFFFFFFF & ~(1 << rsnv.EDGE_BOARD) & ~(1 << rsnv.EDGE_ALIGHT)}
    assert rsnvtrip.VEHICLE == {'water_scale': (256, 256, 256), 'kind_mask': 0xFFFFFFFF, 'tight_extra': 0}
    assert rsnvtrip.DEFAULTS == {'vehicle_scale': 58, 'board_cost': 160, 'alight_cost': 160}
    assert (rsnvtrip.IN_SIGHT_REACH, rsnvtrip.IN_SIGHT_DY, rsnvtrip.IN_SIGHT_TRIES) == (6.0, 2.0, 32)


def test_a_ride_and_a_walk():
    S, V = worlds()
    ride = 38 * 58 >> 8
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert t == {'mode': 'vehicle', 'cost': 160 + ride + 160, 'board': 0, 'alight': 1, 'soldier board': 0,
                 'vehicle board': 0, 'vehicle alight': 1, 'soldier alight': 1, 'walk1': 0, 'ride': 38, 'walk2': 0}, t
    assert rsnvtrip.legs(S, V, t, 0, 1) == [('S', rsnv.DONE, 0, [0]), ('V', rsnv.DONE, 38, [0, 1]),
                                            ('S', rsnv.DONE, 0, [1])]
    assert rsnvtrip.plan(S, V, 0, 1, team=2) == {'mode': 'walk', 'cost': 3800}, 'the team may not board'
    walk = rsnvtrip.plan(S, V, 0, 1, team=1, params={'board_cost': 3800 - 160 - ride})
    assert walk == {'mode': 'walk', 'cost': 3800}, 'a tie walks'
    assert rsnvtrip.legs(S, V, walk, 0, 1) == [('S', rsnv.DONE, 3800, [0, 1])]
    assert rsnvtrip.plan(S, V, 0, 1, team=1, params={'board_cost': 3800 - 161 - ride})['mode'] == 'vehicle'
    assert rsnvtrip.plan(S, V, 0, 1, team=1, params={'vehicle_scale': 512})['cost'] == 160 + 76 + 160
    assert rsnvtrip.plan(S, V, 0, 1, team=3)['mode'] == 'vehicle', 'a team in the mask'


def test_walks_on_after_the_ride():
    S, V = worlds([BOARD, (rsnvtrip.OFFM_DROP, 1, 3, 1, 2.4, 10.0, 0.0, 0.0, 1.0, 1)])
    assert rsnvtrip.plan(S, V, 3, 2, team=1) == {'mode': 'walk', 'cost': 7000}, 'the jump 3 -> 2'
    S.edge_kind[[5, 7]] = 40                        # no jumps: walk 3 -> 0 (9600), ride 0 -> 1, walk 1 -> 2 (3800)
    t = rsnvtrip.plan(S, V, 3, 2, team=1)
    assert t['mode'] == 'vehicle' and t['alight'] == 1 and (t['walk1'], t['ride'], t['walk2']) == (9600, 38, 3800)
    assert t['cost'] == 9600 + 160 + (38 * 58 >> 8) + 160 + 3800 < 9600 + 3800 + 3800


def test_no_way():
    S, V = worlds()
    S.edge_kind[:] = 40                             # every soldier edge closed: no walk; the ride still goes
    assert rsnvtrip.plan(S, V, 0, 1, team=1)['mode'] == 'vehicle'
    V.edge_kind[:] = 40
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert t == {'mode': None, 'cost': None} and rsnvtrip.legs(S, V, t, 0, 1) == []
    S, V = worlds()
    S.edge_kind[[0, 2, 3, 4]] = 40                  # node 1 cut off for soldiers: the alight's soldier node unreached
    assert rsnvtrip.plan(S, V, 0, 1, team=1) == {'mode': 'vehicle', 'cost': 160 + (38 * 58 >> 8) + 160, 'board': 0,
                                                 'alight': 1, 'soldier board': 0, 'vehicle board': 0,
                                                 'vehicle alight': 1, 'soldier alight': 1, 'walk1': 0, 'ride': 38,
                                                 'walk2': 0}
    assert rsnvtrip.plan(S, V, 0, 2, team=1) == {'mode': 'walk', 'cost': 9600 + 7000}, 'no ride reaches node 2'


def test_transfers():
    S, V = worlds([BOARD, ALIGHT, (rsnvtrip.OFFM_LAUNCH, 1, 3, 0, 0.0, 10.0, 0.0, 0.0, 1.0, 0),
                   (rsnvtrip.OFFM_BOARD, 1, 3, 0xFFFFFFFF, 0.0, 10.0, 0.0, 0.0, 1.0, 0),
                   (rsnvtrip.OFFM_BOARD, 1, 3, 4, 0.0, 10.0, 0.0, 0.0, 1.0, 0),
                   (rsnvtrip.OFFM_MOUNT, 1, 3, 0, 0.0, 10.0, 0.0, 0.0, 1.0, 0)])
    assert rsnvtrip.transfers(S, V) == [(0, 1, 1, 0, 0), (1, 2, 1, 1, 1), (3, 1, 1, None, 0), (4, 1, 1, None, 0)]
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert t['mode'] == 'vehicle' and t['board'] == 0, 'boards without a vehicle node are not used'


def test_reach_and_nodes():
    S, _ = worlds()
    assert rsnvtrip.reach_of(BOARD) == (3.0, 3.5) and rsnvtrip.reach_of(ALIGHT) == (2.0, 2.0)
    assert rsnvtrip.soldier_node(S, 1.2, 10.0, 0.0, 1.2, 0.5) == 0, 'a tie: the lower index'
    assert rsnvtrip.soldier_node(S, 1.3, 10.0, 0.0, 1.2, 0.5) == 1
    assert rsnvtrip.soldier_node(S, 0.0, 10.5, 0.0, 1.0, 0.5) == 0 and rsnvtrip.soldier_node(S, 0.0, 10.6, 0.0, 1.0, 0.5) is None
    assert rsnvtrip.soldier_node(S, -3.0, 10.0, 0.0, 2.9, 1.0) is None and rsnvtrip.soldier_node(S, -3.0, 10.0, 0.0, 3.0, 1.0) == 0
    assert rsnvtrip.in_sight_node(S, 1.2, 10.0, 0.0) == 0 and rsnvtrip.in_sight_node(S, 50.0, 10.0, 50.0) is None
    assert rsnvtrip.in_sight_node(S, 1.3, 10.0, 0.0) == 1
    assert rsnvtrip.in_sight_node(S, 1.2, 10.0, 0.0, tries=0) is None
    assert rsnvtrip.in_sight_node(S, 0.0, 10.0, 0.5, reach=0.5) == 0 and rsnvtrip.in_sight_node(S, 0.0, 10.0, 0.5, reach=0.49) is None
    assert rsnvtrip.in_sight_node(S, 0.0, 10.5, 0.0, dy=0.5) == 0 and rsnvtrip.in_sight_node(S, 0.0, 10.5, 0.0, dy=0.49) is None
    assert rsnvtrip.resolve(S, BOARD) == 0 and rsnvtrip.resolve(S, ALIGHT) == 1
    far = BOARD[:9] + (3,)                          # the stored node out of reach: resolved again
    assert rsnvtrip.resolve(S, far) == 0
    past = ALIGHT[:9] + (99,)                       # a stored node past the world's
    assert rsnvtrip.resolve(S, past) == 1


def test_node_lookups_off_the_axes():
    S, _ = worlds()
    assert rsnvtrip.soldier_node(S, 2.4, 10.0, 2.3, 0.2, 1.0) == 2 and rsnvtrip.in_sight_node(S, 2.4, 10.0, 2.3) == 2
    # a stored node in reach is kept though another is nearer; out of reach it is resolved again
    assert rsnvtrip.resolve(S, BOARD[:4] + (1.0, 10.0, 0.0) + BOARD[7:9] + (1,)) == 1
    assert rsnvtrip.resolve(S, BOARD[:4] + (1.0, 10.0, 1.0) + BOARD[7:9] + (2,)) == 2
    assert rsnvtrip.resolve(S, BOARD[:4] + (0.0, 10.0, 3.0) + BOARD[7:9] + (3,)) == 3, 'exactly at its reach (3 m)'
    assert rsnvtrip.resolve(S, BOARD[:4] + (0.0, 13.5, 0.0) + BOARD[7:9] + (1,)) == 1, 'exactly 3.5 m above'
    assert rsnvtrip.resolve(S, BOARD[:4] + (0.0, 10.0, 0.0) + BOARD[7:9] + (4,)) == 0, 'a stored node past the nodes'


def test_dijkstra_and_filters():
    S, _ = worlds()
    walk = {'water_scale': (256, 256, 256), 'kind_mask': 1 << rsnv.EDGE_WALK}
    assert rsnvtrip.dijkstra(S, 0, walk).tolist() == [0, 3800, 7600, 9600]
    assert rsnvtrip.dijkstra(S, 2, walk, reverse=True).tolist() == [7600, 3800, 0, 17200]
    assert rsnvtrip.dijkstra(S, 3, {'water_scale': (256, 256, 256), 'kind_mask': 1 << 4}).tolist() == \
        [rsnvtrip.INF, rsnvtrip.INF, 7000, 0], 'jumps only'
    plain = {'water_scale': (256, 256, 256)}                     # no kind_mask, no tight_extra: every kind, no extra
    S.edge_flags = np.array(S.edge_flags)
    S.edge_flags[1] = rsnv.EDGEF_TIGHT
    assert rsnvtrip.dijkstra(S, 0, plain).tolist() == [0, 3800, 7600, 9600]
    assert rsnvtrip.dijkstra(S, 0, dict(plain, tight_extra=5)).tolist() == [0, 3800, 7600, 9605]
    S, V = worlds()
    no_jumps = {'water_scale': (256, 256, 256), 'kind_mask': 1 << rsnv.EDGE_WALK}
    assert rsnvtrip.plan(S, V, 3, 2, team=1, fs=no_jumps)['cost'] != rsnvtrip.plan(S, V, 3, 2, team=1)['cost']
    slow = {'water_scale': (512, 512, 512), 'kind_mask': 0xFFFFFFFF}
    assert rsnvtrip.plan(S, V, 0, 1, team=1, fv=slow)['ride'] == 76
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert rsnvtrip.legs(S, V, t, 0, 1, fv=slow)[1] == ('V', rsnv.DONE, 76, [0, 1])
    t = rsnvtrip.plan(S, V, 3, 2, team=1)
    assert rsnvtrip.legs(S, V, t, 3, 2, fs=no_jumps)[0][2] == 9600 + 3800 + 3800
