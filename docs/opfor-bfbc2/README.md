# BFBC2 Tool Suite

Tools for building and debugging AI on Battlefield: Bad Company 2 maps, in one repository. The 3D tools
share one core: level loading, a 3D view, picking and the game's data formats.

## Getting started

1. Install Python 3.10 or newer, then the libraries the tools use:
   `pip install numpy matplotlib moderngl glfw imgui-bundle pillow` (and `pefile capstone` for the crash
   reader's code view).
2. Put this folder next to your `Project OPFOR` folder (or set `OPFOR_ROOT` to it): the tools read the game
   copy there.
3. Double-click **`BFBC2 Tool Suite.pyw`**. One window has every tool:
   - **Levels:** choose a level, then **Open in Map Inspector** or **Open in Navigation Studio** (or
     double-click the level). A level is unpacked the first time (offline, about 20 s); the tool's progress
     shows at the bottom and its 3D window opens when the level is loaded.
   - **Bundles, Level diff, Crash reader, Xbox files, Tests:** the command-line tools with file pickers and
     options; their output appears at the bottom.
   - **More tools:** DbxEditGUI and NavKit's window.

The 3D tools can also be started on their own: double-click `bfbc2-map-inspector\inspector.py` or
`bfbc2-navigation-studio\studio.py` and a level picker opens, or give the level on the command line
(`python studio.py sp_01`). On a laptop with two GPUs, see "The 3D tools and your GPU" below.

### When something goes wrong

Every tool keeps a log in `%LOCALAPPDATA%\bfbc2-tool-suite\logs\` (one file per tool and day, the 20 newest
kept): what it did, warnings, and every error with its full details. When a tool crashes, a window says what
happened, the likely cause and what to do - for example "a Python library the tool needs is not installed
(imgui_bundle) - pip install imgui-bundle", "the 3D view could not start OpenGL 3.3 - update the graphics driver",
"the level is not prepared", "a file could not be written - close the game" - and where the log is. In the 3D
tools an error in one panel does not close the window: it is listed in a Problems window and logged once. The
suite window checks the libraries when it opens, and when a tool it runs fails it shows that tool's last lines.
The command-line tools - FbrbTool, DbxEdit, LevelDiff, NavKit's `navkit.py` and `tools\*.py`, the crash reader,
the console readers, the test runner - log the same way and, on an error, print the cause, what to do and the
log's path (exit code 1); DbxEditGUI and NavKit's window show it in a window. If you report a problem, send the
log file.

**Ready to use**

| Folder | Tool |
|---|---|
| `bfbc2-auto-navkit/` | **BC2 NavKit**: generates AI navigation data (`pathdata.hpd`) for levels. Start with `QUICKSTART.md`, or `navkitGUI.pyw`. |
| `bfbc2-dbx-tools/` | **DbxEdit** and **DbxEditGUI**: view, edit, check and repair binary `.dbx` files; XML export and import |
| `bfbc2-fbrb-tool/` | **FbrbTool**: unpack and repack `.fbrb` bundles |
| `bfbc2-console-readers/` | read-only readers for Xbox 360 disc images and content packages |
| `bfbc2-diff-workbench/level-diff/` | **LevelDiff**: what differs between two `.dbx` files, instance by instance |
| `bfbc2-automated-map-and-bot-test-harness/crash-reader/` | **crash reader**: the game's `crashreport.xml` turned into names, code, null pointers and hints |
| `core/` | the shared core: Python readers for `.dbx`, terrain height fields and water; the level loader (entities, teams, collision, areas and routes); the 3D view with picking |
| `bfbc2-map-inspector/` | **Map Inspector**: browse a level in 3D and inspect every object's source, properties and links; measure |
| `bfbc2-navigation-studio/` | **Navigation Studio** (viewing): NavKit's and DICE's navigation data in 3D, by island, sector, pitch or difference; islands and spawns off the main network; a reference level's AI layer drawn on another level (`--compare`) and an anomaly overlay of NavKit builds, both with JSON export (`overlay.py`) |

**Planned** (see [DESIGN.md](DESIGN.md))

| Folder | Tool | Milestone |
|---|---|---|
| `bfbc2-navigation-studio/` | editing, the path test and side files for the AI's navigation data | M4 |
| `bfbc2-automated-map-and-bot-test-harness/` | repeatable map and bot tests, results compared between builds | M5, then growing |
| `bfbc2-bot-ai-debugger/` | live view of the AI while the game runs (through Project OPFOR) | M6 |
| `bfbc2-tactical-map-authoring-tool/` | tactical annotations above the navigation data for bots | M7 |
| `bfbc2-diff-workbench/` | ImHex patterns for BC2 formats, format coverage and structure-aware diffs | alongside |
| `bfbc2-ai-behavior-simulator/` | runs OPFOR's bot decision code on simplified maps, without the game | with Goal 3 |

The asset builder & validator, `bfbc2-asset-builder`, is part of the Project OPFOR repository (DESIGN.md
section 4.9).

## The 3D tools and your GPU

The Map Inspector and the Navigation Studio draw with OpenGL 3.3. Each prints the GPU it uses when it starts
(and shows it in its status line). On a laptop with two GPUs, Windows may give Python the slower integrated
one: set `python.exe` to "High performance" in Settings > System > Display > Graphics. On the development
PC that took the Navigation Studio on SP_01 (10.4 million triangles) from the AMD Radeon integrated GPU to
the GeForce RTX 4060, at 144 frames a second.

## Tests

```
python run_tests.py quick      before every commit: no game data, about a minute
python run_tests.py game       before every push: uses Project OPFOR's game copy, about 10 minutes
python run_tests.py full       after changing a format reader: every unpacked file, about 30 minutes
python run_tests.py --list     what each tier runs;   --only <name> runs the tests whose name contains it
```

| Test | Tier | Checks |
|---|---|---|
| `tests/test_repo.py` | quick | no game files or large files in the history, no co-author lines, no build output tracked, READMEs, LICENSE, every Python file compiles |
| DbxEdit unit tests | quick | 93 checks of the format, XML export and import, edits, level check, cull-slot fixer, command line, window |
| `core/tests/test_fbrb.py` | quick, game, full | unpack + pack gives the same bundle (built here; 12 game bundles; all 1,055), `*deleted*` markers handled |
| `bfbc2-diff-workbench/level-diff/tests/test_leveldiff.py` | quick, game | two levels built in the test with known differences (changed field, re-created object, list entries added / removed / reordered, instances added / removed); `--match-objects`, `--ignore`, `--type`, `--summary`, JSON, exit codes; MP_005 against itself and Dist against Package |
| `bfbc2-automated-map-and-bot-test-harness/crash-reader/tests/test_crashreader.py` | quick, game | crash reports built in the game's layout: registers, stack, symbols and hints, null registers, build note, log lines; with the exe: function start, null operand, call before a return address, text on the stack |
| `bfbc2-console-readers/tests/test_console_readers.py` | quick, game | XDVDFS images (two partition offsets, nested and empty folders) and STFS packages (both hash-table layouts, a fragmented file); with your Xbox 360 files: the disc's 693 files, Onslaught path data from Update #2 and #3 identical and identical to Napisal's PC files |
| `core/tests/test_water.py` | game | every level's water read exactly, indices in range, a shader per triangle |
| `core/tests/test_level.py` | game | the loader's counts per category, team, shape, collision, terrain and water against `level_snapshot.json` (`--update` after an intended change) |
| `core/tests/test_terrain.py` | game | heights within 0.1 m of the in-game terrain samples (13 levels) |
| `hpdtool roundtrip / meshtrip` | game | every path-data file and navigation mesh written back byte for byte |
| `core/tests/test_navdata.py` | game | path data as the studio loads it (islands, cache, a file against itself) and the path test (routes, costs from the stored edges, refusals between islands) |
| `tests/test_cli_errors.py` | quick | every command-line tool (C# and Python) given input that must fail: an error exit, the cause and what to do in words, the error in its log |
| `core/tests/test_errors.py` | quick | error handling: causes explained in words, the log (errors with tracebacks, warnings, other threads), crashes caught with exit code 1, old logs pruned, a failing 3D panel kept to the Problems window |
| `core/tests/test_navtune.py` | quick | navigation side files: every operation saves and loads unchanged, undo / redo, the PC's name recorded, invalid edits refused, DICE bases refuse region edits, the base file's hash |
| `bfbc2-auto-navkit/tests/test_water_rule.py` | quick | NavKit's water rule on a sample grid built in the test: ground over 1.5 m deep left out, shallower ground, decks and dry ground kept, overlapping water |
| `bfbc2-auto-navkit/tests/test_area_link.py` | quick | NavKit's area link rule on a grid built in the test: straight steps up to 0.6 m join an area, diagonals through a side cell, a steep plane cut into single cells, object surfaces and portals on the slope rule |
| `bfbc2-auto-navkit/tests/test_regress_progress.py` | quick | `navregress.py`'s progress estimates: durations in words, each level's expected time from earlier runs, its sample file size or the default |
| `bfbc2-auto-navkit/tests/test_navboundary.py` | quick | `navboundary.py` on path data built in the test: merged and split areas, matching / missing / extra boundary edges, precision and recall with and without tolerance, portal displacement, the CSV and map export |
| `bfbc2-auto-navkit/tests/test_portal_symmetry.py` | quick | NavKit's portals on grids built in the test: a portal each way between two areas, no edge with two portal pieces, a stacked column's one-way portal turned into outline |
| `bfbc2-auto-navkit/tests/test_reference_clip.py` | quick | NavKit's bound to the reference extent: surfaces outside the box (every floor), inclusive edges, the extent from outlines and portals of every pitch without border portals, no bound for a new map |
| `bfbc2-auto-navkit/tests/test_characters.py` | quick | NavKit treats soldiers standing in the level while it was sampled as absent: their surfaces are not floor, the floor under and beside them is not blocked, other floors keep their flags; the rule off blocks as before |
| `bfbc2-auto-navkit/tests/test_headroom.py` | quick | NavKit's headroom band: ground with the next surface 0.5-2.0 m above it is not walkable; a step-height surface or a ceiling above keeps the ground; soldiers overhead do not count; the rule off keeps it all |
| `bfbc2-auto-navkit/tests/test_navedits.py` | quick | walkability edits as generator inputs on a sample grid built in the test: outlines, boxes, height ranges over stacked floors, later edits winning, seeds and unseeds |
| `bfbc2-auto-navkit/tests/test_links.py` | game | links and route edges written into a route graph: a link joins two islands (no route before, one after), one-way edges, removal, costs by DICE's rule, the first-edge index rule, unreachable links reported, a DICE base edited on a copy |
| `bfbc2-auto-navkit/tests/test_traversal.py` | game | traversal annotations in `<level>.traversal.json` (ends, route nodes, islands joined; the path data untouched), the apply report, install / restore into a stand-in game folder, `apply --install` |
| `bfbc2-auto-navkit/tests/test_apply.py` | game | `navkit apply` on sp_004_b: no edits gives the regression build; an unwalkable square leaves no ground, an unseed removes an island, a walkable box adds ground; the output reads back exactly |
| `bfbc2-navigation-studio/tests/test_editing.py` | game | the studio's edit mode driven as clicks would drive it: every tool, outline and box, undo / redo, save and reload, the click mode, DICE-base limits, the apply button building in the background |
| `tests/test_hub.py` | game | the suite window driven as clicks would: its tabs and the levels, FbrbTool, LevelDiff, the crash reader and the test runner through their tabs; a failing tool reported with its last lines, a stopped one not; a 3D tool with a bad level exiting cleanly; the level picker |
| `tests/test_viewers.py` | game | the Map Inspector and the Navigation Studio open a level, draw it (screenshot not blank) and exit; prints the GPU used |
| `bfbc2-auto-navkit/tools/navregress.py` | game (2 levels), full (13) | NavKit rebuilds levels with the current code; fails if any DICE-parity measure is worse than the reference run `regression` |
| DbxEdit corpus, `core/tests/test_dbx.py` | full | every unpacked `.dbx`: DbxEdit's fingerprints, and the Python reader's XML identical to DbxEdit's |

Each tool's folder has its own README. The tools that existed before the suite were merged in with their
full history (2026-10-16), so `git log -- <folder>/<file>` shows each file's history.

## Licence

MIT (see `LICENSE`) for all code and documentation in this repository. The licence covers only this
repository's own work: no Electronic Arts / DICE game data, code or other content is included, and the
tools need your own copy of the game. Third-party libraries keep their own licences (moderngl, imgui-bundle:
MIT; glfw: zlib; numpy: BSD). Not affiliated with or endorsed by Electronic Arts or DICE; Battlefield and
Battlefield: Bad Company are trademarks of Electronic Arts Inc.

Developed for Project OPFOR. Game data is read from your own installation and never distributed.
Battlefield: Bad Company 2 is © Electronic Arts / DICE.
