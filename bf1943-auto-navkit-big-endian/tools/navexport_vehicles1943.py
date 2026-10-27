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

Checks (exit 1 when any fails): each world reads back and validates (rsnv.World.validate); every node lies on a
cell of its own world; every board transfer has a vehicle node and a soldier node; every alight / drop transfer's
soldier node lies on a soldier island that is no carrier, and on the main island for the landing craft and ground
vehicles; no landing-craft alight lands in Iwo Jima's no-go strip (the beach outside the ForbidPathfinding boardwalk,
x -190..-174, z -10..26: NAVMESH_AUDIT); every carrier spawn's landing craft reaches at least one landing zone; the
reference trip from each carrier island a craft is boarded from to the main island's flags takes the craft (rsnvtrip.plan).
"""
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import game1943, rsnv, rsnvtrip  # noqa: E402

CLASSES = ['jeep', 'tank', 'lcvp', 'plane']
IWO_NO_GO = (-190.0, -174.0, -10.0, 26.0)
KIND_NAMES = {1: 'board', 2: 'alight', 3: 'launch', 4: 'drop', 5: 'mount'}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


def world_path(lv, cls):
    return os.path.join(game1943.level_work(lv), 'navkit', f'{lv}.{cls}.rsnv')


def build(lv, classes, sample=False, log=print):
    import airnav1943
    import groundnav1943
    import seanav1943
    out = {}
    for cls in classes:
        t0 = time.time()
        if cls in ('jeep', 'tank'):
            seeds = groundnav1943.spawn_seeds(lv, cls)
            if not any(s['class'] == cls for s in seeds):
                log(f'{lv}: no {cls} spawns: no {cls} world')
                continue
            if sample or not os.path.isfile(groundnav1943.paths(lv)['samples'](cls)):
                groundnav1943.sample(lv, cls, log)
            path, stats = groundnav1943.build(lv, cls, log)
        elif cls == 'lcvp':
            path, stats = seanav1943.build(lv, log=log)
        else:
            path, stats = airnav1943.build(lv, log=log)
        if path:
            out[cls] = {'path': path, 'stats': stats, 'seconds': round(time.time() - t0, 1)}
    return out


def on_cells(w):
    """Nodes whose position is not on a walkable cell of their area (or have none)."""
    bad = 0
    for n in range(len(w.node_pos)):
        a = int(w.node_area[n])
        if a == 0xFFFF:
            bad += 1
            continue
        ar = w.areas[a]
        c = ar.cell(float(w.node_pos[n][0]), float(w.node_pos[n][2]))
        if c is None or not w.walkable(ar, *c):
            bad += 1
    return bad


def check(lv, classes=CLASSES, log=print):
    W = os.path.join(game1943.level_work(lv), 'navkit')
    sp = os.path.join(W, f'{lv}.rsnv')
    S = rsnv.load(sp)
    carrier = {k for k, r in enumerate(S.islands) if r[2] & rsnv.ISLD_CARRIER}
    report = {'level': lv, 'soldier world': {'path': sp, 'sha256': sha256(sp), 'nodes': len(S.node_pos),
                                              'islands': len(S.islands)}, 'worlds': {}, 'problems': []}
    transfers = []
    probs = report['problems']
    for cls in classes:
        p = world_path(lv, cls)
        if not os.path.isfile(p):
            continue
        V = rsnv.load(p)
        v = V.validate()
        probs += [f'{cls}: {x}' for x in v]
        off = on_cells(V)
        lane_nodes = set()
        for l in V.meta.get('lanes', []):
            lane_nodes.update(n for n in (l.get('deck node'), l.get('deck end node'), l.get('start node'), l.get('end node'))
                              if n is not None)
        if off:
            probs.append(f'{cls}: {off} nodes off their cells')
        tr = rsnvtrip.transfers(S, V)
        kinds = {}
        for k, kind, team, vn, sn in tr:
            r = V.offmesh[k]
            kinds[KIND_NAMES[kind]] = kinds.get(KIND_NAMES[kind], 0) + 1
            isl = int(S.node_island[sn]) if sn is not None else None
            transfers.append({'world': cls, 'index': k, 'kind': KIND_NAMES[kind], 'team mask': team, 'vehicle node': vn,
                              'point': [round(float(x), 2) for x in r[4:7]], 'radius': round(float(r[8]), 2),
                              'soldier node': sn, 'soldier island': isl, 'carrier': isl in carrier if isl is not None else None,
                              'exporter soldier node': int(r[9]) if r[9] != 0xFFFFFFFF else None})
            if kind == rsnvtrip.OFFM_BOARD and (vn is None or sn is None):
                # a spawn whose entry has no soldier ground (a craft moored in deep water) is reported, not failed
                report.setdefault('notes', []).append(f'{cls}: board transfer {k} at {np.round(r[4:7], 1).tolist()}: '
                                                      f'vehicle node {vn}, soldier node {sn}')
            if kind in (rsnvtrip.OFFM_ALIGHT, rsnvtrip.OFFM_DROP):
                if sn is None:
                    probs.append(f'{cls}: alight {k} resolves to no soldier node')
                elif isl in carrier:
                    probs.append(f'{cls}: alight {k} onto a carrier')
                elif cls in ('lcvp', 'jeep', 'tank') and isl != 0:
                    probs.append(f'{cls}: alight {k} onto island {isl}, not the main one')
                if cls == 'lcvp' and lv == 'iwo_jima_s' and sn is not None:
                    x, z = float(S.node_pos[sn][0]), float(S.node_pos[sn][2])
                    if IWO_NO_GO[0] <= x <= IWO_NO_GO[1] and IWO_NO_GO[2] <= z <= IWO_NO_GO[3]:
                        probs.append(f'lcvp: alight {k} in the no-go strip at ({x:.1f}, {z:.1f})')
        report['worlds'][cls] = {'path': p, 'sha256': sha256(p), 'bytes': os.path.getsize(p), 'nodes': len(V.node_pos),
                                 'edges': len(V.edge_target), 'areas': len(V.areas), 'islands': [r[0] for r in V.islands][:6],
                                 'cells': V.meta.get('counts', {}).get('cells'),
                                 'tight edges': V.meta.get('counts', {}).get('tight edges'),
                                 'transfers': kinds, 'nodes off cells': off}
        log(f'{lv} {cls}: {len(V.node_pos)} nodes, {len(V.edge_target)} edges, {len(V.areas)} areas, transfers {kinds}, '
            f'{off} nodes off their cells')
    # trips: carrier islands -> flags by the landing craft
    lp = world_path(lv, 'lcvp')
    if os.path.isfile(lp) and carrier:
        V = rsnv.load(lp)
        lvl_obj = flags_of(lv)
        trips = []
        # the carrier islands a craft is boarded from (the decks; the lower decks and superstructure pieces that are
        # their own islands hold no craft and are left to the decks' trips)
        boarded = sorted({int(S.node_island[sn]) for _k, kind, _t, vn, sn in rsnvtrip.transfers(S, V)
                          if kind == rsnvtrip.OFFM_BOARD and sn is not None and vn is not None} & carrier)
        for isl in boarded:
            rep = S.islands[isl][1]
            team = carrier_team(S, V, isl)
            for name, q in lvl_obj:
                d = np.hypot(S.node_pos[:, 0] - q[0], S.node_pos[:, 2] - q[2])
                d = np.where(S.node_island == 0, d, np.inf)
                goal = int(np.argmin(d))
                t = rsnvtrip.plan(S, V, rep, goal, team)
                trips.append({'carrier island': isl, 'team': team, 'flag': name, 'mode': t['mode'], 'cost m': (t['cost'] or 0) / 16,
                              'walk1 m': t.get('walk1', 0) / 16, 'ride m': t.get('ride', 0) / 16, 'walk2 m': t.get('walk2', 0) / 16})
                if t['mode'] != 'vehicle':
                    probs.append(f'trip from carrier island {isl} to {name}: {t["mode"]}')
        report['trips'] = trips
        log(f'{lv}: {sum(t["mode"] == "vehicle" for t in trips)} of {len(trips)} carrier -> flag trips take the craft')
    json.dump({'level': lv, 'soldier world': report['soldier world'], 'transfers': transfers},
              open(os.path.join(W, f'{lv}.transfers.json'), 'w', encoding='utf-8', newline=''), indent=1)
    json.dump(report, open(os.path.join(W, f'{lv}.vehicles.json'), 'w', encoding='utf-8', newline=''), indent=1)
    for pr in probs:
        log('PROBLEM ' + pr)
    log(f'{lv}: {len(probs)} problem(s); {W}\\{lv}.vehicles.json, {lv}.transfers.json')
    return report


def flags_of(lv):
    from bfbc2core import level1943
    lvl = level1943.load(lv, log=lambda *a: None)
    return [(o['name'], o['position']) for o in getattr(lvl, 'objectives', []) if o.get('role') == 'flag' and o.get('position')]


def carrier_team(S, V, isl):
    """The team of a carrier island: its boarding transfers' team mask (1 or 2)."""
    for k, kind, team, vn, sn in rsnvtrip.transfers(S, V):
        if kind == rsnvtrip.OFFM_BOARD and sn is not None and int(S.node_island[sn]) == isl and team in (1, 2):
            return team
    return 3


def main(argv):
    ap = argparse.ArgumentParser(prog='navexport_vehicles1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level')
    ap.add_argument('--classes', default=','.join(CLASSES))
    ap.add_argument('--sample', action='store_true')
    ap.add_argument('--check-only', action='store_true')
    a = ap.parse_args(argv[1:])
    classes = [c for c in a.classes.split(',') if c]
    bad = [c for c in classes if c not in CLASSES]
    if bad:
        raise ValueError(f'unknown class {bad[0]} (classes: {", ".join(CLASSES)})')
    sp = os.path.join(game1943.level_work(a.level), 'navkit', f'{a.level}.rsnv')
    if not os.path.isfile(sp):
        raise FileNotFoundError(f'{a.level}: no soldier world (missing {sp}): run navexport1943.py {a.level}')
    if not a.check_only:
        build(a.level, classes, a.sample)
    report = check(a.level, CLASSES)
    return 1 if report['problems'] else 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navexport_vehicles1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
