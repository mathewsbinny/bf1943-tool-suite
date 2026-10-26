# BF1943 DBX tools (DbxEdit)

Part of the BF1943 Tool Suite. **BF1943 status: yes** (2026-10-26) - DbxEdit and DbxEditGUI read, write and
edit BF1943's binary DBX partitions. The level check uses BF1943's own rules (see [Check a level](#check-a-level)),
and the slot fixer repairs BF1943's cull slots.

**Tested on BF1943** (all 4,062 partitions of Wake Island's bundles and the shared level bundle, 2026-10-23;
the suite's `tests\test_bf1943_tools.py dbxedit` repeats it on 1,133 of them):
- every partition round-trips byte for byte (`roundtrip`);
- DbxEdit's XML equals core `dbx.py`'s on every one (`xml`);
- the level check with BF1943's rules reports nothing (0 errors, 0 warnings) on all five shipped level partitions,
  and with `--bundle` on each of them every reference into its bundles holds its declared class and the static cull
  trees match the level (`check --bundle`);
- broken copies of Wake Island fail with their rule's message, one per rule; `fixslots` repairs swapped cull slots
  byte for byte (2026-10-26, `tests\test_levelcheck1943.py`).

**What a clean check means:** the level breaks none of the rules that every shipped BF1943 level keeps. The rules
come from the game's data, not from crashes. No broken level has been run in the game, so what the game does with a
broken rule is a hypothesis: errors mark data inconsistent with itself or with BF1943's classes, warnings mark a
level unlike every shipped one.

**Workflow for BF1943:**
1. Extract the partitions from a bundle: `python ..\bf1943-fbrb-tool\fbrb1943.py extract <bundle.fbrb> <out dir> [filter]`.
2. Edit them with DbxEdit (commands or the window), or convert to XML and back.
3. Put them back into a copy of the bundle: `python ..\bf1943-fbrb-tool\fbrb1943.py replace <bundle.fbrb> <out.fbrb> <entry name>=<file> ...`.
   The game folder is only read; the copy goes elsewhere.

DbxEdit runs on .NET Framework, which cannot open paths longer than 260 characters. BF1943's partition names are
long: extract into a short folder (for example `C:\dbx`).

What follows is OPFOR's DbxEdit description, with the level check rewritten for BF1943. The counts under "Tested
(BFBC2, in OPFOR)" and the crashes of the BFBC2 rules are BFBC2's.

**DbxEdit** reads, converts, checks and edits binary `.dbx` files (Frostbite 1 data as a tokenised XML tree), from
the command line or in a window (**DbxEditGUI**). The format is the same in BFBC2 (PC and Xbox 360) and BF1943.

| Command | What it does |
|---|---|
| `DbxEdit xml` | binary `.dbx` to readable XML |
| `DbxEdit import` | XML back to binary `.dbx` |
| `DbxEdit edit` | change binary files directly with commands (drop, graft, copy, set, ...) |
| `DbxEdit check` | find what breaks a level, without starting the game (BF1943's or BFBC2's rules) |
| `DbxEdit fixslots` | repair cull slots (houses and vehicles turning see-through) |
| `DbxEdit roundtrip` | read and rewrite files, report any that would change |
| `DbxEditGUI` | all of this in a window |

Anything DbxEdit doesn't change is written back byte for byte. [FORMAT.md](FORMAT.md) describes the format
and everything DbxEdit does; the code is written from it.

## Build

Run `build.bat`. It compiles `DbxEdit.exe` and `DbxEditGUI.exe` into this folder with the C# compiler
that ships with Windows (.NET Framework 4) and needs no install.

### The schema

`DbxEdit.cs` holds BF1943's reflection schema for the level check, in the block between `BEGIN GENERATED BF1943
SCHEMA` and `END GENERATED BF1943 SCHEMA`. It lists 952 classes (with their base classes), 211 structs (value types) and
188 enums. Each class or struct comes with its 6,154 members in all (a member's type when it is a class, struct or
enum, and an array's element type); each enum with its value names.

`python -B schema1943.py [<bf1943-reflection-schema-full.json>]` writes the block and records both files' SHA-256;
then run `build.bat`. It reads the research workspace's `research\evidence\bf1943-reflection-schema-full.json`
(`BF1943_RESEARCH`, else `operation-rising-sun` next to the suite). That file is the schema read from the game's
executable by `src\xexresearch\dump_reflection.py`, completed by `dump_reflection_dynamic.py`:
- **Member records written at start-up.** 1,746 member records are zero in the executable's image; straight-line
  code writes them when the game starts (for LevelData, at 0x82a70ae8, outside every `.pdata` function). The script
  reads them from those constant stores: all 1,746 recovered, none conflicting.
- **The value types.** They are registered by another function (0x8262e198) that the first script did not read.
- **Array element types** (word 3 of an array member's record).

Before completion, 17,986 of the levels' 112,047 instance fields had no member in the schema. All but the 20
data-only fields below are now covered.

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
DbxEdit check <level.dbx> [--rules bf1943|bfbc2] [--slots-from <level.dbx>] [--bundle <unpacked folder>]...
```

The check has two rule sets and says which one it used. Without `--rules` it takes them from the file: BFBC2's when
the file's LevelData has BFBC2's fields (`DataConnections`, `Descriptor` or `MemoryReportGroup`: every BFBC2 PC level
has them, no BF1943 level does), else BF1943's. The window (Level menu) chooses the same way.

### BF1943's rules

Each rule holds in all five shipped BF1943 level partitions (`wake_island_s`, `guadal_canal`, `iwo_jima_s`,
`coral_sea`, `wake_island_tutorial`). The research workspace's `src\assetresearch\derive_level_rules1943.py` derives
them from those partitions and the reflection schema read from the game's executable (completed: see
[The schema](#the-schema)), and records the evidence of
each (levels, counts, types, fields) in `research\evidence\bf1943-level-check-rules.json`. The rules come from data,
not crashes: what the game does with a broken one has not been tried.

**Errors** (exit code 1) - the file is inconsistent with itself or with BF1943's classes:

| Rule | Evidence in the five levels |
|---|---|
| instance GUIDs are unique | 12,166 instances |
| every instance's type is a BF1943 class (the reflection schema) | 73 types, all reflected |
| exactly one LevelData, and it is the partition's `primaryInstance` | 5 of 5 |
| LevelData `Objects` has no null or unresolved entry *(BFBC2's rule; its crash is BFBC2's)* | 8,361 entries |
| every local reference names an instance of the file *(BFBC2's)* | 18,802 references |
| a reference in a class-typed field points to that class or a subclass - the field's class is the schema's | 3,789 references, 19 fields |
| the same for references into other files, with `--bundle` | 6,447 references |
| the items of 29 arrays hold what the shipped levels put there: the nearest common schema base, for example `CapturePointEntityData.Spawners` a `SpawnEntityData`, `*.Geometry` a geometry entity, `LevelData.Objects` and event connections a `GameObjectData` | every item of those arrays |
| model groups, forests and their members have `PhysicsData`, `MeshEntityType` and `MemberType` (terrain may leave `PhysicsData` null) *(BFBC2's, `MemberType` added)* | 21 groups, 130 members |
| a group's members are its kind: static, rigid or middlegrowth model group member, or tree member for the forest | 21 groups |
| a member's `InstanceCount` is the number of its `SpawnDatas` | 130 members |
| a group's members' physics parts follow each other: the first `FirstPhysicsPart` is 0, each next one is the previous member's `PhysicsPartCount` (an end index, not a count), and `PhysicsPartCount - FirstPhysicsPart = InstanceCount x PhysicsPartCountPerInstance` | 130 members |
| an array item that is a reference points to the array's element class (the schema's) or a subclass | 10,775 references |
| a field of an enum type holds one of the enum's value names (for example `Team1`, `TeamNeutral`) | 1,823 fields |
| with `--bundle`: the client cull tree has one leaf per model-group and forest instance (`SpawnDatas`), numbered 1..N, and `EnumOffset` 0 | 5 of 5 (N = 2,505-3,852) |
| with `--bundle`: the exclusive cull tree's leaves are numbered 1..L and its `EnumOffset` is N | 5 of 5 (L = 540-2,414) |
| with `--bundle`: every placed object's slot is in `EnumOffset`+1 .. `EnumOffset`+L - the highest slot fits the tree | 4,595 slotted objects |

**Warnings** - the level is unlike every shipped one:

| Rule | Evidence in the five levels |
|---|---|
| terrain, forests and model groups are listed in `Objects` *(BFBC2's; BFBC2's static envmap is not in BF1943 levels)* | 5 of 5 |
| each instance is listed in `Objects` once | 8,361 entries |
| the cull slots of the model groups' and forest's instances (`SpawnDatas` `Enumeration`) are 1..N, each once | 14,924 instances |
| placed objects' (`ReferenceObjectData`) non-zero slots are unique and above N *(BFBC2's uniqueness; BF1943 adds the group instances)* | 4,595 slotted of 5,855 |
| a blueprint's placed objects are all slotted or all slot 0 | 119 blueprints |
| `Objects` lists three teams: TeamNeutral, Team1, Team2 | 5 of 5 |
| one ticket counter for Team1 and one for Team2 | 5 of 5 |
| Team1 and Team2 each have a base: a listed capture point with `UnableToChangeTeam` true and `InitialOwnerTeam` theirs | 5 of 5 |
| every listed soldier spawn is in a listed capture point's `Spawners` (BF1943's spawns are TeamNeutral and take the capture point's side) | 31 spawns |
| event connections, spawners, map markers, alternative spawn points, trigger geometry and the entities triggers watch point at listed instances (a sound area's outer geometry is exempt) | 4,538 references |
| exactly one terrain, combat area, human player, auto team, pre-round, mission and hot zone are listed | 5 of 5 |
| LevelData `Name` is `Levels/<its id>` | 5 of 5 |
| every field (of an instance, of a by-value struct, of an array element) is a member of its class or struct - except 20 fields the shipped data carries that BF1943's classes do not have (below) | 160,109 fields |
| with `--bundle`: in slot order, the slotted placed objects cover the exclusive tree's leaves exactly - two leaves when the blueprint's partition has a `PhysicsEntityData`, else one | 4,470 objects with two, 125 with one |
| with `--bundle`: an object lies within 6.2 x `SphereKeyW` / 16 of its leaf's centre (`SphereKeyX/Y/Z` / 16); the bound is fitted to the shipped levels (largest 6.16) | 4,595 objects; with the slots shuffled 2-5 per level pass |

**Not rules** (a shipped level breaks them): capture points' `Index` unique (the tutorial repeats 1, 5 and 10);
every capture point has spawners (Coral Sea's south base and a tutorial hangar ship have none); a live session and a
trial validator (the tutorial has neither); every entity listed (four levels keep unlisted ones, Coral Sea three
whole capture points). Not checked, though every shipped level has them: an empty `PathfindingTypes` and `HasAI` false
(navigation and bots will change them).

**Cull slots** (FORMAT.md section 7 gives the cull trees' format). A level's slots are the leaves of its two static
cull trees, the partitions `levels/<level>/staticculltree_client` and `staticculltree_exclusive` that LevelData's
`ClientStaticCullTree` and `ExclusiveStaticCullTree` name:
- Slots 1..N are the client tree's leaves: one per model-group and forest instance (`SpawnDatas` `Enumeration`).
- Slots N+1..N+L are the exclusive tree's leaves (`EnumOffset` N): the placed objects. Every one fits; the last
  object's last leaf is the tree's last leaf on every level.
- Placed objects' slots skip numbers because an object takes two leaves when its blueprint has physics (4,470 objects:
  static and rigid models and ladders with a `PhysicsEntityData`), one when it has none (125: skydomes, wakes, a few
  props). Both leaves have the same centre and size. No slot is free.
- Slot 0 (1,260 objects) is outside the trees: dynamic models, effects, bangers and weather systems.
- **So a new static object cannot get a valid slot:** the exclusive tree has no free leaf, and none can be added
  without rebuilding the tree. `fixslots` gives it 0 (outside the trees); what the game then draws is not tried.
- BFBC2 PC's `mp_005` has the same layout (client leaves = its 2,292 group instances, exclusive `EnumOffset` 2,292,
  slots up to 5,975 = 2,292 + 3,683). This is a lead only.
- **Open:** what the second leaf is for (collision or destruction are guesses) and the node's `Max`; the exact scale of
  `SphereKeyW` (the 6.2 bound is empirical).

**Data-only fields.** The shipped levels carry 20 fields (6,060 times) that BF1943's classes and structs do not have:
- `ReferenceObjectData.IsInObjectSet` (5,855 times);
- `IsObsolete` and `ReplacementObject` on terrain, forest and model groups;
- `ConnectionPair.FromData` and `ToData`;
- `HavokAsset.SourceFile` and `RootNode`;
- `LevelData.Parts`;
- `TeamEntityData.Squads`;
- `RichPresenceConnection.RichPresence`;
- `RichPresenceContextPair.Context` and `Value`.

Six of the names (`IsInObjectSet`, `IsObsolete`, `ReplacementObject`, `FromData`, `ToData`, `Context`) appear nowhere in
the executable. The others are members of other types only. The game loads these levels, so it skips such fields. The
check accepts these 20 and warns about any other field the game's classes don't have.

### BFBC2's rules

`--rules bfbc2`, or a BFBC2 file. They report, with the crash each one causes in BFBC2 PC:

- **empty or unresolved entries in the level's object list:** the game crashes while loading;
- **model groups and their members without physics or mesh data:** the game crashes at the model-group
  set-up;
- **references to instances the file doesn't have;**
- **terrain, model groups, forests or the static envmap missing from the object list:** they aren't loaded;
- **objects that share a cull slot:** they turn see-through together.

### Options

Two options add more checks (both rule sets):
- `--slots-from` names the level whose cull trees this one uses, to find slots that don't match it.
- `--bundle` names unpacked bundle folders, to check references into other files (with BF1943's rules, also their
  class, and the level against its static cull trees: unpack the level's own bundle too). BF1943's shipped levels name a few partitions that are in none of their bundles (Wake Island: two minimap
  meshes); they are warnings.

The exit code is 1 if there are errors.

```
DbxEdit fixslots <in.dbx> <out.dbx> --from <level.dbx>
```

`fixslots` gives every placed object the cull slot of the same object in the level the cull trees belong
to. Objects are matched by GUID, else by blueprint and position, else by the nearest unused object of the
same blueprint within 50 m. Objects with no match get slot 0 (outside the tree). It works on BF1943 levels as on
BFBC2's: on a copy of Wake Island with two placed objects' slots swapped it gives the original back byte for byte.
It changes placed objects only; the model groups' instance slots are left as they are.

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

`python -B tests\test_levelcheck1943.py` (also in the `bf1943` tier; it runs `build.bat` if `DbxEdit.exe` is missing
or older than the code): the level check on all five shipped level partitions (BF1943's rules from the file, nothing
reported), each with `--bundle`, and 25 broken copies of Wake Island (plus one of its exclusive cull tree) - made with `xml`, a text change and
`import` in a temporary folder - each failing with its rule's message (errors exit 1, warnings 0); `--rules bfbc2` and
a LevelData with BFBC2's fields choosing BFBC2's rules; `--slots-from` and `fixslots` on swapped cull slots.
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
