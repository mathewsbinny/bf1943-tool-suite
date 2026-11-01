r"""BF1943 spawn map: each level's objectives (bases and flags) with the soldier and vehicle spawns they own.

Reads each level's main DBX partition from the game folder's bundles; prints the map, and writes it as JSON (--json)
and as the flags files navhost and rsbot read (--navflags). spawn_map(game, level) is the library form.

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
from typing import TYPE_CHECKING, Any, cast

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
from bfbc2core import dbx  # noqa: E402
from fbrb1943 import Bundle  # noqa: E402

if TYPE_CHECKING:
    from bfbc2core.dbx import Element

# spawn_map's result (level, bundle, objectives, unowned soldier and vehicle spawns), an objective and a spawn:
# JSON-like dicts
type LevelMap = dict[str, Any]
type Objective = dict[str, Any]
type Spawn = dict[str, Any]
DEFAULT_GAME = os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'title', 'dist', 'xenon')
LEVELS = ['wake_island_s', 'guadal_canal', 'iwo_jima_s', 'coral_sea']
WORK = os.environ.get('BF1943_WORK') or os.path.join(SUITE, 'work')
FLAGS_FORMAT = '''# rsflags 1 <level>: written by bf1943-spawn-map/spawnmap.py --navflags (from levels/<level>.dbx)
# flag <index> <base|flag> <initial owner: 0 neutral, 1, 2> <x> <y> <z> <capture radius, 0: an area> <name>
# spawn <team 1 or 2> <objective index> <x> <y> <z>   (a base's soldier spawn and its alternates)'''
TEAM_NUMBER = {'TeamNeutral': 0, 'Team1': 1, 'Team2': 2}


def placeholder(objective: Objective) -> str | None:
    """Why an objective is not a real flag, or None: no Index or no position, or a point at the map origin that owns no
    spawn (Coral Sea's three "Spawn" capture points, all Index 0)."""
    if objective['index'] in (None, '') or objective['position'] is None:
        return 'no index' if objective['index'] in (None, '') else 'no position'
    if all(coordinate == 0 for coordinate in objective['position']) and not objective['spawns']:
        return 'at the map origin, owning no spawn'
    return None


def flags_text(level_map: LevelMap) -> str:
    """The level's objectives and base spawns as FLAGS_FORMAT lines: real objectives only (placeholder() ones are
    named in a comment), each index once (a duplicate is an error: the C readers key flags by index)."""
    lines = FLAGS_FORMAT.replace('<level>', level_map['level'], 1).splitlines()
    real: list[Objective] = []
    left_out: list[str] = []
    for objective in level_map['objectives']:
        why = placeholder(objective)
        if why:
            left_out.append(f'{objective["name"] or objective["location"] or "-"} (index {objective["index"]}: {why})')
        else:
            real.append(objective)
    if left_out:
        lines.append('# left out, not flags: ' + '; '.join(left_out))
    seen: dict[int, str] = {}           # index -> the objective's name
    for objective in real:
        index = int(objective['index'])
        label = objective['name'] or objective['location']
        if index in seen:
            raise ValueError(f'{level_map["level"]}: objectives {seen[index]!r} and {label!r} share index {index}')
        seen[index] = label
        x, y, z = objective['position']
        name = (objective['name'] or objective['location'] or '-').replace(' ', '_')
        owner = TEAM_NUMBER.get(objective['initial_owner'], 0)
        radius = float(objective['capture_radius'] or 0)
        lines.append(f'flag {index} {objective["role"]} {owner} {x:.2f} {y:.2f} {z:.2f} {radius:.2f} {name}')
    for objective in real:
        if objective['role'] != 'base':
            continue
        for spawn in objective['spawns']:
            if spawn['kind'] != 'soldier' or TEAM_NUMBER.get(spawn['team'], 0) == 0:
                continue
            # the spawn's own position and its resolved alternates (an unresolved one is its GUID, a string)
            points = [spawn['position']] + [alt for alt in spawn['alternates'] if isinstance(alt, list)]
            for point in points:
                if point is not None:
                    lines.append(f'spawn {TEAM_NUMBER[spawn["team"]]} {int(objective["index"])} '
                                 f'{point[0]:.2f} {point[1]:.2f} {point[2]:.2f}')
    return '\n'.join(lines) + '\n'


def level_partition(game: str, level: str) -> tuple[bytes, str]:
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


def kind(instance: 'Element') -> str:
    """The instance's type name without its namespace."""
    return (instance.attr('type') or '').rsplit('.', 1)[-1]


def guid(instance: 'Element') -> str:
    """The instance's GUID, lower case (every instance of a partition has one)."""
    return cast(str, instance.attr('guid')).lower()


def scalar(instance: 'Element', name: str) -> str | None:
    field = instance.child(name)
    return None if field is None else dbx.value(field)


def number(instance: 'Element', name: str) -> float | None:
    field = instance.child(name)
    return None if field is None or field.kind != dbx.NUMBERS else dbx.floats(field)[0]


def refs(instance: 'Element', name: str) -> list[str]:
    """The references an array field holds: local GUIDs (lower case) or 'partition/guid' paths."""
    field = instance.child(name)
    if field is None:
        return []
    return [cast(str, member.attr('ref')).lower() for member in (field.children or ()) if member.attr('ref')]


def ref(instance: 'Element', name: str) -> str | None:
    field = instance.child(name)
    return cast(str, field.attr('ref')).lower() if field is not None and field.attr('ref') else None


def position(instance: 'Element') -> list[float] | None:
    """The Transform's translation (x, y, z), rounded to cm; None without a 4x4 Transform."""
    transform = instance.child('Transform')
    if transform is None or transform.kind != dbx.NUMBERS:
        return None
    matrix = dbx.floats(transform)
    return [round(matrix[12], 2), round(matrix[13], 2), round(matrix[14], 2)] if len(matrix) == 16 else None


def soldier_spawn(instance: 'Element', by_guid: dict[str, 'Element']) -> Spawn:
    alternates: list[list[float] | None | str] = []     # a position, or the GUID of an alternate not found
    for alternate_guid in refs(instance, 'AlternativeSpawnPoints'):
        alternate = by_guid.get(alternate_guid)
        alternates.append(position(alternate) if alternate is not None else alternate_guid)
    return dict(guid=guid(instance), kind='soldier', name=scalar(instance, 'Name') or '',
                team=scalar(instance, 'Team'), player_type=scalar(instance, 'PlayerType'),
                enabled=scalar(instance, 'Enabled'), location=scalar(instance, 'LocationNameSid') or '',
                position=position(instance), alternates=alternates)


def vehicle_spawn(instance: 'Element') -> Spawn:
    blueprint = ref(instance, 'Vehicle') or ref(instance, 'Template') or ''
    return dict(guid=guid(instance), kind='vehicle', name=scalar(instance, 'Name') or '',
                team=scalar(instance, 'Team'),
                vehicle=blueprint.rsplit('/', 2)[-2] if blueprint.count('/') >= 2 else blueprint,
                position=position(instance))


def spawn_map(game: str, level: str) -> LevelMap:
    """The level's objectives with the spawns each owns, and the soldier and vehicle spawns no objective owns."""
    data, bundle = level_partition(game, level)
    partition = dbx.parse(data)
    by_guid = {(e.attr('guid') or '').lower(): e for e in partition.instances}
    owned: set[str] = set()
    objectives: list[Objective] = []
    for e in partition.instances:
        if kind(e) != 'CapturePointEntityData':
            continue
        spawns: list[Spawn] = []
        for spawner_guid in refs(e, 'Spawners'):
            spawner = by_guid.get(spawner_guid)
            if spawner is None:
                spawns.append(dict(guid=spawner_guid, kind='external'))
            elif kind(spawner) == 'SoldierSpawnEntityData':
                spawns.append(soldier_spawn(spawner, by_guid))
            elif kind(spawner) == 'VehicleSpawnEntityData':
                spawns.append(vehicle_spawn(spawner))
            else:
                spawns.append(dict(guid=spawner_guid, kind=kind(spawner)))
            owned.add(spawner_guid)
        base = scalar(e, 'UnableToChangeTeam') == 'true'
        objectives.append(dict(guid=guid(e), name=scalar(e, 'Name') or '', role='base' if base else 'flag',
                               location=scalar(e, 'LocationName') or '', index=scalar(e, 'Index'),
                               initial_owner=scalar(e, 'InitialOwnerTeam'),
                               only_takeable_by=scalar(e, 'OnlyTakeableByTeam'),
                               capture_radius=number(e, 'CaptureRadius'), position=position(e), spawns=spawns))
    objectives.sort(key=lambda objective: (objective['role'] != 'base', int(objective['index'] or 0)))
    unowned = [soldier_spawn(e, by_guid) for e in partition.instances
               if kind(e) == 'SoldierSpawnEntityData' and (e.attr('guid') or '').lower() not in owned]
    # vehicles no objective owns (e.g. the carriers' own boats and planes): their team is their spawn's
    vehicles = [vehicle_spawn(e) for e in partition.instances
                if kind(e) == 'VehicleSpawnEntityData' and (e.attr('guid') or '').lower() not in owned]
    return dict(level=level, bundle=bundle, objectives=objectives, unowned_soldier_spawns=unowned,
                unowned_vehicle_spawns=vehicles)


def show(level_map: LevelMap) -> None:
    """Prints the level's map: objectives with their spawns and vehicles, then the spawns no objective owns."""
    print(f'== {level_map["level"]} ({level_map["bundle"]})')
    for objective in level_map['objectives']:
        soldiers = [spawn for spawn in objective['spawns'] if spawn['kind'] == 'soldier']
        vehicles = [spawn for spawn in objective['spawns'] if spawn['kind'] == 'vehicle']
        print(f'  {objective["role"]:4} #{objective["index"]} {objective["name"] or "-":28} '
              f'{objective["location"]:34} owner {objective["initial_owner"]:12}'
              f' at {objective["position"]}  radius {objective["capture_radius"]}')
        for spawn in soldiers:
            print(f'       soldier spawn {spawn["name"] or "-":30} {spawn["team"]:8} {spawn["player_type"]:14} '
                  f'at {spawn["position"]} + {len(spawn["alternates"])} alternates')
        if vehicles:
            counts: dict[str, int] = {}
            for vehicle in vehicles:
                counts[vehicle['vehicle']] = counts.get(vehicle['vehicle'], 0) + 1
            print('       vehicles: ' + ', '.join(f'{name} x{count}' for name, count in sorted(counts.items())))
    for vehicle in level_map['unowned_vehicle_spawns']:
        print(f'  (no objective) vehicle {vehicle["vehicle"]:24} {vehicle["team"] or "-":12} at {vehicle["position"]}')
    for spawn in level_map['unowned_soldier_spawns']:
        print(f'  (no objective) soldier spawn {spawn["name"] or "-"} {spawn["team"]} {spawn["player_type"]} '
              f'enabled {spawn["enabled"]} at {spawn["position"]}')


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='spawnmap.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('game', nargs='?', default=DEFAULT_GAME)
    parser.add_argument('levels', nargs='*')
    parser.add_argument('--json')
    parser.add_argument('--navflags', action='store_true',
                        help='also write work/levels/<level>/navkit/<level>.flags.txt')
    args = parser.parse_args(argv[1:])
    if args.game in LEVELS:                   # spawnmap.py wake_island_s: the default game folder
        args.levels, args.game = [args.game] + args.levels, DEFAULT_GAME
    game: str = args.game
    levels: list[str] = args.levels or [level for level in LEVELS
                                        if os.path.isdir(os.path.join(game, 'levels', level))]
    if not levels:
        raise SystemExit(f'no BF1943 levels in {game}: give the dist\\xenon folder of an extracted game')
    maps = [spawn_map(game, level) for level in levels]
    for level_map in maps:
        show(level_map)
    if args.json:
        write_whole(args.json, json.dumps(maps, indent=1))
    if args.navflags:
        # every level's text first (a level that cannot be written - a duplicate index - writes none), then each
        # file put in place whole: an existing flags file is never left empty or half written
        texts: list[tuple[str, str]] = [(level_map['level'], flags_text(level_map)) for level_map in maps]
        for level, text in texts:
            folder = os.path.join(WORK, 'levels', level, 'navkit')
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f'{level}.flags.txt')
            write_whole(path, text)
            print(f'wrote {path}')
    return 0


def write_whole(path: str, text: str) -> None:
    """text (UTF-8, LF) at path, replacing the file only once the new one is written whole."""
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('spawnmap', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
