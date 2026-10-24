"""NavKit (big-endian): object placements and collision of a BF1943 level, read straight from its bundles.

The BF1943 counterpart of levelobjects.py, which reads an unpacked, XML-converted BFBC2 level. Here the level's
binary DBX partitions and Havok 4.6.1 packfiles are read from the game's own .fbrb bundles (the level's folder
and levels/levels-common-00.fbrb). It writes the same files as levelobjects.py, so navbuild.py takes them
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

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
import havok461  # noqa: E402
import hkpack  # noqa: E402
from bfbc2core import dbx  # noqa: E402
from bfbc2core import terrain  # noqa: E402


# the level's bundles and the shared level bundle, by entry name (core game1943.py)
from bfbc2core.game1943 import Game  # noqa: E402,F401


def kind(e):
    return (e.attr('type') or '').rsplit('.', 1)[-1]


def matrix(e, name='Transform'):
    c = e.child(name)
    if c is None or c.kind != dbx.NUMBERS:
        return None
    v = dbx.floats(c)
    return v if len(v) == 16 else None


def blueprint_info(game, bp):
    """(flags, mesh alias) of a blueprint partition: flags as levelobjects.blueprint_flags gives them
    (ForbidPathfinding, ExcludeFromPathfinding, AllowPathdataSeeds, dynamic, DisablePathfinding), then
    NeedsDetailedPathfinding (the field DICE's generator reads to build a sector at pitch 0.2), then destructible (a
    PartComponentData with two or more HealthStates: intact, destroyed - sandbag walls, fences), then ladder (a
    LadderEntityData), or None."""
    f = game.partition(bp)
    if f is None:
        return None, None
    forbid = exclude = seeds = dynamic = detailed = ladder = False
    states = 0
    disable, mesh = '', None
    for inst in f.instances:
        dynamic |= kind(inst) == 'DynamicModelEntityData'
        ladder |= kind(inst) == 'LadderEntityData'
        if kind(inst) == 'PartComponentData' and inst.child('HealthStates') is not None:
            states = max(states, len(inst.child('HealthStates').children or ()))
        for c in inst.children or ():
            n = c.attr('name')
            if n == 'ForbidPathfinding':
                forbid |= dbx.value(c) == 'true'
            elif n == 'ExcludeFromPathfinding':
                exclude |= dbx.value(c) == 'true'
            elif n == 'AllowPathdataSeeds':
                seeds |= dbx.value(c) == 'true'
            elif n == 'NeedsDetailedPathfinding':
                detailed |= dbx.value(c) == 'true'
            elif n == 'DisablePathfinding':
                disable = dbx.value(c)
            elif n == 'Mesh' and c.attr('ref') and c.attr('ref') != 'null' and mesh is None:
                mesh = re.sub(r'_mesh$', '', c.attr('ref').rsplit('/', 1)[0].lower())
    return (forbid, exclude, seeds, dynamic, disable, detailed, states >= 2, ladder), mesh


def collision(game, bp, cache, missing):
    """Local-space collision triangles (N x 3 x 3) of a blueprint's <name>_Havok.res, or None."""
    if bp in cache:
        return cache[bp]
    tris = None
    data = game.data(bp + '_havok.res') or game.data(bp + '_havok_havok.res')
    if data:
        pf = hkpack.Packfile(data, classes=havok461.CLASSES)
        parts = []
        for off in pf.objects_of('hkStorageExtendedMeshShapeMeshSubpartStorage'):
            v, t = havok461.triangles(pf, off)
            if v and t:
                parts.append(np.asarray(v, np.float32)[np.asarray(t)])
        for off in pf.objects_of('hkConvexVerticesShape'):    # sandbags, crates, fences: their vertex box
            box, _, _ = havok461.convex_box(pf, off)
            if box:
                parts.append(np.asarray(box, np.float32))
        if pf.objects_of('hkBoxShape') or pf.objects_of('hkConvexTranslateShape'):
            missing.setdefault('box / translated convex shapes not read', set()).add(bp)
        tris = np.concatenate(parts) if parts else None
    cache[bp] = tris
    return tris


def world(tris, m):
    m = np.asarray(m, np.float64).reshape(4, 4)          # rows: right, up, forward, translation
    return (tris @ m[:3, :3] + m[3, :3]).astype(np.float32)


LADDER_MIN_HEIGHT = 1.5         # as ladders.py: a lower ladder box is a prop lying down or a fragment


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 1
    game_dir, level, out = argv[1:]
    os.makedirs(out, exist_ok=True)
    game = Game(game_dir, level)
    lvl = game.partition(f'levels/{level}')
    if lvl is None:
        raise FileNotFoundError(f'levels/{level}.dbx is not in the level bundles')
    objs, groups, spawns, orders = [], [], [], []
    for e in lvl.instances:
        k = kind(e)
        m = matrix(e)
        if k == 'ReferenceObjectData':
            r = e.child('ReferencedObject')
            if m and r is not None and r.attr('ref') and r.attr('ref') != 'null':
                objs.append({'bp': r.attr('ref').rsplit('/', 1)[0].lower(), 'm': m})
        elif k == 'SoldierSpawnEntityData' and m and any(abs(c) > 1e-3 for c in m[12:15]):
            spawns.append(m[12:15])
        elif k == 'AreaGeometryEntityData' and m and any(abs(c) > 1e-3 for c in m[12:15]):
            orders.append(m[12:15])
    for name in sorted(n for n in game.entries if n.startswith(f'levels/{level}/') and n.endswith('_havokasset.dbx')):
        f = dbx.parse(game.data(name))
        for e in f.instances:
            if kind(e) != 'CompositeHavokAsset':
                continue
            t = e.child('Transforms')
            tr = dbx.floats(t) if t is not None and t.kind == dbx.NUMBERS else []
            assets = [c.attr('ref') for c in (e.child('Assets').children or ())] if e.child('Assets') is not None else []
            for i, a in enumerate(assets):
                if a and len(tr) >= 16 * (i + 1):
                    bp = re.sub(r'_havok$', '', a.rsplit('/', 1)[0].lower())
                    groups.append({'group': os.path.basename(name), 'bp': bp, 'm': tr[16 * i:16 * (i + 1)]})
    info = {}
    for o in objs + groups:
        if o['bp'] not in info:
            info[o['bp']] = blueprint_info(game, o['bp'])
        o['flags'] = info[o['bp']][0]
    cache, missing = {}, {}

    def tris_of(o):
        t = collision(game, o['bp'], cache, missing)
        alias = info[o['bp']][1]
        if t is None and alias and alias != o['bp']:          # a blueprint reusing another object's mesh and collision
            t = collision(game, alias, cache, missing)
        if t is None:
            missing.setdefault('no collision', set()).add(o['bp'])
            return None
        return world(t, o['m'])

    def save(name, parts):
        np.save(os.path.join(out, name), np.concatenate(parts) if parts else np.zeros((0, 3, 3), np.float32))
        return sum(len(p) for p in parts)

    placed = [(o, tris_of(o)) for o in objs + groups]
    placed = [(o, w) for o, w in placed if w is not None]
    fl = lambda o, i: bool(o['flags']) and len(o['flags']) > i and o['flags'][i]
    stair_like = re.compile(r'stair|ramp|steps', re.I)
    counts = {
        'all': save('all_tris.npy', [w for _, w in placed]),
        'forbid': save('forbid_tris.npy', [w for o, w in placed if fl(o, 0) and not fl(o, 1)]),
        'dynamic': save('dynamic_tris.npy', [w for o, w in placed if fl(o, 3) or (o['flags'] and o['flags'][4] == 'PdtWhenAlive')]),
        'stairs': save('stair_tris.npy', [w for o, w in placed if stair_like.search(o['bp'].rsplit('/', 1)[-1])]),
        'exclude': save('exclude_tris.npy', [w for o, w in placed if fl(o, 1)]),
        'exclude_foot': save('exclude_footprint_tris.npy', [w[w[:, :, 1].min(1) <= o['m'][13] + 0.5] for o, w in placed if fl(o, 1)]),
    }
    boxes = [{'index': k, 'bp': o['bp'], 'destructible': bool(o['flags'] and o['flags'][6]),
              'detailed': bool(o['flags'] and o['flags'][5]), 'min': w.reshape(-1, 3).min(0).round(2).tolist(), 'max': w.reshape(-1, 3).max(0).round(2).tolist(),
              'footprint': round(float(np.prod(w.reshape(-1, 3).max(0)[[0, 2]] - w.reshape(-1, 3).min(0)[[0, 2]])), 1)}
             for k, (o, w) in enumerate(placed)]
    hf_data = game.data(f'terrains/{level}/{level}.heightfield-000.res')
    hf = terrain.read(hf_data) if hf_data else None
    ladders = []
    for o, w in placed:
        if fl(o, 7):
            lo, hi = w.reshape(-1, 3).min(0), w.reshape(-1, 3).max(0)
            box_lo = float(lo[1])
            if hf is not None:                        # the foot: where the ladder leaves the ground
                lo[1] = max(lo[1], float(hf.height((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2)))
            if hi[1] - lo[1] >= LADDER_MIN_HEIGHT:
                ladders.append({'bp': o['bp'], 'lo': [round(float(v), 3) for v in lo], 'hi': [round(float(v), 3) for v in hi],
                                'box_lo_y': round(box_lo, 3)})
    for name, value in (('objects.json', objs), ('group_instances.json', groups), ('soldier_spawns.json', spawns),
                        ('order_points.json', orders), ('object_boxes.json', boxes), ('ladders.json', ladders)):
        with open(os.path.join(out, name), 'w', encoding='utf-8') as f:
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
