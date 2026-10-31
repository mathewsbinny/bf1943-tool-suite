r"""NavKit (big-endian): navigation for BF1943's ground vehicles - a build per class (jeep, tank) with the vehicle's
clearance, exported to RSNV as its own world (<level>.jeep.rsnv, <level>.tank.rsnv) for the navigation runtime
(project-opfor-bf1943 rsnav).

  groundnav1943.py prepare <level>            the vehicles' collision (sample runs it when it is missing or of
                                              another preparation: navkit.json's stamps, vehicleclasses1943.py)
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
import math
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
import navkitjson1943 as nj  # noqa: E402
import soldier1943  # noqa: E402
import vehicleclasses1943 as vc  # noqa: E402
from bfbc2core import game1943, terrain  # noqa: E402

GROUND_CLASSES = ('jeep', 'tank')
GROUND_LIKE_SLOPE = 10.0     # deg over the class's slope: ground under the disc may be this much steeper than drivable
SEED_REACH = 6.0             # m: a spawn keeps the drivable component within this of it


def paths(lv):
    work = game1943.level_work(lv)
    vehicles = os.path.join(work, 'vehicles')
    return {'work': work, 'objects': os.path.join(work, 'objects'), 'vehicles': vehicles,
            'vobjects': os.path.join(vehicles, 'objects'), 'navkit': os.path.join(work, 'navkit'),
            'samples': lambda cls: os.path.join(vehicles, f'samples_{cls}.bin'),
            'rsnv': lambda cls: os.path.join(work, 'navkit', f'{lv}.{cls}.rsnv')}


sha256 = nj.sha256                 # (navkitjson1943: one chunked SHA-256 for every tool)


def _rows(a):
    """Each row of a (as float32) as one opaque value, so that np.isin compares whole triangles."""
    a = np.ascontiguousarray(a.reshape(len(a), -1).astype(np.float32))
    return a.view(np.dtype((np.void, a.dtype.itemsize * a.shape[1]))).ravel()


def prepare(lv, log=print):
    """work\\levels\\<lv>\\vehicles\\objects\\: all_tris.npy without excluded objects and dynamic props, and the box
    files the sampler reads. Returns the triangle counts. Stamped in navkit.json (vehicleclasses1943: 'collision',
    the preparation it was made from)."""
    files = paths(lv)
    objects, vobjects = files['objects'], files['vobjects']
    if not os.path.isfile(os.path.join(objects, 'all_tris.npy')):
        raise FileNotFoundError(f'{lv}: the level is not prepared (missing {os.path.join(objects, "all_tris.npy")}): '
                                f'run navkit.py prepare {lv}')
    want = vc.inputs(nj.read(lv), lv)
    vc.drop(lv, 'collision')
    os.makedirs(vobjects, exist_ok=True)
    tris = np.load(os.path.join(objects, 'all_tris.npy'))
    left_out = []                                  # the excluded objects' and dynamic props' triangles, as _rows
    for name in ('exclude_tris.npy', 'dynamic_tris.npy'):
        path = os.path.join(objects, name)
        if os.path.isfile(path):
            excluded = np.load(path)
            if len(excluded):
                left_out.append(_rows(excluded))
    keep = np.ones(len(tris), bool)
    if left_out:
        keep = ~np.isin(_rows(tris), np.concatenate(left_out))
    np.save(os.path.join(vobjects, 'all_tris.npy'), tris[keep])
    for name in ('soldier_spawns.json', 'object_boxes.json'):
        with open(os.path.join(objects, name), 'rb') as src, open(os.path.join(vobjects, name), 'wb') as dst:
            dst.write(src.read())
    vc.record(lv, 'collision', want)
    log(f'{lv}: vehicle collision {int(keep.sum())} of {len(tris)} triangles '
        f'(excluded objects and dynamic props left out)')
    return int(keep.sum()), len(tris)


def sample_command(lv, cls):
    files = paths(lv)
    state = nj.read(lv)
    args = [sys.executable, '-B', '-u', os.path.join(HERE, 'navsample1943.py'), files['vobjects'],
            os.path.join(files['objects'], 'heightfield.res'), files['samples'](cls)]
    for key in vc.SAMPLING_PARAMS:                 # the samples' stamp hashes these
        args += [f'--{key}', str(vc.value(cls, key))]
    return args + soldier1943.sea_level_args(state)


def samples_current(lv, cls, st=None):
    """Whether the class's samples on disk are of the current preparation and class parameters (their stamp)."""
    st = st or nj.read(lv)
    if not os.path.isfile(paths(lv)['samples'](cls)):
        return False
    return vc.current(st, 'samples', vc.inputs(st, lv, cls, sampling=True), cls)


def sample(lv, cls, log=print):
    """The class's samples, on the vehicle collision of the current preparation (made again when it is of another:
    2026-10-30, it was made only when its file was missing). Stamped 'samples' / cls; the class's world loses its."""
    files = paths(lv)
    state = nj.read(lv)
    want = vc.inputs(state, lv, cls, sampling=True)
    collision_made = os.path.isfile(os.path.join(files['vobjects'], 'all_tris.npy'))
    if not collision_made or not vc.current(state, 'collision', vc.inputs(state, lv)):
        prepare(lv, log)
    vc.drop(lv, 'samples', cls)
    vc.drop(lv, 'worlds', cls)
    args = sample_command(lv, cls)
    log_path = os.path.join(files['vehicles'], f'sample_{cls}.log')
    started = time.time()
    with open(log_path, 'w', encoding='utf-8', newline='\n') as out:
        result = subprocess.run(args, stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{lv} {cls}: the sampler failed (exit {result.returncode}); see {log_path}')
    vc.record(lv, 'samples', want, cls)
    log(f'{lv} {cls}: sampled in {(time.time() - started) / 60:.1f} min ({files["samples"](cls)})')


# ---- the samples, as arrays ----------------------------------------------------------------------------------------

class Grid:
    """The samples of one run: columns j-major; surfaces in CSR order (start per column). Per surface k: y[k],
    normal[k], flags[k] (bit 0: blocked), its column col[k] = j[k] * nx + i[k], and the slope as gx, gz (the rise
    per metre along x and z). Elsewhere in this file g is a Grid and k, n, m surface indices."""

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
        n_columns = nx * nz
        counts = np.zeros(n_columns, np.int64)
        at = navsample1943.HEADER.size
        record_starts = np.zeros(n_columns, np.int64)     # per column: the byte offset of its first record
        view = memoryview(data)
        for c in range(n_columns):                       # a count byte, then 8-byte records
            n = view[at]
            counts[c] = n
            record_starts[c] = at + 1
            at += 1 + 8 * n
        self.start = np.zeros(n_columns + 1, np.int64)
        np.cumsum(counts, out=self.start[1:])
        total = int(self.start[-1])
        rank = np.arange(total) - np.repeat(self.start[:-1], counts)     # each surface's place in its column
        record_offset = np.repeat(record_starts, counts) + 8 * rank
        raw = np.frombuffer(data, np.uint8)
        records = raw[record_offset[:, None] + np.arange(8)[None, :]]
        self.y = records[:, :4].copy().view('<f4').ravel().astype(np.float64)
        self.normal = records[:, 4:7].view(np.int8).astype(np.float64) / 127.0
        self.flags = records[:, 7].copy()
        self.col = np.repeat(np.arange(n_columns), counts)
        self.i = self.col % nx
        self.j = self.col // nx
        self.n = total
        normal_y = np.maximum(self.normal[:, 1], 0.05)
        self.gx, self.gz = -self.normal[:, 0] / normal_y, -self.normal[:, 2] / normal_y

    @classmethod
    def from_columns(cls, pitch, x0, z0, nx, nz, columns, radius=0.0, step=0.0, height=0.0):
        """A grid from made-up data (tests): columns[j * nx + i] = [(y, (nx, ny, nz), flags)], top down."""
        grid = cls.__new__(cls)
        grid.pitch, grid.x0, grid.z0, grid.nx, grid.nz = pitch, x0, z0, nx, nz
        grid.radius, grid.step, grid.height = radius, step, height
        counts = np.array([len(c) for c in columns], np.int64)
        grid.start = np.zeros(nx * nz + 1, np.int64)
        np.cumsum(counts, out=grid.start[1:])
        surfaces = [surface for column in columns for surface in column]
        grid.y = np.array([surface[0] for surface in surfaces], np.float64)
        grid.normal = np.array([surface[1] for surface in surfaces], np.float64).reshape(-1, 3)
        grid.flags = np.array([surface[2] for surface in surfaces], np.uint8)
        grid.col = np.repeat(np.arange(nx * nz), counts)
        grid.i, grid.j, grid.n = grid.col % nx, grid.col // nx, len(surfaces)
        normal_y = np.maximum(grid.normal[:, 1], 0.05)
        grid.gx, grid.gz = -grid.normal[:, 0] / normal_y, -grid.normal[:, 2] / normal_y
        return grid

    def xz(self, k):
        return self.x0 + self.i[k] * self.pitch, self.z0 + self.j[k] * self.pitch

    def dense(self, values, k_max=8, fill=np.nan):
        """(nz, nx, k_max) array of a per-surface value by column (the first k_max surfaces, top down)."""
        out = np.full((self.nz, self.nx, k_max), fill)
        rank = np.arange(self.n) - self.start[self.col]
        first = rank < k_max
        out[self.j[first], self.i[first], rank[first]] = values[first]
        return out


def neighbour_links(g, ok, step, di, dj, dense=None):
    """For direction (di, dj): (k, n) pairs of surfaces in ok linked by the step rule (each k: its best n). dense:
    (Y, IDX) from dense_surfaces, shared by the eight directions."""
    K = 8                                          # surfaces per column looked at
    Y, IDX = dense if dense is not None else dense_surfaces(g, ok)
    ks = np.nonzero(ok)[0]                         # the surfaces linked from
    ni, nj = g.i[ks] + di, g.j[ks] + dj            # their neighbour columns
    inside = (ni >= 0) & (nj >= 0) & (ni < g.nx) & (nj < g.nz)
    ks, ni, nj = ks[inside], ni[inside], nj[inside]
    best_n = np.full(len(ks), -1, np.int64)
    best_rise = np.full(len(ks), np.inf)
    for rank in range(K):
        n = IDX[nj, ni, rank]
        y_n = Y[nj, ni, rank]
        valid = (n >= 0) & ~np.isnan(y_n)
        n_safe = np.where(valid, n, 0)
        dy = y_n - g.y[ks]
        # the rise the two surfaces' mean slope predicts over one cell in this direction
        predicted = g.pitch * ((g.gx[ks] + g.gx[n_safe]) / 2 * di + (g.gz[ks] + g.gz[n_safe]) / 2 * dj)
        fit = valid & (np.abs(dy - predicted) <= step)
        rise = np.where(fit, np.abs(dy), np.inf)
        better = rise < best_rise
        best_rise = np.where(better, rise, best_rise)
        best_n = np.where(better, n, best_n)
    found = best_n >= 0
    return ks[found], best_n[found]


def dense_surfaces(g, ok, K=8):
    """(Y, IDX): per column the first K surfaces' heights (NaN where not in ok) and indices (-1 none)."""
    Y = g.dense(np.where(ok, g.y, np.nan), K).astype(np.float32)
    IDX = g.dense(np.arange(g.n).astype(float), K, fill=-1).astype(np.int32)
    return Y, IDX


def components(n, pairs):
    """Connected-component labels (smallest member index) of n items under undirected pairs (a, b)."""
    labels = np.arange(n)
    if not len(pairs):
        return labels
    a, b = pairs[:, 0], pairs[:, 1]
    while True:
        lower = np.minimum(labels[a], labels[b])
        changed = False
        for side in (a, b):
            current = labels[side]
            lowered = lower < current
            if lowered.any():
                np.minimum.at(labels, side[lowered], lower[lowered])
                changed = True
        labels = labels[labels]                    # pointer jumping
        while True:
            jumped = labels[labels]
            if np.array_equal(jumped, labels):
                break
            labels = jumped
        if not changed:
            return labels


DIRS8 = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def drivable(g, cls, sea_level, forbid_mask=None, log=print):
    """Bool per surface: drivable centre positions for the class (see the module's description)."""
    slope = vc.value(cls, 'slope')
    step = vc.value(cls, 'step')
    radius = vc.value(cls, 'radius')
    depth = vc.value(cls, 'water_depth')
    normal_up = g.normal[:, 1]
    flat = normal_up >= math.cos(math.radians(slope)) - 0.005
    groundlike = normal_up >= math.cos(math.radians(min(slope + GROUND_LIKE_SLOPE, 80.0)))
    clear = (g.flags & 1) == 0
    wet_ok = (sea_level - g.y) <= depth
    ok = clear & flat & wet_ok
    if forbid_mask is not None:
        ok &= ~forbid_mask
    # the ground under the disc continues: every column within the radius has a ground-like surface within the slope's
    # rise (plus the step) of this one
    K = 8                                          # surfaces per column looked at
    Y = g.dense(np.where(groundlike, g.y, np.nan), K).astype(np.float32)
    bank_tan = math.tan(math.radians(slope + GROUND_LIKE_SLOPE / 2))
    reach = int(math.floor((radius - g.pitch / 2) / g.pitch + 1e-9))     # cells
    ks = np.nonzero(ok)[0]                         # the surfaces still drivable
    good = np.ones(len(ks), bool)
    for dj in range(-reach, reach + 1):
        for di in range(-reach, reach + 1):
            dist = math.hypot(di, dj) * g.pitch
            if (di == 0 and dj == 0) or dist > radius - g.pitch / 2 + 1e-9:
                continue
            ni, nj = g.i[ks] + di, g.j[ks] + dj
            inside = (ni >= 0) & (nj >= 0) & (ni < g.nx) & (nj < g.nz)
            tol = step + bank_tan * dist
            heights = Y[np.clip(nj, 0, g.nz - 1), np.clip(ni, 0, g.nx - 1)]          # (len, K)
            hit = inside & np.any(np.abs(heights - g.y[ks][:, None]) <= tol, axis=1)
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
    tris = forbid_tris.astype(np.float64)
    cell_lo = np.floor((tris[:, :, [0, 2]].min(1) - [g.x0, g.z0]) / g.pitch).astype(np.int64)
    cell_hi = np.ceil((tris[:, :, [0, 2]].max(1) - [g.x0, g.z0]) / g.pitch).astype(np.int64)
    for tri in range(len(tris)):
        i0, j0 = max(cell_lo[tri, 0], 0), max(cell_lo[tri, 1], 0)
        i1, j1 = min(cell_hi[tri, 0], g.nx - 1), min(cell_hi[tri, 1], g.nz - 1)
        if i1 < i0 or j1 < j0:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))     # the columns under its box
        ii, jj = ii.ravel(), jj.ravel()
        px, pz = g.x0 + ii * g.pitch, g.z0 + jj * g.pitch
        u, v, w, inside = navsample1943.inside(tris[tri:tri + 1, :, [0, 2]], px, pz)   # barycentric, per column
        inside = inside[0]
        if not inside.any():
            continue
        tri_y = u[0] * tris[tri, 0, 1] + v[0] * tris[tri, 1, 1] + w[0] * tris[tri, 2, 1]
        for c, y in zip((jj[inside] * g.nx + ii[inside]).tolist(), tri_y[inside].tolist()):
            for k in range(g.start[c], g.start[c + 1]):
                if abs(g.y[k] - y) <= tol:
                    out[k] = True
    return out


# ---- the route graph -------------------------------------------------------------------------------------------------

def link_table(g, ok, step):
    """{(k, di, dj): n} of linked drivable neighbours, and the undirected pairs."""
    table = {}
    pairs = []
    dense = dense_surfaces(g, ok)
    for di, dj in DIRS8:
        from_k, to_n = neighbour_links(g, ok, step, di, dj, dense)
        for k, n in zip(from_k.tolist(), to_n.tolist()):
            table[(k, di, dj)] = n
        if not (di and dj):                  # components by straight links (a corner touch is no passage)
            pairs.append(np.stack([from_k, to_n], 1))
    all_pairs = np.concatenate(pairs) if pairs else np.zeros((0, 2), np.int64)
    return table, all_pairs


def grid_line(g, table, ka, kb):
    """Whether a straight line from surface ka to kb stays on linked drivable surfaces (the grid line rule): the
    columns the line crosses, stepped through by straight neighbours (a diagonal step: through a linked side column
    on either side). Returns the surfaces passed, or None."""
    ia, ja, ib, jb = int(g.i[ka]), int(g.j[ka]), int(g.i[kb]), int(g.j[kb])
    steps = max(abs(ib - ia), abs(jb - ja))
    if steps == 0:
        return [ka] if ka == kb else None
    current, ci, cj = ka, ia, ja
    out = [ka]
    for s in range(1, steps + 1):
        to_i = int(round(ia + (ib - ia) * s / steps))
        to_j = int(round(ja + (jb - ja) * s / steps))
        di, dj = to_i - ci, to_j - cj
        if di and dj:
            following = None
            for first, second in (((di, 0), (0, dj)), ((0, dj), (di, 0))):
                side = table.get((current, first[0], first[1]))
                if side is not None:
                    beyond = table.get((side, second[0], second[1]))
                    if beyond is not None:
                        following = beyond
                        break
            if following is None:
                return None
        else:
            following = table.get((current, di, dj))
            if following is None:
                return None
        current, ci, cj = following, to_i, to_j
        out.append(current)
    return out if current == kb else None


def clearance(g, ok, max_cells=6):
    """Per surface: cells (chessboard) to the nearest column without a drivable surface linked at this height
    (capped at max_cells) - an approximation by the column masks: drivable columns."""
    drivable_column = np.zeros((g.nz, g.nx), bool)
    drivable_column[g.j[ok], g.i[ok]] = True
    dist = np.where(drivable_column, max_cells, 0).astype(np.int64)
    remaining = drivable_column.copy()
    for r in range(1, max_cells):
        eroded = remaining.copy()
        eroded[1:, :] &= remaining[:-1, :]
        eroded[:-1, :] &= remaining[1:, :]
        eroded[:, 1:] &= remaining[:, :-1]
        eroded[:, :-1] &= remaining[:, 1:]
        dist = np.where(remaining & ~eroded, np.minimum(dist, r), dist)
        remaining = eroded
    return dist[g.j, g.i]


def build_graph(g, ok, table, labels, spacing, log=print):
    """Route nodes (surface indices) and edges [(a, b)] over the drivable surfaces."""
    clear_cells = clearance(g, ok)
    ks = np.nonzero(ok)[0]                         # the drivable surfaces
    block_i, block_j = g.i[ks] // spacing, g.j[ks] // spacing
    # one node per (block, component): the surface farthest from the undrivable, then nearest the block's centre
    centre_i = (block_i * spacing + spacing / 2.0 - 0.5)
    centre_j = (block_j * spacing + spacing / 2.0 - 0.5)
    centre_dist2 = (g.i[ks] - centre_i) ** 2 + (g.j[ks] - centre_j) ** 2
    order = np.lexsort((centre_dist2, -clear_cells[ks], labels[ks], block_j, block_i))
    ordered = ks[order]
    block_keys = np.stack([block_i[order], block_j[order], labels[ordered]], 1)
    first = np.ones(len(ordered), bool)
    first[1:] = np.any(block_keys[1:] != block_keys[:-1], axis=1)
    nodes = ordered[first].tolist()
    node_of_block = {(int(bi), int(bj), int(label)): node
                     for (bi, bj, label), node in zip(block_keys[first].tolist(), nodes)}
    log(f'  {len(nodes)} route nodes ({spacing} x {spacing} cell blocks)')
    edges = set()
    tried = 0
    for (bi, bj, label), node in node_of_block.items():
        for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1), (2, 1), (1, 2), (2, -1), (1, -2)):
            other = node_of_block.get((bi + di, bj + dj, label))
            if other is None:
                continue
            tried += 1
            if grid_line(g, table, node, other) is not None and grid_line(g, table, other, node) is not None:
                edges.add((min(node, other), max(node, other)))
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
        index_of = {node: index for index, node in enumerate(nodes)}
        pairs = np.array([(index_of[a], index_of[b]) for a, b in edges], np.int64).reshape(-1, 2)
        piece_of = components(len(nodes), pairs)        # per node (by its index in nodes): its graph piece
        # {drivable component: {graph piece: [its nodes]}}
        pieces_by_component = collections.defaultdict(lambda: collections.defaultdict(list))
        for index, node in enumerate(nodes):
            pieces_by_component[int(labels[node])][int(piece_of[index])].append(node)
        split = [(component, pieces) for component, pieces in pieces_by_component.items() if len(pieces) > 1]
        if not split:
            break
        for component, pieces in split:
            largest = max(pieces, key=lambda piece: len(pieces[piece]))
            for piece, members in pieces.items():
                if piece == largest:
                    continue
                # BFS from the piece's nodes to any node of another piece
                own = set(members)
                came_from = {node: None for node in members}
                queue = collections.deque(members)
                goal = None
                while queue and goal is None:
                    k = queue.popleft()
                    for di, dj in DIRS8[:4]:
                        n = table.get((k, di, dj))
                        if n is None or n in came_from:
                            continue
                        came_from[n] = k
                        if n in node_set and n not in own:
                            goal = n
                            break
                        queue.append(n)
                if goal is None:
                    continue
                path = [goal]
                while came_from[path[-1]] is not None:
                    path.append(came_from[path[-1]])
                path.reverse()
                # string-pull along the path with the line rule
                at = 0
                while at < len(path) - 1:
                    to = len(path) - 1
                    while to > at + 1 and (grid_line(g, table, path[at], path[to]) is None or
                                           grid_line(g, table, path[to], path[at]) is None):
                        to -= 1
                    u, v = path[at], path[to]
                    for k in (u, v):
                        if k not in node_set:
                            nodes.append(k)
                            node_set.add(k)
                            added += 1
                    edges.add((min(u, v), max(u, v)))
                    at = to
    if added:
        log(f'  joined graph pieces: {added} nodes added')
    return nodes, edges


def spawn_seeds(lv, cls, game=None):
    """[(vehicle name, team, position, yaw, [entry points], spawn index)] of the level's ground vehicle spawns."""
    from bfbc2core import level1943, vehicles1943
    level = level1943.load(lv, game=game, log=lambda *a: None)
    game_copy = game1943.Game(game1943.find_game(game), lv)
    out = []
    for entity in level.entities:
        if entity.category != 'vehicle spawn' or entity.position is None:
            continue
        partition = vehicles1943.vehicle_partition(entity)
        name = partition.rsplit('/', 1)[-1] if partition else ''
        if vehicles1943.vehicle_class(name) != 'ground vehicle':
            continue
        matrix = np.asarray(entity.matrix, float)
        yaw = math.atan2(matrix[2, 0], matrix[2, 2])
        vehicle_class = next((c for c, params in vc.CLASSES.items() if name in params['vehicles']), None)
        out.append({'vehicle': name, 'class': vehicle_class,
                    'team': entity.team, 'pos': [float(v) for v in entity.position], 'yaw': yaw,
                    'entries': vehicles1943.entry_points(game_copy, entity), 'index': entity.index})
    spawn_seeds.objectives = getattr(level, 'objectives', [])
    return out


def build(lv, cls, log=print, game=None):
    """The class's world: returns (rsnv path, stats). Needs samples of the current preparation and class parameters
    and the export of the current soldier build; reads the level from the game copy it was prepared from (2026-10-30:
    game=None read the default copy's spawns). Stamped 'worlds' / cls."""
    import rsnvgrid1943
    files = paths(lv)
    samples_path = files['samples'](cls)
    if not os.path.isfile(samples_path):
        raise FileNotFoundError(f'{lv} {cls}: not sampled yet (missing {samples_path}): '
                                f'run groundnav1943.py sample {lv} {cls}')
    state = nj.read(lv)
    game = vc.prepared_game(state, game, lv)
    if not vc.current(state, 'samples', vc.inputs(state, lv, cls, sampling=True), cls):
        raise vc.StaleError(f'{lv} {cls}: the samples on disk are not of the current preparation and {cls} parameters: '
                            f'sample again (groundnav1943.py sample {lv} {cls})')
    # the export of the current soldier build, as the air and sea builders (2026-10-30: without the file the world was
    # built and stamped on no soldier world)
    soldier = os.path.join(files['navkit'], f'{lv}.rsnv')
    want = vc.inputs(state, lv, cls, soldier=vc.soldier_world(state, lv, soldier))
    vc.drop(lv, 'worlds', cls)
    hf_path = os.path.join(files['objects'], 'heightfield.res')
    sea = soldier1943.sea_level(state, terrain.read(hf_path))       # no water: under all
    started = time.time()
    g = Grid(samples_path)
    log(f'{lv} {cls}: {g.nx} x {g.nz} columns, {g.n} surfaces (radius {g.radius}, step {g.step}, height {g.height})')
    forbid = os.path.join(files['objects'], 'forbid_tris.npy')
    forbid_mask = forbid_surfaces(g, np.load(forbid)) if os.path.isfile(forbid) else None
    ok = drivable(g, cls, sea, forbid_mask, log)
    table, pairs = link_table(g, ok, vc.value(cls, 'step'))
    labels = components(g.n, pairs)
    # keep the components a ground-vehicle spawn stands on (within SEED_REACH)
    seeds = spawn_seeds(lv, cls, game)
    keep_labels = set()
    ks = np.nonzero(ok)[0]                         # the drivable surfaces
    px, pz = g.x0 + g.i[ks] * g.pitch, g.z0 + g.j[ks] * g.pitch
    for seed in seeds:
        dist = np.hypot(px - seed['pos'][0], pz - seed['pos'][2]) + np.abs(g.y[ks] - seed['pos'][1]) * 2
        if len(dist) and dist.min() <= SEED_REACH:
            keep_labels.add(int(labels[ks[int(np.argmin(dist))]]))
    kept = ok & np.isin(labels, list(keep_labels))
    sizes = np.bincount(labels[kept]) if kept.any() else np.zeros(0)
    log(f'  {len(seeds)} ground-vehicle spawns; kept {len(keep_labels)} components, {int(kept.sum())} surfaces '
        f'({int(kept.sum()) * g.pitch * g.pitch / 1e6:.2f} km2), largest {int(sizes.max()) if len(sizes) else 0}')
    table = {key: n for key, n in table.items() if kept[key[0]] and kept[n]}
    nodes, edges = build_graph(g, kept, table, labels, int(vc.value(cls, 'node_spacing')), log)
    out = files['rsnv'](cls)
    meta_sources = {'samples': samples_path, 'vehicle all_tris.npy': os.path.join(files['vobjects'], 'all_tris.npy'),
                    'forbid_tris.npy': forbid, 'navkit.json': os.path.join(files['navkit'], 'navkit.json'),
                    'heightfield.res': hf_path}
    meta_sources['soldier rsnv'] = soldier
    stats = rsnvgrid1943.export_surfaces(lv, cls, g, kept, table, nodes, edges, seeds, out, state, meta_sources, sea,
                                         log, soldier=soldier, objectives=spawn_seeds.objectives)
    vc.record(lv, 'worlds', dict(want, sha256=sha256(out)), cls)   # the file itself too (second review)
    stats['seconds'] = round(time.time() - started, 1)
    log(f'{lv} {cls}: wrote {out} in {stats["seconds"]:.0f} s')
    return out, stats


def main(argv):
    parser = argparse.ArgumentParser(prog='groundnav1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('step', choices=['prepare', 'sample', 'build', 'all'])
    parser.add_argument('level')
    parser.add_argument('cls', nargs='?', choices=GROUND_CLASSES)
    args = parser.parse_args(argv[1:])
    classes = [args.cls] if args.cls else list(GROUND_CLASSES)
    if args.step == 'prepare':
        prepare(args.level)
        return 0
    for cls in classes:
        if args.step in ('sample', 'all'):
            sample(args.level, cls)
        if args.step in ('build', 'all'):
            build(args.level, cls)
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('groundnav1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
