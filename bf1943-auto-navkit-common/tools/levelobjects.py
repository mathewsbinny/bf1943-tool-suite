"""NavKit: object placements and pathfinding flags of a level, from its extracted data.

Reads an unpacked + XML-converted level (FbrbTool unpack of Dist and Package level-00.fbrb, then
DbxEdit xml on both folders) and writes, next to it:
  objects.json       individually placed objects (Entity.ReferenceObjectData): blueprint, flags, matrix
  group_instances.json  instances of static-model groups / forest / middlegrowth (Physics.CompositeHavokAsset:
                     Assets[i] + Transforms[i]), with blueprint flags
  forbid_tris.npy    world-space collision triangles (N x 3 x 3) of every placed object whose blueprint has
                     ForbidPathfinding = true (from the blueprint's <name>_Havok.res packfile)
  stair_tris.npy     collision of staircases and ramps (blueprint names with stair / ramp / steps): floor that
                     does not block its own users' clearance
  dynamic_tris.npy   the same for dynamic (movable / destructible) objects - DynamicModelEntityData blueprints
                     such as crates and barrels - for objects with DisablePathfinding = PdtWhenAlive (doors), and
                     for barrier arms (a forbidden object that is one thin raised bar): path data treats them as absent

Usage: levelobjects.py <level dir> <level name> [extra blueprint dirs ...]
  e.g. levelobjects.py work\\levels\\sp_01 SP_01 work\\audit\\sp_common work\\audit\\mp_common
"""
import glob
import json
import os
import re
import struct
import sys
from collections.abc import Iterable
from typing import Any

import numpy as np
import numpy.typing as npt

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
import havok461  # noqa: E402
import hkpack  # noqa: E402
from bfbc2core import xmlinst  # noqa: E402

SEP = chr(92)

# a blueprint's flags (blueprint_flags): ForbidPathfinding, ExcludeFromPathfinding, AllowPathdataSeeds, dynamic,
# DisablePathfinding value, NeedsDetailedPathfinding
type Flags = tuple[bool, bool, bool, bool, str, bool]


def order_points(level_xml: str) -> list[list[float]]:
    """AI order positions of a level: the Transform translation of every AreaGeometryEntityData (the points the
    squad's area / patrol orders send soldiers to; sp_004_b's stairs-top go-to targets are two of them)."""
    points: list[list[float]] = []
    for _typ, _guid, body in xmlinst.instances(level_xml, 'Entity.AreaGeometryEntityData'):
        transform = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        if transform:
            matrix = [float(value) for value in transform.group(1).split('/')]
            if len(matrix) >= 15 and any(abs(coord) > 1e-3 for coord in matrix[12:15]):
                points.append(matrix[12:15])
    return points


def blueprint_flags(bases: Iterable[str]) -> dict[str, Flags]:
    """lower-case blueprint path -> (ForbidPathfinding, ExcludeFromPathfinding, AllowPathdataSeeds, dynamic,
    DisablePathfinding value - e.g. PdtNever / PdtWhenAlive, NeedsDetailedPathfinding). The last (FLAG_DETAILED) is
    what DICE's detailed zones follow (detailed.py; added 2026-10-31 - objects.json from before has five)."""
    flags: dict[str, Flags] = {}
    for base in bases:
        for path in glob.glob(os.path.join(base, '**', '*.xml'), recursive=True):
            text = open(path, encoding='utf-8', errors='replace').read()
            dynamic = 'type="GameSharedResources.DynamicModelEntityData"' in text
            if 'ForbidPathfinding' not in text and 'NeedsDetailedPathfinding' not in text and not dynamic:
                continue
            rel = os.path.relpath(path, base).replace(SEP, '/').lower()[:-4]
            disable = re.search(r'<field name="DisablePathfinding">([A-Za-z]*)<', text)
            flags.setdefault(rel, ('<field name="ForbidPathfinding">true' in text,
                                   '<field name="ExcludeFromPathfinding">true' in text,
                                   '<field name="AllowPathdataSeeds">true' in text, dynamic,
                                   disable.group(1) if disable else '',
                                   '<field name="NeedsDetailedPathfinding">true' in text))
    return flags


FLAG_DETAILED = 5            # index of NeedsDetailedPathfinding in an object's flags (blueprint_flags)


def mesh_aliases(bases: Iterable[str]) -> dict[str, str]:
    """lower-case blueprint path -> blueprint path of its Mesh reference (without _mesh), for blueprints that
    reuse another object's mesh and collision (InvWall_SP03 -> invisiblewall/invwall)."""
    out: dict[str, str] = {}
    for base in bases:
        for path in glob.glob(os.path.join(base, '**', '*.xml'), recursive=True):
            text = open(path, encoding='utf-8', errors='replace').read()
            ref = xmlinst.field_ref(text, 'Mesh')
            if not ref or ref == 'null':
                continue
            rel = os.path.relpath(path, base).replace(SEP, '/').lower()[:-4]
            mesh = ref.rsplit('/', 1)[0].lower()
            out.setdefault(rel, re.sub(r'_mesh$', '', mesh))
    return out


def havok_index(bases: Iterable[str]) -> dict[str, str]:
    """lower-case blueprint path -> its collision packfile (<name>_Havok.res; the first base folder that has one)."""
    out: dict[str, str] = {}
    for base in bases:
        for path in glob.glob(os.path.join(base, '**', '*_havok.res'), recursive=True):
            rel = os.path.relpath(path, base).replace(SEP, '/').lower()[:-len('_havok.res')]
            out.setdefault(rel, path)
            # some assets name their packfile <name>_Havok_Havok.res (sp_009's animated aircraft door,
            # PdtWhenAlive: without its collision the closed door stayed solid and cut the plane's cabins apart;
            # inv_Wall): the blueprint is <name>
            while rel.endswith('_havok'):
                rel = rel[:-len('_havok')]
                out.setdefault(rel, path)
    return out


def triangles16(words: npt.ArrayLike) -> npt.NDArray[np.integer]:
    """Triangles of 16-bit indices stored in groups of four (three corners + padding); a last triangle stored without
    its padding word (a count of 4n + 3) is kept."""
    words = np.asarray(words)
    full = len(words) // 4 * 4
    tris = words[:full].reshape(-1, 4)[:, :3]
    if len(words) - full == 3:
        tris = np.concatenate([tris, words[full:].reshape(1, 3)])
    return tris


def triangles(path: str) -> npt.NDArray[np.float32] | None:
    """Collision triangles (local space) of a blueprint's Havok asset, or None: storage extended mesh subparts and
    convex shapes' boxes. Havok 5.5 (BFBC2, hkp* classes, described in the file) and Havok 4.6.1 (BF1943, hk* classes,
    layouts from havok461.py), in the file's own byte order."""
    try:
        pf = hkpack.Packfile(open(path, 'rb').read(), classes=havok461.CLASSES)
        return triangles_of(pf)
    except (ValueError, struct.error, IndexError):
        return None


def triangles_of(pf: hkpack.Packfile) -> npt.NDArray[np.float32] | None:
    """triangles() of a loaded packfile."""
    if pf.contents_version.startswith('Havok-4.'):
        tris: list[npt.NDArray[Any]] = []
        for off in pf.objects_of('hkStorageExtendedMeshShapeMeshSubpartStorage'):
            vertex_list, index_list = havok461.triangles(pf, off)
            if vertex_list and index_list:
                vertices, indices = np.asarray(vertex_list, np.float32), np.asarray(index_list, np.int64)
                if indices.max() < len(vertices):
                    tris.append(vertices[indices])
        for off in pf.objects_of('hkConvexVerticesShape'):
            box, _half, _centre = havok461.convex_box(pf, off)
            if box:
                tris.append(np.asarray(box, np.float32))
        return np.concatenate(tris).astype(np.float32) if tris else None
    endian = pf.e                   # the struct byte-order prefix, '<' or '>'
    tris = []
    for off in pf.objects_of('hkpStorageExtendedMeshShapeMeshSubpartStorage'):
        vertices_at = pf.deref(off + 8)
        vertex_count = struct.unpack_from(endian + 'i', pf.d, off + 12)[0]
        indices_at = pf.deref(off + 0x14)
        index_count = struct.unpack_from(endian + 'i', pf.d, off + 0x18)[0]
        if not (vertices_at and vertex_count > 0 and indices_at and index_count > 0):
            continue
        vertices = np.frombuffer(pf.d, dtype=endian + 'f4', count=vertex_count * 4,
                                 offset=vertices_at).reshape(-1, 4)[:, :3]
        indices = triangles16(np.frombuffer(pf.d, dtype=endian + 'u2', count=index_count, offset=indices_at))
        if len(indices) and indices.max() < vertex_count:
            tris.append(vertices[indices])
    # convex shapes (boxes, rails, doors): the 12 triangles of each shape's bounding box. Vertices are stored as
    # FourVectors (x0..3, y0..3, z0..3); the first array member of a plausible size holds them.
    for off in pf.objects_of('hkpConvexVerticesShape'):
        for k in range(0x10, 0x60, 4):
            vectors_at = pf.deref(off + k)
            if not vectors_at:
                continue
            count = struct.unpack_from(endian + 'i', pf.d, off + k + 4)[0]
            if 0 < count < 10000:
                four_vectors = np.frombuffer(pf.d, dtype=endian + 'f4', count=count * 12,
                                             offset=vectors_at).reshape(count, 3, 4)
                points = np.stack([four_vectors[:, 0, :].ravel(), four_vectors[:, 1, :].ravel(),
                                   four_vectors[:, 2, :].ravel()], 1)
                lo, hi = points.min(0), points.max(0)
                corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                                    for z in (lo[2], hi[2])])
                faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
                         (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
                tris.append(corners[np.array(faces)])
                break
    return np.concatenate(tris).astype(np.float32) if tris else None


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    level_dir, level = argv[1], argv[2]
    bases = [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + argv[3:]
    flags = blueprint_flags(bases)
    level_xml = open(os.path.join(level_dir, 'dist', 'levels', level + '.xml'), encoding='utf-8',
                     errors='replace').read()
    # placed objects and group instances: {'bp': blueprint, 'flags': its flags, 'm': its 4 x 4 matrix, row by row}
    objs: list[dict[str, Any]] = []
    for _typ, _guid, body in xmlinst.instances(level_xml, 'Entity.ReferenceObjectData'):
        transform = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        ref = xmlinst.field_ref(body, 'ReferencedObject')
        if transform and ref is not None:
            blueprint = ref.rsplit('/', 1)[0].lower()
            objs.append({'bp': blueprint, 'flags': flags.get(blueprint),
                         'm': [float(value) for value in transform.group(1).split('/')]})
    groups: list[dict[str, Any]] = []
    for path in glob.glob(os.path.join(level_dir, 'dist', 'levels', level, '*_HavokAsset.xml')):
        text = open(path, encoding='utf-8').read()
        transforms = re.search(r'<array name="Transforms">([^<]*)<', text)
        if not transforms:
            continue
        transforms = transforms.group(1).split('/')
        assets = re.findall(r'<item ref="([^"]*)" />', text.split('<array name="Assets">')[1].split('</array>')[0])
        for i, asset in enumerate(assets):
            blueprint = re.sub(r'_havok$', '', asset.rsplit('/', 1)[0].lower())
            groups.append({'group': os.path.basename(path), 'bp': blueprint, 'flags': flags.get(blueprint),
                           'm': [float(value) for value in transforms[i * 16:(i + 1) * 16]]})
    # soldier spawn points (seed points of the path data)
    spawns: list[list[float]] = []
    for _typ, _guid, body in xmlinst.instances(level_xml, '.SoldierSpawnEntityData'):
        transform = re.search(r'<complex name="Transform">([^<]*)</complex>', body)
        if transform:
            matrix = [float(value) for value in transform.group(1).split('/')]
            if any(abs(coord) > 1e-3 for coord in matrix[12:15]):
                spawns.append(matrix[12:15])
    for name, value in (('soldier_spawns.json', spawns), ('order_points.json', order_points(level_xml)),
                        ('objects.json', objs), ('group_instances.json', groups)):
        with open(os.path.join(level_dir, name), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(value, f)

    havok = havok_index(bases)
    # blueprints without their own collision asset use the one of the object their Mesh reference names
    for blueprint, target in mesh_aliases(bases).items():
        if blueprint not in havok and target in havok:
            havok[blueprint] = havok[target]
    cache, missing = {}, set()

    def tris_of(obj: dict[str, Any]) -> npt.NDArray[np.float32] | None:
        """An object's collision triangles in world space, or None (no collision asset)."""
        if obj['bp'] not in cache:
            cache[obj['bp']] = triangles(havok[obj['bp']]) if obj['bp'] in havok else None
        local = cache[obj['bp']]
        if local is None:
            missing.add(obj['bp'])
            return None
        matrix = np.array(obj['m']).reshape(4, 4)        # rows: right, up, forward, translation
        return (local @ matrix[:3, :3] + matrix[3, :3]).astype(np.float32)

    def barrier_arm(obj: dict[str, Any]) -> bool | np.bool:
        """Whole collision is one thin raised bar (boom barrier): >= 0.7 m above the object's origin, <= 0.5 m
        thick, >= 2 m long. Path data treats it as passable (DICE's routes cross closed checkpoint poles)."""
        if obj['bp'] not in havok:
            return False
        if obj['bp'] not in cache:
            cache[obj['bp']] = triangles(havok[obj['bp']])
        local = cache[obj['bp']]
        if local is None:
            return False
        points = local.reshape(-1, 3)
        lo, hi = points.min(0), points.max(0)
        return lo[1] >= 0.7 and hi[1] - lo[1] <= 0.5 and max(hi[0] - lo[0], hi[2] - lo[2]) >= 2.0

    def ignored(obj: dict[str, Any]) -> bool | np.bool:
        """Dynamic objects, objects with DisablePathfinding = PdtWhenAlive (doors ...) and barrier arms: path data
        ignores them."""
        flags_of = obj['flags']
        return ((len(flags_of) > 3 and flags_of[3]) or (len(flags_of) > 4 and flags_of[4] == 'PdtWhenAlive')
                or (flags_of[0] and barrier_arm(obj)))
    dynamic = [tris for tris in (tris_of(obj) for obj in objs + groups if obj['flags'] and ignored(obj))
               if tris is not None]
    # staircases and ramps (DICE blueprint names): their own collision is floor, not an obstacle to clearance
    stair_like = re.compile(r'stair|ramp|steps', re.I)
    stairs = [tris for tris in (tris_of(obj) for obj in objs + groups
                                if stair_like.search(obj['bp'].rsplit('/', 1)[-1]))
              if tris is not None]
    np.save(os.path.join(level_dir, 'stair_tris.npy'), np.concatenate(stairs) if stairs else np.zeros((0, 3, 3)))
    print(f'{sum(len(tris) for tris in stairs)} stair triangles from {len(stairs)} staircases / ramps')
    print('barrier arms:', sorted({obj['bp'].split('/')[-1] for obj in objs + groups
                                   if obj['flags'] and obj['flags'][0] and barrier_arm(obj)}))
    np.save(os.path.join(level_dir, 'dynamic_tris.npy'), np.concatenate(dynamic) if dynamic else np.zeros((0, 3, 3)))
    print(f'{sum(len(tris) for tris in dynamic)} dynamic-object triangles from {len(dynamic)} instances')
    # objects excluded from path finding (fences, sandbags, palm trees): their collision does not block clearance
    # (added to the dynamic set); only their footprint at ground level - parts reaching within 0.5 m of the
    # object's origin height - stays a barrier (exclude_footprint_tris.npy) instead of the whole object
    excluded = [obj for obj in objs + groups if obj['flags'] and obj['flags'][1]]
    excluded_tris, footprint_tris = [], []
    for obj in excluded:
        tris = tris_of(obj)
        if tris is None:
            continue
        excluded_tris.append(tris)
        base = obj['m'][13]
        footprint_tris.append(tris[tris[:, :, 1].min(1) <= base + 0.5])
    np.save(os.path.join(level_dir, 'exclude_tris.npy'),
            np.concatenate(excluded_tris) if excluded_tris else np.zeros((0, 3, 3)))
    np.save(os.path.join(level_dir, 'exclude_footprint_tris.npy'),
            np.concatenate(footprint_tris) if footprint_tris else np.zeros((0, 3, 3)))
    print(f'{len(excluded_tris)} excluded objects: {sum(len(tris) for tris in excluded_tris)} triangles, '
          f'footprint {sum(len(tris) for tris in footprint_tris)}')
    world = []
    for obj in objs + groups:
        if not (obj['flags'] and obj['flags'][0]) or obj['flags'][1]:
            continue
        tris = tris_of(obj)
        if tris is not None:
            world.append(tris)
    np.save(os.path.join(level_dir, 'forbid_tris.npy'), np.concatenate(world) if world else np.zeros((0, 3, 3)))
    print(f'{len(objs)} placed objects, {len(groups)} group instances, {len(flags)} blueprints with flags; '
          f'{sum(len(tris) for tris in world)} forbid triangles from {len(world)} instances; '
          f'no collision: {sorted(missing)}')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('levelobjects', gui=False)       # a log; an unexpected error explained in words (exit code 1)
    with errors.guard():
        sys.exit(main(sys.argv))
