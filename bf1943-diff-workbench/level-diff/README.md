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
| `--ignore <field>` | leave a field (or an attribute) out, e.g. `--ignore Enumeration` (repeatable) |
| `--summary` | counts only |
| `--all` | list everything (default: the first 40 per section) |
| `--json <file>` | also write the full result as JSON |

The exit code is 0 if the files have no differences, 1 if they do, 2 for bad input (an option without its value or
unknown, a file missing, damaged or not binary) or when the comparison could not run (for example a `--json` file
that cannot be written). The hub treats 0 and 1 as success.

Instances are matched by GUID: the n-th instance of a GUID in A with the n-th in B (a GUID repeated in a file pairs
once per instance); an instance without a GUID pairs with none. Each changed instance lists its fields as
`path: old  ->  new`, and its attributes other than `guid` (`(type)`, `(id)`, ...). A value is compared exactly - its
kind, count, width and bytes - not by its XML text: an integer 2 that became the decimal 2.0 (both shown as `2`) is a
change, shown with what differs (`Scale: 2  [1 number(s) of 4 bytes 00000002]  ->  2  [1 number(s) of 4 bytes 40000000]`).
The partition itself is compared too (unless `--type` is given), as `(partition)`: its attributes (`guid`,
`primaryInstance`, ...) and its children other than instances. Lists of
references (like a level's `Objects`) are compared as sets: `-` for entries only in A, `+` for entries only
in B. A list with the same entries in another order is reported as such (each list on its own: a reorder is shown
even when another list of the instance gained or lost entries). Each entry in both files (the n-th entry of a
reference in A with the n-th in B) is also compared exactly - its reference as written, its other attributes, its
value and anything below it - and a difference is shown as `List[<index in A>] -> <reference>` (`[i->j]` when it
moved); the list element's own attributes and byte are shown as `List: (list)`. Until 2026-10-31 only the references
were compared, so an entry's other attribute or value, or the list's own attribute, changed unseen ("changed: 0",
exit 0). Each shipped level's partition against itself still reports `changed: 0` (2026-10-31).

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
and exits with code 2 too (until 2026-10-30 it exited 1, the code for "differences"). The `--json` file is written
as DbxEdit writes (never into the game folder; beside the target, then moved into place). Files nested deeper than
1,000 containers are refused by name (DbxEdit's limit for its recursive commands). Tests: `tests\test_bf1943_tools.py
dbxedit` in the suite (the `bf1943` tier); `bf1943-dbx-tools\tests\test_csharp_tools.py` (quick tier, made-up
partitions: reorders, and in `csharp_regressions.py` partition and instance attributes, an integer turned decimal,
exit codes, GUID pairing, `--all` on 12,000 instances in well under a second - it was quadratic, 7.7 s for 8,000);
its BFBC2 tests were removed.

## Credits

Developed for Project OPFOR. The DBX format knowledge comes from the BC2 modding community, above all
Frankelstner. Battlefield: Bad Company 2 is © Electronic Arts / DICE.
