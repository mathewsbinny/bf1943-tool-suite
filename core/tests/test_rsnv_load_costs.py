r"""RSNV navigation file (rsnv): the loader's limits, default filters, the filter's scale and the search's ties

rsnv on files and worlds made here (no game data), each value from the format in rsnv's docstring, rsnav's loader
(rsnav_load.c: at most 4 x its 18 section kinds in the directory, the directory inside the file, 0.2 m as the finest
pitch of a file without areas) and the docstrings of edge_cost, filter_scale and search: a file of only its header and
directory reads (its NODE the sentinel alone); the directory's limits at and just past them; BMAP and META like the
schema's sections appear once, an unknown tag any number of times; objects and conditional edges read back; the
default filter costs every water class at 256 / 256 and adds no tight extra; filter_scale's least cost per 1/16 m per
water class, kind (named kinds' extras only, kinds past 31 left out), tight edge, edge length (3D, edges of no length
left out); search's partial path to the expanded node of least heuristic (3D distance; then cost, then index), the
lower-indexed of two equal-cost parents, and a node reached again more cheaply searched again.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import math
import struct

import fuzzcheck
import numpy as np

from bfbc2core import rsnv

RSNV = fuzzcheck.fixtures('core/tests/test_rsnv.py')
_RAW = []
REQUIRED = ['SECT', 'NODE', 'NAUX', 'EDGE', 'AREA', 'BMAP', 'HGHT', 'GBKT', 'GLST', 'ANOD']


def minimal(tags=REQUIRED, nsec=None, size=None):
    """A file of only the header and directory: every section empty but NODE, whose one record (the sentinel) is the
    header's 16 bytes at 32 (height, step, water depth, and the .hpd hash's first 4 bytes, 0: the edge count)."""
    entries = b''
    for t in tags:
        stride = struct.calcsize('<' + rsnv.SCHEMA[t]) if t in rsnv.SCHEMA else 1 if t in ('BMAP', 'META') else 0
        off, count = (32, 1) if t == 'NODE' else (0, 0)
        entries += t.encode() + struct.pack('<III', off, count, stride)
    size = 64 + len(entries) if size is None else size
    header = b'RSNV' + struct.pack('<IHHIII', 0x01020304, 1, 0, size, len(tags) if nsec is None else nsec,
                                   rsnv.fnv1a('minimal'))
    header += struct.pack('<5f', 0.0, 0.35, 1.8, 0.37, 1.4) + bytes(20)
    return header + entries


def outcome(data):
    try:
        rsnv.load(bytes(data))
    except ValueError as e:
        return str(e)
    return 'read'


def test_header_and_directory_only():
    raw = minimal()
    w = rsnv.load(raw)
    assert len(raw) == 64 + 16 * 10 and w.node_pos.shape == (0, 3) and len(w.edge_target) == 0, 'the sentinel only'
    assert w.areas == [] and w.min_pitch == float(np.float32(0.2)), 'no areas: 0.2 m, as rsnav'
    assert (w.objects, w.cedg_rows, w.oref, w.cond) == ([], [], [], {})
    assert outcome(raw[:64]) == f'RSNV: header says {len(raw)} bytes, the file has 64', 'a header alone is not short'
    assert outcome(raw[:63]) == 'RSNV: shorter than its header'


def test_directory_limits():
    assert outcome(minimal(nsec=11)) == 'RSNV: section directory outside the file', 'one entry past the end'
    assert outcome(minimal(nsec=9)) == 'RSNV: no ANOD section'
    xtra = REQUIRED + ['XTRA'] * 62                         # 72 entries: 4 x rsnav's 18 kinds
    assert outcome(minimal(xtra)) == 'read'
    assert outcome(minimal(xtra + ['XTRA'])) == 'RSNV: section directory outside the file', '73'


def test_sections_once():
    assert outcome(minimal(REQUIRED + ['META', 'META'])) == 'RSNV: section META appears twice'
    assert outcome(minimal(REQUIRED + ['BMAP'])) == 'RSNV: section BMAP appears twice'
    assert outcome(minimal(REQUIRED + ['XTRA', 'XTRA'])) == 'read', 'an unknown tag is skipped, each time'


def test_objects_read_back(tmp_path):
    sections, header = RSNV.tiny()
    sections['EDGE'][0] = (sections['EDGE'][0][0], 38, rsnv.EDGEF_COND)
    sections.update(OBJT=[(7, 0xABCD, 1.0, 2.0, 3.0, 0), (8, 0x1234, 4.0, 5.0, 6.0, 1), (9, 0x5678, 0.0, 0.0, 0.0, 0)],
                    CEDG=[(0, 1, 2, 0)], OREF=[(2,), (0,), (1,)])
    path = str(tmp_path / 'objects.rsnv')
    rsnv.write(path, sections, header)
    w = rsnv.load(path)
    assert w.objects == [(7, 0xABCD, 1.0, 2.0, 3.0, 0), (8, 0x1234, 4.0, 5.0, 6.0, 1), (9, 0x5678, 0.0, 0.0, 0.0, 0)]
    assert w.oref == [2, 0, 1] and w.cond == {0: [0, 1]}, 'edge 0 opens when objects 0 and 1 are destroyed'
    assert rsnv.edge_cost(w, 0, 0, destroyed={0}) is None and rsnv.edge_cost(w, 0, 0, destroyed={0, 1}) == 38


# ---- default filters and the filter's scale --------------------------------------------------------------------------
def world():
    """The tiny world (nodes 0 (0, 10, 0), 1 (2.4, 10, 0), 2 (2.4, 10, 2.4), 3 (0, 10, 6); edges 0: 0-1 38, 1: 0-3 96,
    2: 1-0 38, 3: 1-2 38, 4: 2-1 38, 5: 2-3 jump 70, 6: 3-0 96, 7: 3-2 jump 70), its arrays writable."""
    if not _RAW:
        import os
        import tempfile
        sections, header = RSNV.tiny()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'tiny.rsnv')
            rsnv.write(path, sections, header)
            _RAW.append(open(path, 'rb').read())
    w = rsnv.load(_RAW[0])
    for name in ('node_pos', 'node_flags', 'edge_kind', 'edge_flags', 'edge_cost', 'edge_target'):
        setattr(w, name, np.array(getattr(w, name)))
    return w


def length(w, a, b):
    """An edge's length as filter_scale takes it: from the float32 positions."""
    p = np.asarray(w.node_pos, np.float32).astype(np.float64)
    d = p[b] - p[a]
    return math.sqrt((d[0] * d[0] + d[1] * d[1]) + d[2] * d[2])


def scaled(rate):
    return max(0.0, min(rsnv.H_SCALE_LEGACY, rate * (1.0 - 1e-9)))


def test_default_filter_by_water_class():
    for flags in (0, rsnv.NAUX_SHALLOW, rsnv.NAUX_WADE):
        w = world()
        w.node_flags[:] = flags
        w.edge_cost = w.edge_cost * 100                       # 3800: one more 256th would show
        assert rsnv.edge_cost(w, 0, 0) == 3800, flags
        assert rsnv.search(w, 0, 1)[:3] == (rsnv.DONE, 3800, [0, 1]), flags
        # a negative extra makes the scale computed: walk edges 3800 - 3790 = 10 over 2.4 m the least
        assert rsnv.filter_scale(w, kind_extra=[-3790]) == scaled(10 / (16 * length(w, 0, 1))), flags
    w = world()
    w.edge_flags[0] = rsnv.EDGEF_TIGHT
    assert rsnv.edge_cost(w, 0, 0) == 38 and rsnv.search(w, 0, 1)[1] == 38, 'no tight extra by default'


def test_scale_by_water_class():
    w = world()
    assert rsnv.filter_scale(w, (256, 256, 64)) == scaled(38 / (16 * length(w, 0, 1))), 'dry: 256'
    w.node_flags[:] = rsnv.NAUX_SHALLOW
    assert rsnv.filter_scale(w, (256, 128, 64)) == scaled((38 * 128 >> 8) / (16 * length(w, 0, 1))), 'shallow: 128'
    w.node_flags[:] = rsnv.NAUX_WADE
    assert rsnv.filter_scale(w, (256, 128, 64)) == scaled((38 * 64 >> 8) / (16 * length(w, 0, 1))), 'wading: 64'


def test_scale_by_kind_and_tight_edge():
    w = world()
    w.edge_cost[[5, 7]] = 60                                  # the jumps the cheapest a metre
    jump = 16 * length(w, 2, 3)
    assert rsnv.filter_scale(w, kind_extra=[5000, -1]) == scaled(60 / jump), 'kinds past the extras given: none'
    w.edge_kind[5], w.edge_kind[7], w.edge_cost[7] = 6, 7, 55     # an alight (the last named kind) and kind 7
    assert rsnv.filter_scale(w, kind_extra=[5000] + [0] * 5 + [-10]) == scaled(50 / jump), 'the alight\'s extra'
    assert rsnv.filter_scale(w, kind_extra=[5000] * 7, tight_extra=-1) == scaled(55 / jump), 'kind 7: no extra'
    w = world()
    w.edge_flags[3] = rsnv.EDGEF_TIGHT                        # 1 -> 2
    assert rsnv.filter_scale(w, tight_extra=-30) == scaled(8 / (16 * length(w, 1, 2))), 'the tight edge only'
    assert rsnv.filter_scale(w, kind_extra=[0, -1], tight_extra=5) == scaled(38 / (16 * length(w, 0, 1)))


def test_scale_by_edge_length():
    w = world()
    w.node_pos[1] = (2.4, 12.0, 0.0)                          # 0 - 1 and 1 - 2 now climb 2 m
    rate = 8 / (16 * length(w, 0, 1))
    assert abs(length(w, 0, 1) - math.sqrt(2.4 ** 2 + 4)) < 1e-6
    assert rsnv.filter_scale(w, kind_extra=[-30]) == scaled(min(rate, 8 / (16 * length(w, 1, 2)))), 'in 3D'
    w = world()
    w.node_pos[3] = w.node_pos[0]                             # 0 - 3 of no length, and free
    w.edge_cost[[1, 6]] = 0
    assert rsnv.filter_scale(w, (256, 256, 255)) == scaled(38 / (16 * length(w, 0, 1))), 'no length: left out'
    w = world()
    w.node_pos[1] = (0.5, 10.0, 0.0)                          # 0 - 1 half a metre, 5 its cost
    w.edge_cost[[0, 2]] = 5
    assert rsnv.filter_scale(w, (256, 256, 255)) == scaled(5 / (16 * length(w, 0, 1))), 'shorter than 1 m counts'
    for kind, counts in ((31, True), (32, False)):
        w = world()
        w.edge_kind[[0, 2]] = kind
        w.edge_cost[[0, 2]] = 5
        want = 5 / (16 * length(w, 0, 1)) if counts else 38 / (16 * length(w, 1, 2))
        assert rsnv.filter_scale(w, (256, 256, 255)) == scaled(want), kind


# ---- the search ------------------------------------------------------------------------------------------------------
def graph(pos, edges):
    """The tiny world's header and filters over a made-up graph: pos per node, edges (from, to, cost) walk edges."""
    w = world()
    n = len(pos)
    edges = sorted(edges, key=lambda e: e[0])
    w.node_pos = np.array(pos, np.float64)
    w.node_first_edge = np.searchsorted([e[0] for e in edges], np.arange(n + 1)).astype(np.int64)
    w.edge_target = np.array([e[1] for e in edges], np.int64)
    w.edge_cost = np.array([e[2] for e in edges], np.int64)
    w.edge_kind = np.zeros(len(edges), np.int64)
    w.edge_flags = np.zeros(len(edges), np.int64)
    w.node_flags = np.zeros(n, np.int64)
    w.cond = {}
    return w


def test_partial_path_to_the_least_heuristic():
    # goal 2 cut off; from 0 (far away) to 1 and 3, 50 each; the partial path ends at whichever is nearer the goal
    goal, start = (0.0, 10.0, 5.0), (10.0, 10.0, 25.0)
    for n1, n3, want in (((3.0, 10.0, 5.0), (0.0, 12.0, 7.0), 3),     # 3 m, 2.83 m
                         ((3.0, 10.0, 5.0), (0.0, 13.0, 6.0), 1),     # 3 m, 3.16 m
                         ((3.0, 10.0, 5.0), (-3.0, 10.0, 5.0), 1)):   # a tie (heuristic and cost): the lower index
        w = graph([start, n1, goal, n3], [(0, 1, 50), (0, 3, 50)])
        assert rsnv.search(w, 0, 2) == (rsnv.PARTIAL, 50, [0, want], 3), (n3, rsnv.search(w, 0, 2))


def test_equal_cost_parents():
    # one point (no heuristic): 0 - 1 - 3 and 0 - 2 - 3 both cost 2; 1 is expanded first, then 2 reaches 3 at the
    # same cost: the lower-indexed parent stays
    w = graph([(0.0, 0.0, 0.0)] * 4, [(0, 1, 1), (0, 2, 1), (1, 3, 1), (2, 3, 1)])
    assert rsnv.search(w, 0, 3) == (rsnv.DONE, 2, [0, 1, 3], 3)


def test_reached_again_more_cheaply():
    # 3 first reached at 100, then through 1 at 2: it must be searched again at 2, before the direct goal at 50
    w = graph([(0.0, 0.0, 0.0)] * 4, [(0, 3, 100), (0, 1, 1), (0, 2, 50), (1, 3, 1), (3, 2, 1)])
    assert rsnv.search(w, 0, 2)[:3] == (rsnv.DONE, 3, [0, 1, 3, 2])
