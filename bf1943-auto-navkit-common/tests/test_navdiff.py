r"""NavKit (both variants): content diff of two path-data files on made-up builds (navdiff)

navdiff on .hpd files built here with navbuild from made-up samples (no game data): a 48 m square with a 6 m gap
(two islands), the same square whole, and a 128 m strip over two sectors. A file against itself is 100 % on every
layer; the gap against the whole square reproduces every reference cell and leaves the gap's 7 x 60 cells as extra;
the other way round they are missing; a sector in one file only counts fully as missing / extra; the clip box leaves
reference cells outside it out. Also area_cells (even-odd fill, ground from the nearest boundary point, y_offset,
points), pieces, match_cells on stacked floors, the per-sector log, the maps and the command line (--json, --png).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json
import os
from types import SimpleNamespace

import numpy as np

import navbuild
import navdiff
import navsample


def flat(pitch, min_x, min_z, nx, nz, y=0.0, ground=None):
    """One flat surface per column; ground(x, z) -> False leaves the column empty (test_navbuild_edges.py)."""
    counts = np.ones(nx * nz, np.int64)
    if ground:
        for j in range(nz):
            for i in range(nx):
                counts[j * nx + i] = bool(ground(min_x + i * pitch, min_z + j * pitch))
    start = np.zeros(nx * nz + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    n = int(start[-1])
    return navsample.Samples(pitch, min_x, min_z, nx, nz, 50.0, -50.0, 0.4, 0.6, 1.8, 0.0, start,
                             np.full(n, y, np.float32), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                             np.zeros(n, np.uint8), None)


def build(path, nx=60, nz=60, ground=None):
    s = flat(0.8, 0.0, 0.0, nx, nz, ground=ground)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, str(path), log=lambda *a: None)
    finally:
        navsample.load = load
    return str(path)


def gap(tmp_path):
    return build(tmp_path / 'gap.hpd', ground=lambda x, z: not (20 < x < 26))


def whole(tmp_path):
    return build(tmp_path / 'whole.hpd')


QUIET = lambda *a: None  # noqa: E731


def test_same_file_is_full_parity(tmp_path):
    path = gap(tmp_path)
    res, rows, R, C = navdiff.diff(path, path, log=QUIET)
    assert res['sectors'] == {'reference': 1, 'candidate': 1, 'only_reference': [], 'only_candidate': []}
    assert res['cells']['parity'] == 100.0 and res['cells']['only_candidate'] == 0
    assert res['areas'] == {'reference': 6, 'candidate': 6, 'same': 6, 'split': 0, 'other': 0, 'parity': 100.0,
                            'detailed_reference': 0, 'detailed_candidate': 0}
    assert res['bounds_of_same_areas'] == {'outline': 100.0, 'portals': 100.0, 'borders': 100.0}
    g = res['graph']
    assert (g['nodes_same'], g['nodes_reference'], g['edges_same'], g['edges_reference']) == (13, 13, 36, 36), g
    assert g['node_parity'] == 100.0 and g['edge_parity'] == 100.0 and g['costs_same_of_same_edges'] == 100.0
    assert len(rows) == 1 and set(R) == set(C) == {0}


def test_gap_against_whole(tmp_path):
    res, _rows, _R, _C = navdiff.diff(gap(tmp_path), whole(tmp_path), log=QUIET)
    cells = res['cells']
    assert cells['parity'] == 100.0 and cells['only_reference'] == 0, cells
    assert cells['only_candidate'] == 7 * 60, 'the gap: 7 columns of 60 cells, in the candidate only'
    assert cells['candidate'] == 60 * 60
    assert res['areas']['other'] + res['areas']['same'] + res['areas']['split'] == 6
    assert res['worst_sectors'] == [{'id': '0x0', 'cells_only_reference': 0, 'cells_only_candidate': 420,
                                     'areas_same': f"{res['areas']['same']}/6"}]


def test_whole_against_gap(tmp_path):
    res, _rows, _R, _C = navdiff.diff(whole(tmp_path), gap(tmp_path), log=QUIET)
    assert res['cells']['only_reference'] == 420 and res['cells']['only_candidate'] == 0
    assert res['cells']['parity'] == round(100.0 * 3180 / 3600, 2)


def test_clip(tmp_path):
    res, _rows, R, _C = navdiff.diff(whole(tmp_path), gap(tmp_path), log=QUIET, clip=(0.0, 0.0, 19.9, 100.0))
    assert res['cells']['only_reference'] == 0, 'the gap is outside the clip box'
    kept = [ij for cells in R[0].cells() for ij in cells]
    assert kept and max(i for i, _ in kept) * navdiff.GRID <= 19.9


def test_sector_in_one_file_only(tmp_path):
    strip = build(tmp_path / 'strip.hpd', nx=160, nz=40)
    res, rows, _R, _C = navdiff.diff(gap(tmp_path), strip, log=QUIET)
    assert res['sectors']['only_candidate'] == ['0x100'] and res['sectors']['only_reference'] == []
    assert len(rows) == 1, 'only the shared sector is compared'
    back, _rows, _R, _C = navdiff.diff(strip, gap(tmp_path), log=QUIET)
    assert back['sectors']['only_reference'] == ['0x100']
    shared = back['cells']['reference'] - back['cells']['same'] - back['cells']['only_reference']
    assert shared == 0
    # the strip's second sector counts fully as missing: its nodes and edges are in the reference totals
    assert back['graph']['nodes_reference'] > rows[0]['nodes_cand']


def test_per_sector_log(tmp_path):
    lines = []
    navdiff.diff(gap(tmp_path), whole(tmp_path), per_sector=True, log=lines.append)
    assert lines[0].startswith('sectors   reference 1, candidate 1')
    assert 'worst sectors' in lines[5] and lines[6].split()[0] == '0x0', lines


def square(y=0.95, size=1.6):
    pts = [(0.0, y, 0.0), (size, y, 0.0), (size, y, size), (0.0, y, size), (0.0, y, 0.0)]
    return SimpleNamespace(outline=[pts], portals=[], border_portals=[])


def test_area_cells():
    cells = navdiff.area_cells(square())
    assert sorted(cells) == [(0, 0), (0, 1), (1, 0), (1, 1)], sorted(cells)
    assert all(abs(v) < 1e-6 for v in cells.values()), 'ground = outline height - 0.95'
    bf1943 = navdiff.area_cells(square(), y_offset=0.9)
    assert all(abs(v - 0.05) < 1e-6 for v in bf1943.values())
    with_points = navdiff.area_cells(square(), points=True)
    ground, x, z = with_points[(1, 1)]
    assert abs(ground) < 1e-6 and (x, z) in {(0.0, 0.0), (1.6, 1.6), (0.0, 1.6), (1.6, 0.0)},         'the boundary point the ground comes from: (1, 1) is as near to each corner'
    assert navdiff.area_cells(SimpleNamespace(outline=[], portals=[], border_portals=[])) == {}
    sliver = SimpleNamespace(outline=[[(0.1, 1.0, 0.1), (0.3, 1.0, 0.1), (0.3, 1.0, 0.3), (0.1, 1.0, 0.1)]],
                             portals=[], border_portals=[])
    assert navdiff.area_cells(sliver) == {}, 'no sample point inside'


def test_segments_and_pieces():
    portal = SimpleNamespace(p0=(0.0, 0.0, 0.0), p1=(0.0, 0.0, 1.6))
    area = SimpleNamespace(outline=[[(0.0, 0.0, 0.0), (1.6, 0.0, 0.0)]], portals=[portal], border_portals=[portal])
    assert len(navdiff.segments(area)) == 3
    one_way = navdiff.pieces([((0.0, 0.0, 0.0), (1.6, 0.0, 0.0))], 0.8)
    other_way = navdiff.pieces([((1.6, 0.0, 0.0), (0.0, 0.0, 0.0))], 0.8)
    assert one_way == other_way == {((0, 0), (1, 0)), ((1, 0), (2, 0)), ((2, 0), (3, 0)), ((3, 0), (4, 0))}
    assert navdiff.pieces([((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))], 0.8) == {((0, 0), (0, 0))}, 'a point: one piece'


def test_match_cells_stacked():
    # two floors at (0, 0) in each file, 3 m apart; the candidate's lower floor is 0.5 m off, its upper 5 m off
    ref = [{(0, 0): 0.0, (1, 0): 0.0}, {(0, 0): 3.0}]
    cand = [{(0, 0): 0.5}, {(0, 0): 8.0, (2, 0): 0.0}]
    ref_to_cand, used = navdiff.match_cells(ref, cand)
    assert ref_to_cand == {(0, (0, 0)): (0, (0, 0))}, 'nearest heights pair; 5 m apart (> STACK_TOL) does not'
    assert used == {(0, (0, 0))}
    single = navdiff.match_cells([{(0, 0): 0.0}], [{(0, 0): 30.0}])
    assert single[0] == {(0, (0, 0)): (0, (0, 0))}, 'one floor each side: the same ground, whatever the heights'


def test_pngs_and_main(tmp_path):
    ref, cand = gap(tmp_path), whole(tmp_path)
    out_json, out_png = tmp_path / 'diff.json', tmp_path / 'maps'
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = navdiff.main(['navdiff.py', ref, cand, '--sectors', '--json', str(out_json), '--png', str(out_png),
                             '--top', '1'])
    assert code == 0 and 'worst sectors' in out.getvalue()
    res = json.loads(out_json.read_text(encoding='utf-8'))
    assert res['cells']['only_candidate'] == 420
    assert os.listdir(out_png) == ['sector_0000.png']
    assert (out_png / 'sector_0000.png').read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert navdiff.main(['navdiff.py', ref]) == 1
    assert 'Usage' in out.getvalue()


def test_pct():
    assert navdiff.pct(1, 4) == 25.0 and navdiff.pct(0, 0) == 100.0, 'nothing to reproduce: full parity'
