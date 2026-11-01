"""BF1943 Navigation Studio: a BF1943 level's navigation build in 3D, with its bases, flags, spawns and ladders.

Reads a level from your copy's bundles and NavKit's .hpd build of it; shows them in a window, or with --screenshot
draws them, saves the picture and exits.

  python studio.py <level> [--hpd <file.hpd>] [--color island|sector|pitch|small-islands] [--game <folder>]
  python studio.py <level> --screenshot out.png [--frames N] [--color ...] [--island N] [--path "x,y,z;x,y,z"]
                   [--focus x,y,z [--distance m]] [--yaw rad] [--pitch rad] [--hide <layer>,...]
                   (tests and pictures; the orbit camera's angles in radians, pitch held within +-1.55: 1.5 looks
                   almost straight down)

The level comes from your copy's bundles (core level1943.py); the build is NavKit's (bf1943-auto-navkit-big-endian:
navkit.py build <level> writes work\\levels\\<level>\\navkit\\<level>.hpd, the default; nodes stand half the
soldier's 1.8 m above the ground). BF1943 ships no path data, so there is no reference file to compare with.

Layers: terrain, water, collision, the build's walkable ground (its 0.8 m cells), outlines, portals, border
portals, the route graph, ladder ground and ladder links; soldier spawns (by team), bases, flags and ladders.
Colour the ground by island (the main network is green), by sector, by pitch (detailed 0.2 m zones in orange), or
small islands (under 25 nodes in magenta). Panels: the build's figures and layers; islands (largest first, fly to
each) with the spawns and flags off the main network; the selected area or spawn; the path test (P: click the
start, then the end). The camera works as in the Map Inspector.
"""
import colorsys
import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
    # the log and error reports start before the heavy imports,
    from bfbc2core import errors                 # so a missing library is reported too
    errors.setup('navigation-studio', gui='--screenshot' not in sys.argv)

import numpy as np  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from bfbc2core import game1943, level1943, navdata, navpath, viewer  # noqa: E402

if TYPE_CHECKING:
    import numpy.typing as npt

    from bfbc2core.level import Entity, Level
    from bfbc2core.terrain import Heightfield

type Colour = tuple[float, float, float]
type Vector = npt.NDArray[np.float64]
# a spawn, base, flag or ladder: (kind, label, position, its entity)
type MapPoint = tuple[str, str, Vector, 'Entity']
# an overlay line: from, to, colour
type Segment = tuple[Vector, Vector, Colour]

NODE_HEIGHT = 0.9                                        # soldier1943: nodes half the soldier's 1.8 m up
ID_AREA = 0x200000                                       # + area index
ID_POINT = 0x100000                                      # + index into Studio.points (spawns, bases, flags)
TERRAIN_ID, WATER_ID, OBJECTS_ID, GRAPH_ID = 0xFFFFFE, 0xFFFFFD, 0xFFFFF0, 0xFFFFF1
COLOR_MODES = ['island', 'sector', 'pitch', 'small islands']
MAIN, NORMAL, DETAILED = (0.35, 0.75, 0.35), (0.30, 0.60, 0.80), (1.0, 0.60, 0.15)
DIMMED, WARNING, MISSING = (0.45, 0.48, 0.45), (0.95, 0.25, 0.85), (0.9, 0.15, 0.15)
LADDER = (0.62, 0.35, 1.0)                               # ground reached only by ladder links, and the links
PATH = (1.0, 0.85, 0.1)
NO_ISLAND = (0.15, 0.15, 0.15)                           # ground whose area has no route node: on no island
LIFT = 0.06                                              # ground cells drawn just above the surface
SMALL = 25                                               # nodes: an island this small is a pocket, not a network
POINT_LAYERS = [('soldier spawns, team 1 (US)', (0.2, 0.55, 1.0)), ('soldier spawns, team 2 (Japan)', (1.0, 0.3, 0.25)),
                ('soldier spawns, at flags', (0.85, 0.85, 0.85)), ('bases', (1.0, 0.6, 0.1)),
                ('flags', (1.0, 0.85, 0.2))]
# a soldier spawn's layer by its team (others: 'at flags')
SPAWN_LAYER: dict[int | None, str] = {1: POINT_LAYERS[0][0], 2: POINT_LAYERS[1][0]}
STATUS_FLAGS = imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_inputs | imgui.WindowFlags_.no_background


def hue(k: int, s: float = 0.65, v: float = 0.92) -> Colour:
    """Colour k of a sequence of well-spread hues (golden-ratio steps); s and v: saturation and value."""
    return colorsys.hsv_to_rgb((k * 0.618034) % 1.0, s, v)


def default_hpd(level: str) -> str:
    """NavKit's build of the level, where navkit.py writes it."""
    return os.path.join(game1943.level_work(level), 'navkit', f'{level}.hpd')


def cell_quads(xz: 'npt.NDArray[np.float64]', y: 'npt.NDArray[np.float64]', heightfield: 'Heightfield | None' = None,
               lift: float = LIFT) -> 'npt.NDArray[np.float64]':
    """Two triangles per 0.8 m cell. Cells on terrain follow it (each corner at the terrain's height), so slopes
    show as a surface rather than as steps; other cells (floors, roofs, decks) are flat at their height."""
    half = navdata.GRID / 2
    x, z = xz[:, 0], xz[:, 1]
    corners = ((-half, -half), (half, -half), (half, half), (-half, half))
    on_terrain = np.zeros(len(y), dtype=bool)
    if heightfield is not None and len(y):
        on_terrain = np.abs(heightfield.height(x, z) - y) < 0.05
    corner_points = []
    for dx, dz in corners:
        corner_y = y.copy()
        if on_terrain.any():            # only with a height field
            terrain = cast('Heightfield', heightfield)
            corner_y[on_terrain] = terrain.height(x[on_terrain] + dx, z[on_terrain] + dz)
        corner_points.append(np.stack([x + dx, corner_y + lift, z + dz], axis=1))
    c0, c1, c2, c3 = corner_points
    return np.stack([np.stack([c0, c1, c2], 1), np.stack([c0, c2, c3], 1)], axis=1).reshape(-1, 3, 3)


def box_tris(lo: 'npt.ArrayLike', hi: 'npt.ArrayLike') -> 'npt.NDArray[np.float32]':
    """The 12 triangles of the axis-aligned box from corner lo to corner hi."""
    lo, hi = np.asarray(lo, np.float32), np.asarray(hi, np.float32)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])],
                       np.float32)
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return np.array([triangle for a, b, c, d in faces
                     for triangle in (corners[[a, b, c]], corners[[a, c, d]])])


class Studio:
    """The level's scene with the build's layers and points (spawns, bases, flags, ladders), and the window's panels
    and state: selection, colour mode, path test. Each panel takes the viewer it is drawn in (`view`)."""

    triangles: int                      # set by main(), for the status bar

    def __init__(self, lvl: 'Level', nav: navdata.NavData, color: str = 'island') -> None:
        self.lvl, self.nav, self.color = lvl, nav, color
        self.scene = viewer.Scene()
        new_layer = self.scene.layer
        self.layers = {'terrain': new_layer('terrain', (0.40, 0.44, 0.36)),
                       'water': new_layer('water', (0.18, 0.42, 0.78)),
                       'collision': new_layer('collision', (0.58, 0.57, 0.55))}
        if lvl.terrain is not None:
            self.scene.add_indexed(lvl.terrain[0], lvl.terrain[1], TERRAIN_ID, self.layers['terrain'])
        if lvl.water is not None and len(lvl.water):
            self.scene.add_indexed(lvl.water.vertices, lvl.water.triangles, WATER_ID, self.layers['water'])
        for mesh in lvl.meshes:
            self.scene.add_triangles(mesh.triangles, OBJECTS_ID, self.layers['collision'])
        build_layers = (('ground', MAIN), ('outlines', (0.95, 0.95, 0.95)), ('portals', (0.3, 0.9, 1.0)),
                        ('border portals', (1.0, 0.9, 0.2)), ('route graph', (1.0, 0.4, 0.9)))
        layer = {name: new_layer(name, colour) for name, colour in build_layers}
        self.layers.update(layer)
        self.ground_batch = self.scene.add_triangles(cell_quads(nav.cell_xz, nav.cell_y, lvl.heightfield),
                                                     np.repeat(ID_AREA + nav.cell_area, 2), layer['ground'])
        drop = np.array([0, nav.y_offset - 0.12, 0])
        lines = [(k, line) for k, area in enumerate(nav.areas) for line in area.outline]
        if lines:                                    # all outline polylines at once: segments within each line
            line_points = np.concatenate([line for _, line in lines])
            owner = np.repeat(np.arange(len(lines)), [len(line) for _, line in lines])
            keep = owner[:-1] == owner[1:]
            area_of_line = np.array([k for k, _ in lines])
            self.scene.add_lines(np.stack([line_points[:-1][keep], line_points[1:][keep]], axis=1) - drop,
                                 ID_AREA + area_of_line[owner[:-1][keep]], layer['outlines'])
        for key, field in (('portals', 'portals'), ('border portals', 'borders')):
            segments = [(k, portal[0], portal[1])
                        for k, area in enumerate(nav.areas) for portal in getattr(area, field)]
            if segments:
                self.scene.add_lines(np.array([[p0, p1] for _, p0, p1 in segments]) - drop,
                                     np.array([ID_AREA + k for k, _, _ in segments]), layer[key])
        ladder_edge = nav.ladder_edge if nav.ladder_edge is not None else np.zeros(len(nav.edges), bool)
        if len(nav.edges):
            raised = nav.node_pos - np.array([0, nav.y_offset - 0.2, 0])
            edges = nav.edges[~ladder_edge]
            self.scene.add_lines(np.stack([raised[edges[:, 0]], raised[edges[:, 1]]], axis=1), GRAPH_ID,
                                 layer['route graph'])
        ladder_area = np.array([area.ladder for area in nav.areas], dtype=bool)
        if ladder_area.any() and len(nav.cell_area):
            on_ladder = ladder_area[nav.cell_area]
            self.layers['ladder ground'] = new_layer('ladder ground', LADDER)
            self.scene.add_triangles(cell_quads(nav.cell_xz[on_ladder], nav.cell_y[on_ladder], lvl.heightfield,
                                                LIFT + 0.05),
                                     np.repeat(ID_AREA + nav.cell_area[on_ladder], 2), self.layers['ladder ground'])
        if ladder_edge.any():
            self.layers['ladder links'] = new_layer('ladder links', LADDER)
            edges = nav.edges[ladder_edge]
            raised = nav.node_pos - np.array([0, nav.y_offset - 0.3, 0])
            self.scene.add_lines(np.stack([raised[edges[:, 0]], raised[edges[:, 1]]], axis=1), GRAPH_ID,
                                 self.layers['ladder links'])
        # points: soldier spawns (and their alternates), bases, flags; ladders as boxes
        for name, colour in POINT_LAYERS:
            self.layers[name] = new_layer(name, colour)
        self.points: list[MapPoint] = []             # (kind, label, position, entity)
        # level1943.load's bf1943-spawn-map objectives, by GUID
        objectives = lvl.objectives  # pyright: ignore[reportAttributeAccessIssue]  # BF1943 only, not a Level field
        roles: dict[str, dict[str, Any]] = {objective['guid']: objective for objective in objectives}
        for e in lvl.entities:
            if e.position is None:
                continue
            if e.category == 'soldier spawn':
                name = SPAWN_LAYER.get(e.team, POINT_LAYERS[2][0])
                self.add_point(name, 'spawn', e.name or e.short_type, e.position, 1.2, e)
            elif e.category in ('base', 'flag'):
                objective = roles.get(e.guid.lower(), {})
                self.add_point('bases' if e.category == 'base' else 'flags', e.category,
                               objective.get('name') or e.name or '', e.position, 2.5, e, tall=25.0)
        self.layers['ladders'] = new_layer('ladders', (0.95, 0.9, 0.35))
        for e in lvl.entities:
            if e.category == 'ladder':
                mesh = next((candidate for candidate in lvl.meshes if candidate.entity == e.index), None)
                if mesh is not None:
                    vertices = mesh.triangles.reshape(-1, 3)
                    k = len(self.points)
                    name = cast(str, e.blueprint).rsplit('/', 1)[-1]      # a ladder is a placed blueprint
                    self.points.append(('ladder', name, (vertices.min(0) + vertices.max(0)) / 2, e))
                    self.scene.add_triangles(box_tris(vertices.min(0) - 0.05, vertices.max(0) + 0.05), ID_POINT + k,
                                             self.layers['ladders'])
        self.main = 0 if nav.islands else -1         # navdata: islands largest first
        self.point_island = [self.island_at(point[2]) for point in self.points]
        self.selected: tuple[str, int] | None = None     # ('area', area index) or ('point', index into points)
        # the path test (P): on or off, its clicked points (start, end) and the route between them
        self.path_mode = False
        self.path_pts: list[Vector] = []
        self.route: navpath.Route | None = None
        self.apply_colors()

    def add_point(self, layer: str, kind: str, label: str, pos: 'npt.ArrayLike', size: float, ent: 'Entity',
                  tall: float = 0.0) -> None:
        """A spawn, base or flag: a box of size m (or a pillar tall m high) in layer, pickable as ID_POINT + index."""
        k = len(self.points)
        position = np.asarray(pos, dtype=np.float64)
        self.points.append((kind, label, position, ent))
        if tall:
            self.scene.add_triangles(box_tris((position[0] - 0.6, position[1], position[2] - 0.6),
                                              (position[0] + 0.6, position[1] + tall, position[2] + 0.6)),
                                     ID_POINT + k, self.layers[layer])
        else:
            self.scene.add_box(np.asarray(pos) + np.array([0, size / 2, 0]), size, ID_POINT + k, self.layers[layer])

    def island_at(self, point: Vector) -> tuple[int | None, float]:
        """(island index or None, route node distance): the nearest node within navpath's join limits."""
        node, distance = navpath.join(self.nav, point)
        return (None, distance) if node is None else (int(self.nav.node_island[node]), distance)

    # --- colours ------------------------------------------------------------------------------------
    def cell_colors(self) -> 'npt.NDArray[np.float64]':
        """(cells, 3) RGB of each ground cell in the colour mode (self.color)."""
        nav = self.nav
        cell_area = nav.cell_area
        island = np.array([area.island for area in nav.areas])[cell_area] if len(cell_area) else np.zeros(0, int)
        if self.color == 'sector':
            sector = np.array([area.sector for area in nav.areas])[cell_area]
            unique, inverse = np.unique(sector, return_inverse=True)
            colours = np.array([hue(k * 7 + 3, 0.5, 0.85) for k in range(len(unique))]).reshape(-1, 3)
            return colours[inverse.ravel()]
        if self.color == 'pitch':
            detailed = np.array([area.detailed for area in nav.areas], dtype=bool)[cell_area]
            return np.where(detailed[:, None], np.array(DETAILED), np.array(NORMAL)).reshape(-1, 3)
        # an area with no route node in its sector has no island (navdata: island -1): NO_ISLAND, never the main
        # network's green - that is the ground no route reaches
        none = (island < 0) | (island >= max(len(nav.islands), 1))
        if self.color == 'small islands':
            size = np.array([i.nodes for i in nav.islands] or [0])
            nodes = size[np.clip(island, 0, len(size) - 1)]
            colours = np.where((nodes >= SMALL)[:, None], np.array(DIMMED), np.array(WARNING)).reshape(-1, 3)
            colours[island == 0] = MAIN
            colours[none] = NO_ISLAND
            return colours
        table = np.array([MAIN] + [hue(k) for k in range(1, max(len(nav.islands), 1))]).reshape(-1, 3)
        colours = table[np.clip(island, 0, len(table) - 1)].reshape(-1, 3)
        colours[none] = NO_ISLAND
        return colours

    def apply_colors(self, view: viewer.Viewer | None = None) -> None:
        """The ground's colours in the viewer, or in the scene before the viewer has it."""
        colours = np.repeat(self.cell_colors(), 2, axis=0)
        if view is not None:
            view.set_colors(self.ground_batch, colours)
        else:
            vertices, tags, _ = self.scene.batches[self.ground_batch]
            self.scene.batches[self.ground_batch] = (vertices, tags,
                                                     viewer.triangle_colors(colours, len(vertices) // 3))

    # --- path test (P) ------------------------------------------------------------------------------
    def set_path_mode(self, view: viewer.Viewer, on: bool) -> None:
        self.path_mode = on
        view.select_on_click = not on

    def path_point(self, view: viewer.Viewer, clicked: 'npt.ArrayLike') -> None:
        """A click in path mode: the start, then the end (a third click starts again)."""
        points = self.path_pts
        if len(points) >= 2:
            points.clear()
        points.append(np.asarray(clicked, dtype=np.float64))
        self.route = navpath.route(self.nav, points[0], points[1]) if len(points) == 2 else None
        segments: list[Segment] = []
        for point in points:
            for arm in ((0.6, 0, 0), (0, 1.5, 0), (0, 0, 0.6)):
                segments.append((point - np.array(arm), point + np.array(arm), (1.0, 1.0, 1.0)))
        route = self.route
        if route is not None:
            ground = navpath.ground_nodes(self.nav) + np.array([0, 0.35, 0])
            if route.found:
                chain = ground[route.nodes]
                segments += [(chain[k], chain[k + 1], PATH) for k in range(len(chain) - 1)]
                segments += [(points[0], chain[0], PATH), (chain[-1], points[1], PATH)]
            elif route.gap:
                gap_start, gap_end = ground[route.gap[0]], ground[route.gap[1]]
                up = np.array([0, 3, 0])
                segments += [(gap_start, gap_end, MISSING), (gap_start, gap_start + up, MISSING),
                             (gap_end, gap_end + up, MISSING)]
        view.set_overlay(segments)  # pyright: ignore[reportArgumentType]  # numpy points; set_overlay only keeps them

    def panel_path(self, view: viewer.Viewer) -> None:
        if not self.path_mode and self.route is None:
            return
        imgui.set_next_window_pos((380, 10), imgui.Cond_.first_use_ever)
        imgui.begin('Path test', flags=imgui.WindowFlags_.always_auto_resize)
        changed, on = imgui.checkbox('path test (P): click the start, then the end', self.path_mode)
        if changed:
            self.set_path_mode(view, on)
        if imgui.small_button('clear##path'):
            self.path_pts, self.route = [], None
            view.set_overlay([])
        for label, point in zip(('start', 'end'), self.path_pts):
            imgui.text(f'{label}: {point[0]:.1f}, {point[1]:.1f}, {point[2]:.1f}')
        route = self.route
        if route is not None:
            if route.found:
                imgui.text(f'route: cost {route.cost:.1f} m over {len(route.nodes)} nodes '
                           f'(length {route.length:.1f} m, straight {route.straight:.1f} m, '
                           f'x{route.cost / max(route.straight, 1e-9):.2f})')
            else:
                imgui.text(f'no route: {route.reason}')
                if route.gap and imgui.small_button('fly to the gap'):
                    view.camera.focus((self.nav.node_pos[route.gap[0]] + self.nav.node_pos[route.gap[1]]) / 2, 40.0)
        imgui.text_disabled('Over the route graph only; a runtime also smooths the route through the areas.')
        imgui.end()

    def on_pick(self, view: viewer.Viewer) -> Callable[[int | None], None]:
        """The viewer's click handler: a path test point in path mode, else the clicked area or point selected."""
        def handler(object_id: int | None) -> None:
            if self.path_mode:
                if view.pick_point is not None:
                    self.path_point(view, view.pick_point)
                return
            self.selected = None
            if object_id is not None and ID_AREA <= object_id < ID_AREA + len(self.nav.areas):
                self.selected = ('area', object_id - ID_AREA)
            elif object_id is not None and ID_POINT <= object_id < ID_POINT + len(self.points):
                self.selected = ('point', object_id - ID_POINT)
        return handler

    # --- panels -------------------------------------------------------------------------------------
    def panel_main(self, view: viewer.Viewer) -> None:
        imgui.set_next_window_pos((10, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((360, 640), imgui.Cond_.first_use_ever)
        imgui.begin('Navigation')
        imgui.text(f'{self.lvl.name}')
        summary = navdata.summary(self.nav)
        imgui.text_disabled(os.path.basename(self.nav.path))
        imgui.text(f'  {summary["areas"]:,} areas ({summary["detailed areas"]:,} detailed), {summary["cells"]:,} cells')
        imgui.text(f'  {summary["nodes"]:,} nodes, {summary["edges"]:,} edges, {summary["islands"]} islands '
                   f'({summary["large islands (25+ nodes)"]} large)')
        if summary['ladder links']:
            imgui.text(f'  {summary["ladder links"]} ladder links, {summary["ladder ground cells"]:,} cells of '
                       f'ladder ground')
        imgui.separator()
        imgui.text('colour the ground by')
        for mode in COLOR_MODES:
            imgui.same_line()
            if imgui.radio_button(mode, self.color == mode):
                self.color = mode
                self.apply_colors(view)
        imgui.separator()
        for name, layer in self.layers.items():
            changed, on = imgui.checkbox(name, view.layer_visible(layer))
            if changed:
                view.set_layer_visible(layer, on)
        imgui.end()

    def panel_islands(self, view: viewer.Viewer) -> None:
        nav = self.nav
        width = imgui.get_io().display_size.x
        imgui.set_next_window_pos((width - 440, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((430, 440), imgui.Cond_.first_use_ever)
        imgui.begin('Islands, spawns and flags')
        off = [(k, point, island) for k, (point, island) in enumerate(zip(self.points, self.point_island))
               if point[0] in ('spawn', 'flag', 'base') and island[0] != self.main]
        count = sum(1 for point in self.points if point[0] in ('spawn', 'flag', 'base'))
        imgui.text(f'{count} spawns, bases and flags; {len(off)} not on the main network')
        imgui.text_disabled('(the carriers are their own networks: the sea divides them)')
        if off and imgui.tree_node('off the main network'):
            for k, (kind, label, pos, _), (island, _distance) in off[:150]:
                if island is not None:
                    where = f'island {island} ({nav.islands[island].nodes} nodes)'
                else:
                    where = 'no route node within 15 m'
                if imgui.small_button(f'fly##pt{k}'):
                    view.camera.focus(pos, 30.0)
                imgui.same_line()
                imgui.text(f'{kind} {label}: {where}')
            imgui.tree_pop()
        imgui.separator()
        imgui.text(f'{len(nav.islands)} islands (largest first; island 0 is the main network)')
        imgui.begin_child('islands', (0, 0))
        for k, island in enumerate(nav.islands[:300]):
            if imgui.small_button(f'fly##is{k}'):
                view.camera.focus(island.centre - np.array([0, nav.y_offset, 0]), 40.0 if k else 300.0)
            imgui.same_line()
            imgui.text(f'{k}: {island.nodes:,} nodes, {island.cells:,} cells')
        imgui.end_child()
        imgui.end()

    def panel_selection(self, view: viewer.Viewer) -> None:
        if self.selected is None:
            return
        io = imgui.get_io()
        imgui.set_next_window_pos((io.display_size.x - 440, 460), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((430, 220), imgui.Cond_.first_use_ever)
        imgui.begin('Selection')
        kind, k = self.selected
        if kind == 'point':
            point_kind, label, pos, ent = self.points[k]
            imgui.text(f'{point_kind} {label}')
            imgui.text_disabled(f'{ent.short_type} {ent.guid}')
            imgui.text(f'at {pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}' + (f', team {ent.team}' if ent.team else ''))
            island, distance = self.point_island[k]
            imgui.text('no route node within 15 m' if island is None else
                       f'island {island}' + (' (main network)' if island == self.main else '')
                       + f', route node {distance:.1f} m away')
        else:
            area = self.nav.areas[k]
            island = self.nav.islands[area.island] if 0 <= area.island < len(self.nav.islands) else None
            imgui.text(f'area {k}: sector 0x{area.sector:04X}, area {area.index}, tile {area.tile[0]},{area.tile[1]}, '
                       f'layer {area.layer}')
            imgui.text(f'pitch {area.pitch:.2f} m{" (detailed zone)" if area.detailed else ""}, {area.cells:,} cells, '
                       f'height {area.min_y:.1f} - {area.max_y:.1f} m')
            if area.ladder:
                imgui.text_colored(imgui.ImVec4(*LADDER, 1.0), 'ladder ground: reached only through a ladder link')
            imgui.text(f'{len(area.outline)} outline lines, {len(area.portals)} portals, '
                       f'{len(area.borders)} border portals')
            if island is not None:
                imgui.text(f'island {area.island}{" (main network)" if area.island == 0 else ""}: '
                           f'{island.nodes:,} nodes, {island.cells:,} cells')
        imgui.end()

    def panel_status(self, view: viewer.Viewer) -> None:
        io = imgui.get_io()
        if not io.want_capture_keyboard and imgui.is_key_pressed(imgui.Key.p):
            self.set_path_mode(view, not self.path_mode)
        if not io.want_capture_mouse and view.hovered != viewer.NO_ID:
            if ID_AREA <= view.hovered < ID_AREA + len(self.nav.areas):
                area = self.nav.areas[view.hovered - ID_AREA]
                imgui.set_tooltip(f'area {view.hovered - ID_AREA} - sector 0x{area.sector:04X}, island {area.island}, '
                                  f'pitch {area.pitch:.1f}')
            elif ID_POINT <= view.hovered < ID_POINT + len(self.points):
                point_kind, label, _, _ = self.points[view.hovered - ID_POINT]
                imgui.set_tooltip(f'{point_kind} {label}')
        imgui.set_next_window_pos((10, io.display_size.y - 40), imgui.Cond_.always)
        imgui.set_next_window_size((io.display_size.x - 20, 30), imgui.Cond_.always)
        imgui.begin('##status', flags=STATUS_FLAGS)
        imgui.text(f'{view.fps:5.1f} fps   {self.triangles:,} triangles   {view.renderer}   colour: {self.color}   '
                   f'camera: {"fly" if view.camera.fly else "orbit"} (F)   P: path test')
        imgui.end()


def main(argv: list[str]) -> int:
    opts: dict[str, str] = {}           # --option -> its value
    args: list[str] = []
    k = 1
    while k < len(argv):
        if argv[k].startswith('--'):
            if k + 1 >= len(argv):
                print(f'error: {argv[k]} needs a value')
                return 2
            opts[argv[k]] = argv[k + 1]
            k += 2
        else:
            args.append(argv[k])
            k += 1
    try:                                                    # bad options are said before the level loads
        checked = check_options(opts)
    except ValueError as e:
        print('error:', e)
        return 2
    if not args:                                            # started without a level: ask for one
        from bfbc2core import pick
        picked = pick.pick_level('BF1943 Navigation Studio')
        if not picked:
            return 1
        args = [picked]
    lvl = level1943.load(args[0], opts.get('--game'))
    hpd = opts.get('--hpd', default_hpd(lvl.lv))
    if not os.path.isfile(hpd):
        print(f'no navigation build for {lvl.lv} ({hpd}): build it with bf1943-auto-navkit-big-endian '
              f'(python navkit.py all {lvl.lv}), or pass --hpd <file>')
        return 1
    print(f'navigation: {hpd}')
    nav = navdata.load(hpd, lvl.heightfield, y_offset=NODE_HEIGHT)
    studio = Studio(lvl, nav, checked['color'])
    studio.triangles = studio.scene.triangle_count()
    panels = [studio.panel_main, studio.panel_islands, studio.panel_selection, studio.panel_path, studio.panel_status]
    view = viewer.Viewer(studio.scene, f'BF1943 Navigation Studio - {lvl.name}', panels=panels)
    view.on_pick = studio.on_pick(view)
    hide = set(opts.get('--hide', 'collision,outlines,portals,border portals,route graph').split(','))
    for name, layer in studio.layers.items():
        if name in hide:
            view.set_layer_visible(layer, False)
    if checked['island'] is not None and checked['island'] < len(nav.islands):
        view.camera.focus(nav.islands[checked['island']].centre, 60.0)
    if checked['path'] is not None:                         # tests: a path test between two points
        studio.set_path_mode(view, True)
        for point in checked['path']:
            studio.path_point(view, point)
        route = cast(navpath.Route, studio.route)           # two points given: a route was searched
        print('path: ' + (f'cost {route.cost:.1f} m, {len(route.nodes)} nodes' if route.found
                          else f'none - {route.reason}'))
    if checked['focus'] is not None:                        # tests: the camera on a point
        view.camera.focus(np.array(checked['focus']), checked['distance'])
    if checked['yaw'] is not None:                          # the orbit camera's angles (radians; pitch 1.5 looks down)
        view.camera.yaw = checked['yaw']
    if checked['pitch'] is not None:
        view.camera.pitch = max(-1.55, min(1.55, checked['pitch']))
    if '--screenshot' in opts:
        view.exit_after = checked['frames']
    view.run()
    if '--screenshot' in opts:
        return save_screenshot(opts['--screenshot'], view.screenshot)
    return 0


def check_options(opts: dict[str, str]) -> dict[str, Any]:
    """The options' values, checked before the level loads: {'color', 'island', 'frames', 'path', 'focus', 'distance',
    'yaw', 'pitch'} (None where not given); ValueError naming the option otherwise."""
    def number(key: str, default: float | None = None) -> float | None:
        if key not in opts:
            return default
        try:
            value = float(opts[key])
        except ValueError:
            value = float('nan')
        if not np.isfinite(value):
            raise ValueError(f'{key} {opts[key]!r}: give a number')
        return value

    def whole(key: str, default: int | None = None, low: int = 0) -> int | None:
        if key not in opts:
            return default
        try:
            n = int(opts[key])
        except ValueError:
            n = low - 1
        if n < low:
            raise ValueError(f'{key} {opts[key]!r}: give a whole number ({low} or more)')
        return n
    color = opts.get('--color', 'island').replace('-', ' ')   # small-islands: the 'small islands' mode
    if color not in COLOR_MODES:
        modes = ', '.join(mode.replace(' ', '-') for mode in COLOR_MODES)
        raise ValueError(f'--color {opts["--color"]!r}: give one of {modes}')
    focus = None
    if '--focus' in opts:
        focus = points(opts['--focus'], 1, '--focus', '"x,y,z"')[0]
    return {'color': color, 'island': whole('--island'), 'frames': whole('--frames', 40, 1),
            'path': path_points(opts['--path']) if '--path' in opts else None, 'focus': focus,
            'distance': number('--distance', 45.0), 'yaw': number('--yaw'), 'pitch': number('--pitch')}


def points(text: str, count: int, key: str, form: str) -> list[list[float]]:
    """count points "x,y,z" separated by ";", all finite; ValueError otherwise."""
    try:
        parsed = [[float(coordinate) for coordinate in point.split(',')] for point in text.split(';') if point.strip()]
    except ValueError:
        parsed = []
    if len(parsed) != count or any(len(point) != 3 or not all(np.isfinite(point)) for point in parsed):
        raise ValueError(f'{key} {text!r}: give {form}')
    return parsed


def path_points(text: str) -> list[list[float]]:
    """--path "x,y,z;x,y,z": exactly two points (the start and the end); ValueError otherwise."""
    return points(text, 2, '--path', 'two points, "x,y,z;x,y,z"')


def save_screenshot(path: str, shot: 'npt.ArrayLike | None') -> int:
    """Writes the viewer's screenshot to path: exit code 0, or 1 when the viewer took none (nothing written)."""
    if shot is None:
        print(f'error: no screenshot was taken - {path} not written')
        return 1
    import matplotlib.pyplot as plt
    plt.imsave(path, np.asarray(shot))
    print('screenshot:', path)
    return 0


if __name__ == '__main__':
    # a crash: logged, explained in a window, exit code 1 (errors: imported by the bootstrap above, under the same
    # condition)
    with errors.guard():  # pyright: ignore[reportPossiblyUnboundVariable]
        sys.exit(main(sys.argv))
