"""BC2 water: Terrains/<Terrain>/<Terrain>.Water.Mesh.res (type WaterMesh) and .Water.res (type VisualWater), in each
level's terrain-00.fbrb.

    w = read(folder_or_mesh_path, name)    # Water: vertices, triangles, per-triangle shader (or None)
    w.vertices (n, 3) float32, w.triangles (m, 3) uint32, w.shader[k] -> shader path of triangle k

WaterMesh (little-endian on BFBC2 PC, read on all 36 levels with a terrain bundle, each file consumed exactly;
big-endian on BF1943 Xbox 360, detected from the body count):

    u32           bodies (1 in every level)
    per body:     u32 vertex count, vertex count x (f32 x, y, z)
                  u32 index count, index count x u32 (triangles; 0 vertices = no water: SP_006, SP_009)

Heights are per vertex: rivers slope (SP_02: 17.9-156.8 m), seas are flat (SP_01: 4.4 m).

VisualWater (the drawing copy; only the parts used here):

    u32 10, u32 vertex stride (36), u32 vertex bytes, vertices (half-float x, y, z first, then normal and texture
    coordinates), u32 index bytes, u32 indices (identical to the mesh's on 33 of 34 levels with water),
    u32 slot count (4) and the slots' shader paths (0-terminated, each followed by one 0 byte, then
    Systems/Water/WaterOceanFragment), u32 range count and per range (u32 slot, u32 triangle count,
    u32 first index, u32 index count), then u32 0 and the byte 0x46.
"""
import os
import struct
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class Water:
    vertices: np.ndarray                  # (n, 3) float32
    triangles: np.ndarray                 # (m, 3) uint32
    shader: Optional[List[str]] = None    # per triangle, from the VisualWater ranges; None if they do not match
    shaders: Optional[List[str]] = None   # the VisualWater slots

    def __len__(self):
        return len(self.triangles)


def _u32(d, e, o, path, what):
    """The u32 at o; ValueError when the file ends before it (2026-10-31 fuzzing: a struct.error before)."""
    if o + 4 > len(d):
        raise ValueError(f'{path}: the file ends at {len(d)} bytes, before {what} at {o} - a damaged or cut file')
    return struct.unpack_from(e + 'I', d, o)[0]


def read_mesh(path):
    """(vertices, triangles) of every body in a .Water.Mesh.res, bodies joined. ValueError for a file that is not a
    whole WaterMesh: cut short, a triangle past its body's vertices, a vertex that is not a finite number."""
    d = open(path, 'rb').read()
    e = '<' if _u32(d, '<', 0, path, 'the body count') < 0x10000 else '>'    # a handful of bodies, either byte order
    bodies = _u32(d, e, 0, path, 'the body count')
    o, verts, tris = 4, [], []
    base = 0
    for b in range(bodies):
        n = _u32(d, e, o, path, f'body {b}\'s vertex count')
        if o + 4 + 12 * n + 4 > len(d):
            raise ValueError(f'{path}: body {b}\'s {n} vertices run past the end of the file - a damaged or cut file')
        v = np.frombuffer(d, e + 'f4', n * 3, o + 4).reshape(n, 3)
        if not np.isfinite(v).all():    # 2026-10-31 review: NaN vertices were read and given to NavKit's water rule
            raise ValueError(f'{path}: body {b} has a vertex that is not a finite number - a damaged WaterMesh')
        o += 4 + 12 * n
        m = _u32(d, e, o, path, f'body {b}\'s index count')
        if m % 3:
            raise ValueError(f'{path}: a body of {m} indices, not whole triangles - not the WaterMesh layout')
        if o + 4 + 4 * m > len(d):
            raise ValueError(f'{path}: body {b}\'s {m} indices run past the end of the file - a damaged or cut file')
        i = np.frombuffer(d, e + 'u4', m, o + 4).reshape(-1, 3)
        if m and int(i.max()) >= n:
            # 2026-10-30: read, then an IndexError far away (NavKit's water rule, the viewers)
            raise ValueError(f'{path}: a triangle names vertex {int(i.max())} of a body of {n} - a damaged WaterMesh')
        o += 4 + 4 * m
        verts.append(v)
        tris.append(i.astype(np.uint32) + base)
        base += n
    if o != len(d):
        raise ValueError(f'{path}: {len(d) - o} unread bytes - not the WaterMesh layout')
    return (np.concatenate(verts).astype(np.float32) if verts else np.zeros((0, 3), np.float32),
            np.concatenate(tris) if tris else np.zeros((0, 3), np.uint32))


def read_visual(path):
    """(indices, slot shader paths, ranges [(slot, triangles, first index, index count)]) of a .Water.res. ValueError
    for a file whose parts run past its end (2026-10-31 fuzzing: a struct.error before)."""
    d = open(path, 'rb').read()
    size = _u32(d, '<', 8, path, 'the vertex byte count')
    o = 12 + size
    ib = _u32(d, '<', o, path, 'the index byte count')
    if o + 4 + ib > len(d):
        raise ValueError(f'{path}: {ib} index bytes run past the end of the file - a damaged or cut VisualWater')
    indices = np.frombuffer(d, '<u4', ib // 4, o + 4)
    o += 4 + ib
    slots = _u32(d, '<', o, path, 'the slot count')
    o += 4
    names = []
    while len(names) < slots + 1:              # the slots, then the fragment shader (each name at least its zero byte)
        e = d.find(b'\0', o)
        if e < 0:
            raise ValueError(f'{path}: shader name {len(names)} at {o} does not end inside the file - a damaged '
                             f'VisualWater')
        names.append(d[o:e].decode('ascii', 'replace'))
        o = e + 1
        if len(names) <= slots and d[o:o + 1] == b'\0':
            o += 1
    count = _u32(d, '<', o, path, 'the range count')
    if o + 4 + 16 * count > len(d):
        raise ValueError(f'{path}: {count} ranges run past the end of the file - a damaged or cut VisualWater')
    ranges = [struct.unpack_from('<4I', d, o + 4 + 16 * k) for k in range(count)]
    return indices, names[:slots], ranges


def read(folder, name):
    """The water of terrain <name> under an unpacked terrain-00.fbrb (None if it has no water files)."""
    mesh = visual = None
    for root, _, files in os.walk(folder):
        for f in files:
            low = f.lower()
            if low == f'{name}.water.mesh.res':
                mesh = os.path.join(root, f)
            elif low == f'{name}.water.res':
                visual = os.path.join(root, f)
    if mesh is None:
        return None
    v, t = read_mesh(mesh)
    w = Water(v, t)
    if visual is not None and len(t):
        try:
            indices, slots, ranges = read_visual(visual)
            if np.array_equal(indices, t.ravel()):
                shader = [''] * len(t)
                for slot, ntri, first, _nidx in ranges:
                    # the triangles the file has, however many a range names (2026-10-31 fuzzing: a range of 2^31
                    # triangles was walked one by one, minutes, to set none)
                    for k in range(first // 3, min(first // 3 + ntri, len(shader))):
                        shader[k] = slots[slot] if slot < len(slots) else f'slot {slot}'
                w.shader, w.shaders = shader, slots
        except (struct.error, ValueError, IndexError):
            pass
    return w
