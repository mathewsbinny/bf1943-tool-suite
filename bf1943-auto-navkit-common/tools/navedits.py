"""NavKit: the walkability edits of a navigation side file (`<level>.navtune.json`, DESIGN.md section 5.1) as
inputs to the generator - which sampled surfaces are forced walkable or unwalkable, extra seeds, and the
points whose islands are dropped.

  walkable / unwalkable  every sampled surface inside the region (its outline or box in x/z, and the height
                         range) is forced walkable (slope and clearance rules skipped) or unwalkable; later
                         operations win where regions overlap
  seed                   a seed at the point: its island is kept (navwalk's seeding, as soldier spawns are)
  unseed                 the island the point is on is dropped after seeding (even if it has other seeds)

Links, route edges and traversal annotations are not walkability: navkit apply handles them after the build.
"""
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from matplotlib.path import Path

if TYPE_CHECKING:
    import navsample

WALKABILITY = ('walkable', 'unwalkable', 'seed', 'unseed')


def surface_positions(s: 'navsample.Samples'
                      ) -> tuple[npt.NDArray[np.floating], npt.NDArray[np.floating], npt.NDArray[np.floating]]:
    """(x, z, y) of every sampled surface."""
    column_of = np.repeat(np.arange(s.nx * s.nz), np.diff(s.start[:s.nx * s.nz + 1]))
    x = s.min_x + (column_of % s.nx) * s.pitch
    z = s.min_z + (column_of // s.nx) * s.pitch
    return x, z, s.y.astype(np.float64)


def region_mask(x: npt.NDArray[np.floating], z: npt.NDArray[np.floating], y: npt.NDArray[np.floating],
                region: dict[str, Any]) -> npt.NDArray[np.bool]:
    """The surfaces (x, z, y per surface) inside a region: its 'box' (x0, y0, z0, x1, y1, z1), or its 'polygon' in
    x/z and its 'y' range."""
    if 'box' in region:
        x0, y0, z0, x1, y1, z1 = region['box']
        return (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1) & (y >= y0) & (y <= y1)
    lo, hi = region['y']
    poly = np.asarray(region['polygon'], dtype=np.float64)
    mask = ((y >= lo) & (y <= hi) & (x >= poly[:, 0].min()) & (x <= poly[:, 0].max())
            & (z >= poly[:, 1].min()) & (z <= poly[:, 1].max()))
    if mask.any():
        in_box = np.nonzero(mask)[0]
        inside = Path(poly).contains_points(np.stack([x[in_box], z[in_box]], axis=1))
        mask[in_box] = inside
    return mask


def edits_for(s: 'navsample.Samples', operations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """dict(force_walkable, force_unwalkable: bool per surface; seeds, unseeds: lists of (x, y, z); counts)."""
    x, z, y = surface_positions(s)
    force_walkable = np.zeros(len(y), dtype=bool)
    force_unwalkable = np.zeros(len(y), dtype=bool)
    seeds, unseeds = [], []
    counts: dict[str, int] = {}
    for operation in operations:
        name = operation.get('op')
        if name not in WALKABILITY:
            continue
        counts[name] = counts.get(name, 0) + 1
        if name in ('walkable', 'unwalkable'):
            mask = region_mask(x, z, y, operation['region'])
            if name == 'walkable':
                force_walkable |= mask
                force_unwalkable &= ~mask
            else:
                force_unwalkable |= mask
                force_walkable &= ~mask
        elif name == 'seed':
            seeds.append(tuple(operation['at']))
        else:
            unseeds.append(tuple(operation['at']))
    return {'force_walkable': force_walkable, 'force_unwalkable': force_unwalkable, 'seeds': seeds,
            'unseeds': unseeds, 'counts': counts, 'surfaces_walkable': int(force_walkable.sum()),
            'surfaces_unwalkable': int(force_unwalkable.sum())}
