# Project OPFOR BF1943 Tool Suite

Tools for converting, inspecting and building AI data for Battlefield 1943 (XBLA), for the native PC client in
[project-opfor-bf1943](../project-opfor-bf1943/README.md). The research and evidence behind them live in
[operation-rising-sun](../operation-rising-sun/README.md).

This suite holds **new BF1943 tools** and **modified copies of OPFOR tools**. OPFOR's own
`bfbc2-tool-suite` (in `C:\Games\Project OPFOR Toolkit`) stays dedicated to BFBC2 and is never changed:
tools are copied out of it with `tools\import_opfor.py`, which records every source file's hash in
[provenance/opfor-imports.json](provenance/opfor-imports.json), and are changed only here.

**Status: set up.** No tools imported yet.

## Planned layout

Mirrors the BFBC2 suite: one shared core and a folder per tool.

| Folder | Tool | Starting point |
|---|---|---|
| `core/` | shared readers (DBX, bundles, terrain), level loader, 3D view | OPFOR `core/`, adapted to BF1943's formats |
| `bf1943-fbrb-tool/` | unpack and repack BF1943 bundles (FbRB directory v2) | OPFOR FbrbTool; BF1943 layout from rising-sun `src/assetresearch` |
| `bf1943-dbx-tools/` | view, edit and check binary `.dbx` | OPFOR DbxEdit; rising-sun `dbx.py` |
| `bf1943-console-readers/` | read-only STFS package and XEX readers | OPFOR console readers |
| `bf1943-auto-navkit/` | navigation data for BF1943 levels | OPFOR NavKit |
| `bf1943-map-inspector/`, `bf1943-navigation-studio/` | 3D views of levels and navigation data | OPFOR tools of the same names |
| `bf1943-xenia-probe/` | reference traces and offline patches in Xenia (research use) | rising-sun `src/xeniaprobe` |
| `bf1943-converter/` | the end-user toolkit: verify the user's copy, convert it to the PC build | new |

## Rules

- Game data comes from the user's own copy and is read where it is. Nothing EA-owned is shipped.
- Copies from OPFOR keep their provenance entry. Changes to a copy are ordinary commits here.
- Copies from operation-rising-sun stay in place there too, so the evidence paths remain valid.
- No network traffic.

## Importing an OPFOR tool

```
python -B tools\import_opfor.py bfbc2-dbx-tools bf1943-dbx-tools --note "base for BF1943 DbxEdit"
```

The copy skips `__pycache__`, `.pyc` and per-user `.ini` files. It refuses to overwrite an existing folder.
