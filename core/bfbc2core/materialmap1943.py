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
from typing import List

import numpy as np


@dataclass
class MaterialMap:
    names: List[str]
    grid: np.ndarray            # (side, side) uint8 map index, [z, x]
    min_x: float = 0.0
    min_z: float = 0.0
    pixel: float = 1.0

    def at(self, x, z):
        side = self.grid.shape[0]
        i = np.floor((np.asarray(x, float) - self.min_x) / self.pixel).astype(int)
        j = np.floor((np.asarray(z, float) - self.min_z) / self.pixel).astype(int)
        ok = (i >= 0) & (i < side) & (j >= 0) & (j < side)
        out = np.full(np.shape(i), -1, int)
        out[ok] = self.grid[j[ok], i[ok]]
        return out

    def name_at(self, x, z):
        k = np.atleast_1d(self.at(x, z))
        return [self.names[v] if 0 <= v < len(self.names) else None for v in k]


def read(data):
    """MaterialMap(names, grid) from the .res bytes (no placement: see place)."""
    side = struct.unpack_from('>I', data, 0)[0]
    tiles = struct.unpack_from('>H', data, 4)[0]
    tile_w_bytes, tile_rows = 256, 256
    tile_w = 2 * tile_w_bytes
    across = side // tile_w
    if across * tile_w != side or tiles != across * (side // tile_rows):
        raise ValueError(f'not a TerrainMaterialMap: side {side}, {tiles} tiles')
    grid = np.zeros((side, side), np.uint8)
    p = 6
    for t in range(tiles):
        n = struct.unpack_from('>I', data, p)[0]
        body = data[p + 4:p + 4 + n]
        p += 4 + n
        lens = struct.unpack_from('>256H', data, p)
        p += 512
        if sum(lens) != n:
            raise ValueError(f'tile {t}: row lengths sum to {sum(lens)}, the body is {n} bytes')
        rows, q = [], 0
        for ln in lens:
            row = body[q:q + ln]
            q += ln
            dec = bytearray()
            i = 0
            while i < len(row):
                b = row[i]
                if i + 1 < len(row) and row[i + 1] == b:
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
    nbytes = struct.unpack_from('>I', data, p)[0]
    names = [n.decode('ascii') for n in data[p + 4:p + 4 + nbytes].split(b'\0')[:-1]]
    if p + 4 + nbytes != len(data):
        raise ValueError(f'{len(data) - p - 4 - nbytes} bytes after the name table')
    return MaterialMap(names, grid)


def place(mm, heightfield):
    """The map laid over the height field's extent."""
    mm.min_x, mm.min_z = float(heightfield.min[0]), float(heightfield.min[2])
    mm.pixel = float(heightfield.max[0] - heightfield.min[0]) / mm.grid.shape[0]
    return mm


def resource_name(game_index, level):
    """The level's material map in the bundle index: terrains/<level>/<level>-materialmap.res, or on Coral Sea
    terrains/coral_sea/terrain.terrainmaterialmapasset.res. None when the level has none."""
    pre = f'terrains/{level}/'
    names = [n for n in game_index if n.startswith(pre) and n.endswith('.res') and 'materialmap' in n.lower()
             and '/textures/' not in n]
    return sorted(names)[0] if names else None


def for_level(game, level, heightfield):
    """The level's material map placed over its height field, from a game1943.Game (None when it has none)."""
    name = resource_name(game.names(), level)
    if name is None:
        return None
    return place(read(game.data(name)), heightfield)
