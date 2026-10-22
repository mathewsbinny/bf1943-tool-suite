r"""Runs the suite's tests: one line per test, the failures' output at the end, exit code 1 if any failed.

  python run_tests.py [quick | game | full] [--only <name>] [--list]

  quick   no game data, about a minute: repository rules, DbxEdit unit tests, and on files built by the
          tests: FbrbTool, LevelDiff, the crash reader, the console readers (needs Windows for the C# tools)
  game    + Project OPFOR's game copy, about 10 minutes: FbrbTool on 12 game bundles, water on every level,
          the level loader against its snapshot, terrain against the in-game samples, path-data and mesh
          round trips, LevelDiff on MP_005, the crash reader's code view on BFBC2Game.exe, the console
          readers on your own Xbox 360 files (skipped when they are not on the PC), the 3D tools opening a
          level (needs a display), NavKit's regression test on two small levels
  full    + the corpus runs, about 90 minutes: DbxEdit and the Python DBX reader on every unpacked .dbx,
          FbrbTool on all 1,055 bundles, NavKit's regression test on all 13 campaign levels

Run `quick` before every commit and `game` before every push; `full` after changes to a format reader.
"""
import os
import subprocess
import sys
import time

SUITE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TIERS = ['quick', 'game', 'full']


def opfor():
    sys.path.insert(0, os.path.join(SUITE, 'bfbc2-auto-navkit'))
    import navkit
    return navkit.find_opfor(None)


def pathdata_files(root):
    """Every path-data file: the game copy's (with DICE's originals kept as pathdata.hpd.<suffix> where NavKit's
    is installed) and OPFOR's work folder's (Xbox Onslaught copies, NavKit's own builds)."""
    out = []
    for top in (os.path.join(root, 'game', 'Output', 'win32', 'ai'), os.path.join(root, 'work')):
        for dp, _, fs in os.walk(top):
            out += [os.path.join(dp, f) for f in fs if f.lower().startswith('pathdata.hpd')]
    return sorted(out)


def tests():
    """(name, tier, argv). Game paths are only looked up for the tiers that need them."""
    t = [
        ('repository rules', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_repo.py')]),
        ('DbxEdit unit tests', 'quick', ['cmd', '/c', os.path.join(SUITE, 'bfbc2-dbx-tools', 'tests', 'run_tests.bat')]),
        ('FbrbTool build', 'quick', ['cmd', '/c', os.path.join(SUITE, 'bfbc2-fbrb-tool', 'build.bat')]),
        ('FbrbTool round trip, built bundle', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_fbrb.py')]),
        ('error handling and logs', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_errors.py')]),
        ('every command-line tool reports errors', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_cli_errors.py')]),
        ('navigation side files (navtune)', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_navtune.py')]),
        ('NavKit water rule (ground too deep under water)', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_water_rule.py')]),
        ('NavKit walkability edits (navedits)', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_navedits.py')]),
        ('NavKit area link rule', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_area_link.py')]),
        ('NavKit regression progress estimates', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_regress_progress.py')]),
        ('NavKit boundary diagnostic (navboundary)', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_navboundary.py')]),
        ('NavKit portal symmetry', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_portal_symmetry.py')]),
        ('NavKit reference extent clip', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_reference_clip.py')]),
        ('NavKit soldiers absent from path data', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_characters.py')]),
        ('NavKit headroom band', 'quick', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_headroom.py')]),
        ('LevelDiff build', 'quick', ['cmd', '/c', os.path.join(SUITE, 'bfbc2-diff-workbench', 'level-diff', 'build.bat')]),
        ('LevelDiff, built levels', 'quick', [PY, os.path.join(SUITE, 'bfbc2-diff-workbench', 'level-diff', 'tests', 'test_leveldiff.py')]),
        ('crash reader, built reports', 'quick', [PY, os.path.join(SUITE, 'bfbc2-automated-map-and-bot-test-harness', 'crash-reader', 'tests', 'test_crashreader.py')]),
        ('console readers, built images', 'quick', [PY, os.path.join(SUITE, 'bfbc2-console-readers', 'tests', 'test_console_readers.py')]),
        ('LevelDiff on MP_005', 'game', [PY, os.path.join(SUITE, 'bfbc2-diff-workbench', 'level-diff', 'tests', 'test_leveldiff.py'), '--game']),
        ('crash reader code view (BFBC2Game.exe)', 'game', [PY, os.path.join(SUITE, 'bfbc2-automated-map-and-bot-test-harness', 'crash-reader', 'tests', 'test_crashreader.py'), '--game']),
        ('console readers on your Xbox files', 'game', [PY, os.path.join(SUITE, 'bfbc2-console-readers', 'tests', 'test_console_readers.py'), '--game']),
        ('FbrbTool round trip, 12 game bundles', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_fbrb.py'), '--game', '12']),
        ('water, every level', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_water.py')]),
        ('level loader snapshot', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_level.py')]),
        ('terrain against in-game samples', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_terrain.py')]),
        ('path data round trip (hpdtool)', 'game', lambda root: [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tools', 'hpdtool.py'),
                                                               'roundtrip', *pathdata_files(root)]),
        ('navigation mesh round trip (kymesh)', 'game', lambda root: [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tools', 'hpdtool.py'),
                                                                    'meshtrip', *pathdata_files(root)]),
        ('navigation data and the path test', 'game', [PY, os.path.join(SUITE, 'core', 'tests', 'test_navdata.py')]),
        ('NavKit links and route edges', 'game', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_links.py')]),
        ('traversals, the apply report, installing', 'game', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_traversal.py')]),
        ('navkit apply with walkability edits', 'game', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tests', 'test_apply.py')]),
        ('Navigation Studio edit mode', 'game', [PY, os.path.join(SUITE, 'bfbc2-navigation-studio', 'tests', 'test_editing.py')]),
        ('the suite window (hub) and the level picker', 'game', [PY, os.path.join(SUITE, 'tests', 'test_hub.py')]),
        ('3D tools open and draw (Map Inspector, Navigation Studio)', 'game', [PY, os.path.join(SUITE, 'tests', 'test_viewers.py')]),
        ('NavKit regression, 2 small levels', 'game', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tools', 'navregress.py'),
                                                       '--levels', 'sp_004_b,sp_009']),
        ('NavKit regression, all 13 campaign levels', 'full', [PY, os.path.join(SUITE, 'bfbc2-auto-navkit', 'tools', 'navregress.py')]),
        ('DbxEdit corpus and fingerprints', 'full', lambda root: ['cmd', '/c', os.path.join(SUITE, 'bfbc2-dbx-tools', 'tests', 'run_tests.bat'),
                                                                 os.path.join(root, 'work', 'levels'), os.path.join(root, 'work', 'navkit', 'common')]),
        ('Python DBX reader against DbxEdit', 'full', lambda root: [PY, os.path.join(SUITE, 'core', 'tests', 'test_dbx.py'),
                                                                   os.path.join(root, 'work', 'levels'), os.path.join(root, 'work', 'navkit', 'common')]),
        ('FbrbTool round trip, every game bundle', 'full', [PY, os.path.join(SUITE, 'core', 'tests', 'test_fbrb.py'), '--game', '0']),
    ]
    return t


def main(argv):
    tier = next((a for a in argv[1:] if a in TIERS), 'quick')
    only = argv[argv.index('--only') + 1].lower() if '--only' in argv else None
    chosen = [x for x in tests() if TIERS.index(x[1]) <= TIERS.index(tier) and (only is None or only in x[0].lower())]
    if '--list' in argv:
        for name, t, _ in chosen:
            print(f'{t:6s} {name}')
        return 0
    root = opfor() if any(callable(a) or t != 'quick' for _, t, a in chosen) else None
    print(f'BFBC2 Tool Suite tests: {tier}, {len(chosen)} test(s)' + (f', OPFOR at {root}' if root else ''))
    failures, start = [], time.time()
    for name, t, args in chosen:
        if callable(args):
            args = args(root)
        t0 = time.time()
        r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=SUITE)
        out = (r.stdout + r.stderr).strip()
        main_out = r.stdout.strip() or out            # the summary line is the test's own last line, not a warning it printed
        last = main_out.splitlines()[-1] if main_out else ''
        ok = r.returncode == 0
        print(f'{"ok  " if ok else "FAIL"} {name:42s} {time.time() - t0:6.1f} s   {last[:90]}', flush=True)
        if not ok:
            failures.append((name, out))
    for name, out in failures:
        print(f'\n--- {name} (last 25 lines) ---')
        print('\n'.join(out.splitlines()[-25:]))
    print(f'\n{len(chosen) - len(failures)} of {len(chosen)} passed in {time.time() - start:.0f} s')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
    from bfbc2core import errors
    errors.setup('run-tests', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
