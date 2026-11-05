r"""RSNV navigation file (rsnv): ground heights, cells, lookups and the heuristic scale on made-up worlds

rsnv.World's geometry on made-up areas and the test_rsnv.py tiny world (no game data), each value worked out by hand
from the rules in rsnv's docstrings: ground() bilinear between block centres, extrapolated up to half a block past
the outer ones, the other blocks' weights renormalised around a block without cells (no extrapolation then), the
nearest filled block of the 3 x 3 when none carries weight (the first on a tie), the area's floor when none is
filled or there is no grid, the 4 m default block; one block wide; Area.cell (rounding half to even) against
_cell_of (half up); walkable at and past the bitmap's edges; _lean's graze band; on_cell leaning on a walkable
neighbour; areas_near's buckets and sector bounds; locate's height window, reach and tie order; nearest_node's
tie; fnv1a's published test values; heuristic_scale's least cost per metre on each axis, its cap and floor.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import dataclasses
import math

import fuzzcheck

from bfbc2core import rsnv

RSNV = fuzzcheck.fixtures('core/tests/test_rsnv.py')
HOLE = 0xFFFF


def tiny_world(tmp_path):
    sections, header = RSNV.tiny()
    path = str(tmp_path / 'tiny.rsnv')
    rsnv.write(path, sections, header)
    return rsnv.load(path)


def area(hw, hh, hstep_mm=1600, pitch=0.8, min_y=10.0):
    return rsnv.Area(index=0, x0=0.0, z0=0.0, x1=3.2, z1=3.2, min_y=min_y, max_y=min_y + 5, bmap=0, width=4,
                     height=4, first_portal=0, portals=0, sector=0, island=0, flags=0, first_node=0, nodes=0,
                     pitch=pitch, hght=0, hw=hw, hh=hh, hstep_mm=hstep_mm)


def heights(world, values):
    world.hght = list(values)
    return world


def close(a, b):
    return abs(a - b) < 1e-9


def test_ground_bilinear_and_extrapolated(tmp_path):
    w = heights(tiny_world(tmp_path), [0, 100, 200, 300])     # blocks of 2 cells: centres at 0.4 and 2.0 m
    a = area(2, 2)
    cases = [((0.4, 0.4), 10.0), ((2.0, 0.4), 11.0), ((1.2, 0.4), 10.5), ((0.4, 2.0), 12.0), ((1.2, 1.2), 11.5),
             ((2.0, 2.0), 13.0), ((0.0, 0.4), 9.75), ((-2.0, 0.4), 9.5), ((3.6, 0.4), 11.5), ((0.4, -0.4), 9.0),
             ((0.4, 9.0), 13.0)]
    got = [(p, w.ground(a, *p)) for p, _ in cases]
    assert all(close(g, want) for (_, g), (_, want) in zip(got, cases)), [(p, g, want) for (p, g), (_, want)
                                                                          in zip(got, cases) if not close(g, want)]


def test_ground_around_a_hole(tmp_path):
    w = heights(tiny_world(tmp_path), [0, 100, 200, HOLE])
    a = area(2, 2)
    assert close(w.ground(a, 1.2, 1.2), 11.0), 'three blocks a quarter each, renormalised: 100 cm'
    assert close(w.ground(a, 0.0, 0.4), 10.0), 'no extrapolation where a block has no cells'
    assert close(w.ground(a, 2.0, 0.8), 11.0 + 0.0), 'the hole has no weight at fv 0.25 with fu 1: 100 cm'
    w = heights(w, [HOLE, 100, 200, 300])
    assert close(w.ground(a, 1.6, 1.2), (0.375 * 100 + 0.125 * 200 + 0.375 * 300) / 0.875 / 100 + 10.0)


def test_ground_nearest_filled_block(tmp_path):
    w = tiny_world(tmp_path)
    # 3 x 3 blocks; the point on block (1, 1)'s centre, which has no cells and is the only one with weight
    grid = [HOLE] * 9
    grid[0] = 100                       # (0, 0): two blocks away, looked at first
    grid[5] = 250                       # (2, 1): one away, before (1, 2)
    grid[7] = 200                       # (1, 2): one away
    heights(w, grid)
    a = area(3, 3)
    assert close(w.ground(a, 2.0, 2.0), 12.5), 'the nearest, the first on a tie'
    grid[5] = HOLE
    assert close(heights(w, grid).ground(a, 2.0, 2.0), 12.0)
    grid[7] = HOLE
    assert close(heights(w, grid).ground(a, 2.0, 2.0), 11.0), 'two blocks away'
    assert close(heights(w, [HOLE] * 9).ground(a, 2.0, 2.0), 10.0), 'no block filled: the floor'
    corner = [HOLE] * 9
    corner[8] = 300                     # (2, 2), from a point at (0, 0): outside the 3 x 3 around it
    assert close(heights(w, corner).ground(a, 0.4, 0.4), 10.0)
    corner[4] = 50                      # (1, 1): diagonal
    assert close(heights(w, corner).ground(a, 0.4, 0.4), 10.5)


def test_ground_without_a_grid_and_one_block_wide(tmp_path):
    w = heights(tiny_world(tmp_path), [0, 100])
    assert w.ground(area(0, 2), 1.0, 1.0) == 10.0 and w.ground(area(2, 0), 1.0, 1.0) == 10.0
    tall = area(1, 2)                                     # one block wide, two deep
    assert close(w.ground(tall, 1.2, 1.2), 10.5) and close(w.ground(tall, 5.0, 1.2), 10.5)
    assert close(w.ground(tall, 0.4, 2.0), 11.0)
    default = area(2, 1, hstep_mm=0)                      # 4 m blocks of 0.8 m cells: 5 cells, centres 1.6 and 5.6 m
    assert close(w.ground(default, 5.6, 0.0), 11.0) and close(w.ground(default, 3.6, 0.0), 10.5)
    assert close(w.ground(default, 6.0, 0.0), 11.1), 'extrapolated past the last centre'
    fine = area(2, 1, hstep_mm=1600, pitch=0.2)           # 8 cells of 0.2 m a block: centres 0.7 and 2.3 m
    assert close(w.ground(fine, 1.5, 0.0), 10.5) and close(w.ground(fine, 2.3, 0.0), 11.0)


def test_cells_and_walkable(tmp_path):
    w = tiny_world(tmp_path)
    a = w.areas[0]                      # 4 x 4 cells of 0.8 m from (0, 0); cell (3, 3) not walkable
    assert [a.cell(x, 0.0) for x in (0.39, 0.4, 0.41, 2.4, 2.79, 2.81, -0.39, -0.41)] == \
        [(0, 0), (0, 0), (1, 0), (3, 0), (3, 0), None, (0, 0), None]
    assert a.cell(0.0, 2.81) is None and a.cell(0.0, 2.79) == (0, 3) and a.cell(1.21, 0.0) == (2, 0)
    assert [w._cell_of(a, x, 0.0) for x in (0.4, 0.39, 2.81, -0.41, 2.79)] == \
        [(1, 0, True), (0, 0, True), (4, 0, False), (-1, 0, False), (3, 0, True)]
    assert w._cell_of(a, 0.0, 2.81) == (0, 4, False) and w._cell_of(a, 0.0, -0.4) == (0, 0, True)
    assert [w.walkable(a, i, j) for i, j in ((3, 3), (2, 3), (3, 2), (0, 0), (-1, 0), (4, 0), (0, 4), (0, -1))] == \
        [False, True, True, True, False, False, False, False]
    assert [rsnv.World._lean(f) for f in (0.0, 0.2, 0.249, 0.25, 0.4, -0.25, -0.3)] == [0, 0, 0, 1, 1, -1, -1]
    assert w.on_cell(a, 0.0, 0.0) and not w.on_cell(a, 2.4, 2.4)
    assert w.on_cell(a, 2.4 - 0.8 * 0.3, 2.4) and w.on_cell(a, 2.4, 2.4 - 0.8 * 0.3), 'leaning on a walkable side'
    assert not w.on_cell(a, 2.4 - 0.8 * 0.2, 2.4), 'not near enough the side'
    assert w.on_cell(a, -0.8 * 0.7, 0.0) and not w.on_cell(a, -0.8 * 0.8, 0.0), 'past the bitmap, leaning back in'
    assert not w.on_cell(a, 5.0, 5.0)


def test_area_lookups(tmp_path):
    w = tiny_world(tmp_path)            # one sector over 0..100 m; bucket (0, 0) holds area 0, the rest none
    assert w.areas_near(1.0, 1.0) == [0] and w.areas_near(3.99, 3.99) == [0] and w.areas_near(0.0, 0.0) == [0]
    assert w.areas_near(4.0, 1.0) == [] and w.areas_near(1.0, 4.0) == []
    assert w.areas_near(-0.01, 1.0) == [] and w.areas_near(1.0, 100.0) == [] and w.areas_near(100.0, 1.0) == []
    assert w.areas_near(99.99, 99.99) == []
    loc = w.locate(2.5, 10.0, 2.5)
    assert loc is not None and loc[:2] == (0, (3, 2)) and close(loc[2], math.hypot(0.1, 0.9)), \
        f'(3, 3) is not walkable; (3, 2) and (2, 3) tie, rows first: {loc}'
    assert w.locate(2.5, 10.0, 2.5, max_dist=0.9) is None and w.locate(2.5, 10.0, 2.5, max_dist=0.91) is not None
    assert w.locate(1.6, 10.5, 1.6, dy=0.5) is not None and w.locate(1.6, 10.51, 1.6, dy=0.5) is None
    assert w.locate(1.6, 9.5, 1.6, dy=0.5) is not None and w.locate(1.6, 9.49, 1.6, dy=0.5) is None
    assert w.locate(1.6, 10.0 + w.step + 0.49, 1.6) is not None and w.locate(1.6, 10.0 + w.step + 0.51, 1.6) is None
    assert w.locate(1.6, 10.0, 1.6, max_dist=0.0) == (0, (2, 2), 0.0), 'on a cell centre'
    loc = w.locate(5.0, 10.0, 0.0, max_dist=2.7)
    assert loc is not None and loc[:2] == (0, (3, 0)) and close(loc[2], 2.6), f'from outside the area: {loc}'
    assert w.locate(5.0, 10.0, 0.0, max_dist=2.5) is None
    assert w.nearest_node(0, 2.3, 0.1) == 1 and w.nearest_node(0, 1.2, 0.0) == 0, 'the first in ANOD order on a tie'
    assert w.nearest_node(0, 0.0, 9.0) == 3


def test_fnv1a():
    assert [rsnv.fnv1a(t) for t in ('', 'a', 'foobar')] == [0x811C9DC5, 0xE40C292C, 0xBF9CF968]


def test_heuristic_scale():
    one = rsnv.H_SCALE_ONE
    axes = [(0, 0, 0), (0, 0, 2), (0, 2, 0), (2, 0, 0)]
    q = rsnv.heuristic_scale(axes, [0, 0, 0], [1, 2, 3], [24, 20, 30])        # 0.75, 0.625, 0.9375 per 16 m
    assert q == math.floor(0.625 * one) - 1, q
    assert rsnv.heuristic_scale(axes, [0, 0], [1, 3], [24, 30]) == math.floor(0.75 * one) - 1
    assert rsnv.heuristic_scale(axes, [0, 0], [2, 3], [20, 40]) == math.floor(0.625 * one) - 1
    assert rsnv.heuristic_scale(axes, [0, 0], [3, 1], [20, 40]) == math.floor(0.625 * one) - 1, 'x counts'
    assert rsnv.heuristic_scale([(0, 0, 0), (3, 0, 4)], [0], [1], [40]) == math.floor(0.5 * one) - 1
    assert rsnv.heuristic_scale([(0, 0, 0), (3, 0, 4)], [0], [1], [4000]) == math.floor(0.999 * one) - 1, 'capped'
    assert rsnv.heuristic_scale([(0, 0, 0), (3, 0, 4)], [0, 1], [1, 0], [40, 0]) == 1, 'a free edge'
    assert rsnv.heuristic_scale([(0, 0, 0), (0, 0, 0)], [0], [1], [5]) == int(0.999 * one), 'no edge has a length'
    assert rsnv.heuristic_scale([(0, 0, 0), (1e6, 0, 0)], [0], [1], [1]) == 1, 'at least 1'


def test_header_layout(tmp_path):
    # the layout rsnav (the C runtime) reads: magic, byte-order mark, version 1, the big-endian flag, the size, the
    # section count, the level's FNV-1a; the five floats at 0x18; the .hpd hash's first 4 bytes and the heuristic
    # scale at 48; the 16-byte directory entries from 64; sections 16-byte aligned
    import struct
    sections, header = RSNV.tiny()
    for big in (False, True):
        path = str(tmp_path / f'tiny-{big}.rsnv')
        size = rsnv.write(path, sections, dict(header, hpd_sha256='a1b2c3d4' + '00' * 28), big_endian=big)
        raw = open(path, 'rb').read()
        e = '>' if big else '<'
        assert raw[:4] == b'RSNV' and len(raw) == size
        assert struct.unpack_from(e + 'IHHIII', raw, 4) == (0x01020304, 1, int(big), size, len(sections),
                                                           rsnv.fnv1a('tiny'))
        assert raw[44:48] == bytes.fromhex('a1b2c3d4') and struct.unpack_from(e + 'I', raw, 48)[0] == 0
        assert raw[52:64] == bytes(12)
        tags = [raw[64 + 16 * k:68 + 16 * k].decode('ascii') for k in range(len(sections))]
        assert tags == ['SECT', 'NODE', 'NAUX', 'EDGE', 'AREA', 'BMAP', 'HGHT', 'GBKT', 'GLST', 'ANOD', 'ISLD', 'META']
        offsets = [struct.unpack_from(e + 'I', raw, 68 + 16 * k)[0] for k in range(len(sections))]
        assert offsets[0] == 64 + 16 * len(sections) == 256 and all(o % 16 == 0 for o in offsets), offsets
        assert offsets[1] == 256 + 32, 'one 32-byte SECT record, then NODE'
        assert size % 16 == 0 and size - offsets[-1] == 32, 'META (20 bytes) padded to 16'
        w = rsnv.load(path)
        assert w.big_endian is big and w.h_scale_q == 0 and w.h_scale == rsnv.H_SCALE_LEGACY


def test_format_constants():
    # values rsnav (rsnav.h) and the exporters share with this reader: a change here is a change of the file format
    # or of the runtime's rules, and must be made in rsnav too
    assert (rsnv.MAGIC, rsnv.VERSION, rsnv.HEADER, rsnv.H_SCALE_AT, rsnv.H_SCALE_ONE) == (b'RSNV', 1, 64, 48, 1 << 24)
    assert (rsnv.EDGE_WALK, rsnv.EDGE_JUMP, rsnv.EDGE_BOARD, rsnv.EDGE_ALIGHT) == (0, 4, 5, 6)
    assert (rsnv.NAUX_SHALLOW, rsnv.NAUX_WADE, rsnv.NAUX_BORDER, rsnv.NAUX_LADDER_END, rsnv.NAUX_JUMP_END) == \
        (1, 2, 4, 8, 16)
    assert (rsnv.EDGEF_COND, rsnv.EDGEF_TIGHT, rsnv.OFFM_BOARD_VEHICLE, rsnv.OFFM_MOUNT_STATIC) == (1, 2, 1, 5)
    assert (rsnv.ISLD_MAIN, rsnv.ISLD_CARRIER, rsnv.ISLD_LADDER_GROUND) == (1, 2, 4)
    assert rsnv.PITCHES == {0: 0.2, 1: 0.8} and (rsnv.COARSE_MM, rsnv.BUCKET, rsnv.BUCKETS) == (400, 4.0, 25)
    assert (rsnv.LOS_STEP, rsnv.LOS_DY, rsnv.COARSE_GAP, rsnv.LOS_GRACE, rsnv.LOS_SEAM, rsnv.LOS_NEAR,
            rsnv.LOS_GRAZE) == (0.37, 0.6, 1.0, 0.25, 2.5, 128, 0.25)
    assert abs(rsnv.LOS_COARSE_DY - (0.37 + 0.8 * 2 ** 0.5)) < 1e-12, 'the step plus a 0.8 m cell\'s diagonal'
    import dataclasses
    assert {f.name: f.default for f in dataclasses.fields(rsnv.Area)}['hstep_mm'] == 0
    assert {f.name: f.default for f in dataclasses.fields(rsnv.World)}['h_scale_q'] == 0


def los(w, a, b):
    trace = {}
    return w.los(a, b, trace), trace.get('rule')


def test_line_of_sight_rules(tmp_path):
    w = tiny_world(tmp_path)            # one 0.8 m (coarse) area, 4 x 4 cells from (0, 0) at 10 m; (3, 3) missing
    assert los(w, (0.0, 10.0, 0.0), (2.4, 10.0, 0.0)) == (True, None), 'along a walkable row'
    assert los(w, (0.0, 10.0, 0.0), (2.4, 10.0, 2.4)) == (False, 'cell'), 'into the missing cell'
    assert los(w, (0.0, 10.0, 0.0), (1.6, 10.0, 2.4)) == (True, None)
    assert los(w, (50.0, 10.0, 50.0), (51.0, 10.0, 50.0)) == (False, 'start'), 'no cell within 1 m of the start'
    assert los(w, (-0.9, 10.0, 0.0), (0.0, 10.0, 0.0))[1] != 'start', 'a cell within 1 m: started there'
    assert los(w, (0.0, 10.0, 0.0), (1e5, 10.0, 0.0)) == (False, 'long')
    assert los(w, (0.0, 10.0, 0.0), (2.4, 11.5, 0.0)) == (True, None), 'the end within the coarse allowance (1.5 m)'
    assert los(w, (0.0, 10.0, 0.0), (2.4, 11.6, 0.0)) == (False, 'end')
    assert los(w, (0.0, 10.0, 0.0), (2.4, 8.4, 0.0)) == (False, 'end'), 'below too'
    assert los(w, (0.0, 10.0, 0.0), (0.1, 11.6, 0.0)) == (False, 'end'), 'a short line still checks its end'


def test_coarse_cells_lean_on_their_neighbours(tmp_path):
    w = tiny_world(tmp_path)
    # a line in row 2 a little above its middle: past 0.25 of a cell it leans on row 3, whose x = 3 cell is missing
    assert los(w, (0.0, 10.0, 1.6 + 0.8 * 0.3), (2.4, 10.0, 1.6 + 0.8 * 0.3)) == (False, 'cell')
    assert los(w, (0.0, 10.0, 1.6 + 0.8 * 0.3), (1.9, 10.0, 1.6 + 0.8 * 0.3)) == (True, None), 'before x = 3'
    assert los(w, (0.0, 10.0, 1.6 + 0.8 * 0.2), (2.4, 10.0, 1.6 + 0.8 * 0.2)) == (True, None), 'not leaning'
    a = w.areas[0]
    assert w._cell_ok(0, 2.4, 1.6 + 0.8 * 0.3) is False and w._cell_ok(0, 2.4, 1.6) is True
    assert w._cell_ok(0, 2.4 - 0.8 * 0.3, 2.4) is True, 'on the missing cell, leaning on a walkable one'
    assert w._cell_ok(0, 0.0, -0.8 * 0.3) is True, 'leaning out of the area: nothing to need'
    assert w._allowed(0, 0) == rsnv.LOS_COARSE_DY and a.pitch_mm_ == 800


def test_line_of_sight_climbs(tmp_path):
    import dataclasses
    w = tiny_world(tmp_path)
    w.areas[0] = dataclasses.replace(w.areas[0], hw=2, hh=2, hstep_mm=1600)
    steep = heights(w, [0, 3000, 0, 3000])          # 30 m between block centres 1.6 m apart: 3.75 m a sample
    a = (0.0, steep.ground(w.areas[0], 0.0, 0.0), 0.0)
    b = (2.4, steep.ground(w.areas[0], 2.4, 0.0), 0.0)
    assert los(w, a, b) == (False, 'climb')
    gentle = heights(w, [0, 100, 0, 100])           # 1 m over 1.6 m
    a = (0.0, gentle.ground(w.areas[0], 0.0, 0.0), 0.0)
    b = (2.4, gentle.ground(w.areas[0], 2.4, 0.0), 0.0)
    assert los(w, a, b) == (True, None)
    assert los(w, a, (0.3, 20.0, 0.0)) == (False, 'end'), 'within the grace distance only the end is checked'


def test_load_refusals(tmp_path):
    import struct
    sections, header = RSNV.tiny()
    path = str(tmp_path / 'tiny.rsnv')
    size = rsnv.write(path, sections, header)
    raw = open(path, 'rb').read()

    def refused(data):
        try:
            rsnv.load(bytes(data))
        except ValueError as e:
            return str(e)
        return 'read'

    def edited(offset, fmt, *values):
        data = bytearray(raw)
        struct.pack_into(fmt, data, offset, *values)
        return data

    def entry(k):                                       # directory entry k: tag, offset, count, stride
        return 64 + 16 * k
    assert refused(raw) == 'read' and refused(raw[:63]) == 'RSNV: shorter than its header'
    assert refused(b'XSNV' + raw[4:]) == 'not an RSNV file'
    assert refused(edited(4, '<I', 0x04030201 + 1)) == 'RSNV: bad byte-order marker'
    assert refused(edited(8, '<H', 2)) == 'RSNV version 2, this reader reads 1'
    assert refused(raw + bytes(16)) == f'RSNV: header says {size} bytes, the file has {size + 16}'
    assert refused(edited(16, '<I', 4 * len(rsnv.SCHEMA) + 9)) == 'RSNV: section directory outside the file'
    assert refused(edited(16, '<I', (size - 64) // 16 + 1)) == 'RSNV: section directory outside the file'
    sect_off = struct.unpack_from('<I', raw, entry(0) + 4)[0]
    assert refused(edited(entry(0) + 4, '<I', sect_off + 1)) == 'RSNV: section SECT outside the file or misaligned'
    assert refused(edited(entry(0) + 4, '<I', size)) == 'RSNV: section SECT outside the file or misaligned'
    assert refused(edited(entry(1) + 12, '<I', 17)) == 'RSNV: section NODE stride 17'
    assert refused(edited(entry(5) + 12, '<I', 2)) == 'RSNV: section BMAP stride 2'
    twice = bytearray(raw)
    twice[entry(1):entry(1) + 16] = raw[entry(0):entry(0) + 16]
    assert refused(twice) == 'RSNV: section SECT appears twice'
    unknown = bytearray(raw)
    unknown[entry(11):entry(11) + 4] = b'XTRA'          # META renamed: an unknown tag is skipped
    assert refused(unknown) == 'read'
    unknown[entry(4):entry(4) + 4] = b'ZZZZ'
    assert refused(unknown) == 'RSNV: no AREA section'
    assert refused(edited(entry(1) + 8, '<I', 0)) == 'RSNV: NODE has no sentinel'


def test_ground_block_size_and_origin(tmp_path):
    w = tiny_world(tmp_path)
    assert heights(w, [50, 100]).ground(area(0, 2), 1.0, 1.0) == 10.0, 'no grid: the floor, whatever HGHT holds'
    heights(w, [0, 100])
    # 2 m blocks of 0.8 m cells: 2.5 cells, rounded up to 3 a block (centres 0.8 and 3.2 m)
    a = area(2, 1, hstep_mm=2000)
    assert close(w.ground(a, 3.2, 0.0), 11.0) and close(w.ground(a, 2.0, 0.0), 10.5)
    # the 4 m default in 0.127 m cells: 31.5 cells, rounded down to 31 (centres 15 and 46 cells in)
    a = area(2, 1, hstep_mm=0, pitch=0.127)
    assert close(w.ground(a, 46 * 0.127, 0.0), 11.0) and close(w.ground(a, 15 * 0.127, 0.0), 10.0)
    moved = dataclasses.replace(area(2, 2), x0=10.0, z0=20.0)
    heights(w, [0, 100, 200, 300])
    assert close(w.ground(moved, 10.4, 20.4), 10.0) and close(w.ground(moved, 12.0, 20.4), 11.0)
    assert close(w.ground(moved, 10.4, 22.0), 12.0)


def test_ground_picks_its_four_blocks(tmp_path):
    w = tiny_world(tmp_path)
    by_x = [0, 100, 400] * 3                      # 3 x 3 blocks of 2 cells, the height growing along x, not linearly
    heights(w, by_x)
    a = area(3, 3)
    assert [round(w.ground(a, x, 2.0), 9) for x in (0.4, 1.2, 2.0, 2.8, 3.6, 4.4)] == [10.0, 10.5, 11.0, 12.5, 14.0, 15.5]
    by_z = [0] * 3 + [100] * 3 + [400] * 3
    heights(w, by_z)
    assert [round(w.ground(a, 2.0, z), 9) for z in (0.4, 1.2, 2.0, 2.8, 3.6, 4.4)] == [10.0, 10.5, 11.0, 12.5, 14.0, 15.5]


def test_ground_one_block_exactly(tmp_path):
    # one block: the same value from both sides of a blend is not always the value itself in floating point
    # ((1 - f) * h + f * h), so a lone block is used as it is (as rsnav does)
    w = heights(tiny_world(tmp_path), [8806])
    p = 0.5107261997155396
    assert w.ground(area(1, 1), p, p) == 10.0 + 8806 / 100.0


def test_ground_weight_limit(tmp_path):
    # 1 m cells in 1 m blocks: at x = 1e-6, z = 1 the only filled block with weight is (1, 1), with weight exactly
    # 1e-6: too little (the limit is excluded), so the nearest filled block is taken, (0, 0) (its weight is 0)
    w = heights(tiny_world(tmp_path), [500, HOLE, HOLE, 300])
    a = area(2, 2, hstep_mm=1000, pitch=1.0)
    assert close(w.ground(a, 1e-6, 1.0), 15.0)
    assert close(w.ground(a, 2e-6, 1.0), 13.0), 'twice the weight: the block itself'


def test_ground_nearest_block_bounds(tmp_path):
    w = tiny_world(tmp_path)
    row = lambda *v: area(len(v), 1)              # noqa: E731
    col = lambda *v: area(1, len(v))              # noqa: E731
    A = 200
    for make in (row, col):
        def at(k, size):                          # a point on block k's centre along the row or column
            return (0.4 + 1.6 * k, 0.0) if make is row else (0.0, 0.4 + 1.6 * k)
        heights(w, [HOLE, A, HOLE])
        assert close(w.ground(make(0, 0, 0), *at(9, 3)), 12.0), 'far past the end: from the last block'
        heights(w, [HOLE, HOLE, A])
        assert w.ground(make(0, 0, 0), *at(-9, 3)) == 10.0, 'far before the start: two blocks from the first, none'
        assert w.ground(make(0, 0, 0), *at(0, 3)) == 10.0, 'two blocks away is past the 3 x 3'
        heights(w, [A, HOLE, HOLE])
        assert w.ground(make(0, 0, 0), *at(2, 3)) == 10.0 and w.ground(make(0, 0, 0), *at(9, 3)) == 10.0
    heights(w, [100, HOLE, 300])                  # a tie between both sides: the lower index first
    assert close(w.ground(row(0, 0, 0), 2.0, 0.0), 11.0)
    grid = [HOLE] * 6
    grid[4] = A                                   # (0, 2) of 2 x 3: three rows down from (1, 0), past the 3 x 3
    heights(w, grid)
    assert w.ground(area(2, 3), 2.0, 0.4) == 10.0, 'not read through the end of a row'
    heights(w, [HOLE] * 4)
    assert w.ground(area(2, 2), 2.0, 2.0) == 10.0, 'not read past the last row'
    heights(w, grid)
    assert w.ground(area(2, 3), 0.4, 0.4) == 10.0, 'not read before the first row'


# ---- away from the origin, wide sectors, odd bitmaps (2026-11-05: mutation testing, tools/mutate.py) --------------
def shifted_world(tmp_path, dx=137.0, dz=-61.0):
    """The tiny world moved by (dx, dz): sector, area and nodes."""
    sections, header = RSNV.tiny()
    s = list(sections['SECT'][0])
    s[3], s[4], s[5], s[6] = s[3] + dx, s[4] + dz, s[5] + dx, s[6] + dz
    a = list(sections['AREA'][0])
    a[0], a[1], a[2], a[3] = a[0] + dx, a[1] + dz, a[2] + dx, a[3] + dz
    nodes = [(x + dx, y, z + dz, e) for x, y, z, e in sections['NODE']]
    sections.update(SECT=[tuple(s)], AREA=[tuple(a)], NODE=nodes)
    path = str(tmp_path / 'shifted.rsnv')
    rsnv.write(path, sections, header)
    return rsnv.load(path)


def test_lookups_away_from_the_origin(tmp_path):
    dx, dz = 137.0, -61.0
    w = shifted_world(tmp_path, dx, dz)
    a = w.areas[0]
    assert a.cell(dx + 0.41, dz + 2.0) == (1, 2) and a.cell(dx - 0.41, dz) is None
    assert w._cell_of(a, dx + 0.41, dz + 0.41) == (1, 1, True)
    assert w.on_cell(a, dx + 0.1, dz + 0.1) and not w.on_cell(a, dx + 2.4, dz + 2.4)
    assert w.areas_near(dx + 1.0, dz + 1.0) == [0] and w.areas_near(dx - 1.0, dz + 1.0) == []
    loc = w.locate(dx + 2.5, 10.0, dz + 2.5)
    assert loc is not None and loc[:2] == (0, (3, 2)), loc
    assert w.nearest_node(0, dx + 2.3, dz + 0.1) == 1
    assert w.los((dx, 10.0, dz), (dx + 2.4, 10.0, dz)) and not w.los((dx, 10.0, dz), (dx + 2.4, 10.0, dz + 2.4))
    assert close(w.ground(a, dx + 1.0, dz + 1.0), 10.0)


def test_buckets_of_a_wide_sector(tmp_path):
    import numpy as np
    w = tiny_world(tmp_path)
    s = list(w.sectors[0])
    s[3], s[4], s[5], s[6] = -50.0, -30.0, 70.0, 70.0      # 120 m across x (more than 25 buckets), 100 m along z
    w.sectors = [tuple(s)]
    gbkt = np.zeros((rsnv.BUCKETS * rsnv.BUCKETS, 2), np.int64)
    gbkt[:, 0] = 3                                          # empty buckets (first 3, count 0)
    gbkt[0] = (0, 1)                                        # bucket (0, 0): area 0
    for bj in range(rsnv.BUCKETS):
        gbkt[bj * rsnv.BUCKETS + rsnv.BUCKETS - 1] = (1, 1)  # the last column: "area 7"
    for bi in range(rsnv.BUCKETS - 1):
        gbkt[(rsnv.BUCKETS - 1) * rsnv.BUCKETS + bi] = (2, 1)   # the last row (but its last bucket): "area 9"
    w.gbkt, w.glst = gbkt, np.array([0, 7, 9])
    assert w.areas_near(-49.0, -29.0) == [0] and w.areas_near(-50.0, -30.0) == [0]
    assert w.areas_near(-46.0, -29.0) == [], 'the next bucket along x'
    assert w.areas_near(46.5, 0.0) == [7] and w.areas_near(69.9, 0.0) == [7], 'past the 25th bucket: clamped to it'
    assert w.areas_near(45.9, 0.0) == [], 'bucket 23'
    assert w.areas_near(0.0, 69.9) == [9] and w.areas_near(0.0, 66.0) == [9] and w.areas_near(0.0, 65.9) == []
    assert w.areas_near(70.0, 0.0) == [] and w.areas_near(0.0, 70.0) == [] and w.areas_near(-50.1, 0.0) == []
    assert w.areas_near(0.0, -30.1) == []


def test_bitmap_rows(tmp_path):
    import dataclasses
    w = tiny_world(tmp_path)
    w.bmap = bytes([0xFF] * 4)                              # every bit set, the padding past the 4 cells too
    a = w.areas[0]
    assert w.walkable(a, 3, 3) and not w.walkable(a, 4, 0) and not w.walkable(a, -1, 0) and not w.walkable(a, 0, 4)
    w.bmap = bytes([0x00, 0x01, 0x00, 0x00])                # 16 cells a row: two bytes; cell (8, 0) in the second
    wide = dataclasses.replace(a, width=16, height=2)
    assert w.walkable(wide, 8, 0) and not w.walkable(wide, 0, 0) and not w.walkable(wide, 8, 1)
    w.bmap = bytes([0x00, 0x01])                            # 8 cells a row: one byte; cell (0, 1) in the second
    eight = dataclasses.replace(a, width=8, height=2)
    assert w.walkable(eight, 0, 1) and not w.walkable(eight, 0, 0)


def test_a_cell_reached_diagonally(tmp_path):
    w = tiny_world(tmp_path)
    w.bmap = bytes([0b0000, 0b0010, 0b0000, 0b0000])        # only cell (1, 1) walkable
    a = w.areas[0]
    assert w.on_cell(a, 0.8 * 0.3, 0.8 * 0.3), 'leaning on both sides: the diagonal cell'
    assert not w.on_cell(a, 0.8 * 0.3, -0.8 * 0.3), 'leaning the other way along z'
    assert not w.on_cell(a, -0.8 * 0.3, 0.8 * 0.3)
    assert w.on_cell(a, 0.8 * 0.7, 0.8 * 1.0), 'leaning on its side'


def test_locate_reach_and_ties(tmp_path):
    import numpy as np
    w = tiny_world(tmp_path)
    loc = w.locate(4.5, 10.0, 0.5, max_dist=3.0)
    assert loc is not None and loc[:2] == (0, (3, 1)), 'the area is in the bucket next to the point'
    loc = w.locate(5.5, 10.0, 0.0, max_dist=3.5)
    assert loc is not None and loc[:2] == (0, (3, 0)) and close(loc[2], 5.5 - 2.4), 'seven cells out, the radius in cells'
    x = float(np.float32(2.4)) / 2                          # exactly between nodes 0 and 1 (their float32 x)
    assert w.nearest_node(0, x, 0.0) == 0, 'a tie: the first in ANOD order'


def test_a_failed_write(tmp_path):
    sections, header = RSNV.tiny()
    target = tmp_path / 'taken'
    target.mkdir()
    (target / 'x').write_bytes(b'')                         # a folder that is not empty: the file cannot replace it
    try:
        rsnv.write(str(target), sections, header)
    except OSError:
        pass
    else:
        raise AssertionError('written over a folder')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['taken'], 'no temporary file left'


def test_heuristic_scale_off_the_origin():
    one = rsnv.H_SCALE_ONE
    assert rsnv.heuristic_scale([(1, 0, 0), (3, 0, 0)], [0], [1], [16]) == one // 2 - 1, '2 m at 16: 0.5 a metre'
    assert rsnv.heuristic_scale([(0, 0, 0), (1, 0, 0)], [0], [1], [1]) == one // 16 - 1, 'a cost of 1 is not 0'


def test_locate_edges_and_tall_sectors(tmp_path):
    w = tiny_world(tmp_path)
    loc = w.locate(0.5, 10.0, 4.5, max_dist=3.0)
    assert loc is not None and loc[0] == 0, 'the area is in the bucket next to the point along z'
    assert w.locate(1.6, 10.0, 2.4, max_dist=0.5)[:2] == (0, (2, 3)), 'the last row searched'
    assert w.locate(2.4, 10.0, 1.6, max_dist=0.5)[:2] == (0, (3, 2)), 'the last column searched'
    s = list(w.sectors[0])
    s[6] = 120.0                                            # 120 m along z: past 25 buckets
    w.sectors = [tuple(s)]
    assert w.areas_near(1.0, 119.0) == [], 'clamped to the last row of buckets (empty), not past the table'
