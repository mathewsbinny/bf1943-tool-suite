r"""Runs the suite's tests: one line per test, the failures' output at the end, exit code 1 if any failed.

  python run_tests.py [quick | bf1943] [--only <name>] [--list] [--strict]

A test that skips (its data is missing: a line starting "skip") is shown as SKIP, one that skipped some of its
checks as "ok, partly skipped", and both are counted in the summary: a passing run is not full coverage unless the
summary says nothing was skipped. --strict fails the run when anything was skipped (use it before a push). An
--only that matches no test is an error, not "0 of 0 passed"; an unknown tier or option is a usage error (exit 2).

Every test is about BF1943 or the tools that work on it; tests for BFBC2 data and for the copied tools not yet
adapted to BF1943 are not kept (a tool gets its tests when it is adapted). Tests are grouped by the data they need:
  quick   no game data, one to two minutes: repository rules, error handling and every command-line tool's errors,
          the DBX and water mesh readers on damaged files, the suite window on a made-up game, the RSNV file (both byte orders, search, locate, damaged files), the STFS
          reader on packages it builds, the Xenia kit's patch file, assembler and bot code, NavKit's builder rules
          (both variants; the little-endian one's edge cases, damaged files, navkit.py steps and window Cancel too),
          the spawn map's --navflags, the Navigation Studio's colours and options, the 3D tools' screenshot exit
          code, the offline sampler on a made-up level, rsnvtool on a made-up file, jump links to a
          made-up ship's gun platform, a full build of a made-up level with a 0.8 m / 0.2 m seam, vehicle navigation
          (ground and sea grids, trips, heading-aware planning, take-off lanes), the navigation reader's heights,
          the Xenia run reader on a log it writes, the executable tools' workspace lookup and vector p-code generator,
          the runner's own command line, the OPFOR import tool's updates and partial runs, the bundle tool's write guards, APT
          validation's exit code, and the C# tools (rebuilt from their current sources; DbxEdit xml, LevelDiff) on
          made-up files
  bf1943  + your BF1943 copy, as bf1943-xenia-kit extracts it (BF1943_GAME overrides), about 5 minutes: the kit's
          full build, every BF1943 bundle and DBX partition, terrain and water on every level, the menu movies, the
          spawn map, Havok 4.6.1 collision, Wake Island's objects for NavKit; the level loader on every map, DbxEdit
          and LevelDiff on BF1943 partitions, DbxEdit's BF1943 level check (shipped levels pass, broken copies fail),
          the bundle tool's command line, NavKit's prepare step, the hub, and the
          3D tools (the Navigation Studio when a Wake Island build exists), the enclosure probe on Wake Island's
          samples, the RSNV export of your builds (skipped without a Wake Island build), the executable tools from a
          research workspace (skipped without one)

--only <part of a name> runs the matching tests; --list lists them without running.
Run `quick` before every commit and `bf1943 --strict` before every push.
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
        ('navigation reader: cell heights use the node offset; the cache follows the terrain', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_navdata_offset.py')]),
        ('game loader: a damaged level bundle raises, never the shared copy', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_game1943_bundles.py')]),
        ('vehicle entry points: cached per game copy, never by id (made-up blueprints)', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_vehicles1943.py')]),
        ('DBX reader: truncated and damaged files refused (parse and XML)', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_dbx_malformed.py')]),
        ('water mesh reader: triangles past their vertices refused', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_water_mesh.py')]),
        ('RSNV navigation file: both byte orders, search, locate, damaged files', 'quick', [PY, os.path.join(SUITE, 'core', 'tests', 'test_rsnv.py')]),
        ('every command-line tool reports errors', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_cli_errors.py')]),
        ('the test runner: unknown tiers and options refused', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_run_tests.py')]),
        ('OPFOR import tool: updates on made-up trees', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_import_opfor.py')]),
        ('the suite window: pane errors, LevelDiff codes, UTF-8 output, Stop (made-up game)', 'quick', [PY, os.path.join(SUITE, 'tests', 'test_hub.py')]),
        ('STFS reader, built and damaged packages', 'quick', [PY, os.path.join(SUITE, 'bf1943-console-readers', 'tests', 'test_console_readers.py')]),
        ('bundle tool: game folder and input never written (made-up bundle)', 'quick', [PY, os.path.join(SUITE, 'bf1943-fbrb-tool', 'tests', 'test_fbrb_guards.py')]),
        ('APT validation: exit code (made-up movie)', 'quick', [PY, os.path.join(SUITE, 'bf1943-apt-tools', 'tests', 'test_apt_cli.py')]),
        ('C# tools: rebuilt from current sources; DbxEdit xml exit code, LevelDiff reorders', 'quick', [PY, os.path.join(SUITE, 'bf1943-dbx-tools', 'tests', 'test_csharp_tools.py')]),
        ('Xenia run reader, a written log', 'quick', [PY, os.path.join(SUITE, 'bf1943-automated-map-and-bot-test-harness', 'xenia-run', 'tests', 'test_xeniarun.py')]),
        ('Xenia guest memory: bots mailbox v2 on a made-up guest', 'quick', [PY, os.path.join(SUITE, 'bf1943-automated-map-and-bot-test-harness', 'xenia-run', 'tests', 'test_guestmem.py')]),
        ('executable tools: research workspace lookup', 'quick', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_workspace.py')]),
        ('executable tools: Xenon vector p-code generator', 'quick', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_vector_semantics.py')]),
        ('executable tools: call graph forgets overwritten stores', 'quick', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_callgraph_memory.py')]),
        ('executable tools: Ghidra mapping judged by its result', 'quick', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_map_project.py')]),
        ('executable tools: VMX recorder checks what the fingerprint misses', 'quick', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_record_vmx.py')]),
        ('Xenia UDP join probe never holds the server port', 'quick', [PY, os.path.join(SUITE, 'bf1943-automated-map-and-bot-test-harness', 'xenia-run', 'tests', 'test_udp_join_probe.py')]),
        ('Xenia probe launcher on a made-up build', 'quick', [PY, os.path.join(SUITE, 'bf1943-automated-map-and-bot-test-harness', 'xenia-run', 'tests', 'test_run_xenia_probe.py')]),
        ('Xenia kit: patch file, port edit, Xenia fix', 'quick', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_xeniakit.py')]),
        ('Xenia kit: PowerPC assembler against capstone', 'quick', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_ppcasm.py')]),
        ('Xenia kit: bots experiment layout', 'quick', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_bots.py')]),
        ('NavKit big-endian: offline sampler (made-up level)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_sampler1943.py')]),
        ('NavKit big-endian: rsnvtool (RSNV inspector) on a made-up file', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_rsnvtool.py')]),
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
        ('NavKit big-endian: vehicle navigation (ground grid, sea grid, trips)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_vehicles_nav1943.py')]),
        ('NavKit big-endian: steps keep to one preparation (stamps, pitches, game copy)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_navkit_stages.py')]),
        ('NavKit big-endian: vehicle steps keep to one preparation (stamps, game copy, absent worlds, no water)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_vehicle_stages.py')]),
        ('NavKit big-endian: review fixes (navkit.json lock, stale samples and masks, arguments, failed checks)', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_navkit_review.py')]),
        ('NavKit big-endian: route check passes only when something was checked', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_routecheck1943.py')]),
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
        ('NavKit little-endian: builder edge cases', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_navbuild_edges.py')]),
        ('NavKit little-endian: damaged files', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_formats.py')]),
        ('NavKit little-endian: navkit.py steps', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_navkit_driver.py')]),
        ('NavKit little-endian: window Cancel', 'quick', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-little-endian', 'tests', 'test_navkit_gui.py')]),
        ('Spawn map: --navflags on made-up maps', 'quick', [PY, os.path.join(SUITE, 'bf1943-spawn-map', 'tests', 'test_spawnmap.py'), '--made-up']),
        ('Navigation Studio: colours and options', 'quick', [PY, os.path.join(SUITE, 'bf1943-navigation-studio', 'tests', 'test_studio.py')]),
        ('Map Inspector: screenshot exit code', 'quick', [PY, os.path.join(SUITE, 'bf1943-map-inspector', 'tests', 'test_inspector.py')]),
        ('Xenia kit: full build from your BF1943 package', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_xeniakit.py'), '--game']),
        ('Xenia kit: bots code against the decoded BF1943 image', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-xenia-kit', 'tests', 'test_bots.py'), '--image']),
        ('executable tools: lis pairs end when the register is written', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_xexrefs.py')]),
        ('executable tools: pinned outputs written byte for byte', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-xex-tools', 'tests', 'test_output_bytes.py')]),
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
        ('DbxEdit level check: BF1943 rules', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-dbx-tools', 'tests', 'test_levelcheck1943.py')]),
        ('Bundle tool command line', 'bf1943', [PY, tools, 'fbrb']),
        ('NavKit big-endian: navkit.py prepare', 'bf1943', [PY, tools, 'navkit']),
        ('the suite window (hub)', 'bf1943', [PY, tools, 'hub']),
        ('3D tools: Map Inspector, Navigation Studio', 'bf1943', [PY, tools, 'viewers']),
        ('NavKit big-endian: enclosure probe on Wake Island', 'bf1943', [PY, tools, 'probe']),
        ('NavKit big-endian: RSNV export of your builds', 'bf1943', [PY, os.path.join(SUITE, 'bf1943-auto-navkit-big-endian', 'tests', 'test_navexport1943.py')]),
        ('executable tools from the research workspace', 'bf1943', [PY, tools, 'xex']),
    ]


def main(argv):
    if '--only' in argv and argv.index('--only') + 1 >= len(argv):
        # 2026-10-30: an IndexError before; a usage error like any other
        print('error: --only needs a part of a test\'s name')
        print(f'usage: python run_tests.py [{" | ".join(TIERS)}] [--only <name>] [--list] [--strict]')
        return 2
    only = argv[argv.index('--only') + 1].lower() if '--only' in argv else None
    rest = list(argv[1:])
    if '--only' in rest:
        del rest[rest.index('--only'):rest.index('--only') + 2]       # its value is a name, not a tier
    tiers = [a for a in rest if a in SELECT]
    unknown = [a for a in rest if a not in SELECT and a not in ('--list', '--strict')]
    if unknown or len(tiers) > 1:
        # a misspelled tier must not quietly run another one (and pass)
        print(f'error: {"unknown tier or option " + repr(unknown[0]) if unknown else "more than one tier: " + " ".join(tiers)}')
        print(f'usage: python run_tests.py [{" | ".join(TIERS)}] [--only <name>] [--list] [--strict]')
        return 2
    tier = tiers[0] if tiers else 'quick'
    chosen = [x for x in tests() if x[1] in SELECT[tier] and (only is None or only in x[0].lower())]
    if only is not None and not chosen:
        print(f'error: --only {only!r} matches no {tier} test (see --list)')
        return 1
    if '--list' in argv:
        for name, t, _ in chosen:
            print(f'{t:6s} {name}')
        return 0
    strict = '--strict' in argv
    print(f'BF1943 Tool Suite tests: {tier}, {len(chosen)} test(s)' + (' (strict: a skip fails)' if strict else ''))
    failures, skipped, partly, start = [], [], [], time.time()
    for name, t, args in chosen:
        t0 = time.time()
        r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=SUITE,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))   # no __pycache__, their children too
        out = (r.stdout + r.stderr).strip()
        main_out = r.stdout.strip() or out            # the summary line is the test's own last line, not a warning it printed
        lines = main_out.splitlines()
        last = lines[-1] if lines else ''
        skips = [ln for ln in lines if ln.lower().startswith('skip')]
        if r.returncode != 0:
            status = 'FAIL'
            failures.append((name, out))
        elif skips and last.lower().startswith('skip'):
            status = 'SKIP'                                # the whole test: its data is missing
            skipped.append((name, last))
        elif skips:
            status = 'ok* '                                # passed, but some checks were skipped
            partly.append((name, skips))
        else:
            status = 'ok  '
        print(f'{status} {name:42s} {time.time() - t0:6.1f} s   {last[:90]}', flush=True)
    for name, out in failures:
        print(f'\n--- {name} (last 25 lines) ---')
        print('\n'.join(out.splitlines()[-25:]))
    if skipped or partly:
        print('\nnot fully run:')
        for name, why in skipped:
            print(f'  SKIP {name}: {why}')
        for name, why in partly:
            print(f'  ok*  {name}: ' + '; '.join(why))
    passed = len(chosen) - len(failures) - len(skipped)
    print(f'\n{passed} of {len(chosen)} passed' + (f', {len(skipped)} skipped' if skipped else '')
          + (f', {len(partly)} partly skipped' if partly else '') + f' in {time.time() - start:.0f} s')
    return 1 if failures or (strict and (skipped or partly)) else 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core'))
    from bfbc2core import errors
    errors.setup('run-tests', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
