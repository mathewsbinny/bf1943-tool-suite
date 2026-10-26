"""Heading-aware local planning for vehicles over an RSNV vehicle world (a lattice hybrid A*): the Python reference of
rsnav's rsn_hybrid_plan (rsnav_vehicle.c), which must give the same plan, cost and expansion count.

    T = table('car')                                    # motion primitives (also emitted as rsnav's C table)
    r = plan(w, T, start, goal, corridor)               # start / goal: (I, J, heading); corridor: [(x, z)]
    r['status'], r['cost'], r['poses'], r['expansions'] # poses: [(I, J, heading, dir, y)]

The state is a lattice cell (I, J) at the table's pitch (cell centres at I x pitch, J x pitch: the vehicle worlds'
own cells), one of H headings (yaw = 2 pi h / H, atan2(dx, dz) as rsnav's) and the direction of the last move
(+1 forward, -1 reverse). Moves are the table's primitives: straight on, or an arc of the class's turning radius that
turns one heading, forward or (at a cost factor) backward; changing direction costs `switch`. A move is allowed when
every cell it sweeps (its centre line every quarter cell, and points `reach` ahead of and behind the centre along the
heading there, the body's length beyond its clearance disc; out of the start state the centre line only) is a walkable cell of the world whose ground lies within
DY of the start state's, and its end lies within the corridor (a polyline, normally the plain path's corners).

Search (identical in C): f = g + floor(16 x pitch x |dI, dJ| x weight / 256), the open list ordered by (f, push
sequence); a closed state is never reopened; a state is the goal when its cell lies within `tol` cells of the goal
and its heading within `htol` headings; at the expansion cap the plan ends at the closed state nearest the goal
(least heuristic, then the earliest). Costs are integers in 1/16 m. All geometry in double precision in a fixed order
of operations, as rsnav.
"""
import heapq
import math

TWO_PI = 6.283185307179586
BUCKET_NUDGE = 1e-3          # m: the buckets are looked up this far inside a sector (a cell on a sector edge)
DY = 1.2                      # m: a swept cell's ground within this of the move's start state
# per kind: turning radius (m), lattice pitch (m), headings, body reach ahead / behind the disc (m), reverse cost
# factor (x/256), direction switch cost (1/16 m), whether arcs are allowed backward, pivot turns (tracked)
KINDS = {
    'car': {'radius': 4.5, 'pitch': 0.8, 'headings': 16, 'reach': 0.6, 'reverse': 512, 'switch': 48, 'pivot': 0},
    'boat': {'radius': 8.0, 'pitch': 2.0, 'headings': 16, 'reach': 2.2, 'reverse': 768, 'switch': 160, 'pivot': 0},
    'tracked': {'radius': 0.0, 'pitch': 0.8, 'headings': 16, 'reach': 1.2, 'reverse': 384, 'switch': 32, 'pivot': 24},
}
KIND_IDS = {'car': 0, 'tracked': 1, 'boat': 2}      # rsnav's RSN_DRIVE_* numbering


def _sweep(theta0, d, s, R, length, pitch, reach, n_sub):
    """Points (x, z) in cells swept by a move: centre line samples and the reach points ahead / behind."""
    pts = []
    for k in range(1, n_sub + 1):
        t = length * k / n_sub
        if s == 0 or R == 0:
            th = theta0
            x, z = d * t * math.sin(theta0), d * t * math.cos(theta0)
        else:
            th = theta0 + d * s * t / R
            # integral of d * (sin, cos)(theta0 + d s u / R) du from 0 to t
            x = d * (R / (d * s)) * (math.cos(theta0) - math.cos(th))
            z = d * (R / (d * s)) * (math.sin(th) - math.sin(theta0))
        for off in (0.0, reach, -reach):
            pts.append(((x + off * math.sin(th)) / pitch, (z + off * math.cos(th)) / pitch))
    return pts


def table(kind):
    """The motion primitives of a kind: {'kind', 'pitch', 'headings', 'prims': [per heading [(dI, dJ, h_end, dir, cost,
    [(ci, cj)], n_centre)]], ...}: the swept cells, the centre line's (with the end) first. Deterministic: rsnav's C table is generated from it (tools/gen_hybrid_tables.py)."""
    k = KINDS[kind]
    H, p, R = k['headings'], k['pitch'], k['radius']
    arc = (R if R else 2.0 * p) * TWO_PI / H           # one heading's turn (or a straight step for pivoting kinds)
    step = arc
    prims = []
    for h in range(H):
        th = TWO_PI * h / H
        lst = []
        moves = [(1, 0), (1, 1), (1, -1), (-1, 0), (-1, 1), (-1, -1)] if R else [(1, 0), (-1, 0)]
        for d, s in moves:
            L = arc if s else step
            pts = _sweep(th, d, s, R, L, p, k['reach'], max(4, int(math.ceil(L / p * 4))))
            ex, ez = pts[-3]                            # the centre line's end
            dI, dJ = int(round(ex)), int(round(ez))
            he = (h + d * s) % H
            # the centre line's cells first (and the end), then the body's reach ahead and behind
            cells = []
            for x, z in pts[0::3]:
                c = (int(round(x)), int(round(z)))
                if c != (0, 0) and c not in cells:
                    cells.append(c)
            if (dI, dJ) not in cells:
                cells.append((dI, dJ))
            n_centre = len(cells)
            for x, z in [q for k, q in enumerate(pts) if k % 3]:
                c = (int(round(x)), int(round(z)))
                if c != (0, 0) and c not in cells:
                    cells.append(c)
            cost = int(round(L * 16))
            if d < 0:
                cost = cost * k['reverse'] >> 8
            lst.append((dI, dJ, he, d, cost, cells, n_centre))
        if k['pivot']:
            for s in (1, -1):                           # turn in place one heading
                cells = []
                for off in (k['reach'], -k['reach']):
                    for a in (th + s * TWO_PI / H / 2, th + s * TWO_PI / H):
                        c = (int(round(off * math.sin(a) / p)), int(round(off * math.cos(a) / p)))
                        if c != (0, 0) and c not in cells:
                            cells.append(c)
                lst.append((0, 0, (h + s) % H, 1, k['pivot'], cells, 0))
        prims.append(lst)
    return {'kind': kind, 'pitch': p, 'headings': H, 'prims': prims, 'switch': k['switch'], 'radius': R}


def heading_of(yaw, H=16):
    return int(math.floor(yaw / TWO_PI * H + 0.5)) % H


def cell_ground(w, x, z, yref, dy):
    """The ground of the walkable cell at (x, z) nearest yref within dy (the lowest area index on a tie), or None."""
    best = None
    for k in w.areas_near(x + BUCKET_NUDGE, z + BUCKET_NUDGE):
        a = w.areas[k]
        i = math.floor((x - a.x0) / a.pitch + 0.5)
        j = math.floor((z - a.z0) / a.pitch + 0.5)
        if not w.walkable(a, i, j):
            continue
        g = w.ground(a, x, z)
        d = abs(g - yref)
        if d <= dy and (best is None or d < best[0] or (d == best[0] and k < best[1])):
            best = (d, k, g)
    return None if best is None else best[2]


def corridor_d2(x, z, corridor):
    """Squared distance from (x, z) to the polyline corridor [(x, z)] (doubles)."""
    best = None
    if len(corridor) == 1:
        ax, az = corridor[0]
        return (x - ax) * (x - ax) + (z - az) * (z - az)
    for k in range(len(corridor) - 1):
        ax, az = corridor[k]
        bx, bz = corridor[k + 1]
        vx, vz = bx - ax, bz - az
        l2 = vx * vx + vz * vz
        t = 0.0 if l2 <= 0.0 else ((x - ax) * vx + (z - az) * vz) / l2
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        qx, qz = x - (ax + t * vx), z - (az + t * vz)
        d = qx * qx + qz * qz
        if best is None or d < best:
            best = d
    return best


# start cells tried in turn: the vehicle's own, then those within 2 cells, nearest first (then by dj, di)
SNAP = sorted(((di, dj) for dj in range(-2, 3) for di in range(-2, 3) if di * di + dj * dj <= 4),
              key=lambda o: (o[0] * o[0] + o[1] * o[1], o[1], o[0]))
DEFAULTS = {'max_expansions': 6000, 'corridor': 10.0, 'tol': 2, 'htol': 1, 'weight': 320, 'start_dy': 1.5}


def plan(w, T, start, goal, corridor, start_y, params=None):
    """start, goal: (I, J, heading); start_y: the vehicle's ground height; corridor: [(x, z)] (pass float32 values for
    parity with rsnav). Returns {'status': 'done' | 'partial' | 'none', 'cost', 'poses', 'expansions'}."""
    P = dict(DEFAULTS, **(params or {}))
    p, H = T['pitch'], T['headings']
    prims = T['prims']
    corr2 = P['corridor'] * P['corridor']
    wf = P['weight'] / 256.0
    gI, gJ, gh = goal

    def heur(I, J):
        dI, dJ = I - gI, J - gJ
        d = math.sqrt(float(dI * dI + dJ * dJ))
        v = (16.0 * p) * d
        return math.floor(v * wf)

    I0, J0, h0 = start
    y0 = None
    for di, dj in SNAP:                                 # the vehicle may stand a little off its cells
        y0 = cell_ground(w, (I0 + di) * p, (J0 + dj) * p, start_y, P['start_dy'])
        if y0 is not None:
            I0, J0 = I0 + di, J0 + dj
            break
    if y0 is None:
        return {'status': 'none', 'cost': None, 'poses': [], 'expansions': 0}
    S = {'I': [I0], 'J': [J0], 'h': [h0], 'd': [1], 'y': [y0], 'g': [0], 'parent': [-1], 'closed': [False], 'hv': [heur(I0, J0)]}
    index = {(I0, J0, h0, 1): 0}
    heap = [(S['hv'][0], 0, 0)]
    seq = 1
    exp = 0
    best = None

    def poses(s):
        out = []
        while s != -1:
            out.append((S['I'][s], S['J'][s], S['h'][s], S['d'][s], S['y'][s]))
            s = S['parent'][s]
        return out[::-1]
    while heap:
        f, _q, s = heapq.heappop(heap)
        if S['closed'][s]:
            continue
        S['closed'][s] = True
        I, J, h = S['I'][s], S['J'][s], S['h'][s]
        dh = abs(h - gh) % H
        dh = min(dh, H - dh)
        if (I - gI) * (I - gI) + (J - gJ) * (J - gJ) <= P['tol'] * P['tol'] and dh <= P['htol']:
            return {'status': 'done', 'cost': S['g'][s], 'poses': poses(s), 'expansions': exp}
        if exp >= P['max_expansions']:
            break
        exp += 1
        if best is None or (S['hv'][s], s) < best:
            best = (S['hv'][s], s)
        y = S['y'][s]
        for dI, dJ, he, d, cost, cells, n_centre in prims[h]:
            I2, J2 = I + dI, J + dJ
            if corridor_d2(I2 * p, J2 * p, corridor) > corr2:
                continue
            ok = True
            # out of the start state only the centre line: the vehicle may have squeezed where its body test fails,
            # and must be able to drive out
            for ci, cj in (cells[:n_centre] if s == 0 else cells):
                if cell_ground(w, (I + ci) * p, (J + cj) * p, y, DY) is None:
                    ok = False
                    break
            if not ok:
                continue
            y2 = cell_ground(w, I2 * p, J2 * p, y, DY)
            g2 = S['g'][s] + cost + (T['switch'] if d != S['d'][s] else 0)
            key = (I2, J2, he, d)
            t = index.get(key)
            if t is not None and (S['closed'][t] or g2 >= S['g'][t]):
                continue
            if t is None:
                t = len(S['I'])
                index[key] = t
                for name, v in (('I', I2), ('J', J2), ('h', he), ('d', d), ('y', y2), ('g', g2), ('parent', s),
                                ('closed', False), ('hv', heur(I2, J2))):
                    S[name].append(v)
            else:
                S['g'][t], S['parent'][t], S['y'][t] = g2, s, y2
            heapq.heappush(heap, (g2 + S['hv'][t], seq, t))
            seq += 1
    if best is None:
        return {'status': 'none', 'cost': None, 'poses': [], 'expansions': exp}
    return {'status': 'partial', 'cost': S['g'][best[1]], 'poses': poses(best[1]), 'expansions': exp}
