"""BF1943 Navigation Studio: a BF1943 level's navigation build in 3D, with its bases, flags, spawns and ladders.

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

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(SUITE, 'core'))
if __name__ == '__main__':                      # the log and error reports start before the heavy imports,
    from bfbc2core import errors                 # so a missing library is reported too
    errors.setup('navigation-studio', gui='--screenshot' not in sys.argv)

import numpy as np  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from bfbc2core import game1943, level1943, navdata, navpath, viewer  # noqa: E402

NODE_HEIGHT = 0.9                                        # soldier1943: nodes half the soldier's 1.8 m up
ID_AREA = 0x200000                                       # + area index
ID_POINT = 0x100000                                      # + index into Studio.points (spawns, bases, flags)
TERRAIN_ID, WATER_ID, OBJECTS_ID, GRAPH_ID = 0xFFFFFE, 0xFFFFFD, 0xFFFFF0, 0xFFFFF1
COLOR_MODES = ['island', 'sector', 'pitch', 'small islands']
MAIN, NORMAL, DETAILED = (0.35, 0.75, 0.35), (0.30, 0.60, 0.80), (1.0, 0.60, 0.15)
DIMMED, WARNING, MISSING = (0.45, 0.48, 0.45), (0.95, 0.25, 0.85), (0.9, 0.15, 0.15)
LADDER = (0.62, 0.35, 1.0)                               # ground reached only by ladder links, and the links
PATH = (1.0, 0.85, 0.1)
LIFT = 0.06                                              # ground cells drawn just above the surface
SMALL = 25                                               # nodes: an island this small is a pocket, not a network
POINT_LAYERS = [('soldier spawns, team 1 (US)', (0.2, 0.55, 1.0)), ('soldier spawns, team 2 (Japan)', (1.0, 0.3, 0.25)),
                ('soldier spawns, at flags', (0.85, 0.85, 0.85)), ('bases', (1.0, 0.6, 0.1)), ('flags', (1.0, 0.85, 0.2))]


def hue(k, s=0.65, v=0.92):
    return colorsys.hsv_to_rgb((k * 0.618034) % 1.0, s, v)


def default_hpd(lv):
    return os.path.join(game1943.level_work(lv), 'navkit', f'{lv}.hpd')


def cell_quads(xz, y, heightfield=None, lift=LIFT):
    """Two triangles per 0.8 m cell. Cells on terrain follow it (each corner at the terrain's height), so slopes
    show as a surface rather than as steps; other cells (floors, roofs, decks) are flat at their height."""
    h = navdata.GRID / 2
    x, z = xz[:, 0], xz[:, 1]
    corners = ((-h, -h), (h, -h), (h, h), (-h, h))
    on_terrain = np.zeros(len(y), dtype=bool)
    if heightfield is not None and len(y):
        on_terrain = np.abs(heightfield.height(x, z) - y) < 0.05
    c = []
    for dx, dz in corners:
        cy = y.copy()
        if on_terrain.any():
            cy[on_terrain] = heightfield.height(x[on_terrain] + dx, z[on_terrain] + dz)
        c.append(np.stack([x + dx, cy + lift, z + dz], axis=1))
    return np.stack([np.stack([c[0], c[1], c[2]], 1), np.stack([c[0], c[2], c[3]], 1)], axis=1).reshape(-1, 3, 3)


def box_tris(lo, hi):
    lo, hi = np.asarray(lo, np.float32), np.asarray(hi, np.float32)
    c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], np.float32)
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return np.array([t for a, b, cc, d in faces for t in (c[[a, b, cc]], c[[a, cc, d]])])


class Studio:
    def __init__(self, lvl, nav, color='island'):
        self.lvl, self.nav, self.color = lvl, nav, color
        self.scene = viewer.Scene()
        L = self.scene.layer
        self.layers = {'terrain': L('terrain', (0.40, 0.44, 0.36)), 'water': L('water', (0.18, 0.42, 0.78)),
                       'collision': L('collision', (0.58, 0.57, 0.55))}
        if lvl.terrain is not None:
            self.scene.add_indexed(lvl.terrain[0], lvl.terrain[1], TERRAIN_ID, self.layers['terrain'])
        if lvl.water is not None and len(lvl.water):
            self.scene.add_indexed(lvl.water.vertices, lvl.water.triangles, WATER_ID, self.layers['water'])
        for m in lvl.meshes:
            self.scene.add_triangles(m.triangles, OBJECTS_ID, self.layers['collision'])
        lay = {k: L(k, col) for k, col in (('ground', MAIN), ('outlines', (0.95, 0.95, 0.95)), ('portals', (0.3, 0.9, 1.0)),
                                            ('border portals', (1.0, 0.9, 0.2)), ('route graph', (1.0, 0.4, 0.9)))}
        self.layers.update(lay)
        self.ground_batch = self.scene.add_triangles(cell_quads(nav.cell_xz, nav.cell_y, lvl.heightfield),
                                                     np.repeat(ID_AREA + nav.cell_area, 2), lay['ground'])
        drop = np.array([0, navdata.Y_OFFSET - 0.12, 0])
        lines = [(k, line) for k, a in enumerate(nav.areas) for line in a.outline]
        if lines:                                    # all outline polylines at once: segments within each line
            pts = np.concatenate([l for _, l in lines])
            owner = np.repeat(np.arange(len(lines)), [len(l) for _, l in lines])
            keep = owner[:-1] == owner[1:]
            area_of_line = np.array([k for k, _ in lines])
            self.scene.add_lines(np.stack([pts[:-1][keep], pts[1:][keep]], axis=1) - drop,
                                 ID_AREA + area_of_line[owner[:-1][keep]], lay['outlines'])
        for key, field in (('portals', 'portals'), ('border portals', 'borders')):
            segs = [(k, s[0], s[1]) for k, a in enumerate(nav.areas) for s in getattr(a, field)]
            if segs:
                self.scene.add_lines(np.array([[p0, p1] for _, p0, p1 in segs]) - drop,
                                     np.array([ID_AREA + k for k, _, _ in segs]), lay[key])
        lad = nav.ladder_edge if nav.ladder_edge is not None else np.zeros(len(nav.edges), bool)
        if len(nav.edges):
            p = nav.node_pos - np.array([0, navdata.Y_OFFSET - 0.2, 0])
            e = nav.edges[~lad]
            self.scene.add_lines(np.stack([p[e[:, 0]], p[e[:, 1]]], axis=1), GRAPH_ID, lay['route graph'])
        lad_area = np.array([a.ladder for a in nav.areas], dtype=bool)
        if lad_area.any() and len(nav.cell_area):
            sel = lad_area[nav.cell_area]
            self.layers['ladder ground'] = L('ladder ground', LADDER)
            self.scene.add_triangles(cell_quads(nav.cell_xz[sel], nav.cell_y[sel], lvl.heightfield, LIFT + 0.05),
                                     np.repeat(ID_AREA + nav.cell_area[sel], 2), self.layers['ladder ground'])
        if lad.any():
            self.layers['ladder links'] = L('ladder links', LADDER)
            e = nav.edges[lad]
            p = nav.node_pos - np.array([0, navdata.Y_OFFSET - 0.3, 0])
            self.scene.add_lines(np.stack([p[e[:, 0]], p[e[:, 1]]], axis=1), GRAPH_ID, self.layers['ladder links'])
        # points: soldier spawns (and their alternates), bases, flags; ladders as boxes
        for name, col in POINT_LAYERS:
            self.layers[name] = L(name, col)
        self.points = []                             # (kind, label, position, entity or objective)
        roles = {o['guid']: o for o in lvl.objectives}
        for e in lvl.entities:
            if e.position is None:
                continue
            if e.category == 'soldier spawn':
                name = {1: POINT_LAYERS[0][0], 2: POINT_LAYERS[1][0]}.get(e.team, POINT_LAYERS[2][0])
                self.add_point(name, 'spawn', e.name or e.short_type, e.position, 1.2, e)
            elif e.category in ('base', 'flag'):
                o = roles.get(e.guid.lower(), {})
                self.add_point('bases' if e.category == 'base' else 'flags', e.category, o.get('name') or e.name or '',
                               e.position, 2.5, e, tall=25.0)
        self.layers['ladders'] = L('ladders', (0.95, 0.9, 0.35))
        for e in lvl.entities:
            if e.category == 'ladder':
                m = next((x for x in lvl.meshes if x.entity == e.index), None)
                if m is not None:
                    v = m.triangles.reshape(-1, 3)
                    k = len(self.points)
                    self.points.append(('ladder', e.blueprint.rsplit('/', 1)[-1], (v.min(0) + v.max(0)) / 2, e))
                    self.scene.add_triangles(box_tris(v.min(0) - 0.05, v.max(0) + 0.05), ID_POINT + k, self.layers['ladders'])
        self.main = 0 if nav.islands else -1         # navdata: islands largest first
        self.point_island = [self.island_at(p[2]) for p in self.points]
        self.selected = None
        self.apply_colors()

    def add_point(self, layer, kind, label, pos, size, ent, tall=0.0):
        k = len(self.points)
        self.points.append((kind, label, np.asarray(pos, dtype=np.float64), ent))
        if tall:
            self.scene.add_triangles(box_tris((pos[0] - 0.6, pos[1], pos[2] - 0.6), (pos[0] + 0.6, pos[1] + tall, pos[2] + 0.6)),
                                     ID_POINT + k, self.layers[layer])
        else:
            self.scene.add_box(np.asarray(pos) + np.array([0, size / 2, 0]), size, ID_POINT + k, self.layers[layer])

    def island_at(self, p):
        """(island index or None, route node distance): the nearest node within navpath's join limits."""
        n, d = navpath.join(self.nav, p)
        return (None, d) if n is None else (int(self.nav.node_island[n]), d)

    # --- colours ------------------------------------------------------------------------------------
    def cell_colors(self):
        nav = self.nav
        a = nav.cell_area
        isl = np.array([x.island for x in nav.areas])[a] if len(a) else np.zeros(0, int)
        if self.color == 'sector':
            sec = np.array([x.sector for x in nav.areas])[a]
            uniq, inv = np.unique(sec, return_inverse=True)
            return np.array([hue(k * 7 + 3, 0.5, 0.85) for k in range(len(uniq))])[inv]
        if self.color == 'pitch':
            det = np.array([x.detailed for x in nav.areas])[a]
            return np.where(det[:, None], np.array(DETAILED), np.array(NORMAL))
        if self.color == 'small islands':
            size = np.array([i.nodes for i in nav.islands] or [0])
            n = size[np.clip(isl, 0, len(size) - 1)]
            col = np.where((n >= SMALL)[:, None], np.array(DIMMED), np.array(WARNING))
            col[isl == 0] = MAIN
            return col
        table = np.array([MAIN] + [hue(k) for k in range(1, max(len(nav.islands), 1))]).reshape(-1, 3)
        return table[np.clip(isl, 0, len(table) - 1)]

    def apply_colors(self, v=None):
        c = np.repeat(self.cell_colors(), 2, axis=0)
        if v is not None:
            v.set_colors(self.ground_batch, c)
        else:
            p, t, _ = self.scene.batches[self.ground_batch]
            self.scene.batches[self.ground_batch] = (p, t, viewer.triangle_colors(c, len(p) // 3))

    # --- path test (P) ------------------------------------------------------------------------------
    def set_path_mode(self, v, on):
        self.path_mode = on
        v.select_on_click = not on

    def path_point(self, v, p):
        """A click in path mode: the start, then the end (a third click starts again)."""
        pts = self.__dict__.setdefault('path_pts', [])
        if len(pts) >= 2:
            pts.clear()
        pts.append(np.asarray(p, dtype=np.float64))
        self.route = navpath.route(self.nav, pts[0], pts[1]) if len(pts) == 2 else None
        seg = []
        for q in pts:
            for d in ((0.6, 0, 0), (0, 1.5, 0), (0, 0, 0.6)):
                seg.append((q - np.array(d), q + np.array(d), (1.0, 1.0, 1.0)))
        r = self.route
        if r is not None:
            g = navpath.ground_nodes(self.nav) + np.array([0, 0.35, 0])
            if r.found:
                chain = g[r.nodes]
                seg += [(chain[k], chain[k + 1], PATH) for k in range(len(chain) - 1)]
                seg += [(pts[0], chain[0], PATH), (chain[-1], pts[1], PATH)]
            elif r.gap:
                a, b = g[r.gap[0]], g[r.gap[1]]
                seg += [(a, b, MISSING), (a, a + np.array([0, 3, 0]), MISSING), (b, b + np.array([0, 3, 0]), MISSING)]
        v.set_overlay(seg)

    def panel_path(self, v):
        if not getattr(self, 'path_mode', False) and getattr(self, 'route', None) is None:
            return
        imgui.set_next_window_pos((380, 10), imgui.Cond_.first_use_ever)
        imgui.begin('Path test', flags=imgui.WindowFlags_.always_auto_resize)
        changed, on = imgui.checkbox('path test (P): click the start, then the end', getattr(self, 'path_mode', False))
        if changed:
            self.set_path_mode(v, on)
        if imgui.small_button('clear##path'):
            self.path_pts, self.route = [], None
            v.set_overlay([])
        for label, p in zip(('start', 'end'), getattr(self, 'path_pts', [])):
            imgui.text(f'{label}: {p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}')
        r = getattr(self, 'route', None)
        if r is not None:
            if r.found:
                imgui.text(f'route: cost {r.cost:.1f} m over {len(r.nodes)} nodes (length {r.length:.1f} m, '
                           f'straight {r.straight:.1f} m, x{r.cost / max(r.straight, 1e-9):.2f})')
            else:
                imgui.text(f'no route: {r.reason}')
                if r.gap and imgui.small_button('fly to the gap'):
                    v.camera.focus((self.nav.node_pos[r.gap[0]] + self.nav.node_pos[r.gap[1]]) / 2, 40.0)
        imgui.text_disabled('Over the route graph only; a runtime also smooths the route through the areas.')
        imgui.end()

    def on_pick(self, v):
        def handler(oid):
            if getattr(self, 'path_mode', False):
                if v.pick_point is not None:
                    self.path_point(v, v.pick_point)
                return
            self.selected = None
            if oid is not None and ID_AREA <= oid < ID_AREA + len(self.nav.areas):
                self.selected = ('area', oid - ID_AREA)
            elif oid is not None and ID_POINT <= oid < ID_POINT + len(self.points):
                self.selected = ('point', oid - ID_POINT)
        return handler

    # --- panels -------------------------------------------------------------------------------------
    def panel_main(self, v):
        imgui.set_next_window_pos((10, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((360, 640), imgui.Cond_.first_use_ever)
        imgui.begin('Navigation')
        imgui.text(f'{self.lvl.name}')
        s = navdata.summary(self.nav)
        imgui.text_disabled(os.path.basename(self.nav.path))
        imgui.text(f'  {s["areas"]:,} areas ({s["detailed areas"]:,} detailed), {s["cells"]:,} cells')
        imgui.text(f'  {s["nodes"]:,} nodes, {s["edges"]:,} edges, {s["islands"]} islands ({s["large islands (25+ nodes)"]} large)')
        if s['ladder links']:
            imgui.text(f'  {s["ladder links"]} ladder links, {s["ladder ground cells"]:,} cells of ladder ground')
        imgui.separator()
        imgui.text('colour the ground by')
        for m in COLOR_MODES:
            imgui.same_line()
            if imgui.radio_button(m, self.color == m):
                self.color = m
                self.apply_colors(v)
        imgui.separator()
        for name, i in self.layers.items():
            changed, on = imgui.checkbox(name, v.layer_visible(i))
            if changed:
                v.set_layer_visible(i, on)
        imgui.end()

    def panel_islands(self, v):
        nav = self.nav
        w = imgui.get_io().display_size.x
        imgui.set_next_window_pos((w - 440, 10), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((430, 440), imgui.Cond_.first_use_ever)
        imgui.begin('Islands, spawns and flags')
        off = [(k, p, isl) for k, (p, isl) in enumerate(zip(self.points, self.point_island))
               if p[0] in ('spawn', 'flag', 'base') and isl[0] != self.main]
        n = sum(1 for p in self.points if p[0] in ('spawn', 'flag', 'base'))
        imgui.text(f'{n} spawns, bases and flags; {len(off)} not on the main network')
        imgui.text_disabled('(the carriers are their own networks: the sea divides them)')
        if off and imgui.tree_node('off the main network'):
            for k, (kind, label, pos, _), (isl, d) in ((k, p, i) for k, p, i in off[:150]):
                where = f'island {isl} ({nav.islands[isl].nodes} nodes)' if isl is not None else 'no route node within 15 m'
                if imgui.small_button(f'fly##pt{k}'):
                    v.camera.focus(pos, 30.0)
                imgui.same_line()
                imgui.text(f'{kind} {label}: {where}')
            imgui.tree_pop()
        imgui.separator()
        imgui.text(f'{len(nav.islands)} islands (largest first; island 0 is the main network)')
        imgui.begin_child('islands', (0, 0))
        for k, isl in enumerate(nav.islands[:300]):
            if imgui.small_button(f'fly##is{k}'):
                v.camera.focus(isl.centre - np.array([0, navdata.Y_OFFSET, 0]), 40.0 if k else 300.0)
            imgui.same_line()
            imgui.text(f'{k}: {isl.nodes:,} nodes, {isl.cells:,} cells')
        imgui.end_child()
        imgui.end()

    def panel_selection(self, v):
        if self.selected is None:
            return
        io = imgui.get_io()
        imgui.set_next_window_pos((io.display_size.x - 440, 460), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size((430, 220), imgui.Cond_.first_use_ever)
        imgui.begin('Selection')
        kind, k = self.selected
        if kind == 'point':
            pk, label, pos, ent = self.points[k]
            imgui.text(f'{pk} {label}')
            imgui.text_disabled(f'{ent.short_type} {ent.guid}')
            imgui.text(f'at {pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}' + (f', team {ent.team}' if ent.team else ''))
            isl, d = self.point_island[k]
            imgui.text('no route node within 15 m' if isl is None else
                       f'island {isl}' + (' (main network)' if isl == self.main else '') + f', route node {d:.1f} m away')
        else:
            a = self.nav.areas[k]
            isl = self.nav.islands[a.island] if 0 <= a.island < len(self.nav.islands) else None
            imgui.text(f'area {k}: sector 0x{a.sector:04X}, area {a.index}, tile {a.tile[0]},{a.tile[1]}, layer {a.layer}')
            imgui.text(f'pitch {a.pitch:.2f} m{" (detailed zone)" if a.detailed else ""}, {a.cells:,} cells, height {a.min_y:.1f} - {a.max_y:.1f} m')
            if a.ladder:
                imgui.text_colored(imgui.ImVec4(*LADDER, 1.0), 'ladder ground: reached only through a ladder link')
            imgui.text(f'{len(a.outline)} outline lines, {len(a.portals)} portals, {len(a.borders)} border portals')
            if isl is not None:
                imgui.text(f'island {a.island}{" (main network)" if a.island == 0 else ""}: {isl.nodes:,} nodes, {isl.cells:,} cells')
        imgui.end()

    def panel_status(self, v):
        io = imgui.get_io()
        if not io.want_capture_keyboard and imgui.is_key_pressed(imgui.Key.p):
            self.set_path_mode(v, not getattr(self, 'path_mode', False))
        if not io.want_capture_mouse and v.hovered != viewer.NO_ID:
            if ID_AREA <= v.hovered < ID_AREA + len(self.nav.areas):
                a = self.nav.areas[v.hovered - ID_AREA]
                imgui.set_tooltip(f'area {v.hovered - ID_AREA} - sector 0x{a.sector:04X}, island {a.island}, pitch {a.pitch:.1f}')
            elif ID_POINT <= v.hovered < ID_POINT + len(self.points):
                pk, label, _, _ = self.points[v.hovered - ID_POINT]
                imgui.set_tooltip(f'{pk} {label}')
        imgui.set_next_window_pos((10, io.display_size.y - 40), imgui.Cond_.always)
        imgui.set_next_window_size((io.display_size.x - 20, 30), imgui.Cond_.always)
        imgui.begin('##status', flags=imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_inputs | imgui.WindowFlags_.no_background)
        imgui.text(f'{v.fps:5.1f} fps   {self.triangles:,} triangles   {v.renderer}   colour: {self.color}   '
                   f'camera: {"fly" if v.camera.fly else "orbit"} (F)   P: path test')
        imgui.end()


def main(argv):
    opts, args, k = {}, [], 1
    while k < len(argv):
        if argv[k].startswith('--') and k + 1 < len(argv):
            opts[argv[k]] = argv[k + 1]
            k += 2
        else:
            args.append(argv[k])
            k += 1
    if not args:                                            # started without a level: ask for one
        from bfbc2core import pick
        lv = pick.pick_level('BF1943 Navigation Studio')
        if not lv:
            return 1
        args = [lv]
    lvl = level1943.load(args[0], opts.get('--game'))
    hpd = opts.get('--hpd', default_hpd(lvl.lv))
    if not os.path.isfile(hpd):
        print(f'no navigation build for {lvl.lv} ({hpd}): build it with bf1943-auto-navkit-big-endian '
              f'(python navkit.py all {lvl.lv}), or pass --hpd <file>')
        return 1
    print(f'navigation: {hpd}')
    navdata.Y_OFFSET = NODE_HEIGHT
    nav = navdata.load(hpd, lvl.heightfield)
    st = Studio(lvl, nav, opts.get('--color', 'island').replace('-', ' '))      # small-islands: the 'small islands' mode
    st.triangles = st.scene.triangle_count()
    v = viewer.Viewer(st.scene, f'BF1943 Navigation Studio - {lvl.name}',
                      panels=[st.panel_main, st.panel_islands, st.panel_selection, st.panel_path, st.panel_status])
    v.on_pick = st.on_pick(v)
    hide = set(opts.get('--hide', 'collision,outlines,portals,border portals,route graph').split(','))
    for name, i in st.layers.items():
        if name in hide:
            v.set_layer_visible(i, False)
    if '--island' in opts and int(opts['--island']) < len(nav.islands):
        v.camera.focus(nav.islands[int(opts['--island'])].centre, 60.0)
    if '--path' in opts:                                    # tests: a path test between two points
        st.set_path_mode(v, True)
        for p in opts['--path'].split(';'):
            st.path_point(v, [float(c) for c in p.split(',')])
        r = st.route
        print('path: ' + (f'cost {r.cost:.1f} m, {len(r.nodes)} nodes' if r.found else f'none - {r.reason}'))
    if '--focus' in opts:                                   # tests: the camera on a point
        v.camera.focus(np.array([float(c) for c in opts['--focus'].split(',')]), float(opts.get('--distance', 45)))
    if '--yaw' in opts:                                     # the orbit camera's angles (radians; pitch 1.5 looks down)
        v.camera.yaw = float(opts['--yaw'])
    if '--pitch' in opts:
        v.camera.pitch = max(-1.55, min(1.55, float(opts['--pitch'])))
    if '--screenshot' in opts:
        v.exit_after = int(opts.get('--frames', 40))
    v.run()
    if '--screenshot' in opts and v.screenshot is not None:
        import matplotlib.pyplot as plt
        plt.imsave(opts['--screenshot'], np.asarray(v.screenshot))
        print('screenshot:', opts['--screenshot'])
    return 0


if __name__ == '__main__':
    with errors.guard():                         # a crash: logged, explained in a window, exit code 1
        sys.exit(main(sys.argv))
