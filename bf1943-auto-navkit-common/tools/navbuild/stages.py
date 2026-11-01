"""NavKit's builder (navbuild), one grid's build (stage): from its input assembly (inputs.stage_level) to its
sectors, areas, shared border nodes, traced boundaries, meshes and route graphs - not yet written. A build is the
normal stage (pitch 0.8) and, with detailed sectors, a second one at pitch 0.2 (merge.merge_detailed)."""
import math
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, cast

import numpy as np

import kymesh

from .config import (AREA_Y_MAX, AREA_Y_MIN, BFBC2, BORDER_NODE_SPACING, CARRY_RADIUS, DEDUP_GRAPH_NODES,
    EDGE_SECTOR_RULE, EDGE_SECTOR_TILES, MAX_WATER_DEPTH, ONE_WAY_PORTALS, PITCH, RADIUS, BuildError, BuildState,
    JoinScope, grid_at)
from .inputs import column_sectors, stage_level
from .areas import (build_areas, drop_one_way, restore_one_way, same_grid_resolver, sector_links, sector_origin_index,
    sector_window, tiling, trace_edges, trace_emit)
from .graph import build_graph

if TYPE_CHECKING:
    from .config import Grid, Ladder, Log, Points, SectorKey, Settings, StageResult, Vec3, Water


def stage(samples_path: str, high_path: str | None, xoff: int, zoff: int, tris_path: str | None = None,
          step: float = 0.28, log: 'Log' = print, dynamic_path: str | None = None, narrow_path: str | None = None,
          stairs_path: str | None = None, exclude_path: str | None = None, exclude_foot_path: str | None = None,
          seeds: 'Points | None' = None, edits: Sequence[dict[str, Any]] | None = None, water: 'Water | None' = None,
          max_water_depth: float | None = MAX_WATER_DEPTH, bounds: Sequence[float] | None = None,
          order_points: 'Points | None' = None, t0: float | None = None,
          sector_filter: 'Iterable[SectorKey] | None' = None, grid_min: tuple[int, int] | None = None,
          id_start: int = 1, carried: 'dict[SectorKey, list[tuple[int, Vec3]]] | None' = None,
          seed_points: 'Points | None' = None, ladders: 'list[Ladder] | None' = None, grid: 'Grid | None' = None,
          settings: 'Settings' = BFBC2, state: BuildState | None = None) -> 'StageResult':
    """One grid's build: walkable surfaces, sectors, areas, border nodes, traced boundaries, meshes and route graphs -
    not yet written. grid: the stage's Grid (grid_at; the normal grid by default); settings: the build's Settings;
    state: the build's BuildState, which the stage adds to (a new one by default). sector_filter: build only these
    (X, Y) sectors;
    grid_min: the level's minimum sector X / Y (EDGE_SECTOR_RULE) when the stage holds only part of it; id_start:
    the first node id; carried: {sector: [(id, pos)]} shared nodes from another stage on this stage's sectors (the
    seams with sectors built at another pitch) - attached to this grid's ground where it has some; seed_points:
    more walkable-seed points (x, ground y, z) - not soldier spawns (those are seeds).
    Returns {'level', 'info', 'graphs', 'border_nodes', 'next_id', 'dropped', 'pitch', 'meshes'}."""
    t0 = t0 or time.time()
    grid = grid if grid is not None else grid_at(PITCH, settings)
    state = state if state is not None else BuildState()
    pitch = grid.pitch
    level, ladder_pairs = stage_level(samples_path, high_path, grid, settings, tris_path=tris_path, step=step, log=log,
                                      dynamic_path=dynamic_path, narrow_path=narrow_path, stairs_path=stairs_path,
                                      exclude_path=exclude_path, exclude_foot_path=exclude_foot_path, seeds=seeds,
                                      edits=edits, water=water, max_water_depth=max_water_depth, bounds=bounds,
                                      order_points=order_points, seed_points=seed_points, ladders=ladders, t0=t0)
    s, keep = level.s, level.keep

    # Sectors that contain kept surfaces.
    sectors = set(zip(*column_sectors(s, level.col_of[np.nonzero(keep)[0]], xoff, zoff, settings, pitch)))
    if sector_filter is None:
        state.normal_sectors = set(sectors)
    else:
        sectors &= set(sector_filter)
    if not sectors and sector_filter is None:
        raise BuildError(f'no walkable ground kept ({len(s.y)} surfaces sampled, {int(keep.sum())} kept): check the '
                         f'sampling box and sector offsets, and the water and reference-extent rules')
    sectors = sorted(sectors)
    log(f'{len(sectors)} sectors (pitch {pitch:g})')

    # Pass 1: areas per sector, and which area each surface belongs to.
    info = {}
    have = set(sectors)
    min_x_sector, min_z_sector = grid_min or (min(X for X, _ in sectors), min(Y for _, Y in sectors))
    for X, Y in sectors:
        gi0, gj0, ox, oz = sector_origin_index(level, X, Y, xoff, zoff)
        if EDGE_SECTOR_RULE == 'grid_min':
            edge = (X == min_x_sector, Y == min_z_sector)
        else:
            edge = ((X - 1, Y) not in have, (X, Y - 1) not in have)
        start = tuple(0 if EDGE_SECTOR_TILES and at_edge else -1 for at_edge in edge)
        win = sector_window(X, Y, level, state)
        areas = build_areas(level, gi0, gj0, start, *tiling(((X & 0xFF) << 8) | (Y & 0xFF), grid), win=win)
        # order: tiles column-major, then layer = index within tile
        areas.sort(key=lambda area: (area['tile'][0], area['tile'][1], min(level.y[c[2]] for c in area['cells'])))
        per_tile = defaultdict(int)
        for area in areas:
            area['layer'] = per_tile[area['tile']]
            per_tile[area['tile']] += 1
        area_of = {c[2]: ai for ai, area in enumerate(areas) for c in area['cells']}
        info[(X, Y)] = {'areas': areas, 'area_of': area_of, 'origin': (gi0, gj0, ox, oz), 'start': start, 'win': win}
    log(f'{sum(len(sector_info["areas"]) for sector_info in info.values())} areas ({time.time() - t0:.0f} s)')

    # Graph: border nodes shared between sector pairs (global ids), then inner nodes.
    next_id = [id_start]
    border_nodes = defaultdict(list)     # sector -> [(id, pos, k)]
    border_at = {}                       # surface -> (id, pos) of the border node on it

    def new_id() -> int:
        next_id[0] += 1
        return next_id[0]

    n_side, yoff = grid.sector_samples, settings.y_offset
    for (X, Y), sector_info in info.items():
        gi0, gj0, ox, oz = sector_info['origin']
        for (dX, dY), line in (((1, 0), n_side + 1), ((0, 1), n_side + 1)):
            other = (X + dX, Y + dY)
            if other not in info:
                continue
            def line_runs(line: int) -> list[list[tuple[int, int]]]:
                """every kept surface on the line (all layers: floors under roofs too), grouped into connected runs
                along it, one run per layer"""
                runs, active = [], []
                for t in grid.window:
                    i, j = (line, t) if dX else (t, line)
                    still_active = []
                    for k in level.surfaces(gi0 + i, gj0 + j):
                        for line_run in active:
                            if line_run[-1][0] == t - 1 and level.connected(line_run[-1][1], k, 0 if dX else 1,
                                                                            1 if dX else 0):
                                line_run.append((t, k))
                                still_active.append(line_run)
                                active.remove(line_run)
                                break
                        else:
                            line_run = [(t, k)]
                            runs.append(line_run)
                            still_active.append(line_run)
                    active = still_active
                return runs
            line_sets = [(line, line_runs(line))]
            if settings.border_seam_line:
                # the seam itself (local sample n_side, both windows hold it): its runs of ground that do not go on
                # to the border line - a piece that reaches the seam but stops short of the line one sample beyond
                # got no shared node and was not joined across (Coral Sea's three seam breaks, 2026-10-26)
                on_line = {k for line_run in line_sets[0][1] for _t, k in line_run}
                seam = [line_run for line_run in line_runs(n_side)
                        if not any(level.neighbour(k, gi0 + (n_side if dX else t), gj0 + (t if dX else n_side),
                                                   dX, dY) in on_line for t, k in line_run)]
                line_sets.append((n_side, seam))
            for line, runs in line_sets:
                for run in runs:
                    n_nodes = max(1, int(math.ceil(len(run) * pitch / BORDER_NODE_SPACING)))
                    for node_index in range(n_nodes):
                        t, k = run[int((node_index + 0.5) * len(run) / n_nodes)]
                        i, j = (line, t) if dX else (t, line)
                        pos = (ox + i * pitch, float(level.y[k]) + yoff, oz + j * pitch)
                        if DEDUP_GRAPH_NODES and k in border_at:
                            # a surface already holding a border node (a sector corner: on both lines): the same
                            # node, shared with this neighbour too - DICE has border ids in 3 and 4 sectors; two ids
                            # on one spot were joined by zero-length edges (11 of 13 campaign levels, 2026-10-20)
                            nid, pos = border_at[k]
                            for sec in ((X, Y), other):
                                if not any(node[0] == nid for node in border_nodes[sec]):
                                    border_nodes[sec].append((nid, pos, k))
                            continue
                        nid = 0x80000000 | new_id()
                        border_at[k] = (nid, pos)
                        border_nodes[(X, Y)].append((nid, pos, k))
                        border_nodes[other].append((nid, pos, k))
    # shared nodes from another stage (seams with sectors at another pitch): on this grid's ground at the node, the
    # nearest kept surface of the sector within 0.6 m of its height; nodes with no such ground are reported dropped
    dropped = set()
    for sec, nodes in (carried or {}).items():
        if sec not in info:
            dropped.update(nid for nid, _pos in nodes)
            continue
        gi0, gj0, ox, oz = info[sec]['origin']
        own = info[sec]['area_of']
        for nid, pos in nodes:
            # within CARRY_RADIUS of the node, on its floor; ground of a larger area first (not a stray speck), then
            # the nearest - a 3 x 3 search (0.2 m at pitch 0.2) left 74 ONS_MP_002 seam nodes without ground and put
            # others on specks, and the detailed core lost its links to the normal sectors (connectivity 91.6 -> 60.9)
            gi, gj = int(round((pos[0] - s.min_x) / pitch)), int(round((pos[2] - s.min_z) / pitch))
            reach = int(math.ceil(CARRY_RADIUS / pitch))
            candidates = []
            for di in range(-reach, reach + 1):
                for dj in range(-reach, reach + 1):
                    flat_dist = math.hypot(di, dj) * pitch
                    if flat_dist > CARRY_RADIUS:
                        continue
                    for k in level.surfaces(gi + di, gj + dj):
                        if k in own and abs(level.y[k] + yoff - pos[1]) <= 0.6:
                            size = len(info[sec]['areas'][own[k]]['cells'])
                            candidates.append((size < 25, flat_dist, k))
            if not candidates:
                dropped.add(nid)
                continue
            k = min(candidates)[2]
            border_nodes[sec].append((nid, pos, k))

    # boundary edges of every sector, then the one-way portal pieces decided level-wide
    edge_sets = {}
    for (X, Y), sector_info in sorted(info.items()):
        gi0, gj0, ox, oz = sector_info['origin']
        edge_sets[(X, Y)] = trace_edges(level, sector_info['areas'], sector_info['area_of'], gi0, gj0,
                                        sector_info['win'])
    n_cand = sum(len(edge_set[1]) for edge_set in edge_sets.values())
    if ONE_WAY_PORTALS == 'restore':
        owners = defaultdict(list)
        for sec, sector_info in info.items():
            for k, ai in sector_info['area_of'].items():
                owners[k].append((sec, ai))
        shared = [(owned_by[0], also_owned_by)
                  for owned_by in owners.values() if len(owned_by) > 1 for also_owned_by in owned_by[1:]]
        keep = restore_one_way(edge_sets, shared)
    else:
        keep = {sec: set(range(len(edge_set[1]))) if ONE_WAY_PORTALS == 'keep' else set()
                for sec, edge_set in edge_sets.items()}
    for sec, (per_area, one_way) in edge_sets.items():
        drop_one_way(per_area, one_way, keep.get(sec, set()))
    log(f'one-way portal pieces: {n_cand}, kept one-way {sum(len(kept) for kept in keep.values())} ({ONE_WAY_PORTALS})')
    graphs, meshes = [], {}
    for (X, Y), sector_info in sorted(info.items()):
        gi0, gj0, ox, oz = sector_info['origin']
        areas, area_of = sector_info['areas'], sector_info['area_of']
        traced = trace_emit(level, areas, edge_sets[(X, Y)][0], gi0, gj0, ox, oz)
        sid = ((X & 0xFF) << 8) | (Y & 0xFF)
        mesh_areas = []
        ntiles = tiling(sid, grid)[1]
        tiles = [[] for _ in range(ntiles * ntiles)]
        for ai, (area, (portals, borders, outline)) in enumerate(zip(areas, traced)):
            ys = cast('list[float]', [level.y[c[2]] for c in area['cells']])   # numpy float64 scalars
            # DICE's area height range: from its lowest ground to its highest ground + 1.52 m (all normal-pitch
            # areas of sp_004_b: median +0.000 / +1.520; NavKit had -0.95 / +1.55, a metre of extra room below
            # each area, so a lower floor's range reached the deck above it - 2026-10-19)
            lo, hi = min(ys) + AREA_Y_MIN, max(ys) + AREA_Y_MAX
            mesh_areas.append(kymesh.Area(sid, area['tile'][0], area['tile'][1], area['layer'], RADIUS, pitch, hi, lo,
                                          portals, [border[0] for border in borders], [], outline, []))
            area['borders'] = borders
            tiles[area['tile'][0] * ntiles + area['tile'][1]].append(kymesh.TileItem(ai, lo, hi))
        mesh_links = sector_links(X, Y, areas, mesh_areas, same_grid_resolver(level, info, X, Y))
        # the runtime's tile grid starts at the rectangle's minimum (DICE: tile lines 39.6 m past it on 3,748 sector
        # axes) - so an edge sector, whose tiles start at its own edge, starts its rectangle there too (DICE 0x101:
        # -800.0 instead of -800.8). The maximum side keeps the 1.1-pitch margin (DICE drops it at the grid's maximum).
        start_i, start_j = sector_info['start']
        rect = (ox + start_i * pitch, ox + 100 + 1.1 * pitch, oz + start_j * pitch, oz + 100 + 1.1 * pitch)
        mesh = kymesh.Mesh(2, 0, 0, sid, grid.resolution, pitch, rect, ntiles, ntiles, RADIUS, tiles, mesh_areas,
                           mesh_links, b'\0' * 4)
        meshes[(X, Y)] = mesh
        orders = [order for order in (order_points or ()) if ox <= order[0] < ox + 100 and oz <= order[2] < oz + 100]
        sector_ladders = [(foot, top) for foot, top in ladder_pairs if foot in area_of and top in area_of]
        ladder_stats = state.ladder_stats
        ladder_stats['linked'] = ladder_stats.get('linked', 0) + len(sector_ladders)
        split = sum(1 for foot, top in ladder_pairs if (foot in area_of) != (top in area_of))
        ladder_stats['split'] = ladder_stats.get('split', 0) + split
        join = JoinScope((X, Y), set(info), sector_info['win'], state.detailed_sectors)
        pts, pairs = build_graph(level, areas, area_of, traced, border_nodes[(X, Y)], gi0, gj0, ox, oz, new_id, orders,
                                 sector_ladders, join, state)
        ys = [mesh_area.min_y for mesh_area in mesh_areas] + [mesh_area.max_y for mesh_area in mesh_areas]
        sector_bounds = (rect[0], rect[1], min(ys), max(ys), rect[2], rect[3])
        graphs.append((sid, sector_bounds, mesh, pts, pairs, (ox, oz)))
        # the areas' own cells for the sidecar file (write_file): global cell indices at this pitch, ground heights
        base_i, base_j = int(round(ox / pitch)), int(round(oz / pitch))
        state.area_cells[sid] = [(np.array([base_i + c[0] for c in area['cells']], np.int32),
                                  np.array([base_j + c[1] for c in area['cells']], np.int32),
                                  np.array([level.y[c[2]] for c in area['cells']], np.float32), pitch)
                                 for area in areas]
    return {'level': level, 'info': info, 'graphs': graphs, 'border_nodes': border_nodes, 'next_id': next_id[0],
            'dropped': dropped, 'pitch': pitch, 'meshes': meshes, 'sectors': set(info)}
