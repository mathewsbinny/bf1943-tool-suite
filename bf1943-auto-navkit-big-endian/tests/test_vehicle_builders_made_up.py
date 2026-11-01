"""NavKit (big-endian): the vehicle world builders (seanav1943, groundnav1943, airnav1943) on a made-up level

No game data: a prepared level in a temporary work folder (BF1943_WORK) - a round island with a beach, a lake on it
and deep sea around (a made-up height field, sea level 0), a carrier deck and an islet, collision triangles (the deck,
a rock in the surf, a ForbidPathfinding plate on the west shore), a soldier world written by rsnvgrid1943 and a
navkit.json stamped as prepared, built, checked and exported. The level's spawns (read from the game by
level1943) are stood in. Checked:

  - seanav1943.build: the landing craft world validates; the deck craft gets a launch lane (one-way drive, a drop into
    the sea, an LCVP_LAUNCH transfer); every craft spawn a boarding transfer (a craft on land none of its own node);
    landing zones only on the main island's beach, 25 m apart, none behind the ForbidPathfinding plate, their nodes
    flagged shallow; no node in the lake or on the rock; the world stamped. No craft spawns: stamped absent, the old
    world removed. The helpers (deck_tops, cell_line, rasterise_band, connect_extra, team_mask) and main's refusal.
  - groundnav1943: prepare leaves out the excluded objects' and dynamic props' triangles; sample runs the sampler
    (stood in) and stamps, a failing sampler raises; Grid reads a navsample file as from_columns makes it; build keeps
    only the component a spawn stands on, nothing on the ForbidPathfinding plate or a roof, boards and alights on the
    soldier world, stamps the world; refusals (not sampled, stale samples); main's steps; spawn_seeds.
  - airnav1943.build: the plane world validates; a take-off lane per spawn (one-way roll, a jump to cruise, a drop
    edge from the approach), cruise nodes above the floor, parachute drops over the flag and the islet but not the
    carrier; flight_box's refusal; floor_grid / grow_max / envelope / segment_clear; main's refusal; plane_spawns.
"""
import contextlib
import json
import math
import os
import struct
from types import SimpleNamespace

import numpy as np

import airnav1943 as an
import groundnav1943 as gn
import navkitjson1943 as nj
import navsample1943
import rsnvgrid1943 as rg
import seanav1943 as sn
import vehicleclasses1943 as vc
from bfbc2core import game1943, level1943, rsnv, terrain, vehicles1943

LV = 'made_up_shore'
PREP = 'a' * 64
HPD = 'b' * 64
HF_HALF, HF_N = 128.0, 64                   # the height field: 64 x 64 samples over -128..128 m (4 m apart)
SOLDIER_PITCH = 2.0
FORBID_BOX = (-45.0, -20.0, -12.0, 12.0)    # x0, x1, z0, z1: ForbidPathfinding plates over the west shore
ROCK_AT = (60.0, 50.0)                      # a rock crossing the waterline
LAKE_AT = (14.0, -14.0)
SHOALS = ((-72.0, 72.0), (72.0, -72.0))         # under the islet; in open sea (on height samples)
quiet = lambda *a: None                     # noqa: E731


def height_at(x, z):
    """The island: a 2 m plateau to r 30, a beach falling to -6 m at r 50 (0 m at r 35); a lake at -3 m on it; two
    shoals 0.5 m high, one under the islet (another island than the main one), one with no soldier ground near."""
    r = np.hypot(x, z)
    h = np.maximum(np.where(r <= 30.0, 2.0, 2.0 - 8.0 * (r - 30.0) / 20.0), -6.0)
    for sx, sz in SHOALS:
        h = np.maximum(h, 0.5 - 6.5 * np.hypot(x - sx, z - sz) / 12.0)
    return np.where(np.hypot(x - LAKE_AT[0], z - LAKE_AT[1]) <= 6.0, -3.0, h)


def heightfield_bytes():
    cell = 2 * HF_HALF / HF_N
    I, J = np.meshgrid(np.arange(HF_N), np.arange(HF_N))
    h = height_at(-HF_HALF + I * cell, -HF_HALF + J * cell)
    raw = np.round((h + 10.0) / 20.0 * 65535).astype('<u2')
    return struct.pack('<6f6IB', -HF_HALF, -10.0, -HF_HALF, HF_HALF, 10.0, HF_HALF, HF_N, 0, 0, 0, 0, 0, 0) + raw.tobytes()


def write_soldier(path, hf):
    """The soldier world: the island (2 m cells where the ground is 0.5 m over the sea, nodes every 8 m), a carrier
    deck at 10 m (flagged a carrier) and an islet at 1 m (nodes every 4 m). Returns the loaded world."""
    cells = {}                                       # (i, j) -> y, on a 2 m grid from (-128, -128)
    node_cells = []

    def ij(x, z):
        return int(round((x + HF_HALF) / SOLDIER_PITCH)), int(round((z + HF_HALF) / SOLDIER_PITCH))
    for x in np.arange(-40.0, 40.1, SOLDIER_PITCH):
        for z in np.arange(-40.0, 40.1, SOLDIER_PITCH):
            y = float(hf.height(x, z))
            if y >= 0.5:
                cells[ij(x, z)] = y
                if x % 8 == 0 and z % 8 == 0:
                    node_cells.append(ij(x, z))
    for x in np.arange(-8.0, 8.1, SOLDIER_PITCH):              # the carrier deck
        for z in np.arange(-116.0, -85.9, SOLDIER_PITCH):
            cells[ij(x, z)] = 10.0
            if x % 8 == 0 and z % 8 == 0:
                node_cells.append(ij(x, z))
    for x in np.arange(-80.0, -59.9, SOLDIER_PITCH):           # the islet
        for z in np.arange(60.0, 80.1, SOLDIER_PITCH):
            cells[ij(x, z)] = 1.0
            if x % 4 == 0 and z % 4 == 0:
                node_cells.append(ij(x, z))
    keys = sorted(cells)
    index = {c: k for k, c in enumerate(keys)}
    links = {}
    for (i, j), k in index.items():
        for di, dj in rg.STRAIGHT:
            n = index.get((i + di, j + dj))
            if n is not None:
                links[(k, di, dj)] = n
    cs = rg.CellSet(SOLDIER_PITCH, -HF_HALF, -HF_HALF, [c[0] for c in keys], [c[1] for c in keys],
                    [cells[c] for c in keys], links, name='soldier')
    node_set = set(node_cells)
    nodes = [(0, index[c]) for c in node_cells]
    nid = {c: n for n, c in enumerate(node_cells)}
    edges = []
    for (i, j) in node_cells:
        for step in (2, 4):                                    # the islet's nodes are 2 cells apart, the others 4
            for di, dj in ((step, 0), (0, step)):
                other = (i + di, j + dj)
                if other in node_set and (step == 2) == (i < 30):          # the islet lies at x < -68 (i < 30)
                    edges.append((nid[(i, j)], nid[other], rsnv.EDGE_WALK, None, True))
    header = {'sea_level': 0.0, 'radius': 0.35, 'height': 1.8, 'step': 0.37, 'water_depth': 1.4, 'sha256': '0' * 8}

    def write(flags):
        rg.write_world(LV, 'jeep', [cs], nodes, edges, [], path, (-2, -2), header, {}, island_flags=flags,
                       flag_tight=False, log=quiet)
        return rsnv.load(path)
    S = write(None)
    carrier = int(S.node_island[next(n for n, c in enumerate(node_cells) if cells[c] == 10.0)])
    return write({carrier: rsnv.ISLD_CARRIER})


def tri(*corners):
    return np.array(corners, np.float32)


def collision():
    """The deck (two triangles at 10 m over x -8..8, z -116..-86), a rock in the surf, and nothing else."""
    deck = [tri((-9, 10, -117), (9, 10, -117), (9, 10, -85)), tri((-9, 10, -117), (9, 10, -85), (-9, 10, -85))]
    rx, rz = ROCK_AT
    rock = [tri((rx - 5, -3, rz), (rx + 5, -3, rz), (rx, 4, rz + 5)), tri((rx - 5, -3, rz), (rx, 4, rz - 5), (rx + 5, -3, rz))]
    return np.stack(deck + rock)


def forbid():
    """Small plates (0.5 m) every metre over FORBID_BOX: the export judges ForbidPathfinding by triangle centres."""
    x0, x1, z0, z1 = FORBID_BOX
    return np.stack([tri((x, 0.5, z), (x + 0.5, 0.5, z), (x, 0.5, z + 0.5))
                     for x in np.arange(x0, x1 + 0.1) for z in np.arange(z0, z1 + 0.1)])


@contextlib.contextmanager
def made_up_level(tmp_path, patches=(), sea_level=0.0):
    """A prepared, built, checked and exported made-up level under tmp_path (BF1943_WORK); patches: (object, name,
    value) set for the duration. Yields a namespace: files (navkit paths), hf, soldier (path), S (the world)."""
    saved_env = os.environ.get('BF1943_WORK')
    saved = [(obj, name, getattr(obj, name)) for obj, name, _value in patches]
    os.environ['BF1943_WORK'] = str(tmp_path / 'work')
    try:
        for obj, name, value in patches:
            setattr(obj, name, value)
        files = nj.paths(LV)
        os.makedirs(files['objects'])
        os.makedirs(files['navkit'])
        hf_path = os.path.join(files['objects'], 'heightfield.res')
        with open(hf_path, 'wb') as f:
            f.write(heightfield_bytes())
        hf = terrain.read(hf_path)
        np.save(os.path.join(files['objects'], 'all_tris.npy'), collision())
        np.save(os.path.join(files['objects'], 'forbid_tris.npy'), forbid())
        soldier = os.path.join(files['navkit'], f'{LV}.rsnv')
        S = write_soldier(soldier, hf)
        st = {'preparation': PREP, 'game': str(tmp_path / 'game'), 'sea_level': sea_level, 'sector_offsets': [-2, -2],
              'built': 'today', 'build_from': {'preparation': PREP, 'hpd_sha256': HPD}, 'checked': 'today ok',
              'exported': {'sha256': nj.sha256(soldier), 'hpd_sha256': HPD}}
        nj.write(LV, st)
        yield SimpleNamespace(files=files, hf=hf, soldier=soldier, S=S, hf_path=hf_path)
    finally:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)
        if saved_env is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = saved_env


def offm_rows(w, kind):
    return [r for r in w.offmesh if r[0] == kind]


def edges_from(w, n):
    return [(int(w.edge_target[k]), int(w.edge_kind[k])) for k in range(w.node_first_edge[n], w.node_first_edge[n + 1])]


# ---- seanav1943 --------------------------------------------------------------------------------------------------

DECK_SPAWN = {'vehicle': 'lcvp-landingcraft', 'team': 1, 'pos': [0.0, 10.5, -112.0], 'fwd': [0.0, 0.0, 1.0],
              'yaw': 0.0, 'entries': [{'point': [0.0, 10.5, -110.0], 'radius': 8.0, 'order': 0}], 'index': 7}
SEA_SPAWN = {'vehicle': 'lcvp-landingcraft', 'team': 2, 'pos': [72.0, 0.5, 0.0], 'fwd': [-1.0, 0.0, 0.0],
             'yaw': -math.pi / 2, 'entries': [], 'index': 8}
LAND_SPAWN = {'vehicle': 'lcvp-landingcraft', 'team': 0, 'pos': [-10.0, 2.5, 10.0], 'fwd': [0.0, 0.0, 1.0],
              'yaw': 0.0, 'entries': [], 'index': 9}


def craft(*spawns):
    return lambda lv, game=None: ([dict(s, entries=list(s['entries'])) for s in spawns], None)


def test_sea_world(tmp_path):
    log = []
    with made_up_level(tmp_path, [(sn, 'craft_spawns', craft(DECK_SPAWN, SEA_SPAWN, LAND_SPAWN))]) as L:
        # a second deck craft on a deck longer than 300 m ahead: no deck end found, no lane
        long_deck = [tri((95, 10, -125), (105, 10, -125), (105, 10, 200)), tri((95, 10, -125), (105, 10, 200), (95, 10, 200))]
        np.save(os.path.join(L.files['objects'], 'all_tris.npy'), np.concatenate([collision(), np.stack(long_deck)]))
        sn.craft_spawns = craft(DECK_SPAWN, SEA_SPAWN, LAND_SPAWN, dict(DECK_SPAWN, pos=[100.0, 10.5, -120.0], entries=[], index=10))
        path, stats = sn.build(LV, log=log.append)
        assert path == sn.paths(LV)['rsnv'] and os.path.isfile(path)
        assert any('craft 10 at' in m and 'no deck end within 300 m' in m for m in log), log
        w = rsnv.load(path)                                     # validates as it loads
        st = nj.read(LV)
        stamp = vc.stamp(st, 'worlds', 'lcvp')
        assert stamp['sha256'] == nj.sha256(path) and stamp['soldier'] == nj.sha256(L.soldier), stamp
        assert stats['lanes'] == 1 and stats['boarding'] == 4 and stats['landing zones'] >= 3, stats
        assert w.meta['class'] == 'lcvp' and w.meta['layer'] == vc.CLASS_IDS['lcvp']
        # the launch lane: deck node -> deck end one way (a drive), then a drop into the sea
        lane = w.meta['lanes'][0]
        assert lane['spawn'] == 7 and lane['team'] == 1 and lane['water node'] is not None
        deck, end, water = lane['deck node'], lane['deck end node'], lane['water node']
        assert edges_from(w, deck) == [(end, rsnv.EDGE_WALK)], edges_from(w, deck)
        assert edges_from(w, end) == [(water, rsnv.EDGE_DROP)], edges_from(w, end)
        assert all(t != deck for n in range(len(w.node_pos)) for t, _k in edges_from(w, n)), 'nothing drives back up'
        assert abs(w.node_pos[deck][1] - 10.5) < 0.01 and abs(w.node_pos[water][1]) < 0.01
        # the deck ends at z -85: the lane is about 27 m, the craft lands past it
        assert 24.0 <= lane['deck length'] <= 36.0, lane
        assert w.node_pos[end][2] > -90.0 and w.node_pos[water][2] > w.node_pos[end][2], (w.node_pos[end], w.node_pos[water])
        launch = offm_rows(w, sn.OFFM_LCVP_LAUNCH)
        assert len(launch) == 1 and launch[0][1] == 1 and launch[0][3] == end and launch[0][9] == water, launch
        # boarding: every spawn; the craft on land has no node of its own (0xFFFFFFFF)
        board = {r[4]: r for r in offm_rows(w, rsnv.OFFM_BOARD_VEHICLE)}      # keyed by the entry's x
        assert len(board) == 4 and board[100.0][3] == 0xFFFFFFFF, "the long deck's craft: no lane, no node"
        assert board[0.0][3] == deck and board[0.0][1] == 1 and board[0.0][8] == 8.0
        assert board[72.0][3] != 0xFFFFFFFF and board[72.0][1] == 2
        assert board[-10.0][3] == 0xFFFFFFFF and board[-10.0][1] == 3
        boarding = {b['spawn']: b for b in w.meta['boarding']}
        assert boarding[7]['on deck'] and not boarding[8]['on deck'] and boarding[9]['node'] is None
        # the deck craft's driver boards from the carrier: the soldier node there is on the carrier island
        assert L.S.islands[boarding[7]['soldier island']][2] & rsnv.ISLD_CARRIER
        # landing zones: on the main island's beach, 25 m apart, none behind the ForbidPathfinding plate
        zones = w.meta['landing zones']
        alight = offm_rows(w, sn.OFFM_ALIGHT_ZONE)
        assert len(zones) == len(alight) == stats['landing zones']
        for zone in zones:
            assert int(L.S.node_island[zone['soldier']]) == 0, zone
            assert w.node_flags[zone['node']] & rsnv.NAUX_SHALLOW
            assert 0.9 <= zone['depth'] <= 3.0 and 35.0 < math.hypot(zone['pos'][0], zone['pos'][2]) < 50.0, zone
            x0, x1, z0, z1 = FORBID_BOX
            assert not (x0 + 1.5 < zone['bow'][0] < x1 - 1.5 and z0 + 1.5 < zone['bow'][1] < z1 - 1.5), zone
        for a in range(len(zones)):
            for b in range(a):
                pa, pb = zones[a]['pos'], zones[b]['pos']
                assert math.hypot(pa[0] - pb[0], pa[2] - pb[2]) >= sn.ZONE_SPACING
        rejected = next(m for m in log if 'rejected candidates' in m)
        for why in ('ForbidPathfinding collision between bow and soldier ground', 'no soldier ground within reach',
                    'nearest soldier ground not the main island'):
            assert why in rejected, (why, rejected)
        for sx, sz in SHOALS:
            assert all(math.hypot(z['pos'][0] - sx, z['pos'][2] - sz) > 20.0 for z in zones), 'no zone at a shoal'
        # no node in the lake (a water body no craft reaches) nor on the rock
        sea_nodes = w.node_pos[w.node_area >= 0]
        assert np.hypot(sea_nodes[:, 0] - LAKE_AT[0], sea_nodes[:, 2] - LAKE_AT[1]).min() > 15.0
        assert np.hypot(sea_nodes[:, 0] - ROCK_AT[0], sea_nodes[:, 2] - ROCK_AT[1]).min() > sn.vc.value('lcvp', 'radius')
        assert any('kept 1 water bodies' in m for m in log), log
        # the sea graph is one island (pieces joined); the lane's deck end joins it through its drop
        assert len(set(w.node_island.tolist())) <= 2


def test_sea_no_spawns(tmp_path):
    with made_up_level(tmp_path, [(sn, 'craft_spawns', craft())]):
        old = sn.paths(LV)['rsnv']
        with open(old, 'wb') as f:
            f.write(b'an old landing craft world')
        notes = []
        got = sn.build(LV, log=notes.append)
        assert got == (None, {'spawns': 0}), got
        assert not os.path.isfile(old)
        assert vc.stamp(nj.read(LV), 'worlds', 'lcvp')['absent'] == 'no landing craft spawns on the level'
        assert any('no landing craft spawns' in m for m in notes)


def test_sea_main_refuses_without_soldier(tmp_path):
    with made_up_level(tmp_path):
        missing = str(tmp_path / 'none.rsnv')
        try:
            sn.main(['seanav1943.py', LV, '--soldier', missing])
        except FileNotFoundError as e:
            assert 'no soldier world' in str(e) and missing in str(e) and f'navexport1943.py {LV}' in str(e)
        else:
            raise AssertionError('main went on without a soldier world')


def test_sea_main_builds(tmp_path):
    with made_up_level(tmp_path, [(sn, 'craft_spawns', craft(SEA_SPAWN))]):
        assert sn.main(['seanav1943.py', LV]) == 0
        w = rsnv.load(sn.paths(LV)['rsnv'])
        assert not offm_rows(w, sn.OFFM_LCVP_LAUNCH) and len(offm_rows(w, rsnv.OFFM_BOARD_VEHICLE)) == 1


def test_sea_helpers():
    T = np.array([[[0, 1, 0], [4, 1, 0], [0, 1, 4]],          # a floor at 1 m
                  [[0, 3, 0], [4, 3, 0], [0, 3, 4]],          # one at 3 m over it
                  [[0, 2, 0], [4, 2, 0], [8, 2, 0]]], np.float32)   # a line seen from above: skipped
    assert sn.deck_tops(T, 1.0, 1.0, 0.0, 5.0) == 3.0
    assert sn.deck_tops(T, 1.0, 1.0, 0.0, 2.0) == 1.0, 'the highest within the band'
    assert sn.deck_tops(T, 3.9, 3.9, 0.0, 5.0) is None, 'outside the triangles'
    assert sn.deck_tops(T, 1.0, 1.0, 4.0, 5.0) is None, 'no surface in the band'
    T2 = T[:1].copy()                                        # another array: the cache follows it
    assert sn.deck_tops(T2, 1.0, 1.0, 0.0, 5.0) == 1.0
    # a diagonal step squeezing between two blocked side cells is no passage
    nav = np.ones((3, 3), bool)
    nav[0, 1] = nav[1, 0] = False
    assert not sn.cell_line(nav, (0, 0), (1, 1))
    nav[1, 0] = True
    assert sn.cell_line(nav, (0, 0), (1, 1)) and sn.cell_line(nav, (2, 2), (2, 2))
    nav[2, 2] = False
    assert not sn.cell_line(nav, (2, 2), (2, 2)), 'a single blocked cell'
    band, n = sn.rasterise_band(np.zeros((0, 3, 3), np.float32), 0.0, 1.0, 0.0, 0.0, 4, 4, 1.0)
    assert n == 0 and not band.any()
    small = np.array([[[1.1, 0.5, 1.1], [1.3, 0.5, 1.1], [1.1, 0.5, 1.3]]], np.float32)   # under a cell
    band, n = sn.rasterise_band(small, 0.0, 1.0, 0.0, 0.0, 4, 4, 1.0)
    assert n == 1 and band.sum() == 1 and band[1, 1], 'a small triangle marks the cell of its centroid'
    assert [sn.team_mask(t) for t in (1, 2, 0, None)] == [1, 2, 3, 3]
    # connect_extra: an extra cell links to the nodes in sight within 10 cells, a node already there is skipped
    mask = np.ones((1, 30), bool)
    mask[0, 15] = False
    idx = np.full((1, 30), -1, np.int64)
    idx[mask] = np.arange(int(mask.sum()))
    ii = np.nonzero(mask[0])[0]
    cs = rg.CellSet(2.0, 0.0, 0.0, ii, np.zeros(len(ii), np.int64), np.zeros(len(ii)), rg.grid_links(idx))
    k_of = {int(i): k for k, i in enumerate(ii.tolist())}
    nodes, edges = sn.connect_extra(cs, mask, [k_of[0], k_of[20]], set(), {k_of[8], k_of[0]})
    assert nodes == [k_of[0], k_of[20], k_of[8]] and edges == {(k_of[0], k_of[8])}, (nodes, edges)


def test_craft_spawns(tmp_path):
    """craft_spawns: boats only, heading from the spawn's matrix, entries from the game copy."""
    yaw = math.radians(30)
    m = np.eye(4)
    m[2, :3] = [math.sin(yaw), 0.0, math.cos(yaw)]
    m[0, :3] = [math.cos(yaw), 0.0, -math.sin(yaw)]

    def spawn(name, index, category='vehicle spawn', position=(1.0, 2.0, 3.0)):
        child = SimpleNamespace(attr=lambda k: f'objects/vehicles/beach/{name}/{name}/x' if name else 'null')
        return SimpleNamespace(category=category, position=position, matrix=m.tolist(), team=2, index=index,
                               element=SimpleNamespace(child=lambda k: child))
    level = SimpleNamespace(entities=[spawn('lcvp-landingcraft', 1), spawn('willys-mb', 2), spawn(None, 3),
                                      spawn('lcvp-landingcraft', 4, position=None),
                                      spawn('lcvp-landingcraft', 5, category='ladder')], objectives=[])
    seen = []
    patches = [(level1943, 'load', lambda lv, game=None, log=None: seen.append(game) or level),
               (game1943, 'find_game', lambda given=None: given or 'default'),
               (game1943, 'Game', lambda folder, lv: SimpleNamespace(folder=folder)),
               (vehicles1943, 'entry_points', lambda g, e: [{'point': [9.0, 9.0, 9.0], 'radius': 8.0, 'order': 0,
                                                             'game': g.folder}])]
    saved = [(o, n, getattr(o, n)) for o, n, _v in patches]
    try:
        for o, n, v in patches:
            setattr(o, n, v)
        out, got_level = sn.craft_spawns(LV, game='copy')
        assert got_level is level and seen == ['copy']
        assert [s['index'] for s in out] == [1] and out[0]['vehicle'] == 'lcvp-landingcraft'
        assert abs(out[0]['yaw'] - yaw) < 1e-9 and out[0]['entries'][0]['game'] == 'copy'
        assert abs(math.hypot(out[0]['fwd'][0], out[0]['fwd'][2]) - 1.0) < 1e-9
        seeds = gn.spawn_seeds(LV, 'jeep', game='copy')
        assert [(s['index'], s['class']) for s in seeds] == [(2, 'jeep')] and abs(seeds[0]['yaw'] - yaw) < 1e-9
        assert gn.spawn_seeds.objectives == []
        planes, _ = an.plane_spawns(LV, game='copy')
        assert planes == [], 'no aircraft among the spawns'
        level.entities.append(spawn('corsair', 6))
        planes, _ = an.plane_spawns(LV, game='copy')
        assert [p['index'] for p in planes] == [6] and abs(planes[0]['yaw'] - yaw) < 1e-9
        # the entry point: ENTRY_LOCAL turned by the spawn's matrix and moved to its translation (here none)
        want = np.array(an.ENTRY_LOCAL) @ m[:3, :3]
        assert np.allclose(planes[0]['entry'], want)
    finally:
        for o, n, v in reversed(saved):
            setattr(o, n, v)


# ---- groundnav1943 -----------------------------------------------------------------------------------------------

YARD = (-16.0, -16.0, 40, 40)             # x0, z0, nx, nz of the jeep's samples (0.8 m)
WALL_I = 20                               # blocked column across the yard: west and east halves
ROOF = (5, 9)                             # columns i, j in 5..9: a roof at 6 m over the ground
GROUND_FORBID = (-12.0, 11.0)             # a ForbidPathfinding plate on the yard's west half


def yard_columns():
    x0, z0, nx, nz = YARD
    cols = []
    for j in range(nz):
        for i in range(nx):
            col = []
            if ROOF[0] <= i <= ROOF[1] and ROOF[0] <= j <= ROOF[1]:
                col.append((6.0, (0.0, 1.0, 0.0), 0))
            col.append((2.0, (0.0, 1.0, 0.0), 1 if i == WALL_I else 0))
            cols.append(col)
    return cols


def write_samples(path, cls='jeep'):
    x0, z0, nx, nz = YARD
    out = bytearray(navsample1943.HEADER.pack(b'ONS1', 1, 0.8, x0, z0, nx, nz, 20.0, -10.0, vc.value(cls, 'radius'),
                                              vc.value(cls, 'step'), vc.value(cls, 'height'), 0.0, 0))
    for col in yard_columns():
        out.append(len(col))
        for y, normal, flags in col:
            out += struct.pack('<f3bB', y, *[int(round(v * 127)) for v in normal], flags)
    with open(path, 'wb') as f:
        f.write(bytes(out))


def ground_level(tmp_path, seeds, objectives=()):
    def spawn_seeds(lv, cls, game=None):
        spawn_seeds.objectives = list(objectives)
        spawn_seeds.games.append(game)
        return [dict(s) for s in seeds]
    spawn_seeds.games = []
    spawn_seeds.objectives = []
    return made_up_level(tmp_path, [(gn, 'spawn_seeds', spawn_seeds)]), spawn_seeds


def stamp_samples(cls='jeep'):
    st = nj.read(LV)
    vc.record(LV, 'samples', vc.inputs(st, LV, cls, sampling=True), cls)


def test_ground_world(tmp_path):
    seeds = [{'vehicle': 'willys-mb', 'class': 'jeep', 'team': 1, 'pos': [-8.0, 2.0, -4.0], 'yaw': 0.0,
              'entries': [{'point': [-9.0, 2.0, -4.0], 'radius': 4.0, 'order': 0},
                          {'point': [-7.0, 2.0, -4.0], 'radius': 4.0, 'order': 1}], 'index': 3},
             {'vehicle': 'm4-sherman', 'class': 'tank', 'team': 2, 'pos': [8.0, 2.0, 0.0], 'yaw': 0.0, 'entries': [],
              'index': 4}]
    objectives = [{'name': 'flag A', 'position': [-8.0, 2.0, 8.0]}]
    ctx, spawn_seeds = ground_level(tmp_path, seeds, objectives)
    with ctx as L:
        os.makedirs(gn.paths(LV)['vehicles'])
        write_samples(gn.paths(LV)['samples']('jeep'))
        stamp_samples()
        log = []
        out, stats = gn.build(LV, 'jeep', log=log.append)
        assert out == gn.paths(LV)['rsnv']('jeep') and spawn_seeds.games == [str(tmp_path / 'game')]
        w = rsnv.load(out)
        assert vc.stamp(nj.read(LV), 'worlds', 'jeep')['sha256'] == nj.sha256(out)
        # the halves either side of the wall, where a ground vehicle stands (the jeep's and the tank's spawn: any
        # ground vehicle's keeps its component), two islands; not the roof, where none stands
        wall_x = YARD[0] + WALL_I * 0.8
        assert (w.node_pos[:, 0] < wall_x).any() and (w.node_pos[:, 0] > wall_x).any()
        assert not (np.abs(w.node_pos[:, 0] - wall_x) < 1.0).any(), 'no node at the wall'
        assert np.allclose(w.node_pos[:, 1], 2.0), 'no node on the roof'
        west = w.node_pos[:, 0] < wall_x
        assert len(set(w.node_island.tolist())) == 2 and not set(w.node_island[west]) & set(w.node_island[~west])
        board = offm_rows(w, rsnv.OFFM_BOARD_VEHICLE)
        assert len(board) == 1 and board[0][4] == -9.0 and board[0][1] == 1, board
        assert board[0][9] != 0xFFFFFFFF and int(L.S.node_island[board[0][9]]) == 0, 'the driver boards from the island'
        alight = w.meta['alighting']
        assert [a['target'] for a in alight] == ['flag A', 'spawn 3'], alight
        assert len(offm_rows(w, rg.OFFM_ALIGHT_ZONE)) == 2
        assert any('2 ground-vehicle spawns; kept 2 components' in m for m in log), log
        assert stats['nodes'] == len(w.node_pos) and 'seconds' in stats


def ground_forbid_tris():
    fx, fz = GROUND_FORBID
    return np.stack([tri((fx - 3, 2.0, fz - 3), (fx + 3, 2.0, fz - 3), (fx, 2.0, fz + 3))])


def test_ground_refusals(tmp_path):
    ctx, _ = ground_level(tmp_path, [])
    with ctx:
        try:
            gn.build(LV, 'jeep', log=quiet)
        except FileNotFoundError as e:
            assert 'not sampled yet' in str(e) and f'groundnav1943.py sample {LV} jeep' in str(e)
        else:
            raise AssertionError('built without samples')
        os.makedirs(gn.paths(LV)['vehicles'])
        write_samples(gn.paths(LV)['samples']('jeep'))
        try:                                         # samples on disk, no stamp
            gn.build(LV, 'jeep', log=quiet)
        except vc.StaleError as e:
            assert 'not of the current preparation' in str(e)
        else:
            raise AssertionError('built on unstamped samples')
        assert not gn.samples_current(LV, 'jeep')
        stamp_samples()
        assert gn.samples_current(LV, 'jeep') and not gn.samples_current(LV, 'tank')
        try:
            gn.build(LV, 'jeep', log=quiet, game=str(tmp_path / 'another copy'))
        except vc.StaleError as e:
            assert 'prepared from' in str(e)
        else:
            raise AssertionError('built on another game copy')


def test_ground_build_forbid(tmp_path):
    """build reads forbid_tris.npy: no drivable surface on the plate (the level's plate is on the west beach, far
    from the yard; here one on the yard)."""
    seeds = [{'vehicle': 'willys-mb', 'class': 'jeep', 'team': 1, 'pos': [-8.0, 2.0, -4.0], 'yaw': 0.0, 'entries': [],
              'index': 3}]
    ctx, _ = ground_level(tmp_path, seeds)
    with ctx:
        np.save(os.path.join(gn.paths(LV)['objects'], 'forbid_tris.npy'), ground_forbid_tris())
        os.makedirs(gn.paths(LV)['vehicles'])
        write_samples(gn.paths(LV)['samples']('jeep'))
        stamp_samples()
        out, _stats = gn.build(LV, 'jeep', log=quiet)
        w = rsnv.load(out)
        on_plate = []
        for k, a in enumerate(w.areas):
            for jj in range(a.height):
                for ii in range(a.width):
                    if w.walkable(a, ii, jj):
                        x, z = a.x0 + ii * a.pitch, a.z0 + jj * a.pitch
                        if abs(x - GROUND_FORBID[0]) < 1.0 and abs(z - GROUND_FORBID[1]) < 1.0:
                            on_plate.append((k, x, z))
        assert not on_plate, on_plate[:4]


def test_grid_file_and_forbid(tmp_path):
    path = str(tmp_path / 'samples.bin')
    write_samples(path)
    g = gn.Grid(path)
    ref = gn.Grid.from_columns(0.8, YARD[0], YARD[1], YARD[2], YARD[3], yard_columns(), 1.5, 0.3, 2.0)
    assert (g.nx, g.nz, g.n) == (ref.nx, ref.nz, ref.n) and np.array_equal(g.start, ref.start)
    assert np.allclose(g.y, ref.y) and np.array_equal(g.flags, ref.flags) and np.allclose(g.normal, ref.normal)
    assert (g.radius, g.step) == (np.float32(vc.value('jeep', 'radius')), np.float32(vc.value('jeep', 'step')))
    assert np.allclose(g.xz(ref.start[1]), (YARD[0] + 0.8, YARD[1]))
    with open(path, 'r+b') as f:
        f.write(b'XXXX')
    try:
        gn.Grid(path)
    except ValueError as e:
        assert 'not a navsample1943 file' in str(e)
    else:
        raise AssertionError('read a file of another kind')
    # forbid_surfaces: the plate's surfaces, not the roof over the ground there; none without triangles
    mask = gn.forbid_surfaces(ref, ground_forbid_tris())
    on = np.nonzero(mask)[0]
    assert len(on) and np.allclose(ref.y[on], 2.0)
    x, z = ref.xz(on)
    assert (np.hypot(x - GROUND_FORBID[0], z - GROUND_FORBID[1]) <= 6.0).all()
    assert not gn.forbid_surfaces(ref, None).any() and not gn.forbid_surfaces(ref, np.zeros((0, 3, 3))).any()
    off_grid = np.stack([tri((500, 2, 500), (510, 2, 500), (500, 2, 510))])
    assert not gn.forbid_surfaces(ref, off_grid).any()
    # a drivable roof needs ground under its whole disc: the roof's 5 x 5 columns keep only the 3 x 3 inside
    ok = gn.drivable(ref, 'jeep', sea_level=0.0, log=quiet)
    roof = np.nonzero(ok & (ref.y == 6.0))[0]
    assert len(roof) == 9, len(roof)
    deep = gn.drivable(ref, 'jeep', sea_level=3.0, log=quiet)
    assert not deep[ref.y == 2.0].any(), '1 m of water over the ground: deeper than the jeep fords'


def test_ground_prepare_and_sample(tmp_path):
    with made_up_level(tmp_path):
        objects = gn.paths(LV)['objects']
        tris = np.stack([tri((0, 0, 0), (1, 0, 0), (0, 0, 1)), tri((5, 0, 5), (6, 0, 5), (5, 0, 6)),
                         tri((9, 1, 9), (9, 2, 9), (9, 1, 8))])
        np.save(os.path.join(objects, 'all_tris.npy'), tris)
        np.save(os.path.join(objects, 'exclude_tris.npy'), tris[1:2])
        np.save(os.path.join(objects, 'dynamic_tris.npy'), tris[2:3])
        for name in ('soldier_spawns.json', 'object_boxes.json'):
            with open(os.path.join(objects, name), 'w', encoding='utf-8', newline='') as f:
                f.write(json.dumps([name]))
        msgs = []
        assert gn.prepare(LV, log=msgs.append) == (1, 3)
        kept = np.load(os.path.join(gn.paths(LV)['vobjects'], 'all_tris.npy'))
        assert np.array_equal(kept, tris[:1]) and '1 of 3 triangles' in msgs[0]
        with open(os.path.join(gn.paths(LV)['vobjects'], 'object_boxes.json'), encoding='utf-8') as f:
            assert json.load(f) == ['object_boxes.json']
        assert vc.current(nj.read(LV), 'collision', vc.inputs(nj.read(LV), LV))
        # an empty exclusion list leaves everything
        np.save(os.path.join(objects, 'exclude_tris.npy'), np.zeros((0, 3, 3), np.float32))
        os.remove(os.path.join(objects, 'dynamic_tris.npy'))
        assert gn.prepare(LV, log=quiet) == (3, 3)
        # the sampler's command: the class's sampling parameters and the sea level
        args = gn.sample_command(LV, 'tank')
        assert args[args.index('--radius') + 1] == str(vc.value('tank', 'radius'))
        assert args[args.index('--step') + 1] == str(vc.value('tank', 'step'))
        assert args[-2:] == ['--sea-level', '0.0'] and args[6].endswith('samples_tank.bin')
        runs = SimpleNamespace(args=[], code=0)

        def run(args, stdout=None, stderr=None):
            runs.args.append(args)
            with open(args[6], 'wb') as f:
                f.write(b'samples')
            return SimpleNamespace(returncode=runs.code)
        saved = gn.subprocess
        gn.subprocess = SimpleNamespace(run=run, STDOUT=-2)
        try:
            gn.sample(LV, 'tank', log=msgs.append)
            assert gn.samples_current(LV, 'tank') and 'sampled in' in msgs[-1]
            runs.code = 3
            try:
                gn.sample(LV, 'jeep', log=quiet)
            except RuntimeError as e:
                assert 'the sampler failed (exit 3)' in str(e) and 'sample_jeep.log' in str(e)
            else:
                raise AssertionError('a failing sampler was stamped')
            assert not gn.samples_current(LV, 'jeep')
        finally:
            gn.subprocess = saved
        os.remove(os.path.join(objects, 'all_tris.npy'))
        try:
            gn.prepare(LV, log=quiet)
        except FileNotFoundError as e:
            assert 'the level is not prepared' in str(e)
        else:
            raise AssertionError('prepared without the level\'s collision')


def test_ground_main():
    calls = []
    saved = gn.prepare, gn.sample, gn.build
    gn.prepare = lambda lv, log=print: calls.append(('prepare', lv))
    gn.sample = lambda lv, cls, log=print: calls.append(('sample', lv, cls))
    gn.build = lambda lv, cls, log=print, game=None: calls.append(('build', lv, cls))
    try:
        assert gn.main(['groundnav1943.py', 'prepare', 'lv']) == 0 and calls == [('prepare', 'lv')]
        calls.clear()
        assert gn.main(['groundnav1943.py', 'all', 'lv']) == 0
        assert calls == [('sample', 'lv', 'jeep'), ('build', 'lv', 'jeep'), ('sample', 'lv', 'tank'), ('build', 'lv', 'tank')]
        calls.clear()
        assert gn.main(['groundnav1943.py', 'build', 'lv', 'tank']) == 0 and calls == [('build', 'lv', 'tank')]
    finally:
        gn.prepare, gn.sample, gn.build = saved


def test_grid_line_and_join():
    # a strip 10 x 3 with a wall in the middle row's column 5 only: lines across the middle row are cut there
    cols = []
    for j in range(3):
        for i in range(10):
            cols.append([(1.0, (0, 1, 0), 1 if (i == 5 and j == 1) else 0)])
    g = gn.Grid.from_columns(1.0, 0.0, 0.0, 10, 3, cols)
    ok = (g.flags & 1) == 0
    table, pairs = gn.link_table(g, ok, 0.3)
    k = {(int(g.i[s]), int(g.j[s])): s for s in range(g.n)}
    assert gn.grid_line(g, table, k[(0, 1)], k[(9, 1)]) is None, 'through the wall'
    assert gn.grid_line(g, table, k[(0, 0)], k[(9, 0)]) is not None
    assert gn.grid_line(g, table, k[(0, 0)], k[(0, 0)]) == [k[(0, 0)]]
    assert gn.grid_line(g, table, k[(4, 0)], k[(6, 2)]) is None, 'the diagonal passes the wall cell'
    labels = gn.components(g.n, pairs)
    assert len(set(labels[ok].tolist())) == 1
    # two nodes on one component with no edge: join_pieces walks round the wall and joins them
    nodes, edges = gn.join_pieces(g, ok, table, labels, [k[(0, 1)], k[(9, 1)]], set(), quiet)
    idx = {n: i for i, n in enumerate(nodes)}
    pieces = gn.components(len(nodes), np.array([(idx[a], idx[b]) for a, b in edges], np.int64).reshape(-1, 2))
    assert len(set(pieces.tolist())) == 1 and len(nodes) > 2
    assert all(gn.grid_line(g, table, a, b) is not None for a, b in edges)
    assert gn.components(3, np.zeros((0, 2), np.int64)).tolist() == [0, 1, 2]


# ---- airnav1943 --------------------------------------------------------------------------------------------------

PLANE_SPAWN = {'vehicle': 'corsair', 'team': 1, 'pos': [-20.0, 2.5, -10.0], 'fwd': [1.0, 0.0, 0.0],
               'yaw': math.pi / 2, 'entry': [-20.5, 2.7, -10.0], 'index': 11}


def planes(*spawns, objectives=()):
    level = SimpleNamespace(objectives=list(objectives))
    return lambda lv, game=None: ([dict(s) for s in spawns], level)


def test_air_world(tmp_path):
    objectives = [{'name': 'flag A', 'position': [8.0, 2.0, 8.0]}, {'name': 'carrier base', 'position': [0.0, 10.0, -100.0]},
                  {'name': 'no position'}]
    log = []
    with made_up_level(tmp_path, [(an, 'plane_spawns', planes(PLANE_SPAWN, objectives=objectives))]) as L:
        path, stats = an.build(LV, log=log.append)
        w = rsnv.load(path)
        assert vc.stamp(nj.read(LV), 'worlds', 'plane')['sha256'] == nj.sha256(path)
        assert stats['lanes'] == 1 and stats['drops'] == 2, stats
        lane = w.meta['lanes'][0]
        assert lane['spawn'] == 11 and lane['centre line clear'] and not lane['straight lane blocked'], lane
        assert lane['length m'] == vc.value('plane', 'takeoff_run') and lane['heading offset deg'] == 0.0
        start, end = lane['start node'], lane['end node']
        assert edges_from(w, start) == [(end, rsnv.EDGE_WALK)]
        assert edges_from(w, end) == [(lane['climb-out node'], rsnv.EDGE_JUMP)]
        assert (start, rsnv.EDGE_DROP) in edges_from(w, lane['approach node'])
        assert abs(w.node_pos[start][1] - 2.5) < 1e-4 and w.node_pos[end][0] > w.node_pos[start][0] + 100
        # cruise nodes: at least the floor's sea clearance over the sea, and over the island's 2 m plateau
        cruise = [n for n in range(len(w.node_pos)) if n not in (start, end)]
        assert w.node_pos[cruise, 1].min() >= an.SEA_CLEAR + vc.value('plane', 'cruise_agl') - 1e-3
        # drops: the flag, and the islet (an island that is no carrier); not the carrier base
        drops = {d['target']: d for d in w.meta['drops']}
        assert set(drops) == {'flag A', 'island 1'}, drops
        assert drops['flag A']['soldier island'] == 0 and drops['island 1']['soldier island'] == 1
        assert not L.S.islands[1][2] & rsnv.ISLD_CARRIER
        assert len(offm_rows(w, an.OFFM_DROP)) == 2
        board = offm_rows(w, rsnv.OFFM_BOARD_VEHICLE)
        assert len(board) == 1 and board[0][3] == start and board[0][8] == an.ENTRY_RADIUS and board[0][9] != 0xFFFFFFFF
        assert any('1 take-off lanes (0 with the spawn heading blocked' in m for m in log), log


def test_air_no_water_and_main(tmp_path):
    """A level without water flies over its terrain; main refuses without a soldier world and builds with one."""
    with made_up_level(tmp_path, [(an, 'plane_spawns', planes())], sea_level=None) as L:
        try:
            an.main(['airnav1943.py', LV, '--soldier', str(tmp_path / 'none.rsnv')])
        except FileNotFoundError as e:
            assert 'no soldier world' in str(e)
        else:
            raise AssertionError('main went on without a soldier world')
        assert an.main(['airnav1943.py', LV]) == 0
        w = rsnv.load(an.paths(LV)['rsnv'])
        lowest = float(L.hf.heights.min()) - 10.0                # soldier1943.NO_WATER_BELOW under the terrain
        assert abs(w.sea_level - lowest) < 1e-3 and w.meta['lanes'] == []


def test_air_helpers():
    hf = terrain.read(heightfield_bytes())
    # flight_box: the land (to the shoals' samples at -72 and 72, the last one's cell ending at 76) and the points,
    # MARGIN around; neither: refused
    lo, hi = an.flight_box(hf, 0.0, [])
    assert lo.tolist() == [-72 - an.MARGIN] * 2 and hi.tolist() == [76 + an.MARGIN] * 2, (lo, hi)
    lo2, hi2 = an.flight_box(hf, 0.0, [[200.0, 0.0, -300.0]])
    assert hi2[0] == 200.0 + an.MARGIN and lo2[1] == -300.0 - an.MARGIN
    try:
        an.flight_box(hf, 50.0, [])
    except ValueError as e:
        assert 'no land above the sea' in str(e)
    else:
        raise AssertionError('a box with no land and no points')
    lo3, hi3 = an.flight_box(hf, 50.0, [[1.0, 0.0, 2.0]])
    assert list(lo3) == [1.0 - an.MARGIN, 2.0 - an.MARGIN]
    # floor_grid: terrain and collision per cell, at least the sea; one big triangle raises every cell it spans
    T = np.stack([tri((0, 30, 0), (1, 30, 0), (0, 30, 1)), tri((-60, 20, -60), (-20, 20, -60), (-60, 20, -20))])
    top = an.floor_grid(T, hf, -64.0, -64.0, 9, 9, 16.0, 0.0)
    assert top[4, 4] == 30.0, 'the small triangle in the centre cell'
    assert top[1, 1] == 20.0 and top[0, 0] >= 20.0, 'the big triangle over several cells'
    assert top[0, 4] == 0.0 and top.min() >= 0.0, 'the sea over the sea floor'
    grown = an.grow_max(np.pad(np.array([[5.0]]), 3), 1.0)
    assert grown.sum() == 25.0 and grown[3, 4] == 5.0 and grown[2, 2] == 0.0, 'a disc of radius 1: five cells'
    env = an.envelope(np.pad(np.array([[100.0]]), 3), 10.0, 0.5)
    assert env[3, 3] == 100.0 and env[3, 4] == 95.0 and abs(env[3, 6] - 85.0) < 1e-9
    floor = np.zeros((5, 5))
    floor[2, 2] = 50.0
    assert an.segment_clear(floor, 0.0, 0.0, 10.0, (0, 80, 0), (40, 80, 40), 25.0)
    assert not an.segment_clear(floor, 0.0, 0.0, 10.0, (0, 60, 0), (40, 60, 40), 25.0)
    assert an.lane_cell((3.1, 0, -2.9), 0.0, 0.0) == (2, -1)
    assert an.snap_lane((3.1, 7.0, -2.9)) == (4.0, 7.0, -2.0)
    assert [an.team_mask(t) for t in (1, 2, 5)] == [1, 2, 3]
    # obstacle_tops: the window from the height field's corner, a triangle's top in its cells
    x0, z0, tops = an.obstacle_tops(T, hf, 0.0, 0.0, 0.0, half=20.0)
    assert (x0, z0) == (-20.0, -20.0) and tops.shape == (11, 11)
    assert tops[5, 5] == 30.0 and tops.min() >= 0.0
