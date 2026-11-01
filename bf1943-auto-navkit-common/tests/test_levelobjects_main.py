r"""NavKit (both variants): a level's objects and pathfinding flags from a made-up extracted level (levelobjects)

No game data: a made-up unpacked + XML-converted level in a temp folder (level partition, blueprint XMLs, empty
packfiles; the collision reader replaced by unit boxes). Checked: order_points; blueprint_flags (ForbidPathfinding,
ExcludeFromPathfinding, AllowPathdataSeeds, dynamic, DisablePathfinding, NeedsDetailedPathfinding); mesh_aliases;
triangles16 (a last triangle without its padding word); triangles_of on made-up packfile objects of both Havok
versions (storage mesh subparts, convex shapes' boxes, out-of-range indices dropped); triangles on a file that is not
a packfile (None); and main: which object lands in which output (forbid, dynamic, barrier arm, door, staircase,
excluded object and its footprint, a blueprint using another's collision through its Mesh, group instances, objects
with no collision), placed in world space, and the JSON files.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json
import struct

import numpy as np

import havok461
import levelobjects

FACES = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
         (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]


def box(lo, hi):
    c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], np.float32)
    return c[np.array(FACES)]


def transform(x, y, z):
    return '1/0/0/0/0/1/0/0/0/0/1/0/' + f'{x}/{y}/{z}' + '/1'


def test_order_points():
    xml = ''.join(f'<instance guid="g{k}" type="Entity.AreaGeometryEntityData"><complex name="Transform">{t}'
                  f'</complex></instance>' for k, t in enumerate([transform(1, 2, 3), transform(0, 0, 0)]))
    xml += '<instance guid="g9" type="Entity.AreaGeometryEntityData"><field name="Name">none</field></instance>'
    assert levelobjects.order_points(xml) == [[1.0, 2.0, 3.0]], 'origin and no transform: no position'


def test_triangles16():
    assert levelobjects.triangles16([0, 1, 2, 9, 3, 4, 5, 9]).tolist() == [[0, 1, 2], [3, 4, 5]]
    assert levelobjects.triangles16([0, 1, 2, 9, 3, 4, 5]).tolist() == [[0, 1, 2], [3, 4, 5]], \
        'a last triangle stored without padding is kept'
    assert levelobjects.triangles16([0, 1, 2, 9, 3]).tolist() == [[0, 1, 2]]


class FakePackfile:
    """The parts of hkpack.Packfile triangles_of reads: a byte buffer, its byte order, the objects of each class and
    pointers (a u32 at the offset, 0 = none)."""
    def __init__(self, version, data, objects, e='<'):
        self.contents_version, self.d, self.objects, self.e = version, bytes(data), objects, e

    def objects_of(self, name):
        return self.objects.get(name, [])

    def deref(self, off):
        return struct.unpack_from(self.e + 'I', self.d, off)[0]


def havok55(e='<'):
    d = bytearray(0x200)
    # mesh subpart storage at 0: vertices at 0x40 (4 floats each), indices at 0x80
    struct.pack_into(e + 'Ii', d, 8, 0x40, 3)
    struct.pack_into(e + 'Ii', d, 0x14, 0x80, 7)
    for k, v in enumerate([(0, 0, 0), (1, 0, 0), (0, 0, 1)]):
        struct.pack_into(e + '4f', d, 0x40 + 16 * k, *v, 0)
    struct.pack_into(e + '7H', d, 0x80, 0, 1, 2, 0, 2, 1, 0)
    # a second subpart with an index past its vertices: dropped
    struct.pack_into(e + 'Ii', d, 0xA8, 0x40, 3)
    struct.pack_into(e + 'Ii', d, 0xB4, 0x90, 4)
    struct.pack_into(e + '4H', d, 0x90, 0, 1, 5, 0)
    # a convex shape at 0x100: its FourVectors (one block: x0..3, y0..3, z0..3) at 0x140
    struct.pack_into(e + 'Ii', d, 0x110, 0x140, 1)
    struct.pack_into(e + '12f', d, 0x140, -1, 2, 0, 0, 0, 3, 1, 1, 5, 5, 6, 5)
    return FakePackfile('Havok-5.5.0-r1', d, {'hkpStorageExtendedMeshShapeMeshSubpartStorage': [0, 0xA0],
                                              'hkpConvexVerticesShape': [0x100]}, e)


def test_triangles_of_havok55():
    for e in '<>':
        tris = levelobjects.triangles_of(havok55(e))
        assert tris.dtype == np.float32 and tris.shape == (2 + 12, 3, 3), (e, tris.shape)
        assert tris[0].tolist() == [[0, 0, 0], [1, 0, 0], [0, 0, 1]]
        assert tris[1].tolist() == [[0, 0, 1], [1, 0, 0], [0, 0, 0]]
        corners = tris[2:].reshape(-1, 3)
        assert corners.min(0).tolist() == [-1, 0, 5] and corners.max(0).tolist() == [2, 3, 6], 'the convex box'
    empty = FakePackfile('Havok-5.5.0-r1', b'\0' * 0x100, {'hkpStorageExtendedMeshShapeMeshSubpartStorage': [0],
                                                       'hkpConvexVerticesShape': [16]})
    assert levelobjects.triangles_of(empty) is None, 'no vertices, no convex members: nothing'


def test_triangles_of_havok4():
    old = havok461.triangles, havok461.convex_box
    meshes = {1: ([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)]), 2: ([(0, 0, 0)], [(0, 1, 2)]), 3: ([], [])}
    try:
        havok461.triangles = lambda pf, off: meshes[off]
        havok461.convex_box = lambda pf, off: (box((0, 0, 0), (1, 1, 1)).tolist() if off == 7 else None, None, None)
        pf = FakePackfile('Havok-4.6.1', b'', {'hkStorageExtendedMeshShapeMeshSubpartStorage': [1, 2, 3],
                                              'hkConvexVerticesShape': [7, 8]})
        tris = levelobjects.triangles_of(pf)
        assert tris.shape == (1 + 12, 3, 3), 'the index past its vertices and the empty mesh are dropped'
        assert levelobjects.triangles_of(FakePackfile('Havok-4.6.1', b'', {})) is None
    finally:
        havok461.triangles, havok461.convex_box = old


def test_triangles_not_a_packfile(tmp_path):
    for data in (b'', b'garbage' * 10):
        path = tmp_path / 'x_Havok.res'
        path.write_bytes(data)
        assert levelobjects.triangles(str(path)) is None


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def blueprint(fields='', typ='Entity.StaticModelEntityData', mesh=None):
    body = ''.join(f'<field name="{k}">{v}</field>' for k, v in fields.items()) if fields else ''
    if mesh:
        body += f'<field ref="{mesh}" name="Mesh" />'
    return f'<partition><instance guid="b" type="{typ}">{body}</instance></partition>'


def placed(ref, xyz):
    return (f'<instance guid="p" type="Entity.ReferenceObjectData"><complex name="Transform">{transform(*xyz)}'
            f'</complex><field name="ReferencedObject" ref="{ref}" /></instance>\n')


def made_up_level(tmp_path):
    level = tmp_path / 'sp_01'
    dist, pkg = level / 'dist', level / 'pkg'
    write(pkg / 'objects' / 'props' / 'crate.xml', blueprint(typ='GameSharedResources.DynamicModelEntityData'))
    write(pkg / 'objects' / 'props' / 'door.xml', blueprint({'ForbidPathfinding': 'false',
                                                            'DisablePathfinding': 'PdtWhenAlive'}))
    write(pkg / 'objects' / 'props' / 'wall.xml', blueprint({'ForbidPathfinding': 'true',
                                                            'NeedsDetailedPathfinding': 'true'}))
    write(pkg / 'objects' / 'props' / 'barrier.xml', blueprint({'ForbidPathfinding': 'true'}))
    write(pkg / 'objects' / 'props' / 'fence.xml', blueprint({'ForbidPathfinding': 'true',
                                                             'ExcludeFromPathfinding': 'true',
                                                             'AllowPathdataSeeds': 'true'}))
    write(pkg / 'objects' / 'props' / 'nocollision.xml', blueprint({'ForbidPathfinding': 'true'}))
    write(pkg / 'objects' / 'props' / 'plain.xml', blueprint({'Something': 'else'}))
    write(dist / 'objects' / 'rock' / 'rock.xml', blueprint({'ForbidPathfinding': 'true'}))
    write(dist / 'objects' / 'invwall_sp03.xml',
          blueprint({'ForbidPathfinding': 'true'}, mesh='Objects/InvisibleWall/InvWall_Mesh/0000'))
    for rel in ('objects/props/crate', 'objects/props/door', 'objects/props/wall', 'objects/props/barrier',
                'objects/props/fence', 'objects/house/stairs_wood', 'objects/invisiblewall/invwall',
                'objects/rock/rock'):
        f = pkg / (rel + '_Havok.res')
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'')
    xml = ''.join([placed('Objects/Props/Crate/c1', (1, 0, 1)), placed('Objects/Props/Door/d1', (2, 0, 2)),
                   placed('Objects/Props/Wall/w1', (100, 0, 0)), placed('Objects/Props/Barrier/b1', (3, 0, 3)),
                   placed('Objects/Props/Fence/f1', (10, 2, 10)), placed('Objects/House/Stairs_Wood/s1', (4, 0, 4)),
                   placed('Objects/InvWall_SP03/i1', (5, 0, 5)), placed('Objects/Props/NoCollision/n1', (6, 0, 6)),
                   placed('Objects/Props/Plain/p1', (7, 0, 7)),
                   '<instance guid="x" type="Entity.ReferenceObjectData"><field name="Name">no transform</field>'
                   '</instance>\n',
                   f'<instance guid="s" type="Game.SoldierSpawnEntityData"><complex name="Transform">'
                   f'{transform(8, 1, 8)}</complex></instance>\n',
                   f'<instance guid="s0" type="Game.SoldierSpawnEntityData"><complex name="Transform">'
                   f'{transform(0, 0, 0)}</complex></instance>\n',
                   f'<instance guid="o" type="Entity.AreaGeometryEntityData"><complex name="Transform">'
                   f'{transform(9, 0, 9)}</complex></instance>\n'])
    write(dist / 'levels' / 'SP_01.xml', xml)
    group = ('<partition><array name="Transforms">' + '/'.join(transform(20, 0, 20).split('/')
                                                               + transform(30, 0, 30).split('/')) + '</array>'
             '<array name="Assets"><item ref="Objects/Rock/Rock_Havok/0001" /><item ref="Objects/Props/Crate/0002" />'
             '</array></partition>')
    write(dist / 'levels' / 'SP_01' / 'Forest_HavokAsset.xml', group)
    write(dist / 'levels' / 'SP_01' / 'Empty_HavokAsset.xml', '<partition />')
    return level


def collision(path):
    """Unit boxes; the barrier a thin raised bar."""
    if 'barrier' in path.lower():
        return box((0, 0.8, 0), (3, 1.0, 0.2))
    return box((0, 0, 0), (1, 1, 1))


def test_flags_and_aliases(tmp_path):
    level = made_up_level(tmp_path)
    bases = [str(level / 'pkg'), str(level / 'dist')]
    flags = levelobjects.blueprint_flags(bases)
    assert flags['objects/props/crate'] == (False, False, False, True, '', False)
    assert flags['objects/props/door'] == (False, False, False, False, 'PdtWhenAlive', False)
    assert flags['objects/props/wall'][levelobjects.FLAG_DETAILED] and flags['objects/props/wall'][0]
    assert flags['objects/props/fence'] == (True, True, True, False, '', False)
    assert 'objects/props/plain' not in flags, 'no pathfinding fields: not listed'
    aliases = levelobjects.mesh_aliases(bases)
    assert aliases == {'objects/invwall_sp03': 'objects/invisiblewall/invwall'}


def test_main(tmp_path):
    level = made_up_level(tmp_path)
    old = levelobjects.triangles
    levelobjects.triangles = collision
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = levelobjects.main(['levelobjects.py', str(level), 'SP_01'])
    finally:
        levelobjects.triangles = old
    text = out.getvalue()
    assert code == 0
    objects = json.load(open(level / 'objects.json', encoding='utf-8'))
    assert [o['bp'] for o in objects] == ['objects/props/crate', 'objects/props/door', 'objects/props/wall',
                                          'objects/props/barrier', 'objects/props/fence', 'objects/house/stairs_wood',
                                          'objects/invwall_sp03', 'objects/props/nocollision', 'objects/props/plain']
    assert objects[0]['flags'] == [False, False, False, True, '', False] and objects[5]['flags'] is None
    assert objects[2]['m'][12:15] == [100.0, 0.0, 0.0]
    groups = json.load(open(level / 'group_instances.json', encoding='utf-8'))
    assert [(g['group'], g['bp'], g['m'][12]) for g in groups] == [('Forest_HavokAsset.xml', 'objects/rock/rock', 20.0),
                                                                  ('Forest_HavokAsset.xml', 'objects/props/crate', 30.0)]
    assert json.load(open(level / 'soldier_spawns.json', encoding='utf-8')) == [[8.0, 1.0, 8.0]]
    assert json.load(open(level / 'order_points.json', encoding='utf-8')) == [[9.0, 0.0, 9.0]]

    forbid = np.load(level / 'forbid_tris.npy')
    # wall, barrier, the InvWall (InvisibleWall's collision through its Mesh), the rock group instance; not the
    # excluded fence, not the object without collision
    assert forbid.shape == (4 * 12, 3, 3)
    assert forbid[:, :, 0].max() == 101.0, 'placed in world space (the wall at x 100)'
    dynamic = np.load(level / 'dynamic_tris.npy')
    assert dynamic.shape == (4 * 12, 3, 3), 'crate, door, barrier arm, and the crate in the group'
    stairs = np.load(level / 'stair_tris.npy')
    assert stairs.shape == (12, 3, 3) and stairs[:, :, 0].min() == 4.0
    exclude, foot = np.load(level / 'exclude_tris.npy'), np.load(level / 'exclude_footprint_tris.npy')
    assert exclude.shape == (12, 3, 3) and foot.shape == (10, 3, 3), 'the top face is above the footprint'
    assert exclude[:, :, 1].min() == 2.0
    assert "barrier arms: ['barrier']" in text
    assert "no collision: ['objects/props/nocollision']" in text, text


def test_main_nothing_flagged(tmp_path):
    level = tmp_path / 'lv'
    write(level / 'dist' / 'levels' / 'LV.xml', placed('Objects/A/a1', (1, 1, 1)))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert levelobjects.main(['levelobjects.py', str(level), 'LV']) == 0
    for name in ('forbid_tris.npy', 'dynamic_tris.npy', 'stair_tris.npy', 'exclude_tris.npy',
                 'exclude_footprint_tris.npy'):
        assert np.load(level / name).shape == (0, 3, 3), name
    with contextlib.redirect_stdout(io.StringIO()) as usage:
        assert levelobjects.main(['levelobjects.py', str(level)]) == 1
    assert 'Usage' in usage.getvalue()


def test_barrier_needs_collision(tmp_path):
    """A forbidden object whose collision cannot be read is not a barrier arm (and not dynamic)."""
    level = tmp_path / 'lv'
    write(level / 'pkg' / 'objects' / 'gate.xml', blueprint({'ForbidPathfinding': 'true'}))
    (level / 'pkg' / 'objects' / 'gate_Havok.res').write_bytes(b'')
    write(level / 'dist' / 'levels' / 'LV.xml', placed('Objects/Gate/g1', (1, 1, 1)))
    old = levelobjects.triangles
    levelobjects.triangles = lambda path: None
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert levelobjects.main(['levelobjects.py', str(level), 'LV']) == 0
    finally:
        levelobjects.triangles = old
    assert 'barrier arms: []' in out.getvalue() and np.load(level / 'dynamic_tris.npy').shape == (0, 3, 3)
