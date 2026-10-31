r"""navkit.py and its tools: the guards of the second review (2026-10-31), on a made-up Project OPFOR folder in a temp
folder (no game, no game data; the game is never started, nothing is sampled, navbuild.build is a stand-in):
  - apply --out naming the game's own path data is refused before anything is built or written;
  - an apply that fails in the builder (BuildError) is a clear error and its report says so, not an old success;
  - a 0.2 m sampling run that fails part-way is not taken for good samples by the next build;
  - --budget reaches the sampling runs; --pitch takes only 0.8 or 0.2;
  - install refuses a file that is not path data, and leaves the game's file as it was;
  - SectorOffsets: DbxEdit's forms are read; a malformed file is a clear error, not an AttributeError;
  - opfor.ini with a byte-order mark: its [dev] section is changed, no second one added;
  - build and the detailed-zone choice take the same 0.8 m samples (the ones with their _hi run);
  - detailed.py: without --offsets the level's navkit.json, else exit code 2; --score never against NavKit's own
    installed file; detailed zones around NeedsDetailedPathfinding objects (not ForbidPathfinding ones);
  - navregress runs are saved whole; navwalk.score without reference cells and ordercheck without route nodes.

  python -B test_driver_guards.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path[:0] = [HERE, KIT, os.path.join(KIT, 'tools'), os.path.join(KIT, '..', 'core')]
import detailed  # noqa: E402
import navbuild  # noqa: E402
import navkit  # noqa: E402
import navregress  # noqa: E402
from bfbc2core import navtune  # noqa: E402
from test_navkit_driver import project, touch  # noqa: E402

failures = []
QUIET = lambda *a, **k: None  # noqa: E731


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def hpd(n=0):
    import hpdtool
    return hpdtool.write(hpdtool.HpdFile((2, 2, 0, 1, n) + (0,) * 31, []))


@contextlib.contextmanager
def patched(obj, **attrs):
    saved = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


@contextlib.contextmanager
def apply_inputs(pr, build, kind='navkit'):
    """apply's inputs made up: navregress.inputs, the water and extent, the detailed pass, the side file."""
    L = pr.level_work('sp_y')
    touch(os.path.join(L, 'soldier_spawns.json'), b'[]')
    tune = os.path.join(pr.root, 't.navtune.json')
    touch(tune, b'{}')
    with patched(navbuild, build=build), \
            patched(navregress, inputs=lambda r, lv: ('x.bin', 0, 0, {k: os.path.join(L, k) for k in (
                'forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy', 'soldier_spawns.json')}),
                level_water=lambda *a: None, reference_bounds=lambda *a: None), \
            patched(navkit, detailed_pass=lambda *a, **k: None), \
            patched(navtune, load=lambda p: SimpleNamespace(kind=kind, operations=[], level='sp_y',
                                                            base_changed=lambda s: False)):
        yield tune


def writer(path_index=4):
    calls = []

    def build(*a, **k):
        calls.append(a[path_index])
        tmp = a[path_index] + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(hpd())
        os.replace(tmp, a[path_index])
    return build, calls


def apply_out(root):
    pr = project(root)
    game_file = pr.pathdata('sp_y')
    touch(game_file, b'DICE ORIGINAL')
    build, calls = writer()
    for kind in ('navkit', 'dice'):
        with apply_inputs(pr, build, kind) as tune:
            for out in (game_file, game_file + navkit.BACKUP_SUFFIX):
                try:
                    navkit.apply(pr, 'sp_y', tune, out=out, log=QUIET)
                    refused = False
                except navkit.NavKitError:
                    refused = True
                except Exception:                     # noqa: BLE001 - anything else is not the clear refusal
                    refused = False
                check(refused, f'apply ({kind} base) --out {os.path.basename(out)}: refused')
    with open(game_file, 'rb') as f:
        same = f.read() == b'DICE ORIGINAL'
    left = sorted(n for n in os.listdir(os.path.dirname(game_file)) if n != 'pathdata.hpd')
    check(same and not calls and not left, f'... the game\'s file as it was, nothing built, nothing written beside it '
                                          f'({len(calls)} builds, {left})')


def apply_build_error(root):
    pr = project(root)
    out = os.path.join(root, 'mine.hpd')
    build, _ = writer()
    with apply_inputs(pr, build) as tune:
        navkit.apply(pr, 'sp_y', tune, out=out, log=QUIET)

    def fail(*a, **k):
        raise navbuild.BuildError('no walkable ground kept (made up)')
    with apply_inputs(pr, fail) as tune:
        try:
            navkit.apply(pr, 'sp_y', tune, out=out, log=QUIET)
            kind = None
        except Exception as e:                        # noqa: BLE001
            kind = type(e).__name__
    with open(out + '.report.txt', encoding='utf-8') as f:
        last = f.read().strip().splitlines()[-1]
    check(kind == 'NavKitError', f'a BuildError in apply is a clear error (NavKitError: exit code 1) - got {kind}')
    check(last.startswith('error:'), f'... and the report says so, not the earlier success ({last!r})')


def failed_p02(root):
    pr = project(root)
    lv = 'sp_y'
    navkit.write_json(pr.settings_path(lv), {'level': 'SP_Y', 'box': [0, 0, 400, 400], 'y': [-10, 100], 'xoff': 0,
                                             'zoff': 0})
    s08 = pr.samples(lv)
    touch(s08, b'0.8')
    touch(s08[:-4] + '_hi.bin', b'0.8hi')
    old = time.time() - 100
    os.utime(s08, (old, old))
    chosen = [(1, 1), (2, 1)]

    def run(result):
        def sample_run(pr_, s, agent, name, log, *a):
            touch(os.path.join(pr_.work, 'navsample', name + '.bin'), result.encode())
            return result
        return sample_run
    with patched(navkit, detailed_sectors=lambda pr_, lv_: chosen, game_running=lambda: False,
                 sample_run=run('done')):
        navkit.sample(pr, lv, log=QUIET, pitch=0.2)
    check(navregress.detailed_of(root, lv, s08, 0, 0, chosen=chosen) is not None, 'a finished 0.2 m run is used')
    with patched(navkit, detailed_sectors=lambda pr_, lv_: chosen, game_running=lambda: False,
                 sample_run=run('the game exited')):
        try:
            navkit.sample(pr, lv, log=QUIET, pitch=0.2, box=[100, 100, 200, 200])
        except navkit.NavKitError:
            pass
    try:
        navregress.detailed_of(root, lv, s08, 0, 0, chosen=chosen)
        check(False, 'the samples of a 0.2 m run that failed part-way are refused by the next build')
    except RuntimeError as e:
        check('did not finish' in str(e), f'the samples of a 0.2 m run that failed part-way are refused ({e})')


def command_line(root):
    project(root)
    calls = []
    with patched(navkit, sample=lambda pr, lv, **k: calls.append(('sample', k)) or True,
                 sample_level=lambda pr, lv, **k: calls.append(('level', k)) or True):
        with contextlib.redirect_stdout(io.StringIO()):
            rc1 = navkit.main(['navkit.py', 'sample', 'sp_y', '--budget', '800', '--opfor', root, '--yes'])
            rc2 = navkit.main(['navkit.py', 'sample', 'sp_y', '--pitch', '0.2', '--budget', '650', '--opfor', root,
                               '--yes'])
        check(rc1 == 0 and rc2 == 0 and [c[1].get('budget_ms') for c in calls] == [800, 650],
              f'--budget reaches the sampling runs ({[(c[0], c[1].get("budget_ms")) for c in calls]})')
        for args, what in ((['--pitch', '0'], '--pitch 0'), (['--pitch', '-0.2'], '--pitch -0.2'),
                           (['--pitch', '0.4'], '--pitch 0.4'), (['--budget', 'fast'], '--budget fast'),
                           (['--budget', '0'], '--budget 0')):
            n = len(calls)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = navkit.main(['navkit.py', 'sample', 'sp_y'] + args + ['--opfor', root, '--yes'])
            check(rc == 2 and len(calls) == n, f'{what}: exit code 2, nothing sampled ({rc})')


def install_checked(root):
    pr = project(root)
    game_file = pr.pathdata('sp_y')
    touch(game_file, b'DICE ORIGINAL')
    bad = os.path.join(root, 'notes.txt')
    touch(bad, b'not path data\n')
    try:
        navkit.install(pr, 'sp_y', log=QUIET, src=bad)
        refused = False
    except navkit.NavKitError:
        refused = True
    with open(game_file, 'rb') as f:
        same = f.read() == b'DICE ORIGINAL'
    check(refused and same and not pr.installed('sp_y'),
          'install --from a file that is not path data: refused, the game\'s file as it was, nothing installed')
    good = os.path.join(root, 'good.hpd')
    touch(good, hpd(7))
    navkit.install(pr, 'sp_y', log=QUIET, src=good)
    with open(game_file, 'rb') as f:
        check(f.read() == hpd(7) and not os.path.exists(game_file + '.tmp'), '... a good file is installed whole')


def sector_offsets(root):
    p = os.path.join(root, 'so.xml')
    for text, want in (('<field name="XOffset">-8</field>\n<field name="ZOffset">3</field>', (-8, 3)),
                       ('<field name="XOffset"> -8.0 </field><field name="ZOffset" type="Int32">12</field>', (-8, 12))):
        touch(p, text.encode())
        try:
            got = navregress.sector_offsets(p)
        except Exception as e:                        # noqa: BLE001 - an older kit has no sector_offsets
            got = f'{type(e).__name__}: {e}'
        check(got == want, f'SectorOffsets {text[:40]!r}...: {got}')
    # a malformed file: navregress.inputs and navkit (ensure_settings) say which file, no AttributeError
    pr = project(root)
    so = os.path.join(pr.level_work('sp_y'), 'dist', 'levels', 'sp_y', 'Precomputed', 'Infantry', 'SectorOffsets.xml')
    touch(so, b'<field name="XOffset">abc</field>')
    s08 = pr.samples('sp_y')
    touch(s08, b'')
    touch(s08[:-4] + '_hi.bin', b'')
    try:
        navregress.inputs(root, 'sp_y')
        kind = None
    except Exception as e:                            # noqa: BLE001
        kind = type(e).__name__
        msg = str(e)
    check(kind == 'RuntimeError' and 'SectorOffsets' in msg, f'navregress.inputs on a malformed SectorOffsets: {kind}')
    try:
        navkit.ensure_settings(pr, 'sp_y')
        kind = None
    except Exception as e:                            # noqa: BLE001
        kind = type(e).__name__
    check(kind == 'NavKitError', f'navkit on a malformed SectorOffsets: a clear error (NavKitError) - got {kind}')


def ini_bom(root):
    t = '﻿[dev]\r\nlevel=Levels/SP_01\r\nnavsample=0\r\n\r\n[debug]\r\nprobes=0\r\n'
    for sec, k, v in (('dev', 'navsample', '1'), ('dev', 'level', 'Levels/SP_05'), ('debug', 'probes', '1')):
        t = navkit.ini_set(t, sec, k, v)
    check(t.startswith('﻿[dev]\r\n') and t.lower().count('[dev]') == 1 and 'navsample=1\r' in t
          and 'navsample=0' not in t and 'level=Levels/SP_05\r' in t and t.count('[debug]') == 1,
          f'opfor.ini with a byte-order mark: its [dev] changed, the mark kept ({t!r})')


def samples_pair(root):
    pr = project(root)
    touch(pr.samples('sp_y'), b'')                            # <lv>_navkit.bin without its _hi run
    full = os.path.join(pr.work, 'navsample', 'sp_y_full.bin')
    touch(full, b'')
    touch(full[:-4] + '_hi.bin', b'')
    check(os.path.normcase(pr.samples08('sp_y') or '') == os.path.normcase(full),
          f'the 0.8 m samples: the ones with their _hi run, as navregress.inputs (build, apply) takes them '
          f'({os.path.basename(pr.samples08("sp_y") or "-")})')


def run_detailed(args):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return detailed.main(['detailed.py'] + args)
    except Exception as e:                            # noqa: BLE001 - the old tool: KeyError
        return type(e).__name__


def detailed_main(root):
    pr = project(root)
    os.environ['OPFOR_ROOT'] = root
    try:
        with patched(detailed, choose=lambda *a, **k: ([], {})):
            rc = run_detailed(['sp_y'])
        check(rc == 2, f'detailed.py without --offsets or navkit.json: exit code 2 ({rc})')
        navkit.write_json(pr.settings_path('sp_y'), {'xoff': -3, 'zoff': 4})
        seen = []
        with patched(detailed, choose=lambda L, c, s, x, z: seen.append((x, z)) or ([], {})):
            rc = run_detailed(['sp_y'])
        check(rc == 0 and seen == [(-3, 4)], f'... with navkit.json: its offsets ({rc}, {seen})')
        # NavKit's own file installed where the level had none: no DICE file to score against
        touch(pr.pathdata('sp_y'), hpd())
        touch(pr.pathdata('sp_y') + navkit.NONE_SUFFIX, b'')
        scored = []
        with patched(detailed, choose=lambda *a, **k: ([], {}), score=lambda chosen, p: scored.append(p) or (0, 0, 0)):
            rc = run_detailed(['sp_y', '--offsets', '0,0', '--score'])
        check(not scored and rc == 1, f'--score never scores NavKit\'s own installed file ({rc}, {scored})')
    finally:
        os.environ.pop('OPFOR_ROOT', None)


def detailed_flag(root):
    """Detailed zones follow NeedsDetailedPathfinding (flags[5]), not ForbidPathfinding (flags[0]); an objects.json
    from before that flag was extracted is refused, not read as "no flagged object"."""
    L = os.path.join(root, 'level')
    m = lambda x, z: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, 0.0, z, 1]  # noqa: E731
    objs = [{'bp': 'a/forbidden', 'flags': [True, False, False, False, '', False], 'm': m(150.0, 150.0)},
            {'bp': 'a/detailed', 'flags': [False, False, False, False, '', True], 'm': m(350.0, 350.0)},
            {'bp': 'a/plain', 'flags': None, 'm': m(550.0, 550.0)}]
    touch(os.path.join(L, 'objects.json'), json.dumps(objs).encode())
    try:
        got = detailed.flagged_sectors(L, [], 0, 0)
    except Exception as e:                            # noqa: BLE001
        got = f'{type(e).__name__}: {e}'
    check(got == {(3, 3)}, f'detailed zones: around NeedsDetailedPathfinding objects only ({got})')
    import levelobjects
    base = os.path.join(root, 'bp')
    touch(os.path.join(base, 'a', 'house.xml'), b'<field name="NeedsDetailedPathfinding">true</field>')
    fl = levelobjects.blueprint_flags([base]).get('a/house')
    check(bool(fl) and len(fl) > 5 and fl[5] is True,
          f'levelobjects: a blueprint\'s NeedsDetailedPathfinding is extracted ({fl})')
    touch(os.path.join(L, 'objects.json'), json.dumps([dict(objs[0], flags=objs[0]['flags'][:5])]).encode())
    try:
        detailed.flagged_sectors(L, [], 0, 0)
        check(False, 'an objects.json from before NeedsDetailedPathfinding was extracted is refused')
    except ValueError as e:
        check('prepare' in str(e), 'an objects.json from before NeedsDetailedPathfinding was extracted is refused')


def regress_saved_whole(root):
    target = os.path.join(root, 'run.json')
    touch(target, b'{"levels": []}')
    save = getattr(navregress, 'save_run', None)
    if save is None:
        check(False, 'navregress saves runs whole (no save_run)')
        return
    try:
        save(target, {'levels': [object()]})
    except TypeError:
        pass
    with open(target, 'rb') as f:
        check(f.read() == b'{"levels": []}' and not os.path.exists(target + '.tmp'),
              'navregress: a run that cannot be written leaves the previous file whole')


def empty_measures(root):
    import navsample
    import navwalk
    import ordercheck
    s = SimpleNamespace(nx=2, nz=2, pitch=0.8, min_x=0.0, min_z=0.0, start=np.array([0, 1, 2, 3, 4]),
                        y=np.zeros(4, np.float32))
    with patched(navsample, reference_cells=lambda s_, p: {}):
        try:
            r = navwalk.score(s, np.ones(4, bool), 'none.hpd')
            ok = r['reference'] == 0 and r['found'] == 0
        except ZeroDivisionError:
            ok = False
    check(ok, 'navwalk.score with no reference cell in the box: shares of 0, no ZeroDivisionError')
    pd = ordercheck.PathData.__new__(ordercheck.PathData)
    pd.ids, pd.pos, pd.group, pd.group_size = [], np.zeros((0, 3)), {}, {}
    try:
        got = pd.nearest_group((0.0, 0.0, 0.0))
    except ValueError as e:
        got = f'ValueError: {e}'
    check(got == (None, None), f'ordercheck: a file with no route nodes has none near a point ({got})')


if __name__ == '__main__':
    with patched(navkit, game_running=lambda: False):         # never look for, start or close the game here
        for test in (apply_out, apply_build_error, failed_p02, command_line, install_checked, sector_offsets, ini_bom,
                     samples_pair, detailed_main, detailed_flag, regress_saved_whole, empty_measures):
            tmp = tempfile.mkdtemp()
            try:
                test(tmp)
            except Exception as e:                            # noqa: BLE001 - one test's crash fails it, not the run
                check(False, f'{test.__name__}: {type(e).__name__}: {e}')
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
