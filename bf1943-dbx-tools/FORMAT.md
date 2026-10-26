# Binary DBX format and DbxEdit's behaviour

This is the specification DbxEdit is written from. It describes Battlefield: Bad Company 2's binary `.dbx`
files and exactly what DbxEdit outputs. The format knowledge comes from the BC2 modding community, first
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
- Strings are decoded as UTF-8; a string runs to its zero byte or the end of the file.

The XML is for reading. DbxEdit edits the binary file itself.

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

- `replace=<old>|<new>`: every string-table entry exactly `<old>` becomes `<new>`. Fails if none matched.
- `replaceprefix=<old>|<new>[|<except>...]`: every entry starting with `<old>` (ignoring case), unless it
  starts with one of the non-empty exceptions (ignoring case), gets `<new>` in place of that start; if the
  entry's start was all lower case, `<new>` is lower-cased too.
- `drop=<guid>`: walks the tree from the last element to the first, at every level. An `instance` with that
  GUID is removed. Any other element whose `ref` equals the GUID, or ends in `/<guid>`, is removed if it is
  an `item`; otherwise all its `ref` attributes become `null`, and its children are still visited. Fails if
  no instance was removed.
- `dropfield=<name>`: removes every element, at any depth, that is not an `instance` and has an attribute
  `name` equal to `<name>` (children of removed elements are not visited).
- `set=<field>|<true|false|0-255>`: for every non-container element of a type other than text or numbers,
  with a two-byte value and an attribute `name` equal to `<field>`, sets the second byte. Fails if none.
- `setfield=<guid>|<field>|<value>`: on a direct child of that instance named `<field>` (first match that is
  text or numbers), sets the text, or the single 4-byte number (a float if the value has a `.`).
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
  integer or the decimal that the XML would print as exactly that token. If both would (`1`), the
  reference value's reading decides. If neither would (more digits than a 4-byte decimal holds), an
  integer field takes the integer and anything else the nearest decimal, which is counted as rounded.
- **New elements** take kind, size and integer-or-decimal from the same field (tag + first attribute
  value) in the reference or the `--types` files. With no such field, the XML's own shape decides, and
  each such guess is reported:
  - `<x ... />` is empty text;
  - a multi-line element without children is a container;
  - `true` or `false` is a bool;
  - `/`-separated numbers are 4-byte numbers;
  - anything else is text.
- New files (no reference) get header value 0, container byte 0, first byte 1 and no trailing bytes.

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
  `<instance type>.<array path without indices>`) refers to an instance that is not of the listed class.
- **Error:** a `StaticModelGroupEntityData`, `RigidModelGroupEntityData`, `MiddlegrowthModelGroupEntityData` or
  `ForestEntityData` whose `MemberDatas` item is not its member class (`...MemberData`; `TreeMemberData` for the
  forest). For each member, in `MemberDatas` order, with *run* starting at 0: `InstanceCount` must equal the number of
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
  `EnumOffset` + L + 1 (a blueprint partition not in the folders skips this, with a warning); an object whose
  `Transform` position is more than 1.55 x W / 4 + 0.1 m from its leaf's centre (X, Y, Z / 16); a two-leaf object
  whose leaves differ (any key or `Max`). For each tree: not one pre-order tree from node 0 followed by exactly 256
  all-zero nodes is an **error**; an inner node whose `Max` is not the larger of its children's, and a leaf with
  W / 4 < `Max` + 0.125, are **warnings**.

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
- At run time each renderable carries its slot. 0x825ca390 finds a slot's tree as the one with
  `EnumOffset` < slot <= `EnumOffset` + L.

`fixslots <in> <out> --from <level>` gives every object the slot of the same object in `<level>`:

1. by GUID;
2. else by blueprint name and position (rounded to 0.1 m);
3. else the nearest unused same-blueprint object within 50 m;
4. else 0.

It prints how many objects each rule matched.
