r"""NavKit (big-endian): an audit of a BF1943 navigation build against its own level - the level's required positions
(capture areas, soldier and vehicle spawns, repair stations, AA guns), the build's internal consistency, and the
samples it was built from. Nothing here changes a build; every input is hashed into the report.

  navaudit1943.py masks <level> [--variants base,headroom,lips,water,ladders] [--stage normal|detailed|both]
      the build's walkable surfaces, recomputed exactly as navbuild.stage computes them (the check: the base run's
      count equals build.log's "walkable kept"), and once per rule with that rule turned off - the surfaces each rule
      changed. Written to work/levels/<level>/audit/masks/<stage>_<variant>.npz, with the SHA-256 of the samples and
      the build they were made from: a mask of other samples or another build is made again (and the audit refuses
      it), and only the checked build of the level's current preparation is audited (navkitjson1943.build_refusal).
      The detailed stage only when the build used a current 0.2 m pass. One variant (both stages, most of it
      the detailed stage) takes about 6 min on Wake Island and 2-3 GB of memory, all five about 30 min per level:
      run them one at a time.
  navaudit1943.py audit <level> [--json out.json]
      the checks (needs the masks' base runs; the rule-off runs for the rule section): work/levels/<level>/audit/
      audit.json and audit.md.

Settings the audit adds (not in the game's data, not in NavKit): OBJECTIVE_REACH, FLOOR_DY, LAYER_WINDOW (below),
and navaudit1943_checks' (its config.py) BOAT_REACH, VEHICLE_REACH, MOUNT_DROP, AIRBORNE and USABLE_SHARE.
"""
import argparse
import json
import math
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import numpy.typing as npt

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import navkitjson1943 as nj  # noqa: E402
import navsample  # noqa: E402
import soldier1943  # noqa: E402
from bfbc2core import game1943  # noqa: E402

if TYPE_CHECKING:
    from navsample import Samples

type Log = Callable[[str], object]
type BoolArray = npt.NDArray[np.bool]

OBJECTIVE_REACH = 2.0     # m (horizontal): a required position passes when walkable ground on its own floor is this close.
                          # NavKit has no such value and the runtime's locate() takes its distance as a parameter
                          # (NAVIGATION_RUNTIME_DESIGN.md); 2 m is a soldier's arm's length plus his radius, chosen here
FLOOR_DY = 0.6            # m: a nav cell is on a position's floor when its ground is within this of the floor's height
LAYER_WINDOW = 3.0        # m: surfaces this far above or below a position's floor are its layers (check 5)
# each variant's rule turned off: navwalk.walkable's arguments, or an input left out
VARIANTS: dict[str, dict[str, Any]] = {
    'base': {},
    'headroom': {'headroom_band': None},
    'lips': {'lip_width': 0},
    'water': {'water': None},
    'ladders': {'ladders': None},
}
WALKABLE_ARGS = ('headroom_band', 'lip_width')


sha256 = nj.sha256                 # (navkitjson1943: one chunked SHA-256 for every tool)


def load_samples(path: str) -> 'Samples':
    """A navsample version-1 file (navsample1943's output) as navsample.Samples, read with numpy: the same arrays
    navsample.load gives (normals / 127 as float32), in a fraction of its time and memory; as it does, an interrupted
    run keeps its finished columns. ValueError for a file that is not one (2026-10-31 fuzzing: struct.error on a cut
    header, a 2^31-column header that made tables of 16 GiB and more; review: an IndexError where a run stopped
    inside a column's records: navsample.load keeps the finished columns there, this read past the end)."""
    data = open(path, 'rb').read()
    if len(data) < navsample.HEADER.size:
        raise ValueError(f'{path}: not a version-1 navsample file ({len(data)} bytes, shorter than its header)')
    magic, version, pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_bottom, cap_top, ray_radius, _ = \
        navsample.HEADER.unpack_from(data, 0)
    if magic != b'ONS1' or version != 1:
        raise ValueError(f'{path}: not a version-1 navsample file')
    if nx < 1 or nz < 1 or nx * nz > navsample.MAX_COLUMNS:
        raise ValueError(f'{path}: a grid of {nx} x {nz} columns (at least 1 x 1, at most {navsample.MAX_COLUMNS})')
    column_count = nx * nz
    raw = np.frombuffer(data, np.uint8)
    pos = navsample.HEADER.size
    column_pos = np.empty(column_count, np.int64)      # each column's count byte
    counts = np.empty(column_count, np.int64)
    c = 0
    while c < column_count and pos < len(data):    # each column: a count byte, then 8-byte records
        n = data[pos]
        column_pos[c], counts[c] = pos, n
        pos += 1 + 8 * n
        c += 1
    if pos > len(data):                           # the run stopped inside the last column's records: as navsample.load,
        c -= 1                                    # keep the finished columns only
        pos = int(column_pos[c])
    counts[c:] = 0
    column_pos[c:] = pos
    start = np.zeros(column_count + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    total = int(start[-1])
    within = np.arange(total, dtype=np.int64) - np.repeat(start[:-1], counts)   # each record's index in its column
    record_pos = np.repeat(column_pos + 1, counts) + 8 * within
    del within
    y = np.empty(total, np.float32)
    normal = np.empty((total, 3), np.float32)
    flags = np.empty(total, np.uint8)
    for first in range(0, total, 1 << 22):         # 4 M records at a time
        chunk = record_pos[first:first + (1 << 22)]
        records = raw[chunk[:, None] + np.arange(8)]
        y[first:first + len(chunk)] = records[:, :4].copy().view('<f4')[:, 0]
        normal[first:first + len(chunk)] = records[:, 4:7].view(np.int8) / np.float32(127.0)
        flags[first:first + len(chunk)] = records[:, 7]
    return navsample.Samples(pitch, min_x, min_z, nx, nz, y_top, y_bottom, radius, cap_bottom, cap_top, ray_radius,
                             start, y, normal, flags, None)


def current_build(lv: str) -> dict[str, Any]:
    """navkit.json, refused (NavKitError) unless the build on disk is the checked build of the current preparation
    (2026-10-30 review: the audit and its masks read whatever <level>.hpd was on disk)."""
    st = nj.read(lv)
    hpd = level_paths(lv)['hpd']
    why = nj.build_refusal(st, lv, hpd if os.path.isfile(hpd) else None)
    if why:
        raise nj.NavKitError(why)
    if not os.path.isfile(hpd):
        raise nj.NavKitError(f'{lv}: no build (missing {hpd}): navkit.py build {lv}')
    return st


def detailed_current(st: dict[str, Any]) -> bool:
    """Whether the build was made from a current 0.2 m pass (navkit.json's build_from names one)."""
    build_from = st.get('build_from') or {}
    return bool(build_from.get('samples_02_id') or build_from.get('samples_02'))


class Hashes:
    """The SHA-256 of a level's samples and build, each read once."""

    def __init__(self, lv: str) -> None:
        self.p: dict[str, str] = level_paths(lv)
        self.done: dict[str, str] = {}      # path -> its SHA-256

    def __call__(self, name: str) -> str:
        """The SHA-256 of 'hpd' (the build) or of a samples file by name."""
        path = self.p['hpd'] if name == 'hpd' else os.path.join(self.p['samples'], name)
        if path not in self.done:
            self.done[path] = sha256(path)
        return self.done[path]


def mask_stale(path: str, src: str, hashes: Hashes) -> str | None:
    """Why a mask file is not of the current samples and build (None when it is)."""
    try:
        mask_file = np.load(path)
        have = (str(mask_file['samples_sha256']), str(mask_file['hpd_sha256']))
    except (OSError, KeyError, ValueError) as e:
        return f'{path}: unreadable ({e})'
    if have[0] != hashes(src):
        return f'{path}: made from other samples than {src}'
    if have[1] != hashes('hpd'):
        return f'{path}: made for another build'
    return None


def level_paths(lv: str) -> dict[str, str]:
    """The level's work folder and its parts: root, objects, samples, navkit, audit, hpd, settings."""
    root = game1943.level_work(lv)
    return {'root': root, 'objects': os.path.join(root, 'objects'), 'samples': os.path.join(root, 'samples'),
            'navkit': os.path.join(root, 'navkit'), 'audit': os.path.join(root, 'audit'),
            'hpd': os.path.join(root, 'navkit', f'{lv}.hpd'), 'settings': os.path.join(root, 'navkit', 'navkit.json')}


def stage_keep(lv: str, which: str, variant: str = 'base',
               normal_keep: 'tuple[Samples, BoolArray] | None' = None, log: Log = print
               ) -> 'tuple[Samples, BoolArray]':
    """The kept walkable surfaces (bool per surface) of one stage of a build, as navbuild.stage computes them for
    navkit.py build: which = 'normal' (the 0.8 m run) or 'detailed' (the 0.2 m run, seeded from the normal stage's
    kept ground in the detailed sectors - normal_keep). Mirrors navbuild.build / merge_detailed / stage up to
    navwalk.walkable, with the variant's rule turned off. Returns (samples, keep)."""
    import navbuild
    import navwalk
    from bfbc2core import water as water_mod
    paths = level_paths(lv)
    st = nj.read(lv)
    objects, samples_dir = paths['objects'], paths['samples']
    settings = soldier1943.SETTINGS                 # what navkit.py build passes
    opts = dict(VARIANTS[variant])
    # navwalk's rules as the build has them, the variant's turned off (until 2026-10-31 the module constants were
    # rewritten for the run and restored after it)
    rules: dict[str, Any] = {'headroom_band': settings.headroom_band, 'lip_width': navwalk.LIP_WIDTH}
    rules.update((k, opts.pop(k)) for k in WALKABLE_ARGS if k in opts)
    s = load_samples(os.path.join(samples_dir, 'samples.bin' if which == 'normal' else 'samples_02.bin'))
    high = load_samples(os.path.join(samples_dir, 'samples_high.bin' if which == 'normal' else 'samples_02_high.bin'))
    with open(os.path.join(objects, 'soldier_spawns.json'), encoding='utf-8') as f:
        seeds = json.load(f)
    with open(os.path.join(objects, 'ladders.json'), encoding='utf-8') as f:
        ladders = json.load(f)
    jumps_path = os.path.join(paths['navkit'], 'jump_links.json')      # navkit.py build adds them (jumplinks1943)
    if os.path.isfile(jumps_path):
        with open(jumps_path, encoding='utf-8') as f:
            ladders += json.load(f)
    if 'ladders' in opts:
        ladders = opts['ladders']
    water_path = os.path.join(objects, 'water_mesh.res')
    has_mesh = soldier1943.has_water(st) and os.path.isfile(water_path)
    mesh = water_mod.read_mesh(water_path) if has_mesh else None
    if 'water' in opts:
        mesh = None
    hand = {}                                       # navwalk.walkable's hand-made rules: deep water
    if mesh is not None:
        hand['force_unwalkable'] = navbuild.deep_water(s, mesh, soldier1943.WATER_DEPTH)
    seed_points = []
    if which == 'detailed':
        xoff, zoff = st['sector_offsets']
        dset = {(int(k[:2], 16), int(k[2:], 16)) for k in st.get('detailed_sectors') or ()}
        # the detailed stage is seeded from the normal one: masks() passes normal_keep
        normal_s, normal_kept = cast('tuple[Samples, BoolArray]', normal_keep)
        col = np.repeat(np.arange(normal_s.nx * normal_s.nz), np.diff(normal_s.start))
        for k in np.nonzero(normal_kept)[0]:
            c = col[k]
            x = normal_s.min_x + (c % normal_s.nx) * normal_s.pitch
            z = normal_s.min_z + (c // normal_s.nx) * normal_s.pitch
            if (int(math.floor(x / 100.0)) - xoff, int(math.floor(z / 100.0)) - zoff) in dset:
                seed_points.append((x, float(normal_s.y[k]), z))
    tris = np.load(os.path.join(objects, 'forbid_tris.npy'))
    dynamic = np.load(os.path.join(objects, 'dynamic_tris.npy'))
    exclude = np.load(os.path.join(objects, 'exclude_tris.npy'))
    exclude_footprint = np.load(os.path.join(objects, 'exclude_footprint_tris.npy'))
    t0 = time.time()
    # with return_gradients and return_stairs: (keep, gradient x, gradient z, stairs)
    keep, _gx, _gz, _stair = cast('tuple[BoolArray, Any, Any, Any]', navwalk.walkable(
        s, tris, slope=settings.slope_max, step=soldier1943.STEP, dynamic_tris=dynamic, high_capsule=high,
        return_gradients=True, return_stairs=True, narrow_capsule=None, stair_tris=None, exclude_tris=exclude,
        exclude_footprint_tris=exclude_footprint, extra_seeds=list(seeds) + seed_points, soldier_points=seeds,
        ladders=ladders, **hand, anchors=list(seeds) + seed_points, **rules))
    log(f'{lv} {which} {variant}: {len(s.y)} surfaces, {int(keep.sum())} walkable kept ({time.time() - t0:.0f} s)')
    return s, keep


def masks(lv: str, variants: Sequence[str], stages: Sequence[str], log: Log = print) -> None:
    """The masks of the checked build of the current preparation; a mask file there of other samples or another build
    is made again (2026-10-30 review: any file there was kept)."""
    st = current_build(lv)
    paths = level_paths(lv)
    out = os.path.join(paths['audit'], 'masks')
    os.makedirs(out, exist_ok=True)
    samples_dir = paths['samples']
    hashes = Hashes(lv)
    if 'detailed' in stages and not detailed_current(st):
        log(f'{lv}: the build has no 0.2 m samples of this preparation (its detailed sectors are built at 0.8 m): no '
            f'detailed masks')
        stages = [stage for stage in stages if stage != 'detailed']
    normal_base = None                              # (samples, keep) of the normal stage's base run
    for which in stages:
        src = 'samples.bin' if which == 'normal' else 'samples_02.bin'
        for variant in variants:
            path = os.path.join(out, f'{which}_{variant}.npz')
            if os.path.isfile(path):
                why = mask_stale(path, src, hashes)
                if why is None:
                    log(f'{path}: kept from an earlier run (of these samples and this build)')
                    continue
                log(f'{why}: made again')
            normal = None
            if which == 'detailed':
                if normal_base is None:
                    base_path = os.path.join(out, 'normal_base.npz')
                    base_current = os.path.isfile(base_path) and mask_stale(base_path, 'samples.bin', hashes) is None
                    base_file = np.load(base_path) if base_current else None
                    normal_s = load_samples(os.path.join(samples_dir, 'samples.bin'))
                    if base_file is not None:
                        normal_kept = base_file['keep']
                    else:
                        normal_kept = stage_keep(lv, 'normal', 'base', log=log)[1]
                    normal_base = (normal_s, normal_kept)
                normal = normal_base
            s, keep = stage_keep(lv, which, variant, normal_keep=normal, log=log)
            if which == 'normal' and variant == 'base':
                normal_base = (s, keep)
            np.savez_compressed(path, keep=keep, surfaces=len(s.y), samples=src,
                                samples_sha256=hashes(src), hpd_sha256=hashes('hpd'))
            log(f'wrote {path}')
            del s, keep


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='navaudit1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    masks_cmd = sub.add_parser('masks')
    masks_cmd.add_argument('level')
    masks_cmd.add_argument('--variants', default=','.join(VARIANTS))
    masks_cmd.add_argument('--stage', default='both', choices=['normal', 'detailed', 'both'])
    audit_cmd = sub.add_parser('audit')
    audit_cmd.add_argument('level')
    audit_cmd.add_argument('--json')
    args = parser.parse_args(argv[1:])
    if args.cmd == 'masks':
        chosen = [v for v in args.variants.split(',') if v]
        bad = [v for v in chosen if v not in VARIANTS]
        if bad:
            raise SystemExit(f'unknown variant(s) {bad}; known: {", ".join(VARIANTS)}')
        masks(args.level, chosen, ['normal', 'detailed'] if args.stage == 'both' else [args.stage])
    else:
        import navaudit1943_checks
        navaudit1943_checks.run(args.level, json_out=args.json)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('navaudit1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
