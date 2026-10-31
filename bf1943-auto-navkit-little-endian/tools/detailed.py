"""NavKit: which sectors to build as detailed zones (pitch 0.2), from the level alone - no DICE path data.

A sector is detailed when
  - a placed object whose blueprint sets NeedsDetailedPathfinding has its collision box within MARGIN m of the
    sector's square (the level's own trigger - the field DICE's generator reads), or
  - at least STACKED_MIN columns of NavKit's 0.8 m samples in the sector hold two standable surfaces at least 2 m apart
    (stacked floors: what the 0.8 m grid cannot separate well).
Scored against DICE's choice on the 14 levels that have both (2026-10-21, with ForbidPathfinding, flags[0], which this
tested until 2026-10-31): it found 445 of DICE's 481 detailed sectors (93 %; the flag alone 84 %) and added 243 of about
2,500 that DICE builds at 0.8 m. The flag DICE's zones follow is NeedsDetailedPathfinding (flags[5]): on the 13 BFBC2
campaign levels the object rule alone finds 396 of DICE's 458 detailed sectors with 29 extra, against 387 with 157 extra
for ForbidPathfinding (README, "Second review"). The misses have neither a flagged object nor stacked floors and stay
at 0.8 m; the additions cost sampling and build time, not quality.

Usage: detailed.py <level> [--samples <0.8 m samples.bin>] [--offsets X,Z] [--score]   (--score: against DICE's file;
--offsets: by default the level's navkit.json)
"""
import json
import math
import os
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import levelobjects  # noqa: E402
import navsample  # noqa: E402

MARGIN = 5.0           # m around a flagged object's collision box
STACKED_MIN = 100      # stacked-floor columns (0.8 m samples) that make a sector detailed
STACK_GAP = 2.0        # m between the lowest and highest standable surface of a column
SLOPE_MAX = 40.0


def flagged_sectors(level_dir, common, xoff, zoff, margin=MARGIN):
    """Sectors (X, Z) within margin of a NeedsDetailedPathfinding object's collision box (objects.json). ValueError on
    an objects.json from before that flag was extracted (2026-10-31): prepare the level again."""
    bases = [b for b in [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + list(common) if os.path.isdir(b)]
    hv = levelobjects.havok_index(bases)
    out, cache = set(), {}
    k = levelobjects.FLAG_DETAILED
    with open(os.path.join(level_dir, 'objects.json'), encoding='utf-8') as f:
        objects = json.load(f)
    if any(o.get('flags') and len(o['flags']) <= k for o in objects):
        raise ValueError(f'{os.path.join(level_dir, "objects.json")} is from before NeedsDetailedPathfinding was '
                         f'extracted: run navkit.py prepare on the level again')
    for o in objects:
        if not (o.get('flags') and o['flags'][k]):
            continue
        bp = o['bp'].lower()
        if bp not in cache:
            cache[bp] = levelobjects.triangles(hv[bp]) if bp in hv else None
        t = np.array(o['m'][12:15])
        if cache[bp] is None:
            x0, z0, x1, z1 = t[0], t[2], t[0], t[2]
        else:
            V = cache[bp].reshape(-1, 3) @ np.array([o['m'][0:3], o['m'][4:7], o['m'][8:11]]) + t
            x0, z0, x1, z1 = V[:, 0].min(), V[:, 2].min(), V[:, 0].max(), V[:, 2].max()
        for X in range(int(math.floor((x0 - margin) / 100)) - xoff, int(math.floor((x1 + margin) / 100)) - xoff + 1):
            for Z in range(int(math.floor((z0 - margin) / 100)) - zoff, int(math.floor((z1 + margin) / 100)) - zoff + 1):
                out.add((X, Z))
    return out


def stacked_columns(samples_path, xoff, zoff):
    """{sector (X, Z): columns holding standable surfaces at least STACK_GAP apart} from 0.8 m samples."""
    s, _ = navsample.load(samples_path)
    ok = ((s.flags & 3) == 0) & (s.normal[:, 1] >= math.cos(math.radians(SLOPE_MAX)) - 0.01)
    col = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
    y = np.where(ok, s.y.astype(float), np.nan)
    lo = np.full(s.nx * s.nz, np.inf)
    hi = np.full(s.nx * s.nz, -np.inf)
    sel = ok
    np.minimum.at(lo, col[sel], y[sel])
    np.maximum.at(hi, col[sel], y[sel])
    stacked = np.nonzero(hi - lo >= STACK_GAP)[0]
    X, Z = navsample.column_sectors(s, stacked)     # in whole centimetres: the same sectors navbuild.stage gives
    return Counter(zip((X - xoff).tolist(), (Z - zoff).tolist()))


def choose(level_dir, common, samples_path, xoff, zoff, margin=MARGIN, stacked_min=STACKED_MIN):
    """(sorted [(X, Z)], {'flag': n, 'stacked': n}) - the detailed sectors of a level."""
    flag = flagged_sectors(level_dir, common, xoff, zoff, margin)
    stacked = {k for k, n in stacked_columns(samples_path, xoff, zoff).items() if n >= stacked_min} if samples_path else set()
    return sorted(flag | stacked), {'flag': len(flag), 'stacked': len(stacked), 'stacked only': len(stacked - flag)}


def sampled_record_path(p02):
    """The record a pitch-0.2 sampling run leaves next to its samples: which sectors it covered, at which offsets."""
    return p02[:-4] + '.sectors.json'


def write_sampled_record(p02, sectors, xoff, zoff, pending=False):
    """pending: written as a 0.2 m run starts, replaced by the covered sectors when both its runs finish - so the
    samples of a run that failed part-way are never taken for good ones."""
    tmp = sampled_record_path(p02) + '.tmp'
    rec = {'xoff': xoff, 'zoff': zoff, 'sectors': sorted([int(i), int(j)] for i, j in sectors)}
    if pending:
        rec['pending'] = True
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(rec, f)
    os.replace(tmp, sampled_record_path(p02))


def sampled_problem(p02, samples08, sectors, xoff, zoff):
    """Why the pitch-0.2 samples p02 do not fit the detailed sectors chosen now (None: they fit). With the run's record:
    a finished run (not "pending": one that failed part-way leaves that), the same sector offsets, and every chosen
    sector among those it covered. Without one (a run from before the record): the 0.8 m samples the choice came
    from must not be newer than the 0.2 m ones."""
    rec = sampled_record_path(p02)
    if os.path.isfile(rec):
        try:
            with open(rec, encoding='utf-8') as f:
                r = json.load(f)
        except ValueError:
            return f'its record {os.path.basename(rec)} is damaged'
        if r.get('pending'):
            return 'the last 0.2 m sampling run did not finish'
        if (r.get('xoff'), r.get('zoff')) != (xoff, zoff):
            return f'sampled with sector offsets {r.get("xoff")},{r.get("zoff")}, the level now has {xoff},{zoff}'
        missing = sorted({(int(i), int(j)) for i, j in sectors} - {(int(i), int(j)) for i, j in r.get('sectors', ())})
        if missing:
            return f'{len(missing)} detailed sector(s) are not in the 0.2 m samples, e.g. {missing[0]}'
        return None
    if samples08 and os.path.isfile(samples08) and os.path.getmtime(samples08) > os.path.getmtime(p02):
        return 'the 0.8 m samples (which choose the detailed sectors) are newer than the 0.2 m samples'
    return None


def score(chosen, dice_pathdata):
    """Against DICE's choice (evaluation only): (found, DICE detailed, chosen sectors DICE builds at 0.8)."""
    import hpdtool
    import kymesh
    det, norm = set(), set()
    for s in hpdtool.parse(open(dice_pathdata, 'rb').read()).sectors:
        (det if abs(kymesh.parse_mesh(s.mesh).pitch - 0.2) < 1e-3 else norm).add((s.id >> 8, s.id & 0xff))
    c = set(chosen)
    return len(det & c), len(det), len(norm & c)


def offsets_of(root, lv, given=None):
    """(xoff, zoff): --offsets X,Z when given, else the level's navkit.json (navkit.py prepare); ValueError if neither."""
    if given:
        try:
            x, z = (int(v) for v in given.split(','))
            return x, z
        except ValueError:
            raise ValueError(f'--offsets {given!r}: give X,Z (two whole numbers)')
    p = os.path.join(root, 'work', 'navkit', lv, 'navkit.json')
    if not os.path.isfile(p):
        raise ValueError(f'{lv}: no --offsets X,Z and no {p} (run navkit.py prepare {lv})')
    with open(p, encoding='utf-8') as f:
        s = json.load(f)
    return int(s['xoff']), int(s['zoff'])


def main(argv):
    if any(a in ('-h', '--help') for a in argv[1:]):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 1
    lv = argv[1].lower()
    opts = dict(zip(argv[2:], argv[3:]))
    root = os.environ.get('OPFOR_ROOT') or r'C:\Games\Project OPFOR'
    L = os.path.join(root, 'work', 'levels', lv)
    common = [os.path.join(root, 'work', 'navkit', 'common', c) for c in ('mp_common', 'sp_common')]
    try:
        xoff, zoff = offsets_of(root, lv, opts.get('--offsets'))
    except ValueError as e:
        print('error:', e)
        return 2
    chosen, why = choose(L, common, opts.get('--samples'), xoff, zoff)
    print(f'{lv}: {len(chosen)} detailed sectors ({why})')
    if '--score' in argv:
        import navparity
        p = navparity.reference_of(root, lv)     # DICE's file: never NavKit's own installed one (.opfor-none)
        if not os.path.isfile(p):
            print(f'error: {lv} has no original path data to score against')
            return 1
        f, d, x = score(chosen, p)
        print(f'  against DICE: {f} of its {d} detailed sectors, {x} more')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
