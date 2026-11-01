r"""NavKit (big-endian): why a spot of a BF1943 level is cut off from the network.

  enclosure_probe1943.py <level> <x> <y> <z> [radius m, default 20] [--png <out.png>]

A flood fill over the level's open 0.2 m samples from the point (steps within soldier1943's step), then, for every
blocked surface on the fill's edge that it could step to, the triangles the capsule test counts against it, grouped
by the object whose box holds them. Prints the fill's extent and the blockers, with how far above the blocked
surface they reach; --png draws it from above (the fill, its blocked edge, other open and blocked ground near the
point's height, the object boxes). Reads the level's work folder as navkit.py writes it (objects\ and samples\:
samples_02.bin, all_tris.npy, object_boxes.json): run navkit.py prepare and sample first. A point outside the detailed
(0.2 m) sectors has no samples to fill.

Found with it on Wake Island (2026-10-23): a door lip 0.33 m high (the step tolerance), a bunker exit under a
1.94 m lintel (the headroom band), and three sampler flaws (walls flat from above, ramps, buried surfaces).
"""
import json
import os
import sys
from collections import defaultdict, deque

import numpy as np

sys.dont_write_bytecode = True
if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import navsample  # noqa: E402
import navsample1943 as ns  # noqa: E402


def blockers(T, N, steep, slope, tri_lo, tri_hi, px, pz, y):
    """(triangle index, low, high, over) for the triangles that block a surface at y in column (px, pz). T: triangles
    (N, 3, 3) with their normals N, steep mask, height change per metre (slope) and height range (tri_lo, tri_hi)."""
    X, Z = np.array([px]), np.array([pz])
    T2 = T[:, :, [0, 2]]
    u, v, w, ins = ns.inside(T2, X, Z)
    plane_y = (u * T[:, 0, 1, None] + v * T[:, 1, 1, None] + w * T[:, 2, 1, None])[:, 0]   # each plane at the column
    ins = ins[:, 0]
    dist = ns.dist2d(T2, X, Z)[:, 0]
    out = []
    for tri in np.nonzero(dist <= ns.RADIUS)[0]:
        if ins[tri] and not steep[tri]:
            low = high = plane_y[tri]
            over = True
        else:
            # as navsample1943: the plane height at the column, inside or beside it
            no_plane = np.isnan(plane_y[tri])
            plane_lo = tri_lo[tri] if no_plane else plane_y[tri] - slope[tri] * ns.RADIUS
            plane_hi = tri_hi[tri] if no_plane else plane_y[tri] + slope[tri] * ns.RADIUS
            low = tri_lo[tri] if steep[tri] else max(plane_lo, tri_lo[tri])
            high = tri_hi[tri] if steep[tri] else min(plane_hi, tri_hi[tri])
            over = False
        if low < y + ns.HEIGHT and high > y + (ns.OVERHEAD_MIN if over else ns.STEP):
            out.append((tri, low, high, over))
    return out


def main(argv):
    png = argv[argv.index('--png') + 1] if '--png' in argv else None
    argv = [arg for arg in argv if arg not in ('--png', png)]
    if len(argv) < 5:
        print(__doc__)
        return 1
    from bfbc2core import game1943
    work = game1943.level_work(argv[1].lower())
    samples, objects = os.path.join(work, 'samples', 'samples_02.bin'), os.path.join(work, 'objects')
    if not os.path.isfile(samples):
        raise FileNotFoundError(f'{samples}: run navkit.py prepare and sample {argv[1]} first')
    x, y, z = map(float, argv[2:5])
    radius = float(argv[5]) if len(argv) > 5 else 20.0
    s, _ = navsample.load(samples)
    i0, j0 = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
    radius_cells = int(radius / s.pitch)

    def surfaces(i, j):
        """[(surface, height, blocked)] of column (i, j); none off the sample grid (2026-10-31: column -1 read the
        previous row's last column, and past the last row raised IndexError)."""
        return [(k, float(s.y[k]), bool(s.flags[k] & 1)) for k in column(s, i, j)]

    # (height difference, surface, height) of open surfaces near the point's height
    near = [(abs(sy - y), k, sy) for k, sy, blocked in surfaces(i0, j0) if not blocked and abs(sy - y) < 0.6]
    if not near:                                     # the spawn's own sample may be within a wall's radius
        for r in range(1, 6):
            near = [(abs(sy - y), k, sy) for di in range(-r, r + 1) for dj in range(-r, r + 1)
                    for k, sy, blocked in surfaces(i0 + di, j0 + dj) if not blocked and abs(sy - y) < 0.6]
            if near:
                break
    if not near:
        print('no open surface near the point')
        return 1
    _, k0, y0 = min(near)
    start_i, start_j = next((i, j) for i in range(i0 - 5, i0 + 6) for j in range(j0 - 5, j0 + 6)
                            if k0 in column(s, i, j))
    seen = {k0}
    queue = deque([(start_i, start_j, y0)])
    cells, edge = [], {}                             # the fill's (i, j, y); its blocked edge: surface -> (i, j, y)
    while queue:
        i, j, cell_y = queue.popleft()
        cells.append((i, j, cell_y))
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if abs(ni - i0) > radius_cells or abs(nj - j0) > radius_cells:
                continue
            for k, next_y, blocked in surfaces(ni, nj):
                if abs(next_y - cell_y) <= ns.STEP and k not in seen:
                    if blocked:
                        edge[k] = (ni, nj, next_y)
                    else:
                        seen.add(k)
                        queue.append((ni, nj, next_y))
    xs = [s.min_x + i * s.pitch for i, _, _ in cells]
    zs = [s.min_z + j * s.pitch for _, j, _ in cells]
    ys = [cell[2] for cell in cells]
    print(f'fill: {len(cells)} open surfaces, x {min(xs):.1f}..{max(xs):.1f}, z {min(zs):.1f}..{max(zs):.1f}, '
          f'y {min(ys):.2f}..{max(ys):.2f}; {len(edge)} blocked surfaces on its edge')
    if len(cells) > 20000:
        print('(the fill is large: the point is probably not enclosed)')

    # the collision triangles within the radius (plus 1 m) of the point
    T = np.load(os.path.join(objects, 'all_tris.npy'))
    corner_min, corner_max = T.min(1), T.max(1)
    pad = radius + 1
    nearby = np.nonzero((corner_max[:, 0] > x - pad) & (corner_min[:, 0] < x + pad)
                        & (corner_max[:, 2] > z - pad) & (corner_min[:, 2] < z + pad))[0]
    T = T[nearby]
    N, _ = ns.normals(T)
    steep = N[:, 1] <= ns.FLOOR_MIN
    slope = np.sqrt(np.maximum(1 - N[:, 1] ** 2, 0)) / np.maximum(N[:, 1], 0.05)
    tri_lo, tri_hi = T[:, :, 1].min(1), T[:, :, 1].max(1)
    boxes = json.load(open(os.path.join(objects, 'object_boxes.json')))
    box_min = np.array([box['min'] for box in boxes])
    box_max = np.array([box['max'] for box in boxes])
    centres = T.mean(1)

    def owner(tri):
        centre = centres[tri]
        holding = np.nonzero(np.all(box_min - 0.05 <= centre, 1) & np.all(box_max + 0.05 >= centre, 1))[0]
        if not len(holding):
            return '(no object box: terrain or a group member)'
        box = boxes[min(holding, key=lambda b: np.prod(box_max[b] - box_min[b]))]   # the smallest box holding it
        return box['bp'].split('/')[-1] + (' [destructible]' if box['destructible'] else '')

    by_owner = defaultdict(lambda: {'surfaces': set(), 'rise': [], 'over': 0, 'xz': []})
    for k, (i, j, edge_y) in edge.items():
        px, pz = s.min_x + i * s.pitch, s.min_z + j * s.pitch
        for tri, _low, high, over in blockers(T, N, steep, slope, tri_lo, tri_hi, px, pz, edge_y):
            entry = by_owner[owner(tri)]
            entry['surfaces'].add(k)
            entry['rise'].append(min(high, edge_y + ns.HEIGHT) - edge_y)
            entry['over'] += over
            entry['xz'].append((px, pz))
    if png:
        draw(png, s, i0, j0, radius_cells, (x, y, z), cells, edge, boxes)
    for name, entry in sorted(by_owner.items(), key=lambda kv: -len(kv[1]['surfaces'])):
        xz = np.array(entry['xz'])
        print(f"  {len(entry['surfaces']):4d} edge surfaces blocked by {name}: "
              f"reaches {np.median(entry['rise']):.2f} m above them (median), {entry['over']} overhead hits; "
              f"around x {xz[:, 0].min():.1f}..{xz[:, 0].max():.1f}, z {xz[:, 1].min():.1f}..{xz[:, 1].max():.1f}")
    return 0


def column(s, i, j):
    """The surfaces of column (i, j), none off the sample grid."""
    return s.column(i, j) if 0 <= i < s.nx and 0 <= j < s.nz else range(0)


def draw(png, s, i0, j0, radius_cells, point, cells, edge, boxes):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    size = 2 * radius_cells + 1
    img = np.zeros((size, size), np.uint8)        # 0 nothing near the height, 1 open, 2 blocked, 3 fill, 4 fill edge
    for col in range(size):
        for row in range(size):
            # (height difference, blocked) of the column's surfaces within 1 m of the point's height
            near = [(abs(float(s.y[k]) - point[1]), s.flags[k] & 1)
                    for k in column(s, i0 - radius_cells + col, j0 - radius_cells + row)
                    if abs(float(s.y[k]) - point[1]) < 1.0]
            if near:
                img[row, col] = 2 if min(near)[1] else 1
    for i, j, _ in cells:
        img[j - j0 + radius_cells, i - i0 + radius_cells] = 3
    for i, j, _ in edge.values():
        img[j - j0 + radius_cells, i - i0 + radius_cells] = 4
    colours = np.array([[255, 255, 255], [200, 225, 200], [150, 150, 150], [70, 160, 70], [220, 60, 40]], np.uint8)
    x0, z0 = s.min_x + (i0 - radius_cells) * s.pitch, s.min_z + (j0 - radius_cells) * s.pitch
    x1, z1 = x0 + size * s.pitch, z0 + size * s.pitch
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(colours[img], origin='lower', extent=(x0, x1, z0, z1), interpolation='nearest')
    for box in boxes:
        lo, hi = box['min'], box['max']
        if hi[0] < x0 or lo[0] > x1 or hi[2] < z0 or lo[2] > z1 or hi[1] < point[1] - 1 or lo[1] > point[1] + 3:
            continue
        colour = 'tab:orange' if box['destructible'] else 'tab:blue'
        ax.add_patch(Rectangle((lo[0], lo[2]), hi[0] - lo[0], hi[2] - lo[2], fill=False, lw=0.8, ec=colour))
        ax.text(max(lo[0], x0), min(hi[2], z1), box['bp'].split('/')[-1][:22], fontsize=6, va='top', color=colour)
    ax.plot([point[0]], [point[2]], 'k*', ms=12)
    ax.set_xlim(x0, x1)
    ax.set_ylim(z0, z1)
    ax.set_xlabel('x (m)')
    ax.set_ylabel('z (m)')
    ax.set_title(f'{point}: fill (dark green), its blocked edge (red), other open / blocked ground within 1 m of the '
                 f'height (light green / grey); boxes: blue solid, orange destructible', fontsize=8)
    fig.savefig(png, dpi=110, bbox_inches='tight')
    print(f'wrote {png}')

if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('enclosure_probe1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
