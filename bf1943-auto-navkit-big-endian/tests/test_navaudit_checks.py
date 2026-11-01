r"""NavKit big-endian: the navigation audit's checks on a made-up level

navaudit1943_checks on a level made up here (no game data): sample grids, a build of them by navbuild, a water mesh,
a spawn map, level outlines, object boxes and a ladder, put together as the audit's level context (what its load()
reads from a prepared level). The level, 64 m square at 0.8 m:

  west   x 0-47.2, ground at 0: the main network; a flag with a HotZone outline, a base, spawns, a tank, an AA gun,
         a repair station, a low roof over one corner (its ground left out by the headroom rule)
  gap    x 48-53.6, no ground, except one rock column that is not walkable
  east   x 54-59.6 ground at 0 under 1 m of water (the wading band), a flag; x 60-63.2 ground at -2 under 3 m of
         water (deep) with a landing craft

Each check family is run on it and its verdicts asserted: positions found and passed or failed for the right reason,
islands classified, routes between spawns and flags, the file's consistency, 0.8 m against 0.2 m ground, the water
band, the rule-off masks, the playable area, and the whole run with its JSON and Markdown report.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import json
import math
import struct

import numpy as np

import navaudit1943_checks as K
import navbuild
import navsample
import soldier1943
from bfbc2core import dbx, navdata

PITCH = 0.8
N = 80                                     # columns per side: x and z 0 .. 63.2
ROCK = (51.2, 20.0)                        # the gap's one surface (not walkable)
ROOF = (2.0, 4.4, 42.0, 44.4)              # x0, x1, z0, z1 of a roof 1.5 m over the ground
QUIET = lambda *a: None  # noqa: E731


def ground_y(x):
    return -2.0 if x > 59.8 else 0.0


def in_gap(x):
    return 47.8 < x < 53.8


def under_roof(x, z):
    return ROOF[0] <= x <= ROOF[1] and ROOF[2] <= z <= ROOF[3]


def columns(pitch, nx, nz, surfaces, min_x=0.0, min_z=0.0):
    """navsample.Samples from surfaces(x, z) -> [(y, flags)] top down."""
    counts, ys, flags = [], [], []
    for j in range(nz):
        for i in range(nx):
            col = surfaces(min_x + i * pitch, min_z + j * pitch)
            counts.append(len(col))
            ys += [y for y, _ in col]
            flags += [f for _, f in col]
    start = np.zeros(nx * nz + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    n = int(start[-1])
    return navsample.Samples(pitch, min_x, min_z, nx, nz, 50.0, -50.0, 0.35, 0.37, 1.8, 0.0, start,
                             np.array(ys, np.float32), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                             np.array(flags, np.uint8), None)


def build_surfaces(x, z):
    """The ground the build is made from: the gap is empty, the roof is a second surface."""
    if in_gap(x):
        return []
    out = [(1.5, 0)] if under_roof(x, z) else []
    return out + [(ground_y(x), 0)]


def audit_surfaces(x, z):
    """What the audit's grid sees: the build's surfaces and the rock in the gap."""
    if abs(x - ROCK[0]) < 0.1 and abs(z - ROCK[1]) < 0.1:
        return [(0.0, 1)]                  # capsule blocked
    return build_surfaces(x, z)


class Terrain:
    """A height field: 0, and -2 under the deep water. No samples (raw None): navdata caches nothing."""
    raw = None
    min = np.array([0.0, -10.0, 0.0])
    max = np.array([64.0, 10.0, 64.0])
    heights = np.array([[0.0, -2.0]])            # the samples' range (soldier1943.sea_level without water)

    def height(self, x, z):
        x = np.asarray(x, float)
        h = np.where(x > 59.8, -2.0, 0.0)
        return h if h.ndim else float(h)


class Shape:
    def __init__(self, entity, kind, points):
        self.entity, self.kind, self.points = entity, kind, np.array(points, float)


class Element:
    """The part of a dbx Element vehicle_entries reads: child('Vehicle').attr('ref')."""

    def __init__(self, ref=None):
        self.ref = ref

    def child(self, name):
        return Element.Ref(self.ref) if name == 'Vehicle' and self.ref else None

    class Ref:
        def __init__(self, ref):
            self.ref = ref

        def attr(self, k):
            return self.ref if k == 'ref' else None


class Entity:
    def __init__(self, index, short_type, position=None, team=0, guid='', matrix=None, element=None):
        self.index, self.short_type, self.team, self.guid = index, short_type, team, guid
        self.position = None if position is None else np.array(position, float)
        self.matrix, self.element = matrix, element or Element()


def square(x0, z0, x1, z1, y=0.0):
    return [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]


def dbx_file(instances):
    """A binary .dbx partition of instances [(type, {field: text})] (dbx.py's layout: TEXT fields only)."""
    names = ['']
    idx = lambda s: names.index(s) if s in names else (names.append(s), len(names) - 1)[1]  # noqa: E731

    def varint(v):
        out = b''
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out += bytes([b | 0x80])
            else:
                return out + bytes([b])
    body = varint(idx('partition')) + bytes([dbx.CONTAINER << 4, 0])
    for typ, fields in instances:
        body += varint(idx('instance')) + bytes([dbx.CONTAINER << 4 | 1]) + varint(idx('type')) + varint(idx(typ)) + b'\0'
        for name, text in fields.items():
            body += varint(idx('field')) + bytes([dbx.TEXT << 4 | 1]) + varint(idx('name')) + varint(idx(name))
            body += varint(idx(text))
        body += b'\0'
    body += b'\0\0'
    offsets, blob = [], b''
    for s in names:
        offsets.append(len(blob))
        blob += s.encode() + b'\0'
    area = 4 * len(names) + len(blob)
    return dbx.MAGIC + bytes(8) + struct.pack('>II', area, len(names)) + struct.pack(f'>{len(names)}I', *offsets) + blob + body


class Game:
    """The game copy as vehicle_entries reads it: the tank's blueprint, one entry point 1 m to its right, radius 3."""

    def data(self, name):
        if name == 'objects/vehicles/tank.dbx':
            return dbx_file([('Entity.PlayerEntryComponentData',
                              {'Transform': '/'.join(['1', '0', '0', '0', '0', '1', '0', '0', '0', '0', '1', '0',
                                                      '1', '0', '0', '1']),
                               'EntryRadius': '3', 'EntryOrderNumber': '0'}),
                             ('Entity.PlayerEntryComponentData', {'Transform': 'not numbers', 'EntryRadius': '1'})])
        return None


def spawn_map():
    flag_w = {'role': 'flag', 'name': 'West Flag', 'initial_owner': None, 'position': [10.0, 0.0, 10.0],
              'capture_radius': 15.0, 'spawns': [
                  {'kind': 'soldier', 'name': 'w1', 'team': None, 'position': [12.0, 0.2, 12.0],
                   'alternates': [[14.0, 0.0, 14.0], 'not a position']}]}
    flag_e = {'role': 'flag', 'name': 'East Flag', 'initial_owner': None, 'position': [57.0, 0.0, 30.0],
              'capture_radius': 3.0, 'spawns': [
                  {'kind': 'soldier', 'name': 'e1', 'team': None, 'position': [57.0, 0.0, 32.0]}]}
    base = {'role': 'base', 'name': 'West Base', 'initial_owner': 'Team1', 'position': [3.0, 5.0, 3.0],
            'capture_radius': 0, 'spawns': [
                {'kind': 'soldier', 'name': 'b1', 'team': 'Team1', 'position': [4.0, 0.0, 4.0]},
                {'kind': 'vehicle', 'name': 'tank', 'vehicle': 'm4-sherman', 'team': 'Team1',
                 'position': [15.0, 0.5, 40.0], 'guid': 'GTANK'},
                {'kind': 'vehicle', 'name': 'aa', 'vehicle': 'aa_bofors', 'team': 'Team1',
                 'position': [20.0, 0.0, 20.0], 'guid': None},
                {'kind': 'vehicle', 'name': 'jeep', 'vehicle': 'willys-mb', 'team': 'Team1',
                 'position': [27.0, 0.5, 30.0], 'guid': None}]}
    return {'objectives': [flag_w, flag_e, base],
            'unowned_soldier_spawns': [
                {'name': 'rock', 'team': None, 'position': [ROCK[0], 0.0, ROCK[1]]},
                {'name': 'gap', 'team': None, 'position': [51.6, 0.0, 30.0]},
                {'name': 'placeholder', 'team': None, 'position': [0.0, 0.0, 0.0]}],
            'unowned_vehicle_spawns': [
                {'name': 'template', 'vehicle': None, 'team': None, 'position': [0.0, 0.0, 0.0]},
                {'name': 'plane', 'vehicle': 'corsair', 'team': None, 'position': [10.0, 25.0, 20.0]},
                {'name': 'lcvp', 'vehicle': 'lcvp-landingcraft', 'team': None, 'position': [62.0, 1.0, 20.0]}]}


def level():
    ents = [Entity(0, 'HotZoneEntityData'), Entity(1, 'HotZoneEntityData'), Entity(2, 'CombatAreaTriggerEntityData'),
            Entity(3, 'RepairStationEntityData', position=(5.0, 0.0, 40.0), team=1),
            Entity(4, 'VehicleSpawnReferenceObjectData', guid='gtank',
                   matrix=np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [15, 0.5, 40, 1]], float),
                   element=Element('objects/vehicles/tank/vehicle_guid'))]
    shapes = [Shape(0, 'area', square(6, 6, 14, 14)), Shape(1, 'area', square(55, 10, 59, 14)),
              Shape(2, 'area', square(0, 0, 30, 48)), Shape(2, 'outer area', square(0, 0, 64, 64)),
              Shape(3, 'area', square(3, 38, 7, 42))]
    return type('Level', (), {'entities': ents, 'shapes': shapes})()


def made_up(tmp_path, detailed=False):
    """The audit's level context (load()'s dict) for the made-up level, its build written under tmp_path."""
    build_s = columns(PITCH, N, N, build_surfaces)
    hpd = str(tmp_path / 'made_up.hpd')
    load = navsample.load
    navsample.load = lambda p: (build_s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, hpd, log=QUIET, settings=soldier1943.SETTINGS)
    finally:
        navsample.load = load
    hf = Terrain()
    nav = navdata.load(hpd, hf, y_offset=soldier1943.SETTINGS.y_offset)
    s = columns(PITCH, N, N, audit_surfaces)
    # what the build kept: every ground surface but the rock and the ground under the roof (headroom); the roofs too
    keep = np.ones(len(s.y), bool)
    variant = np.ones(len(s.y), bool)
    for c in range(N * N):
        x, z = c % N * PITCH, c // N * PITCH
        for k in range(s.start[c], s.start[c + 1]):
            if s.flags[k]:
                keep[k] = variant[k] = False
            elif under_roof(x, z) and s.y[k] < 1.0:
                keep[k] = False                    # the headroom rule removed it; without the rule it stays
    water = (np.array([[52.0, 1.0, -1.0], [65.0, 1.0, -1.0], [65.0, 1.0, 65.0], [52.0, 1.0, 65.0]]),
             np.array([[0, 1, 2], [0, 2, 3]]))
    G = K.Grid(s, keep, hf, water, '0.8 m')
    G.variants['headroom'] = variant
    objects = tmp_path / 'objects'
    objects.mkdir()
    (objects / 'ladders.json').write_text(json.dumps([{'bp': 'objects/props/ladder_01', 'lo': [5.0, 0.0, 20.0],
                                                       'hi': [5.5, 4.0, 20.5]}]), encoding='utf-8')
    grids = {'normal': G}
    dset_built = set()
    if detailed:
        # a 0.2 m run with ground only west of x 20: the 0.8 m ground at x 20-30 is "0.8 m only"
        s2 = columns(0.2, 100, 240, lambda x, z: [(0.0, 0)])
        grids['detailed'] = K.Grid(s2, np.ones(len(s2.y), bool), hf, None, '0.2 m')
        dset_built = {(0, 0)}
    boxes = [{'bp': 'objects/props/wood_pier_01', 'min': [54.0, -1.0, 0.0], 'max': [60.0, 1.0, 10.0]},
             {'bp': 'objects/props/crate_01', 'min': [9.0, -1.0, 9.0], 'max': [11.0, 1.0, 11.0]}]
    lvl = level()
    return dict(lv='made_up', p={'hpd': hpd, 'objects': str(objects), 'audit': str(tmp_path / 'audit')},
                st={'sea_level': 1.0}, game=None, g=Game(), hf=hf, mm=None, nav=K.Nav(nav, navbuild.read_cells(hpd)),
                lvl=lvl, map=spawn_map(), water=water, entity_by_guid={e.guid.lower(): e for e in lvl.entities if e.guid},
                grids=grids, xoff=0, zoff=0, dset=set(dset_built), dset_built=dset_built, boxes=boxes,
                inputs={'hpd': {'path': hpd, 'bytes': 1, 'sha256': '0' * 64}})


def by_name(rows):
    return {r['name']: r for r in rows}


# ------------------------------------------------------------------------------------------------ helpers

def test_geometry_helpers():
    sq = np.array(square(0, 0, 10, 10), float)
    inside = K.in_poly([5, 15, -1, 9.9], [5, 5, 5, 9.9], sq)
    assert inside.tolist() == [True, False, False, True], 'even-odd point in polygon on x, z'
    assert K.poly_area(sq) == 100.0, 'shoelace area'
    d = K.seg_dist(3.0, 4.0, np.array([[0.0, 0.0], [10.0, 0.0]]), np.array([[0.0, 0.0], [10.0, 10.0]]))
    assert math.isclose(d[0], 5.0) and math.isclose(d[1], 7.0), 'distance to a point segment and to a segment'


def test_label_components():
    m = np.zeros((5, 6), bool)
    m[0, 0] = m[1, 1] = True               # diagonal neighbours: one component (8-connected)
    m[3:5, 4:6] = True
    lab, n = K.label(m)
    assert n == 2 and lab[0, 0] == lab[1, 1] != lab[4, 5] and lab[2, 2] == -1, (n, lab)
    assert K.label(np.zeros((3, 3), bool))[1] == 0, 'an empty mask has no component'


def test_clusters():
    assert K.clusters_xz([], []) == [], 'no points, no clusters'
    x = [0.1, 0.5, 2.5, 30.0]
    z = [0.1, 0.2, 0.3, 30.0]
    cl = K.clusters_xz(x, z)
    assert [c['points'] for c in cl] == [3, 1], f'largest first; points 2.5 m apart join on the 2 m grid ({cl})'
    assert cl[0]['bbox'] == [0.1, 0.1, 2.5, 0.3] and cl[1]['centre'] == [30.0, 30.0]
    assert len(K.clusters_xz(x, z, top=1)) == 1, 'top keeps the largest'


def test_why_not_reasons():
    base = {'blocked': False, 'slope_deg': 0.0, 'clearance': None, 'water_depth': 0.0, 'below_terrain': 0.0}
    assert K.why_not(base)[0].startswith('passes the surface tests'), 'nothing wrong: not reached by the flood'
    bad = dict(base, blocked=True, slope_deg=60.0, clearance=1.0, water_depth=2.0, below_terrain=1.0,
               rules={'lips': 'removed it', 'water': 'added it'})
    assert K.why_not(bad) == ['capsule blocked', 'steeper than 45 deg', 'headroom under 1.8 m',
                              'deeper than 1.4 m under water', 'more than 0.5 m under the terrain',
                              'lips rule removed it'], K.why_not(bad)
    assert K.why_not(dict(base, clearance=0.3)) == K.why_not(base), 'a clearance under 0.5 m is a sampling double'


def test_vehicle_entries(tmp_path):
    C = made_up(tmp_path)
    got = K.vehicle_entries(C, 'GTANK')
    assert got == [{'point': [16.0, 0.5, 40.0], 'radius': 3.0, 'order': 0}], \
        f'the entry point placed by the spawn transform; an entry without numbers is skipped ({got})'
    assert K.vehicle_entries(C, None) == [] and K.vehicle_entries(C, 'unknown') == [], 'no entity: no entries'
    C['entity_by_guid']['gtank'].element = Element(None)
    assert K.vehicle_entries(C, 'gtank') == [], 'a spawn without a vehicle has no entries'


# ------------------------------------------------------------------------------------------------ the grid

def test_grid_columns(tmp_path):
    C = made_up(tmp_path)
    G = C['grids']['normal']
    assert G.col(-5, 0) is None and G.col(0, 100) is None, 'outside the grid: no column'
    c = G.col(3.2, 43.2)
    assert G.centre(c) == (3.2, 43.2)
    col = G.describe(c, mm=None)
    assert [s['y'] for s in col['surfaces']] == [1.5, 0.0] and col['water_top'] is None, col
    roof, ground = col['surfaces']
    assert roof['kept'] and roof['clearance'] is None and ground['clearance'] == 1.5
    assert ground['rules'] == {'headroom': 'removed it'}, 'the rule-off mask shows the rule removed it'
    assert ground['why_not'] == ['headroom under 1.8 m', 'headroom rule removed it'], ground['why_not']
    wet = G.describe(G.col(62.4, 20.0))
    assert wet['water_top'] == 1.0 and wet['surfaces'][0]['water_depth'] == 3.0, wet
    assert wet['surfaces'][0]['below_terrain'] == 0.0
    rock = G.describe(G.col(*ROCK))['surfaces'][0]
    assert rock['blocked'] and rock['why_not'] == ['capsule blocked'], rock
    assert G.describe(c, lo=1.0)['surfaces'][0]['y'] == 1.5 and len(G.describe(c, lo=1.0)['surfaces']) == 1, \
        'only the surfaces between lo and hi'


def test_floor_of(tmp_path):
    G = made_up(tmp_path)['grids']['normal']
    c, n, y = K.floor_of(G, 3.2, 0.2, 43.2)
    assert y == 0.0, 'a position just above the ground stands on the ground, not on the roof over it'
    assert K.floor_of(G, 3.2, 1.6, 43.2)[2] == 1.5, 'a position on the roof stands on the roof'
    assert K.floor_of(G, 3.2, -5.0, 43.2)[2] == 0.0, 'under the terrain: the terrain surface over it'
    assert K.floor_of(G, 51.6, 0.0, 30.0)[1:] == (None, None), 'a column without surfaces: no floor'
    assert K.floor_of(G, -10.0, 0.0, 0.0) == (None, None, None), 'outside the grid'


# ------------------------------------------------------------------------------------------------ 1 + 5: positions

def test_required_positions(tmp_path):
    C = made_up(tmp_path)
    pos = K.required_positions(C)
    names = [r['name'] for r in pos]
    assert names.count('w1') == 1 and 'w1 alt 1' in names and 'w1 alt 2' not in names, \
        'a spawn and its alternates that are positions'
    assert 'template' not in names and 'placeholder' not in names, 'entities at the origin are not checked'
    assert [p['name'] for p in C['placeholders']] == ['placeholder'], 'the origin placeholders are listed apart'
    r = by_name(pos)
    assert r['West Flag']['capture_area'] is not None and r['East Flag']['capture_area'] is None, \
        'a flag takes the HotZone outline around it'
    assert r['hotzone 1']['kind'] == 'hot zone without a flag'
    assert r['repair 3']['kind'] == 'repair station' and r['repair 3']['capture_area'] is not None
    assert (r['tank']['class'], r['aa']['class'], r['plane']['class'], r['lcvp']['class'], r['jeep']['class']) == \
        ('ground vehicle', 'stationary weapon', 'aircraft', 'boat', 'ground vehicle')
    assert len(r['tank']['entries']) == 1 and r['jeep']['entries'] == [], 'entry points from the blueprint'


def check_all(C):
    pos = K.required_positions(C)
    return {r['name']: K.check_position(C, r) for r in pos}, pos


def test_check_positions(tmp_path):
    C = made_up(tmp_path)
    res, _ = check_all(C)
    assert res['w1']['status'] == 'ok' and res['w1']['nearest_same_floor']['main'], res['w1']
    assert res['b1']['status'] == 'ok'
    assert res['e1']['status'] == 'ok (not main network)', 'a spawn on the east island: not the main network'
    assert res['West Base']['status'] == 'info (base marker)' and 'marker 5.0 m above' in res['West Base']['reason']
    assert res['plane']['status'] == 'not required' and 'in the air' in res['plane']['reason']
    assert res['tank']['status'] == 'ok' and res['tank']['entry']['radius'] == 3.0 and \
        'EntryRadius' in res['tank']['note'], 'a vehicle with entry points: judged by its EntryRadius'
    assert res['jeep']['status'] == 'ok', 'a vehicle without entry points: walkable ground within VEHICLE_REACH'
    assert res['aa']['status'] == 'ok'
    gap = res['gap']
    assert gap['status'] == 'fail' and gap['reason'] == 'no sampled surface under the position', gap
    rock = res['rock']
    assert rock['status'] == 'fail' and 'floor surface not walkable: capsule blocked' in rock['reason'], rock
    assert 'nearest area on its floor is' in rock['reason']
    boat = res['lcvp']
    assert boat.get('afloat') and boat['grid'] == '0.8 m', 'a landing craft over water is afloat'
    assert res['West Flag']['objects'] == ['crate_01'], 'objects around a position'


def test_mounted_gun_and_bunker_floor(tmp_path):
    C = made_up(tmp_path)
    # a window-mounted gun 1.5 m over the ground: its gunner stands on the floor below it
    r = {'kind': 'vehicle spawn', 'name': 'mg', 'class': 'stationary weapon', 'position': [10.0, 1.5, 30.0],
         'entries': []}
    res = K.check_position(C, r)
    assert res['status'] == 'ok', res
    # a vehicle whose entry points are all far from walkable ground fails on its EntryRadius
    r = {'kind': 'vehicle spawn', 'name': 'far', 'class': 'ground vehicle', 'position': [ROCK[0], 0.0, ROCK[1]],
         'entries': [{'point': [ROCK[0], 0.0, ROCK[1]], 'radius': 0.5, 'order': 0}]}
    res = K.check_position(C, r)
    assert res['status'] == 'fail' and 'no walkable ground within its EntryRadius 0.5 m' in res['reason'], res


def test_capture_share(tmp_path):
    C = made_up(tmp_path)
    r = by_name(K.required_positions(C))
    west = K.capture_share(C, r['West Flag'])
    assert west['source'] == 'HotZone outline' and west['status'] == 'ok' and west['share'] > 0.75, west
    assert west['area_m2'] == 64.0 and west['share_of'] == 'land, main network'
    east = K.capture_share(C, r['East Flag'])
    assert east['source'] == 'capture radius 3 m' and east['status'] == 'fail' and east['share'] == 0.0, \
        f'a flag whose ground is all on another island: none of it on the main network ({east})'
    assert east['other_islands'], 'the island it is on is named'
    base = K.capture_share(C, dict(r['East Flag'], kind='base'))
    assert base['status'] == 'info (base)'
    assert K.capture_share(C, {'kind': 'flag', 'position': [0, 0, 0], 'capture_radius': 0}) is None, \
        'no outline and no radius: nothing to measure'


# ------------------------------------------------------------------------------------------------ 2: reachability

def test_islands_and_routes(tmp_path):
    C = made_up(tmp_path)
    res, _ = check_all(C)
    checked = list(res.values())
    for r in checked:
        r['_poly'] = None
    LM = K.landmass(C)
    assert K.sea_level(C) == 1.0, 'the water mesh gives the sea level'
    assert K.landmass_at(LM, 10.0, 10.0) != K.landmass_at(LM, 62.0, 10.0), 'deep water parts the land'
    islands = K.classify_islands(C, LM, checked)
    kinds = sorted(i['kind'] for i in islands)
    assert kinds.count('main') == 1 and 'vehicle-only zone' in kinds, kinds
    deep = next(i for i in islands if i['kind'] == 'vehicle-only zone')
    assert deep['linked_by'] == ['boat (landing craft spawns: 1)'], deep
    reach = K.reachability(C, checked)
    rows = {r['spawn']: r for r in reach['spawns']}
    assert rows['w1']['to']['West Flag'] and rows['w1']['back']['West Flag'], ('west spawn to and from the west flag', reach)
    assert not rows['w1']['to']['East Flag'], '... but not across the gap'
    assert rows['e1']['to']['East Flag'] and not rows['e1']['main']
    assert rows['gap'] == {'spawn': 'gap', 'joined': False, 'reason': 'no floor'}
    assert rows['b1']['reason'] == 'no route node within 15 m on its floor', 'a spawn far from every route node'
    assert reach['targets']['West Flag'] > 0


def test_vehicle_link_without_spawns():
    isl = {'bbox': [0.0, 0.0, 10.0, 10.0]}
    assert K.vehicle_link({}, isl, []) == ['no vehicle spawn on it: a boat landing or a parachute drop from a plane']
    plane = {'kind': 'vehicle spawn', 'class': 'aircraft', 'position': [5.0, 0.0, 5.0]}
    assert K.vehicle_link({}, isl, [plane]) == ['plane (aircraft spawns: 1)']


def test_sea_level_without_water(tmp_path):
    C = made_up(tmp_path)
    C['water'] = None
    assert K.sea_level(C) == 1.0, 'no water mesh: navkit.json\'s sea level'
    C['st'] = {'sea_level': None}
    assert K.sea_level(C) == -2.0 - soldier1943.NO_WATER_BELOW, 'a level without water: its sea under the lowest terrain'


# ------------------------------------------------------------------------------------------------ 3: consistency

def test_consistency(tmp_path):
    C = made_up(tmp_path)
    cons = K.consistency(C)
    assert cons['portals one-way'] == [] and cons['self-loop edges'] == [] and cons['nodes sharing a position'] == [], \
        'a build NavKit wrote is consistent'
    assert isinstance(cons['hpdcheck'], dict) and cons['border nodes'] >= 0
    assert K.crossings(C) == [], 'the islands do not touch: no crossings'


# ------------------------------------------------------------------------------------------------ 4: resolution

def test_resolution(tmp_path):
    C = made_up(tmp_path)
    assert K.resolution(C) == {'skipped': 'no detailed mask'}
    (tmp_path / 'detailed').mkdir()
    C = made_up(tmp_path / 'detailed', detailed=True)
    res = K.resolution(C)
    sec = res['sectors']
    # 0.2 m only: the ground under the roof, which the 0.8 m mask left out (headroom) and the 0.2 m one keeps
    assert len(sec) == 1 and sec[0]['sector'] == '0000' and sec[0]['only08'] > 0 and sec[0]['only02'] == 9, sec
    only8 = res['walkable at 0.8 m, not at 0.2 m']
    assert only8 and all(d['centre'][0] > 19.0 for d in only8), \
        f'the 0.8 m ground east of the 0.2 m run is reported ({only8})'
    only2 = res['walkable at 0.2 m, not at 0.8 m']
    assert [d['centre'] for d in only2] == [[3.2, 43.2]], only2
    assert only8[0]["column_08"] is not None and only8[0]["column_02"] is None, "no 0.2 m column east of its run"


# ------------------------------------------------------------------------------------------------ 6, 7, 8

def test_water_checks(tmp_path):
    C = made_up(tmp_path)
    w = K.water_checks(C)
    assert w['sea_level'] == 1.0
    assert w['walkable cells under more than 1.5 m of water'] > 0 and w['deep walkable clusters'], \
        'the deep east ground is walkable in the build: reported'
    assert w['walkable cells in the wading band (0-1.5 m)'] > 0
    band = w['wading band']['0.8 m']
    assert band['band surfaces (terrain, 0-1.4 m deep)'] == 7 * N and band['not kept'] == 0, band
    assert band['kept surfaces deeper than 1.4 m'] == 5 * N, band
    piers = w['piers, jetties, bridges']
    assert [p['object'] for p in piers] == ['wood_pier_01'] and piers[0]['cells'] > 0 and piers[0]['on_main'] == 0


def test_rules(tmp_path):
    C = made_up(tmp_path)
    res, pos = check_all(C)
    rl = K.rules(C, list(res.values()))
    head = rl['headroom (0.8 m)']
    assert head['removed'] == 9 and head['added'] == 0, 'the ground under the 3 x 3 column roof'
    assert head['removed clusters'][0]['nearest_required'] == 'repair station: repair 3 (4 m)', head
    nm = rl['near-misses']['0.8 m']
    assert any(k.startswith('headroom') for k in nm) and 'step (sills, stairs)' in nm
    lad = rl['ladders']
    assert len(lad) == 1 and lad[0]['ladder'] == 'ladder_01' and lad[0]['foot_area'] is not None, lad
    assert lad[0]['ends_in_two_sectors'] is False


def test_step_near():
    # two kept floors 0.33 m apart (inside the step) and one 0.42 m up that the build left out (just past it)
    def surf(x, z):
        return [(0.42 if x > 1.0 else 0.33 if x > 0.5 else 0.0, 0)]
    s = columns(0.8, 3, 1, surf)
    keep = np.array([True, True, False])
    G = K.Grid(s, keep, Terrain(), None, '0.8 m')
    got = K.step_near(G)
    lo = soldier1943.STEP * (1 - K.NEAR)
    assert got['kept pairs stepping %.2f-%.2f m' % (lo, soldier1943.STEP)] == 1, got
    assert got['left out, %.2f-%.2f m above or below kept ground' % (soldier1943.STEP, soldier1943.STEP * 1.2)] == 0, \
        '0.42 m above its neighbour 0.33 m: 0.09 m, not near the step'


def test_playable(tmp_path):
    C = made_up(tmp_path)
    pa = K.playable(C)
    assert pa['combat areas'] == 1 and pa['outer areas'] == 1, pa
    assert pa['walkable outside, m2'] > 0 and pa['walkable outside, clusters'], 'the east island is outside x 0-30'
    assert 0.9 < pa['walkable share of the land inside'] <= 1.0, pa
    C['lvl'].shapes = []
    assert K.playable(C) == {'combat areas': 0, 'outer areas': 0}, 'no combat area: nothing more'


# ------------------------------------------------------------------------------------------------ the run

def test_run_writes_report(tmp_path):
    C = made_up(tmp_path)
    load = K.load
    K.load = lambda lv, log=print: C
    try:
        out = tmp_path / 'report' / 'audit.json'
        R = K.run('made_up', json_out=str(out), log=QUIET)
    finally:
        K.load = load
    saved = json.loads(out.read_text(encoding='utf-8'))
    assert saved['level'] == 'made_up' and saved['masks'] == {'normal': ['headroom']}
    status = {p['name']: p['status'] for p in saved['1 positions']}
    assert status['repair 3'] == 'ok', 'a repair station is judged by its outline'
    assert status['rock'] == 'fail' and status['w1'] == 'ok'
    assert '_poly' not in saved['1 positions'][0], 'working fields are not saved'
    md = (tmp_path / 'report' / 'audit.md').read_text(encoding='utf-8')
    for part in ('# Navigation audit: made_up', '### Failures', '**soldier spawn  rock**', '## 2. Reachability',
                 '#### Vehicle-only zones and separate landmasses', '## 3. File consistency',
                 'no detailed mask', '## 6. Water and shore', 'wood_pier_01', '### Ladders', '## 8. Playable area',
                 'Placeholders at the origin, not checked: 1'):
        assert part in md, f'the Markdown report has {part!r}'
    assert R['4 resolution'] == {'skipped': 'no detailed mask'}


def test_markdown_resolution_section(tmp_path):
    (tmp_path / 'd').mkdir()
    C = made_up(tmp_path / 'd', detailed=True)
    load = K.load
    K.load = lambda lv, log=print: C
    try:
        R = K.run('made_up', json_out=str(tmp_path / 'a.json'), log=QUIET)
    finally:
        K.load = load
    md = K.markdown(json.loads(json.dumps(R, default=K._json)))
    assert '### walkable at 0.8 m, not at 0.2 m: ' in md and '### Connectivity:' in md


def test_json_default():
    assert K._json(np.int64(3)) == 3 and K._json(np.float32(0.5)) == 0.5
    assert K._json(np.array([1, 2])) == [1, 2] and sorted(K._json({1, 2})) == [1, 2]
    try:
        K._json(object())
        raise AssertionError('an unknown type is a TypeError')
    except TypeError:
        pass
