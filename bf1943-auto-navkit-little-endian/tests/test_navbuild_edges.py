r"""NavKit's builder on edge cases, with sample grids built here (no game data; the builder's file writer is replaced, so
nothing is written but a temp folder's files):
  - a level with no walkable ground fails with a clear BuildError, and leaves the previous pathdata.hpd as it was;
  - the file is put in place only whole (written beside, read back, then replaced);
  - a detailed sector whose 0.2 m run has no ground stays at 0.8 m with its route links: to its normal neighbour, and to
    a detailed sector next to it (the pass is made again without it, so that seam's nodes are carried);
  - a short ladder whose foot and top find one surface adds no self edge;
  - a clearance run on another grid than the main run is an error, not a silent column mismatch;
  - roof pruning sees the lowest surface of the last sampled column (empty columns after it: an interrupted run);
  - the detailed pass made again keeps no order id of the discarded attempt (its node ids are reused);
  - a column on a sector line belongs to one sector, the same in navbuild and detailed.py (whole centimetres).

  python -B test_navbuild_edges.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import navbuild  # noqa: E402
import navsample  # noqa: E402
import navwalk  # noqa: E402

failures = []
# (an older builder has no BuildError: every check below then reports its own failure)
BuildError = getattr(navbuild, 'BuildError', type('NoBuildError', (Exception,), {}))
QUIET = lambda *a: None  # noqa: E731


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def flat(pitch, min_x, min_z, nx, nz, y=0.0, ground=None):
    """One flat surface per column; ground(x, z) -> False leaves the column empty."""
    counts = np.ones(nx * nz, np.int64)
    if ground:
        for j in range(nz):
            for i in range(nx):
                counts[j * nx + i] = bool(ground(min_x + i * pitch, min_z + j * pitch))
    start = np.zeros(nx * nz + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    n = int(start[-1])
    return navsample.Samples(pitch, min_x, min_z, nx, nz, 50.0, -50.0, 0.4, 0.6, 1.8, 0.0, start,
                             np.full(n, y, np.float32), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                             np.zeros(n, np.uint8), None)


class Runs:
    """navsample.load replaced by a table of made-up runs; navbuild.write_file by a capture (unless keep_writer)."""
    def __init__(self, table, keep_writer=False):
        self.table, self.keep_writer, self.graphs = table, keep_writer, None

    def __enter__(self):
        self.load, self.write = navsample.load, navbuild.write_file
        navsample.load = lambda p: (self.table[p], 0)
        if not self.keep_writer:
            navbuild.write_file = lambda graphs, out, log, t0: setattr(self, 'graphs', graphs)
        return self

    def __exit__(self, *exc):
        navsample.load, navbuild.write_file = self.load, self.write


def edges_by_node(graphs):
    """{node id: {sector ids where it has an edge}} and {node id: {sector ids holding it}}."""
    linked, held = {}, {}
    for sid, _b, _m, pts, pairs, _o in graphs:
        for p in pts:
            held.setdefault(p[0], set()).add(sid)
        for a, b, _d in pairs:
            for q in (a, b):
                linked.setdefault(pts[q][0], set()).add(sid)
    return linked, held


def seam_linked(graphs, s1, s2):
    """Border nodes the two sectors share, and how many of them have an edge on both sides."""
    linked, held = edges_by_node(graphs)
    shared = [n for n, sids in held.items() if n & 0x80000000 and {s1, s2} <= sids]
    return len(shared), sum(1 for n in shared if {s1, s2} <= linked.get(n, set()))


def no_ground():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'pathdata.hpd')
        with open(out, 'wb') as f:
            f.write(b'previous build')
        with Runs({'s.bin': flat(0.8, 0.0, 0.0, 10, 10, ground=lambda x, z: False)}, keep_writer=True):
            try:
                navbuild.build('s.bin', None, 0, 0, out, log=QUIET)
                check(False, 'no walkable ground: a BuildError')
            except BuildError as e:
                check('no walkable ground' in str(e), f'no walkable ground: a BuildError ({e})')
            except Exception as e:                    # noqa: BLE001 - the old builder: ValueError from min()
                check(False, f'no walkable ground: a BuildError, not {type(e).__name__}: {e}')
        with open(out, 'rb') as f:
            check(f.read() == b'previous build', '... and the previous pathdata.hpd is left as it was')


def written_whole():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'pathdata.hpd')
        with Runs({'s.bin': flat(0.8, 0.0, 0.0, 30, 30)}, keep_writer=True):
            navbuild.build('s.bin', None, 0, 0, out, log=QUIET)
        import hpdtool
        with open(out, 'rb') as f:
            data = f.read()
        check(hpdtool.write(hpdtool.parse(data)) == data and not os.path.exists(out + '.tmp'),
              'a build is written whole and reads back, no temporary file left')
        # a new file that does not read back (made to fail here) does not replace the previous one
        parse = hpdtool.parse
        hpdtool.parse = lambda d: (_ for _ in ()).throw(ValueError('made to fail'))
        try:
            with Runs({'s.bin': flat(0.8, 0.0, 0.0, 20, 20)}, keep_writer=True):
                navbuild.build('s.bin', None, 0, 0, out, log=QUIET)
            check(False, 'a file that does not read back is an error')
        except Exception as e:                        # noqa: BLE001
            check(isinstance(e, BuildError), f'a file that does not read back is an error ({type(e).__name__}: {e})')
        finally:
            hpdtool.parse = parse
        with open(out, 'rb') as f:
            check(f.read() == data and not os.path.exists(out + '.tmp'),
                  '... and the previous file is kept, no temporary file left')


def detailed_fallback():
    # flat ground x 0..299.2, z 0..99.2: sectors (0,0), (1,0), (2,0). Detailed: (1,0) and (2,0); the 0.2 m run has
    # ground only in (2,0), along its seam with (1,0) (x 200-210, z 40-60): (1,0) stays at 0.8 m
    s08 = flat(0.8, 0.0, 0.0, 375, 125)
    s02 = flat(0.2, 100.0, 0.0, 1001, 501, ground=lambda x, z: 199.9 <= x <= 210.0 and 40.0 <= z <= 60.0)
    with Runs({'a.bin': s08, 'b.bin': s02}) as r:
        navbuild.build('a.bin', None, 0, 0, 'unused', log=QUIET)
        plain = r.graphs
        navbuild.build('a.bin', None, 0, 0, 'unused', log=QUIET,
                       detailed={'samples': 'b.bin', 'high': None, 'sectors': {(1, 0), (2, 0)}})
        merged = r.graphs
    n0, l0 = seam_linked(plain, 0x0000, 0x0100)
    n1, l1 = seam_linked(merged, 0x0000, 0x0100)
    check(n0 > 0 and l1 == n1 == n0 == l0, f'a detailed sector without 0.2 m ground keeps its links to its normal '
                                           f'neighbour ({l1} of {n1} seam nodes linked; without the pass {l0} of {n0})')
    n2, l2 = seam_linked(merged, 0x0100, 0x0200)
    check(l2 > 0, f'... and to the detailed sector beside it ({l2} of {n2} shared seam nodes linked)')
    pitches = {g[0]: round(g[2].pitch, 2) for g in merged}
    check(pitches.get(0x0100) == 0.8 and pitches.get(0x0200) == 0.2, f'sector pitches {pitches}')


def ladder_self_edge():
    # a 1 m ladder on flat ground: its foot band and its top band both hold the ground surface
    lad = {'lo': [40.0, 0.0, 40.0], 'hi': [40.5, 1.0, 40.5]}
    with Runs({'s.bin': flat(0.8, 0.0, 0.0, 100, 100)}) as r:
        navbuild.build('s.bin', None, 0, 0, 'unused', log=QUIET, ladders=[lad])
    selfs = sum(1 for g in r.graphs for a, b, _d in g[4] if a == b)
    check(selfs == 0, f'a ladder whose foot and top are one surface adds no self edge ({selfs})')


def grid_mismatch():
    main = flat(0.8, 0.0, 0.0, 40, 40)
    other = flat(0.8, 0.0, 0.0, 20, 20)
    with Runs({'s.bin': main, 'hi.bin': other}):
        try:
            navbuild.build('s.bin', None, 0, 0, 'unused', high_path='hi.bin', log=QUIET)
            check(False, 'a high-capsule run on another grid is an error')
        except BuildError as e:
            check('high-capsule run' in str(e), f'a high-capsule run on another grid is an error ({e})')
        except Exception as e:                        # noqa: BLE001 - the old builder: IndexError or garbage
            check(False, f'a high-capsule run on another grid is a BuildError, not {type(e).__name__}: {e}')
    fine = flat(0.4, 0.0, 0.0, 79, 79)
    with Runs({'s.bin': main, 'hi.bin': fine}) as r:
        navbuild.build('s.bin', None, 0, 0, 'unused', high_path='hi.bin', log=QUIET)
        check(r.graphs is not None, 'a finer high-capsule run on the same box is coarsened to the main grid')
    navbuild.set_pitch(0.2)
    try:
        g = navwalk.coarse(flat(0.1, 0.0, 0.0, 9, 9), 0.2)
        check(abs(g.pitch - 0.2) < 1e-9 and g.nx == 5, f'coarse() to 0.2 m in the detailed pass (pitch {g.pitch})')
    finally:
        navbuild.set_pitch(0.8)


def roof_last_column():
    # terrain at y 0 in columns (0..7, 0..1); the last sampled column (7, 1) also holds a roof at y 5 (rays record
    # surfaces top-down: roof, then ground); row 2 is empty (an interrupted run's unsampled columns)
    nx, nz, p = 8, 3, 0.8
    ys, hit, start = [], [], [0]
    for j in range(nz):
        for i in range(nx):
            if j < 2:
                if (i, j) == (7, 1):
                    ys.append(5.0), hit.append(0x1483B38)
                ys.append(0.0), hit.append(navwalk.TERRAIN_SHAPE_VTABLE)
            start.append(len(ys))
    n = len(ys)
    s = SimpleNamespace(nx=nx, nz=nz, pitch=p, min_x=10.0, min_z=10.0, start=np.array(start),
                        y=np.array(ys, np.float32), flags=np.zeros(n, int),
                        hit=np.array([(h, 0, 0) for h in hit], np.int64),
                        normal=np.tile(np.array([0.0, 1.0, 0.0], np.float32), (n, 1)))
    seeds = [(10.0, 0.0, 10.0), (10.0 + 7 * p, 5.0, 10.0 + p)]
    keep = navwalk.walkable(s, step=0.28, stairs=False, extra_seeds=seeds, anchors=[(10.0, 0.0, 10.0)])
    roof = next(k for k in range(s.start[15], s.start[16]) if s.y[k] > 1)
    check(not keep[roof], 'a lone roof in the last sampled column is dropped (its ground below it counts)')


def order_ids_retry():
    # the detailed_fallback level with AI order points in the 0.2 m ground: the first detailed attempt (sectors (1,0)
    # and (2,0)) adds order nodes, the retry without (1,0) reuses its node ids - no id of the discarded attempt may
    # stay an order id (prune_order_nodes would take an ordinary node holding it for an order node)
    s08 = flat(0.8, 0.0, 0.0, 375, 125)
    s02 = flat(0.2, 100.0, 0.0, 1001, 501, ground=lambda x, z: (199.9 <= x <= 210.0 and 40.0 <= z <= 60.0)
               or (260.0 <= x <= 266.0 and 80.0 <= z <= 86.0))
    orders = [[x * 0.5, 0.0, z * 0.5] for x in range(400, 421, 3) for z in range(80, 121, 3)]
    attempts = []
    stage = navbuild.stage

    def recording(*a, **k):
        before = set(navbuild.ORDER_IDS)
        r = stage(*a, **k)
        if k.get('sector_filter') is not None:
            attempts.append(set(navbuild.ORDER_IDS) - before)
        return r
    navbuild.stage = recording
    try:
        with Runs({'a.bin': s08, 'b.bin': s02}):
            navbuild.build('a.bin', None, 0, 0, 'unused', log=QUIET, order_points=orders,
                           detailed={'samples': 'b.bin', 'high': None, 'sectors': {(1, 0), (2, 0)}})
    finally:
        navbuild.stage = stage
    stale = set().union(*attempts[:-1]) - attempts[-1] if len(attempts) > 1 else set()
    check(len(attempts) == 2 and bool(set().union(*attempts[:-1])),
          f'(the made-up level makes a detailed retry whose first attempt has order nodes: {len(attempts)} attempts)')
    check(not (stale & navbuild.ORDER_IDS), f'no order id of a discarded detailed attempt is kept '
                                            f'({sorted(stale & navbuild.ORDER_IDS)})')


def sector_line():
    # a run whose minimum is not a float32-exact number (-1598.4): the column at x = 0 is on the line between sectors
    # -1 and 0 and belongs to sector 0. Ground in x 0 .. 3.2 only: the build's sectors are (0, 0) alone, and
    # detailed.py counts that column's stacked floors in the same sector
    min_x = float(np.float32(-1598.4))
    nx, nz = 2004, 10
    s = flat(0.8, min_x, 0.0, nx, nz, ground=lambda x, z: x > -0.5)
    with Runs({'s.bin': s}) as r:
        navbuild.build('s.bin', None, 0, 0, 'unused', log=QUIET)
    sids = sorted(g[0] for g in (r.graphs or ()))
    check(sids == [0x0000], f'a column on a sector line belongs to the sector it starts ({[f"{x:04x}" for x in sids]})')
    # two floors 3 m apart in the same columns: stacked floors, counted in sector (0, 0) too
    counts = np.where(np.arange(nx * nz) % nx >= 1998, 2, 0)
    start = np.zeros(nx * nz + 1, np.int64)
    np.cumsum(counts, out=start[1:])
    n = int(start[-1])
    two = navsample.Samples(0.8, min_x, 0.0, nx, nz, 50.0, -50.0, 0.4, 0.6, 1.8, 0.0, start,
                            np.tile(np.array([3.0, 0.0], np.float32), n // 2), np.tile(np.array([0, 1, 0], np.float32), (n, 1)),
                            np.zeros(n, np.uint8), None)
    import detailed
    with Runs({'two.bin': two}):
        c = detailed.stacked_columns('two.bin', 0, 0)
    check(set(c) == {(0, 0)}, f'detailed.py counts the line column in the same sector ({dict(c)})')


if __name__ == '__main__':
    order_ids_retry()
    sector_line()
    no_ground()
    written_whole()
    detailed_fallback()
    ladder_self_edge()
    grid_mismatch()
    roof_last_column()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
