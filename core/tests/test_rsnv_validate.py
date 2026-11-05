r"""RSNV navigation file (rsnv): validate's checks, each at its limit and just past it (made-up world)

rsnv.World.validate on the test_rsnv.py tiny world (no game data), with one field changed per case: a value at a
check's limit passes and one just past it is reported with rsnav's message (rsn_load accepts exactly what this
accepts): the heuristic scale, node counts, positions (finite and inside +-1e6 m) and edge ranges, NAUX's areas and
sectors, the sectors' buckets, areas and bounds, the buckets' lists, ANOD, each area's bitmap, height grid (and its
step against the pitch), nodes, portals, pitch, sector and corners, the portals', ladders', off-mesh links' and
islands' nodes and areas, OREF's objects and CEDG's rows; a problem found twice is reported once; edge kinds this
reader does not name go to notes, not problems.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import dataclasses

import fuzzcheck
import numpy as np

from bfbc2core import rsnv

RSNV = fuzzcheck.fixtures('core/tests/test_rsnv.py')
_RAW = []


def world():
    """A fresh tiny world (4 nodes, 8 edges, one sector, one 4 x 4 area of 0.8 m), its arrays writable."""
    if not _RAW:
        import os
        import tempfile
        sections, header = RSNV.tiny()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'tiny.rsnv')
            rsnv.write(path, sections, header)
            _RAW.append(open(path, 'rb').read())
    w = rsnv.load(_RAW[0])
    for name in ('hght', 'glst', 'anod', 'node_first_edge', 'node_area', 'node_sector', 'edge_target', 'edge_kind',
                 'edge_flags', 'gbkt'):
        setattr(w, name, np.array(getattr(w, name)))
    w.sectors = [list(s) for s in w.sectors]
    return w


def problems(change):
    w = world()
    change(w)
    return w.validate()


def area(w, **fields):
    w.areas[0] = dataclasses.replace(w.areas[0], **fields)


def sector(w, k, value):
    w.sectors[0][k] = value


def test_the_tiny_world_passes():
    w = world()
    assert w.validate() == [] and w.notes == [], (w.validate(), w.notes)
    assert (len(w.node_pos), len(w.edge_target), len(w.areas), len(w.sectors)) == (4, 8, 1, 1)


def test_heuristic_scale_and_nodes():
    ok = lambda w: setattr(w, 'h_scale_q', 1 << 24)            # noqa: E731
    assert problems(ok) == []
    assert problems(lambda w: setattr(w, 'h_scale_q', (1 << 24) + 1)) == \
        ['the heuristic scale is over 1 (header word 48 above 1 << 24)']
    assert problems(lambda w: setattr(w, 'node_area', w.node_area[:-1])) == ['NAUX count is not the node count']
    assert problems(lambda w: w.node_first_edge.__setitem__(1, 5)) == ['NODE first edges not ascending']


def test_node_positions():
    for value, bad in ((999_999.0, False), (-999_999.0, False), (1e6, True), (-1e6, True), (float('nan'), True),
                       (float('inf'), True)):
        def change(w, value=value):
            w.node_pos = w.node_pos.copy()
            w.node_pos[2, 1] = value
        assert problems(change) == (['NODE position not a number or past 1e6 m'] if bad else []), value


def test_edge_ranges():
    def short_last(w):
        w.node_first_edge[-1] = 7
    assert 'NODE edge ranges do not cover EDGE' in problems(short_last)

    def late_first(w):
        w.node_first_edge[0] = 1
    assert problems(late_first) == ['NODE edge ranges do not cover EDGE']
    assert problems(lambda w: w.edge_target.__setitem__(0, 3)) == []
    assert problems(lambda w: w.edge_target.__setitem__(0, 4)) == ['EDGE target out of range']


def test_naux_ranges():
    assert problems(lambda w: w.node_area.__setitem__(0, 0xFFFF)) == [], 'a node in no area'
    assert problems(lambda w: w.node_area.__setitem__(0, 1)) == ['NAUX area or sector out of range']
    assert problems(lambda w: w.node_sector.__setitem__(0, 1)) == ['NAUX area or sector out of range']


def test_sectors():
    assert problems(lambda w: sector(w, 7, 1)) == ['SECT buckets past GBKT']
    assert problems(lambda w: sector(w, 8, 1)) == ['SECT areas past AREA']
    assert problems(lambda w: sector(w, 9, 2)) == ['SECT areas past AREA']
    assert problems(lambda w: sector(w, 9, 0)) == [], 'no areas'
    assert problems(lambda w: sector(w, 5, 0.0)) == [], 'x0 = x1'
    assert problems(lambda w: sector(w, 5, -1.0)) == ['SECT bounds inverted, not numbers or past 1e6 m']
    assert problems(lambda w: sector(w, 6, -1.0)) == ['SECT bounds inverted, not numbers or past 1e6 m']
    for k in (3, 4, 5, 6):
        assert problems(lambda w, k=k: sector(w, k, float('nan'))) == \
            ['SECT bounds inverted, not numbers or past 1e6 m'], k
    assert problems(lambda w: sector(w, 3, -1e6)) == ['SECT bounds inverted, not numbers or past 1e6 m']
    assert problems(lambda w: sector(w, 5, 1e6)) == ['SECT bounds inverted, not numbers or past 1e6 m']

    def two(w):
        w.sectors.append(list(w.sectors[0]))
        w.sectors[0][9] = w.sectors[1][9] = 5
    assert problems(two) == ['SECT areas past AREA'], 'one message for two sectors'


def test_buckets_lists_and_area_nodes():
    assert problems(lambda w: w.gbkt.__setitem__((0, 1), 1)) == []
    assert problems(lambda w: w.gbkt.__setitem__((0, 1), 2)) == ['GBKT list past GLST']
    assert problems(lambda w: w.gbkt.__setitem__((5, 0), 2)) == ['GBKT list past GLST'], 'an empty bucket past GLST'
    assert problems(lambda w: w.glst.__setitem__(0, 1)) == ['GLST area out of range']
    assert problems(lambda w: w.anod.__setitem__(3, 3)) == []
    assert problems(lambda w: w.anod.__setitem__(3, 4)) == ['ANOD node out of range']


def test_area_ranges():
    assert problems(lambda w: area(w, bmap=0, height=4)) == []
    assert problems(lambda w: area(w, height=5)) == ['AREA bitmap past BMAP']
    assert problems(lambda w: area(w, bmap=1)) == ['AREA bitmap past BMAP']
    assert problems(lambda w: area(w, width=9, height=2)) == [], 'two bytes a row, two rows'
    assert problems(lambda w: area(w, width=9, height=3)) == ['AREA bitmap past BMAP']
    grid = 'AREA height grid past HGHT or empty (every area has at least one block)'
    assert problems(lambda w: area(w, hght=1)) == [grid]
    assert problems(lambda w: area(w, hw=0)) == [grid] and problems(lambda w: area(w, hh=0)) == [grid]
    assert problems(lambda w: area(w, hw=2)) == [grid]

    def bigger(w):
        w.hght = np.zeros(4, np.uint16)
        area(w, hw=2, hh=2, hstep_mm=1600)
    assert problems(bigger) == []
    assert problems(lambda w: area(w, first_node=1)) == ['AREA nodes past ANOD']
    assert problems(lambda w: area(w, nodes=5)) == ['AREA nodes past ANOD']
    assert problems(lambda w: area(w, portals=1)) == ['AREA portals past PORT']


def test_area_pitch_step_and_corners():
    assert problems(lambda w: area(w, pitch=0.05, hstep_mm=1600)) == []
    assert problems(lambda w: area(w, pitch=0.049, hstep_mm=1600)) == ['AREA pitch or sector invalid']
    assert problems(lambda w: area(w, sector=1)) == ['AREA pitch or sector invalid']
    step = 'AREA height grid step under its pitch or over 100 m'
    assert problems(lambda w: area(w, hstep_mm=800)) == [], 'a block of one cell'
    assert problems(lambda w: area(w, hstep_mm=799)) == [step]
    assert problems(lambda w: area(w, hstep_mm=100_000)) == []
    assert problems(lambda w: area(w, hstep_mm=100_001)) == [step]
    assert problems(lambda w: area(w, hstep_mm=0, pitch=4.0)) == [], 'the 4 m default, one cell a block'
    assert problems(lambda w: area(w, hstep_mm=0, pitch=4.001)) == [step]
    corner = 'AREA corner or heights inverted, not numbers or past 1e6 m'
    assert problems(lambda w: area(w, min_y=10.0, max_y=10.0)) == []
    assert problems(lambda w: area(w, min_y=10.5, max_y=10.0)) == [corner]
    for field in ('x0', 'z0', 'min_y', 'max_y'):
        value = 2e6 if field != 'min_y' else -2e6
        assert problems(lambda w, f=field, v=value: area(w, **{f: v})) == [corner], field


def test_links_and_objects():
    port = (0.0, 0.0, 0.0, 0.0, 0, 0, 0)
    assert problems(lambda w: setattr(w, 'ports', [port])) == []
    assert problems(lambda w: setattr(w, 'ports', [port[:5] + (1, 0)])) == ['PORT area out of range']
    assert problems(lambda w: setattr(w, 'ports', [port[:6] + (1,)])) == ['PORT area out of range']
    ladder = (0.0,) * 7 + (3, 3)
    assert problems(lambda w: setattr(w, 'ladders', [ladder])) == []
    assert problems(lambda w: setattr(w, 'ladders', [ladder[:7] + (4, 0)])) == ['LADR node out of range']
    assert problems(lambda w: setattr(w, 'ladders', [ladder[:8] + (4,)])) == ['LADR node out of range']
    assert problems(lambda w: setattr(w, 'offmesh', [(1, 1, 0, 0xFFFFFFFF)])) == [], 'a link to no node'
    assert problems(lambda w: setattr(w, 'offmesh', [(1, 1, 0, 3)])) == []
    assert problems(lambda w: setattr(w, 'offmesh', [(1, 1, 0, 4)])) == ['OFFM node out of range']
    assert problems(lambda w: setattr(w, 'islands', [(4, 3)])) == []
    assert problems(lambda w: setattr(w, 'islands', [(4, 4)])) == ['ISLD representative out of range']

    def objects(w, oref, rows, flagged=(0,)):
        w.objects = [('object',)] * 2
        w.oref = oref
        w.cedg_rows = rows
        for e in flagged:
            w.edge_flags[e] |= rsnv.EDGEF_COND
    assert problems(lambda w: objects(w, [0, 1], [(0, 0, 2, 0)])) == []
    assert problems(lambda w: objects(w, [0, 2], [(0, 0, 2, 0)])) == ['OREF object out of range']
    listed = 'CEDG does not list exactly the conditional edges, in order'
    assert problems(lambda w: objects(w, [0], [])) == [listed], 'a flagged edge not listed'
    assert problems(lambda w: objects(w, [0], [(0, 0, 1, 0)], flagged=())) == [listed], 'listed, not flagged'
    assert problems(lambda w: objects(w, [0], [(1, 0, 1, 0)])) == [listed], 'another edge listed'
    none = 'CEDG objects past OREF or none'
    assert problems(lambda w: objects(w, [0], [(0, 0, 0, 0)])) == [none]
    assert problems(lambda w: objects(w, [0], [(0, 0, 2, 0)])) == [none]
    assert problems(lambda w: objects(w, [0, 1], [(0, 1, 1, 0)])) == [], 'the second object alone'
    assert problems(lambda w: objects(w, [0, 1], [(0, 0, 1, 0), (7, 1, 1, 0)], flagged=(0, 7))) == []
    assert problems(lambda w: objects(w, [0, 1], [(0, 0, 1, 0), (9, 1, 1, 0)])) == \
        ['CEDG lists an edge out of range or out of order']


def test_unknown_edge_kinds_are_notes():
    w = world()
    w.edge_kind[0] = 9
    w.edge_kind[1] = 40
    assert w.validate() == [] and w.notes == ['EDGE kinds this reader does not name: [9, 40] (no kind extra; kinds '
                                              'past 31 never used)'], w.notes
    w.edge_kind[:] = 6
    assert w.validate() == [] and w.notes == []


class Rows(np.ndarray):
    """An array that reports more rows than it holds (a node count at rsnav's limit without 2^24 nodes)."""
    count = 0

    def __len__(self):
        return self.count


def test_node_count_limit():
    for count, bad in (((1 << 24) - 1, False), (1 << 24, True)):
        w = world()
        pos = w.node_pos.view(Rows)
        pos.count = count
        w.node_pos = pos
        assert ('more nodes than an edge can address' in w.validate()) is bad, count


def test_more_guards():
    def short_naux(w):
        w.node_area = np.array([7, 0, 0])               # one short, and an area out of range: only the count told
    assert problems(short_naux) == ['NAUX count is not the node count']

    def no_nodes(w):
        w.node_pos = np.zeros((0, 3))
        w.node_area = w.node_sector = np.zeros(0, np.int64)
        w.node_first_edge = np.array([8])
        w.anod = np.zeros(0, np.int64)
    assert 'NODE edge ranges do not cover EDGE' not in problems(no_nodes), 'with no nodes only the end is checked'

    def empty_lists(w):
        w.gbkt = np.zeros((0, 2), np.int64)
        w.glst = np.zeros(0, np.int64)
        w.anod = np.zeros(0, np.int64)
        w.sectors = []
        w.areas = []
    assert problems(empty_lists) == ['NAUX area or sector out of range'], 'empty tables read as empty (no crash)'
    assert problems(lambda w: sector(w, 6, 0.0)) == [], 'z0 = z1'
    for k, value in ((4, -2e6), (6, 2e6)):
        assert problems(lambda w, k=k, v=value: sector(w, k, v)) == \
            ['SECT bounds inverted, not numbers or past 1e6 m'], (k, value)


def test_cedg_from_cond():
    # a World made in code (not by load) has no cedg_rows or oref: they are made from its cond, each edge's objects
    # from the first
    w = world()
    del w.cedg_rows
    del w.oref
    w.objects = [('object',)]
    w.cond = {0: [0]}
    w.edge_flags[0] |= rsnv.EDGEF_COND
    assert w.validate() == []
    w.cond = {0: [1]}
    assert w.validate() == ['OREF object out of range']
