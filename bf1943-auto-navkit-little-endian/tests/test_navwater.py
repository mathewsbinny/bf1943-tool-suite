r"""NavKit little-endian: water depth against path-data coverage on made-up data (navwater)

water_heights on made-up triangles (inside, outside, overlapping pieces: the highest; a degenerate triangle; no
water), floors (stacked floors apart), band_of, depths (the terrain's height only where it is near the estimate);
level_report and the command line on a made-up OPFOR folder: DICE's file and NavKit's are built here with navbuild
from made-up samples (a 48 m square with and without a 6 m gap), the water a made-up mesh (2.25 m over the gap, 3.5 m
over its middle, 0.75 m over a strip further on) put in place of the level's water reader, so the report's bands,
extra ground under water and DICE's depth limit have known values. No game data.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json
import math
from types import SimpleNamespace

import numpy as np

import navbuild
import navsample
import navwater
from bfbc2core import level as levelmod
from bfbc2core import water as watermod

LV = 'sp_01'


def quads(*rects):
    """Water of flat rectangles (x0, x1, z0, z1, height)."""
    verts, tris = [], []
    for x0, x1, z0, z1, y in rects:
        n = len(verts)
        verts += [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
        tris += [(n, n + 1, n + 2), (n, n + 2, n + 3)]
    return watermod.Water(np.array(verts, np.float32), np.array(tris, np.uint32))


def test_water_heights():
    w = quads((0.0, 10.0, 0.0, 10.0, 1.0), (5.0, 20.0, 0.0, 10.0, 2.0))
    pts = np.array([[1.0, 1.0], [6.0, 5.0], [15.0, 5.0], [30.0, 5.0], [-1.0, 5.0]])
    top = navwater.water_heights(pts, w)
    assert np.allclose(top[:3], [1.0, 2.0, 2.0]), 'the highest piece where two overlap'
    assert top[3] == navwater.NO_WATER and top[4] == navwater.NO_WATER
    assert (navwater.water_heights(pts, None) == navwater.NO_WATER).all()
    flat_tri = watermod.Water(np.array([(0, 1, 0), (1, 1, 0), (2, 1, 0)], np.float32), np.array([(0, 1, 2)], np.uint32))
    assert (navwater.water_heights(np.array([[1.0, 0.0]]), flat_tri) == navwater.NO_WATER).all(), 'no area: skipped'
    sloped = watermod.Water(np.array([(0, 0, 0), (10, 10, 0), (0, 0, 10)], np.float32), np.array([(0, 1, 2)], np.uint32))
    assert abs(navwater.water_heights(np.array([[5.0, 2.0]]), sloped)[0] - 5.0) < 1e-6, 'interpolated'


def test_floors_and_bands():
    cellmap = {(0, 0): [0.0, 0.04, 5.0], (1, 0): [2.0]}
    assert navwater.floors(cellmap) == [((0, 0), 0.0), ((0, 0), 5.0), ((1, 0), 2.0)], 'stacked floors apart'
    assert navwater.band_of(float('nan')) == 0
    assert navwater.band_of(-3.0) == 0 and navwater.band_of(-0.2) == 1 and navwater.band_of(0.2) == 2
    assert navwater.band_of(2.5) == 6 and navwater.band_of(50.0) == 7


def test_depths():
    entries = [((0, 0), 0.0), ((10, 0), 0.0), ((20, 0), 0.0)]
    w = quads((-1.0, 12.0, -1.0, 1.0, 1.0))           # cells at x 0, 8 and 16 m
    terrain = SimpleNamespace(height=lambda x, z: np.where(x < 5, 0.5, 4.0))
    d = navwater.depths(entries, terrain, w)
    assert abs(d[0] - 0.5) < 1e-6, 'terrain 0.5 m from the estimate: its height'
    assert abs(d[1] - 1.0) < 1e-6, 'terrain 4 m off (a bridge deck over it): the estimate'
    assert math.isnan(d[2]), 'no water above'
    assert np.allclose(navwater.depths(entries[:2], None, w), [1.0, 1.0])


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
    s = flat(0.8, 0.0, 0.0, 60, 60, ground=ground)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, str(path), log=lambda *a: None)
    finally:
        navsample.load = load
    return str(path)


WATER = quads((19.5, 26.5, -5.0, 60.0, 2.25), (21.0, 25.0, -5.0, 60.0, 3.5), (30.0, 34.0, -5.0, 60.0, 0.75))


def opfor(tmp_path):
    root = tmp_path / 'Project OPFOR'
    infantry = root / 'game' / 'Output' / 'win32' / 'ai' / 'levels' / LV / 'infantry'
    infantry.mkdir(parents=True)
    build(infantry / 'pathdata.hpd', ground=lambda x, z: not (20 < x < 26))
    (root / 'builds').mkdir()
    build(root / 'builds' / f'{LV}_W.hpd')
    return root


class Patched:
    """The level's height field and water readers replaced (water: {level name: Water})."""
    def __init__(self, water):
        self.water = water

    def __enter__(self):
        self.hf, self.read = levelmod.heightfield_of, watermod.read
        levelmod.heightfield_of = lambda *a, **k: None
        watermod.read = lambda folder, name: self.water.get(name)
        return self

    def __exit__(self, *exc):
        levelmod.heightfield_of, watermod.read = self.hf, self.read


def test_level_report(tmp_path):
    root = opfor(tmp_path)
    cand = str(root / 'builds' / f'{LV}_W.hpd')
    lines = []
    with Patched({LV: WATER}):
        r = navwater.level_report(str(root), LV, cand, log=lines.append)
    assert lines == []
    assert r['level'] == LV and r['navkit_cells'] == 3600 and r['dice_cells'] == 3180 and r['water_triangles'] == 6
    assert sum(b['navkit'] for b in r['bands']) == 3600 and sum(b['dice'] for b in r['bands']) == 3180
    assert r['extra_cells'] == 420 and r['extra_under_water'] == 420 and r['extra_under_water_pct'] == 100.0, \
        'the gap is all under water'
    assert r['dice_depth_limit'] == 2.25, 'DICE\'s deepest ground: the edge of the gap'
    assert r['navkit_deeper_than_dice'] == 5 * 60, 'the middle of the gap (x 21.6 .. 24.8) is deeper than DICE goes'
    deepest = r['bands'][-1]
    assert (deepest['navkit'], deepest['dice'], deepest['navkit_in_dice'], deepest['share']) == (300, 0, 0, 0.0)
    shallow = next(b for b in r['bands'] if b['band'] == 'under 0.5-1 m')
    assert shallow['navkit'] == shallow['dice'] == shallow['navkit_in_dice'] == 5 * 60 and shallow['share'] == 100.0
    assert r['bands'][1]['share'] is None, 'a band with no cells has no share'


def test_level_report_no_water(tmp_path):
    root = opfor(tmp_path)
    lines = []
    with Patched({}):
        assert navwater.level_report(str(root), LV, str(root / 'builds' / f'{LV}_W.hpd'), log=lines.append) is None
    assert lines == [f'{LV}: no water']


def test_main(tmp_path):
    root = opfor(tmp_path)
    out_json = tmp_path / 'water.json'
    pattern = str(root / 'builds' / '{lv}_W.hpd')
    text = io.StringIO()
    with Patched({LV: WATER}), contextlib.redirect_stdout(text):
        code = navwater.main(['navwater.py', LV, 'sp_02', '--candidate', pattern, '--json', str(out_json),
                              '--opfor', str(root)])
    out = text.getvalue()
    assert code == 0
    assert 'sp_02: no water' in out and 'all 1 levels:' in out, out
    assert "NavKit extra ground under water: 420 of 420 floors (100.0 %); deeper than DICE goes on the level: 300" in out
    assert "DICE's depth limit per level (99.9 % of its ground under water is within): 2.25 - 2.25 m" in out
    reports = json.loads(out_json.read_text(encoding='utf-8'))
    assert [r['level'] for r in reports] == [LV]


def test_main_default_levels(tmp_path):
    (tmp_path / 'game').mkdir()
    text = io.StringIO()
    with Patched({}), contextlib.redirect_stdout(text):
        code = navwater.main(['navwater.py', '--opfor', str(tmp_path)])
    out = text.getvalue()
    assert code == 0 and 'sp_006' not in out and 'sp_009:' not in out, 'the levels without water are not tried'
    assert 'sp_01: no water' in out and 'all 0 levels:' in out
