# Binary DBX format and DbxEdit's behaviour

This is the specification DbxEdit is written from. It describes Battlefield: Bad Company 2's binary `.dbx`
files, which BF1943 uses unchanged, and exactly what DbxEdit outputs. Counts of "game files" (section 6) are
BFBC2's; section 7 gives BF1943's level rules. The format knowledge comes from the BC2 modding community, first
of all **Frankelstner**'s research; the description and DbxEdit's code are this project's own.

All multi-byte header numbers are big-endian. A *varint* is an unsigned number in 7-bit groups, least
significant group first; the high bit of each byte means "another byte follows".

## 1. File layout

| Offset | Size | Content |
|---|---|---|
| 0 | 8 | ASCII `{binary}` |
| 8 | 4 | payload start (= 24 + string-area size) |
| 12 | 4 | a value kept as it is |
| 16 | 4 | string-area size *S* (= 4 × string count + size of the string bytes) |
| 20 | 4 | string count *N* |
| 24 | 4 × *N* | offset of each string, from the end of this table |
| 24 + 4*N* | | the strings: UTF-8, each ended by a zero byte |
| 24 + *S* | | payload: the element tree |

String 0 is the empty string in every game file. It cannot name a tag, because a tag index of 0 closes
the open element, so a new file starts its table with it.

A file is binary DBX if it is longer than 24 bytes and starts with `{binary}`. Other `.dbx` files are plain
XML already.

## 2. Element tree

The payload is a sequence of elements. Each element is:

1. **Tag:** a varint string index (the tag name, e.g. `partition`, `instance`, `field`, `array`, `item`,
   `complex`). Index 0 is not a tag: it closes the element that is currently open.
2. **Type and attribute count:** one byte, type in the high 4 bits, attribute count in the low 4 bits.
3. **Attributes:** that many pairs of varint string indexes (name, value), in order.
4. **Value**, by type:

| Type | Value |
|---|---|
| 0xA, container | one byte (kept as it is), then child elements up to a closing 0 |
| 2, text | a varint string index |
| 7, numbers | varint count *n*, varint size *s*, then *n* × *s* bytes, big-endian |
| anything else | two bytes; the second one is the value (bool or byte) |

The top level holds one `partition` container. A 0 where a tag would start at the top level ends the tree;
that byte and everything after it are kept as they are.

**Damaged files.** DbxEdit (every command, DbxEditGUI and LevelDiff) reads a file exactly as core `dbx.py` does and
refuses what it refuses (2026-10-30; before, DbxEdit read on and printed neighbouring bytes as values, or wrote XML
with unclosed elements):
- the offset table, the string area (*S*) or a string's zero byte not inside the file, the table not inside the
  string area, or a string ending outside the string area;
- a varint running past the end of the file, or longer than 64 bits;
- a tag, attribute or text string index past the table;
- an element header, container byte or two-byte value cut off by the end of the file;
- numbers 0 or 1 byte wide (an empty list of any width is fine), or *n* × *s* bytes running past the file;
- no element after the string table, or containers still open at the end of the file.

The tree is read and written without recursion, so `xml` and `roundtrip` take any nesting the format allows (an
export whose indentation would pass 2^28 tabs is refused). The commands that walk the tree recursively (`edit`,
`import` (counting the XML's containers, section 6), `check`, `fixslots`, DbxEditGUI, LevelDiff) take files nested up to 1,000 containers deep and refuse
deeper ones by name; every game file is far below that.

## 3. XML output (`DbxEdit xml`)

For every binary `.dbx` file, `<name>.xml` is written next to it, UTF-8 without a byte-order mark, lines
ended by CR LF:

- First line: `<?xml version="1.0"?>`.
- Each element starts on its own line, indented by one tab per open container.
- Opening: `<tag` then ` name="value"` for each attribute in file order.
- Container: `<tag ...>`; its closing line `</tag>` has the container's own indentation.
- Text: `<tag ...>text</tag>`, or `<tag ... />` when the text is empty.
- Numbers: `<tag ...>v1/v2/...</tag>` (an empty list gives `<tag ...></tag>`), with each value written as:
  - size 4: a whole number if the element's *first* attribute value is one of `OriginalHashedWaveName`,
    `HashedName`, `HashedWaveName`, `OnRoadMaterialNameHashes`, `Hash`, `Id`, `CompositeMeshPartNames`,
    or if the 32-bit value has a zero top byte, or if its eight exponent bits are all ones (a NaN or
    infinity pattern, either sign); otherwise a 32-bit float in .NET round-trip format (`R`). Whole
    numbers are the value read as a signed 32-bit integer;
  - size 8: a 64-bit float in round-trip format;
  - any other size: the first two bytes as an unsigned 16-bit number (the reader still advances by the size).
- Other types: `true` for 1, `false` for 0, else the number.
- In attribute values and text, `&`, `<`, `>` and `"` are written as `&amp;`, `&lt;`, `&gt;`, `&quot;`.
- Strings are decoded as UTF-8; a string runs to its zero byte, which is inside the string area (section 2).

The XML is for reading. DbxEdit edits the binary file itself. A folder is searched for files whose extension is
exactly `.dbx` (Windows' `*.dbx` pattern also matches `.dbxold`, whose XML would overwrite the `.dbx` file's).

**Where files are written.** Everything the tools write (`xml`, `edit`, `import`, `fixslots`, DbxEditGUI's saves and
exports, LevelDiff's `--json`) is written to a temporary file beside the target and moved into its place, so a failed
write leaves the old file and a hard link at the target is replaced, never written through. Nothing is written into
the BF1943 game folder (`BF1943_GAME`, else the Xenia kit's `dist\xenon` in the suite, as core `game1943.find_game`
finds it), however the path is cased and through whatever link, junction or share it leads there: such a write is
refused (`xml` counts the file as FAILED, the other commands exit 2). This is the rule of the suite's `fbrb1943.py`.
The paths are compared as Windows resolves them (case folded, links followed), and then by identity: the volume serial
number and file index of every folder above the target's nearest existing part, as written and as resolved, against
the game folder's - so `\\localhost\C$\<game>\...` is refused too (2026-10-31). The temporary file is
`~<7 hex digits>.tmp` (until 2026-10-31 a 45-character name, which pushed targets near 260 characters past .NET
Framework's limit). When the target cannot be replaced, a read-only target is reported as read-only and any other one
(held open by a program that does not share deletion) as in use.

## 4. Writing (`DbxEdit edit`, `DbxEdit roundtrip`)

A file is written back as: the header (payload start and *S* recomputed, the kept value unchanged), the
string table, the element tree, then the kept trailing bytes.

- If no string was changed or added, the original string area is copied byte for byte.
- Otherwise the string area is rebuilt: every string in table order (unused ones included), each followed
  by a zero byte, with offsets counted from the start of the string bytes.
- Varints are written in the shortest form.
- A string needed by an edit reuses the first identical entry in the table, or is added at the end.

An unchanged file therefore comes back byte for byte.

## 5. Edit commands

Commands run in the order given. A GUID comparison ignores case. "References" are attributes named `ref`.
A local reference is a `ref` value 36 characters long without a `/`.

Exit codes: 0, written; 1, a command found nothing to change (named on stderr; nothing is written); 2, a command
without its arguments or with an empty one it needs (each command's usage is printed), or the edit could not run
(a file damaged or missing, the output refused).

- `replace=<old>|<new>`: every string-table entry exactly `<old>` becomes `<new>`. Fails if none matched. An
  empty `<old>` is refused (it would rewrite string 0, the empty string every empty value uses).
- `replaceprefix=<old>|<new>[|<except>...]`: every entry starting with `<old>` (ignoring case), unless it
  starts with one of the non-empty exceptions (ignoring case), gets `<new>` in place of that start; if the
  entry's start was all lower case, `<new>` is lower-cased too. Fails if none matched; an empty `<old>` is
  refused (every string would match, tag names included).
- `drop=<guid>` (a GUID, not empty): walks the tree from the last element to the first, at every level. An `instance` with that
  GUID is removed. Any other element whose `ref` equals the GUID (a local reference to it) is removed if it is
  an `item`; otherwise all its `ref` attributes become `null`, and its children are still visited. A reference
  `<partition>/<guid>` names another partition's instance and is left as it is (a graft keeps its source's
  GUIDs, so the source partition's instance may share the GUID). Fails if no instance was removed.
- `dropfield=<name>`: removes every element, at any depth, that is not an `instance` and has an attribute
  `name` equal to `<name>` (children of removed elements are not visited).
- `set=<field>|<true|false|0-255>`: for every non-container element of a type other than text or numbers,
  with a two-byte value and an attribute `name` equal to `<field>`, sets the second byte. Fails if none.
- `setfield=<guid>|<field>|<value>`: on the first direct child of that instance named `<field>` that is not a
  container. A reference field (one with a `ref` attribute) gets `<value>` as its reference. Any other value is
  typed like the stored one: text; numbers of the stored width, `/`-separated; bool or byte. A 4-byte number keeps
  its field's kind: each value's stored kind, for a stored 0 or -0 (both kinds) most of the list's stored non-zero
  values', else the same field's (tag + first attribute value) elsewhere in the file, else decimal. An integer field
  takes only an integer of -2147483648..2147483647 (a whole-number field of section 3, such as a hash, also
  2147483648..4294967295, stored as its bits); a decimal field takes only a decimal (`2` is 2.0; `-0` keeps its sign;
  `NaN` and `Infinity` are written and reported). A value shown as it was keeps its bytes. A decimal with more digits
  than 4 bytes hold is rounded and reported. Fails (exit 1, nothing written) when there is no such field or the
  value does not fit it. `<value>` may hold `|`. (Before 2026-10-30 any whole value was written as an integer; before
  2026-10-31 a stored 0 chose the integer, and an integer field took decimals as "rounded".)
- `partguid=<guid>`: sets the `guid` attribute of the top-level partition.
- `additem=<guid>|<array>|<ref>[|<before ref>]`: adds an item to the direct child `array` of that instance
  named `<array>`, shaped like its first existing item but with only a `ref` attribute; at the end, or
  before the first item whose `ref` is `<before ref>`. Fails if the array has no item to copy the shape from.
- `copyfield=<src.dbx>|<src guid>|<field>|<guid>`: takes the first element named `<field>` that is not an
  `instance` inside the source instance (searching depth first; each element is checked before its
  children), copies it with its children, and puts the copy in place of the first element named `<field>`
  inside the target instance (same search), or appends it to the instance.
- `graft=<src.dbx>|<guid>[|<before guid>]`: copies that instance from the source file, then every source
  instance it references locally, and so on (depth first: the references of each copied instance, in
  document order, are visited last-first). Instances the file already has are skipped. The copies are
  inserted before instance `<before guid>`, or after the last element of the partition.

When an element is copied from another file, its strings are added to this file's table in this order:
tag, then each attribute's name and value, then a text value, then the children in order.

A `@file` argument reads more commands from that file: one per line, blank lines and lines starting with
`#` ignored.

## 6. XML import (`DbxEdit import`)

The XML of section 3 leaves out what the binary needs to be exact:

- whether a value is text, a number list or a bool/byte;
- the size of each number (4, 8 or 2 bytes);
- for 4-byte numbers, integer or decimal (`1` is both the integer 1 and the decimal 1.0);
- the container byte, the first byte of bool/byte values, the header value, trailing bytes and the
  string-table order.

Across 43,289 game files the container byte is always 0, the first byte of bool/byte values always 1,
the header value always 0, and there are no trailing bytes. But 5.9 million decimals print as whole
numbers, and 8,683 text values look like numbers. So the import takes types from a **reference file**,
normally the original `.dbx` the XML was exported from (`--like`, or `<name>.dbx` next to the XML).

- **Matching.** Each XML element is matched to a reference element among its siblings: by `guid`, else
  `name`, else `ref`, else all attributes, in order. A reference element is used once; the next XML
  element looks after it, and reference elements left over count as removed.
- **Unchanged values** (the same text as the reference shows, line endings aside) keep the reference's
  bytes. Unchanged attributes keep its string indexes. The reference's string table is kept and new
  strings are appended, so an unedited XML gives the original file byte for byte.
- **Changed values** keep the reference element's kind and number size. A 4-byte token is read as the
  integer or the decimal that the XML would print as exactly that token; a token shown as the reference value
  was keeps its bytes. If both readings would (`1`), the kind decides: the reference value's, but a stored 0 or -0
  is both, so for it most of the list's stored non-zero values', else the field's in the reference and `--types`
  files, else decimal. If neither would (more digits than a 4-byte decimal holds), an integer field takes the
  integer (a whole-number field also its unsigned form) and anything else the nearest decimal, which is counted
  as rounded. A token of the other kind than its field's (`1.5` in an integer field), a NaN or an infinity is
  written and listed as a `note:` on stderr.
- **New elements** take kind, size and integer-or-decimal from the same field (tag + first attribute
  value) in the reference or the `--types` files. With no such field, the XML's own shape decides, and
  each such guess is reported:
  - `<x ... />` is empty text;
  - a multi-line element without children is a container;
  - `true` or `false` is a bool;
  - `/`-separated numbers are 4-byte numbers;
  - anything else is text.
- New files (no reference) get header value 0, container byte 0, first byte 1 and no trailing bytes.

The import refuses (exit 1, `import failed: line N: ...`, nothing written) what it cannot write as it is: an
element with more than 15 attributes (section 2: the count is the low 4 bits of the head byte), a NUL character, raw
or `&#0;` (a string ends at its zero byte), anything after the root element but white space, comments and `<?...?>`,
a character reference that is not a Unicode character (not decimal or hex digits, a surrogate, past U+10FFFF), and
containers nested deeper than 1,000 (an element with children, or one holding only a line break, counts; a value
element inside the 1,000th container is taken, as `edit` takes the binary).

Exit code 0 means written; 1 means failed; 3 means written, but some values were typed by guess.

## 7. Level checks (`DbxEdit check`, `DbxEdit fixslots`)

`check` has two rule sets, BFBC2's and BF1943's, and names the one it used in its last line (`(BF1943 rules, from
the file)`). `--rules bf1943|bfbc2` chooses; otherwise a file whose LevelData has a field `DataConnections`,
`Descriptor` or `MemoryReportGroup` gets BFBC2's rules, any other file BF1943's.

**BFBC2's rules** - `check` reports, for a level partition:

- **Error:** an item in LevelData `Objects` that is `null` or refers to an instance not in the file. The
  game reads the list without a check (function 0x006EFBC0, crash at 0x006EFF8A).
- **Error:** a `PhysicsData` or `MeshEntityType` reference that is `null` on a model group, model-group
  member, forest or tree member. The model-group set-up dereferences it (crash at 0x00AF7144). Terrain
  is exempt, since its collision comes from elsewhere.
- **Error:** any other local reference to an instance not in the file.
- **Warning:** terrain, model groups, forests or the static envmap not listed in LevelData `Objects`.
  These are not loaded.
- **Warning:** cull slots shared by objects. A `ReferenceObjectData`'s `Enumeration` is its slot in the
  level's static cull trees: unique per object, 0 for objects outside the trees (dynamic props).
  Objects that share a slot are shown and hidden together.
- **Warning** (with `--slots-from <level>`): objects whose slot differs from the same object (by GUID) in
  the level the cull trees were built for.
- **With `--bundle <folder>`:** references into other files are resolved against the unpacked folders.
  - Warning: a referenced file is missing.
  - Error: the file is there, but the referenced instance is not.

(The crash addresses are BFBC2 PC's.)

**BF1943's rules** - everything above, except that the static envmap is not required and a member's `MemberType`
must not be `null` either, plus (each holds in all five shipped BF1943 levels; README "Check a level" gives the
evidence). *Where* is `<type> <guid> [<field path>]`, as above.

- **Error:** two instances with the same GUID.
- **Error:** an instance whose type (the part after the last `.`) is not a class (`c`) of `Bf1943Schema.Types` (the
  reflection schema, generated into `DbxEdit.cs` by `schema1943.py`). Reported once per type.
- **Error:** not exactly one LevelData, or the partition's `primaryInstance` is not its GUID.
- **Error:** a local reference in a top-level field whose member is class-typed in `Bf1943Schema.Types`
  (looked up on the instance's class, then its bases) to an instance that is not that class or one derived from
  it. With `--bundle`, the same for references into other files (the target's type is read from that file).
- **Error:** an item (or a field of a complex item) of one of the arrays in `LevelRules1943.ArrayTypes` (keyed
  `<instance type>.<array path without indices>`) refers to an instance that is not of the listed class, or is
  `null` (2026-10-31; `LevelData.Objects` is left to the null-entry error above).
- **Error:** a `StaticModelGroupEntityData`, `RigidModelGroupEntityData`, `MiddlegrowthModelGroupEntityData` or
  `ForestEntityData` whose `MemberDatas` item is not its member class (`...MemberData`; `TreeMemberData` for the
  forest), or names no instance of this partition (a reference into another partition, 2026-10-31; a missing local
  one is the unresolved-reference error). For each member, in `MemberDatas` order, with *run* starting at 0: `InstanceCount` must equal the number of
  `SpawnDatas`; `FirstPhysicsPart` must equal *run*; `PhysicsPartCount - FirstPhysicsPart` must equal
  `InstanceCount x PhysicsPartCountPerInstance`; then *run* = `PhysicsPartCount`.
- **Warning:** an instance listed twice in LevelData `Objects`.
- **Warning:** a `SpawnDatas` `Enumeration` (group instance slot) used more than once (at most 20 reported), and the
  group instance slots not being exactly 1..N (N = the number of `SpawnDatas` in the file).
- **Warning:** placed objects with a non-zero slot of at most N (one warning with the count).
- **Warning:** a blueprint (the `ReferencedObject` partition's last folder name, as `fixslots` uses) with placed
  objects of slot 0 and others with a slot.
- **Warning** (*listed* = in LevelData `Objects`): the listed `TeamEntityData` `Id`s, sorted, are not `Team1`,
  `Team2`, `TeamNeutral`; the listed `TicketCounterEntityData` `TeamId`s are not `Team1`, `Team2`; for `Team1` or
  `Team2`, no listed `CapturePointEntityData` has `UnableToChangeTeam` true and that `InitialOwnerTeam`; a listed
  `SoldierSpawnEntityData` is in no listed capture point's `Spawners`; the count of listed `TerrainEntityData`,
  `CombatAreaTriggerEntityData`, `HumanPlayerEntityData`, `AutoTeamEntityData`, `PreRoundEntityData`,
  `MissionEntityData` or `HotZoneEntityData` is not 1; LevelData `Name` is not `Levels/` + the LevelData's `id`.
- **Warning:** in a listed instance or the LevelData, a resolved local reference whose field (an array item's: its
  array's) is `Source`, `Target`, `Spawners`, `AlternativeSpawnPoints`, `MapMarker`, `MapMarkers`, `Geometry`,
  `SurroundingGeometry`, `Soldiers`, `ObjectToFollow`, `PlayerToCheck` or `VehicleToCheck` points to an instance
  that is not listed (a `SoundAreaEntityData`'s `OuterGeometry` is exempt).
- **Schema** (`Bf1943Schema.Types`: classes `c`, structs `s`, enums `e`). Walking each instance whose class is known:
  a named child that is no member of its class (with bases) or struct is a **warning** (at most 50 listed, then a
  count), unless `<owner>.<field>` is one of the 20 data-only fields (`LevelRules1943.DataOnlyFields`); a `complex`
  child whose member type is a struct is walked as that struct; an array's `complex` items are walked as its element
  struct; an array item referring to a local instance that is not its element class or derived from it is an
  **error**; a text field whose member type is an enum and whose value is not one of the enum's names is an
  **error**.
- **With `--bundle`, the static cull trees** (format below): the client tree's leaves not numbered 1..L each once, or
  L not the number of `SpawnDatas` in the file, or its `EnumOffset` not 0, is an **error**; the same numbering for the
  exclusive tree, or its `EnumOffset` not that number, is an **error**; a placed object (non-zero `Enumeration`) with
  a slot <= `EnumOffset` or > `EnumOffset` + L is an **error**. **Warnings:** taking the slotted placed objects in slot
  order, with *next* = `EnumOffset` + 1, each object's slot must equal *next*, then *next* = slot + width (2 when the
  partition its `ReferencedObject` names has a `PhysicsEntityData` instance, else 1), and at the end *next* must be
  `EnumOffset` + L + 1 (a blueprint partition not in the folders, or a slotted object without a `ReferencedObject`
  into another partition, skips this with a warning naming it); an object whose
  `Transform` position is more than 1.55 x W / 4 + 0.1 m from its leaf's centre (X, Y, Z / 16); a two-leaf object
  whose leaves differ (any key or `Max`). For each tree: not one pre-order tree from node 0 followed by exactly 256
  all-zero nodes is an **error**; an inner node whose `Max` is not the larger of its children's, and a leaf whose
  W is neither max(5, ceil(4 x (`Max` + 0.125))) nor one more, are **warnings**.

**Static cull trees** (`World.StaticCullTreeData`, one instance per partition; BF1943, and BFBC2 PC's `mp_005`
alike). Fields: `Nodes` (array of complex) and `EnumOffset` (4-byte integer). Each node: `Right` (4 bytes), `Max` (4-byte
decimal), `SphereKeyX`, `SphereKeyY`, `SphereKeyZ`, `SphereKeyW` (2 bytes each, signed).
- The nodes are a binary tree in pre-order, starting at node 0. An inner node's left child is the next node, and its
  right child is `Right` nodes after it.
- A leaf has bit 31 of `Right` set. The low 31 bits are its number, 1..L: each once, and leaf *n* is cull slot
  `EnumOffset` + *n*.
- The tree is followed by 256 nodes that are all zero.
- A node's sphere is centre `SphereKeyX/Y/Z` / 16 m and radius `SphereKeyW` / 4 m. The game's frustum walk at
  0x825c3730 converts the four keys with `vcfsx` scales 2^-4 and 2^-2 (0x825c2f7c, 0x825c2f80) [O]. It compares
  the smallest of six plane distances with the radius: a subtree wholly outside is dropped, one wholly inside goes to
  0x825c2f20, and otherwise the walk visits node + 1 and node + `Right`.
- An inner node's sphere holds its children's only loosely: W/4 encloses them at 95 % of nodes on Wake Island, and
  W/2 at 99 %.
- `Max` is, for a leaf, about its object's bounding radius (unquantised). It is constant per blueprint: 88 of 98
  blueprints have one value, the rest differ in the last digit. On Coral Sea the collision geometry lies within
  1.01 x `Max` of the leaf's centre.
- For an inner node, `Max` is the larger of its children's `Max`, so it is the largest object radius below it (all
  ten trees).
- In every leaf, W/4 >= `Max` + 0.127 m: the quantised radius is rounded up.
- 0x825c2f20 tests `Max` against (a camera factor x distance - radius) for subtrees inside the frustum [O pseudocode].
  That reads as a size-at-distance cull [H]: a subtree whose largest object is too small for its distance is skipped.
- A physics object's two exclusive leaves are identical (sphere and `Max`; 4,470 of 4,470).
- At run time each renderable carries its slot (renderable + 0x10) [O]:
  - It joins through 0x825ca640 -> 0x825c9c50 and leaves through 0x825cb928 -> 0x825ca390.
  - 0x825ca390 finds a slot's tree as the one with `EnumOffset` < slot <= `EnumOffset` + L, and the slot's
    12-byte record in it.
  - Renderable slots are set from a record's + 0x18 (GameObjectData `Enumeration`): 0x8227abc0 on the client,
    0x82397e70 on the server.
- Physics parts look up slot records too [O]:
  - 0x82227e80 and 0x82228768 take each part's slot from 0x82519cb8 and fetch it through 0x825c54c0.
  - The table index runs through the Havok shape's sub-part records (+0x58, 0x40 bytes each).
  - This is most likely a model group's combined asset mapping parts to group-instance slots [H].
- The consumer of a physics object's second exclusive slot is not identified [U].
- The client level load (0x82276048) builds the exclusive, client and client-tree trees with per-leaf records. The
  server level load (0x82391098) builds the exclusive tree only, without per-leaf records [O].
- W = max(5, ceil(4 x (`Max` + 1/8))) in 19,429 of 23,989 leaves, and one more in the other 4,560 [O]. That is, the
  radius is `Max` + 1/8 m rounded up to 1/4 m, with a minimum of 1.25 m; the extra one presumably comes from the
  centre's rounding [H].

`fixslots <in> <out> --from <level>` gives every object the slot of the same object in `<level>`:

1. by GUID;
2. else by blueprint name and position (rounded to 0.1 m);
3. else the nearest unused same-blueprint object within 50 m;
4. else 0.

Each object of `<level>`, and each non-zero slot, is given once, GUID matches first: a copy of an object at the same
place under another GUID gets the next rule's slot or 0, not the original's (before 2026-10-30 both got it, the shared
slot the check warns about). It prints how many objects each rule matched.

`check` exits 0 with no errors, 1 with errors, 2 when it could not run (bad usage, a file damaged, missing or not
binary, `--slots-from` included). The cull-tree walk uses no recursion: a tree of any depth is checked.
