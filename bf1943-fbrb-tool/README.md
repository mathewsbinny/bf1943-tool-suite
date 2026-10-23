# BF1943 bundle tools: fbrb1943.py and FbrbTool

Part of the BF1943 Tool Suite. **BF1943 status: ready for BF1943 bundles through `fbrb1943.py`** (see
[BF1943 bundles](#bf1943-bundles-python) below; used by the Xenia kit, the spawn map and NavKit's big-endian
variant). `FbrbTool.exe` is OPFOR's BFBC2 tool, not adapted yet: it reads Battlefield: Bad Company 2's PC
`.fbrb` bundles and is not tested on BF1943's Xenon bundles.

**FbrbTool** is a command-line tool to unpack and repack Battlefield: Bad Company 2's `.fbrb` bundles
(Frostbite 1 archives: the game's levels, menus and shared data). The counts below are BFBC2's.

## Build

Run `build.bat`. It compiles `FbrbTool.exe` into this folder with the C# compiler that ships with
Windows (.NET Framework 4) and needs no install.

## Use

```
FbrbTool list   <file.fbrb>                list the entries
FbrbTool unpack <file.fbrb> <outdir>       write every entry under <outdir>, plus fbrb.manifest
FbrbTool pack   <dir> <file.fbrb>          rebuild a bundle from an unpacked folder
```

`unpack` writes `fbrb.manifest`, which lists the entries in archive order with their type, flags and
sizes. `pack` uses it to rebuild the bundle faithfully. To change a bundle, unpack it, edit or add files
(add new ones to the manifest), then pack it.

Zero-byte entries are `*deleted*` markers: they remove shared data from the bundle's level (mp_005's
level bundle has 4,334 of its 6,692 entries as markers; all bundles together 286,055). `unpack` lists them in
`fbrb.manifest` but writes no file for them, so other tools do not meet thousands of empty files that look
broken; `pack` restores them from the manifest, and the rebuilt bundle's header and payload are identical
to the original's. Folders unpacked by older versions, which hold the markers as empty files, pack the same.

## Errors and logs

FbrbTool keeps a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools. When it cannot do
a job it says why in words - a file that is not an FbRB bundle or is damaged, a file in use, a missing file,
a read-only folder - what to do, and where the details are; exit code 1.

## Credits

The format follows **Frankelstner**'s fbrb script and **Heico**'s BFBC2 Toolkit. Developed for
Project OPFOR. Battlefield: Bad Company 2 is © Electronic Arts / DICE.

## BF1943 bundles (Python)

`fbrb1943.py` reads and writes BF1943 Xenon FbRB bundles (directory version 2): `Bundle(raw).data(name)`, and `build({name: bytes})` to replace entries. An unchanged rebuild reproduces the decompressed directory and payload byte-for-byte for 82 of the 85 bundles in BF1943. It refuses the other three (two async bundles and build_overlay), whose layout differs. Source and provenance: `operation-rising-sun/src/assetresearch/fbrb.py`, `provenance/rising-sun-imports.json`.
