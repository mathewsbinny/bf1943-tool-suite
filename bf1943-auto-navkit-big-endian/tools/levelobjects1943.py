"""NavKit (big-endian): object placements and collision of a BF1943 level, read straight from its bundles.

The BF1943 counterpart of levelobjects.py, which reads an unpacked, XML-converted BFBC2 level. Here the level's
binary DBX partitions and Havok 4.6.1 packfiles are read from the game's own .fbrb bundles (the level's folder
and levels/levels-common-00.fbrb). It writes the same files as levelobjects.py, so the navbuild package takes them
unchanged, plus all_tris.npy (every placed object's collision, for sampling without the game):

  objects.json              individually placed objects (Entity.ReferenceObjectData): blueprint, flags, matrix
  group_instances.json      static-model / middlegrowth / rigid-model group members (Physics.CompositeHavokAsset:
                            Assets[i] + Transforms[i])
  forbid_tris.npy           world-space collision (N x 3 x 3) of objects whose blueprint has ForbidPathfinding
  stair_tris.npy, dynamic_tris.npy, exclude_tris.npy, exclude_footprint_tris.npy   as in levelobjects.py
  all_tris.npy              world-space collision of every placed object and group member that has any
  object_boxes.json         per object with collision: index (in objects.json + group_instances.json order of the
                            placed ones), blueprint, destructible, NeedsDetailedPathfinding, world bounds (min,
                            max) and footprint (m2)
  soldier_spawns.json, order_points.json   spawn positions and AreaGeometryEntityData points
  ladders.json              the placed ladders (blueprints with a LadderEntityData: a soldier who walks into one
                            climbs it), as ladders.py writes them: blueprint and the world box of its collision
                            (foot = bottom, top = top), for navbuild's ladder links. A ladder whose bottom is buried
                            (Wake's guard towers stand 3.9 m into the ground) has its foot raised to the terrain
                            there; 'box_lo_y' keeps the collision's own bottom

Usage: levelobjects1943.py <dist\\xenon folder> <level> <out dir>
  e.g. levelobjects1943.py ..\\..\\bf1943-xenia-kit\\output\\title\\dist\\xenon wake_island_s work\\wake_island_s

Collision comes from hkStorageExtendedMeshShapeMeshSubpartStorage meshes and hkConvexVerticesShape boxes (their
vertex bounding box, as levelobjects.py does) (havok461.py); hkBoxShape and hkConvexTranslateShape are not read yet
and are reported.
"""
import json
import os
import re
import sys
from typing import Any, cast

import numpy as np
import numpy.typing as npt

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import havok461  # noqa: E402
import hkpack  # noqa: E402
from bfbc2core import dbx  # noqa: E402
from bfbc2core import terrain  # noqa: E402


# the level's bundles and the shared level bundle, by entry name (core game1943.py)
from bfbc2core.game1943 import Game  # noqa: E402,F401

type Triangles = npt.NDArray[np.float32]        # (N, 3 corners, x y z)
# a blueprint's flags (blueprint_info): ForbidPathfinding, ExcludeFromPathfinding, AllowPathdataSeeds, dynamic,
# DisablePathfinding, NeedsDetailedPathfinding, destructible, ladder
type Flags = tuple[bool, bool, bool, bool, str, bool, bool, bool]
type Placement = dict[str, Any]                 # {'bp': blueprint, 'm': its 16 matrix floats, 'flags': Flags | None}


def kind(element: dbx.Element) -> str:
    """An instance's type without its namespace (Entity.ReferenceObjectData: ReferenceObjectData)."""
    return (element.attr('type') or '').rsplit('.', 1)[-1]


def matrix(element: dbx.Element, name: str = 'Transform') -> list[float] | None:
    """The 16 floats of an instance's 4 x 4 matrix field (rows: right, up, forward, translation), or None."""
    field = element.child(name)
    if field is None or field.kind != dbx.NUMBERS:
        return None
    values = dbx.floats(field)
    return values if len(values) == 16 else None


def blueprint_info(game: Game, bp: str) -> tuple[Flags | None, str | None]:
    """(flags, mesh alias) of a blueprint partition: flags as levelobjects.blueprint_flags gives them
    (ForbidPathfinding, ExcludeFromPathfinding, AllowPathdataSeeds, dynamic, DisablePathfinding), then
    NeedsDetailedPathfinding (the field DICE's generator reads to build a sector at pitch 0.2), then destructible (a
    PartComponentData with two or more HealthStates: intact, destroyed - sandbag walls, fences), then ladder (a
    LadderEntityData), or None."""
    partition = game.partition(bp)
    if partition is None:
        return None, None
    forbid = exclude = seeds = dynamic = detailed = ladder = False
    states = 0
    disable, mesh = '', None
    for inst in partition.instances:
        dynamic |= kind(inst) == 'DynamicModelEntityData'
        ladder |= kind(inst) == 'LadderEntityData'
        if kind(inst) == 'PartComponentData':
            health_states = inst.child('HealthStates')
            if health_states is not None:
                states = max(states, len(health_states.children or ()))
        for field in inst.children or ():
            field_name = field.attr('name')
            if field_name == 'ForbidPathfinding':
                forbid |= dbx.value(field) == 'true'
            elif field_name == 'ExcludeFromPathfinding':
                exclude |= dbx.value(field) == 'true'
            elif field_name == 'AllowPathdataSeeds':
                seeds |= dbx.value(field) == 'true'
            elif field_name == 'NeedsDetailedPathfinding':
                detailed |= dbx.value(field) == 'true'
            elif field_name == 'DisablePathfinding':
                disable = dbx.value(field)
            elif field_name == 'Mesh' and mesh is None:
                mesh_ref = _ref(field)
                if mesh_ref:
                    mesh = re.sub(r'_mesh$', '', mesh_ref.rsplit('/', 1)[0].lower())
    return (forbid, exclude, seeds, dynamic, disable, detailed, states >= 2, ladder), mesh


def _ref(element: dbx.Element | None) -> str | None:
    """A reference field's target, or None (no field, no reference, 'null')."""
    if element is None:
        return None
    ref = element.attr('ref')
    return ref if ref and ref != 'null' else None


def collision(game: Game, bp: str, cache: dict[str, Triangles | None],
              missing: dict[str, set[str]]) -> Triangles | None:
    """Local-space collision triangles (N x 3 x 3) of a blueprint's <name>_Havok.res, or None. cache: by blueprint;
    missing: {why: blueprints} for the report."""
    if bp in cache:
        return cache[bp]
    tris = None
    data = game.data(bp + '_havok.res') or game.data(bp + '_havok_havok.res')
    if data:
        packfile = hkpack.Packfile(data, classes=havok461.CLASSES)
        parts = []
        for offset in packfile.objects_of('hkStorageExtendedMeshShapeMeshSubpartStorage'):
            vertices, triangles = havok461.triangles(packfile, offset)
            if vertices and triangles:
                parts.append(np.asarray(vertices, np.float32)[np.asarray(triangles)])
        for offset in packfile.objects_of('hkConvexVerticesShape'):    # sandbags, crates, fences: their vertex box
            box, _, _ = havok461.convex_box(packfile, offset)
            if box:
                parts.append(np.asarray(box, np.float32))
        if packfile.objects_of('hkBoxShape') or packfile.objects_of('hkConvexTranslateShape'):
            missing.setdefault('box / translated convex shapes not read', set()).add(bp)
        tris = np.concatenate(parts) if parts else None
    cache[bp] = tris
    return tris


def world(tris: Triangles, transform: npt.ArrayLike) -> Triangles:
    """Local-space triangles placed by an instance's 16-float matrix."""
    m = np.asarray(transform, np.float64).reshape(4, 4)          # rows: right, up, forward, translation
    return (tris @ m[:3, :3] + m[3, :3]).astype(np.float32)


LADDER_MIN_HEIGHT = 1.5         # as ladders.py: a lower ladder box is a prop lying down or a fragment


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 1
    game_dir, level, out = argv[1:]
    os.makedirs(out, exist_ok=True)
    game = Game(game_dir, level)
    level_partition = game.partition(f'levels/{level}')
    if level_partition is None:
        raise FileNotFoundError(f'levels/{level}.dbx is not in the level bundles')
    objs: list[Placement] = []
    groups: list[Placement] = []
    spawns: list[list[float]] = []
    orders: list[list[float]] = []
    for element in level_partition.instances:
        element_kind = kind(element)
        transform = matrix(element)
        if element_kind == 'ReferenceObjectData':
            target = _ref(element.child('ReferencedObject'))
            if transform and target:
                objs.append({'bp': target.rsplit('/', 1)[0].lower(), 'm': transform})
        elif element_kind == 'SoldierSpawnEntityData' and transform and any(abs(c) > 1e-3 for c in transform[12:15]):
            spawns.append(transform[12:15])
        elif element_kind == 'AreaGeometryEntityData' and transform and any(abs(c) > 1e-3 for c in transform[12:15]):
            orders.append(transform[12:15])
    asset_names = sorted(name for name in game.entries
                         if name.startswith(f'levels/{level}/') and name.endswith('_havokasset.dbx'))
    for name in asset_names:
        asset_file = dbx.parse(cast(bytes, game.data(name)))      # a listed entry: it has data
        for element in asset_file.instances:
            if kind(element) != 'CompositeHavokAsset':
                continue
            transforms_field = element.child('Transforms')
            if transforms_field is not None and transforms_field.kind == dbx.NUMBERS:
                transforms = dbx.floats(transforms_field)
            else:
                transforms = []
            assets_field = element.child('Assets')
            assets = [c.attr('ref') for c in (assets_field.children or ())] if assets_field is not None else []
            for i, asset in enumerate(assets):
                if asset and len(transforms) >= 16 * (i + 1):
                    bp = re.sub(r'_havok$', '', asset.rsplit('/', 1)[0].lower())
                    groups.append({'group': os.path.basename(name), 'bp': bp, 'm': transforms[16 * i:16 * (i + 1)]})
    info: dict[str, tuple[Flags | None, str | None]] = {}       # blueprint: (flags, mesh alias)
    for placement in objs + groups:
        if placement['bp'] not in info:
            info[placement['bp']] = blueprint_info(game, placement['bp'])
        placement['flags'] = info[placement['bp']][0]
    cache: dict[str, Triangles | None] = {}
    missing: dict[str, set[str]] = {}

    def tris_of(placement: Placement) -> Triangles | None:
        """The placement's world-space collision, or None."""
        tris = collision(game, placement['bp'], cache, missing)
        alias = info[placement['bp']][1]
        if tris is None and alias and alias != placement['bp']:    # a blueprint reusing another object's collision
            tris = collision(game, alias, cache, missing)
        if tris is None:
            missing.setdefault('no collision', set()).add(placement['bp'])
            return None
        return world(tris, placement['m'])

    def save(name: str, parts: list[Triangles]) -> int:
        np.save(os.path.join(out, name), np.concatenate(parts) if parts else np.zeros((0, 3, 3), np.float32))
        return sum(len(p) for p in parts)

    with_collision = [(placement, tris_of(placement)) for placement in objs + groups]
    # (placement, its world-space collision) of every placement that has collision
    placed = [(placement, tris) for placement, tris in with_collision if tris is not None]

    def flag(placement: Placement, index: int) -> Any:
        """The placement's blueprint flag at index (Flags; falsy when the blueprint has none)."""
        flags = placement['flags']
        return bool(flags) and len(flags) > index and flags[index]

    def dynamic(placement: Placement) -> Any:
        """A dynamic prop, or one whose collision counts only while it stands (DisablePathfinding PdtWhenAlive)."""
        return flag(placement, 3) or (placement['flags'] and placement['flags'][4] == 'PdtWhenAlive')

    def footprint(placement: Placement, tris: Triangles) -> Triangles:
        """An excluded object's triangles reaching within 0.5 m of its origin's height."""
        return tris[tris[:, :, 1].min(1) <= placement['m'][13] + 0.5]
    stair_like = re.compile(r'stair|ramp|steps', re.I)
    counts = {
        'all': save('all_tris.npy', [tris for _, tris in placed]),
        'forbid': save('forbid_tris.npy', [tris for p, tris in placed if flag(p, 0) and not flag(p, 1)]),
        'dynamic': save('dynamic_tris.npy', [tris for p, tris in placed if dynamic(p)]),
        'stairs': save('stair_tris.npy',
                       [tris for p, tris in placed if stair_like.search(p['bp'].rsplit('/', 1)[-1])]),
        'exclude': save('exclude_tris.npy', [tris for p, tris in placed if flag(p, 1)]),
        'exclude_foot': save('exclude_footprint_tris.npy', [footprint(p, tris) for p, tris in placed if flag(p, 1)]),
    }

    def object_box(index: int, placement: Placement, tris: Triangles) -> dict[str, Any]:
        """object_boxes.json's record of a placement with collision."""
        lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
        return {'index': index, 'bp': placement['bp'],
                'destructible': bool(placement['flags'] and placement['flags'][6]),
                'detailed': bool(placement['flags'] and placement['flags'][5]),
                'min': lo.round(2).tolist(), 'max': hi.round(2).tolist(),
                'footprint': round(float(np.prod(hi[[0, 2]] - lo[[0, 2]])), 1)}
    boxes = [object_box(index, placement, tris) for index, (placement, tris) in enumerate(placed)]
    hf_data = game.data(f'terrains/{level}/{level}.heightfield-000.res')
    hf = terrain.read(hf_data) if hf_data else None
    ladders = []
    for placement, tris in placed:
        if flag(placement, 7):
            lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
            box_lo = float(lo[1])
            if hf is not None:                        # the foot: where the ladder leaves the ground
                lo[1] = max(lo[1], float(hf.height((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2)))
            if hi[1] - lo[1] >= LADDER_MIN_HEIGHT:
                ladders.append({'bp': placement['bp'], 'lo': [round(float(v), 3) for v in lo],
                                'hi': [round(float(v), 3) for v in hi], 'box_lo_y': round(box_lo, 3)})
    for name, value in (('objects.json', objs), ('group_instances.json', groups), ('soldier_spawns.json', spawns),
                        ('order_points.json', orders), ('object_boxes.json', boxes), ('ladders.json', ladders)):
        with open(os.path.join(out, name), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(value, f)
    print(f'{level}: {len(objs)} placed objects, {len(groups)} group members, {len(placed)} with collision')
    print('triangles: ' + ', '.join(f'{k} {v}' for k, v in counts.items()))
    print(f'ladders: {len(ladders)}')
    for why, bps in sorted(missing.items()):
        print(f'{why}: {len(bps)} blueprints, e.g. {sorted(bps)[:4]}')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('levelobjects1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
