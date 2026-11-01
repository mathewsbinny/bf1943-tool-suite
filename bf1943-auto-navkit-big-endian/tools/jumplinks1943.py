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
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
import soldier1943  # noqa: E402

if TYPE_CHECKING:
    from navsample import Samples

type Log = Callable[[str], object]
type Link = dict[str, Any]          # a jump link in the ladders.json format (_record)
type Box = Mapping[str, Any]        # an object_boxes.json record: 'bp', 'min', 'max', ...
type Terrain = Callable[[float, float], float]      # terrain(x, z): the terrain height

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


def _column_surfaces(s: 'Samples', col: int) -> list[int]:
    """The surface indices of column col (j-major) of samples s, top down."""
    return list(range(s.start[col], s.start[col + 1]))


def _clear(s: 'Samples', k: int, position: int, column: Sequence[int]) -> bool:
    """Clear (capsule), flat enough, and with the soldier's headroom. k is the surface at index position of column
    (its surfaces, top down): the one above it must be at least the soldier's height higher."""
    if s.flags[k] & 1 or s.normal[k, 1] < math.cos(math.radians(soldier1943.SLOPE_MAX)) - 0.01:
        return False
    return position == 0 or s.y[column[position - 1]] - s.y[k] >= soldier1943.HEIGHT


def _wide(s: 'Samples', i: int, j: int, y: float, radius: float = 0.6, share: float = 0.7) -> bool | int:
    """Floor, not a rail or a ledge: most columns within radius have a clear surface at this height (a platform's
    rail top lies at the deck's height and is one or two samples wide)."""
    reach = int(round(radius / s.pitch))
    columns = with_floor = 0
    for col_j in range(j - reach, j + reach + 1):
        for col_i in range(i - reach, i + reach + 1):
            if (col_i - i) ** 2 + (col_j - j) ** 2 > reach * reach:
                continue
            if not (0 <= col_i < s.nx and 0 <= col_j < s.nz):
                continue
            columns += 1
            column = _column_surfaces(s, col_j * s.nx + col_i)
            with_floor += any(abs(s.y[k] - y) <= 0.1 and _clear(s, k, position, column)
                              for position, k in enumerate(column))
    return columns and with_floor / columns >= share


def platform_links(s: 'Samples', spawns: Iterable[tuple[str, Sequence[float]]], water_top: Callable[[int], float],
                   terrain: Terrain, log: Log = print) -> list[Link]:
    """Jump-link records for the gun spawns [(name, (x, y, z))] on samples s. water_top(c) and terrain(x, z) give the
    water surface over a column and the terrain height."""
    out = []
    for name, (x, y, z) in spawns:
        spawn_i, spawn_j = int(round((x - s.min_x) / s.pitch)), int(round((z - s.min_z) / s.pitch))
        if not (0 <= spawn_i < s.nx and 0 <= spawn_j < s.nz):
            continue
        spawn_col = spawn_j * s.nx + spawn_i
        below = [k for k in _column_surfaces(s, spawn_col) if s.y[k] <= y + 0.5]
        if not below:
            continue
        floor_k = max(below, key=lambda k: s.y[k])
        floor_y = float(s.y[floor_k])
        ground_y = float(terrain(x, z))
        if abs(floor_y - ground_y) < 0.06 or water_top(spawn_col) - ground_y <= soldier1943.WATER_DEPTH:
            continue                                     # on terrain, or not over deep water: not a ship's gun
        # the platform: clear floor joined by the step rule, starting from clear floor nearest the spawn
        reach = int(PLATFORM_RADIUS / s.pitch)
        seeds = []                                       # (distance in columns, surface, i, j)
        for j in range(max(spawn_j - 6, 0), min(spawn_j + 7, s.nz)):
            for i in range(max(spawn_i - 6, 0), min(spawn_i + 7, s.nx)):
                column = _column_surfaces(s, j * s.nx + i)
                for position, k in enumerate(column):
                    if abs(s.y[k] - floor_y) <= END_BAND and _clear(s, k, position, column):
                        seeds.append((math.hypot(i - spawn_i, j - spawn_j), k, i, j))
        if not seeds:
            log(f'  {name}: no clear floor beside the gun at {floor_y:.2f}')
            continue
        _, first_k, first_i, first_j = min(seeds)
        seen = {first_k: (first_i, first_j)}             # platform surface -> its column (i, j)
        queue = deque([first_k])
        while queue:
            k = queue.popleft()
            i, j = seen[k]
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    col_i, col_j = i + di, j + dj
                    if not (di or dj) or abs(col_i - spawn_i) > reach or abs(col_j - spawn_j) > reach:
                        continue
                    if not (0 <= col_i < s.nx and 0 <= col_j < s.nz):
                        continue
                    column = _column_surfaces(s, col_j * s.nx + col_i)
                    for position, other in enumerate(column):
                        if other in seen or not abs(s.y[other] - s.y[k]) <= soldier1943.STEP:
                            continue
                        if _clear(s, other, position, column):
                            seen[other] = (col_i, col_j)
                            queue.append(other)
        platform_y = np.array([s.y[k] for k in seen])
        # each platform surface's (x, z), in the order of platform_y
        platform_xz = np.array([[s.min_x + i * s.pitch, s.min_z + j * s.pitch] for i, j in seen.values()])
        # the deck: clear floor STEP..JUMP_HEIGHT above the platform within JUMP_GAP of it
        gap_cells = int(math.ceil(JUMP_GAP / s.pitch))
        platform_i = [i for i, _ in seen.values()]
        platform_j = [j for _, j in seen.values()]
        # (gap, platform end (x, y, z), deck end (x, y, z))
        best: tuple[float, tuple[float, float, float], tuple[float, float, float]] | None = None
        platform_top = float(platform_y.mean())
        for j in range(max(min(platform_j) - gap_cells, 0), min(max(platform_j) + gap_cells + 1, s.nz)):
            for i in range(max(min(platform_i) - gap_cells, 0), min(max(platform_i) + gap_cells + 1, s.nx)):
                column = _column_surfaces(s, j * s.nx + i)
                for position, deck_k in enumerate(column):
                    rise = s.y[deck_k] - platform_top
                    if not (soldier1943.STEP < rise <= JUMP_HEIGHT) or not _clear(s, deck_k, position, column):
                        continue
                    px, pz = s.min_x + i * s.pitch, s.min_z + j * s.pitch
                    dist = np.hypot(platform_xz[:, 0] - px, platform_xz[:, 1] - pz)
                    nearest = int(np.argmin(dist))
                    if not (dist[nearest] <= JUMP_GAP and (best is None or dist[nearest] < best[0])):
                        continue
                    if _wide(s, i, j, float(s.y[deck_k])):
                        platform_end = (float(platform_xz[nearest, 0]), float(platform_y[nearest]),
                                        float(platform_xz[nearest, 1]))
                        best = (float(dist[nearest]), platform_end, (px, float(s.y[deck_k]), pz))
        if best is None:
            log(f'  {name}: platform of {len(seen)} cells at {platform_top:.2f}, no deck within {JUMP_GAP} m and '
                f'{JUMP_HEIGHT} m up')
            continue
        # a: the platform end, b: the deck end
        gap, (ax, ay, az), (bx, by, bz) = best
        out.append({'bp': f'jump link: {name}', 'kind': 'jump', 'gun': [round(x, 2), round(y, 2), round(z, 2)],
                    'lo': [round(min(ax, bx), 3), round(ay, 3), round(min(az, bz), 3)],
                    'hi': [round(max(ax, bx), 3), round(by, 3), round(max(az, bz), 3)], 'box_lo_y': round(ay, 3),
                    'platform': [round(ax, 2), round(ay, 2), round(az, 2)],
                    'deck': [round(bx, 2), round(by, 2), round(bz, 2)],
                    'rise': round(by - ay, 2), 'gap': round(gap, 2), 'platform_cells': len(seen),
                    'foot_band': [-END_BAND, END_BAND], 'top_band': [-END_BAND, END_BAND], 'reach': 0.5})
        log(f'  {name} at ({x:.1f}, {y:.1f}, {z:.1f}): platform {platform_top:.2f} ({len(seen)} cells), deck {by:.2f} '
            f'(+{by - ay:.2f} m), gap {gap:.2f} m')
    return out


def _record(name: str, ax: float, ay: float, az: float, bx: float, by: float, bz: float, gap: float, cells: int,
            rule: str) -> Link:
    """A jump-link record from its lower end a to its upper end b, gap apart horizontally, onto a piece of cells
    samples, made by rule."""
    return {'bp': f'jump link: {name}', 'kind': 'jump', 'rule': rule,
            'lo': [round(min(ax, bx), 3), round(ay, 3), round(min(az, bz), 3)],
            'hi': [round(max(ax, bx), 3), round(by, 3), round(max(az, bz), 3)], 'box_lo_y': round(ay, 3),
            'platform': [round(ax, 2), round(ay, 2), round(az, 2)], 'deck': [round(bx, 2), round(by, 2), round(bz, 2)],
            'rise': round(by - ay, 2), 'gap': round(gap, 2), 'platform_cells': cells,
            'foot_band': [-END_BAND, END_BAND], 'top_band': [-END_BAND, END_BAND], 'reach': 0.5}


def _box_columns(s: 'Samples', box: Box) -> tuple[int, int, int, int]:
    """The column range (i0, i1, j0, j1), inclusive, that an object box covers on samples s."""
    lo, hi, pitch = box['min'], box['max'], s.pitch
    i0, i1 = max(int((lo[0] - s.min_x) / pitch), 0), min(int((hi[0] - s.min_x) / pitch) + 1, s.nx - 1)
    j0, j1 = max(int((lo[2] - s.min_z) / pitch), 0), min(int((hi[2] - s.min_z) / pitch) + 1, s.nz - 1)
    return i0, i1, j0, j1


def raised_floor_links(s: 'Samples', terrain: Terrain, boxes: Iterable[Box], log: Log = print) -> list[Link]:
    """Jump links onto raised floors (the user's choice, 2026-10-24): wide, flat, clear floors on objects that the step
    rule joins to no terrain - Iwo Jima's beach boardwalk, its deck 0.55-1.05 m over the ground on both sides, left a
    strip of beach and the boardwalk itself out of the network. A floor is a piece of clear, flat object surfaces (not
    terrain) joined by the step rule, flooded from the object boxes; one that reaches terrain by steps, or is larger
    than FLOOR_MAX (a carrier's deck), or smaller than FLOOR_MIN_AREA (a crate's top), gets none. Along each other
    floor's edge, a link wherever clear, flat ground lies within LINK_REACH horizontally and more than STEP, at most
    JUMP_HEIGHT, below; at least LINK_SPACING apart, at most LINK_MAX per floor. s: samples; terrain(x, z) heights."""
    STEP = soldier1943.STEP
    nx, nz, pitch = s.nx, s.nz, s.pitch

    def column_of(k: int) -> int:
        return int(np.searchsorted(s.start, k, side='right')) - 1
    terrain_cache: dict[int, float] = {}

    def terrain_at(col: int) -> float:
        if col not in terrain_cache:
            terrain_cache[col] = float(terrain(s.min_x + (col % nx) * pitch, s.min_z + (col // nx) * pitch))
        return terrain_cache[col]

    floor_cache: dict[int, dict[int, bool]] = {}

    def flat_clear(col: int) -> dict[int, bool]:
        """{surface: on terrain} of the column's clear, flat surfaces with headroom."""
        if col not in floor_cache:
            column = _column_surfaces(s, col)
            floor_cache[col] = {k: abs(s.y[k] - terrain_at(col)) < 0.06
                                for position, k in enumerate(column) if _clear(s, k, position, column)}
        return floor_cache[col]
    seen = set()
    out = []
    link_cells = max(1, int(round(LINK_REACH / pitch)))
    for box in boxes:
        lo, hi = box['min'], box['max']
        i0, i1, j0, j1 = _box_columns(s, box)
        if i0 > i1 or j0 > j1:
            continue
        object_name = box['bp'].split('/')[-1]
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                for first_k, on_terrain in flat_clear(j * nx + i).items():
                    if on_terrain or first_k in seen or not (lo[1] - 0.5 <= s.y[first_k] <= hi[1] + 0.5):
                        continue
                    # flood one floor
                    piece, queue, touches_terrain = {first_k}, deque([first_k]), False
                    seen.add(first_k)
                    while queue and len(piece) <= FLOOR_MAX:
                        k = queue.popleft()
                        col_k = column_of(k)
                        col_i, col_j = col_k % nx, col_k // nx
                        for dj in (-1, 0, 1):
                            for di in (-1, 0, 1):
                                near_i, near_j = col_i + di, col_j + dj
                                if not (di or dj) or not (0 <= near_i < nx and 0 <= near_j < nz):
                                    continue
                                for other, other_on_terrain in flat_clear(near_j * nx + near_i).items():
                                    if abs(s.y[other] - s.y[k]) > STEP:
                                        continue
                                    if other_on_terrain:
                                        touches_terrain = True
                                    elif other not in seen:
                                        seen.add(other)
                                        piece.add(other)
                                        queue.append(other)
                    area = len(piece) * pitch * pitch
                    if touches_terrain or len(piece) > FLOOR_MAX or area < FLOOR_MIN_AREA:
                        continue
                    # links along its edge to ground below:
                    # (drop, horizontal distance, floor surface, ground surface, floor column i, j, ground column i, j)
                    candidates = []
                    for k in piece:
                        col_k = column_of(k)
                        col_i, col_j = col_k % nx, col_k // nx
                        for dj in range(-link_cells, link_cells + 1):
                            for di in range(-link_cells, link_cells + 1):
                                near_i, near_j = col_i + di, col_j + dj
                                if not (di or dj) or not (0 <= near_i < nx and 0 <= near_j < nz):
                                    continue
                                if math.hypot(di, dj) * pitch > LINK_REACH:
                                    continue
                                for ground in flat_clear(near_j * nx + near_i):
                                    drop = float(s.y[k] - s.y[ground])
                                    if ground in piece or not (STEP < drop <= JUMP_HEIGHT):
                                        continue
                                    if _wide(s, near_i, near_j, float(s.y[ground]), 0.4, 0.5):
                                        candidates.append((drop, math.hypot(di, dj) * pitch, k, ground,
                                                           col_i, col_j, near_i, near_j))
                    candidates.sort()
                    chosen: list[tuple[float, float]] = []      # the links' floor ends (x, z)
                    for _drop, gap, k, ground, col_i, col_j, near_i, near_j in candidates:
                        floor_x, floor_z = s.min_x + col_i * pitch, s.min_z + col_j * pitch
                        if any(math.hypot(floor_x - chosen_x, floor_z - chosen_z) < LINK_SPACING
                               for chosen_x, chosen_z in chosen):
                            continue
                        chosen.append((floor_x, floor_z))
                        ground_x, ground_z = s.min_x + near_i * pitch, s.min_z + near_j * pitch
                        out.append(_record(f"raised floor ({object_name})", ground_x, float(s.y[ground]), ground_z,
                                           floor_x, float(s.y[k]), floor_z, gap, len(piece), 'raised floor'))
                        if len(chosen) >= LINK_MAX:
                            break
                    if chosen:
                        log(f"  raised floor of {area:.0f} m2 on {object_name} at ({s.min_x + i * pitch:.1f}, "
                            f"{s.y[first_k]:.2f}, {s.min_z + j * pitch:.1f}): {len(chosen)} jump link(s)")
    return out


# the last level_links run's ship report per grid ('detailed', 'normal'): ship_ledge_links
SHIP_REPORT: dict[str, list[dict[str, Any]]] = {}


def ship_boxes(boxes: Iterable[Box], terrain: Terrain, sea: float) -> list[Box]:
    """The ships among the object boxes, from the level's data alone: a box of SHIP_MIN_FOOTPRINT or more over water
    deeper than the soldier's WATER_DEPTH at its centre, reaching SHIP_MIN_FREEBOARD above the sea (the carriers of
    Wake Island, Iwo Jima and Coral Sea; not Guadalcanal's wrecks, 888-1,827 m2, nor the bridges)."""
    out = []
    for box in boxes:
        lo, hi = box['min'], box['max']
        if (hi[0] - lo[0]) * (hi[2] - lo[2]) < SHIP_MIN_FOOTPRINT or hi[1] < sea + SHIP_MIN_FREEBOARD:
            continue
        if float(terrain((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2)) < sea - soldier1943.WATER_DEPTH:
            out.append(box)
    return out


def ship_ledge_links(s: 'Samples', ships: Iterable[Box], terrain: Terrain,
                     usable: Callable[[float, float], bool] = lambda x, z: True,
                     taken: Iterable[Sequence[float]] = (), log: Log = print) \
        -> tuple[list[Link], list[dict[str, Any]]]:
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
    nx, pitch = s.nx, s.pitch
    out, report = [], []
    for box in ships:
        lo, hi = box['min'], box['max']
        i0, i1, j0, j1 = _box_columns(s, box)
        floor: dict[int, tuple[int, int]] = {}      # surface -> (i, j): the clear, flat floor on the ship
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                column = _column_surfaces(s, j * nx + i)
                if not column:
                    continue
                ground_y = float(terrain(s.min_x + i * pitch, s.min_z + j * pitch))
                for position, k in enumerate(column):
                    if not (lo[1] - 0.5 <= s.y[k] <= hi[1] + 0.5 and abs(s.y[k] - ground_y) >= 0.06):
                        continue
                    if _clear(s, k, position, column):
                        floor[k] = (i, j)
        if not floor:
            continue
        floor_at: dict[tuple[int, int], list[int]] = {}     # (i, j) -> the floor surfaces in that column
        for k, ij in floor.items():
            floor_at.setdefault(ij, []).append(k)
        # the pieces: floor joined by the step rule
        piece_of: dict[int, int] = {}               # surface -> piece number
        pieces: list[list[int]] = []                # per piece, its surfaces
        for first_k in floor:
            if first_k in piece_of:
                continue
            piece = len(pieces)
            piece_of[first_k] = piece
            members, queue = [first_k], deque([first_k])
            while queue:
                k = queue.popleft()
                i, j = floor[k]
                for dj in (-1, 0, 1):
                    for di in (-1, 0, 1):
                        for other in floor_at.get((i + di, j + dj), ()):
                            if other not in piece_of and abs(s.y[other] - s.y[k]) <= STEP:
                                piece_of[other] = piece
                                members.append(other)
                                queue.append(other)
            pieces.append(members)

        def area(piece: int) -> float:
            return len(pieces[piece]) * pitch * pitch
        deck = max(range(len(pieces)), key=lambda piece: len(pieces[piece]))
        taken_pieces = set()                        # the pieces holding a link end already made (the gun platforms)
        for x, y, z in taken:
            ij = (int(round((x - s.min_x) / pitch)), int(round((z - s.min_z) / pitch)))
            for k in floor_at.get(ij, ()):
                if abs(s.y[k] - y) <= END_BAND:
                    taken_pieces.add(piece_of[k])
        # each piece's edge: its surfaces with a side column holding none of its surfaces
        edge: dict[int, list[int]] = {}
        for piece, members in enumerate(pieces):
            mine = set(members)

            def on_edge(k: int) -> bool:
                i, j = floor[k]
                return any(not any(other in mine for other in floor_at.get((i + di, j + dj), ()))
                           for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            edge[piece] = [k for k in members if on_edge(k)]
        reach = int(math.ceil(SHIP_REACH / pitch))
        reached, frontier = {deck}, [deck]
        linked: dict[int, int] = {}                 # piece -> its number of links
        while frontier:
            next_frontier = []
            for source in frontier:
                near: dict[tuple[int, int], list[int]] = {}     # column -> source's edge surfaces within reach
                for k in edge[source]:
                    i, j = floor[k]
                    for dj in range(-reach, reach + 1):
                        for di in range(-reach, reach + 1):
                            if math.hypot(di, dj) * pitch <= SHIP_REACH:
                                near.setdefault((i + di, j + dj), []).append(k)
                # piece -> [(gap, |rise|, its edge surface, source's edge surface)]
                candidates: dict[int, list[tuple[float, float, int, int]]] = {}
                for piece, piece_edge in edge.items():
                    if piece in reached or piece in taken_pieces or area(piece) < SHIP_MIN_AREA:
                        continue
                    for m in piece_edge:
                        for k in near.get(floor[m], ()):
                            rise = float(s.y[k] - s.y[m])
                            if STEP < abs(rise) <= JUMP_HEIGHT:
                                (ia, ja), (ib, jb) = floor[m], floor[k]
                                candidates.setdefault(piece, []).append(
                                    (math.hypot(ia - ib, ja - jb) * pitch, abs(rise), m, k))
                for piece, options in sorted(candidates.items()):
                    if piece in reached:
                        continue
                    options.sort()
                    chosen: list[tuple[float, float]] = []      # the links' ends (x, z) on this piece
                    for gap, _rise, m, k in options:
                        # a: the end on this piece (m), b: the end on the source piece (k)
                        (ia, ja), (ib, jb) = floor[m], floor[k]
                        xa, za = s.min_x + ia * pitch, s.min_z + ja * pitch
                        xb, zb = s.min_x + ib * pitch, s.min_z + jb * pitch
                        if not (usable(xa, za) and usable(xb, zb)):
                            continue
                        if any(math.hypot(xa - chosen_x, za - chosen_z) < LINK_SPACING
                               for chosen_x, chosen_z in chosen):
                            continue
                        ya, yb = float(s.y[m]), float(s.y[k])
                        if not (_wide(s, ia, ja, ya, 0.4, 0.5) and _wide(s, ib, jb, yb, 0.4, 0.5)):
                            continue
                        # nothing between the two ends higher than the upper floor plus SHIP_CLEAR (below headroom)
                        top, n_steps, clear = max(ya, yb), max(abs(ia - ib), abs(ja - jb)), True
                        for step in range(1, n_steps):
                            col = ((ja + round((jb - ja) * step / n_steps)) * nx
                                   + ia + round((ib - ia) * step / n_steps))
                            if any(top + SHIP_CLEAR < s.y[above] < top + soldier1943.HEIGHT
                                   for above in range(s.start[col], s.start[col + 1])):
                                clear = False
                                break
                        if not clear:
                            continue
                        chosen.append((xa, za))
                        # l: the lower end, u: the upper
                        (lx, ly, lz), (ux, uy, uz) = sorted(((xa, ya, za), (xb, yb, zb)), key=lambda end: end[1])
                        out.append(_record(f"ship ledge ({box['bp'].split('/')[-1]})", lx, ly, lz, ux, uy, uz, gap,
                                           len(pieces[piece]), 'ship ledge'))
                        if len(chosen) >= SHIP_LINKS:
                            break
                    if chosen:
                        reached.add(piece)
                        next_frontier.append(piece)
                        linked[piece] = len(chosen)
            frontier = next_frontier

        def median_y(piece: int) -> float:
            return round(float(np.median([s.y[k] for k in pieces[piece]])), 2)

        def centre(piece: int) -> list[float]:
            return [round(float(np.mean([s.min_x + floor[k][0] * pitch for k in pieces[piece]])), 1),
                    round(float(np.mean([s.min_z + floor[k][1] * pitch for k in pieces[piece]])), 1)]
        not_linked = [piece for piece in range(len(pieces))
                      if piece not in reached and area(piece) >= SHIP_MIN_AREA]
        ship = {'ship': box['bp'].split('/')[-1], 'box': [round(v, 1) for v in lo] + [round(v, 1) for v in hi],
                'deck': {'m2': round(area(deck), 1), 'y': median_y(deck)},
                'linked': [{'m2': round(area(piece), 1), 'y': median_y(piece), 'centre': centre(piece),
                            'links': linked[piece]} for piece in sorted(linked)],
                'not linked': [{'m2': round(area(piece), 1), 'y': median_y(piece), 'centre': centre(piece),
                                'gun platform': piece in taken_pieces} for piece in not_linked]}
        report.append(ship)
        log(f"  {ship['ship']}: deck {ship['deck']['y']} ({ship['deck']['m2']:.0f} m2); {len(linked)} piece(s) linked "
            f"by {sum(linked.values())} jump link(s); {len(not_linked)} piece(s) of {SHIP_MIN_AREA:g} m2 or more not "
            f"linked")
    return out, report


def current_detailed(st: Mapping[str, Any]) -> bool:
    """Whether navkit.json stamps a 0.2 m pass of the current preparation over the current detailed sectors (what
    navkit.py build builds the detailed sectors from)."""
    pass_02 = (st.get('samples') or {}).get('0.2') or {}
    return (bool(st.get('preparation')) and pass_02.get('preparation') == st['preparation']
            and pass_02.get('sectors') == st.get('detailed_sectors'))


def level_links(lv: str, log: Log = print, game: str | None = None, detailed: bool | None = None) -> list[Link]:
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
    paths = navaudit1943.level_paths(lv)
    st = nj.read(lv)                                 # navkit.json
    if detailed is None:
        detailed = current_detailed(st)
    game = game1943.find_game(game or st.get('game'))
    level_game = game1943.Game(game, lv)
    # the height field the level was prepared with; a game copy changed since is refused (second review)
    hf = terrain.read(nj.prepared_heightfield(st, lv, level_game.data(f'terrains/{lv}/{lv}.heightfield-000.res')))
    spawns = spawnmap.spawn_map(game, lv)
    guns = [(spawn['name'], spawn['position']) for objective in spawns['objectives'] for spawn in objective['spawns']
            if spawn.get('vehicle') in GUNS]
    guns += [(spawn['name'], spawn['position']) for spawn in spawns.get('unowned_vehicle_spawns', [])
             if spawn.get('vehicle') in GUNS]
    water_path = os.path.join(paths['objects'], 'water_mesh.res')
    mesh = water.read_mesh(water_path) if soldier1943.has_water(st) and os.path.isfile(water_path) else None
    xoff, zoff = st['sector_offsets']
    detailed_sectors = set()                         # (X, Z) of the sectors the build reads at 0.2 m
    if detailed:
        detailed_sectors = {(int(k[:2], 16), int(k[2:], 16)) for k in st.get('detailed_sectors') or ()}
    # objects whose blueprint says their tops are not floor are left out: ForbidPathfinding (Iwo Jima's jetties and
    # boardwalk, trees, razor wire), ExcludeFromPathfinding (vegetation), dynamic props - the build drops their surfaces
    placed = json.load(open(os.path.join(paths['objects'], 'objects.json'), encoding='utf-8'))
    placed += json.load(open(os.path.join(paths['objects'], 'group_instances.json'), encoding='utf-8'))
    # by blueprint (the flags are the blueprint's): a box's 'index' counts only the placements with collision, so it is
    # no index into objects.json + group_instances.json (before 2026-10-26 it was taken as one, and the wrong boxes -
    # Wake Island's carriers among them - were left out)
    no_floor = {placement['bp'] for placement in placed
                if placement.get('flags') and (placement['flags'][0] or placement['flags'][1] or placement['flags'][3])}
    boxes = [box for box in json.load(open(os.path.join(paths['objects'], 'object_boxes.json'), encoding='utf-8'))
             if box['bp'] not in no_floor]

    def detailed_at(x: float, z: float) -> bool:
        return (math.floor(x / 100) - xoff, math.floor(z / 100) - zoff) in detailed_sectors
    out: list[Link] = []
    for which, file_name in (('detailed', 'samples_02.bin'), ('normal', 'samples.bin')):
        path = os.path.join(paths['samples'], file_name)
        if not os.path.isfile(path) or (which == 'detailed' and not detailed):
            continue
        on_this_grid = which == 'detailed'           # the guns and boxes this grid holds: detailed_at says which
        guns_here = [(name, at) for name, at in guns if detailed_at(at[0], at[2]) == on_this_grid]
        boxes_here = [box for box in boxes
                      if detailed_at((box['min'][0] + box['max'][0]) / 2, (box['min'][2] + box['max'][2]) / 2)
                      == on_this_grid]
        s = navaudit1943.load_samples(path)

        def height(x: float, z: float) -> float:
            return float(hf.height(x, z))
        if guns_here:
            if mesh is not None:
                water_top = navbuild.water_tops(s, mesh[0], mesh[1])
            else:
                water_top = np.full(s.nx * s.nz, -np.inf)
            log(f'{lv}: {len(guns_here)} AA gun(s) on the {s.pitch:g} m samples')
            out += platform_links(s, guns_here, lambda c: water_top[c], height, log)
            del water_top
        if RAISED_FLOORS:
            count_before = len(out)
            out += raised_floor_links(s, height, boxes_here, log)
            log(f'{lv}: {len(out) - count_before} raised-floor jump link(s) on the {s.pitch:g} m samples')
        if SHIP_LEDGES:
            # 2026-10-30: float(None) raised on a level without water (prepare writes sea_level None)
            ships = ship_boxes(boxes, height, soldier1943.sea_level(st, hf))
            if ships:
                taken = [tuple(link['platform']) for link in out if link.get('rule') != 'ship ledge']
                log(f'{lv}: {len(ships)} ship(s), ledges on the {s.pitch:g} m samples')

                def on_grid(x: float, z: float, grid_detailed: bool = on_this_grid) -> bool:
                    return detailed_at(x, z) == grid_detailed
                links, ship_report = ship_ledge_links(s, ships, height, on_grid, taken, log)
                out += links
                SHIP_REPORT[which] = ship_report
        del s
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='jumplinks1943.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('level')
    parser.add_argument('--json')
    parser.add_argument('--game', help='the game folder (default: the copy the level was prepared from)')
    args = parser.parse_args(argv[1:])
    import navaudit1943
    import navkitjson1943 as nj
    if args.json is None and args.game is not None:
        # the build's own jump_links.json: only from the copy the level was prepared from (navkit.py's same_game rule;
        # 2026-10-30 second review: another copy's AA guns replaced the build's links, and the export used them)
        st = nj.read(args.level)
        if not nj.same_file(st.get('game'), args.game):
            print(f'error: {args.level}: prepared from {st.get("game")}, not {args.game}: jump links of another copy '
                  f'are not written over the build\'s (pass --json <file>, or prepare again: navkit.py prepare '
                  f'{args.level})')
            return 1
    links = level_links(args.level, game=args.game)
    path = args.json or os.path.join(navaudit1943.level_paths(args.level)['navkit'], 'jump_links.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(links, f, indent=1)
    print(f'{args.level}: {len(links)} jump link(s) -> {path}')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('jumplinks1943', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
