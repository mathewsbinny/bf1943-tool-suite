# NavKit Quickstart (BF1943)

NavKit makes a navigation build for a level of your Battlefield 1943 copy: walkable ground, areas, portals and a
route graph, saved as `<level>.hpd`. The build is the input of our own navigation runtime for bots; BF1943 has no
Kynapse runtime, so nothing is installed in the game. This page shows how to make one from start to finish.

To learn how it works inside, read [README.md](README.md) and the [specification](research/KYNAPSE_SPEC.md).

## Before you start

You need:

1. **A BF1943 game folder** from `bf1943-xenia-kit` (its `output\title\dist\xenon` folder, made from your own
   package). NavKit finds it by itself. If yours is elsewhere, set the environment variable `BF1943_GAME` to it,
   or add `--game <folder>` to every command.
2. **Python 3** from [python.org](https://www.python.org/). When you install it, tick "Add python.exe to PATH".
3. **numpy.** Open a command prompt and run:

   ```
   pip install numpy
   ```

   **Show in 3D** opens the Navigation Studio, which also needs `moderngl`, `glfw` and `imgui-bundle`.

NavKit does not start the game. The game folder is only read.

## The easy way: the NavKit window

Double-click **`navkitGUI.pyw`** in the `bf1943-auto-navkit-big-endian` folder. The window has a level list at the
top, then the steps, and a log of what NavKit is doing at the bottom.

1. **Pick a level** in the *Level* list, for example `wake_island_s`.
2. Click **1. Prepare.** NavKit reads the level's objects, collision, flags, ladders, terrain and water from the
   bundles, and sets the sea level and sector offsets.
3. Click **2. Sample.** NavKit computes the collision samples from the level's data: twice at 0.8 m, then twice
   at 0.2 m in the detailed sectors (buildings and other places with floors above floors, which NavKit finds by
   itself). This is the long step: about 30 minutes on Wake Island.
4. Click **3. Build.** NavKit makes `<level>.hpd` with BF1943's soldier, its ladders, and jump links from ships'
   decks down to their AA gun platforms. About 15 minutes on Wake Island.
5. Click **4. Check.** NavKit checks the file's structure and its routes (see [Reading the check](#reading-the-check)).
6. Click **Show in 3D** to open the build in the Navigation Studio.

**Run all (1-4)** does steps 1 to 4 in a row. A tick next to a step means it has been done for that level (a cross:
the check failed). **Cancel** ends the running step and the samplers it started. **Open output folder** shows that
level's files.

## The same steps from a command prompt

Run these from the `bf1943-auto-navkit-big-endian` folder:

```
python navkit.py levels                    your copy's levels, and what has been done for each
python navkit.py prepare wake_island_s
python navkit.py sample  wake_island_s     --pitch 0.8 or --pitch 0.2: only that pass
python navkit.py build   wake_island_s
python navkit.py check   wake_island_s     exit code 1 when the check fails
python navkit.py status  wake_island_s     what has been done for a level
python navkit.py all     wake_island_s     prepare, sample, build and check in one go
```

The 0.2 m pass needs the 0.8 m pass first. If the 0.2 m samples are missing, Build builds the detailed sectors at
0.8 m and says so.

## Reading the check

Check runs two checks:

- **structure** (`tools\hpdcheck.py`): the rules a navigation file must keep. It prints "every check passes" or
  the rules that fail, with an example.
- **routes** (`tools\routecheck1943.py`): every soldier spawn of each base and flag is joined to the route graph
  (the nearest node within 15 m across and 4 m in height), and a route is looked for between every pair of flags.

For Wake Island's build of 2026-10-24 it reports 12,652 nodes, 44,980 edges and 69 islands; the main network has
11,371 nodes; 16 of 16 ladders are linked from the main network; all 10 flag pairs are routed; the structure check
passes. (The ladder count does not include the jump links to the carriers' AA guns, which are in
`jump_links.json`.)

A spawn that is not on the main network is listed with the size of its island. A base on a carrier is its own
network (the sea divides it); that is expected and listed apart. The check fails when a flag's spawn is off the
main network or a flag pair has no route.

The full report is in `check.txt`, and the same in `check.json`.

## Auditing a build

The check covers flags, spawns and routes. For a fuller test of a build against its level, run the audit from the
variant's folder (the masks take about 30 minutes per level, once per build; an audit then about a minute):

```
python tools\navaudit1943.py masks wake_island_s     the build's walkable surfaces, and once per rule turned off
python tools\navaudit1943.py audit wake_island_s     the checks: work\levels\wake_island_s\audit\audit.md
```

`masks --variants base` makes only the build's own mask (about 6 minutes on Wake Island); the audit then leaves out
where each rule fired. The audit lists every capture area, spawn, vehicle spawn, mounted gun and repair station that
is not on walkable ground of its own floor - a vehicle: no walkable ground within its own entry radius of one of its
entry points, from the vehicle's data (with the sample columns there and why each surface is or is not walkable),
routes from every infantry spawn to every capture area and back, every island and what kind it is (a
carrier deck is a vehicle-only zone, not a fault), the file's consistency and the places the route graph does not
cross, and more. Run the masks again after every build: they belong to one build.

## Exporting for the navigation runtime

```
python tools\navexport1943.py wake_island_s --check     work\levels\wake_island_s\navkit\wake_island_s.rsnv
python tools\navexport1943.py wake_island_s --big-endian   wake_island_s.be.rsnv, for experiments in Xenia
```

`--check` reads the file back and compares it with the build. Export again after every build (under a minute on Wake Island).

## Where the files go

All of these are in the suite's work folder (`<suite>\work`; the environment variable `BF1943_WORK` sets another):

| File | What it is |
|---|---|
| `work\levels\<level>\objects\` | the level's objects and collision (`levelobjects1943.py`'s files), `heightfield.res`, `water_mesh.res` |
| `work\levels\<level>\samples\` | the collision samples (`samples*.bin`) and their logs |
| `work\levels\<level>\navkit\navkit.json` | the settings Prepare chose (sea level, sector offsets, detailed sectors) and the steps done |
| `work\levels\<level>\navkit\<level>.hpd` | **the result** |
| `work\levels\<level>\navkit\build.log` | the build's log |
| `work\levels\<level>\navkit\check.txt`, `check.json` | the check report |
| `work\levels\<level>\navkit\jump_links.json` | the jump links Build made (ships' AA gun platforms) |
| `work\levels\<level>\audit\` | the audit's masks (`masks\`) and report (`audit.json`, `audit.md`) |

NavKit never changes your game folder.

## Troubleshooting

| Problem | What to do |
|---|---|
| "... is not a BF1943 game folder" / no game folder found | Run `bf1943-xenia-kit` first, or point `BF1943_GAME` or `--game` at a `dist\xenon` folder with a `levels\` folder. |
| "...: not a level of ..." | Use a name `navkit.py levels` lists, such as `wake_island_s`. |
| "not prepared yet" / "not sampled yet" / "not built yet" | Run the step before it. |
| A step failed in the window | The window shows its last lines; the whole output is in the log above it. |

## How NavKit stays clean

NavKit is written from a published specification of the file format. It contains no code from the original
licensed generator and never runs it. All game data comes from your own copy. See
[README.md](README.md#rules-clean-room-approach).
