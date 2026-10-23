r"""BF1943 spawn map: each level's objectives (bases and flags) with the soldier and vehicle spawns they own.

  python -B spawnmap.py [<dist\xenon folder>] [level ...] [--json <file>]

<dist\xenon folder> is an extracted BF1943 game folder (default: bf1943-xenia-kit\output\title\dist\xenon,
which `xeniakit.py build` makes from your own package). Levels default to every multiplayer level found
(wake_island_s, guadal_canal, iwo_jima_s, coral_sea). Only reads; nothing is written except --json.

From each level's main partition (levels/<level>.dbx):
  CapturePointEntityData   an objective: Name, LocationName (text id), Index, InitialOwnerTeam, CaptureRadius,
                           OnlyTakeableByTeam, UnableToChangeTeam (a base cannot change hands), Spawners (the
                           spawns the point owns: their team follows the point's owner)
  SoldierSpawnEntityData   Team, PlayerType (SSHumanPlayer / SSAiPlayer), Enabled, LocationNameSid,
                           AlternativeSpawnPoints (AlternateSpawnEntityData: more positions for the same spawn)
  VehicleSpawnEntityData   the vehicle blueprint and its team
Positions are the Transform's translation (x, y, z; y is up), in metres.
"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
from bfbc2core import dbx
from fbrb1943 import Bundle

DEFAULT_GAME = os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'title', 'dist', 'xenon')
LEVELS = ['wake_island_s', 'guadal_canal', 'iwo_jima_s', 'coral_sea']


def level_partition(game, level):
    """The bytes of levels/<level>.dbx, from whichever of the level's bundles holds it."""
    folder = os.path.join(game, 'levels', level)
    want = f'levels/{level}.dbx'
    for name in sorted(os.listdir(folder)):
        if name.endswith('.fbrb'):
            bundle = Bundle(open(os.path.join(folder, name), 'rb').read())
            for e in bundle.entries:
                if e['name'].lower() == want:
                    return bundle.data(e['name']), name
    raise FileNotFoundError(f'{want} is in none of {folder}\\*.fbrb')


def kind(e):
    return (e.attr('type') or '').rsplit('.', 1)[-1]


def scalar(e, name):
    c = e.child(name)
    return None if c is None else dbx.value(c)


def number(e, name):
    c = e.child(name)
    return None if c is None or c.kind != dbx.NUMBERS else dbx.floats(c)[0]


def refs(e, name):
    """The references an array field holds: local GUIDs (lower case) or 'partition/guid' paths."""
    c = e.child(name)
    return [m.attr('ref').lower() for m in (c.children or ()) if m.attr('ref')] if c is not None else []


def ref(e, name):
    c = e.child(name)
    return c.attr('ref').lower() if c is not None and c.attr('ref') else None


def position(e):
    t = e.child('Transform')
    if t is None or t.kind != dbx.NUMBERS:
        return None
    v = dbx.floats(t)
    return [round(v[12], 2), round(v[13], 2), round(v[14], 2)] if len(v) == 16 else None


def soldier_spawn(e, by_guid):
    alternates = []
    for g in refs(e, 'AlternativeSpawnPoints'):
        a = by_guid.get(g)
        alternates.append(position(a) if a is not None else g)
    return dict(guid=e.attr('guid').lower(), kind='soldier', name=scalar(e, 'Name') or '', team=scalar(e, 'Team'),
                player_type=scalar(e, 'PlayerType'), enabled=scalar(e, 'Enabled'),
                location=scalar(e, 'LocationNameSid') or '', position=position(e), alternates=alternates)


def vehicle_spawn(e):
    blueprint = ref(e, 'Vehicle') or ref(e, 'Template') or ''
    return dict(guid=e.attr('guid').lower(), kind='vehicle', name=scalar(e, 'Name') or '', team=scalar(e, 'Team'),
                vehicle=blueprint.rsplit('/', 2)[-2] if blueprint.count('/') >= 2 else blueprint,
                position=position(e))


def spawn_map(game, level):
    data, bundle = level_partition(game, level)
    f = dbx.parse(data)
    by_guid = {(e.attr('guid') or '').lower(): e for e in f.instances}
    owned = set()
    objectives = []
    for e in f.instances:
        if kind(e) != 'CapturePointEntityData':
            continue
        spawns = []
        for g in refs(e, 'Spawners'):
            s = by_guid.get(g)
            if s is None:
                spawns.append(dict(guid=g, kind='external'))
            elif kind(s) == 'SoldierSpawnEntityData':
                spawns.append(soldier_spawn(s, by_guid))
            elif kind(s) == 'VehicleSpawnEntityData':
                spawns.append(vehicle_spawn(s))
            else:
                spawns.append(dict(guid=g, kind=kind(s)))
            owned.add(g)
        base = scalar(e, 'UnableToChangeTeam') == 'true'
        objectives.append(dict(guid=e.attr('guid').lower(), name=scalar(e, 'Name') or '', role='base' if base else 'flag',
                               location=scalar(e, 'LocationName') or '', index=scalar(e, 'Index'),
                               initial_owner=scalar(e, 'InitialOwnerTeam'),
                               only_takeable_by=scalar(e, 'OnlyTakeableByTeam'),
                               capture_radius=number(e, 'CaptureRadius'), position=position(e), spawns=spawns))
    objectives.sort(key=lambda o: (o['role'] != 'base', int(o['index'] or 0)))
    unowned = [soldier_spawn(e, by_guid) for e in f.instances
               if kind(e) == 'SoldierSpawnEntityData' and (e.attr('guid') or '').lower() not in owned]
    return dict(level=level, bundle=bundle, objectives=objectives, unowned_soldier_spawns=unowned)


def show(m):
    print(f'== {m["level"]} ({m["bundle"]})')
    for o in m['objectives']:
        soldiers = [s for s in o['spawns'] if s['kind'] == 'soldier']
        vehicles = [s for s in o['spawns'] if s['kind'] == 'vehicle']
        print(f'  {o["role"]:4} #{o["index"]} {o["name"] or "-":28} {o["location"]:34} owner {o["initial_owner"]:12}'
              f' at {o["position"]}  radius {o["capture_radius"]}')
        for s in soldiers:
            print(f'       soldier spawn {s["name"] or "-":30} {s["team"]:8} {s["player_type"]:14} at {s["position"]}'
                  f' + {len(s["alternates"])} alternates')
        if vehicles:
            counts = {}
            for v in vehicles:
                counts[v['vehicle']] = counts.get(v['vehicle'], 0) + 1
            print('       vehicles: ' + ', '.join(f'{n} x{c}' for n, c in sorted(counts.items())))
    for s in m['unowned_soldier_spawns']:
        print(f'  (no objective) soldier spawn {s["name"] or "-"} {s["team"]} {s["player_type"]} enabled {s["enabled"]}'
              f' at {s["position"]}')


def main(argv):
    p = argparse.ArgumentParser(prog='spawnmap.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('game', nargs='?', default=DEFAULT_GAME)
    p.add_argument('levels', nargs='*')
    p.add_argument('--json')
    a = p.parse_args(argv[1:])
    if a.game in LEVELS:                      # spawnmap.py wake_island_s: the default game folder
        a.levels, a.game = [a.game] + a.levels, DEFAULT_GAME
    levels = a.levels or [lv for lv in LEVELS if os.path.isdir(os.path.join(a.game, 'levels', lv))]
    if not levels:
        raise SystemExit(f'no BF1943 levels in {a.game}: give the dist\\xenon folder of an extracted game')
    maps = [spawn_map(a.game, lv) for lv in levels]
    for m in maps:
        show(m)
    if a.json:
        with open(a.json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(maps, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
