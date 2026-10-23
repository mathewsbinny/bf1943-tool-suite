# BF1943 Tool Suite

Tools for converting, inspecting and building AI data for Battlefield 1943 (XBLA). They support the native PC client
(project-opfor-bf1943) and the research behind it (operation-rising-sun); both are separate, local projects.
Repository: https://github.com/mathewsbinny/bf1943-tool-suite

This suite holds **new BF1943 tools** and **modified copies of OPFOR tools**. OPFOR's own
`bfbc2-tool-suite` (in `C:\Games\Project OPFOR Toolkit`) stays dedicated to BFBC2 and is never changed:
tools are copied out of it with `tools\import_opfor.py`, which records every source file's hash in
[provenance/opfor-imports.json](provenance/opfor-imports.json), and are changed only here.

**Status:** every OPFOR tool is copied (from `bfbc2-tool-suite` commit `fab30c0`, 114 files) and rewired to the
`bf1943-*` folder names; logs and window layouts go to `%LOCALAPPDATA%\bf1943-tool-suite` and
`%APPDATA%\bf1943-tool-suite`. The tools are still BFBC2 tools inside: they read Project OPFOR's BFBC2 copy and
BFBC2's formats until each is adapted to BF1943. OPFOR's own README and design are kept in
[docs/opfor-bfbc2](docs/opfor-bfbc2/) as reference. Built `.exe` files are not copied (OPFOR does not commit them);
build them here with each tool's `build.bat`.

## Layout

Mirrors the BFBC2 suite: one shared core and a folder per tool. `bf1943-converter/` (the native PC conversion) is new and not created yet.

| Folder | Tool | Starting point |
|---|---|---|
| `core/` | shared readers (DBX, bundles, terrain), level loader, 3D view | OPFOR `core/`, adapted to BF1943's formats |
| `bf1943-fbrb-tool/` | unpack and repack BF1943 bundles (FbRB directory v2) | OPFOR FbrbTool; BF1943 layout from rising-sun `src/assetresearch` |
| `bf1943-dbx-tools/` | view, edit and check binary `.dbx` | OPFOR DbxEdit; rising-sun `dbx.py` |
| `bf1943-console-readers/` | read-only STFS package and XEX readers | OPFOR console readers |
| `bf1943-auto-navkit/` | navigation data for BF1943 levels | OPFOR NavKit |
| `bf1943-map-inspector/`, `bf1943-navigation-studio/` | 3D views of levels and navigation data | OPFOR tools of the same names |
| `bf1943-xenia-kit/` | **ready:** turns your own BF1943 package into a Xenia setup with offline Quick Match and local matches (`xeniakit.py build`) | new; findings from rising-sun `src/xeniaprobe`, `src/uiresearch` |
| `bf1943-apt-tools/` | **ready:** menu movie (APT) reader, disassembler and rewriter | rising-sun `src/uiresearch/apt.py` |
| `bf1943-spawn-map/` | **ready:** each level's bases and flags with the soldier and vehicle spawns they own (`spawnmap.py`) | new; uses `core/bfbc2core/dbx.py` and `bf1943-fbrb-tool` |
| `bf1943-converter/` | the end-user toolkit: verify the user's copy, convert it to the PC build | new |

## Rules

- Game data comes from the user's own copy and is read where it is. Nothing EA-owned is shipped.
- Copies from OPFOR keep their provenance entry. Changes to a copy are ordinary commits here.
- Copies from operation-rising-sun stay in place there too, so the evidence paths remain valid.
- No network traffic.

## Importing from OPFOR

```
python -B tools\import_opfor.py <path in bfbc2-tool-suite> <path here> --note "why"
python -B tools\import_opfor.py --plan tools\opfor-import-plan.json
```

Files come from OPFOR's committed state (HEAD) through read-only git commands, so uncommitted work there and
ignored build outputs are never copied. It refuses to overwrite. To take a later OPFOR change, diff OPFOR's
commit against the recorded one and apply it here by hand.
