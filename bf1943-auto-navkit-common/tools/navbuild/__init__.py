"""NavKit generator: collision samples -> Kynapse-format pathdata.hpd (infantry), for both NavKit variants (one copy
since 2026-10-31, in bf1943-auto-navkit-common). For BF1943 the samples are computed offline from the level's data
(navsample1943.py) and the big-endian navkit.py builds the normal sectors at 0.8 m and the detailed ones at 0.2 m
(build(..., detailed=...), merge_detailed) with soldier1943.SETTINGS; for BFBC2 (the little-endian navkit_le.py,
navregress.py) they came from OPFOR's in-game sampler, and the build takes NavKit's defaults (BFBC2).

Pipeline (research/KYNAPSE_SPEC.md sections 3 and 4):
  1. walkable surfaces: navwalk.walkable (slope, capsule, ForbidPathfinding objects, StepMax, perimeter seeds)
  2. sectors: 100 m grid from the level's SectorOffsets; each sector uses the samples -1 .. 126 of its
     window (two-sample overlap with each neighbour)
  3. tiles: 3 x 3 per sector, 50 samples each (edges half a sample outside the samples: -1.2, 38.8,
     78.8, 101.2 m); areas = connected regions of a tile (8-connected, AREA_LINK rule); the old bisection at
     the middle of their extent (over 25 samples) is off - DICE has no such cut (BISECT_AREAS)
  4. area boundary, traced on the half-sample lines and merged into straight runs:
       portal (B)   - to a connected surface in another area of the same sector
       border (C)   - to a connected surface outside the sector window (direction = outward axis)
       outline (E)  - everything else; where two cells of an area touch only diagonally, the two outline
                      corners between them are replaced by 45-degree segments (a bridge through the
                      half cells, as in DICE's outlines)
  5. neighbour links: per neighbouring sector, link objects {tile x, tile z, area index in tile} with the
     C records leading into that area
  6. graph: one node per area (sample nearest the area centre) and per portal run; border nodes on the
     overlap lines, shared by both sectors (same id and position); edges between nodes up to DistEdgeMax
     apart whose straight line stays on connected walkable samples; cost = 0x80000000 | floor(d * 16)
  7. write with kymesh.write_mesh / hpdtool.write, and beside the build <name>.cells.npz (write_cells): every area's
     own cells and ground heights and the build's SHA-256 - the RSNV exporter's cells and heights

Detail features D and F are not generated (counts 0, allowed by the format). Detailed zones are built at pitch 0.2 in a
second pass from their own sampling run and merged with the normal build. BF1943's settings (soldier1943.SETTINGS,
passed as build(settings=...)) turn on AREA_CLIFF, BORDER_SEAM_LINE, JOIN_MARGIN, SEAM_FINE_LINKS, MIXED_OVERLAP
(2026-10-26), SEAM_GAP_NODES (2026-10-24), SEAM_FAR_LINKS (2026-10-29) and NORMAL_LINKS_OWN_GRID (2026-11);
they took the float sector rule (COLUMN_SECTORS_CM off) until 2026-11 and NavKit's whole-centimetre one since.

Configuration (2026-10-31): the constants in config.py are documented defaults, never changed at run time. What a
build is made for comes in as a Settings (BFBC2 = NavKit's defaults); each stage's grid (pitch 0.8 or 0.2) is a Grid
(grid_at); both travel on the stage's Level, and what a build gathers as it goes (order nodes, ladder counts, the
areas' cells) is its BuildState. Before, soldier1943.configure() and set_pitch() rewrote this module's constants, and a
build was right only when they had run first.

The package (navbuild.py until 2026-10-31, split along its stages; every name it had is here as navbuild.<name>):
  config.py   the constants, Settings, Grid, BuildState, JoinScope
  inputs.py   input assembly: the sampling runs on the stage's grid, water and extent rules, the walkable surfaces
              (Level), stage_level (step 1)
  areas.py    a stage's sectors, tiles and areas, their traced boundaries and the links between sectors (steps 2-5)
  graph.py    a sector's route graph and its final tables (step 6)
  stages.py   stage: one grid's build, not yet written
  seams.py    links across mixed-pitch seams, after the two stages are merged
  merge.py    the detailed stage and the merge (merge_detailed), nodes at one position folded
  write.py    the .hpd file and its <name>.cells.npz (step 7)
  __init__.py build(): the normal stage, the detailed one and the merge, the file
  __main__.py the command line

Usage: python -B -m navbuild <samples.bin> <forbid_tris.npy> <XOffset> <ZOffset> <out.hpd> [--step 0.28 (BFBC2's
                   default; the big-endian navkit.py passes BF1943's soldier1943.STEP, 0.37)]
                   [--dynamic dynamic_tris.npy] [--high samples_high_capsule.bin] [--narrow samples_narrow.bin]
                   [--stairs stair_tris.npy] [--exclude exclude_tris.npy] [--exclude-foot exclude_footprint_tris.npy]
"""
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

# the package's modules, re-exported: navbuild.<name> is every name navbuild.py had (tests set
# navbuild.<module>.<CONSTANT> where they switch a rule, the module that reads it)
from .config import (  # noqa: F401
    AREA_CLIFF, AREA_CLIFF_REACH, AREA_LINK, AREA_Y_MAX, AREA_Y_MIN, BFBC2, BISECT_AREAS, BORDER_NODE_SPACING,
    BORDER_OWNER, BORDER_SEAM_LINE, BRIDGE_UNION, CARRY_RADIUS, CLIP_TO_REFERENCE, COLUMN_SECTORS_CM, DEDUP_GRAPH_NODES,
    DETAILED_LINK_STEP, DETAILED_PITCH, DETOUR_LINK_ALL, DETOUR_MAX, DETOUR_PASSES, DETOUR_REPAIR, DETOUR_TRIGGER,
    DIST_EDGE_MAX, EDGE_SECTOR_RULE, EDGE_SECTOR_TILES, GABRIEL_EDGES, HEADER, JOIN_MARGIN, LADDER_ALL_GROUND,
    LINK_RULE, LINK_STEP, MAX_AREA, MAX_WATER_DEPTH, MIXED_OVERLAP, NODE_COVER, NORMAL_LINKS_OWN_GRID, ONE_WAY_PORTALS,
    ORDER_MIN_ISLAND,
    ORDER_NODE_MIN, ORDER_NODES, PITCH, RADIUS, RESOLUTION, SEAM_FAR_LINKS, SEAM_FINE_LINKS, SEAM_GAP_NODES,
    SECTOR_SAMPLES, SLOPE_MAX, SORT_NODES_BY_ID, SYMMETRIC_PORTALS, TILE, TILE_20M_SECTORS, TILES, WINDOW, Y_OFFSET,
    BuildError, BuildState, Grid, JoinScope, Settings, grid_at)
from .inputs import (  # noqa: F401
    Level, column_sectors, deep_water, outside_bounds, same_grid, stage_level, to_pitch, water_tops)
from .areas import (  # noqa: F401
    _cliff, bisect, border_cell, bridge_union, build_areas, clip_bridges, components, cross_grid_resolver, drop_one_way,
    restore_one_way, same_grid_resolver, sector_links, sector_origin_index, sector_window, tiling, trace, trace_edges,
    trace_emit)
from .graph import (  # noqa: F401
    add_cover_nodes, add_order_nodes, build_graph, finalize_graph, gabriel_prune, join_groups, line_walkable,
    prune_order_nodes, repair_detours)
from .stages import stage  # noqa: F401
from .seams import (  # noqa: F401
    SEAM_FINE_BAND, SEAM_REACH, fine_columns, seam_far_links, seam_fine_links, seam_gap_nodes)
from .merge import (  # noqa: F401
    merge_coincident_border, merge_detailed)
from .write import (  # noqa: F401
    cells_path, read_cells, write_cells, write_file)

if TYPE_CHECKING:
    from .config import Ladder, Log, Points, Water


def build(samples_path: str, tris_path: str | None, xoff: int, zoff: int, out_path: str, step: float = 0.28,
          log: 'Log' = print, dynamic_path: str | None = None, high_path: str | None = None,
          narrow_path: str | None = None, stairs_path: str | None = None, exclude_path: str | None = None,
          exclude_foot_path: str | None = None, seeds: 'Points | None' = None,
          edits: Sequence[dict[str, Any]] | None = None, water: 'Water | None' = None,
          max_water_depth: float | None = MAX_WATER_DEPTH, bounds: Sequence[float] | None = None,
          order_points: 'Points | None' = None, detailed: dict[str, Any] | None = None,
          ladders: 'list[Ladder] | None' = None, settings: Settings = BFBC2) -> BuildState:
    """settings: what the build is made for (Settings: BFBC2, NavKit's defaults, or soldier1943.SETTINGS).
    edits: the operations of a navigation side file (navedits.py applies the walkability ones).
    water: the level's water mesh (vertices, triangles; core/bfbc2core/water.py): ground more than max_water_depth
    under it is not walkable, as in DICE's data (hand edits forcing ground walkable still win).
    bounds: (minX, minZ, maxX, maxZ) of the reference path data (CLIP_TO_REFERENCE), or None.
    detailed: {'samples': a pitch-0.2 sampling run, 'high': its high-capsule run (or None), 'sectors': {(X, Y)}} -
    the second pass: those sectors are built at pitch 0.2 (DICE's detailed zones) and merged with the normal build
    (merge_detailed). Without it the build is the normal one, unchanged.
    Returns the build's BuildState (its order nodes, ladder counts and the areas' cells)."""
    t0 = time.time()
    state = BuildState(detailed_sectors=set((detailed or {}).get('sectors') or ()))
    common: dict[str, Any] = dict(tris_path=tris_path, step=step, log=log, dynamic_path=dynamic_path,
                                  narrow_path=narrow_path, stairs_path=stairs_path, exclude_path=exclude_path,
                                  exclude_foot_path=exclude_foot_path, edits=edits, water=water,
                                  max_water_depth=max_water_depth, bounds=bounds, order_points=order_points, t0=t0,
                                  ladders=ladders, settings=settings, state=state)
    normal = stage(samples_path, high_path, xoff, zoff, seeds=seeds, grid=grid_at(PITCH, settings), **common)
    graphs = merge_detailed(normal, detailed, xoff, zoff, seeds, common, log) if detailed else normal['graphs']
    if ladders:
        log(f'ladders: {len(ladders)}; links made {state.ladder_stats.get("linked", 0)} (counted per stage), ends in '
            f'two sectors {state.ladder_stats.get("split", 0)}')
    write_file(graphs, out_path, log, t0, state, settings)
    return state
