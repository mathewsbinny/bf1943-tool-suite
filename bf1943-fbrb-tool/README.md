# BF1943 bundle tool: fbrb1943.py

Part of the BF1943 Tool Suite. **BF1943 status: ready** (2026-10-23) - `fbrb1943.py` reads and writes BF1943's
Xenon FbRB bundles (directory version 2), from Python and from the command line. It is used by the Xenia kit, the
spawn map, core's `game1943.py` and NavKit's big-endian variant.

OPFOR's `FbrbTool.cs` / `FbrbTool.exe` and its `build.bat` were removed from this folder: they read Battlefield:
Bad Company 2's PC `.fbrb` format, not BF1943's. They remain in OPFOR's `bfbc2-tool-suite` and in this
repository's history.

## Use

```
python fbrb1943.py list    <bundle.fbrb> [filter]      entries: size, type, name (filter: part of the name)
python fbrb1943.py info    <bundle.fbrb>               counts, sizes, compression
python fbrb1943.py extract <bundle.fbrb> <out dir> [filter]
python fbrb1943.py replace <bundle.fbrb> <out.fbrb> <entry name>=<file> [...]
```

- `extract` writes the entries (all, or those whose name contains the filter) as files under their names, and
  `fbrb.manifest.json`: the bundle's path and SHA-256, and per entry its name, type, flags, size and SHA-256.
- `replace` writes a copy of the bundle with the named entries replaced (names as `list` shows them). It refuses
  to write into the game folder (core `game1943.find_game`) or over the input bundle. A changed bundle goes
  elsewhere, for the Xenia kit or a mod folder to use.

From Python: `Bundle(raw).data(name)`, and `build({name: bytes})` to replace entries.

## Format

As observed in 82 of this build's 85 bundles: "FbRB", a u32 size of the gzip directory, the gzip directory, then
the payload (gzip when the directory's tail flag is 1). The directory holds the version (2), a string table, the
entries (name, flags, payload offset, size, size again, type name), a payload-compressed flag and the payload
size. The module docstring gives the exact layout.

The reader refuses the other three bundles (`async/ondemand_awards-00`, `async/ondemand_sounds-00`,
`build_overlay-00`), whose payload size or entry layout differs.

`build` rewrites only offsets and sizes. With no replaced entries it reproduces the original directory and
(decompressed) payload byte for byte. A compressed bundle's gzip bytes differ, since zlib is not the encoder
DICE's tools used; the game reads the decompressed content.

## Tests

- `python -B ..\tests\test_bf1943_data.py bundles`: the unchanged rebuild on all 82 bundles.
- `python -B ..\tests\test_bf1943_tools.py fbrb`: the command line on Wake Island's terrain bundle: `info`, `list`
  with a filter, `extract` with a manifest; `replace` writes a copy whose entry reads back, and refuses the game
  folder.

## Errors and logs

The command line keeps a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools. When it
cannot do a job it says why, with exit code 1.

## Source

Source and provenance: `operation-rising-sun/src/assetresearch/fbrb.py`, `provenance/rising-sun-imports.json`.
