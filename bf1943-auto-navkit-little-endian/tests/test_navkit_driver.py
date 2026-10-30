r"""navkit.py's steps on a made-up Project OPFOR folder in a temp folder (no game, no game data; the game is never started,
nothing is sampled, and navbuild.build is replaced by a recorder):
  - install where the level had no path data: a marker, so the file is never taken for DICE's (dice_pathdata,
    navparity.reference_of) and restore removes it; over an original: backup kept once, restore puts it back;
  - apply builds with the same inputs as build (detailed zones and ladders included);
  - the detailed-sector cache follows the sector offsets;
  - build refuses 0.2 m samples that do not cover the detailed sectors chosen now;
  - an unpack that did not finish is redone; a complete one from before the marker is kept;
  - the game log is read from the right place (none yet, a new shorter log, a new log of the same size);
  - settings are written whole or not at all;
  - command-line options: a missing or malformed value is exit code 2, a declined sampling run exit code 1;
  - navregress: a --save over a subset keeps the saved run's other levels.

  python -B test_navkit_driver.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path[:0] = [KIT, os.path.join(KIT, 'tools')]
import detailed  # noqa: E402
import navkit  # noqa: E402
import navparity  # noqa: E402
import navregress  # noqa: E402

failures = []
QUIET = lambda *a, **k: None  # noqa: E731


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def touch(path, data=b''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def project(root):
    for p in ('game/BFBC2Game.exe', 'tools/FbrbTool.exe', 'tools/DbxEdit.exe',
              'game/Dist/win32/levels/mp_x/level-00.fbrb', 'game/Dist/win32/levels/sp_y/level-00.fbrb'):
        touch(os.path.join(root, p))
    return navkit.Project(root)


def install_restore(root):
    pr = project(root)
    built = os.path.join(pr.out_dir('mp_x'), 'pathdata.hpd')
    touch(built, b'NAVKIT')
    dest = pr.pathdata('mp_x')
    navkit.install(pr, 'mp_x', log=QUIET)
    check(pr.dice_pathdata('mp_x') is None and not os.path.isfile(navparity.reference_of(root, 'mp_x')),
          'installed where the level had no path data: not taken for DICE\'s (dice_pathdata, reference_of)')
    check(dict(navkit.level_names(pr))['mp_x'] is False and pr.installed('mp_x'), '... listed as installed, not samplable')
    navkit.install(pr, 'mp_x', log=QUIET)                    # again: still no backup of NavKit's own file
    check(not os.path.isfile(dest + navkit.BACKUP_SUFFIX), '... installing again keeps no backup of NavKit\'s own file')
    navkit.restore(pr, 'mp_x', log=QUIET)
    check(not os.path.exists(dest) and not os.path.exists(dest + navkit.NONE_SUFFIX) and not pr.installed('mp_x'),
          '... restore removes NavKit\'s file and the marker')
    orig = pr.pathdata('sp_y')
    touch(orig, b'DICE')
    touch(os.path.join(pr.out_dir('sp_y'), 'pathdata.hpd'), b'NAVKIT1')
    navkit.install(pr, 'sp_y', log=QUIET)
    touch(os.path.join(pr.out_dir('sp_y'), 'pathdata.hpd'), b'NAVKIT2')
    navkit.install(pr, 'sp_y', log=QUIET)
    with open(orig + navkit.BACKUP_SUFFIX, 'rb') as f:
        check(f.read() == b'DICE' and pr.dice_pathdata('sp_y') == orig + navkit.BACKUP_SUFFIX,
              'over an original: the backup is DICE\'s file, kept once')
    navkit.restore(pr, 'sp_y', log=QUIET)
    with open(orig, 'rb') as f:
        check(f.read() == b'DICE' and not os.path.exists(orig + navkit.BACKUP_SUFFIX), '... restore puts it back')
    try:
        navkit.install(pr, 'sp_y', log=QUIET, src=orig)
        check(False, 'installing the installed file onto itself is refused')
    except navkit.NavKitError:
        check(True, 'installing the installed file onto itself is refused')


def apply_inputs(root):
    import navbuild
    from types import SimpleNamespace
    sys.path.insert(0, os.path.join(KIT, '..', 'core'))
    from bfbc2core import navtune
    pr = project(root)
    calls = []
    saved = (navbuild.build, navregress.inputs, navregress.level_water, navregress.reference_bounds, navtune.load,
             navkit.detailed_pass)
    L = pr.level_work('sp_y')
    touch(os.path.join(L, 'soldier_spawns.json'), b'[]')
    touch(os.path.join(L, 'ladders.json'), b'[{"lo": [0, 0, 0], "hi": [1, 3, 1]}]')
    try:
        def fake_build(*a, **k):
            calls.append(k)
            import hpdtool
            touch(a[4], hpdtool.write(hpdtool.HpdFile((2, 2, 0, 1) + (0,) * 32, [])))
        navbuild.build = fake_build
        navregress.inputs = lambda r, lv: ('x.bin', 0, 0, {k: os.path.join(L, k) for k in (
            'forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy', 'soldier_spawns.json')})
        navregress.level_water = navregress.reference_bounds = lambda *a: None
        navkit.detailed_pass = lambda *a, **k: {'samples': 'p02', 'high': None, 'sectors': {(1, 1)}}
        navtune.load = lambda p: SimpleNamespace(kind='navkit', operations=[], level='sp_y')
        tune = os.path.join(root, 't.navtune.json')
        touch(tune, b'{}')
        navkit.apply(pr, 'sp_y', tune, log=QUIET)
        k = calls[-1] if calls else {}
        check(k.get('detailed') == {'samples': 'p02', 'high': None, 'sectors': {(1, 1)}} and k.get('ladders'),
              f'apply builds with the detailed zones and ladders, as build does ({sorted(k)})')
    finally:
        (navbuild.build, navregress.inputs, navregress.level_water, navregress.reference_bounds, navtune.load,
         navkit.detailed_pass) = saved


def detailed_cache(root):
    pr = project(root)
    sp = pr.samples('sp_y')
    touch(sp)
    touch(sp[:-4] + '_hi.bin')
    settings = {'level': 'SP_Y', 'box': [0, 0, 100, 100], 'y': [0, 10], 'xoff': 0, 'zoff': 0}
    navkit.write_json(pr.settings_path('sp_y'), settings)
    choose = detailed.choose
    detailed.choose = lambda L, c, s, x, z: ([(5 - x, 5 - z)], {})
    try:
        first = navkit.detailed_sectors(pr, 'sp_y')
        navkit.write_json(pr.settings_path('sp_y'), dict(settings, xoff=-3, zoff=-7))
        second = navkit.detailed_sectors(pr, 'sp_y')
    finally:
        detailed.choose = choose
    check(first == [(5, 5)] and second == [(8, 12)], f'the detailed-sector cache follows the sector offsets ({first}, {second})')
    with open(os.path.join(pr.out_dir('sp_y'), 'detailed.json'), 'w', encoding='utf-8') as f:
        f.write('{"key": ')                                  # a damaged cache
    detailed.choose = lambda L, c, s, x, z: ([(1, 2)], {})
    try:
        check(navkit.detailed_sectors(pr, 'sp_y') == [(1, 2)], 'a damaged detailed-sector cache is made again')
    finally:
        detailed.choose = choose


def stale_p02(root):
    pr = project(root)
    s08 = pr.samples('sp_y')
    p02 = pr.samples('sp_y', '_p02')
    touch(s08)
    touch(p02)
    detailed.write_sampled_record(p02, [(1, 1), (1, 2)], 0, 0)
    check(navregress.detailed_of(root, 'sp_y', s08, 0, 0, chosen=[(1, 1)]) is not None,
          '0.2 m samples covering the chosen sectors are used')
    for what, chosen, off in (('a chosen sector they do not cover', [(1, 1), (4, 4)], (0, 0)),
                              ('other sector offsets', [(1, 1)], (-3, -7))):
        try:
            navregress.detailed_of(root, 'sp_y', s08, off[0], off[1], chosen=chosen)
            check(False, f'stale 0.2 m samples are refused: {what}')
        except RuntimeError as e:
            check('stale' in str(e), f'stale 0.2 m samples are refused: {what}')
    os.remove(detailed.sampled_record_path(p02))            # a run from before the record: by time
    old = time.time() - 100
    os.utime(p02, (old, old))
    try:
        navregress.detailed_of(root, 'sp_y', s08, 0, 0, chosen=[(1, 1)])
        check(False, '0.2 m samples older than the 0.8 m ones are refused')
    except RuntimeError:
        check(True, '0.2 m samples older than the 0.8 m ones are refused')


def unpacking(root):
    pr = project(root)
    runs = []

    def fake_run(args, log):
        runs.append(os.path.basename(args[0]))
        if args[1] == 'unpack':
            touch(os.path.join(args[3], 'levels', 'a.dbx'))
        else:
            touch(os.path.join(args[2], 'levels', 'a.xml'))
        return ['converted 1 file']
    pr.run = fake_run
    dest = os.path.join(root, 'work', 'u')
    touch(os.path.join(dest, 'levels', 'a.dbx'))           # FbrbTool finished, DbxEdit did not
    touch(os.path.join(dest, 'levels', 'b.dbx'))
    pr.unpack('bundle.fbrb', dest, QUIET)
    check(runs == ['FbrbTool.exe', 'DbxEdit.exe'] and os.path.isfile(os.path.join(dest, navkit.UNPACKED))
          and not os.path.exists(os.path.join(dest, 'levels', 'b.dbx')),
          f'an unpack that did not finish is redone from an empty folder ({runs})')
    runs.clear()
    pr.unpack('bundle.fbrb', dest, QUIET)
    check(runs == [], 'a finished unpack is kept')
    legacy = os.path.join(root, 'work', 'legacy')
    touch(os.path.join(legacy, 'x', 'a.dbx'))
    touch(os.path.join(legacy, 'x', 'a.xml'))
    pr.unpack('bundle.fbrb', legacy, QUIET)
    check(runs == [] and os.path.isfile(os.path.join(legacy, navkit.UNPACKED)),
          'a complete unpack from before the marker is kept (every .dbx has its .xml)')


def game_log(root):
    pr = project(root)
    check(navkit.log_mark(pr) == (0, b'') and navkit.read_log(pr, navkit.log_mark(pr)) == [],
          'no game log yet: nothing read, no error')
    touch(pr.log_path, b'old line 1\nold line 2\nold line 3\n')
    mark = navkit.log_mark(pr)
    with open(pr.log_path, 'ab') as f:
        f.write(b'navsample: done\n')
    check(navkit.read_log(pr, mark) == ['navsample: done'], 'an appended log: only the new lines')
    touch(pr.log_path, b'navsample: done\n')                  # the game began a new, shorter log
    check(navkit.read_log(pr, mark) == ['navsample: done'], 'a new shorter log: read from its start')
    touch(pr.log_path, b'new start..\nnew line 2\nnavsample: done\n')   # a new log as long as the old one
    check('navsample: done' in navkit.read_log(pr, mark), 'a new log of the old size: read from its start')


def settings_whole(root):
    p = os.path.join(root, 'navkit.json')
    navkit.write_json(p, {'box': [0, 0, 1, 1]})
    try:
        navkit.write_json(p, {'box': object()})
    except TypeError:
        pass
    with open(p, encoding='utf-8') as f:
        check(json.load(f) == {'box': [0, 0, 1, 1]} and not os.path.exists(p + '.tmp'),
              'a write that fails leaves the previous settings whole')


def command_line(root):
    project(root)
    import io
    import contextlib
    for args, code, what in ((['build', 'sp_y', '--box'], 2, 'an option without its value'),
                             (['sample', 'sp_y', '--box', '1,2,3'], 2, '--box with three numbers'),
                             (['sample', 'sp_y', '--box', '5,0,1,10'], 2, '--box min above max'),
                             (['sample', 'sp_y', '--y', 'a,b'], 2, '--y not numbers')):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = navkit.main(['navkit.py'] + args + ['--opfor', root])
        check(rc == code, f'{what}: exit code {code} ({rc})')
    saved = navkit.sample_level
    navkit.sample_level = lambda *a, **k: False             # the user declined the game launches
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = navkit.main(['navkit.py', 'sample', 'sp_y', '--opfor', root])
        check(rc == 1, f'a declined sampling run: exit code 1 ({rc})')
    finally:
        navkit.sample_level = saved


def regress_save():
    rows = [{'level': 'sp_01', 'ground': 1}]
    existing = [{'level': 'sp_01', 'ground': 0}, {'level': 'sp_02', 'ground': 5}]
    merged = navregress.merged_rows(rows, existing) if hasattr(navregress, 'merged_rows') else rows
    check(merged == [{'level': 'sp_01', 'ground': 1}, {'level': 'sp_02', 'ground': 5}],
          'navregress --save over a subset keeps the saved run\'s other levels')
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        rc = navregress.main(['navregress.py', '--levels', 'sp_01', '--save'])
    check(rc == 2, f'navregress --save without a name: exit code 2 before any build ({rc})')


if __name__ == '__main__':
    running = navkit.game_running
    navkit.game_running = lambda: False                       # never look for, start or close the game here
    try:
        for test in (install_restore, apply_inputs, detailed_cache, stale_p02, unpacking, game_log, settings_whole,
                     command_line):
            tmp = tempfile.mkdtemp()
            try:
                test(tmp)
            except Exception as e:                            # noqa: BLE001 - one test's crash fails it, not the run
                check(False, f'{test.__name__}: {type(e).__name__}: {e}')
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        regress_save()
    finally:
        navkit.game_running = running
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
