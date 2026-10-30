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

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
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
    lvl = level1943.load(lv, game=game, log=lambda *a: None)
    g = game1943.Game(game1943.find_game(game), lv)
    out = []
    for e in lvl.entities:
        if e.category != 'vehicle spawn' or e.position is None:
            continue
        part = vehicles1943.vehicle_partition(e)
        name = part.rsplit('/', 1)[-1] if part else ''
        if vehicles1943.vehicle_class(name) != 'boat':
            continue
        M = np.asarray(e.matrix, float)
        f = M[2, :3]
        out.append({'vehicle': name, 'team': e.team, 'pos': [float(v) for v in e.position],
                    'fwd': (f / max(np.linalg.norm(f[[0, 2]]), 1e-9)).tolist(), 'yaw': math.atan2(f[0], f[2]),
                    'entries': vehicles1943.entry_points(g, e), 'index': e.index})
    return out, lvl


def rasterise_band(T, lo, hi, x0, z0, nx, nz, pitch):
    """Bool (nz, nx): cells whose centre lies under a triangle crossing the height band [lo, hi] (a triangle smaller
    than a cell marks the cell of its centroid)."""
    import navsample1943
    ylo, yhi = T[:, :, 1].min(1), T[:, :, 1].max(1)
    T = T[(ylo <= hi) & (yhi >= lo)].astype(np.float64)
    out = np.zeros((nz, nx), bool)
    if not len(T):
        return out, 0
    c = T.mean(1)
    ci = np.round((c[:, 0] - x0) / pitch).astype(np.int64)
    cj = np.round((c[:, 2] - z0) / pitch).astype(np.int64)
    m = (ci >= 0) & (cj >= 0) & (ci < nx) & (cj < nz)
    out[cj[m], ci[m]] = True
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
        _u, _v, _w, ins = navsample1943.inside(T[t:t + 1, :, [0, 2]], x0 + ii * pitch, z0 + jj * pitch)
        out[jj[ins[0]], ii[ins[0]]] = True
    return out, len(T)


def dilate(mask, cells):
    """Bool mask grown by a disc of `cells` radius."""
    out = mask.copy()
    r = int(math.ceil(cells))
    nz, nx = mask.shape
    for dj in range(-r, r + 1):
        for di in range(-r, r + 1):
            if (di or dj) and di * di + dj * dj <= cells * cells + 1e-9:
                src = mask[max(0, -dj):nz - max(0, dj), max(0, -di):nx - max(0, di)]
                out[max(0, dj):nz + min(0, dj), max(0, di):nx + min(0, di)] |= src
    return out


def distance_field(mask, cap):
    """Chessboard distance (cells) from each True cell to the nearest False, capped."""
    d = np.where(mask, cap, 0).astype(np.int64)
    cur = mask.copy()
    for r in range(1, cap):
        er = cur.copy()
        er[1:, :] &= cur[:-1, :]
        er[:-1, :] &= cur[1:, :]
        er[:, 1:] &= cur[:, :-1]
        er[:, :-1] &= cur[:, 1:]
        d = np.where(cur & ~er, np.minimum(d, r), d)
        cur = er
        if not cur.any():
            break
    return d


def cell_line(nav, a, b):
    """Whether the straight line between cells a (i, j) and b crosses only navigable cells (4-connected steps)."""
    (ia, ja), (ib, jb) = a, b
    n = max(abs(ib - ia), abs(jb - ja))
    if n == 0:
        return bool(nav[ja, ia])
    pi, pj = ia, ja
    for s in range(1, n + 1):
        ti = int(round(ia + (ib - ia) * s / n))
        tj = int(round(ja + (jb - ja) * s / n))
        if ti != pi and tj != pj and not (nav[pj, ti] or nav[tj, pi]):
            return False
        if not nav[tj, ti]:
            return False
        pi, pj = ti, tj
    return True


def build(lv, soldier_path=None, game=None, log=print):
    """The LCVP world: (rsnv path, stats), or (None, stats) when the level has none (no water, no craft spawns: stamped
    absent). Reads the level from the game copy it was prepared from (2026-10-30: game=None read the default copy)."""
    p = paths(lv)
    t0 = time.time()
    st = nj.read(lv)
    game = vc.prepared_game(st, game, lv)
    want = vc.inputs(st, lv, 'lcvp')
    if not soldier1943.has_water(st):            # prepare found no water mesh (2026-10-30: float(None) raised)
        # absent whatever the soldier world: the level's data says so (2026-10-30: a current soldier export was needed)
        why = 'no water on the level (navkit.py prepare found no water mesh)'
        log(f'{lv}: {why}: no LCVP world')
        vc.absent(lv, 'lcvp', want, p['rsnv'], why, log)
        return None, {'spawns': 0, 'note': why}
    soldier_path = soldier_path or p['soldier']
    want['soldier'] = vc.soldier_world(st, lv, soldier_path)
    vc.drop(lv, 'worlds', 'lcvp')
    pitch = vc.value('lcvp', 'pitch')
    draft = vc.value('lcvp', 'draft')
    radius = vc.value('lcvp', 'radius')
    half_w = vc.value('lcvp', 'half_width')
    beach = vc.value('lcvp', 'beach_depth')
    bow = vc.value('lcvp', 'bow')
    hf_path = os.path.join(p['objects'], 'heightfield.res')
    hf = terrain.read(hf_path)
    sea = soldier1943.sea_level(st, hf)
    spawns, lvl = craft_spawns(lv, game)
    S = rsnv.load(soldier_path)
    if not spawns:
        log(f'{lv}: no landing craft spawns: no LCVP world')
        vc.absent(lv, 'lcvp', want, p['rsnv'], 'no landing craft spawns on the level', log)
        return None, {'spawns': 0}
    # the box: land, carriers (objects holding soldier spawns), craft spawns
    land = hf.heights > sea - draft
    jj, ii = np.nonzero(land)
    cx, cz = hf.cell
    lo = np.array([hf.min[0] + ii.min() * cx, hf.min[2] + jj.min() * cz])
    hi = np.array([hf.min[0] + (ii.max() + 1) * cx, hf.min[2] + (jj.max() + 1) * cz])
    pts = np.array([s['pos'] for s in spawns])[:, [0, 2]]
    carrier_nodes = S.node_pos[[k for k in range(len(S.node_pos)) if S.islands and
                                S.islands[int(S.node_island[k])][2] & rsnv.ISLD_CARRIER]] if S.islands else np.zeros((0, 3))
    if len(carrier_nodes):
        pts = np.concatenate([pts, carrier_nodes[:, [0, 2]]])
    lo, hi = np.minimum(lo, pts.min(0)) - MARGIN, np.maximum(hi, pts.max(0)) + MARGIN
    lo = np.maximum(lo, [hf.min[0], hf.min[2]])
    hi = np.minimum(hi, [hf.max[0] - pitch, hf.max[2] - pitch])
    x0, z0 = math.floor(lo[0] / pitch) * pitch, math.floor(lo[1] / pitch) * pitch
    nx, nz = int((hi[0] - x0) / pitch) + 1, int((hi[1] - z0) / pitch) + 1
    I, J = np.meshgrid(np.arange(nx), np.arange(nz))
    X, Z = x0 + I * pitch, z0 + J * pitch
    depth = sea - hf.height(X, Z)
    T = np.load(os.path.join(p['objects'], 'all_tris.npy'))
    blocked, n_band = rasterise_band(T, sea - draft - BAND_BELOW, sea + BAND_ABOVE, x0, z0, nx, nz, pitch)
    log(f'{lv}: sea grid {nx} x {nz} cells of {pitch} m (x {x0:.0f}..{x0 + nx * pitch:.0f}, z {z0:.0f}..'
        f'{z0 + nz * pitch:.0f}); {n_band} triangles cross the waterline band, {int(blocked.sum())} cells blocked')
    water = depth >= draft
    shallow_ground = ~water
    nav = water & ~dilate(blocked, radius / pitch) & ~dilate(shallow_ground, half_w / pitch)
    # the boat's own water bodies: keep those a craft spawn or a launch reaches
    beach_cells = (depth <= beach) & (depth > -1.0) & ~dilate(blocked, 1.0 / pitch)
    log(f'  navigable {int(nav.sum())} cells ({nav.sum() * pitch * pitch / 1e6:.2f} km2), beach {int(beach_cells.sum())}')
    # launch lanes
    deck_cells = []            # (i, j, y, lane)
    lanes = []
    deck_spawns = [s for s in spawns if s['pos'][1] - sea >= DECK_MIN]
    if deck_spawns:
        sp_xz = np.array([s['pos'] for s in deck_spawns])[:, [0, 2]]
        cen = T[:, :, [0, 2]].mean(1)
        near = np.zeros(len(T), bool)
        for q in sp_xz:
            near |= np.hypot(cen[:, 0] - q[0], cen[:, 1] - q[1]) < 400.0
        Tdeck = T[near & (T[:, :, 1].max(1) >= sea + DECK_MIN - 3.0)]
    for s in deck_spawns:
        f = np.array(s['fwd'], float)
        f[1] = 0.0
        f /= np.linalg.norm(f)
        p0 = np.array(s['pos'], float)
        deck_y = p0[1]
        end = None
        d = 0.0
        last_deck = 0.0
        while d < 300.0:
            q = p0 + f * d
            # collision top within 1.5 m below the craft and 1 m above it, at the lane point or 1 m around it (the
            # craft's cradle leaves holes in the deck's collision under it)
            if any(deck_tops(Tdeck, q[0] + ox, q[2] + oz, deck_y - 1.5, deck_y + 1.0) is not None
                   for ox, oz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))):
                last_deck = d
            elif d - last_deck > DECK_GAP:
                end = last_deck + pitch
                break
            d += pitch
        if end is None:
            log(f'  craft {s["index"]} at {np.round(p0, 1).tolist()}: no deck end within 300 m, no launch lane')
            continue
        deck_end = p0 + f * max(end - pitch, 0.0)
        land_pt = p0 + f * (end + vc.value('lcvp', 'half_length') + LAUNCH_GAP)
        lanes.append({'spawn': s, 'deck_end': deck_end.tolist(), 'land': [float(land_pt[0]), sea, float(land_pt[2])],
                      'length': round(end, 1)})
    log(f'  {len(lanes)} launch lanes (deck lengths {[l["length"] for l in lanes]})')
    # graph over sea cells
    nav_idx = np.full((nz, nx), -1, np.int64)
    ks = np.nonzero(nav.ravel())[0]
    nav_idx.ravel()[ks] = np.arange(len(ks))
    links = rsnvgrid1943.grid_links(nav_idx)
    sea_set = rsnvgrid1943.CellSet(pitch, x0, z0, I.ravel()[ks], J.ravel()[ks], np.full(len(ks), sea), links, name=f'{lv} sea')
    # components of the navigable water (straight links), to keep the craft's water bodies
    pairs = np.array([(k, n) for (k, _di, _dj), n in links.items()], np.int64).reshape(-1, 2)
    import groundnav1943
    comp = groundnav1943.components(len(ks), pairs)
    ci_ = sea_set.i
    cj_ = sea_set.j

    def nearest_cell(x, z, maxd=60.0):
        d = (x0 + ci_ * pitch - x) ** 2 + (z0 + cj_ * pitch - z) ** 2
        k = int(np.argmin(d))
        return (k, math.sqrt(float(d[k]))) if d[k] <= maxd * maxd else (None, math.sqrt(float(d[k])))
    keep_comp = set()
    starts = []
    for l in lanes:
        k, dist = nearest_cell(l['land'][0], l['land'][2])
        l['water_cell'] = k
        l['water_dist'] = round(dist, 1)
        if k is not None:
            keep_comp.add(int(comp[k]))
    for s in spawns:
        if s['pos'][1] - sea < DECK_MIN:
            k, dist = nearest_cell(s['pos'][0], s['pos'][2], 20.0)
            s['water_cell'] = k
            if k is not None:
                keep_comp.add(int(comp[k]))
    kept = np.isin(comp, list(keep_comp))
    log(f'  kept {len(keep_comp)} water bodies, {int(kept.sum())} cells ({kept.sum() * pitch * pitch / 1e6:.2f} km2)')
    nav2 = np.zeros_like(nav)
    nav2[sea_set.j[kept], sea_set.i[kept]] = True
    # rebuild the set on the kept cells
    nav_idx = np.full((nz, nx), -1, np.int64)
    ks2 = np.nonzero(nav2.ravel())[0]
    nav_idx.ravel()[ks2] = np.arange(len(ks2))
    links = rsnvgrid1943.grid_links(nav_idx)
    sea_set = rsnvgrid1943.CellSet(pitch, x0, z0, I.ravel()[ks2], J.ravel()[ks2], np.full(len(ks2), sea), links,
                                   name=f'{lv} sea')
    old_to_new = {int(o): int(nav_idx.ravel()[ks[o]]) for o in np.nonzero(kept)[0].tolist()}
    for l in lanes:
        l['water_cell'] = old_to_new.get(l['water_cell']) if l['water_cell'] is not None else None
    for s in spawns:
        if s.get('water_cell') is not None:
            s['water_cell'] = old_to_new.get(s['water_cell'])
    # route nodes: per 4 x 4 block the cell farthest from the shore
    clr = distance_field(nav2, 12)
    spacing = int(vc.value('lcvp', 'node_spacing'))
    si, sj = sea_set.i, sea_set.j
    bi, bj = si // spacing, sj // spacing
    centre = (si - (bi * spacing + spacing / 2 - 0.5)) ** 2 + (sj - (bj * spacing + spacing / 2 - 0.5)) ** 2
    order = np.lexsort((centre, -clr[sj, si], bj, bi))
    blk = np.stack([bi[order], bj[order]], 1)
    first = np.ones(len(order), bool)
    first[1:] = np.any(blk[1:] != blk[:-1], axis=1)
    node_cells = order[first].tolist()
    node_blk = {(int(a), int(b)): k for (a, b), k in zip(blk[first].tolist(), node_cells)}
    edges = set()
    for (a, b), k in node_blk.items():
        for da, db in ((1, 0), (0, 1), (1, 1), (1, -1)):
            m = node_blk.get((a + da, b + db))
            if m is not None and cell_line(nav2, (int(si[k]), int(sj[k])), (int(si[m]), int(sj[m]))):
                edges.add((min(k, m), max(k, m)))
    log(f'  {len(node_cells)} sea nodes, {len(edges)} edges')
    # extra nodes: craft water cells, lane landings, landing zones
    extra = set()
    for l in lanes:
        if l['water_cell'] is not None:
            extra.add(l['water_cell'])
    for s in spawns:
        if s.get('water_cell') is not None:
            extra.add(s['water_cell'])
    zones = landing_zones(lv, sea_set, nav2, depth, beach_cells, S, bow, pitch, x0, z0, log)
    for z in zones:
        extra.add(z['cell'])
    node_cells, edges = connect_extra(sea_set, nav2, node_cells, edges, extra)
    node_cells, edges = join_cell_pieces(sea_set, nav2, node_cells, edges, log)
    # deck lanes: a cell set per lane (3 cells wide at deck height), a node at each end
    lane_sets = []
    for l in lanes:
        s = l['spawn']
        cells = {}
        p0 = np.array(s['pos'], float)
        f = np.array(s['fwd'], float)
        f[1] = 0
        f /= np.linalg.norm(f)
        side = np.array([f[2], 0.0, -f[0]])
        dist = float(np.linalg.norm((np.array(l['deck_end']) - p0)[[0, 2]]))
        for t in np.arange(0.0, dist + 1e-6, pitch / 2):
            for w in (-pitch, 0.0, pitch):
                q = p0 + f * t + side * w
                ci = int(round((q[0] - x0) / pitch))
                cj = int(round((q[2] - z0) / pitch))
                cells.setdefault((ci, cj), p0[1])
        key = sorted(cells)
        idx = {c: k for k, c in enumerate(key)}
        lk = {}
        for (ci, cj), k in idx.items():
            for di, dj in rsnvgrid1943.STRAIGHT:
                n = idx.get((ci + di, cj + dj))
                if n is not None:
                    lk[(k, di, dj)] = n
        lane_set = rsnvgrid1943.CellSet(pitch, x0, z0, [c[0] for c in key], [c[1] for c in key],
                                        [cells[c] for c in key], lk, name=f'lane {s["index"]}')
        c0 = (int(round((p0[0] - x0) / pitch)), int(round((p0[2] - z0) / pitch)))
        de = np.array(l['deck_end'])
        c1 = (int(round((de[0] - x0) / pitch)), int(round((de[2] - z0) / pitch)))
        l['cells'] = (idx[c0], idx.get(c1, min(idx.items(), key=lambda kv: (kv[0][0] - c1[0]) ** 2 + (kv[0][1] - c1[1]) ** 2)[1]))
        lane_sets.append(lane_set)
    # all nodes: sea set 0, lanes 1..
    sets = [sea_set] + lane_sets
    node_list = [(0, k) for k in node_cells]
    nid = {k: n for n, k in enumerate(node_cells)}
    edge_list = [(nid[a], nid[b], rsnv.EDGE_WALK, None, True) for a, b in sorted(edges)]
    offm = []
    lane_info = []
    for li, l in enumerate(lanes):
        a = len(node_list)
        node_list.append((li + 1, l['cells'][0]))
        b = len(node_list)
        node_list.append((li + 1, l['cells'][1]))
        edge_list.append((a, b, rsnv.EDGE_WALK, None, False))      # forward only: the craft does not drive back up
        if l['water_cell'] is not None:
            wn = nid[l['water_cell']]
            de = np.array(l['deck_end'])
            wp = sea_set.xz(l['water_cell'])
            edge_list.append((b, wn, rsnv.EDGE_DROP, float(math.hypot(wp[0] - de[0], wp[1] - de[2])), False))
            offm.append((OFFM_LCVP_LAUNCH, team_mask(l['spawn']['team']), vc.CLASS_IDS['lcvp'], b,
                         float(l['land'][0]), float(l['land'][1]), float(l['land'][2]), float(l['spawn']['yaw']),
                         float(vc.value('lcvp', 'half_length')), wn))
        l['nodes'] = (a, b)
        lane_info.append({'spawn': l['spawn']['index'], 'team': l['spawn']['team'], 'deck node': a, 'deck end node': b,
                          'water node': nid.get(l['water_cell']), 'deck length': l['length'],
                          'landing point': [round(v, 2) for v in l['land']], 'snap to water': l['water_dist']})
    # boarding transfers: every craft spawn
    board = []
    for s in spawns:
        lane = next((l for l in lanes if l['spawn'] is s), None)
        if lane is not None:
            node = lane['nodes'][0]
        elif s.get('water_cell') is not None:
            node = nid[s['water_cell']]
        else:
            node = None
        ent = next((e for e in s['entries'] if e.get('order', 0) == 0), None) or \
            {'point': s['pos'], 'radius': vc.value('lcvp', 'radius') + 4.0}
        sn = soldier_node(S, ent['point'], ent['radius'] + 2.0)
        offm.append((rsnv.OFFM_BOARD_VEHICLE, team_mask(s['team']), vc.CLASS_IDS['lcvp'],
                     node if node is not None else 0xFFFFFFFF, *[float(v) for v in ent['point']], float(s['yaw']),
                     float(ent['radius']), sn if sn is not None else 0xFFFFFFFF))
        board.append({'spawn': s['index'], 'team': s['team'], 'pos': [round(v, 2) for v in s['pos']],
                      'node': node, 'entry': [round(v, 2) for v in ent['point']], 'radius': ent['radius'],
                      'soldier node': sn, 'soldier island': int(S.node_island[sn]) if sn is not None else None,
                      'on deck': lane is not None})
    # alight transfers
    for z in zones:
        n = nid[z['cell']]
        sp = z['unload']
        offm.append((OFFM_ALIGHT_ZONE, 3, vc.CLASS_IDS['lcvp'], n, float(sp[0]), float(sp[1]), float(sp[2]),
                     float(z['yaw']), float(z['reach']), int(z['soldier'])))
        z['node'] = n
    nflags = [0] * len(node_list)
    for z in zones:
        nflags[z['node']] |= rsnv.NAUX_SHALLOW               # a landing node: the craft grounds here
    meta = {'sources': {k: {'path': v, 'sha256': rsnvgrid1943.sha256(v)} for k, v in (
        ('soldier rsnv', soldier_path), ('all_tris.npy', os.path.join(p['objects'], 'all_tris.npy')),
        ('heightfield.res', hf_path), ('navkit.json', os.path.join(p['navkit'], 'navkit.json'))) if os.path.isfile(v)},
        'class parameters': {k: list(v) if isinstance(v, tuple) else v for k, v in vc.CLASSES['lcvp'].items()},
        'grid': {'pitch': pitch, 'x0': x0, 'z0': z0, 'nx': nx, 'nz': nz}, 'lanes': lane_info, 'boarding': board,
        'landing zones': [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in z.items() if k != 'cell'} for z in zones]}
    header = {'sea_level': sea, 'radius': radius, 'height': vc.value('lcvp', 'height'), 'step': 0.5,
              'water_depth': draft, 'sha256': meta['sources']['soldier rsnv']['sha256']}
    stats = rsnvgrid1943.write_world(lv, 'lcvp', sets, node_list, edge_list, offm, p['rsnv'], st['sector_offsets'],
                                     header, meta, node_flags=nflags, log=log)
    vc.record(lv, 'worlds', want, 'lcvp')
    stats.update({'lanes': len(lanes), 'landing zones': len(zones), 'boarding': len(board),
                  'seconds': round(time.time() - t0, 1)})
    log(f'{lv}: LCVP world {p["rsnv"]}: {stats["nodes"]} nodes, {len(lanes)} lanes, {len(zones)} landing zones, '
        f'{len(board)} boarding points ({stats["seconds"]:.0f} s)')
    return p['rsnv'], stats


def team_mask(team):
    return {1: 1, 2: 2}.get(team, 3)


_tops_cache = {}


def deck_tops(T, x, z, ylo, yhi):
    """The highest collision surface at (x, z) between ylo and yhi, or None."""
    key = id(T)
    if key not in _tops_cache:
        _tops_cache.clear()
        _tops_cache[key] = (T[:, :, [0, 2]].min(1), T[:, :, [0, 2]].max(1), T[:, :, 1].min(1), T[:, :, 1].max(1))
    lo, hi, tlo, thi = _tops_cache[key]
    m = np.nonzero((lo[:, 0] <= x) & (hi[:, 0] >= x) & (lo[:, 1] <= z) & (hi[:, 1] >= z) & (thi >= ylo) & (tlo <= yhi))[0]
    best = None
    for t in m.tolist():
        a, b, c = (T[t, 0, [0, 2]].astype(float), T[t, 1, [0, 2]].astype(float), T[t, 2, [0, 2]].astype(float))
        v0, v1, v2 = b - a, c - a, np.array([x, z]) - a
        d = v0[0] * v1[1] - v0[1] * v1[0]
        if abs(d) < 1e-9:
            continue
        u = (v2[0] * v1[1] - v2[1] * v1[0]) / d
        w = (v0[0] * v2[1] - v0[1] * v2[0]) / d
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
    nz, nx = nav.shape
    near_beach = dilate(beach_cells, (bow + 0.5) / pitch)
    cand = np.nonzero(near_beach[cs.j, cs.i])[0]
    if not len(cand):
        return []
    forbid_path = os.path.join(game1943.level_work(lv), 'objects', 'forbid_tris.npy')
    F = np.load(forbid_path) if os.path.isfile(forbid_path) else np.zeros((0, 3, 3), np.float32)
    Fc = F.mean(1) if len(F) else np.zeros((0, 3))
    bj, bi = np.nonzero(beach_cells)
    bx, bz = x0 + bi * pitch, z0 + bj * pitch
    main = 0
    carrier = {k for k, r in enumerate(S.islands) if r[2] & rsnv.ISLD_CARRIER}
    zones, rejected = [], collections.Counter()
    taken = []
    # nearest first to the shore: shallower water first
    cx, cz = cs.x0 + cs.i[cand] * pitch, cs.z0 + cs.j[cand] * pitch
    d_shallow = depth[cs.j[cand], cs.i[cand]]
    for k in cand[np.argsort(d_shallow, kind='stable')].tolist():
        x, z = cs.xz(k)
        if any((x - a) ** 2 + (z - b) ** 2 < ZONE_SPACING ** 2 for a, b in taken):
            continue
        d2 = (bx - x) ** 2 + (bz - z) ** 2
        q = int(np.argmin(d2))
        dirx, dirz = bx[q] - x, bz[q] - z
        ln = math.hypot(dirx, dirz)
        if ln < 1e-6:
            continue
        dirx, dirz = dirx / ln, dirz / ln
        bowp = (x + dirx * bow, z + dirz * bow)
        dn = np.hypot(S.node_pos[:, 0] - bowp[0], S.node_pos[:, 2] - bowp[1])
        dn = np.where(np.abs(S.node_pos[:, 1] - cs.y[k]) <= 3.0, dn, np.inf)
        sn = int(np.argmin(dn))
        if not np.isfinite(dn[sn]) or dn[sn] > ALIGHT_REACH:
            rejected['no soldier ground within reach'] += 1
            continue
        isl = int(S.node_island[sn])
        if isl != main or isl in carrier:
            rejected['nearest soldier ground not the main island'] += 1
            continue
        sp = S.node_pos[sn]
        if len(Fc):
            # ForbidPathfinding collision near the bow -> soldier node line
            ax, az, ex, ez = bowp[0], bowp[1], float(sp[0]), float(sp[2])
            vx, vz = ex - ax, ez - az
            L2 = max(vx * vx + vz * vz, 1e-9)
            t = np.clip(((Fc[:, 0] - ax) * vx + (Fc[:, 2] - az) * vz) / L2, 0, 1)
            dd = np.hypot(Fc[:, 0] - (ax + t * vx), Fc[:, 2] - (az + t * vz))
            if (dd <= FORBID_CLEAR).any():
                rejected['ForbidPathfinding collision between bow and soldier ground'] += 1
                continue
        # the unload point: the soldier cell nearest the bow point (the craft's ramp reach) from which a main-island
        # node is in sight (rsnvtrip.in_sight_node, the runtime's rule) - a point on the soldier world's cells, which
        # stays put when the soldier world is rebuilt; else the node's own position
        unload, un = None, None
        loc = S.locate(bowp[0], float(sp[1]), bowp[1], max_dist=ALIGHT_REACH, dy=3.0)
        if loc is not None:
            ar = S.areas[loc[0]]
            ux, uz = ar.x0 + loc[1][0] * ar.pitch, ar.z0 + loc[1][1] * ar.pitch
            uy = S.ground(ar, ux, uz)
            un = rsnvtrip.in_sight_node(S, ux, uy, uz)
            if un is not None and int(S.node_island[un]) == main:
                unload = (float(ux), float(uy), float(uz))
        if unload is None:
            unload, un = (float(sp[0]), float(sp[1]), float(sp[2])), sn
            how = 'the soldier node itself'
        else:
            how = 'a soldier cell by the bow, a main-island node in sight'
        taken.append((x, z))
        zones.append({'cell': k, 'pos': [x, float(cs.y[k]), z], 'bow': [bowp[0], bowp[1]], 'yaw': math.atan2(dirx, dirz),
                      'soldier': un, 'soldier pos': [float(v) for v in S.node_pos[un]], 'unload': list(unload),
                      'unload is': how, 'reach': float(math.hypot(unload[0] - x, unload[2] - z)),
                      'depth': float(depth[cs.j[k], cs.i[k]])})
    log(f'  {len(zones)} landing zones; rejected candidates: {dict(rejected)}')
    return zones


def connect_extra(cs, nav, node_cells, edges, extra):
    """Adds the extra cells as nodes, each linked to every node within 2 blocks it has a straight line to."""
    node_cells = list(node_cells)
    have = set(node_cells)
    pos = np.stack([cs.i[node_cells], cs.j[node_cells]], 1)
    for k in sorted(extra):
        if k in have:
            continue
        a = (int(cs.i[k]), int(cs.j[k]))
        d = np.abs(pos - a).max(1)
        near = np.nonzero(d <= 10)[0]
        for t in near[np.argsort(d[near], kind='stable')].tolist():
            m = node_cells[t]
            if cell_line(nav, a, (int(cs.i[m]), int(cs.j[m]))):
                edges.add((min(k, m), max(k, m)))
        node_cells.append(k)
        have.add(k)
        pos = np.concatenate([pos, [a]])
    return node_cells, edges


def join_cell_pieces(cs, nav, node_cells, edges, log):
    """Pieces of the sea graph on one water body joined along the grid's shortest path (as groundnav1943)."""
    import groundnav1943
    links = cs.links
    table = {}
    for (k, di, dj), n in links.items():
        table[(k, di, dj)] = n
    lab_cells = groundnav1943.components(cs.n, np.array([(k, n) for (k, _a, _b), n in links.items()], np.int64).reshape(-1, 2))

    class G:
        pass
    g = G()
    g.i, g.j = cs.i, cs.j

    def line(ka, kb):
        return [ka, kb] if cell_line(nav, (int(cs.i[ka]), int(cs.j[ka])), (int(cs.i[kb]), int(cs.j[kb]))) else None
    saved = groundnav1943.grid_line
    groundnav1943.grid_line = lambda _g, _t, a, b: line(a, b)
    try:
        nodes, edges = groundnav1943.join_pieces(g, None, table, lab_cells, node_cells, edges, log)
    finally:
        groundnav1943.grid_line = saved
    return nodes, edges


def main(argv):
    ap = argparse.ArgumentParser(prog='seanav1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level')
    ap.add_argument('--soldier', help='the soldier world (default work\\levels\\<level>\\navkit\\<level>.rsnv)')
    a = ap.parse_args(argv[1:])
    p = paths(a.level)
    sp = a.soldier or p['soldier']
    if not os.path.isfile(sp):
        raise FileNotFoundError(f'{a.level}: no soldier world (missing {sp}): run navexport1943.py {a.level}')
    build(a.level, sp)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('seanav1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
