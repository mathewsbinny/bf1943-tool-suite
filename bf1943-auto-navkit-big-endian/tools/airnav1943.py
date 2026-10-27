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
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
import rsnvgrid1943  # noqa: E402
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
    W = game1943.level_work(lv)
    return {'objects': os.path.join(W, 'objects'), 'navkit': os.path.join(W, 'navkit'),
            'rsnv': os.path.join(W, 'navkit', f'{lv}.plane.rsnv'), 'soldier': os.path.join(W, 'navkit', f'{lv}.rsnv')}


def plane_spawns(lv, game=None):
    from bfbc2core import level1943, vehicles1943
    lvl = level1943.load(lv, game=game, log=lambda *a: None)
    out = []
    for e in lvl.entities:
        if e.category != 'vehicle spawn' or e.position is None:
            continue
        part = vehicles1943.vehicle_partition(e)
        name = part.rsplit('/', 1)[-1] if part else ''
        if vehicles1943.vehicle_class(name) != 'aircraft':
            continue
        M = np.asarray(e.matrix, float)
        f = M[2, :3].copy()
        f[1] = 0.0
        f /= max(np.linalg.norm(f), 1e-9)
        entry = np.array(ENTRY_LOCAL) @ M[:3, :3] + M[3, :3]
        out.append({'vehicle': name, 'team': e.team, 'pos': [float(v) for v in e.position], 'fwd': f.tolist(),
                    'yaw': math.atan2(f[0], f[2]), 'entry': entry.tolist(), 'index': e.index})
    return out, lvl


def floor_grid(T, hf, x0, z0, nx, nz, pitch, sea):
    """Highest terrain or collision per cell."""
    top = np.full((nz, nx), -1e9)
    for a in np.linspace(-0.5, 0.5, 5):
        for b in np.linspace(-0.5, 0.5, 5):
            I, J = np.meshgrid(np.arange(nx), np.arange(nz))
            top = np.maximum(top, hf.height(x0 + (I + a) * pitch, z0 + (J + b) * pitch))
    lo = np.floor((T[:, :, [0, 2]].min(1) - [x0 - pitch / 2, z0 - pitch / 2]) / pitch).astype(np.int64)
    hi = np.floor((T[:, :, [0, 2]].max(1) - [x0 - pitch / 2, z0 - pitch / 2]) / pitch).astype(np.int64)
    ymax = T[:, :, 1].max(1).astype(np.float64)
    one = (lo[:, 0] == hi[:, 0]) & (lo[:, 1] == hi[:, 1])
    m = one & (lo[:, 0] >= 0) & (lo[:, 1] >= 0) & (lo[:, 0] < nx) & (lo[:, 1] < nz)
    np.maximum.at(top, (lo[m, 1], lo[m, 0]), ymax[m])
    for t in np.nonzero(~one)[0].tolist():
        i0, i1 = max(lo[t, 0], 0), min(hi[t, 0], nx - 1)
        j0, j1 = max(lo[t, 1], 0), min(hi[t, 1], nz - 1)
        if i1 >= i0 and j1 >= j0:
            top[j0:j1 + 1, i0:i1 + 1] = np.maximum(top[j0:j1 + 1, i0:i1 + 1], ymax[t])
    return np.maximum(top, sea)


def grow_max(a, cells):
    out = a.copy()
    nz, nx = a.shape
    r = int(math.ceil(cells))
    for dj in range(-r, r + 1):
        for di in range(-r, r + 1):
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
            d = math.hypot(di, dj) * pitch * climb
            src = out[max(0, -dj):nz - max(0, dj), max(0, -di):nx - max(0, di)]
            dst = out[max(0, dj):nz + min(0, dj), max(0, di):nx + min(0, di)]
            np.maximum(dst, src - d, out=dst)
        if np.array_equal(prev, out):
            break
    return out


def obstacle_tops(T, hf, sea, cx, cz, half=OBS_WINDOW):
    """(x0, z0, top): the highest terrain or object per OBS_CELL cell over a window around (cx, cz), on the grid of
    rsnav's flightsim.Obstacles (cells from the height field's corner)."""
    hx0, hz0 = float(hf.min[0]), float(hf.min[2])
    i0 = max(int((cx - half - hx0) // OBS_CELL), 0)
    j0 = max(int((cz - half - hz0) // OBS_CELL), 0)
    nmax = int((hf.max[0] - hf.min[0]) // OBS_CELL), int((hf.max[2] - hf.min[2]) // OBS_CELL)
    i1 = min(int((cx + half - hx0) // OBS_CELL) + 1, nmax[0])
    j1 = min(int((cz + half - hz0) // OBS_CELL) + 1, nmax[1])
    nx, nz = i1 - i0, j1 - j0
    x0, z0 = hx0 + i0 * OBS_CELL, hz0 + j0 * OBS_CELL
    top = np.full((nz, nx), -1e9)
    I, J = np.meshgrid(np.arange(nx), np.arange(nz))
    for a in np.linspace(0.0, 1.0, 5):
        for b in np.linspace(0.0, 1.0, 5):
            top = np.maximum(top, hf.height(x0 + (I + a) * OBS_CELL, z0 + (J + b) * OBS_CELL))
    lo, hi = T[:, :, [0, 2]].min(1), T[:, :, [0, 2]].max(1)
    sel = (hi[:, 0] >= x0) & (lo[:, 0] <= x0 + nx * OBS_CELL) & (hi[:, 1] >= z0) & (lo[:, 1] <= z0 + nz * OBS_CELL)
    t_all = np.asarray(T[sel], np.float64)
    e = np.maximum(np.maximum(np.linalg.norm(t_all[:, 0] - t_all[:, 1], axis=1), np.linalg.norm(t_all[:, 1] - t_all[:, 2], axis=1)),
                   np.linalg.norm(t_all[:, 2] - t_all[:, 0], axis=1))
    n = np.maximum(np.ceil(e / 2.0), 1).astype(np.int64)
    for m in np.unique(n):
        t = t_all[n == m]
        uv = np.array([(i / m, j / m) for i in range(m + 1) for j in range(m + 1 - i)])
        for c0 in range(0, len(t), max(1, 2_000_000 // len(uv))):
            tt = t[c0:c0 + max(1, 2_000_000 // len(uv))]
            q = tt[:, None, 0] + uv[None, :, 0, None] * (tt[:, None, 1] - tt[:, None, 0]) + \
                uv[None, :, 1, None] * (tt[:, None, 2] - tt[:, None, 0])
            q = q.reshape(-1, 3)
            ii = np.floor((q[:, 0] - x0) / OBS_CELL).astype(np.int64)
            jj = np.floor((q[:, 2] - z0) / OBS_CELL).astype(np.int64)
            ok = (ii >= 0) & (jj >= 0) & (ii < nx) & (jj < nz)
            np.maximum.at(top, (jj[ok], ii[ok]), q[ok, 1])
    return x0, z0, np.maximum(top, sea)


def lane_check(obs, pa, pb, y):
    """(centre line clear, the roll carried to lift-off, the highest obstacle over the lane within the half wingspan)
    of the lane pa -> pb at height y: the flight test's rule (rsnav tests/test_flight.py lane_obstruction), the centre
    line +-CLEAR_ACROSS every metre from the start to CLEAR_PAST past the end, here every 1 m across."""
    x0, z0, top = obs
    nz, nx = top.shape

    def at(x, z):
        i, j = int((x - x0) // OBS_CELL), int((z - z0) // OBS_CELL)
        return top[j, i] if 0 <= i < nx and 0 <= j < nz else -1e9
    yaw = math.atan2(pb[0] - pa[0], pb[2] - pa[2])
    sy, cy = math.sin(yaw), math.cos(yaw)
    length = math.dist((pa[0], pa[2]), (pb[0], pb[2]))
    clear, carried, wing = True, True, -1e9
    for k in range(0, int(length + CLEAR_PAST) + 1):
        for c in np.arange(-CLEAR_ACROSS, CLEAR_ACROSS + 0.01, 1.0):
            x, z = pa[0] + sy * k + cy * c, pa[2] + cy * k - sy * c
            if at(x, z) - y > CLEAR_RISE:
                clear = False
                break
        if not clear:
            break
        if k <= LIFT_OFF and at(pa[0] + sy * k, pa[2] + cy * k) < y - SUPPORT_DROP:
            carried = False
        if k <= length and k % 2 == 0:
            for c in range(-int(WING_HALF), int(WING_HALF) + 1, 2):
                wing = max(wing, at(pa[0] + sy * k + cy * c, pa[2] + cy * k - sy * c) - y)
    return clear, carried, wing


def segment_clear(floor, x0, z0, pitch, a, b, clear):
    """Whether the straight segment a -> b (x, y, z) stays clear above the floor grid (samples every half cell)."""
    n = max(2, int(math.ceil(math.dist(a, b) / (pitch / 2))))
    t = np.linspace(0, 1, n + 1)
    p = np.asarray(a)[None, :] + (np.asarray(b) - np.asarray(a))[None, :] * t[:, None]
    i = np.clip(np.round((p[:, 0] - x0) / pitch).astype(int), 0, floor.shape[1] - 1)
    j = np.clip(np.round((p[:, 2] - z0) / pitch).astype(int), 0, floor.shape[0] - 1)
    return bool((p[:, 1] >= floor[j, i] + clear - 1e-6).all())


def build(lv, soldier_path=None, game=None, log=print):
    p = paths(lv)
    t0 = time.time()
    st = json.load(open(os.path.join(p['navkit'], 'navkit.json'), encoding='utf-8'))
    sea = float(st.get('sea_level', 0.0))
    pitch = vc.value('plane', 'pitch')
    radius = vc.value('plane', 'radius')
    agl = vc.value('plane', 'cruise_agl')
    hf_path = os.path.join(p['objects'], 'heightfield.res')
    hf = terrain.read(hf_path)
    spawns, lvl = plane_spawns(lv, game)
    soldier_path = soldier_path or p['soldier']
    S = rsnv.load(soldier_path)
    land = hf.heights > sea
    jj, ii = np.nonzero(land)
    cx, cz = hf.cell
    lo = np.array([hf.min[0] + ii.min() * cx, hf.min[2] + jj.min() * cz])
    hi = np.array([hf.min[0] + (ii.max() + 1) * cx, hf.min[2] + (jj.max() + 1) * cz])
    pts = [s['pos'] for s in spawns] + S.node_pos.tolist()
    P = np.array(pts)[:, [0, 2]]
    lo, hi = np.minimum(lo, P.min(0)) - MARGIN, np.maximum(hi, P.max(0)) + MARGIN
    lo = np.maximum(lo, [hf.min[0], hf.min[2]])
    hi = np.minimum(hi, [hf.max[0] - pitch, hf.max[2] - pitch])
    # whole sectors' worth of the soldier grid: keep the box inside the sector grid (X, Z >= 0)
    xoff, zoff = st['sector_offsets']
    lo = np.maximum(lo, [xoff * 100.0 + pitch, zoff * 100.0 + pitch])
    x0, z0 = math.floor(lo[0] / pitch) * pitch + pitch / 2, math.floor(lo[1] / pitch) * pitch + pitch / 2
    nx, nz = int((hi[0] - x0) / pitch) + 1, int((hi[1] - z0) / pitch) + 1
    T = np.load(os.path.join(p['objects'], 'all_tris.npy'))
    top = floor_grid(T, hf, x0, z0, nx, nz, pitch, sea)
    floor = np.maximum(grow_max(top, radius / pitch), sea + SEA_CLEAR)
    cruise = envelope(floor + agl, pitch, CLIMB)
    log(f'{lv}: flight grid {nx} x {nz} cells of {pitch:g} m; floor {floor.min():.0f}..{floor.max():.0f} m, '
        f'cruise {cruise.min():.0f}..{cruise.max():.0f} m')
    idx = np.arange(nx * nz).reshape(nz, nx)
    I, J = np.meshgrid(np.arange(nx), np.arange(nz))
    links = rsnvgrid1943.grid_links(idx)
    air = rsnvgrid1943.CellSet(pitch, x0, z0, I.ravel(), J.ravel(), cruise.ravel(), links, name=f'{lv} air')
    spacing = int(vc.value('plane', 'node_spacing'))
    blocks = {}
    for bj in range(0, nz, spacing):
        for bi in range(0, nx, spacing):
            ci, cj = min(bi + spacing // 2, nx - 1), min(bj + spacing // 2, nz - 1)
            blocks[(bi // spacing, bj // spacing)] = int(idx[cj, ci])
    cell_nodes = sorted(set(blocks.values()))
    pos = lambda k: (air.x0 + air.i[k] * pitch, float(air.y[k]), air.z0 + air.j[k] * pitch)
    edges = set()
    for (a, b), k in blocks.items():
        for da, db in ((1, 0), (0, 1), (1, 1), (1, -1), (2, 1), (1, 2), (2, -1), (1, -2)):
            m = blocks.get((a + da, b + db))
            if m is not None and segment_clear(floor, x0, z0, pitch, pos(k), pos(m), radius):
                edges.add((min(k, m), max(k, m)))
    node_list = [(0, k) for k in cell_nodes]
    nid = {k: n for n, k in enumerate(cell_nodes)}
    edge_list = [(nid[a], nid[b], rsnv.EDGE_WALK, None, True) for a, b in sorted(edges)]
    npos = np.array([pos(k) for k in cell_nodes])
    log(f'  {len(cell_nodes)} cruise nodes, {len(edges)} edges')

    def nearest_cruise(x, z):
        d = (npos[:, 0] - x) ** 2 + (npos[:, 2] - z) ** 2
        return int(np.argmin(d))
    # take-off and landing lanes: from the spawn along its heading [O: the planes face along the decks and fields],
    # checked against the collision; a blocked lane is turned (up to 20 deg), shortened (to 90 m) or started a few
    # metres ahead, in that order of preference (see choose_lane)
    sets = [air]
    offm, lanes, board = [], [], []
    run_default = vc.value('plane', 'takeoff_run')
    for s in spawns:
        p0 = np.array(s['pos'])
        obs = obstacle_tops(T, hf, sea, p0[0], p0[2])
        choice = choose_lane(obs, p0, s['yaw'])
        f = np.array([math.sin(choice['yaw']), 0.0, math.cos(choice['yaw'])])
        side = np.array([f[2], 0.0, -f[0]])
        run = choice['length']
        start = p0 + f * choice['start']
        lx0 = math.floor((p0[0] - run_default - 20) / LANE_PITCH) * LANE_PITCH
        lz0 = math.floor((p0[2] - run_default - 20) / LANE_PITCH) * LANE_PITCH
        cells = {}
        for t in np.arange(0.0, choice['start'] + run + 1e-6, LANE_PITCH / 2):
            for w in (-LANE_PITCH, 0.0, LANE_PITCH):
                q = p0 + f * t + side * w
                cells.setdefault((int(round((q[0] - lx0) / LANE_PITCH)), int(round((q[2] - lz0) / LANE_PITCH))), p0[1])
        key = sorted(cells)
        cid = {c: k for k, c in enumerate(key)}
        lk = {}
        for (ci, cj), k in cid.items():
            for di, dj in rsnvgrid1943.STRAIGHT:
                n = cid.get((ci + di, cj + dj))
                if n is not None:
                    lk[(k, di, dj)] = n
        sets.append(rsnvgrid1943.CellSet(LANE_PITCH, lx0, lz0, [c[0] for c in key], [c[1] for c in key],
                                         [cells[c] for c in key], lk, name=f'runway {s["index"]}'))
        si = len(sets) - 1
        c_start = (int(round((start[0] - lx0) / LANE_PITCH)), int(round((start[2] - lz0) / LANE_PITCH)))
        pe = start + f * run
        c_end = (int(round((pe[0] - lx0) / LANE_PITCH)), int(round((pe[2] - lz0) / LANE_PITCH)))
        a = len(node_list)
        node_list.append((si, cid[c_start]))
        b = len(node_list)
        node_list.append((si, cid.get(c_end, max(cid.values()))))
        edge_list.append((a, b, rsnv.EDGE_WALK, None, False))
        up = nearest_cruise(*(start + f * (run + CLIMB_OUT))[[0, 2]])
        down = nearest_cruise(*(p0 - f * APPROACH)[[0, 2]])
        edge_list.append((b, up, rsnv.EDGE_JUMP, float(math.dist(pe, npos[up])), False))
        edge_list.append((down, a, rsnv.EDGE_DROP, float(math.dist(npos[down], p0)), False))
        sn = soldier_node(S, s['entry'], ENTRY_RADIUS + 2.0)
        offm.append((rsnv.OFFM_BOARD_VEHICLE, team_mask(s['team']), vc.CLASS_IDS['plane'], a, *[float(v) for v in s['entry']],
                     float(s['yaw']), ENTRY_RADIUS, sn if sn is not None else 0xFFFFFFFF))
        lanes.append({'spawn': s['index'], 'vehicle': s['vehicle'], 'team': s['team'], 'start node': a, 'end node': b,
                      'climb-out node': up, 'approach node': down,
                      'heading offset deg': choice['offset'], 'start ahead m': choice['start'], 'length m': run,
                      'centre line clear': choice['clear'], 'roll carried to lift-off': choice['carried'],
                      'highest within the half wingspan m': round(float(choice['wing']), 1),
                      'straight lane blocked': choice['straight blocked'], 'source': choice['source']})
        board.append({'spawn': s['index'], 'team': s['team'], 'entry': [round(v, 2) for v in s['entry']], 'node': a,
                      'soldier node': sn, 'soldier island': int(S.node_island[sn]) if sn is not None else None})
    # parachute drops: over bases and flags, and over each soldier island that is no carrier
    drops = []
    targets = [(o.get('name'), o['position']) for o in getattr(lvl, 'objectives', []) if o.get('position')]
    for k, r in enumerate(S.islands):
        if r[0] >= DROP_ISLAND_MIN and not r[2] & rsnv.ISLD_CARRIER and k != 0:
            targets.append((f'island {k}', S.node_pos[r[1]].tolist()))
    for name, q in targets:
        d = np.hypot(S.node_pos[:, 0] - q[0], S.node_pos[:, 2] - q[2])
        sn = int(np.argmin(d))
        isl = int(S.node_island[sn])
        if S.islands and S.islands[isl][2] & rsnv.ISLD_CARRIER:
            continue                                     # no drops on carriers
        sp = S.node_pos[sn]
        cn = nearest_cruise(sp[0], sp[2])
        offm.append((OFFM_DROP, 3, vc.CLASS_IDS['plane'], cn, float(sp[0]), float(sp[1]), float(sp[2]), 0.0,
                     float(np.hypot(npos[cn, 0] - sp[0], npos[cn, 2] - sp[2])), sn))
        drops.append({'target': name, 'soldier node': sn, 'soldier island': isl, 'cruise node': cn,
                      'height over ground': round(float(npos[cn, 1] - sp[1]), 1)})
    log(f'  {len(lanes)} take-off lanes ({sum(1 for l in lanes if l["straight lane blocked"])} with the spawn heading '
        f'blocked, {sum(1 for l in lanes if not l["centre line clear"])} not cleared, {sum(1 for l in lanes if not l["roll carried to lift-off"])} rolling off the ground before lift-off), {len(drops)} parachute drops')
    for l in lanes:
        if l["straight lane blocked"] or not l["roll carried to lift-off"]:
            log(f'    spawn {l["spawn"]} {l["vehicle"]}: heading {l["heading offset deg"]:+.1f} deg, start +{l["start ahead m"]:g} m, '
                f'{l["length m"]:g} m, clear {l["centre line clear"]}, carried {l["roll carried to lift-off"]}, wing obstacle {l["highest within the half wingspan m"]} m')
    meta = {'sources': {k: {'path': v, 'sha256': rsnvgrid1943.sha256(v)} for k, v in (
        ('soldier rsnv', soldier_path), ('all_tris.npy', os.path.join(p['objects'], 'all_tris.npy')),
        ('heightfield.res', hf_path), ('navkit.json', os.path.join(p['navkit'], 'navkit.json'))) if os.path.isfile(v)},
        'class parameters': {k: list(v) if isinstance(v, tuple) else v for k, v in vc.CLASSES['plane'].items()},
        'grid': {'pitch': pitch, 'x0': x0, 'z0': z0, 'nx': nx, 'nz': nz, 'floor range': [float(floor.min()), float(floor.max())]},
        'edge kinds': 'walk: fly; jump: take-off climb-out; drop: landing approach', 'lanes': lanes, 'boarding': board,
        'drops': drops}
    header = {'sea_level': sea, 'radius': radius, 'height': vc.value('plane', 'half_length') * 2, 'step': 0.5,
              'water_depth': 0.0, 'sha256': meta['sources']['soldier rsnv']['sha256']}
    stats = rsnvgrid1943.write_world(lv, 'plane', sets, node_list, edge_list, offm, p['rsnv'], st['sector_offsets'],
                                     header, meta, log=log)
    stats.update({'lanes': len(lanes), 'drops': len(drops), 'seconds': round(time.time() - t0, 1)})
    log(f'{lv}: plane world {p["rsnv"]} ({stats["seconds"]:.0f} s)')
    return p['rsnv'], stats


def choose_lane(obs, p0, yaw0):
    """The take-off lane of a plane spawn: the spawn's own heading and takeoff_run [H] when clear (lane_check on the
    lane's nodes as the world will hold them: 2 m cell centres); else the first clear candidate by (start ahead,
    length longest, heading offset smallest, then the least wing obstacle); flagged when none is clear."""
    def geometry(d0, L, off):
        yaw = yaw0 + math.radians(off)
        f = np.array([math.sin(yaw), 0.0, math.cos(yaw)])
        return yaw, p0 + f * d0, p0 + f * (d0 + L)
    run0 = vc.value('plane', 'takeoff_run')
    y = float(p0[1])
    _yaw, a0, b0 = geometry(0.0, run0, 0.0)
    clear0, carried0, wing0 = lane_check(obs, snap_lane(a0), snap_lane(b0), y)
    if clear0 and carried0:
        return {'yaw': yaw0, 'offset': 0.0, 'start': 0.0, 'length': run0, 'clear': True, 'carried': True, 'wing': wing0,
                'straight blocked': False, 'source': '[H] the spawn heading x takeoff_run; clear of the collision [O]'}
    best = None
    for headings in (HEADINGS, HEADINGS_WIDE):
        best = search_lane(obs, p0, yaw0, y, geometry, headings)
        if best is not None:
            if headings is HEADINGS_WIDE:
                best['source'] = (f'[H] the plane turns {best["offset"]:+.1f} deg on the ground first: no clear lane '
                                  f'within 20 deg of the spawn heading; clear of the collision [O]')
            return best
    return {'yaw': yaw0, 'offset': 0.0, 'start': 0.0, 'length': run0, 'clear': clear0, 'carried': carried0, 'wing': wing0,
            'straight blocked': not clear0,
            'source': ('[H] the spawn heading x takeoff_run, centre line clear [O]; but the roll leaves the ground before '
                       'lift-off (no lane at any heading is both clear and carried): an in-game check' if clear0 else
                       '[U] no clear lane at any heading, 75-120 m, within 16 m of the spawn: kept, blocked')}


def search_lane(obs, p0, yaw0, y, geometry, headings):
    best = None
    for d0 in STARTS:
        for L in LENGTHS:
            for off in headings:
                yaw, a, b = geometry(d0, L, off)
                clear, carried, wing = lane_check(obs, snap_lane(a), snap_lane(b), y)
                if clear and carried:
                    key = (d0, -L, abs(off), wing)
                    if best is None or key < best[0]:
                        best = (key, {'yaw': yaw, 'offset': float(off), 'start': d0, 'length': L, 'clear': True,
                                      'carried': True, 'wing': wing, 'straight blocked': True,
                                      'source': '[H] turned / shortened / moved off the spawn heading to clear the '
                                                'collision [O] on the deck or field [O]'})
            if best is not None and best[0][0] == d0 and best[0][1] == -L:
                break                                    # the longest clear length at this start is found
        if best is not None:
            break
    return best[1] if best is not None else None


def snap_lane(q):
    """A lane end as the world holds it: the 2 m lane cell's centre (on the lane cell grid of build())."""
    return (math.floor(q[0] / LANE_PITCH + 0.5) * LANE_PITCH, float(q[1]), math.floor(q[2] / LANE_PITCH + 0.5) * LANE_PITCH)


def team_mask(team):
    return {1: 1, 2: 2}.get(team, 3)


def soldier_node(S, p, reach, dy=3.5):
    return rsnvgrid1943.soldier_node(S, p, reach, dy)


def main(argv):
    ap = argparse.ArgumentParser(prog='airnav1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level')
    ap.add_argument('--soldier')
    a = ap.parse_args(argv[1:])
    sp = a.soldier or paths(a.level)['soldier']
    if not os.path.isfile(sp):
        raise FileNotFoundError(f'{a.level}: no soldier world (missing {sp}): run navexport1943.py {a.level}')
    build(a.level, sp)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('airnav1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
