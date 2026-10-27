r"""NavKit (big-endian): navigation for BF1943's ground vehicles - a build per class (jeep, tank) with the vehicle's
clearance, exported to RSNV as its own world (<level>.jeep.rsnv, <level>.tank.rsnv) for the navigation runtime
(project-opfor-bf1943 rsnav).

  groundnav1943.py prepare <level>            the vehicles' collision (sample runs it when it is missing)
  groundnav1943.py sample <level> <class>     the collision samples with the class's clearance (navsample1943.py)
  groundnav1943.py build  <level> <class>     the drivable grid, its route graph, the RSNV world
  groundnav1943.py all    <level> [class]     both (classes: jeep, tank; default both)

Classes and their parameters: vehicleclasses1943.py ([O] from the blueprints, [E] estimates).

Steps:
  - prepare: the level's collision without the objects DICE excludes from pathfinding (ExcludeFromPathfinding:
    fences, palm trees, razor wire, sandbag walls) and its dynamic props (crates, chairs, drums), which a vehicle
    pushes through or flattens [H: palm trunks and sandbags need an in-game look], written to
    work\levels\<level>\vehicles\objects\all_tris.npy;
  - sample: navsample1943.py on it with the class's disc (radius), step and height, at the soldier build's 0.8 m grid
    (the same box: work\levels\<level>\vehicles\samples_<class>.bin);
  - build (this file): a surface is drivable when the samples did not block it, it is no steeper than the class's
    slope, the water over it is no deeper than its fording depth, it is not on a ForbidPathfinding object, and the
    ground under the whole disc continues (no drop, cliff or steep bank within the radius: the samples test objects,
    not the terrain); neighbouring surfaces link when the height change is within the step of the rise the slope
    predicts (navwalk.step_fits' rule); the drivable components touching a vehicle spawn of the class (or any
    ground-vehicle spawn) are kept. Areas: per 100 m sector, flood-filled pieces with at most one surface per
    column. Route nodes: one per block of node_spacing x node_spacing cells, at the cell farthest from the
    undrivable; edges between nodes of neighbouring blocks with a straight drivable line (this file's grid line
    rule, the class's step), and graph pieces of one component joined along the grid's shortest path, so the graph
    has every component the grid has.
  - export (rsnvgrid1943.py): sectors, areas with bitmaps and per-cell heights, the bucket grid, nodes, edges, islands,
    and the transfers (OFFM): boarding at each spawn of the class (the soldier-side point: the entry point).
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import game1943  # noqa: E402

GROUND_CLASSES = ('jeep', 'tank')
GROUND_LIKE_SLOPE = 10.0     # deg over the class's slope: ground under the disc may be this much steeper than drivable
SEED_REACH = 6.0             # m: a spawn keeps the drivable component within this of it


def paths(lv):
    W = game1943.level_work(lv)
    v = os.path.join(W, 'vehicles')
    return {'work': W, 'objects': os.path.join(W, 'objects'), 'vehicles': v, 'vobjects': os.path.join(v, 'objects'),
            'navkit': os.path.join(W, 'navkit'), 'samples': lambda cls: os.path.join(v, f'samples_{cls}.bin'),
            'rsnv': lambda cls: os.path.join(W, 'navkit', f'{lv}.{cls}.rsnv')}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


def _rows(a):
    a = np.ascontiguousarray(a.reshape(len(a), -1).astype(np.float32))
    return a.view(np.dtype((np.void, a.dtype.itemsize * a.shape[1]))).ravel()


def prepare(lv, log=print):
    """work\\levels\\<lv>\\vehicles\\objects\\: all_tris.npy without excluded objects and dynamic props, and the box
    files the sampler reads. Returns the triangle counts."""
    p = paths(lv)
    o, vo = p['objects'], p['vobjects']
    if not os.path.isfile(os.path.join(o, 'all_tris.npy')):
        raise FileNotFoundError(f'{lv}: the level is not prepared (missing {os.path.join(o, "all_tris.npy")}): '
                                f'run navkit.py prepare {lv}')
    os.makedirs(vo, exist_ok=True)
    T = np.load(os.path.join(o, 'all_tris.npy'))
    drop = []
    for name in ('exclude_tris.npy', 'dynamic_tris.npy'):
        f = os.path.join(o, name)
        if os.path.isfile(f):
            d = np.load(f)
            if len(d):
                drop.append(_rows(d))
    keep = np.ones(len(T), bool)
    if drop:
        keep = ~np.isin(_rows(T), np.concatenate(drop))
    np.save(os.path.join(vo, 'all_tris.npy'), T[keep])
    for name in ('soldier_spawns.json', 'object_boxes.json'):
        with open(os.path.join(o, name), 'rb') as f, open(os.path.join(vo, name), 'wb') as g:
            g.write(f.read())
    log(f'{lv}: vehicle collision {int(keep.sum())} of {len(T)} triangles (excluded objects and dynamic props left out)')
    return int(keep.sum()), len(T)


def sample_command(lv, cls):
    p = paths(lv)
    st = json.load(open(os.path.join(p['navkit'], 'navkit.json'), encoding='utf-8'))
    args = [sys.executable, '-B', '-u', os.path.join(HERE, 'navsample1943.py'), p['vobjects'],
            os.path.join(p['objects'], 'heightfield.res'), p['samples'](cls),
            '--radius', str(vc.value(cls, 'radius')), '--step', str(vc.value(cls, 'step')),
            '--height', str(vc.value(cls, 'height')), '--pitch', str(vc.value(cls, 'pitch'))]
    if st.get('sea_level') is not None:
        args += ['--sea-level', str(st['sea_level'])]
    return args


def sample(lv, cls, log=print):
    p = paths(lv)
    if not os.path.isfile(os.path.join(p['vobjects'], 'all_tris.npy')):
        prepare(lv, log)
    args = sample_command(lv, cls)
    logf = os.path.join(p['vehicles'], f'sample_{cls}.log')
    t0 = time.time()
    with open(logf, 'w', encoding='utf-8') as f:
        r = subprocess.run(args, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:
        raise RuntimeError(f'{lv} {cls}: the sampler failed (exit {r.returncode}); see {logf}')
    log(f'{lv} {cls}: sampled in {(time.time() - t0) / 60:.1f} min ({p["samples"](cls)})')


# ---- the samples, as arrays ----------------------------------------------------------------------------------------

class Grid:
    """The samples of one run: columns j-major; surfaces in CSR order (start per column)."""

    def __init__(self, path):
        import struct
        import navsample1943
        data = open(path, 'rb').read()
        (magic, version, pitch, x0, z0, nx, nz, y_top, y_bottom, radius, step, height, _r, _z) = \
            navsample1943.HEADER.unpack_from(data, 0)
        if magic != b'ONS1' or version != 1:
            raise ValueError(f'{path}: not a navsample1943 file')
        self.pitch, self.x0, self.z0, self.nx, self.nz = pitch, x0, z0, nx, nz
        self.radius, self.step, self.height = radius, step, height
        cols = nx * nz
        counts = np.zeros(cols, np.int64)
        pos = navsample1943.HEADER.size
        offs = np.zeros(cols, np.int64)
        mv = memoryview(data)
        for c in range(cols):                         # a count byte, then 8-byte records
            n = mv[pos]
            counts[c] = n
            offs[c] = pos + 1
            pos += 1 + 8 * n
        self.start = np.zeros(cols + 1, np.int64)
        np.cumsum(counts, out=self.start[1:])
        total = int(self.start[-1])
        idx = (np.repeat(offs, counts) + 8 * (np.arange(total) - np.repeat(self.start[:-1], counts)))
        raw = np.frombuffer(data, np.uint8)
        rec = raw[idx[:, None] + np.arange(8)[None, :]]
        self.y = rec[:, :4].copy().view('<f4').ravel().astype(np.float64)
        self.normal = rec[:, 4:7].view(np.int8).astype(np.float64) / 127.0
        self.flags = rec[:, 7].copy()
        self.col = np.repeat(np.arange(cols), counts)
        self.i = self.col % nx
        self.j = self.col // nx
        self.n = total
        ny = np.maximum(self.normal[:, 1], 0.05)
        self.gx, self.gz = -self.normal[:, 0] / ny, -self.normal[:, 2] / ny

    @classmethod
    def from_columns(cls, pitch, x0, z0, nx, nz, columns, radius=0.0, step=0.0, height=0.0):
        """A grid from made-up data (tests): columns[j * nx + i] = [(y, (nx, ny, nz), flags)], top down."""
        g = cls.__new__(cls)
        g.pitch, g.x0, g.z0, g.nx, g.nz = pitch, x0, z0, nx, nz
        g.radius, g.step, g.height = radius, step, height
        counts = np.array([len(c) for c in columns], np.int64)
        g.start = np.zeros(nx * nz + 1, np.int64)
        np.cumsum(counts, out=g.start[1:])
        flat = [s for c in columns for s in c]
        g.y = np.array([s[0] for s in flat], np.float64)
        g.normal = np.array([s[1] for s in flat], np.float64).reshape(-1, 3)
        g.flags = np.array([s[2] for s in flat], np.uint8)
        g.col = np.repeat(np.arange(nx * nz), counts)
        g.i, g.j, g.n = g.col % nx, g.col // nx, len(flat)
        ny = np.maximum(g.normal[:, 1], 0.05)
        g.gx, g.gz = -g.normal[:, 0] / ny, -g.normal[:, 2] / ny
        return g

    def xz(self, k):
        return self.x0 + self.i[k] * self.pitch, self.z0 + self.j[k] * self.pitch

    def dense(self, values, k_max=8, fill=np.nan):
        """(nz, nx, k_max) array of a per-surface value by column (the first k_max surfaces, top down)."""
        out = np.full((self.nz, self.nx, k_max), fill)
        rank = np.arange(self.n) - self.start[self.col]
        m = rank < k_max
        out[self.j[m], self.i[m], rank[m]] = values[m]
        return out


def neighbour_links(g, ok, step, di, dj, dense=None):
    """For direction (di, dj): (k, n) pairs of surfaces in ok linked by the step rule (each k: its best n). dense:
    (Y, IDX) from dense_surfaces, shared by the eight directions."""
    K = 8
    Y, IDX = dense if dense is not None else dense_surfaces(g, ok)
    ks = np.nonzero(ok)[0]
    ii, jj = g.i[ks] + di, g.j[ks] + dj
    inside = (ii >= 0) & (jj >= 0) & (ii < g.nx) & (jj < g.nz)
    ks, ii, jj = ks[inside], ii[inside], jj[inside]
    best_n = np.full(len(ks), -1, np.int64)
    best_d = np.full(len(ks), np.inf)
    for r in range(K):
        n = IDX[jj, ii, r]
        yn = Y[jj, ii, r]
        valid = (n >= 0) & ~np.isnan(yn)
        n_safe = np.where(valid, n, 0)
        dy = yn - g.y[ks]
        pred = g.pitch * ((g.gx[ks] + g.gx[n_safe]) / 2 * di + (g.gz[ks] + g.gz[n_safe]) / 2 * dj)
        fit = valid & (np.abs(dy - pred) <= step)
        d = np.where(fit, np.abs(dy), np.inf)
        better = d < best_d
        best_d = np.where(better, d, best_d)
        best_n = np.where(better, n, best_n)
    m = best_n >= 0
    return ks[m], best_n[m]


def dense_surfaces(g, ok, K=8):
    """(Y, IDX): per column the first K surfaces' heights (NaN where not in ok) and indices (-1 none)."""
    Y = g.dense(np.where(ok, g.y, np.nan), K).astype(np.float32)
    IDX = g.dense(np.arange(g.n).astype(float), K, fill=-1).astype(np.int32)
    return Y, IDX


def components(n, pairs):
    """Connected-component labels (smallest member index) of n items under undirected pairs (a, b)."""
    lab = np.arange(n)
    if not len(pairs):
        return lab
    a, b = pairs[:, 0], pairs[:, 1]
    while True:
        m = np.minimum(lab[a], lab[b])
        changed = False
        for side in (a, b):
            cur = lab[side]
            upd = m < cur
            if upd.any():
                np.minimum.at(lab, side[upd], m[upd])
                changed = True
        lab = lab[lab]                           # pointer jumping
        while True:
            nxt = lab[lab]
            if np.array_equal(nxt, lab):
                break
            lab = nxt
        if not changed:
            return lab


DIRS8 = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def drivable(g, cls, sea_level, forbid_mask=None, log=print):
    """Bool per surface: drivable centre positions for the class (see the module's description)."""
    slope = vc.value(cls, 'slope')
    step = vc.value(cls, 'step')
    radius = vc.value(cls, 'radius')
    depth = vc.value(cls, 'water_depth')
    up = g.normal[:, 1]
    flat = up >= math.cos(math.radians(slope)) - 0.005
    groundlike = up >= math.cos(math.radians(min(slope + GROUND_LIKE_SLOPE, 80.0)))
    clear = (g.flags & 1) == 0
    wet_ok = (sea_level - g.y) <= depth
    ok = clear & flat & wet_ok
    if forbid_mask is not None:
        ok &= ~forbid_mask
    # the ground under the disc continues: every column within the radius has a ground-like surface within the slope's
    # rise (plus the step) of this one
    K = 8
    Y = g.dense(np.where(groundlike, g.y, np.nan), K).astype(np.float32)
    tan = math.tan(math.radians(slope + GROUND_LIKE_SLOPE / 2))
    rc = int(math.floor((radius - g.pitch / 2) / g.pitch + 1e-9))
    ks = np.nonzero(ok)[0]
    good = np.ones(len(ks), bool)
    for dj in range(-rc, rc + 1):
        for di in range(-rc, rc + 1):
            d = math.hypot(di, dj) * g.pitch
            if (di == 0 and dj == 0) or d > radius - g.pitch / 2 + 1e-9:
                continue
            ii, jj = g.i[ks] + di, g.j[ks] + dj
            inside = (ii >= 0) & (jj >= 0) & (ii < g.nx) & (jj < g.nz)
            tol = step + tan * d
            hit = np.zeros(len(ks), bool)
            yy = Y[np.clip(jj, 0, g.nz - 1), np.clip(ii, 0, g.nx - 1)]          # (len, K)
            hit = inside & np.any(np.abs(yy - g.y[ks][:, None]) <= tol, axis=1)
            good &= hit
    ok[ks[~good]] = False
    log(f'  drivable: {int(ok.sum())} of {g.n} surfaces (blocked {int((~clear).sum())}, steep {int((~flat).sum())}, '
        f'deep {int((~wet_ok).sum())}, bank or drop under the disc {int((~good).sum())})')
    return ok


def forbid_surfaces(g, forbid_tris, tol=0.3):
    """Bool per surface: on the top of a ForbidPathfinding object (its triangle under the column within tol)."""
    out = np.zeros(g.n, bool)
    if forbid_tris is None or not len(forbid_tris):
        return out
    import navsample1943
    T = forbid_tris.astype(np.float64)
    lo = np.floor((T[:, :, [0, 2]].min(1) - [g.x0, g.z0]) / g.pitch).astype(np.int64)
    hi = np.ceil((T[:, :, [0, 2]].max(1) - [g.x0, g.z0]) / g.pitch).astype(np.int64)
    for t in range(len(T)):
        i0, j0 = max(lo[t, 0], 0), max(lo[t, 1], 0)
        i1, j1 = min(hi[t, 0], g.nx - 1), min(hi[t, 1], g.nz - 1)
        if i1 < i0 or j1 < j0:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        ii, jj = ii.ravel(), jj.ravel()
        px, pz = g.x0 + ii * g.pitch, g.z0 + jj * g.pitch
        u, v, w, ins = navsample1943.inside(T[t:t + 1, :, [0, 2]], px, pz)
        ins = ins[0]
        if not ins.any():
            continue
        Y = u[0] * T[t, 0, 1] + v[0] * T[t, 1, 1] + w[0] * T[t, 2, 1]
        for c, yv in zip((jj[ins] * g.nx + ii[ins]).tolist(), Y[ins].tolist()):
            for k in range(g.start[c], g.start[c + 1]):
                if abs(g.y[k] - yv) <= tol:
                    out[k] = True
    return out


# ---- the route graph -------------------------------------------------------------------------------------------------

def link_table(g, ok, step):
    """{(k, di, dj): n} of linked drivable neighbours, and the undirected pairs."""
    table = {}
    pairs = []
    dense = dense_surfaces(g, ok)
    for di, dj in DIRS8:
        a, b = neighbour_links(g, ok, step, di, dj, dense)
        for k, n in zip(a.tolist(), b.tolist()):
            table[(k, di, dj)] = n
        if not (di and dj):                  # components by straight links (a corner touch is no passage)
            pairs.append(np.stack([a, b], 1))
    P = np.concatenate(pairs) if pairs else np.zeros((0, 2), np.int64)
    return table, P


def grid_line(g, table, ka, kb):
    """Whether a straight line from surface ka to kb stays on linked drivable surfaces (the grid line rule): the
    columns the line crosses, stepped through by straight neighbours (a diagonal step: through a linked side column
    on either side). Returns the surfaces passed, or None."""
    ia, ja, ib, jb = int(g.i[ka]), int(g.j[ka]), int(g.i[kb]), int(g.j[kb])
    n = max(abs(ib - ia), abs(jb - ja))
    if n == 0:
        return [ka] if ka == kb else None
    cur, ci, cj = ka, ia, ja
    out = [ka]
    for s in range(1, n + 1):
        ti = int(round(ia + (ib - ia) * s / n))
        tj = int(round(ja + (jb - ja) * s / n))
        di, dj = ti - ci, tj - cj
        if di and dj:
            nxt = None
            for a, b in (((di, 0), (0, dj)), ((0, dj), (di, 0))):
                m1 = table.get((cur, a[0], a[1]))
                if m1 is not None:
                    m2 = table.get((m1, b[0], b[1]))
                    if m2 is not None:
                        nxt = m2
                        break
            if nxt is None:
                return None
        else:
            nxt = table.get((cur, di, dj))
            if nxt is None:
                return None
        cur, ci, cj = nxt, ti, tj
        out.append(cur)
    return out if cur == kb else None


def clearance(g, ok, max_cells=6):
    """Per surface: cells (chessboard) to the nearest column without a drivable surface linked at this height
    (capped at max_cells) - an approximation by the column masks: drivable columns."""
    m = np.zeros((g.nz, g.nx), bool)
    m[g.j[ok], g.i[ok]] = True
    d = np.where(m, max_cells, 0).astype(np.int64)
    cur = m.copy()
    for r in range(1, max_cells):
        er = cur.copy()
        er[1:, :] &= cur[:-1, :]
        er[:-1, :] &= cur[1:, :]
        er[:, 1:] &= cur[:, :-1]
        er[:, :-1] &= cur[:, 1:]
        d = np.where(cur & ~er, np.minimum(d, r), d)
        cur = er
    return d[g.j, g.i]


def build_graph(g, ok, table, labels, spacing, log=print):
    """Route nodes (surface indices) and edges [(a, b)] over the drivable surfaces."""
    clr = clearance(g, ok)
    ks = np.nonzero(ok)[0]
    bi, bj = g.i[ks] // spacing, g.j[ks] // spacing
    # one node per (block, component): the surface farthest from the undrivable, then nearest the block's centre
    ci = (bi * spacing + spacing / 2.0 - 0.5)
    cj = (bj * spacing + spacing / 2.0 - 0.5)
    centre_d = (g.i[ks] - ci) ** 2 + (g.j[ks] - cj) ** 2
    key = np.lexsort((centre_d, -clr[ks], labels[ks], bj, bi))
    ks_s = ks[key]
    blk = np.stack([bi[key], bj[key], labels[ks_s]], 1)
    first = np.ones(len(ks_s), bool)
    first[1:] = np.any(blk[1:] != blk[:-1], axis=1)
    nodes = ks_s[first].tolist()
    node_blk = {(int(a), int(b), int(c)): n for (a, b, c), n in zip(blk[first].tolist(), nodes)}
    log(f'  {len(nodes)} route nodes ({spacing} x {spacing} cell blocks)')
    edges = set()
    tried = 0
    for (a, b, c), n in node_blk.items():
        for da, db in ((1, 0), (0, 1), (1, 1), (1, -1), (2, 1), (1, 2), (2, -1), (1, -2)):
            m = node_blk.get((a + da, b + db, c))
            if m is None:
                continue
            tried += 1
            if grid_line(g, table, n, m) is not None and grid_line(g, table, m, n) is not None:
                edges.add((min(n, m), max(n, m)))
    log(f'  {len(edges)} edges of {tried} candidate pairs')
    nodes, edges = join_pieces(g, ok, table, labels, nodes, edges, log)
    return nodes, sorted(edges)


def join_pieces(g, ok, table, labels, nodes, edges, log):
    """Joins graph pieces lying on one drivable component: from each smaller piece, the grid's shortest path (BFS
    over linked surfaces) to the nearest node of another piece of the component, with nodes placed along it by
    the line rule (each as far along the path as a straight line holds)."""
    import collections
    nodes = list(nodes)
    added = 0
    for _round in range(6):
        node_set = set(nodes)
        idx = {n: k for k, n in enumerate(nodes)}
        pairs = np.array([(idx[a], idx[b]) for a, b in edges], np.int64).reshape(-1, 2)
        lab = components(len(nodes), pairs)
        by_comp = collections.defaultdict(lambda: collections.defaultdict(list))
        for k, n in enumerate(nodes):
            by_comp[int(labels[n])][int(lab[k])].append(n)
        work = [(c, pieces) for c, pieces in by_comp.items() if len(pieces) > 1]
        if not work:
            break
        for c, pieces in work:
            big = max(pieces, key=lambda p: len(pieces[p]))
            for p, members in pieces.items():
                if p == big:
                    continue
                # BFS from the piece's nodes to any node of another piece
                start = set(members)
                prev = {n: None for n in members}
                q = collections.deque(members)
                goal = None
                while q and goal is None:
                    k = q.popleft()
                    for di, dj in DIRS8[:4]:
                        n = table.get((k, di, dj))
                        if n is None or n in prev:
                            continue
                        prev[n] = k
                        if n in node_set and n not in start:
                            goal = n
                            break
                        q.append(n)
                if goal is None:
                    continue
                path = [goal]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                path.reverse()
                # string-pull along the path with the line rule
                a = 0
                while a < len(path) - 1:
                    b = len(path) - 1
                    while b > a + 1 and (grid_line(g, table, path[a], path[b]) is None or
                                         grid_line(g, table, path[b], path[a]) is None):
                        b -= 1
                    u, v = path[a], path[b]
                    for w in (u, v):
                        if w not in node_set:
                            nodes.append(w)
                            node_set.add(w)
                            added += 1
                    edges.add((min(u, v), max(u, v)))
                    a = b
    if added:
        log(f'  joined graph pieces: {added} nodes added')
    return nodes, edges


def spawn_seeds(lv, cls, game=None):
    """[(vehicle name, team, position, yaw, [entry points], spawn index)] of the level's ground vehicle spawns."""
    from bfbc2core import level1943, vehicles1943
    lvl = level1943.load(lv, game=game, log=lambda *a: None)
    g = game1943.Game(game1943.find_game(game), lv)
    out = []
    for e in lvl.entities:
        if e.category != 'vehicle spawn' or e.position is None:
            continue
        part = vehicles1943.vehicle_partition(e)
        name = part.rsplit('/', 1)[-1] if part else ''
        if vehicles1943.vehicle_class(name) != 'ground vehicle':
            continue
        M = np.asarray(e.matrix, float)
        yaw = math.atan2(M[2, 0], M[2, 2])
        out.append({'vehicle': name, 'class': next((c for c, p in vc.CLASSES.items() if name in p['vehicles']), None),
                    'team': e.team, 'pos': [float(v) for v in e.position], 'yaw': yaw,
                    'entries': vehicles1943.entry_points(g, e), 'index': e.index})
    spawn_seeds.objectives = getattr(lvl, 'objectives', [])
    return out


def build(lv, cls, log=print, game=None):
    """The class's world: returns (rsnv path, stats)."""
    import rsnvgrid1943
    p = paths(lv)
    sp = p['samples'](cls)
    if not os.path.isfile(sp):
        raise FileNotFoundError(f'{lv} {cls}: not sampled yet (missing {sp}): run groundnav1943.py sample {lv} {cls}')
    st = json.load(open(os.path.join(p['navkit'], 'navkit.json'), encoding='utf-8'))
    sea = float(st.get('sea_level', 0.0))
    t0 = time.time()
    g = Grid(sp)
    log(f'{lv} {cls}: {g.nx} x {g.nz} columns, {g.n} surfaces (radius {g.radius}, step {g.step}, height {g.height})')
    forbid = os.path.join(p['objects'], 'forbid_tris.npy')
    fmask = forbid_surfaces(g, np.load(forbid)) if os.path.isfile(forbid) else None
    ok = drivable(g, cls, sea, fmask, log)
    table, pairs = link_table(g, ok, vc.value(cls, 'step'))
    labels = components(g.n, pairs)
    # keep the components a ground-vehicle spawn stands on (within SEED_REACH)
    seeds = spawn_seeds(lv, cls, game)
    keep_labels = set()
    ks = np.nonzero(ok)[0]
    px, pz = g.x0 + g.i[ks] * g.pitch, g.z0 + g.j[ks] * g.pitch
    for s in seeds:
        d = np.hypot(px - s['pos'][0], pz - s['pos'][2]) + np.abs(g.y[ks] - s['pos'][1]) * 2
        if len(d) and d.min() <= SEED_REACH:
            keep_labels.add(int(labels[ks[int(np.argmin(d))]]))
    kept = ok & np.isin(labels, list(keep_labels))
    sizes = np.bincount(labels[kept]) if kept.any() else np.zeros(0)
    log(f'  {len(seeds)} ground-vehicle spawns; kept {len(keep_labels)} components, {int(kept.sum())} surfaces '
        f'({int(kept.sum()) * g.pitch * g.pitch / 1e6:.2f} km2), largest {int(sizes.max()) if len(sizes) else 0}')
    table = {k: v for k, v in table.items() if kept[k[0]] and kept[v]}
    nodes, edges = build_graph(g, kept, table, labels, int(vc.value(cls, 'node_spacing')), log)
    out = p['rsnv'](cls)
    hf = os.path.join(p['objects'], 'heightfield.res')
    meta_sources = {'samples': sp, 'vehicle all_tris.npy': os.path.join(p['vobjects'], 'all_tris.npy'),
                    'forbid_tris.npy': forbid, 'navkit.json': os.path.join(p['navkit'], 'navkit.json'), 'heightfield.res': hf}
    soldier = os.path.join(p['navkit'], f'{lv}.rsnv')
    meta_sources['soldier rsnv'] = soldier
    stats = rsnvgrid1943.export_surfaces(lv, cls, g, kept, table, nodes, edges, seeds, out, st, meta_sources, sea, log,
                                         soldier=soldier, objectives=spawn_seeds.objectives)
    stats['seconds'] = round(time.time() - t0, 1)
    log(f'{lv} {cls}: wrote {out} in {stats["seconds"]:.0f} s')
    return out, stats


def main(argv):
    ap = argparse.ArgumentParser(prog='groundnav1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('step', choices=['prepare', 'sample', 'build', 'all'])
    ap.add_argument('level')
    ap.add_argument('cls', nargs='?', choices=GROUND_CLASSES)
    a = ap.parse_args(argv[1:])
    classes = [a.cls] if a.cls else list(GROUND_CLASSES)
    if a.step == 'prepare':
        prepare(a.level)
        return 0
    for cls in classes:
        if a.step in ('sample', 'all'):
            sample(a.level, cls)
        if a.step in ('build', 'all'):
            build(a.level, cls)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('groundnav1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
