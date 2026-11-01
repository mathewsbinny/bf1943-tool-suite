r"""NavKit little-endian: the regression run's parts on a made-up OPFOR folder (navregress)

No game data: a made-up Project OPFOR folder in a temp folder. Checked: sector_offsets (whole numbers, a missing or
fractional field is an error naming the file); inputs (what is missing is named: samples, the high-capsule samples,
the offsets, the prepared files; navkit.json wins over SectorOffsets.xml); level_water, reference_bounds (on a .hpd
built here with navbuild from made-up samples), order_points_of, ladders_of, detailed_of (no 0.2 m samples; stale
ones refused), build_level's inputs to navbuild.build; duration, estimates (earlier runs, then sample size at the
median rate, else the default), compare (worse, better, counts, tolerance), save_run (whole or not at all),
merged_rows; main with the build and the parity measure replaced: a level that holds, one that got worse, one with
structure problems, one new, one that fails to build, the saved runs and the exit codes.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json
import os

import numpy as np

import detailed
import hpdcheck
import navbuild
import navparity
import navregress
import navsample
from bfbc2core import level as levelmod
from bfbc2core import water as watermod

LV = 'sp_01'


def offsets_xml(path, x='-12', z='34.0'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<instance><field name="XOffset">{x}</field>\n<field name="ZOffset" >\n {z} </field></instance>',
                    encoding='utf-8')
    return str(path)


def test_sector_offsets(tmp_path):
    assert navregress.sector_offsets(offsets_xml(tmp_path / 'a.xml')) == (-12, 34)
    for x, what in (('1.5', 'XOffset'), ('', 'XOffset')):
        path = offsets_xml(tmp_path / 'b.xml', x=x)
        try:
            navregress.sector_offsets(path)
        except ValueError as e:
            assert path in str(e) and f'no whole-number {what} field' in str(e), e
        else:
            raise AssertionError(f'XOffset {x!r}: ValueError')


def prepared(root, lv=LV, settings=True, files=True, samples='navkit', high=True):
    work = root / 'work'
    if samples:
        (work / 'navsample').mkdir(parents=True, exist_ok=True)
        (work / 'navsample' / f'{lv}_{samples}.bin').write_bytes(b'x' * 2_000_000)
        if high:
            (work / 'navsample' / f'{lv}_{samples}_hi.bin').write_bytes(b'')
    if settings:
        (work / 'navkit' / lv).mkdir(parents=True, exist_ok=True)
        (work / 'navkit' / lv / 'navkit.json').write_text(json.dumps({'xoff': 3, 'zoff': -4}), encoding='utf-8')
    level = work / 'levels' / lv
    level.mkdir(parents=True, exist_ok=True)
    if files:
        for name in ('forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy'):
            np.save(level / name, np.zeros((0, 3, 3)))
        (level / 'soldier_spawns.json').write_text('[[1.0, 2.0, 3.0]]', encoding='utf-8')
    return str(root)


def refused(fn, *args):
    try:
        fn(*args)
    except RuntimeError as e:
        return str(e)
    raise AssertionError('RuntimeError expected')


def test_inputs(tmp_path):
    root = str(tmp_path)
    assert 'no in-game samples' in refused(navregress.inputs, root, LV)
    prepared(tmp_path, settings=False, files=False, high=False)
    assert 'no in-game samples' in refused(navregress.inputs, root, LV), 'samples without _hi do not count'
    prepared(tmp_path, settings=False, files=False, samples='full')
    assert 'no navkit.json and no SectorOffsets.xml' in refused(navregress.inputs, root, LV)
    so = tmp_path / 'work' / 'levels' / LV / 'dist' / 'levels' / LV / 'Precomputed' / 'Infantry' / 'SectorOffsets.xml'
    offsets_xml(so, x='2.5')
    message = refused(navregress.inputs, root, LV)
    assert message.startswith(f'{LV}: ') and 'no whole-number XOffset' in message
    offsets_xml(so, x='7', z='8')
    message = refused(navregress.inputs, root, LV)
    assert 'forbid_tris.npy, dynamic_tris.npy, stair_tris.npy, soldier_spawns.json missing' in message, message
    prepared(tmp_path, settings=False, samples=None)
    samples, xoff, zoff, files = navregress.inputs(root, LV)
    assert samples.endswith(f'{LV}_full.bin') and (xoff, zoff) == (7, 8), 'offsets from SectorOffsets.xml'
    prepared(tmp_path)
    samples, xoff, zoff, files = navregress.inputs(root, LV)
    assert samples.endswith(f'{LV}_navkit.bin') and (xoff, zoff) == (3, -4), 'navkit.json first, _navkit before _full'
    assert set(files) == {'forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy', 'soldier_spawns.json'}


def test_level_water(tmp_path):
    old = levelmod.water_of
    try:
        levelmod.water_of = lambda root, lv, log=print: None
        assert navregress.level_water(str(tmp_path), LV) is None
        levelmod.water_of = lambda root, lv, log=print: watermod.Water(np.zeros((0, 3), np.float32),
                                                                          np.zeros((0, 3), np.uint32))
        assert navregress.level_water(str(tmp_path), LV) is None, 'no triangles: no water'
        w = watermod.Water(np.zeros((3, 3), np.float32), np.array([[0, 1, 2]], np.uint32))
        levelmod.water_of = lambda root, lv, log=print: w
        vertices, triangles = navregress.level_water(str(tmp_path), LV)
        assert vertices is w.vertices and triangles is w.triangles
    finally:
        levelmod.water_of = old


def flat(pitch, min_x, min_z, nx, nz, y=0.0):
    start = np.arange(nx * nz + 1, dtype=np.int64)
    n = nx * nz
    return navsample.Samples(pitch, min_x, min_z, nx, nz, 50.0, -50.0, 0.4, 0.6, 1.8, 0.0, start,
                             np.full(n, y, np.float32), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                             np.zeros(n, np.uint8), None)


def test_reference_bounds(tmp_path):
    root = str(tmp_path)
    assert navregress.reference_bounds(root, LV) is None, 'a new map: no DICE path data'
    infantry = tmp_path / 'game' / 'Output' / 'win32' / 'ai' / 'levels' / LV / 'infantry'
    infantry.mkdir(parents=True)
    s = flat(0.8, 8.0, 20.0, 30, 20)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, str(infantry / 'pathdata.hpd'), log=lambda *a: None,
                       seeds=[[20.0, 0.0, 24.0]])
    finally:
        navsample.load = load
    x0, z0, x1, z1 = navregress.reference_bounds(root, LV)
    # samples at 8.0 .. 31.2 by 20.0 .. 35.2: outlines halfway between samples, 0.4 m outside them
    assert abs(x0 - 7.6) < 1e-4 and abs(z0 - 19.6) < 1e-4 and abs(x1 - 31.6) < 1e-4 and abs(z1 - 35.6) < 1e-4, \
        (x0, z0, x1, z1)


def test_json_side_files(tmp_path):
    root = str(tmp_path)
    assert navregress.order_points_of(root, LV) is None and navregress.ladders_of(root, LV) is None
    level = tmp_path / 'work' / 'levels' / LV
    level.mkdir(parents=True)
    (level / 'order_points.json').write_text('[[1, 2, 3]]', encoding='utf-8')
    (level / 'ladders.json').write_text('[{"bp": "x", "lo": [0, 0, 0], "hi": [1, 3, 1]}]', encoding='utf-8')
    assert navregress.order_points_of(root, LV) == [[1, 2, 3]]
    assert navregress.ladders_of(root, LV)[0]['hi'] == [1, 3, 1]


def test_detailed_of(tmp_path):
    root = str(tmp_path)
    assert navregress.detailed_of(root, LV, 's.bin', 0, 0) is None, 'no 0.2 m samples'
    sample_dir = tmp_path / 'work' / 'navsample'
    sample_dir.mkdir(parents=True)
    (sample_dir / f'{LV}_navkit_p02.bin').write_bytes(b'')
    old = detailed.choose, detailed.sampled_problem
    calls = []
    try:
        detailed.choose = lambda level_dir, common, samples, xoff, zoff: calls.append((level_dir, common)) or ([[1, 2]], 'why')
        detailed.sampled_problem = lambda p02, samples, chosen, xoff, zoff: ''
        d = navregress.detailed_of(root, LV, 's.bin', 0, 0)
        assert d == {'samples': str(sample_dir / f'{LV}_navkit_p02.bin'), 'high': None, 'sectors': {(1, 2)}}
        assert calls[0][0] == os.path.join(root, 'work', 'levels', LV)
        assert [os.path.basename(c) for c in calls[0][1]] == ['mp_common', 'sp_common']
        (sample_dir / f'{LV}_navkit_hi_p02.bin').write_bytes(b'')
        d = navregress.detailed_of(root, LV, 's.bin', 0, 0, chosen=[(5, 6)])
        assert d['high'] == str(sample_dir / f'{LV}_navkit_hi_p02.bin') and d['sectors'] == {(5, 6)}
        assert len(calls) == 1, 'a choice given is not made again'
        detailed.sampled_problem = lambda *a: 'sector (5, 6) not sampled'
        message = refused(navregress.detailed_of, root, LV, 's.bin', 0, 0, [(5, 6)])
        assert 'the 0.2 m samples are stale: sector (5, 6) not sampled' in message and '--pitch 0.2' in message
    finally:
        detailed.choose, detailed.sampled_problem = old


def test_build_level(tmp_path):
    root = prepared(tmp_path)
    level = tmp_path / 'work' / 'levels' / LV
    (level / 'order_points.json').write_text('[[5, 0, 5]]', encoding='utf-8')
    seen = {}
    old = navbuild.build, levelmod.water_of
    try:
        navbuild.build = lambda *a, **k: seen.update(args=a, kwargs=k)
        levelmod.water_of = lambda *a, **k: None
        navregress.build_level(root, LV, 'out.hpd')
    finally:
        navbuild.build, levelmod.water_of = old
    samples = os.path.join(root, 'work', 'navsample', f'{LV}_navkit.bin')
    assert seen['args'] == (samples, str(level / 'forbid_tris.npy'), 3, -4, 'out.hpd')
    k = seen['kwargs']
    assert k['high_path'] == samples[:-4] + '_hi.bin' and k['seeds'] == [[1.0, 2.0, 3.0]]
    assert k['order_points'] == [[5, 0, 5]] and k['water'] is None and k['bounds'] is None
    assert k['detailed'] is None and k['ladders'] is None
    assert k['dynamic_path'] == str(level / 'dynamic_tris.npy') and k['stairs_path'] == str(level / 'stair_tris.npy')


def test_duration():
    assert navregress.duration(45.4) == '45 s' and navregress.duration(89) == '89 s'
    assert navregress.duration(90) == '2 min' and navregress.duration(600) == '10 min'
    assert navregress.duration(3700) == '1 h 01 min' and navregress.duration(7260) == '2 h 01 min'


def test_estimates(tmp_path):
    root = str(tmp_path)
    prepared(tmp_path, 'sp_02')                                  # 2 MB of samples
    prepared(tmp_path, 'sp_03')
    earlier = {'sp_02': {'seconds': 100}, 'sp_04': {'seconds': 0}}
    later = {'sp_02': {'seconds': 500}, 'sp_05': {'seconds': 70}}
    est = navregress.estimates(root, ['sp_02', 'sp_03', 'sp_05', 'sp_06'], earlier, later)
    assert est['sp_02'] == 100, 'the first run that times a level'
    assert est['sp_03'] == 100, 'sample size at the median seconds per MB (sp_02: 100 s for 2 MB)'
    assert est['sp_05'] == 70 and est['sp_06'] == navregress.DEFAULT_SECONDS
    assert navregress.estimates(root, ['sp_03']) == {'sp_03': navregress.DEFAULT_SECONDS}, 'no timed level: default'


def test_compare():
    ref = {'ground': 90.0, 'connectivity': 80.0, 'extra_ground': 5.0, 'over_connection': 1.0, 'nodes_same': 50.0,
           'sectors_candidate': 4, 'islands_candidate': 2, 'ai_coverage': None}
    now = dict(ref, ground=89.9, connectivity=80.04, extra_ground=4.0, over_connection=1.2, nodes_same=None,
               islands_candidate=3, ai_coverage=99.0)
    worse, better, counts = navregress.compare(ref, now, 0.05)
    assert worse == [('ground', 90.0, 89.9), ('over_connection', 1.0, 1.2)], worse
    assert better == [('extra_ground', 5.0, 4.0)], 'lower is better for extra ground'
    assert counts == [('islands_candidate', 2, 3)]
    assert navregress.compare(ref, now, 0.5) == ([], [('extra_ground', 5.0, 4.0)], [('islands_candidate', 2, 3)])


def test_save_run_and_merged_rows(tmp_path):
    target = str(tmp_path / 'run.json')
    navregress.save_run(target, {'levels': [1]})
    assert json.load(open(target, encoding='utf-8')) == {'levels': [1]}
    try:
        navregress.save_run(target, {'levels': [object()]})
    except TypeError:
        pass
    else:
        raise AssertionError('an object JSON cannot hold: TypeError')
    assert json.load(open(target, encoding='utf-8')) == {'levels': [1]}, 'a failed write leaves the previous file'
    assert os.listdir(tmp_path) == ['run.json'], 'and no .tmp'
    rows = [{'level': 'a', 'v': 2}]
    existing = [{'level': 'a', 'v': 1}, {'level': 'b', 'v': 1}]
    assert navregress.merged_rows(rows, existing) == [{'level': 'a', 'v': 2}, {'level': 'b', 'v': 1}]


ROW = {'ground': 90.0, 'extra_ground': 2.0, 'nodes_same': 80.0, 'connectivity': 95.0, 'over_connection': 1.0,
       'ai_coverage': 99.0, 'areas_candidate': 10}


class Replaced:
    """navregress's build, hpdcheck and navparity's measure replaced: per level a row, a problem list or an error."""
    def __init__(self, rows, problems=None, fail=()):
        self.rows, self.problems, self.fail, self.built = rows, problems or {}, set(fail), []

    def build(self, root, lv, out, log=lambda *a: None):
        if lv in self.fail:
            raise RuntimeError(f'{lv}: no in-game samples')
        log('stage done')
        self.built.append(lv)
        with open(out, 'wb') as fh:
            fh.write(lv.encode())

    def check(self, out):
        lv = os.path.basename(out)[:-4]
        return {'closed boundaries': self.problems.get(lv, []), 'node order': []}

    def run_level(self, root, lv, out, log):
        return dict(self.rows[lv], level=lv, candidate=out)

    def __enter__(self):
        self.old = navregress.build_level, hpdcheck.check, navparity.run_level
        navregress.build_level, hpdcheck.check, navparity.run_level = self.build, self.check, self.run_level
        return self

    def __exit__(self, *exc):
        navregress.build_level, hpdcheck.check, navparity.run_level = self.old


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = navregress.main(argv)
    return code, out.getvalue()


def test_main_option_errors(tmp_path):
    (tmp_path / 'game').mkdir()
    code, out = run_main(['navregress.py', '--levels'])
    assert code == 2 and 'error: --levels needs a value' in out
    code, out = run_main(['navregress.py', '--tolerance', '--save', 'x'])
    assert code == 2 and 'error: --tolerance needs a value' in out
    code, out = run_main(['navregress.py', '--opfor', str(tmp_path), '--levels', LV])
    assert code == 2 and 'no reference run "regression"' in out and 'navregress.py --save regression' in out


def test_main(tmp_path):
    root = tmp_path / 'Project OPFOR'
    (root / 'game').mkdir(parents=True)
    reference = {'name': 'regression', 'date': 'd', 'levels': [
        dict(ROW, level='sp_01', seconds=30), dict(ROW, level='sp_02', seconds=30), dict(ROW, level='sp_03'),
        dict(ROW, level='sp_09')]}
    with open(navparity.save_path(str(root), 'regression'), 'w', encoding='utf-8') as fh:
        json.dump(reference, fh)
    rows = {'sp_01': dict(ROW, ground=90.01, areas_candidate=11),       # holds (a count changed)
            'sp_02': dict(ROW, connectivity=90.0, extra_ground=1.0),     # worse connectivity, better extra ground
            'sp_03': dict(ROW),                                          # structure problems
            'sp_new': dict(ROW)}                                         # not in the reference
    with Replaced(rows, problems={'sp_03': ['area 2: open outline']}, fail={'sp_04'}) as r:
        code, out = run_main(['navregress.py', '--opfor', str(root), '--levels', 'sp_01,sp_02,sp_03,sp_new,sp_04'])
    assert code == 1, out
    assert r.built == ['sp_01', 'sp_02', 'sp_03', 'sp_new']
    assert 'NavKit regression: 5 level(s), reference "regression", tolerance 0.05 points' in out
    assert '[1/5] sp_01: building (about 30 s)' in out and '      stage done' in out
    assert 'ok   sp_01' in out and '       count:  areas_candidate 10 -> 11' in out
    assert 'FAIL sp_02' in out and '       worse:  connectivity 95.0 -> 90.0' in out
    assert '       better: extra_ground 2.0 -> 1.0' in out
    assert '      structure: closed boundaries: 1 - area 2: open outline' in out
    assert 'FAIL sp_03' in out and 'worse:  structure (hpdcheck) 0 -> 1' in out
    assert 'new  sp_new: not in the reference run' in out
    assert 'FAIL sp_04: sp_04: no in-game samples' in out
    assert '3 level(s) worse or failed' in out
    last = json.load(open(navparity.save_path(str(root), 'regression_last'), encoding='utf-8'))
    assert [row['level'] for row in last['levels']] == ['sp_01', 'sp_02', 'sp_03', 'sp_new']
    assert last['levels'][2]['structure_problems'] == 1 and 'seconds' in last['levels'][0]

    # a subset saved as the reference keeps the reference's other levels; then a clean run passes
    with Replaced(rows) as r:
        code, out = run_main(['navregress.py', '--opfor', str(root), '--levels', 'sp_01', '--save', 'regression'])
    assert code == 0 and 'saved as reference run "regression"' in out and 'all passed' in out, out
    saved = json.load(open(navparity.save_path(str(root), 'regression'), encoding='utf-8'))
    assert [row['level'] for row in saved['levels']] == ['sp_01', 'sp_02', 'sp_03', 'sp_09']
    assert saved['levels'][0]['ground'] == 90.01 and saved['name'] == 'regression'


def test_main_default_levels(tmp_path):
    root = tmp_path / 'Project OPFOR'
    (root / 'game').mkdir(parents=True)
    for lv in ('sp_02', 'ons_mp_004'):
        infantry = root / 'game' / 'Output' / 'win32' / 'ai' / 'levels' / lv / 'infantry'
        infantry.mkdir(parents=True)
        (infantry / 'pathdata.hpd').write_bytes(b'')
    with Replaced({'sp_02': ROW, 'ons_mp_004': ROW}) as r:
        code, out = run_main(['navregress.py', '--opfor', str(root), '--save', 'first'])
    assert code == 0 and r.built == ['sp_02', 'ons_mp_004'], 'the levels with DICE path data, campaign first'
    assert 'new  sp_02' in out and os.path.isfile(navparity.save_path(str(root), 'first'))
