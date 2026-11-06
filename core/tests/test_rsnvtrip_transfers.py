r"""Trips across navigation worlds (rsnvtrip): which transfers the plan uses, and its tie rule

rsnvtrip.plan on test_rsnvtrip_plan.py's two made-up worlds (the tiny world twice; walking costs 100 times dearer),
each value from the plan's rules in rsnvtrip's docstring: a board is used only with both its nodes and its soldier
node reached from the start; an alight or drop only with both its nodes, its vehicle node reached from the board and
its soldier node reaching the goal; the least total wins, the first in OFFM order on a tie (boards before alights:
the first board, then its first alight); a transfer whose nodes are missing is never used, wherever it comes.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import fuzzcheck

from bfbc2core import rsnvtrip

T = fuzzcheck.fixtures('core/tests/test_rsnvtrip_plan.py')
BOARD, ALIGHT = T.BOARD, T.ALIGHT
RIDE = 38 * 58 >> 8                             # the vehicle world's 38 (node 0 to 1) at vehicle_scale 58
NOWHERE = (50.0, 10.0, 50.0)                    # no soldier node within any reach


def at(rec, vnode=None, pos=None, aux=None, kind=None):
    r = list(rec)
    if kind is not None:
        r[0] = kind
    if vnode is not None:
        r[3] = vnode
    if pos is not None:
        r[4:7] = pos
    if aux is not None:
        r[9] = aux
    return tuple(r)


def test_ties_go_to_the_first():
    S, V = T.worlds([BOARD, ALIGHT, ALIGHT])
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert (t['board'], t['alight'], t['cost']) == (0, 1, 160 + RIDE + 160), 'two equal alights: the first'
    S, V = T.worlds([BOARD, BOARD, ALIGHT])
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert (t['board'], t['alight']) == (0, 2), 'two equal boards: the first'
    S, V = T.worlds([BOARD, at(ALIGHT, kind=rsnvtrip.OFFM_DROP), ALIGHT])
    assert rsnvtrip.plan(S, V, 0, 1, team=1)['alight'] == 1, 'a drop and an alight alike: the first'
    # a dearer alight first (vehicle node 2, soldier node 2: 76 riding, then 3800 walking), the cheaper one after
    S, V = T.worlds([BOARD, at(ALIGHT, vnode=2, pos=(2.4, 10.0, 2.4), aux=2), ALIGHT])
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert (t['alight'], t['cost']) == (2, 160 + RIDE + 160), t


def test_unreached_ends_are_not_used():
    S, V = T.worlds()
    S.edge_kind[[1, 5, 6, 7]] = 40                  # node 3 cut off for soldiers (its walk to 0, both jumps)
    assert rsnvtrip.plan(S, V, 3, 1, team=1) == {'mode': None, 'cost': None}, 'the board out of the start\'s reach'
    assert rsnvtrip.plan(S, V, 0, 3, team=1) == {'mode': None, 'cost': None}, 'the goal out of the alight\'s reach'
    assert rsnvtrip.plan(S, V, 0, 1, team=1)['mode'] == 'vehicle'
    S, V = T.worlds()
    V.edge_kind[[0, 2]] = 40                        # the vehicle world's 0 - 1 closed: 1 reached by 0 - 3 - 2 - 1
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert t['ride'] == 96 + 70 + 38 and t['cost'] == 160 + (204 * 58 >> 8) + 160, t
    V.edge_kind[:] = 40
    S.edge_kind[:] = 40
    assert rsnvtrip.plan(S, V, 0, 1, team=1) == {'mode': None, 'cost': None}, 'the alight\'s vehicle node unreached'


def test_transfers_without_their_nodes():
    far_board = at(BOARD, pos=NOWHERE, aux=99)               # no soldier node
    lost_board = at(BOARD, vnode=0xFFFFFFFF)                 # no vehicle node
    far_alight = at(ALIGHT, pos=NOWHERE, aux=99)
    lost_alight = at(ALIGHT, vnode=0xFFFFFFFF)
    offmesh = [far_board, lost_board, far_alight, lost_alight, BOARD, ALIGHT]
    S, V = T.worlds(offmesh)
    assert rsnvtrip.transfers(S, V) == [(0, 1, 1, 0, None), (1, 1, 1, None, 0), (2, 2, 1, 1, None),
                                        (3, 2, 1, None, 1), (4, 1, 1, 0, 0), (5, 2, 1, 1, 1)]
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert (t['mode'], t['board'], t['alight'], t['cost']) == ('vehicle', 4, 5, 160 + RIDE + 160), t
    for k in range(4):                                       # without the good ones: none of them rides
        S, V = T.worlds([offmesh[k], BOARD, ALIGHT] if k >= 2 else [offmesh[k], ALIGHT])
        t = rsnvtrip.plan(S, V, 0, 1, team=1)
        want = {'mode': 'walk', 'cost': 3800} if k < 2 else dict(t, board=1, alight=2)
        assert t == want, (k, t)
    S, V = T.worlds([BOARD, far_alight, lost_alight])
    assert rsnvtrip.plan(S, V, 0, 1, team=1) == {'mode': 'walk', 'cost': 3800}
    S, V = T.worlds([at(BOARD, kind=rsnvtrip.OFFM_LAUNCH), at(BOARD, kind=rsnvtrip.OFFM_MOUNT), ALIGHT])
    assert rsnvtrip.plan(S, V, 0, 1, team=1) == {'mode': 'walk', 'cost': 3800}, 'launch points and mounts do not board'
    S, V = T.worlds([BOARD, at(ALIGHT, kind=rsnvtrip.OFFM_LAUNCH), at(ALIGHT, kind=rsnvtrip.OFFM_BOARD)])
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert t == {'mode': 'walk', 'cost': 3800}, ('a launch point and a board do not alight', t)


def test_vehicle_nodes_are_not_soldier_nodes():
    # board at soldier node 0 into vehicle node 2; alight from vehicle node 3 at soldier node 1 (the jump 2 -> 3: 70)
    S, V = T.worlds([at(BOARD, vnode=2), at(ALIGHT, vnode=3)])
    t = rsnvtrip.plan(S, V, 0, 1, team=1)
    assert t == {'mode': 'vehicle', 'cost': 160 + (70 * 58 >> 8) + 160, 'board': 0, 'alight': 1, 'soldier board': 0,
                 'vehicle board': 2, 'vehicle alight': 3, 'soldier alight': 1, 'walk1': 0, 'ride': 70, 'walk2': 0}, t
    assert rsnvtrip.legs(S, V, t, 0, 1)[1] == ('V', 'done', 70, [2, 3])
    V.edge_kind[[0, 4]] = 40                        # the vehicle world's node 1 (the alight's soldier index) cut off
    assert rsnvtrip.plan(S, V, 0, 1, team=1) == t, 'the vehicle node is what must be reached'


def test_only_boards_board():
    # an alight at soldier node 1 (vehicle node 1) and a drop at soldier node 0 (vehicle node 0): from 1 to 0 the
    # only board is at node 0, 3800 away, so the walk (3800) wins; an alight is not a board
    drop = at(ALIGHT, vnode=0, pos=(0.0, 10.0, 0.0), aux=0, kind=rsnvtrip.OFFM_DROP)
    S, V = T.worlds([BOARD, ALIGHT, drop])
    assert rsnvtrip.plan(S, V, 1, 0, team=1) == {'mode': 'walk', 'cost': 3800}
    S, V = T.worlds([at(BOARD, vnode=0)[:1] + (3,) + BOARD[2:], ALIGHT])   # team mask 3: teams 1 and 2
    assert rsnvtrip.plan(S, V, 0, 1, team=1)['mode'] == 'vehicle' and rsnvtrip.plan(S, V, 0, 1, team=2)['board'] == 0
    assert rsnvtrip.plan(S, V, 0, 1, team=4) == {'mode': 'walk', 'cost': 3800}


def test_reverse_costs_with_jumps():
    S, _ = T.worlds()
    every = {'water_scale': (256, 256, 256)}
    # to node 2: from 1 (3800), from 0 through 1 (7600), from 3 by its jump (7000); 0 is not reached through 3
    assert rsnvtrip.dijkstra(S, 2, every, reverse=True).tolist() == [7600, 3800, 0, 7000]
    assert rsnvtrip.dijkstra(S, 3, every, reverse=True).tolist() == [9600, 3800 + 7000, 7000, 0]


def test_legs_without_a_tight_extra():
    import numpy as np
    S, V = T.worlds()
    S.edge_flags = np.array(S.edge_flags)
    S.edge_flags[0] = 2                             # rsnv.EDGEF_TIGHT on 0 -> 1
    plain = {'water_scale': (256, 256, 256), 'kind_mask': 0xFFFFFFFF}
    assert rsnvtrip.legs(S, V, {'mode': 'walk'}, 0, 1, fs=plain) == [('S', 'done', 3800, [0, 1])], 'no extra given: none'
    assert rsnvtrip.legs(S, V, {'mode': 'walk'}, 0, 1)[0][2] == 3800 + 800, 'the soldier\'s 50 m'


def test_resolve_by_kind():
    S, _ = T.worlds()
    # 1.5 m outside the area (no cell within 1 m: no line of sight from there), 1.5 m from node 0
    off = (0.0, 10.0, -1.5)
    assert rsnvtrip.resolve(S, at(BOARD, pos=off, aux=99)) == 0, 'a board: the nearest node in reach, no sight needed'
    assert rsnvtrip.resolve(S, at(ALIGHT, pos=off, aux=99)) is None, 'an alight: only a node in sight'
    assert rsnvtrip.resolve(S, at(ALIGHT, pos=off, aux=0, kind=rsnvtrip.OFFM_DROP)) is None, 'a drop: the same'


def test_stored_unload_node_needs_its_height_too():
    import numpy as np
    A = fuzzcheck.fixtures('core/tests/test_rsnv_areas.py')
    # a ramp rising 1.6 m a metre (0.25 m cells, a height block each); the stored node 2 m up it, 3.2 m higher
    S = A.build([A.area(0.0, 30.0, ['1' * 13], pitch=0.25, grid=(13, 1, 250, [40 * k for k in range(13)]))])
    S.node_pos = np.array([[2.0, 3.2, 30.0], [50.0, 0.0, 50.0]])
    assert S.los((0.0, 0.0, 30.0), (2.0, 3.2, 30.0)), 'in sight up the ramp'
    foot = at(ALIGHT, pos=(0.0, 0.0, 30.0), aux=0)
    assert rsnvtrip.resolve(S, foot) is None, '2 m away but 3.2 m above (more than 2 m): resolved again, none in sight'
    assert rsnvtrip.resolve(S, at(foot, kind=rsnvtrip.OFFM_DROP)) is None
    assert rsnvtrip.resolve(S, at(ALIGHT, pos=(1.0, 1.6, 30.0), aux=0)) == 0, '1.6 m above: kept'
