# LevelDiff (BF1943 Tool Suite, diff workbench)

**BF1943 status: ready** (2026-10-23) - it reads BF1943's binary partitions through DbxEdit's code. Tested on
BF1943: Wake Island's level partition against itself shows no change (the suite's `tests\test_bf1943_tools.py dbxedit`).

Compares two binary `.dbx` partitions instance by instance: what was added, what was removed, and which fields
changed. It works on levels and on any other partition. Typical uses:

- a level before and after an edit with DbxEdit;
- two partitions of the game, for example two levels' shared blueprints.

Get the partitions out of a bundle with `..\..\bf1943-fbrb-tool\fbrb1943.py extract`. Like DbxEdit it runs on
.NET Framework, which cannot open paths longer than 260 characters: extract into a short folder.

## Build

Run `build.bat`. It compiles `LevelDiff.exe` with the C# compiler that ships with Windows. It reads files
with the suite's DbxEdit code (`..\..\bf1943-dbx-tools`; or set `DBX_TOOLS` to
that folder). The hub and the tests build it through core `csbuild.py`: again whenever `LevelDiff.cs`, the
compiled-in `DbxEdit.cs` or `build.bat` is newer than the exe or differs from what it was built from.

## Use

```
LevelDiff <a.dbx> <b.dbx> [options]
```

| Option | What it does |
|---|---|
| `--match-objects` | also pair placed objects whose GUIDs differ, by blueprint and position (builds of one map often re-create them) |
| `--type <text>` | only instances whose type contains `<text>`, e.g. `--type ReferenceObjectData` |
| `--ignore <field>` | leave a field out, e.g. `--ignore Enumeration` (repeatable) |
| `--summary` | counts only |
| `--all` | list everything (default: the first 40 per section) |
| `--json <file>` | also write the full result as JSON |

The exit code is 0 if the files have no differences, 1 if they do, 2 for bad input.

Instances are matched by GUID. Each changed instance lists its fields as `path: old  ->  new`. Lists of
references (like a level's `Objects`) are compared as sets: `-` for entries only in A, `+` for entries only
in B. A list with the same entries in another order is reported as such (each list on its own: a reorder is shown
even when another list of the instance gained or lost entries).

Example: Wake Island's level partition before and after an edit (extracted into `C:\dbx`, edited into
`C:\dbx-edited`):

```
python ..\..\bf1943-fbrb-tool\fbrb1943.py extract <game>\levels\wake_island_s\reduced-level-00.fbrb C:\dbx levels/wake_island_s
LevelDiff C:\dbx\levels\wake_island_s.dbx C:\dbx-edited\levels\wake_island_s.dbx --type ReferenceObjectData
```

A partition compared with itself reports `changed: 0` with `--summary`.

## Errors and logs

LevelDiff keeps a log in `%LOCALAPPDATA%\bf1943-tool-suite\logs\` like the suite's other tools (`ToolLog`, shared
with DbxEdit). A file it cannot read, or one that is not binary `.dbx`, is bad input: it prints the cause (and, for
a read failure, what to do and the log's path) and exits with code 2. An unexpected error is explained the same way
and exits with code 1 (the code for "differences" too). Tests: `tests\test_bf1943_tools.py dbxedit` in the suite (the `bf1943` tier); `bf1943-dbx-tools\tests\test_csharp_tools.py`
(quick tier: reorders on made-up partitions); its BFBC2 tests were removed.

## Credits

Developed for Project OPFOR. The DBX format knowledge comes from the BC2 modding community, above all
Frankelstner. Battlefield: Bad Company 2 is © Electronic Arts / DICE.
