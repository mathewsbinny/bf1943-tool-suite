r"""NavKit little-endian: DICE-parity report on a made-up OPFOR folder (navparity)

A made-up Project OPFOR folder in a temp folder (no game data): DICE's path data for a level is a .hpd built here
with navbuild from made-up samples (a 48 m square with a 6 m gap: two islands), NavKit's candidate the same square
without the gap (one island), the level partition a made-up XML with a few AI orders. Checked: reference_of (the
kept original while NavKit's file is installed, a level that had none), level_xml (any case; missing: a clear
error), terrain_bounds, stale_nodes (reference nodes over no sampled ground), islands (connectivity, over-
connection, clip and dropped nodes), ladder_ground without ladders, run_level's row, the table (one run, one run
against another, a level with no matching areas) and the command line (run with level=file and with --pattern,
show with one and two runs, usage).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import json
import os
from types import SimpleNamespace

import numpy as np

import hpdtool
import navbuild
import navlinks
import navparity
import navsample
from bfbc2core import terrain

LV = 'sp_01'


def flat(pitch, min_x, min_z, nx, nz, y=0.0, ground=None):
    """One flat surface per column; ground(x, z) -> False leaves the column empty (test_navbuild_edges.py)."""
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


def build(path, ground=None):
    s = flat(0.8, 0.0, 0.0, 60, 60, ground=ground)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        navbuild.build('s.bin', None, 0, 0, str(path), log=lambda *a: None)
    finally:
        navsample.load = load
    return str(path)


def gap_ground(x, z):
    return not (20 < x < 26)


def transform(x, y, z):
    return '1/0/0/0/0/1/0/0/0/0/1/0/' + f'{x}/{y}/{z}' + '/1'


ORDERS = [('a0000000-0000-0000-0000-000000000001', (10.0, 0.0, 10.0)),
          ('a0000000-0000-0000-0000-000000000002', (12.0, 0.0, 40.0)),
          ('a0000000-0000-0000-0000-000000000003', (30.0, 0.0, 30.0)),
          ('a0000000-0000-0000-0000-000000000004', (23.0, 0.0, 30.0))]       # in the gap


def opfor(tmp_path):
    """root, reference (two islands), candidate (one island)."""
    root = tmp_path / 'Project OPFOR'
    (root / 'game').mkdir(parents=True)
    infantry = root / 'game' / 'Output' / 'win32' / 'ai' / 'levels' / LV / 'infantry'
    infantry.mkdir(parents=True)
    ref = build(infantry / 'pathdata.hpd', ground=gap_ground)
    levels = root / 'work' / 'levels' / LV / 'dist' / 'levels'
    levels.mkdir(parents=True)
    xml = ''.join(f'<instance guid="{g}" type="Entity.AreaOrderEntityData"><field name="Name">o{k}</field>'
                  f'<complex name="Transform">{transform(*p)}</complex></instance>\n'
                  for k, (g, p) in enumerate(ORDERS))
    (levels / 'SP_01.xml').write_text(xml, encoding='utf-8')
    cand = build(tmp_path / 'candidate.hpd')
    return str(root), ref, cand


def test_reference_of(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, 'game', 'Output', 'win32', 'ai', 'levels', LV, 'infantry', 'pathdata.hpd')
    assert navparity.reference_of(root, LV) == path, 'no backup: the installed file is DICE\'s'
    os.makedirs(os.path.dirname(path))
    open(path + '.opfor-none', 'wb').close()
    assert navparity.reference_of(root, LV) == path + '.opfor-orig', 'NavKit installed over no file: no reference'
    os.remove(path + '.opfor-none')
    open(path + '.opfor-orig', 'wb').close()
    assert navparity.reference_of(root, LV) == path + '.opfor-orig', 'the kept original'


def test_level_xml(tmp_path):
    folder = tmp_path / 'work' / 'levels' / LV / 'dist' / 'levels'
    folder.mkdir(parents=True)
    (folder / 'other.xml').write_text('', encoding='utf-8')
    try:
        navparity.level_xml(str(tmp_path), LV)
    except FileNotFoundError as e:
        assert 'converted level partition not found' in str(e) and f'prepare {LV}' in str(e), e
    else:
        raise AssertionError('no partition: FileNotFoundError')
    (folder / 'Sp_01.XML').write_text('', encoding='utf-8')
    assert navparity.level_xml(str(tmp_path), LV) == str(folder / 'Sp_01.XML'), 'any case'


def test_terrain_bounds(tmp_path):
    assert navparity.terrain_bounds(str(tmp_path), LV) is None, 'no terrain folder'
    folder = tmp_path / 'work' / 'levels' / LV / 'terrain' / 'sub'
    folder.mkdir(parents=True)
    (folder / 'notes.txt').write_text('', encoding='utf-8')
    assert navparity.terrain_bounds(str(tmp_path), LV) is None, 'no height field'
    (folder / 'SP_01.HeightField-000.res').write_bytes(b'')
    read = terrain.read
    seen = []
    terrain.read = lambda p: seen.append(p) or SimpleNamespace(min=(-512.0, 0.0, -256.0), max=(512.0, 90.0, 256.0))
    try:
        assert navparity.terrain_bounds(str(tmp_path), LV) == (-512.0, -256.0, 512.0, 256.0)
    finally:
        terrain.read = read
    assert seen == [str(folder / 'SP_01.HeightField-000.res')]


def test_stale_nodes(tmp_path):
    root, ref, _cand = opfor(tmp_path)
    assert navparity.stale_nodes(root, LV, ref) == set(), 'no 0.8 m samples: none'
    samples = tmp_path / 'Project OPFOR' / 'work' / 'navsample'
    samples.mkdir(parents=True)
    (samples / f'{LV}_full.bin').write_bytes(b'')
    # the PC level has ground only left of the gap: the right island's nodes are stale
    s = flat(0.8, 0.0, 0.0, 60, 60, ground=lambda x, z: x < 20)
    load = navsample.load
    navsample.load = lambda p: (s, 0)
    try:
        stale = navparity.stale_nodes(root, LV, ref)
        clipped = navparity.stale_nodes(root, LV, ref, clip=(0.0, 0.0, 35.0, 100.0))
    finally:
        navsample.load = load
    import ordercheck
    nodes = dict(ordercheck.PathData(ref).nodes)
    assert stale == {n for n, p in nodes.items() if p[0] > 26}, stale
    assert clipped == {n for n, p in nodes.items() if 26 < p[0] <= 35}, 'nodes outside the clip box are not judged'
    # on raised ground (5 m over the nodes' ground) every node is stale
    high = flat(0.8, 0.0, 0.0, 60, 60, y=5.0)
    navsample.load = lambda p: (high, 0)
    try:
        assert navparity.stale_nodes(root, LV, ref) == set(nodes)
    finally:
        navsample.load = load


def linked(ref, path):
    """The reference with a route edge across the gap (navlinks): one island, every node where it was."""
    f = hpdtool.parse(open(ref, 'rb').read())
    results = navlinks.apply(f, [{'op': 'link-areas', 'a': [9.6, 0.0, 19.2], 'b': [32.8, 0.0, 19.2]}], log=lambda *a: None)
    assert results[0][1], results
    with open(path, 'wb') as fh:
        fh.write(hpdtool.write(f))
    return str(path)


def test_islands(tmp_path):
    _root, ref, cand = opfor(tmp_path)
    res = navparity.islands(ref, cand)
    assert (res['reference'], res['candidate'], res['large_reference'], res['large_candidate']) == (2, 1, 0, 0), res
    assert res['connectivity'] == 100.0, 'one island connects every pair the reference connects'
    same = navparity.islands(ref, ref)
    assert same['nodes_near'] == 100.0 and same['connectivity'] == 100.0 and same['over_connection'] == 0.0
    joined = linked(ref, tmp_path / 'linked.hpd')
    over = navparity.islands(ref, joined)
    # every node matched; the reference connects 6 + 36 pairs of its 4- and 9-node islands, the joined file all 78
    assert over['nodes_near'] == 100.0 and over['connectivity'] == 100.0 and over['candidate'] == 1
    assert over['over_connection'] == round(100.0 * (78 - 42) / 78, 2), over
    back = navparity.islands(joined, ref)
    assert back['connectivity'] == round(100.0 * 42 / 78, 2) and back['over_connection'] == 0.0, back
    left = navparity.islands(joined, ref, clip=(0.0, 0.0, 20.0, 100.0))
    assert left['reference'] == 1 and left['connectivity'] == 100.0, 'the clip leaves the left island alone'
    assert navparity.islands(joined, ref, drop={3})['nodes_near'] == 100.0
    assert navparity.pairs(4) == 6 and navparity.pairs(1) == 0


def test_ladder_ground_without_ladders(tmp_path):
    _root, ref, cand = opfor(tmp_path)
    assert navparity.ladder_ground(ref, cand, 100) == {'percent': 0.0, 'extra_cells': 0, 'cells': 0}


def test_run_level_and_table(tmp_path):
    root, ref, cand = opfor(tmp_path)
    lines = []
    row = navparity.run_level(root, LV, cand, lines.append)
    assert row['level'] == LV and row['candidate'] == cand and row['reference_clip'] is None
    assert row['stale_reference_nodes'] == 0
    assert (row['sectors_reference'], row['sectors_candidate'], row['sectors_missing'], row['sectors_extra']) == (1, 1, 0, 0)
    assert row['ground'] == 100.0 and row['extra_ground'] == round(100.0 * 420 / 3180, 2), row['extra_ground']
    assert row['ladder_ground'] == 0.0
    assert (row['islands_reference'], row['islands_candidate'], row['connectivity']) == (2, 1, 100.0)
    assert row['ai_coverage'] == 100.0, 'the three orders on the reference\'s ground are on the candidate\'s'
    assert len(lines) == 1 and lines[0].split()[0] == LV
    same = navparity.run_level(root, LV, ref, lambda *a: None)
    assert same['areas_same'] == 100.0 and same['nodes_same'] == 100.0 and same['over_connection'] == 0.0

    run = {'name': 'a', 'date': '2026-10-31 10:00', 'levels': [row, dict(same, level='zz_new')]}
    out = []
    navparity.table(run, log=out.append)
    assert out[0] == "run 'a' (2026-10-31 10:00)"
    assert [line.split()[0] for line in out[2:]] == [LV, 'zz_new', 'mean'], 'campaign order, then the others'
    mean_ground = float(out[-1].split()[1])
    assert mean_ground == 100.0
    other = {'name': 'b', 'date': '2026-10-30 09:00', 'levels': [same, dict(same, level='zz_new')]}
    out = []
    navparity.table(run, other, log=out.append)
    assert "against 'b'" in out[0]
    assert out[-1].split()[0] == 'change'
    assert '(+' in out[2] or '(-' in out[2], 'a changed value shows its change: ' + out[2]
    # no area matches anywhere: the portal share is n/a and has no mean
    none_same = dict(row, areas_same=0)
    out = []
    navparity.table({'name': 'c', 'date': 'd', 'levels': [none_same]}, log=out.append)
    assert 'n/a' in out[2]


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = navparity.main(argv)
    return code, out.getvalue()


def test_main(tmp_path):
    root, ref, cand = opfor(tmp_path)
    code, out = run_main(['navparity.py', 'run', 'first', f'{LV}={cand}', '--opfor', root])
    assert code == 0 and "parity run 'first': 1 level(s)" in out, out
    saved = os.path.join(root, 'work', 'navkit', 'parity', 'first.json')
    assert navparity.save_path(root, 'first') == saved and os.path.isfile(saved)
    first = json.load(open(saved, encoding='utf-8'))
    assert first['name'] == 'first' and [r['level'] for r in first['levels']] == [LV]
    os.makedirs(os.path.join(tmp_path, 'builds'))
    pattern = os.path.join(str(tmp_path), 'builds', '{lv}.hpd')
    with open(ref, 'rb') as src, open(pattern.replace('{lv}', LV), 'wb') as dst:
        dst.write(src.read())
    code, out = run_main(['navparity.py', '--opfor', root, 'run', 'second run', '--pattern', pattern, '--levels', LV])
    assert code == 0
    second = json.load(open(navparity.save_path(root, 'second run'), encoding='utf-8'))
    assert second['levels'][0]['candidate'] == pattern.replace('{lv}', LV)
    assert os.path.basename(navparity.save_path(root, 'second run')) == 'second_run.json'
    code, out = run_main(['navparity.py', 'show', 'first', '--opfor', root])
    assert code == 0 and out.startswith("run 'first'"), out
    code, out = run_main(['navparity.py', 'show', 'first', 'second run', '--opfor', root])
    assert code == 0 and "run 'second run'" in out and "against 'first'" in out, 'the second against the first'
    code, out = run_main(['navparity.py', 'compare', 'x', '--opfor', root])
    assert code == 1 and 'navparity.py run' in out
