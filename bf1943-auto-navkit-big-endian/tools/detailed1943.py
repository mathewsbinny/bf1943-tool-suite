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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import detailed  # noqa: E402
import soldier1943  # noqa: E402

soldier1943.configure()          # standable at BF1943's slope limit (45 deg), not NavKit's BFBC2 40


def flagged_sectors(level_dir, xoff, zoff, margin=detailed.MARGIN):
    out = set()
    for b in json.load(open(os.path.join(level_dir, 'object_boxes.json'), encoding='utf-8')):
        if not b.get('detailed'):
            continue
        (x0, _, z0), (x1, _, z1) = b['min'], b['max']
        for X in range(math.floor((x0 - margin) / 100) - xoff, math.floor((x1 + margin) / 100) - xoff + 1):
            for Z in range(math.floor((z0 - margin) / 100) - zoff, math.floor((z1 + margin) / 100) - zoff + 1):
                out.add((X, Z))
    return out


def choose(level_dir, samples_path, xoff, zoff, margin=detailed.MARGIN, stacked_min=detailed.STACKED_MIN):
    """(sorted [(X, Z)], counts) - the detailed sectors of a BF1943 level."""
    flag = flagged_sectors(level_dir, xoff, zoff, margin)
    stacked = {k for k, n in detailed.stacked_columns(samples_path, xoff, zoff).items() if n >= stacked_min}
    return sorted(flag | stacked), {'flag': len(flag), 'stacked': len(stacked), 'stacked only': len(stacked - flag)}


def main(argv):
    if len(argv) != 5:
        print(__doc__)
        return 1
    chosen, counts = choose(argv[1], argv[2], int(argv[3]), int(argv[4]))
    print(f'{len(chosen)} detailed sectors: {counts}')
    print(' '.join(f'{x:02x}{z:02x}' for x, z in chosen))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'core'))
    from bfbc2core import errors
    errors.setup('detailed1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
