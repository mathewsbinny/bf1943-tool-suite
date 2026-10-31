"""BF1943 vehicles as the navigation tools need them: each vehicle spawn's class and its entry points.

    entries = entry_points(game, spawn_entity)     # [{'point': [x, y, z], 'radius': r, 'order': n}] in the world
    vehicle_class('aa_bofors')                     # 'stationary weapon'

A vehicle is entered with "use" within its EntryRadius of an entry point: the vehicle blueprint's
PlayerEntryComponentData (Transform, EntryRadius, EntryOrderNumber), placed by the spawn's transform [O] - Wake
Island's air-raid bunker (airraidbunker_wake.dbx: radius 3 m, entry at local (0, 0.5, -2.5)) is entered from outside its
sealed room this way. The entry points of parts that move (turrets) are taken at their rest transform. Whether the game
also requires a line of sight to the entry point is not known.
"""
import numpy as np

from . import dbx

GROUND = {'willys-mb', 'kurogane', 'm4-sherman', 'type97-chiha'}
BOATS = {'lcvp-landingcraft'}
AIRCRAFT = {'corsair', 'zero_fighter'}
STATIONARY = {'aa_bofors', '30cal_tripod', '30cal_windowmount'}
CLASSES = ['ground vehicle', 'boat', 'aircraft', 'stationary weapon', 'other']
CACHE_ATTR = '_vehicle_entries'     # the per-game cache: an attribute of the game object, so it goes with the object


def vehicle_class(name):
    name = name or ''
    return ('ground vehicle' if name in GROUND else 'boat' if name in BOATS else 'aircraft' if name in AIRCRAFT
            else 'stationary weapon' if name in STATIONARY else 'other')


def vehicle_partition(entity):
    """The vehicle blueprint partition a vehicle spawn entity makes ('objects/vehicles/...'), or None."""
    v = entity.element.child('Vehicle')
    ref = v.attr('ref') if v is not None else None
    return ref.rsplit('/', 1)[0] if ref and ref != 'null' else None


def local_entries(game, partition):
    """[(local position (3,), radius, order)] of a vehicle blueprint's PlayerEntryComponentData (cached on the game
    object: 2026-10-30, a module cache keyed by id(game) gave a new game, whose id Python had reused, another copy's
    entries)."""
    cache = getattr(game, CACHE_ATTR, None)
    if cache is None:
        cache = {}
        try:
            setattr(game, CACHE_ATTR, cache)
        except (AttributeError, TypeError):
            pass                                  # an object that takes no attributes: read every time
    if partition not in cache:
        out = []
        data = game.data(partition + '.dbx')
        if data:
            for inst in dbx.parse(data).instances:
                if 'PlayerEntryComponentData' not in (inst.attr('type') or ''):
                    continue
                vals = {(c.attr('name') or c.tag): c for c in inst.children or ()}
                try:
                    m = [float(t) for t in dbx.value(vals['Transform']).split('/')]
                    if len(m) != 16:                # 2026-10-30 second review: a short one failed later, in entry_points
                        continue
                    radius = float(dbx.value(vals['EntryRadius']))
                    order = int(dbx.value(vals['EntryOrderNumber'])) if 'EntryOrderNumber' in vals else 0
                except (KeyError, ValueError, TypeError):
                    continue
                out.append((np.array(m[12:15]), radius, order))
        cache[partition] = out
    return cache[partition]


def entry_points(game, entity):
    """The spawn's vehicle's entry points in the world: [{'point', 'radius', 'order'}]; [] when it has none."""
    part = vehicle_partition(entity)
    if part is None or entity.matrix is None:
        return []
    M = np.asarray(entity.matrix, float)
    return [{'point': (p @ M[:3, :3] + M[3, :3]).tolist(), 'radius': r, 'order': o}
            for p, r, o in local_entries(game, part)]
