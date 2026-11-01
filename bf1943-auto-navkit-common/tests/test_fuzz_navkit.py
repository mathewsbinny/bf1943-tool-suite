r"""NavKit (both variants): readers fuzzed (hpd, runtime mesh, Havok packfile, samples)

Mutation fuzzing (core/fuzzcheck.py) of the shared NavKit readers on made-up files (no game data): hpdtool.parse and
mesh_coverage, kymesh.parse_mesh (test_formats.py's builders), hkpack.Packfile with the havok461 layouts as
levelobjects1943.collision reads them (a small Havok 4.6.1 packfile built here), and navsample.load (versions 1, 2
and 3). On any input each returns or raises ValueError, within its time and memory limits, and never reads a file
cut short as whole (navsample's versions 1 and 2 keep an interrupted run's finished columns, by design). Fixed seeds;
BF1943_FUZZ=long for more (README "Tests").
"""
import struct

import numpy as np

import fuzzcheck
import havok461
import hkpack
import hpdtool
import kymesh
import navsample

FORMATS = fuzzcheck.fixtures('bf1943-auto-navkit-common/tests/test_formats.py')
STORAGE, CONVEX = 'hkStorageExtendedMeshShapeMeshSubpartStorage', 'hkConvexVerticesShape'


# ------------------------------------------------------------------------------------------------ seeds

def hpd_seed():
    data = FORMATS.hpd()
    count = struct.unpack_from('<I', data, 20)[0]
    table = hpdtool.HEADER_SIZE
    fields = [(20, '<I')] + [(table + 48 * k + o, '<I') for k in range(count) for o in (32, 36)]
    bounds = [12, table, table + 48 * count]
    blocks = table + 48 * count
    for k in range(count):
        size, off = struct.unpack_from('<2I', data, table + 48 * k + 32)
        fields += [(blocks + off, '<I'), (blocks + off + 4, '<I')]
        bounds += [blocks + off, blocks + off + 8, blocks + off + size - 8]
    return data, bounds, fields


def mesh_seed():
    m = kymesh.write_mesh(FORMATS.mesh())
    fields = [(o, '<I') for o in range(0x38, 0x50, 4)] + [(0x50 + 4 * k, '<I') for k in range(5)]
    for _p, ptr in [(p, struct.unpack_from('<I', m, struct.unpack_from('<I', m, 0x50)[0] + 4)[0])
                    for p in range(1)]:
        fields += [(ptr + o, '<I') for o in range(0x20, 0x50, 4)]
    return m, [0x14, 0x50, len(m) - 8], fields


def packfile(prefix=b'RES\0' * 8):
    """A big-endian Havok 4.6.1 packfile (as BF1943's *_Havok.res: no __types__) after `prefix`: __classnames__,
    __data__ with one hkStorageExtendedMeshShapeMeshSubpartStorage (4 vertices, two 16-bit triangles) and one
    hkConvexVerticesShape (one group of four points), their pointers as local fixups, their classes as virtual ones."""
    names = b''
    name_at = {}
    for n in (STORAGE, CONVEX):
        name_at[n] = len(names) + 5
        names += b'\0\0\0\0\x09' + n.encode('ascii') + b'\0'
    names += bytes(-len(names) % 16)
    data = bytearray(0x130)
    struct.pack_into('>3I', data, 0x08, 0, 4, 0xC0000004)        # vertices: data pointer (fixup), count, capacity
    struct.pack_into('>3I', data, 0x14, 0, 8, 0xC0000008)        # indices16
    for k, v in enumerate([(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 1, 1)]):
        struct.pack_into('>4f', data, 0x50 + 16 * k, *v, 0.0)
    struct.pack_into('>8H', data, 0x90, 0, 1, 2, 0, 1, 3, 2, 0)
    struct.pack_into('>3f', data, 0xA0 + 0x20, 0.5, 0.5, 0.5)     # convex: half extents, centre, rotated vertices
    struct.pack_into('>3f', data, 0xA0 + 0x30, 0.5, 0.5, 0.5)
    struct.pack_into('>2i', data, 0xA0 + 0x44, 1, 0)
    struct.pack_into('>i', data, 0xA0 + 0x4C, 4)
    struct.pack_into('>12f', data, 0x100, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1)
    local = struct.pack('>6I', 0x08, 0x50, 0x14, 0x90, 0xA0 + 0x40, 0x100)
    virtual = struct.pack('>6I', 0, 0, name_at[STORAGE], 0xA0, 0, name_at[CONVEX])
    body = bytes(data) + local + virtual
    sections = [('__classnames__', names, (len(names),) * 5), ('__data__', body, (len(data), len(data) + len(local),
                len(data) + len(local), len(body), len(body), len(body))), ('__types__', b'', (0,) * 6)]
    head = bytearray(0x40 + 0x30 * len(sections))
    head[0:8] = hkpack.MAGIC
    struct.pack_into('>2I', head, 8, 0, 5)
    head[16:20] = bytes([4, 0, 0, 1])                             # pointers of 4 bytes, big-endian
    struct.pack_into('>3I', head, 20, len(sections), 1, 0)
    head[0x28:0x38] = b'Havok-4.6.1-r1'.ljust(16, b'\0')
    at = len(head)
    for k, (name, blob, offs) in enumerate(sections):
        o = 0x40 + 0x30 * k
        head[o:o + 20] = name.encode('ascii').ljust(20, b'\0')
        if len(offs) == 5:
            offs = (offs[0],) + offs
        struct.pack_into('>7I', head, o + 20, at, *offs)
        at += len(blob)
    return prefix + bytes(head) + b''.join(blob for _, blob, _ in sections)


def samples(version, columns=4):
    """A navsample file of `columns` columns of two surfaces each (version 3: records, column counts, then their
    offset and 'CNT3')."""
    head = navsample.HEADER.pack(b'ONS1', version, 0.8, 0.0, 0.0, columns, 1, 100, -100, 0.4, 0.3, 1.8, 0.1, 0)
    if version == 3:
        recs = b''.join(struct.pack('<f3bB3I', float(k), 0, 127, 0, 0, 1, 2, 3) for _ in range(columns) for k in range(2))
        at = len(head) + len(recs)
        return head + recs + bytes([2] * columns) + struct.pack('<Q', at) + b'CNT3'
    out = bytearray(head)
    for _ in range(columns):
        out.append(2)
        for k in range(2):
            out += struct.pack('<f3bB', float(k), 0, 127, 0, 0) + (struct.pack('<3I', 1, 2, 3) if version == 2 else b'')
    return bytes(out)


# ------------------------------------------------------------------------------------------------ readers as used

def collision(data):
    """levelobjects1943.collision's reading of a *_Havok.res: triangles indexed into their vertices, convex boxes."""
    pf = hkpack.Packfile(data, classes=havok461.CLASSES)
    parts = []
    for off in pf.objects_of(STORAGE):
        v, t = havok461.triangles(pf, off)
        if v and t:
            parts.append(np.asarray(v, np.float32)[np.asarray(t)])
    for off in pf.objects_of(CONVEX):
        box, _, _ = havok461.convex_box(pf, off)
        if box:
            parts.append(np.asarray(box, np.float32))
    return parts


# ------------------------------------------------------------------------------------------------ fuzz

def test_hpd_parse():
    data, bounds, fields = hpd_seed()
    report = fuzzcheck.run('hpdtool.parse', hpdtool.parse, [data], iterations=1500, boundaries=[bounds], fields=[fields])
    assert report.ok, report.text()


def test_runtime_mesh():
    m, bounds, fields = mesh_seed()

    def mesh(d):
        out = kymesh.parse_mesh(d)
        hpdtool.mesh_coverage(d)
        return out
    report = fuzzcheck.run('kymesh.parse_mesh', mesh, [m], iterations=2000, boundaries=[bounds], fields=[fields])
    assert report.ok, report.text()


def test_havok_packfile():
    seed = packfile()
    assert len(collision(seed)) == 2, 'the made-up packfile gives a mesh and a convex box'
    base = seed.find(hkpack.MAGIC)
    fields = [(base + 20, '>I'), (base + 24, '>I')] + [(base + 0x40 + 0x30 * k + 20 + 4 * w, '>I')
                                                       for k in range(3) for w in range(7)]
    data_at = struct.unpack_from('>I', seed, base + 0x40 + 0x30 + 20)[0] + base
    fields += [(data_at + o, '>I') for o in (0x08, 0x0C, 0x14, 0x18, 0xA0 + 0x40, 0xA0 + 0x44, 0xA0 + 0x4C)]
    fields += [(data_at + 0x130 + 4 * k, '>I') for k in range(12)]
    # prefix_ok: the packfile's sections need not reach the end of the .res (the reader never reads past them)
    report = fuzzcheck.run('hkpack + havok461', collision, [seed], iterations=2000, fields=[fields], prefix_ok=True)
    assert report.ok, report.text()


def test_samples(tmp_path):
    seeds = [samples(1), samples(2), samples(3)]
    path = str(tmp_path / 'samples.bin')

    def load(d):
        with open(path, 'wb') as f:
            f.write(d)
        s, cols = navsample.load(path)
        for c in range(cols):                       # what the builder does with each column
            list(s.column(c % s.nx, c // s.nx))
        return s
    h = navsample.HEADER.size
    fields = [[(4, '<I'), (20, '<i'), (24, '<i')]] * 2 + [[(4, '<I'), (20, '<i'), (24, '<i'), (len(seeds[2]) - 12, '<Q')]]
    # versions 1 and 2 keep an interrupted run's finished columns: a cut copy is a shorter run, not a damaged file
    # memory: a header may ask for up to navsample.MAX_COLUMNS columns (8 bytes each in Samples.start), whether or not
    # the run finished them
    report = fuzzcheck.run('navsample.load', load, seeds, iterations=800, fields=fields, prefix_ok=True,
                           boundaries=[[h], [h], [h, len(seeds[2]) - 12]], memory=3 << 30, limit=10.0)
    assert report.ok, report.text()
    v3 = seeds[2]
    for cut in range(h, len(v3)):                   # version 3 is written at the end of a run: a cut one is refused
        assert fuzzcheck.refused(load, v3[:cut]), f'navsample v3 cut at {cut} is refused'


# ------------------------------------------------------------------------------------------------ regressions

def test_regression_havok():
    """hkpack: a fixup naming a section past the table, or a class member table past the file, was an IndexError or
    struct.error; a class that is its own parent looped for ever; an array of 2^31 elements of a size-0 struct was
    read element by element. havok461: a triangle naming a vertex past the storage's was read (an IndexError in
    levelobjects1943.collision)."""
    import time
    seed = packfile()
    base = seed.find(hkpack.MAGIC)
    data_at = base + struct.unpack_from('>I', seed, base + 0x40 + 0x30 + 20)[0]
    bad = bytearray(seed)
    struct.pack_into('>I', bad, data_at + 0x130 + 24 + 4, 9)         # the first virtual fixup's section: 9 of 3
    assert fuzzcheck.refused(collision, bytes(bad))
    bad = bytearray(seed)
    struct.pack_into('>8H', bad, data_at + 0x90, 0, 1, 7, 0, 1, 3, 2, 0)   # vertex 7 of 4
    assert fuzzcheck.refused(collision, bytes(bad))
    pf = hkpack.Packfile(seed, classes=havok461.CLASSES)
    pf.classes['loop'] = {'name': 'Loop', 'parent': 'loop', 'size': 4, 'members': []}
    pf.class_by_name['Loop'] = 'loop'
    assert fuzzcheck.refused(lambda _: pf.all_members('Loop'), b'')
    pf.classes['empty'] = {'name': 'Empty', 'parent': None, 'size': 0, 'members': []}
    pf.class_by_name['Empty'] = 'empty'
    at = data_at + 0x08                                               # the vertices array: 2^31 x Empty
    big = bytearray(seed)
    struct.pack_into('>i', big, at + 4, 0x7FFFFFFF)
    pf.d = bytes(big)
    t = time.perf_counter()
    assert fuzzcheck.refused(lambda _: pf.read_value(at, hkpack.T_ARRAY, hkpack.T_STRUCT, 'Empty'), b'')
    assert time.perf_counter() - t < 1.0


def test_regression_samples(tmp_path):
    """navsample.load: a header cut short was a struct.error; a grid of 2^31 columns hung making its column table;
    version 3 counts or records outside their blocks were read."""
    import time
    path = str(tmp_path / 'samples.bin')

    def load(d):
        with open(path, 'wb') as f:
            f.write(d)
        return navsample.load(path)
    assert fuzzcheck.refused(load, samples(1)[:20])
    wide = bytearray(samples(1))
    struct.pack_into('<i', wide, 20, 0x7FFFFFFF)
    t = time.perf_counter()
    assert fuzzcheck.refused(load, bytes(wide)) and time.perf_counter() - t < 1.0
    v3 = bytearray(samples(3))
    v3[-13] = 3                                                       # the last column claims 3 surfaces, not 2
    assert fuzzcheck.refused(load, bytes(v3))


def test_regression_mesh_layout():
    """hpdtool.mesh_layout (hpdtool coverage) read a tile table and tiles past the mesh with struct.error."""
    m = bytearray(kymesh.write_mesh(FORMATS.mesh()))
    struct.pack_into('<I', m, 0x38, 0x10000)                          # 65,536 x 2 tiles
    assert fuzzcheck.refused(hpdtool.mesh_coverage, bytes(m))
