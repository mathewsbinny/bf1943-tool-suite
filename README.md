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
new and not created yet.

Status: **ready** = works on BF1943 data today; **partly** = some parts read BF1943 data, the rest is still BFBC2;
**not yet** = still the BFBC2 tool, reading BFBC2 / OPFOR data.

| Folder | Tool | BF1943 status | Starting point |
|---|---|---|---|
| `bf1943-xenia-kit/` | turns your own BF1943 package into an offline Xenia setup: Local Match browser, CHOOSE YOUR SIDE, menu clean-ups, no automatic team; opt-in `--experiment-bots` (23 server-side bots that spawn at their bases and are on the scoreboard; their soldiers are stepped with input frames, and walked and jumped with test input; the default build gives none; no decisions yet) | **ready** | new; findings from rising-sun `src/xeniaprobe`, `src/uiresearch` |
| `bf1943-apt-tools/` | menu movie (APT) reader, disassembler and rewriter | **ready** | rising-sun `src/uiresearch/apt.py` |
| `bf1943-fbrb-tool/` | BF1943 bundles (FbRB directory v2): `fbrb1943.py` (`list`, `info`, `extract` with a manifest, `replace` into a copy) | **ready** (`fbrb1943.py` only; OPFOR's BFBC2 `FbrbTool` was removed) | OPFOR FbrbTool; BF1943 layout from rising-sun `src/assetresearch` |
| `bf1943-spawn-map/` | each level's bases and flags with the soldier and vehicle spawns they own (`spawnmap.py`) | **ready** | new; uses `core/bfbc2core/dbx.py` and `bf1943-fbrb-tool` |
| `bf1943-console-readers/` | read-only STFS package reader (`stfs/stfs.py`) | **ready** (STFS only: it extracts BF1943's package for the Xenia kit; the disc image reader was removed, BF1943 is download-only) | OPFOR console readers |
| `bf1943-auto-navkit-big-endian/` | NavKit for BF1943's Xbox 360 levels (big-endian; Havok 4.6.1): `navkit.py` / `navkitGUI.pyw` prepare, sample (offline), build and check a level's navigation; `tools/navaudit1943.py` audits a build against its level; the build is the input of our own navigation runtime | **ready** (the BF1943 pipeline; all four MP maps built and audited 2026-10-24: every flag pair routed (Coral Sea has one flag), `hpdcheck` passes, every required position on land reachable but Coral Sea's South Base flag, whose walkable ground is 2.1 m away against the audit's 2 m reach) | a copy of the little-endian variant |
| `bf1943-auto-navkit-little-endian/` | NavKit for little-endian levels: BFBC2 PC today, later BF1943 converted for the PC port | **partly** (reads Havok 4.6.1; otherwise OPFOR's BFBC2 NavKit; its `prepare` needs BFBC2's FbrbTool, which this suite no longer has: it uses OPFOR's `tools\` copy) | OPFOR NavKit (kept in step with `tools/import_opfor.py --update`) |
| `core/` | shared readers (DBX, terrain, water), the game copy and its bundles (`game1943.py`), level loaders, 3D view | **ready for BF1943**: `dbx.py`, `terrain.py`, `water.py`, `game1943.py`, `level1943.py`; `level.py` stays the BFBC2 loader | OPFOR `core/` |
| `bf1943-dbx-tools/` | view, edit and check binary `.dbx` (DbxEdit, DbxEditGUI) | **partly** (reads, writes and edits BF1943 partitions: all of Wake Island's round-trip byte for byte; the level check and slot fixer are BFBC2's rules) | OPFOR DbxEdit |
| `bf1943-map-inspector/`, `bf1943-navigation-studio/` | 3D views of BF1943 levels (bases, flags, spawns by team) and of NavKit's navigation builds (path test; no editing) | **ready** | OPFOR tools of the same names |
| `bf1943-diff-workbench/` | LevelDiff: two `.dbx` files compared instance by instance | **ready** (on BF1943's binary partitions) | OPFOR diff workbench |
| `bf1943-automated-map-and-bot-test-harness/` | `xenia-run/`: `xeniarun.py` (the last Xenia session of the kit's build: patches, warnings, the bots' reports, a Xenia crash from Windows' Application log), `run_xenia_probe.ps1` (traced sessions), `capture_xenia_window.ps1`, `udp_join_probe.py`; map and bot tests planned | **partly** (the Xenia run tools are ready; OPFOR's BFBC2 crash reader was removed) | OPFOR test harness; probes from rising-sun `src/xeniaprobe` |
| `bf1943-xex-tools/` | BF1943's executable: decode the XEX, map functions and imports, the isolated Xenon (PowerPC + VMX128) language for Ghidra and its tests, batch decompiling, disassembly and reference helpers; run from a research workspace (`BF1943_RESEARCH`) | **ready** | rising-sun `src/xexresearch` (moved 2026-10-23) |
| `BF1943 Tool Suite.pyw` (hub) | one window for the tools: tabs Levels, Bundles, Level diff, Xenia run, Xbox files, Tests, More tools | **ready** | OPFOR's suite window |
| `bf1943-ai-behavior-simulator/`, `bf1943-bot-ai-debugger/`, `bf1943-tactical-map-authoring-tool/` | planned in OPFOR's design; no code | **not yet** (placeholders) | OPFOR placeholders |
| `bf1943-converter/` | the end-user toolkit: verify the user's copy, convert it to the PC build | not created | new |

## Tests

`python -B run_tests.py <tier>`, grouped by the data a test needs:

| Tier | Needs | Covers |
|---|---|---|
| `quick` | nothing (about a minute) | repository rules; error handling; the STFS reader on packages it builds; NavKit's builder rules in both variants; the offline sampler on a made-up level; jump links to a made-up ship's gun platform; a full build of a made-up level across a 0.8 m / 0.2 m seam; the navigation reader's heights; the Xenia kit's patch file, assembler and bot code; the Xenia run reader on a log it writes; the executable tools' workspace lookup |
| `bf1943` | your BF1943 copy, as `bf1943-xenia-kit` extracts it (`BF1943_GAME` overrides; about 5 minutes) | the kit's full build; every bundle and DBX partition; terrain and water on every level; the menu movies; the spawn map; Havok 4.6.1 collision; Wake Island's objects for NavKit; the level loader on every multiplayer map; DbxEdit and LevelDiff on BF1943 partitions; the bundle tool's command line; `navkit.py prepare`; the hub; the 3D tools (the Navigation Studio when a Wake Island build exists); the enclosure probe on Wake Island's samples; the executable tools from a research workspace (skipped without one) |

Run `quick` before every commit and `bf1943` before every push.

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
