"""BF1943 Map Inspector: browse a BF1943 level in 3D and inspect every object's source and properties.

Reads a level from your copy's bundles (core level1943.py) and shows it in a window; --screenshot draws it, saves
the picture and exits.

  python inspector.py <level>                 e.g. inspector.py wake_island_s (read from your copy's bundles,
                                              core level1943.py; BF1943_GAME selects the game folder)
  python inspector.py <level> --screenshot out.png [--select <guid>] [--frames N]   draw, save, exit (tests)
                     [--measure "x,y,z;x,y,z;..."]   with measure points (tests)

Click an object to select it, double-click to fly the camera to it. Left drag rotates, right or middle drag
pans, the wheel zooms; F switches to fly mode (hold the right button, W A S D, Q / E, Shift faster).
M switches measuring on and off: clicks then add points (distance, height difference); Esc clears them.
The selection's links are drawn over the scene: yellow to what it references, cyan from what references it.
"""
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE)
    # the log and error reports start before the heavy imports,
    from bfbc2core import errors                 # so a missing library is reported too
    errors.setup('map-inspector', gui='--screenshot' not in sys.argv)

import numpy as np  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from bfbc2core import dbx, level1943 as levelmod, viewer  # noqa: E402

if TYPE_CHECKING:
    import numpy.typing as npt

    from bfbc2core.level import Entity, Level

type Colour = tuple[float, float, float]
type Point = npt.NDArray[np.float64]
# an overlay line: from, to, colour
type Segment = tuple[Point, Point, Colour]

TERRAIN_ID = 0xFFFFFE
WATER_ID = 0xFFFF00          # + the index of the triangle's shader in Inspector.water_shaders
BLUE, RED, GREY = (0.20, 0.55, 1.00), (1.00, 0.30, 0.25), (0.70, 0.70, 0.75)
# layer: colour, marker size for entities without collision (None: not drawn without collision)
LAYERS: list[tuple[str, Colour, float | None]] = [
    ('terrain', (0.42, 0.47, 0.36), None),
    ('water', (0.18, 0.42, 0.78), None),
    ('placed object', (0.62, 0.60, 0.56), 1.0),
    ('destructible', (0.80, 0.64, 0.46), 1.0),
    ('dynamic object', (0.62, 0.48, 0.34), 0.6),
    ('ladder', (0.95, 0.90, 0.35), 1.0),
    ('group member', (0.55, 0.53, 0.50), None),
    ('soldier spawn, team 1', BLUE, 1.2),
    ('soldier spawn, team 2', RED, 1.2),
    ('soldier spawn, no team', GREY, 1.2),
    ('vehicle spawn, team 1', (0.35, 0.80, 1.00), 2.5),
    ('vehicle spawn, team 2', (1.00, 0.58, 0.35), 2.5),
    ('vehicle spawn, neutral', (0.75, 0.85, 0.80), 2.5),
    ('end-of-round spawn', (0.85, 0.85, 0.55), 0.8),
    ('base', (1.00, 0.60, 0.10), 3.0),
    ('flag', (1.00, 0.85, 0.20), 2.5),
    ('combat area', (0.85, 0.25, 0.85), 1.5),
    ('dmz', (0.30, 0.85, 0.65), 1.5),
    ('map marker', (0.80, 0.80, 0.95), 1.0),
    ('area point', (0.70, 0.70, 0.95), 0.6),
    ('other', (0.60, 0.60, 0.60), 0.6),
]
HIDDEN_AT_START = {'area point', 'other', 'end-of-round spawn', 'map marker'}
# BF1943: Team1 holds the Allies' (US) base, Team2 the Axis' (Japanese) one
TEAM_NAME = {1: 'team 1 (US)', 2: 'team 2 (Japan)', 0: 'neutral', None: 'no team'}
REF_TO, REF_FROM, MEASURE = (1.0, 0.9, 0.2), (0.3, 0.95, 1.0), (1.0, 1.0, 1.0)
# a spawn's layer by its team (layer_name)
SOLDIER_SPAWN_LAYER: dict[int | None, str] = {1: 'soldier spawn, team 1', 2: 'soldier spawn, team 2'}
VEHICLE_SPAWN_LAYER: dict[int | None, str] = {1: 'vehicle spawn, team 1', 2: 'vehicle spawn, team 2'}


def layer_name(entity: 'Entity') -> str:
    """The layer an entity is drawn in: its category, spawns split by team."""
    if entity.category == 'soldier spawn':
        return SOLDIER_SPAWN_LAYER.get(entity.team, 'soldier spawn, no team')
    if entity.category == 'vehicle spawn':
        return VEHICLE_SPAWN_LAYER.get(entity.team, 'vehicle spawn, neutral')
    return entity.category


class Inspector:
    """The level's scene (a layer per LAYERS entry), the links between its objects, and the window's panels and
    state: selection, search, measuring. Each panel takes the viewer it is drawn in (`view`)."""

    scene_triangles: int                # set by main(), for the status bar

    def __init__(self, lvl: 'Level') -> None:
        self.lvl = lvl
        self.scene = viewer.Scene()
        self.layer_index: dict[str, int] = {}
        for name, colour, _ in LAYERS:
            self.layer_index[name] = self.scene.layer(name, colour)
        sizes = {name: size for name, _, size in LAYERS}
        self.entity_layer = [self.layer_index.get(layer_name(e), self.layer_index['other']) for e in lvl.entities]
        with_mesh: set[int] = set()
        for mesh in lvl.meshes:
            self.scene.add_triangles(mesh.triangles, mesh.entity, self.entity_layer[mesh.entity])
            with_mesh.add(mesh.entity)
        for e in lvl.entities:
            if e.index in with_mesh or e.position is None:
                continue
            size = sizes.get(layer_name(e), 0.6)
            if size:
                layer = self.entity_layer[e.index]
                centre = e.position + np.array([0, size / 2, 0])
                self.scene.add_box(centre, size, e.index, layer)
                if e.category in ('soldier spawn', 'vehicle spawn'):        # facing: the transform's forward row
                    forward = cast('npt.NDArray[np.float64]', e.matrix)[2, :3]    # positioned: it has a matrix
                    self.scene.add_lines([[centre, centre + forward * (size * 2.5)]], e.index, layer)
        for shape in lvl.shapes:                                           # outlines, in their owner's layer
            layer = self.entity_layer[shape.entity]
            self.scene.add_polyline(shape.points, shape.entity, layer, closed=shape.closed)
            if shape.height > 0:
                top = shape.points + np.array([0, shape.height, 0])
                self.scene.add_polyline(top, shape.entity, layer, closed=shape.closed)
                self.scene.add_lines(np.stack([shape.points, top], axis=1), shape.entity, layer)
        if lvl.terrain is not None:
            self.scene.add_indexed(lvl.terrain[0], lvl.terrain[1], TERRAIN_ID, self.layer_index['terrain'])
        self.water_shaders: list[str] = []
        water = lvl.water
        if water is not None and len(water):
            shader = np.array(water.shader if water.shader else [''] * len(water))
            for name in dict.fromkeys(shader):                             # one id per shader, for the tooltip
                shader_index = len(self.water_shaders)
                self.water_shaders.append(name)
                self.scene.add_indexed(water.vertices, water.triangles[shader == name], WATER_ID + shader_index,
                                       self.layer_index['water'])
        self.counts: dict[str, int] = {}
        for e in lvl.entities:
            self.counts[layer_name(e)] = self.counts.get(layer_name(e), 0) + 1
        self.guids = {e.guid.lower(): e for e in lvl.entities}
        self.referrers: 'dict[int, list[tuple[str, Entity]]]' = {}         # entity index -> [(field, entity)]
        for e in lvl.entities:
            if e.short_type == 'LevelData':                                # lists every object; not a link
                continue
            for path, ref in e.refs():
                target = self.guids.get(ref.lower())
                if target is not None and target is not e:
                    self.referrers.setdefault(target.index, []).append((path, e))
        types: dict[str, int] = {}
        for e in lvl.entities:
            types[e.short_type] = types.get(e.short_type, 0) + 1
        self.types = sorted(types.items(), key=lambda item: -item[1])
        self.type_filter = ''
        self.selected: 'Entity | None' = None
        self.search = ''
        self.results: 'list[Entity]' = []
        self.history: 'list[Entity | None]' = []
        self.measuring = False
        self.measure: list[Point] = []                                     # world points

    # --- selection ----------------------------------------------------------------------------------
    def select(self, view: viewer.Viewer, entity: 'Entity | None', focus: bool = False) -> None:
        self.selected = entity
        view.selected = entity.index if entity is not None else viewer.NO_ID
        if entity is not None and focus and entity.position is not None:
            view.camera.focus(entity.position, 40.0)
        self.update_overlay(view)

    def links(self, e: 'Entity | None') -> list[Segment]:
        """Segments to the positioned entities the selection references (yellow) and that reference it (cyan)."""
        out: list[Segment] = []
        if e is None or e.position is None:
            return out
        for _, ref in e.refs():
            target = self.guids.get(ref.lower())
            if target is not None and target is not e and target.position is not None:
                out.append((e.position, target.position, REF_TO))
        for _, referrer in self.referrers.get(e.index, ()):
            if referrer.position is not None:
                out.append((referrer.position, e.position, REF_FROM))
        return out

    def update_overlay(self, view: viewer.Viewer) -> None:
        segments = self.links(self.selected)
        for start, end in zip(self.measure, self.measure[1:]):
            segments.append((start, end, MEASURE))
        for point in self.measure:                                         # small crosses at measure points
            for arm in ((0.4, 0, 0), (0, 0.4, 0), (0, 0, 0.4)):
                segments.append((point - np.array(arm), point + np.array(arm), MEASURE))
        view.set_overlay(segments)  # pyright: ignore[reportArgumentType]  # numpy points; set_overlay only keeps them

    def on_pick(self, view: viewer.Viewer) -> Callable[[int | None], None]:
        """The viewer's click handler: a measure point while measuring, else the clicked object selected."""
        def handler(object_id: int | None) -> None:
            if self.measuring:
                if view.pick_point is not None:
                    self.measure.append(np.asarray(view.pick_point, dtype=np.float64))
                self.update_overlay(view)
                return
            if object_id is None or object_id >= len(self.lvl.entities):   # sky, terrain, water
                self.select(view, None)
            else:
                self.select(view, self.lvl.entities[object_id])
        return handler

    def set_measuring(self, view: viewer.Viewer, on: bool) -> None:
        self.measuring = on
        view.select_on_click = not on

    def keys(self, view: viewer.Viewer) -> None:
        if imgui.get_io().want_capture_keyboard:
            return
        if imgui.is_key_pressed(imgui.Key.m):
            self.set_measuring(view, not self.measuring)
        if imgui.is_key_pressed(imgui.Key.escape) and self.measure:
            self.measure = []
            self.update_overlay(view)

    def follow(self, view: viewer.Viewer, target: 'Entity') -> None:
        self.history.append(self.selected)
        self.select(view, target, focus=True)

    # --- panels -------------------------------------------------------------------------------------
    def panel_layers(self, view: viewer.Viewer) -> None:
        self.keys(view)
        imgui.set_next_window_pos((10, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((320, 640), imgui.Cond_.first_use_ever)
        imgui.begin('Layers and search')
        imgui.text(f'{self.lvl.name}: {len(self.lvl.entities)} entities')
        imgui.separator()
        for name, colour, _ in LAYERS:
            n = self.counts.get(name, 1 if name == 'terrain' else 0)
            if name == 'water':
                n = len(self.lvl.water) if self.lvl.water is not None else 0
            if not n:
                continue
            layer = self.layer_index[name]
            imgui.color_button(f'##c{name}', imgui.ImVec4(*colour, 1.0), 0, (14, 14))
            imgui.same_line()
            changed, on = imgui.checkbox(f'{name} ({n})', view.layer_visible(layer))
            if changed:
                view.set_layer_visible(layer, on)
        imgui.separator()
        changed_search, self.search = imgui.input_text('search', self.search)
        changed_type = False
        if imgui.begin_combo('type', self.type_filter or 'any type'):
            if imgui.selectable('any type', not self.type_filter)[0]:
                self.type_filter, changed_type = '', True
            for type_name, n in self.types:
                if imgui.selectable(f'{type_name} ({n})', self.type_filter == type_name)[0]:
                    self.type_filter, changed_type = type_name, True
            imgui.end_combo()
        if changed_search or changed_type:
            self.results = self.matches(self.search.lower().strip())
        imgui.text_disabled(f'{len(self.results)} shown - GUID, type, id, name or blueprint')
        imgui.begin_child('results', (0, 0))
        for e in self.results:
            label = f'{e.short_type}  {e.name or e.id or e.blueprint or ""}##{e.index}'
            if imgui.selectable(label, self.selected is e)[0]:
                self.select(view, e, focus=True)
        imgui.end_child()
        imgui.end()

    def matches(self, query: str) -> list['Entity']:
        """The first 500 entities of the chosen type whose GUID, type, id, name or blueprint holds query (2 characters
        at least; a shorter one lists the type's entities, or none without a type)."""
        pool = [e for e in self.lvl.entities if not self.type_filter or e.short_type == self.type_filter]
        if len(query) >= 2:
            pool = [e for e in pool
                    if query in ' '.join((e.guid, e.type, e.id or '', e.name or '', e.blueprint or '')).lower()]
        elif not self.type_filter:
            pool = []
        return pool[:500]

    def tree(self, view: viewer.Viewer, element: dbx.Element, path: str) -> None:
        """Property tree of an element: containers expand, values show inline, local references are links."""
        for k, child in enumerate(element.children or ()):
            name = child.attr('name') or child.tag
            ref = child.attr('ref')
            if child.children is not None:
                label = f'{name}  [{len(child.children)}]##{path}/{k}'
                if imgui.tree_node(label):
                    self.tree(view, child, f'{path}/{k}')
                    imgui.tree_pop()
            elif ref is not None:
                target = self.guids.get(ref.lower())
                if target is not None:
                    if imgui.small_button(f'-> {name}##{path}/{k}'):
                        self.follow(view, target)
                    imgui.same_line()
                    imgui.text_disabled(f'{target.short_type} {target.name or target.id or ""}')
                else:
                    imgui.text(f'{name} -> {ref}')
            else:
                value = dbx.value(child)
                imgui.text(f'{name} = {value if len(value) < 140 else value[:140] + " ..."}')

    def panel_selection(self, view: viewer.Viewer) -> None:
        width = imgui.get_io().display_size.x
        imgui.set_next_window_pos((width - 480, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((470, 720), imgui.Cond_.first_use_ever)
        imgui.begin('Selection')
        e = self.selected
        if e is None:
            imgui.text_disabled('Click an object (double-click: fly to it).')
        else:
            if self.history and imgui.small_button('< back'):
                self.select(view, self.history.pop(), focus=True)
            imgui.text(e.short_type)
            imgui.text_disabled(e.type)
            facts = [('guid', e.guid), ('id', e.id), ('name', e.name), ('category', e.category),
                     ('blueprint', e.blueprint), ('blueprint object', e.blueprint_type),
                     ('source', e.partition + (f'  ({e.group})' if e.group else ''))]
            if e.team is not None:
                facts.append(('team', TEAM_NAME[e.team]))
            if e.ai is not None:
                facts.append(('player type', 'AI (PlayerType is not SSHumanPlayer)' if e.ai else 'human player'))
            if e.destructible:
                facts.append(('destructible', 'yes (health states / breakable parts)'))
            for label, value in facts:
                if value:
                    imgui.text(f'{label}: {value}')
            if e.position is not None:
                position = e.position
                imgui.text(f'position: {position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}')
            if imgui.button('fly to'):
                self.select(view, e, focus=True)
            imgui.same_line()
            if imgui.button('copy guid'):
                imgui.set_clipboard_text(e.guid)
            refs = self.referrers.get(e.index, [])
            if refs and imgui.tree_node(f'referenced by [{len(refs)}]##refby'):
                for k, (field, referrer) in enumerate(refs[:200]):
                    if imgui.small_button(f'<- {referrer.short_type} {referrer.name or referrer.id or ""}##rb{k}'):
                        self.follow(view, referrer)
                    imgui.same_line()
                    imgui.text_disabled(field)
                imgui.tree_pop()
            imgui.separator()
            imgui.begin_child('props', (0, 0))
            self.tree(view, e.element, str(e.index))
            imgui.end_child()
        imgui.end()

    def panel_measure(self, view: viewer.Viewer) -> None:
        if not self.measuring and not self.measure:
            return
        imgui.set_next_window_pos((340, 10), imgui.Cond_.first_use_ever)
        imgui.begin('Measure', flags=imgui.WindowFlags_.always_auto_resize)
        changed, on = imgui.checkbox('measuring (M)', self.measuring)
        if changed:
            self.set_measuring(view, on)
        imgui.same_line()
        if imgui.small_button('clear (Esc)'):
            self.measure = []
            self.update_overlay(view)
        points = self.measure
        if not points:
            imgui.text_disabled('Click points on the terrain or objects.')
        else:
            last = points[-1]
            imgui.text(f'{len(points)} points; last at {last[0]:.2f}, {last[1]:.2f}, {last[2]:.2f}')
            if self.lvl.heightfield is not None:
                above = last[1] - float(self.lvl.heightfield.height(last[0], last[2]))
                imgui.text(f'last point above terrain: {above:+.2f} m')
            depth = self.water_above(last)
            if depth is not None:
                imgui.text(f'water surface above it: {depth:+.2f} m')
            if len(points) >= 2:
                step = points[-1] - points[-2]
                flat = float(np.hypot(step[0], step[2]))
                imgui.text(f'last segment: {np.linalg.norm(step):.2f} m')
                imgui.text(f'  horizontal {flat:.2f} m, height {step[1]:+.2f} m, '
                           f'slope {np.degrees(np.arctan2(step[1], max(flat, 1e-9))):+.1f} deg')
                total = sum(float(np.linalg.norm(end - start)) for start, end in zip(points, points[1:]))
                imgui.text(f'path total: {total:.2f} m')
        imgui.end()

    def water_above(self, p: Point) -> float | None:
        """Height of the water surface over point p minus p's height, or None where there is no water."""
        water = self.lvl.water
        if water is None or not len(water):
            return None
        # barycentric coordinates (l1, l2, l3) of p in each water triangle (a, b, c) seen from above; d is twice the
        # triangle's signed area in x-z
        t = water.vertices[water.triangles].astype(np.float64)             # (m, 3, 3)
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
        with np.errstate(divide='ignore', invalid='ignore'):
            l1 = ((b[:, 2] - c[:, 2]) * (p[0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[2] - c[:, 2])) / d
            l2 = ((c[:, 2] - a[:, 2]) * (p[0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[2] - c[:, 2])) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= 0) & (l2 >= 0) & (l3 >= 0) & np.isfinite(l1)
        if not inside.any():
            return None
        k = np.nonzero(inside)[0][0]
        return float(l1[k] * a[k, 1] + l2[k] * b[k, 1] + l3[k] * c[k, 1] - p[1])

    def tooltip(self, view: viewer.Viewer) -> None:
        io = imgui.get_io()
        if (io.want_capture_mouse or self.measuring or view.hovered == viewer.NO_ID
                or imgui.is_mouse_down(imgui.MouseButton_.left)):
            return
        if view.hovered == TERRAIN_ID:
            text = 'terrain'
        elif WATER_ID <= view.hovered < WATER_ID + len(self.water_shaders):
            text = 'water  ' + (self.water_shaders[view.hovered - WATER_ID] or '(shader unknown)')
        elif view.hovered < len(self.lvl.entities):
            hovered = self.lvl.entities[view.hovered]
            text = f'{hovered.short_type}  {hovered.name or hovered.id or hovered.blueprint or ""}'
            if hovered.team is not None:
                text += f'\n{TEAM_NAME[hovered.team]}'
        else:
            return
        if view.hover_point is not None:
            point = view.hover_point
            text += f'\n{point[0]:.1f}, {point[1]:.1f}, {point[2]:.1f}'
        imgui.set_tooltip(text)

    def panel_status(self, view: viewer.Viewer) -> None:
        self.tooltip(view)
        io = imgui.get_io()
        imgui.set_next_window_pos((10, io.display_size.y - 40), imgui.Cond_.always)
        imgui.set_next_window_size((io.display_size.x - 20, 30), imgui.Cond_.always)
        flags = imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_inputs | imgui.WindowFlags_.no_background
        imgui.begin('##status', flags=flags)
        imgui.text(f'{view.fps:5.1f} fps   {self.scene_triangles:,} triangles   {view.renderer}   '
                   f'camera: {"fly" if view.camera.fly else "orbit"} (F)   '
                   f'click: {"measure" if self.measuring else "select"} (M)   '
                   f'terrain: {"height field" if self.lvl.heightfield is not None else "samples"}')
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
        measure = measure_points(opts['--measure']) if '--measure' in opts else None
        frames = frame_count(opts.get('--frames', '40'))
    except ValueError as e:
        print('error:', e)
        return 2
    if not args:                                            # started without a level: ask for one
        from bfbc2core import pick
        picked = pick.pick_level('BF1943 Map Inspector')
        if not picked:
            return 1
        args = [picked]
    lvl = levelmod.load(args[0])
    inspector = Inspector(lvl)
    inspector.scene_triangles = inspector.scene.triangle_count()
    panels = [inspector.panel_layers, inspector.panel_selection, inspector.panel_measure, inspector.panel_status]
    view = viewer.Viewer(inspector.scene, f'BF1943 Map Inspector - {lvl.name}', panels=panels)
    view.on_pick = inspector.on_pick(view)
    for name in HIDDEN_AT_START:
        view.set_layer_visible(inspector.layer_index[name], False)
    if '--select' in opts:
        entity = lvl.by_guid(opts['--select'])
        if entity is not None:
            inspector.select(view, entity, focus=True)
    if measure is not None:
        inspector.measure = [np.array(point) for point in measure]
        inspector.set_measuring(view, True)
        inspector.update_overlay(view)
    if '--screenshot' in opts:
        view.exit_after = frames
    view.run()
    if '--screenshot' in opts:
        return save_screenshot(opts['--screenshot'], view.screenshot)
    return 0


def measure_points(text: str) -> list[list[float]]:
    """--measure "x,y,z;x,y,z;...": one or more finite points; ValueError otherwise."""
    try:
        points = [[float(coordinate) for coordinate in point.split(',')] for point in text.split(';') if point.strip()]
    except ValueError:
        points = []
    if not points or any(len(point) != 3 or not all(np.isfinite(point)) for point in points):
        raise ValueError(f'--measure {text!r}: give points as "x,y,z;x,y,z;..."')
    return points


def frame_count(text: str) -> int:
    """--frames N: a whole number of frames, at least 1; ValueError otherwise."""
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise ValueError(f'--frames {text!r}: give a whole number of frames (1 or more)')
    return n


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
