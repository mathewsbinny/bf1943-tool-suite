r"""Terrain height field (terrain): heights, mesh, byte order and refusals on made-up fields

terrain on made-up height fields (no game data), with a field that is not a plane so each rule shows: height() splits
each grid square along its (i, j)-(i+1, j+1) diagonal (either side agrees on the diagonal itself), extrapolates past
the outer samples from the last square, and scales the stored samples between the bounds' heights; mesh() takes every
stride-th sample (2 by default) at its world place and joins them in two triangles a square; read() takes the byte
order whose size fits the file (little-endian when neither does, as the refusal's size shows), and refuses a file
of just a header, a size that is not a power of two or at least 2, heights past the end, and bounds that are not
finite, empty across, or upside down (a flat field is fine).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import struct

import numpy as np

from bfbc2core import terrain


def field(size=4, lo=(0.0, 0.0, 0.0), hi=(4.0, 65.535, 4.0), samples=None, big=False, extra=0, cut=0):
    """A height field's bytes: the header (bounds, size, five more words, a byte), size x size samples (rows along
    z), extra bytes after them, cut bytes taken off the end."""
    e = '>' if big else '<'
    if samples is None:
        samples = [[1000 * (i + 10 * j + 100 * i * j) // 20 for i in range(size)] for j in range(size)]
    head = struct.pack(e + '6f6IB', *lo, *hi, size, 0, 0, 0, 0, 0, 0)
    body = struct.pack(f'{e}{size * size}H', *[v for row in samples for v in row])
    data = head + body + bytes(extra)
    return data[:len(data) - cut] if cut else data


def refusal(data):
    try:
        terrain.read(data)
    except ValueError as e:
        return str(e)
    return 'read'


def test_heights_split_on_the_diagonal():
    hf = terrain.read(field())                      # H[j, i] = (i + 10 j + 100 i j) / 20 m, cells of 1 m
    points = [(0.75, 0.25), (0.25, 0.75), (2.5, 1.5), (3.5, 0.25), (0.25, 3.5), (-0.5, 0.25), (0.25, -0.5),
              (1.0, 2.0)]
    want = [1.4125, 1.6375, 20.875, 4.05, 5.5125, -2.4, -2.7375, 11.05]
    got = hf.height([p[0] for p in points], [p[1] for p in points])
    assert np.allclose(got, want, atol=1e-4), list(zip(got.tolist(), want))
    assert np.allclose(hf.height(0.75, 0.25), 1.4125, atol=1e-4), 'a single point'


def test_heights_scaled_between_the_bounds():
    hf = terrain.read(field(size=2, lo=(10.0, -5.0, 20.0), hi=(12.0, 5.0, 22.0), samples=[[0, 65535], [32768, 0]]))
    assert np.allclose(hf.heights, [[-5.0, 5.0], [32768 * 10 / 65535 - 5, -5.0]], atol=1e-5)
    assert hf.cell == (1.0, 1.0) and hf.size == 2 and hf.raw.tolist() == [[0, 65535], [32768, 0]]
    assert np.allclose(hf.height(10.5, 20.25), -5.0 + 10 * 0.5 + (-10.0) * 0.25, atol=1e-4), 'origin at the bounds'


def test_mesh():
    hf = terrain.read(field(lo=(100.0, 0.0, 200.0), hi=(108.0, 65.535, 216.0)))     # cells of 2 m by 4 m
    verts, tris = hf.mesh()
    assert np.allclose(verts, [[100, 0, 200], [104, 0.1, 200], [100, 1.0, 208], [104, 21.1, 208]], atol=1e-4), verts
    assert tris.tolist() == [[0, 2, 3], [0, 3, 1]]
    verts, tris = hf.mesh(stride=1)
    assert len(verts) == 16 and len(tris) == 18 and np.allclose(verts[5], [102.0, hf.heights[1, 1], 204.0])
    verts, _ = hf.mesh(stride=3)
    assert np.allclose(verts[:, 0], [100, 106, 100, 106]) and np.allclose(verts[:, 2], [200, 200, 212, 212])


def test_byte_orders():
    little, big = terrain.read(field()), terrain.read(field(big=True))
    assert np.array_equal(little.raw, big.raw) and np.array_equal(little.heights, big.heights)
    assert terrain.read(field(size=2, big=True)).size == 2, 'the smallest field, big-endian'
    assert terrain.read(field(big=True, extra=100)).size == 4, 'data after the samples (level of detail)'
    # neither order fits (cut short): read little-endian, and refused with the size read so
    assert refusal(field(cut=2)) == 'data: not a TerrainHeightfield (size 4)'
    assert refusal(field(big=True, cut=2)) == f'data: not a TerrainHeightfield (size {4 << 24})'


def test_refusals():
    header = field()[:terrain.HEADER.size]
    assert refusal(header[:-1]) == 'data: 48 bytes, shorter than a TerrainHeightfield header'
    assert refusal(header) == 'data: not a TerrainHeightfield (size 4)', 'a header and no samples'
    assert refusal(field(size=3, samples=[[0] * 3] * 3)) == 'data: not a TerrainHeightfield (size 3)'
    assert refusal(field(size=1, samples=[[0]])) == 'data: not a TerrainHeightfield (size 1)'
    assert refusal(field(size=6, samples=[[0] * 6] * 6, big=True)) == f'data: not a TerrainHeightfield (size {6 << 24})'
    flat = terrain.read(field(lo=(0, 7.0, 0), hi=(4, 7.0, 4)))
    assert np.all(flat.heights == 7.0), 'a flat field'
    for lo, hi in (((0, 50.0, 0), (4, 40.0, 100.0)), ((4.0, 0, 0), (4.0, 1, 4)), ((0, 0, 4.0), (4, 1, 4.0)),
                   ((0, float('-inf'), 0), (4, 1, 4)), ((0, 0, 0), (4, float('inf'), 4)),
                   ((float('-inf'), 0, 0), (4, 1, 4)), ((0, 0, 0), (4, 1, float('inf'))),
                   ((0, float('nan'), 0), (4, 1, 4)), ((0, 0, float('-inf')), (4, 1, 4)),
                   ((0, 0, 0), (float('inf'), 1, 4))):
        assert refusal(field(lo=lo, hi=hi)).startswith('data: not a TerrainHeightfield (bounds '), (lo, hi)


def test_find(tmp_path):
    (tmp_path / 'a' / 'b').mkdir(parents=True)
    (tmp_path / 'a' / 'b' / 'Wake.HeightField-000.res').write_bytes(b'x')
    assert terrain.find(str(tmp_path), 'wake') == str(tmp_path / 'a' / 'b' / 'Wake.HeightField-000.res')
    assert terrain.find(str(tmp_path), 'other') is None


def test_the_diagonal_is_the_second_triangle():
    # on the diagonal both triangles give a + (d - a) t, but not always to the last bit: a point on it is the
    # (i, j + 1) triangle's, as the game splits the square (here the two differ by one rounding)
    hf = terrain.read(field(size=2, lo=(0, 0, 0), hi=(2, 100.0, 2), samples=[[42445, 19772], [51750, 6328]]))
    a, b, c, d = hf.heights[0, 0], hf.heights[0, 1], hf.heights[1, 0], hf.heights[1, 1]   # float32, as stored
    t = np.array(0.3)
    assert a + (b - a) * t + (d - b) * t != a + (c - a) * t + (d - c) * t
    assert float(hf.height(t, t)) == a + (c - a) * t + (d - c) * t


def test_a_size_read_only_one_way():
    # a big-endian size of 1: too small that way, and huge read little-endian: read little-endian, and refused so
    assert refusal(field(size=1, samples=[[0]], big=True)) == f'data: not a TerrainHeightfield (size {1 << 24})'
    hf = terrain.read(field(lo=(1.0, 2.0, 3.0), hi=(5.0, 6.0, 7.0)))
    assert hf.min.tolist() == [1.0, 2.0, 3.0] and hf.max.tolist() == [5.0, 6.0, 7.0] and hf.size == 4
