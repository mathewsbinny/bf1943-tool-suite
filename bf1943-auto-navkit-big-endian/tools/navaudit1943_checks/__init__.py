"""The checks of navaudit1943.py (audit): a BF1943 build against its level's own data and its own consistency.

  1 required positions   capture areas (HotZone outlines), bases, soldier spawns and alternates, vehicle spawns
                         (ground, boats, aircraft, AA guns and mounted MGs), repair stations: walkable ground of the
                         build on the position's own floor within OBJECTIVE_REACH; a vehicle: walkable ground within
                         its own EntryRadius of one of its entry points (the blueprint's PlayerEntryComponentData),
                         else within VEHICLE_REACH (BOAT_REACH for a boat in the water); a base's marker is reported
                         only; the walkable share of each capture area
  2 reachability         every infantry spawn to every capture area and back on the route graph; every island
                         classified (main, vehicle-only zone, separate landmass, raised surface, gap candidate)
  3 file consistency     hpdcheck; portals two-way; border portals paired; one node per position; border-node owners;
                         crossings (touching ground of one floor on two islands)
  4 resolution           0.8 m and 0.2 m walkable ground compared in the detailed sectors
  5 layers               every surface within LAYER_WINDOW of each required position's floor: kept or not, and why
  6 water and shore      the wading band down to WATER_DEPTH; walkable ground under deeper water; jetties and piers
  7 rules                where each rule changed the kept ground (the rule-off masks), and near-misses within 20 %
  8 playable area        walkable ground inside and outside the combat area

The package (navaudit1943_checks.py until 2026-10-31, split along its checks; every name it had is here as
navaudit1943_checks.<name>, and run() stays here, so a test that replaces navaudit1943_checks.load is what run calls):
  config.py       the vehicle classes and the audit's own settings (BOAT_REACH, VEHICLE_REACH, MOUNT_DROP, AIRBORNE,
                  USABLE_SHARE, NEAR, CLUSTER_CELL)
  geometry.py     geometry helpers: polygons, distances, labelling, clusters
  grids.py        the level: one grid's samples and masks (Grid), why a surface is not kept, the build's graph (Nav)
  loading.py      load: the level, its build and masks (C); lookups by position
  positions.py    1 + 5: the required positions, their checks and layers, the capture areas' walkable share
  islands.py      2: the landmass, the islands' classes and reachability
  filecheck.py    3: file consistency and crossings
  pitches.py      4: resolution, 0.8 m against 0.2 m
  shore.py        6: water and shore
  rulemasks.py    7: where each rule changed the kept ground, near-misses, ladders
  combatarea.py   8: the playable area
  report.py       audit.md (markdown) and the JSON encoder
  __init__.py     run: every check, audit.json and audit.md
"""
import json
import os
import time

import navaudit1943 as A  # noqa: E402
import soldier1943  # noqa: E402

# the package's modules, re-exported: every name the module had
from .config import (  # noqa: F401
    AIRBORNE, AIRCRAFT, BOAT_REACH, BOATS, CLUSTER_CELL, GROUND, MOUNT_DROP, NEAR, PIER_RX, STATIONARY, USABLE_SHARE,
    VEHICLE_REACH)
from .geometry import clusters_xz, in_poly, label, poly_area, seg_dist  # noqa: F401
from .grids import Grid, Nav, why_not  # noqa: F401
from .loading import grid_at, load, objects_at, sector_of  # noqa: F401
from .positions import (  # noqa: F401
    capture_share, check_position, fine_entry, floor_of, nearest_cell_3d, required_positions, vehicle_entries)
from .islands import classify_islands, landmass, landmass_at, reachability, sea_level, vehicle_link  # noqa: F401
from .filecheck import consistency, crossings  # noqa: F401
from .pitches import ground_heights, resolution  # noqa: F401
from .shore import water_checks  # noqa: F401
from .rulemasks import _near, ladder_report, rules, step_near  # noqa: F401
from .combatarea import playable  # noqa: F401
from .report import _cols, _fmt, _json, markdown  # noqa: F401


# ------------------------------------------------------------------------------------------------ run

def run(lv, json_out=None, log=print):
    t0 = time.time()
    C = load(lv, log)
    positions = required_positions(C)
    for r in positions:
        if r.get('capture_area') is not None:
            r['_poly'] = r['capture_area']
    checked = []
    for r in positions:
        res = check_position(C, r)
        if r['kind'] in ('flag', 'base', 'repair station', 'hot zone without a flag'):
            res['capture_share'] = capture_share(C, r)
        if r['kind'] == 'repair station' and res.get('capture_share'):
            # a repair station is its outline (vehicles are repaired inside it); its entity's point can sit above the
            # ground (Guadalcanal: 4.4 m) - reported, not the test
            res['marker_status'], res['marker_reason'] = res['status'], res.get('reason')
            res['status'] = res['capture_share']['status']
            res['reason'] = (f"outline {res['capture_share']['share']:.0%} walkable ({res['capture_share']['share_of']})"
                             if res['capture_share']['share'] is not None else 'outline: no ground')
        res['_poly'] = r.get('_poly')
        checked.append(res)
    log(f'{lv}: 1 positions ({time.time() - t0:.0f} s)')
    LM = landmass(C)
    islands = classify_islands(C, LM, checked)
    reach = reachability(C, checked)
    log(f'{lv}: 2 reachability ({time.time() - t0:.0f} s)')
    cons = consistency(C)
    cons['crossings'] = crossings(C)
    log(f'{lv}: 3 consistency ({time.time() - t0:.0f} s)')
    resol = resolution(C)
    log(f'{lv}: 4 resolution ({time.time() - t0:.0f} s)')
    water = water_checks(C)
    log(f'{lv}: 6 water ({time.time() - t0:.0f} s)')
    rl = rules(C, checked)
    log(f'{lv}: 7 rules ({time.time() - t0:.0f} s)')
    play = playable(C)
    log(f'{lv}: 8 playable area ({time.time() - t0:.0f} s)')
    for r in checked:
        r.pop('_poly', None)
    report = {'level': lv, 'date': time.strftime('%Y-%m-%d %I:%M %p'), 'inputs': C['inputs'],
              'settings': {'OBJECTIVE_REACH': A.OBJECTIVE_REACH, 'FLOOR_DY': A.FLOOR_DY, 'LAYER_WINDOW': A.LAYER_WINDOW,
                           'BOAT_REACH': BOAT_REACH, 'USABLE_SHARE': USABLE_SHARE, 'soldier': {
                               'radius': soldier1943.RADIUS, 'step': soldier1943.STEP, 'height': soldier1943.HEIGHT,
                               'slope_max': soldier1943.SLOPE_MAX, 'water_depth': soldier1943.WATER_DEPTH}},
              'masks': {w: sorted(G.variants) for w, G in C['grids'].items()},
              'placeholders at the origin (not checked)': C['placeholders'],
              '1 positions': checked, '2 reachability': reach, '2 islands': islands, '3 consistency': cons,
              '4 resolution': resol, '6 water': water, '7 rules': rl, '8 playable area': play}
    path = json_out or os.path.join(C['p']['audit'], 'audit.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(report, f, indent=1, default=_json)
    md = os.path.splitext(path)[0] + '.md'
    with open(md, 'w', encoding='utf-8', newline='\n') as f:
        f.write(markdown(json.loads(json.dumps(report, default=_json))))
    log(f'{lv}: wrote {path} and {md} ({time.time() - t0:.0f} s)')
    return report
