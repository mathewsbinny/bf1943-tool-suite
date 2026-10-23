"""Tests for hkpack.py on Havok 4.6.1 (big-endian, no __types__) and the havok461 layouts.

Uses BF1943's Wake Island bundle from the game folder bf1943-xenia-kit builds (BF1943_GAME overrides); skipped
when it is absent.
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, 'tools'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
import havok461
import hkpack
from fbrb1943 import Bundle

failures = []
GAME = os.environ.get('BF1943_GAME', os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'title', 'dist', 'xenon'))
BUNDLE = os.path.join(GAME, 'levels', 'wake_island_s', 'reduced-level-00.fbrb')


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


if __name__ == '__main__':
    if not os.path.isfile(BUNDLE):
        print(f'skip: no BF1943 game folder at {GAME}')
        sys.exit(0)
    bundle = Bundle(open(BUNDLE, 'rb').read())
    names = [e['name'] for e in bundle.entries if e['name'].lower().endswith('_havok.res')]
    meshes = checked = 0
    for name in names:
        pf = hkpack.Packfile(bundle.data(name), classes=havok461.CLASSES)
        if checked == 0:
            check(not pf.little_endian and pf.contents_version.startswith('Havok-4.6') and pf.member_size == 20,
                  f'{name}: big-endian Havok 4.6 with 20-byte member records')
        checked += 1
        for off in pf.objects_of('hkStorageExtendedMeshShapeMeshSubpartStorage'):
            o = pf.read_object(off, 'hkStorageExtendedMeshShapeMeshSubpartStorage')
            v, t = havok461.triangles(pf, off)
            # the vertex data ends where the index data begins (both arrays are contiguous after the object)
            vp, ip = pf.deref(off + 0x08), pf.deref(off + 0x14)
            spans = not o['indices16'] or vp + 16 * len(o['vertices']) == ip
            # 16-bit triangles are 4 words (a, b, c, then padding the exporter did not always clear)
            if not (v and t and all(max(tri) < len(v) for tri in t) and len(o['indices16']) % 4 == 0 and spans):
                check(False, f'{name} @0x{off:X}: {len(v)} vertices, {len(t)} triangles')
            meshes += 1
    check(meshes > 0 and not failures, f'{meshes} triangle meshes in {checked} Havok files read with valid indices')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
