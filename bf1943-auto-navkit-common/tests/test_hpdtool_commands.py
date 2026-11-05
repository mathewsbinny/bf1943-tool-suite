r"""NavKit (both variants): hpdtool's commands on a made-up pathdata.hpd

hpdtool's command line, run in-process on made-up files (no game data): info counts sectors, nodes, edges and mesh
bytes and gives the build dates (or says there are none); sector lists one sector's nodes and edges; coverage,
meshtrip and roundtrip take files and folders (every pathdata.hpd under a folder, whatever its case) and report each
file; a damaged file is reported and the batch goes on, with exit code 2; a mesh whose round trip differs is
reported with where; an unknown command or too few arguments print the usage (exit code 1).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io
import os
import struct

import fuzzcheck
import hpdtool

FORMATS = fuzzcheck.fixtures('bf1943-auto-navkit-common/tests/test_formats.py')


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hpdtool.main(['hpdtool.py', *argv])
    return code, out.getvalue()


def files(tmp_path):
    good = tmp_path / 'lv' / 'navkit' / 'PathData.HPD'
    good.parent.mkdir(parents=True)
    good.write_bytes(FORMATS.hpd())
    (tmp_path / 'lv' / 'navkit' / 'notes.txt').write_text('not read', encoding='utf-8')
    return str(good)


def test_info_and_sector(tmp_path):
    path = files(tmp_path)
    code, out = run('info', path)
    hpd = hpdtool.parse(open(path, 'rb').read())
    mesh = sum(len(s.mesh) for s in hpd.sectors)
    assert code == 0 and out.strip() == (f'2 sectors, 4 graph nodes, 4 edges, {mesh} bytes of runtime mesh; '
                                         f'build dates 100007..100007'), out
    empty = tmp_path / 'empty.hpd'
    empty.write_bytes(hpdtool.write(hpdtool.HpdFile(hpd.header, [])))
    code, out = run('info', str(empty))
    assert code == 0 and out.strip().endswith('no build dates (no sectors)'), out
    code, out = run('sector', path, '1')
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith('sector 0x0102 bounds [0.0, 100.0, 0.0, 5.0, 0.0, 100.0] built 100007 '
                                             '13305600'), lines[0]
    assert lines[1] == '  node 0x00000003 pos (1.00, 1.00, 1.00) flags 0x80000000 value 15 a 1 b 0,0 c 0', lines[1]
    assert lines[3] == '  edge 0x80000040 0 -> 1' and lines[5].startswith('  mesh ') and 'bytes: ' in lines[5], lines


def test_batches(tmp_path):
    path = files(tmp_path)
    damaged = tmp_path / 'damaged.hpd'
    damaged.write_bytes(FORMATS.hpd()[:-8])
    code, out = run('roundtrip', str(tmp_path / 'lv'))
    assert code == 0 and out.strip() == 'round trip: 1 identical, 0 different', out
    code, out = run('roundtrip', str(tmp_path / 'lv'), str(damaged))
    assert code == 2 and f'ERROR {damaged}' in out and f'MISMATCH {damaged}' in out, out
    assert out.strip().endswith('round trip: 1 identical, 1 different'), out
    code, out = run('meshtrip', path)
    assert code == 0 and out.strip() == 'mesh round trip: 2/2 identical', out
    code, out = run('coverage', path)
    assert code == 0 and out.strip().endswith('coverage: 2/2 meshes fully explained; gap patterns 0'), out


def test_mesh_mismatch_reported(tmp_path):
    path = files(tmp_path)
    hpd = hpdtool.parse(open(path, 'rb').read())
    sector = hpd.sectors[0]
    body = sector.mesh[:-8] + b'\0' * 4                                # 4 bytes the mesh writer would not write,
    sector.mesh = body + struct.pack('<2I', len(body), 0)             # then the trailer: its offset and 0
    odd = tmp_path / 'odd' / 'pathdata.hpd'
    odd.parent.mkdir()
    odd.write_bytes(hpdtool.write(hpd))
    code, out = run('meshtrip', str(odd.parent))
    assert code == 2 and 'mesh round trip: 1/2 identical' in out, out
    assert out.startswith('first mismatch: ') or '1 x ' in out, out
    code, out = run('coverage', str(odd))
    assert code == 0 and 'coverage: 1/2 meshes fully explained' in out, out


def test_usage(tmp_path):
    for argv in (['info'], ['nonsense', 'x']):
        code, out = run(*argv)
        assert code == 1 and out.strip() == (hpdtool.__doc__ or '').strip(), argv
    assert list(hpdtool._files([str(tmp_path / 'missing.hpd')])) == [str(tmp_path / 'missing.hpd')]
    path = files(tmp_path)
    assert list(hpdtool._files([str(tmp_path)])) == [path] and os.path.isfile(path)
