r"""NavKit (big-endian): a first flight graph for BF1943's planes - 3-D waypoints over the terrain and collision with
clearance, take-off and landing lanes at the plane spawns (carrier decks, airfields), parachute drops to the ground,
exported to RSNV as its own world (<level>.plane.rsnv).

  airnav1943.py <level> [--soldier <level.rsnv>]

What it builds (parameters: vehicleclasses1943.CLASSES['plane'], all [E] except where marked):
  - the altitude floor: 16 m cells over the land, carriers and plane spawns plus a 600 m margin; each cell's
    highest terrain (5 x 5 samples, core terrain.py) or collision (all_tris.npy, every object: towers, trees,
    carrier islands) [O], grown by the clearance radius (25 m) - at least the sea level + 30 m;
  - the cruise surface: the floor + cruise_agl (60 m), raised so that it climbs at most 0.12 m per metre (7 deg:
    the runtime's line of sight allows 0.6 m between its samples 4 m apart); every cell of it is a cell of the
    world, its ground the cruise height (the bitmap is the whole box: a plane may fly anywhere in it);
  - route nodes: one per 4 x 4 block (64 m) on the cruise surface; edges to the 8 neighbouring blocks and the knight
    moves, kept when the straight segment stays at least the radius above the floor;
  - take-off: at each plane spawn a lane on its deck or field (2 m cells, 3 wide, at the spawn's height) along the
    spawn's heading [O: the carrier planes face the bows], takeoff_run (120 m) long, then a JUMP edge (here: the
    climb-out) to the cruise node nearest a point 300 m ahead; landing: a DROP edge (here: the approach) from the
    cruise node nearest a point 400 m behind the spawn to the lane's start. That planes take off along their spawn
    heading on Wake's airfield and the decks is [H]; the runway length is [U];
  - transfers (OFFM): BOARD_VEHICLE at each plane spawn (the soldier-side point: the LandingGearEntryComponentData
    entry, local (0, 0.24, -0.59), EntryRadius 7 m [O]; aux: the soldier node nearest it); DROP (kind 4, a parachute:
    IsAllowedToExitInAir is true [O]) from the cruise node over each base and flag, and over each soldier island of
    at least 10 nodes that is not a carrier (Coral Sea's islets), to the soldier node there (aux).
"""
import argparse
import math
import os
import sys
import time

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import rsnvgrid1943  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import game1943, rsnv, terrain  # noqa: E402

MARGIN = 600.0
SEA_CLEAR = 30.0
CLIMB = 0.12                 # m per m: the cruise surface's steepest climb
CLIMB_OUT = 300.0            # m ahead of the lane's end: the first cruise node
APPROACH = 400.0             # m behind the spawn: the landing approach node
LANE_PITCH = 2.0
ENTRY_LOCAL = (0.0, 0.239, -0.592)     # [O] corsair.dbx LandingGearEntryComponentData Transform (zero: the same)
ENTRY_RADIUS = 7.0                     # [O] its EntryRadius
OFFM_DROP = 4
DROP_ISLAND_MIN = 10
# take-off lanes checked against the collision (2026-10-26): the obstacle grid of rsnav's flight test (flightsim.py:
# the highest terrain or object per 4 m cell from the height field's corner, terrain sampled 5 x 5 a cell, triangles
# every 2 m), so a lane clear here is clear there
OBS_CELL = 4.0
OBS_WINDOW = 320.0           # m around each plane spawn
CLEAR_PAST = 40.0            # m past the lane's end the centre line stays clear (the climb-out)
CLEAR_ACROSS = 2.0           # m either side of the centre line that must be clear
CLEAR_RISE = 0.5             # m over the lane's height: an obstacle
WING_HALF = 6.0              # m: half the wingspan [O] (11.9 / 12.0 m)
LIFT_OFF = 85.0              # m of roll the deck or runway must carry (the flight test's plane lifts off at 81 m [E])
SUPPORT_DROP = 3.5           # m: the deck or runway under the roll no lower than the lane less this
HEADINGS = [0.0] + [v * sgn for v in np.arange(0.5, 20.01, 0.5) for sgn in (1, -1)]    # deg off the spawn heading
LENGTHS = [120.0, 110.0, 100.0, 90.0, 80.0, 75.0]   # m; under 90 only when nothing longer is clear (short: the
                                                   # plane rolls on past the end to lift off, flightsim.Lane)
HEADINGS_WIDE = [v * sgn for v in np.arange(22.5, 180.01, 2.5) for sgn in (1, -1)]  # deg: second stage, turning
STARTS = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0]   # m ahead of the spawn the lane may begin: the plane taxis
                             # that far first [H] (out of a hangar: Iwo Jima's zero in hangar_t01 [O])


def paths(lv):
    work = game1943.level_work(lv)
    return {'objects': os.path.join(work, 'objects'), 'navkit': os.path.join(work, 'navkit'),
            'rsnv': os.path.join(work, 'navkit', f'{lv}.plane.rsnv'),
            'soldier': os.path.join(work, 'navkit', f'{lv}.rsnv')}


def plane_spawns(lv, game=None):
    from bfbc2core import level1943, vehicles1943
    level = level1943.load(lv, game=game, log=lambda *a: None)
    out = []
    for entity in level.entities:
        if entity.category != 'vehicle spawn' or entity.position is None:
            continue
        partition = vehicles1943.vehicle_partition(entity)
        name = partition.rsplit('/', 1)[-1] if partition else ''
        if vehicles1943.vehicle_class(name) != 'aircraft':
            continue
        matrix = np.asarray(entity.matrix, float)
        forward = matrix[2, :3].copy()
        forward[1] = 0.0
        forward /= max(np.linalg.norm(forward), 1e-9)
        entry = np.array(ENTRY_LOCAL) @ matrix[:3, :3] + matrix[3, :3]
        out.append({'vehicle': name, 'team': entity.team, 'pos': [float(v) for v in entity.position],
                    'fwd': forward.tolist(), 'yaw': math.atan2(forward[0], forward[2]), 'entry': entry.tolist(),
                    'index': entity.index})
    return out, level


def floor_grid(T, hf, x0, z0, nx, nz, pitch, sea):
    """Highest terrain or collision per cell. T: the triangles, (n, 3 corners, x y z)."""
    top = np.full((nz, nx), -1e9)
    for a in np.linspace(-0.5, 0.5, 5):
        for b in np.linspace(-0.5, 0.5, 5):
            I, J = np.meshgrid(np.arange(nx), np.arange(nz))      # cell column / row indices
            top = np.maximum(top, hf.height(x0 + (I + a) * pitch, z0 + (J + b) * pitch))
    # each triangle's cell range (first and last cell in x and z) and its highest corner
    grid_corner = [x0 - pitch / 2, z0 - pitch / 2]
    cell_lo = np.floor((T[:, :, [0, 2]].min(1) - grid_corner) / pitch).astype(np.int64)
    cell_hi = np.floor((T[:, :, [0, 2]].max(1) - grid_corner) / pitch).astype(np.int64)
    tri_top = T[:, :, 1].max(1).astype(np.float64)
    in_one_cell = (cell_lo[:, 0] == cell_hi[:, 0]) & (cell_lo[:, 1] == cell_hi[:, 1])
    on_grid = (cell_lo[:, 0] >= 0) & (cell_lo[:, 1] >= 0) & (cell_lo[:, 0] < nx) & (cell_lo[:, 1] < nz)
    single = in_one_cell & on_grid
    np.maximum.at(top, (cell_lo[single, 1], cell_lo[single, 0]), tri_top[single])
    for tri in np.nonzero(~in_one_cell)[0].tolist():
        i0, i1 = max(cell_lo[tri, 0], 0), min(cell_hi[tri, 0], nx - 1)
        j0, j1 = max(cell_lo[tri, 1], 0), min(cell_hi[tri, 1], nz - 1)
        if i1 >= i0 and j1 >= j0:
            top[j0:j1 + 1, i0:i1 + 1] = np.maximum(top[j0:j1 + 1, i0:i1 + 1], tri_top[tri])
    return np.maximum(top, sea)


def grow_max(a, cells):
    out = a.copy()
    nz, nx = a.shape
    reach = int(math.ceil(cells))
    for dj in range(-reach, reach + 1):
        for di in range(-reach, reach + 1):
            if di * di + dj * dj <= cells * cells + 1e-9:
                src = a[max(0, -dj):nz - max(0, dj), max(0, -di):nx - max(0, di)]
                dst = out[max(0, dj):nz + min(0, dj), max(0, di):nx + min(0, di)]
                np.maximum(dst, src, out=dst)
    return out


def envelope(alt, pitch, climb):
    """The lowest surface over alt whose rise between neighbouring cells is at most climb x their distance."""
    out = alt.copy()
    nz, nx = out.shape
    for _ in range(4 * max(nx, nz)):
        prev = out.copy()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            max_rise = math.hypot(di, dj) * pitch * climb
            src = out[max(0, -dj):nz - max(0, dj), max(0, -di):nx - max(0, di)]
            dst = out[max(0, dj):nz + min(0, dj), max(0, di):nx + min(0, di)]
            np.maximum(dst, src - max_rise, out=dst)
        if np.array_equal(prev, out):
            break
    return out


def obstacle_tops(T, hf, sea, cx, cz, half=OBS_WINDOW):
    """(x0, z0, top): the highest terrain or object per OBS_CELL cell over a window around (cx, cz), on the grid of
    rsnav's flightsim.Obstacles (cells from the height field's corner). T: the triangles, (n, 3 corners, x y z)."""
    hf_x0, hf_z0 = float(hf.min[0]), float(hf.min[2])
    i0 = max(int((cx - half - hf_x0) // OBS_CELL), 0)
    j0 = max(int((cz - half - hf_z0) // OBS_CELL), 0)
    hf_cells = int((hf.max[0] - hf.min[0]) // OBS_CELL), int((hf.max[2] - hf.min[2]) // OBS_CELL)
    i1 = min(int((cx + half - hf_x0) // OBS_CELL) + 1, hf_cells[0])
    j1 = min(int((cz + half - hf_z0) // OBS_CELL) + 1, hf_cells[1])
    nx, nz = i1 - i0, j1 - j0
    x0, z0 = hf_x0 + i0 * OBS_CELL, hf_z0 + j0 * OBS_CELL
    top = np.full((nz, nx), -1e9)
    I, J = np.meshgrid(np.arange(nx), np.arange(nz))              # cell column / row indices
    for a in np.linspace(0.0, 1.0, 5):
        for b in np.linspace(0.0, 1.0, 5):
            top = np.maximum(top, hf.height(x0 + (I + a) * OBS_CELL, z0 + (J + b) * OBS_CELL))
    # the triangles over the window, each sampled every 2 m or closer (barycentric points on a regular lattice)
    tri_lo, tri_hi = T[:, :, [0, 2]].min(1), T[:, :, [0, 2]].max(1)
    near = ((tri_hi[:, 0] >= x0) & (tri_lo[:, 0] <= x0 + nx * OBS_CELL)
            & (tri_hi[:, 1] >= z0) & (tri_lo[:, 1] <= z0 + nz * OBS_CELL))
    tris = np.asarray(T[near], np.float64)
    longest_edge = np.maximum(np.maximum(np.linalg.norm(tris[:, 0] - tris[:, 1], axis=1),
                                         np.linalg.norm(tris[:, 1] - tris[:, 2], axis=1)),
                              np.linalg.norm(tris[:, 2] - tris[:, 0], axis=1))
    divisions = np.maximum(np.ceil(longest_edge / 2.0), 1).astype(np.int64)
    for m in np.unique(divisions):
        group = tris[divisions == m]
        uv = np.array([(i / m, j / m) for i in range(m + 1) for j in range(m + 1 - i)])   # barycentric (u, v)
        chunk_size = max(1, 2_000_000 // len(uv))
        for chunk_start in range(0, len(group), chunk_size):
            chunk = group[chunk_start:chunk_start + chunk_size]
            points = chunk[:, None, 0] + uv[None, :, 0, None] * (chunk[:, None, 1] - chunk[:, None, 0]) + \
                uv[None, :, 1, None] * (chunk[:, None, 2] - chunk[:, None, 0])
            points = points.reshape(-1, 3)
            cell_i = np.floor((points[:, 0] - x0) / OBS_CELL).astype(np.int64)
            cell_j = np.floor((points[:, 2] - z0) / OBS_CELL).astype(np.int64)
            inside = (cell_i >= 0) & (cell_j >= 0) & (cell_i < nx) & (cell_j < nz)
            np.maximum.at(top, (cell_j[inside], cell_i[inside]), points[inside, 1])
    return x0, z0, np.maximum(top, sea)


def lane_check(obs, pa, pb, y):
    """(centre line clear, the roll carried to lift-off, the highest obstacle over the lane within the half wingspan)
    of the lane pa -> pb at height y: the flight test's rule (rsnav tests/test_flight.py lane_obstruction), the centre
    line +-CLEAR_ACROSS every metre from the start to CLEAR_PAST past the end, here every 1 m across."""
    x0, z0, top = obs
    nz, nx = top.shape

    def obstacle_at(x, z):
        i, j = int((x - x0) // OBS_CELL), int((z - z0) // OBS_CELL)
        return top[j, i] if 0 <= i < nx and 0 <= j < nz else -1e9
    yaw = math.atan2(pb[0] - pa[0], pb[2] - pa[2])
    sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
    length = math.dist((pa[0], pa[2]), (pb[0], pb[2]))
    clear, carried, wing = True, True, -1e9
    for along in range(0, int(length + CLEAR_PAST) + 1):            # m from the lane's start
        for across in np.arange(-CLEAR_ACROSS, CLEAR_ACROSS + 0.01, 1.0):
            x = pa[0] + sin_yaw * along + cos_yaw * across
            z = pa[2] + cos_yaw * along - sin_yaw * across
            if obstacle_at(x, z) - y > CLEAR_RISE:
                clear = False
                break
        if not clear:
            break
        if along <= LIFT_OFF and obstacle_at(pa[0] + sin_yaw * along, pa[2] + cos_yaw * along) < y - SUPPORT_DROP:
            carried = False
        if along <= length and along % 2 == 0:
            for across in range(-int(WING_HALF), int(WING_HALF) + 1, 2):
                x = pa[0] + sin_yaw * along + cos_yaw * across
                z = pa[2] + cos_yaw * along - sin_yaw * across
                wing = max(wing, obstacle_at(x, z) - y)
    return clear, carried, wing


def segment_clear(floor, x0, z0, pitch, a, b, clear):
    """Whether the straight segment a -> b (x, y, z) stays clear above the floor grid (samples every half cell)."""
    steps = max(2, int(math.ceil(math.dist(a, b) / (pitch / 2))))
    t = np.linspace(0, 1, steps + 1)
    points = np.asarray(a)[None, :] + (np.asarray(b) - np.asarray(a))[None, :] * t[:, None]
    i = np.clip(np.round((points[:, 0] - x0) / pitch).astype(int), 0, floor.shape[1] - 1)
    j = np.clip(np.round((points[:, 2] - z0) / pitch).astype(int), 0, floor.shape[0] - 1)
    return bool((points[:, 1] >= floor[j, i] + clear - 1e-6).all())


def flight_box(hf, sea, pts):
    """(min (x, z), max (x, z)) the plane world covers before it is fitted to the sectors: the land (terrain above the
    sea) and the points given (plane spawns, soldier nodes), MARGIN around. 2026-10-30 review: no terrain above the
    sea raised ValueError on an empty array; ValueError now only when there is neither land nor a point."""
    lo, hi = np.array([np.inf, np.inf]), np.array([-np.inf, -np.inf])
    land = hf.heights > sea
    if land.any():
        jj, ii = np.nonzero(land)
        cell_x, cell_z = hf.cell
        lo = np.array([hf.min[0] + ii.min() * cell_x, hf.min[2] + jj.min() * cell_z])
        hi = np.array([hf.min[0] + (ii.max() + 1) * cell_x, hf.min[2] + (jj.max() + 1) * cell_z])
    if len(pts):
        points_xz = np.asarray(pts, float).reshape(-1, 3)[:, [0, 2]]
        lo, hi = np.minimum(lo, points_xz.min(0)), np.maximum(hi, points_xz.max(0))
    if not (lo <= hi).all():
        raise ValueError(f'no land above the sea ({sea:g} m), no plane spawn and no soldier node: no plane world')
    return lo - MARGIN, hi + MARGIN


def lane_cell(point, lane_x0, lane_z0):
    """The (i, j) lane cell (LANE_PITCH, from the lane grid's corner) nearest a point."""
    return int(round((point[0] - lane_x0) / LANE_PITCH)), int(round((point[2] - lane_z0) / LANE_PITCH))


def build(lv, soldier_path=None, game=None, log=print):
    """The plane world: (rsnv path, stats). Reads the level from the game copy it was prepared from (2026-10-30:
    game=None read the default copy); a level without water flies over its terrain (soldier1943.sea_level)."""
    import soldier1943
    files = paths(lv)
    started = time.time()
    state = nj.read(lv)
    game = vc.prepared_game(state, game, lv)
    want = vc.inputs(state, lv, 'plane')
    soldier_path = soldier_path or files['soldier']
    want['soldier'] = vc.soldier_world(state, lv, soldier_path)
    vc.drop(lv, 'worlds', 'plane')
    pitch = vc.value('plane', 'pitch')
    radius = vc.value('plane', 'radius')
    cruise_agl = vc.value('plane', 'cruise_agl')
    hf_path = os.path.join(files['objects'], 'heightfield.res')
    hf = terrain.read(hf_path)
    sea = soldier1943.sea_level(state, hf)              # 2026-10-30: float(None) raised on a level without water
    spawns, level = plane_spawns(lv, game)
    soldier = rsnv.load(soldier_path)
    lo, hi = flight_box(hf, sea, [spawn['pos'] for spawn in spawns] + soldier.node_pos.tolist())
    lo = np.maximum(lo, [hf.min[0], hf.min[2]])
    hi = np.minimum(hi, [hf.max[0] - pitch, hf.max[2] - pitch])
    # whole sectors' worth of the soldier grid: keep the box inside the sector grid (X, Z >= 0)
    xoff, zoff = state['sector_offsets']
    lo = np.maximum(lo, [xoff * 100.0 + pitch, zoff * 100.0 + pitch])
    x0 = math.floor(lo[0] / pitch) * pitch + pitch / 2
    z0 = math.floor(lo[1] / pitch) * pitch + pitch / 2
    nx, nz = int((hi[0] - x0) / pitch) + 1, int((hi[1] - z0) / pitch) + 1
    tris = np.load(os.path.join(files['objects'], 'all_tris.npy'))
    top = floor_grid(tris, hf, x0, z0, nx, nz, pitch, sea)
    floor = np.maximum(grow_max(top, radius / pitch), sea + SEA_CLEAR)
    cruise = envelope(floor + cruise_agl, pitch, CLIMB)
    log(f'{lv}: flight grid {nx} x {nz} cells of {pitch:g} m; floor {floor.min():.0f}..{floor.max():.0f} m, '
        f'cruise {cruise.min():.0f}..{cruise.max():.0f} m')
    cell_index = np.arange(nx * nz).reshape(nz, nx)
    I, J = np.meshgrid(np.arange(nx), np.arange(nz))              # cell column / row indices
    links = rsnvgrid1943.grid_links(cell_index)
    air = rsnvgrid1943.CellSet(pitch, x0, z0, I.ravel(), J.ravel(), cruise.ravel(), links, name=f'{lv} air')
    # cruise nodes: the centre cell of each node_spacing x node_spacing block
    spacing = int(vc.value('plane', 'node_spacing'))
    blocks = {}
    for block_j in range(0, nz, spacing):
        for block_i in range(0, nx, spacing):
            centre_i, centre_j = min(block_i + spacing // 2, nx - 1), min(block_j + spacing // 2, nz - 1)
            blocks[(block_i // spacing, block_j // spacing)] = int(cell_index[centre_j, centre_i])
    cell_nodes = sorted(set(blocks.values()))

    def cell_pos(k):
        return air.x0 + air.i[k] * pitch, float(air.y[k]), air.z0 + air.j[k] * pitch
    edges = set()
    for (block_i, block_j), cell in blocks.items():
        for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1), (2, 1), (1, 2), (2, -1), (1, -2)):
            other = blocks.get((block_i + di, block_j + dj))
            if other is not None and segment_clear(floor, x0, z0, pitch, cell_pos(cell), cell_pos(other), radius):
                edges.add((min(cell, other), max(cell, other)))
    node_list = [(0, k) for k in cell_nodes]
    node_of_cell = {k: n for n, k in enumerate(cell_nodes)}
    edge_list = [(node_of_cell[a], node_of_cell[b], rsnv.EDGE_WALK, None, True) for a, b in sorted(edges)]
    node_pos = np.array([cell_pos(k) for k in cell_nodes])
    log(f'  {len(cell_nodes)} cruise nodes, {len(edges)} edges')

    def nearest_cruise(x, z):
        dist2 = (node_pos[:, 0] - x) ** 2 + (node_pos[:, 2] - z) ** 2
        return int(np.argmin(dist2))
    # take-off and landing lanes: from the spawn along its heading [O: the planes face along the decks and fields],
    # checked against the collision; a blocked lane is turned (up to 20 deg), shortened (to 90 m) or started a few
    # metres ahead, in that order of preference (see choose_lane)
    sets = [air]
    offm, lanes, board = [], [], []
    run_default = vc.value('plane', 'takeoff_run')
    for spawn in spawns:
        spawn_pos = np.array(spawn['pos'])
        obstacles = obstacle_tops(tris, hf, sea, spawn_pos[0], spawn_pos[2])
        choice = choose_lane(obstacles, spawn_pos, spawn['yaw'])
        forward = np.array([math.sin(choice['yaw']), 0.0, math.cos(choice['yaw'])])
        side = np.array([forward[2], 0.0, -forward[0]])
        run = choice['length']
        start = spawn_pos + forward * choice['start']
        lane_x0 = math.floor((spawn_pos[0] - run_default - 20) / LANE_PITCH) * LANE_PITCH
        lane_z0 = math.floor((spawn_pos[2] - run_default - 20) / LANE_PITCH) * LANE_PITCH
        # the lane's cells (3 wide, from the spawn to the run's end) at the spawn's height
        cells = {}
        for along in np.arange(0.0, choice['start'] + run + 1e-6, LANE_PITCH / 2):
            for across in (-LANE_PITCH, 0.0, LANE_PITCH):
                point = spawn_pos + forward * along + side * across
                cells.setdefault(lane_cell(point, lane_x0, lane_z0), spawn_pos[1])
        lane_cells = sorted(cells)
        cell_id = {c: k for k, c in enumerate(lane_cells)}
        lane_links = {}
        for (ci, cj), k in cell_id.items():
            for di, dj in rsnvgrid1943.STRAIGHT:
                neighbour = cell_id.get((ci + di, cj + dj))
                if neighbour is not None:
                    lane_links[(k, di, dj)] = neighbour
        sets.append(rsnvgrid1943.CellSet(LANE_PITCH, lane_x0, lane_z0, [c[0] for c in lane_cells],
                                         [c[1] for c in lane_cells], [cells[c] for c in lane_cells], lane_links,
                                         name=f'runway {spawn["index"]}'))
        set_index = len(sets) - 1
        start_cell = lane_cell(start, lane_x0, lane_z0)
        end = start + forward * run
        end_cell = lane_cell(end, lane_x0, lane_z0)
        start_node = len(node_list)
        node_list.append((set_index, cell_id[start_cell]))
        end_node = len(node_list)
        node_list.append((set_index, cell_id.get(end_cell, max(cell_id.values()))))
        edge_list.append((start_node, end_node, rsnv.EDGE_WALK, None, False))
        climb_node = nearest_cruise(*(start + forward * (run + CLIMB_OUT))[[0, 2]])
        approach_node = nearest_cruise(*(spawn_pos - forward * APPROACH)[[0, 2]])
        edge_list.append((end_node, climb_node, rsnv.EDGE_JUMP, float(math.dist(end, node_pos[climb_node])), False))
        edge_list.append((approach_node, start_node, rsnv.EDGE_DROP,
                          float(math.dist(node_pos[approach_node], spawn_pos)), False))
        entry_node = soldier_node(soldier, spawn['entry'], ENTRY_RADIUS + 2.0)
        offm.append((rsnv.OFFM_BOARD_VEHICLE, team_mask(spawn['team']), vc.CLASS_IDS['plane'], start_node,
                     *[float(v) for v in spawn['entry']], float(spawn['yaw']), ENTRY_RADIUS,
                     entry_node if entry_node is not None else 0xFFFFFFFF))
        lanes.append({'spawn': spawn['index'], 'vehicle': spawn['vehicle'], 'team': spawn['team'],
                      'start node': start_node, 'end node': end_node,
                      'climb-out node': climb_node, 'approach node': approach_node,
                      'heading offset deg': choice['offset'], 'start ahead m': choice['start'], 'length m': run,
                      'centre line clear': choice['clear'], 'roll carried to lift-off': choice['carried'],
                      'highest within the half wingspan m': round(float(choice['wing']), 1),
                      'straight lane blocked': choice['straight blocked'], 'source': choice['source']})
        board.append({'spawn': spawn['index'], 'team': spawn['team'], 'entry': [round(v, 2) for v in spawn['entry']],
                      'node': start_node, 'soldier node': entry_node,
                      'soldier island': int(soldier.node_island[entry_node]) if entry_node is not None else None})
    # parachute drops: over bases and flags, and over each soldier island that is no carrier
    drops = []
    targets = [(o.get('name'), o['position']) for o in getattr(level, 'objectives', []) if o.get('position')]
    for k, island in enumerate(soldier.islands):          # island rows: (node count, a node of it, flags, ...)
        if island[0] >= DROP_ISLAND_MIN and not island[2] & rsnv.ISLD_CARRIER and k != 0:
            targets.append((f'island {k}', soldier.node_pos[island[1]].tolist()))
    for name, target in targets:
        dist = np.hypot(soldier.node_pos[:, 0] - target[0], soldier.node_pos[:, 2] - target[2])
        ground_node = int(np.argmin(dist))
        island_id = int(soldier.node_island[ground_node])
        if soldier.islands and soldier.islands[island_id][2] & rsnv.ISLD_CARRIER:
            continue                                     # no drops on carriers
        ground = soldier.node_pos[ground_node]
        cruise_node = nearest_cruise(ground[0], ground[2])
        offm.append((OFFM_DROP, 3, vc.CLASS_IDS['plane'], cruise_node,
                     float(ground[0]), float(ground[1]), float(ground[2]), 0.0,
                     float(np.hypot(node_pos[cruise_node, 0] - ground[0], node_pos[cruise_node, 2] - ground[2])),
                     ground_node))
        drops.append({'target': name, 'soldier node': ground_node, 'soldier island': island_id,
                      'cruise node': cruise_node,
                      'height over ground': round(float(node_pos[cruise_node, 1] - ground[1]), 1)})
    turned = sum(1 for lane in lanes if lane['straight lane blocked'])
    not_cleared = sum(1 for lane in lanes if not lane['centre line clear'])
    rolling_off = sum(1 for lane in lanes if not lane['roll carried to lift-off'])
    log(f'  {len(lanes)} take-off lanes ({turned} with the spawn heading blocked, {not_cleared} not cleared, '
        f'{rolling_off} rolling off the ground before lift-off), {len(drops)} parachute drops')
    for lane in lanes:
        if lane['straight lane blocked'] or not lane['roll carried to lift-off']:
            log(f'    spawn {lane["spawn"]} {lane["vehicle"]}: heading {lane["heading offset deg"]:+.1f} deg, '
                f'start +{lane["start ahead m"]:g} m, {lane["length m"]:g} m, clear {lane["centre line clear"]}, '
                f'carried {lane["roll carried to lift-off"]}, '
                f'wing obstacle {lane["highest within the half wingspan m"]} m')
    sources = (('soldier rsnv', soldier_path), ('all_tris.npy', os.path.join(files['objects'], 'all_tris.npy')),
               ('heightfield.res', hf_path), ('navkit.json', os.path.join(files['navkit'], 'navkit.json')))
    meta = {'sources': {k: {'path': v, 'sha256': rsnvgrid1943.sha256(v)} for k, v in sources if os.path.isfile(v)},
            'class parameters': {k: list(v) if isinstance(v, tuple) else v for k, v in vc.CLASSES['plane'].items()},
            'grid': {'pitch': pitch, 'x0': x0, 'z0': z0, 'nx': nx, 'nz': nz,
                     'floor range': [float(floor.min()), float(floor.max())]},
            'edge kinds': 'walk: fly; jump: take-off climb-out; drop: landing approach', 'lanes': lanes,
            'boarding': board, 'drops': drops}
    header = {'sea_level': sea, 'radius': radius, 'height': vc.value('plane', 'half_length') * 2, 'step': 0.5,
              'water_depth': 0.0, 'sha256': meta['sources']['soldier rsnv']['sha256']}
    stats = rsnvgrid1943.write_world(lv, 'plane', sets, node_list, edge_list, offm, files['rsnv'],
                                     state['sector_offsets'], header, meta, log=log)
    vc.record(lv, 'worlds', dict(want, sha256=nj.sha256(files['rsnv'])), 'plane', soldier_path=soldier_path)
    stats.update({'lanes': len(lanes), 'drops': len(drops), 'seconds': round(time.time() - started, 1)})
    log(f'{lv}: plane world {files["rsnv"]} ({stats["seconds"]:.0f} s)')
    return files['rsnv'], stats


def choose_lane(obs, p0, yaw0):
    """The take-off lane of a plane spawn: the spawn's own heading and takeoff_run [H] when clear (lane_check on the
    lane's nodes as the world will hold them: 2 m cell centres); else the first clear candidate by (start ahead,
    length longest, heading offset smallest, then the least wing obstacle); flagged when none is clear."""
    def geometry(ahead, length, offset):
        """(yaw, lane start, lane end) of a lane starting ahead m in front of the spawn, offset deg off its heading."""
        yaw = yaw0 + math.radians(offset)
        forward = np.array([math.sin(yaw), 0.0, math.cos(yaw)])
        return yaw, p0 + forward * ahead, p0 + forward * (ahead + length)
    run = vc.value('plane', 'takeoff_run')
    y = float(p0[1])
    _yaw, lane_start, lane_end = geometry(0.0, run, 0.0)
    clear, carried, wing = lane_check(obs, snap_lane(lane_start), snap_lane(lane_end), y)
    if clear and carried:
        return {'yaw': yaw0, 'offset': 0.0, 'start': 0.0, 'length': run, 'clear': True, 'carried': True, 'wing': wing,
                'straight blocked': False, 'source': '[H] the spawn heading x takeoff_run; clear of the collision [O]'}
    best = None
    for headings in (HEADINGS, HEADINGS_WIDE):
        best = search_lane(obs, p0, yaw0, y, geometry, headings)
        if best is not None:
            if headings is HEADINGS_WIDE:
                best['source'] = (f'[H] the plane turns {best["offset"]:+.1f} deg on the ground first: no clear lane '
                                  f'within 20 deg of the spawn heading; clear of the collision [O]')
            return best
    return {'yaw': yaw0, 'offset': 0.0, 'start': 0.0, 'length': run, 'clear': clear, 'carried': carried, 'wing': wing,
            'straight blocked': not clear,
            'source': ('[H] the spawn heading x takeoff_run, centre line clear [O]; but the roll leaves the ground before '
                       'lift-off (no lane at any heading is both clear and carried): an in-game check' if clear else
                       '[U] no clear lane at any heading, 75-120 m, within 16 m of the spawn: kept, blocked')}


def search_lane(obs, p0, yaw0, y, geometry, headings):
    best = None                                          # (sort key, lane)
    for ahead in STARTS:
        for length in LENGTHS:
            for offset in headings:
                yaw, lane_start, lane_end = geometry(ahead, length, offset)
                clear, carried, wing = lane_check(obs, snap_lane(lane_start), snap_lane(lane_end), y)
                if clear and carried:
                    key = (ahead, -length, abs(offset), wing)
                    if best is None or key < best[0]:
                        best = (key, {'yaw': yaw, 'offset': float(offset), 'start': ahead, 'length': length,
                                      'clear': True, 'carried': True, 'wing': wing, 'straight blocked': True,
                                      'source': '[H] turned / shortened / moved off the spawn heading to clear the '
                                                'collision [O] on the deck or field [O]'})
            if best is not None and best[0][0] == ahead and best[0][1] == -length:
                break                                    # the longest clear length at this start is found
        if best is not None:
            break
    return best[1] if best is not None else None


def snap_lane(q):
    """A lane end as the world holds it: the 2 m lane cell's centre (on the lane cell grid of build())."""
    return (math.floor(q[0] / LANE_PITCH + 0.5) * LANE_PITCH, float(q[1]),
            math.floor(q[2] / LANE_PITCH + 0.5) * LANE_PITCH)


def team_mask(team):
    return {1: 1, 2: 2}.get(team, 3)


def soldier_node(S, p, reach, dy=3.5):
    return rsnvgrid1943.soldier_node(S, p, reach, dy)


def main(argv):
    parser = argparse.ArgumentParser(prog='airnav1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('level')
    parser.add_argument('--soldier')
    args = parser.parse_args(argv[1:])
    soldier_path = args.soldier or paths(args.level)['soldier']
    if not os.path.isfile(soldier_path):
        raise FileNotFoundError(f'{args.level}: no soldier world (missing {soldier_path}): '
                                f'run navexport1943.py {args.level}')
    build(args.level, soldier_path)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('airnav1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
