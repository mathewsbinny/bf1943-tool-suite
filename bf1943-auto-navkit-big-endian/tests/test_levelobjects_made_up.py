r"""NavKit big-endian: level objects and collision of a made-up level (levelobjects1943)

levelobjects1943 on a made-up game copy (a stand-in Game holding binary DBX partitions written here) and stand-in
Havok readers: blueprint flags (ForbidPathfinding, ExcludeFromPathfinding, seeds, dynamic, DisablePathfinding,
NeedsDetailedPathfinding, health states, ladders) and the mesh alias; collision from meshes and convex boxes, the
_havok_havok.res name, box shapes reported, the cache; the world transform; and main's files: placed objects, group
members, spawns and order points, each triangle file, the object boxes and the ladders (the foot raised to the
terrain, a short one left out), with the usage refusal and a level without a partition.
"""
import contextlib
import io
import json
import os
import struct
from types import SimpleNamespace

import numpy as np

import levelobjects1943 as lo
from bfbc2core import dbx

# ---- a binary DBX writer (dbx.parse's layout: string table, varint tags, kind << 4 | attribute count) -------------


def node(tag, kind, attrs=(), value=None, children=()):
    return (tag, kind, list(attrs), value, list(children))


def field(name, text='', **attrs):
    return node('field', dbx.TEXT, [('name', name)] + list(attrs.items()), text)


def numbers(name, values):
    return node('field', dbx.NUMBERS, [('name', name)], [float(v) for v in values])


def container(name, children, tag='array'):
    return node(tag, dbx.CONTAINER, [('name', name)], None, children)


def item(ref):
    return node('item', dbx.TEXT, [('ref', ref)], '')


def instance(typ, guid, children=()):
    return node('instance', dbx.CONTAINER, [('guid', guid), ('type', typ)], None, children)


def dbx_bytes(instances):
    names = ['']

    def idx(s):
        if s not in names:
            names.append(s)
        return names.index(s)

    def varint(v):
        out = b''
        while True:
            b = v & 0x7F
            v >>= 7
            if not v:
                return out + bytes([b])
            out += bytes([b | 0x80])

    def enc(n):
        tag, kind, attrs, value, children = n
        out = varint(idx(tag)) + bytes([kind << 4 | len(attrs)])
        for k, v in attrs:
            out += varint(idx(k)) + varint(idx(v))
        if kind == dbx.CONTAINER:
            return out + b'\0' + b''.join(enc(c) for c in children) + b'\0'
        if kind == dbx.TEXT:
            return out + varint(idx(value or ''))
        return out + varint(len(value)) + varint(4) + b''.join(struct.pack('>f', v) for v in value)
    body = enc(node('partition', dbx.CONTAINER, children=instances))
    offsets, blob = [], b''
    for s in names:
        offsets.append(len(blob))
        blob += s.encode() + b'\0'
    area = 4 * len(names) + len(blob)
    return dbx.MAGIC + bytes(8) + struct.pack('>II', area, len(names)) + struct.pack(f'>{len(names)}I', *offsets) + blob + body


def placed(x, y, z):
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def heightfield(size=4, lo_=(-100.0, 0.0, -100.0), hi=(100.0, 10.0, 100.0), value=0x1999):
    """A little-endian TerrainHeightfield, every sample value/65535 of the way up (0x1999: 1.0 m)."""
    head = struct.pack('<6f6IB', *lo_, *hi, size, 0, 0, 0, 0, 0, 0)
    return head + struct.pack(f'<{size * size}H', *([value] * (size * size)))


class Game:
    def __init__(self, files):
        self.files = {k.lower(): v for k, v in files.items()}
        self.entries = dict.fromkeys(self.files)

    def data(self, name):
        return self.files.get(name.lower())

    def partition(self, name):
        d = self.data(name + '.dbx')
        return dbx.parse(d) if d else None


# ---- stand-in Havok readers: a file's bytes name what it holds ----------------------------------------------------
MESH_V = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 2.0, 0.0)]
MESH_T = [(0, 1, 2), (0, 1, 3)]
BOX = [[(0, 0, 0), (1, 0, 0), (0, 1, 0)]]
LADDER = [[(0, -1, 0), (0.5, -1, 0), (0, 4, 0)]]
SHORT = [[(0, 0, 0), (0.5, 0, 0), (0, 1, 0)]]


class Packfile:
    def __init__(self, data, classes=None):
        self.data = data

    def objects_of(self, name):
        holds = {b'mesh': 'hkStorageExtendedMeshShapeMeshSubpartStorage', b'empty': 'hkStorageExtendedMeshShapeMeshSubpartStorage',
                 b'convex': 'hkConvexVerticesShape', b'ladder': 'hkConvexVerticesShape', b'short': 'hkConvexVerticesShape',
                 b'box': 'hkBoxShape'}
        return [0] if holds.get(self.data) == name else []


def triangles(pf, off):
    return ([], []) if pf.data == b'empty' else (MESH_V, MESH_T)


def convex_box(pf, off):
    return {b'convex': BOX, b'ladder': LADDER, b'short': SHORT}[pf.data], None, None


@contextlib.contextmanager
def stand_in_havok():
    saved = lo.hkpack, lo.havok461
    lo.hkpack = SimpleNamespace(Packfile=Packfile)
    lo.havok461 = SimpleNamespace(CLASSES=None, triangles=triangles, convex_box=convex_box)
    try:
        yield
    finally:
        lo.hkpack, lo.havok461 = saved


LV = 'made_up'


def ref_object(guid, bp, at):
    return instance('Entity.ReferenceObjectData', guid, [numbers('Transform', placed(*at)),
                                                         field('ReferencedObject', ref=f'{bp}/AAAA')])


def blueprint(typ, *fields_, extra=()):
    return dbx_bytes([instance(f'Entity.{typ}', 'BP', list(fields_))] + list(extra))


def made_up_game(terrain=True):
    level = dbx_bytes([
        ref_object('1', 'Objects/Props/Crate', (10, 0, 0)),
        ref_object('2', 'objects/props/ladder', (0, 0, 20)),
        ref_object('3', 'objects/props/short_ladder', (5, 0, 20)),
        ref_object('4', 'objects/props/alias_user', (-10, 0, 0)),
        ref_object('5', 'objects/props/nothing', (0, 0, 0)),
        ref_object('6', 'objects/props/stairs_a', (20, 0, 0)),
        ref_object('7', 'objects/props/wall', (30, 5, 0)),
        ref_object('8', 'objects/props/boxy', (40, 0, 0)),
        instance('Entity.ReferenceObjectData', '9', [numbers('Transform', placed(1, 1, 1)),
                                                     field('ReferencedObject', ref='null')]),
        instance('Entity.ReferenceObjectData', '10', [field('ReferencedObject', ref='objects/props/crate/AAAA')]),
        instance('Entity.SoldierSpawnEntityData', '11', [numbers('Transform', placed(1, 2, 3))]),
        instance('Entity.SoldierSpawnEntityData', '12', [numbers('Transform', placed(0, 0, 0))]),
        instance('Entity.AreaGeometryEntityData', '13', [numbers('Transform', placed(4, 5, 6))]),
        instance('Entity.LevelData', '14', [field('Name', 'Levels/made_up')]),
    ])
    group = dbx_bytes([
        instance('Physics.CompositeHavokAsset', 'G', [
            numbers('Transforms', placed(100, 0, 0) + placed(200, 0, 0)),
            container('Assets', [item('objects/rocks/rock_havok/R1'), item('objects/rocks/rock2_havok/R2'),
                                 item('objects/rocks/rock_havok/R3')])]),     # a third asset without a transform
        instance('Physics.Other', 'H'),
    ])
    files = {
        f'levels/{LV}.dbx': level,
        f'levels/{LV}/group_havokasset.dbx': group,
        f'levels/{LV}/notes.dbx': b'not read',
        'objects/props/crate.dbx': blueprint(
            'DynamicModelEntityData', field('NeedsDetailedPathfinding', 'true'), field('AllowPathdataSeeds', 'true'),
            extra=[instance('Entity.PartComponentData', 'P', [container('HealthStates', [item('a'), item('b')])])]),
        'objects/props/ladder.dbx': blueprint('LadderEntityData'),
        'objects/props/short_ladder.dbx': blueprint('LadderEntityData'),
        'objects/props/alias_user.dbx': blueprint('StaticModelEntityData', field('Mesh', ref='objects/props/crate_mesh/M')),
        'objects/props/stairs_a.dbx': blueprint('StaticModelEntityData', field('ForbidPathfinding', 'true')),
        'objects/props/wall.dbx': blueprint('StaticModelEntityData', field('ExcludeFromPathfinding', 'true'),
                                            field('DisablePathfinding', 'PdtWhenAlive')),
        'objects/props/crate_havok.res': b'mesh',
        'objects/props/ladder_havok.res': b'ladder',
        'objects/props/short_ladder_havok.res': b'short',
        'objects/props/stairs_a_havok.res': b'convex',
        'objects/props/wall_havok_havok.res': b'mesh',
        'objects/props/boxy_havok.res': b'box',
        'objects/rocks/rock_havok.res': b'convex',
        'objects/rocks/rock2_havok.res': b'empty',
    }
    if terrain:
        files[f'terrains/{LV}/{LV}.heightfield-000.res'] = heightfield()
    return Game(files)


def test_blueprint_info():
    g = made_up_game()
    assert lo.blueprint_info(g, 'objects/props/nowhere') == (None, None)
    flags, mesh = lo.blueprint_info(g, 'objects/props/crate')
    assert flags == (False, False, True, True, '', True, True, False) and mesh is None
    assert lo.blueprint_info(g, 'objects/props/ladder')[0][7] is True
    assert lo.blueprint_info(g, 'objects/props/alias_user')[1] == 'objects/props/crate'
    assert lo.blueprint_info(g, 'objects/props/stairs_a')[0][0] is True
    wall = lo.blueprint_info(g, 'objects/props/wall')[0]
    assert wall[1] is True and wall[4] == 'PdtWhenAlive'


def test_kind_and_matrix():
    f = dbx.parse(dbx_bytes([instance('Entity.X', '1', [numbers('Transform', placed(1, 2, 3)), numbers('Short', [1, 2]),
                                                       field('Name', 'n')])]))
    e = f.instances[0]
    assert lo.kind(e) == 'X'
    assert lo.matrix(e)[12:15] == [1.0, 2.0, 3.0]
    assert lo.matrix(e, 'Short') is None and lo.matrix(e, 'Name') is None and lo.matrix(e, 'Missing') is None


def test_collision_and_world():
    g = made_up_game()
    with stand_in_havok():
        cache, missing = {}, {}
        t = lo.collision(g, 'objects/props/crate', cache, missing)
        assert t.shape == (2, 3, 3) and t.dtype == np.float32
        assert lo.collision(g, 'objects/props/crate', cache, missing) is t, 'cached'
        assert lo.collision(g, 'objects/props/wall', cache, missing).shape == (2, 3, 3), 'the _havok_havok.res name'
        assert lo.collision(g, 'objects/props/stairs_a', cache, missing).shape == (1, 3, 3), 'a convex shape\'s box'
        assert lo.collision(g, 'objects/props/boxy', cache, missing) is None
        assert missing == {'box / translated convex shapes not read': {'objects/props/boxy'}}
        assert lo.collision(g, 'objects/rocks/rock2', cache, missing) is None, 'an empty mesh'
        assert lo.collision(g, 'objects/props/nowhere', cache, missing) is None and 'objects/props/nowhere' in cache
    m = [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 10, 20, 30, 1]          # turned 90 degrees about y, then moved
    w = lo.world(np.array([[[1, 0, 0], [0, 1, 0], [0, 0, 1]]], np.float32), m)
    assert w.dtype == np.float32
    assert np.allclose(w[0], [[10, 20, 29], [10, 21, 30], [11, 20, 30]])


def run_main(argv, game):
    saved = lo.Game
    lo.Game = lambda game_dir, level: game
    out = io.StringIO()
    try:
        with stand_in_havok(), contextlib.redirect_stdout(out):
            code = lo.main(argv)
    finally:
        lo.Game = saved
    return code, out.getvalue()


def test_main_writes_the_level(tmp_path):
    out = str(tmp_path / 'objects')
    code, text = run_main(['levelobjects1943.py', 'game', LV, out], made_up_game())
    assert code == 0, text
    read = lambda name: json.load(open(os.path.join(out, name), encoding='utf-8'))   # noqa: E731
    objs, groups = read('objects.json'), read('group_instances.json')
    assert [o['bp'] for o in objs] == ['objects/props/crate', 'objects/props/ladder', 'objects/props/short_ladder',
                                       'objects/props/alias_user', 'objects/props/nothing', 'objects/props/stairs_a',
                                       'objects/props/wall', 'objects/props/boxy']
    assert [(g['group'], g['bp'], g['m'][12]) for g in groups] == [
        ('group_havokasset.dbx', 'objects/rocks/rock', 100.0), ('group_havokasset.dbx', 'objects/rocks/rock2', 200.0)]
    assert read('soldier_spawns.json') == [[1.0, 2.0, 3.0]] and read('order_points.json') == [[4.0, 5.0, 6.0]]
    n = {name: len(np.load(os.path.join(out, name))) for name in
         ('all_tris.npy', 'forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy', 'exclude_tris.npy',
          'exclude_footprint_tris.npy')}
    # crate 2, ladder 1, short ladder 1, alias_user (the crate's) 2, stairs 1, wall 2, rock 1 (rock2 and boxy none)
    assert n == {'all_tris.npy': 10, 'forbid_tris.npy': 1, 'dynamic_tris.npy': 4, 'stair_tris.npy': 1,
                 'exclude_tris.npy': 2, 'exclude_footprint_tris.npy': 2}, n
    boxes = read('object_boxes.json')
    assert [b['index'] for b in boxes] == list(range(7))
    crate = boxes[0]
    assert crate['bp'] == 'objects/props/crate' and crate['destructible'] and crate['detailed']
    assert crate['min'] == [10.0, 0.0, 0.0] and crate['max'] == [11.0, 2.0, 1.0] and crate['footprint'] == 1.0
    assert boxes[3]['min'] == [-10.0, 0.0, 0.0], 'the alias\'s collision at the user\'s placement'
    ladders = read('ladders.json')
    assert ladders == [{'bp': 'objects/props/ladder', 'lo': [0.0, 1.0, 20.0], 'hi': [0.5, 4.0, 20.0], 'box_lo_y': -1.0}], \
        'the foot raised to the terrain (1 m); the 1 m ladder left out'
    assert 'made_up: 8 placed objects, 2 group members, 7 with collision' in text
    assert 'ladders: 1' in text
    assert "no collision: 3 blueprints, e.g. ['objects/props/boxy', 'objects/props/nothing', 'objects/rocks/rock2']" in text
    assert 'box / translated convex shapes not read: 1 blueprints' in text


def test_main_without_terrain(tmp_path):
    out = str(tmp_path / 'objects')
    code, _ = run_main(['levelobjects1943.py', 'game', LV, out], made_up_game(terrain=False))
    ladders = json.load(open(os.path.join(out, 'ladders.json'), encoding='utf-8'))
    assert code == 0 and [(x['bp'], x['lo'][1]) for x in ladders] == [('objects/props/ladder', -1.0)], ladders


def test_main_refusals(tmp_path):
    code, text = run_main(['levelobjects1943.py', 'game'], made_up_game())
    assert code == 1 and 'Usage: levelobjects1943.py' in text
    try:
        run_main(['levelobjects1943.py', 'game', 'other', str(tmp_path / 'o')], made_up_game())
    except FileNotFoundError as e:
        assert str(e) == 'levels/other.dbx is not in the level bundles'
    else:
        raise AssertionError('a level without its partition was read')
