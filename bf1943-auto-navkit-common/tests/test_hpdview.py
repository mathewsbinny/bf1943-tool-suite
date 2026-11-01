r"""NavKit (both variants): one sector of a path-data file drawn to a PNG (hpdview)

hpdview on a .hpd built here with navbuild from made-up samples (no game data): the sector picked by index and by
id, the drawing's title (areas, nodes, edges), the zoom window, the help and usage exits; points_between and
segs_from_lists on made-up mesh bytes.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import struct
import sys

import matplotlib.pyplot as plt
import numpy as np

import hpdtool
import hpdview
import navbuild
import navsample


def flat(pitch, min_x, min_z, nx, nz, y=0.0, ground=None):
    """One flat surface per column; ground(x, z) -> False leaves the column empty (test_navbuild_edges.py)."""
    counts = np.ones(nx * nz, np.int64)
    if ground:
        for j in range(nz):
            for i in range(nx):
                counts[j * nx + i] = bool(ground(min_x + i * pitch, min_z + j * pitch))
    start = np.zeros(nx * nz + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    n = int(start[-1])
    return navsample.Samples(pitch, min_x, min_z, nx, nz, 50.0, -50.0, 0.4, 0.6, 1.8, 0.0, start,
                             np.full(n, y, np.float32), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                             np.zeros(n, np.uint8), None)


def build(path):
    s = flat(0.8, 0.0, 0.0, 60, 60, ground=lambda x, z: not (20 < x < 26))
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, str(path), log=lambda *a: None)
    finally:
        navsample.load = load
    return str(path)


def run(argv):
    """hpdview.main with argv; (exit code, output, the drawing's axes or None)."""
    old = sys.argv
    sys.argv = argv
    plt.close('all')
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = hpdview.main()
        axes = plt.gcf().axes[0] if plt.get_fignums() else None
        return code, out.getvalue(), axes
    finally:
        sys.argv = old


def test_draw_by_index_and_id(tmp_path):
    path = build(tmp_path / 'gap.hpd')
    sector = hpdtool.parse(open(path, 'rb').read()).sectors[0]
    png = tmp_path / 'sector.png'
    try:
        code, out, ax = run(['hpdview.py', path, '0', str(png)])
        assert code is None and out.strip() == f'wrote {png}'
        assert png.read_bytes()[:4] == b'\x89PNG'
        title = ax.get_title()
        assert title == f'sector 0x0000: 6 areas, {len(sector.nodes)} nodes, {len(sector.edges)} edges', title
        assert len(ax.lines) > len(sector.edges), 'grid, outlines and edges drawn'
        code, out, ax = run(['hpdview.py', path, '0x0', str(tmp_path / 'zoom.png'), '0', '10', '5', '15'])
        assert ax.get_xlim() == (0.0, 10.0) and ax.get_ylim() == (5.0, 15.0)
        assert (tmp_path / 'zoom.png').is_file()
    finally:
        plt.close('all')


def test_usage():
    try:
        code, out, _ = run(['hpdview.py', '--help'])
        assert code == 0 and 'Usage' in out
        code, out, _ = run(['hpdview.py'])
        assert code == 1 and 'Usage' in out
        code, out, _ = run(['hpdview.py', 'a.hpd', '0', 'out.png', '-h'])
        assert code == 0
    finally:
        plt.close('all')


def test_lists():
    mesh = bytearray(0x100)
    struct.pack_into('<3f3f3f', mesh, 0x80, 1, 2, 3, 4, 5, 6, 7, 8, 9)
    assert hpdview.points_between(bytes(mesh), 0x80, 0x80 + 36) == [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
    assert hpdview.points_between(bytes(mesh), 0x80, 0x80 + 35) == [(1, 2, 3), (4, 5, 6)], 'a whole point only'
    # an area object at 0x10: two lists (count 2 at +0x40, table offset at +0x44 -> 0x60 from the object)
    struct.pack_into('<II', mesh, 0x10 + 0x40, 2, 0x60)
    struct.pack_into('<3I', mesh, 0x10 + 0x60, 0x70, 0x70 + 24, 0x70 + 36)
    lists = hpdview.segs_from_lists(bytes(mesh), 0x10, 0x40, 0x44)
    assert lists == [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9)]]
    assert hpdview.segs_from_lists(bytes(mesh), 0x10, 0x38, 0x3C) == [], 'count 0: none'
    assert hpdview.u32(bytes(mesh), 0x50) == 2 and hpdview.f32(bytes(mesh), 0x80) == 1.0
