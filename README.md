# BF1943 Tool Suite

Tools for converting, inspecting and building AI data for Battlefield 1943 (XBLA). They support the native PC client
(project-opfor-bf1943) and the research behind it (operation-rising-sun); both are separate, local projects.
Repository: https://github.com/mathewsbinny/bf1943-tool-suite

This suite holds **new BF1943 tools** and **modified copies of OPFOR tools**. OPFOR's own
`bfbc2-tool-suite` (in `C:\Games\Project OPFOR Toolkit`) stays dedicated to BFBC2 and is never changed:
tools are copied out of it with `tools\import_opfor.py`, which records every source file's hash in
[provenance/opfor-imports.json](provenance/opfor-imports.json), and are changed only here.

**Status (2026-10-23):** every OPFOR tool is copied (from `bfbc2-tool-suite` commit `fab30c0`, 114 files) and
rewired to the `bf1943-*` folder names; logs and window layouts go to `%LOCALAPPDATA%\bf1943-tool-suite` and
`%APPDATA%\bf1943-tool-suite`. Copying is not adapting: a copied tool reads BFBC2's data until it is adapted to
BF1943, and the table below says which are. The `bf1943-*` folder name alone does not mean a tool reads BF1943
data. Tools rebuilt for BF1943 drop their BFBC2-only parts (they remain in OPFOR's `bfbc2-tool-suite` and in this
repository's history). OPFOR's own README and design are kept in [docs/opfor-bfbc2](docs/opfor-bfbc2/) as
reference (its `DESIGN.md` is the design the copied READMEs refer to). Built `.exe` files are not copied (OPFOR
does not commit them); build DbxEdit and LevelDiff here with their `build.bat` (the only two C# tools left).

**Your game copy and the work folder.** The BF1943 tools read your copy where `bf1943-xenia-kit` extracts it
(`BF1943_GAME` selects another folder); the copy is only read, never changed. What they make from it (level
objects, collision samples, navigation builds) goes to the suite's work folder, `<suite>\work`
(`BF1943_WORK` sets another), for example `work\levels\<level>\navkit\<level>.hpd`.

Bots will be driven by our own navigation runtime (a per-level graph exported from NavKit's builder, queried with
A*), not by BFBC2's Kynapse runtime; the evidence is in operation-rising-sun
`research/evidence/navigation-runtime-options.json`.

## Layout

Mirrors the BFBC2 suite: one shared core and a folder per tool. `bf1943-converter/` (the native PC conversion) is
new and not created yet. Besides the tools: `run_tests.py` and `tests/` (the suite's tests), `tools/import_opfor.py`
(copies from OPFOR), `provenance/` (where every copied file came from, with its hash), `docs/opfor-bfbc2/` (OPFOR's
own documentation, unchanged), `work/` (what the tools make; git-ignored), `pyproject.toml` (the libraries, the
package, tool settings) and `.github/workflows/tests.yml` (CI).

Status: **ready** = works on BF1943 data today; **partly** = some parts read BF1943 data, the rest is still BFBC2;
**not yet** = still the BFBC2 tool, reading BFBC2 / OPFOR data.

| Folder | Tool | BF1943 status | Starting point |
|---|---|---|---|
| `bf1943-xenia-kit/` | turns your own BF1943 package into an offline Xenia setup: Local Match browser, CHOOSE YOUR SIDE, menu clean-ups, no automatic team; opt-in `--experiment-bots` (23 server-side bots that spawn at their bases and are on the scoreboard; their soldiers are stepped with input frames taken from a mailbox in guest memory, which the test harness's `guestmem.py` writes: walk, strafe, facing, sprint, jump, crouch, confirmed in game 2026-10-30; no decisions yet) | **ready** | new; findings from rising-sun `src/xeniaprobe`, `src/uiresearch` |
| `bf1943-apt-tools/` | menu movie (APT) reader, disassembler and rewriter | **ready** | rising-sun `src/uiresearch/apt.py` |
| `bf1943-fbrb-tool/` | BF1943 bundles (FbRB directory v2): `fbrb1943.py` (`list`, `info`, `extract` with a manifest, `replace` into a copy) | **ready** (`fbrb1943.py` only; OPFOR's BFBC2 `FbrbTool` was removed) | OPFOR FbrbTool; BF1943 layout from rising-sun `src/assetresearch` |
| `bf1943-spawn-map/` | each level's bases and flags with the soldier and vehicle spawns they own (`spawnmap.py`) | **ready** | new; uses `core/bfbc2core/dbx.py` and `bf1943-fbrb-tool` |
| `bf1943-console-readers/` | read-only STFS package reader (`stfs/stfs.py`) | **ready** (STFS only: it extracts BF1943's package for the Xenia kit; the disc image reader was removed, BF1943 is download-only) | OPFOR console readers |
| `bf1943-auto-navkit-big-endian/` | NavKit for BF1943's Xbox 360 levels (big-endian; Havok 4.6.1): `navkit.py` / `navkitGUI.pyw` prepare, sample (offline), build and check a level's navigation; `tools/navaudit1943.py` audits a build against its level; `tools/navexport1943.py` exports it as the navigation runtime's RSNV file (`tools/rsnvtool.py` inspects one); `tools/navexport_vehicles1943.py` builds the vehicle worlds (ground: `groundnav1943.py`, sea: `seanav1943.py`, planes: `airnav1943.py`) | **ready** (the BF1943 pipeline; all four MP maps built and audited 2026-10-24: every flag pair routed (Coral Sea has one flag), `hpdcheck` passes, every required position on land reachable but Coral Sea's South Base flag, whose walkable ground is 2.1 m away against the audit's 2 m reach) | a copy of the little-endian variant |
| `bf1943-auto-navkit-little-endian/` | NavKit for little-endian levels: BFBC2 PC today, later BF1943 converted for the PC port (`navkit_le.py` / `navkitGUI_le.pyw`, named so since 2026-10-31) | **partly** (reads Havok 4.6.1; otherwise OPFOR's BFBC2 NavKit; its `prepare` needs BFBC2's FbrbTool, which this suite no longer has: it uses OPFOR's `tools\` copy) | OPFOR NavKit (kept in step with `tools/import_opfor.py --update`) |
| `bf1943-auto-navkit-common/` | the NavKit modules both variants use, one copy (2026-10-31): the builder `navbuild` (a package, with explicit `Settings`: BFBC2's defaults or BF1943's `soldier1943.SETTINGS`), walkability rules, samples, the `.hpd` reader and writer, the Havok reader, level objects, the detailed-sector rule, `navdiff`; the format spec; their rule tests | **ready** (both variants' builds byte for byte as before the merge: four BF1943 maps with their RSNV exports, two BFBC2 levels) | the two variants' copies, merged |
| `core/` | shared readers (DBX, terrain, water, ground materials), the game copy and its bundles (`game1943.py`), level loaders, vehicle entry points, the navigation runtime's RSNV file and its reference search and planners, 3D view | **ready for BF1943**: `dbx.py`, `terrain.py`, `water.py`, `materialmap1943.py`, `game1943.py`, `level1943.py`, `vehicles1943.py`, `navdata.py`, `navpath.py`, `rsnv.py`, `rsnvtrip.py`, `rsnvhybrid.py`; `level.py`, `xmlinst.py` and `navtune.py` stay BFBC2 | OPFOR `core/` |
| `bf1943-dbx-tools/` | view, edit and check binary `.dbx` (DbxEdit, DbxEditGUI; `schema1943.py` writes the executable's reflection schema into DbxEdit for the level check) | **ready** (reads, writes and edits BF1943 partitions: all of Wake Island's round-trip byte for byte; the level check uses BF1943's own rules - what all five shipped levels satisfy, the executable's completed reflection schema (members, reference and element classes, enum values) and, with `--bundle`, the level's static cull trees - chosen from the file, BFBC2's on request; the slot fixer repairs BF1943 cull slots; the rules' in-game effects are not tried) | OPFOR DbxEdit |
| `bf1943-map-inspector/`, `bf1943-navigation-studio/` | 3D views of BF1943 levels (bases, flags, spawns by team) and of NavKit's navigation builds (path test; no editing) | **ready** | OPFOR tools of the same names |
| `bf1943-diff-workbench/` | LevelDiff: two `.dbx` files compared instance by instance | **ready** (on BF1943's binary partitions) | OPFOR diff workbench |
| `bf1943-automated-map-and-bot-test-harness/` | `xenia-run/`: `xeniarun.py` (the last Xenia session of the kit's build: patches, warnings, the bots' reports, a Xenia crash from Windows' Application log), `run_xenia_probe.ps1` (traced sessions), `guestmem.py` (the running game's memory from outside Xenia: the bots' mailbox), `capture_xenia_window.ps1`, `udp_join_probe.py`; map and bot tests planned | **partly** (the Xenia run tools are ready; OPFOR's BFBC2 crash reader was removed) | OPFOR test harness; probes from rising-sun `src/xeniaprobe` |
| `bf1943-xex-tools/` | BF1943's executable: decode the XEX and verify its page digests (`verify_xex_image.py`), map functions and imports, the isolated Xenon (PowerPC + VMX128) language for Ghidra with our own vector and FP semantics (`xenon_vector_semantics.py`, `xenon_splice.py`) and its tests (results stamped with `vmx_inputs.py`), batch decompiling, the call graph and strings (`export_callgraph_strings.py`), disassembly and reference helpers; run from a research workspace (`BF1943_RESEARCH`) | **ready** | rising-sun `src/xexresearch` (moved 2026-10-23) |
| `BF1943 Tool Suite.pyw` (hub) | one window for the tools: tabs Levels, Bundles, Level diff, Xenia run, Xbox files, Tests, More tools | **ready** | OPFOR's suite window |
| `bf1943-ai-behavior-simulator/`, `bf1943-bot-ai-debugger/`, `bf1943-tactical-map-authoring-tool/` | planned in OPFOR's design; no code | **not yet** (placeholders) | OPFOR placeholders |
| `bf1943-converter/` | the end-user toolkit: verify the user's copy, convert it to the PC build | not created | new |

## Running the tools

Every tool runs straight from a clone, with no install step: `python -B <folder>\<tool>.py`. They need Python 3.13
(what the suite and CI run) and the libraries `pyproject.toml` lists: numpy and matplotlib for most tools; moderngl,
glfw and imgui-bundle for the 3D tools; capstone for the executable tools and the Xenia kit's assembler test; pillow
for rsnvtool's pictures. `pip install --group ci` (pip 25.1 or later, in the suite folder) installs all of them at the
versions CI uses.

**How the tools find each other.** The suite's folders are not packages (their names have hyphens), so a script that
imports from another folder starts with the suite's bootstrap, the same lines in every one
([core/suitepath.py](core/suitepath.py) has them): under
`if __name__ == '__main__':` it finds the suite root above the script, puts `core` on `sys.path`, and
`suitepath.use(...)` adds the shared folders (core, the bundle and APT tools, the spawn map, the STFS reader, the Xenia
kit, the Xenia run tools, the executable tools, NavKit's shared modules) and, first, the NavKit variant(s) the script
names.
Libraries (core, the tools' importable modules) never change `sys.path`; they import plainly, so what they get is
decided by the script that was run, never by what happened to be imported first. `tests/test_repo.py` fails on any
other `sys.path` edit and on a module name shared by two of those folders, the NavKit variants included: what both
use is one copy in `bf1943-auto-navkit-common` (a shared folder), so the order a script names the variants in changes
nothing it imports.

**Installing (development).** `pip install -e .` puts `core` (`bfbc2core`, `suitepath`) and the shared folders on
`sys.path` for that environment, so the bootstrap adds nothing for them; a script still names the NavKit variant whose
own modules it uses.
The tools need no install, and CI runs them without one.

**From another project** (operation-rising-sun's investigations): put the suite's `core` on `sys.path`, then
`import suitepath` and `suitepath.use(...)` with the NavKit variant(s) the script uses, as a tool does.

## Tests

`python -B run_tests.py <tier>`, grouped by the data a test needs:

| Tier | Needs | Covers |
|---|---|---|
| `quick` | nothing (58 tests, two to three minutes) | repository rules; error handling, and every command-line tool's errors; the DBX reader on damaged files; the RSNV file (both byte orders, search, locate, damaged files); the STFS reader on packages it builds (names that would leave the output folder refused); NavKit's builder rules (the shared modules: rules, edge cases, damaged files) and the little-endian driver's steps; the offline sampler on a made-up level; `rsnvtool` on a made-up file; jump links to a made-up ship's gun platform; a full build of a made-up level across a 0.8 m / 0.2 m seam; vehicle navigation (ground and sea grids, trips, heading-aware planning, plane take-off lanes); the navigation reader's heights; the Xenia kit's patch file, assembler and bot code; the Xenia run reader on a log it writes; the executable tools' workspace lookup and Xenon vector p-code generator |
| `bf1943` | your BF1943 copy, as `bf1943-xenia-kit` extracts it (`BF1943_GAME` overrides; the quick tests plus 22, about 5 minutes more) | the kit's full build; every bundle and DBX partition; terrain and water on every level; the menu movies; the spawn map; Havok 4.6.1 collision; Wake Island's objects for NavKit; the level loader on every multiplayer map; DbxEdit and LevelDiff on BF1943 partitions; DbxEdit's BF1943 level check (shipped levels pass, broken copies fail); the bundle tool's command line; `navkit.py prepare`; the hub; the 3D tools (the Navigation Studio when a Wake Island build exists); the enclosure probe on Wake Island's samples; the RSNV export of your builds (skipped without a Wake Island build); the executable tools from a research workspace, the VMX recorder's checks among them (skipped without one) |

Run `quick` before every commit and `bf1943 --strict` before every push.

**CI** ([.github/workflows/tests.yml](.github/workflows/tests.yml)) runs the quick tier with `--strict` on every push
and pull request, on GitHub's `windows-latest` with Python 3.13: it installs the libraries (`pip install --group ci`),
runs the lint (`ruff check`) and the type check (`pyright`, below: "Types"), points `BF1943_GAME` and
`BF1943_RESEARCH` at empty folders, builds the C# tools with their `build.bat` (the .NET Framework 4 compiler is part
of the image) and runs the tools from the checkout, without an install; then it checks that the package installs (`pip install -e .`). It has no game copy, no research workspace and
no Xenia, and downloads nothing but Python libraries: so every quick test runs without them, and a skip fails the run
like a failure. A second job, `coverage`, runs the quick tier again through pytest (`--strict-skips`) with line
coverage of every Python process it starts, uploads the report (text, XML and HTML) as the artifact `coverage`, and
fails when the total is below the threshold (`fail_under`, below: "Coverage").
The console's code page does not matter: the Xenia kit's `launch.cmd` switches to UTF-8 itself, and its test runs it
under both the OEM page (437) and UTF-8 (65001).

A test whose data is missing is shown as SKIP, and one that skipped some of its checks as `ok*`. The summary counts
both ("58 of 59 passed, 1 skipped", "..., 1 partly skipped") and lists them under "not fully run" with the reason, so
a passing run is full coverage only when nothing was skipped; `--strict` makes any skip fail the run.
`--only <part of a name>` runs the matching tests (an `--only` that matches none is an error) and `--list` lists a
tier's tests without running them.

The Xenia kit's full-build test builds against a scratch copy of `xenia_canary.exe`, so it never writes your Xenia
folder. The test checks your folder's patch files and fixed exe afterwards.

### Writing a test

A new test is a **pytest-style file**: `test_<what>.py` in the tool's `tests` folder, with plain functions whose
names start with `test` and plain `assert`s, and no `if __name__ == '__main__':` block. No list to add it to:
`run_tests.py` finds it, runs it with [tests/functions.py](tests/functions.py) (pytest is not needed) and names it
by the first line of its docstring; it is in the quick tier unless the file sets `TIER = 'bf1943'`. pytest runs the
same file as it is. Examples: [core/tests/test_water_mesh.py](core/tests/test_water_mesh.py),
[test_havok_index.py](bf1943-auto-navkit-common/tests/test_havok_index.py) and
[test_headroom.py](bf1943-auto-navkit-common/tests/test_headroom.py).

```python
r"""NavKit (both variants): what is tested, in a few words (the runner's name for the test)

What the file checks, and on what data.
"""
import unittest

import navwalk                      # any suite module, imported plainly (no bootstrap)


def test_headroom_band_default():
    assert navwalk.HEADROOM_BAND == (0.5, 2.0), 'default band 0.5-2.0 m'


def test_on_a_file(tmp_path):       # tmp_path: a new empty folder, removed afterwards
    if not have_the_data():
        raise unittest.SkipTest('no Wake Island build')     # shown as SKIP (ok* when only some tests skip)
    ...
```

So that both runners give the same result, a test uses only `assert`, the `tmp_path` argument and
`raise unittest.SkipTest(reason)`: other pytest fixtures, marks or `pytest.raises` need pytest, which `run_tests.py`
does not require. Each suite folder is on `sys.path` while it runs, both NavKit variants included (no module name is
in two of them).

The older tests are **scripts**: run as `python -B <test>.py`, they print one `ok`/`FAIL` line per check and a summary
line, and are listed by hand in `run_tests.py` (`scripts()`), with arguments when one script serves several entries.
They stay as they are; a script is the right form only when a test must run as its own program (arguments, exit
codes, a fresh process).

### Fuzz tests

Every binary reader is fuzzed (`test_fuzz_*.py`, with [core/fuzzcheck.py](core/fuzzcheck.py): standard library only,
seeded random). The contract each reader keeps on any input: it returns a result or raises `ValueError` (its own error
type is a subclass) with a message; never another exception, never a hang or unbounded memory (each case runs under a
time and memory limit), and never a file cut short read as whole. Cases are mutations of valid files (bit flips,
bytes and words set to edge values, counts and offsets in named fields set to 0, 1, huge or negative, cuts at each
structural boundary, deleted, duplicated or random slices); a breaking case is shrunk and reported with what replays
it. The quick tier's seeds are made-up files from the tests' own builders, with fixed seeds (a few seconds a reader);
the bf1943 tier's (`tests/test_fuzz_real.py`) are your own files, only read. `set BF1943_FUZZ=long` runs 50 times as
many cases (`BF1943_FUZZ=<n>`: n times) from a random run seed, which it prints; `set BF1943_FUZZ_SEED=<seed>` replays
one. A new reader gets a fuzz test, and each bug the fuzzer finds a regression test with its shrunk input.

### pytest

`python -B -m pytest` (from the suite folder; pytest is in the `ci` group) runs both kinds:
the pytest-style files directly, and every listed script through [tests/test_suite_tiers.py](tests/test_suite_tiers.py),
one pytest test per script, run exactly as `run_tests.py` runs it: a script that fails fails with its last 25 lines,
one whose data is missing is skipped with its reason, and one that skipped some checks passes with a warning.
`--tier bf1943` takes the bf1943 tier, `--strict-skips` fails on any skip (as `--strict`), and `-k <part of a name>`
picks tests. [conftest.py](conftest.py) keeps the scripts from being collected as modules. Settings:
`pyproject.toml` `[tool.pytest.ini_options]`. `run_tests.py` stays the reference: `quick` before a commit,
`bf1943 --strict` before a push.

### Lint

`ruff check` (pinned in the `ci` group; settings in `pyproject.toml` `[tool.ruff]`) with a small set of rules that
find bugs, not style: pyflakes (`F`), syntax errors (`E9`), imports below code (`E402`), bugbear (`B`) and pylint's
errors (`PLE`). An import that must follow the bootstrap or `sys.dont_write_bytecode = True` carries
`# noqa: E402`. Two bugbear rules are off, for the reasons pyproject.toml gives: `B905` (`zip()` without
`strict=`) and `B023` (a closure using a loop variable; every such closure here runs within its iteration). The VMX
fingerprint's Python inputs (`bf1943-xex-tools/vmx_inputs.py` `INPUTS`) are left out, as they
are never edited; `tests/test_repo.py` keeps that list equal to `INPUTS`.

### Before a commit: pre-commit

`.pre-commit-config.yaml` runs ruff, pyright and `tests/test_repo.py` before each commit, using the tools the `ci`
group installs (local hooks, so their versions are CI's and pre-commit downloads nothing). Set it up once with
`pip install pre-commit` and `pre-commit install`; `pre-commit run --all-files` runs every hook. The test tiers
stay their own step (`python -B run_tests.py quick --strict`).

### CI's pins

The workflow's actions are pinned by commit hash, each with its version in a comment, so a moved tag cannot change
what CI runs. Dependabot (`.github/dependabot.yml`) opens weekly pull requests that move those pins and the Python
dependencies; a major version comes as its own pull request, checked by CI before it is merged.

### Types

The shared library code is annotated (Python 3.13 syntax: `list[int]`, `X | None`, numpy arrays as
`npt.NDArray[np.float32]` and the like) and checked by [pyright](https://github.com/microsoft/pyright) at its
`standard` strictness: `core/bfbc2core` and `bf1943-auto-navkit-common/tools` (the navbuild package included). The
settings are `pyproject.toml` `[tool.pyright]`: the folders checked (`include`), and where their imports are found
(`extraPaths`: the folders `suitepath.use` puts on `sys.path`, both NavKit variants included). pyright is pinned in the
`ci` group with its `nodejs` extra, which brings Node.js as a PyPI wheel, so nothing outside PyPI is downloaded. CI's
`quick` job runs it after the lint. From the suite folder, in the Python that has the `ci` group:

```
set PYRIGHT_PYTHON_IGNORE_WARNINGS=1
python -m pyright
```

(the variable keeps it from asking PyPI for a newer version; `python -m pyright <file>` checks one file). A checked
folder must stay at 0 errors. Where the checker cannot see a type the code guarantees, the code says so with
`typing.cast` or a `# pyright: ignore[<rule>]` that carries its reason; an annotation never changes behaviour (they are
evaluated at import time in 3.13, so a name used only in annotations is imported under `if TYPE_CHECKING:` and quoted).

**To check another folder:** annotate its public functions, classes and dataclasses, add the folder to `include`, run
pyright and fix what it finds (a real bug gets a regression test), and keep the folder at 0 errors in the same commit.
The VMX fingerprint's inputs are never annotated or checked (`tests/test_repo.py` fails if `include` covers one).

### Coverage

Line coverage of a tier, the processes its tests start included (pyproject.toml `[tool.coverage]`), with coverage in
the Python that runs the tests (the `ci` group):

```
set COVERAGE_PROCESS_START=%CD%\pyproject.toml
set COVERAGE_FILE=%CD%\.coverage
set COVERAGE_SOURCE=%CD%
python -B run_tests.py quick
set COVERAGE_PROCESS_START=
python -m coverage combine
python -m coverage report
```

Every Python started while `COVERAGE_PROCESS_START` is set measures itself (coverage installs a `.pth` file that
starts it), so the runner, its tests and their children all count. The three are absolute because tests start
children in other folders and coverage reads a relative path from each process's own folder. The report counts the
suite's files, those that never ran too, and leaves out the tests themselves.

**The threshold** is `fail_under` in `pyproject.toml` `[tool.coverage.report]`: `coverage report` exits 2 when the
quick tier's total is below it, and CI's coverage job fails then (its last step; the reports are uploaded first). It is
a ratchet, set a little below the measured total so that a small change does not trip it, and it only goes up:
`tests/test_repo.py` ("repository rules", quick tier) fails when any commit of `pyproject.toml`, or the working copy,
has a `fail_under` below an earlier one (CI checks out the whole history, so a lowering committed fails there too). To
raise it after adding tests: run the steps above with pytest as CI does (`python -B -m pytest --tier quick
--strict-skips` in place of `run_tests.py quick`), read the total from `python -m coverage report --format=total`,
and set `fail_under` about one point below it, in the same commit as the tests. When coverage falls below it, add
tests rather than lower it. The one exception, a well-tested tool removed, is a commit whose message has a line
`Coverage-threshold-lowered: <reason>`: that commit may lower `fail_under`, and later ones are held to its value.

Only tests about BF1943 are kept. The tests OPFOR's tools came with ran on BFBC2 data (Project OPFOR's copy) and were
removed; a copied tool gets BF1943 tests when it is adapted (they remain in OPFOR's `bfbc2-tool-suite` and in this
repository's history). `provenance/opfor-imports.json` records the removal, so `tools\import_opfor.py --update`
does not bring them back.

## Rules

- Game data comes from the user's own copy and is read where it is. Nothing EA-owned is shipped.
- Copies from OPFOR keep their provenance entry. Changes to a copy are ordinary commits here.
- Tools from operation-rising-sun move here (2026-10-23: the executable tools, the Xenia probes, the enclosure
  probe); its investigations stay there and import what they need from here. Evidence written before a move names
  the old path, which that repository's history keeps.
- No network traffic.
- Third-party references (XenonRecomp, GNU binutils' `ppc-dis.c`, Xenia, Ghidra) are cited and credited where they
  are used. Their files are supplied by you and checked by hash, and none is copied here. See
  [bf1943-xex-tools](bf1943-xex-tools/README.md#sources-and-credits).

## Importing from OPFOR

```
python -B tools\import_opfor.py <path in bfbc2-tool-suite> <path here> --note "why"
python -B tools\import_opfor.py --plan tools\opfor-import-plan.json
python -B tools\import_opfor.py --update <path here> [...]
```

Files come from OPFOR's committed state (HEAD) through read-only git commands, so uncommitted work there and
ignored build outputs are never copied. It refuses to overwrite. `--update` brings earlier imports up to OPFOR's
current commit: a file changed only in OPFOR is replaced, a file changed in both places is reported as a conflict
to merge by hand, and renames recorded here (such as `bf1943-auto-navkit` to `bf1943-auto-navkit-little-endian`)
are followed.
