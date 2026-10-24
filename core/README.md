# BF1943 Tool Suite core (bfbc2core)

Part of the BF1943 Tool Suite. **BF1943 status: ready for BF1943** (2026-10-24) through `dbx.py`, `terrain.py`,
`water.py`, `materialmap1943.py`, `game1943.py` and `level1943.py`. The package keeps OPFOR's name, `bfbc2core`.
- `game1943.py` finds your BF1943 copy, lists its levels, indexes a level's bundles, and gives the suite's work
  folder. `level1943.py` loads a BF1943 level straight from the bundles, with the same interface as `level.py`, so
  the 3D tools take either. `pick.py` lists the BF1943 copy's levels.
- `dbx.py` reads BF1943's binary DBX partitions (the format is BFBC2's). `terrain.py` and `water.py` detect the
  byte order: little-endian on BFBC2 PC, big-endian on BF1943 Xbox 360.
- `level.py` remains the BFBC2 loader (Project OPFOR's copy), used by the little-endian NavKit. `xmlinst.py` and
  `navtune.py` are BFBC2 too.

The game copy is only read. What the tools make from it goes to the suite's work folder, `<suite>\work`
(`BF1943_WORK` sets another).

The suite's shared Python package, `bfbc2core`: the game's data formats, the level loaders and the 3D view
that the 3D tools are built on. Python 3.10+ with `numpy`; the view also needs `moderngl`, `glfw` and
`imgui-bundle`.

| Module | What it does | Checked by |
|---|---|---|
| `game1943.py` | your BF1943 copy: `find_game` (given, else `BF1943_GAME`, else `bf1943-xenia-kit`'s output), `levels`, `Game` (every file of a level's bundles and the shared level bundle: `data`, `partition`, `names`), `work_root` / `level_work` | `../tests/test_bf1943_tools.py level` and the other BF1943 tool tests |
| `level1943.py` | a BF1943 level from the bundles: entities with transforms and sources, collision meshes, terrain, water, outlines, bases and flags with their spawns; the same `Level` / `Entity` / `Mesh` / `Shape` as `level.py`. Categories: base, flag, soldier / vehicle spawn, end-of-round spawn, combat area, DMZ, map marker, area point, ladder, destructible, dynamic object, placed object. BF1943's spawns are all `TeamNeutral` in the data, so a spawn takes the side of its base, or of the carrier it stands on. Nothing is unpacked or written | `../tests/test_bf1943_tools.py level`: every multiplayer map (entities, collision, terrain, water, two bases and their flags, spawns with sides); Wake Island's 16 ladders |
| `dbx.py` | reads binary `.dbx` files into an element tree and writes DbxEdit's XML, from `bf1943-dbx-tools/FORMAT.md` | `../tests/test_bf1943_data.py dbx`: every `.dbx` partition of BF1943's readable bundles parses |
| `terrain.py` | terrain height fields (`.Heightfield-000.res`): heights, exact height at any point, a mesh | `../tests/test_bf1943_data.py terrain`: every BF1943 level (big-endian), Wake Island's ground spawns on the terrain |
| `water.py` | water meshes (`.Water.Mesh.res`) and each triangle's shader (from `.Water.res`) | `../tests/test_bf1943_data.py terrain`: every BF1943 level, flat water |
| `materialmap1943.py` | a BF1943 level's terrain material map (TerrainMaterialMap `.res`, big-endian): the ground material under any point, 4096 x 4096 pixels over the height field (0.5 m), names from the file's own table; the format is in operation-rising-sun `research/GROUND_MATERIALS.md` | no test here; used by NavKit's audit (`navaudit1943_checks.py`) on all four MP levels; the research study decoded all four MP levels' maps, and all 29 terrain names in their tables resolve to the level's MaterialGrid |
| `xmlinst.py` | the `<instance>` elements of a converted BFBC2 level partition (type, guid, body) and a field's reference (`field_ref`), whatever the attribute order; used by the little-endian NavKit's `levelobjects.py` and `ordercheck.py` | output identical to the old readers on BFBC2's 13 campaign levels and ONS_MP_004 (2026-10-20) |
| `level.py` | a BFBC2 level from Project OPFOR's copy: entities (types, transforms, blueprints, teams, player types), static-group members, world-space collision, areas and routes, terrain and water; prepares a level on first use (with BFBC2's FbrbTool from OPFOR's `tools\` folder: this suite no longer has it). `level1943.py` reuses its classes | no test here (BFBC2 data) |
| `viewer.py` | the 3D view: triangles and lines tagged with object ids and layers, orbit and fly camera, GPU picking with the world point, an overlay for links and measurements, ImGui panels | `../tests/test_bf1943_tools.py viewers` |
| `navdata.py` | a navigation file (Kynapse `.hpd` layout, as NavKit writes it) as the 3D tools draw it: walkable cells, area boundaries, the route graph and its islands, ladder links. A cell's height is its area outline's (less `Y_OFFSET`: BF1943 builds 0.9 m, callers set it; DICE's 0.95 is the default), or the terrain's where that outline lies on the terrain - a floor up to 1.5 m above the terrain kept the terrain's height before 2026-10-24 | `tests/test_navdata_offset.py` (made-up areas: the offset, a floor over flat terrain, sloped terrain, the cache key); `../tests/test_bf1943_tools.py viewers`: the Navigation Studio on Wake Island's build, when one exists |
| `rsnv.py` | RSNV, our navigation runtime's per-level file (from NavKit's `navexport1943.py`), either byte order: `write`, `load` (rejects a damaged file with a `ValueError`), `World` (`locate` a point on a walkable cell, `ground`, `areas_near`, `walkable`, `validate`) and `astar`, the reference search the C runtime is compared with (integer costs in 1/16 m, ties broken by node index). Sections: SECT, NODE, NAUX, EDGE, AREA, BMAP, HGHT, GBKT, GLST, ANOD, LADR, OFFM, ISLD, META; 64-byte header, each section 16-byte aligned. The design is operation-rising-sun `research/NAVIGATION_RUNTIME_DESIGN.md` | `tests/test_rsnv.py` (a made-up file: both byte orders, search, locate, damaged files); `../bf1943-auto-navkit-big-endian/tests/test_navexport1943.py` on your builds |
| `vehicles1943.py` | a BF1943 vehicle's class (ground, boat, aircraft, stationary) and its entry points from its blueprint's `PlayerEntryComponentData` (position, `EntryRadius`, order); NavKit's exporter writes them into the RSNV file (OFFM), the audit still has its own copy | `../bf1943-auto-navkit-big-endian/tests/test_navexport1943.py` (the export on your builds) |
| `navpath.py` | the path test: a route between two points over a file's route graph (costs from the stored edges), or why there is none (no node near, different islands) | `../tests/test_bf1943_tools.py viewers`: South Base to the airfield routed, when a Wake Island build exists |
| `navtune.py` | navigation side files (`<level>.navtune.json`) of the little-endian NavKit: the edits made to a level's AI navigation, their author and the base they were made on | no test here (BFBC2 data) |
| `pick.py` | the level picker the 3D tools show when started without a level: your BF1943 copy's levels, and whether NavKit has built each one's navigation | the 3D tools when started without a level |
| `errors.py` | error handling and logs for every tool: a log per tool and day in `%LOCALAPPDATA%\bf1943-tool-suite\logs`, the cause and what to do when something fails, a window for the windowed tools | `tests/test_errors.py` |

```python
import sys; sys.path.insert(0, r'<suite>\core')
from bfbc2core import game1943, level1943
game1943.levels()                                 # ['wake_island_s', 'guadal_canal', 'iwo_jima_s', 'coral_sea', 'wake_island_tutorial']
lvl = level1943.load('wake_island_s')             # from your copy (BF1943_GAME overrides)
flags = [e for e in lvl.entities if e.category == 'flag']
lvl.heightfield.height(-20.9, 115.8)              # terrain height there
game1943.level_work('wake_island_s')              # <suite>\work\levels\wake_island_s
```

The formats are described in the module docstrings and in OPFOR's [DESIGN.md](../docs/opfor-bfbc2/DESIGN.md) section 7. Tests:
`python ..\run_tests.py quick` (no game data) and `python ..\run_tests.py bf1943`.
