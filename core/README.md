# BF1943 Tool Suite core (bfbc2core)

Part of the BF1943 Tool Suite. **BF1943 status: partly adapted.** The package keeps OPFOR's name, `bfbc2core`.
- `dbx.py` reads BF1943's binary DBX partitions (the format is BFBC2's; used by `bf1943-spawn-map` and NavKit's
  big-endian `levelobjects1943.py`).
- `terrain.py` and `water.py` detect the byte order: little-endian on BFBC2 PC, big-endian on BF1943 Xbox 360
  (terrain checked against Wake Island's spawn points, 2026-10-23).
- The rest - the level loader `level.py`, `xmlinst.py`, the Kynapse path-data modules and the 3D view's level
  picker - still reads Project OPFOR's BFBC2 copy and BFBC2's formats. The "Checked by" column below is measured
  on BFBC2 data unless it says otherwise.

The suite's shared Python package, `bfbc2core`: the game's data formats, the level loader and the 3D view
that the 3D tools are built on. Python 3.10+ with `numpy`; the view also needs `moderngl`, `glfw` and
`imgui-bundle`.

| Module | What it does | Checked by |
|---|---|---|
| `dbx.py` | reads binary `.dbx` files into an element tree and writes DbxEdit's XML, from `bf1943-dbx-tools/FORMAT.md` | `../tests/test_bf1943_data.py dbx`: every `.dbx` partition of BF1943's readable bundles parses |
| `terrain.py` | terrain height fields (`.Heightfield-000.res`): heights, exact height at any point, a mesh | `../tests/test_bf1943_data.py terrain`: every BF1943 level (big-endian), Wake Island's flag spawns on the terrain |
| `water.py` | water meshes (`.Water.Mesh.res`) and each triangle's shader (from `.Water.res`) | `../tests/test_bf1943_data.py terrain`: every BF1943 level, flat water |
| `xmlinst.py` | the `<instance>` elements of a converted level partition (type, guid, body) and a field's reference (`field_ref`), whatever the attribute order (DICE writes `guid` / `ref` first, Napisal `type` / `name` first); used by NavKit's `levelobjects.py` and `ordercheck.py` | output identical to the old readers on the 13 campaign levels and ONS_MP_004 (2026-10-20) |
| `level.py` | a level: entities (types, transforms, blueprints, teams, player types), static-group members, world-space collision, areas and routes, terrain and water; prepares a level on first use | no test here (BFBC2 data; a test comes with its BF1943 adaptation) |
| `viewer.py` | the 3D view: triangles and lines tagged with object ids and layers, orbit and fly camera, GPU picking with the world point, an overlay for links and measurements, ImGui panels | the Map Inspector's screenshot runs |
| `navdata.py` | a Kynapse path-data file as the 3D tools draw it: walkable cells (ground heights from the terrain), area boundaries, the route graph and its islands; a cache per file; cell comparison of two files | no test here (BFBC2 data; a test comes with its BF1943 adaptation) |
| `navpath.py` | the path test: a route between two points over a file's route graph (costs from the stored edges), or why there is none (no node near, different islands) | no test here (BFBC2 data; a test comes with its BF1943 adaptation) |
| `navtune.py` | navigation side files (`<level>.navtune.json`): the edits made to a level's AI navigation, their author (the PC's name) and the base they were made on | no test here (BFBC2 data; a test comes with its BF1943 adaptation) |
| `pick.py` | the level picker the 3D tools show when started without a level | no test here (BFBC2 data; a test comes with its BF1943 adaptation) |
| `errors.py` | error handling and logs for every tool: a log per tool and day in `%LOCALAPPDATA%\bf1943-tool-suite\logs`, the cause and what to do when something fails, a window for the windowed tools | `tests/test_errors.py` |

```python
import sys; sys.path.insert(0, r'<suite>\core')
from bfbc2core import level
lvl = level.load('mp_005')                    # from Project OPFOR's game copy (found next to the suite)
flags = [e for e in lvl.entities if e.category == 'capture point']
lvl.heightfield.height(-111.0, 163.3)         # terrain height there
```

The formats are described in the module docstrings and in OPFOR's [DESIGN.md](../docs/opfor-bfbc2/DESIGN.md) section 7. Tests:
`python ..\run_tests.py quick` (no game data) and `python ..\run_tests.py bf1943`.
