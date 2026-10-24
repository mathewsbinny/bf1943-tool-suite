r"""NavKit (big-endian): an audit of a BF1943 navigation build against its own level - the level's required positions
(capture areas, soldier and vehicle spawns, repair stations, AA guns), the build's internal consistency, and the
samples it was built from. Nothing here changes a build; every input is hashed into the report.

  navaudit1943.py masks <level> [--variants base,headroom,lips,water,ladders] [--stage normal|detailed|both]
      the build's walkable surfaces, recomputed exactly as navbuild.stage computes them (the check: the base run's
      count equals build.log's "walkable kept"), and once per rule with that rule turned off - the surfaces each rule
      changed. Written to work/levels/<level>/audit/masks/<stage>_<variant>.npz. One variant (both stages, most of it
      the detailed stage) takes about 6 min on Wake Island and 2-3 GB of memory, all five about 30 min per level:
      run them one at a time.
  navaudit1943.py audit <level> [--json out.json]
      the checks (needs the masks' base runs; the rule-off runs for the rule section): work/levels/<level>/audit/
      audit.json and audit.md.

Settings the audit adds (not in the game's data, not in NavKit): OBJECTIVE_REACH, FLOOR_DY, LAYER_WINDOW (below),
and navaudit1943_checks.py's BOAT_REACH, VEHICLE_REACH, MOUNT_DROP, AIRBORNE and USABLE_SHARE.
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-spawn-map'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
import navsample  # noqa: E402
import soldier1943  # noqa: E402
from bfbc2core import game1943  # noqa: E402

OBJECTIVE_REACH = 2.0     # m (horizontal): a required position passes when walkable ground on its own floor is this close.
                          # NavKit has no such value and the runtime's locate() takes its distance as a parameter
                          # (NAVIGATION_RUNTIME_DESIGN.md); 2 m is a soldier's arm's length plus his radius, chosen here
FLOOR_DY = 0.6            # m: a nav cell is on a position's floor when its ground is within this of the floor's height
LAYER_WINDOW = 3.0        # m: surfaces this far above or below a position's floor are its layers (check 5)
VARIANTS = {
    'base': {},
    'headroom': {'navwalk.HEADROOM_BAND': None},
    'lips': {'navwalk.LIP_WIDTH': 0},
    'water': {'water': None},
    'ladders': {'ladders': None},
}


def sha256(path, block=1 << 22):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_samples(path):
    """A navsample version-1 file (navsample1943's output) as navsample.Samples, read with numpy: the same arrays
    navsample.load gives (normals / 127 as float32), in a fraction of its time and memory."""
    data = open(path, 'rb').read()
    magic, version, pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_b, cap_t, ray_r, _ = \
        navsample.HEADER.unpack_from(data, 0)
    if magic != b'ONS1' or version != 1:
        raise ValueError(f'{path}: not a version-1 navsample file')
    cols = nx * nz
    raw = np.frombuffer(data, np.uint8)
    pos = navsample.HEADER.size
    colpos = np.empty(cols, np.int64)
    counts = np.empty(cols, np.int64)
    c = 0
    while c < cols and pos < len(data):           # each column: a count byte, then 8-byte records
        n = data[pos]
        colpos[c], counts[c] = pos, n
        pos += 1 + 8 * n
        c += 1
    counts[c:] = 0
    colpos[c:] = pos
    start = np.zeros(cols + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    total = int(start[-1])
    k = np.arange(total, dtype=np.int64) - np.repeat(start[:-1], counts)
    off = np.repeat(colpos + 1, counts) + 8 * k
    del k
    y = np.empty(total, np.float32)
    normal = np.empty((total, 3), np.float32)
    flags = np.empty(total, np.uint8)
    for a in range(0, total, 1 << 22):
        o = off[a:a + (1 << 22)]
        rec = raw[o[:, None] + np.arange(8)]
        y[a:a + len(o)] = rec[:, :4].copy().view('<f4')[:, 0]
        normal[a:a + len(o)] = rec[:, 4:7].view(np.int8) / np.float32(127.0)
        flags[a:a + len(o)] = rec[:, 7]
    return navsample.Samples(pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_b, cap_t, ray_r, start, y,
                             normal, flags, None)


def level_paths(lv):
    root = game1943.level_work(lv)
    return {'root': root, 'objects': os.path.join(root, 'objects'), 'samples': os.path.join(root, 'samples'),
            'navkit': os.path.join(root, 'navkit'), 'audit': os.path.join(root, 'audit'),
            'hpd': os.path.join(root, 'navkit', f'{lv}.hpd'), 'settings': os.path.join(root, 'navkit', 'navkit.json')}


def _set(name, value, saved):
    import navbuild
    import navwalk
    mod, attr = name.split('.')
    m = {'navwalk': navwalk, 'navbuild': navbuild}[mod]
    saved.append((m, attr, getattr(m, attr)))
    setattr(m, attr, value)


def stage_keep(lv, which, variant='base', normal_keep=None, log=print):
    """The kept walkable surfaces (bool per surface) of one stage of a build, as navbuild.stage computes them for
    navkit.py build: which = 'normal' (the 0.8 m run) or 'detailed' (the 0.2 m run, seeded from the normal stage's
    kept ground in the detailed sectors - normal_keep). Mirrors navbuild.build / merge_detailed / stage up to
    navwalk.walkable, with the variant's rule turned off. Returns (samples, keep)."""
    import navbuild
    import navwalk
    from bfbc2core import water as water_mod
    p = level_paths(lv)
    st = json.load(open(p['settings'], encoding='utf-8'))
    o, sm = p['objects'], p['samples']
    soldier1943.configure()
    saved = []
    opts = dict(VARIANTS[variant])
    try:
        for k, v in list(opts.items()):
            if '.' in k:
                _set(k, v, saved)
                del opts[k]
        navbuild.set_pitch(0.8 if which == 'normal' else navbuild.DETAILED_PITCH)
        s = load_samples(os.path.join(sm, 'samples.bin' if which == 'normal' else 'samples_02.bin'))
        hi = load_samples(os.path.join(sm, 'samples_high.bin' if which == 'normal' else 'samples_02_high.bin'))
        seeds = json.load(open(os.path.join(o, 'soldier_spawns.json')))
        ladders = json.load(open(os.path.join(o, 'ladders.json')))
        jp = os.path.join(p['navkit'], 'jump_links.json')            # navkit.py build adds them (jumplinks1943)
        if os.path.isfile(jp):
            ladders += json.load(open(jp, encoding='utf-8'))
        if 'ladders' in opts:
            ladders = opts['ladders']
        wpath = os.path.join(o, 'water_mesh.res')
        mesh = water_mod.read_mesh(wpath) if os.path.isfile(wpath) else None
        if 'water' in opts:
            mesh = None
        hand = {}
        if mesh is not None:
            hand['force_unwalkable'] = navbuild.deep_water(s, mesh, soldier1943.WATER_DEPTH)
        seed_points = []
        if which == 'detailed':
            xoff, zoff = st['sector_offsets']
            dset = {(int(k[:2], 16), int(k[2:], 16)) for k in st.get('detailed_sectors') or ()}
            sa, ka = normal_keep
            col = np.repeat(np.arange(sa.nx * sa.nz), np.diff(sa.start))
            for k in np.nonzero(ka)[0]:
                c = col[k]
                x, z = sa.min_x + (c % sa.nx) * sa.pitch, sa.min_z + (c // sa.nx) * sa.pitch
                if (int(math.floor(x / 100.0)) - xoff, int(math.floor(z / 100.0)) - zoff) in dset:
                    seed_points.append((x, float(sa.y[k]), z))
        tris = np.load(os.path.join(o, 'forbid_tris.npy'))
        dyn = np.load(os.path.join(o, 'dynamic_tris.npy'))
        ex = np.load(os.path.join(o, 'exclude_tris.npy'))
        exf = np.load(os.path.join(o, 'exclude_footprint_tris.npy'))
        t0 = time.time()
        keep, _gx, _gz, _stair = navwalk.walkable(
            s, tris, slope=navbuild.SLOPE_MAX, step=soldier1943.STEP, dynamic_tris=dyn, high_capsule=hi,
            return_gradients=True, return_stairs=True, narrow_capsule=None, stair_tris=None, exclude_tris=ex,
            exclude_footprint_tris=exf, extra_seeds=list(seeds) + seed_points, soldier_points=seeds,
            ladders=ladders, **hand, anchors=list(seeds) + seed_points)
        log(f'{lv} {which} {variant}: {len(s.y)} surfaces, {int(keep.sum())} walkable kept ({time.time() - t0:.0f} s)')
        return s, keep
    finally:
        for m, attr, v in reversed(saved):
            setattr(m, attr, v)
        navbuild.set_pitch(0.8)


def masks(lv, variants, stages, log=print):
    p = level_paths(lv)
    out = os.path.join(p['audit'], 'masks')
    os.makedirs(out, exist_ok=True)
    sm = p['samples']
    normal_base = None
    for which in stages:
        for v in variants:
            path = os.path.join(out, f'{which}_{v}.npz')
            if os.path.isfile(path):
                log(f'{path}: kept from an earlier run')
                continue
            normal = None
            if which == 'detailed':
                if normal_base is None:
                    z = np.load(os.path.join(out, 'normal_base.npz')) if os.path.isfile(os.path.join(out, 'normal_base.npz')) else None
                    sa = load_samples(os.path.join(sm, 'samples.bin'))
                    ka = z['keep'] if z is not None else stage_keep(lv, 'normal', 'base', log=log)[1]
                    normal_base = (sa, ka)
                normal = normal_base
            s, keep = stage_keep(lv, which, v, normal_keep=normal, log=log)
            if which == 'normal' and v == 'base':
                normal_base = (s, keep)
            src = 'samples.bin' if which == 'normal' else 'samples_02.bin'
            np.savez_compressed(path, keep=keep, surfaces=len(s.y), samples=src,
                                samples_sha256=sha256(os.path.join(sm, src)), hpd_sha256=sha256(p['hpd']))
            log(f'wrote {path}')
            del s, keep


def main(argv):
    ap = argparse.ArgumentParser(prog='navaudit1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('masks')
    m.add_argument('level')
    m.add_argument('--variants', default=','.join(VARIANTS))
    m.add_argument('--stage', default='both', choices=['normal', 'detailed', 'both'])
    a = sub.add_parser('audit')
    a.add_argument('level')
    a.add_argument('--json')
    args = ap.parse_args(argv[1:])
    if args.cmd == 'masks':
        vs = [v for v in args.variants.split(',') if v]
        bad = [v for v in vs if v not in VARIANTS]
        if bad:
            raise SystemExit(f'unknown variant(s) {bad}; known: {", ".join(VARIANTS)}')
        masks(args.level, vs, ['normal', 'detailed'] if args.stage == 'both' else [args.stage])
    else:
        import navaudit1943_checks
        navaudit1943_checks.run(args.level, json_out=args.json)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navaudit1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
