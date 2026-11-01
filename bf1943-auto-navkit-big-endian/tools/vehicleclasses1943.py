r"""NavKit (big-endian): BF1943's vehicle classes as the vehicle navigation needs them - size and movement limits read
from the vehicles' own blueprints and collision, and the navigation parameters that follow from them.

  vehicleclasses1943.py [--game <dist\xenon>] [--json <out.json>]     prints the table (and writes it as JSON)

  import vehicleclasses1943 as vc
  vc.CLASSES['jeep']                 # the navigation parameters of a class (radius, height, step, slope, ...)
  vc.read(game)                      # {vehicle name: what its blueprint and collision give}

Labels: [O] read from the game's data (the blueprint partition or its *_havok.res; the source is named), [E] an
estimate with its basis, [H] a hypothesis, [U] not known. BFBC2 is a lead only: its vehicle graphs used one radius,
2.5 m, for every vehicle (KYNAPSE_SPEC 4.3).

What the data gives (read 2026-10-26, Wake Island's bundles; every MP level shares levels-common-00.fbrb):
  - size: the collision's bounding box (the vehicle's *_havok.res: meshes and convex shapes) [O];
  - wheels and tracks: wheel positions (WheelComponentData Transform), radius and spring (WheelConfigData) [O];
  - slope: WheelConfigData SlopeGripMinAngle / SlopeGripMaxAngle (jeeps 18-20 / 25 deg, tanks 25 / 52 deg) [O]. That
    grip fades between the two angles is [H] (the name; DICE's code is not read);
  - speed: GearboxConfigData ForwardGearSpeeds, the last gear's (jeeps 100, tanks 50, LCVP 80, planes 130) [O]; the
    unit (km/h) is [H];
  - entry and exit points: PlayerEntryComponentData (EntryRadius) and VehicleExitPointComponentData [O];
  - LCVP: BoatFloatPhysicsData (Depth / Width / Length 0: taken from the body [H]), a water-jet engine
    (JetEngineConfigData IsWaterJetEngine) [O]; its exits at the bow ramp (+-1, 0.5, 5.5) [O];
  - not in the data: a steering angle (WheelConfigData has only SteeringAngleIndex, an index into a table not found)
    [U], a step height [U], the vehicles' AIData (the entries' AIData references resolve to no partition in the
    bundles) [U].
"""
import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Container, Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import navkitjson1943 as nj  # noqa: E402

if TYPE_CHECKING:
    from bfbc2core.game1943 import Game

type Stamp = dict[str, Any]                     # a vehicle step's stamp in navkit.json 'vehicles' (inputs, record)
type Box = tuple[list[float], list[float]]      # (min, max) corners, x y z

# vehicle name: (class, blueprint partition, collision *_havok.res)
VEHICLES = {
    'willys-mb': ('jeep', 'objects/vehicles/beach/willys-mb/willys-mb', 'objects/vehicles/beach/willys-mb/willys-mb_havok.res'),
    'kurogane': ('jeep', 'objects/vehicles/beach/kurogane/kurogane', 'objects/vehicles/beach/kurogane/kurogane_mesh_havok.res'),
    'm4-sherman': ('tank', 'objects/vehicles/beach/m4-sherman/m4-sherman', 'objects/vehicles/beach/m4-sherman/us_shermanm4_tank_havok.res'),
    'type97-chiha': ('tank', 'objects/vehicles/beach/type97-chiha/type97-chiha', 'objects/vehicles/beach/type97-chiha/chiha_mb_havok.res'),
    'lcvp-landingcraft': ('lcvp', 'objects/vehicles/beach/lcvp-landingcraft/lcvp-landingcraft', 'objects/vehicles/beach/lcvp-landingcraft/gen_landingcraft_havok.res'),
    'corsair': ('plane', 'objects/vehicles/beach/us_corsair/corsair', 'objects/vehicles/beach/us_corsair/us_corsair_havok.res'),
    'zero_fighter': ('plane', 'objects/vehicles/beach/jap_zero_fighter/zero_fighter', 'objects/vehicles/beach/jap_zero_fighter/zero_fighter_havok.res'),
}

# The navigation parameters per class. Each value: (value, label and basis). The [O] values are checked against the
# blueprints by read() / tests; the margins are ours.
MARGIN = 0.45            # m added to the half-width: steering error and the body's corners at small turns [E]
CLASSES: dict[str, dict[str, Any]] = {
    'jeep': {
        'vehicles': ['willys-mb', 'kurogane'],
        'half_width': (1.06, '[O] collision box: willys-mb x -1.06..0.84 (the wider side), kurogane -0.87..0.87'),
        'half_length': (2.1, '[O] collision box: willys-mb z -1.97..1.83, kurogane -2.16..1.97'),
        'radius': (1.5, '[E] half-width 1.06 [O] + 0.45 m margin; the clearance disc of the samples'),
        'height': (2.0, '[O] top of the collision 1.54-1.73 m over its base, the wheels 0.4-0.45 m [O]; + margin'),
        'step': (0.3, '[E] three quarters of the wheel radius 0.4-0.45 m [O]'),
        'slope': (25.0, '[O] WheelConfigData SlopeGripMaxAngle 25 (SlopeGripMinAngle 18-20)'),
        'water_depth': (0.8, '[E] fording depth; the jeeps have BoxFloatPhysicsData (Density 0.5, FilledDensity 1.2) [O]'),
        'speed': (100 / 3.6, '[O] last forward gear 100 (km/h [H])'),
        'turn_radius': (4.5, '[E] wheelbase 2.25-2.38 m [O] at about 27 deg of steering (the angle is [U])'),
        'pitch': (0.8, 'the soldier build\'s 0.8 m grid (the samples line up with it)'),
        'node_spacing': (8, 'cells between route nodes (6.4 m)'),
    },
    'tank': {
        'vehicles': ['m4-sherman', 'type97-chiha'],
        'half_width': (1.37, '[O] collision box: m4-sherman x -1.29..1.37, type97-chiha -1.22..1.22'),
        'half_length': (3.0, '[O] collision box: m4-sherman z -3.01..2.96, type97-chiha -2.86..2.76'),
        'radius': (1.8, '[E] half-width 1.37 [O] + 0.45 m margin'),
        'height': (2.9, '[O] top of the collision 2.55-2.78 m over its base; + margin'),
        'step': (0.6, '[E] tracked; climbs about its road wheels\' height (BFBC2 lead)'),
        'slope': (40.0, '[E] between WheelConfigData SlopeGripMinAngle 25 and SlopeGripMaxAngle 52 [O]'),
        'water_depth': (1.2, '[E] fording depth'),
        'speed': (50 / 3.6, '[O] last forward gear 50 (km/h [H])'),
        'turn_radius': (0.0, '[O] SteeringType 2 with TrackedTurnSpeedLimit: tracked, turns in place [H]'),
        'pitch': (0.8, 'the soldier build\'s 0.8 m grid'),
        'node_spacing': (8, 'cells between route nodes (6.4 m)'),
    },
    'lcvp': {
        'vehicles': ['lcvp-landingcraft'],
        'half_width': (1.67, '[O] collision box x -1.67..1.67'),
        'half_length': (4.7, '[O] collision box z -4.56..4.71'),
        'radius': (2.5, '[E] half-width 1.67 [O] + 0.8 m: kept off rocks and hulls'),
        'height': (2.83, '[O] collision box y -0.44..2.39'),
        'draft': (0.9, '[E] collision bottom 0.44 m under the origin [O], floating with Density 0.5 [O]; + margin'),
        'beach_depth': (0.5, '[E] the bow grounds where the water is this shallow: the landing zones'),
        'bow': (5.5, '[O] exit points at the bow ramp: (+-1, 0.5, 5.5)'),
        'speed': (80 / 3.6, '[O] last forward gear 80 (km/h [H]); BOT_MODULE_DESIGN 4.5 guessed 10 m/s'),
        'turn_radius': (8.0, '[E] water jet, IsTurnable [O]; about the boat\'s length'),
        'pitch': (2.0, 'sea grid cell (m)'),
        'node_spacing': (5, 'cells between route nodes (10 m)'),
    },
    'plane': {
        'vehicles': ['corsair', 'zero_fighter'],
        'half_width': (6.0, '[O] collision box: wingspan 11.9 (corsair), 12.0 (zero_fighter)'),
        'half_length': (5.0, '[O] collision box z -5.81..3.24 (zero), -5.74..3.85 (corsair)'),
        'radius': (25.0, '[E] clearance kept from terrain and objects in flight'),
        'cruise_agl': (60.0, '[E] cruise height over the altitude floor (design 4.2)'),
        'speed': (130 / 3.6, '[O] last forward gear 130 (km/h [H]); flight speed itself [U]'),
        'takeoff_run': (120.0, '[E] the carrier decks are 235 m [O]; the take-off roll is not in the data [U]'),
        'turn_radius': (150.0, '[E] at cruise speed'),
        'pitch': (16.0, 'altitude-floor grid cell (m)'),
        'node_spacing': (4, 'cells between route nodes (64 m)'),
    },
}
CLASS_IDS = {'jeep': 1, 'tank': 2, 'lcvp': 3, 'plane': 4}       # the RSNV layer / OFFM vehicle class of each file


def value(cls: str, key: str) -> Any:
    """A class's navigation parameter (CLASSES[cls][key] without its label)."""
    return CLASSES[cls][key][0]


# ---- what each vehicle step was made from (navkit.json 'vehicles') -------------------------------------------------
# 2026-10-30: the vehicle collision was made again only when its file was missing, and the samples reused whatever was
# on disk, so a new preparation (another game copy, other objects) kept the old collision and samples. Each step now
# stamps navkit.json: 'vehicles': {'collision': stamp, 'samples': {class: stamp}, 'worlds': {class: stamp}}, every
# stamp the preparation's digest and game folder ('preparation', 'game'), a class's also the digest of its parameters
# ('params': for the samples only those the sampler reads, SAMPLING_PARAMS; for a world every one), a world's also the
# SHA-256 of the soldier world it was built on ('soldier'), or 'absent' with why when the level has no such world (no
# spawns of the class, no water). navkit.py prepare writes a new navkit.json (every stamp goes); a soldier sampling or
# build drops the worlds' stamps (navkit.invalidate); the next step needs its input's stamp to be current, and refuses
# or makes it again. Every write is a fresh read-modify-write (navkitjson1943.update).

class StaleError(RuntimeError):
    """A vehicle step's input is of another preparation, game copy, class parameters or soldier world."""


SAMPLING_PARAMS = ('radius', 'step', 'height', 'pitch')   # what navsample1943 is given (groundnav1943.sample_command)


def params_id(cls: str, keys: Container[str] | None = None) -> str:
    """The digest of a class's navigation parameters: every value of CLASSES[cls], or only keys."""
    import hashlib
    values = {key: (entry[0] if isinstance(entry, tuple) else entry) for key, entry in CLASSES[cls].items()
              if keys is None or key in keys}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def sampling_params_id(cls: str) -> str:
    """The digest of the parameters the class's samples depend on (2026-10-30: every parameter was hashed, so a new
    speed or node spacing sampled again for nothing)."""
    return params_id(cls, SAMPLING_PARAMS)


def inputs(st: 'nj.Settings', lv: str, cls: str | None = None, sampling: bool = False, **extra: Any) -> Stamp:
    """The stamp a vehicle step of the current preparation makes (and the one its next step needs); sampling: the
    class's samples' (their parameters only those the sampler reads)."""
    if not st.get('preparation'):
        raise StaleError(f'{lv}: prepared without a preparation stamp (an older NavKit): prepare again '
                         f'(navkit.py prepare {lv})')
    out = {'preparation': st['preparation'], 'game': st.get('game')}
    if cls is not None:
        out.update({'class': cls, 'params': sampling_params_id(cls) if sampling else params_id(cls)})
    out.update(extra)
    return out


def stamp(st: Mapping[str, Any], kind: str, cls: str | None = None) -> Any:
    """A vehicle step's stamp in navkit.json ('collision'; 'samples' or 'worlds' with a class), or None."""
    of_kind = (st.get('vehicles') or {}).get(kind)
    return (of_kind or {}).get(cls) if cls is not None else of_kind


def current(st: Mapping[str, Any], kind: str, want: Mapping[str, Any], cls: str | None = None) -> bool:
    """Whether the step's stamp in st is want (its time and what it found aside)."""
    have = stamp(st, kind, cls)
    return have is not None and all(have.get(key) == wanted for key, wanted in want.items())


def soldier_changed(st: Mapping[str, Any], lv: str, soldier: str) -> str | None:
    """Why a world built on the soldier world whose SHA-256 is soldier may not be stamped now (None when it may): that
    file must still be the export of the current, passed build (navkit.json 'exported' and 'build_from')."""
    built = st.get('build_from') or {}
    exported = st.get('exported') or {}
    if 'built' not in st or not built.get('hpd_sha256') or exported.get('hpd_sha256') != built['hpd_sha256'] \
            or exported.get('sha256') != soldier or not nj.check_passed(st):
        return (f'{lv}: the soldier world changed while the vehicle world was built (built, checked or exported again): '
                f'it is not stamped; build it again (navexport_vehicles1943.py {lv})')
    return None


def record(lv: str, kind: str, value: Mapping[str, Any], cls: str | None = None,
           soldier_path: str | None = None) -> 'nj.Settings':
    """Stamps a finished step; nothing when navkit.json now holds another preparation (prepared meanwhile). A world
    (a value with 'soldier') built on the default soldier world (soldier_path None or that file, as soldier_world
    tells it) is refused with StaleError, and not stamped, when that file is no longer the export of the current
    build: 2026-10-30 second review, a world finished after the soldier was built again was stamped, and the vehicle
    check took it as current while the old export was still on disk."""
    import time
    value = dict(value, time=time.strftime('%Y-%m-%d %H:%M'))
    refused: list[str] = []
    default_soldier = soldier_path is None or nj.same_file(soldier_path,
                                                           os.path.join(nj.paths(lv)['navkit'], f'{lv}.rsnv'))

    def change(st: 'nj.Settings') -> None:
        if st.get('preparation') != value['preparation']:
            return
        if 'soldier' in value and default_soldier:
            why = soldier_changed(st, lv, value['soldier'])
            if why:
                refused.append(why)
                return
        vehicles = st.setdefault('vehicles', {})
        if cls is None:
            vehicles[kind] = value
        else:
            vehicles.setdefault(kind, {})[cls] = value
    st = nj.update(lv, change)
    if refused:
        raise StaleError(refused[0])
    return st


def drop(lv: str, kind: str, cls: str | None = None) -> None:
    """Removes a step's stamp before it runs: an interrupted run leaves none."""
    def change(st: 'nj.Settings') -> None:
        vehicles = st.get('vehicles') or {}
        if cls is None:
            vehicles.pop(kind, None)
        else:
            (vehicles.get(kind) or {}).pop(cls, None)
    if os.path.isfile(nj.paths(lv)['settings']):
        nj.update(lv, change)


def absent(lv: str, cls: str, want: Mapping[str, Any], path: str, why: str,
           log: Callable[[str], object] = print) -> None:
    """The level has no world of the class (its data says so: no spawns of it, no water): a world file of an earlier
    run goes, and the stamp says why (navexport_vehicles1943.check tells this from a world not built)."""
    if os.path.isfile(path):
        os.remove(path)
        log(f'{lv}: {os.path.basename(path)} of an earlier run removed ({why})')
    record(lv, 'worlds', dict(want, absent=why), cls)


def prepared_game(st: Mapping[str, Any], game: str | None, lv: str) -> str | None:
    """The game copy the level was prepared from; a different one given is refused (navkit.same_game's rule: the
    same folder by identity, navkitjson1943.same_file)."""
    want = st.get('game')
    if game is None:
        return want
    if not nj.same_file(want, game):
        raise StaleError(f'{lv}: prepared from {want}, not {game}: use that copy, or prepare again (navkit.py prepare {lv})')
    return game


def soldier_world(st: Mapping[str, Any], lv: str, path: str) -> str:
    """The SHA-256 of the soldier world a vehicle world is built on. Refused when it is missing, when the soldier build
    is not of the current preparation, or (the default <level>.rsnv, by file identity) when the file is not the export
    of the current build: the export's stamp names the build by its .hpd's SHA-256 (navkit.json 'build_from'
    'hpd_sha256'; 2026-10-30: by its 'built' minute, which a quick rebuild repeats)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f'{lv}: no soldier world (missing {path}): export it (navexport1943.py {lv})')
    built = st.get('build_from') or {}
    if built.get('preparation') != st.get('preparation') or 'built' not in st or not built.get('hpd_sha256'):
        raise StaleError(f'{lv}: the soldier build is not of the current preparation: build and export it again '
                         f'(navkit.py build {lv}; navexport1943.py {lv})')
    if not nj.check_passed(st):              # 2026-10-30 review: a build whose check failed was exported and stamped
        raise StaleError(f'{lv}: the soldier build\'s check did not pass ({st.get("checked") or "not checked"}): no '
                         f'vehicle world is built on it (navkit.py check {lv})')
    digest = nj.sha256(path)
    exported = st.get('exported') or {}
    default = os.path.join(nj.paths(lv)['navkit'], f'{lv}.rsnv')
    if nj.same_file(path, default) and (exported.get('sha256') != digest
                                        or exported.get('hpd_sha256') != built['hpd_sha256']):
        raise StaleError(f'{lv}: {path} is not the export of the current build: export again (navexport1943.py {lv})')
    return digest


def _fields(xml: str, name: str) -> list[str]:
    """The text of every <field name="name"> in a partition's XML."""
    return re.findall(r'<field name="' + re.escape(name) + r'">([^<]*)</field>', xml)


def _instances(xml: str, typename: str) -> list[str]:
    """The XML of every instance of a type (its name without the namespace)."""
    return re.findall(r'<instance[^>]*type="[^"]*\.' + typename + r'"[^>]*>.*?</instance>', xml, re.S)


def _translation(inst: str) -> list[float] | None:
    """An instance's Transform translation (x, y, z), or None."""
    match = re.search(r'<complex name="Transform">([^<]*)</complex>', inst)
    if not match:
        return None
    values = [float(t) for t in match.group(1).split('/')]
    return [round(x, 3) for x in values[12:15]] if len(values) == 16 else None


def collision_box(game: 'Game', res: str) -> Box | None:
    """(min, max) of a vehicle's collision (meshes and convex shapes of its *_havok.res), or None."""
    import havok461
    import hkpack
    data = game.data(res)
    if not data:
        return None
    packfile = hkpack.Packfile(data, classes=havok461.CLASSES)
    points = []
    for offset in packfile.objects_of('hkStorageExtendedMeshShapeMeshSubpartStorage'):
        vertices, _triangles = havok461.triangles(packfile, offset)
        if vertices:
            points.append(np.asarray(vertices, float)[:, :3])
    for offset in packfile.objects_of('hkConvexVerticesShape'):
        box, _, _ = havok461.convex_box(packfile, offset)
        if box:
            points.append(np.asarray(box, float).reshape(-1, 3))
    if not points:
        return None
    every = np.concatenate(points)
    return every.min(0).round(3).tolist(), every.max(0).round(3).tolist()


def read(game_dir: str | None = None, level: str = 'wake_island_s') -> dict[str, dict[str, Any]]:
    """{vehicle: {...}} from the blueprints and collision of your copy (every value [O]); hashes of the inputs."""
    import hashlib
    from bfbc2core import dbx, game1943
    level_game = game1943.Game(game1943.find_game(game_dir), level)
    out: dict[str, dict[str, Any]] = {}
    for name, (cls, part, res) in VEHICLES.items():
        data = level_game.data(part + '.dbx')
        if data is None:
            out[name] = {'class': cls, 'missing': part}
            continue
        xml = dbx.to_xml(data)
        xml = xml if isinstance(xml, str) else xml.decode('utf-8')

        def floats(field: str) -> list[float]:
            return [float(text) for text in _fields(xml, field)]
        wheels: dict[str | int, list[float] | None] = {}     # wheel name (or its number): its local position
        for inst in _instances(xml, 'WheelComponentData'):
            name_match = re.search(r'<field name="Name">([^<]*)<', inst)
            wheels[name_match.group(1) if name_match else len(wheels)] = _translation(inst)
        exits = [_translation(inst) for inst in _instances(xml, 'VehicleExitPointComponentData')]
        entries = []
        for inst in _instances(xml, 'PlayerEntryComponentData') + _instances(xml, 'LandingGearEntryComponentData'):
            name_match = re.search(r'<field name="Name">([^<]*)<', inst)
            radius_match = re.search(r'<field name="EntryRadius">([^<]*)<', inst)
            entries.append({'name': name_match.group(1) if name_match else '', 'local': _translation(inst),
                            'radius': float(radius_match.group(1)) if radius_match else None})
        gears = [[float(speed) for speed in speeds.split('/')]
                 for speeds in re.findall(r'<array name="ForwardGearSpeeds">([^<]*)<', xml)]
        box = collision_box(level_game, res)
        collision_data = level_game.data(res)
        spring_lengths = re.findall(r'<complex name="Spring">\s*<field name="Length">([^<]*)<', xml)
        out[name] = {
            'class': cls, 'blueprint': part + '.dbx', 'sha256': hashlib.sha256(data).hexdigest(),
            'collision': res,
            'collision_sha256': hashlib.sha256(collision_data).hexdigest() if collision_data else None,
            'collision_box': box,
            'size': [round(b - a, 3) for a, b in zip(*box)] if box else None,
            'body_mass': floats('BodyMass')[:1],
            'wheel_radius': floats('Radius')[:2] if wheels else [],
            'spring_length': [float(length) for length in spring_lengths],
            'slope_grip_min': floats('SlopeGripMinAngle'), 'slope_grip_max': floats('SlopeGripMaxAngle'),
            'steering_type': [int(text) for text in _fields(xml, 'SteeringType')],
            'forward_gear_speeds': gears, 'top_gear_speed': max((max(speeds) for speeds in gears), default=None),
            'wheels': wheels, 'entries': entries, 'exits': exits,
            'water_jet': 'true' in _fields(xml, 'IsWaterJetEngine'),
            'float_physics': [t for t in ('BoatFloatPhysicsData', 'BoxFloatPhysicsData') if _instances(xml, t)],
        }
        if len(wheels) >= 4:
            wheel_pos = np.array([at for wheel, at in wheels.items() if str(wheel).startswith('Wheel ') and at], float)
            if len(wheel_pos) >= 4:
                out[name]['wheelbase'] = round(float(wheel_pos[:, 2].max() - wheel_pos[:, 2].min()), 3)
                out[name]['track'] = round(float(wheel_pos[:, 0].max() - wheel_pos[:, 0].min()), 3)
    return out


def check(table: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Problems where the [O] parameters of CLASSES disagree with the data read ([] when none)."""
    problems = []
    for cls, params in CLASSES.items():
        # the wider side (x) of the class's collision boxes
        half_width = max((max(-vehicle['collision_box'][0][0], vehicle['collision_box'][1][0])
                          for _name, vehicle in table.items()
                          if vehicle.get('class') == cls and vehicle.get('collision_box')), default=None)
        if half_width is not None and abs(half_width - params['half_width'][0]) > 0.06:
            problems.append(f'{cls}: half-width {params["half_width"][0]} against the collision\'s {half_width:.2f}')
        if cls in ('jeep', 'tank'):
            grip = max((max(vehicle['slope_grip_max']) for vehicle in table.values()
                        if vehicle.get('class') == cls and vehicle.get('slope_grip_max')), default=None)
            if cls == 'jeep' and grip is not None and grip != params['slope'][0]:
                problems.append(f'jeep: slope {params["slope"][0]} against SlopeGripMaxAngle {grip}')
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='vehicleclasses1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game')
    parser.add_argument('--json')
    args = parser.parse_args(argv[1:])
    table = read(args.game)
    for name, vehicle in table.items():
        print(f'{name:18s} {vehicle["class"]:6s} size {vehicle.get("size")} mass {vehicle.get("body_mass")} slope grip '
              f'{vehicle.get("slope_grip_min")}..{vehicle.get("slope_grip_max")} '
              f'top gear {vehicle.get("top_gear_speed")} wheelbase {vehicle.get("wheelbase")} '
              f'track {vehicle.get("track")} entries {len(vehicle.get("entries", []))} '
              f'exits {len(vehicle.get("exits", []))}')
    for cls, params in CLASSES.items():
        print(f'{cls}: ' + ', '.join(f'{key} {entry[0]:g}' for key, entry in params.items() if key != 'vehicles'))
    problems = check(table)
    for problem in problems:
        print('MISMATCH ' + problem)
    if args.json:
        classes = {cls: {key: (list(entry) if isinstance(entry, tuple) else entry) for key, entry in params.items()}
                   for cls, params in CLASSES.items()}
        with open(args.json, 'w', encoding='utf-8', newline='') as f:
            json.dump({'vehicles': table, 'classes': classes}, f, indent=1)
    return 1 if problems else 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('vehicleclasses1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
