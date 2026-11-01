r"""binary readers fuzzed from your BF1943 files (DBX, bundles, terrain, water, material map, APT, Havok, STFS, NavKit)

The bf1943 tier's variant of the fuzz tests (core/fuzzcheck.py; the quick tier's start from made-up files): the seeds
are real files, read where they are and never written - DBX partitions, a bundle and its directory, a height field,
a material map, a water mesh, a menu movie, Havok collision packfiles from your game copy (bf1943-xenia-kit's
output, BF1943_GAME overrides); the header, hash tables and file table of your BF1943 package (served from memory
over the read-only file: BF1943_PACKAGE, else operation-rising-sun's copy next to the suite); and NavKit's builds of
Wake Island (.hpd, .rsnv, samples) when they exist. On any input each reader returns or raises ValueError, within its
time and memory limits. Fixed seeds; BF1943_FUZZ=long for more (README "Tests").
"""
import gzip
import io
import os
import struct
import unittest

import numpy as np

import fuzzcheck

TIER = 'bf1943'
SUITE = fuzzcheck.SUITE
GAME = os.environ.get('BF1943_GAME', os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'title', 'dist', 'xenon'))
PACKAGE = os.environ.get('BF1943_PACKAGE') or os.path.join(
    os.path.dirname(SUITE), 'operation-rising-sun', 'Battlefield 1943 (XBLA)', '5841097E', '000D0000',
    '945CD965D6B2B2584A9C06CE16F38A075A6D390558')
BUILD = os.path.join(os.environ.get('BF1943_WORK') or os.path.join(SUITE, 'work'), 'levels', 'wake_island_s')


def fuzz(*args, **kw):
    report = fuzzcheck.run(*args, **kw)
    assert report.ok, report.text()


_GAME = []


def game():
    """Wake Island's bundles (read once)."""
    from bfbc2core import game1943
    if not os.path.isdir(os.path.join(GAME, 'levels')):
        raise unittest.SkipTest(f'no BF1943 game folder at {GAME}')
    if not _GAME:
        _GAME.append(game1943.Game(GAME, 'wake_island_s'))
    return _GAME[0]


def test_dbx_partitions():
    from bfbc2core import dbx
    g = game()
    names = sorted((n for n in g.names() if n.endswith('.dbx')), key=lambda n: (len(g.data(n)), n))
    picks = [names[len(names) // 10], names[len(names) // 2], 'levels/wake_island_s.dbx'] if names else []
    seeds = [g.data(n) for n in picks if g.data(n)]
    fuzz('dbx.to_xml (BF1943)', dbx.to_xml, seeds[:2], iterations=600)
    fuzz('dbx.parse (BF1943 level)', dbx.parse, seeds[2:], iterations=60, limit=20.0)


def test_bundle():
    from fbrb1943 import Bundle
    game()
    path = os.path.join(GAME, 'menu', 'shared', 'fonts_us-00.fbrb')
    raw = open(path, 'rb').read()
    fuzz('fbrb1943.Bundle (BF1943)', Bundle, [raw], iterations=300)
    cut = struct.unpack_from('>I', raw, 4)[0]
    directory, body = gzip.decompress(raw[8:8 + cut]), raw[8 + cut:]

    def with_directory(d):
        z = gzip.compress(d, mtime=0)
        return Bundle(b'FbRB' + struct.pack('>I', len(z)) + z + body)
    strlen = struct.unpack_from('>I', directory, 4)[0]
    fields = [(4, '>I'), (8 + strlen, '>I'), (len(directory) - 4, '>I')] + \
        [(12 + strlen + 24 * k + 4 * w, '>I') for k in range(3) for w in range(6)]
    fuzz('fbrb1943 directory (BF1943)', with_directory, [directory], iterations=600, fields=[fields])


def test_terrain_water_material():
    from bfbc2core import materialmap1943, terrain, water
    g = game()
    hf = g.data('terrains/wake_island_s/wake_island_s.heightfield-000.res')
    small = [n for n in g.names('terrains/') if n.endswith('.res') and 'heightfield-0' in n and
             len(g.data(n)) < 100000]
    seeds = [g.data(n) for n in small[:1]] or [hf]
    # same=: the table after the heights is not decoded, so a cut there loses nothing the reader returns
    fuzz('terrain.read (BF1943)', terrain.read, seeds, iterations=150,
         same=lambda a, b: np.array_equal(a.raw, b.raw) and np.array_equal(a.min, b.min) and np.array_equal(a.max, b.max))
    fuzz('materialmap1943.read (BF1943)', materialmap1943.read,
         [g.data('terrains/wake_island_s/wake_island_s-materialmap.res')], iterations=150, limit=10.0)
    path = os.path.join(SUITE, 'work', 'fuzz', 'water.mesh.res')
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def mesh(d):
        with open(path, 'wb') as f:
            f.write(d)
        return water.read_mesh(path)
    try:
        fuzz('water.read_mesh (BF1943)', mesh, [g.data('terrains/wake_island_s/wake_island_s.water.mesh.res')],
             iterations=200)
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_menu_movie():
    import apt
    from fbrb1943 import Bundle
    game()
    b = Bundle(open(os.path.join(GAME, 'menu', 'main', 'mainmenu-00.fbrb'), 'rb').read())

    def validated(raw):
        a = apt.Apt(raw)
        return a.validate()
    fuzz('apt.Apt (BF1943)', validated, [b.data('Menu/main_nav.res')], iterations=150, limit=10.0)


def test_havok():
    import havok461
    import hkpack
    g = game()
    names = sorted(n for n in g.names() if n.endswith('_havok.res'))
    seeds = [g.data(n) for n in names[:: max(1, len(names) // 3)][:3]]

    def collision(d):
        pf = hkpack.Packfile(d, classes=havok461.CLASSES)
        out = []
        for off in pf.objects_of('hkStorageExtendedMeshShapeMeshSubpartStorage'):
            v, t = havok461.triangles(pf, off)
            if v and t:
                out.append(np.asarray(v, np.float32)[np.asarray(t)])
        for off in pf.objects_of('hkConvexVerticesShape'):
            out.append(havok461.convex_box(pf, off)[0])
        return out
    # prefix_ok: a packfile need not reach the end of its .res, and the reader reads no further than its sections
    fuzz('hkpack + havok461 (BF1943)', collision, seeds, iterations=300, prefix_ok=True, limit=10.0)


class Overlay(io.RawIOBase):
    """A read-only file whose first bytes are `head` (the case) and the rest the real file's from `skip` on."""

    def __init__(self, path, head, skip):
        self.f, self.head, self.skip, self.pos = open(path, 'rb'), head, skip, 0

    def seek(self, pos, whence=0):
        self.pos = pos
        return pos

    def read(self, n=-1):
        out = b''
        if self.pos < len(self.head):
            out = self.head[self.pos:self.pos + n]
        rest = n - len(out)
        if rest > 0:
            self.f.seek(self.pos + len(out) - len(self.head) + self.skip)
            out += self.f.read(rest)
        self.pos += len(out)
        return out

    def close(self):
        self.f.close()


def test_package():
    import stfs
    if not os.path.isfile(PACKAGE):
        raise unittest.SkipTest(f'no BF1943 package at {PACKAGE}')
    with open(PACKAGE, 'rb') as f:
        region = f.read(0x20000)        # header, the first hash tables, the file table
    pkg = stfs.Package(PACKAGE)
    try:
        small = sorted((e for e in pkg.entries if not e['dir']), key=lambda e: e['size'])[:4]
        names = [e['path'] for e in small]
        table = pkg._addr(pkg.ft_start)
    finally:
        pkg.f.close()

    def read(head):
        stfs.open = lambda path, mode: Overlay(path, head, len(region))
        try:
            p = stfs.Package(PACKAGE)
        finally:
            del stfs.open
        try:
            return [p.read(e) for e in p.entries if e['path'] in names]
        finally:
            p.f.close()
    fields = [(0x340, '>I'), (0x37B, 'B'), (0x37C, '<H'), (0x37E, '<H'), (0x395, '>I')]
    fields += [(table + 0x40 * k + o, fmt) for k in range(8) for o, fmt in ((0x28, 'B'), (0x29, '<H'), (0x2F, '<H'),
                                                                            (0x32, '>H'), (0x34, '>I'))]
    # prefix_ok: a cut of this region is a deletion inside the package (the rest of the real file follows it)
    fuzz('stfs.Package (BF1943 package)', read, [region], iterations=400, fields=[fields], prefix_ok=True, limit=10.0)


def test_navkit_builds():
    import hpdtool
    import kymesh
    from bfbc2core import rsnv
    hpd = os.path.join(BUILD, 'navkit', 'wake_island_s.hpd')
    if not os.path.isfile(hpd):
        raise unittest.SkipTest(f'no Wake Island build at {hpd}')
    f = hpdtool.parse(open(hpd, 'rb').read())
    meshes = [s.mesh for s in f.sectors[:: max(1, len(f.sectors) // 3)][:3]]
    fuzz('kymesh.parse_mesh (Wake Island)', kymesh.parse_mesh, meshes, iterations=600)
    rs = os.path.join(BUILD, 'navkit', 'wake_island_s.be.rsnv')
    if os.path.isfile(rs):
        fuzz('rsnv.load (Wake Island)', rsnv.load, [open(rs, 'rb').read()], iterations=40, limit=30.0)
