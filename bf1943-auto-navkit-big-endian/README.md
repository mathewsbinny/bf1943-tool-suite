# BF1943 NavKit (big-endian)

> **This variant reads big-endian data:** BF1943's Xbox 360 levels (terrain heightfields; Havok 4.6.1 packfiles). It began as a copy of `bf1943-auto-navkit-little-endian` on 2026-10-23. OPFOR's changes reach it through that variant, not directly.

Part of the BF1943 Tool Suite. **BF1943 status: ready** (2026-10-24) - `navkit.py` and `navkitGUI.pyw` are the
BF1943 pipeline: from a level of your BF1943 copy to a checked navigation build, and `tools\navaudit1943.py`
audits a build against its level. **To use it, start with [QUICKSTART.md](QUICKSTART.md).**

```
python navkit.py levels                   the copy's levels, and what has been done for each
python navkit.py prepare wake_island_s    objects, collision, flags, ladders, terrain and water from the bundles
python navkit.py sample  wake_island_s    the collision samples, computed offline (--pitch 0.8 or 0.2: that pass only)
python navkit.py build   wake_island_s    the navigation build with BF1943's soldier
python navkit.py check   wake_island_s    structure (hpdcheck.py) and routes (routecheck1943.py)
python navkit.py all     wake_island_s    prepare, sample, build, check
python navkit.py status  wake_island_s    what has been done for a level
```

Option: `--game <dist\xenon folder>` (default: `BF1943_GAME`, else `bf1943-xenia-kit`'s output).

- **Nothing is started in the game.** The game copy is only read; everything is computed from the level's data.
  There is no `install` or `restore`: BF1943 has no Kynapse runtime. The build is the input of our own navigation
  runtime (a per-level graph queried with A*; operation-rising-sun
  `research/evidence/navigation-runtime-options.json`).
- **Files** go under the suite's work folder (`BF1943_WORK` overrides `<suite>\work`):
  `work\levels\<level>\objects\` (`levelobjects1943.py`'s files, `heightfield.res`, `water_mesh.res`),
  `samples\` (`samples*.bin` and their logs), `navkit\` (`navkit.json` settings, `<level>.hpd`, `<level>.cells.npz`,
  `build.log`, `check.txt` / `check.json`, `jump_links.json`; the exports `<level>.rsnv` and the vehicle worlds),
  `vehicles\` (the ground vehicles' collision, samples and logs), `audit\` (the audit's masks, `audit.json` and
  `audit.md`).
- **Steps.** *prepare* reads the level's objects and collision (`tools\levelobjects1943.py`), its height field and
  water, and sets the sea level and sector offsets. *sample* computes the collision samples offline
  (`tools\navsample1943.py`): two runs at 0.8 m (BF1943's soldier, and a 0.6 m step for stairs), the detailed
  sectors (`tools\detailed1943.py`), then two runs at 0.2 m over them. *build* runs `tools\navbuild.py` with
  BF1943's soldier (`tools\soldier1943.py`): 0.8 m everywhere, the detailed sectors at 0.2 m, water, spawn seeds,
  ladder links, jump links to ships' AA gun platforms (`tools\jumplinks1943.py`), and a shared node wherever the
  two grids' ground meets a mixed-pitch seam with no node joining it (`navbuild.SEAM_GAP_NODES`). *check* runs `tools\hpdcheck.py` and `tools\routecheck1943.py` (new): every spawn and flag joined
  to the route graph, and a route between every pair of flags. A Wake Island run takes about 45 minutes on 8
  cores, most of it the 0.2 m sampling.
- **The window** (`navkitGUI.pyw`): a level list, the steps Prepare, Sample, Build and Check with a mark for each
  step done, **Run all (1-4)**, **Cancel** (ends the running step and its samplers), **Show in 3D** (opens the
  Navigation Studio on the build) and **Open output folder**.
- **Wake Island, today's build (2026-10-26, round 3):** 12,718 nodes, 45,118 edges, 65 islands; the main network has 11,363
  nodes; 16 of 16 ladders linked from the main network, the 4 carrier AA guns and 21 carrier ledges by jump links; all 10 flag pairs
  routed; `hpdcheck` passes.
- **The audit** (`tools\navaudit1943.py`, new 2026-10-24; operation-rising-sun `research/NAVMESH_AUDIT.md`):
  `navaudit1943.py masks <level>` recomputes the build's walkable surfaces exactly as the build does (and once per
  rule with the rule off: headroom, lips, water, ladders); `navaudit1943.py audit <level>` then checks the build
  against the level's own data - every capture area, spawn, mounted gun and repair station on an area of its own floor,
  every vehicle with walkable ground within its own EntryRadius of an entry point (the vehicle's
  PlayerEntryComponentData; `VEHICLE_REACH`, 4 m from the spawn, only where a blueprint gives none), routes from every infantry spawn to every capture area and back, islands classified (main,
  vehicle-only zone, separate landmass, raised surface, gap candidate), file consistency and crossings (touching
  ground on two islands, tested on the samples), 0.8 m against 0.2 m, the wading band, where each rule fired and its
  near-misses, and the playable area. Where it lists a column's surfaces, a terrain surface carries its ground
  material (core `materialmap1943.py`, the level's terrain material map). The checks are in
  `tools\navaudit1943_checks.py`. Reports: `work\levels\<level>\audit\audit.{json,md}`.
- **The export for our navigation runtime** (`tools\navexport1943.py`, new 2026-10-24): `navexport1943.py <level>
  [--big-endian] [--check]` writes `work\levels\<level>\navkit\<level>.rsnv` (`.be.rsnv` big-endian), the build in
  the runtime's own format (core `rsnv.py`): the route graph with integer costs (1/16 m) and edge kinds (walk, ladder
  up and down, jump), each area's walkable cells and their heights (a height grid of 1 m blocks at 0.2 m pitch, one
  block per cell at 0.8 m; exporter 1.1, 2026-10-25), a grid to find the areas near a point, the islands, and the
  build's hash. Exporter 1.2 flags tight walk edges, those with no line of sight either way over the cells
  (`rsnv.World.los`, the runtime's rule). `--check` reads the file back and compares it with the build.
  **Exporter 1.3 (2026-10-26)** takes each area's cells and ground heights from the build itself: `navbuild` writes
  `<level>.cells.npz` beside `<level>.hpd` (every area's cells and heights, and the build's SHA-256). Before, heights
  were estimated from the nearest outline point, 0.3 m or more off on 11 % of Coral Sea's 0.8 m cells (`--compare`
  reports the old rasterisation's differences). With it, and the runtime's quarter-cell line-of-sight rule, tight edges
  fell from 1.6-4 % to 0.16-0.33 % of the walk edges and pockets from 5.5-36.5 % to 0.4-1.0 % of each main network;
  0 nodes outside an area on all four maps. LADR holds only ladder and jump records the build linked (a
  `jump_links.json` newer than the build gave records with no node, which rsnav refuses). Details: operation-rising-sun
  `research/NAVMESH_AUDIT.md`, update 2026-10-26.
- **2026-10-26 build rules:** ships' ledges (`jumplinks1943.SHIP_LEDGES`: jump links between a carrier's floors a
  ledge apart, its deck-edge galleries 0.58 m and gun tubs 0.84 m under the deck; Wake 21, Iwo Jima 21, Coral Sea 41
  links) and border nodes on the seam line (`navbuild.BORDER_SEAM_LINE`, set by `soldier1943.configure`: ground that
  reaches a sector seam but not the border line one sample beyond gets a shared node; two of Coral Sea's three seam
  breaks closed).
- **2026-10-26, round 2:** areas hold no cliff (`navbuild.AREA_CLIFF`: a surface beside a cell of its area more than
  1 m and a 45-degree slope plus the step away in height starts its own area - a bridge deck beside the ground under
  it); `join_groups` floods up to 2 m past a sector's window into sectors of the same pitch (`JOIN_MARGIN`); the
  exporter gives an area 0.4 m height blocks, then one per cell, where its 1 m grid is more than 0.25 m off at one of
  its cells (`HEIGHT_TOL`); a seam's coincident nodes are merged (`merge_coincident_border`). Tight walk edges
  150/88/124/158 -> 8/40/26/96, pockets 0.2 % or less of each main network; exports 4.2-5.5 MB.
- **2026-10-26, round 3 (exporter 1.4):** beside a mixed (0.2/0.8 m) seam, the 0.8 m graph gets walk edges where the
  0.2 m samples join ground its own step rule cannot (`SEAM_FINE_LINKS`: Coral Sea's last seam break, a 0.53 m lip
  beside the South Base bunker, closed); a sector's window grows 1 m past a mixed seam so both grids' cells overlap there
  (`MIXED_OVERLAP`; no nodes on the grown cells); exporter 1.4 rounds every edge cost up to 16 x its length and writes
  the search heuristic's scale in the header (0.999 on all four maps: the heuristic is admissible), and lists an area in
  every 4 m bucket its cells reach. Tight walk edges 8/40/26/96 -> 8/10/14/30 (Wake, Guadalcanal, Iwo Jima, Coral Sea); pockets 0.24/0.05/0.20/0.00 %
  of each main network; exports 4.3-5.5 MB.

**Vehicle navigation (new 2026-10-26).** One RSNV world per vehicle class beside the soldier's, read by rsnav like
any other world: `work\levels\<level>\navkit\<level>.jeep.rsnv`, `.tank.rsnv`, `.lcvp.rsnv`, `.plane.rsnv`, plus
`<level>.transfers.json` (every transfer, resolved against the current soldier world) and `<level>.vehicles.json` (the
checks). `tools\navexport_vehicles1943.py <level> [--sample] [--classes ...] [--check-only]` builds and checks them
all; it only reads `<level>.rsnv`. Labels: [O] from the data, [E] estimate, [H] hypothesis, [U] unknown.

- **Classes** (`tools\vehicleclasses1943.py`, which reads and prints the blueprints and collision): sizes [O] from each
  vehicle's `*_havok.res` box; slope from `WheelConfigData SlopeGripMaxAngle` (jeeps 25 deg [O]; tanks 25..52 [O],
  40 used [E]); top gear speed [O] (unit km/h [H]); entry radius and exit points [O]. Jeep: radius 1.5 m (half-width
  1.06 [O] + 0.45 [E]), height 2.0, step 0.3 [E], ford 0.8 [E]. Tank: radius 1.8, height 2.9, step 0.6 [E], ford 1.2
  [E]. LCVP: radius 2.5, draft 0.9 [E], bow ramp 5.5 m ahead [O]. Plane: clearance 25 m, cruise 60 m above the floor
  [E]. Not in the data: steering angle, step height, the entries' AIData (dangling references) [U].
- **Ground** (`tools\groundnav1943.py prepare|sample|build|all <level> [jeep|tank]`): the collision without ExcludeFromPathfinding
  objects and dynamic props (fences, palms, wire, sandbags, crates - pushed through [H]), sampled by
  `navsample1943.py` with the class's disc, step and height on the soldier's 0.8 m grid (about 11-13 min per class
  and map); drivable = not blocked, slope, ford depth, not on ForbidPathfinding objects, continuous ground under the
  disc; links by the step rule; components touching a ground-vehicle spawn kept; nodes every 6.4 m at the cell
  farthest from the undrivable, edges by a straight grid line, graph pieces joined along the grid's shortest path.
  NavKit's `.hpd` builder is not used: its rules are fitted to the soldier and DICE's BFBC2 data.
- **Landing craft** (`tools\seanav1943.py <level>`): 2 m sea grid, depth = sea level - terrain [O], blocked by collision
  crossing the waterline band; nodes every 10 m; deck lanes from each carrier craft along its own heading to the
  deck's end (the craft face the sterns [O]; leaving that way [H]) and a one-way DROP edge to the water; landing zones
  every 25 m of beach whose bow point unloads onto the soldier main island, nearest of any island and with no
  ForbidPathfinding collision between (Iwo Jima's no-go strip is never used; checked).
- **Planes** (`tools\airnav1943.py <level>`, a first version): 16 m altitude-floor grid (terrain and every object's top,
  grown by 25 m), a cruise surface 60 m above it that climbs at most 7 deg, nodes every 64 m; take-off lanes along the
  spawn headings (decks, airfields) with a JUMP edge (climb-out) and a DROP edge (landing approach); parachute DROP
  transfers over every base, flag and non-carrier soldier island (Coral Sea's islets).
- **Writer 1.2 (2026-10-29): admissible costs.** As the soldier exporter 1.4: each drive edge costs 16 x its length
  between the float32 node positions, rounded up (a given cost, a deck drop's horizontal run, no less), and the header
  carries the heuristic scale (`rsnv.heuristic_scale`: 0.999 on every vehicle world), so `rsnv.search` and rsnav's
  search never overestimate on vehicle worlds either. The heading-aware planner keeps its own weighted heuristic (x1.25,
  by design: a local, bounded search over its own primitive costs).
- **Unload points (2026-10-29):** a landing zone's alight transfer stores a soldier cell beside the craft's bow from
  which a main-island node is in sight (100 of 104 zones on Wake, 58 of 62 on Iwo Jima; the rest the node itself), and
  `rsnvtrip.resolve` / `rsn_transfer_soldier_node` resolve alight and drop transfers to the nearest node within 6 m in
  line of sight of that point (`rsnv.World.los` / `rsn_los`, 32 tried at most) when the stored node has moved: a
  rebuilt soldier world moves its nodes, the point stays [O: the cells; H: that a soldier steps off the ramp there].
- **Take-off lanes checked (2026-10-26):** every lane is tested against the collision on rsnav's flight-test obstacle
  grid (4 m cells, all objects): the centre line +-2 m clear from its start to 40 m past its end, the deck or field
  under the roll to lift-off (85 m). A blocked spawn heading is turned (up to 20 deg; then any heading: a turn on the
  ground), started up to 16 m ahead (out of a hangar) or shortened (to 75 m), in that order; each lane's meta records
  its offset, start, length, clearance and the highest obstacle within the half wingspan, with [O] / [H]. Changed: 7
  of 23 (Guadalcanal 1533, 1535, 1536; Iwo Jima 1986, 2219; Coral Sea 640, 648); Guadalcanal 1581's roll leaves its
  hilltop at 58 m (kept, flagged). The flight test takes off from all 23.
- **Transfers** (OFFM): board (1: entry point, EntryRadius, aux = soldier node), alight (2) and drop (4: the soldier
  node), launch (3: the deck end). `core\bfbc2core\rsnvtrip.py` resolves them again against the soldier world in use
  and plans walk -> ride -> walk (rsnav's `rsn_trip_plan` gives the same answer).
- **Writer:** `tools\rsnvgrid1943.py` (areas per 100 m sector by flood fill, one cell per column; bitmaps, per-cell
  heights, buckets, portals, islands, tight edges by `rsnv.World.los`). Writer 1.1 (2026-10-26): a cell on a 100 m
  sector edge goes to the sector it starts (before, i x 0.8 m rounding lost a one-cell column on every sector edge to the
  bucket lookup).
- **Heading-aware planning** (`core\bfbc2core\rsnvhybrid.py`, 2026-10-26): the reference of rsnav's `rsn_hybrid_plan`, a
  lattice hybrid A* over (cell, 16 headings, direction) with motion primitives per kind (car 4.5 m, boat 8 m turning
  radius [E], reversing at a cost; tanks pivot); rsnav's C tables are generated from it. Used locally by the drivers:
  jeeps 120 / 120, tanks 120 / 120, landing craft 96 / 96 routes in the kinematic simulation (rsnav README).
- **Tests:** `tests\test_vehicles_nav1943.py` (quick tier, made-up data); rsnav's `tests\test_vehicles.py` on the real
  worlds.
- **Built (2026-10-26) [M]:** jeep 3,263 / 4,178 / 3,012 nodes (Wake / Guadalcanal / Iwo Jima; 0.08-0.11 km2
  drivable, 1.0-1.5 MB); tank 5,252 / 5,697 / 4,191 (0.12-0.17 km2, 1.4-1.9 MB); LCVP 24,870 nodes, 104 landing zones,
  8 deck lanes on Wake, 15,093 / 63 / 8 on Iwo Jima (Guadalcanal and Coral Sea have no craft); plane about 1,000 nodes
  per map, 5-8 take-off lanes, 10-71 drops. Every check of `navexport_vehicles1943.py` passes on all four maps; every
  carrier deck reaches every flag by the craft (Wake 10/10, Iwo Jima 20/20 trips). The soldier export changes under
  these files without harm: transfers carry positions and are resolved again.

**Looking inside an export** (`tools\rsnvtool.py`, new 2026-10-26; read-only; `<level>` is a level name or an
`.rsnv` path; `--class jeep|tank|lcvp|plane` reads a vehicle world; tested by `tests\test_rsnvtool.py`):

| Command | What it gives |
|---|---|
| `info <level>` | sections, counts, edge kinds, tight edges, islands, the exporter and its sources' hashes |
| `validate <level>` | consistency (exit 1 on a problem) |
| `locate <level> X Y Z` | the walkable cell and nearest node under a point |
| `los <level> X,Y,Z X,Y,Z` | line of sight by the runtime's rule |
| `route <level> FROM TO [--soldier] [--png]` | a search between nodes or points, as rsnav finds it |
| `pockets <level>` | the main network's pieces without tight edges, the largest pockets with positions |
| `picture <level> --at X,Z [--radius R] [--y Y] [--route FROM TO]` | a top-down PNG: cells; edges (tight red, ladders orange, jumps cyan); a route in yellow |
| `diff <a> <b>` | what changed between two exports |

**Reading BF1943.** `tools\hkpack.py` reads Havok 4.6.1 packfiles (byte order from the file's layout rules,
20-byte class-member records); BF1943's packfiles carry no `__types__`, so class layouts are given by
`tools\havok461.py` (checked by `tests\test_havok461.py` on all 807 meshes of Wake Island's 97 Havok files).
`tools\levelobjects1943.py` reads object placements and collision straight from the bundles (meshes and
`hkConvexVerticesShape` boxes; `hkBoxShape` and `hkConvexTranslateShape` are not read yet and are reported), and
writes `ladders.json` from the placed objects whose blueprint has a `LadderEntityData` (Wake Island: 16). Core's
`terrain.py` and `water.py` detect the byte order.

**BF1943's soldier.** `tools\soldier1943.py` holds the soldier's physics (`DefaultSoldierPhysics`: radius 0.35 m,
step 0.3 m plus a 0.07 m tolerance, height 1.8 m, 45 deg, water depth 1.4 m), and its `configure()` sets the NavKit
settings that follow from it over the BFBC2 defaults: slope limit, water depth, the detailed zones' link step
(0.37 m), node height (0.9 m), and the headroom band (0.5-1.8 m). It also switches on BF1943's build rules
(`LADDER_ALL_GROUND`, and the seam and area rules of the 2026-10-26 entries above). NavKit's other values were fitted to DICE's BFBC2 path data and
are not checked on BF1943.

**Tools in this variant:** `detailed.py`, `detailed1943.py`, `havok461.py`, `hkpack.py`, `hpdcheck.py`,
`hpdtool.py`, `hpdview.py`, `kymesh.py`, `levelobjects.py`, `levelobjects1943.py`, `navbuild.py`, `navedits.py`,
`navsample.py`, `navsample1943.py`, `navwalk.py`, `routecheck1943.py`, `soldier1943.py`, `jumplinks1943.py`,
`navaudit1943.py` with `navaudit1943_checks.py`, `navexport1943.py`, `rsnvtool.py`, `enclosure_probe1943.py`,
`vehicleclasses1943.py`, `groundnav1943.py`, `seanav1943.py`, `airnav1943.py`, `rsnvgrid1943.py`,
`navexport_vehicles1943.py`
(`enclosure_probe1943.py <level> <x> <y> <z> [radius] [--png out.png]`: why a spot is cut off from the network). Removed from this variant:
`graphstats`, `sample_all`, `navwater`, `navlinks`, `navtraversal`, `navboundary`, `navcompare`, `navdiff`,
`navparity`, `navregress`, `ordercheck` and `ladders.py`. They remain in `bf1943-auto-navkit-little-endian`, which
stays OPFOR's BFBC2 NavKit.

**Layout of this variant:**

```
QUICKSTART.md        how to build a BF1943 level's navigation, step by step
navkitGUI.pyw        the NavKit window (Tkinter): the steps as buttons
navkit.py            driver: levels / prepare / sample / build / check / all / status
research\            specification of the path-data format (KYNAPSE_SPEC.md) and images (img\)
tools\               NavKit tools (Python 3 + numpy)
  levelobjects1943.py  BF1943: object placements, collision, flags and ladders straight from the level's bundles
  havok461.py          class layouts for Havok 4.6.1 packfiles, which have no __types__ section
  hkpack.py            Havok binary packfile reader: 5.5 (BFBC2) and 4.6.1 (BF1943), byte order from the file
  levelobjects.py      the pathfinding-flag rules levelobjects1943.py shares (and BFBC2's XML reader)
  navsample1943.py     BF1943: collision samples computed offline (terrain + collision)
  navsample.py         the samples file format: read, and compare with a path-data file
  detailed1943.py      BF1943: which sectors are built at 0.2 m (detailed.py's rule on BF1943 data)
  detailed.py          the detailed-sector rule
  soldier1943.py       BF1943's soldier and the NavKit settings that follow from it
  navwalk.py           walkability rules
  navbuild.py          the generator: samples + level objects -> a navigation file (pathdata.hpd layout)
  navedits.py          walkability edits as generator inputs
  hpdtool.py           the navigation file: info / sector / roundtrip / coverage / meshtrip
  kymesh.py            per-sector mesh: parse_mesh / write_mesh (byte-exact)
  hpdcheck.py          structure checks (closed boundaries, edges, node order, zero-length edges)
  routecheck1943.py    BF1943: spawns and flags on the route graph, routes between every pair of flags
  jumplinks1943.py     BF1943: jump links from ships' decks to their AA gun platforms, and between a carrier's floors
                       a ledge apart (SHIP_LEDGES) (ladder records of kind 'jump'); a raised-floor rule
                       (RAISED_FLOORS) is kept but off - see operation-rising-sun NAVMESH_AUDIT.md
  navaudit1943.py      BF1943: the audit - the build's walkable masks, then the checks
  navaudit1943_checks.py  BF1943: the audit's checks (required positions, reachability, consistency, layers, rules)
  navexport1943.py     BF1943: the build exported for our navigation runtime (<level>.rsnv, core rsnv.py)
  rsnvtool.py          BF1943: look inside an RSNV export - info, validate, locate, los, route, pockets, picture, diff
  vehicleclasses1943.py  BF1943: the vehicle classes (jeep, tank, lcvp, plane) from the blueprints and collision
  groundnav1943.py     BF1943: jeep and tank worlds (<level>.jeep.rsnv, <level>.tank.rsnv)
  seanav1943.py        BF1943: the landing craft's world (<level>.lcvp.rsnv)
  airnav1943.py        BF1943: the planes' world (<level>.plane.rsnv), a first version
  rsnvgrid1943.py      BF1943: the vehicle worlds' RSNV writer (cells on a grid -> an RSNV world)
  navexport_vehicles1943.py  BF1943: builds and checks every vehicle world of a level, and its transfers
  enclosure_probe1943.py  BF1943: why a spot is cut off (flood fill over the 0.2 m samples, the blocking objects, a map)
  hpdview.py           render one sector to PNG
tests\               the rule tests (made-up data), the sampler, rsnvtool, jump links, mixed-pitch seams, vehicle
                     navigation, Havok 4.6.1, Wake Island's objects and the RSNV export
```

What follows is OPFOR's NavKit description, which this variant shares. Its commands, steps and tools are the
little-endian variant's (`bf1943-auto-navkit-little-endian`), not this variant's: where it names tools this
variant no longer has (`ladders.py`, `navregress.py`, `navparity.py` and the others listed above) or OPFOR's in-game
sampler, FbrbTool and DbxEdit, those are BFBC2's pipeline. Standalone tooling to create AI navigation data
(`pathdata.hpd`) for Battlefield: Bad Company 2 levels – including multiplayer maps that never shipped with any – so
the game's own AI can move on them. Developed for Project OPFOR and published for other modders as
`bfbc2-auto-navkit`, the automated navigation-data generator of the BFBC2 suite. For BF1943, use this variant's
[QUICKSTART.md](QUICKSTART.md); BFBC2's is the little-endian variant's.

## Why

BC2's AI uses Autodesk Kynapse. The game ships the Kynapse *runtime* (which loads `pathdata.hpd`)
under DICE's runtime licence, but the *generator* that DICE used to create that data is a separately
licensed developer tool: the copy that is still inside the game executable stops with a FLEXnet
licence error. NavKit replaces it with independent code.

## Rules (clean-room approach)

1. **Research** (`research\`): the Kynapse map builder in BFBC2Game.exe and the `.hpd` files are
   reverse engineered for interoperability. Results are written up as a *specification* – file
   formats, data structures, units, what each build stage produces. Licence checks are only located
   and documented as the boundary; they are not analysed in order to defeat them and nothing in this
   project bypasses them.
2. **Implementation** (`src\`): written from the specification with our own code and open-source
   libraries (e.g. recastnavigation, zlib licence). No Kynapse code is copied or translated
   line-by-line, no Kynapse binaries are shipped, and the tool never runs Kynapse code.
3. Game data (levels, collision meshes) always comes from the user's own installation.
4. Geometry is sampled through the **game engine's own physics queries** (DICE code, via Project
   OPFOR's `[dev] navsample` option) or read from the level's own data files – never through Kynapse.
   Observations of the original generator are limited to its boundary with DICE's code (the point
   queries it makes, the files it writes) and its output data.
5. The private folder `research\kynapse mapbuilder research\` is git-ignored and never published.

## Layout

This variant's layout is at the top of this README; OPFOR's full BFBC2 layout is in the little-endian variant's README.

## Detailed zones, ladders and walkability rules (2026-10-21 / 22)

All 15 levels with DICE path data, rebuilt and measured on PC ground (`navregress.py`, run `regression_det_v6`,
2026-10-23, now the default reference `regression`): **ground 98.2 %** of DICE's, **extra ground 8.3 %**,
**connectivity 98.5** (of the routes DICE's data allows, the share NavKit's allows), over-connection 15.3 % (routes
only NavKit's allows: open slopes, stairs, steps and ladders - see below). **Every area boundary is closed** on all 15
builds (0 open areas; before the exact bridge tracing below, 1.7 - 3.1 % of the areas on sp_004_b / sp_01 were open,
DICE has none).

- **Exact bridge tracing (`navbuild.BRIDGE_UNION`, 2026-10-23):** an area's 45-degree bridges are the exact union of
  the quarter cells they cut, traced as one outline, in place of clipping each bridge on its own (`clip_bridges`,
  which left gaps where bridges met: open boundaries, and walkable ground in no area). Ground 98.12 -> 98.22 %,
  extra 8.36 -> 8.31; checked by `tests\test_bridge_union.py` (600 random patterns; the old method fails 107) and by
  `hpdcheck.py` on every regression build.

- **The default pipeline:** `navkit.py sample` (and `all`, and the window's buttons) samples the level at 0.8 m, chooses
  its detailed zones from the level alone, then samples those at 0.2 m (`DETAILED_BUDGET_MS` = 400 ms of sampling per
  frame: a 0.2 m run is ten times the columns and must end before a round, or a campaign level with an idle player,
  does). A run whose level ends first is reported, not waited out. `navkit.py build` builds the detailed sectors at
  0.2 and merges them with the normal build.
- **Detailed zones (pitch 0.2):** chosen by `tools\detailed.py` without DICE's data - sectors near an object whose
  blueprint sets `NeedsDetailedPathfinding`, and sectors with 100+ stacked-floor columns in the 0.8 m samples (93 % of
  DICE's choice on 14 levels). Built with 6 x 6 tiles, link step 0.3 m, a Gabriel-pruned route graph and coverage
  nodes every 4 m.
- **Ladder links:** `navkit.py prepare` writes `ladders.json` (BFBC2: `tools\ladders.py`; in this variant
  `tools\levelobjects1943.py`, from the objects whose blueprint has a `LadderEntityData`: placed ladders and their
  collision boxes, a buried foot raised to the terrain); the build seeds the far end of a ladder whose near end is walkable and links a node at each end.
  DICE's path data has none; the game puts any soldier who walks into a ladder on it.
- **Walkability rules added 2026-10-22** (`tools\navwalk.py`, each with a switch and its measurements in the code):
  - `LIP_WIDTH`: at 0.2 m, a band of up to two samples steeper than 40 deg between walkable ground (a lip the 0.8 m
    step rule stepped over) is walkable; `LIP_OBJECT_RISE`: the same on objects when the band is within 0.3 m of the
    floor on both sides (door sills, thresholds) - steeper than 55 deg only between flat floors (`LIP_OBJECT_FLAT`
    15 deg: a rock mesh meeting sloped terrain is no sill). sp_007's north strip, sp_009's aircraft aisles (sp_009
    connectivity 90.9 -> 100).
  - `DYNAMIC_FLOOR`, `DYNAMIC_ABSENT_HEADROOM`: dynamic props (and `PdtWhenAlive` doors) are absent for path data -
    the floor under a prop lying on it counts, and neither the prop nor rays grazing its vertical faces count as a
    ceiling. sp_003_b's monastery gate, sp_009's aircraft doors.
  - `ROOF_PRUNE`: roof islands nothing reaches are dropped - an island with no soldier spawn, AI order position or
    ladder end, mostly on objects with ground 2 m or more below, and no reached ground within 2 m at its own height.
    Ground-level islands stay (DICE keeps those). sp_008 extra ground 29.5 -> 22.7 %, sp_01 12.2 -> 9.6 %.
  - Not adopted: a slope test from heights instead of normals (`STEEP_TERRAIN`, off - DICE cuts at 40 deg by the
    normal: 15 % of 40-45 deg terrain covered), pruning islands by size or by combat area (DICE keeps small islands
    and ground outside its combat areas).
- **Collision of assets named `<name>_Havok_Havok.res`** (`levelobjects.havok_index`): indexed under the blueprint's
  name; before, such an object had no collision in NavKit (sp_009's animated aircraft door stayed solid).
- **navparity** clips DICE's reference to the level's terrain (ground beyond it does not exist on PC), leaves out
  DICE's route nodes with no PC ground within 1 m of their height (`stale_nodes`, column *stale_reference_nodes*:
  data built before late geometry - sp_03's only valley-to-plateau links run under a cliff mesh added afterwards),
  matches nodes within 1 m of height, and reports ground reached by ladder as its own column (*ladder %*), not as
  extra ground.
- **"Up" border portals** (a border portal whose direction is (0, 1, 0)): about half of DICE's border portals
  (50-58 % on sp_004_b, sp_01, ONS_MP_002), none of NavKit's. Not a kind of portal: they lie where DICE's ordinary
  ones lie (the sector margin, along the line), the neighbour sector's area is across them as for the others (100 %),
  and along one border line consecutive pieces of the same geometry alternate between (1, 0, 0) and (0, 1, 0); the
  y slot also holds 0.38, 0.92, 0.71 and -2.0. A default or left-over value in DICE's generator; NavKit writes the
  outward direction, and its files route across sector borders in game. (DICE also splits border portals per 0.8 m
  cell - 3-4x NavKit's count, the same geometry.)
- **Anomalies against DICE** (Navigation Studio's overlay counts, 15 levels, 2026-10-22 - DICE / NavKit): soldier
  spawns on islands under 25 nodes 147 / 72, AI orders without a route node 768 / 686, sliver areas 77,996 /
  62,708, lonely route nodes 5,081 / 10,011, islands under 25 nodes 5,684 / 6,390. NavKit has fewer of the two that
  stop bots; more lonely nodes, and more slivers on levels with many detailed sectors (clutter at 0.2 m).
  - *Lonely nodes* (no other node within 12 m) are node spacing, not junk: on sp_006 2,634 of NavKit's 2,638 are on
    islands of 25+ nodes - normal sectors get route nodes only at area centres and portals. Proposed: coverage nodes
    in normal sectors too (`NODE_COVER`, about 8-10 m), tuned by regression.
  - *Sliver areas* (under 5 cells, no portal) are mostly 0.2 m clutter (sp_006: 7,712 of 9,060 in detailed zones;
    DICE 1,160): one-area islands nothing routes onto. Proposed: drop isolated areas of a few 0.2 m cells unless a
    spawn, order or ladder anchors them.
  - *Border-to-border duplicate nodes* (two border nodes at one spot joined by a zero-length edge): none any more -
    sp_01, sp_004_b and ONS_MP_002 have 0, as DICE's (shared corner nodes, 2026-10-20).
- **Missing ground** (DICE's walkable ground NavKit lacks, about 1.9 %; five levels classified, 2026-10-23): 74-97 %
  of it is a rim beside NavKit's ground. By what NavKit's samples show there: terrain steeper than 40 deg 56 % (DICE's
  areas reach a cell past where the samples turn steep), walkable but not kept 22 % (on sp_01: DICE's own small
  unreached islands of 2-23 nodes 64 %, cells outside their own unclosed outline 22 % - fixed by `BRIDGE_UNION`),
  no surface at DICE's height 17 % (half of it under objects - on ONS_MP_002 vegetation; making vegetation passable
  was tried and dropped: about 0.1 % of DICE's ground to gain, and both versions lost ground), no room 3.5 %.
- **Closed area boundaries** (`navbuild.BRIDGE_UNION`, 2026-10-23): DICE's files have no area whose outline, portals
  and border portals leave an open end; NavKit's had them in 1.7-3.1 % of areas (sp_004_b, sp_01), all at cell
  corners where a 45-degree bridge piece ends - overlapping bridges were clipped asymmetrically, and an area's cells
  fell outside its own outline. The bridges' outline is now the exact union of the half-cells they cover.
- **M-COM targets on the Onslaught maps** (2026-10-23): 14 of the 15 AI attack orders that sit on an M-COM
  (`mpobjective`, within 3 m; ONS_MP_002 7 of 8, ONS_MP_004 6 of 6) are on no area in NavKit's file - DICE's console
  file has ground at all 15, its layout having no object there. The M-COM is `ForbidPathfinding` (its top is not
  floor - DICE's campaign files carve all 24 of theirs); the hole at its centre is its collision (no room for the
  soldier), not the flag, so clearing the flag would not help. The game's destination query fails there and units
  stop short. Planned: keep the floor under an AI order point that lies on a forbidden object, out to the walkable
  ground around it (the order point unchanged - as in DICE's console file, with which bots reach and arm M-COMs).
  No campaign order sits on an M-COM.
- **Destruction:** path data describes the intact level (DICE's and NavKit's). Objects carry `DisablePathfinding`:
  `PdtNever` (32,854 placed objects on the 15 levels), `PdtWhenAlive` (729: doors, gates, scripted walls - absent for
  path data; NavKit honours it), `PdtWhenDestroyed` (556: houses, factories, walkways, rope bridges - inferred: the
  runtime switches their path data off once destroyed), `PdtWhenPartDestroyed` (13); level settings
  `DestructibleLocalPathfindingParts`, `PathObjectCount`, `MaxLocalPathfindingObjects` point to runtime handling.
  Not yet checked that the switch-off works on NavKit's files (a play-test item). Openings made by destruction are
  not routes in either; precomputed "breach links" for OPFOR's bot layer are on the TODO list (OPFOR
  `DEVELOPMENT_NOTES.md`).
- **Still open:** sp_003_b (connectivity 93.3: a rocky gully whose rough terrain fails the slope test at 0.2 m almost
  everywhere); soldier spawns on small islands where NavKit has more than DICE (sp_008_b, sp_02, sp_003_b, sp_006,
  sp_01, a few each); ONS_MP_002's power-line pylons and a cliff top that roof pruning keeps (they stand beside each
  other at one height); the M-COM target rule (above); lonely nodes and slivers (above); vehicle path data; and play
  tests of the current builds (ladder links and destroyed houses included).

## Plan and status (2026-10-23)

1. **`.hpd` format – done.** Header, sector table, per-sector graph and runtime mesh fully decoded
   (spec §4); `hpdtool.py` parses every campaign file.
2. **Byte-exact rewrite – done.** `hpdtool.py roundtrip` / `meshtrip` re-emit every shipped file and
   all 7,944 sector meshes identically.
3. **Builder behaviour – done.** Spec §3: stages, the sampling grid, area cuts, graph rules,
   auto-seeding, the walkability rules (§3.7), structures and doors (§3.8), and the rules found on the
   other campaign levels (§3.9).
4. **Generator – runs on all 13 campaign levels and the Onslaught maps (`tools\navbuild.py`).**
   - Full pipeline: walkable surfaces from two in-game sampling runs (normal and high capsule) plus the
     level's objects (`levelobjects.py`), 100 m sectors (level `SectorOffsets`; without one, DICE's rule:
     one sector below the path data's lowest extent), 3 × 3 tiles of 50
     samples, single-layer 8-connected areas, outlines with 45° bridges, portals (B), border portals (C),
     neighbour links, route network (area, portal and shared border nodes; walkable edges up to 15 m;
     groups joined through string-pulled waypoints; no lone nodes), written as `pathdata.hpd`. Seeds:
     sector perimeter lines and soldier spawn points.
   - Rules added 2026-10-17 to 10-19 (details and measurements: spec §4.5):
     - **areas** are whole pieces of a tile joined by DICE's raw 0.6 m terrain step (`AREA_LINK`); no
       20 m bisection;
     - **edge sectors** (on the sector grid's minimum X / Z) start their tiles and their rectangle at
       their own edge (`EDGE_SECTOR_TILES`, `EDGE_SECTOR_RULE`);
     - on a level with DICE path data the ground is **bounded to DICE's extent** (`CLIP_TO_REFERENCE` -
       a bound for comparison, not a rule found in the level data; a new map keeps its sampling box);
     - **portals** stand on both of their areas; a piece whose target sees that side as its own interior
       becomes outline (`SYMMETRIC_PORTALS`, `ONE_WAY_PORTALS`);
     - **soldiers** standing in the level while it was sampled are not in the path data
       (`navwalk.CHARACTERS_ABSENT`);
     - each area's **height range** follows DICE's convention: lowest ground to highest ground + 1.52 m.
   - **In game:** sp_01's file loads and the AI walks on it (play test 2026-10-15). sp_004_b (play tests
     2026-10-19, same launch as DICE's file, only the `.hpd` differing): the file loads, the pathfinder
     finds paths (394 of 400 requests), but **the friendly squad does not follow them**, where with DICE's
     file it does. After a found path the game checks whether its last point equals the requested destination
     exactly, and further checks decide whether it is followed (`DEVELOPMENT_NOTES.md` in Project OPFOR);
     measuring how NavKit's paths end, and which check they fail, is the next step. Leaving the level with NavKit's file also crashes at shutdown (a Kynapse block released after its memory manager was
     destroyed; not with DICE's file) - a separate open bug.
   - Accuracy against DICE's files (`ordercheck.py`: AI order / patrol positions on the areas, and the
     pairwise route check), 2026-10-15:

| Level | DICE-covered order positions also on NavKit's areas | Pairwise route check | Main route network (NavKit / DICE) |
|---|---|---|---|
| sp_01 | 618 / 633 (97.6 %) | 89.5 % | 639 / 607 |
| sp_02 | 241 / 260 (92.7 %) | 84.2 % | 313 / 362 |
| sp_03 | 343 / 353 (97.2 %) | 92.4 % | 374 / 232 |
| sp_04 | 500 / 526 (95.1 %) | 89.5 % | 582 / 593 |
| sp_003_b | 600 / 635 (94.5 %) | 87.3 % | 686 / 712 |
| sp_004_b | 169 / 181 (93.4 %) | 86.4 % | 185 / 192 |
| sp_005 | 304 / 309 (98.4 %) | 97.2 % | 308 / 312 |
| sp_005_b | 671 / 683 (98.2 %) | 94.8 % | 722 / 728 |
| sp_006 | 258 / 270 (95.6 %) | 92.7 % | 280 / 263 |
| sp_007 | 367 / 388 (94.6 %) | 88.6 % | 427 / 443 |
| sp_008 | 278 / 289 (96.2 %) | 83.4 % | 44 / 16 |
| sp_008_b | 110 / 113 (97.3 %) | 79.3 % | 107 / 58 |
| sp_009 | 68 / 72 (94.4 %) | 20.1 % | – (plane interior; measure unsuited) |
| **All 13** | **4,527 / 4,712 (96.1 %)** | | |

   - The pairwise check compares route-network layout; DICE's own network has gaps (spec §3.8), so it
     is a guide, not ground truth. Open leads: sp_008_b (sloped bridge foundation / fence passage DICE leaves
     out), sp_009 (plane aisles narrower than the capsule; DICE connects them), sp_03 / sp_008 (DICE network gaps).
   - Not generated yet: D lines and F cells (mostly in normal-pitch areas, not only detailed zones),
     "up" border portals, diagonal border portals, the 20 m midline cuts, detailed zones (pitch 0.2),
     vehicle data, ladder links (planned for MP bots, optional).
   - Then: MP maps (self-check, no DICE data); later a standalone mode that reads Havok collision
     directly instead of sampling in game.
5. **Tooling – done (2026-10-15).** `navkit.py` runs the whole workflow from a level name (unpack, objects,
   automatic sampling box and sector offsets, both in-game sampling runs with `opfor.ini` restored
   afterwards, build, check, install / restore with the original kept); `navkitGUI.pyw` is a small window
   over it. Stock MP maps wait for MP support: the sampler needs the level's AI running.
6. **DICE parity – the goal from 2026-10-16: 100 % of DICE's content, not identical bytes.**
   - **Measured by content:**
     - `tools\navdiff.py` compares two files layer by layer: sectors, walkable cells, areas,
       boundaries, route graph. A file against itself scores 100 % on every layer.
     - `tools\navparity.py` runs it on every level, with islands (connectivity, over-connection) and the
       AI-position figures, saved per named run.
   - **Baseline A** (the builds behind the table above), means over the 13 levels:
     - 98.0 % of DICE's walkable ground reproduced, but +36.9 % extra ground;
     - 0 % of areas identical, and 0.6 % of route nodes in the same place;
     - connectivity 96.6 %, over-connection 26.4 %;
     - AI positions 95.8 %, pairwise 83.5 %.

     `python tools\navparity.py show "baseline A"` prints the per-level table.
   - **The earlier figures measured less:** the AI-position coverage in the table above counts only where
     the level's AI is ordered to go.
   - **Regression test (2026-10-16):** `tools\navregress.py` rebuilds levels with the current code (the
     inputs of `navkit.py build`) and fails if any parity measure got worse than the reference run
     (`regression`, made from the code of 2026-10-16 and compared with baseline A). Run it before and after
     every generator change; `--save regression` after an intended improvement.
     - **The reference against baseline A** (13 levels, 70 minutes): mostly within a few tenths of a point
       either way, from different build inputs (baseline A's files were built by hand, the reference uses
       `navkit.py build`'s: spawns as seeds, no exclusion files). Larger: over-connection on SP_008
       (10.5 -> 35.5 %) and SP_008_B (32.2 -> 69.0 %), connectivity on SP_009 (85.9 -> 83.8 %), AI pairwise on
       SP_008_B (79.3 -> 76.6 %) and SP_02 (84.2 -> 82.7 %); connectivity better on SP_02 (+0.7) and SP_008
       (+1.2). Which input makes the SP_008 difference is still to find (likely the exclusion files).
   - **Hand edits (2026-10-16): `navkit.py apply <level>`** builds the level with the edits of its
     navigation side file (`work\navkit\<level>\<level>.navtune.json`, made in the Navigation Studio):
     unwalkable regions remove the sampled surfaces inside them before seeding (so a cut really
     disconnects), walkable regions force them in past the slope and clearance rules (they still join the
     network by the step rule), seeds add a seed, unseeds drop the island at the point. Later edits win where
     regions overlap. Then links and route edges go into the route graph (`tools\navlinks.py`): each point
     joins its nearest route node, both nodes must be in one sector (border nodes count for both), links and
     added edges are two-way unless set one-way, costs follow DICE's rule. On a DICE base there is no rebuild:
     the links go into a copy of DICE's file, `pathdata_dice_edited.hpd`, for this PC only. The output is
     read back to check it. Traversals (ladders, jumps, vaults, drops) go to `<level>.traversal.json` next to
     it (`tools\navtraversal.py`) for OPFOR's bot layer, not into the path data: the game's AI would plan
     through a ladder it cannot climb. Each has its ends, the route node and island at each end, and whether
     it joins two islands. The whole log is kept as `<output>.report.txt`. `--install` then installs the
     result (`navkit.py install --from <file>` installs any built file), with the traversal file beside the
     installed path data; `restore` removes both and puts DICE's file back. A full rebuild (about 1-8
     minutes a level); without edits the build is unchanged.
   - **Edge costs (fixed 2026-10-16):** every DICE edge costs `floor(distance * 16)` from the positions as
     stored; NavKit computed it from unrounded positions, and 138 of SP_004_B's 60,142 costs came out one
     unit off. NavKit now uses the stored positions (0 off; parity measures unchanged).
   - **Ground under water (2026-10-16, `tools\navwater.py`):** DICE keeps walkable ground down to about
     1.5 m under water and almost none deeper (11 campaign levels: 85-90 % of NavKit's ground at 0-1.5 m is
     DICE's too, 1-8 % below that; DICE has 241 cells at 1.5-2 m, 154 at 2-3 m and none deeper; median
     per-level limit 1.45 m). NavKit keeps it all: 29 % of NavKit's extra ground is deeper than DICE goes on
     that level (SP_01 73 %, SP_02 52 %). DICE's multiplayer data does the same (Onslaught: ONS_MP_002 72 of
     18,878 floors under water deeper than 1.5 m, ONS_MP_004 1 of 19,639).
   - **The water rule (2026-10-16):** NavKit's builder leaves out ground more than 1.5 m under the level's
     water (`navbuild.MAX_WATER_DEPTH`; the water mesh from the terrain bundle; hand edits forcing ground
     walkable still win). Over the 13 levels, against baseline A: extra ground +36.9 % -> +29.1 % (SP_01
     54 -> 16 %, SP_008_B 127 -> 91 %, SP_007 17 -> 10 %), DICE ground reproduced 98.0 % (unchanged),
     connectivity 96.6 % (unchanged); over-connection lower than the earlier reference on most levels.
     Connectivity fell 0.16 points on SP_04 and SP_004_B (SP_04 is the level where DICE keeps ground deepest,
     2.64 m) - accepted; a per-level limit is possible later. The reference run `regression` now includes the
     rule (the one before it: `regression_before_water`).
   - **Area division and bounds (2026-10-17 to 10-19):** `tools\navboundary.py` compares area boundaries
     cell by cell with DICE's (maps, CSV, precision / recall). On sp_004_b, from the start of this work to
     now: boundary precision / recall about 59 / 54 -> 99.4 / 94.1 %, extra ground 21.5 -> 8.6 %,
     over-connection 55.7 -> 21.7 %, sectors 50 -> 40 (DICE 40), portals with a portal back 78 -> 100 %,
     AI positions 93.4 -> 97.2 %, AI pairwise 85.0 -> 91.2 %; route nodes within 2 m of DICE's 36.5 -> 34.7 %
     (the bisection's extra nodes had matched by chance). On the other levels measured (sp_005, sp_007,
     sp_01, sp_006, sp_008_b) the structure measures improve the same way; nodes_near is still below the
     old reference. Levels whose DICE ground is mostly detailed zones (sp_008, sp_008_b, sp_009,
     ons_mp_005gr) are to be reported apart until NavKit builds detailed zones. The full 13-level regression
     is run only when asked (`navregress.py` shows progress and time left per level).
   - **What the game needs from the file (found in play tests on sp_004_b, 2026-10-19 to 10-20):** with
     these four rules the friendly squad's go-to requests succeed 100 % (they had stalled at the spawn, then
     at the top of the stairs), and leaving the level no longer crashes (also checked on sp_01). Each is a
     `tools\navbuild.py` setting, on by default:
     1. **one route node per surface** (`DEDUP_GRAPH_NODES`): no two ordinary nodes at one position, as in
        DICE's files (NavKit had 377 duplicate pairs joined by zero-length edges);
     2. **each sector's nodes sorted by id** (`SORT_NODES_BY_ID`), ordinary nodes first, border nodes
        (id flag 80000000h) last, as DICE writes them: the game binary-searches these arrays, and unsorted
        ones made it miss path ids and crash when leaving the level;
     3. **one owner per border node** (`BORDER_OWNER`): bit 16 of a node's `c` field is set only on the
        copies in sectors that do not own it (DICE: the owner is the sector whose 100 m square holds the
        node); NavKit had set it on every copy, and searches did not expand through border nodes;
     4. **a route node at each AI order position** (`ORDER_NODES`): the game resolves a go-to point to the
        nearest node within 15 m, and the request failed where that node was 3.6-6.3 m from the order
        point (DICE: 0.2 m). Order positions are the level's `AreaGeometryEntityData` points, written as
        `work\levels\<level>\order_points.json` by `levelobjects.py` (part of `navkit.py prepare`).
     A detour repair pass (`DETOUR_REPAIR`) is kept but off: shorter routes did not fix the stairs-top
     failure. How the game uses the file is written up in spec §4.5 and in OPFOR's
     `research\BFBC2_DECOMP_RESEARCH.md` §9.
   - **13-level regression with the four rules - accepted (2026-10-20):** order nodes in route islands under
     25 nodes are dropped (`ORDER_MIN_ISLAND`; it replaced a per-level exception for sp_009); zero-cost edges
     0 everywhere, sorted sectors 100 %, owners 98-100 %.
   - **Multiplayer, first map: ONS_MP_002 (2026-10-20)** - Napisal's co-op Rush level on Valparaiso, whose
     path data is DICE's Xbox 360 Onslaught file (byte-identical). Found and done on the way:
     - levels without a `SectorOffsets` asset (the Onslaught ports, stock MP maps, new maps) get DICE's rule
       for the sector numbering: one sector below the path data's lowest extent. Correction (2026-10-21): the
       rule holds for every shipped infantry asset but not for 7 of 13 vehicle assets; a DICE file's own offset is
       read from its sector ids (floor(sector centre / 100) - grid id, the most common value: 27 of 27 shipped
       assets). NavKit's own files are numbered by its own offsets, so its builds stay consistent;
     - the level reader accepts the attribute orders other tools write (`core/bfbc2core/xmlinst.py`);
     - the game itself reads `Precomputed/<kind>/SectorOffsets` at level load and uses 0,0 when it is
       missing - every route request from one sector then fails, on DICE's own file. OPFOR's
       `[dev] sector_offsets=-3,-7` supplies DICE's values; with it the AI advance and destroy an M-COM;
     - the first NavKit-against-DICE play test on an MP map (per unit, with OPFOR's unit counters): NavKit's
       file fails 1.8-2.5 % of route requests (DICE's 0.09 %) but gets more units walking. The failures:
       units stopping at (83, 254) - probed in game, not a route split and not node placement: the request's
       node lookup fails at the runtime's query-chain check at that spot (0.65 % of requests; parked as a known
       confound); and the objective targets, where NavKit carves the M-COM footprints and the destination point
       query fails (a mission-layer question). An order-node rule for orders inside obstacles was tried and not
       adopted (it fired on campaign levels; kept as a stash).
     - The in-game samples have a coverage hole south of z ~ -511 on this map (a sampler question).
     - Stock MP maps have no AI layer (HasAI false, one disabled AI spawner, no orders, squads or path data):
       bots there need an AI layer authored on top, as Napisal did; NavKit's part comes after that.
     Details: OPFOR's `MP_APPLICATION_LOG.md`, spec section 4.5.
   - **Since then (2026-10-21 to 10-23):** combat on ONS_MP_002 fixed (OPFOR's Onslaught import); the sampler's
     south "hole" is beyond the PC terrain (the reference is now clipped to it); detailed zones at 0.2 m, chosen
     from the level alone, sampled and built by default; ladder links; the walkability rules and parity measures in
     the section above; "up" border portals found to be a quirk of DICE's data. The stock-MP AI layer is planned in
     OPFOR's `STOCK_MP_AI_PLAN.md`.
   - **Next:** the M-COM target rule and a play test on ONS_MP_002 (bots reaching and arming M-COMs; a destroyed
     house's floors), then the open items above (spawns on small islands first), D lines and F cells, vehicle path
     data. Later, with OPFOR's bot layer: breach links for walls opened by destruction. The 20 m midline cuts are
     parity only (conditional, one axis; not what broke the sp_004_b routes).
   - **Accepted limits:** hand edits, and geometry that differs from the shipped game.

Credits: in BFBC2, level data is read with the suite's FbrbTool and DbxEdit (in this variant, with
`fbrb1943.py` and core `dbx.py` through `game1943.py`), based on **Frankelstner**'s format
documentation and **Heico**'s BFBC2 Toolkit. **Napisal**'s path data for multiplayer maps (BBC2 ModPack)
showed that BC2's AI can run on them. Kynapse is a trademark of Autodesk; Battlefield: Bad Company 2 is
© Electronic Arts / DICE.
