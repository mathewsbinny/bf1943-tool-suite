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
  A BFBC2 level of Project OPFOR (OPFOR_ROOT, else C:/Games/Project OPFOR: its work/levels/<level>). For BF1943
  levels use the big-endian NavKit's detailed1943.py, which applies this rule (stacked_columns with BF1943's slope).
"""
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Iterable

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)     # navparity (--score) is the little-endian NavKit's
import levelobjects  # noqa: E402
import navsample  # noqa: E402

MARGIN = 5.0           # m around a flagged object's collision box
STACKED_MIN = 100      # stacked-floor columns (0.8 m samples) that make a sector detailed
STACK_GAP = 2.0        # m between the lowest and highest standable surface of a column
SLOPE_MAX = 40.0


def flagged_sectors(level_dir: str, common: Iterable[str], xoff: int, zoff: int,
                    margin: float = MARGIN) -> set[tuple[int, int]]:
    """Sectors (X, Z) within margin of a NeedsDetailedPathfinding object's collision box (objects.json). ValueError on
    an objects.json from before that flag was extracted (2026-10-31): prepare the level again."""
    folders = [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + list(common)
    bases = [base for base in folders if os.path.isdir(base)]
    havok = levelobjects.havok_index(bases)
    out, cache = set(), {}
    detailed_flag = levelobjects.FLAG_DETAILED
    with open(os.path.join(level_dir, 'objects.json'), encoding='utf-8') as f:
        objects = json.load(f)
    if any(obj.get('flags') and len(obj['flags']) <= detailed_flag for obj in objects):
        raise ValueError(f'{os.path.join(level_dir, "objects.json")} is from before NeedsDetailedPathfinding was '
                         f'extracted: run navkit_le.py prepare on the level again')
    for obj in objects:
        if not (obj.get('flags') and obj['flags'][detailed_flag]):
            continue
        blueprint = obj['bp'].lower()
        if blueprint not in cache:
            cache[blueprint] = levelobjects.triangles(havok[blueprint]) if blueprint in havok else None
        origin = np.array(obj['m'][12:15])
        if cache[blueprint] is None:
            x0, z0, x1, z1 = origin[0], origin[2], origin[0], origin[2]
        else:
            rotation = np.array([obj['m'][0:3], obj['m'][4:7], obj['m'][8:11]])
            world = cache[blueprint].reshape(-1, 3) @ rotation + origin
            x0, z0, x1, z1 = world[:, 0].min(), world[:, 2].min(), world[:, 0].max(), world[:, 2].max()
        # X, Z: sector indices (100 m squares less the level's sector offsets)
        for X in range(int(math.floor((x0 - margin) / 100)) - xoff, int(math.floor((x1 + margin) / 100)) - xoff + 1):
            for Z in range(int(math.floor((z0 - margin) / 100)) - zoff,
                           int(math.floor((z1 + margin) / 100)) - zoff + 1):
                out.add((X, Z))
    return out


def stacked_columns(samples_path: str, xoff: int, zoff: int, slope_max: float = SLOPE_MAX,
                    cm_sectors: bool = True) -> Counter[tuple[int, int]]:
    """{sector (X, Z): columns holding standable surfaces at least STACK_GAP apart} from 0.8 m samples. slope_max: the
    steepest standable surface (deg; BF1943's soldier: soldier1943.SLOPE_MAX). cm_sectors: a column's sector in whole
    centimetres (navsample.column_sectors, as navbuild's default Settings and BF1943's since 2026-11); False: from its
    float position, as the big-endian copy counted until 2026-10-31 and BF1943's builds until 2026-11 - on the four
    BF1943 maps the two put stacked columns on either side of a sector line, the chosen sectors are the same."""
    s, _ = navsample.load(samples_path)
    ok = ((s.flags & 3) == 0) & (s.normal[:, 1] >= math.cos(math.radians(slope_max)) - 0.01)
    column_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start))
    y = np.where(ok, s.y.astype(float), np.nan)
    lo = np.full(s.nx * s.nz, np.inf)               # per column: the lowest and highest standable surface
    hi = np.full(s.nx * s.nz, -np.inf)
    standable = ok
    np.minimum.at(lo, column_of[standable], y[standable])
    np.maximum.at(hi, column_of[standable], y[standable])
    stacked = np.nonzero(hi - lo >= STACK_GAP)[0]
    if cm_sectors:
        X, Z = navsample.column_sectors(s, stacked)     # in whole centimetres: the same sectors navbuild.stage gives
        return Counter(zip((X - xoff).tolist(), (Z - zoff).tolist()))
    x = s.min_x + (stacked % s.nx) * s.pitch
    z = s.min_z + (stacked // s.nx) * s.pitch
    return Counter(zip((np.floor(x / 100).astype(int) - xoff).tolist(),
                       (np.floor(z / 100).astype(int) - zoff).tolist()))


def choose(level_dir: str, common: Iterable[str], samples_path: str | None, xoff: int, zoff: int,
           margin: float = MARGIN, stacked_min: int = STACKED_MIN) -> tuple[list[tuple[int, int]], dict[str, int]]:
    """(sorted [(X, Z)], {'flag': n, 'stacked': n}) - the detailed sectors of a level."""
    flag = flagged_sectors(level_dir, common, xoff, zoff, margin)
    if samples_path:
        stacked = {sector for sector, count in stacked_columns(samples_path, xoff, zoff).items()
                   if count >= stacked_min}
    else:
        stacked = set()
    return sorted(flag | stacked), {'flag': len(flag), 'stacked': len(stacked), 'stacked only': len(stacked - flag)}


def sampled_record_path(p02: str) -> str:
    """The record a pitch-0.2 sampling run leaves next to its samples: which sectors it covered, at which offsets."""
    return p02[:-4] + '.sectors.json'


def write_sampled_record(p02: str, sectors: Iterable[tuple[int, int]], xoff: int, zoff: int,
                         pending: bool = False) -> None:
    """pending: written as a 0.2 m run starts, replaced by the covered sectors when both its runs finish - so the
    samples of a run that failed part-way are never taken for good ones."""
    tmp = sampled_record_path(p02) + '.tmp'
    record = {'xoff': xoff, 'zoff': zoff, 'sectors': sorted([int(i), int(j)] for i, j in sectors)}
    if pending:
        record['pending'] = True
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(record, f)
    os.replace(tmp, sampled_record_path(p02))


def sampled_problem(p02: str, samples08: str | None, sectors: Iterable[tuple[int, int]], xoff: int,
                    zoff: int) -> str | None:
    """Why the pitch-0.2 samples p02 do not fit the detailed sectors chosen now (None: they fit). With the run's record:
    a finished run (not "pending": one that failed part-way leaves that), the same sector offsets, and every chosen
    sector among those it covered. Without one (a run from before the record): the 0.8 m samples the choice came
    from must not be newer than the 0.2 m ones."""
    record_path = sampled_record_path(p02)
    if os.path.isfile(record_path):
        try:
            with open(record_path, encoding='utf-8') as f:
                record = json.load(f)
        except ValueError:
            return f'its record {os.path.basename(record_path)} is damaged'
        if record.get('pending'):
            return 'the last 0.2 m sampling run did not finish'
        if (record.get('xoff'), record.get('zoff')) != (xoff, zoff):
            return (f'sampled with sector offsets {record.get("xoff")},{record.get("zoff")}, '
                    f'the level now has {xoff},{zoff}')
        covered = {(int(i), int(j)) for i, j in record.get('sectors', ())}
        missing = sorted({(int(i), int(j)) for i, j in sectors} - covered)
        if missing:
            return f'{len(missing)} detailed sector(s) are not in the 0.2 m samples, e.g. {missing[0]}'
        return None
    if samples08 and os.path.isfile(samples08) and os.path.getmtime(samples08) > os.path.getmtime(p02):
        return 'the 0.8 m samples (which choose the detailed sectors) are newer than the 0.2 m samples'
    return None


def score(chosen: Iterable[tuple[int, int]], dice_pathdata: str) -> tuple[int, int, int]:
    """Against DICE's choice (evaluation only): (found, DICE detailed, chosen sectors DICE builds at 0.8)."""
    import hpdtool
    import kymesh
    detailed, normal = set(), set()
    for sector in hpdtool.parse(open(dice_pathdata, 'rb').read()).sectors:
        is_detailed = abs(kymesh.parse_mesh(sector.mesh).pitch - 0.2) < 1e-3
        (detailed if is_detailed else normal).add((sector.id >> 8, sector.id & 0xff))
    chosen_set = set(chosen)
    return len(detailed & chosen_set), len(detailed), len(normal & chosen_set)


def offsets_of(root: str, lv: str, given: str | None = None) -> tuple[int, int]:
    """(xoff, zoff): --offsets X,Z when given, else the level's navkit.json (navkit_le.py prepare); ValueError if neither."""
    if given:
        try:
            x, z = (int(part) for part in given.split(','))
            return x, z
        except ValueError as e:
            raise ValueError(f'--offsets {given!r}: give X,Z (two whole numbers)') from e
    path = os.path.join(root, 'work', 'navkit', lv, 'navkit.json')
    if not os.path.isfile(path):
        raise ValueError(f'{lv}: no --offsets X,Z and no {path} (run navkit_le.py prepare {lv})')
    with open(path, encoding='utf-8') as f:
        st = json.load(f)
    return int(st['xoff']), int(st['zoff'])


def main(argv: list[str]) -> int:
    if any(arg in ('-h', '--help') for arg in argv[1:]):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 1
    lv = argv[1].lower()
    opts = dict(zip(argv[2:], argv[3:]))
    root = os.environ.get('OPFOR_ROOT') or r'C:\Games\Project OPFOR'
    level_dir = os.path.join(root, 'work', 'levels', lv)
    common = [os.path.join(root, 'work', 'navkit', 'common', name) for name in ('mp_common', 'sp_common')]
    try:
        xoff, zoff = offsets_of(root, lv, opts.get('--offsets'))
    except ValueError as e:
        print('error:', e)
        return 2
    chosen, why = choose(level_dir, common, opts.get('--samples'), xoff, zoff)
    print(f'{lv}: {len(chosen)} detailed sectors ({why})')
    if '--score' in argv:
        import navparity
        reference = navparity.reference_of(root, lv)     # DICE's file: never NavKit's own installed one (.opfor-none)
        if not os.path.isfile(reference):
            print(f'error: {lv} has no original path data to score against')
            return 1
        found, dice_detailed, extra = score(chosen, reference)
        print(f'  against DICE: {found} of its {dice_detailed} detailed sectors, {extra} more')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
