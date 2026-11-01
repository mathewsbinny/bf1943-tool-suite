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
from typing import TYPE_CHECKING, Any, cast

import navaudit1943  # noqa: E402
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

if TYPE_CHECKING:
    from navaudit1943 import Log


# ------------------------------------------------------------------------------------------------ run

def run(lv: str, json_out: str | None = None, log: 'Log' = print) -> dict[str, Any]:
    """Every check on a level's checked build: writes audit.json (json_out, else the level's audit folder) and
    audit.md beside it, and returns the report."""
    t0 = time.time()
    ctx = load(lv, log)
    positions = required_positions(ctx)
    for position in positions:
        if position.get('capture_area') is not None:
            position['_poly'] = position['capture_area']
    checked = []
    for position in positions:
        res = check_position(ctx, position)
        if position['kind'] in ('flag', 'base', 'repair station', 'hot zone without a flag'):
            res['capture_share'] = capture_share(ctx, position)
        if position['kind'] == 'repair station' and res.get('capture_share'):
            # a repair station is its outline (vehicles are repaired inside it); its entity's point can sit above the
            # ground (Guadalcanal: 4.4 m) - reported, not the test
            share = cast('dict[str, Any]', res['capture_share'])     # checked just above
            res['marker_status'], res['marker_reason'] = res['status'], res.get('reason')
            res['status'] = share['status']
            res['reason'] = (f"outline {share['share']:.0%} walkable ({share['share_of']})"
                             if share['share'] is not None else 'outline: no ground')
        res['_poly'] = position.get('_poly')
        checked.append(res)
    log(f'{lv}: 1 positions ({time.time() - t0:.0f} s)')
    land = landmass(ctx)
    islands = classify_islands(ctx, land, checked)
    reach = reachability(ctx, checked)
    log(f'{lv}: 2 reachability ({time.time() - t0:.0f} s)')
    file_consistency = consistency(ctx)
    file_consistency['crossings'] = crossings(ctx)
    log(f'{lv}: 3 consistency ({time.time() - t0:.0f} s)')
    pitch_comparison = resolution(ctx)
    log(f'{lv}: 4 resolution ({time.time() - t0:.0f} s)')
    water = water_checks(ctx)
    log(f'{lv}: 6 water ({time.time() - t0:.0f} s)')
    rule_report = rules(ctx, checked)
    log(f'{lv}: 7 rules ({time.time() - t0:.0f} s)')
    play = playable(ctx)
    log(f'{lv}: 8 playable area ({time.time() - t0:.0f} s)')
    for res in checked:
        res.pop('_poly', None)
    soldier = {'radius': soldier1943.RADIUS, 'step': soldier1943.STEP, 'height': soldier1943.HEIGHT,
               'slope_max': soldier1943.SLOPE_MAX, 'water_depth': soldier1943.WATER_DEPTH}
    settings = {'OBJECTIVE_REACH': navaudit1943.OBJECTIVE_REACH, 'FLOOR_DY': navaudit1943.FLOOR_DY,
                'LAYER_WINDOW': navaudit1943.LAYER_WINDOW, 'BOAT_REACH': BOAT_REACH, 'USABLE_SHARE': USABLE_SHARE,
                'soldier': soldier}
    # the positions at the origin: required_positions (above) set them
    placeholders = ctx['placeholders']  # pyright: ignore[reportTypedDictNotRequiredAccess]
    report = {'level': lv, 'date': time.strftime('%Y-%m-%d %I:%M %p'), 'inputs': ctx['inputs'],
              'settings': settings,
              'masks': {which: sorted(grid.variants) for which, grid in ctx['grids'].items()},
              'placeholders at the origin (not checked)': placeholders,
              '1 positions': checked, '2 reachability': reach, '2 islands': islands, '3 consistency': file_consistency,
              '4 resolution': pitch_comparison, '6 water': water, '7 rules': rule_report, '8 playable area': play}
    path = json_out or os.path.join(ctx['p']['audit'], 'audit.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(report, f, indent=1, default=_json)
    md = os.path.splitext(path)[0] + '.md'
    with open(md, 'w', encoding='utf-8', newline='\n') as f:
        f.write(markdown(json.loads(json.dumps(report, default=_json))))
    log(f'{lv}: wrote {path} and {md} ({time.time() - t0:.0f} s)')
    return report
