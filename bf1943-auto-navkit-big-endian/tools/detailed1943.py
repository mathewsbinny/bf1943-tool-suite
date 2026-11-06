"""NavKit (big-endian): which sectors of a BF1943 level to build as detailed zones (pitch 0.2).

detailed.py's rule on BF1943 data: a sector is detailed when
  - an object whose blueprint sets NeedsDetailedPathfinding (object_boxes.json from levelobjects1943.py) has its
    collision box within MARGIN m of the sector's square, or
  - at least STACKED_MIN columns of the 0.8 m samples in the sector hold two standable surfaces at least 2 m apart
    (detailed.stacked_columns, unchanged).
detailed.flagged_sectors documents NeedsDetailedPathfinding but tests the ForbidPathfinding flag (flags[0]); this
uses NeedsDetailedPathfinding itself.

Usage: detailed1943.py <level dir> <0.8 m samples.bin> <XOffset> <ZOffset>
"""
import json
import math
import os
import sys

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import detailed  # noqa: E402
import soldier1943  # noqa: E402


type SectorKey = tuple[int, int]        # (X, Z): a 100 m sector, less the level's sector offsets


def flagged_sectors(level_dir: str, xoff: int, zoff: int, margin: float = detailed.MARGIN) -> set[SectorKey]:
    """The sectors within margin m of a NeedsDetailedPathfinding object's collision box (object_boxes.json)."""
    out = set()
    for box in json.load(open(os.path.join(level_dir, 'object_boxes.json'), encoding='utf-8')):
        if not box.get('detailed'):
            continue
        (x0, _, z0), (x1, _, z1) = box['min'], box['max']
        for X in range(math.floor((x0 - margin) / 100) - xoff, math.floor((x1 + margin) / 100) - xoff + 1):
            for Z in range(math.floor((z0 - margin) / 100) - zoff, math.floor((z1 + margin) / 100) - zoff + 1):
                out.add((X, Z))
    return out


def choose(level_dir: str, samples_path: str, xoff: int, zoff: int, margin: float = detailed.MARGIN,
           stacked_min: int = detailed.STACKED_MIN) -> tuple[list[SectorKey], dict[str, int]]:
    """(sorted [(X, Z)], counts) - the detailed sectors of a BF1943 level."""
    flagged = flagged_sectors(level_dir, xoff, zoff, margin)
    # standable at BF1943's slope limit (45 deg), not NavKit's BFBC2 40; a column's sector by the rule BF1943's builds
    # take (soldier1943.SETTINGS: whole centimetres since 2026-11, the same chosen sectors on the four maps)
    counts = detailed.stacked_columns(samples_path, xoff, zoff, slope_max=soldier1943.SLOPE_MAX,
                                      cm_sectors=soldier1943.SETTINGS.column_sectors_cm)
    stacked = {sector for sector, columns in counts.items() if columns >= stacked_min}
    return sorted(flagged | stacked), {'flag': len(flagged), 'stacked': len(stacked),
                                       'stacked only': len(stacked - flagged)}


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 1
    chosen, counts = choose(argv[1], argv[2], int(argv[3]), int(argv[4]))
    print(f'{len(chosen)} detailed sectors: {counts}')
    print(' '.join(f'{x:02x}{z:02x}' for x, z in chosen))
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('detailed1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
