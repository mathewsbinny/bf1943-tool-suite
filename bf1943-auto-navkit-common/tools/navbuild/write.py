"""NavKit's builder (navbuild), writing: order-node pruning, the final tables and the .hpd file (write_file, written
beside itself and read back before it replaces the old one), and the build's sidecar <name>.cells.npz (every area's
cells and ground heights; write_cells, read_cells) (pipeline step 7)."""
import os
import struct
import time

import numpy as np

import hpdtool
import kymesh

from .config import BFBC2, HEADER, ORDER_MIN_ISLAND, BuildError, BuildState
from .graph import finalize_graph, prune_order_nodes
from .merge import merge_coincident_border


def write_file(graphs, out_path, log, t0, state=None, settings=BFBC2):
    """Order-node pruning, final node / edge tables, and the file. state: the build's BuildState (its order nodes,
    and the areas' cells for the sidecar file); settings: the build's Settings."""
    state = state if state is not None else BuildState()
    hpd_sectors = []
    stamp = time.localtime()
    date = (stamp.tm_year % 100) * 10000 + (stamp.tm_mon - 1) * 100 + stamp.tm_mday
    clock = stamp.tm_hour * 1000000 + stamp.tm_min * 10000 + stamp.tm_sec * 100
    if state.order_ids and ORDER_MIN_ISLAND:
        graphs = prune_order_nodes(graphs, state)
    if settings.border_seam_line:
        graphs = merge_coincident_border(graphs, log)
    linked = {pts[i][0] for _sid, _b, _m, pts, pairs, _o in graphs for a_, b_, _d in pairs for i in (a_, b_)}
    for sid, bounds, mesh, pts, pairs, origin in graphs:
        nodes, edges = finalize_graph(pts, pairs, linked, origin)
        hpd_sectors.append(hpdtool.Sector(sid, bounds, 0, date, clock, nodes, edges, kymesh.write_mesh(mesh)))
    out = hpdtool.HpdFile(HEADER, hpd_sectors)
    data = hpdtool.write(out)
    if not hpd_sectors:
        raise BuildError('no sectors to write')
    tmp = out_path + '.tmp'
    try:
        with open(tmp, 'wb') as fh:          # the previous file stays until the new one is whole and reads back
            fh.write(data)
        with open(tmp, 'rb') as fh:
            try:
                same = hpdtool.write(hpdtool.parse(fh.read())) == data
            except (ValueError, struct.error) as e:
                raise BuildError(f'{out_path}: the new file does not read back ({e}) - the previous one is kept') from e
        if not same:
            raise BuildError(f'{out_path}: the new file does not read back exactly - the previous one is kept')
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    write_cells(graphs, out_path, data, log, state.area_cells)
    log(f'wrote {out_path}: {len(hpd_sectors)} sectors, {sum(len(x.nodes) for x in hpd_sectors)} nodes, '
        f'{sum(len(x.edges) for x in hpd_sectors)} edges, {len(data)} bytes ({time.time() - t0:.0f} s)')


def cells_path(hpd_path):
    """The sidecar file of a build: <name>.cells.npz beside <name>.hpd."""
    return os.path.splitext(hpd_path)[0] + '.cells.npz'


def write_cells(graphs, out_path, data, log, area_cells):
    """The sidecar of the build just written (area_cells: BuildState.area_cells, written in the file's sector and area
    order): per area its sector id,
    pitch and cell count; the cells' global indices and ground heights; the build's SHA-256, so a reader can tell it
    belongs to this file. Nothing is written when a sector's areas were not recorded (a caller that builds graphs
    itself)."""
    import hashlib
    sids, pitch, count, ii, jj, yy = [], [], [], [], [], []
    for sid, _b, mesh, _p, _q, _o in graphs:
        rec = area_cells.get(sid)
        if rec is None or len(rec) != len(mesh.areas):
            log(f'cells: sector {sid:04x} not recorded; no {os.path.basename(cells_path(out_path))} written')
            return
        for i_, j_, y_, p_ in rec:
            sids.append(sid)
            pitch.append(p_)
            count.append(len(i_))
            ii.append(i_)
            jj.append(j_)
            yy.append(y_)
    cat = lambda v, t: np.concatenate(v).astype(t) if v else np.zeros(0, t)
    np.savez_compressed(cells_path(out_path), sector=np.array(sids, np.int32), pitch=np.array(pitch, np.float32),
                        count=np.array(count, np.int64), i=cat(ii, np.int32), j=cat(jj, np.int32), y=cat(yy, np.float32),
                        hpd_sha256=np.array(hashlib.sha256(data).hexdigest()))
    log(f'cells: {len(count)} areas, {sum(count)} cells -> {cells_path(out_path)}')


def read_cells(hpd_path):
    """The sidecar of a build, or None when it is missing or belongs to another file: [(sector id, pitch, i, j, y)] per
    area in the file's order."""
    import hashlib
    p = cells_path(hpd_path)
    if not os.path.isfile(p):
        return None
    z = np.load(p)
    h = hashlib.sha256(open(hpd_path, 'rb').read()).hexdigest()
    if str(z['hpd_sha256']) != h:
        return None
    count = z['count']
    ends = np.cumsum(count)
    starts = ends - count
    ci, cj, cy = z['i'], z['j'], z['y']                 # read once (each z[...] reads the array from the file again)
    return [(int(s_), float(p_), ci[a:b], cj[a:b], cy[a:b])
            for s_, p_, a, b in zip(z['sector'], z['pitch'], starts, ends)]
