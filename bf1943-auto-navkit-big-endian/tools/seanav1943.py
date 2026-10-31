r"""NavKit (big-endian): navigation for BF1943's landing craft (LCVP) - a sea grid with its route graph, the carrier
launch lanes and the beach landing zones, exported to RSNV as its own world (<level>.lcvp.rsnv), with the
transfers to and from the soldier world (<level>.rsnv).

  seanav1943.py <level> [--soldier <level.rsnv>]

What it builds (parameters: vehicleclasses1943.CLASSES['lcvp']):
  - the sea grid: 2 m cells over the land, the carriers and the craft spawns plus a 250 m margin; a cell's depth is
    the sea level (navkit.json) less the terrain height (core terrain.py) [O]; a cell is blocked by collision
    (all_tris.npy, every object) that crosses the waterline band (sea - draft - 0.3 .. sea + 2 m): carriers, wrecks,
    piers, rocks [O]; the boat's centre may stand where the water is at least the draft deep, no collision lies within
    the radius and no ground shallower than the draft lies within the half-width;
  - route nodes: one per 4 x 4 cell block (8 m) at the cell farthest from shore and collision, edges between nodes of
    neighbouring blocks (including knight moves) with a straight line over navigable cells, pieces of one water
    body joined along the grid's shortest path (groundnav1943.join_pieces' rule on cells);
  - launch lanes: a craft spawned on a carrier deck (higher than sea + 5 m) drives forward along its own heading on
    the deck [O: the Wake and Iwo Jima craft face the carriers' sterns] until the deck ends; a lane of cells at deck
    height (3 cells wide), a node at the spawn and one at the deck's end, and a one-way DROP edge from there to the
    sea node where the craft lands (the deck end + half the craft's length + 2 m, at sea level). That a craft leaves
    the deck this way is [H]: the level shows lcvp_shell objects ("invisible boat wall", destructible) around each
    deck craft [O]; an in-game look is needed;
  - landing zones: navigable cells within the bow distance (5.5 m [O], the exit points at the ramp) of beach ground
    (depth at most beach_depth, no collision), one per 25 m of shore; each zone's ALIGHT transfer goes to the
    soldier node nearest its bow point, which must lie on the soldier world's main island and be the nearest of any
    island (Iwo Jima's no-go strip behind the ForbidPathfinding boardwalk is its own island: zones there are
    dropped), with no ForbidPathfinding collision between the bow and that node;
  - transfers (OFFM): BOARD_VEHICLE at each craft spawn (the soldier-side point: the driver's entry point, radius
    EntryRadius 8 m [O]; aux: the soldier node nearest it); ALIGHT_ZONE at each landing node (the soldier node's
    position; aux: its index); LCVP_LAUNCH at each lane's deck end (the drop point; yaw: the heading).
"""
import argparse
import collections
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
import soldier1943  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import game1943, rsnv, rsnvtrip, terrain  # noqa: E402

MARGIN = 250.0               # m of sea around the land, carriers and craft spawns
BAND_BELOW = 0.3             # m under the draft: collision there still touches the hull
BAND_ABOVE = 2.0             # m over the sea level: collision there touches the hull
ZONE_SPACING = 25.0          # m between landing zones along a shore
ALIGHT_REACH = 12.0          # m from the bow point to the soldier node a zone unloads onto
FORBID_CLEAR = 1.5           # m: no ForbidPathfinding collision this close to the bow -> soldier node line
DECK_MIN = 5.0               # m over the sea: a craft spawn this high is on a deck
DECK_GAP = 6.0               # m without deck along a lane: the deck has ended
LAUNCH_GAP = 2.0             # m past the deck end + half the craft's length: where it lands in the water
OFFM_ALIGHT_ZONE, OFFM_LCVP_LAUNCH = rsnvgrid1943.OFFM_ALIGHT_ZONE, 3      # design 1.3's OFFM kinds



def paths(lv):
    W = game1943.level_work(lv)
    return {'work': W, 'objects': os.path.join(W, 'objects'), 'navkit': os.path.join(W, 'navkit'),
            'rsnv': os.path.join(W, 'navkit', f'{lv}.lcvp.rsnv'), 'soldier': os.path.join(W, 'navkit', f'{lv}.rsnv')}


def craft_spawns(lv, game=None):
    from bfbc2core import level1943, vehicles1943
    level = level1943.load(lv, game=game, log=lambda *a: None)
    game_level = game1943.Game(game1943.find_game(game), lv)
    out = []
    for entity in level.entities:
        if entity.category != 'vehicle spawn' or entity.position is None:
            continue
        part = vehicles1943.vehicle_partition(entity)
        name = part.rsplit('/', 1)[-1] if part else ''
        if vehicles1943.vehicle_class(name) != 'boat':
            continue
        matrix = np.asarray(entity.matrix, float)
        forward = matrix[2, :3]
        out.append({'vehicle': name, 'team': entity.team, 'pos': [float(v) for v in entity.position],
                    'fwd': (forward / max(np.linalg.norm(forward[[0, 2]]), 1e-9)).tolist(),
                    'yaw': math.atan2(forward[0], forward[2]),
                    'entries': vehicles1943.entry_points(game_level, entity), 'index': entity.index})
    return out, level


def rasterise_band(T, lo, hi, x0, z0, nx, nz, pitch):
    """Bool (nz, nx): cells whose centre lies under a triangle crossing the height band [lo, hi] (a triangle smaller
    than a cell marks the cell of its centroid). T: triangles (n, 3 corners, xyz)."""
    import navsample1943
    tri_ylo, tri_yhi = T[:, :, 1].min(1), T[:, :, 1].max(1)
    T = T[(tri_ylo <= hi) & (tri_yhi >= lo)].astype(np.float64)
    out = np.zeros((nz, nx), bool)
    if not len(T):
        return out, 0
    centroid = T.mean(1)
    cent_i = np.round((centroid[:, 0] - x0) / pitch).astype(np.int64)
    cent_j = np.round((centroid[:, 2] - z0) / pitch).astype(np.int64)
    on_grid = (cent_i >= 0) & (cent_j >= 0) & (cent_i < nx) & (cent_j < nz)
    out[cent_j[on_grid], cent_i[on_grid]] = True
    # triangles two cells or more across: every cell centre under them
    lo_i = np.floor((T[:, :, 0].min(1) - x0) / pitch).astype(np.int64)
    hi_i = np.ceil((T[:, :, 0].max(1) - x0) / pitch).astype(np.int64)
    lo_j = np.floor((T[:, :, 2].min(1) - z0) / pitch).astype(np.int64)
    hi_j = np.ceil((T[:, :, 2].max(1) - z0) / pitch).astype(np.int64)
    big = np.nonzero((hi_i - lo_i >= 2) | (hi_j - lo_j >= 2))[0]
    for t in big.tolist():
        i0, i1 = max(lo_i[t], 0), min(hi_i[t], nx - 1)
        j0, j1 = max(lo_j[t], 0), min(hi_j[t], nz - 1)
        if i1 < i0 or j1 < j0:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        ii, jj = ii.ravel(), jj.ravel()
        _u, _v, _w, under = navsample1943.inside(T[t:t + 1, :, [0, 2]], x0 + ii * pitch, z0 + jj * pitch)
        out[jj[under[0]], ii[under[0]]] = True
    return out, len(T)


def dilate(mask, cells):
    """Bool mask grown by a disc of `cells` radius."""
    out = mask.copy()
    reach = int(math.ceil(cells))
    nz, nx = mask.shape
    for dj in range(-reach, reach + 1):
        for di in range(-reach, reach + 1):
            if (di or dj) and di * di + dj * dj <= cells * cells + 1e-9:
                src = mask[max(0, -dj):nz - max(0, dj), max(0, -di):nx - max(0, di)]
                out[max(0, dj):nz + min(0, dj), max(0, di):nx + min(0, di)] |= src
    return out


def distance_field(mask, cap):
    """Chessboard distance (cells) from each True cell to the nearest False, capped."""
    dist = np.where(mask, cap, 0).astype(np.int64)
    inside = mask.copy()
    for r in range(1, cap):
        # erode by one cell: a cell stays inside when its four neighbours are inside
        eroded = inside.copy()
        eroded[1:, :] &= inside[:-1, :]
        eroded[:-1, :] &= inside[1:, :]
        eroded[:, 1:] &= inside[:, :-1]
        eroded[:, :-1] &= inside[:, 1:]
        dist = np.where(inside & ~eroded, np.minimum(dist, r), dist)
        inside = eroded
        if not inside.any():
            break
    return dist


def cell_line(nav, a, b):
    """Whether the straight line between cells a (i, j) and b crosses only navigable cells (4-connected steps)."""
    (ia, ja), (ib, jb) = a, b
    steps = max(abs(ib - ia), abs(jb - ja))
    if steps == 0:
        return bool(nav[ja, ia])
    prev_i, prev_j = ia, ja
    for step in range(1, steps + 1):
        ti = int(round(ia + (ib - ia) * step / steps))
        tj = int(round(ja + (jb - ja) * step / steps))
        # a diagonal step passes through either side cell
        if ti != prev_i and tj != prev_j and not (nav[prev_j, ti] or nav[tj, prev_i]):
            return False
        if not nav[tj, ti]:
            return False
        prev_i, prev_j = ti, tj
    return True


def build(lv, soldier_path=None, game=None, log=print):
    """The LCVP world: (rsnv path, stats), or (None, stats) when the level has none (no water, no craft spawns: stamped
    absent). Reads the level from the game copy it was prepared from (2026-10-30: game=None read the default copy)."""
    files = paths(lv)
    t0 = time.time()
    st = nj.read(lv)
    game = vc.prepared_game(st, game, lv)
    want = vc.inputs(st, lv, 'lcvp')
    if not soldier1943.has_water(st):            # prepare found no water mesh (2026-10-30: float(None) raised)
        # absent whatever the soldier world: the level's data says so (2026-10-30: a current soldier export was needed)
        why = 'no water on the level (navkit.py prepare found no water mesh)'
        log(f'{lv}: {why}: no LCVP world')
        vc.absent(lv, 'lcvp', want, files['rsnv'], why, log)
        return None, {'spawns': 0, 'note': why}
    soldier_path = soldier_path or files['soldier']
    want['soldier'] = vc.soldier_world(st, lv, soldier_path)
    vc.drop(lv, 'worlds', 'lcvp')
    pitch = vc.value('lcvp', 'pitch')
    draft = vc.value('lcvp', 'draft')
    radius = vc.value('lcvp', 'radius')
    half_w = vc.value('lcvp', 'half_width')
    beach = vc.value('lcvp', 'beach_depth')
    bow = vc.value('lcvp', 'bow')
    hf_path = os.path.join(files['objects'], 'heightfield.res')
    hf = terrain.read(hf_path)
    sea = soldier1943.sea_level(st, hf)
    spawns, _level = craft_spawns(lv, game)
    S = rsnv.load(soldier_path)
    if not spawns:
        log(f'{lv}: no landing craft spawns: no LCVP world')
        vc.absent(lv, 'lcvp', want, files['rsnv'], 'no landing craft spawns on the level', log)
        return None, {'spawns': 0}
    # the box: land, carriers (objects holding soldier spawns), craft spawns
    land = hf.heights > sea - draft
    land_j, land_i = np.nonzero(land)
    cell_x, cell_z = hf.cell
    lo = np.array([hf.min[0] + land_i.min() * cell_x, hf.min[2] + land_j.min() * cell_z])
    hi = np.array([hf.min[0] + (land_i.max() + 1) * cell_x, hf.min[2] + (land_j.max() + 1) * cell_z])
    must_hold = np.array([s['pos'] for s in spawns])[:, [0, 2]]         # x, z the box must hold
    if S.islands:
        on_carrier = [k for k in range(len(S.node_pos)) if S.islands[int(S.node_island[k])][2] & rsnv.ISLD_CARRIER]
        carrier_nodes = S.node_pos[on_carrier]
    else:
        carrier_nodes = np.zeros((0, 3))
    if len(carrier_nodes):
        must_hold = np.concatenate([must_hold, carrier_nodes[:, [0, 2]]])
    lo, hi = np.minimum(lo, must_hold.min(0)) - MARGIN, np.maximum(hi, must_hold.max(0)) + MARGIN
    lo = np.maximum(lo, [hf.min[0], hf.min[2]])
    hi = np.minimum(hi, [hf.max[0] - pitch, hf.max[2] - pitch])
    x0, z0 = math.floor(lo[0] / pitch) * pitch, math.floor(lo[1] / pitch) * pitch
    nx, nz = int((hi[0] - x0) / pitch) + 1, int((hi[1] - z0) / pitch) + 1
    I, J = np.meshgrid(np.arange(nx), np.arange(nz))            # the sea grid's cell indices, (nz, nx)
    X, Z = x0 + I * pitch, z0 + J * pitch                       # ... and their centres
    depth = sea - hf.height(X, Z)
    T = np.load(os.path.join(files['objects'], 'all_tris.npy'))     # collision triangles (n, 3 corners, xyz)
    blocked, n_band = rasterise_band(T, sea - draft - BAND_BELOW, sea + BAND_ABOVE, x0, z0, nx, nz, pitch)
    log(f'{lv}: sea grid {nx} x {nz} cells of {pitch} m (x {x0:.0f}..{x0 + nx * pitch:.0f}, z {z0:.0f}..'
        f'{z0 + nz * pitch:.0f}); {n_band} triangles cross the waterline band, {int(blocked.sum())} cells blocked')
    water = depth >= draft
    shallow_ground = ~water
    nav = water & ~dilate(blocked, radius / pitch) & ~dilate(shallow_ground, half_w / pitch)
    # the boat's own water bodies: keep those a craft spawn or a launch reaches
    beach_cells = (depth <= beach) & (depth > -1.0) & ~dilate(blocked, 1.0 / pitch)
    log(f'  navigable {int(nav.sum())} cells ({nav.sum() * pitch * pitch / 1e6:.2f} km2), '
        f'beach {int(beach_cells.sum())}')
    # launch lanes
    lanes = []
    deck_spawns = [s for s in spawns if s['pos'][1] - sea >= DECK_MIN]
    if deck_spawns:
        deck_xz = np.array([s['pos'] for s in deck_spawns])[:, [0, 2]]
        tri_centres = T[:, :, [0, 2]].mean(1)
        near = np.zeros(len(T), bool)
        for spawn_xz in deck_xz:
            near |= np.hypot(tri_centres[:, 0] - spawn_xz[0], tri_centres[:, 1] - spawn_xz[1]) < 400.0
        deck_tris = T[near & (T[:, :, 1].max(1) >= sea + DECK_MIN - 3.0)]
    for spawn in deck_spawns:
        fwd = np.array(spawn['fwd'], float)
        fwd[1] = 0.0
        fwd /= np.linalg.norm(fwd)
        start = np.array(spawn['pos'], float)
        deck_y = start[1]
        end = None
        along = 0.0
        last_deck = 0.0
        while along < 300.0:
            point = start + fwd * along
            # collision top within 1.5 m below the craft and 1 m above it, at the lane point or 1 m around it (the
            # craft's cradle leaves holes in the deck's collision under it)
            if any(deck_tops(deck_tris, point[0] + ox, point[2] + oz, deck_y - 1.5, deck_y + 1.0) is not None
                   for ox, oz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))):
                last_deck = along
            elif along - last_deck > DECK_GAP:
                end = last_deck + pitch
                break
            along += pitch
        if end is None:
            log(f'  craft {spawn["index"]} at {np.round(start, 1).tolist()}: no deck end within 300 m, no launch lane')
            continue
        deck_end = start + fwd * max(end - pitch, 0.0)
        land_pt = start + fwd * (end + vc.value('lcvp', 'half_length') + LAUNCH_GAP)
        lanes.append({'spawn': spawn, 'deck_end': deck_end.tolist(),
                      'land': [float(land_pt[0]), sea, float(land_pt[2])], 'length': round(end, 1)})
    log(f'  {len(lanes)} launch lanes (deck lengths {[lane["length"] for lane in lanes]})')
    # graph over sea cells
    nav_idx = np.full((nz, nx), -1, np.int64)
    nav_cells = np.nonzero(nav.ravel())[0]
    nav_idx.ravel()[nav_cells] = np.arange(len(nav_cells))
    links = rsnvgrid1943.grid_links(nav_idx)
    sea_set = rsnvgrid1943.CellSet(pitch, x0, z0, I.ravel()[nav_cells], J.ravel()[nav_cells],
                                   np.full(len(nav_cells), sea), links, name=f'{lv} sea')
    # components of the navigable water (straight links), to keep the craft's water bodies
    pairs = np.array([(k, n) for (k, _di, _dj), n in links.items()], np.int64).reshape(-1, 2)
    import groundnav1943
    water_body = groundnav1943.components(len(nav_cells), pairs)
    sea_i = sea_set.i
    sea_j = sea_set.j

    def nearest_cell(x, z, maxd=60.0):
        """(sea cell nearest (x, z), its distance), the cell None when farther than maxd."""
        dist2 = (x0 + sea_i * pitch - x) ** 2 + (z0 + sea_j * pitch - z) ** 2
        k = int(np.argmin(dist2))
        dist = math.sqrt(float(dist2[k]))
        return (k, dist) if dist2[k] <= maxd * maxd else (None, dist)
    kept_bodies = set()
    for lane in lanes:
        k, dist = nearest_cell(lane['land'][0], lane['land'][2])
        lane['water_cell'] = k
        lane['water_dist'] = round(dist, 1)
        if k is not None:
            kept_bodies.add(int(water_body[k]))
    for spawn in spawns:
        if spawn['pos'][1] - sea < DECK_MIN:
            k, dist = nearest_cell(spawn['pos'][0], spawn['pos'][2], 20.0)
            spawn['water_cell'] = k
            if k is not None:
                kept_bodies.add(int(water_body[k]))
    kept = np.isin(water_body, list(kept_bodies))
    log(f'  kept {len(kept_bodies)} water bodies, {int(kept.sum())} cells ({kept.sum() * pitch * pitch / 1e6:.2f} km2)')
    kept_nav = np.zeros_like(nav)
    kept_nav[sea_set.j[kept], sea_set.i[kept]] = True
    # rebuild the set on the kept cells
    nav_idx = np.full((nz, nx), -1, np.int64)
    kept_nav_cells = np.nonzero(kept_nav.ravel())[0]
    nav_idx.ravel()[kept_nav_cells] = np.arange(len(kept_nav_cells))
    links = rsnvgrid1943.grid_links(nav_idx)
    sea_set = rsnvgrid1943.CellSet(pitch, x0, z0, I.ravel()[kept_nav_cells], J.ravel()[kept_nav_cells],
                                   np.full(len(kept_nav_cells), sea), links, name=f'{lv} sea')
    old_to_new = {int(old): int(nav_idx.ravel()[nav_cells[old]]) for old in np.nonzero(kept)[0].tolist()}
    for lane in lanes:
        lane['water_cell'] = old_to_new.get(lane['water_cell']) if lane['water_cell'] is not None else None
    for spawn in spawns:
        if spawn.get('water_cell') is not None:
            spawn['water_cell'] = old_to_new.get(spawn['water_cell'])
    # route nodes: per 4 x 4 block the cell farthest from the shore
    clearance = distance_field(kept_nav, 12)
    spacing = int(vc.value('lcvp', 'node_spacing'))
    cell_i, cell_j = sea_set.i, sea_set.j
    block_i, block_j = cell_i // spacing, cell_j // spacing
    off_centre = ((cell_i - (block_i * spacing + spacing / 2 - 0.5)) ** 2
                  + (cell_j - (block_j * spacing + spacing / 2 - 0.5)) ** 2)
    # per block (block_i, block_j): the most clearance first, then the nearest the block's centre
    order = np.lexsort((off_centre, -clearance[cell_j, cell_i], block_j, block_i))
    block = np.stack([block_i[order], block_j[order]], 1)
    first_in_block = np.ones(len(order), bool)
    first_in_block[1:] = np.any(block[1:] != block[:-1], axis=1)
    node_cells = order[first_in_block].tolist()
    node_of_block = {(int(a), int(b)): k for (a, b), k in zip(block[first_in_block].tolist(), node_cells)}
    edges = set()
    for (a, b), k in node_of_block.items():
        for da, db in ((1, 0), (0, 1), (1, 1), (1, -1)):
            other = node_of_block.get((a + da, b + db))
            if other is not None and cell_line(kept_nav, (int(cell_i[k]), int(cell_j[k])),
                                               (int(cell_i[other]), int(cell_j[other]))):
                edges.add((min(k, other), max(k, other)))
    log(f'  {len(node_cells)} sea nodes, {len(edges)} edges')
    # extra nodes: craft water cells, lane landings, landing zones
    extra = set()
    for lane in lanes:
        if lane['water_cell'] is not None:
            extra.add(lane['water_cell'])
    for spawn in spawns:
        if spawn.get('water_cell') is not None:
            extra.add(spawn['water_cell'])
    zones = landing_zones(lv, sea_set, kept_nav, depth, beach_cells, S, bow, pitch, x0, z0, log)
    for zone in zones:
        extra.add(zone['cell'])
    node_cells, edges = connect_extra(sea_set, kept_nav, node_cells, edges, extra)
    node_cells, edges = join_cell_pieces(sea_set, kept_nav, node_cells, edges, log)
    # deck lanes: a cell set per lane (3 cells wide at deck height), a node at each end
    lane_sets = []
    for lane in lanes:
        spawn = lane['spawn']
        cells = {}                                      # (i, j) -> deck height
        start = np.array(spawn['pos'], float)
        fwd = np.array(spawn['fwd'], float)
        fwd[1] = 0
        fwd /= np.linalg.norm(fwd)
        side = np.array([fwd[2], 0.0, -fwd[0]])
        dist = float(np.linalg.norm((np.array(lane['deck_end']) - start)[[0, 2]]))
        for along in np.arange(0.0, dist + 1e-6, pitch / 2):
            for offset in (-pitch, 0.0, pitch):
                point = start + fwd * along + side * offset
                ci = int(round((point[0] - x0) / pitch))
                cj = int(round((point[2] - z0) / pitch))
                cells.setdefault((ci, cj), start[1])
        lane_cells = sorted(cells)
        cell_index = {c: k for k, c in enumerate(lane_cells)}
        lane_links = {}
        for (ci, cj), k in cell_index.items():
            for di, dj in rsnvgrid1943.STRAIGHT:
                n = cell_index.get((ci + di, cj + dj))
                if n is not None:
                    lane_links[(k, di, dj)] = n
        lane_set = rsnvgrid1943.CellSet(pitch, x0, z0, [c[0] for c in lane_cells], [c[1] for c in lane_cells],
                                        [cells[c] for c in lane_cells], lane_links, name=f'lane {spawn["index"]}')
        start_cell = (int(round((start[0] - x0) / pitch)), int(round((start[2] - z0) / pitch)))
        deck_end = np.array(lane['deck_end'])
        end_cell = (int(round((deck_end[0] - x0) / pitch)), int(round((deck_end[2] - z0) / pitch)))
        if end_cell in cell_index:
            end_k = cell_index[end_cell]
        else:                                           # the lane cell nearest the deck end
            end_k = min(cell_index.items(),
                        key=lambda item: (item[0][0] - end_cell[0]) ** 2 + (item[0][1] - end_cell[1]) ** 2)[1]
        lane['cells'] = (cell_index[start_cell], end_k)
        lane_sets.append(lane_set)
    # all nodes: sea set 0, lanes 1..
    sets = [sea_set] + lane_sets
    node_list = [(0, k) for k in node_cells]
    node_of_cell = {k: n for n, k in enumerate(node_cells)}
    edge_list = [(node_of_cell[a], node_of_cell[b], rsnv.EDGE_WALK, None, True) for a, b in sorted(edges)]
    offm = []
    lane_info = []
    for lane_no, lane in enumerate(lanes):
        deck_node = len(node_list)
        node_list.append((lane_no + 1, lane['cells'][0]))
        end_node = len(node_list)
        node_list.append((lane_no + 1, lane['cells'][1]))
        # forward only: the craft does not drive back up
        edge_list.append((deck_node, end_node, rsnv.EDGE_WALK, None, False))
        if lane['water_cell'] is not None:
            water_node = node_of_cell[lane['water_cell']]
            deck_end = np.array(lane['deck_end'])
            water_xz = sea_set.xz(lane['water_cell'])
            drop_run = float(math.hypot(water_xz[0] - deck_end[0], water_xz[1] - deck_end[2]))
            edge_list.append((end_node, water_node, rsnv.EDGE_DROP, drop_run, False))
            offm.append((OFFM_LCVP_LAUNCH, team_mask(lane['spawn']['team']), vc.CLASS_IDS['lcvp'], end_node,
                         float(lane['land'][0]), float(lane['land'][1]), float(lane['land'][2]),
                         float(lane['spawn']['yaw']), float(vc.value('lcvp', 'half_length')), water_node))
        lane['nodes'] = (deck_node, end_node)
        lane_info.append({'spawn': lane['spawn']['index'], 'team': lane['spawn']['team'], 'deck node': deck_node,
                          'deck end node': end_node, 'water node': node_of_cell.get(lane['water_cell']),
                          'deck length': lane['length'], 'landing point': [round(v, 2) for v in lane['land']],
                          'snap to water': lane['water_dist']})
    # boarding transfers: every craft spawn
    board = []
    for spawn in spawns:
        lane = next((candidate for candidate in lanes if candidate['spawn'] is spawn), None)
        if lane is not None:
            node = lane['nodes'][0]
        elif spawn.get('water_cell') is not None:
            node = node_of_cell[spawn['water_cell']]
        else:
            node = None
        entry = next((e for e in spawn['entries'] if e.get('order', 0) == 0), None)    # the driver's
        if not entry:
            entry = {'point': spawn['pos'], 'radius': vc.value('lcvp', 'radius') + 4.0}
        soldier_n = soldier_node(S, entry['point'], entry['radius'] + 2.0)
        offm.append((rsnv.OFFM_BOARD_VEHICLE, team_mask(spawn['team']), vc.CLASS_IDS['lcvp'],
                     node if node is not None else 0xFFFFFFFF, *[float(v) for v in entry['point']],
                     float(spawn['yaw']), float(entry['radius']), soldier_n if soldier_n is not None else 0xFFFFFFFF))
        board.append({'spawn': spawn['index'], 'team': spawn['team'], 'pos': [round(v, 2) for v in spawn['pos']],
                      'node': node, 'entry': [round(v, 2) for v in entry['point']], 'radius': entry['radius'],
                      'soldier node': soldier_n,
                      'soldier island': int(S.node_island[soldier_n]) if soldier_n is not None else None,
                      'on deck': lane is not None})
    # alight transfers
    for zone in zones:
        node = node_of_cell[zone['cell']]
        unload = zone['unload']
        offm.append((OFFM_ALIGHT_ZONE, 3, vc.CLASS_IDS['lcvp'], node, float(unload[0]), float(unload[1]),
                     float(unload[2]), float(zone['yaw']), float(zone['reach']), int(zone['soldier'])))
        zone['node'] = node
    flags_of_node = [0] * len(node_list)
    for zone in zones:
        flags_of_node[zone['node']] |= rsnv.NAUX_SHALLOW               # a landing node: the craft grounds here
    sources = (('soldier rsnv', soldier_path), ('all_tris.npy', os.path.join(files['objects'], 'all_tris.npy')),
               ('heightfield.res', hf_path), ('navkit.json', os.path.join(files['navkit'], 'navkit.json')))
    meta = {'sources': {k: {'path': v, 'sha256': rsnvgrid1943.sha256(v)} for k, v in sources if os.path.isfile(v)},
            'class parameters': {k: list(v) if isinstance(v, tuple) else v for k, v in vc.CLASSES['lcvp'].items()},
            'grid': {'pitch': pitch, 'x0': x0, 'z0': z0, 'nx': nx, 'nz': nz}, 'lanes': lane_info, 'boarding': board,
            'landing zones': [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in zone.items() if k != 'cell'}
                              for zone in zones]}
    header = {'sea_level': sea, 'radius': radius, 'height': vc.value('lcvp', 'height'), 'step': 0.5,
              'water_depth': draft, 'sha256': meta['sources']['soldier rsnv']['sha256']}
    stats = rsnvgrid1943.write_world(lv, 'lcvp', sets, node_list, edge_list, offm, files['rsnv'], st['sector_offsets'],
                                     header, meta, node_flags=flags_of_node, log=log)
    vc.record(lv, 'worlds', dict(want, sha256=nj.sha256(files['rsnv'])), 'lcvp', soldier_path=soldier_path)
    stats.update({'lanes': len(lanes), 'landing zones': len(zones), 'boarding': len(board),
                  'seconds': round(time.time() - t0, 1)})
    log(f'{lv}: LCVP world {files["rsnv"]}: {stats["nodes"]} nodes, {len(lanes)} lanes, {len(zones)} landing zones, '
        f'{len(board)} boarding points ({stats["seconds"]:.0f} s)')
    return files['rsnv'], stats


def team_mask(team):
    return {1: 1, 2: 2}.get(team, 3)


_tops_cache = {}


def deck_tops(T, x, z, ylo, yhi):
    """The highest collision surface at (x, z) between ylo and yhi, or None. T: triangles (n, 3 corners, xyz)."""
    key = id(T)
    if key not in _tops_cache:
        _tops_cache.clear()
        _tops_cache[key] = (T[:, :, [0, 2]].min(1), T[:, :, [0, 2]].max(1), T[:, :, 1].min(1), T[:, :, 1].max(1))
    box_lo, box_hi, tri_ylo, tri_yhi = _tops_cache[key]       # per triangle: x-z box and height range
    candidates = np.nonzero((box_lo[:, 0] <= x) & (box_hi[:, 0] >= x) & (box_lo[:, 1] <= z) & (box_hi[:, 1] >= z)
                            & (tri_yhi >= ylo) & (tri_ylo <= yhi))[0]
    best = None
    for t in candidates.tolist():
        a, b, c = (T[t, 0, [0, 2]].astype(float), T[t, 1, [0, 2]].astype(float), T[t, 2, [0, 2]].astype(float))
        # barycentric (u, w) of (x, z) in the triangle's x-z projection
        v0, v1, v2 = b - a, c - a, np.array([x, z]) - a
        det = v0[0] * v1[1] - v0[1] * v1[0]
        if abs(det) < 1e-9:
            continue
        u = (v2[0] * v1[1] - v2[1] * v1[0]) / det
        w = (v0[0] * v2[1] - v0[1] * v2[0]) / det
        if u >= -1e-6 and w >= -1e-6 and u + w <= 1 + 1e-6:
            y = float(T[t, 0, 1] + u * (T[t, 1, 1] - T[t, 0, 1]) + w * (T[t, 2, 1] - T[t, 0, 1]))
            if ylo <= y <= yhi and (best is None or y > best):
                best = y
    return best


def soldier_node(S, p, reach, dy=3.5):
    """rsnvgrid1943.soldier_node: the rule the runtime resolves a transfer with again."""
    return rsnvgrid1943.soldier_node(S, p, reach, dy)


def landing_zones(lv, cs, nav, depth, beach_cells, S, bow, pitch, x0, z0, log):
    """Landing zones: navigable cells within the bow distance of beach ground, one per ZONE_SPACING, unloading onto
    the soldier main island (see the module's description)."""
    near_beach = dilate(beach_cells, (bow + 0.5) / pitch)
    candidates = np.nonzero(near_beach[cs.j, cs.i])[0]
    if not len(candidates):
        return []
    forbid_path = os.path.join(game1943.level_work(lv), 'objects', 'forbid_tris.npy')
    forbid_tris = np.load(forbid_path) if os.path.isfile(forbid_path) else np.zeros((0, 3, 3), np.float32)
    forbid_centres = forbid_tris.mean(1) if len(forbid_tris) else np.zeros((0, 3))
    beach_j, beach_i = np.nonzero(beach_cells)
    beach_x, beach_z = x0 + beach_i * pitch, z0 + beach_j * pitch
    main = 0
    carrier_islands = {k for k, r in enumerate(S.islands) if r[2] & rsnv.ISLD_CARRIER}
    zones, rejected = [], collections.Counter()
    taken = []
    # nearest first to the shore: shallower water first
    candidate_depth = depth[cs.j[candidates], cs.i[candidates]]
    for k in candidates[np.argsort(candidate_depth, kind='stable')].tolist():
        x, z = cs.xz(k)
        if any((x - a) ** 2 + (z - b) ** 2 < ZONE_SPACING ** 2 for a, b in taken):
            continue
        beach_dist2 = (beach_x - x) ** 2 + (beach_z - z) ** 2
        nearest_beach = int(np.argmin(beach_dist2))
        dirx, dirz = beach_x[nearest_beach] - x, beach_z[nearest_beach] - z
        length = math.hypot(dirx, dirz)
        if length < 1e-6:
            continue
        dirx, dirz = dirx / length, dirz / length
        bow_xz = (x + dirx * bow, z + dirz * bow)
        node_dist = np.hypot(S.node_pos[:, 0] - bow_xz[0], S.node_pos[:, 2] - bow_xz[1])
        node_dist = np.where(np.abs(S.node_pos[:, 1] - cs.y[k]) <= 3.0, node_dist, np.inf)
        soldier_n = int(np.argmin(node_dist))
        if not np.isfinite(node_dist[soldier_n]) or node_dist[soldier_n] > ALIGHT_REACH:
            rejected['no soldier ground within reach'] += 1
            continue
        island = int(S.node_island[soldier_n])
        if island != main or island in carrier_islands:
            rejected['nearest soldier ground not the main island'] += 1
            continue
        soldier_pos = S.node_pos[soldier_n]
        if len(forbid_centres):
            # ForbidPathfinding collision near the bow -> soldier node line: each triangle centre's distance to it
            ax, az, ex, ez = bow_xz[0], bow_xz[1], float(soldier_pos[0]), float(soldier_pos[2])
            vx, vz = ex - ax, ez - az
            length2 = max(vx * vx + vz * vz, 1e-9)
            t = np.clip(((forbid_centres[:, 0] - ax) * vx + (forbid_centres[:, 2] - az) * vz) / length2, 0, 1)
            line_dist = np.hypot(forbid_centres[:, 0] - (ax + t * vx), forbid_centres[:, 2] - (az + t * vz))
            if (line_dist <= FORBID_CLEAR).any():
                rejected['ForbidPathfinding collision between bow and soldier ground'] += 1
                continue
        # the unload point: the soldier cell nearest the bow point (the craft's ramp reach) from which a main-island
        # node is in sight (rsnvtrip.in_sight_node, the runtime's rule) - a point on the soldier world's cells, which
        # stays put when the soldier world is rebuilt; else the node's own position
        unload, unload_node = None, None
        located = S.locate(bow_xz[0], float(soldier_pos[1]), bow_xz[1], max_dist=ALIGHT_REACH, dy=3.0)
        if located is not None:
            area = S.areas[located[0]]
            ux, uz = area.x0 + located[1][0] * area.pitch, area.z0 + located[1][1] * area.pitch
            uy = S.ground(area, ux, uz)
            unload_node = rsnvtrip.in_sight_node(S, ux, uy, uz)
            if unload_node is not None and int(S.node_island[unload_node]) == main:
                unload = (float(ux), float(uy), float(uz))
        if unload is None:
            unload, unload_node = (float(soldier_pos[0]), float(soldier_pos[1]), float(soldier_pos[2])), soldier_n
            how = 'the soldier node itself'
        else:
            how = 'a soldier cell by the bow, a main-island node in sight'
        taken.append((x, z))
        zones.append({'cell': k, 'pos': [x, float(cs.y[k]), z], 'bow': [bow_xz[0], bow_xz[1]],
                      'yaw': math.atan2(dirx, dirz), 'soldier': unload_node,
                      'soldier pos': [float(v) for v in S.node_pos[unload_node]], 'unload': list(unload),
                      'unload is': how, 'reach': float(math.hypot(unload[0] - x, unload[2] - z)),
                      'depth': float(depth[cs.j[k], cs.i[k]])})
    log(f'  {len(zones)} landing zones; rejected candidates: {dict(rejected)}')
    return zones


def connect_extra(cs, nav, node_cells, edges, extra):
    """Adds the extra cells as nodes, each linked to every node within 2 blocks it has a straight line to."""
    node_cells = list(node_cells)
    have = set(node_cells)
    node_ij = np.stack([cs.i[node_cells], cs.j[node_cells]], 1)
    for k in sorted(extra):
        if k in have:
            continue
        cell = (int(cs.i[k]), int(cs.j[k]))
        dist = np.abs(node_ij - cell).max(1)
        near = np.nonzero(dist <= 10)[0]
        for t in near[np.argsort(dist[near], kind='stable')].tolist():
            other = node_cells[t]
            if cell_line(nav, cell, (int(cs.i[other]), int(cs.j[other]))):
                edges.add((min(k, other), max(k, other)))
        node_cells.append(k)
        have.add(k)
        node_ij = np.concatenate([node_ij, [cell]])
    return node_cells, edges


def join_cell_pieces(cs, nav, node_cells, edges, log):
    """Pieces of the sea graph on one water body joined along the grid's shortest path (as groundnav1943)."""
    import groundnav1943
    links = cs.links
    table = {}
    for (k, di, dj), n in links.items():
        table[(k, di, dj)] = n
    pairs = np.array([(k, n) for (k, _a, _b), n in links.items()], np.int64).reshape(-1, 2)
    body_of_cell = groundnav1943.components(cs.n, pairs)

    class G:
        pass
    grid = G()                                          # what join_pieces reads of a ground grid: i, j
    grid.i, grid.j = cs.i, cs.j

    def line(ka, kb):
        return [ka, kb] if cell_line(nav, (int(cs.i[ka]), int(cs.j[ka])), (int(cs.i[kb]), int(cs.j[kb]))) else None
    saved = groundnav1943.grid_line
    groundnav1943.grid_line = lambda _g, _t, a, b: line(a, b)
    try:
        nodes, edges = groundnav1943.join_pieces(grid, None, table, body_of_cell, node_cells, edges, log)
    finally:
        groundnav1943.grid_line = saved
    return nodes, edges


def main(argv):
    ap = argparse.ArgumentParser(prog='seanav1943.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level')
    ap.add_argument('--soldier', help='the soldier world (default work\\levels\\<level>\\navkit\\<level>.rsnv)')
    args = ap.parse_args(argv[1:])
    files = paths(args.level)
    soldier = args.soldier or files['soldier']
    if not os.path.isfile(soldier):
        raise FileNotFoundError(f'{args.level}: no soldier world (missing {soldier}): '
                                f'run navexport1943.py {args.level}')
    build(args.level, soldier)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('seanav1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
