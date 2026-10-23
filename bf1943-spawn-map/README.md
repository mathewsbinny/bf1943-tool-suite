# bf1943-spawn-map

Lists each BF1943 level's objectives (the two bases and the flags) with the soldier and vehicle spawns each one owns, read from the level's own data. Bots use it to choose where to spawn and what to attack or defend.

```
python -B spawnmap.py                       every multiplayer level, from bf1943-xenia-kit\output\title\dist\xenon
python -B spawnmap.py wake_island_s         one level
python -B spawnmap.py <dist\xenon folder> [level ...] [--json spawns.json]
```

The default game folder is the one `bf1943-xenia-kit` builds from your own package. The tool only reads it; with `--json` it also writes the map as JSON.

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

`tests/test_spawnmap.py` runs the tool on the default game folder when it exists, checks every level's two bases, and checks that every soldier spawn has a team.
