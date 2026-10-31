r"""Core readers on made-up damaged input (no game data), 2026-10-30 second review: the terrain material map refuses
a row that ends inside a run and a tile past the end of the file (an IndexError / struct.error before); the height
field reader refuses a size of 1 (it was read, and height() then indexed outside it); level1943.load puts its tool
folders on sys.path once, however many levels are loaded (it grew by two per load).

  python -B core\tests\test_readers_malformed.py
"""
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bfbc2core import level1943, materialmap1943, terrain  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def refused(fn, *args):
    """'ValueError: ...' for the refusal wanted, another exception's name, or 'read'."""
    try:
        fn(*args)
        return 'read'
    except ValueError as e:
        return f'ValueError: {e}'
    except Exception as e:
        return f'{type(e).__name__}: {e}'


def material_map(rows):
    """A 512 x 512 map (one tile across, two down, of 256 rows each) whose first tile's rows are `rows` (each the
    encoded bytes), every other row a plain run; names 'a', 'b'."""
    plain = bytes([0x11, 0x11, 255])                       # 256 copies of 0x11
    out = struct.pack('>IH', 512, 2)
    for t in range(2):
        body_rows = (rows if t == 0 else []) + [plain] * (256 - (len(rows) if t == 0 else 0))
        body = b''.join(body_rows)
        out += struct.pack('>I', len(body)) + body + struct.pack('>256H', *[len(r) for r in body_rows])
    names = b'a\0b\0'
    return out + struct.pack('>I', len(names)) + names


if __name__ == '__main__':
    good = material_map([])
    check(materialmap1943.read(good).names == ['a', 'b'], 'a made-up material map reads')
    # a row of 255 literal bytes ending in a pair of equal bytes with no count byte after them
    bad_row = bytes(range(1, 255)) + bytes([7, 7])
    why = refused(materialmap1943.read, material_map([bad_row]))
    check(why.startswith('ValueError'), f'a row ending inside a run is refused in words ({why[:80]})')
    why = refused(materialmap1943.read, good[:6 + 4 + 100])
    check(why.startswith('ValueError'), f'a material map cut inside a tile is refused in words ({why[:80]})')

    def heightfield(size):
        head = struct.pack('<6f6IB', 0, 0, 0, 100, 50, 100, size, 0, 0, 0, 0, 0, 0)
        return head + struct.pack(f'<{size * size}H', *([1000] * (size * size))) + bytes(16)
    check(terrain.read(heightfield(2)).size == 2, 'a 2 x 2 height field reads')
    why = refused(terrain.read, heightfield(1))
    check(why.startswith('ValueError'), f'a height field of size 1 is refused ({why[:80]})')

    # 2026-10-30 second review (user's): a name table whose last name has no zero byte, or names fewer materials than
    # the map uses, is refused (the last name was dropped and lookups gave None)
    def with_names(table):
        body = good[:-(4 + 4)]                              # the map without its name table ('a\0b\0', 4 bytes)
        return body + struct.pack('>I', len(table)) + table
    check(materialmap1943.read(with_names(b'a\0b\0')).names == ['a', 'b'], 'the name table rebuilt reads back')
    why = refused(materialmap1943.read, with_names(b'a\0sand'))
    check(why.startswith('ValueError'), f'a last name without its zero byte is refused ({why[:80]})')
    why = refused(materialmap1943.read, with_names(b''))
    check(why.startswith('ValueError'), f'a map using a material its name table does not name is refused ({why[:80]})')

    # height field bounds: finite, a positive horizontal extent (a zero extent or a NaN bound gave NaN heights)
    def bounded(lo, hi, size=2):
        head = struct.pack('<6f6IB', *lo, *hi, size, 0, 0, 0, 0, 0, 0)
        return head + struct.pack(f'<{size * size}H', *([1000] * (size * size))) + bytes(16)
    nan, inf = float('nan'), float('inf')
    for label, lo, hi in (('a zero x extent', (5, 0, 0), (5, 50, 100)), ('a zero z extent', (0, 0, 7), (100, 50, 7)),
                          ('a NaN height bound', (0, 0, 0), (100, nan, 100)),
                          ('an infinite x bound', (0, 0, 0), (inf, 50, 100))):
        why = refused(terrain.read, bounded(lo, hi))
        check(why.startswith('ValueError'), f'a height field with {label} is refused ({why[:80]})')
    why = refused(terrain.read, bytes(20))
    check(why.startswith('ValueError'), f'20 bytes are no height field ({why[:60]})')

    # navpath.route finds the cheapest route although stored costs are rounded down (the plain distance overestimated:
    # 5.1875 came back where 5.125 existed). 0 -> 1 -> 2 -> 3 costs 1.8 + 2.0 + 1.3125 = 5.1125; 0 -> 3 costs 5.1875
    import numpy as np
    from types import SimpleNamespace
    from bfbc2core import navdata, navpath
    pos = np.array([[0, 0, 0], [1.7, 0, 0], [3.4, 0, 0], [5.0, 0, 0]], float) + [0, navdata.Y_OFFSET, 0]
    nav = SimpleNamespace(node_pos=pos, edges=np.array([[0, 1], [1, 2], [2, 3], [0, 3]]),
                          edge_cost=np.array([1.8, 2.0, 1.3125, 5.1875]), node_island=np.zeros(4, int))
    r = navpath.route(nav, (0, 0, 0), (5.0, 0, 0))
    check(r.found and r.nodes == [0, 1, 2, 3] and abs(r.cost - 5.1125) < 1e-9,
          f'navpath.route finds the cheapest route under rounded-down costs ({r.nodes}, {r.cost})')

    missing = os.path.join(HERE, 'no such game folder')
    sizes = []
    for _ in range(3):
        try:
            level1943.load('wake_island_s', game=missing, log=lambda *a: None)
        except (FileNotFoundError, ImportError):
            pass
        sizes.append(len(sys.path))
    check(sizes[0] == sizes[1] == sizes[2], f'sys.path does not grow with each level1943.load (lengths {sizes})')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
