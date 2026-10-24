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
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'core')]
import navsample  # noqa: E402
import navsample1943 as ns  # noqa: E402



def blockers(T, N, steep, slope, tlo, thi, px, pz, y):
    """(triangle index, low, high, over) for the triangles that block a surface at y in column (px, pz)."""
    X, Z = np.array([px]), np.array([pz])
    T2 = T[:, :, [0, 2]]
    u, v, w, ins = ns.inside(T2, X, Z)
    Y = (u * T[:, 0, 1, None] + v * T[:, 1, 1, None] + w * T[:, 2, 1, None])[:, 0]
    ins = ins[:, 0]
    d = ns.dist2d(T2, X, Z)[:, 0]
    out = []
    for a in np.nonzero(d <= ns.RADIUS)[0]:
        if ins[a] and not steep[a]:
            low = high = Y[a]
            over = True
        else:
            flo = tlo[a] if np.isnan(Y[a]) else Y[a] - slope[a] * ns.RADIUS     # as navsample1943: the plane height
            fhi = thi[a] if np.isnan(Y[a]) else Y[a] + slope[a] * ns.RADIUS     # at the column, inside or beside it
            low = tlo[a] if steep[a] else max(flo, tlo[a])
            high = thi[a] if steep[a] else min(fhi, thi[a])
            over = False
        if low < y + ns.HEIGHT and high > y + (ns.OVERHEAD_MIN if over else ns.STEP):
            out.append((a, low, high, over))
    return out


def main(argv):
    png = argv[argv.index('--png') + 1] if '--png' in argv else None
    argv = [a for a in argv if a not in ('--png', png)]
    if len(argv) < 5:
        print(__doc__)
        return 1
    from bfbc2core import game1943
    work = game1943.level_work(argv[1].lower())
    samples, objects = os.path.join(work, 'samples', 'samples_02.bin'), os.path.join(work, 'objects')
    if not os.path.isfile(samples):
        raise FileNotFoundError(f'{samples}: run navkit.py prepare and sample {argv[1]} first')
    x, y, z = map(float, argv[2:5])
    R = float(argv[5]) if len(argv) > 5 else 20.0
    s, _ = navsample.load(samples)
    i0, j0 = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
    Rc = int(R / s.pitch)

    def surfs(i, j):
        return [(k, float(s.y[k]), bool(s.flags[k] & 1)) for k in s.column(i, j)]

    near = [(abs(yy - y), k, yy) for k, yy, b in surfs(i0, j0) if not b and abs(yy - y) < 0.6]
    if not near:                                     # the spawn's own sample may be within a wall's radius
        for r in range(1, 6):
            near = [(abs(yy - y), k, yy) for di in range(-r, r + 1) for dj in range(-r, r + 1)
                    for k, yy, b in surfs(i0 + di, j0 + dj) if not b and abs(yy - y) < 0.6]
            if near:
                break
    if not near:
        print('no open surface near the point')
        return 1
    _, k0, y0 = min(near)
    ci, cj = next((i, j) for i in range(i0 - 5, i0 + 6) for j in range(j0 - 5, j0 + 6) if k0 in s.column(i, j))
    seen = {k0}
    q = deque([(ci, cj, y0)])
    cells, edge = [], {}
    while q:
        i, j, yy = q.popleft()
        cells.append((i, j, yy))
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if abs(a - i0) > Rc or abs(b - j0) > Rc:
                continue
            for k, y2, blk in surfs(a, b):
                if abs(y2 - yy) <= ns.STEP and k not in seen:
                    if blk:
                        edge[k] = (a, b, y2)
                    else:
                        seen.add(k)
                        q.append((a, b, y2))
    xs = [s.min_x + i * s.pitch for i, _, _ in cells]
    zs = [s.min_z + j * s.pitch for _, j, _ in cells]
    ys = [c[2] for c in cells]
    print(f'fill: {len(cells)} open surfaces, x {min(xs):.1f}..{max(xs):.1f}, z {min(zs):.1f}..{max(zs):.1f}, '
          f'y {min(ys):.2f}..{max(ys):.2f}; {len(edge)} blocked surfaces on its edge')
    if len(cells) > 20000:
        print('(the fill is large: the point is probably not enclosed)')

    T = np.load(os.path.join(objects, 'all_tris.npy'))
    lo, hi = T.min(1), T.max(1)
    pad = R + 1
    sel = np.nonzero((hi[:, 0] > x - pad) & (lo[:, 0] < x + pad) & (hi[:, 2] > z - pad) & (lo[:, 2] < z + pad))[0]
    T = T[sel]
    N, _ = ns.normals(T)
    steep = N[:, 1] <= ns.FLOOR_MIN
    slope = np.sqrt(np.maximum(1 - N[:, 1] ** 2, 0)) / np.maximum(N[:, 1], 0.05)
    tlo, thi = T[:, :, 1].min(1), T[:, :, 1].max(1)
    boxes = json.load(open(os.path.join(objects, 'object_boxes.json')))
    bmn = np.array([b['min'] for b in boxes])
    bmx = np.array([b['max'] for b in boxes])
    c = T.mean(1)

    def owner(a):
        p = c[a]
        k = np.nonzero(np.all(bmn - 0.05 <= p, 1) & np.all(bmx + 0.05 >= p, 1))[0]
        if not len(k):
            return '(no object box: terrain or a group member)'
        b = boxes[min(k, key=lambda i: np.prod(bmx[i] - bmn[i]))]   # the smallest box holding it
        return b['bp'].split('/')[-1] + (' [destructible]' if b['destructible'] else '')

    by = defaultdict(lambda: {'surfaces': set(), 'rise': [], 'over': 0, 'xz': []})
    for k, (a, b, y2) in edge.items():
        px, pz = s.min_x + a * s.pitch, s.min_z + b * s.pitch
        for t, low, high, over in blockers(T, N, steep, slope, tlo, thi, px, pz, y2):
            e = by[owner(t)]
            e['surfaces'].add(k)
            e['rise'].append(min(high, y2 + ns.HEIGHT) - y2)
            e['over'] += over
            e['xz'].append((px, pz))
    if png:
        draw(png, s, i0, j0, Rc, (x, y, z), cells, edge, boxes)
    for name, e in sorted(by.items(), key=lambda kv: -len(kv[1]['surfaces'])):
        xz = np.array(e['xz'])
        print(f"  {len(e['surfaces']):4d} edge surfaces blocked by {name}: reaches {np.median(e['rise']):.2f} m above them "
              f"(median), {e['over']} overhead hits; around x {xz[:, 0].min():.1f}..{xz[:, 0].max():.1f}, "
              f"z {xz[:, 1].min():.1f}..{xz[:, 1].max():.1f}")
    return 0


def draw(png, s, i0, j0, Rc, P, cells, edge, boxes):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    n = 2 * Rc + 1
    img = np.zeros((n, n), np.uint8)              # 0 nothing near the height, 1 open, 2 blocked, 3 fill, 4 fill edge
    for a in range(n):
        for b in range(n):
            near = [(abs(float(s.y[k]) - P[1]), s.flags[k] & 1) for k in s.column(i0 - Rc + a, j0 - Rc + b)
                    if abs(float(s.y[k]) - P[1]) < 1.0]
            if near:
                img[b, a] = 2 if min(near)[1] else 1
    for i, j, _ in cells:
        img[j - j0 + Rc, i - i0 + Rc] = 3
    for i, j, _ in edge.values():
        img[j - j0 + Rc, i - i0 + Rc] = 4
    colours = np.array([[255, 255, 255], [200, 225, 200], [150, 150, 150], [70, 160, 70], [220, 60, 40]], np.uint8)
    x0, z0 = s.min_x + (i0 - Rc) * s.pitch, s.min_z + (j0 - Rc) * s.pitch
    x1, z1 = x0 + n * s.pitch, z0 + n * s.pitch
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.imshow(colours[img], origin='lower', extent=(x0, x1, z0, z1), interpolation='nearest')
    for b in boxes:
        lo, hi = b['min'], b['max']
        if hi[0] < x0 or lo[0] > x1 or hi[2] < z0 or lo[2] > z1 or hi[1] < P[1] - 1 or lo[1] > P[1] + 3:
            continue
        ax.add_patch(Rectangle((lo[0], lo[2]), hi[0] - lo[0], hi[2] - lo[2], fill=False, lw=0.8,
                               ec='tab:orange' if b['destructible'] else 'tab:blue'))
        ax.text(max(lo[0], x0), min(hi[2], z1), b['bp'].split('/')[-1][:22], fontsize=6, va='top',
                color='tab:orange' if b['destructible'] else 'tab:blue')
    ax.plot([P[0]], [P[2]], 'k*', ms=12)
    ax.set_xlim(x0, x1)
    ax.set_ylim(z0, z1)
    ax.set_xlabel('x (m)')
    ax.set_ylabel('z (m)')
    ax.set_title(f'{P}: fill (dark green), its blocked edge (red), other open / blocked ground within 1 m of the '
                 f'height (light green / grey); boxes: blue solid, orange destructible', fontsize=8)
    fig.savefig(png, dpi=110, bbox_inches='tight')
    print(f'wrote {png}')


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('enclosure_probe1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
