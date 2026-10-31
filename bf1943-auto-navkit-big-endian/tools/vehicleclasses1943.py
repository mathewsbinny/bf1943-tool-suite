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
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
import navkitjson1943 as nj  # noqa: E402

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
CLASSES = {
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


def value(cls, key):
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


def params_id(cls, keys=None):
    """The digest of a class's navigation parameters: every value of CLASSES[cls], or only keys."""
    import hashlib
    vals = {k: (v[0] if isinstance(v, tuple) else v) for k, v in CLASSES[cls].items() if keys is None or k in keys}
    return hashlib.sha256(json.dumps(vals, sort_keys=True).encode()).hexdigest()


def sampling_params_id(cls):
    """The digest of the parameters the class's samples depend on (2026-10-30: every parameter was hashed, so a new
    speed or node spacing sampled again for nothing)."""
    return params_id(cls, SAMPLING_PARAMS)


def inputs(st, lv, cls=None, sampling=False, **extra):
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


def stamp(st, kind, cls=None):
    v = (st.get('vehicles') or {}).get(kind)
    return (v or {}).get(cls) if cls is not None else v


def current(st, kind, want, cls=None):
    """Whether the step's stamp in st is want (its time and what it found aside)."""
    have = stamp(st, kind, cls)
    return have is not None and all(have.get(k) == v for k, v in want.items())


def soldier_changed(st, lv, soldier):
    """Why a world built on the soldier world whose SHA-256 is soldier may not be stamped now (None when it may): that
    file must still be the export of the current, passed build (navkit.json 'exported' and 'build_from')."""
    built = st.get('build_from') or {}
    ex = st.get('exported') or {}
    if 'built' not in st or not built.get('hpd_sha256') or ex.get('hpd_sha256') != built['hpd_sha256']             or ex.get('sha256') != soldier or not nj.check_passed(st):
        return (f'{lv}: the soldier world changed while the vehicle world was built (built, checked or exported again): '
                f'it is not stamped; build it again (navexport_vehicles1943.py {lv})')
    return None


def record(lv, kind, value, cls=None, soldier_path=None):
    """Stamps a finished step; nothing when navkit.json now holds another preparation (prepared meanwhile). A world
    (a value with 'soldier') built on the default soldier world (soldier_path None or that file, as soldier_world
    tells it) is refused with StaleError, and not stamped, when that file is no longer the export of the current
    build: 2026-10-30 second review, a world finished after the soldier was built again was stamped, and the vehicle
    check took it as current while the old export was still on disk."""
    import time
    value = dict(value, time=time.strftime('%Y-%m-%d %H:%M'))
    refused = []
    default_soldier = soldier_path is None or nj.same_file(soldier_path, os.path.join(nj.paths(lv)['navkit'], f'{lv}.rsnv'))

    def change(st):
        if st.get('preparation') != value['preparation']:
            return
        if 'soldier' in value and default_soldier:
            why = soldier_changed(st, lv, value['soldier'])
            if why:
                refused.append(why)
                return
        v = st.setdefault('vehicles', {})
        if cls is None:
            v[kind] = value
        else:
            v.setdefault(kind, {})[cls] = value
    st = nj.update(lv, change)
    if refused:
        raise StaleError(refused[0])
    return st


def drop(lv, kind, cls=None):
    """Removes a step's stamp before it runs: an interrupted run leaves none."""
    def change(st):
        v = st.get('vehicles') or {}
        if cls is None:
            v.pop(kind, None)
        else:
            (v.get(kind) or {}).pop(cls, None)
    if os.path.isfile(nj.paths(lv)['settings']):
        nj.update(lv, change)


def absent(lv, cls, want, path, why, log=print):
    """The level has no world of the class (its data says so: no spawns of it, no water): a world file of an earlier
    run goes, and the stamp says why (navexport_vehicles1943.check tells this from a world not built)."""
    if os.path.isfile(path):
        os.remove(path)
        log(f'{lv}: {os.path.basename(path)} of an earlier run removed ({why})')
    record(lv, 'worlds', dict(want, absent=why), cls)


def prepared_game(st, game, lv):
    """The game copy the level was prepared from; a different one given is refused (navkit.same_game's rule: the
    same folder by identity, navkitjson1943.same_file)."""
    want = st.get('game')
    if game is None:
        return want
    if not nj.same_file(want, game):
        raise StaleError(f'{lv}: prepared from {want}, not {game}: use that copy, or prepare again (navkit.py prepare {lv})')
    return game


def soldier_world(st, lv, path):
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
    h = nj.sha256(path)
    ex = st.get('exported') or {}
    default = os.path.join(nj.paths(lv)['navkit'], f'{lv}.rsnv')
    if nj.same_file(path, default) and (ex.get('sha256') != h or ex.get('hpd_sha256') != built['hpd_sha256']):
        raise StaleError(f'{lv}: {path} is not the export of the current build: export again (navexport1943.py {lv})')
    return h


def _fields(xml, name):
    return re.findall(r'<field name="' + re.escape(name) + r'">([^<]*)</field>', xml)


def _instances(xml, typename):
    return re.findall(r'<instance[^>]*type="[^"]*\.' + typename + r'"[^>]*>.*?</instance>', xml, re.S)


def _translation(inst):
    m = re.search(r'<complex name="Transform">([^<]*)</complex>', inst)
    if not m:
        return None
    v = [float(t) for t in m.group(1).split('/')]
    return [round(x, 3) for x in v[12:15]] if len(v) == 16 else None


def collision_box(game, res):
    """(min, max) of a vehicle's collision (meshes and convex shapes of its *_havok.res), or None."""
    import havok461
    import hkpack
    data = game.data(res)
    if not data:
        return None
    pf = hkpack.Packfile(data, classes=havok461.CLASSES)
    pts = []
    for off in pf.objects_of('hkStorageExtendedMeshShapeMeshSubpartStorage'):
        v, t = havok461.triangles(pf, off)
        if v:
            pts.append(np.asarray(v, float)[:, :3])
    for off in pf.objects_of('hkConvexVerticesShape'):
        box, _, _ = havok461.convex_box(pf, off)
        if box:
            pts.append(np.asarray(box, float).reshape(-1, 3))
    if not pts:
        return None
    p = np.concatenate(pts)
    return p.min(0).round(3).tolist(), p.max(0).round(3).tolist()


def read(game_dir=None, level='wake_island_s'):
    """{vehicle: {...}} from the blueprints and collision of your copy (every value [O]); hashes of the inputs."""
    import hashlib
    from bfbc2core import dbx, game1943
    g = game1943.Game(game1943.find_game(game_dir), level)
    out = {}
    for name, (cls, part, res) in VEHICLES.items():
        data = g.data(part + '.dbx')
        if data is None:
            out[name] = {'class': cls, 'missing': part}
            continue
        xml = dbx.to_xml(data)
        xml = xml if isinstance(xml, str) else xml.decode('utf-8')
        floats = lambda n: [float(v) for v in _fields(xml, n)]
        wheels = {}
        for inst in _instances(xml, 'WheelComponentData'):
            nm = re.search(r'<field name="Name">([^<]*)<', inst)
            wheels[nm.group(1) if nm else len(wheels)] = _translation(inst)
        exits = [_translation(i) for i in _instances(xml, 'VehicleExitPointComponentData')]
        entries = []
        for inst in _instances(xml, 'PlayerEntryComponentData') + _instances(xml, 'LandingGearEntryComponentData'):
            nm = re.search(r'<field name="Name">([^<]*)<', inst)
            r = re.search(r'<field name="EntryRadius">([^<]*)<', inst)
            entries.append({'name': nm.group(1) if nm else '', 'local': _translation(inst),
                            'radius': float(r.group(1)) if r else None})
        gears = [[float(v) for v in s.split('/')] for s in re.findall(r'<array name="ForwardGearSpeeds">([^<]*)<', xml)]
        box = collision_box(g, res)
        out[name] = {
            'class': cls, 'blueprint': part + '.dbx', 'sha256': hashlib.sha256(data).hexdigest(),
            'collision': res, 'collision_sha256': hashlib.sha256(g.data(res)).hexdigest() if g.data(res) else None,
            'collision_box': box,
            'size': [round(b - a, 3) for a, b in zip(*box)] if box else None,
            'body_mass': floats('BodyMass')[:1],
            'wheel_radius': floats('Radius')[:2] if wheels else [],
            'spring_length': [float(v) for v in re.findall(r'<complex name="Spring">\s*<field name="Length">([^<]*)<', xml)],
            'slope_grip_min': floats('SlopeGripMinAngle'), 'slope_grip_max': floats('SlopeGripMaxAngle'),
            'steering_type': [int(v) for v in _fields(xml, 'SteeringType')],
            'forward_gear_speeds': gears, 'top_gear_speed': max((max(gs) for gs in gears), default=None),
            'wheels': wheels, 'entries': entries, 'exits': exits,
            'water_jet': 'true' in _fields(xml, 'IsWaterJetEngine'),
            'float_physics': [t for t in ('BoatFloatPhysicsData', 'BoxFloatPhysicsData') if _instances(xml, t)],
        }
        if len(wheels) >= 4:
            pos = np.array([v for k, v in wheels.items() if str(k).startswith('Wheel ') and v], float)
            if len(pos) >= 4:
                out[name]['wheelbase'] = round(float(pos[:, 2].max() - pos[:, 2].min()), 3)
                out[name]['track'] = round(float(pos[:, 0].max() - pos[:, 0].min()), 3)
    return out


def check(table):
    """Problems where the [O] parameters of CLASSES disagree with the data read ([] when none)."""
    problems = []
    for cls, p in CLASSES.items():
        hw = max((max(-v['collision_box'][0][0], v['collision_box'][1][0]) for n, v in table.items()
                  if v.get('class') == cls and v.get('collision_box')), default=None)
        if hw is not None and abs(hw - p['half_width'][0]) > 0.06:
            problems.append(f'{cls}: half-width {p["half_width"][0]} against the collision\'s {hw:.2f}')
        if cls in ('jeep', 'tank'):
            grip = max((max(v['slope_grip_max']) for v in table.values() if v.get('class') == cls and v.get('slope_grip_max')), default=None)
            if cls == 'jeep' and grip is not None and grip != p['slope'][0]:
                problems.append(f'jeep: slope {p["slope"][0]} against SlopeGripMaxAngle {grip}')
    return problems


def main(argv):
    ap = argparse.ArgumentParser(prog='vehicleclasses1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--game')
    ap.add_argument('--json')
    a = ap.parse_args(argv[1:])
    table = read(a.game)
    for name, v in table.items():
        print(f'{name:18s} {v["class"]:6s} size {v.get("size")} mass {v.get("body_mass")} slope grip '
              f'{v.get("slope_grip_min")}..{v.get("slope_grip_max")} top gear {v.get("top_gear_speed")} '
              f'wheelbase {v.get("wheelbase")} track {v.get("track")} entries {len(v.get("entries", []))} exits {len(v.get("exits", []))}')
    for cls, p in CLASSES.items():
        print(f'{cls}: ' + ', '.join(f'{k} {v[0]:g}' for k, v in p.items() if k != 'vehicles'))
    problems = check(table)
    for pr in problems:
        print('MISMATCH ' + pr)
    if a.json:
        with open(a.json, 'w', encoding='utf-8', newline='') as f:
            json.dump({'vehicles': table, 'classes': {c: {k: (list(v) if isinstance(v, tuple) else v) for k, v in p.items()}
                                                       for c, p in CLASSES.items()}}, f, indent=1)
    return 1 if problems else 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('vehicleclasses1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
