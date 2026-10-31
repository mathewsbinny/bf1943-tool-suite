r"""NavKit (big-endian): jump links on BF1943's ships - a soldier drops from a deck onto a floor a ledge below it and
jumps back up. No step joins such floors (they lie more than the 0.37 m step and at most JUMP_HEIGHT 0.95 m apart), so
without a link they were out of the network. Two rules make links, on ships only, found from the level's own data
(no blueprint names):

- AA gun platforms (platform_links): AA gun spawns (the spawn map's vehicle aa_bofors) whose floor is collision, not
  terrain, over water deeper than the soldier's WATER_DEPTH. The platform is the clear, flat floor joined to the spawn's
  floor by the step rule; the link joins the platform surface and the deck surface nearest each other horizontally, the
  deck between STEP and JUMP_HEIGHT above the platform and at most JUMP_GAP away (0.58-0.84 m under the deck: Wake
  Island 4 links, Iwo Jima 4, Coral Sea 8, Guadalcanal none).
- Ship ledges (ship_ledge_links, SHIP_LEDGES, on since 2026-10-26): on each ship (an object box of 5,000 m2 or more
  over deep water), the floor pieces a ledge apart from the deck or from a piece already reached - the deck-edge
  galleries 0.58 m and the gun tubs 0.84 m under the decks: Wake Island 21 links, Iwo Jima 21, Coral Sea 41.

Each link is written in the ladders.json format (ladder_ends and ladder_end_options find its ends) with kind 'jump'
and tight height bands, so navbuild seeds the far floor and links them both ways; the runtime reads kind to jump
instead of climbing. A third rule, jump links onto wide raised floors on land (RAISED_FLOORS), is kept but off.

  jumplinks1943.py <level> [--json out.json] [--game <dist\xenon folder>]
      (needs navkit.py prepare and sample; writes navkit/jump_links.json; navkit.py build runs it with its --game.
      The build stamps that file's SHA-256: written again with other links, the export and the audit refuse the build
      until it is built again; a --game other than the prepared copy is refused unless --json writes elsewhere)
"""
import argparse
import json
import math
import os
import sys
from collections import deque

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-spawn-map'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
import soldier1943  # noqa: E402

JUMP_HEIGHT = 0.95        # m: the soldier's JumpStateData.JumpHeight (objects/soldiers/common/defaultsoldierphysics.dbx);
                          # that he climbs out of a 0.84 m platform with it is not yet seen in game
JUMP_GAP = 2.0            # m: the most a link spans horizontally (a deck edge overhangs Wake's platforms by about 1 m)
END_BAND = 0.15           # m: a link's end surface lies within this of the platform's / deck's height
PLATFORM_RADIUS = 8.0     # m: the platform flood stays this close to the spawn
GUNS = {'aa_bofors'}
# raised floors (the user's choice, 2026-10-24: jump links onto wide raised floors, not onto every ledge). Off: Iwo Jima's
# boardwalk, the case it was for, is ForbidPathfinding in its blueprint (no floor for AI by the level's own data), and
# what is left (measured on the four maps: 2-34 links each) is mostly sandbag-wall tops, boulders and clutter, with a few
# real decks (Iwo Jima's boat houses, bunker roofs). Kept for a decision.
RAISED_FLOORS = False
FLOOR_MIN_AREA = 10.0     # m2: a raised floor this large or larger gets jump links (boardwalks, jetties, platforms; not
                          # crate tops or wall tops)
FLOOR_MAX = 60000         # surfaces: a floor larger than this is a deck reached in its own way (a carrier's)
LINK_REACH = 0.6          # m: a raised floor's edge and the ground below it lie at most this far apart horizontally
LINK_SPACING = 5.0        # m: links along one floor's edge at least this far apart
LINK_MAX = 16             # links per floor
# ships' ledges (2026-10-26, ship_ledge_links): jump links between a ship's floors a ledge apart
SHIP_LEDGES = True
SHIP_MIN_FOOTPRINT = 5000.0  # m2: an object box this large over deep water is a ship (the carriers: 11,500-30,100 m2)
SHIP_MIN_FREEBOARD = 2.0  # m: ... reaching this far above the sea
SHIP_MIN_AREA = 2.0       # m2: a floor piece this large or larger gets links (not a rail top or a bollard)
SHIP_REACH = 1.0          # m: the two ends of a ledge link lie at most this far apart horizontally (measured 0.4-0.6 m)
SHIP_CLEAR = 0.3          # m: nothing between the two ends higher than the upper floor plus this (a rail, a wall)
SHIP_LINKS = 4            # links per piece


def _clear(s, k, idx_pos, idx):
    """Clear (capsule), flat enough, and with the soldier's headroom."""
    if s.flags[k] & 1 or s.normal[k, 1] < math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01:
        return False
    q = idx_pos
    return q == 0 or s.y[idx[q - 1]] - s.y[k] >= soldier1943.HEIGHT


def _wide(s, i, j, y, radius=0.6, share=0.7):
    """Floor, not a rail or a ledge: most columns within radius have a clear surface at this height (a platform's
    rail top lies at the deck's height and is one or two samples wide)."""
    r = int(round(radius / s.pitch))
    n = hit = 0
    for b in range(j - r, j + r + 1):
        for a in range(i - r, i + r + 1):
            if (a - i) ** 2 + (b - j) ** 2 > r * r or not (0 <= a < s.nx and 0 <= b < s.nz):
                continue
            n += 1
            cc = b * s.nx + a
            ix = list(range(s.start[cc], s.start[cc + 1]))
            hit += any(abs(s.y[k] - y) <= 0.1 and _clear(s, k, q, ix) for q, k in enumerate(ix))
    return n and hit / n >= share


def platform_links(s, spawns, water_top, terrain, log=print):
    """Jump-link records for the gun spawns [(name, (x, y, z))] on samples s. water_top(c) and terrain(x, z) give the
    water surface over a column and the terrain height."""
    out = []
    for name, (x, y, z) in spawns:
        ci, cj = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        if not (0 <= ci < s.nx and 0 <= cj < s.nz):
            continue
        c = cj * s.nx + ci
        idx = list(range(s.start[c], s.start[c + 1]))
        below = [k for k in idx if s.y[k] <= y + 0.5]
        if not below:
            continue
        fk = max(below, key=lambda k: s.y[k])
        fy = float(s.y[fk])
        t = float(terrain(x, z))
        if abs(fy - t) < 0.06 or water_top(c) - t <= soldier1943.WATER_DEPTH:
            continue                                     # on terrain, or not over deep water: not a ship's gun
        # the platform: clear floor joined by the step rule, starting from clear floor nearest the spawn
        n = int(PLATFORM_RADIUS / s.pitch)
        seeds = []
        for j in range(max(cj - 6, 0), min(cj + 7, s.nz)):
            for i in range(max(ci - 6, 0), min(ci + 7, s.nx)):
                cc = j * s.nx + i
                ix = list(range(s.start[cc], s.start[cc + 1]))
                for q, k in enumerate(ix):
                    if abs(s.y[k] - fy) <= END_BAND and _clear(s, k, q, ix):
                        seeds.append((math.hypot(i - ci, j - cj), k, i, j))
        if not seeds:
            log(f'  {name}: no clear floor beside the gun at {fy:.2f}')
            continue
        _, k0, i0, j0 = min(seeds)
        seen = {k0: (i0, j0)}
        q = deque([k0])
        while q:
            k = q.popleft()
            i, j = seen[k]
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    a, b = i + di, j + dj
                    if not (di or dj) or abs(a - ci) > n or abs(b - cj) > n or not (0 <= a < s.nx and 0 <= b < s.nz):
                        continue
                    cc = b * s.nx + a
                    ix = list(range(s.start[cc], s.start[cc + 1]))
                    for qq, m in enumerate(ix):
                        if m not in seen and abs(s.y[m] - s.y[k]) <= soldier1943.STEP and _clear(s, m, qq, ix):
                            seen[m] = (a, b)
                            q.append(m)
        plat_y = np.array([s.y[k] for k in seen])
        P = np.array([[s.min_x + i * s.pitch, s.min_z + j * s.pitch] for i, j in seen.values()])
        # the deck: clear floor STEP..JUMP_HEIGHT above the platform within JUMP_GAP of it
        g = int(math.ceil(JUMP_GAP / s.pitch))
        ii = [i for i, _ in seen.values()]
        jj = [j for _, j in seen.values()]
        best = None
        top = float(plat_y.mean())
        for j in range(max(min(jj) - g, 0), min(max(jj) + g + 1, s.nz)):
            for i in range(max(min(ii) - g, 0), min(max(ii) + g + 1, s.nx)):
                cc = j * s.nx + i
                ix = list(range(s.start[cc], s.start[cc + 1]))
                for qq, m in enumerate(ix):
                    rise = s.y[m] - top
                    if not (soldier1943.STEP < rise <= JUMP_HEIGHT) or not _clear(s, m, qq, ix):
                        continue
                    px, pz = s.min_x + i * s.pitch, s.min_z + j * s.pitch
                    d = np.hypot(P[:, 0] - px, P[:, 1] - pz)
                    a = int(np.argmin(d))
                    if d[a] <= JUMP_GAP and (best is None or d[a] < best[0]) and _wide(s, i, j, float(s.y[m])):
                        best = (float(d[a]), (float(P[a, 0]), float(plat_y[a]), float(P[a, 1])), (px, float(s.y[m]), pz))
        if best is None:
            log(f'  {name}: platform of {len(seen)} cells at {top:.2f}, no deck within {JUMP_GAP} m and {JUMP_HEIGHT} m up')
            continue
        d, (ax, ay, az), (bx, by, bz) = best
        out.append({'bp': f'jump link: {name}', 'kind': 'jump', 'gun': [round(x, 2), round(y, 2), round(z, 2)],
                    'lo': [round(min(ax, bx), 3), round(ay, 3), round(min(az, bz), 3)],
                    'hi': [round(max(ax, bx), 3), round(by, 3), round(max(az, bz), 3)], 'box_lo_y': round(ay, 3),
                    'platform': [round(ax, 2), round(ay, 2), round(az, 2)], 'deck': [round(bx, 2), round(by, 2), round(bz, 2)],
                    'rise': round(by - ay, 2), 'gap': round(d, 2), 'platform_cells': len(seen),
                    'foot_band': [-END_BAND, END_BAND], 'top_band': [-END_BAND, END_BAND], 'reach': 0.5})
        log(f'  {name} at ({x:.1f}, {y:.1f}, {z:.1f}): platform {top:.2f} ({len(seen)} cells), deck {by:.2f} '
            f'(+{by - ay:.2f} m), gap {d:.2f} m')
    return out


def _record(name, ax, ay, az, bx, by, bz, d, cells, rule):
    return {'bp': f'jump link: {name}', 'kind': 'jump', 'rule': rule,
            'lo': [round(min(ax, bx), 3), round(ay, 3), round(min(az, bz), 3)],
            'hi': [round(max(ax, bx), 3), round(by, 3), round(max(az, bz), 3)], 'box_lo_y': round(ay, 3),
            'platform': [round(ax, 2), round(ay, 2), round(az, 2)], 'deck': [round(bx, 2), round(by, 2), round(bz, 2)],
            'rise': round(by - ay, 2), 'gap': round(d, 2), 'platform_cells': cells,
            'foot_band': [-END_BAND, END_BAND], 'top_band': [-END_BAND, END_BAND], 'reach': 0.5}


def raised_floor_links(s, terrain, boxes, log=print):
    """Jump links onto raised floors (the user's choice, 2026-10-24): wide, flat, clear floors on objects that the step
    rule joins to no terrain - Iwo Jima's beach boardwalk, its deck 0.55-1.05 m over the ground on both sides, left a
    strip of beach and the boardwalk itself out of the network. A floor is a piece of clear, flat object surfaces (not
    terrain) joined by the step rule, flooded from the object boxes; one that reaches terrain by steps, or is larger
    than FLOOR_MAX (a carrier's deck), or smaller than FLOOR_MIN_AREA (a crate's top), gets none. Along each other
    floor's edge, a link wherever clear, flat ground lies within LINK_REACH horizontally and more than STEP, at most
    JUMP_HEIGHT, below; at least LINK_SPACING apart, at most LINK_MAX per floor. s: samples; terrain(x, z) heights."""
    STEP = soldier1943.STEP
    nx, nz, p = s.nx, s.nz, s.pitch
    col_of = lambda k: int(np.searchsorted(s.start, k, side='right')) - 1
    tcache = {}

    def t_at(c):
        if c not in tcache:
            tcache[c] = float(terrain(s.min_x + (c % nx) * p, s.min_z + (c // nx) * p))
        return tcache[c]

    fcache = {}

    def flat_clear(c):
        """{surface: on terrain} of the column's clear, flat surfaces with headroom."""
        if c not in fcache:
            ix = list(range(s.start[c], s.start[c + 1]))
            fcache[c] = {k: abs(s.y[k] - t_at(c)) < 0.06 for q, k in enumerate(ix) if _clear(s, k, q, ix)}
        return fcache[c]
    seen = set()
    out = []
    r_link = max(1, int(round(LINK_REACH / p)))
    for b in boxes:
        lo, hi = b['min'], b['max']
        i0, i1 = max(int((lo[0] - s.min_x) / p), 0), min(int((hi[0] - s.min_x) / p) + 1, nx - 1)
        j0, j1 = max(int((lo[2] - s.min_z) / p), 0), min(int((hi[2] - s.min_z) / p) + 1, nz - 1)
        if i0 > i1 or j0 > j1:
            continue
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                c = j * nx + i
                for k0, on_t in flat_clear(c).items():
                    if on_t or k0 in seen or not (lo[1] - 0.5 <= s.y[k0] <= hi[1] + 0.5):
                        continue
                    # flood one floor
                    piece, q, touches_terrain = {k0}, deque([k0]), False
                    seen.add(k0)
                    while q and len(piece) <= FLOOR_MAX:
                        k = q.popleft()
                        ck = col_of(k)
                        ci, cj = ck % nx, ck // nx
                        for dj in (-1, 0, 1):
                            for di in (-1, 0, 1):
                                a, bb = ci + di, cj + dj
                                if not (di or dj) or not (0 <= a < nx and 0 <= bb < nz):
                                    continue
                                for n, n_t in flat_clear(bb * nx + a).items():
                                    if abs(s.y[n] - s.y[k]) > STEP:
                                        continue
                                    if n_t:
                                        touches_terrain = True
                                    elif n not in seen:
                                        seen.add(n)
                                        piece.add(n)
                                        q.append(n)
                    area = len(piece) * p * p
                    if touches_terrain or len(piece) > FLOOR_MAX or area < FLOOR_MIN_AREA:
                        continue
                    # links along its edge to ground below
                    cand = []
                    for k in piece:
                        ck = col_of(k)
                        ci, cj = ck % nx, ck // nx
                        for dj in range(-r_link, r_link + 1):
                            for di in range(-r_link, r_link + 1):
                                a, bb = ci + di, cj + dj
                                if not (di or dj) or not (0 <= a < nx and 0 <= bb < nz) or math.hypot(di, dj) * p > LINK_REACH:
                                    continue
                                for n in flat_clear(bb * nx + a):
                                    drop = float(s.y[k] - s.y[n])
                                    if n not in piece and STEP < drop <= JUMP_HEIGHT and _wide(s, a, bb, float(s.y[n]), 0.4, 0.5):
                                        cand.append((drop, math.hypot(di, dj) * p, k, n, ci, cj, a, bb))
                    cand.sort()
                    chosen = []
                    for drop, d, k, n, ci, cj, a, bb in cand:
                        fx, fz = s.min_x + ci * p, s.min_z + cj * p
                        if any(math.hypot(fx - cx, fz - cz) < LINK_SPACING for cx, cz in chosen):
                            continue
                        chosen.append((fx, fz))
                        gx, gz = s.min_x + a * p, s.min_z + bb * p
                        out.append(_record(f"raised floor ({b['bp'].split('/')[-1]})", gx, float(s.y[n]), gz, fx,
                                           float(s.y[k]), fz, d, len(piece), 'raised floor'))
                        if len(chosen) >= LINK_MAX:
                            break
                    if chosen:
                        log(f"  raised floor of {area:.0f} m2 on {b['bp'].split('/')[-1]} at ({s.min_x + i * p:.1f}, "
                            f"{s.y[k0]:.2f}, {s.min_z + j * p:.1f}): {len(chosen)} jump link(s)")
    return out


SHIP_REPORT = {}          # the last level_links run's ship report per grid ('detailed', 'normal'): ship_ledge_links


def ship_boxes(boxes, terrain, sea):
    """The ships among the object boxes, from the level's data alone: a box of SHIP_MIN_FOOTPRINT or more over water
    deeper than the soldier's WATER_DEPTH at its centre, reaching SHIP_MIN_FREEBOARD above the sea (the carriers of
    Wake Island, Iwo Jima and Coral Sea; not Guadalcanal's wrecks, 888-1,827 m2, nor the bridges)."""
    out = []
    for b in boxes:
        lo, hi = b['min'], b['max']
        if (hi[0] - lo[0]) * (hi[2] - lo[2]) < SHIP_MIN_FOOTPRINT or hi[1] < sea + SHIP_MIN_FREEBOARD:
            continue
        if float(terrain((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2)) < sea - soldier1943.WATER_DEPTH:
            out.append(b)
    return out


def ship_ledge_links(s, ships, terrain, usable=lambda x, z: True, taken=(), log=print):
    """SHIP_LEDGES: jump links between a ship's floors (2026-10-26). On a carrier the step rule leaves out the floors a
    ledge apart: the galleries along the flight deck's edges 0.4-0.6 m under it, and floor pieces of the island
    superstructure. A soldier drops off such a ledge and jumps back up (JUMP_HEIGHT), as from the AA gun platforms
    (platform_links). Per ship: its clear, flat floors on collision (not terrain) inside its box are pieces joined by
    the step rule; the largest is the deck. From the deck outward, a piece of SHIP_MIN_AREA or more whose edge lies
    within SHIP_REACH horizontally of a reached piece, more than STEP and at most JUMP_HEIGHT above or below it, wide
    floor at both ends (_wide), with nothing between the two ends higher than the upper floor plus SHIP_CLEAR (a rail,
    a wall), is reached and linked, at most SHIP_LINKS links per piece, LINK_SPACING apart. Pieces only a longer drop
    reaches (lower galleries 3-6 m under the deck) and pieces no jump reaches get none. usable(x, z): whether a link
    end may lie there (the sampled grid's own sectors); taken: link ends already made (the AA platforms), whose pieces
    are left out. Returns (records, report per ship)."""
    STEP = soldier1943.STEP
    nx, nz, p = s.nx, s.nz, s.pitch
    out, report = [], []
    for b in ships:
        lo, hi = b['min'], b['max']
        i0, i1 = max(int((lo[0] - s.min_x) / p), 0), min(int((hi[0] - s.min_x) / p) + 1, nx - 1)
        j0, j1 = max(int((lo[2] - s.min_z) / p), 0), min(int((hi[2] - s.min_z) / p) + 1, nz - 1)
        floor = {}                                  # surface -> (i, j): the clear, flat floor on the ship
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                c = j * nx + i
                ix = list(range(s.start[c], s.start[c + 1]))
                if not ix:
                    continue
                t = float(terrain(s.min_x + i * p, s.min_z + j * p))
                for q, k in enumerate(ix):
                    if lo[1] - 0.5 <= s.y[k] <= hi[1] + 0.5 and abs(s.y[k] - t) >= 0.06 and _clear(s, k, q, ix):
                        floor[k] = (i, j)
        if not floor:
            continue
        at = {}
        for k, ij in floor.items():
            at.setdefault(ij, []).append(k)
        piece_of, pieces = {}, []
        for k0 in floor:
            if k0 in piece_of:
                continue
            n_ = len(pieces)
            piece_of[k0] = n_
            members, q = [k0], deque([k0])
            while q:
                k = q.popleft()
                i, j = floor[k]
                for dj in (-1, 0, 1):
                    for di in (-1, 0, 1):
                        for m in at.get((i + di, j + dj), ()):
                            if m not in piece_of and abs(s.y[m] - s.y[k]) <= STEP:
                                piece_of[m] = n_
                                members.append(m)
                                q.append(m)
            pieces.append(members)
        deck = max(range(len(pieces)), key=lambda n_: len(pieces[n_]))
        skip = set()
        for x, y, z in taken:
            ij = (int(round((x - s.min_x) / p)), int(round((z - s.min_z) / p)))
            for k in at.get(ij, ()):
                if abs(s.y[k] - y) <= END_BAND:
                    skip.add(piece_of[k])
        # each piece's edge: its surfaces with a side column holding none of its surfaces
        edge = {}
        for n_, members in enumerate(pieces):
            mine = set(members)
            edge[n_] = [k for k in members
                        if any(not any(m in mine for m in at.get((floor[k][0] + di, floor[k][1] + dj), ()))
                               for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        r = int(math.ceil(SHIP_REACH / p))
        reached, frontier, linked = {deck}, [deck], {}
        while frontier:
            nxt = []
            for src in frontier:
                near = {}                           # column -> src's edge surfaces within SHIP_REACH of it
                for k in edge[src]:
                    i, j = floor[k]
                    for dj in range(-r, r + 1):
                        for di in range(-r, r + 1):
                            if math.hypot(di, dj) * p <= SHIP_REACH:
                                near.setdefault((i + di, j + dj), []).append(k)
                cand = {}
                for n_, e in edge.items():
                    if n_ in reached or n_ in skip or len(pieces[n_]) * p * p < SHIP_MIN_AREA:
                        continue
                    for m in e:
                        for k in near.get(floor[m], ()):
                            rise = float(s.y[k] - s.y[m])
                            if STEP < abs(rise) <= JUMP_HEIGHT:
                                (ia, ja), (ib, jb) = floor[m], floor[k]
                                cand.setdefault(n_, []).append((math.hypot(ia - ib, ja - jb) * p, abs(rise), m, k))
                for n_, cs in sorted(cand.items()):
                    if n_ in reached:
                        continue
                    cs.sort()
                    chosen = []
                    for d, _r, m, k in cs:
                        (ia, ja), (ib, jb) = floor[m], floor[k]
                        xa, za, xb, zb = s.min_x + ia * p, s.min_z + ja * p, s.min_x + ib * p, s.min_z + jb * p
                        if not (usable(xa, za) and usable(xb, zb)):
                            continue
                        if any(math.hypot(xa - cx, za - cz) < LINK_SPACING for cx, cz in chosen):
                            continue
                        ya, yb = float(s.y[m]), float(s.y[k])
                        if not (_wide(s, ia, ja, ya, 0.4, 0.5) and _wide(s, ib, jb, yb, 0.4, 0.5)):
                            continue
                        top, n_steps, clear = max(ya, yb), max(abs(ia - ib), abs(ja - jb)), True
                        for t_ in range(1, n_steps):
                            cc = (ja + round((jb - ja) * t_ / n_steps)) * nx + ia + round((ib - ia) * t_ / n_steps)
                            if any(top + SHIP_CLEAR < s.y[q_] < top + soldier1943.HEIGHT
                                   for q_ in range(s.start[cc], s.start[cc + 1])):
                                clear = False
                                break
                        if not clear:
                            continue
                        chosen.append((xa, za))
                        (lx, ly, lz), (ux, uy, uz) = sorted(((xa, ya, za), (xb, yb, zb)), key=lambda v: v[1])
                        out.append(_record(f"ship ledge ({b['bp'].split('/')[-1]})", lx, ly, lz, ux, uy, uz, d,
                                           len(pieces[n_]), 'ship ledge'))
                        if len(chosen) >= SHIP_LINKS:
                            break
                    if chosen:
                        reached.add(n_)
                        nxt.append(n_)
                        linked[n_] = len(chosen)
            frontier = nxt
        med = lambda n_: round(float(np.median([s.y[k] for k in pieces[n_]])), 2)
        cen = lambda n_: [round(float(np.mean([s.min_x + floor[k][0] * p for k in pieces[n_]])), 1),
                          round(float(np.mean([s.min_z + floor[k][1] * p for k in pieces[n_]])), 1)]
        rest = [n_ for n_ in range(len(pieces)) if n_ not in reached and len(pieces[n_]) * p * p >= SHIP_MIN_AREA]
        rep = {'ship': b['bp'].split('/')[-1], 'box': [round(v, 1) for v in lo] + [round(v, 1) for v in hi],
               'deck': {'m2': round(len(pieces[deck]) * p * p, 1), 'y': med(deck)},
               'linked': [{'m2': round(len(pieces[n_]) * p * p, 1), 'y': med(n_), 'centre': cen(n_), 'links': linked[n_]}
                          for n_ in sorted(linked)],
               'not linked': [{'m2': round(len(pieces[n_]) * p * p, 1), 'y': med(n_), 'centre': cen(n_),
                               'gun platform': n_ in skip} for n_ in rest]}
        report.append(rep)
        log(f"  {rep['ship']}: deck {rep['deck']['y']} ({rep['deck']['m2']:.0f} m2); {len(linked)} piece(s) linked by "
            f"{sum(linked.values())} jump link(s); {len(rest)} piece(s) of {SHIP_MIN_AREA:g} m2 or more not linked")
    return out, report


def current_detailed(st):
    """Whether navkit.json stamps a 0.2 m pass of the current preparation over the current detailed sectors (what
    navkit.py build builds the detailed sectors from)."""
    s2 = (st.get('samples') or {}).get('0.2') or {}
    return bool(st.get('preparation')) and s2.get('preparation') == st['preparation'] and \
        s2.get('sectors') == st.get('detailed_sectors')


def level_links(lv, log=print, game=None, detailed=None):
    """The jump links of a prepared and sampled level: from its 0.2 m samples where the gun's sector is detailed,
    else its 0.8 m samples. game: the game folder (navkit.py's --game); None = the copy the level was prepared from
    (2026-10-26: it was always the default, so a build for another copy took that copy's spawns and terrain).
    detailed: whether the build uses the 0.2 m samples (navkit.py build says; None: when navkit.json stamps a current
    0.2 m pass). Without them every sector is read at 0.8 m, as the build builds it (2026-10-30 review: samples_02.bin
    was read whenever it was on disk, a pass of an earlier sampling or preparation)."""
    import navaudit1943
    import navbuild
    import navkitjson1943 as nj
    import spawnmap
    from bfbc2core import game1943, terrain, water
    p = navaudit1943.level_paths(lv)
    st = nj.read(lv)
    if detailed is None:
        detailed = current_detailed(st)
    game = game1943.find_game(game or st.get('game'))
    g = game1943.Game(game, lv)
    # the height field the level was prepared with; a game copy changed since is refused (second review)
    hf = terrain.read(nj.prepared_heightfield(st, lv, g.data(f'terrains/{lv}/{lv}.heightfield-000.res')))
    m = spawnmap.spawn_map(game, lv)
    guns = [(s['name'], s['position']) for o in m['objectives'] for s in o['spawns'] if s.get('vehicle') in GUNS]
    guns += [(s['name'], s['position']) for s in m.get('unowned_vehicle_spawns', []) if s.get('vehicle') in GUNS]
    wpath = os.path.join(p['objects'], 'water_mesh.res')
    mesh = water.read_mesh(wpath) if soldier1943.has_water(st) and os.path.isfile(wpath) else None
    xoff, zoff = st['sector_offsets']
    dset = {(int(k[:2], 16), int(k[2:], 16)) for k in st.get('detailed_sectors') or ()} if detailed else set()
    # objects whose blueprint says their tops are not floor are left out: ForbidPathfinding (Iwo Jima's jetties and
    # boardwalk, trees, razor wire), ExcludeFromPathfinding (vegetation), dynamic props - the build drops their surfaces
    placed = json.load(open(os.path.join(p['objects'], 'objects.json'), encoding='utf-8'))
    placed += json.load(open(os.path.join(p['objects'], 'group_instances.json'), encoding='utf-8'))
    # by blueprint (the flags are the blueprint's): a box's 'index' counts only the placements with collision, so it is
    # no index into objects.json + group_instances.json (before 2026-10-26 it was taken as one, and the wrong boxes -
    # Wake Island's carriers among them - were left out)
    no_floor = {o['bp'] for o in placed if o.get('flags') and (o['flags'][0] or o['flags'][1] or o['flags'][3])}
    boxes = [b for b in json.load(open(os.path.join(p['objects'], 'object_boxes.json'), encoding='utf-8'))
             if b['bp'] not in no_floor]
    detailed_at = lambda x, z: (math.floor(x / 100) - xoff, math.floor(z / 100) - zoff) in dset
    out = []
    for which, src in (('detailed', 'samples_02.bin'), ('normal', 'samples.bin')):
        path = os.path.join(p['samples'], src)
        if not os.path.isfile(path) or (which == 'detailed' and not detailed):
            continue
        mine = [(n, q) for n, q in guns if detailed_at(q[0], q[2]) == (which == 'detailed')]
        my_boxes = [b for b in boxes if detailed_at((b['min'][0] + b['max'][0]) / 2, (b['min'][2] + b['max'][2]) / 2)
                    == (which == 'detailed')]
        s = navaudit1943.load_samples(path)
        height = lambda x, z: float(hf.height(x, z))
        if mine:
            top = navbuild.water_tops(s, mesh[0], mesh[1]) if mesh is not None else np.full(s.nx * s.nz, -np.inf)
            log(f'{lv}: {len(mine)} AA gun(s) on the {s.pitch:g} m samples')
            out += platform_links(s, mine, lambda c: top[c], height, log)
            del top
        if RAISED_FLOORS:
            n0 = len(out)
            out += raised_floor_links(s, height, my_boxes, log)
            log(f'{lv}: {len(out) - n0} raised-floor jump link(s) on the {s.pitch:g} m samples')
        if SHIP_LEDGES:
            # 2026-10-30: float(None) raised on a level without water (prepare writes sea_level None)
            ships = ship_boxes(boxes, height, soldier1943.sea_level(st, hf))
            if ships:
                taken = [tuple(r['platform']) for r in out if r.get('rule') != 'ship ledge']
                log(f'{lv}: {len(ships)} ship(s), ledges on the {s.pitch:g} m samples')
                links, rep = ship_ledge_links(s, ships, height, lambda x, z, d=(which == 'detailed'): detailed_at(x, z) == d,
                                              taken, log)
                out += links
                SHIP_REPORT[which] = rep
        del s
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog='jumplinks1943.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('level')
    ap.add_argument('--json')
    ap.add_argument('--game', help='the game folder (default: the copy the level was prepared from)')
    a = ap.parse_args(argv[1:])
    import navaudit1943
    import navkitjson1943 as nj
    if a.json is None and a.game is not None:
        # the build's own jump_links.json: only from the copy the level was prepared from (navkit.py's same_game rule;
        # 2026-10-30 second review: another copy's AA guns replaced the build's links, and the export used them)
        st = nj.read(a.level)
        if not nj.same_file(st.get('game'), a.game):
            print(f'error: {a.level}: prepared from {st.get("game")}, not {a.game}: jump links of another copy are not '
                  f'written over the build\'s (pass --json <file>, or prepare again: navkit.py prepare {a.level})')
            return 1
    links = level_links(a.level, game=a.game)
    path = a.json or os.path.join(navaudit1943.level_paths(a.level)['navkit'], 'jump_links.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(links, f, indent=1)
    print(f'{a.level}: {len(links)} jump link(s) -> {path}')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('jumplinks1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
