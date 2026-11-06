"""navaudit1943_checks: loading a level, its build and masks; lookups by position.

load() reads a level's checked build (<level>.hpd and its cells file), its samples and the masks navaudit1943.py
masks made, the level's entities and spawn map from the game copy, and its object boxes; it returns the audit's
level context (AuditContext, a dict), which every check takes as `ctx`. Every mask must be of the samples and
build on disk.
"""
import hashlib
import json
import math
import os
import time
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast

import numpy as np
import numpy.typing as npt

import navaudit1943  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import navbuild  # noqa: E402  (bf1943-auto-navkit-common, a shared folder)
import soldier1943  # noqa: E402
from bfbc2core import game1943, materialmap1943, terrain  # noqa: E402

from .grids import Grid, Nav

if TYPE_CHECKING:
    from bfbc2core.level import Entity, Level
    from bfbc2core.materialmap1943 import MaterialMap
    from bfbc2core.terrain import Heightfield

    from .grids import WaterMesh

    class AuditContext(TypedDict):
        """The audit's level context: what load() read, and what the checks add as they go."""
        lv: str                                     # the level's name
        p: dict[str, str]                           # navaudit1943.level_paths
        st: dict[str, Any]                          # navkit.json
        game: str                                   # the game copy's folder
        g: game1943.Game                            # the level's bundles
        hf: Heightfield
        mm: MaterialMap | None
        nav: Nav                                    # the build
        lvl: Level                                  # the level's entities and outlines
        map: dict[str, Any]                         # bf1943-spawn-map's objectives and spawns
        water: WaterMesh | None
        entity_by_guid: dict[str, Entity]           # lower-case GUID -> entity
        grids: dict[str, Grid]                      # 'normal' (0.8 m) and, with a current 0.2 m pass, 'detailed'
        xoff: int                                   # navkit.json's sector offsets
        zoff: int
        dset: set[tuple[int, int]]                  # navkit.json's detailed sectors
        dset_built: set[tuple[int, int]]            # the sectors the build has 0.2 m areas in
        boxes: list[dict[str, Any]]                 # object_boxes.json
        inputs: dict[str, dict[str, Any]]           # every input's path, size and SHA-256
        placeholders: NotRequired[list[dict[str, Any]]]     # required_positions: the positions at the origin
        _cell_island: NotRequired[npt.NDArray[Any]]         # nearest_cell_3d: each cell's island
        # vehicle_entries: per vehicle blueprint, its entry points (local point, radius, seat order)
        _entries: NotRequired[dict[str, list[tuple[npt.NDArray[np.float64], float, int]]]]


# ------------------------------------------------------------------------------------------------ loading

def load(lv: str, log: 'navaudit1943.Log' = print) -> 'AuditContext':
    """The audit's level context of a level's checked build (NavKitError without one, or with a stale mask)."""
    import spawnmap
    from bfbc2core import level1943, navdata
    from bfbc2core import water as water_mod
    level = navaudit1943.open_level(lv)     # the checked build of the current preparation only (2026-10-30 review)
    paths, st = level.paths, level.st
    game = game1943.find_game(st.get('game'))         # the copy the level was prepared from (2026-10-30: the default)
    level_game = game1943.Game(game, lv)
    hf_name = f'terrains/{lv}/{lv}.heightfield-000.res'
    game_hf = nj.game_heightfield(st, lv, level_game.data(hf_name))   # refused when the copy has none (2026-11-01)
    hf = terrain.read(nj.prepared_heightfield(st, lv, game_hf))    # the prepared one (second review)
    mm = materialmap1943.for_level(level_game, lv, hf)
    started = time.time()
    # BF1943 builds store nodes and outlines half the soldier's height (0.9 m) up
    nav = navdata.load(paths['hpd'], hf, cache_dir=os.path.join(paths['audit'], 'cache'),
                       y_offset=soldier1943.SETTINGS.y_offset)
    log(f'{lv}: build read ({time.time() - started:.0f} s): {len(nav.areas)} areas, {len(nav.node_pos)} nodes')
    exact = navbuild.read_cells(paths['hpd'])
    log(f'{lv}: ' + ('cells file of the build read (area heights exact)' if exact is not None else
                     'no cells file for this build: area heights from the reader cells'))
    lvl = level1943.load(lv, game=game, log=lambda *a: None)
    level_map = spawnmap.spawn_map(game, lv)
    water_path = os.path.join(paths['objects'], 'water_mesh.res')
    has_mesh = soldier1943.has_water(st) and os.path.isfile(water_path)
    water = water_mod.read_mesh(water_path) if has_mesh else None
    masks = os.path.join(paths['audit'], 'masks')
    grids = {}
    hashes = level.hashes
    # every mask must be of the samples and build on disk (their SHA-256, stored in it): 2026-10-30 review, a base mask
    # was checked only by its surface count and the rule-off masks not at all
    for which, (src, _high) in navaudit1943.STAGE_SAMPLES.items():
        if not level.built_from(which, ': no detailed grid', log):
            continue
        path = os.path.join(masks, f'{which}_base.npz')
        if not os.path.isfile(path):
            log(f'{lv}: no {which} base mask ({path}): navaudit1943.py masks {lv}')
            continue
        why = navaudit1943.mask_stale(path, src, hashes)
        if why:
            raise nj.NavKitError(f'{why}: make the masks again (navaudit1943.py masks {lv})')
        base_mask = np.load(path)
        s = navaudit1943.load_samples(level.samples(which))
        if int(base_mask['surfaces']) != len(s.y):
            raise nj.NavKitError(f'{path}: made from another sampling run ({int(base_mask["surfaces"])} surfaces, '
                                 f'now {len(s.y)})')
        grid = Grid(s, base_mask['keep'], hf, water, '0.2 m' if which == 'detailed' else '0.8 m')
        for variant in navaudit1943.VARIANTS:
            variant_path = os.path.join(masks, f'{which}_{variant}.npz')
            if variant != 'base' and os.path.isfile(variant_path):
                why = navaudit1943.mask_stale(variant_path, src, hashes)
                if why:
                    log(f'{why}: left out (navaudit1943.py masks {lv} --variants {variant})')
                    continue
                grid.variants[variant] = np.load(variant_path)['keep']
        grids[which] = grid
    if 'normal' not in grids:
        raise nj.NavKitError(f'{lv}: no normal base mask of this build: navaudit1943.py masks {lv}')
    xoff, zoff = st['sector_offsets']
    dset = {(int(k[:2], 16), int(k[2:], 16)) for k in st.get('detailed_sectors') or ()}
    built = {area.sector for area in nav.areas if area.detailed}
    dset_built = {((sector_id >> 8) & 0xFF, sector_id & 0xFF) for sector_id in built}
    boxes = json.load(open(os.path.join(paths['objects'], 'object_boxes.json'), encoding='utf-8'))
    inputs = {}
    for name, path in (('hpd', paths['hpd']), ('samples.bin', os.path.join(paths['samples'], 'samples.bin')),
                       ('samples_02.bin', os.path.join(paths['samples'], 'samples_02.bin')),
                       ('object_boxes.json', os.path.join(paths['objects'], 'object_boxes.json')),
                       ('ladders.json', os.path.join(paths['objects'], 'ladders.json')),
                       ('water_mesh.res', water_path)):
        if os.path.isfile(path):
            inputs[name] = {'path': path, 'bytes': os.path.getsize(path), 'sha256': navaudit1943.sha256(path)}
    game_hf_sha256 = hashlib.sha256(cast(bytes, level_game.data(hf_name))).hexdigest()
    inputs['game heightfield'] = {'name': hf_name, 'sha256': game_hf_sha256}
    context = dict(lv=lv, p=paths, st=st, game=game, g=level_game, hf=hf, mm=mm, nav=Nav(nav, exact), lvl=lvl,
                   map=level_map, water=water, entity_by_guid={e.guid.lower(): e for e in lvl.entities},
                   grids=grids, xoff=xoff, zoff=zoff, dset=dset, dset_built=dset_built, boxes=boxes, inputs=inputs)
    return cast('AuditContext', context)


def sector_of(ctx: 'AuditContext', x: float, z: float) -> tuple[int, int]:
    """The sector (X, Z) holding (x, z), less the level's sector offsets."""
    return int(math.floor(x / 100.0)) - ctx['xoff'], int(math.floor(z / 100.0)) - ctx['zoff']


def grid_at(ctx: 'AuditContext', x: float, z: float) -> Grid:
    """The grid the build used at (x, z): the 0.2 m one in a built detailed sector, else the 0.8 m one."""
    if 'detailed' in ctx['grids'] and sector_of(ctx, x, z) in ctx['dset_built']:
        return ctx['grids']['detailed']
    return ctx['grids']['normal']


def objects_at(ctx: 'AuditContext', x: float, y: float, z: float, grow: float = 0.5) -> list[str]:
    """The names of up to 8 objects whose boxes (grown by grow, and 3 m in height) hold (x, y, z)."""
    out = []
    for box in ctx['boxes']:
        lo, hi = box['min'], box['max']
        inside_xz = lo[0] - grow <= x <= hi[0] + grow and lo[2] - grow <= z <= hi[2] + grow
        if inside_xz and lo[1] - 3 <= y <= hi[1] + 3:
            out.append(box['bp'].split('/')[-1])
    return sorted(set(out))[:8]
