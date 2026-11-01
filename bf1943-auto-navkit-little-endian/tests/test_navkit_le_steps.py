r"""NavKit little-endian: navkit_le.py's steps on a made-up Project OPFOR folder (prepare, sample, build, check, apply)

No game, no game data: a made-up OPFOR folder in a temp folder; the game is never started (the check for it, the
launch, the waits and the game log are replaced), FbrbTool / DbxEdit are replaced by a fake unpack, and path data is
built here with navbuild from made-up samples (a 48 m square with a 6 m gap; and without it). Checked (test_navkit_
driver.py covers install / restore, the unpack markers, the game log, the detailed-sector cache):
  - the level list and status; a tool that fails names itself and its last output;
  - prepare: settings from the placed objects (box, heights, sector offsets by DICE's rule), from SectorOffsets.xml,
    from DICE's path data, a box given; no objects is an error; a level sampled before its settings gets them;
  - the detailed-zone helpers (rects, box, sectors in a box, DICE's detailed sectors, pitch suffixes);
  - sample: refused while the game runs, declined, both runs written, an unfinished run, a 0.2 m run and its record;
    sample_level; sample_run's opfor.ini changes, put back afterwards, and each way a run ends;
  - build (inputs, missing samples, a build error made a clear one), check (ordercheck run on the files), compare;
  - apply on DICE's file: links and traversal annotations, the report, refusals; read_back and game_path_data;
  - the command line's exits.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import time
from types import SimpleNamespace

import numpy as np

import detailed
import hpdtool
import navbuild
import navkit_le as navkit
import navregress
import navsample
from bfbc2core import navtune

LV = 'sp_01'
QUIET = lambda *a: None  # noqa: E731


def touch(path, data=b''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def project(root):
    root = str(root)
    for p in ('game/BFBC2Game.exe', 'tools/FbrbTool.exe', 'tools/DbxEdit.exe', 'game/Dist/win32/levels/SP_01/level-00.fbrb',
              'game/Dist/win32/levels/mp_x/level-00.fbrb', 'game/Dist/win32/levels/sp_common/level-00.fbrb',
              'game/Dist/win32/levels/opfor_fill/level-00.fbrb', 'game/Dist/win32/levels/no_bundle/readme.txt'):
        touch(os.path.join(root, p))
    return navkit.Project(root)


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


def build_hpd(path, gap=True):
    s = flat(0.8, 0.0, 0.0, 60, 60, ground=(lambda x, z: not (20 < x < 26)) if gap else None)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
        navbuild.build('s.bin', None, 0, 0, str(path), log=QUIET)
    finally:
        navsample.load = load
    return str(path)


class Replace:
    """Module attributes replaced for the with block: Replace((module, 'name', value), ...)."""
    def __init__(self, *items):
        self.items, self.saved = items, []

    def __enter__(self):
        for module, name, value in self.items:
            self.saved.append((module, name, getattr(module, name)))
            setattr(module, name, value)
        return self

    def __exit__(self, *exc):
        for module, name, value in reversed(self.saved):
            setattr(module, name, value)


def no_game():
    return Replace((navkit, 'game_running', lambda: False), (navkit, 'close_game', lambda: None))


def transform(x, y, z):
    return '1/0/0/0/0/1/0/0/0/0/1/0/' + f'{x}/{y}/{z}' + '/1'


LEVEL_XML = ('<instance guid="p" type="Entity.ReferenceObjectData"><complex name="Transform">' + transform(100, 5, 200)
             + '</complex><field name="ReferencedObject" ref="Objects/Props/Crate/c1" /></instance>\n'
             '<instance guid="s" type="Game.SoldierSpawnEntityData"><complex name="Transform">' + transform(150, 10, 260)
             + '</complex></instance>\n'
             '<instance guid="o" type="Entity.AreaOrderEntityData"><field name="Name">left</field>'
             '<complex name="Transform">' + transform(10.0, 0.0, 10.0) + '</complex></instance>\n'
             '<instance guid="q" type="Entity.AreaOrderEntityData"><field name="Name">right</field>'
             '<complex name="Transform">' + transform(30.0, 0.0, 30.0) + '</complex></instance>\n')


def fake_unpack(calls, offsets=None):
    def unpack(bundle, dest, log):
        calls.append((os.path.relpath(bundle, pr_root[0]), os.path.relpath(dest, pr_root[0])))
        if os.path.basename(os.path.dirname(bundle)) == 'SP_01':
            touch(os.path.join(dest, 'levels', 'SP_01.xml'), LEVEL_XML.encode())
            if offsets:
                touch(os.path.join(dest, 'levels', LV, 'Precomputed', 'Infantry', 'SectorOffsets.xml'),
                      f'<field name="XOffset">{offsets[0]}</field><field name="ZOffset">{offsets[1]}</field>'.encode())
    return unpack


pr_root = [None]


def prepared(pr, offsets=None, box=None, y=None):
    calls = []
    pr_root[0] = pr.root
    pr.unpack = fake_unpack(calls, offsets)
    lines = []
    with contextlib.redirect_stdout(io.StringIO()):
        navkit.prepare(pr, LV, log=lines.append, box=box, y=y)
    return calls, lines, navkit.read_json(pr.settings_path(LV))


def test_levels_status_and_run(tmp_path):
    pr = project(tmp_path)
    assert sorted(pr.level_dirs()) == ['mp_x', 'sp_01'], 'not the common bundles, OPFOR\'s fill or a folder without one'
    lines = []
    navkit.levels(pr, log=lines.append)
    assert lines[0].startswith('level') and lines[1].split() == ['sp_01', 'no'] and lines[2].split() == ['mp_x', 'no']
    build_hpd(pr.pathdata(LV))
    navkit.write_json(pr.settings_path(LV), {'level': 'SP_01', 'box': [0, 0, 10, 10], 'y': [0, 5], 'xoff': 0, 'zoff': 0})
    touch(os.path.join(pr.out_dir(LV), 'pathdata.hpd'))
    lines = []
    navkit.levels(pr, log=lines.append)
    assert lines[1].split()[1:] == ['yes', '(samplable)', 'prepared,', 'built'], lines[1]
    lines = []
    navkit.status(pr, LV, log=lines.append)
    assert lines[0] == f'{LV}:' and 'box 0,0,10,10, y 0,5, offsets 0,0' in lines[3] and lines[-1].endswith('no')
    try:
        pr.level('SP_99')
    except navkit.NavKitError as e:
        assert "unknown level 'SP_99'" in str(e)
    else:
        raise AssertionError('an unknown level is refused')
    assert pr.level('Levels/SP_01') == LV
    out = pr.run([sys.executable, '-c', 'print("one"); print("two")'], QUIET)
    assert out == ['one', 'two']
    try:
        pr.run([sys.executable, '-c', 'import sys; print("a"); print("b"); print("c"); print("d"); sys.exit(3)'], QUIET)
    except navkit.NavKitError as e:
        assert str(e) == f'{os.path.basename(sys.executable)} failed: b | c | d', e
    else:
        raise AssertionError('a failing tool is an error')


def test_prepare_from_objects(tmp_path):
    pr = project(tmp_path)
    calls, lines, s = prepared(pr)
    assert [c[1] for c in calls] == [os.path.join('work', 'navkit', 'common', 'sp_common'),
                                     os.path.join('work', 'navkit', 'common', 'mp_common'),
                                     os.path.join('work', 'levels', LV, 'dist')], 'no package bundle: no pkg unpack'
    assert os.path.isdir(os.path.join(pr.level_work(LV), 'pkg'))
    assert s == {'level': 'SP_01', 'box': [50, 150, 200, 310], 'y': [-30, 160],
                 'box_source': "extent of the level's objects + 50 m", 'offsets_source': 'rule: box',
                 'xoff': -1, 'zoff': 0}, s
    assert any('ladders: 0' in line for line in lines) and any('0.0 M columns' in line for line in lines)
    for name in ('objects.json', 'soldier_spawns.json', 'forbid_tris.npy', 'ladders.json'):
        assert os.path.isfile(os.path.join(pr.level_work(LV), name)), name


def test_prepare_offsets_and_box(tmp_path):
    pr = project(tmp_path)
    touch(os.path.join(pr.game, 'Package', 'levels', 'SP_01', 'level-00.fbrb'))
    calls, _lines, s = prepared(pr, offsets=(-3, '-2.0'), box=[0, 0, 400, 400], y=[-10, 90])
    assert calls[-1][1] == os.path.join('work', 'levels', LV, 'pkg'), 'the package bundle is unpacked too'
    assert (s['xoff'], s['zoff'], s['offsets_source']) == (-3, -2, 'SectorOffsets')
    assert s['box'] == [0, 0, 400, 400] and s['y'] == [-10, 90] and s['box_source'] == 'given'
    shutil.rmtree(pr.level_work(LV))
    try:
        prepared(pr, offsets=('1.5', 0))
    except navkit.NavKitError as e:
        assert 'no whole-number XOffset' in str(e) and 'SectorOffsets.xml' in str(e), e
    else:
        raise AssertionError('fractional offsets are refused')


def test_prepare_from_path_data(tmp_path):
    pr = project(tmp_path)
    build_hpd(pr.pathdata(LV))
    _calls, _lines, s = prepared(pr)
    # outlines -0.4 .. 47.6 at 0.95 m: + 20 m, to 10 m; offsets one sector below the extent
    assert s['box'] == [-30, -30, 70, 70] and s['y'] == [-30, 160], s
    assert s['box_source'] == "extent of the level's path data + 20 m" and s['offsets_source'] == 'rule: path data extent'
    assert (s['xoff'], s['zoff']) == (-2, -2)


def test_sampling_box_and_partition_errors(tmp_path):
    pr = project(tmp_path)
    L = pr.level_work(LV)
    for name in ('objects.json', 'group_instances.json', 'soldier_spawns.json'):
        touch(os.path.join(L, name), b'[]')
    try:
        navkit.sampling_box(pr, LV, L)
    except navkit.NavKitError as e:
        assert 'no objects to size the sampling box - pass --box and --y' in str(e)
    else:
        raise AssertionError('no objects: an error')
    try:
        navkit.level_partition_name(L, LV)
    except navkit.NavKitError as e:
        assert 'level partition levels/sp_01.xml not found' in str(e)
    else:
        raise AssertionError('no partition: an error')
    assert navkit.fmt([1.0, 2.5, -3]) == '1,2.5,-3'


def test_ensure_settings_from_samples(tmp_path):
    pr = project(tmp_path)
    try:
        navkit.ensure_settings(pr, LV)
    except navkit.NavKitError as e:
        assert 'not prepared yet (navkit_le.py prepare SP_01)' in str(e)
    else:
        raise AssertionError('nothing there: not prepared')
    touch(pr.samples(LV))
    L = pr.level_work(LV)
    touch(os.path.join(L, 'dist', 'levels', 'Sp_01.xml'))
    touch(os.path.join(L, 'dist', 'levels', 'Sp_01', 'Precomputed', 'Infantry', 'SectorOffsets.xml'),
          b'<field name="XOffset">4</field><field name="ZOffset">-5</field>')
    s = flat(0.8, 8.0, 16.0, 10, 5)
    with Replace((navsample, 'load', lambda p: (s, 0))):
        st = navkit.ensure_settings(pr, LV)
    assert st == {'level': 'Sp_01', 'box': [8.0, 16.0, 15.2, 19.2], 'y': [-50.0, 50.0], 'box_source': 'its 0.8 m samples',
                  'offsets_source': 'its SectorOffsets', 'xoff': 4, 'zoff': -5}, st
    assert navkit.ensure_settings(pr, LV) == st, 'written: read back the next time'


def settings(pr, xoff=-1, zoff=2):
    navkit.write_json(pr.settings_path(LV), {'level': 'SP_01', 'box': [0, 0, 400, 400], 'y': [-10, 90],
                                             'xoff': xoff, 'zoff': zoff})


def choose(sectors, pr=None):
    """detailed.choose replaced (its sectors are tuples, as the real one's); with pr, the cache of an earlier choice
    removed (its key does not change with the replacement)."""
    sectors = [tuple(s) for s in sectors]
    if pr is not None:
        cache = os.path.join(pr.out_dir(LV), 'detailed.json')
        if os.path.isfile(cache):
            os.remove(cache)
    return Replace((detailed, 'choose', lambda level_dir, common, samples, xoff, zoff: (sectors, {'flag': len(sectors)})))


def test_detailed_helpers(tmp_path):
    pr = project(tmp_path)
    settings(pr)
    assert navkit.pitch_suffix(0.8) == '' and navkit.pitch_suffix(0.2) == '_p02'
    with choose([[1, 1], [2, 1]], pr):
        assert navkit.detailed_sectors(pr, LV) == [(1, 1), (2, 1)]
        assert navkit.detailed_rects(pr, LV) == [[-1, 299, 101, 401], [99, 299, 201, 401]]
        assert navkit.detailed_box(pr, LV) == [-1, 299, 201, 401]
        assert navkit.sectors_in_box(pr, LV, [0, 300, 100, 400]) == [(1, 1)]
        assert navkit.sectors_in_box(pr, LV, [-5, 295, 205, 405]) == [(1, 1), (2, 1)]
    settings(pr, xoff=0)                         # other offsets: chosen again
    with choose([], pr):
        try:
            navkit.detailed_box(pr, LV)
        except navkit.NavKitError as e:
            assert 'no detailed sectors' in str(e)
        else:
            raise AssertionError('no detailed sectors: nothing to sample at 0.2')

    def broken(*a):
        raise ValueError('objects.json has five flags')
    settings(pr, xoff=7)
    with Replace((detailed, 'choose', broken)):
        try:
            navkit.detailed_sectors(pr, LV)
        except navkit.NavKitError as e:
            assert str(e) == f'{LV}: objects.json has five flags'
        else:
            raise AssertionError('a ValueError of the choice: a NavKitError')
    assert navkit.dice_detailed_sectors(pr, LV) == []
    build_hpd(pr.pathdata(LV))
    assert navkit.dice_detailed_sectors(pr, LV) == [], 'a file with no 0.2 m sector'


class Runs:
    """sample_run replaced: writes the run's samples (unless told not to) and returns its result."""
    def __init__(self, pr, result='done', write=True):
        self.pr, self.result, self.write, self.calls = pr, result, write, []

    def __call__(self, pr, s, agent, name, log, timeout, pitch=0.8, budget_ms=None):
        self.calls.append((agent, name, pitch, budget_ms, s.get('rects'), list(s['box'])))
        if self.write:
            touch(os.path.join(pr.work, 'navsample', name + '.bin'))
        return self.result


def test_sample(tmp_path):
    pr = project(tmp_path)
    settings(pr)
    with Replace((navkit, 'game_running', lambda: True)):
        try:
            navkit.sample(pr, LV, log=QUIET)
        except navkit.NavKitError as e:
            assert 'the game is running' in str(e)
        else:
            raise AssertionError('the game running: refused')
    runs = Runs(pr)
    with no_game(), Replace((navkit, 'sample_run', runs)):
        lines = []
        assert navkit.sample(pr, LV, log=lines.append, confirm=lambda q: False) is False
        assert lines[-1] == '  cancelled' and 'no AI path data' in lines[1] and runs.calls == []
        touch(pr.ini, b'[dev]\n')
        touch(navkit.ini_backup(pr), b'[dev]\nnavsample=1\n')        # left by a killed run
        lines = []
        assert navkit.sample(pr, LV, log=lines.append, box=[0, 0, 200, 200], confirm=lambda q: True) is True
        assert open(pr.ini, 'rb').read() == b'[dev]\nnavsample=1\n' and not os.path.exists(navkit.ini_backup(pr)), \
            'the ini an interrupted run changed is put back first'
        assert [c[:2] for c in runs.calls] == [('0.4,0.28,1.9', 'sp_01_navkit'), ('0.4,0.6,1.9', 'sp_01_navkit_hi')]
        assert navkit.read_json(pr.settings_path(LV))['box'] == [0, 0, 200, 200], 'a box given is kept'
        assert lines[-1].startswith('  samples: ')
    with no_game(), Replace((navkit, 'sample_run', Runs(pr, result='the game exited'))):
        try:
            navkit.sample(pr, LV, log=QUIET)
        except navkit.NavKitError as e:
            assert str(e) == 'sampling did not finish: the game exited'
        else:
            raise AssertionError('an unfinished run is an error')
    with no_game(), Replace((navkit, 'sample_run', Runs(pr, write=False))):
        try:
            navkit.sample(pr, LV, log=QUIET)
        except navkit.NavKitError as e:
            assert 'sampling did not finish' in str(e), 'no samples written: not finished'
        else:
            raise AssertionError('no samples written: an error')


def test_sample_detailed(tmp_path):
    pr = project(tmp_path)
    settings(pr)
    runs = Runs(pr)
    with no_game(), Replace((navkit, 'sample_run', runs)), choose([[1, 1], [2, 1]], pr):
        assert navkit.sample(pr, LV, log=QUIET, pitch=0.2, budget_ms=300)
    assert [c[1] for c in runs.calls] == ['sp_01_navkit_p02', 'sp_01_navkit_hi_p02']
    assert runs.calls[0][2:5] == (0.2, 300, [[-1, 299, 101, 401], [99, 299, 201, 401]])
    assert runs.calls[0][5] == [-1, 299, 201, 401], 'the box around the detailed sectors'
    record = json.load(open(detailed.sampled_record_path(pr.samples(LV, '_p02')), encoding='utf-8'))
    assert record == {'xoff': -1, 'zoff': 2, 'sectors': [[1, 1], [2, 1]]}, 'finished: no longer pending'
    assert navkit.read_json(pr.settings_path(LV))['box'] == [0, 0, 400, 400], 'the level\'s settings stay'
    # a box given: no rects
    runs = Runs(pr)
    with no_game(), Replace((navkit, 'sample_run', runs)), choose([[1, 1], [2, 1]], pr):
        navkit.sample(pr, LV, log=QUIET, pitch=0.2, box=[0, 300, 100, 400])
    assert runs.calls[0][4] is None
    record = json.load(open(detailed.sampled_record_path(pr.samples(LV, '_p02')), encoding='utf-8'))
    assert record['sectors'] == [[1, 1]], 'the record holds the sectors the box covers'


def test_sample_level(tmp_path):
    pr = project(tmp_path)
    settings(pr)
    lines = []
    assert navkit.sample_level(pr, LV, log=lines.append, confirm=lambda q: False) is False and lines == ['  cancelled']
    runs = Runs(pr)
    with no_game(), Replace((navkit, 'sample_run', runs)), choose([], pr):
        lines = []
        assert navkit.sample_level(pr, LV, log=lines.append, confirm=lambda q: True)
    assert len(runs.calls) == 2 and lines[-1] == f'  {LV}: no detailed sectors - no 0.2 m runs'
    runs = Runs(pr)
    with no_game(), Replace((navkit, 'sample_run', runs)), choose([[1, 1]], pr):
        lines = []
        assert navkit.sample_level(pr, LV, log=lines.append, budget_ms=50)
    assert [c[2] for c in runs.calls] == [0.8, 0.8, 0.2, 0.2] and f'  {LV}: 1 detailed sectors - 0.2 m runs' in lines
    with no_game(), Replace((navkit, 'sample_run', Runs(pr, result='timed out'))):
        try:
            navkit.sample_level(pr, LV, log=QUIET)
        except navkit.NavKitError as e:
            assert 'timed out' in str(e)
        else:
            raise AssertionError('a failed run fails the level\'s sampling')


class Game:
    """sample_run's game replaced: the launch recorded (with opfor.ini as the game would read it), the game log's new
    lines scripted, the clock's waits skipped."""
    def __init__(self, pr, lines, running=True):
        self.pr, self.lines, self.running, self.launched, self.closed = pr, lines, running, [], 0

    def popen(self, args, cwd=None):
        self.launched.append((args, open(self.pr.ini, encoding='utf-8', newline='').read()))

    def close(self):
        self.closed += 1

    def replace(self):
        fake_time = SimpleNamespace(time=time.time, sleep=lambda s: None)
        return Replace((navkit, 'subprocess', SimpleNamespace(Popen=self.popen)), (navkit, 'time', fake_time),
                       (navkit, 'read_log', lambda pr, mark: list(self.lines)),
                       (navkit, 'game_running', lambda: self.running), (navkit, 'close_game', self.close))


def test_sample_run(tmp_path):
    pr = project(tmp_path)
    ini = '﻿[dev]\r\nlevel=Levels/MP_X\r\n[debug]\r\nprobes=0\r\n'
    touch(pr.ini, ini.encode('utf-8'))
    s = {'level': 'SP_01', 'box': [0, 0, 400, 400], 'y': [-10, 90]}
    pr.out_dir(LV)                               # work/navkit, where the ini's copy is kept during a run
    game = Game(pr, ['navsample: sampling 10 %', 'navsample: done'])
    lines = []
    with game.replace():
        result = navkit.sample_run(pr, s, '0.4,0.28,1.9', 'sp_01_navkit', lines.append, 3600)
    assert result == 'done' and game.closed == 1 and lines == ['    done']
    args, during = game.launched[0]
    assert args == [os.path.join(pr.root, 'OPFOR.exe')]
    for line in ('level=Levels/SP_01', 'autostart=1', 'navsample=1', 'navsample_name=sp_01_navkit',
                 'navsample_box=0,0,400,400', 'navsample_y=-10,90', 'navsample_pitch=0.8', 'navsample_agent=0.4,0.28,1.9',
                 'probes=1', 'navsample_rects='):
        assert line + '\r' in during.split('\n'), line
    assert 'navsample_budget_ms' not in during, 'a normal run keeps the ini\'s budget'
    assert open(pr.ini, encoding='utf-8', newline='').read() == ini and not os.path.exists(navkit.ini_backup(pr)), 'put back'

    s02 = dict(s, rects=[[0, 0, 100, 100], [100, 0, 200, 100]])
    game = Game(pr, ['navsample: sampling 5 %', 'UI cmd [main menu]'])
    with game.replace():
        result = navkit.sample_run(pr, s02, 'a', 'n', QUIET, 3600, pitch=0.2)
    assert result.startswith('the level ended (back at the main menu)') and result.endswith(f'(this run: {navkit.DETAILED_BUDGET_MS})')
    during = game.launched[0][1]
    assert f'navsample_budget_ms={navkit.DETAILED_BUDGET_MS}\r' in during and 'navsample_rects=0,0,100,100;100,0,200,100\r' in during
    game = Game(pr, ['navsample: armed', 'UI cmd [main menu]', 'navsample: sampling 1 %'], running=False)
    with game.replace():
        assert navkit.sample_run(pr, s, 'a', 'n', QUIET, 3600, budget_ms=77) == 'the game exited', \
            'the menu before sampling began is not the level ending'
    assert 'navsample_budget_ms=77\r' in game.launched[0][1]
    game = Game(pr, [])
    with game.replace():
        assert navkit.sample_run(pr, s, 'a', 'n', QUIET, 0) == 'timed out'
    assert game.closed == 1 and open(pr.ini, encoding='utf-8', newline='').read() == ini


def build_inputs(pr):
    settings(pr, xoff=0, zoff=0)
    touch(pr.samples(LV))
    touch(pr.samples(LV, '_hi'))
    L = pr.level_work(LV)
    for name in ('forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy'):
        touch(os.path.join(L, name))
    touch(os.path.join(L, 'soldier_spawns.json'), b'[[1, 2, 3]]')


def test_build(tmp_path):
    pr = project(tmp_path)
    settings(pr)
    try:
        navkit.build(pr, LV, log=QUIET)
    except navkit.NavKitError as e:
        assert 'samples missing (sp_01_navkit.bin and its _hi run) - run sample first' in str(e)
    else:
        raise AssertionError('no samples: an error')
    build_inputs(pr)
    seen = []

    def fake_build(*a, **k):
        seen.append((a, k))
        touch(a[4], b'x' * 1000)
    lines = []
    with Replace((navbuild, 'build', fake_build), (navregress, 'level_water', lambda *a: None)), choose([], pr):
        navkit.build(pr, LV, log=lines.append)
    a, k = seen[0]
    assert a == (pr.samples(LV), os.path.join(pr.level_work(LV), 'forbid_tris.npy'), 0, 0,
                 os.path.join(pr.out_dir(LV), 'pathdata.hpd'))
    assert k['seeds'] == [[1, 2, 3]] and k['detailed'] is None and k['high_path'] == pr.samples(LV, '_hi')
    assert lines[0].startswith(f'== build {LV} -> ') and lines[-1].startswith('  built in ')

    def failing(*a, **k):
        raise navbuild.BuildError('no walkable ground kept')
    with Replace((navbuild, 'build', failing), (navregress, 'level_water', lambda *a: None)), choose([], pr):
        try:
            navkit.build(pr, LV, log=QUIET)
        except navkit.NavKitError as e:
            assert str(e) == f'{LV}: no walkable ground kept'
        else:
            raise AssertionError('a BuildError is a NavKitError')


def test_detailed_pass(tmp_path):
    pr = project(tmp_path)
    build_inputs(pr)
    touch(pr.samples(LV, '_p02'))
    detailed.write_sampled_record(pr.samples(LV, '_p02'), [(1, 1)], 0, 0)
    lines = []
    with choose([[1, 1]], pr):
        d = navkit.detailed_pass(pr, LV, pr.samples(LV), 0, 0, log=lines.append)
    assert d['sectors'] == {(1, 1)} and 'no high-capsule run at 0.2' in lines[0]
    with choose([[1, 1], [3, 3]], pr):
        try:
            navkit.detailed_pass(pr, LV, pr.samples(LV), 0, 0, log=QUIET)
        except navkit.NavKitError as e:
            assert 'the 0.2 m samples are stale' in str(e)
        else:
            raise AssertionError('stale 0.2 m samples: a NavKitError')


def test_check_and_compare(tmp_path):
    pr = project(tmp_path)
    settings(pr)
    try:
        navkit.check(pr, LV, log=QUIET)
    except navkit.NavKitError as e:
        assert str(e) == f'{LV}: not built yet'
    else:
        raise AssertionError('nothing built: an error')
    touch(os.path.join(pr.level_work(LV), 'dist', 'levels', 'SP_01.xml'), LEVEL_XML.encode())
    ours = build_hpd(os.path.join(pr.out_dir(LV), 'pathdata.hpd'), gap=False)
    lines = []
    navkit.check(pr, LV, log=lines.append)
    assert lines[0] == f'== check {LV} (self-check: no original path data)'
    assert any('self-check: 1/1 point pairs' in line for line in lines), lines
    report = open(os.path.join(pr.out_dir(LV), 'check.txt'), encoding='utf-8').read()
    assert report.startswith("2 order / waypoint positions: {'AreaOrder': 2}")
    try:
        navkit.compare(pr, LV, log=QUIET)
    except navkit.NavKitError as e:
        assert 'no reference file' in str(e)
    else:
        raise AssertionError('no original: compare needs --reference')
    build_hpd(pr.pathdata(LV))
    lines = []
    navkit.check(pr, LV, log=lines.append)
    assert lines[0] == f'== check {LV} against the original path data'
    assert '  summary against the original:' in lines
    assert any('pairwise route check: 0/0' in line for line in lines), 'the two orders are on two islands in DICE\'s'
    lines = []
    navkit.compare(pr, LV, log=lines.append)
    assert lines[0].startswith(f'== compare {LV}: reference ') and len(lines) == 4
    try:
        navkit.compare(pr, LV, log=QUIET, candidate=os.path.join(str(tmp_path), 'none.hpd'))
    except navkit.NavKitError as e:
        assert 'no candidate file' in str(e)
    else:
        raise AssertionError('no candidate: an error')
    assert ours


def test_read_back_and_game_path_data(tmp_path):
    pr = project(tmp_path)
    bad = os.path.join(str(tmp_path), 'bad.hpd')
    touch(bad, b'not path data')
    try:
        navkit.read_back(bad)
    except navkit.NavKitError as e:
        assert 'is not a usable path-data file' in str(e)
    else:
        raise AssertionError('not path data: refused')
    good = build_hpd(os.path.join(str(tmp_path), 'good.hpd'))
    data = open(good, 'rb').read()
    assert navkit.read_back(good) == data
    touch(bad, data + b'\0' * 16)
    try:
        navkit.read_back(bad)
    except navkit.NavKitError as e:
        assert 'not a usable path-data file' in str(e), e
    else:
        raise AssertionError('trailing bytes: refused')
    assert not navkit.game_path_data(pr, LV, good)
    assert navkit.game_path_data(pr, LV, os.path.join(pr.game, 'Output', 'win32', 'ai', 'x', 'y.hpd'))
    touch(pr.pathdata(LV), data)
    assert navkit.game_path_data(pr, LV, pr.pathdata(LV))
    lines = []
    navkit.restore(pr, 'mp_x', log=lines.append)
    assert lines == ['mp_x: nothing to restore (no NavKit file installed)']


def side_file(pr, operations, kind='dice', sha1='not the file'):
    path = navtune.default_path(pr.root, LV)
    touch(path, b'{}')
    return Replace((navtune, 'load', lambda p: navtune.NavTune('Levels/SP_01', {'kind': kind, 'sha1': sha1}, operations)))


LINK = {'op': 'link-areas', 'a': [9.6, 0.0, 19.2], 'b': [32.8, 0.0, 19.2]}
LADDER = {'op': 'traversal', 'kind': 'ladder', 'bottom': [9.6, 0.0, 19.2], 'top': [32.8, 0.0, 19.2]}
NOTE = {'op': 'note', 'at': [1, 2, 3], 'text': 'look here'}


def test_apply_on_dice_file(tmp_path):
    pr = project(tmp_path)
    try:
        navkit.apply(pr, LV, log=QUIET)
    except navkit.NavKitError as e:
        assert 'no side file' in str(e)
    else:
        raise AssertionError('no side file: an error')
    touch(navtune.default_path(pr.root, LV), b'{}')
    try:
        navkit.apply(pr, LV, log=QUIET)
    except navkit.NavKitError as e:
        assert 'not a navtune side file' in str(e)
    else:
        raise AssertionError('a damaged side file: an error')
    with side_file(pr, [LINK]):
        try:
            navkit.apply(pr, LV, log=QUIET)
        except navkit.NavKitError as e:
            assert "the side file's base is DICE's data, but the level has none" in str(e)
        else:
            raise AssertionError('no DICE file: an error')
    dice = build_hpd(pr.pathdata(LV))
    before = open(dice, 'rb').read()
    with side_file(pr, [{'op': 'walkable', 'region': [[0, 0], [1, 0], [1, 1]]}]):
        try:
            navkit.apply(pr, LV, log=QUIET)
        except navkit.NavKitError as e:
            assert 'region edits need a NavKit base' in str(e)
        else:
            raise AssertionError('region edits on DICE\'s file: refused')
    lines = []
    with side_file(pr, [LINK, LADDER, NOTE]), no_game():
        out = navkit.apply(pr, LV, log=lines.append, install_after=True)
    assert out == os.path.join(pr.out_dir(LV), 'pathdata_dice_edited.hpd')
    assert any("note: DICE's file is not the one the edits were made on" in line for line in lines)
    assert '  1 of 1 link / route-edge edit(s) applied' in lines and '  1 note(s): kept in the side file only' in lines
    f = hpdtool.parse(open(out, 'rb').read())
    assert sum(len(s.edges) for s in f.sectors) == sum(len(s.edges) for s in hpdtool.parse(before).sectors) + 2
    trav = navkit.traversal_path(pr.out_dir(LV), LV)
    assert json.load(open(trav, encoding='utf-8'))['links'][0]['kind'] == 'ladder'
    report = open(out + '.report.txt', encoding='utf-8').read()
    assert report.startswith(f'== apply {LV}: 3 edit(s) on a copy of DICE') and 'read back exactly' in report
    assert open(pr.pathdata(LV), 'rb').read() == open(out, 'rb').read(), 'installed'
    assert open(pr.pathdata(LV) + navkit.BACKUP_SUFFIX, 'rb').read() == before, 'DICE\'s file kept'
    assert os.path.isfile(navkit.traversal_path(os.path.dirname(pr.pathdata(LV)), LV))
    with side_file(pr, [LINK]), no_game():
        navkit.apply(pr, LV, log=QUIET)
    assert not os.path.exists(trav), 'no traversal edits now: the earlier annotations go'
    try:
        with side_file(pr, [LINK]):
            navkit.apply(pr, LV, out=pr.pathdata(LV), log=QUIET)
    except navkit.NavKitError as e:
        assert "is the game's own path data" in str(e)
    else:
        raise AssertionError('--out onto the game\'s file: refused')


def test_apply_report_on_failure(tmp_path):
    pr = project(tmp_path)
    out = os.path.join(str(tmp_path), 'edited.hpd')
    touch(pr.pathdata(LV), b'not path data')
    with side_file(pr, [LINK], sha1=''):
        try:
            navkit.apply(pr, LV, out=out, log=QUIET)
        except Exception as e:                    # noqa: BLE001 - whatever the reader raises
            message = str(e)
        else:
            raise AssertionError('DICE\'s file unreadable: the apply fails')
    report = open(out + '.report.txt', encoding='utf-8').read()
    assert report.rstrip().endswith('error: ' + message), 'the report says it failed'


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), no_game():
        code = navkit.main(['navkit_le.py'] + argv)
    return code, out.getvalue()


def test_main(tmp_path):
    root = str(tmp_path)
    project(tmp_path)
    assert run_main([])[0] == 0 and 'navkit_le.py' in run_main(['--help'])[1]
    for args, message in ((['sample', LV, '--pitch', '0.5'], "give 0.8 (the normal runs) or 0.2"),
                          (['sample', LV, '--budget', '0'], 'whole milliseconds'),
                          (['sample', LV, '--budget', '2.5'], 'whole milliseconds'),
                          (['sample', LV, '--y', '5,1'], 'the bottom must be below the top'),
                          (['sample', LV, '--y', '1,inf'], 'give bottom,top (2 numbers)')):
        code, out = run_main(args + ['--opfor', root])
        assert code == 2 and message in out, (args, out)
    code, out = run_main(['levels', '--root', root])
    assert code == 0 and 'sp_01' in out
    code, out = run_main(['build', '--opfor', root])
    assert code == 1 and 'build: which level? (e.g. navkit_le.py build SP_01)' in out
    code, out = run_main(['dance', LV, '--opfor', root])
    assert code == 1 and "unknown command 'dance'" in out
    code, out = run_main(['build', 'sp_77', '--opfor', root])
    assert code == 1 and "unknown level 'sp_77'" in out
    code, out = run_main(['build', LV, '--opfor', root])
    assert code == 1 and 'not prepared yet' in out
    code, out = run_main(['status', LV, '--opfor', root])
    assert code == 0 and 'prepared           : no' in out
    code, out = run_main(['restore', LV, '--opfor', root])
    assert code == 0 and 'nothing to restore' in out
    code, out = run_main(['install', LV, '--opfor', root])
    assert code == 1 and 'not built yet' in out
    code, out = run_main(['check', LV, '--opfor', root])
    assert code == 1 and 'not prepared yet' in out
    code, out = run_main(['compare', LV, '--opfor', root])
    assert code == 1
    code, out = run_main(['apply', LV, '--opfor', root])
    assert code == 1 and 'no side file' in out
    with Replace((navkit, 'sample', lambda *a, **k: False)):
        code, out = run_main(['sample', LV, '--pitch', '0.2', '--yes', '--opfor', root])
    assert code == 1 and 'cancelled - nothing was sampled' in out
    with Replace((navkit, 'prepare', lambda *a, **k: None), (navkit, 'sample_level', lambda *a, **k: False)):
        code, out = run_main(['all', LV, '--yes', '--opfor', root])
    assert code == 1 and 'cancelled - prepared, but not sampled, built or checked' in out
    calls = []
    with Replace((navkit, 'prepare', lambda *a, **k: calls.append('prepare')),
                 (navkit, 'sample_level', lambda *a, **k: calls.append('sample') or True),
                 (navkit, 'build', lambda *a, **k: calls.append('build')),
                 (navkit, 'check', lambda *a, **k: calls.append('check'))):
        code, out = run_main(['all', LV, '--yes', '--opfor', root, '--box', '0,0,10,10', '--y', '0,5'])
    assert code == 0 and calls == ['prepare', 'sample', 'build', 'check']
