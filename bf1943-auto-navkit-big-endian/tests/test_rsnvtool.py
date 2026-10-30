"""rsnvtool.py (NavKit's RSNV inspector) on a made-up file (no game data): info, validate, locate, los, route (by nodes
and by a point), pockets, picture, diff, tight (each tight pair, JSON) and cells (text maps) answer as the file says;
los on two stacked floors keeps to each floor; a missing export is refused.

  python -B test_rsnvtool.py
"""
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'rsnvtool.py')
sys.path[:0] = [os.path.join(SUITE, 'core'), os.path.join(SUITE, 'core', 'tests')]
from bfbc2core import rsnv  # noqa: E402
import test_rsnv  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def run(*args):
    r = subprocess.run([sys.executable, '-B', TOOL, *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


if __name__ == '__main__':
    tmp = tempfile.mkdtemp(prefix='rsnvtool_')
    sections, header = test_rsnv.tiny()
    a = os.path.join(tmp, 'tiny.rsnv')
    rsnv.write(a, sections, header)
    code, out = run('info', a)
    check(code == 0 and '4 nodes, 8 edges' in out and 'jump 2' in out, 'info: 4 nodes, 8 edges, 2 of them jumps')
    code, out = run('validate', a)
    check(code == 0 and '0 problem(s)' in out, 'validate: consistent')
    code, out = run('locate', a, '1.7', '10.0', '1.7')
    check(code == 0 and 'cell (2, 2)' in out, 'locate: the walkable cell (2, 2)')
    code, out = run('locate', a, '1.0', '30.0', '1.0')
    check(code == 1 and 'no walkable cell' in out, 'locate: nothing 20 m above the area')
    code, out = run('route', a, '0', '2')
    check(code == 0 and 'done, cost 4.8 m' in out and '3 nodes' in out, 'route 0 -> 2: done, cost 4.8 m (76/16)')
    code, out = run('route', a, '0.1,10.0,0.1', '2.3,10.0,2.3')
    check(code == 0 and '0 -> 2' in out, 'route by points: from the nodes of the cells nearest them')
    code, out = run('los', a, '0.0,10.0,0.0', '1.6,10.0,1.6')
    check(code == 0 and 'in sight' in out, 'los over walkable cells: in sight')
    code, out = run('pockets', a)
    check(code == 0 and '1 pieces' in out, 'pockets: one piece (no tight edges)')
    png = os.path.join(tmp, 'p.png')
    code, out = run('picture', a, '--at', '1.2,1.2', '--radius', '3', '--route', '0', '2', '--png', png)
    check(code == 0 and os.path.isfile(png) and os.path.getsize(png) > 0, 'picture: a PNG with the route')
    s2 = dict(sections)
    s2['EDGE'] = [(t, c, rsnv.EDGEF_TIGHT if k == 0 else f) for k, (t, c, f) in enumerate(sections['EDGE'])]
    b = os.path.join(tmp, 'tight.rsnv')
    rsnv.write(b, s2, header)
    code, out = run('diff', a, b)
    check(code == 0 and 'tight edges' in out and '+1' in out, 'diff: one more tight edge')
    js = os.path.join(tmp, 'tight.json')
    code, out = run('tight', b, '--json', js)
    import json
    rows = json.load(open(js, encoding='utf-8'))['pairs'] if os.path.isfile(js) else []
    check(code == 0 and '1 tight pairs' in out and len(rows) == 1 and rows[0]['nodes'] == [0, 1]
          and rows[0]['ends'][0]['area'] == 0, 'tight: the one flagged pair, its ends\' area, written as JSON')
    code, out = run('tight', a)
    check(code == 0 and '0 tight pairs' in out, 'tight: none in a file without flags')
    code, out = run('cells', a, '--at', '1.2,1.2', '--y', '10.0', '--radius', '0.4')
    check(code == 0 and 'area 0 (sector 0b0b, 0.8 m' in out and 'X' in out, 'cells: the area\'s map around the point')
    code, out = run('cells', a, '--at', '2.4,2.4', '--y', '10.0', '--radius', '0.2')
    check(code == 0 and 'x' in out.split('area 0')[-1], 'cells: the missing cell is shown as not walkable')
    s3, h3 = test_rsnv.stacked()
    c = os.path.join(tmp, 'stacked.rsnv')
    rsnv.write(c, s3, h3)
    code, out = run('los', c, '0.2,10.8,0.2', '1.2,10.0,1.2')
    check(code == 1 and 'not in sight' in out, 'los: from one floor to the floor under it, not in sight')
    code2, out2 = run('los', c, '0.2,10.8,0.2', '1.2,10.8,1.2')
    check(code2 == 0, 'los: along the upper of two stacked floors, in sight (the start on the nearest floor)')
    code, out = run('info', os.path.join(tmp, 'missing.rsnv'))
    check(code == 1 and 'no such export' in out, 'a missing export: refused with its cause')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
