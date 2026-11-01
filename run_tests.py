r"""Runs the suite's tests: one line per test, the failures' output at the end, exit code 1 if any failed.

  python run_tests.py [quick | bf1943] [--only <name>] [--list] [--strict]

A test that skips (its data is missing: a line starting "skip") is shown as SKIP, one that skipped some of its
checks as "ok, partly skipped", and both are counted in the summary: a passing run is not full coverage unless the
summary says nothing was skipped. --strict fails the run when anything was skipped (use it before a push). An
--only that matches no test is an error, not "0 of 0 passed"; an unknown tier or option is a usage error (exit 2).

Every test is about BF1943 or the tools that work on it; tests for BFBC2 data and for the copied tools not yet
adapted to BF1943 are not kept (a tool gets its tests when it is adapted). Tests are grouped by the data they need:
  quick   no game data, one to two minutes: repository rules, error handling and every command-line tool's errors,
          the DBX, water mesh, material map and height field readers on damaged files, the suite window on a
          made-up game, the RSNV file (both byte orders, search, locate, damaged files), the STFS
          reader on packages it builds, the Xenia kit's patch file, assembler and bot code, NavKit's builder rules
          (the shared modules both variants use: rules, edge cases, damaged files; the little-endian navkit_le.py's
          steps and window Cancel),
          the spawn map's --navflags, the Navigation Studio's colours and options, the 3D tools' screenshot exit
          code, the offline sampler on a made-up level, rsnvtool on a made-up file, jump links to a
          made-up ship's gun platform, a full build of a made-up level with a 0.8 m / 0.2 m seam, vehicle navigation
          (ground and sea grids, trips, heading-aware planning, take-off lanes), the navigation reader's heights,
          the Xenia run reader on a log it writes, the executable tools' workspace lookup and vector p-code generator,
          the runner's own command line, the OPFOR import tool's updates and partial runs, the bundle tool's write
          guards, APT validation's exit code, and the C# tools (rebuilt from their current sources; DbxEdit xml,
          LevelDiff) on
          made-up files
  bf1943  + your BF1943 copy, as bf1943-xenia-kit extracts it (BF1943_GAME overrides), about 5 minutes: the kit's
          full build, every BF1943 bundle and DBX partition, terrain and water on every level, the menu movies, the
          spawn map, Havok 4.6.1 collision, Wake Island's objects for NavKit; the level loader on every map, DbxEdit
          and LevelDiff on BF1943 partitions, DbxEdit's BF1943 level check (shipped levels pass, broken copies fail),
          the bundle tool's command line, NavKit's prepare step, the hub, and the
          3D tools (the Navigation Studio when a Wake Island build exists), the enclosure probe on Wake Island's
          samples, the RSNV export of your builds (skipped without a Wake Island build), the executable tools from a
          research workspace (skipped without one; the VMX recorder's checks too: they copy its checked image)

--only <part of a name> runs the matching tests; --list lists them without running.
Run `quick` before every commit and `bf1943 --strict` before every push.

Two kinds of test (README "Tests"): a script, listed in scripts() below, which prints a line per check and a summary
line; and a pytest-style file (test_*.py with plain `def test_...():` functions and no `__main__` block), which needs
no listing: the runner finds it (function_tests()), runs it with tests/functions.py (no pytest needed) and names it
by its docstring's first line, in the quick tier unless it sets TIER = 'bf1943'. `python -B -m pytest` runs both
kinds as well (conftest.py, tests/test_suite_tiers.py).
"""
import ast
import os
import subprocess
import sys
import time

# a test: (name, tier, the command that runs it)
type Test = tuple[str, str, list[str]]

SUITE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TIERS = ['quick', 'bf1943']
# what each tier runs: tests are tagged by the data they need, not by how long they take
SELECT = {'quick': {'quick'}, 'bf1943': {'quick', 'bf1943'}}
# runs a pytest-style file's test functions without pytest
FUNCTIONS = os.path.join(SUITE, 'tests', 'functions.py')
# pytest-style in form but for pytest only: the runner's tests, run through pytest (running it here would run all twice)
PYTEST_ONLY = {os.path.normcase(os.path.join(SUITE, 'tests', 'test_suite_tiers.py'))}
# as pytest's norecursedirs
SKIP_DIRS = {'.git', 'work', 'output', '__pycache__', 'docs', 'provenance', 'research', '.pytest_cache'}
USAGE = f'usage: python run_tests.py [{" | ".join(TIERS)}] [--only <name>] [--list] [--strict]'


def tests() -> list[Test]:
    """(name, tier, argv): the scripts, then the pytest-style files."""
    return scripts() + function_tests()


def function_tests() -> list[Test]:
    """(name, tier, [PY, FUNCTIONS, path]) of each pytest-style test file under the suite (function_test()), sorted
    by path."""
    found: list[Test] = []
    for folder, dirs, files in os.walk(SUITE):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
        for f in sorted(files):
            path = os.path.join(folder, f)
            test = function_test(path)
            if test:
                found.append((*test, [PY, FUNCTIONS, path]))
    return found


def function_test(path: str) -> tuple[str, str] | None:
    """(name, tier) when the file is a pytest-style test file: test_*.py with a top-level `def test...` and no
    `if __name__ == '__main__':` block (that is a script), not in PYTEST_ONLY; else None. Named by its docstring's
    first line (else its path), in the tier its module-level TIER names (default 'quick')."""
    name = os.path.basename(path)
    is_test_file = name.startswith('test_') and name.endswith('.py')
    if not is_test_file or os.path.normcase(os.path.abspath(path)) in PYTEST_ONLY:
        return None
    with open(path, encoding='utf-8') as source:
        tree = ast.parse(source.read(), path)
    body = tree.body
    if any(isinstance(n, ast.If) and ast.unparse(n.test) == "__name__ == '__main__'" for n in body):
        return None
    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith('test') for n in body):
        return None
    tier = next((n.value.value for n in body if isinstance(n, ast.Assign) and len(n.targets) == 1
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'TIER'
                 and isinstance(n.value, ast.Constant)), 'quick')
    if tier not in TIERS:
        raise ValueError(f'{path}: TIER = {tier!r}, not a tier ({", ".join(TIERS)})')
    doc = (ast.get_docstring(tree) or '').strip()
    return (doc.splitlines()[0].rstrip('.:') if doc else os.path.relpath(path, SUITE)), tier


# the folders whose tests\ the script tests are in (script())
CORE = 'core'
NAVKIT_LE = 'bf1943-auto-navkit-little-endian'
NAVKIT_BE = 'bf1943-auto-navkit-big-endian'
NAVKIT_COMMON = 'bf1943-auto-navkit-common'
XENIA_KIT = 'bf1943-xenia-kit'
XENIA_RUN = os.path.join('bf1943-automated-map-and-bot-test-harness', 'xenia-run')
XEX_TOOLS = 'bf1943-xex-tools'


def script(name: str, tier: str, folder: str, file: str, *args: str) -> Test:
    """(name, tier, argv) of a script test: <folder>\\tests\\<file> run with args (folder '': the suite's own
    tests\\)."""
    return name, tier, [PY, os.path.join(SUITE, folder, 'tests', file), *args]


def scripts() -> list[Test]:
    """(name, tier, argv) of the script tests."""
    return [
        script('repository rules', 'quick', '', 'test_repo.py'),
        script('error handling and logs', 'quick', CORE, 'test_errors.py'),
        script('navigation reader: cell heights use the node offset; the cache follows the terrain', 'quick',
               CORE, 'test_navdata_offset.py'),
        script('game loader: a damaged level bundle raises, never the shared copy', 'quick',
               CORE, 'test_game1943_bundles.py'),
        script('vehicle entry points: cached per game copy, never by id (made-up blueprints)', 'quick',
               CORE, 'test_vehicles1943.py'),
        script('DBX reader: truncated and damaged files refused (parse and XML)', 'quick',
               CORE, 'test_dbx_malformed.py'),
        script('core readers: damaged material maps and height fields refused; path test finds the cheapest route; '
               'sys.path kept', 'quick', CORE, 'test_readers_malformed.py'),
        script('RSNV navigation file: both byte orders, search, locate, damaged files', 'quick', CORE, 'test_rsnv.py'),
        script('every command-line tool reports errors', 'quick', '', 'test_cli_errors.py'),
        script('the test runner: unknown tiers and options refused', 'quick', '', 'test_run_tests.py'),
        script('OPFOR import tool: updates on made-up trees', 'quick', '', 'test_import_opfor.py'),
        script('the suite window: pane errors, LevelDiff codes, UTF-8 output, Stop (made-up game)', 'quick',
               '', 'test_hub.py'),
        script('STFS reader, built and damaged packages', 'quick', 'bf1943-console-readers', 'test_console_readers.py'),
        script('bundle tool: game folder and input never written (made-up bundle)', 'quick',
               'bf1943-fbrb-tool', 'test_fbrb_guards.py'),
        script('APT validation: exit code (made-up movie)', 'quick', 'bf1943-apt-tools', 'test_apt_cli.py'),
        script('C# tools: rebuilt from current sources; DbxEdit xml exit code, LevelDiff reorders', 'quick',
               'bf1943-dbx-tools', 'test_csharp_tools.py'),
        script('Xenia run reader, a written log', 'quick', XENIA_RUN, 'test_xeniarun.py'),
        script('Xenia guest memory: bots mailbox v2 on a made-up guest', 'quick', XENIA_RUN, 'test_guestmem.py'),
        script('executable tools: research workspace lookup', 'quick', XEX_TOOLS, 'test_workspace.py'),
        script('executable tools: Xenon vector p-code generator', 'quick', XEX_TOOLS, 'test_vector_semantics.py'),
        script('executable tools: call graph forgets overwritten stores', 'quick',
               XEX_TOOLS, 'test_callgraph_memory.py'),
        script('executable tools: Ghidra mapping judged by its result', 'quick', XEX_TOOLS, 'test_map_project.py'),
        script('Xenia UDP join probe never holds the server port', 'quick', XENIA_RUN, 'test_udp_join_probe.py'),
        script('Xenia probe launcher on a made-up build', 'quick', XENIA_RUN, 'test_run_xenia_probe.py'),
        script('Xenia kit: patch file, port edit, Xenia fix', 'quick', XENIA_KIT, 'test_xeniakit.py'),
        script('Xenia kit: PowerPC assembler against capstone', 'quick', XENIA_KIT, 'test_ppcasm.py'),
        script('Xenia kit: bots experiment layout', 'quick', XENIA_KIT, 'test_bots.py'),
        script('NavKit big-endian: offline sampler (made-up level)', 'quick', NAVKIT_BE, 'test_sampler1943.py'),
        script('NavKit big-endian: rsnvtool (RSNV inspector) on a made-up file', 'quick',
               NAVKIT_BE, 'test_rsnvtool.py'),
        script('NavKit big-endian: jump links to ships\' AA gun platforms', 'quick',
               NAVKIT_BE, 'test_jumplinks1943.py'),
        script('NavKit big-endian: seams between 0.8 m and 0.2 m sectors', 'quick', NAVKIT_BE, 'test_mixed_seam.py'),
        script('NavKit (both variants): water rule (ground too deep under water)', 'quick',
               NAVKIT_COMMON, 'test_water_rule.py'),
        script('NavKit (both variants): walkability edits', 'quick', NAVKIT_COMMON, 'test_navedits.py'),
        script('NavKit (both variants): area link rule', 'quick', NAVKIT_COMMON, 'test_area_link.py'),
        script('NavKit (both variants): portal symmetry', 'quick', NAVKIT_COMMON, 'test_portal_symmetry.py'),
        script('NavKit (both variants): soldiers absent from samples', 'quick', NAVKIT_COMMON, 'test_characters.py'),
        script('NavKit (both variants): 45-degree bridge outlines', 'quick', NAVKIT_COMMON, 'test_bridge_union.py'),
        script('NavKit (both variants): dynamic props and doors', 'quick', NAVKIT_COMMON, 'test_dynamic_props.py'),
        script('NavKit (both variants): narrow lips and sills', 'quick', NAVKIT_COMMON, 'test_lips.py'),
        script('NavKit (both variants): roof pruning', 'quick', NAVKIT_COMMON, 'test_roof_prune.py'),
        script('NavKit (both variants): builder edge cases (default settings)', 'quick',
               NAVKIT_COMMON, 'test_navbuild_edges.py'),
        script('NavKit (both variants): damaged files', 'quick', NAVKIT_COMMON, 'test_formats.py'),
        script('NavKit big-endian: vehicle navigation (ground grid, sea grid, trips)', 'quick',
               NAVKIT_BE, 'test_vehicles_nav1943.py'),
        script('NavKit big-endian: steps keep to one preparation (stamps, pitches, game copy)', 'quick',
               NAVKIT_BE, 'test_navkit_stages.py'),
        script('NavKit big-endian: vehicle steps keep to one preparation (stamps, game copy, absent worlds, no water)',
               'quick', NAVKIT_BE, 'test_vehicle_stages.py'),
        script('NavKit big-endian: review fixes (navkit.json lock, stale samples and masks, arguments, failed checks)',
               'quick', NAVKIT_BE, 'test_navkit_review.py'),
        script('NavKit big-endian: second review fixes (OS lock, world stamps, jump links stamp, failed exports aside)',
               'quick', NAVKIT_BE, 'test_navkit_review2.py'),
        script('NavKit big-endian: user review fixes (vehicle world identity, prepared terrain, disjoint pockets)',
               'quick', NAVKIT_BE, 'test_navkit_review3.py'),
        script('NavKit big-endian: route check passes only when something was checked', 'quick',
               NAVKIT_BE, 'test_routecheck1943.py'),
        script('NavKit little-endian: navkit_le.py steps', 'quick', NAVKIT_LE, 'test_navkit_driver.py'),
        script('NavKit little-endian: apply, install and sampling guards', 'quick', NAVKIT_LE, 'test_driver_guards.py'),
        script('NavKit little-endian: window Cancel', 'quick', NAVKIT_LE, 'test_navkit_gui.py'),
        script('Spawn map: --navflags on made-up maps', 'quick', 'bf1943-spawn-map', 'test_spawnmap.py', '--made-up'),
        script('Navigation Studio: colours and options', 'quick', 'bf1943-navigation-studio', 'test_studio.py'),
        script('Map Inspector: screenshot exit code', 'quick', 'bf1943-map-inspector', 'test_inspector.py'),
        script('Xenia kit: full build from your BF1943 package', 'bf1943', XENIA_KIT, 'test_xeniakit.py', '--game'),
        script('Xenia kit: bots code against the decoded BF1943 image', 'bf1943', XENIA_KIT, 'test_bots.py', '--image'),
        script('executable tools: lis pairs end when the register is written', 'bf1943', XEX_TOOLS, 'test_xexrefs.py'),
        script('executable tools: VMX recorder checks what the fingerprint misses', 'bf1943',
               XEX_TOOLS, 'test_record_vmx.py'),
        script('executable tools: pinned outputs written byte for byte', 'bf1943', XEX_TOOLS, 'test_output_bytes.py'),
        script('BF1943 bundles: read and rebuild (fbrb1943)', 'bf1943', '', 'test_bf1943_data.py', 'bundles'),
        script('BF1943 DBX: every partition parses', 'bf1943', '', 'test_bf1943_data.py', 'dbx'),
        script('BF1943 terrain and water, every level', 'bf1943', '', 'test_bf1943_data.py', 'terrain'),
        script('BF1943 menu movies (apt)', 'bf1943', '', 'test_bf1943_data.py', 'apt'),
        script('Spawn map: every level bases and spawns', 'bf1943', 'bf1943-spawn-map', 'test_spawnmap.py'),
        script('NavKit big-endian: Wake Island objects', 'bf1943', NAVKIT_BE, 'test_levelobjects1943.py'),
        script('NavKit (both variants): Havok 4.6.1 meshes, and the collision reader on them', 'bf1943',
               NAVKIT_COMMON, 'test_havok461.py'),
        script('Level loader: every multiplayer map', 'bf1943', '', 'test_bf1943_tools.py', 'level'),
        script('DbxEdit and LevelDiff on BF1943 partitions', 'bf1943', '', 'test_bf1943_tools.py', 'dbxedit'),
        script('DbxEdit level check: BF1943 rules', 'bf1943', 'bf1943-dbx-tools', 'test_levelcheck1943.py'),
        script('Bundle tool command line', 'bf1943', '', 'test_bf1943_tools.py', 'fbrb'),
        script('NavKit big-endian: navkit.py prepare', 'bf1943', '', 'test_bf1943_tools.py', 'navkit'),
        script('the suite window (hub)', 'bf1943', '', 'test_bf1943_tools.py', 'hub'),
        script('3D tools: Map Inspector, Navigation Studio', 'bf1943', '', 'test_bf1943_tools.py', 'viewers'),
        script('NavKit big-endian: enclosure probe on Wake Island', 'bf1943', '', 'test_bf1943_tools.py', 'probe'),
        script('NavKit big-endian: RSNV export of your builds', 'bf1943', NAVKIT_BE, 'test_navexport1943.py'),
        script('executable tools from the research workspace', 'bf1943', '', 'test_bf1943_tools.py', 'xex'),
    ]


def run_one(args: list[str]) -> tuple[str, str, str, list[str]]:
    """Runs one test as the runner does: (status, its last line, its output, its skip lines). status: 'FAIL' (a
    nonzero exit), 'SKIP' (its last line starts "skip": the whole test, its data is missing), 'ok* ' (passed, but
    some checks were skipped) or 'ok  '."""
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=SUITE,
                            # no __pycache__, their children too; output in UTF-8, as it is read (2026-10-30 second
                            # review: a test printing a path outside the ANSI code page died in the pipe)
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8'))
    out = (result.stdout + result.stderr).strip()
    # the summary line is the test's own last line, not a warning it printed
    main_out = result.stdout.strip() or out
    lines = main_out.splitlines()
    last = lines[-1] if lines else ''
    skips = [line for line in lines if line.lower().startswith('skip')]
    if result.returncode != 0:
        status = 'FAIL'
    elif skips and last.lower().startswith('skip'):
        status = 'SKIP'
    elif skips:
        status = 'ok* '
    else:
        status = 'ok  '
    return status, last, out, skips


def main(argv: list[str]) -> int:
    if '--only' in argv and argv.index('--only') + 1 >= len(argv):
        # 2026-10-30: an IndexError before; a usage error like any other
        print('error: --only needs a part of a test\'s name')
        print(USAGE)
        return 2
    only = argv[argv.index('--only') + 1].lower() if '--only' in argv else None
    rest = list(argv[1:])
    if '--only' in rest:
        del rest[rest.index('--only'):rest.index('--only') + 2]       # its value is a name, not a tier
    tiers = [arg for arg in rest if arg in SELECT]
    unknown = [arg for arg in rest if arg not in SELECT and arg not in ('--list', '--strict')]
    if unknown or len(tiers) > 1:
        # a misspelled tier must not quietly run another one (and pass)
        problem = 'unknown tier or option ' + repr(unknown[0]) if unknown else 'more than one tier: ' + ' '.join(tiers)
        print(f'error: {problem}')
        print(USAGE)
        return 2
    tier = tiers[0] if tiers else 'quick'
    chosen = [test for test in tests()
              if test[1] in SELECT[tier] and (only is None or only in test[0].lower())]
    if only is not None and not chosen:
        print(f'error: --only {only!r} matches no {tier} test (see --list)')
        return 1
    if '--list' in argv:
        for name, test_tier, _ in chosen:
            print(f'{test_tier:6s} {name}')
        return 0
    strict = '--strict' in argv
    print(f'BF1943 Tool Suite tests: {tier}, {len(chosen)} test(s)' + (' (strict: a skip fails)' if strict else ''))
    failures: list[tuple[str, str]] = []            # (name, output)
    skipped: list[tuple[str, str]] = []             # (name, its last line)
    partly: list[tuple[str, list[str]]] = []        # (name, its skip lines)
    start = time.time()
    for name, _tier, args in chosen:
        test_start = time.time()
        status, last, out, skips = run_one(args)
        if status == 'FAIL':
            failures.append((name, out))
        elif status == 'SKIP':
            skipped.append((name, last))
        elif status == 'ok* ':
            partly.append((name, skips))
        print(f'{status} {name:42s} {time.time() - test_start:6.1f} s   {last[:90]}', flush=True)
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
    # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
    from bfbc2core import errors
    errors.setup('run-tests', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
