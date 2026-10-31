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
heading there, the body's length beyond its clearance disc; out of the start state the centre line only) is a
walkable cell of the world whose ground lies within DY of the start state's, and its end lies within the corridor (a
polyline, normally the plain path's corners).

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


def _sweep(theta0, direction, turn, R, length, pitch, reach, n_sub):
    """Points (x, z) in cells swept by a move: centre line samples and the reach points ahead / behind. direction is +1
    forward or -1 reverse, turn +1, -1 or 0 (straight); R the turning radius; x and z in cells of the pitch."""
    points = []
    for k in range(1, n_sub + 1):
        t = length * k / n_sub
        if turn == 0 or R == 0:
            theta = theta0
            x, z = direction * t * math.sin(theta0), direction * t * math.cos(theta0)
        else:
            theta = theta0 + direction * turn * t / R
            # integral of d * (sin, cos)(theta0 + d s u / R) du from 0 to t (d the direction, s the turn)
            x = direction * (R / (direction * turn)) * (math.cos(theta0) - math.cos(theta))
            z = direction * (R / (direction * turn)) * (math.sin(theta) - math.sin(theta0))
        for offset in (0.0, reach, -reach):
            points.append(((x + offset * math.sin(theta)) / pitch, (z + offset * math.cos(theta)) / pitch))
    return points


def table(kind):
    """The motion primitives of a kind: {'kind', 'pitch', 'headings', 'prims': [per heading [(dI, dJ, h_end, dir, cost,
    [(ci, cj)], n_centre)]], ...}: the swept cells, the centre line's (with the end) first. Deterministic: rsnav's C
    table is generated from it (tools/gen_hybrid_tables.py)."""
    spec = KINDS[kind]
    H, pitch, R = spec['headings'], spec['pitch'], spec['radius']
    arc = (R if R else 2.0 * pitch) * TWO_PI / H       # one heading's turn (or a straight step for pivoting kinds)
    step = arc
    prims = []
    for h in range(H):
        theta = TWO_PI * h / H
        heading_prims = []
        if R:
            moves = [(1, 0), (1, 1), (1, -1), (-1, 0), (-1, 1), (-1, -1)]        # (direction, turn)
        else:
            moves = [(1, 0), (-1, 0)]
        for direction, turn in moves:
            length = arc if turn else step
            n_sub = max(4, int(math.ceil(length / pitch * 4)))
            points = _sweep(theta, direction, turn, R, length, pitch, spec['reach'], n_sub)
            end_x, end_z = points[-3]                   # the centre line's end
            dI, dJ = int(round(end_x)), int(round(end_z))
            h_end = (h + direction * turn) % H
            # the centre line's cells first (and the end), then the body's reach ahead and behind
            cells = []
            for x, z in points[0::3]:
                cell = (int(round(x)), int(round(z)))
                if cell != (0, 0) and cell not in cells:
                    cells.append(cell)
            if (dI, dJ) not in cells:
                cells.append((dI, dJ))
            n_centre = len(cells)
            for x, z in [point for k, point in enumerate(points) if k % 3]:
                cell = (int(round(x)), int(round(z)))
                if cell != (0, 0) and cell not in cells:
                    cells.append(cell)
            cost = int(round(length * 16))
            if direction < 0:
                cost = cost * spec['reverse'] >> 8
            heading_prims.append((dI, dJ, h_end, direction, cost, cells, n_centre))
        if spec['pivot']:
            for turn in (1, -1):                        # turn in place one heading
                cells = []
                for offset in (spec['reach'], -spec['reach']):
                    for angle in (theta + turn * TWO_PI / H / 2, theta + turn * TWO_PI / H):
                        cell = (int(round(offset * math.sin(angle) / pitch)),
                                int(round(offset * math.cos(angle) / pitch)))
                        if cell != (0, 0) and cell not in cells:
                            cells.append(cell)
                heading_prims.append((0, 0, (h + turn) % H, 1, spec['pivot'], cells, 0))
        prims.append(heading_prims)
    return {'kind': kind, 'pitch': pitch, 'headings': H, 'prims': prims, 'switch': spec['switch'], 'radius': R}


def heading_of(yaw, H=16):
    return int(math.floor(yaw / TWO_PI * H + 0.5)) % H


def cell_ground(w, x, z, yref, dy):
    """The ground of the walkable cell at (x, z) nearest yref within dy (the lowest area index on a tie), or None."""
    best = None                                         # (height difference, area index, ground)
    for area_index in w.areas_near(x + BUCKET_NUDGE, z + BUCKET_NUDGE):
        area = w.areas[area_index]
        i = math.floor((x - area.x0) / area.pitch + 0.5)
        j = math.floor((z - area.z0) / area.pitch + 0.5)
        if not w.walkable(area, i, j):
            continue
        ground = w.ground(area, x, z)
        diff = abs(ground - yref)
        if diff <= dy and (best is None or diff < best[0] or (diff == best[0] and area_index < best[1])):
            best = (diff, area_index, ground)
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
        d2 = qx * qx + qz * qz
        if best is None or d2 < best:
            best = d2
    return best


# start cells tried in turn: the vehicle's own, then those within 2 cells, nearest first (then by dj, di)
SNAP = sorted(((di, dj) for dj in range(-2, 3) for di in range(-2, 3) if di * di + dj * dj <= 4),
              key=lambda o: (o[0] * o[0] + o[1] * o[1], o[1], o[0]))
DEFAULTS = {'max_expansions': 6000, 'corridor': 10.0, 'tol': 2, 'htol': 1, 'weight': 320, 'start_dy': 1.5}


def plan(w, T, start, goal, corridor, start_y, params=None):
    """start, goal: (I, J, heading); start_y: the vehicle's ground height; corridor: [(x, z)] (pass float32 values for
    parity with rsnav). Returns {'status': 'done' | 'partial' | 'none', 'cost', 'poses', 'expansions'}."""
    opts = dict(DEFAULTS, **(params or {}))
    pitch, H = T['pitch'], T['headings']
    prims = T['prims']
    corridor_max2 = opts['corridor'] * opts['corridor']
    weight = opts['weight'] / 256.0
    goal_I, goal_J, goal_h = goal

    def heuristic(I, J):
        dI, dJ = I - goal_I, J - goal_J
        dist = math.sqrt(float(dI * dI + dJ * dJ))
        v = (16.0 * pitch) * dist
        return math.floor(v * weight)

    I0, J0, h0 = start
    y0 = None
    for di, dj in SNAP:                                 # the vehicle may stand a little off its cells
        y0 = cell_ground(w, (I0 + di) * pitch, (J0 + dj) * pitch, start_y, opts['start_dy'])
        if y0 is not None:
            I0, J0 = I0 + di, J0 + dj
            break
    if y0 is None:
        return {'status': 'none', 'cost': None, 'poses': [], 'expansions': 0}
    # the states, one list per field (index = state number): cell, heading, direction of the last move, ground,
    # cost so far, the state it was reached from, closed, heuristic
    states = {'I': [I0], 'J': [J0], 'h': [h0], 'd': [1], 'y': [y0], 'g': [0], 'parent': [-1], 'closed': [False],
              'hv': [heuristic(I0, J0)]}
    index = {(I0, J0, h0, 1): 0}
    heap = [(states['hv'][0], 0, 0)]                    # (f, push sequence, state)
    seq = 1
    expansions = 0
    nearest = None                                      # (heuristic, state) of the closed state nearest the goal

    def poses(state):
        out = []
        while state != -1:
            out.append((states['I'][state], states['J'][state], states['h'][state], states['d'][state],
                        states['y'][state]))
            state = states['parent'][state]
        return out[::-1]
    while heap:
        _f, _seq, state = heapq.heappop(heap)
        if states['closed'][state]:
            continue
        states['closed'][state] = True
        I, J, h = states['I'][state], states['J'][state], states['h'][state]
        dh = abs(h - goal_h) % H
        dh = min(dh, H - dh)
        near_goal = (I - goal_I) * (I - goal_I) + (J - goal_J) * (J - goal_J) <= opts['tol'] * opts['tol']
        if near_goal and dh <= opts['htol']:
            return {'status': 'done', 'cost': states['g'][state], 'poses': poses(state), 'expansions': expansions}
        if expansions >= opts['max_expansions']:
            break
        expansions += 1
        if nearest is None or (states['hv'][state], state) < nearest:
            nearest = (states['hv'][state], state)
        y = states['y'][state]
        for dI, dJ, h_end, direction, cost, cells, n_centre in prims[h]:
            I2, J2 = I + dI, J + dJ
            if corridor_d2(I2 * pitch, J2 * pitch, corridor) > corridor_max2:
                continue
            clear = True
            # out of the start state only the centre line: the vehicle may have squeezed where its body test fails,
            # and must be able to drive out
            for ci, cj in (cells[:n_centre] if state == 0 else cells):
                if cell_ground(w, (I + ci) * pitch, (J + cj) * pitch, y, DY) is None:
                    clear = False
                    break
            if not clear:
                continue
            y2 = cell_ground(w, I2 * pitch, J2 * pitch, y, DY)
            g2 = states['g'][state] + cost + (T['switch'] if direction != states['d'][state] else 0)
            key = (I2, J2, h_end, direction)
            known = index.get(key)
            if known is not None and (states['closed'][known] or g2 >= states['g'][known]):
                continue
            if known is None:
                known = len(states['I'])
                index[key] = known
                for name, value in (('I', I2), ('J', J2), ('h', h_end), ('d', direction), ('y', y2), ('g', g2),
                                    ('parent', state), ('closed', False), ('hv', heuristic(I2, J2))):
                    states[name].append(value)
            else:
                states['g'][known], states['parent'][known], states['y'][known] = g2, state, y2
            heapq.heappush(heap, (g2 + states['hv'][known], seq, known))
            seq += 1
    if nearest is None:
        return {'status': 'none', 'cost': None, 'poses': [], 'expansions': expansions}
    return {'status': 'partial', 'cost': states['g'][nearest[1]], 'poses': poses(nearest[1]), 'expansions': expansions}
