# BC2 NavKit

Standalone tooling to create AI navigation data (`pathdata.hpd`) for Battlefield: Bad Company 2
levels – including multiplayer maps that never shipped with any – so the game's own AI can move on
them. Developed for Project OPFOR and published for other modders as `bfbc2-auto-navkit`, the automated
navigation-data generator of the BFBC2 suite. It measures levels through Project OPFOR's in-game sampler,
so it needs an OPFOR install, plus the suite's FbrbTool and DbxEdit.

**To use it, start with [QUICKSTART.md](QUICKSTART.md).** Double-click `navkitGUI.pyw` for the window, or
run `python navkit.py` from a command prompt.

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

```
QUICKSTART.md        how to build path data for a level, step by step
navkitGUI.pyw        the NavKit window (Tkinter): the steps below as buttons
navkit.py            driver: levels / prepare / sample / build / check / install / restore / status / all
research\            specification (KYNAPSE_SPEC.md) and images (img\)
tools\               NavKit tools (Python 3.13 + numpy / matplotlib)
  hpdtool.py         pathdata.hpd: info / sector / roundtrip / coverage / meshtrip
  kymesh.py          per-sector runtime mesh: parse_mesh / write_mesh (byte-exact)
  hpdview.py         render a sector to PNG (outlines, portals, graph)
  navsample.py       read in-game collision samples; compare with a pathdata.hpd
  hkpack.py          Havok 5.5 binary packfile reader (the level's *_Havok.res collision assets)
  levelobjects.py    object placements, pathfinding flags, forbidden-object triangles of a level
                     (reads the level XML through core's bfbc2core/xmlinst.py: instances and field refs in any
                     attribute order - DICE's tools write guid / ref first, Napisal's type / name first)
  navwalk.py         walkability rules + scoring against existing path data
  navbuild.py        the generator: samples + level objects -> pathdata.hpd
  ordercheck.py      scoring: AI order positions on the areas, pairwise route check, self-check
  navcompare.py      two files on a level's AI positions: coverage, main network, pairwise, size
  navdiff.py         two files layer by layer: sectors, walkable cells, areas, boundaries, route graph
  navparity.py       DICE-parity report over many levels, saved per named run
  navregress.py      regression test: rebuild levels with the current code, fail if a parity measure got worse
  navwater.py        water depth against path-data coverage (does the data leave out ground under water?)
  navboundary.py     area boundaries cell by cell against DICE's: matching / missing / extra edges, merged areas, portals; CSV + maps
  navedits.py        the walkability edits of a navigation side file as generator inputs (navkit.py apply)
  navlinks.py        a side file's links and route edges written into a path-data file's route graph
  navtraversal.py    a side file's traversal annotations (ladders, jumps ...) as <level>.traversal.json
src\, tests\         (reserved for the generator proper and its tests)
```

## Detailed zones, ladders and walkability rules (2026-10-21 / 22)

All 15 levels with DICE path data, rebuilt and measured on PC ground (`navregress.py`, run `regression_det_v3`,
2026-10-22): **ground 98.1 %** of DICE's, **extra ground 8.4 %**, **connectivity 98.3** (of the routes DICE's data
allows, the share NavKit's allows), over-connection 15.3 % (routes only NavKit's allows: open slopes, stairs, steps
and ladders - see below).

- **The default pipeline:** `navkit.py sample` (and `all`, and the window's buttons) samples the level at 0.8 m, chooses
  its detailed zones from the level alone, then samples those at 0.2 m (`DETAILED_BUDGET_MS` = 400 ms of sampling per
  frame: a 0.2 m run is ten times the columns and must end before a round, or a campaign level with an idle player,
  does). A run whose level ends first is reported, not waited out. `navkit.py build` builds the detailed sectors at
  0.2 and merges them with the normal build.
- **Detailed zones (pitch 0.2):** chosen by `tools\detailed.py` without DICE's data - sectors near an object whose
  blueprint sets `NeedsDetailedPathfinding`, and sectors with 100+ stacked-floor columns in the 0.8 m samples (93 % of
  DICE's choice on 14 levels). Built with 6 x 6 tiles, link step 0.3 m, a Gabriel-pruned route graph and coverage
  nodes every 4 m.
- **Ladder links:** `navkit.py prepare` writes `ladders.json` (`tools\ladders.py`: placed ladder objects and their
  collision boxes); the build seeds the far end of a ladder whose near end is walkable and links a node at each end.
  DICE's path data has none; the game puts any soldier who walks into a ladder on it.
- **Walkability rules added 2026-10-22** (`tools\navwalk.py`, each with a switch and its measurements in the code):
  - `LIP_WIDTH`: at 0.2 m, a band of up to two samples steeper than 40 deg between walkable ground (a lip the 0.8 m
    step rule stepped over) is walkable; `LIP_OBJECT_RISE`: the same on objects when the band is within 0.3 m of the
    floor on both sides (door sills, thresholds). sp_007's north strip, sp_009's aircraft aisles.
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
- **Still open:** sp_003_b (connectivity 93.3: a rocky gully whose rough terrain fails the slope test at 0.2 m almost
  everywhere), a small seat-row pocket in sp_009's aircraft, and ladder links not yet play-tested with NavKit's
  files (does a bot on a route take the ladder?).

## Plan and status (2026-10-20)

1. **`.hpd` format – done.** Header, sector table, per-sector graph and runtime mesh fully decoded
   (spec §4); `hpdtool.py` parses every campaign file.
2. **Byte-exact rewrite – done.** `hpdtool.py roundtrip` / `meshtrip` re-emit every shipped file and
   all 7,944 sector meshes identically.
3. **Builder behaviour – done.** Spec §3: stages, the sampling grid, area cuts, graph rules,
   auto-seeding, the walkability rules (§3.7), structures and doors (§3.8), and the rules found on the
   other campaign levels (§3.9).
4. **Generator – runs on all 13 campaign levels (`tools\navbuild.py`).**
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
   - **Next** (in this order):
     1. combat on ONS_MP_002 (the AI aim but never fire - level-data candidates: no squad entities, player
        kits on the AI spawners; tests proposed in OPFOR's MP log);
     2. the AI layer for stock MP maps (a design first; the Navigation Studio's reference overlay shows
        Napisal's and DICE's layers on a stock map);
     3. the tile size of detailed sectors (how the game gets 20 m tiles), then detailed zones at 0.2 m
        (sectors with an object flagged `NeedsDetailedPathfinding`; new in-game sampling);
     4. side items: D lines and F cells, "up" border portals, sliver areas, border-to-border duplicate
        nodes (sp_01 has 22 zero-cost edges from them), the sampler coverage hole.
     The 20 m midline cuts are parity only (conditional, one axis; not what broke the sp_004_b routes).
   - **Accepted limits:** hand edits, and geometry that differs from the shipped game.

Credits: level data is read with the suite's FbrbTool and DbxEdit, based on **Frankelstner**'s format
documentation and **Heico**'s BFBC2 Toolkit. **Napisal**'s path data for multiplayer maps (BBC2 ModPack)
showed that BC2's AI can run on them. Kynapse is a trademark of Autodesk; Battlefield: Bad Company 2 is
© Electronic Arts / DICE.
