r"""NavKit big-endian: neighbour links between two 0.8 m sectors of a build with detailed zones

On sample files made here (no game data): ground sloped at 35 degrees along x (0.56 m per 0.8 m column, within the
soldier's slope), three sectors, one of them detailed (pitch 0.2). merge_detailed finds the normal sectors' neighbour
links again after the merge. By the 0.2 m grid's step rule (NavKit's default, navbuild.NORMAL_LINKS_OWN_GRID False:
what every build did until 2026-11) the slope rule predicts a quarter of a 0.8 m column's rise and the border portals
across the sloped seam lose their links; by the 0.8 m grid's own rule (soldier1943.SETTINGS) every one is linked.
"""
import dataclasses
import math
import os
import struct

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import hpdtool  # noqa: E402
import kymesh  # noqa: E402
import navbuild  # noqa: E402
import soldier1943  # noqa: E402

SLOPE = math.tan(math.radians(35.0))


def write_samples(path, pitch, x0, z0, nx, nz, ground=None):
    """A navsample version-1 file: one surface per column on the plane y = SLOPE x, its normal from the slope; with
    ground (x0, x1, z0, z1), only columns inside it have one (a small detailed stage)."""
    out = bytearray(struct.Struct('<4sI3f2i6fI').pack(b'ONS1', 1, pitch, x0, z0, nx, nz, 2.0, -2.0, 0.35, 0.37, 1.8,
                                                      0.0, 0))
    norm = math.hypot(SLOPE, 1.0)
    nxb, nyb = int(round(-SLOPE / norm * 127)), int(round(1.0 / norm * 127))
    rec = struct.Struct('<f3bB')
    for j in range(nz):
        for i in range(nx):
            x, z = x0 + i * pitch, z0 + j * pitch
            if ground and not (ground[0] <= x < ground[1] and ground[2] <= z < ground[3]):
                out.append(0)
                continue
            out.append(1)
            out += rec.pack((x0 + i * pitch) * SLOPE, nxb, nyb, 0, 0)
    with open(path, 'wb') as f:
        f.write(bytes(out))


def seam_portals(path):
    """(linked, all): the border portals of sector (0, 0) facing (1, 0) - the sloped seam between two 0.8 m sectors."""
    f = hpdtool.parse(open(path, 'rb').read())
    meshes = {s.id: kymesh.parse_mesh(s.mesh) for s in f.sectors}
    mesh = meshes[0x0000]
    assert abs(mesh.pitch - 0.8) < 1e-3 and abs(meshes[0x0101].pitch - 0.2) < 1e-3, 'sector (1, 1) detailed, (0, 0) not'
    refs = {ref for link in mesh.links if link.sector_id == 0x0100 for obj in link.objects for ref in obj.refs}
    linked = total = 0
    for ai, area in enumerate(mesh.areas):
        first = 0x50 + len(area.portals) * 0x20
        for bi, portal in enumerate(area.border_portals):
            if round(portal.direction[0]) == 1 and round(portal.direction[2]) == 0:
                total += 1
                linked += (ai, first + bi * 0x2C) in refs
    return linked, total


def build(tmp, own_grid):
    settings = dataclasses.replace(soldier1943.SETTINGS, normal_links_own_grid=own_grid)
    out = os.path.join(tmp, f'links_{int(own_grid)}.hpd')
    navbuild.build(os.path.join(tmp, 's08.bin'), os.path.join(tmp, 'forbid.npy'), 0, 0, out, step=soldier1943.STEP,
                   seeds=[(100.0, 100.0 * SLOPE, 50.0)], max_water_depth=None,
                   detailed={'samples': os.path.join(tmp, 's02.bin'), 'high': None, 'sectors': {(1, 1)}},
                   log=lambda _m: None, settings=settings)
    return out


def test_settings():
    assert soldier1943.SETTINGS.normal_links_own_grid, "BF1943's builds link normal sectors by the 0.8 m rule"
    assert not navbuild.BFBC2.normal_links_own_grid, "NavKit's default (BFBC2) keeps the 0.2 m rule until measured"


def test_sloped_seam_links(tmp_path):
    tmp = str(tmp_path)
    np.save(os.path.join(tmp, 'forbid.npy'), np.zeros((0, 3, 3), np.float32))
    # sectors (0, 0), (1, 0) and (1, 1): ground on a strip 16 m either side of the sloped seam x = 100
    write_samples(os.path.join(tmp, 's08.bin'), 0.8, 0.0, 0.0, 250, 250, ground=(84.0, 116.0, 0.0, 116.0))
    # sector (1, 1) and 4 m around it, with ground in its corner only
    write_samples(os.path.join(tmp, 's02.bin'), 0.2, 96.0, 96.0, 520, 520, ground=(96.0, 112.0, 96.0, 112.0))
    linked_old, total_old = seam_portals(build(tmp, False))
    linked_new, total_new = seam_portals(build(tmp, True))
    assert total_old == total_new and total_new > 0, (total_old, total_new)
    assert linked_new == total_new, f'0.8 m rule: {linked_new} of {total_new} seam portals linked'
    assert linked_old < total_old, f'0.2 m rule: {linked_old} of {total_old} linked (the old loss not reproduced)'
