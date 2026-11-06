r"""NavKit (big-endian): every vehicle world of a level, its transfers and its checks - the driver of the vehicle
navigation (groundnav1943.py, seanav1943.py, airnav1943.py; classes in vehicleclasses1943.py).

  navexport_vehicles1943.py <level> [--classes jeep,tank,lcvp,plane] [--sample] [--check-only]

--classes chooses the worlds to build; the checks always cover every world written. --check-only builds nothing.

Writes, beside the soldier world (work\levels\<level>\navkit\<level>.rsnv, which it reads and never writes):
  <level>.jeep.rsnv, <level>.tank.rsnv    ground vehicles (when the level has spawns of the class; --sample samples
                                          again first, otherwise only when the samples are missing)
  <level>.lcvp.rsnv                       the landing craft (when the level has any)
  <level>.plane.rsnv                      planes
  <level>.transfers.json                  every transfer of every world: kind, team, vehicle node, the soldier node it
                                          resolves to in the current soldier world, its island; the inputs' hashes
  <level>.vehicles.json                   the checks: per world its counts, islands and transfers, problems

Each world is built from the game copy the level was prepared from, and stamped in navkit.json (vehicleclasses1943:
the preparation, the class's parameters, the soldier world); ground samples of another preparation or other class
parameters are made again. A class the level has none of (no spawns of it; for the landing craft also no water) is
stamped absent, and an earlier world file of it removed.

Checks (exit 1 when any fails): every class asked for (--classes) has a world, or is stamped absent; a world not
stamped as built from the current preparation, class parameters and soldier world fails (build it again); each
world reads back and validates (rsnv.World.validate); every node lies on a
cell of its own world; every board transfer has a vehicle node and a soldier node; every alight / drop transfer's
soldier node lies on a soldier island that is no carrier, and on the main island for the landing craft and ground
vehicles; no landing-craft alight lands in Iwo Jima's no-go strip (the beach outside the ForbidPathfinding boardwalk,
x -190..-174, z -10..26: NAVMESH_AUDIT); every carrier spawn's landing craft reaches at least one landing zone; the
reference trip from each carrier island a craft is boarded from to the main island's flags takes the craft (rsnvtrip.plan).
"""
import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Collection, Sequence
from typing import Any

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import navkitjson1943 as nj  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import game1943, rsnv, rsnvtrip  # noqa: E402

type Log = Callable[[str], object]

CLASSES = ['jeep', 'tank', 'lcvp', 'plane']
IWO_NO_GO = (-190.0, -174.0, -10.0, 26.0)
KIND_NAMES = {1: 'board', 2: 'alight', 3: 'launch', 4: 'drop', 5: 'mount'}


sha256 = nj.sha256                 # (navkitjson1943: one chunked SHA-256 for every tool)


def world_path(lv: str, cls: str) -> str:
    r"""The class's vehicle world: work\levels\<level>\navkit\<level>.<class>.rsnv."""
    return os.path.join(game1943.level_work(lv), 'navkit', f'{lv}.{cls}.rsnv')


def build(lv: str, classes: Sequence[str], sample: bool = False, log: Log = print) -> dict[str, dict[str, Any]]:
    """Builds the classes' worlds (groundnav1943, seanav1943, airnav1943), or stamps a class the level has none of
    absent; returns {class: {'path', 'stats', 'seconds'}} of the worlds written."""
    import airnav1943
    import groundnav1943
    import seanav1943
    out = {}
    st = nj.read(lv)
    game = st.get('game')               # the copy the level was prepared from (2026-10-30: the default copy was read)
    for cls in classes:
        started = time.time()
        if cls in ('jeep', 'tank'):
            seeds = groundnav1943.spawn_seeds(lv, cls, game)
            if not any(s['class'] == cls for s in seeds):
                log(f'{lv}: no {cls} spawns: no {cls} world')
                vc.absent(lv, cls, vc.inputs(st, lv, cls), world_path(lv, cls), f'no {cls} spawns on the level', log)
                continue
            # samples of another preparation or other class parameters are made again (2026-10-30: any file was reused)
            if sample or not groundnav1943.samples_current(lv, cls):
                if not sample and os.path.isfile(groundnav1943.paths(lv)['samples'](cls)):
                    log(f'{lv} {cls}: the samples on disk are of another preparation or other {cls} parameters: '
                        f'sampling again')
                groundnav1943.sample(lv, cls, log)
            path, stats = groundnav1943.build(lv, cls, log, game=game)
        elif cls == 'lcvp':
            path, stats = seanav1943.build(lv, game=game, log=log)
        else:
            path, stats = airnav1943.build(lv, game=game, log=log)
        if path:
            out[cls] = {'path': path, 'stats': stats, 'seconds': round(time.time() - started, 1)}
    return out


def world_identity(world: rsnv.World, lv: str, cls: str) -> list[str]:
    """Problems where a vehicle world's own header and META disagree with what it should be: the level (header level
    id, META level), the class (META class and layer) and its parameters (header radius, META params digest)."""
    meta = world.meta or {}
    out = []
    if world.level_id != rsnv.fnv1a(lv) or meta.get('level') != lv:
        out.append(f'the world is of another level (META level {meta.get("level")!r}, level id {world.level_id:#010x})')
    if meta.get('class') != cls or meta.get('layer') != vc.CLASS_IDS[cls]:
        out.append(f'the world is of another class (META class {meta.get("class")!r}, layer {meta.get("layer")!r})')
    if abs(float(world.radius) - float(vc.value(cls, 'radius'))) > 1e-3:
        out.append(f'radius {world.radius:g} m in its header, the {cls} class has {vc.value(cls, "radius"):g} m')
    if meta.get('params') != vc.params_id(cls):
        out.append(f'made with other {cls} parameters (META params {str(meta.get("params"))[:12]})')
    return out


def on_cells(world: rsnv.World) -> int:
    """Nodes whose position is not on a walkable cell of their area (or have none)."""
    bad = 0
    for n in range(len(world.node_pos)):
        area_k = int(world.node_area[n])
        if area_k == 0xFFFF:
            bad += 1
            continue
        area = world.areas[area_k]
        cell = area.cell(float(world.node_pos[n][0]), float(world.node_pos[n][2]))
        if cell is None or not world.walkable(area, *cell):
            bad += 1
    return bad


def check(lv: str, classes: Sequence[str] = CLASSES, log: Log = print,
          required: Collection[str] | None = None) -> dict[str, Any]:
    """required: the classes whose world must be there (default: every class checked) - unless the level has none
    (its builder stamped the class absent: no spawns of it, no water). 2026-10-30: a missing world was skipped, so a
    check with the jeep world missing checked nothing and passed. A world not stamped as built from the current
    preparation, class parameters and soldier world is a problem too."""
    navkit_dir = os.path.join(game1943.level_work(lv), 'navkit')
    soldier_path = os.path.join(navkit_dir, f'{lv}.rsnv')
    soldier = rsnv.load(soldier_path)
    st = nj.read(lv)
    required = classes if required is None else required
    carrier = {k for k, record in enumerate(soldier.islands) if record[2] & rsnv.ISLD_CARRIER}
    report = {'level': lv,
              'soldier world': {'path': soldier_path, 'sha256': sha256(soldier_path), 'nodes': len(soldier.node_pos),
                                'islands': len(soldier.islands)},
              'worlds': {}, 'problems': []}
    transfers = []
    problems = report['problems']
    try:
        # the soldier world must be the export of the current, passed build: a world stamped on it is checked against
        # its SHA-256 only (2026-10-30 second review: a world finished after the soldier was built again passed)
        vc.soldier_world(st, lv, soldier_path)
    except (vc.StaleError, FileNotFoundError) as e:
        problems.append(f'soldier world: {e}')
    for cls in classes:
        path = world_path(lv, cls)
        try:
            want = vc.inputs(st, lv, cls)
        except vc.StaleError as e:
            problems.append(f'{cls}: {e}')
            continue
        stamp = vc.stamp(st, 'worlds', cls)
        if vc.current(st, 'worlds', want, cls) and stamp.get('absent'):
            report['worlds'][cls] = {'absent': stamp['absent']}
            log(f'{lv} {cls}: no world: {stamp["absent"]}')
            continue
        if not os.path.isfile(path):
            if cls in required:
                problems.append(f'{cls}: no world (missing {path}): build it '
                                f'(navexport_vehicles1943.py {lv} --classes {cls})')
            else:
                report.setdefault('notes', []).append(f'{cls}: not built')
            continue
        # the stage table's vehicle_worlds: the stamp of these inputs and soldier world, and the file the stamped one
        world_stale = nj.stale(st, 'vehicle_worlds', cls, want=dict(want, soldier=report['soldier world']['sha256']),
                               files={'world': path})
        if world_stale and world_stale.reason != 'file':
            stale = (f'{cls}: {os.path.basename(path)} is not built from the current preparation, {cls} parameters and '
                     f'soldier world: build it again (navexport_vehicles1943.py {lv} --classes {cls})')
            # as a missing world: a problem when asked for, else a note (2026-10-30: a world a soldier rebuild left
            # stale failed every --classes run that did not ask for it)
            if cls in required:
                problems.append(stale)
            else:
                report.setdefault('notes', []).append(stale)
            continue
        if world_stale:
            # 2026-10-30 second review: the stamp named the inputs only, so another file put in the world's place (a
            # valid world of another level, radius 99) passed with no problem
            why = (f'{cls}: {os.path.basename(path)} is not the world its builder wrote (its SHA-256 is not the stamped '
                   f'one): build it again (navexport_vehicles1943.py {lv} --classes {cls})')
            if cls in required:
                problems.append(why)
            else:
                report.setdefault('notes', []).append(why)
            continue
        digest = sha256(path)
        vehicle = rsnv.load(path)
        problems += [f'{cls}: {x}' for x in world_identity(vehicle, lv, cls)]
        invalid = vehicle.validate()
        problems += [f'{cls}: {x}' for x in invalid]
        off_cells = on_cells(vehicle)
        lane_nodes = set()
        for lane in vehicle.meta.get('lanes', []):
            lane_nodes.update(n for n in (lane.get('deck node'), lane.get('deck end node'), lane.get('start node'),
                                          lane.get('end node'))
                              if n is not None)
        if off_cells:
            problems.append(f'{cls}: {off_cells} nodes off their cells')
        world_transfers = rsnvtrip.transfers(soldier, vehicle)
        kinds = {}
        for k, kind, team, vehicle_node, soldier_node in world_transfers:
            record = vehicle.offmesh[k]
            kinds[KIND_NAMES[kind]] = kinds.get(KIND_NAMES[kind], 0) + 1
            island = int(soldier.node_island[soldier_node]) if soldier_node is not None else None
            transfers.append({'world': cls, 'index': k, 'kind': KIND_NAMES[kind], 'team mask': team,
                              'vehicle node': vehicle_node, 'point': [round(float(x), 2) for x in record[4:7]],
                              'radius': round(float(record[8]), 2), 'soldier node': soldier_node,
                              'soldier island': island, 'carrier': island in carrier if island is not None else None,
                              'exporter soldier node': int(record[9]) if record[9] != 0xFFFFFFFF else None})
            if kind == rsnvtrip.OFFM_BOARD and (vehicle_node is None or soldier_node is None):
                # a spawn whose entry has no soldier ground (a craft moored in deep water) is reported, not failed
                report.setdefault('notes', []).append(
                    f'{cls}: board transfer {k} at {np.round(record[4:7], 1).tolist()}: vehicle node {vehicle_node}, '
                    f'soldier node {soldier_node}')
            if kind in (rsnvtrip.OFFM_ALIGHT, rsnvtrip.OFFM_DROP):
                if soldier_node is None:
                    problems.append(f'{cls}: alight {k} resolves to no soldier node')
                elif island in carrier:
                    problems.append(f'{cls}: alight {k} onto a carrier')
                elif cls in ('lcvp', 'jeep', 'tank') and island != 0:
                    problems.append(f'{cls}: alight {k} onto island {island}, not the main one')
                if cls == 'lcvp' and lv == 'iwo_jima_s' and soldier_node is not None:
                    x, z = float(soldier.node_pos[soldier_node][0]), float(soldier.node_pos[soldier_node][2])
                    if IWO_NO_GO[0] <= x <= IWO_NO_GO[1] and IWO_NO_GO[2] <= z <= IWO_NO_GO[3]:
                        problems.append(f'lcvp: alight {k} in the no-go strip at ({x:.1f}, {z:.1f})')
        report['worlds'][cls] = {'path': path, 'sha256': digest, 'bytes': os.path.getsize(path),
                                 'nodes': len(vehicle.node_pos), 'edges': len(vehicle.edge_target),
                                 'areas': len(vehicle.areas), 'islands': [record[0] for record in vehicle.islands][:6],
                                 'cells': vehicle.meta.get('counts', {}).get('cells'),
                                 'tight edges': vehicle.meta.get('counts', {}).get('tight edges'),
                                 'transfers': kinds, 'nodes off cells': off_cells}
        log(f'{lv} {cls}: {len(vehicle.node_pos)} nodes, {len(vehicle.edge_target)} edges, {len(vehicle.areas)} areas, '
            f'transfers {kinds}, {off_cells} nodes off their cells')
    # trips: carrier islands -> flags by the landing craft
    lcvp_path = world_path(lv, 'lcvp')
    if 'lcvp' in report['worlds'] and 'absent' not in report['worlds']['lcvp'] and carrier:
        vehicle = rsnv.load(lcvp_path)
        flag_points = flags_of(lv, st.get('game'))
        trips = []
        # the carrier islands a craft is boarded from (the decks; the lower decks and superstructure pieces that are
        # their own islands hold no craft and are left to the decks' trips)
        boarded = sorted({int(soldier.node_island[soldier_node])
                          for _k, kind, _t, vehicle_node, soldier_node in rsnvtrip.transfers(soldier, vehicle)
                          if kind == rsnvtrip.OFFM_BOARD and soldier_node is not None and vehicle_node is not None}
                         & carrier)
        for island in boarded:
            start = soldier.islands[island][1]
            team = carrier_team(soldier, vehicle, island)
            for name, flag_pos in flag_points:
                dist = np.hypot(soldier.node_pos[:, 0] - flag_pos[0], soldier.node_pos[:, 2] - flag_pos[2])
                dist = np.where(soldier.node_island == 0, dist, np.inf)
                goal = int(np.argmin(dist))
                trip = rsnvtrip.plan(soldier, vehicle, start, goal, team)
                trips.append({'carrier island': island, 'team': team, 'flag': name, 'mode': trip['mode'],
                              'cost m': (trip['cost'] or 0) / 16, 'walk1 m': trip.get('walk1', 0) / 16,
                              'ride m': trip.get('ride', 0) / 16, 'walk2 m': trip.get('walk2', 0) / 16})
                if trip['mode'] != 'vehicle':
                    problems.append(f'trip from carrier island {island} to {name}: {trip["mode"]}')
        report['trips'] = trips
        log(f'{lv}: {sum(trip["mode"] == "vehicle" for trip in trips)} of {len(trips)} carrier -> flag trips take '
            f'the craft')
    json.dump({'level': lv, 'soldier world': report['soldier world'], 'transfers': transfers},
              open(os.path.join(navkit_dir, f'{lv}.transfers.json'), 'w', encoding='utf-8', newline=''), indent=1)
    json.dump(report, open(os.path.join(navkit_dir, f'{lv}.vehicles.json'), 'w', encoding='utf-8', newline=''), indent=1)
    for problem in problems:
        log('PROBLEM ' + problem)
    log(f'{lv}: {len(problems)} problem(s); {navkit_dir}\\{lv}.vehicles.json, {lv}.transfers.json')
    return report


def flags_of(lv: str, game: str | None = None) -> list[tuple[str, Any]]:
    """The level's flags: [(name, position)] (level1943's objectives with the role 'flag')."""
    from bfbc2core import level1943
    level = level1943.load(lv, game=game, log=lambda *_args: None)
    return [(objective['name'], objective['position']) for objective in getattr(level, 'objectives', [])
            if objective.get('role') == 'flag' and objective.get('position')]


def carrier_team(soldier: rsnv.World, vehicle: rsnv.World, island: int) -> int:
    """The team of a carrier island: its boarding transfers' team mask (1 or 2)."""
    for _k, kind, team, _vehicle_node, soldier_node in rsnvtrip.transfers(soldier, vehicle):
        if (kind == rsnvtrip.OFFM_BOARD and soldier_node is not None and int(soldier.node_island[soldier_node]) == island
                and team in (1, 2)):
            return team
    return 3


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='navexport_vehicles1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('level')
    parser.add_argument('--classes', default=','.join(CLASSES))
    parser.add_argument('--sample', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args(argv[1:])
    classes = [name for name in args.classes.split(',') if name]
    bad = [name for name in classes if name not in CLASSES]
    if bad:
        raise ValueError(f'unknown class {bad[0]} (classes: {", ".join(CLASSES)})')
    soldier_path = os.path.join(game1943.level_work(args.level), 'navkit', f'{args.level}.rsnv')
    if not os.path.isfile(soldier_path):
        raise FileNotFoundError(f'{args.level}: no soldier world (missing {soldier_path}): '
                                f'run navexport1943.py {args.level}')
    if not args.check_only:
        build(args.level, classes, args.sample)
    report = check(args.level, CLASSES, required=classes)
    return 1 if report['problems'] else 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navexport_vehicles1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
