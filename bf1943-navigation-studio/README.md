# BF1943 Navigation Studio (bf1943-navigation-studio)

Part of the BF1943 Tool Suite. **BF1943 status: ready** (2026-10-23) for viewing and the path test; there is no
editing.

View a BF1943 level's navigation build in 3D, over the level's terrain, water and collision, with its bases, flags,
spawns and ladders. The build is NavKit's (`bf1943-auto-navkit-big-endian`): BF1943 ships no path data, so there is
no DICE file to compare with.

**Opening it:** in the suite window (`BF1943 Tool Suite.pyw`), Levels tab: choose a level, then **Open in
Navigation Studio**. Or double-click `studio.py` here: a picker of your copy's levels opens. Or from a command line:

```
python studio.py <level>                       e.g. python studio.py wake_island_s
python studio.py <level> [--hpd <file.hpd>] [--color island|sector|pitch|small-islands]
```

**Files.** The level is read from your copy's bundles (`core/bfbc2core/level1943.py`; `--game <folder>` or
`BF1943_GAME` selects the game folder); the copy is only read. The default build is `work\levels\<level>\navkit\<level>.hpd`, written by
`bf1943-auto-navkit-big-endian` (`python navkit.py all <level>`); `--hpd` opens another file. Without a build the
studio says so and exits. Route nodes stand 0.9 m above the ground (half the soldier's 1.8 m).

**Layers:** terrain, water, collision; the build's walkable ground (its 0.8 m cells, on the exact terrain height
where they lie on terrain), outlines, portals, border portals, the route graph, ladder ground (violet: ground
reached only through a ladder link) and ladder links; soldier spawns of team 1 (US), of team 2 (Japan) and at
flags; bases, flags and ladders. Collision, outlines, portals, border portals and the route graph are hidden at
start.

**Colour the ground by**

| Mode | Shows |
|---|---|
| island | connected parts of the route graph: the main network green, every other island its own colour |
| sector | the 100 m sectors |
| pitch | detailed zones (0.2 m areas) in orange |
| small islands | islands under 25 nodes in magenta |

**Path test (P).** Press P, click a start and an end on the ground: the cheapest route over the build's route
graph (its directed edges and stored costs) is drawn, with its cost, length and the detour against the straight
line. Without a route the panel says why: which island each point is on and where they come closest (drawn in
red, with a button that flies there), or that no route node is within 15 m of a point. It plans over the graph
only; a runtime also smooths the route through the areas. (`core/bfbc2core/navpath.py`; `--path "x,y,z;x,y,z"`
for tests.)

**Panels**

- *Navigation:* the build's areas (detailed ones), cells, route nodes and edges, islands, ladder links and
  ladder ground; colour mode; layers.
- *Islands, spawns and flags:* the spawns, bases and flags that are not on the main network (or have no route
  node within 15 m), each with a button that flies there; the carriers are their own networks (the sea divides
  them). Then the islands, largest first, with their size.
- *Selection:* click ground to see its area (sector, tile, layer, pitch, cells, boundary pieces, island);
  click a spawn, base or flag to see its island and its nearest route node. Hovering shows the area under the
  mouse.
- *Path test:* see above.

The camera works as in the Map Inspector (left drag rotates, right drag pans, wheel zooms, double-click flies
to a point, F for fly mode). For tests: `--screenshot out.png [--frames N] [--color ...] [--island N]
[--path "x,y,z;x,y,z"] [--focus x,y,z] [--hide <layer>,...]`.

The navigation data comes from `core/bfbc2core/navdata.py`, which reads the file with NavKit's byte-exact reader.

The BFBC2 studio's DICE comparison, `--compare` / overlay and edit workflow (`editing.py`, `overlay.py`, navtune
side files with `navkit apply` / `install`) were removed: BF1943 has no DICE reference and no Kynapse runtime to
install into.
