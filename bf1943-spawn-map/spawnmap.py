r"""BF1943 spawn map: each level's objectives (bases and flags) with the soldier and vehicle spawns they own.

  python -B spawnmap.py [<dist\xenon folder>] [level ...] [--json <file>] [--navflags]

<dist\xenon folder> is an extracted BF1943 game folder (default: bf1943-xenia-kit\output\title\dist\xenon,
which `xeniakit.py build` makes from your own package). Levels default to every multiplayer level found
(wake_island_s, guadal_canal, iwo_jima_s, coral_sea). Only reads; nothing is written except --json and --navflags.

From each level's main partition (levels/<level>.dbx):
  CapturePointEntityData   an objective: Name, LocationName (text id), Index, InitialOwnerTeam, CaptureRadius,
                           OnlyTakeableByTeam, UnableToChangeTeam (a base cannot change hands), Spawners (the
                           spawns the point owns: their team follows the point's owner)
  SoldierSpawnEntityData   Team, PlayerType (SSHumanPlayer / SSAiPlayer), Enabled, LocationNameSid,
                           AlternativeSpawnPoints (AlternateSpawnEntityData: more positions for the same spawn)
  VehicleSpawnEntityData   the vehicle blueprint and its team
Positions are the Transform's translation (x, y, z; y is up), in metres.

--navflags also writes <suite>\work\levels\<level>\navkit\<level>.flags.txt (BF1943_WORK sets another work folder)
for each level: the objectives and the bases' soldier spawns as plain lines a C program reads (navhost and rsbot's
tests, project-opfor-bf1943 TnT/Code/Engine/AI/Bots), in FLAGS_FORMAT below.
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
WORK = os.environ.get('BF1943_WORK') or os.path.join(SUITE, 'work')
FLAGS_FORMAT = '''# rsflags 1 <level>: written by bf1943-spawn-map/spawnmap.py --navflags (from levels/<level>.dbx)
# flag <index> <base|flag> <initial owner: 0 neutral, 1, 2> <x> <y> <z> <capture radius, 0: an area> <name>
# spawn <team 1 or 2> <objective index> <x> <y> <z>   (a base's soldier spawn and its alternates)'''
TEAM_NUMBER = {'TeamNeutral': 0, 'Team1': 1, 'Team2': 2}


def placeholder(o):
    """Why an objective is not a real flag, or None: no Index or no position, or a point at the map origin that owns no
    spawn (Coral Sea's three "Spawn" capture points, all Index 0)."""
    if o['index'] in (None, '') or o['position'] is None:
        return 'no index' if o['index'] in (None, '') else 'no position'
    if all(c == 0 for c in o['position']) and not o['spawns']:
        return 'at the map origin, owning no spawn'
    return None


def flags_text(m):
    """The level's objectives and base spawns as FLAGS_FORMAT lines: real objectives only (placeholder() ones are
    named in a comment), each index once (a duplicate is an error: the C readers key flags by index)."""
    lines = FLAGS_FORMAT.replace('<level>', m['level'], 1).splitlines()
    real, left_out = [], []
    for o in m['objectives']:
        why = placeholder(o)
        if why:
            left_out.append(f'{o["name"] or o["location"] or "-"} (index {o["index"]}: {why})')
        else:
            real.append(o)
    if left_out:
        lines.append('# left out, not flags: ' + '; '.join(left_out))
    seen = {}
    for o in real:
        k = int(o['index'])
        if k in seen:
            raise ValueError(f'{m["level"]}: objectives {seen[k]!r} and {o["name"] or o["location"]!r} share index {k}')
        seen[k] = o['name'] or o['location']
        x, y, z = o['position']
        name = (o['name'] or o['location'] or '-').replace(' ', '_')
        lines.append(f'flag {k} {o["role"]} {TEAM_NUMBER.get(o["initial_owner"], 0)} '
                     f'{x:.2f} {y:.2f} {z:.2f} {float(o["capture_radius"] or 0):.2f} {name}')
    for o in real:
        if o['role'] != 'base':
            continue
        for s in o['spawns']:
            if s['kind'] != 'soldier' or TEAM_NUMBER.get(s['team'], 0) == 0:
                continue
            for p in [s['position']] + [a for a in s['alternates'] if isinstance(a, list)]:
                if p is not None:
                    lines.append(f'spawn {TEAM_NUMBER[s["team"]]} {int(o["index"])} {p[0]:.2f} {p[1]:.2f} {p[2]:.2f}')
    return '\n'.join(lines) + '\n'


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
    # vehicles no objective owns (e.g. the carriers' own boats and planes): their team is their spawn's
    vehicles = [vehicle_spawn(e) for e in f.instances
                if kind(e) == 'VehicleSpawnEntityData' and (e.attr('guid') or '').lower() not in owned]
    return dict(level=level, bundle=bundle, objectives=objectives, unowned_soldier_spawns=unowned,
                unowned_vehicle_spawns=vehicles)


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
    for v in m['unowned_vehicle_spawns']:
        print(f'  (no objective) vehicle {v["vehicle"]:24} {v["team"] or "-":12} at {v["position"]}')
    for s in m['unowned_soldier_spawns']:
        print(f'  (no objective) soldier spawn {s["name"] or "-"} {s["team"]} {s["player_type"]} enabled {s["enabled"]}'
              f' at {s["position"]}')


def main(argv):
    p = argparse.ArgumentParser(prog='spawnmap.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('game', nargs='?', default=DEFAULT_GAME)
    p.add_argument('levels', nargs='*')
    p.add_argument('--json')
    p.add_argument('--navflags', action='store_true', help='also write work/levels/<level>/navkit/<level>.flags.txt')
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
    if a.navflags:
        for m in maps:
            folder = os.path.join(WORK, 'levels', m['level'], 'navkit')
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f'{m["level"]}.flags.txt')
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(flags_text(m))
            print(f'wrote {path}')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('spawnmap', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
