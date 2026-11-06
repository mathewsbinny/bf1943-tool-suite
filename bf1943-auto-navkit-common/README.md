# NavKit, the modules both variants use (bf1943-auto-navkit-common)

Part of the BF1943 Tool Suite. **BF1943 status: ready** (2026-10-31) - the BF1943 pipeline
(`bf1943-auto-navkit-big-endian`) builds with these modules and BF1943's settings; the BFBC2 pipeline
(`bf1943-auto-navkit-little-endian`) with the same modules and NavKit's defaults.

Until 2026-10-31 each NavKit variant had its own copy of these modules: 14 module names were in both, 10 of the files
the same but for their bootstrap line, the rest forks (the builder, the walkability rules, the `.hpd` reader and the
mesh model, the Havok 4.6.1 reader, the level objects, the samples, the detailed-sector rule), and which copy a
script got depended on the order it named the variants in (`core/suitepath.py`). Now there is one copy of each, in
`tools\`, a shared folder (`suitepath.SHARED`, pyproject's `dev-mode-dirs`): every script and library finds them
without naming a variant, and no module name is in two of the suite's folders (`tests\test_repo.py` checks it).

How NavKit is built, these modules among the rest: the big-endian kit's
[ARCHITECTURE.md](../bf1943-auto-navkit-big-endian/ARCHITECTURE.md).

## What was merged, and how both kits get what they had

Each fork was merged into one module that serves both kits through explicit settings. Checked 2026-10-31: the four
BF1943 maps built as `navkit.py build` builds them and exported to RSNV (`.hpd`, `.rsnv` and the `.cells.npz` arrays),
and two BFBC2 levels built as `navregress.py` builds them (sp_009, ONS_MP_004), come out byte for byte as before the
merge.

| Module | Merged from | How the two kits keep their behaviour |
|---|---|---|
| `navbuild` (a package) | the big-endian copy (BF1943's rules, the `Settings`), with the little-endian copy's later fixes | `build(settings=...)`: `navbuild.BFBC2` (NavKit's defaults, the little-endian kit's builds) or `soldier1943.SETTINGS` (BF1943). Two rules that were not settings are now: `seam_gap_nodes` (always on in the big-endian copy, never in the little-endian one) and `column_sectors_cm` (a sample column's sector from whole centimetres, the little-endian fix; BF1943's builds kept the float rule until 2026-11, and take the fix since, after a rebuild and an audit). `normal_links_own_grid` (2026-11, on for BF1943): after the merge, links between two 0.8 m sectors by the 0.8 m step rule; NavKit's default keeps the 0.2 m rule every build had until BFBC2's builds are measured with it (2026-11-06: not yet measured; every BFBC2 level's `objects.json` in the OPFOR folder predates `NeedsDetailedPathfinding` and needs `navkit_le.py prepare` first; little-endian README). The little-endian copy's checks (a sampling run on another grid, no walkable ground, a file that does not read back: `BuildError`), its atomic file write, its detailed pass made again without sectors that have no 0.2 m ground, and its guard against a ladder linked to itself are kept for both; none of them fires on a BF1943 build |
| `navwalk.py` | the big-endian copy (the soldier's headroom band and lip width as arguments, jump-link bands, `ladder_end_options`) | the little-endian copy's `coarse(f, pitch)`, its roof rule's lowest surface over non-empty columns only (it differs only for the last non-empty column when empty ones follow it), and its `score` guard |
| `hpdtool.py`, `kymesh.py` | the little-endian copies | their damaged-file checks (a truncated file, overlapping blocks, a mesh without its trailer, a portal to no area, an area in no tile) |
| `havok461.py` | the little-endian copy | a last triangle stored without its padding word is kept; every one of the 3,721 meshes in your BF1943 copy's 445 Havok files has a whole number of padded triangles, so no BF1943 output changes |
| `levelobjects.py` | the little-endian copy (Havok 4.6.1 collision, `NeedsDetailedPathfinding`) | the big-endian copy's JSON files written through `with` blocks |
| `detailed.py` | the little-endian copy (the flag DICE's zones follow, the sampled-run record, the offsets from `navkit.json`) | the big-endian copy's `slope_max`; `stacked_columns(cm_sectors=...)`, the sector rule as in `navbuild` (`detailed1943.py` passes BF1943's) |
| `navsample.py` | the little-endian copy (`column_sectors`, a run stopped inside a column) | |
| `hkpack.py`, `hpdcheck.py`, `hpdview.py`, `navedits.py` | either: the same | |
| `navdiff.py` | the little-endian kit's only copy | moved here so core's `navdata.py` needs no NavKit variant |

The OPFOR copies' provenance (`provenance/opfor-imports.json`) records the move: `tools\import_opfor.py --update`
leaves these files out of the little-endian kit and reports an OPFOR change to one as a conflict to merge by hand
here.

## The builder package (`tools\navbuild\`)

`navbuild.py` (2,315 lines) split along its stages; `import navbuild` and every name it had (`navbuild.build`,
`navbuild.Settings`, `navbuild.BFBC2`, `navbuild.stage`, `navbuild.read_cells`, the constants, ...) work as before.

| Module | What it holds |
|---|---|
| `__init__.py` | `build()`: the normal stage, the detailed stage and the merge, the file; the package's names re-exported |
| `config.py` | the documented constants (defaults, never changed at run time), `Settings`, `BFBC2`, `BuildError`, `Grid` / `grid_at`, `BuildState`, `JoinScope` |
| `inputs.py` | input assembly: the sampling runs on the stage's grid (`to_pitch`, `same_grid`), the water and extent rules, a column's sector, the walkable surfaces (`Level`), and `stage_level`, which puts them together for a stage |
| `areas.py` | a stage's sectors, tiles and areas, their traced boundaries (portals, border portals, outlines with 45-degree bridges) and the links between sectors |
| `graph.py` | a sector's route graph (nodes, walk edges, joined groups, order and ladder nodes) and its final tables |
| `stages.py` | `stage`: one grid's build, not yet written |
| `seams.py` | links across mixed-pitch seams after the merge: gap nodes, fine and far links |
| `merge.py` | `merge_detailed` (the 0.2 m stage over the detailed sectors, merged with the normal build) and `merge_coincident_border` |
| `write.py` | the `.hpd` file and its `<name>.cells.npz` |
| `__main__.py` | the command line: `python -B -m navbuild <samples.bin> <forbid_tris.npy> <XOffset> <ZOffset> <out.hpd> [...]` (in `tools\`) |

A test that switches a rule sets it in the module that reads it (`navbuild.areas.ONE_WAY_PORTALS`), and one that
replaces a step replaces it where it is called (`navbuild.write_file` for `build`, `navbuild.merge.stage` for the
detailed pass).

## Layout

```
research\KYNAPSE_SPEC.md   the path-data format and the builder's stages (one copy; was in both variants)
research\img\              its pictures
tools\                     the shared modules (on sys.path for every script: suitepath.SHARED)
  navbuild\                the generator: samples + level objects -> a navigation file (pathdata.hpd layout)
  navwalk.py               walkability rules
  navsample.py             the samples file format: read, and compare with a path-data file
  navedits.py              walkability edits as generator inputs
  hpdtool.py               the navigation file: info / sector / roundtrip / coverage / meshtrip
  kymesh.py                per-sector mesh: parse_mesh / write_mesh (byte-exact)
  hpdcheck.py              structure checks (closed boundaries, edges, node order, zero-length edges)
  hpdview.py               render one sector to PNG
  navdiff.py               two navigation files compared layer by layer (core's navdata reads cells with it)
  hkpack.py                Havok binary packfile reader: 5.5 (BFBC2) and 4.6.1 (BF1943), byte order from the file
  havok461.py              class layouts for Havok 4.6.1 packfiles, which have no __types__ section
  levelobjects.py          BFBC2's level objects and the pathfinding-flag rules; the collision index
  detailed.py              the detailed-sector rule (BF1943: the big-endian kit's detailed1943.py applies it)
tests\                     the rule tests (made-up data) of these modules, and the Havok 4.6.1 test (your copy)
```

## Tests

`python -B ..\run_tests.py quick` runs the made-up-data tests here ("NavKit (both variants): ..."): the water,
area-link, portal, headroom, lip, roof, dynamic-prop and character rules, the 45-degree bridges, walkability edits,
the collision index, the builder's edge cases under NavKit's defaults (`test_navbuild_edges.py`) and the readers on
damaged files (`test_formats.py`). `bf1943` adds `test_havok461.py` (Wake Island's Havok files, and the collision
reader on them). The BF1943 settings are tested in the big-endian kit (`test_mixed_seam.py`, the steps and the
export).
