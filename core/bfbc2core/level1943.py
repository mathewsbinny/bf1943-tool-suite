"""A BF1943 level as the suite's tools see it, read straight from your copy's bundles (game1943.py): entities with
transforms and sources, collision meshes, terrain, water, outlines - the same Level / Entity / Mesh / Shape as
level.py gives for a BFBC2 level, so the 3D tools take either.

    lvl = load('wake_island_s')               # from find_game() (bf1943-xenia-kit's output, BF1943_GAME overrides)
    lvl.entities                              # the level partition's instances and the static-group members
    lvl.meshes                                # world-space collision per placed object and group member
    lvl.heightfield, lvl.terrain              # terrain.Heightfield (big-endian), and its mesh
    lvl.water                                 # water.Water: the sea surface
    lvl.shapes                                # outlines: capture areas, combat areas, DMZs (AreaGeometry chains)
    lvl.objectives                            # bf1943-spawn-map's bases and flags, with the spawns they own

BF1943's categories: 'base' and 'flag' (capture points: a base cannot change team), soldier / vehicle spawns,
'end-of-round spawn', 'combat area', 'dmz', 'map marker', 'area point'; placed objects by their blueprint:
'ladder' (LadderEntityData), 'destructible' (two or more health states), 'dynamic object', 'placed object'.
Level.opfor holds the game folder (the field's name is the BFBC2 loader's). Nothing is unpacked or written.
"""
import os
import sys

import numpy as np

from . import dbx, game1943, terrain, water
from .level import CATEGORIES, Entity, Level, Mesh, assign_teams, blueprint_of, category_of, shapes_of, transform_of

NAVKIT_TOOLS = os.path.join(game1943.SUITE, 'bf1943-auto-navkit-big-endian', 'tools')
SPAWN_MAP = os.path.join(game1943.SUITE, 'bf1943-spawn-map')
CATEGORIES_1943 = [
    ('end-of-round spawn', r'\.EndOfRoundSoldierSpawnEntityData$'),
    ('dmz', r'\.DmzEntityData$'),
    ('map marker', r'\.MapMarkerEntityData$'),
] + CATEGORIES
FLAG_INDEX = {'forbid': 0, 'exclude': 1, 'dynamic': 3, 'detailed': 5, 'destructible': 6, 'ladder': 7}


def category_1943(t):
    import re
    for name, rx in CATEGORIES_1943:
        if re.search(rx, t or ''):
            return name
    return category_of(t)


def load(level, game=None, terrain_stride=2, log=print):
    # each folder once (2026-10-30 second review: every load put both in front again, so sys.path grew per level)
    sys.path[:0] = [p for p in (NAVKIT_TOOLS, SPAWN_MAP) if p not in sys.path]
    import levelobjects1943 as lo
    game = game1943.find_game(game)
    lv = level.replace('\\', '/').split('/')[-1].lower()
    g = game1943.Game(game, lv)
    f = g.partition(f'levels/{lv}')
    if f is None:
        raise FileNotFoundError(f'levels/{lv}.dbx is in none of {lv}\'s bundles')
    name = next((dbx.value(c) for e in f.instances if lo.kind(e) == 'LevelData' for c in (e.children or ())
                 if c.attr('name') == 'Name' and c.kind == dbx.TEXT), lv)
    lvl = Level(name.rsplit('/', 1)[-1], lv, game)
    log(f'{lvl.name}: reading levels/{lv}.dbx')
    pname = 'levels/' + lv
    for el in f.instances:
        t = el.attr('type') or ''
        nm = el.child('Name')
        lvl.entities.append(Entity(len(lvl.entities), el.attr('guid') or '', t, t.rsplit('.', 1)[-1], category_1943(t),
                                   el.attr('id'), nm.text if nm is not None and nm.kind == dbx.TEXT else None, pname,
                                   transform_of(el), blueprint_of(el), el))
    # static-model groups: one entity per member placement (Physics.CompositeHavokAsset: Assets[i] + Transforms[i])
    for gname in g.names(f'levels/{lv}/'):
        if not gname.endswith('_havokasset.dbx'):
            continue
        for el in dbx.parse(g.data(gname)).instances:
            if lo.kind(el) != 'CompositeHavokAsset':
                continue
            tr, assets = el.child('Transforms'), el.child('Assets')
            if tr is None or assets is None or tr.kind != dbx.NUMBERS or not assets.children:
                continue
            vals = np.array(dbx.floats(tr), dtype=np.float64)
            for k, item in enumerate(assets.children):
                if 16 * (k + 1) > len(vals):
                    break
                bp = (item.attr('ref') or '').rsplit('/', 1)[0].lower()
                bp = bp[:-len('_havok')] if bp.endswith('_havok') else bp
                lvl.entities.append(Entity(len(lvl.entities), f'{el.attr("guid")}#{k}', 'StaticGroupMember', 'StaticGroupMember',
                                           'group member', None, bp.rsplit('/', 1)[-1], os.path.basename(gname)[:-4],
                                           vals[16 * k:16 * (k + 1)].reshape(4, 4), bp, item, group=os.path.basename(gname)))
    # placed objects: category and flags from the blueprint (levelobjects1943.blueprint_info)
    info = {}
    for e in lvl.entities:
        if e.short_type != 'ReferenceObjectData' or not e.blueprint:
            continue
        if e.blueprint not in info:
            info[e.blueprint] = lo.blueprint_info(g, e.blueprint)
        flags = info[e.blueprint][0]
        if not flags:
            continue
        fl = lambda k: len(flags) > FLAG_INDEX[k] and bool(flags[FLAG_INDEX[k]])
        e.destructible = fl('destructible')
        e.category = 'ladder' if fl('ladder') else 'destructible' if e.destructible else 'dynamic object' if fl('dynamic') else e.category
    assign_teams(lvl)
    for e in lvl.entities:                                  # DMZs name their team in TeamId
        c = e.element.child('TeamId') if e.short_type == 'DmzEntityData' else None
        if c is not None and c.kind == dbx.TEXT:
            e.team = {'Team1': 1, 'Team2': 2}.get(c.text, 0)
    lvl.shapes = shapes_of(lvl)
    # bases and flags (bf1943-spawn-map)
    import spawnmap
    m = spawnmap.spawn_map(game, lv)
    lvl.objectives = m['objectives']
    roles = {o['guid']: o['role'] for o in m['objectives']}
    for e in lvl.entities:
        if e.short_type == 'CapturePointEntityData':
            e.category = roles.get(e.guid.lower(), 'flag')
    # BF1943's spawns are all TeamNeutral in the data: a spawn's side is its owner's. A base never changes team, so
    # its spawns (and their alternates) take its initial owner; a flag's stay neutral (they follow the flag)
    by_guid = {e.guid.lower(): e for e in lvl.entities}
    teams = {'Team1': 1, 'Team2': 2}
    for o in m['objectives']:
        if o['role'] != 'base' or o['initial_owner'] not in teams:
            continue
        for s in o['spawns']:
            e = by_guid.get(s['guid'])
            if e is None:
                continue
            e.team = teams[o['initial_owner']]
            alt = e.element.child('AlternativeSpawnPoints')
            for item in (alt.children if alt is not None and alt.children else ()):
                a = by_guid.get((item.attr('ref') or '').lower())
                if a is not None:
                    a.team = e.team
    # collision
    cache, missing = {}, {}
    for e in lvl.entities:
        if e.blueprint is None or e.matrix is None:
            continue
        t = lo.collision(g, e.blueprint, cache, missing)
        alias = (info.get(e.blueprint) or (None, None))[1]
        if t is None and alias and alias != e.blueprint:
            t = lo.collision(g, alias, cache, missing)
        if t is not None:
            lvl.meshes.append(Mesh(e.index, lo.world(t, e.matrix)))
    lvl.missing_collision = sorted({bp for bps in missing.values() for bp in bps})
    # terrain and water: Terrains/<lv>/<lv>.* in the level's terrain bundle
    tname = terrain_name_1943(f.instances) or lv
    hf_data = g.data(f'terrains/{tname}/{tname}.heightfield-000.res')
    if hf_data:
        lvl.heightfield = terrain.read(hf_data)
        lvl.terrain = lvl.heightfield.mesh(terrain_stride)
    else:
        lvl.notes.append(f'no height field terrains/{tname}/{tname}.heightfield-000.res')
    mesh = g.data(f'terrains/{tname}/{tname}.water.mesh.res')
    if mesh:
        lvl.water = water_from_bytes(mesh)
    else:
        lvl.notes.append('no water mesh')
    # spawns no base owns (the carriers' boats, planes and guns): the side of the carrier they stand on
    carriers = []
    for x in lvl.meshes:
        bp = lvl.entities[x.entity].blueprint or ''
        side = 1 if bp.endswith('/us_carrier') else 2 if bp.endswith('/jap_carrier') else None
        if side:
            v = x.triangles.reshape(-1, 3)
            carriers.append((side, v.min(0), v.max(0)))
    for e in lvl.entities:
        if e.category in ('vehicle spawn', 'soldier spawn') and not e.team and e.position is not None:
            p = e.position
            for side, lo_, hi_ in carriers:
                if lo_[0] <= p[0] <= hi_[0] and lo_[2] <= p[2] <= hi_[2] and lo_[1] - 1 <= p[1] <= hi_[1] + 1:
                    e.team = side
    n_tris = sum(len(x.triangles) for x in lvl.meshes)
    log(f'{lvl.name}: {len(lvl.entities)} entities, {len(lvl.meshes)} with collision ({n_tris} triangles), '
        f'{len(lvl.missing_collision)} blueprints without collision, {len(lvl.objectives)} bases and flags')
    return lvl


def terrain_name_1943(instances):
    """The terrain's name from TerrainEntityData.TerrainAsset (terrains/<name>/<name>/<guid>), or None."""
    for el in instances:
        if (el.attr('type') or '').endswith('.TerrainEntityData'):
            a = el.child('TerrainAsset')
            parts = ((a.attr('ref') or '') if a is not None else '').split('/')
            return parts[1].lower() if len(parts) >= 3 else None
    return None


def water_from_bytes(data):
    """water.Water from a .Water.Mesh.res's bytes (water.read_mesh reads a file: a temporary one)."""
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.water.mesh.res')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        v, t = water.read_mesh(path)
    finally:
        os.remove(path)
    return water.Water(v, t)
