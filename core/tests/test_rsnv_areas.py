r"""RSNV navigation file (rsnv): line of sight between areas on made-up worlds of several areas

rsnv.World's line of sight where more than one area is involved, on worlds made here (no game data), each value
worked out by hand from the rules in rsnv's docstrings and rsnav's README ("Line of sight"): the area lookup in every
sector whose rectangle grown by LOS_SEAM holds the sample (bucket indices clamped to the grid, at most LOS_NEAR
areas, sectors in order); the allowance between two areas (LOS_COARSE_DY where either is coarse, 0.4 m and over,
else LOS_DY), another area's ground at a point; a coarse cell needing its neighbours on the sides a sample leans to
(walkable in its own bitmap, outside it, or in another area within the allowance at the neighbour's centre); a change
of area tested against the followed ground; portals before the buckets, never the current area; the start on the
area nearest the start's height within the step plus 0.5 m (the first in lookup order on a tie); the ground change
allowed at a change of area; gaps up to COARSE_GAP crossed in coarse areas only; the end's allowance by its area.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import math

import fuzzcheck
import numpy as np

from bfbc2core import rsnv

RSNV = fuzzcheck.fixtures('core/tests/test_rsnv.py')
_RAW = []
FINE, COARSE = 0.2, 0.8


def _tiny():
    """The tiny world, read once: its header (the 0.37 m step as float32) and arrays the made-up areas replace."""
    if not _RAW:
        import os
        import tempfile
        sections, header = RSNV.tiny()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'tiny.rsnv')
            rsnv.write(path, sections, header)
            _RAW.append(open(path, 'rb').read())
    return rsnv.load(_RAW[0])


def area(x0, z0, rows, pitch=FINE, y=0.0, grid=None, ports=(0, 0), sector=0):
    """A made-up area: its first cell's centre (x0, z0), bitmap rows (row j along z; '1' walkable), pitch, floor y,
    height grid (hw, hh, hstep mm, [cm]) or none (flat at y), its portals (first, count) and sector."""
    return dict(x0=x0, z0=z0, rows=rows, pitch=pitch, y=y, grid=grid, ports=ports, sector=sector)


def build(specs, ports=(), sectors=((0.0, 0.0, 100.0, 100.0),), buckets=None):
    """A world of the made-up areas. Sectors: (x0, z0, x1, z1), 25 x 25 buckets each, in order. Buckets: by default
    each area in every bucket of its sector its cells' squares touch, a bucket's areas by floor (as the format
    sorts them); or {(sector, bi, bj): [areas]}."""
    w = _tiny()
    areas, bmap, hght = [], bytearray(), []
    for k, s in enumerate(specs):
        rows, p = s['rows'], s['pitch']
        width, height = len(rows[0]), len(rows)
        off = len(bmap)
        for r in rows:
            row = bytearray((width + 7) >> 3)
            for i, c in enumerate(r):
                if c == '1':
                    row[i >> 3] |= 1 << (i & 7)
            bmap += row
        hw, hh, hstep, values = s['grid'] or (0, 0, 0, [])
        first = len(hght)
        hght += values
        areas.append(rsnv.Area(index=k, x0=s['x0'], z0=s['z0'], x1=s['x0'] + (width - 1) * p,
                               z1=s['z0'] + (height - 1) * p, min_y=s['y'], max_y=s['y'] + 2.0, bmap=off, width=width,
                               height=height, first_portal=s['ports'][0], portals=s['ports'][1], sector=s['sector'],
                               island=0, flags=0, first_node=0, nodes=0, pitch=p, hght=first, hw=hw, hh=hh,
                               hstep_mm=hstep))
    if buckets is None:
        buckets = {}
        for a in sorted(areas, key=lambda a: (a.min_y, a.index)):
            x0, z0, x1, z1 = sectors[a.sector]
            lo_i = min(max(math.floor((a.x0 - a.pitch / 2 - x0) / rsnv.BUCKET), 0), rsnv.BUCKETS - 1)
            hi_i = min(max(math.floor((a.x1 + a.pitch / 2 - x0) / rsnv.BUCKET), 0), rsnv.BUCKETS - 1)
            lo_j = min(max(math.floor((a.z0 - a.pitch / 2 - z0) / rsnv.BUCKET), 0), rsnv.BUCKETS - 1)
            hi_j = min(max(math.floor((a.z1 + a.pitch / 2 - z0) / rsnv.BUCKET), 0), rsnv.BUCKETS - 1)
            for bj in range(lo_j, hi_j + 1):
                for bi in range(lo_i, hi_i + 1):
                    buckets.setdefault((a.sector, bi, bj), []).append(a.index)
    nb = rsnv.BUCKETS * rsnv.BUCKETS
    gbkt = np.zeros((nb * len(sectors), 2), np.int64)
    glst = []
    for (s, bi, bj), listed in sorted(buckets.items()):
        gbkt[s * nb + bj * rsnv.BUCKETS + bi] = (len(glst), len(listed))
        glst += listed
    w.sectors = [(0x0B0B + s, 0, 0, x0, z0, x1, z1, s * nb, 0, 0, 0) for s, (x0, z0, x1, z1) in enumerate(sectors)]
    w.areas, w.bmap = areas, bytes(bmap)
    w.hght = np.array(hght or [0], np.uint16)
    w.gbkt, w.glst = gbkt, np.array(glst or [0], np.uint16)
    w.ports = list(ports)
    pm = min(a.pitch_mm_ for a in areas)
    w.min_pitch = float(np.float32(np.float32(pm) / np.float32(1000.0)))
    return w


def full(n, m=None):
    return ['1' * n] * (m or n)


def los(w, a, b):
    trace = {}
    return w.los(a, b, trace), trace.get('rule')


# ---- the area lookup -----------------------------------------------------------------------------------------------
def test_lookup_across_sector_seams():
    # two sectors side by side, 100 m each; one area in each corner bucket that matters
    w = build([area(98.0, 1.0, full(1)), area(101.0, 1.0, full(1), sector=1), area(1.0, 1.0, full(1)),
               area(98.0, 98.0, full(1)), area(1.0, 98.0, full(1)), area(101.0, 98.0, full(1), sector=1),
               area(198.0, 1.0, full(1), sector=1)],
              sectors=((0.0, 0.0, 100.0, 100.0), (100.0, 0.0, 200.0, 100.0)))
    near = w._near_los
    assert near(99.0, 1.0) == [0, 1], 'sector 1 grown by 2.5 m holds x = 99: its first bucket (clamped)'
    assert near(97.5, 1.0) == [0, 1] and near(97.49, 1.0) == [0], 'from 2.5 m before it'
    assert near(102.49, 1.0) == [0, 1] and near(102.5, 1.0) == [1], 'sector 0 to 2.5 m past it (excluded)'
    assert near(1.0, 1.0) == [2] and near(1.0, -2.5) == [2] and near(1.0, -2.51) == []
    assert near(-2.5, 1.0) == [2] and near(-2.51, 1.0) == []
    assert near(99.0, 99.0) == [3, 5] and near(1.0, 102.49) == [4] and near(1.0, 102.5) == []
    assert near(101.0, 99.0) == [3, 5] and near(101.0, 102.49) == [3, 5] and near(101.0, 102.5) == []
    assert near(202.49, 1.0) == [6] and near(202.5, 1.0) == [], 'sector 1 to 2.5 m past it, its last bucket'
    assert near(5.0, 1.0) == [] and near(1.0, 5.0) == [], 'the next bucket along each axis'


def test_lookup_at_most_128_areas():
    specs = [area(1.0, 1.0, full(1))]
    two = ((0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 100.0, 100.0))     # two layers over the same ground
    w = build(specs, sectors=two, buckets={(0, 0, 0): list(range(100)), (1, 0, 0): list(range(100, 200))})
    assert w._near_los(1.0, 1.0) == list(range(rsnv.LOS_NEAR)), 'the first sector\'s 100, then 28 of the second'
    w = build(specs, buckets={(0, 0, 0): list(range(129))})
    assert w._near_los(1.0, 1.0) == list(range(128))
    w = build(specs, buckets={(0, 0, 0): list(range(128))})
    assert w._near_los(1.0, 1.0) == list(range(128))
    w = build(specs, buckets={(0, 0, 0): list(range(127))})
    assert w._near_los(1.0, 1.0) == list(range(127))


# ---- allowances and other areas' ground ----------------------------------------------------------------------------
def test_allowance_between_areas():
    w = build([area(1.0, 1.0, full(1), pitch=p) for p in (0.2, 0.4, 0.399, 0.8)])
    dy, coarse = rsnv.LOS_DY, rsnv.LOS_COARSE_DY
    got = [[w._allowed(i, j) for j in range(4)] for i in range(4)]
    assert got == [[dy, coarse, dy, coarse], [coarse, coarse, coarse, coarse], [dy, coarse, dy, coarse],
                   [coarse, coarse, coarse, coarse]], got


P = (20.4, 20.4)                    # cell (2, 2) of a 5 x 5 fine area from (20, 20)


def test_other_areas_ground():
    def at(*floors, rows=None, extra=()):
        return build([area(20.0, 20.0, rows or full(5), y=y) for y in floors] + list(extra))
    w = at(0.0, 0.6)
    assert w._other_ok(0, *P, 0.0), 'another fine area 0.6 m away: within LOS_DY'
    assert w._other_ok(1, *P, 0.6) and w._other_ok(1, *P, 0.0)
    w = at(0.0, 0.625)
    assert not w._other_ok(0, *P, 0.0), '0.625 m between fine areas'
    assert not w._other_ok(1, *P, 0.625), 'the area itself is not another area'
    assert w._other_ok(0, *P, 0.625) and not w._other_ok(0, *P, 0.0)
    holed = ['11111', '11111', '11011', '11111', '11111']
    w = build([area(20.0, 20.0, full(5), y=0.0), area(20.0, 20.0, holed, y=0.0)])
    assert not w._other_ok(0, *P, 0.0), 'its cell there is not walkable'
    assert w._other_ok(0, 20.4 + 0.2 * 0.3, 20.4, 0.0), 'leaning on a walkable neighbour (on_cell)'
    coarse = area(19.6, 19.6, full(2), pitch=COARSE, y=1.5)     # cell (1, 1) centred on P
    w = build([area(20.0, 20.0, full(5), y=0.0), coarse])
    assert w._other_ok(0, *P, 0.0), '1.5 m to a coarse area: within LOS_COARSE_DY'
    assert w._other_ok(1, *P, -1.5) and not w._other_ok(1, *P, -1.6), 'from the coarse area: the same allowance'
    w = build([area(20.0, 20.0, full(5), y=0.0), dict(coarse, y=1.6)])
    assert not w._other_ok(0, *P, 0.0)
    w = build([area(20.0, 20.0, full(5), y=0.0)])
    assert not w._other_ok(0, *P, 0.0), 'no other area'
    w = build([area(20.0, 20.0, full(5), y=0.0), area(20.0, 20.0, full(5), y=0.0), area(20.0, 20.0, full(5), y=5.0)])
    assert w._other_ok(2, *P, 0.0), 'the first of the others that fits'


# ---- coarse cells and their neighbours ------------------------------------------------------------------------------
ROWS = ['10111',                    # (1, 0) missing
        '01111',                    # (0, 1) missing
        '11011',                    # (2, 2) missing
        '11111',
        '11111']
X0, Z0 = 20.0, 40.0


def on(i, j, fu=0.0, fv=0.0, pitch=COARSE):
    """The point fu, fv of a cell from cell (i, j)'s centre of the ROWS area."""
    return X0 + (i + fu) * pitch, Z0 + (j + fv) * pitch


def test_coarse_cells_need_their_neighbours():
    w = build([area(X0, Z0, ROWS, pitch=COARSE)])
    ok = lambda *c: w._cell_ok(0, *on(*c))          # noqa: E731
    assert not ok(1, 2, 0.3) and not ok(3, 2, -0.3), 'leaning along x to the missing (2, 2)'
    assert not ok(2, 1, 0.0, 0.3) and not ok(2, 3, 0.0, -0.3), 'along z'
    assert ok(1, 2, 0.2) and ok(3, 2, -0.2) and ok(2, 1, 0.0, 0.2) and ok(2, 3, 0.0, -0.2), 'not leaning'
    assert ok(1, 2, 0.0, 0.3) and ok(2, 1, 0.3), 'leaning to a walkable neighbour'
    assert not ok(1, 1, -0.3) and not ok(1, 1, 0.0, -0.3), 'the missing neighbour in the first column, the first row'
    assert ok(0, 3, -0.3) and ok(3, 0, 0.0, -0.3), 'leaning out of the bitmap: nothing to need'
    assert ok(4, 3, 0.3) and ok(3, 4, 0.0, 0.3), 'out past the last column, the last row'
    assert ok(3, 3, 0.3, 0.3) and ok(4, 4, 0.3, 0.3)
    assert not ok(2, 1, 0.3, 0.3), 'the side along x is walkable, the one along z is not'
    assert not ok(1, 2, 0.3, 0.3) and not ok(1, 2, 0.3, -0.3)
    assert ok(2, 2, 0.3) and not ok(2, 2), 'on the missing cell: leaning on a walkable one (on_cell), or not'
    # another area at the missing cell's centre within the coarse allowance stands in for it
    patch = area(X0 + 2 * COARSE - 0.2, Z0 + 2 * COARSE - 0.2, full(3), y=1.5)
    w = build([area(X0, Z0, ROWS, pitch=COARSE), patch])
    ok = lambda *c: w._cell_ok(0, *on(*c))          # noqa: E731
    assert ok(1, 2, 0.3) and ok(3, 2, -0.3) and ok(2, 1, 0.0, 0.3) and ok(2, 3, 0.0, -0.3)
    assert not ok(1, 1, -0.3), 'nothing at (0, 1)'
    w = build([area(X0, Z0, ROWS, pitch=COARSE), dict(patch, y=1.6)])
    assert not w._cell_ok(0, *on(1, 2, 0.3)), 'too high'


def test_coarse_from_0_4_m():
    for pitch, coarse in ((0.4, True), (0.399, False)):
        w = build([area(X0, Z0, ROWS, pitch=pitch)])
        assert w._cell_ok(0, *on(1, 2, 0.3, pitch=pitch)) is not coarse, pitch
        assert w._cell_ok(0, *on(1, 2, 0.0, pitch=pitch)), pitch


def test_coarse_neighbour_height_from_the_sample():
    # the area's own ground at the sample is what the other area must be near: a slope of 1 m per cell along x
    grid = (5, 1, 800, [0, 100, 200, 300, 400])
    rows = ['11111', '11011']
    w = build([area(X0, Z0, rows, pitch=COARSE, grid=grid),
               area(X0 + 2 * COARSE - 0.2, Z0 + COARSE - 0.2, full(3), y=3.5)])
    # from cell (2, 0) leaning +z, at x on the cell's centre: the ground there is 2.0, the patch 1.5 above
    assert w._cell_ok(0, *on(2, 0, 0.0, 0.3))
    w.areas[1] = dict_area(w.areas[1], min_y=3.6, max_y=5.6)
    assert not w._cell_ok(0, *on(2, 0, 0.0, 0.3))


def dict_area(a, **changes):
    import dataclasses
    return dataclasses.replace(a, **changes)


# ---- a change of area -----------------------------------------------------------------------------------------------
def test_compatible_against_the_followed_ground():
    holed = ['11111', '11111', '11011', '11111', '11111']
    w = build([area(20.0, 20.0, full(5), y=2.0), area(20.0, 20.0, full(5), y=0.6), area(20.0, 20.0, holed, y=0.0),
               area(20.0, 20.0, full(5), y=0.625), area(19.6, 19.6, full(2), pitch=COARSE, y=1.0)])
    assert w._compatible(0, 1, *P, 0.0), 'the followed ground (0.0) is what counts: 0.6 m'
    assert not w._compatible(0, 1, *P), 'without it, area 0\'s ground (2.0): 1.4 m'
    assert not w._compatible(0, 3, *P, 0.0), '0.625 m'
    assert not w._compatible(0, 2, *P, 0.0), 'its cell is not walkable'
    assert w._compatible(0, 4, *P, 0.0) and w._compatible(4, 1, *P, 0.0), 'a coarse area: 1.0 m is allowed'
    assert w._compatible(1, 0, *P) is False and w._compatible(1, 0, *P, 1.5)


def test_neighbour_portals_first():
    # four areas over P, floors 0.0 to 0.3 (the buckets' order); ports: the area's own range only
    ports = [(0, 0, 0, 0, 0, 0, 1), (0, 0, 0, 0, 0, 0, 2), (0, 0, 0, 0, 0, 0, 3), (0, 0, 0, 0, 0, 0, 0),
             (0, 0, 0, 0, 0, 0, 2)]
    specs = [area(20.0, 20.0, full(5), y=0.1 * k) for k in range(4)]
    w = build([dict(specs[0], ports=(2, 1))] + specs[1:], ports=ports)
    assert w._neighbour(0, *P, 0.0) == 3, 'its portal to area 3 before the buckets\' 1'
    w = build([dict(specs[0], ports=(3, 2))] + specs[1:], ports=ports)
    assert w._neighbour(0, *P, 0.0) == 2, 'a portal to itself skipped'
    w = build([dict(specs[0], ports=(1, 2))] + specs[1:], ports=ports)
    assert w._neighbour(0, *P, 0.0) == 2, 'the first of its portals'
    w = build(specs, ports=ports)
    assert w._neighbour(0, *P, 0.0) == 1, 'no portals: the buckets, itself skipped'
    assert w._neighbour(1, *P, 0.0) == 0
    assert w._neighbour(0, *P, 5.0) is None, 'none within the allowance'
    holed = ['11111', '11111', '11011', '11111', '11111']
    w = build([dict(specs[0], ports=(2, 1)), specs[1], specs[2], dict(specs[3], rows=holed)], ports=ports)
    assert w._neighbour(0, *P, 0.0) == 1, 'the portal\'s area does not fit: the buckets'
    assert w._neighbour(0, *P, 0.25) == 1 and w._neighbour(0, *P, 0.75) == 2


# ---- where a line starts ---------------------------------------------------------------------------------------------
def test_start_on_the_nearest_floor():
    w = build([area(20.0, 20.0, full(5), y=0.0), area(20.0, 20.0, full(5), y=0.5), area(20.0, 20.0, full(5), y=1.2)])
    assert w._los_start((*P[:1], 0.3, P[1])) == 1 and w._los_start((P[0], 0.1, P[1])) == 0
    assert w._los_start((P[0], 0.25, P[1])) == 0, 'a tie: the first in lookup order'
    assert w._los_start((P[0], 1.0, P[1])) == 2
    w = build([area(20.0, 20.0, full(5), y=0.0), area(20.0, 20.0, full(5), y=0.5)], buckets={(0, 5, 5): [1, 0]})
    assert w._los_start((P[0], 0.25, P[1])) == 1, 'a tie: the first in lookup order, whatever the floors'
    holed = ['11111', '11111', '11011', '11111', '11111']
    w = build([area(20.0, 20.0, full(5), y=0.0), area(20.0, 20.0, holed, y=0.5)])
    assert w._los_start((P[0], 0.5, P[1])) == 0, 'no ground under the point on the nearer floor'
    lim = w.step + 0.5
    w = build([area(20.0, 20.0, full(5), y=0.0)])
    assert w._los_start((P[0], lim, P[1])) == 0 and w._los_start((P[0], -lim, P[1])) == 0, 'the step plus 0.5 m'
    assert w._los_start((P[0], lim + 1e-6, P[1])) is None and w._los_start((P[0], -lim - 1e-6, P[1])) is None
    w = build([area(20.0, 20.0, full(5), y=0.0), area(20.0, 20.0, full(5), y=3.0)])
    assert w._los_start((P[0], 3.0, P[1])) == 1, 'the second area, index 1'
    assert w._los_start((50.0, 0.0, 50.0)) is None


# ---- lines between areas ---------------------------------------------------------------------------------------------
def strip(x0, n, pitch, y=0.0, z=30.0, **kw):
    """A one-row area of n cells along x from x0 at z."""
    return area(x0, z, ['1' * n], pitch=pitch, y=y, **kw)


def test_line_into_a_finer_area_rises_the_coarse_allowance():
    # coarse cells at 20.0 .. 21.6 (on_cell to 22.2), fine ones from 22.0: the line changes area 1.0 m up
    w = build([strip(20.0, 3, COARSE), strip(22.0, 11, FINE, y=1.0)])
    assert los(w, (20.0, 0.0, 30.0), (23.6, 1.0, 30.0)) == (True, None)
    w = build([strip(20.0, 3, COARSE), strip(22.0, 11, FINE, y=1.6)])
    assert los(w, (20.0, 0.0, 30.0), (23.6, 1.6, 30.0)) == (False, 'cell'), '1.6 m: more than LOS_COARSE_DY'
    w = build([strip(20.0, 11, FINE), strip(22.2, 11, FINE, y=1.0)])
    assert los(w, (20.0, 0.0, 30.0), (24.0, 1.0, 30.0)) == (False, 'cell'), 'between fine areas: LOS_DY'
    w = build([strip(20.0, 11, FINE), strip(22.2, 11, FINE, y=0.5)])
    assert los(w, (20.0, 0.0, 30.0), (24.0, 0.5, 30.0)) == (True, None)


def test_climb_inside_a_coarse_area():
    # 4 m over one 0.8 m cell: 1 m between samples 0.2 m apart (more than LOS_DY, less than LOS_COARSE_DY)
    grid = (2, 1, 800, [0, 400])
    w = build([area(20.0, 30.0, ['11'], pitch=COARSE, grid=grid)])
    a, b = (20.0, 0.0, 30.0), (20.8, 4.0, 30.0)
    assert abs(w.ground(w.areas[0], 20.2, 30.0) - 1.0) < 1e-12
    assert los(w, a, b) == (False, 'climb')


def test_gap_crossed_in_coarse_areas():
    # coarse cells on_cell to 22.2; the fine strip's from 22.85 (its first cell at 23.0): a 0.65 m gap
    w = build([strip(20.0, 3, COARSE), strip(23.0, 6, FINE, y=0.5)])
    assert los(w, (20.0, 0.0, 30.0), (24.0, 0.5, 30.0)) == (True, None), 'across, within the step plus the gap'
    w = build([strip(20.0, 3, COARSE), strip(23.0, 6, FINE, y=1.45)])
    assert los(w, (20.0, 0.0, 30.0), (24.0, 1.45, 30.0)) == (False, 'cell'), 'more than the step and 1 m beyond'
    w = build([strip(20.0, 3, COARSE), strip(23.5, 6, FINE, y=0.0)])
    assert los(w, (20.0, 0.0, 30.0), (24.5, 0.0, 30.0)) == (False, 'cell'), 'a gap over 1 m'
    w = build([strip(20.0, 9, 0.4), strip(24.0, 6, FINE, y=0.5)])      # 0.4 m cells on_cell to 23.5
    assert los(w, (20.0, 0.0, 30.0), (25.0, 0.5, 30.0)) == (True, None), '0.4 m cells are coarse'
    w = build([strip(20.0, 11, FINE), strip(22.85, 6, FINE, y=0.0)])
    assert los(w, (20.0, 0.0, 30.0), (23.85, 0.0, 30.0)) == (False, 'cell'), 'no gap crossed from a fine area'


def test_end_allowance_by_area():
    w = build([strip(20.0, 9, 0.4)])
    assert los(w, (20.0, 0.0, 30.0), (22.8, 1.0, 30.0)) == (True, None), 'ending on a 0.4 m (coarse) area'
    w = build([strip(20.0, 9, 0.399)])
    assert los(w, (20.0, 0.0, 30.0), (22.8, 1.0, 30.0)) == (False, 'end')


def test_lookup_in_a_sector_away_from_the_origin():
    w = build([area(1.0, 41.0, full(1))], sectors=((0.0, 40.0, 100.0, 140.0),))
    assert w._near_los(1.0, 41.0) == [0] and w._near_los(1.0, 37.5) == [0] and w._near_los(1.0, 37.49) == []
    assert w._near_los(1.0, 45.0) == [], 'the next bucket along z'


# ---- the walk along a line, with exact binary values ----------------------------------------------------------------
# 0.25 m (fine) and 1.0 m (coarse) cells: the samples, a quarter of the finest pitch apart, fall on exact values
Q = 0.25
Z = 30.0


def pts(swap, *xs):
    """Points (x, y) along the line at z = Z, or along z at x = Z (swap)."""
    return [(Z, y, x) if swap else (x, y, Z) for x, y in xs]


def lane(x0, rows, pitch, swap=False, **kw):
    """A one-row area along x from x0 at Z (one column along z with swap)."""
    if swap:
        return area(Z, x0, list(rows), pitch=pitch, **kw)
    return area(x0, Z, [''.join(rows)], pitch=pitch, **kw)


def test_long_lines():
    w = build([lane(0.0, '11', Q)])
    a = (0.0, 0.0, Z)
    assert los(w, a, (200000 * Q / 4, 0.0, Z)) == (False, 'cell'), '200000 samples are walked (rsnav_los.c)'
    assert los(w, a, (200000 * Q / 4 + Q / 4, 0.0, Z)) == (False, 'long'), 'more are not'


def test_first_sample_and_trace():
    # 1 m cells only: samples 0.25 m apart. From x = -0.75 (leaning on cell 0) to -1.25: the first sample, 0.25 m
    # along (past LOS_GRACE: tested), at -1.0 is on no cell
    w = build([lane(0.0, '1', 1.0)])
    trace = {}
    assert not w.los((-0.75, 0.0, Z), (-1.25, 0.0, Z), trace) and trace == {'rule': 'cell', 'k': 1, 'n': 2, 'x': -1.0, 'z': Z, 'along': 0.25, 'area': 0,
                                'gy': 0.0}, trace


def test_every_sample_is_tested():
    # a 0.25 m lane with cell 8 (centre 2.0) missing: only a sample within 0.0625 of its centre fails; from 0.0625
    # the samples (every 0.0625) hit it at the 31st
    for swap in (False, True):
        w = build([lane(0.0, '1' * 8 + '0' + '1' * 8, Q, swap)])
        assert los(w, *pts(swap, (0.0625, 0.0), (4.0625, 0.0))) == (False, 'cell'), swap


def test_grace_ends_at_0_25_m():
    a = (0.0, 0.0, Z)
    # cells from 0.5: the sample at 0.25 m (on no cell) is tested, the one at 0.3125 leans on the first cell
    w = build([lane(0.5, '1111', Q)])
    assert los(w, a, (1.25, 0.0, Z)) == (False, 'cell'), 'a sample at exactly LOS_GRACE is tested'
    # cells from 0.4375: on_cell from 0.25; the samples before (0.0625 .. 0.1875, on none) are not tested
    w = build([lane(0.4375, '1111', Q)])
    assert los(w, a, (1.1875, 0.0, Z)) == (True, None)


def test_start_ground_under_the_start():
    # area 0: one 0.25 m cell at 0, its height grid rising 2 m to the missing cell 1; area 1 from 0.25 at 0.0.
    # From (0, 0.5): the followed ground starts at area 0's ground under the start (0.0), so area 1 fits at 0.25 m
    w = build([lane(0.0, '10', Q, grid=(2, 1, 250, [0, 200])), lane(0.25, '1111', Q)])
    assert los(w, (0.0, 0.5, Z), (1.0, 0.0, Z)) == (True, None)


def test_a_neighbour_in_a_coarse_area_is_not_a_gap():
    # coarse cells 0 .. 2 (on_cell to 2.75); a fine lane from 2.625 whose cell at 2.875 is missing: the change of area
    # at 2.8125 is to the lane, and the next sample, on its missing cell, fails (no gap search past a neighbour)
    for swap in (False, True):
        w = build([lane(0.0, '111', 1.0, swap), lane(2.625, '10111111', Q, swap)])
        assert los(w, *pts(swap, (0.0, 0.0), (4.0, 0.0))) == (False, 'cell'), swap


def test_gap_to_the_last_sample():
    # coarse cell 0 (on_cell to 0.75); a fine lane on_cell from 1.5, where the line ends (its 24th sample). The
    # followed ground is 1.0 on both
    for swap in (False, True):
        w = build([lane(0.0, '1', 1.0, swap, y=1.0), lane(1.6875, '1111', Q, swap, y=1.0)])
        assert los(w, *pts(swap, (0.0, 1.0), (1.5, 1.0))) == (True, None), swap


def test_gap_of_exactly_1_m():
    # the first sample off the coarse cell is 0.8125; the lane is on_cell from 1.8125, exactly COARSE_GAP on, where
    # the allowance is LOS_STEP + the gap (1.0) + a sample (0.0625): a lane at exactly that height is reached
    for swap in (False, True):
        top = rsnv.LOS_STEP + 1.0625
        w = build([lane(0.0, '1', 1.0, swap), lane(2.0, '1' * 9, Q, swap, y=top)])
        assert los(w, *pts(swap, (0.0, 0.0), (4.0, top))) == (True, None), swap
        w = build([lane(0.0, '1', 1.0, swap), lane(2.0625, '1' * 9, Q, swap, y=0.0)])
        assert los(w, *pts(swap, (0.0, 0.0), (4.0, 0.0))) == (False, 'cell'), ('past 1 m', swap)


def test_climb_tested_from_the_grace_and_a_sample():
    # 4 m a metre (1 m a 0.25 m cell): 1 m from the start to the first tested sample (0.25 m, not climb-tested), then
    # 0.25 m a sample
    w = build([lane(0.0, '1' * 9, Q, grid=(9, 1, 250, [100 * k for k in range(9)]))])
    assert los(w, (0.0, 0.0, Z), (1.0, 4.0, Z)) == (True, None)
    assert los(w, (0.0, 0.0, Z), (1.0, 4.5, Z)) == (True, None), 'the end within LOS_DY'
    assert los(w, (0.0, 0.0, Z), (1.0, 3.0, Z)) == (False, 'end'), 'the end\'s ground under its x and z'


def test_climb_tested_from_grace_plus_a_sample():
    # area 0: one 0.25 m cell at 0.125, its grid rising 3.2 m to the missing next cell: 1.6 at 0.25 m, 2.4 at 0.3125
    # (the first climb-tested sample: 0.8 m, too much); area 1 at 2.4 m, on from 0.3125
    w = build([lane(0.125, '10', Q, grid=(2, 1, 250, [0, 320])), lane(0.5, '1111', Q, y=2.4)])
    trace = {}
    assert not w.los((0.0, -1.6, Z), (1.25, 2.4, Z), trace) and trace['rule'] == 'climb', trace
    assert (trace['k'], trace['n'], trace['x'], trace['along'], trace['area'], trace['prev_area']) == \
        (5, 20, 0.3125, 0.3125, 0, 0), trace
    assert (trace['gy'], trace['ny'], trace['lim']) == (1.6, 2.4, rsnv.LOS_DY)
    assert abs(trace['excess'] - 0.2) < 1e-12, trace


def test_climb_of_exactly_los_dy():
    # cells at -0.25, 0 and 0.25, their blocks 0, 0 and 2.4 m: 0.6 m a sample past 0; from -0.25 the tested samples
    # are at 0.0 (ground 0), 0.0625 (0.6: the first climb-tested) and 0.125 (1.2)
    w = build([lane(-0.25, '111', Q, grid=(3, 1, 250, [0, 0, 240]))])
    g = [w.ground(w.areas[0], x, Z) for x in (0.0, 0.0625, 0.125)]
    assert g[1] - g[0] == rsnv.LOS_DY and g[2] - g[1] == rsnv.LOS_DY, g
    assert los(w, (-0.25, 0.0, Z), (0.125, 1.2, Z)) == (True, None), 'exactly LOS_DY between samples'


def test_end_of_exactly_the_allowance():
    w = build([lane(0.0, '1111', Q)])
    for y in (rsnv.LOS_DY, -rsnv.LOS_DY):
        trace = {}
        assert w.los((0.0, 0.0, Z), (0.75, y, Z), trace) and trace == {}, y
    trace = {}
    assert not w.los((0.0, 0.0, Z), (0.75, 1.0, Z), trace)
    assert trace == {'rule': 'end', 'area': 0, 'd': -1.0, 'lim': rsnv.LOS_DY, 'excess': 1.0 - rsnv.LOS_DY,
                     'x': 0.75, 'z': Z, 'along': 0.75}, trace
