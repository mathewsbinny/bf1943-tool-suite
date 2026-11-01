"""navaudit1943_checks: loading a level, its build and masks; lookups by position."""
import json
import math
import os
import time

import numpy as np

import navaudit1943 as A  # noqa: E402
import navkitjson1943 as nj  # noqa: E402
import navbuild  # noqa: E402  (bf1943-auto-navkit-common, a shared folder)
import soldier1943  # noqa: E402
from bfbc2core import game1943, materialmap1943, terrain  # noqa: E402

from .grids import Grid, Nav


# ------------------------------------------------------------------------------------------------ loading

def load(lv, log=print):
    import spawnmap
    from bfbc2core import navdata
    from bfbc2core import level1943
    from bfbc2core import water as water_mod
    p = A.level_paths(lv)
    st = A.current_build(lv)       # the checked build of the current preparation only (2026-10-30 review)
    game = game1943.find_game(st.get('game'))         # the copy the level was prepared from (2026-10-30: the default)
    g = game1943.Game(game, lv)
    hf_name = f'terrains/{lv}/{lv}.heightfield-000.res'
    hf = terrain.read(nj.prepared_heightfield(st, lv, g.data(hf_name)))    # the prepared one (second review)
    mm = materialmap1943.for_level(g, lv, hf)
    t = time.time()
    # BF1943 builds store nodes and outlines half the soldier's height (0.9 m) up
    nav = navdata.load(p['hpd'], hf, cache_dir=os.path.join(p['audit'], 'cache'),
                       y_offset=soldier1943.SETTINGS.y_offset)
    log(f'{lv}: build read ({time.time() - t:.0f} s): {len(nav.areas)} areas, {len(nav.node_pos)} nodes')
    exact = navbuild.read_cells(p['hpd'])
    log(f'{lv}: ' + ('cells file of the build read (area heights exact)' if exact is not None else
                     'no cells file for this build: area heights from the reader cells'))
    lvl = level1943.load(lv, game=game, log=lambda *a: None)
    m = spawnmap.spawn_map(game, lv)
    wpath = os.path.join(p['objects'], 'water_mesh.res')
    water = water_mod.read_mesh(wpath) if soldier1943.has_water(st) and os.path.isfile(wpath) else None
    masks = os.path.join(p['audit'], 'masks')
    grids = {}
    hashes = A.Hashes(lv)
    # every mask must be of the samples and build on disk (their SHA-256, stored in it): 2026-10-30 review, a base mask
    # was checked only by its surface count and the rule-off masks not at all
    for which, src in (('normal', 'samples.bin'), ('detailed', 'samples_02.bin')):
        if which == 'detailed' and not A.detailed_current(st):
            log(f'{lv}: the build has no 0.2 m samples of this preparation: no detailed grid')
            continue
        path = os.path.join(masks, f'{which}_base.npz')
        if not os.path.isfile(path):
            log(f'{lv}: no {which} base mask ({path}): navaudit1943.py masks {lv}')
            continue
        why = A.mask_stale(path, src, hashes)
        if why:
            raise nj.NavKitError(f'{why}: make the masks again (navaudit1943.py masks {lv})')
        z = np.load(path)
        s = A.load_samples(os.path.join(p['samples'], src))
        if int(z['surfaces']) != len(s.y):
            raise nj.NavKitError(f'{path}: made from another sampling run ({int(z["surfaces"])} surfaces, now {len(s.y)})')
        G = Grid(s, z['keep'], hf, water, '0.2 m' if which == 'detailed' else '0.8 m')
        for v in A.VARIANTS:
            vp = os.path.join(masks, f'{which}_{v}.npz')
            if v != 'base' and os.path.isfile(vp):
                why = A.mask_stale(vp, src, hashes)
                if why:
                    log(f'{why}: left out (navaudit1943.py masks {lv} --variants {v})')
                    continue
                G.variants[v] = np.load(vp)['keep']
        grids[which] = G
    if 'normal' not in grids:
        raise nj.NavKitError(f'{lv}: no normal base mask of this build: navaudit1943.py masks {lv}')
    xoff, zoff = st['sector_offsets']
    dset = {(int(k[:2], 16), int(k[2:], 16)) for k in st.get('detailed_sectors') or ()}
    built = {a.sector for a in nav.areas if a.detailed}
    dset_built = {((s_ >> 8) & 0xFF, s_ & 0xFF) for s_ in built}
    boxes = json.load(open(os.path.join(p['objects'], 'object_boxes.json'), encoding='utf-8'))
    inputs = {}
    for name, path in (('hpd', p['hpd']), ('samples.bin', os.path.join(p['samples'], 'samples.bin')),
                       ('samples_02.bin', os.path.join(p['samples'], 'samples_02.bin')),
                       ('object_boxes.json', os.path.join(p['objects'], 'object_boxes.json')),
                       ('ladders.json', os.path.join(p['objects'], 'ladders.json')),
                       ('water_mesh.res', wpath)):
        if os.path.isfile(path):
            inputs[name] = {'path': path, 'bytes': os.path.getsize(path), 'sha256': A.sha256(path)}
    inputs['game heightfield'] = {'name': hf_name, 'sha256': __import__('hashlib').sha256(g.data(hf_name)).hexdigest()}
    return dict(lv=lv, p=p, st=st, game=game, g=g, hf=hf, mm=mm, nav=Nav(nav, exact), lvl=lvl, map=m, water=water,
                entity_by_guid={e.guid.lower(): e for e in lvl.entities},
                grids=grids, xoff=xoff, zoff=zoff, dset=dset, dset_built=dset_built, boxes=boxes, inputs=inputs)


def sector_of(C, x, z):
    return int(math.floor(x / 100.0)) - C['xoff'], int(math.floor(z / 100.0)) - C['zoff']


def grid_at(C, x, z):
    if 'detailed' in C['grids'] and sector_of(C, x, z) in C['dset_built']:
        return C['grids']['detailed']
    return C['grids']['normal']


def objects_at(C, x, y, z, grow=0.5):
    out = []
    for b in C['boxes']:
        lo, hi = b['min'], b['max']
        if lo[0] - grow <= x <= hi[0] + grow and lo[2] - grow <= z <= hi[2] + grow and lo[1] - 3 <= y <= hi[1] + 3:
            out.append(b['bp'].split('/')[-1])
    return sorted(set(out))[:8]
