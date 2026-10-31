"""NavKit's builder (navbuild), its configuration: the documented constants (NavKit's defaults, never changed at run
time), the Settings a build is made for (BFBC2 = NavKit's defaults, soldier1943.SETTINGS = BF1943's), a stage's Grid
(grid_at), what a build gathers as it goes (BuildState) and where a sector's group joining may flood (JoinScope).
Part of the package since 2026-10-31 (navbuild.py until then)."""
from dataclasses import dataclass, field

import navwalk


PITCH = 0.8                     # the normal grid (grid_at gives a stage's settings at its pitch)
SECTOR_SAMPLES = 125            # 100 m / 0.8 m
WINDOW = range(-1, 127)         # sample indices of a sector window (local)
TILE = 50                       # samples per tile
TILES = 3
TILE_20M_SECTORS = None         # experiment: sector ids (e.g. {0x901, 0x902}) tiled at 20 m (25 samples, 6 x 6 tiles)
                                # at the normal pitch - DICE's detailed zones have 20 m tiles (but pitch 0.2). For
                                # offline route checks only: how the runtime gets a sector's tile size is not known
                                # (its header has no tile size field), so such a build is not for the game (2026-10-19)
CLIP_TO_REFERENCE = True        # on a level with DICE path data, ground outside its extent (navregress.reference_bounds:
                                # outlines and portals at every pitch) is left out before seeding. A bound to the
                                # reference extent, NOT a found rule: DICE's edges are straight cuts (its last cell row
                                # fills 9-64 % of a side) from a generation rectangle that is not in the shipped level
                                # data. A new map (no DICE data) keeps the sampling box. (KYNAPSE_SPEC.md 4.5)
EDGE_SECTOR_RULE = 'grid_min'   # which sectors start their tiles at their own edge (EDGE_SECTOR_TILES): 'grid_min' = on
                                # the sector grid's minimum X (Z) - DICE 3,798 of 3,798 sector axes; 'no_neighbour' = no
                                # sector on the low side - 3,788 (misses holes inside the map)
SYMMETRIC_PORTALS = True        # every portal stands on both of its areas (DICE: 771 of 771 have a portal back)
ONE_WAY_PORTALS = 'drop'        # a portal piece whose target sees that side as its own interior (a second surface of
                                # the target in the source's column) cannot have a piece back (DICE meets no such case:
                                # its stacked floors are detailed zones): 'drop' = always outline; 'keep' = always a
                                # one-way portal; 'restore' = outline unless the two areas are joined through no other
                                # portal or shared surface level-wide (restore_one_way) - measured on sp_004_b it keeps
                                # 1,066 one-way records (80 % two-way) and AI pairwise falls 91.2 -> 89.1: most pairs a
                                # drop leaves without a portal are still joined by the route graph (walkable lines),
                                # which a portal-level test does not see. Open until the in-game test shows whether the
                                # runtime needs a portal to cross between areas (KYNAPSE_SPEC.md 4.5)
EDGE_SECTOR_TILES = True       # a sector with no sector next to it on the low x (or z) side starts its tiles at its
                                # own edge (sample 0, not -1): tile lines one cell later, 39.6 / 79.6 m into the sector
                                # (DICE's data: 3,788 of 3,798 sector axes on 17 levels; KYNAPSE_SPEC.md 4.5). Latent
                                # for now: NavKit's extra sectors outside DICE's bounds give most edge sectors a neighbour
MAX_AREA = 25                   # samples (20 m) before an area is bisected (only when BISECT_AREAS)
BISECT_AREAS = False            # DICE does not cut areas at the middle of their extent: its same-sector portals lie on
                                # the tile lines (38.8 / 78.8 m into the sector) and only sometimes on the fixed tile
                                # midlines (18.8 / 58.8 m; sp_005 17 %, sp_007 3 %, sp_004_b 2 % of portals), mostly in
                                # ground with long outlines; trigger not yet known (KYNAPSE_SPEC.md 4.5, 2026-10-16)
AREA_LINK = 'terrain'           # which neighbours one area joins: 'terrain' = DICE's rule below between terrain
                                # surfaces (the slope rule elsewhere), 'raw' = DICE's rule everywhere, 'slope' = the
                                # slope rule. Portals, borders and the route graph keep the slope rule ('slope'), so
                                # the steep pieces DICE cuts off stay reachable (NavKit lacks some of the steep cells
                                # that join them in DICE's data: the DICE rule there cost sp_004_b 9 connectivity points)
LINK_RULE = 'slope'             # the rule for everything else
LINK_STEP = 0.6                 # DICE: straight neighbours link when their raw height step is <= 0.6 m (36.9 deg at
                                # 0.8 m - steeper walkable cells are cut off, DICE's single-cell areas); diagonal ones
                                # when a route through either side cell (walkable or not) has both steps <= 0.6 m
                                # (sp_004_b terrain: linked steps max 0.591, unlinked mostly 0.62+; KYNAPSE_SPEC.md 4.5)
RADIUS = 0.4
RESOLUTION = 0.025
DIST_EDGE_MAX = 15.0
NODE_COVER = {'detailed': 4.0, 'normal': None}   # coverage nodes: in each area, a route node wherever walkable ground is
                                # farther than this (m, straight line, same floor) from every node, farthest first.
                                # DICE's detailed sectors: 90 % of outline points within 4.3 m of a node, 99 % within
                                # 8.3; NavKit's area-centre and portal nodes left 5.4 / 18.2 (ONS_MP_002, 2026-10-21).
                                # Normal sectors: off until checked against the campaign regression
GABRIEL_EDGES = 'detailed'      # prune the route graph's walkable pairs to the Gabriel graph (no other same-floor node
                                # inside the circle on the pair as diameter), keeping each sector's connectivity:
                                # 'detailed' = in pitch-0.2 sectors, 'all', or None. DICE's detailed-sector edges are 71 %
                                # Gabriel (RNG 50 %), 2.7 edges per node; NavKit's all-visible-pairs rule gave 15.9 per node
                                # there (174,024 edges against DICE's 24,710; ONS_MP_002, 2026-10-21)
DEDUP_GRAPH_NODES = True        # at most one route node per surface (build_graph; DICE: only border pairs share one)
SORT_NODES_BY_ID = True         # a sector's nodes in the file sorted by unsigned id, border nodes last (finalize_graph)
BORDER_OWNER = True             # node field c bit 16 only on the border copies the sector does not own (finalize_graph)
ORDER_NODES = True              # a route node at AI order positions far from any node (add_order_nodes; needs the level's
                                # order_points.json from levelobjects.py) - sp_004_b play test 2026-10-20: 0 failures. Was off until
                                # the sp_004_b play test confirms it (2026-10-20)
ORDER_NODE_MIN = 3.0            # ... when the nearest node on the same floor is farther than this (m)
ORDER_MIN_ISLAND = 25           # an order node in a route island with fewer nodes is dropped (prune_order_nodes): such
                                # pockets took the nearest-node matches next to the main networks (sp_009: connectivity
                                # 66.99 without order nodes, 62.58 with all, 67.45 with this rule; sp_004_b keeps its
                                # stairs-top nodes and gains 0.26 connectivity / 0.6 AI pairwise). A size, not "the main
                                # network": campaign levels have several large networks (sp_004_b: 2,577 and 1,255
                                # nodes). Calibrated on campaign levels; to check on MP geometry (2026-10-20)
DETOUR_REPAIR = False           # add waypoint nodes where the route graph detours (repair_detours) - off: at 1.3x it
                                # adds 65 % nodes / 4x edges on sp_004_b, far tighter than DICE's own graph (2026-10-20)
DETOUR_TRIGGER = 3.0            # repair node pairs within DIST_EDGE_MAX whose route exceeds this x straight - above DICE's
                                # p90 (3.12; median 1.34, sp_004_b 2026-10-20); the failing go-to routes ran 3.15-6.38x
DETOUR_MAX = 1.3                # ... to a walk of at most this x straight (DICE at the failing spot: 1.02-1.11)
DETOUR_LINK_ALL = False         # also link new waypoints to all walkable neighbours within DIST_EDGE_MAX (4x the edges)
DETOUR_PASSES = 2               # pass A, then pass B on the repaired graph
LADDER_ALL_GROUND = False       # link a ladder to each piece of ground within reach of its ends, not only the nearest
                                # surface (navwalk.ladder_end_options; BF1943: soldier1943.SETTINGS sets it)
BORDER_NODE_SPACING = 10.0
JOIN_MARGIN = None              # m: join_groups floods this far past the sector's window (2026-10-26;
                                # soldier1943.SETTINGS sets 2.0 for BF1943); None = the window only
MIXED_OVERLAP = None            # m: windows grow this far past a mixed seam (sector_window; 2026-10-26,
                                # soldier1943.SETTINGS sets 1.0 for BF1943)
AREA_CLIFF = None               # m: no two plan-neighbouring cells of one area further apart in height than this and than
                                # a 45-degree slope plus the step (components, _cliff; 2026-10-26, soldier1943.SETTINGS
                                # sets 0.6 for BF1943); None = off
AREA_CLIFF_REACH = 1            # 2: AREA_CLIFF also between cells two apart with an empty cell between them (2026-10-29,
                                # soldier1943.SETTINGS sets 2 for BF1943)
BORDER_SEAM_LINE = False        # shared border nodes also on the seam line itself for ground that does not reach the
                                # border line one sample beyond (2026-10-26; soldier1943.SETTINGS sets it for BF1943)
BRIDGE_UNION = True             # 45-degree bridges: outline from the exact union (bridge_union) - False: clip_bridges
SLOPE_MAX = 40.0                # the steepest walkable ground (deg), passed to navwalk.walkable; BF1943's soldier
                                # physics allows 45 (DefaultSoldierPhysics MaxAscendAngle: soldier1943.SETTINGS)
MAX_WATER_DEPTH = 1.5           # ground deeper under water is not walkable (DICE's data: 99.9 % of its ground under
                                # water is within 1.45 m, median over 11 levels; navwater.py)
AREA_Y_MIN = 0.0                # area height range relative to its ground: DICE's convention (see build())
AREA_Y_MAX = 1.52
Y_OFFSET = 0.95                 # outline / node height above the ground (EntityHeight / 2)
HEADER = (2, 2, 0, 1, 0, 0, 0, 0, 2, 0, 8, 0) + (0,) * 20 + (36, 12, 1, 0)
DETAILED_PITCH = 0.2            # DICE's detailed zones: whole sectors at pitch 0.2, resolution 0.05, 6 x 6 tiles of 20 m
                                # (header rect -0.2 .. 100.22; KYNAPSE_SPEC.md 4.5). Built in a second pass (build(...,
                                # detailed=...)) from a pitch-0.2 sampling run, merged with the normal build.
CARRY_RADIUS = 1.0              # a shared node carried across a mixed-pitch seam is attached to ground within this (m)
SEAM_FINE_LINKS = False         # walk edges in a normal sector beside a mixed seam where the 0.2 m samples join ground the
                                # 0.8 m step rule does not (seam_fine_links; 2026-10-26, soldier1943.SETTINGS sets it)
SEAM_FAR_LINKS = False          # seam_far_links (2026-10-29; soldier1943.SETTINGS sets it for BF1943)
SEAM_GAP_NODES = False          # mixed-pitch seams: a shared node wherever kept ground meets the seam on both grids
                                # and no carried node joins it (seam_gap_nodes; 2026-10-24, soldier1943.SETTINGS sets
                                # it for BF1943 - the big-endian copy had it on for every build until 2026-10-31, the
                                # little-endian copy, which builds BFBC2, never)
COLUMN_SECTORS_CM = True        # a sample column's 100 m sector from whole centimetres (navsample.column_sectors: the
                                # sectors detailed.py counts stacked floors in, 2026-10-31); False: from its float
                                # position, floor((min + i x pitch) / 100), which the float32 rounding of the header's
                                # minimum and pitch puts on either side of a sector line (Wake Island: 17,172 of its
                                # 2,149,362 0.8 m columns differ). BF1943's builds keep False (soldier1943.SETTINGS) so
                                # they stay byte for byte what they were; True for them is a rebuild and an audit
DETAILED_LINK_STEP = 0.3        # LINK_STEP in detailed zones: DICE's one-cell height steps inside its pitch-0.2 areas
                                # (ONS_MP_002, 10,353 outline segments) p99 0.31, p99.9 0.42, max 0.49 m - about half its
                                # pitch-0.8 values (p99 0.96, max 1.03), not a quarter: the same slope (0.15 m) cut sloped
                                # ground into 3x DICE's areas (8,400 against 2,640; 2026-10-21)


@dataclass(frozen=True)
class Settings:
    """What a build is made for: the soldier, and the rules tuned to his game's data. The defaults are the constants
    above, NavKit's own for BFBC2 (BFBC2's soldier, rules fitted to DICE's BFBC2 path data); soldier1943.SETTINGS is
    BF1943's. build() takes one and every stage's Level carries it."""
    slope_max: float = SLOPE_MAX                    # the steepest walkable ground (deg; navwalk.walkable)
    headroom_band: tuple = navwalk.HEADROOM_BAND    # ground with less headroom is not walkable (navwalk.walkable)
    y_offset: float = Y_OFFSET                      # nodes and outlines stand this far above the ground
    detailed_link_step: float = DETAILED_LINK_STEP  # the raw rule's step in detailed zones (Grid.link_step)
    ladder_all_ground: bool = LADDER_ALL_GROUND
    border_seam_line: bool = BORDER_SEAM_LINE
    area_cliff: float = AREA_CLIFF                  # None: off
    area_cliff_reach: int = AREA_CLIFF_REACH
    join_margin: float = JOIN_MARGIN                # None: the window only
    seam_fine_links: bool = SEAM_FINE_LINKS
    seam_far_links: bool = SEAM_FAR_LINKS
    seam_gap_nodes: bool = SEAM_GAP_NODES
    mixed_overlap: float = MIXED_OVERLAP            # None: off
    column_sectors_cm: bool = COLUMN_SECTORS_CM     # False: the float sector rule (column_sectors)


BFBC2 = Settings()                                  # NavKit's defaults


class BuildError(ValueError):
    # a build that cannot be made from its inputs (e.g. no walkable ground at all)
    pass


@dataclass(frozen=True)
class Grid:
    """One stage's sample grid: pitch 0.8 (normal sectors) or DETAILED_PITCH (detailed zones), and the settings that
    follow from the pitch (grid_at)."""
    pitch: float
    sector_samples: int                             # samples per sector side: 125 / 500
    window: range                                   # local sample indices of a sector window
    tile: int                                       # samples per tile side: 50 / 100
    tiles: int                                      # tiles per sector side: 3 / 6
    resolution: float                               # the mesh header's resolution
    link_step: float                                # the raw rule's height step (LINK_STEP)

    @property
    def detailed(self):
        return abs(self.pitch - DETAILED_PITCH) < 1e-6


def grid_at(p, settings=BFBC2):
    """The Grid of a stage at pitch p: 0.8 (normal sectors) or 0.2 (detailed zones). (set_pitch until 2026-10-31,
    which rewrote this module's PITCH, WINDOW, TILE ... for whatever ran next.)"""
    detailed = abs(p - DETAILED_PITCH) < 1e-6
    sector_samples = int(round(100 / p))                  # 125 / 500
    return Grid(pitch=p, sector_samples=sector_samples,
                window=range(-1, sector_samples + 2),     # two samples of overlap with each neighbour
                tile=int(round((20 if detailed else 40) / p)),        # 100 / 50 samples
                tiles=6 if detailed else 3,
                resolution=0.05 if detailed else 0.025,
                link_step=settings.detailed_link_step if detailed else LINK_STEP)


@dataclass
class BuildState:
    """What one build gathers as it goes, across its stages (module globals until 2026-10-31, cleared by build())."""
    detailed_sectors: set = field(default_factory=set)  # the sectors built at DETAILED_PITCH: join_groups stays out of
                                                        # the other grid's
    normal_sectors: set = field(default_factory=set)    # the normal stage's sectors: sector_window grows only toward
                                                        # a sector that exists
    order_ids: set = field(default_factory=set)         # ids of the order nodes (prune_order_nodes)
    order_stats: dict = field(default_factory=dict)     # order nodes added, and those prune_order_nodes removed
    repair_stats: dict = field(default_factory=dict)    # detour-repair nodes added per pass (summed over sectors)
    ladder_stats: dict = field(default_factory=dict)    # ladder links made, and ladders whose ends lie in two sectors
    area_cells: dict = field(default_factory=dict)      # sector id -> per area (in the file's order): its cells' global
                                                        # indices i, j at its pitch (cell centre = index x pitch) and
                                                        # their ground heights - the kept surfaces the area was made of,
                                                        # exactly. Written beside the build as <name>.cells.npz
                                                        # (write_cells): the outlines alone do not give a cell's height
                                                        # (the RSNV exporter's estimate from the nearest outline point
                                                        # was 0.3 m or more off on 11 % of Coral Sea's 0.8 m cells,
                                                        # 2026-10-25)
    fine_links: list = field(default_factory=list)      # the links seam_fine_links made: (x, y, z) of both ends (log)
    far_links: list = field(default_factory=list)       # ... and seam_far_links


@dataclass(frozen=True)
class JoinScope:
    """Where join_groups may flood past the sector's own window: the sector, its stage's sectors, its window, and
    the build's detailed sectors (until 2026-10-31 JOIN_CONTEXT, set before each call)."""
    sector: tuple
    sectors: set
    window: tuple
    detailed_sectors: set
