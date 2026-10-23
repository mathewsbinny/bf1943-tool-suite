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
`%APPDATA%\bf1943-tool-suite`. Copying is not adapting: **most copied tools still read Project OPFOR's BFBC2
copy and BFBC2's formats internally** until each is adapted to BF1943, and the table below says which are. The
`bf1943-*` folder name alone does not mean a tool reads BF1943 data. OPFOR's own README and design are kept in
[docs/opfor-bfbc2](docs/opfor-bfbc2/) as reference (its `DESIGN.md` is the design the copied READMEs refer to).
Built `.exe` files are not copied (OPFOR does not commit them); build them here with each tool's `build.bat`.

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
| `bf1943-xenia-kit/` | turns your own BF1943 package into an offline Xenia setup: Local Match browser, CHOOSE YOUR SIDE, menu clean-ups, no automatic team; opt-in `--experiment-bots` (23 server-side bots that spawn at their bases, are on the scoreboard, walk and jump; no decisions yet) | **ready** | new; findings from rising-sun `src/xeniaprobe`, `src/uiresearch` |
| `bf1943-apt-tools/` | menu movie (APT) reader, disassembler and rewriter | **ready** | rising-sun `src/uiresearch/apt.py` |
| `bf1943-fbrb-tool/` | BF1943 bundles (FbRB directory v2): `fbrb1943.py` | **ready** (`fbrb1943.py`); `FbrbTool.exe` is still the BFBC2 `.fbrb` tool | OPFOR FbrbTool; BF1943 layout from rising-sun `src/assetresearch` |
| `bf1943-spawn-map/` | each level's bases and flags with the soldier and vehicle spawns they own (`spawnmap.py`) | **ready** | new; uses `core/bfbc2core/dbx.py` and `bf1943-fbrb-tool` |
| `bf1943-console-readers/` | read-only STFS package and Xbox disc image readers | **ready** (the STFS reader extracts BF1943's package for the Xenia kit) | OPFOR console readers |
| `bf1943-auto-navkit-big-endian/` | NavKit for BF1943's Xbox 360 levels (big-endian; Havok 4.6.1): collision straight from the bundles, collision samples computed offline | **partly** (work in progress; the BFBC2-specific steps are not adapted) | a copy of the little-endian variant |
| `bf1943-auto-navkit-little-endian/` | NavKit for little-endian levels: BFBC2 PC today, later BF1943 converted for the PC port | **partly** (reads Havok 4.6.1; otherwise OPFOR's BFBC2 NavKit) | OPFOR NavKit (kept in step with `tools/import_opfor.py --update`) |
| `core/` | shared readers (DBX, bundles, terrain, water), level loader, 3D view | **partly**: `dbx.py` reads BF1943 partitions, `terrain.py` and `water.py` detect the byte order; the level loader reads OPFOR's BFBC2 copy | OPFOR `core/` |
| `bf1943-dbx-tools/` | view, edit and check binary `.dbx` (DbxEdit, DbxEditGUI) | **not yet** (BFBC2; BF1943 uses the same binary DBX format, but DbxEdit is not tested on BF1943 files and its level checks are BFBC2 rules) | OPFOR DbxEdit |
| `bf1943-map-inspector/`, `bf1943-navigation-studio/` | 3D views of levels and navigation data | **not yet** (read OPFOR's BFBC2 copy and Kynapse path data) | OPFOR tools of the same names |
| `bf1943-diff-workbench/` | LevelDiff: two `.dbx` files compared instance by instance | **not yet** (BFBC2) | OPFOR diff workbench |
| `bf1943-automated-map-and-bot-test-harness/` | crash reader (`crashreport.xml`, `BFBC2Game.exe`), level test runs through OPFOR | **not yet** (BFBC2 / OPFOR) | OPFOR test harness |
| `bf1943-ai-behavior-simulator/`, `bf1943-bot-ai-debugger/`, `bf1943-tactical-map-authoring-tool/` | planned in OPFOR's design; no code | **not yet** (placeholders) | OPFOR placeholders |
| `bf1943-converter/` | the end-user toolkit: verify the user's copy, convert it to the PC build | not created | new |

## Tests

`python -B run_tests.py <tier>`, grouped by the data a test needs:

| Tier | Needs | Covers |
|---|---|---|
| `quick` | nothing (about a minute) | repository rules; error handling; the STFS reader on packages it builds; NavKit's builder rules in both variants; the offline sampler on a made-up level; the Xenia kit's patch file, assembler and bot code |
| `bf1943` | your BF1943 copy, as `bf1943-xenia-kit` extracts it (`BF1943_GAME` overrides) | the kit's full build; every bundle and DBX partition; terrain and water on every level; the menu movies; the spawn map; Havok 4.6.1 collision; Wake Island's objects for NavKit |

Run `quick` before every commit and `bf1943` before every push.

Only tests about BF1943 are kept. The tests OPFOR's tools came with ran on BFBC2 data (Project OPFOR's copy) and were
removed; a copied tool gets BF1943 tests when it is adapted (they remain in OPFOR's `bfbc2-tool-suite` and in this
repository's history). `provenance/opfor-imports.json` records the removal, so `tools\import_opfor.py --update`
does not bring them back.

## Rules

- Game data comes from the user's own copy and is read where it is. Nothing EA-owned is shipped.
- Copies from OPFOR keep their provenance entry. Changes to a copy are ordinary commits here.
- Copies from operation-rising-sun stay in place there too, so the evidence paths remain valid.
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
