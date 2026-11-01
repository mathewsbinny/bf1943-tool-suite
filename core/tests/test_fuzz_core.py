r"""core readers fuzzed: DBX, bundles, RSNV, terrain, material map, water, navigation data

Mutation fuzzing (core/fuzzcheck.py) of core's binary readers on made-up files built by the existing tests' builders
(no game data): dbx.parse and to_xml, game1943.Game (a made-up game folder with a damaged level bundle), rsnv.load,
terrain.read, materialmap1943.read, water.read_mesh / read_visual / read, navdata.load. On any input each returns
or raises ValueError, within its time and memory limits, and never reads a file cut short as whole. Fixed seeds, a
few seconds a reader; BF1943_FUZZ=long runs 50 times as many cases from a random seed (printed for replay).

The regression cases below are the shrunk inputs of what the fuzzer found (2026-10-31), each with its fix.
"""
import os
import struct

import numpy as np

import fuzzcheck
from bfbc2core import dbx, game1943, materialmap1943, navdata, rsnv, terrain, water

DBX = fuzzcheck.fixtures('core/tests/test_dbx_malformed.py')
BUNDLES = fuzzcheck.fixtures('core/tests/test_game1943_bundles.py')
RSNV = fuzzcheck.fixtures('core/tests/test_rsnv.py')
READERS = fuzzcheck.fixtures('core/tests/test_readers_malformed.py')
WATER = fuzzcheck.fixtures('core/tests/test_water_mesh.py')
FORMATS = fuzzcheck.fixtures('bf1943-auto-navkit-common/tests/test_formats.py')


def fuzz(*args, **kw):
    report = fuzzcheck.run(*args, **kw)
    assert report.ok, report.text()


# ------------------------------------------------------------------------------------------------ seeds

def dbx_seed():
    data = DBX.made_up()[:-1]           # as the game's partitions end: nothing after the root closes (FORMAT.md)
    area, count = struct.unpack_from('>II', data, 16)
    fields = [(16, '>I'), (20, '>I')] + [(24 + 4 * i, '>I') for i in range(count)]
    return data, list(range(0, len(data))), fields


def rsnv_seeds(tmp):
    sections, header = RSNV.tiny()
    out = []
    for big in (False, True):
        p = os.path.join(tmp, f'tiny{big}.rsnv')
        rsnv.write(p, sections, header, big_endian=big)
        data = open(p, 'rb').read()
        e = '>' if big else '<'
        nsec = struct.unpack_from(e + 'I', data, 16)[0]
        fields = [(12, e + 'I'), (16, e + 'I')]
        bounds = [0, 4, 8, 64]
        for k in range(nsec):
            at = rsnv.HEADER + 16 * k
            fields += [(at + 4, e + 'I'), (at + 8, e + 'I'), (at + 12, e + 'I')]
            off, count, stride = struct.unpack_from(e + 'III', data, at + 4)
            bounds += [off, off + count * stride]
        out.append((data, bounds, fields))
    return out


def heightfield(size, e='<'):
    """A height field without the table after its heights: terrain.read does not decode that (its length varies), so
    a cut there cannot be told from a whole file; every cut of this seed loses heights."""
    head = struct.pack(e + '6f6IB', 0, 0, 0, 100, 50, 100, size, 0, 0, 0, 0, 0, 0)
    return head + struct.pack(f'{e}{size * size}H', *range(size * size))


def visual_water(indices, slots=('Shaders/Water/A', 'Shaders/Water/B'), ranges=None):
    """A VisualWater (.Water.res) in water.py's layout, little-endian (BFBC2's; read_visual does not read BF1943's
    big-endian ones): 3 vertices of 36 bytes, the indices, the slots and the fragment shader, the ranges. Without the
    u32 0 and 0x46 that follow them, which read_visual does not read: every cut of this seed loses data."""
    verts = bytes(36 * 3)
    out = struct.pack('<3I', 10, 36, len(verts)) + verts
    out += struct.pack('<I', 4 * len(indices)) + struct.pack(f'<{len(indices)}I', *indices)
    out += struct.pack('<I', len(slots))
    for s in slots:
        out += s.encode('ascii') + b'\0\0'
    out += b'Systems/Water/WaterOceanFragment\0'
    ranges = ranges if ranges is not None else [(0, len(indices) // 3, 0, len(indices))]
    out += struct.pack('<I', len(ranges)) + b''.join(struct.pack('<4I', *r) for r in ranges)
    return out


# ------------------------------------------------------------------------------------------------ fuzz

def test_dbx_parse_and_xml():
    data, bounds, fields = dbx_seed()

    def read(d):
        f = dbx.parse(d)
        for e in f.instances:                       # what the tools do with a partition
            e.attr('guid')
        return f
    fuzz('dbx.parse', read, [data], iterations=1500, boundaries=[bounds], fields=[fields])
    fuzz('dbx.to_xml', dbx.to_xml, [data], iterations=1500, boundaries=[bounds], fields=[fields])


def test_game1943_bundle_loader(tmp_path):
    seed = BUNDLES.bundle({'a/x.res': b'level', 'levels/lv.dbx': DBX.made_up()})
    root = str(tmp_path / 'game')
    BUNDLES.game_folder(root, seed)
    level = os.path.join(root, 'levels', 'lv', 'level-00.fbrb')
    cut = struct.unpack_from('>I', seed, 4)[0]

    def load(d):
        with open(level, 'wb') as f:
            f.write(d)
        g = game1943.Game(root, 'lv')
        for n in g.names():
            g.data(n)
        g.partition('levels/lv')
        return g
    fuzz('game1943.Game', load, [seed], iterations=400, boundaries=[[0, 4, 8, 8 + cut, len(seed) - 1]],
         fields=[[(4, '>I')]])


def test_rsnv_load(tmp_path):
    seeds = rsnv_seeds(str(tmp_path))

    def load(d):
        w = rsnv.load(d)
        # what readers do with an accepted file: locate, search, line of sight
        p = w.node_pos
        if len(p):
            w.locate(*p[0])
            rsnv.search(w, 0, len(p) - 1, max_expansions=10000)
        return w
    fuzz('rsnv.load', load, [s for s, _, _ in seeds], iterations=1200, boundaries=[b for _, b, _ in seeds],
         fields=[f for _, _, f in seeds])


def test_terrain_read():
    seeds = [heightfield(2), heightfield(4, '>')]

    def read(d):
        hf = terrain.read(d)
        h = hf.height([hf.min[0], (hf.min[0] + hf.max[0]) / 2], [hf.min[2], hf.max[2]])
        assert np.isfinite(h).all(), 'heights not finite'
        return hf
    fuzz('terrain.read', read, seeds, iterations=1500, boundaries=[[0, 0x18, 0x31, len(s) - 2] for s in seeds],
         fields=[[(0x18, e + 'I')] + [(4 * k, e + 'f') for k in range(6)] for e in '<>'])


def test_materialmap_read():
    seed = READERS.material_map([])
    tile = 4 + struct.unpack_from('>I', seed, 6)[0] + 512
    bounds = [0, 4, 6, 10, 6 + tile, 6 + tile + 4, 6 + 2 * tile, 6 + 2 * tile + 4, len(seed) - 1]
    fields = [(0, '>I'), (4, '>H'), (6, '>I'), (6 + tile, '>I'), (6 + 2 * tile, '>I')]
    fuzz('materialmap1943.read', materialmap1943.read, [seed], iterations=300, boundaries=[bounds], fields=[fields])


def test_water_mesh(tmp_path):
    seeds = [WATER.mesh([(3, [0, 1, 2]), (4, [0, 1, 2, 1, 2, 3])]), WATER.mesh([(3, [0, 1, 2])], '>')]
    path = str(tmp_path / 'w.water.mesh.res')

    def read(d):
        with open(path, 'wb') as f:
            f.write(d)
        return water.read_mesh(path)
    fuzz('water.read_mesh', read, seeds, iterations=600, fields=[[(0, '<I'), (4, '<I'), (44, '<I')],
                                                                  [(0, '>I'), (4, '>I'), (44, '>I')]])


def test_water_visual_and_level(tmp_path):
    seed = visual_water([0, 1, 2, 0, 2, 1])
    folder = tmp_path / 'terrain'
    folder.mkdir()
    (folder / 'lv.water.mesh.res').write_bytes(WATER.mesh([(3, [0, 1, 2, 0, 2, 1])]))
    visual = str(folder / 'lv.water.res')

    def read(d):
        with open(visual, 'wb') as f:
            f.write(d)
        water.read_visual(visual)
        return water.read(str(folder), 'lv')

    at = 12 + 108
    fields = [(4, '<I'), (8, '<I'), (at, '<I'), (at + 4 + 24, '<I')] + [(len(seed) - 4 * k, '<I') for k in range(1, 6)]
    fuzz('water.read_visual', read, [seed], iterations=600, fields=[fields])


def test_navdata_load(tmp_path):
    seed = FORMATS.hpd()
    path = str(tmp_path / 'pathdata.hpd')

    def load(d):
        with open(path, 'wb') as f:
            f.write(d)
        return navdata.load(path)
    count = struct.unpack_from('<I', seed, 12 + 8)[0]
    fields = [(20, '<I')] + [(0x9C + 48 * k + o, '<I') for k in range(count) for o in (32, 36)]
    fuzz('navdata.load', load, [seed], iterations=250, fields=[fields])


# ------------------------------------------------------------------------------------------------ regressions

def test_regression_material_map_header():
    """A cut header was a struct.error; a side of 0 read as an empty map (place() then divided by zero); a 6-byte
    header asking for 2,048 tiles made a 256 MiB map before finding no tile."""
    assert fuzzcheck.refused(materialmap1943.read, b'')
    assert fuzzcheck.refused(materialmap1943.read, bytes(4))
    assert fuzzcheck.refused(materialmap1943.read, struct.pack('>IHI', 0, 0, 0))
    assert fuzzcheck.refused(materialmap1943.read, struct.pack('>IH', 16384, 2048))


def test_regression_water_mesh_cut(tmp_path):
    """A mesh cut before its body count, inside a body or before an index count was a struct.error; a NaN vertex was
    read."""
    path = str(tmp_path / 'w.water.mesh.res')

    def read(d):
        with open(path, 'wb') as f:
            f.write(d)
        return water.read_mesh(path)
    good = WATER.mesh([(3, [0, 1, 2])])
    assert fuzzcheck.outcome(read, good) == 'read'
    for cut in (0, 3, 8, len(good) - 13, len(good) - 4):
        assert fuzzcheck.refused(read, good[:cut]), cut
    nan = bytearray(good)
    struct.pack_into('<f', nan, 8, float('nan'))
    assert fuzzcheck.refused(read, bytes(nan))


def test_regression_water_visual(tmp_path):
    """read_visual: 12 zero bytes, or ranges past the end, were a struct.error; read() walked a range of 2^31
    triangles one by one (minutes) to set none."""
    path = tmp_path / 'lv.water.res'
    path.write_bytes(bytes(12))
    assert fuzzcheck.refused(water.read_visual, str(path))
    seed = visual_water([0, 1, 2, 0, 2, 1])
    path.write_bytes(seed[:-8])
    assert fuzzcheck.refused(water.read_visual, str(path))
    (tmp_path / 'lv.water.mesh.res').write_bytes(WATER.mesh([(3, [0, 1, 2, 0, 2, 1])]))
    path.write_bytes(visual_water([0, 1, 2, 0, 2, 1], ranges=[(0, 0x7FFFFFFF, 0, 6)]))
    import time
    t = time.perf_counter()
    w = water.read(str(tmp_path), 'lv')
    assert time.perf_counter() - t < 2.0 and w.shader == ['Shaders/Water/A'] * 2, w.shader


def test_regression_navdata(tmp_path):
    """navdata.load: an edge past its sector's nodes was an IndexError; a boundary point at 1e30 m made navdiff sample
    the box between without end, and an infinite one was an OverflowError; kymesh read offsets past the record with
    struct.error."""
    import hpdtool
    import kymesh
    path = str(tmp_path / 'pathdata.hpd')

    def load(d):
        with open(path, 'wb') as f:
            f.write(d)
        return navdata.load(path)
    f = hpdtool.parse(FORMATS.hpd())
    f.sectors[0].edges[0].dst = 7
    assert fuzzcheck.refused(load, hpdtool.write(f))
    for far in (1e30, float('inf')):
        m = FORMATS.mesh()
        m.areas[0].outline[0][1] = (far, 0, 0)
        f = hpdtool.parse(FORMATS.hpd())
        f.sectors[0].mesh = kymesh.write_mesh(m)
        assert fuzzcheck.refused(load, hpdtool.write(f)), far
    mesh = bytearray(kymesh.write_mesh(FORMATS.mesh()))
    struct.pack_into('<I', mesh, 0x50, 0xFFFFFF00)        # the first tile past the record
    assert fuzzcheck.refused(kymesh.parse_mesh, bytes(mesh))


def test_regression_rsnv_meta_nesting(tmp_path):
    """A META section of JSON nested past Python's recursion limit raised RecursionError (found reading rsnv.load,
    not by the fuzzer: a mutation does not build it); META is provenance only, so the file loads without it."""
    sections, header = RSNV.tiny()
    sections['META'] = b'[' * 100000
    p = str(tmp_path / 'deep.rsnv')
    rsnv.write(p, sections, header)
    assert rsnv.load(p).meta == {'unreadable META': True}
