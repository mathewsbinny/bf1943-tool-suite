r"""RSNV navigation file (rsnv): edge costs, the filter's heuristic scale and the search on the made-up tiny world

rsnv's cost rules on the test_rsnv.py tiny world (no game data; four nodes, edges of 38, 96 and 70 in 1/16 m, two of
them jumps): edge_cost scales by the deeper end's water class (wading over shallow; floor of cost x scale / 256), adds
a named kind's extra (kinds past the names get none), a tight edge's extra and a penalty, and closes a kind past 31
and a conditional edge until all its objects are destroyed; filter_scale keeps the file's scale unless the filter
can make an edge cheaper, then gives the least filtered cost per 1/16 m (never below 0, never above the file's),
leaving out edges of no length and closed kinds; search returns DONE with the cheapest path, PARTIAL at its
expansion limit or when the goal is cut off (the expanded node nearest the goal), honours kind_mask, and astar
wraps it.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import fuzzcheck
import numpy as np

from bfbc2core import rsnv

RSNV = fuzzcheck.fixtures('core/tests/test_rsnv.py')
_RAW = []
JUMP = 4


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
    for name in ('node_flags', 'edge_kind', 'edge_flags', 'edge_cost', 'edge_target'):
        setattr(w, name, np.array(getattr(w, name)))
    return w


def test_the_tiny_world():
    w = world()
    assert w.node_first_edge.tolist() == [0, 2, 4, 6, 8]
    assert w.edge_target.tolist() == [1, 3, 0, 2, 1, 3, 0, 2]
    assert w.edge_cost.tolist() == [38, 96, 38, 38, 38, 70, 96, 70]
    assert w.edge_kind.tolist() == [0, 0, 0, 0, 0, JUMP, 0, JUMP]


def test_water_classes():
    w = world()
    assert rsnv.edge_cost(w, 0, 0) == 38
    shallow = (256, 384, 768)
    assert rsnv.edge_cost(w, 0, 0, shallow) == 38, 'dry'
    w.node_flags[1] = rsnv.NAUX_SHALLOW
    assert rsnv.edge_cost(w, 0, 0, shallow) == 38 * 384 >> 8 == 57, 'the target shallow'
    assert rsnv.edge_cost(w, 1, 2, shallow) == 57, 'the source shallow'
    w.node_flags[0] = rsnv.NAUX_WADE
    assert rsnv.edge_cost(w, 0, 0, shallow) == 38 * 768 >> 8 == 114, 'wading at one end, shallow at the other'
    w.node_flags[0] = rsnv.NAUX_WADE | rsnv.NAUX_SHALLOW
    assert rsnv.edge_cost(w, 0, 0, shallow) == 114, 'wading wins'
    w.edge_cost[0] = 39
    assert rsnv.edge_cost(w, 0, 0, (256, 384, 300)) == 39 * 300 >> 8 == 45, 'rounded down'


def test_kinds_extras_and_penalties():
    w = world()
    extra = [1, 2, 3, 4, 100, 6, 7]
    assert rsnv.edge_cost(w, 0, 0, kind_extra=extra) == 39 and rsnv.edge_cost(w, 2, 5, kind_extra=extra) == 170
    w.edge_kind[0] = 6
    assert rsnv.edge_cost(w, 0, 0, kind_extra=extra) == 45, 'the last named kind'
    w.edge_kind[0] = 7
    assert rsnv.edge_cost(w, 0, 0, kind_extra=extra + [50]) == 38, 'a kind past the names: no extra'
    w.edge_kind[0] = 31
    assert rsnv.edge_cost(w, 0, 0) == 38
    w.edge_kind[0] = 32
    assert rsnv.edge_cost(w, 0, 0) is None, 'a kind past 31 is closed'
    w = world()
    w.edge_flags[0] = rsnv.EDGEF_TIGHT
    assert rsnv.edge_cost(w, 0, 0, tight_extra=800) == 838 and rsnv.edge_cost(w, 0, 0) == 38
    assert rsnv.edge_cost(w, 1, 2, tight_extra=800) == 38, 'not a tight edge'
    assert rsnv.edge_cost(w, 0, 0, penalty={0: 5}) == 43 and rsnv.edge_cost(w, 0, 0, penalty={1: 5}) == 38
    assert rsnv.edge_cost(w, 0, 0, kind_extra=[10] * 7, penalty={0: 5}, tight_extra=1) == 54, 'all of them'


def test_conditional_edges():
    w = world()
    w.edge_flags[0] = rsnv.EDGEF_COND
    w.cond = {0: [1, 2]}
    assert rsnv.edge_cost(w, 0, 0) is None and rsnv.edge_cost(w, 0, 0, destroyed={1}) is None
    assert rsnv.edge_cost(w, 0, 0, destroyed={1, 2}) == 38 and rsnv.edge_cost(w, 0, 0, destroyed={2, 1, 5}) == 38
    w.cond = {}
    assert rsnv.edge_cost(w, 0, 0, destroyed={1, 2}) is None, 'flagged without objects: closed'
    assert rsnv.edge_cost(w, 1, 2, destroyed=set()) == 38, 'not conditional'


def length(w, k, n):
    p = np.asarray(w.node_pos, np.float32).astype(np.float64)
    d = p[int(w.edge_target[k])] - p[n]
    return float(np.sqrt((d[0] * d[0] + d[1] * d[1]) + d[2] * d[2]))


def near(a, b):
    return abs(a - b) <= 1e-12 * max(1.0, abs(b))


def test_filter_scale():
    w = world()
    assert rsnv.filter_scale(w) == rsnv.H_SCALE_LEGACY and w.h_scale == rsnv.H_SCALE_LEGACY
    assert rsnv.filter_scale(w, (256, 300, 512), [0, 5], {3: 7}, 2) == rsnv.H_SCALE_LEGACY, 'nothing made cheaper'
    w.node_flags[1] = rsnv.NAUX_WADE                    # edges 0, 2, 3 and 4 touch node 1: 38 x 64 / 256 = 9
    assert near(rsnv.filter_scale(w, (256, 256, 64)), 9 / (16 * length(w, 0, 0)) * (1 - 1e-9))
    # a cheaper shallow class: computed (it could make an edge cheaper), though no node is shallow here
    assert near(rsnv.filter_scale(w, (256, 64, 256)), 38 / (16 * length(w, 0, 0)) * (1 - 1e-9))
    w = world()
    assert 70 / (16 * length(w, 5, 2)) > 1, 'the jump costs more than its length'
    assert near(rsnv.filter_scale(w, kind_extra=[0, 0, 0, 0, -60]), 10 / (16 * length(w, 5, 2)) * (1 - 1e-9))
    assert near(rsnv.filter_scale(w, kind_extra=[0, 0, 0, 0, -60, 0, 0, -1000]),
                10 / (16 * length(w, 5, 2)) * (1 - 1e-9)), 'extras past the named kinds are not used'
    w.edge_kind[5] = w.edge_kind[7] = 9
    assert near(rsnv.filter_scale(w, kind_extra=[0, 0, 0, 0, -60]), 38 / (16 * length(w, 0, 0)) * (1 - 1e-9)), \
        'kind 9 takes no extra'
    w = world()
    w.edge_flags[0] = rsnv.EDGEF_TIGHT
    assert near(rsnv.filter_scale(w, tight_extra=-30), 8 / (16 * length(w, 0, 0)) * (1 - 1e-9))
    assert near(rsnv.filter_scale(w, penalty={1: -90}), 6 / (16 * length(w, 1, 0)) * (1 - 1e-9))
    assert rsnv.filter_scale(w, penalty={0: -38}) == 0.0 and rsnv.filter_scale(w, penalty={0: -50}) == 0.0
    w.h_scale_q = 1 << 22                               # the file's own scale, 0.25: the filtered one is at most that
    assert rsnv.filter_scale(w, penalty={1: -1}) == 0.25 and rsnv.filter_scale(w) == 0.25
    w = world()
    w.edge_kind[1] = 40                                 # the cheapest edge per metre, closed: left out
    assert near(rsnv.filter_scale(w, penalty={1: -90, 0: -1}), 37 / (16 * length(w, 0, 0)) * (1 - 1e-9))
    flat = world()
    flat.node_pos = np.zeros_like(flat.node_pos)
    assert rsnv.filter_scale(flat, penalty={0: -1}) == rsnv.H_SCALE_LEGACY, 'no edge has a length'
    bare = world()
    bare.edge_target = bare.edge_target[:0]
    assert rsnv.filter_scale(bare, penalty={0: -1}) == rsnv.H_SCALE_LEGACY, 'no edges'


def test_search():
    w = world()
    status, cost, path, exp = rsnv.search(w, 0, 2)
    assert (status, cost, path) == (rsnv.DONE, 76, [0, 1, 2]) and exp == 2, (status, cost, path, exp)
    assert rsnv.astar(w, 0, 2) == (76, [0, 1, 2], 2)
    assert rsnv.search(w, 0, 0) == (rsnv.DONE, 0, [0], 0)
    assert rsnv.search(w, 0, 2, max_expansions=1) == (rsnv.PARTIAL, 0, [0], 1), 'only the start expanded'
    assert rsnv.search(w, 0, 2, max_expansions=0) == (rsnv.PARTIAL, 0, [0], 0)
    assert rsnv.astar(w, 0, 2, max_expansions=1) == (None, [], 1)
    walk_only = 1 << rsnv.EDGE_WALK
    assert rsnv.search(w, 3, 2, kind_mask=walk_only)[:3] == (rsnv.DONE, 96 + 38 + 38, [3, 0, 1, 2])
    assert rsnv.search(w, 3, 2)[:3] == (rsnv.DONE, 70, [3, 2]), 'the jump'
    no_jump = ~(1 << JUMP) & 0xFFFFFFFF
    assert rsnv.astar(w, 3, 2, kind_mask=no_jump)[0] == 172
    cut = world()
    cut.edge_kind[3] = 40                               # 1 -> 2 closed; walking, node 2 is reached only by the jumps
    status, cost, path, _ = rsnv.search(cut, 0, 2, kind_mask=walk_only)
    assert status == rsnv.PARTIAL and path == [0, 1] and cost == 38, ('cut off: the expanded node nearest the goal',
                                                                     status, path, cost)
    w = world()
    w.edge_cost[0] = w.edge_cost[2] = 1000              # 0 -> 1 dear: round by 3 and the jump
    assert rsnv.search(w, 0, 2)[:3] == (rsnv.DONE, 96 + 70, [0, 3, 2])
    assert rsnv.search(w, 0, 2, penalty={1: 900})[:3] == (rsnv.DONE, 1038, [0, 1, 2])
    w = world()
    w.edge_flags[0] = rsnv.EDGEF_COND
    w.cond = {0: [0]}
    assert rsnv.search(w, 0, 1)[2] == [0, 3, 2, 1] and rsnv.search(w, 0, 1, destroyed={0})[2] == [0, 1]


def test_default_water_scale():
    w = world()
    w.edge_cost = w.edge_cost * 100                     # costs large enough that a scale of 257 / 256 would show
    assert rsnv.edge_cost(w, 0, 0) == 3800 and rsnv.search(w, 0, 2)[:2] == (rsnv.DONE, 7600)
    assert rsnv.filter_scale(w, penalty={0: -1}) < 1.0


def test_equal_cost_parent_is_the_lower_index():
    # 0 -> 1 -> 2 and 0 -> 3 -> 2 both cost 76; node 3 is expanded first (its f is 75 against node 1's 76), so node
    # 2 is first reached from 3, then from 1 at the same cost: the lower-indexed parent, 1, replaces 3
    w = world()
    w.edge_cost[1] = 6
    assert rsnv.search(w, 0, 2)[:3] == (rsnv.DONE, 76, [0, 1, 2])
    w.edge_cost[3] = 39                                 # 0 -> 1 -> 2 now dearer: 3 stays the parent
    assert rsnv.search(w, 0, 2)[:3] == (rsnv.DONE, 76, [0, 3, 2])


def test_kind_masks_and_kind_31():
    w = world()
    assert rsnv.search(w, 3, 2, kind_mask=1 << JUMP)[:3] == (rsnv.DONE, 70, [3, 2]), 'jumps only'
    assert rsnv.search(w, 0, 2, kind_mask=1 << JUMP)[0] == rsnv.PARTIAL, 'no walking'
    w.edge_kind[0] = w.edge_kind[3] = 31                # a kind past the names, still open
    assert rsnv.search(w, 0, 2)[:3] == (rsnv.DONE, 76, [0, 1, 2])
    assert rsnv.search(w, 0, 2, kind_mask=1 << 31)[:3] == (rsnv.DONE, 76, [0, 1, 2])
