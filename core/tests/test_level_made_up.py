r"""Core: a BFBC2-layout level read from a made-up OPFOR work folder (level.load)

level.load on a made-up Project OPFOR folder (binary DBX partitions in work\levels\<lv>\pkg and dist, a terrain
folder, NavKit samples), with the Havok readers stood in: the Package copy of the partition and of a group file wins
over Dist; categories from the blueprints (destructible, ladder through a blueprint's Object in another partition,
Rush objectives, dynamic objects); teams and AI from the entities, alternates from their spawner; outlines (an area,
an open route); collision (a mesh alias; mesh aliases that cannot be read are a note); terrain from the height field,
else from the samples; water; a level not prepared yet is prepared first; a level without its partition is refused.
heightfield_of unpacks the terrain bundle once (stood-in tool), water_of reads the water, terrain_from_samples skips
empty columns and cliffs.
"""
import os
import struct
import subprocess

import numpy as np

import levelobjects
import navkit_le
from bfbc2core import dbx, level

LV = 'sp_x'


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


def at(x, y, z, name='Transform'):
    return numbers(name, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1])


def placed(guid, bp, x, y, z):
    return instance('Entity.ReferenceObjectData', guid, [at(x, y, z), field('ReferencedObject', ref=f'{bp}/O')])


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def heightfield(size=4, value=0x8000):
    return struct.pack('<6f6IB', -100.0, 0.0, -100.0, 100.0, 10.0, 100.0, size, 0, 0, 0, 0, 0, 0) + \
        struct.pack(f'<{size * size}H', *([value] * (size * size)))


def water_mesh():
    v = np.array([[-50, 0, -50], [50, 0, -50], [50, 0, 50]], '<f4')
    return struct.pack('<II', 1, 3) + v.tobytes() + struct.pack('<I', 3) + np.array([0, 1, 2], '<u4').tobytes()


def samples(path, heights):
    """A version-1 navsample file of 1 m columns; heights[j][i] None leaves the column empty."""
    nz, nx = len(heights), len(heights[0])
    out = bytearray(struct.pack('<4sI3f2i6fI', b'ONS1', 1, 1.0, 10.0, 20.0, nx, nz, 50.0, -50.0, 0.35, 0.37, 1.8, 0.0, 0))
    for row in heights:
        for h in row:
            out += bytes([0]) if h is None else bytes([1]) + struct.pack('<f3bB', h, 0, 127, 0, 0)
    write(path, bytes(out))


def opfor(tmp_path, terrain=True, partition=True):
    root = str(tmp_path / 'Project OPFOR')
    os.makedirs(os.path.join(root, 'game'))
    base = os.path.join(root, 'work', 'levels', LV)
    dist, pkg = os.path.join(base, 'dist'), os.path.join(base, 'pkg')
    entities = [
        instance('Entity.TerrainEntityData', 'te', [field('TerrainAsset', ref='terrains/sp_x_t/sp_x_t/T')]),
        instance('Entity.SoldierSpawnEntityData', 's1', [at(1, 0, 1), field('Team', 'Team2'),
                                                         field('PlayerType', 'SSAIPlayer'),
                                                         array('AlternativeSpawnPoints', ['s2', 'nope'])]),
        instance('Entity.AlternateSpawnEntityData', 's2', [at(2, 0, 2)]),
        instance('Entity.CapturePointEntityData', 'cp', [at(0, 0, 0), field('InitialOwnerTeam', 'Team1'),
                                                         field('Name', 'Alpha'), array('Geometry', ['a1']),
                                                         array('RouteWaypoints', ['r1'])]),
        instance('Entity.AreaGeometryEntityData', 'a1', [at(0, 0, 0), numbers('Height', [12]), field('Next', ref='a2')]),
        instance('Entity.AreaGeometryEntityData', 'a2', [at(5, 0, 0), field('Next', ref='a3')]),
        instance('Entity.AreaGeometryEntityData', 'a3', [at(5, 0, 5), field('Next', ref='a1')]),
        instance('Entity.AreaGeometryEntityData', 'r1', [at(0, 0, 9), field('Next', ref='r2')]),
        instance('Entity.AreaGeometryEntityData', 'r2', [at(9, 0, 9), field('Next', ref='r1')]),
        placed('crate', 'objects/props/crate', 10, 0, 0),
        placed('ladder', 'objects/props/ladder', 20, 0, 0),
        placed('mcom', 'objects/objects/mpobjective/mcom', 30, 0, 0),
        placed('barrel', 'objects/props/barrel', 40, 0, 0),
        placed('broken', 'objects/props/broken', 50, 0, 0),
        placed('alias', 'objects/props/alias_user', 60, 0, 0),
        placed('nohavok', 'objects/props/nohavok', 70, 0, 0),
        instance('Entity.ReferenceObjectData', 'noref', [at(0, 0, 0), field('ReferencedObject', ref='null')]),
        instance('Entity.ReferenceObjectData', 'local', [at(0, 0, 0), field('ReferencedObject', ref='plain')]),
    ]
    if partition:
        write(os.path.join(pkg, 'levels', 'SP_X.dbx'), dbx_bytes(entities))
        write(os.path.join(dist, 'levels', 'sp_x.dbx'), dbx_bytes([instance('Entity.Other', 'dist-copy')]))
    group = dbx_bytes([instance('Physics.CompositeHavokAsset', 'g', [
        at(100, 0, 0, 'Transforms'), array('Assets', ['objects/rocks/rock_havok/R', 'objects/rocks/rock_havok/R2'])]),
        instance('Physics.CompositeHavokAsset', 'h', [field('Transforms', 'text')])])
    write(os.path.join(pkg, 'levels', LV, 'SP_X_Group_HavokAsset.dbx'), group)
    write(os.path.join(dist, 'levels', LV, 'sp_x_group_havokasset.dbx'), dbx_bytes([instance('Physics.Other', 'x')]))
    bp = lambda typ, *f, extra=(): dbx_bytes([instance(typ, 'O', list(f))] + list(extra))   # noqa: E731
    write(os.path.join(dist, 'objects', 'props', 'crate.dbx'),
          bp('Entity.StaticModelEntityData', array('HealthStates', ['a', 'b'])))
    write(os.path.join(dist, 'objects', 'props', 'ladder.dbx'),
          bp('Entity.ObjectBlueprint', field('Object', ref='objects/props/ladder_obj/L')))
    write(os.path.join(dist, 'objects', 'props', 'ladder_obj.dbx'), dbx_bytes([instance('Entity.LadderEntityData', 'L')]))
    write(os.path.join(dist, 'objects', 'objects', 'mpobjective', 'mcom.dbx'), bp('Entity.StaticModelEntityData'))
    write(os.path.join(dist, 'objects', 'props', 'barrel.dbx'),
          bp('Entity.ObjectBlueprint', field('Object', ref='D'), extra=[instance('Entity.DynamicModelEntityData', 'D')]))
    write(os.path.join(dist, 'objects', 'props', 'broken.dbx'), b'not a dbx')
    write(os.path.join(dist, 'objects', 'props', 'alias_user.dbx'), bp('Entity.StaticModelEntityData'))
    for name in ('objects/props/crate_havok.res', 'objects/props/ladder_havok.res', 'objects/rocks/rock_havok.res',
                 'objects/props/nohavok_havok.res'):
        write(os.path.join(dist, *name.split('/')), name.encode())
    if terrain:
        write(os.path.join(base, 'terrain', 'Terrains', 'sp_x_t.heightfield-000.res'), heightfield())
        write(os.path.join(base, 'terrain', 'Terrains', 'sp_x_t.water.mesh.res'), water_mesh())
    return root


ONE = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], np.float32)


def stand_ins(aliases):
    """levelobjects' Havok readers: every *_havok.res but nohavok's holds one triangle."""
    saved = levelobjects.triangles, levelobjects.mesh_aliases

    def mesh_aliases(bases):
        if aliases is None:
            raise ValueError('no converted XML')
        return aliases
    levelobjects.triangles = lambda path: None if 'nohavok' in path else ONE
    levelobjects.mesh_aliases = mesh_aliases
    return saved


def load(root, aliases, **k):
    saved = stand_ins(aliases)
    lines = []
    try:
        return level.load(LV.upper(), opfor=root, log=lines.append, **k), lines
    finally:
        levelobjects.triangles, levelobjects.mesh_aliases = saved


def test_load_made_up_level(tmp_path):
    root = opfor(tmp_path)
    lvl, lines = load(root, {'objects/props/alias_user': 'objects/props/crate', 'objects/props/other': 'missing'})
    assert lvl.name == 'SP_X' and lvl.lv == LV and lvl.opfor == os.path.abspath(root)
    e = {x.guid: x for x in lvl.entities}
    assert 'dist-copy' not in e, 'the Package partition wins over Dist'
    cats = {g: e[g].category for g in ('s1', 's2', 'cp', 'a1', 'crate', 'ladder', 'mcom', 'barrel', 'broken', 'alias',
                                      'noref', 'local')}
    assert cats == {'s1': 'soldier spawn', 's2': 'soldier spawn', 'cp': 'capture point', 'a1': 'area point',
                    'crate': 'destructible', 'ladder': 'ladder', 'mcom': 'objective', 'barrel': 'dynamic object',
                    'broken': 'placed object', 'alias': 'placed object', 'noref': 'placed object',
                    'local': 'placed object'}, cats
    assert e['crate'].destructible and e['crate'].blueprint_type == 'StaticModelEntityData'
    assert e['barrel'].blueprint_type == 'DynamicModelEntityData' and e['broken'].blueprint_type is None
    assert (e['s1'].team, e['s1'].ai, e['s2'].team, e['s2'].ai, e['cp'].team) == (2, True, 2, True, 1)
    assert e['cp'].name == 'Alpha'
    group = [x for x in lvl.entities if x.category == 'group member']
    assert [(x.guid, x.blueprint, x.group) for x in group] == [('g#0', 'objects/rocks/rock', 'SP_X_Group_HavokAsset.dbx')]
    shapes = sorted((s.kind, s.closed, len(s.points), s.height) for s in lvl.shapes)
    assert shapes == [('area', True, 3, 12.0), ('route', False, 2, 0.0)], shapes
    owners = sorted(lvl.entities[m.entity].guid for m in lvl.meshes)
    assert owners == ['alias', 'crate', 'g#0', 'ladder'], owners
    alias = next(m for m in lvl.meshes if lvl.entities[m.entity].guid == 'alias')
    assert alias.triangles.reshape(-1, 3).min(0).tolist() == [60.0, 0.0, 0.0]
    assert lvl.missing_collision == ['objects/objects/mpobjective/mcom', 'objects/props/barrel', 'objects/props/broken',
                                     'objects/props/nohavok']
    assert lvl.heightfield is not None and len(lvl.terrain[1]) == 2 and lvl.water is not None and len(lvl.water) == 1
    assert lvl.notes == []
    assert lines[0] == f'SP_X: reading {os.path.join("work", "levels", LV, "pkg", "levels", "SP_X.dbx")}'
    assert 'terrain from height field' in lines[-1] and '4 with collision (4 triangles)' in lines[-1]
    assert lvl.by_guid('CRATE') is e['crate'] and lvl.by_guid('none') is None
    refs = e['cp'].refs()
    assert ('Geometry/item', 'a1') in refs and ('RouteWaypoints/item', 'r1') in refs


def test_load_terrain_from_samples(tmp_path):
    root = opfor(tmp_path, terrain=False)
    samples(os.path.join(root, 'work', 'navsample', f'{LV}_navkit.bin'), [[1.0, 1.0], [1.0, 1.0]])
    lvl, lines = load(root, None, terrain_stride=1)
    assert lvl.heightfield is None and lvl.water is None
    assert lvl.notes == ['blueprint mesh aliases not read: no converted XML']
    assert lvl.terrain is not None and len(lvl.terrain[1]) == 2 and 'terrain from samples' in lines[-1]
    os.remove(os.path.join(root, 'work', 'navsample', f'{LV}_navkit.bin'))
    lvl, lines = load(root, {})
    assert lvl.terrain is None and 'terrain from none' in lines[-1]


def test_load_prepares_first_and_refuses_without_partition(tmp_path):
    root = opfor(tmp_path, partition=False)
    os.rename(os.path.join(root, 'work', 'levels', LV, 'dist'), os.path.join(root, 'work', 'levels', LV, 'unpacked'))
    prepared = []
    saved = navkit_le.Project, navkit_le.prepare
    navkit_le.Project = lambda r: type('P', (), {'level': lambda self, lv: lv})()
    navkit_le.prepare = lambda pr, lv, log=print: (prepared.append(lv), os.makedirs(
        os.path.join(root, 'work', 'levels', LV, 'dist')))
    try:
        load(root, {})
    except FileNotFoundError as e:
        assert str(e).startswith(f'{LV}: level partition not found under ')
    else:
        raise AssertionError('a level without its partition was read')
    finally:
        navkit_le.Project, navkit_le.prepare = saved
    assert prepared == [LV]


def test_heightfield_of_unpacks_once(tmp_path):
    root = opfor(tmp_path, terrain=False)
    write(os.path.join(root, 'game', 'Dist', 'win32', 'levels', 'SP_X', 'terrain-00.fbrb'), b'bundle')
    runs = []

    def run(cmd, check=False, capture_output=False):
        runs.append(cmd)
        if len(runs) == 1:
            write(os.path.join(cmd[3], 'Terrains', 'sp_x_t.heightfield-000.res'), heightfield())
            write(os.path.join(cmd[3], 'Terrains', 'sp_x_t.water.mesh.res'), water_mesh())
    saved = navkit_le.find_tool, subprocess.run
    navkit_le.find_tool = lambda r, exe, folder: 'FbrbTool.exe'
    subprocess.run = run
    lines = []
    try:
        hf = level.heightfield_of(root, LV, lines.append)            # the name from the partition (pkg)
        again = level.heightfield_of(root, LV, lines.append, 'sp_x_t')
        w = level.water_of(root, LV, lines.append)
        assert level.heightfield_of(root, LV, lines.append, 'other') is None, 'unpacked, no such height field'
    finally:
        navkit_le.find_tool, subprocess.run = saved
    assert hf is not None and abs(float(hf.height(0.0, 0.0)) - 5.0) < 1e-3 and again is not None
    assert w is not None and len(w) == 1
    assert len(runs) == 2 and runs[0][:2] == ['FbrbTool.exe', 'unpack'] and len(lines) == 2
    assert level.heightfield_of(str(tmp_path / 'nowhere'), LV) is None, 'no partition, no bundle'


def test_terrain_from_samples(tmp_path):
    root = str(tmp_path)
    samples(os.path.join(root, 'work', 'navsample', f'{LV}_full.bin'),
            [[0.0, 0.5, 1.0, 1.0], [0.0, None, 1.0, 9.0], [0.0, 0.0, 0.0, 0.0]])
    verts, tris = level.terrain_from_samples(root, LV, stride=1)
    assert verts.shape == (12, 3) and verts.dtype == np.float32 and tris.dtype == np.uint32
    assert verts[0].tolist() == [10.0, 0.0, 20.0] and verts[5][1] == 0.0, 'an empty column\'s vertex at 0'
    # 6 squares: the 4 touching the empty column and the 2 touching the 8 m cliff have no triangles
    assert len(tris) == 0, tris
    samples(os.path.join(root, 'work', 'navsample', f'{LV}_navkit.bin'), [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    verts, tris = level.terrain_from_samples(root, LV, stride=1)
    assert verts.shape == (6, 3) and len(tris) == 4, '<lv>_navkit.bin is read first'
    assert level.terrain_from_samples(root, 'other') is None


def test_helpers():
    assert level.category_of('Entity.VehicleSpawnEntityData') == 'vehicle spawn'
    assert level.category_of('x') == 'other'
    f = dbx.parse(dbx_bytes([instance('Entity.X', '1', [numbers('Transform', [1, 2]), field('ReferencedObject', ref='a')]),
                             instance('Entity.Y', '2', [field('ReferencedObject', ref='objects/A/G')])]))
    a, b = f.instances
    assert level.transform_of(a) is None and level.blueprint_of(a) is None
    assert level.transform_of(b) is None and level.blueprint_of(b) == 'objects/a'
    assert level.terrain_name(f.instances) is None


def test_load_damaged_terrain_is_a_note(tmp_path):
    root = opfor(tmp_path, terrain=False)
    terrain = os.path.join(root, 'work', 'levels', LV, 'terrain')
    write(os.path.join(terrain, 'sp_x_t.heightfield-000.res'), b'short')
    write(os.path.join(terrain, 'sp_x_t.water.mesh.res'), b'\0\0\0\1')
    lvl, lines = load(root, {})
    assert lvl.heightfield is None and lvl.water is None
    assert [n.split(':')[0] for n in lvl.notes] == ['terrain height field not read', 'water not read'], lvl.notes
    assert 'terrain from none' in lines[-1]
