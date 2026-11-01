r"""Core: a BF1943 level read from a made-up game copy (level1943.load)

level1943.load on a stand-in game copy (binary DBX partitions written here, a height field, a water mesh), with the
spawn map and the Havok readers stood in: the level's name, each entity's category (BF1943's own: end-of-round
spawn, DMZ, map marker; bases and flags from the spawn map; ladders, destructibles and dynamic objects from the
blueprints), teams (a base's spawns and their alternates take its owner, DMZs their TeamId, unowned spawns the
carrier they stand on), static-group members, collision (a mesh alias), outlines, terrain and water; a level
without terrain or water gets notes; a level without its partition is refused.
"""
import struct
from types import SimpleNamespace

import numpy as np

import levelobjects1943 as lo
import spawnmap
from bfbc2core import dbx, game1943, level1943

LV = 'made_up'


# ---- a binary DBX writer (dbx.parse's layout) ---------------------------------------------------------------------
def node(tag, kind, attrs=(), value=None, children=()):
    return (tag, kind, list(attrs), value, list(children))


def field(name, text='', **attrs):
    return node('field', dbx.TEXT, [('name', name)] + list(attrs.items()), text)


def numbers(name, values):
    return node('field', dbx.NUMBERS, [('name', name)], [float(v) for v in values])


def array(name, refs):
    return node('array', dbx.CONTAINER, [('name', name)], None, [node('item', dbx.TEXT, [('ref', r)], '') for r in refs])


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


def at(x, y, z):
    return numbers('Transform', [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1])


def placed(guid, bp, x, y, z):
    return instance('Entity.ReferenceObjectData', guid, [at(x, y, z), field('ReferencedObject', ref=f'{bp}/BP')])


def blueprint(typ, *fields_, extra=()):
    return dbx_bytes([instance(f'Entity.{typ}', 'BP', list(fields_))] + list(extra))


def heightfield(size=4):
    """A big-endian TerrainHeightfield 0..10 m high over -100..100, every sample 5 m (as BF1943's)."""
    return struct.pack('>6f6IB', -100.0, 0.0, -100.0, 100.0, 10.0, 100.0, size, 0, 0, 0, 0, 0, 0) + \
        struct.pack(f'>{size * size}H', *([32767] * (size * size)))


def water_mesh():
    v = np.array([[-50, 0, -50], [50, 0, -50], [50, 0, 50], [-50, 0, 50]], '>f4')
    return struct.pack('>I', 1) + struct.pack('>I', 4) + v.tobytes() + struct.pack('>I', 6) + \
        np.array([0, 1, 2, 0, 2, 3], '>u4').tobytes()


class Game:
    def __init__(self, files):
        self.files = {k.lower(): v for k, v in files.items()}

    def data(self, name):
        return self.files.get(name.lower())

    def partition(self, name):
        d = self.data(name + '.dbx')
        return dbx.parse(d) if d else None

    def names(self, prefix=''):
        return sorted(n for n in self.files if n.startswith(prefix.lower()))


class Packfile:
    """A stand-in Havok file: a 10 x 10 x 10 m box's floor (two triangles) for b'box'."""
    def __init__(self, data, classes=None):
        self.data = data

    def objects_of(self, name):
        return [0] if name == 'hkStorageExtendedMeshShapeMeshSubpartStorage' and self.data == b'box' else []


def box_triangles(pf, off):
    return [(0, 0, 0), (10, 0, 0), (10, 10, 10), (0, 0, 10)], [(0, 1, 2), (0, 2, 3)]


def level_files(terrain=True):
    level = dbx_bytes([
        instance('Levels.LevelData', 'ld', [field('Name', 'Levels/Made_Up')]),
        instance('Entity.TerrainEntityData', 'te', [field('TerrainAsset', ref='terrains/made_up_t/made_up_t/T')]),
        instance('Entity.CapturePointEntityData', 'cp1', [at(0, 0, 0), field('Team', 'TeamNeutral')]),
        instance('Entity.CapturePointEntityData', 'cp2', [at(50, 0, 0), field('Name', 'East Flag')]),
        instance('Entity.SoldierSpawnEntityData', 's1', [at(1, 0, 1), field('Team', 'TeamNeutral'),
                                                         field('PlayerType', 'SSHumanPlayer'),
                                                         array('AlternativeSpawnPoints', ['s2', 'missing'])]),
        instance('Entity.AlternateSpawnEntityData', 's2', [at(2, 0, 2)]),
        instance('Entity.SoldierSpawnEntityData', 's3', [at(305, 10, 5)]),          # on the US carrier
        instance('Entity.VehicleSpawnEntityData', 'v1', [at(-195, 10, 5)]),        # on the Japanese carrier
        instance('Entity.VehicleSpawnEntityData', 'v2', [at(0, 50, 0)]),           # on neither
        instance('Entity.DmzEntityData', 'd1', [field('TeamId', 'Team2')]),
        instance('Entity.DmzEntityData', 'd2', [field('TeamId', 'TeamNeutral')]),
        instance('Entity.EndOfRoundSoldierSpawnEntityData', 'eor', [at(3, 0, 3)]),
        instance('Entity.MapMarkerEntityData', 'mm', [at(4, 0, 4)]),
        placed('us', 'objects/ships/us_carrier', 300, 0, 0),
        placed('jp', 'objects/ships/jap_carrier', -200, 0, 0),
        placed('l1', 'objects/props/ladder', 10, 0, 10),
        placed('c1', 'objects/props/crate', 20, 0, 20),
        placed('b1', 'objects/props/barrel', 30, 0, 30),
        placed('n1', 'objects/props/nothing', 40, 0, 40),
        placed('a1', 'objects/props/alias_user', 60, 0, 60),
        instance('Entity.CombatAreaTriggerEntityData', 'ca', [field('Team', 'Team1'), array('Geometry', ['p1'])]),
        instance('Entity.AreaGeometryEntityData', 'p1', [at(0, 0, 0), numbers('Height', [25]), field('Next', ref='p2')]),
        instance('Entity.AreaGeometryEntityData', 'p2', [at(10, 0, 0), field('Next', ref='p3')]),
        instance('Entity.AreaGeometryEntityData', 'p3', [at(10, 0, 10), field('Next', ref='p1')]),
    ])
    group = dbx_bytes([
        instance('Physics.CompositeHavokAsset', 'g', [
            numbers('Transforms', [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 500, 0, 0, 1]),
            array('Assets', ['objects/rocks/rock_havok/R', 'objects/rocks/rock_havok/R2'])]),
        instance('Physics.CompositeHavokAsset', 'h', [field('Transforms', 'not numbers'), array('Assets', ['x/y'])]),
        instance('Physics.Other', 'o'),
    ])
    files = {
        f'levels/{LV}.dbx': level,
        f'levels/{LV}/group_havokasset.dbx': group,
        f'levels/{LV}/other.dbx': b'not a group',
        'objects/props/ladder.dbx': blueprint('LadderEntityData'),
        'objects/props/crate.dbx': blueprint('StaticModelEntityData', extra=[
            instance('Entity.PartComponentData', 'P', [array('HealthStates', ['a', 'b'])])]),
        'objects/props/barrel.dbx': blueprint('DynamicModelEntityData'),
        'objects/props/alias_user.dbx': blueprint('StaticModelEntityData', field('Mesh', ref='objects/props/crate_mesh/M')),
        'objects/ships/us_carrier_havok.res': b'box',
        'objects/ships/jap_carrier_havok.res': b'box',
        'objects/props/crate_havok.res': b'box',
        'objects/rocks/rock_havok.res': b'box',
    }
    if terrain:
        files['terrains/made_up_t/made_up_t.heightfield-000.res'] = heightfield()
        files['terrains/made_up_t/made_up_t.water.mesh.res'] = water_mesh()
    return files


SPAWN_MAP = {'objectives': [
    {'guid': 'cp1', 'role': 'base', 'initial_owner': 'Team1', 'spawns': [{'guid': 's1'}, {'guid': 'gone'}]},
    {'guid': 'cpx', 'role': 'base', 'initial_owner': None, 'spawns': [{'guid': 'eor'}]},
    {'guid': 'cp2x', 'role': 'flag', 'initial_owner': None, 'spawns': [{'guid': 'mm'}]},
]}


def load(files, lv=LV):
    saved = (game1943.Game, game1943.find_game, spawnmap.spawn_map, lo.hkpack, lo.havok461)
    game1943.Game = lambda game, level: Game(files)
    game1943.find_game = lambda given=None: given or 'made-up copy'
    spawnmap.spawn_map = lambda game, level: SPAWN_MAP
    lo.hkpack = SimpleNamespace(Packfile=Packfile)
    lo.havok461 = SimpleNamespace(CLASSES=None, triangles=box_triangles, convex_box=None)
    lines = []
    try:
        return level1943.load(lv, log=lines.append), lines
    finally:
        game1943.Game, game1943.find_game, spawnmap.spawn_map, lo.hkpack, lo.havok461 = saved


def test_load_made_up_level():
    lvl, lines = load(level_files())
    assert lvl.name == 'Made_Up' and lvl.lv == LV and lvl.opfor == 'made-up copy'
    e = {x.guid: x for x in lvl.entities}
    cats = {g: e[g].category for g in ('cp1', 'cp2', 's1', 's2', 'v1', 'd1', 'eor', 'mm', 'l1', 'c1', 'b1', 'n1', 'a1',
                                      'p1', 'ca')}
    assert cats == {'cp1': 'base', 'cp2': 'flag', 's1': 'soldier spawn', 's2': 'soldier spawn', 'v1': 'vehicle spawn',
                    'd1': 'dmz', 'eor': 'end-of-round spawn', 'mm': 'map marker', 'l1': 'ladder', 'c1': 'destructible',
                    'b1': 'dynamic object', 'n1': 'placed object', 'a1': 'placed object', 'p1': 'area point',
                    'ca': 'combat area'}, cats
    assert e['c1'].destructible and not e['b1'].destructible
    assert e['cp2'].name == 'East Flag' and e['cp1'].name is None
    teams = {g: e[g].team for g in ('s1', 's2', 's3', 'v1', 'v2', 'd1', 'd2', 'cp1', 'eor', 'ca')}
    assert teams == {'s1': 1, 's2': 1, 's3': 1, 'v1': 2, 'v2': None, 'd1': 2, 'd2': 0, 'cp1': 0, 'eor': None,
                     'ca': 1}, teams
    assert e['s1'].ai is False
    members = [x for x in lvl.entities if x.category == 'group member']
    assert [(m.guid, m.blueprint, m.group, m.name) for m in members] == [
        ('g#0', 'objects/rocks/rock', 'group_havokasset.dbx', 'rock')], 'the second asset has no transform'
    assert members[0].position.tolist() == [500.0, 0.0, 0.0]
    # collision: the carriers, the crate, the alias user (the crate's) and the rock
    owners = sorted(lvl.entities[m.entity].guid for m in lvl.meshes)
    assert owners == ['a1', 'c1', 'g#0', 'jp', 'us'], owners
    a1 = next(m for m in lvl.meshes if lvl.entities[m.entity].guid == 'a1')
    assert a1.triangles.dtype == np.float32 and a1.triangles.reshape(-1, 3).min(0).tolist() == [60.0, 0.0, 60.0]
    assert lvl.missing_collision == []
    assert len(lvl.shapes) == 1
    sh = lvl.shapes[0]
    assert sh.kind == 'area' and sh.closed and sh.height == 25.0 and len(sh.points) == 3 and sh.field == 'Geometry'
    assert lvl.heightfield is not None and abs(float(lvl.heightfield.height(0.0, 0.0)) - 5.0) < 1e-3
    assert lvl.terrain is not None and len(lvl.terrain[1]) == 2       # stride 2 over 4 x 4 samples: one square
    assert lvl.water is not None and len(lvl.water) == 2
    assert lvl.objectives == SPAWN_MAP['objectives'] and lvl.notes == []
    assert lines[0] == 'Made_Up: reading levels/made_up.dbx'
    assert lines[-1].startswith('Made_Up: ') and '5 with collision (10 triangles)' in lines[-1]
    assert '3 bases and flags' in lines[-1]


def test_load_without_terrain_or_water():
    lvl, _ = load(level_files(terrain=False))
    assert lvl.heightfield is None and lvl.terrain is None and lvl.water is None
    assert lvl.notes == ['no height field terrains/made_up_t/made_up_t.heightfield-000.res', 'no water mesh']


def test_load_without_partition():
    try:
        load(level_files(), lv='Levels/Other')
    except FileNotFoundError as e:
        assert str(e) == "levels/other.dbx is in none of other's bundles"
    else:
        raise AssertionError('a level without its partition was read')


def test_category_and_terrain_name():
    assert level1943.category_1943('Entity.DmzEntityData') == 'dmz'
    assert level1943.category_1943('Entity.CapturePointEntityData') == 'capture point'
    assert level1943.category_1943(None) == 'other'
    insts = dbx.parse(dbx_bytes([instance('Entity.TerrainEntityData', 't', [field('TerrainAsset', ref='bad')])])).instances
    assert level1943.terrain_name_1943(insts) is None
    insts = dbx.parse(dbx_bytes([instance('Entity.Other', 't')])).instances
    assert level1943.terrain_name_1943(insts) is None


def test_water_from_bytes():
    w = level1943.water_from_bytes(water_mesh())
    assert w.vertices.shape == (4, 3) and w.triangles.tolist() == [[0, 1, 2], [0, 2, 3]]
    try:
        level1943.water_from_bytes(b'\0\0\0\1')
    except ValueError:
        pass
    else:
        raise AssertionError('a cut water mesh was read')
