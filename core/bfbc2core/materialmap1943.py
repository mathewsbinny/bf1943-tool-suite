"""A BF1943 level's terrain material map (TerrainMaterialMap .res, big-endian): the ground material under every
point of the terrain, 4096 x 4096 pixels over the height field's extent (0.5 m per pixel on the MP levels).

    mm = for_level(game, 'wake_island_s', heightfield)   # from the level's own bundles (game1943.Game)
    mm.names                                            # material names, by map index (the file's name table)
    mm.at(x, z)                                         # map indices at world points (arrays); -1 off the map
    mm.name_at(x, z)                                    # names

Format (operation-rising-sun research/GROUND_MATERIALS.md; decoded exactly on all four MP levels): u32 side, u16
tile count; then per tile (512 x 256 pixels, 8 across, row-major) u32 body size, the body, 256 u16 row lengths. A
row packs bytes as literals, or a pair of equal bytes followed by a count byte for count + 1 copies; each byte holds
two 4-bit indices, the low nibble the left pixel. Last, u32 size and NUL-separated names. Pixel (z, x) centres lie at
the height field's minimum + (index + 0.5) x pixel size, the height field's orientation.
"""
import struct
from dataclasses import dataclass
from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from .game1943 import Game
    from .terrain import Heightfield


@dataclass
class MaterialMap:
    names: list[str]
    grid: npt.NDArray[np.uint8]  # (side, side) uint8 map index, [z, x]
    min_x: float = 0.0
    min_z: float = 0.0
    pixel: float = 1.0

    def at(self, x: npt.ArrayLike, z: npt.ArrayLike) -> npt.NDArray[np.int_]:
        side = self.grid.shape[0]
        i = np.floor((np.asarray(x, float) - self.min_x) / self.pixel).astype(int)
        j = np.floor((np.asarray(z, float) - self.min_z) / self.pixel).astype(int)
        ok = (i >= 0) & (i < side) & (j >= 0) & (j < side)
        out = np.full(np.shape(i), -1, int)
        out[ok] = self.grid[j[ok], i[ok]]
        return out

    def name_at(self, x: npt.ArrayLike, z: npt.ArrayLike) -> list[str | None]:
        k = np.atleast_1d(self.at(x, z))
        return [self.names[v] if 0 <= v < len(self.names) else None for v in k]


def read(data: bytes) -> MaterialMap:
    """MaterialMap(names, grid) from the .res bytes (no placement: see place). ValueError for a file that is not a
    whole material map."""
    if len(data) < 6:                   # 2026-10-31 fuzzing: a struct.error before
        raise ValueError(f'not a TerrainMaterialMap: {len(data)} bytes, shorter than its 6-byte header')
    side = struct.unpack_from('>I', data, 0)[0]
    tiles = struct.unpack_from('>H', data, 4)[0]
    tile_w_bytes, tile_rows = 256, 256
    tile_w = 2 * tile_w_bytes
    across = side // tile_w
    # at least one tile (a side of 0 read as an empty map, and place() then divided by zero), and every tile's size
    # word and row lengths inside the file before the map is made (2026-10-31 review: the count is a u16, so a few
    # damaged bytes could ask for a map of up to 65,535 tiles of 512 x 256 pixels, 8 GiB)
    if not side or across * tile_w != side or tiles != across * (side // tile_rows):
        raise ValueError(f'not a TerrainMaterialMap: side {side}, {tiles} tiles')
    if 6 + tiles * (4 + 512) + 4 > len(data):
        raise ValueError(f'not a TerrainMaterialMap: {tiles} tiles cannot fit in {len(data)} bytes')
    grid = np.zeros((side, side), np.uint8)
    p = 6
    for t in range(tiles):
        if p + 4 > len(data):
            raise ValueError(f'tile {t}: the file ends at {len(data)} bytes, before the tile')
        n = struct.unpack_from('>I', data, p)[0]
        if p + 4 + n + 512 > len(data):
            raise ValueError(f'tile {t}: {n} bytes of body and its row lengths run past the end of the file')
        body = data[p + 4:p + 4 + n]
        p += 4 + n
        lens = struct.unpack_from('>256H', data, p)
        p += 512
        if sum(lens) != n:
            raise ValueError(f'tile {t}: row lengths sum to {sum(lens)}, the body is {n} bytes')
        rows: list[bytes]
        rows, q = [], 0
        for ln in lens:
            row = body[q:q + ln]
            q += ln
            dec = bytearray()
            i = 0
            while i < len(row):
                b = row[i]
                if i + 1 < len(row) and row[i + 1] == b:
                    if i + 2 >= len(row):           # 2026-10-30 second review: an IndexError before
                        raise ValueError(f'tile {t}: a row ends inside a run (a pair with no count byte)')
                    dec += bytes([b]) * (row[i + 2] + 1)
                    i += 3
                else:
                    dec.append(b)
                    i += 1
            if len(dec) != tile_w_bytes:
                raise ValueError(f'tile {t}: a row decodes to {len(dec)} bytes, not {tile_w_bytes}')
            rows.append(bytes(dec))
        raw = np.frombuffer(b''.join(rows), np.uint8).reshape(tile_rows, tile_w_bytes)
        ty, tx = divmod(t, across)
        px = grid[ty * tile_rows:(ty + 1) * tile_rows, tx * tile_w:(tx + 1) * tile_w]
        px[:, 0::2] = raw & 0x0F
        px[:, 1::2] = raw >> 4
    if p + 4 > len(data):
        raise ValueError('the file ends before the name table')
    nbytes = struct.unpack_from('>I', data, p)[0]
    if p + 4 + nbytes != len(data):
        raise ValueError(f'{len(data) - p - 4 - nbytes} bytes after the name table')
    table = data[p + 4:p + 4 + nbytes]
    # every name ends with a zero byte, and every material the map uses has one (2026-10-30 second review: a last name
    # without its zero byte was dropped, and lookups of it gave None)
    if table and not table.endswith(b'\0'):
        last = table.rsplit(b'\0', 1)[-1][:40]
        raise ValueError(f'the name table\'s last name ({last!r}) does not end with a zero byte')
    try:
        names = [n.decode('ascii') for n in table.split(b'\0')[:-1]]
    except UnicodeDecodeError as e:     # a ValueError already; said in words
        raise ValueError(f'the name table holds a byte that is not ASCII ({e.object[e.start]:#x})') from None
    used = int(grid.max()) if grid.size else -1
    if used >= len(names):
        raise ValueError(f'the map uses material {used}, and the name table names {len(names)}')
    return MaterialMap(names, grid)


def place(mm: MaterialMap, heightfield: 'Heightfield') -> MaterialMap:
    """The map laid over the height field's extent."""
    mm.min_x, mm.min_z = float(heightfield.min[0]), float(heightfield.min[2])
    mm.pixel = float(heightfield.max[0] - heightfield.min[0]) / mm.grid.shape[0]
    return mm


def resource_name(game_index: Iterable[str], level: str) -> str | None:
    """The level's material map in the bundle index: terrains/<level>/<level>-materialmap.res, or on Coral Sea
    terrains/coral_sea/terrain.terrainmaterialmapasset.res. None when the level has none."""
    pre = f'terrains/{level}/'
    names = [n for n in game_index if n.startswith(pre) and n.endswith('.res') and 'materialmap' in n.lower()
             and '/textures/' not in n]
    return sorted(names)[0] if names else None


def for_level(game: 'Game', level: str, heightfield: 'Heightfield') -> MaterialMap | None:
    """The level's material map placed over its height field, from a game1943.Game (None when it has none)."""
    name = resource_name(game.names(), level)
    if name is None:
        return None
    return place(read(cast(bytes, game.data(name))), heightfield)     # a name from game.names(): its data is there
