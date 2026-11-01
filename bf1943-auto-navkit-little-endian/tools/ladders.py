"""NavKit: the level's ladders, for ladder links in the path data: reads a prepared level folder (objects.json and
the blueprints' collision) and writes ladders.json, each ladder's world box (navwalk.walkable seeds the far end of a
ladder whose near end is walkable; navbuild links a node at each end).

Ladders are objects (LadderEntityData): a soldier who walks into one, facing it, climbs it - the AI too (a friendly
bot followed the user up a ladder on Atacama, 2026-10-21). DICE's path data has no ladder links (no route edge on
any level climbs steeper than walkable ground), so ground reached only by ladder is not in it; NavKit adds them.

A ladder is found by its blueprint path (containing 'ladder', with a collision model) among the level's placed
objects (objects.json from levelobjects.py); its collision model in world space gives the box: foot = the box
bottom, top = the box top.

Usage: ladders.py <level dir> [<more blueprint folders>...]   writes <level dir>\\ladders.json
"""
import json
import os
import sys
from collections.abc import Iterable
from typing import Any

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)
import levelobjects  # noqa: E402


type Ladder = dict[str, Any]       # {'bp': blueprint path, 'lo': [x, y, z], 'hi': [x, y, z]}


def extract(level_dir: str, extra_bases: Iterable[str] = ()) -> list[Ladder]:
    """[{'bp', 'lo': [x, y, z], 'hi': [x, y, z]}] - world boxes of the level's placed ladders."""
    bases = [base for base in [os.path.join(level_dir, 'pkg'), os.path.join(level_dir, 'dist')] + list(extra_bases)
             if os.path.isdir(base)]
    havok_by_blueprint = levelobjects.havok_index(bases)
    out = []
    for placed in json.load(open(os.path.join(level_dir, 'objects.json'))):
        blueprint = placed['bp'].lower()
        if 'ladder' not in blueprint.rsplit('/', 1)[-1] or blueprint not in havok_by_blueprint:
            continue
        tris = levelobjects.triangles(havok_by_blueprint[blueprint])
        if tris is None:
            continue
        matrix = placed['m']           # row-major 4 x 4: rotation rows, then the translation at 12-14
        rotation = np.array([matrix[0:3], matrix[4:7], matrix[8:11]])
        vertices = tris.reshape(-1, 3) @ rotation + np.array(matrix[12:15])
        low, high = vertices.min(0), vertices.max(0)
        if high[1] - low[1] < 1.5:                    # a ladder prop lying down, or a fragment: nothing to climb
            continue
        out.append({'bp': placed['bp'], 'lo': [round(float(coord), 3) for coord in low],
                    'hi': [round(float(coord), 3) for coord in high]})
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    found = extract(argv[1], argv[2:])
    json.dump(found, open(os.path.join(argv[1], 'ladders.json'), 'w', encoding='utf-8', newline='\n'), indent=1)
    print(f'{len(found)} ladders -> {os.path.join(argv[1], "ladders.json")}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
