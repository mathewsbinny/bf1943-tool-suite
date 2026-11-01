r"""NavKit little-endian: cell-by-cell area boundary diagnostic (navboundary)

diagnose on a made-up pair of cell sets with known answers (a 4 x 2 grid: DICE splits it in three areas, one a single
cell, NavKit in two; a column with two floors, a cell in each dataset only, a detailed area): edge kinds (match,
missing, extra), merged / split / exact areas, precision and recall at each tolerance, portal displacement, terrain
height and slope from a made-up height field. Also cells_of on an empty file, _nearest, worst_square, the CSV export,
the maps, and the command line on a made-up OPFOR folder (DICE's file and NavKit's built here with navbuild from
made-up samples; no game data): without terrain heights, with them, with --region; no candidate is a clear error.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import csv
import io
import json
import os
from types import SimpleNamespace

import numpy as np

import navboundary
import navbuild
import navsample
from bfbc2core import level as levelmod
from bfbc2core import navdata

G = navdata.GRID
LV = 'sp_01'


def area(index, pitch=0.8, portals=()):
    return navdata.NavArea(0, index, (0, 0), 0, pitch, 0.0, 1.0, portals=list(portals))


def portal(x0, z0, x1, z1):
    return (np.array([x0, 0.95, z0]), np.array([x1, 0.95, z1]), 0)


def nav(cells, areas):
    """cells: [(i, j, area index)]."""
    return SimpleNamespace(areas=areas, cell_xz=np.array([(i * G, j * G) for i, j, _ in cells], float).reshape(-1, 2),
                           cell_area=np.array([a for _, _, a in cells], np.int64))


def made_up():
    dice_cells = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (2, 0, 1), (2, 1, 1), (3, 0, 1), (3, 1, 2),
                  (5, 0, 0), (5, 0, 1),            # two floors: left out
                  (7, 0, 0)]                       # DICE only
    navkit_cells = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (2, 0, 0), (2, 1, 0), (3, 0, 1), (3, 1, 1),
                    (5, 0, 0),
                    (6, 0, 1),                     # NavKit only
                    (9, 9, 2)]                     # in a detailed area: not a normal cell
    dice = nav(dice_cells, [area(0, portals=[portal(1.2, -0.4, 1.2, 1.2)]), area(1), area(2)])
    navkit = nav(navkit_cells, [area(0, portals=[portal(2.0, -0.4, 2.0, 1.2)]), area(1),
                                area(2, pitch=0.2, portals=[portal(9.0, 9.0, 9.0, 9.2)])])
    return dice, navkit


class Slope:
    """A made-up height field: a plane rising 0.1 m per metre in x."""
    def height(self, x, z):
        return 0.1 * np.asarray(x, float) + 0.0 * np.asarray(z, float)


def test_diagnose_known_answers():
    dice, navkit = made_up()
    summary, cells, edges, portals, only = navboundary.diagnose(dice, navkit, Slope())
    assert summary['shared_cells'] == 8 and summary['multi_layer_columns_left_out'] == 1
    assert summary['dice_only_cells'] == 1 and summary['navkit_only_cells'] == 1
    assert (summary['edges'], summary['match'], summary['missing'], summary['extra']) == (10, 1, 3, 1), summary
    assert summary['dice_areas'] == 3 and summary['dice_single_cell_areas'] == 1
    assert summary['dice_areas_merged_by_navkit'] == 3 and summary['dice_areas_split_by_navkit'] == 1
    assert summary['exact_areas'] == 0 and summary['exact_areas_pct'] == 0.0
    assert summary['boundary_precision_0'] == 50.0 and summary['boundary_recall_0'] == 25.0
    for t in (1, 2):
        assert summary[f'boundary_precision_{t}'] == 100.0 and summary[f'boundary_recall_{t}'] == 100.0, t
    assert summary['portals_dice'] == 1 and summary['portals_navkit'] == 1, 'the detailed area\'s portal is left out'
    assert abs(summary['portal_displacement_dice_to_navkit_median'] - 0.8) < 1e-6
    assert summary['dice_portals_within_1m'] == 100.0
    assert sorted(map(tuple, only['dice'].tolist())) == [(7, 0)] and sorted(map(tuple, only['navkit'].tolist())) == [(6, 0)]
    single = {tuple(ij) for ij, s in zip(cells['ij'].tolist(), cells['dice_single']) if s}
    assert single == {(3, 1)}
    assert np.allclose(cells['height'], 0.1 * cells['ij'][:, 0] * G)
    assert np.allclose(cells['slope'], np.degrees(np.arctan(0.1)), atol=1e-3)
    kinds = sorted(k for k in edges['kind'].tolist() if k)
    assert kinds == ['extra', 'match', 'missing', 'missing', 'missing']
    assert len(portals['dice']) == 1 and portals['navkit'][0, 2] == 1.6, 'portal length'


def test_diagnose_same_and_empty():
    dice, _navkit = made_up()
    summary, cells, _edges, _portals, _only = navboundary.diagnose(dice, dice)
    assert summary['missing'] == summary['extra'] == 0 and summary['exact_areas'] == 3
    assert summary['dice_areas_merged_by_navkit'] == 0 and cells['height'] is None and cells['slope'] is None
    empty = nav([], [])
    summary, *_ = navboundary.diagnose(empty, empty)
    assert summary['shared_cells'] == 0 and summary['edges'] == 0
    assert summary['boundary_precision_0'] is None and summary['boundary_recall_0'] is None
    assert summary['portal_displacement_dice_to_navkit_median'] is None and summary['dice_portals_within_1m'] is None


def test_nearest():
    a = np.array([[0.0, 0.0], [55.0, 0.0], [250.0, 250.0]])
    b = np.array([[3.0, 4.0], [60.0, 0.0]])
    d = navboundary._nearest(a, b)
    assert d[0] == 5.0 and d[1] == 5.0, d
    assert abs(d[2] - np.hypot(190.0, 250.0)) < 1e-6, 'many rings out'
    assert np.isinf(navboundary._nearest(a, np.zeros((0, 2)))).all()
    assert len(navboundary._nearest(np.zeros((0, 2)), b)) == 0


def test_index_and_near():
    keys = np.array([1, 5, 9])
    pos, found = navboundary._index(keys, np.array([5, 6, 100]))
    assert pos.tolist()[0] == 1 and found.tolist() == [True, False, False]
    pos, found = navboundary._index(np.zeros(0, np.int64), np.array([1]))
    assert found.tolist() == [False]
    pts = np.array([[0, 0], [10, 10]])
    assert navboundary._near(pts, np.array([[2, 1]]), 2).tolist() == [True, False]
    assert navboundary._near(pts, np.array([[2, 1]]), 1).tolist() == [False, False]


def test_worst_square_export_render(tmp_path):
    dice, navkit = made_up()
    summary, cells, edges, portals, only = navboundary.diagnose(dice, navkit, Slope())
    assert navboundary.worst_square(edges) == (0.0, 0.0, 100.0, 100.0)
    clean = dict(edges, kind=np.array(['match' if k else '' for k in edges['kind']], dtype=object))
    assert navboundary.worst_square(clean) is None
    navboundary.export(str(tmp_path), cells, edges, portals)
    rows = list(csv.reader(open(tmp_path / 'cells.csv', encoding='utf-8')))
    assert rows[0][:4] == ['x', 'z', 'dice_area', 'navkit_area'] and len(rows) == 9
    assert ['2.4', '0.8', '2', '1', '0.24', '5.7', '1', '1'] in rows, 'the single-cell DICE area at (3, 1)'
    edge_rows = list(csv.reader(open(tmp_path / 'edges.csv', encoding='utf-8')))
    assert len(edge_rows) == 6 and sorted(r[3] for r in edge_rows[1:]) == ['extra', 'match', 'missing', 'missing', 'missing']
    assert ['2.0', '0.0', 'z', 'extra', '1', '1', '0', '1', '0.08'] in edge_rows, edge_rows
    portal_rows = list(csv.reader(open(tmp_path / 'portals.csv', encoding='utf-8')))
    assert portal_rows == [['source', 'x', 'z', 'length'], ['dice', '1.2', '0.4', '1.6'], ['navkit', '2.0', '0.4', '1.6']]
    navboundary.render(str(tmp_path / 'map.png'), cells, edges, portals, only, (-1.0, -1.0, 8.0, 8.0), 'made up')
    assert (tmp_path / 'map.png').read_bytes()[:4] == b'\x89PNG'
    plain = dict(cells, slope=None, height=None)
    navboundary.export(str(tmp_path), plain, edges, portals)
    assert ['2.4', '0.8', '2', '1', '', '', '1', '1'] in list(csv.reader(open(tmp_path / 'cells.csv', encoding='utf-8')))
    navboundary.render(str(tmp_path / 'plain.png'), plain, edges, portals, only, (-1.0, -1.0, 600.0, 600.0), 'large')
    assert (tmp_path / 'plain.png').is_file()


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


def build(path, ground=None):
    s = flat(0.8, 0.0, 0.0, 30, 30, ground=ground)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, str(path), log=lambda *a: None)
    finally:
        navsample.load = load
    return str(path)


def run_main(argv, heightfield):
    old = levelmod.heightfield_of
    levelmod.heightfield_of = heightfield
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = navboundary.main(argv)
    finally:
        levelmod.heightfield_of = old
    return code, out.getvalue()


def no_terrain(*a, **k):
    raise FileNotFoundError('no terrain here')


def test_main(tmp_path):
    root = tmp_path / 'Project OPFOR'
    infantry = root / 'game' / 'Output' / 'win32' / 'ai' / 'levels' / LV / 'infantry'
    infantry.mkdir(parents=True)
    build(infantry / 'pathdata.hpd', ground=lambda x, z: not (10 < x < 13))
    cand = build(tmp_path / 'cand.hpd')
    out = tmp_path / 'out'
    code, text = run_main(['navboundary.py', LV, cand, '--out', str(out), '--opfor', str(root)], no_terrain)
    assert code == 0 and 'no terrain heights (no terrain here); maps without slope' in text, text
    summary = json.load(open(out / 'summary.json', encoding='utf-8'))
    assert summary['level'] == LV and summary['candidate'] == cand
    assert summary['navkit_only_cells'] > 0 and summary['dice_only_cells'] == 0
    assert sorted(os.listdir(out)) == ['cells.csv', 'edges.csv', 'overview.png', 'portals.csv', 'summary.json'], \
        'no mismatched edge: no worst-sector map'
    region = tmp_path / 'region'
    code, text = run_main(['navboundary.py', LV, cand, '--out', str(region), '--opfor', str(root),
                           '--region', '0,0,12,12'], lambda *a, **k: Slope())
    assert code == 0 and 'no terrain heights' not in text and (region / 'region.png').is_file()
    assert not (region / 'overview.png').exists()
    rows = list(csv.reader(open(region / 'cells.csv', encoding='utf-8')))
    assert rows[1][5] != '', 'slope from the height field'


def test_main_worst_square(tmp_path):
    root = tmp_path / 'Project OPFOR'
    infantry = root / 'game' / 'Output' / 'win32' / 'ai' / 'levels' / LV / 'infantry'
    infantry.mkdir(parents=True)
    dice, navkit = made_up()
    loads = {str(infantry / 'pathdata.hpd'): dice, str(tmp_path / 'cand.hpd'): navkit}
    for p in loads:
        open(p, 'wb').close()
    old = navdata.load
    navdata.load = lambda p: loads[p]
    try:
        code, text = run_main(['navboundary.py', LV, str(tmp_path / 'cand.hpd'), '--out', str(tmp_path / 'o'),
                               '--opfor', str(root)], no_terrain)
    finally:
        navdata.load = old
    assert code == 0, text
    summary = json.load(open(tmp_path / 'o' / 'summary.json', encoding='utf-8'))
    assert summary['worst_square'] == [0.0, 0.0, 100.0, 100.0] and (tmp_path / 'o' / 'worst_sector.png').is_file()


def test_main_errors(tmp_path):
    (tmp_path / 'game').mkdir()
    code, text = run_main(['navboundary.py'], no_terrain)
    assert code == 2 and 'navboundary.py <level>' in text
    try:
        run_main(['navboundary.py', LV, '--opfor', str(tmp_path)], no_terrain)
    except FileNotFoundError as e:
        assert 'no NavKit file' in str(e) and f'navregress.py --levels {LV}' in str(e), e
    else:
        raise AssertionError('no candidate: FileNotFoundError')


def test_cells_of_multi_layer():
    dice, _ = made_up()
    ij, area_ids, key, multi = navboundary.cells_of(dice)
    assert len(multi) == 1 and len(ij) == 9, 'the two-floor column is left out of the single-layer cells'
    assert list(key) == sorted(key) and set(area_ids.tolist()) == {0, 1, 2}
