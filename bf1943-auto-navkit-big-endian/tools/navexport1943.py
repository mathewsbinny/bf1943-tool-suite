r"""NavKit (big-endian): a BF1943 navigation build exported to RSNV, the navigation runtime's per-level file
(core/bfbc2core/rsnv.py; operation-rising-sun research/NAVIGATION_RUNTIME_DESIGN.md, sections 1.2-1.4: exporter v1).

  navexport1943.py <level> [--out <file.rsnv>] [--big-endian] [--check] [--compare]

Reads the checked build (<level>.hpd), its jump links (jump_links.json), the level's ladders (ladders.json), sea level
(navkit.json) and vehicle spawns (the level's own data), and writes work\levels\<level>\navkit\<level>.rsnv
(little-endian; --big-endian for an in-guest build: <level>.be.rsnv). A build whose check failed is exported for
inspection to <level>.failed.rsnv (unless --out says where), never over the soldier world, and the exit code is 1.
--check reads it back and validates it (rsnv.World.validate) and compares its graph with the build's (navdata).

What it holds:
  - the route graph: nodes (ground heights), edges with integer costs (1/16 m, as stored) and kinds - walk, ladder
    (up / down: a NavKit ladder link, navdata.ladder_edge) and jump (the edges of jumplinks1943's links);
  - areas with bitmaps of their walkable cells at their own pitch (0.2 m in detailed sectors, 0.8 m elsewhere): the
    build's own cells and ground heights (navbuild's <level>.cells.npz, exporter 1.3); for a build without that file,
    rasterised from their outlines, portals and border portals (even-odd, as navdiff.area_cells, at the area's pitch)
    with heights estimated from the nearest outline point;
    a height grid per area (1 m blocks at 0.2 m, finer where they misread the area: HEIGHT_TOL;
    one block per cell at 0.8 m); per sector, 25 x 25 buckets of 4 m listing the areas over each;
  - portals between areas (both directions; border portals resolved to the neighbour sector's area across them);
  - ladders and jump links (LADR), vehicle entry points (OFFM: board a vehicle, or mount a gun, within EntryRadius of an
    entry point; core vehicles1943.py), islands (ISLD: the main network; carriers by the water under them);
  - provenance in META: the source files' hashes, NavKit's soldier, the exporter version.
"""
import argparse
import collections
import hashlib
import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import numpy.typing as npt

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import navbuild  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import soldier1943  # noqa: E402
from bfbc2core import game1943, navdata, rsnv, terrain, vehicles1943  # noqa: E402

if TYPE_CHECKING:
    from bfbc2core.navdata import NavArea, NavData
    from bfbc2core.terrain import Heightfield

type IntArray = npt.NDArray[np.int64]
type FloatArray = npt.NDArray[np.float64]
type Log = Callable[[str], object]
type BuildCells = list[tuple[int, float, npt.NDArray[np.int32], npt.NDArray[np.int32], npt.NDArray[np.float32]]]

EXPORTER_VERSION = ('1.4 (2026-10-26: admissible heuristic scale in the header; finer height grids where needed; '
                    'cells and heights from the cells of the build (<level>.cells.npz) where present; '
                    'height grid of 1 m blocks at 0.2 m pitch, per cell at 0.8 m; tight walk edges flagged)')
HEIGHT_CELLS = {0.2: 5, 0.4: 2, 0.8: 1}   # cells per height-grid block, by pitch
COARSE_PITCH = 0.8        # m: the bitmap pitch of the build's 0.8 m areas (0.4 was tried 2026-10-25: no better)
END_MATCH = 1.0           # m: a ladder's / jump link's end and its route node lie at most this far apart
AREA_GROUND_TOP = 1.52    # m: an area's stored max_y is its highest ground plus this (navbuild.AREA_Y_MAX)


sha256 = nj.sha256                 # (navkitjson1943: one chunked SHA-256 for every tool)


def rasterise(area: 'NavArea', pitch: float, y_offset: float,
              heightfield: 'Heightfield') -> tuple[IntArray, IntArray, FloatArray]:
    """The area's walkable cells at its pitch: (ii, jj) sample indices (centre = index x pitch) and ground heights.
    Samples inside the boundary (even-odd on xz), the height from the nearest boundary point less y_offset, or the
    terrain's where that point lies on the terrain (navdata._ground's rule)."""
    segments = [(np.asarray(line[k], float), np.asarray(line[k + 1], float))
                for line in area.outline for k in range(len(line) - 1)]
    segments += [(np.asarray(p0, float), np.asarray(p1, float)) for p0, p1, *_ in area.portals]
    segments += [(np.asarray(p0, float), np.asarray(p1, float)) for p0, p1 in area.borders]
    if not segments:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    seg_start = np.array([segment[0] for segment in segments])
    seg_end = np.array([segment[1] for segment in segments])
    xs, zs = np.concatenate([seg_start[:, 0], seg_end[:, 0]]), np.concatenate([seg_start[:, 2], seg_end[:, 2]])
    i0, i1 = int(math.floor(xs.min() / pitch)) - 1, int(math.ceil(xs.max() / pitch)) + 1
    j0, j1 = int(math.floor(zs.min() / pitch)) - 1, int(math.ceil(zs.max() / pitch)) + 1
    ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
    ii, jj = ii.ravel(), jj.ravel()
    sample_x, sample_z = ii * pitch + pitch * 1.37e-3, jj * pitch + pitch * 0.71e-3    # off the 45-degree bridges' corners
    inside = np.zeros(len(ii), bool)
    for k in range(0, len(seg_start), 256):
        # even-odd: a sample is inside when a ray to +x crosses an odd number of segments (a: start, b: end)
        ax, az, bx, bz = (seg_start[k:k + 256, 0], seg_start[k:k + 256, 2],
                          seg_end[k:k + 256, 0], seg_end[k:k + 256, 2])
        spans_z = (az[None, :] > sample_z[:, None]) != (bz[None, :] > sample_z[:, None])
        with np.errstate(divide='ignore', invalid='ignore'):
            x_cross = ax[None, :] + (sample_z[:, None] - az[None, :]) * (bx - ax)[None, :] / (bz - az)[None, :]
        inside ^= np.count_nonzero(spans_z & (sample_x[:, None] < x_cross), axis=1) % 2 == 1
    ii, jj = ii[inside], jj[inside]
    if not len(ii):
        return ii, jj, np.zeros(0)
    points = np.concatenate([seg_start, seg_end])
    estimate, nearest_xz = np.empty(len(ii)), np.empty((len(ii), 2))
    for k in range(0, len(ii), 2048):
        chunk_x, chunk_z = ii[k:k + 2048] * pitch, jj[k:k + 2048] * pitch
        nearest = np.argmin((chunk_x[:, None] - points[None, :, 0]) ** 2 + (chunk_z[:, None] - points[None, :, 2]) ** 2,
                            axis=1)
        estimate[k:k + 2048] = points[nearest, 1] - y_offset
        nearest_xz[k:k + 2048] = points[nearest][:, [0, 2]]
    xz = cast('FloatArray', np.stack([ii * pitch, jj * pitch], 1))
    y = navdata._ground(xz, estimate, heightfield, nearest_xz)
    # in a large 0.8 m area on sloped terrain the nearest boundary point can be 20 m away and its height metres off
    # (navdata's 1.5 m rule then keeps the estimate: 457 of Wake's nodes fell outside their area). Where that point lies
    # on the terrain, a cell whose terrain height is inside the area's own ground range (min_y .. max_y - 1.52, DICE's
    # convention) takes the terrain's height
    terrain_y = heightfield.height(xz[:, 0], xz[:, 1])
    half = navdata.GRID / 2
    ring = np.stack([heightfield.height(nearest_xz[:, 0] + dx, nearest_xz[:, 1] + dz)
                     for dx, dz in ((0, 0), (half, 0), (-half, 0), (0, half), (0, -half))])
    near_on_terrain = (estimate >= ring.min(0) - navdata.ON_TERRAIN) & (estimate <= ring.max(0) + navdata.ON_TERRAIN)
    in_range = (terrain_y >= area.min_y - 0.2) & (terrain_y <= area.max_y - AREA_GROUND_TOP + 0.2)
    y = np.where(near_on_terrain & in_range, terrain_y, y)
    return ii, jj, y


HEIGHT_TOL = 0.25         # m: a 0.2 m area whose 1 m height grid is further off than this at one of its own cells gets a
                          # grid of one block per cell instead (2026-10-26: at steep area edges the blocks' means put the
                          # ground 0.3-0.7 m off and made walk edges there tight)


def admissible_costs(pos: npt.ArrayLike, src: npt.ArrayLike, dst: npt.ArrayLike, cost: npt.ArrayLike) -> IntArray:
    """The edges' costs for the runtime: the build's (floor(16 x length), DICE's rule), raised where that is under 16 x
    the length between the exported float32 node positions as the search measures it (x, y summed, then z) - to that,
    rounded up. Every path then costs at least 16 x its length, so the search's heuristic floor(16 x distance x 0.999)
    never overestimates (2026-10-26: with the build's costs, chains of short edges each rounded down could cost less
    than the heuristic, and the search returned a costlier path; the least cost per length over a map's edges was 0, so
    no scale on the heuristic alone could fix it)."""
    pos32 = np.asarray(pos, np.float32).astype(np.float64)
    delta = pos32[np.asarray(dst)] - pos32[np.asarray(src)]
    length = np.sqrt((delta[:, 0] * delta[:, 0] + delta[:, 1] * delta[:, 1]) + delta[:, 2] * delta[:, 2])
    return np.maximum(np.asarray(cost, np.int64), np.ceil(16.0 * length).astype(np.int64))


def height_grid(ii: IntArray, jj: IntArray, y: FloatArray, cells_per_block: int,
                min_y: float) -> tuple[IntArray, int, int]:
    """The area's height grid (blocks of cells_per_block x cells_per_block cells from its first cell, each its cells'
    mean, cm above min_y; 0xFFFF for a block with no cell): (grid rows x columns, width, height). hw, hh: the grid's
    width and height in blocks (rsnv.Area's names)."""
    hw = max(1, -(-(int(ii.max()) + 1 if len(ii) else 1) // cells_per_block))
    hh = max(1, -(-(int(jj.max()) + 1 if len(jj) else 1) // cells_per_block))
    grid = np.full((hh, hw), 0xFFFF, np.int64)
    if len(ii):
        block_i, block_j = ii // cells_per_block, jj // cells_per_block
        block = block_j * hw + block_i
        sums = np.bincount(block, weights=y, minlength=hw * hh)
        counts = np.bincount(block, minlength=hw * hh)
        filled = counts > 0
        flat = grid.reshape(-1)
        flat[filled] = np.minimum(np.round((sums[filled] / counts[filled] - min_y) * 100).astype(np.int64), 0xFFFE)
    return grid, hw, hh


def grid_error(ii: IntArray, jj: IntArray, y: FloatArray, grid: IntArray, hw: int, hh: int, cells_per_block: int,
               pitch: float, min_y: float) -> float:
    """The largest difference between the grid's ground (rsnv.World.ground, the runtime's reading) and the cells'
    own heights, at the cells."""
    area = rsnv.Area(0, 0.0, 0.0, 0.0, 0.0, min_y, min_y, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, pitch, 0, hw, hh,
                  int(round(cells_per_block * pitch * 1000)))
    stub: Any = type('G', (), {})()                 # the one World field ground() reads
    stub.hght = grid.reshape(-1)
    return max((abs(rsnv.World.ground(stub, area, float(i) * pitch, float(j) * pitch) - float(cell_y))
                for i, j, cell_y in zip(ii, jj, y)), default=0.0)


class NotCurrent(RuntimeError):
    """The build on disk is not the checked build of the level's current preparation."""


build_refusal = nj.build_refusal    # why the build on disk may not be exported (navkitjson1943: the audit's rule too)


def prepared_terrain(lv: str, st: Mapping[str, Any]) -> tuple[str, game1943.Game, str, 'Heightfield']:
    """(game folder, game1943.Game, height field name, terrain) of the copy the level was prepared from (navkit.json's
    'game'; 2026-10-30: the export and test_navexport1943 read the default copy's). The terrain is the one saved by
    the preparation (objects\\heightfield.res, checked against its stamp), and a game copy whose height field changed
    since is refused (navkitjson1943.prepared_heightfield; second review: the game's was reread, unchecked)."""
    game = game1943.find_game(st.get('game'))
    game_level = game1943.Game(game, lv)
    hf_name = f'terrains/{lv}/{lv}.heightfield-000.res'
    game_hf = nj.game_heightfield(st, lv, game_level.data(hf_name))     # refused when the copy has none
    return game, game_level, hf_name, terrain.read(nj.prepared_heightfield(st, lv, game_hf))


def output_path(lv: str, st: Mapping[str, Any], out: str | None = None, big_endian: bool = False) -> str:
    """Where the export goes: out when given; else work\\levels\\<level>\\navkit\\<level>.rsnv (.be.rsnv with
    big_endian), or <level>.failed.rsnv (.be.failed.rsnv) when the build's check failed - the soldier world the runtime
    and the vehicle builders read stays as it was (2026-10-30 second review: an export for inspection replaced it)."""
    if out:
        return out
    failed = '' if nj.check_passed(st) else '.failed'
    return os.path.join(game1943.level_work(lv), 'navkit', f'{lv}{".be" if big_endian else ""}{failed}.rsnv')


def _area_cells(lv: str, k: int, area: 'NavArea', pitch: float, y_offset: float, hf: 'Heightfield',
                build_cells: BuildCells | None, compare: bool,
                stats: collections.Counter[str]) -> tuple[IntArray, IntArray, FloatArray]:
    """Area k's cells (ii, jj) and their ground heights: the build's own (build_cells, navbuild.read_cells), or without
    them rasterised from the area's outline (rasterise). compare: also rasterise where the build's cells are read, and
    count into stats how the two differ."""
    if build_cells is None:
        return rasterise(area, pitch, y_offset, hf)
    rasterised = rasterise(area, pitch, y_offset, hf) if compare else None
    cell_i, cell_j, cell_y = (np.asarray(column) for column in build_cells[k][2:])
    if abs(build_cells[k][1] - pitch) > 1e-3:
        raise ValueError(f'{lv}: area {k}: cells file pitch {build_cells[k][1]} against {pitch}')
    if rasterised is not None:
        # the outline's cells against the build's (a check: they agree but for bridge corners)
        ii, jj, y = rasterised
        outline = dict(zip(zip(ii.tolist(), jj.tolist()), y.tolist()))
        built = set(zip(cell_i.tolist(), cell_j.tolist()))
        stats['cells'] += len(built)
        stats['only in the outline'] += len(set(outline) - built)
        stats['only in the cells file'] += len(built - set(outline))
        offs = [abs(outline[cell] - float(ground))
                for cell, ground in zip(zip(cell_i.tolist(), cell_j.tolist()), cell_y.tolist()) if cell in outline]
        stats['estimate off by over 0.3 m'] += sum(off > 0.3 for off in offs)
    return cell_i.astype(np.int64), cell_j.astype(np.int64), cell_y.astype(np.float64)


def export(lv: str, out: str | None = None, big_endian: bool = False, log: Log = print,
           compare: bool = False) -> tuple[str, 'NavData']:
    """Exports the level's checked build to RSNV (out, or output_path's file); returns (the file, the build read).
    compare: also rasterise the outlines where the build's cells are read, and report how they differ."""
    work = game1943.level_work(lv)
    hpd = os.path.join(work, 'navkit', f'{lv}.hpd')
    if not os.path.isfile(hpd):
        raise FileNotFoundError(f'{hpd}: build the level first (navkit.py build {lv})')
    st = nj.read(lv)
    refusal = build_refusal(st, lv, hpd)
    if refusal:
        raise NotCurrent(refusal)
    out = output_path(lv, st, out, big_endian)
    if not nj.check_passed(st):
        # exported, so the failed build can be looked at, but never stamped as the soldier world: the vehicle builders
        # refuse it, and navexport1943.py exits with code 1 (2026-10-30 review: it was stamped, exit code 0); without
        # --out it goes to <level>.failed.rsnv, beside the soldier world, not over it (second review)
        log(f'{lv}: the build\'s check failed ({st["checked"]}; navkit\\check.txt): exported for inspection only to '
            f'{out}, not stamped as the soldier world')
    xoff, zoff = st['sector_offsets']
    game, game_level, hf_name, hf = prepared_terrain(lv, st)
    sea = soldier1943.sea_level(st, hf)               # a level without water: under all its terrain
    y_offset = soldier1943.SETTINGS.y_offset          # the build's nodes and outlines stand this far up (0.9 m)
    nav = navdata.load(hpd, hf, y_offset=y_offset)
    log(f'{lv}: {len(nav.areas)} areas, {len(nav.node_pos)} nodes, {len(nav.edges)} edges')
    # the build's own cells (navbuild's sidecar): each area's kept surfaces and their heights, exactly. Without it the
    # cells are rasterised from the outlines and their heights estimated (rasterise)
    build_cells = navbuild.read_cells(hpd)
    if build_cells is not None and (len(build_cells) != len(nav.areas)
                                    or any(cells[0] != area.sector for cells, area in zip(build_cells, nav.areas))):
        log(f'{lv}: {os.path.basename(navbuild.cells_path(hpd))} does not match the areas of the build: not used')
        build_cells = None
    if build_cells is None:
        log(f'{lv}: no cells file for this build: cells rasterised from the outlines, heights estimated (rebuild to '
            f'get {os.path.basename(navbuild.cells_path(hpd))})')
    stats: collections.Counter[str] = collections.Counter()

    # sectors and their areas, in the build's order
    sector_ids = []
    for area in nav.areas:
        if not sector_ids or sector_ids[-1] != area.sector:
            sector_ids.append(area.sector)
    sector_index = {sector: k for k, sector in enumerate(sector_ids)}
    first_area = {}
    for k, area in enumerate(nav.areas):
        first_area.setdefault(area.sector, k)

    # areas: bitmaps at their pitch, height grids (the BMAP and HGHT sections, AREA's rows)
    bmap, hght, area_rows, area_cells = bytearray(), [], [], []
    for k, area in enumerate(nav.areas):
        pitch = 0.2 if area.pitch < 0.5 else COARSE_PITCH
        ii, jj, y = _area_cells(lv, k, area, pitch, y_offset, hf, build_cells, compare, stats)
        if len(ii):
            first_i, first_j = int(ii.min()), int(jj.min())
            width, height = int(ii.max()) - first_i + 1, int(jj.max()) - first_j + 1
            min_y, max_y = float(y.min()), float(y.max())
        else:
            first_i = first_j = 0
            width = height = 0
            min_y, max_y = area.min_y, area.max_y
        row_bytes = (width + 7) >> 3
        bits = np.zeros((height, row_bytes * 8), np.uint8)
        if len(ii):
            bits[jj - first_j, ii - first_i] = 1
        packed = np.packbits(bits.reshape(height, row_bytes, 8)[:, :, ::-1], axis=-1).reshape(-1) if height \
            else np.zeros(0, np.uint8)
        x0, z0 = first_i * pitch, first_j * pitch
        # height grid: blocks of HEIGHT_CELLS[pitch] x that many cells (1 m at 0.2 m, each cell at 0.8 m), each the
        # ground at the block's centre; readers interpolate between block centres (rsnv.World.ground)
        cells_per_block = HEIGHT_CELLS[pitch]
        local_i, local_j = (ii - first_i, jj - first_j) if len(ii) else (ii, jj)
        grid, hw, hh = height_grid(local_i, local_j, y, cells_per_block, min_y)
        # finer blocks where the 1 m ones misread the area's own cells: 0.4 m, then one per cell
        for finer in (2, 1):
            if cells_per_block <= finer or not len(ii) \
                    or grid_error(local_i, local_j, y, grid, hw, hh, cells_per_block, pitch, min_y) <= HEIGHT_TOL:
                break
            cells_per_block = finer
            grid, hw, hh = height_grid(local_i, local_j, y, cells_per_block, min_y)
        if cells_per_block != HEIGHT_CELLS[pitch]:
            stats[f'areas with {cells_per_block * pitch:g} m height blocks'] += 1
        pitch_mm = int(round(pitch * 1000))
        area_rows.append([x0, z0, x0 + (width - 1) * pitch, z0 + (height - 1) * pitch, min_y, max_y, len(bmap), width,
                          height, 0, 0, sector_index[area.sector], 0, 0, 0, 0, pitch_mm, len(hght), hw, hh,
                          cells_per_block * pitch_mm])
        bmap += packed.tobytes()
        hght += grid.ravel().tolist()
        area_cells.append((ii, jj, y, pitch))
    log(f'{lv}: bitmaps {len(bmap) / 1e6:.2f} MB, {sum(len(cells[0]) for cells in area_cells):,} cells'
        + (' (those of the build' + (f'; against the outlines: {dict(stats)})' if compare else ')')
           if build_cells is not None else '')
        + '; finer height grids: ' + str({name: count for name, count in stats.items() if 'height blocks' in name}))

    # nodes: ground heights, their areas
    ground_pos = nav.node_pos.copy()
    ground_pos[:, 1] -= y_offset
    cells_at = {}                                     # (pitch, i, j) -> [(area, ground height)]
    for k, (ii, jj, y, pitch) in enumerate(area_cells):
        for i, j, ground in zip(ii.tolist(), jj.tolist(), y.tolist()):
            cells_at.setdefault((round(pitch, 1), i, j), []).append((k, ground))
    node_area = np.full(len(ground_pos), 0xFFFF, np.int64)
    for n, (x, y, z) in enumerate(ground_pos):
        best = None
        for pitch in sorted({0.2, COARSE_PITCH}):
            near_i, near_j = int(round(x / pitch)), int(round(z / pitch))
            for di in (0, 1, -1, 2, -2):
                for dj in (0, 1, -1, 2, -2):
                    for k, ground in cells_at.get((pitch, near_i + di, near_j + dj), ()):
                        if abs(ground - y) <= 1.0:
                            off = math.hypot(di, dj) * pitch + abs(ground - y)
                            if best is None or off < best[0]:
                                best = (off, k)
        if best is not None:
            node_area[n] = best[1]

    # edges: CSR by source, kinds
    edges = nav.edges
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges = edges[order]
    build_cost = np.round(nav.edge_cost[order] * 16).astype(np.int64)
    cost = admissible_costs(ground_pos, edges[:, 0], edges[:, 1], build_cost)
    raised = int((cost != build_cost).sum())
    kind = np.zeros(len(edges), np.int64)
    if nav.ladder_edge is not None:
        ladder = nav.ladder_edge[order]
        up = ground_pos[edges[:, 1], 1] > ground_pos[edges[:, 0], 1]
        kind[ladder & up] = rsnv.EDGE_LADDER_UP
        kind[ladder & ~up] = rsnv.EDGE_LADDER_DOWN
    jump_links_path = os.path.join(work, 'navkit', 'jump_links.json')
    jumps = json.load(open(jump_links_path, encoding='utf-8')) if os.path.isfile(jump_links_path) else []
    ladders = json.load(open(os.path.join(work, 'objects', 'ladders.json'), encoding='utf-8'))

    def nearest_node(point: npt.ArrayLike, max_dist: float = END_MATCH) -> tuple[int | None, float]:
        dist = np.linalg.norm(ground_pos - np.asarray(point, float), axis=1)
        nearest = int(np.argmin(dist))
        return (nearest, float(dist[nearest])) if dist[nearest] <= max_dist else (None, float(dist[nearest]))
    edge_index = {}                                   # (source node, target node) -> edge
    for k_edge, (src, dst) in enumerate(edges):
        edge_index[(int(src), int(dst))] = k_edge
    jump_ends = []
    for link in jumps:
        platform_node, _ = nearest_node(link['platform'])
        deck_node, _ = nearest_node(link['deck'])
        jump_ends.append((platform_node, deck_node))
        for src, dst in ((platform_node, deck_node), (deck_node, platform_node)):
            if src is not None and dst is not None and (src, dst) in edge_index:
                kind[edge_index[(src, dst)]] = rsnv.EDGE_JUMP
    first_edge = np.searchsorted(edges[:, 0], np.arange(len(ground_pos) + 1))
    log(f'{lv}: edges by kind '
        + str({rsnv.EDGE_NAMES[k]: int(count) for k, count in zip(*np.unique(kind, return_counts=True))}))

    # islands
    node_island = nav.node_island.astype(np.int64)
    terrain_y = hf.height(ground_pos[:, 0], ground_pos[:, 2])
    over_deep = terrain_y < sea - soldier1943.WATER_DEPTH
    island_rows = []
    for k_island, _island in enumerate(nav.islands):
        members = np.nonzero(node_island == k_island)[0]
        flags = ((rsnv.ISLD_MAIN if k_island == 0 else 0)
                 | (rsnv.ISLD_CARRIER if len(members) and over_deep[members].mean() > 0.5 else 0))
        island_rows.append((len(members), int(members[0]) if len(members) else 0, flags, 0, 0))

    # node flags
    depth = sea - ground_pos[:, 1]
    node_flags = np.zeros(len(ground_pos), np.int64)
    node_flags[(depth > 0) & (depth <= 0.5)] |= rsnv.NAUX_SHALLOW
    node_flags[(depth > 0.5) & (depth <= soldier1943.WATER_DEPTH + 0.05)] |= rsnv.NAUX_WADE
    for k_edge in np.nonzero((kind == rsnv.EDGE_LADDER_UP) | (kind == rsnv.EDGE_LADDER_DOWN))[0]:
        node_flags[edges[k_edge, 0]] |= rsnv.NAUX_LADDER_END
    for k_edge in np.nonzero(kind == rsnv.EDGE_JUMP)[0]:
        node_flags[edges[k_edge, 0]] |= rsnv.NAUX_JUMP_END

    # areas: island (most of their nodes, else the reader's), nodes by area (the ANOD section)
    anod = []
    by_area = collections.defaultdict(list)
    for n, area_k in enumerate(node_area):
        if area_k != 0xFFFF:
            by_area[int(area_k)].append(n)
    for k, row in enumerate(area_rows):
        area_nodes = by_area.get(k, [])
        row[12] = collections.Counter(node_island[area_nodes].tolist()).most_common(1)[0][0] if area_nodes \
            else max(nav.areas[k].island, 0)
        row[14], row[15] = len(anod), len(area_nodes)
        anod += area_nodes

    # portals: inside sectors by target index; border portals to the neighbour sector's area across
    portals = []
    per_area = collections.defaultdict(list)
    for k, area in enumerate(nav.areas):
        first = first_area[area.sector]
        for p0, p1, target in area.portals:
            per_area[k].append((p0, p1, first + int(target)))
        for p0, p1 in area.borders:
            mid = (np.asarray(p0) + np.asarray(p1)) / 2
            along = np.asarray(p1) - np.asarray(p0)
            normal = np.array([-along[2], 0.0, along[0]])
            normal /= max(np.linalg.norm(normal), 1e-9)
            for side in (1, -1):
                probe = mid + side * 0.6 * normal
                hit = None
                for pitch in sorted({0.2, COARSE_PITCH}):
                    for other, ground in cells_at.get((pitch, int(round(probe[0] / pitch)), int(round(probe[2] / pitch))),
                                                      ()):
                        if nav.areas[other].sector != area.sector and abs(ground - (mid[1] - y_offset)) <= 1.0:
                            hit = other
                            break
                    if hit is not None:
                        break
                if hit is not None:
                    per_area[k].append((p0, p1, hit))
                    break
    for k in range(len(nav.areas)):
        area_rows[k][9] = len(portals)
        area_rows[k][10] = len(per_area[k])
        for p0, p1, target in per_area[k]:
            portals.append((float(p0[0]), float(p0[2]), float(p1[0]), float(p1[2]),
                            float((p0[1] + p1[1]) / 2 - y_offset), k, int(target)))

    # sectors and their 4 m buckets (the SECT, GBKT and GLST sections)
    sect, gbkt, glst = [], [], []
    for sector in sector_ids:
        X, Z = (sector >> 8) & 0xFF, sector & 0xFF
        sector_x0, sector_z0 = (X + xoff) * 100.0, (Z + zoff) * 100.0
        sector_areas = [k for k, area in enumerate(nav.areas) if area.sector == sector]
        pitch_code = 0 if nav.areas[sector_areas[0]].pitch < 0.5 else 1
        first_bucket = len(gbkt)
        buckets = [[] for _ in range(rsnv.BUCKETS * rsnv.BUCKETS)]
        for k in sector_areas:
            box = area_rows[k]                        # the area's row: min x, min z, max x, max z of the cell centres, ...
            if box[7] == 0:
                continue
            # the area's cells reach half a cell past their centres, and a sample leaning on a cell a quarter cell
            # further (rsnv.LOS_GRAZE): a whole cell of margin (2026-10-26: by its centres alone an area was missing from
            # the bucket left of its first cell, though points there lie on that cell)
            margin = box[16] / 1000.0
            bucket_i0 = max(int((box[0] - margin - sector_x0) // rsnv.BUCKET), 0)
            bucket_i1 = min(int((box[2] + margin - sector_x0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            bucket_j0 = max(int((box[1] - margin - sector_z0) // rsnv.BUCKET), 0)
            bucket_j1 = min(int((box[3] + margin - sector_z0) // rsnv.BUCKET), rsnv.BUCKETS - 1)
            for bj in range(bucket_j0, bucket_j1 + 1):
                for bi in range(bucket_i0, bucket_i1 + 1):
                    buckets[bj * rsnv.BUCKETS + bi].append(k)
        for bucket in buckets:
            bucket.sort(key=lambda k: area_rows[k][4])
            gbkt.append((len(glst), len(bucket)))
            glst += bucket
        sect.append((sector, 0, pitch_code, sector_x0, sector_z0, sector_x0 + 100.0, sector_z0 + 100.0, first_bucket,
                     sector_areas[0], len(sector_areas), 0))

    # ladders and jump links
    # only records whose two ends are nodes of the build: rsnav's loader refuses a LADR end that is no node, and a
    # record the build did not link (a ladder with no ground at an end, or a jump_links.json newer than the build: the
    # Wake Island and Iwo Jima exports of 2026-10-26 01:30, whose builds had run out of memory) is no link at all
    ladr, unlinked = [], []
    for k, ladder_record in enumerate(ladders):
        lo, hi = ladder_record['lo'], ladder_record['hi']
        foot = [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]
        top = [(lo[0] + hi[0]) / 2, hi[1], (lo[2] + hi[2]) / 2]
        foot_node, _ = nearest_node(foot, 3.0)
        top_node, _ = nearest_node(top, 3.0)
        if foot_node is None or top_node is None:
            unlinked.append(f"ladder {k} ({ladder_record.get('bp', '?').rsplit('/', 1)[-1]})")
            continue
        ladr.append((*foot, *top, 0.0, foot_node, top_node, k))
    for k, (link, (platform_node, deck_node)) in enumerate(zip(jumps, jump_ends)):
        if platform_node is None or deck_node is None or (platform_node, deck_node) not in edge_index:
            unlinked.append(f"jump link {k} ({link.get('bp', '?')})")
            continue
        dx, dz = link['deck'][0] - link['platform'][0], link['deck'][2] - link['platform'][2]
        ladr.append((*link['platform'], *link['deck'], math.atan2(dx, dz), platform_node, deck_node, (1 << 24) | k))
    if unlinked:
        log(f'{lv}: {len(unlinked)} ladder / jump record(s) with no route node or edge in the build left out of LADR '
            f'(rebuild if jump_links.json is newer than the build): ' + ', '.join(unlinked[:6])
            + (' ...' if len(unlinked) > 6 else ''))

    # vehicle entry points
    from bfbc2core import level1943
    level = level1943.load(lv, game=game, log=lambda *_args: None)
    offm = []
    for spawn in level.entities:
        if spawn.category != 'vehicle spawn' or spawn.position is None:
            continue
        partition = vehicles1943.vehicle_partition(spawn)
        vehicle_name = partition.rsplit('/', 1)[-1] if partition else ''
        vehicle_cls = vehicles1943.vehicle_class(vehicle_name)
        # a spawn without a team (None) is both teams' (3)
        team = {1: 1, 2: 2}.get(spawn.team, 3)  # pyright: ignore[reportCallIssue, reportArgumentType]
        for entry in vehicles1943.entry_points(game_level, spawn):
            node, _ = nearest_node([entry['point'][0], entry['point'][1] - y_offset, entry['point'][2]],
                                   entry['radius'] + 2.0)
            offm_kind = rsnv.OFFM_MOUNT_STATIC if vehicle_cls == 'stationary weapon' else rsnv.OFFM_BOARD_VEHICLE
            offm.append((offm_kind, team, vehicles1943.CLASSES.index(vehicle_cls),
                         node if node is not None else 0xFFFFFFFF, *entry['point'], 0.0, entry['radius'], spawn.index))

    node_rows = [(float(x), float(y), float(z), int(first_edge[n])) for n, (x, y, z) in enumerate(ground_pos)]
    node_rows.append((0.0, 0.0, 0.0, int(first_edge[-1])))
    naux = [(int(node_area[n]), sector_index.get(int(nav.node_sector[n]), 0xFFFF), int(node_island[n]),
             int(node_flags[n])) for n in range(len(ground_pos))]
    edge_rows = [(int(dst) | (int(kind[k]) << 24), min(int(cost[k]), 0xFFFF), 0) for k, (src, dst) in enumerate(edges)]
    inputs = {'hpd': hpd, 'jump_links.json': jump_links_path,
              'ladders.json': os.path.join(work, 'objects', 'ladders.json'),
              'navkit.json': os.path.join(work, 'navkit', 'navkit.json')}
    if build_cells is not None:
        inputs['cells.npz'] = navbuild.cells_path(hpd)
    meta = {'exporter': EXPORTER_VERSION, 'level': lv,
            'cells': 'build' if build_cells is not None else 'rasterised outlines',
            'sources': {name: {'path': path, 'sha256': sha256(path)} for name, path in inputs.items()
                        if os.path.isfile(path)},
            # (cast: prepared_terrain refused a copy without the height field)
            'game heightfield': {'name': hf_name,
                                 'sha256': hashlib.sha256(cast(bytes, game_level.data(hf_name))).hexdigest()},
            'soldier': {'radius': soldier1943.RADIUS, 'step': soldier1943.STEP, 'height': soldier1943.HEIGHT,
                        'slope_max': soldier1943.SLOPE_MAX, 'water_depth': soldier1943.WATER_DEPTH},
            'counts': {'nodes': len(ground_pos), 'edges': len(edges), 'areas': len(nav.areas), 'portals': len(portals),
                       'ladders': len(ladders), 'jump links': len(jumps), 'LADR records': len(ladr),
                       'ladder and jump records not linked in the build': len(unlinked), 'vehicle entries': len(offm),
                       'islands': len(island_rows)}}
    sections = {'SECT': sect, 'NODE': node_rows, 'NAUX': naux, 'EDGE': edge_rows,
                'AREA': [tuple(row) for row in area_rows], 'PORT': portals, 'BMAP': bytes(bmap),
                'HGHT': [(value,) for value in hght], 'GBKT': gbkt, 'GLST': [(value,) for value in glst],
                'ANOD': [(value,) for value in anod], 'LADR': ladr, 'OFFM': offm, 'ISLD': island_rows,
                'META': json.dumps(meta, indent=1).encode('utf-8')}
    # the search heuristic's scale (rsnv.heuristic_scale, header): admissible for this graph's edge costs
    h_scale_q = rsnv.heuristic_scale(ground_pos, edges[:, 0], edges[:, 1], np.minimum(cost, 0xFFFF))
    meta['heuristic scale'] = h_scale_q / rsnv.H_SCALE_ONE
    meta['counts']['edge costs rounded up'] = raised
    header = {'level': lv, 'sea_level': sea, 'radius': soldier1943.RADIUS, 'height': soldier1943.HEIGHT,
              'step': soldier1943.STEP, 'water_depth': soldier1943.WATER_DEPTH,
              'hpd_sha256': meta['sources']['hpd']['sha256'], 'h_scale_q': h_scale_q}
    size = rsnv.write(out, sections, header, big_endian)
    # tight edges: walk edges with no line of sight either way over the cells just written (rsnv.World.los, the
    # runtime's rule): the graph links them, the cells leave no room (a slit, an obstacle's corner). Flagged so a
    # soldier's search can price them (EDGEF_TIGHT); the file is written again with the flags.
    world = rsnv.load(out)
    tight = set()
    for k, (src, dst) in enumerate(edges):
        if int(kind[k]) != rsnv.EDGE_WALK or k in tight:
            continue
        pa, pb = tuple(float(v) for v in world.node_pos[src]), tuple(float(v) for v in world.node_pos[dst])
        if not world.los(pa, pb) and not world.los(pb, pa):
            tight.add(k)
            # and the reverse edge, dst -> src
            tight.update(j for j in range(int(first_edge[dst]), int(first_edge[dst + 1])) if int(edges[j][1]) == int(src))
    sections['EDGE'] = [(target, cost_q, rsnv.EDGEF_TIGHT if k in tight else 0)
                        for k, (target, cost_q, _flags) in enumerate(edge_rows)]
    meta['counts']['tight walk edges'] = len(tight)
    sections['META'] = json.dumps(meta, indent=1).encode('utf-8')
    size = rsnv.write(out, sections, header, big_endian)
    stamp_export(lv, st, out)                         # when out is the soldier world the vehicle builders read
    walk = sum(1 for k in range(len(edges)) if int(kind[k]) == rsnv.EDGE_WALK)
    log(f'{lv}: heuristic scale {h_scale_q / rsnv.H_SCALE_ONE:.6f} (the least edge cost per 1/16 m of length)')
    log(f'{lv}: wrote {out} ({size / 1e6:.2f} MB; {len(portals)} portals, {len(ladr)} ladders and jump links, '
        f'{len(offm)} vehicle entries, {len(island_rows)} islands; {len(tight)} of {walk} walk edges tight)')
    return out, nav


def stamp_export(lv: str, st: Mapping[str, Any], path: str) -> bool:
    """navkit.json 'exported' when path is the soldier world the vehicle builders read (<level>.rsnv, by file identity:
    2026-10-30, --out naming that file left the old stamp): the build it was made from (its .hpd's SHA-256,
    'hpd_sha256'; 'built' keeps only the minute) and the file's SHA-256 (vehicleclasses1943.soldier_world checks both).
    Not written when navkit.json now holds another build (built again meanwhile), or when the build's check failed
    (an export of a failed build is for inspection only). Returns whether it was written."""
    if not nj.same_file(path, os.path.join(nj.paths(lv)['navkit'], f'{lv}.rsnv')):
        return False
    build = (st.get('build_from') or {}).get('hpd_sha256')
    digest = sha256(path)
    stamped = []

    def change(now: dict[str, Any]) -> None:
        # the stage table's 'exported': it needs the build, the one exported (not one made since), and a passed check
        if not build or now.get('preparation') != st['preparation'] or nj.stale(now, 'built') \
                or now['build_from']['hpd_sha256'] != build:
            return
        if not nj.check_passed(now):
            return
        nj.record_stage(now, 'exported', {'exported': {
            'preparation': st['preparation'], 'built': now.get('built'), 'hpd_sha256': build, 'sha256': digest,
            'exporter': EXPORTER_VERSION.split(' ')[0]}})
        stamped.append(True)
    nj.update(lv, change)
    return bool(stamped)


def check(path: str, nav: 'NavData', log: Log = print) -> tuple[list[str], rsnv.World]:
    """Reads the file back: validate(), and its graph against the build's (navdata)."""
    world = rsnv.load(path)
    problems = list(world.validate())
    n = len(nav.node_pos)
    if len(world.node_pos) != n:
        problems.append(f'{len(world.node_pos)} nodes, the build has {n}')
    else:
        off = np.abs(world.node_pos[:, [0, 2]] - nav.node_pos[:, [0, 2]]).max()
        if off > 1e-3:
            problems.append(f'node positions differ by up to {off:.4f} m')
        src = np.repeat(np.arange(n), np.diff(world.node_first_edge))
        got = sorted(zip(src.tolist(), world.edge_target.tolist(), world.edge_cost.tolist()))
        ground_pos = nav.node_pos.copy()
        ground_pos[:, 1] -= soldier1943.HEIGHT / 2
        want = sorted(zip(nav.edges[:, 0].tolist(), nav.edges[:, 1].tolist(),
                          admissible_costs(ground_pos, nav.edges[:, 0], nav.edges[:, 1],
                                           np.round(nav.edge_cost * 16).astype(np.int64)).tolist()))
        if got != want:
            problems.append('edges or costs differ from the build')
    unplaced = int((world.node_area == 0xFFFF).sum())
    log(f'check: {len(problems)} problem(s); nodes without an area {unplaced} of {len(world.node_pos)}')
    for problem in problems:
        log('  ' + problem)
    return problems, world


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='navexport1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('level')
    parser.add_argument('--out')
    parser.add_argument('--big-endian', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--compare', action='store_true',
                        help='also compare the cells of the build with the rasterised outlines')
    args = parser.parse_args(argv[1:])
    path, nav = export(args.level, args.out, args.big_endian, compare=args.compare)
    failed = 0
    if args.check:
        problems, _ = check(path, nav)
        failed = 1 if problems else 0
    st = nj.read(args.level)
    if not nj.check_passed(st):
        print(f'error: {args.level}: the build\'s check failed ({st.get("checked")}): {path} was written for inspection only '
              f'and is not the soldier world, which stays as it was (fix the build, navkit.py check {args.level}, export '
              f'again)')
        return 1
    return failed


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navexport1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
