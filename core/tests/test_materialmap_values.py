r"""Terrain material map (materialmap1943): pixels, placement and lookups on a made-up map

materialmap1943 on a made-up 512 x 512 map of two tiles (no game data): each byte holds two pixels, the low nibble
first; a run is a pair of equal bytes and a count of further copies; tiles go down the map; lookups outside the map
give -1 (at) or None (name_at), as does a material the name table does not name; place lays the map over a height
field's extent; the level's map is found in a bundle index (resource_name, for_level); and the name table is refused
when it is missing, followed by bytes, without its last zero byte, not ASCII, or shorter than the map needs.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import struct
from types import SimpleNamespace

import numpy as np

from bfbc2core import materialmap1943 as mm

PLAIN = bytes([0x11, 0x11, 255])                            # 256 bytes of 0x11: material 1 in both nibbles


def tile(first_rows):
    rows = list(first_rows) + [PLAIN] * (256 - len(first_rows))
    body = b''.join(rows)
    return struct.pack('>I', len(body)) + body + struct.pack('>256H', *[len(r) for r in rows])


def made_up(names=b'a\0b\0c\0', tiles=None):
    """512 x 512: tile 0 (z 0-255) row 0 is 0x21 then 255 zero bytes (pixels 1, 2, then 0); tile 1's row 0 (z 256)
    is all zero; every other row material 1."""
    if tiles is None:
        tiles = [tile([bytes([0x21, 0x00, 0x00, 254])]), tile([bytes([0x00, 0x00, 255])])]
    return struct.pack('>IH', 512, len(tiles)) + b''.join(tiles) + struct.pack('>I', len(names)) + names


def refusal(data):
    try:
        mm.read(data)
    except ValueError as e:
        return str(e)
    raise AssertionError('read')


def test_pixels():
    m = mm.read(made_up())
    assert m.names == ['a', 'b', 'c'] and m.grid.shape == (512, 512)
    assert m.grid[0, :4].tolist() == [1, 2, 0, 0], 'the low nibble is the first pixel'
    assert m.grid[1, :3].tolist() == [1, 1, 1] and int(m.grid[0, 511]) == 0
    assert int(m.grid[256, 7]) == 0 and int(m.grid[257, 7]) == 1 and int(m.grid[255, 511]) == 1, 'tile 1 below tile 0'
    assert int(m.grid[2:256].min()) == 1 and int(m.grid[257:].min()) == 1


def test_lookups():
    m = mm.read(made_up())
    xs = [0, 1, 2, 0.99, -0.01, 511.99, 512, 0, 0, 5, 5]
    zs = [0, 0, 0, 0.5, 0, 511.99, 0, -0.5, 512, 256, 257.5]
    assert m.at(xs, zs).tolist() == [1, 2, 0, 1, -1, 1, -1, -1, -1, 0, 1]
    assert m.at(1, 0).tolist() == 2, 'a single point'
    assert m.name_at([0, 1, 2, -3], [0, 0, 0, 0]) == ['b', 'c', 'a', None]
    assert m.name_at(1.5, 0.0) == ['c']
    unnamed = mm.MaterialMap(['a'], np.array([[0, 1], [3, 0]], np.uint8))
    assert unnamed.name_at([0, 1, 0], [0, 0, 1]) == ['a', None, None], 'a material without a name'


def test_place():
    m = mm.read(made_up())
    hf = SimpleNamespace(min=np.array([-1024.0, -50.0, -2048.0]), max=np.array([1024.0, 300.0, 0.0]))
    assert mm.place(m, hf) is m
    assert (m.min_x, m.min_z, m.pixel) == (-1024.0, -2048.0, 4.0)
    assert m.at([-1024 + 4.5, -1024 + 3.9, -1024 - 0.1], [-2048, -2048, -2048]).tolist() == [2, 1, -1]
    assert m.at(-1020, -2048 + 4 * 256).tolist() == 0


def test_resource_name():
    index = ['terrains/wake/wake-materialmap.res', 'terrains/wake/textures/x-materialmap.res',
             'terrains/wake/other.res', 'terrains/wake2/wake2-materialmap.res', 'terrains/wake/z-materialmap.dds',
             'terrains/wake/a-TerrainMaterialMapAsset.res', 'levels/wake/materialmap.res']
    assert mm.resource_name(index, 'wake') == 'terrains/wake/a-TerrainMaterialMapAsset.res'
    assert mm.resource_name(index[:1], 'wake') == 'terrains/wake/wake-materialmap.res'
    assert mm.resource_name(index[1:5], 'wake') is None, 'textures, other names, other levels, other types'
    assert mm.resource_name(index, 'wake2') == 'terrains/wake2/wake2-materialmap.res'


def test_for_level():
    data = made_up()
    game = SimpleNamespace(names=lambda: ['terrains/lv/lv-materialmap.res', 'other.res'],
                           data=lambda name: data if name == 'terrains/lv/lv-materialmap.res' else None)
    hf = SimpleNamespace(min=np.array([0.0, 0.0, 0.0]), max=np.array([1024.0, 0.0, 1024.0]))
    m = mm.for_level(game, 'lv', hf)                     # pyright: ignore[reportArgumentType]
    assert m is not None and m.pixel == 2.0 and m.name_at(2.5, 0.5) == ['c']
    assert mm.for_level(game, 'other', hf) is None       # pyright: ignore[reportArgumentType]


def test_name_table_refused():
    good = made_up()
    head = len(good) - len(b'a\0b\0c\0') - 4
    assert 'ends before the name table' in refusal(good[:head + 3])
    assert refusal(good + b'xy') == '2 bytes after the name table'
    assert refusal(made_up(b'a\0b\0c')) == "the name table's last name (b'c') does not end with a zero byte"
    assert refusal(made_up(b'a\0\xe9\0c\0')) == 'the name table holds a byte that is not ASCII (0xe9)'
    assert refusal(made_up(b'a\0b\0')) == 'the map uses material 2, and the name table names 2'
    assert mm.read(made_up(b'a\0b\0c\0d\0')).names == ['a', 'b', 'c', 'd'], 'unused names are fine'


def test_tiles_refused():
    good = made_up()
    assert 'side 0' in refusal(struct.pack('>IH', 0, 0) + good[6:])
    assert '3 tiles' in refusal(struct.pack('>IH', 512, 3) + good[6:])
    assert 'side 768' in refusal(struct.pack('>IH', 768, 2) + good[6:]), 'a side that is not whole tiles'
    assert refusal(good[:5]).startswith('not a TerrainMaterialMap: 5 bytes')
    short_row = tile([bytes([0x01, 0x02] * 127 + [0x01])])         # 255 literal bytes (no two equal side by side)
    assert refusal(made_up(tiles=[short_row, tile([])])) == 'tile 0: a row decodes to 255 bytes, not 256'
    long_row = tile([bytes([0x11, 0x11, 255, 0x11])])
    assert refusal(made_up(tiles=[tile([]), long_row])) == 'tile 1: a row decodes to 257 bytes, not 256'
    lying = bytearray(tile([]))
    struct.pack_into('>H', lying, 4 + 256 * 3, 4)                   # the first row's length says 4, not 3
    assert refusal(made_up(tiles=[bytes(lying), tile([])])) == 'tile 0: row lengths sum to 769, the body is 768 bytes'


def test_length_boundaries():
    head = struct.pack('>IH', 512, 2)
    assert refusal(head) == 'not a TerrainMaterialMap: 2 tiles cannot fit in 6 bytes', 'six bytes: a header'
    assert refusal(head[:5]) == 'not a TerrainMaterialMap: 5 bytes, shorter than its 6-byte header'
    least = 6 + 2 * (4 + 512) + 4                        # the least two tiles can take (empty bodies) and the table
    assert refusal(head + bytes(least - 7)) == f'not a TerrainMaterialMap: 2 tiles cannot fit in {least - 1} bytes'
    assert refusal(head + bytes(least - 6)) == 'tile 0: a row decodes to 0 bytes, not 256', 'past the first check'
    one = tile([])                                       # a whole tile: its size word, 768 bytes of rows, 512 of lengths
    end_of_tile_1 = head + one + struct.pack('>I', 0)    # tile 1: only its size word, then the file ends
    assert refusal(end_of_tile_1) == 'tile 1: 0 bytes of body and its row lengths run past the end of the file'
    assert refusal(end_of_tile_1[:-1]) == f'tile 1: the file ends at {len(end_of_tile_1) - 1} bytes, before the tile'
    two = head + one + one
    assert refusal(two) == 'the file ends before the name table', 'both tiles whole, no table'
    assert refusal(two + bytes(3)) == 'the file ends before the name table'
    assert refusal(two + struct.pack('>I', 0)) == 'the map uses material 1, and the name table names 0', 'empty table'
    assert refusal(made_up(names=b'a\0' + b'x' * 50)) == \
        f"the name table's last name ({b'x' * 40!r}) does not end with a zero byte", 'cut to 40 bytes'


def test_row_ending_in_a_pair():
    row = bytes(range(1, 255)) + bytes([7, 7])          # 254 literal bytes, then a pair with no count byte
    assert refusal(made_up(names=b'a\0b\0c\0d\0e\0f\0g\0h\0', tiles=[tile([row]), tile([])])) == \
        'tile 0: a row ends inside a run (a pair with no count byte)'


def test_two_tiles_across():
    tiles = [tile([bytes([0x22, 0x22, 255])] if k == 1 else []) for k in range(8)]   # tile 1: the top right
    m = mm.read(struct.pack('>IH', 1024, 8) + b''.join(tiles) + struct.pack('>I', 6) + b'a\0b\0c\0')
    assert m.grid.shape == (1024, 1024)
    assert int(m.grid[0, 511]) == 1 and int(m.grid[0, 512]) == 2 and int(m.grid[1, 512]) == 1 and int(m.grid[256, 600]) == 1
    assert refusal(struct.pack('>IH', 768, 3) + b''.join(tiles[:3]) + struct.pack('>I', 2) + b'a\0') == \
        'not a TerrainMaterialMap: side 768, 3 tiles', 'a side that is not whole tiles across'
