# BF1943 DBX tools (DbxEdit)

Part of the BF1943 Tool Suite. **BF1943 status: partly** (2026-10-23) - DbxEdit and DbxEditGUI read, write and
edit BF1943's binary DBX partitions; the level check's rules and the slot fixer are BFBC2's.

**Tested on BF1943** (all 4,062 partitions of Wake Island's bundles and the shared level bundle, 2026-10-23;
the suite's `tests\test_bf1943_tools.py dbxedit` repeats it on 1,133 of them):
- every partition round-trips byte for byte (`roundtrip`);
- DbxEdit's XML equals core `dbx.py`'s on every one (`xml`);
- the level check reports 0 errors on Wake Island's level partition (`check`).

**Not verified on BF1943:** the rules of `check` and the slot fixer (`fixslots`) were derived from crashes of
BFBC2 PC. A clean check on a BF1943 level means only that none of BFBC2's problems were found.

**Workflow for BF1943:**
1. Extract the partitions from a bundle: `python ..\bf1943-fbrb-tool\fbrb1943.py extract <bundle.fbrb> <out dir> [filter]`.
2. Edit them with DbxEdit (commands or the window), or convert to XML and back.
3. Put them back into a copy of the bundle: `python ..\bf1943-fbrb-tool\fbrb1943.py replace <bundle.fbrb> <out.fbrb> <entry name>=<file> ...`.
   The game folder is only read; the copy goes elsewhere.

DbxEdit runs on .NET Framework, which cannot open paths longer than 260 characters. BF1943's partition names are
long: extract into a short folder (for example `C:\dbx`).

What follows is OPFOR's DbxEdit description. Its counts and the level check's crashes are BFBC2's.

**DbxEdit** reads, converts, checks and edits binary `.dbx` files (Frostbite 1 data as a tokenised XML tree), from
the command line or in a window (**DbxEditGUI**). The format is the same in BFBC2 (PC and Xbox 360) and BF1943.

| Command | What it does |
|---|---|
| `DbxEdit xml` | binary `.dbx` to readable XML |
| `DbxEdit import` | XML back to binary `.dbx` |
| `DbxEdit edit` | change binary files directly with commands (drop, graft, copy, set, ...) |
| `DbxEdit check` | find what crashes or breaks a level, without starting the game |
| `DbxEdit fixslots` | repair cull slots (houses and vehicles turning see-through) |
| `DbxEdit roundtrip` | read and rewrite files, report any that would change |
| `DbxEditGUI` | all of this in a window |

Anything DbxEdit doesn't change is written back byte for byte. [FORMAT.md](FORMAT.md) describes the format
and everything DbxEdit does; the code is written from it.

## Build

Run `build.bat`. It compiles `DbxEdit.exe` and `DbxEditGUI.exe` into this folder with the C# compiler
that ships with Windows (.NET Framework 4) and needs no install.

## The window

Start `DbxEditGUI.exe` and open a `.dbx` file (File > Open, or drop it on the window).

- **The tree** shows the file: instances (bold) with their fields, lists and values. Lists open when you
  expand them.
- **Select a value** to edit it on the right. Numbers in a list are separated by `/`. Attributes, for
  example a reference, are edited in the table above it. **Apply** stores the change, and
  **Go to reference** jumps to the instance a reference points to.
- **Instance menu** (also on right-click): remove an instance, graft one from another file, copy a field
  from another file, add a list entry.
- **Edit menu:** undo (Ctrl+Z), find (Ctrl+F, then F3), replace strings, remove a field everywhere, set a
  bool everywhere, change the file's own GUID.
- **Level menu:** check the level (F5), check its cull slots against another level, fix cull slots. The
  results appear in the bottom pane; double-click a line to go to its instance.
- **File menu:** save (the first save keeps the original as `<name>.dbx.bak`), export or import XML, and
  **Save edit script**, which writes your edits as DbxEdit commands to replay on another file.

## Convert to XML and back

```
DbxEdit xml <file.dbx | folder> [more ...]
DbxEdit import <file.xml> <out.dbx> [--like <original.dbx>] [--types <file.dbx | folder>]...
```

`xml` converts every binary `.dbx` file it finds (folders are searched recursively) and skips plain-XML
ones. Empty files are skipped and counted, not reported as errors: they are bundles' `*deleted*` entries,
which OPFOR's BFBC2 FbrbTool versions before 2026-10-16 unpacked as zero-byte files (current ones list them in
`fbrb.manifest` only). `roundtrip` counts them the same way.

The XML doesn't record every type. For example, `1` may be the integer 1 or the decimal 1.0. So `import`
takes the types from **the original file**: `--like`, or `<name>.dbx` next to the XML.

- **Unchanged values** keep their exact bytes, so an unedited XML gives back the original file exactly.
- **Changed values** keep their field's type. Decimals with more digits than the game stores are rounded,
  and the import reports how many.
- **New elements** take their type from the same field elsewhere in the file. Add more files to learn
  from with `--types`.
- **Guesses:** anything still unknown is guessed from the XML, every guess is listed, and the exit code
  is 3. Without the original file most values are guesses, so keep it next to the XML.

## Check a level

```
DbxEdit check <level.dbx> [--slots-from <level.dbx>] [--bundle <unpacked folder>]...
```

It reports, with the crash each one causes:

- **empty or unresolved entries in the level's object list:** the game crashes while loading;
- **model groups and their members without physics or mesh data:** the game crashes at the model-group
  set-up;
- **references to instances the file doesn't have;**
- **terrain, model groups or forests missing from the object list:** they aren't loaded;
- **objects that share a cull slot:** they turn see-through together.

Two options add more checks:
- `--slots-from` names the level whose cull trees this one uses, to find slots that don't match it.
- `--bundle` names unpacked bundle folders, to check references into other files.

The exit code is 1 if there are errors.

```
DbxEdit fixslots <in.dbx> <out.dbx> --from <level.dbx>
```

`fixslots` gives every placed object the cull slot of the same object in the level the cull trees belong
to. Objects are matched by GUID, else by blueprint and position, else by the nearest unused object of the
same blueprint within 50 m. Objects with no match get slot 0 (outside the tree).

## Edit with commands

```
DbxEdit roundtrip <file.dbx | folder>         parse and rewrite every file, report any that change
DbxEdit edit <in.dbx> <out.dbx> <command> ...  apply commands in order, write the result
```

Commands (a `@file` argument reads more commands from a file, one per line; `#` starts a comment):

| Command | What it does |
|---|---|
| `replace=<old>\|<new>` | every string exactly `<old>` becomes `<new>` (re-points references) |
| `replaceprefix=<old>\|<new>[\|<except>...]` | strings starting with `<old>` get `<new>` as prefix |
| `drop=<guid>` | removes that instance; array items pointing to it go, other references become `null` |
| `dropfield=<name>` | removes every field called `<name>` |
| `graft=<src.dbx>\|<guid>[\|<before guid>]` | copies an instance from another file, with every instance of that file it references |
| `copyfield=<src.dbx>\|<src guid>\|<field>\|<guid>` | copies one field (arrays included) from another file's instance |
| `additem=<guid>\|<array>\|<ref>[\|<before ref>]` | adds a reference to an array field of an instance |
| `setfield=<guid>\|<field>\|<value>` | sets a text field or a single 4-byte number of an instance (not a reference - see below) |
| `set=<field>\|<true\|false\|0-255>` | sets every bool / byte field called `<field>` |
| `partguid=<guid>` | sets the partition's own GUID (for a copy under a new name) |

The exact rules for each command are in [FORMAT.md](FORMAT.md).

**Known limitation - `setfield` on a reference field.** It reports `setfield: 1 field(s)` but writes the value as
the element's text and leaves the reference unchanged: after
`DbxEdit edit in.dbx out.dbx "setfield=<guid>|Template|objects/x/y/<guid>"`, `DbxEdit xml out.dbx` shows
`<field name="Template" ref="<old ref>">objects/x/y/<guid></field>`. To change a reference, use
`copyfield=<src.dbx>|<src guid>|<field>|<guid>` from an instance that already holds the wanted one.

**Known limitation - `import` gives new fields no type from `--types`.** A field the reference does not match
(for example in an instance with a new guid) is typed by guess even when the same field exists in the `--types`
files: after `DbxEdit import new.xml out.dbx --types SectorOffsets.dbx` (new.xml: a PathdataSectorOffsets instance
with a new guid, `<field name="XOffset">-3</field>`), the import reports "no type known, taken as decimal" and writes
a 4-byte float where the game stores a 4-byte integer. Workaround: write the XML with the reference's own guids and
import with `--like` (the fields are then changed values and keep their type), then give the result its own guids
with `edit "replace=<old instance guid>|<new>" "partguid=<new>"`. The reference's string table is kept, so its
now-unused strings stay in the file.

## Errors and logs

DbxEdit, DbxEditGUI and LevelDiff (which builds on `DbxEdit.cs`) keep a log in
`%LOCALAPPDATA%\bf1943-tool-suite\logs\` (`ToolLog` in `DbxEdit.cs`), as all the suite's tools do. An unexpected
error is explained - the likely cause (a file in use, read-only, missing, not a binary .dbx or damaged, out of
memory) and what to do - with the log's path: on the command line with exit code 1, in DbxEditGUI in a window
(the window keeps running). Files skipped in a folder conversion are logged as warnings.

## Tests

`python -B ..\tests\test_bf1943_tools.py dbxedit` (in the `bf1943` tier of `run_tests.py`; it runs `build.bat` if
`DbxEdit.exe` is missing): DbxEdit on Wake Island's partitions, as above, and LevelDiff on a partition against
itself. BF1943's `.dbx` partitions are also checked by the Python reader (the suite's `tests\test_bf1943_data.py dbx`).
DbxEdit's own tests were BFBC2's (its unit tests, the PC game's files and their fingerprints) and were removed with
the suite's other BFBC2 tests; they are in OPFOR's `bfbc2-tool-suite` and in this repository's history.

## Tested (BFBC2, in OPFOR)

On 43,290 unique `.dbx` files (PC, Xbox 360 and Linux server data, plus edited levels):

- **`roundtrip`:** every binary file comes back byte for byte.
- **`xml`:** byte-identical to the earlier DbxTool (a port of Frankelstner's `dbx.py`), which this
  replaces. The edit commands give output, messages and exit codes identical to the earlier DbxEdit.
  This was tested on every command, alone and combined, on 12 levels, plus a real 9,000-command level
  merge.
- **`import`:**
  - all 43,289 binary files come back exactly through `xml`, then `import` with the original;
  - on 1,731 files with simulated edits (values changed, instances removed and copied), the result
    contains exactly the edits, with every type kept;
  - without the original, no value differs that wasn't reported as a guess.
- **`check` and `fixslots`:** on the stages of a real level port, each stage was flagged with the
  problems it had (an empty object-list entry, model groups without physics, missing world objects, wrong
  cull slots), with no false alarms on stock levels. `fixslots` reproduced a hand-made slot repair byte
  for byte.
- **The window:** opening, editing, saving, undo and the level check were driven from a test program;
  saving gives the same bytes as the command line.

## Credits

The binary DBX format was worked out by the BC2 modding community, above all by **Frankelstner** (his
`dbx.py` scripts); **Heico**'s BFBC2 Toolkit was a format reference. DbxEdit's code is this project's
own, written from [FORMAT.md](FORMAT.md). Developed for Project OPFOR. Battlefield: Bad Company 2 is
© Electronic Arts / DICE.
