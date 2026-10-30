# BF1943 Map Inspector (bf1943-map-inspector)

Part of the BF1943 Tool Suite. **BF1943 status: ready** (2026-10-23) - it opens BF1943 levels from your own copy,
read straight from the bundles through `core/bfbc2core/level1943.py`.

Browse a Battlefield 1943 level in 3D and inspect every object: where it is, what it is, which file it comes from,
its full property tree, and how it links to other objects.

**Opening it:** in the suite window (`BF1943 Tool Suite.pyw`), Levels tab: choose a level, then **Open in Map
Inspector** (or double-click the level). Or double-click `inspector.py` here: a picker of your copy's levels opens.
Or from a command line:

```
python inspector.py <level>          e.g. python inspector.py wake_island_s   (or guadal_canal, iwo_jima_s, coral_sea)
```

The level is read from the game folder `bf1943-xenia-kit` extracts (`BF1943_GAME` selects another). The copy is
only read; nothing is unpacked or written. Needs Python 3.10+ with `numpy`, `moderngl`, `glfw` and `imgui-bundle`
(`pip install numpy moderngl glfw imgui-bundle`).

## What it shows

| Layer | From |
|---|---|
| terrain, water | the level's height field (`core/bfbc2core/terrain.py`, big-endian) and sea surface (`water.py`) |
| placed objects, destructibles, dynamic objects, ladders | collision of the placed objects; the group comes from the blueprint (ladder: `LadderEntityData`; destructible: two or more health states) |
| group members | static model groups |
| soldier and vehicle spawns, per team | team 1 (US) blue, team 2 (Japan) red; a short line shows the facing |
| end-of-round spawns | `EndOfRoundSoldierSpawnEntityData` (hidden at start) |
| bases, flags | the capture points, as `bf1943-spawn-map` reads them (a base cannot change team) |
| combat areas, DMZs | their outlines (`DmzEntityData` names its team) |
| map markers | `MapMarkerEntityData` (hidden at start) |
| area points, other | hidden at start |

BF1943's spawns are all `TeamNeutral` in the data, so a spawn takes the side of the base that owns it, or of the
carrier it stands on. BF1943's levels have no Rush objectives, AI orders or effects, so the inspector has no such
layers.

## Using it

- **Camera:** left drag rotates, right or middle drag pans, the wheel zooms, double-click flies to the
  point. F switches to fly mode: hold the right button and use W A S D, Q / E for down / up, Shift for speed.
- **Select:** click an object. The Selection panel shows its type, GUID, partition, blueprint, team,
  player type and position, the objects that reference it, and the full property tree; references in the
  tree are buttons that follow them (with *back*). Over the scene, yellow lines go to what it references and
  cyan lines come from what references it.
- **Search:** by GUID, type, id, name or blueprint, and filter by type; results fly the camera to the object.
- **Measure (M):** clicks add points; the panel gives the last segment (length, horizontal, height, slope),
  the path total, and the last point's height above the terrain. Esc clears.
- Hovering shows what is under the mouse and where.

Window layout is kept in `%APPDATA%\bf1943-tool-suite\`.

For tests: `inspector.py <level> --screenshot out.png [--frames N] [--select <guid>] [--measure "x,y,z;x,y,z"]`
draws, saves a screenshot and exits (exit code 1, nothing written, when the viewer took no picture).
`tests/test_inspector.py` checks that exit code without game data or a window.
