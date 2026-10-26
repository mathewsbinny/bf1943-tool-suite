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
    # take-off and landing lanes
    sets = [air]
    offm, lanes, board = [], [], []
    run = vc.value('plane', 'takeoff_run')
    for s in spawns:
        p0 = np.array(s['pos'])
        f = np.array(s['fwd'])
        side = np.array([f[2], 0.0, -f[0]])
        lx0 = math.floor((p0[0] - run - 10) / LANE_PITCH) * LANE_PITCH
        lz0 = math.floor((p0[2] - run - 10) / LANE_PITCH) * LANE_PITCH
        cells = {}
        for t in np.arange(0.0, run + 1e-6, LANE_PITCH / 2):
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
        c_start = (int(round((p0[0] - lx0) / LANE_PITCH)), int(round((p0[2] - lz0) / LANE_PITCH)))
        pe = p0 + f * run
        c_end = (int(round((pe[0] - lx0) / LANE_PITCH)), int(round((pe[2] - lz0) / LANE_PITCH)))
        a = len(node_list)
        node_list.append((si, cid[c_start]))
        b = len(node_list)
        node_list.append((si, cid.get(c_end, max(cid.values()))))
        edge_list.append((a, b, rsnv.EDGE_WALK, None, False))
        up = nearest_cruise(*(p0 + f * (run + CLIMB_OUT))[[0, 2]])
        down = nearest_cruise(*(p0 - f * APPROACH)[[0, 2]])
        edge_list.append((b, up, rsnv.EDGE_JUMP, float(math.dist(pe, npos[up])), False))
        edge_list.append((down, a, rsnv.EDGE_DROP, float(math.dist(npos[down], p0)), False))
        sn = soldier_node(S, s['entry'], ENTRY_RADIUS + 2.0)
        offm.append((rsnv.OFFM_BOARD_VEHICLE, team_mask(s['team']), vc.CLASS_IDS['plane'], a, *[float(v) for v in s['entry']],
                     float(s['yaw']), ENTRY_RADIUS, sn if sn is not None else 0xFFFFFFFF))
        lanes.append({'spawn': s['index'], 'vehicle': s['vehicle'], 'team': s['team'], 'start node': a, 'end node': b,
                      'climb-out node': up, 'approach node': down})
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
    log(f'  {len(lanes)} take-off lanes, {len(drops)} parachute drops')
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
