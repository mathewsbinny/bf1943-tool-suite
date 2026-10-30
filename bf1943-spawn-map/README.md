# BF1943 spawn map (bf1943-spawn-map)

Part of the BF1943 Tool Suite. **BF1943 status: ready** - a new BF1943 tool; it reads BF1943's Xbox 360 level bundles (from the Xenia kit's extracted copy) through `fbrb1943.py` and `core/bfbc2core/dbx.py`.

Lists each BF1943 level's objectives (the two bases and the flags) with the soldier and vehicle spawns each one owns, read from the level's own data. Bots use it to choose where to spawn and what to attack or defend.

```
python -B spawnmap.py                       every multiplayer level, from bf1943-xenia-kit\output\title\dist\xenon
python -B spawnmap.py wake_island_s         one level
python -B spawnmap.py <dist\xenon folder> [level ...] [--json spawns.json] [--navflags]
```

The default game folder is the one `bf1943-xenia-kit` builds from your own package. The tool only reads it; with `--json` it also writes the map as JSON.

With `--navflags` (2026-10-30) it also writes `work\levels\<level>\navkit\<level>.flags.txt` for each level (`BF1943_WORK` sets another work folder): plain lines a C program reads, `flag <index> <base|flag> <initial owner 0|1|2> <x> <y> <z> <radius> <name>`, and `spawn <team> <objective> <x> <y> <z>` for each base's soldier spawn and its alternates. navhost and rsbot's offline tests (project-opfor-bf1943 `TnT/Code/Engine/AI/Bots`) read it. Only real objectives become flags (2026-10-30): one with no index or no position, or a point at the map origin that owns no spawn (Coral Sea's three "Spawn" points, all index 0), is named in a `# left out` comment instead; each index appears once (a duplicate is an error).

## What it reads

Everything comes from `levels/<level>.dbx`, found in whichever of the level's bundles holds it.

| Type | Fields used |
|---|---|
| `CapturePointEntityData` (an objective) | `Name`, `LocationName` (text ID), `Index`, `InitialOwnerTeam`, `CaptureRadius`, `OnlyTakeableByTeam`, `UnableToChangeTeam` (set on the two bases), `Spawners` (the spawns it owns; their team follows the point's owner) |
| `SoldierSpawnEntityData` | `Team`, `PlayerType` (`SSHumanPlayer` / `SSAiPlayer`), `Enabled`, `LocationNameSid`, `AlternativeSpawnPoints` (more positions for the same spawn) |
| `VehicleSpawnEntityData` | the vehicle blueprint and its team |

Positions are the `Transform` translation (x, y, z, with y up), in metres.

## Findings (2026-10-23)

- **Wake Island, Guadalcanal, Iwo Jima:** each has two bases with a fixed team and five neutral flags. Each base or flag has one soldier spawn, with 4–6 alternate positions, and its own vehicles.
- **Coral Sea:**
  - the two carrier bases;
  - three placeholder points named "Spawn", at the map origin;
  - the Air Superiority zone;
  - two `SSAiPlayer` spawners that no objective owns.

## Tests

`tests/test_spawnmap.py` checks `--navflags`' lines on a made-up map (and that placeholder objectives and duplicate indices are handled; `--made-up` stops there, the quick tier), then runs the tool on the default game folder when it exists, checks every level's two bases, that every soldier spawn has a team, and that each level's flags have unique indices and none at the map origin.
