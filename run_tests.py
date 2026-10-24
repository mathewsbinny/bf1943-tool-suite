r"""Runs the suite's tests: one line per test, the failures' output at the end, exit code 1 if any failed.

  python run_tests.py [quick | bf1943] [--only <name>] [--list]

Every test is about BF1943 or the tools that work on it; tests for BFBC2 data and for the copied tools not yet
adapted to BF1943 are not kept (a tool gets its tests when it is adapted). Tests are grouped by the data they need:
  quick   no game data, about a minute: repository rules, error handling, the STFS reader on packages it builds,
          the Xenia kit's patch file, assembler and bot code, NavKit's builder rules (both variants), the offline
          sampler on a made-up level, jump links to a made-up ship's gun platform, a full build of a made-up level
          with a 0.8 m / 0.2 m seam, the navigation reader's heights, the Xenia run reader on a log it writes, the
          executable tools' workspace lookup
  bf1943  + your BF1943 copy, as bf1943-xenia-kit extracts it (BF1943_GAME overrides), about 5 minutes: the kit's
          full build, every BF1943 bundle and DBX partition, terrain and water on every level, the menu movies, the
          spawn map, Havok 4.6.1 collision, Wake Island's objects for NavKit; the level loader on every map, DbxEdit
          and LevelDiff on BF1943 partitions, the bundle tool's command line, NavKit's prepare step, the hub, and the
          3D tools (the Navigation Studio when a Wake Island build exists), the enclosure probe on Wake Island's
          samples, the executable tools from a research workspace (skipped without one)

Run `quick` before every commit and `bf1943` before every push.
"""
import os
import subprocess
import sys
import time

SUITE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TIERS = ['quick', 'bf1943']
# what each tier runs: tests are tagged by the data they need, not by how long they take
SELECT = {'quick': {'quick'}, 'bf1943': {'quick', 'bf1943'}}


def tests():
    """(name, tier, argv)."""
    data = os.path.join(SUITE, 'tests', 'test_bf1943_data.py')
    tools = os.path.join(SUITE, 'tests', 'test_bf1943_tools.py')
    return [
        ('repository rules', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_repo.py')]),
        ('error handling and logs', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_errors.py')]),
        ('navigation reader: cell heights use the node offset', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_navdata_offset.py')]),
        ('RSNV navigation file: both byte orders, search, locate, damaged files', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_rsnv.py')]),
        ('every command-line tool reports errors', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_cli_errors.py')]),
        ('STFS reader, built packages', 'quick', [PY, os.path.join(SUITE, 'bf1943-console-readers', 'tests', 'test_console_readers.py')]),
        ('Xenia run reader, a written log', 'quick', [PY, os.path.join(SUITE, 'bf1943-automated-map-and-bot-test-harness', 'xenia-run', 'tests', 'test_xeniarun.py')]),
        ('executable tools: research workspace lookup', 'quick', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_workspace.py')]),
        ('Xenia kit: patch file, port edit, Xenia fix', 'quick', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_xeniakit.py')]),
        ('Xenia kit: PowerPC assembler against capstone', 'quick', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_ppcasm.py')]),
        ('Xenia kit: bots experiment layout', 'quick', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_bots.py')]),
        ('NavKit big-endian: offline sampler (made-up level)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_sampler1943.py')]),
        ('NavKit big-endian: jump links to ships\' AA gun platforms', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_jumplinks1943.py')]),
        ('NavKit big-endian: seams between 0.8 m and 0.2 m sectors', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_mixed_seam.py')]),
        ('NavKit big-endian: water rule (ground too deep under water)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_water_rule.py')]),
        ('NavKit big-endian: walkability edits', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_navedits.py')]),
        ('NavKit big-endian: area link rule', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_area_link.py')]),
        ('NavKit big-endian: portal symmetry', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_portal_symmetry.py')]),
        ('NavKit big-endian: soldiers absent from samples', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_characters.py')]),
        ('NavKit big-endian: headroom band', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_headroom.py')]),
        ('NavKit big-endian: 45-degree bridge outlines', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_bridge_union.py')]),
        ('NavKit big-endian: dynamic props and doors', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_dynamic_props.py')]),
        ('NavKit big-endian: collision index', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_havok_index.py')]),
        ('NavKit big-endian: narrow lips and sills', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_lips.py')]),
        ('NavKit big-endian: roof pruning', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_roof_prune.py')]),
        ('NavKit little-endian: water rule (ground too deep under water)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_water_rule.py')]),
        ('NavKit little-endian: walkability edits', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_navedits.py')]),
        ('NavKit little-endian: area link rule', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_area_link.py')]),
        ('NavKit little-endian: portal symmetry', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_portal_symmetry.py')]),
        ('NavKit little-endian: soldiers absent from samples', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_characters.py')]),
        ('NavKit little-endian: headroom band', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_headroom.py')]),
        ('NavKit little-endian: 45-degree bridge outlines', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_bridge_union.py')]),
        ('NavKit little-endian: dynamic props and doors', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_dynamic_props.py')]),
        ('NavKit little-endian: collision index', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_havok_index.py')]),
        ('NavKit little-endian: narrow lips and sills', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_lips.py')]),
        ('NavKit little-endian: roof pruning', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_roof_prune.py')]),
        ('Xenia kit: full build from your BF1943 package', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_xeniakit.py'), '--game']),
        ('BF1943 bundles: read and rebuild (fbrb1943)', 'bf1943', [PY, data, 'bundles']),
        ('BF1943 DBX: every partition parses', 'bf1943', [PY, data, 'dbx']),
        ('BF1943 terrain and water, every level', 'bf1943', [PY, data, 'terrain']),
        ('BF1943 menu movies (apt)', 'bf1943', [PY, data, 'apt']),
        ('Spawn map: every level bases and spawns', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-spawn-map', 'tests', 'test_spawnmap.py')]),
        ('NavKit big-endian: Wake Island objects', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_levelobjects1943.py')]),
        ('NavKit big-endian: Havok 4.6.1 meshes', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_havok461.py')]),
        ('NavKit little-endian: Havok 4.6.1 meshes', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_havok461.py')]),
        ('Level loader: every multiplayer map', 'bf1943', [PY, tools, 'level']),
        ('DbxEdit and LevelDiff on BF1943 partitions', 'bf1943', [PY, tools, 'dbxedit']),
        ('Bundle tool command line', 'bf1943', [PY, tools, 'fbrb']),
        ('NavKit big-endian: navkit.py prepare', 'bf1943', [PY, tools, 'navkit']),
        ('the suite window (hub)', 'bf1943', [PY, tools, 'hub']),
        ('3D tools: Map Inspector, Navigation Studio', 'bf1943', [PY, tools, 'viewers']),
        ('NavKit big-endian: enclosure probe on Wake Island', 'bf1943', [PY, tools, 'probe']),
        ('NavKit big-endian: RSNV export of your builds', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_navexport1943.py')]),
        ('executable tools from the research workspace', 'bf1943', [PY, tools, 'xex']),
    ]


def main(argv):
    tier = next((a for a in argv[1:] if a in SELECT), 'quick')
    only = argv[argv.index('--only') + 1].lower() if '--only' in argv else None
    chosen = [x for x in tests() if x[1] in SELECT[tier] and (only is None or only in x[0].lower())]
    if '--list' in argv:
        for name, t, _ in chosen:
            print(f'{t:6s} {name}')
        return 0
    print(f'BF1943 Tool Suite tests: {tier}, {len(chosen)} test(s)')
    failures, start = [], time.time()
    for name, t, args in chosen:
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
