r"""NavKit little-endian: the level's ladders and the unattended sampling run, on made-up data (ladders, sample_all)

ladders.extract on a made-up level folder (objects.json, blueprint packfiles; the collision reader replaced by boxes;
no game data): an upright ladder gives its world box (rotation and translation applied), a ladder lying down, one with
no collision and an object that is not a ladder give none; its command line writes ladders.json. sample_all.main with
navkit_le's sampling replaced by a recorder on a made-up Project OPFOR folder (the game is never started): the smoke
level first, a level without 0.8 m samples gets them first, a cancelled or failing level is logged and the run goes
on (exit code 1), a failing smoke level stops the run, the log file.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json

import numpy as np

import ladders
import levelobjects
import navkit_le
import sample_all

FACES = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
         (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]


def box(lo, hi):
    c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], np.float32)
    return c[np.array(FACES)]


def matrix(right, up, forward, t):
    return [*right, 0, *up, 0, *forward, 0, *t, 1]


def level_folder(tmp_path):
    level = tmp_path / 'sp_01'
    for rel in ('pkg/objects/ladders/ladder_3m', 'pkg/objects/ladders/ladder_lying', 'dist/objects/ladders/ladder_x',
                'pkg/objects/props/crate'):
        f = level / (rel + '_Havok.res')
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'')
    turned = matrix((0, 0, -1), (0, 1, 0), (1, 0, 0), (10, 2, 20))           # 90 degrees about y
    objects = [{'bp': 'Objects/Ladders/Ladder_3m', 'm': turned},
               {'bp': 'objects/ladders/ladder_lying', 'm': matrix((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0))},
               {'bp': 'objects/ladders/ladder_x', 'm': matrix((1, 0, 0), (0, 1, 0), (0, 0, 1), (5, 0, 5))},
               {'bp': 'objects/ladders/ladder_nocollision', 'm': turned},
               {'bp': 'objects/props/crate', 'm': turned}]
    (level / 'objects.json').write_text(json.dumps(objects), encoding='utf-8')
    return level


def collision(path):
    if 'lying' in path:
        return box((0, 0, 0), (3, 0.3, 0.5))
    if 'ladder_x' in path:
        return None                                       # unreadable
    return box((0, 0, 0), (0.6, 3, 0.2))


class Collision:
    def __enter__(self):
        self.old = levelobjects.triangles
        levelobjects.triangles = collision

    def __exit__(self, *exc):
        levelobjects.triangles = self.old


def test_extract(tmp_path):
    level = level_folder(tmp_path)
    with Collision():
        found = ladders.extract(str(level))
    assert len(found) == 1, found
    ladder = found[0]
    assert ladder['bp'] == 'Objects/Ladders/Ladder_3m', 'the blueprint as placed'
    # local x 0..0.6 -> world -z, local z 0..0.2 -> world +x; then moved by (10, 2, 20)
    assert ladder['lo'] == [10.0, 2.0, 19.4] and ladder['hi'] == [10.2, 5.0, 20.0], ladder


def test_extra_bases(tmp_path):
    level = level_folder(tmp_path)
    extra = tmp_path / 'common'
    f = extra / 'objects' / 'ladders' / 'ladder_nocollision_Havok.res'
    f.parent.mkdir(parents=True)
    f.write_bytes(b'')
    with Collision():
        found = ladders.extract(str(level), [str(extra), str(tmp_path / 'missing')])
    assert [x['bp'] for x in found] == ['Objects/Ladders/Ladder_3m', 'objects/ladders/ladder_nocollision'], \
        'a blueprint folder given extra holds its collision'


def test_main(tmp_path):
    level = level_folder(tmp_path)
    out = io.StringIO()
    with Collision(), contextlib.redirect_stdout(out):
        assert ladders.main(['ladders.py', str(level)]) == 0
    assert out.getvalue().startswith('1 ladders -> ')
    assert json.loads((level / 'ladders.json').read_text(encoding='utf-8'))[0]['hi'] == [10.2, 5.0, 20.0]
    with contextlib.redirect_stdout(io.StringIO()) as usage:
        assert ladders.main(['ladders.py']) == 1
    assert 'Usage' in usage.getvalue()


class Sampling:
    """navkit_le's Project on a made-up OPFOR folder, and its sampling replaced: per level, what sample returns (or
    raises); records each call."""
    def __init__(self, root, outcome):
        self.root, self.outcome, self.calls = str(root), outcome, []

    def sample(self, pr, lv, log=print, pitch=0.8, budget_ms=None):
        self.calls.append((lv, pitch, budget_ms))
        result = self.outcome.get((lv, pitch), True)
        if isinstance(result, Exception):
            raise result
        return result

    def __enter__(self):
        self.old = navkit_le.Project, navkit_le.sample, navkit_le.detailed_sectors
        real = self.old[0]
        navkit_le.Project = lambda root=None: real(self.root)
        navkit_le.sample = self.sample
        navkit_le.detailed_sectors = lambda pr, lv: [(1, 2), (3, 4)]
        return self

    def __exit__(self, *exc):
        navkit_le.Project, navkit_le.sample, navkit_le.detailed_sectors = self.old


def opfor(tmp_path, with_samples=()):
    root = tmp_path / 'Project OPFOR'
    for rel in ('game/BFBC2Game.exe', 'tools/FbrbTool.exe', 'tools/DbxEdit.exe'):    # what Project looks for
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b'')
    (root / 'work' / 'navsample').mkdir(parents=True)
    for lv in with_samples:
        (root / 'work' / 'navsample' / f'{lv}_navkit.bin').write_bytes(b'')
        (root / 'work' / 'navsample' / f'{lv}_navkit_hi.bin').write_bytes(b'')
    return root


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sample_all.main(argv)
    return code, out.getvalue()


def test_sample_all(tmp_path):
    root = opfor(tmp_path, with_samples=('sp_02', 'sp_03'))
    log = tmp_path / 'run.log'
    outcome = {('sp_03', 0.2): False, ('sp_04', 0.2): RuntimeError('game crashed')}
    with Sampling(root, outcome) as s:
        code, out = run_main(['sample_all.py', '--levels', 'sp_01,sp_02,sp_03,sp_04', '--smoke', 'sp_02',
                              '--budget', '40', '--log', str(log)])
    assert code == 1
    assert s.calls == [('sp_02', 0.2, 40), ('sp_01', 0.8, None), ('sp_01', 0.2, 40), ('sp_03', 0.2, 40),
                       ('sp_04', 0.8, None), ('sp_04', 0.2, 40)], s.calls
    assert '[2/4] sp_01: no 0.8 m samples - those first' in out and '[1/4] sp_02: 2 sectors' in out
    assert '[3/4] sp_03: FAILED - cancelled' in out and '[4/4] sp_04: FAILED - game crashed' in out
    assert '2 done, 2 failed' in out and '   sp_04: game crashed' in out
    text = log.read_text(encoding='utf-8')
    assert '== sample_all: 4 levels at pitch 0.2: sp_02, sp_01, sp_03, sp_04' in text
    assert 'Traceback' in text and 'RuntimeError: game crashed' in text, 'the failure\'s traceback in the log'
    # the log is closed when main returns, even while the failure (kept in `outcome`) holds main's frame: until
    # 2026-10-31 it stayed open, and on Windows the file could not be removed
    log.unlink()
    assert not log.exists()


def test_sample_all_smoke_fails(tmp_path):
    root = opfor(tmp_path)
    log = tmp_path / 'run.log'
    with Sampling(root, {('sp_09', 0.8): False}) as s:
        code, out = run_main(['sample_all.py', '--levels', 'sp_01,sp_09', '--smoke', 'sp_09', '--log', str(log)])
    assert code == 1 and s.calls == [('sp_09', 0.8, None)]
    assert '0.8 m sampling cancelled' in out and 'smoke test failed - stopping' in out
    assert '0 done, 1 failed' in out


def test_sample_all_normal_pitch(tmp_path):
    root = opfor(tmp_path)
    with Sampling(root, {}) as s:
        code, out = run_main(['sample_all.py', '--pitch', '0.8', '--levels', 'sp_01'])
    assert code == 0 and s.calls == [('sp_01', 0.8, None)] and '[1/1] sp_01: all sectors' in out
    assert (root / 'work' / 'navsample' / 'sample_all.log').is_file(), 'the default log in OPFOR\'s work folder'
    assert '1 done, 0 failed' in out
