"""xeniarun.py on a Xenia log written here (no game data, no Xenia): the build, title and patches; warnings; the bots'
reports (host team, bots' teams and kits, spawns, the last positions from big-endian float bits); the normal exit
line; and the report text.

  python test_xeniarun.py
"""
import os
import struct
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import xeniarun  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def bits(*v):
    return ' '.join(struct.pack('>f', x).hex() for x in v)


LOG = f"""i> 0000733C Build: canary_experimental@e6cd2e4bc on Oct 20 2026
i> 0000733C Title name: Battlefield 1943
!> 0000733C Patcher: Applying patch for: Battlefield 1943(5841097E) - Offline: no automatic team on joining
!> 0000733C Patcher: Applying patch for: Battlefield 1943(5841097E) - Experiment: offline bots
w> 0000733C something Xenia warns about
d> F80000C0 DbgPrint(82AAA5F8(rs-bots: host player %08x team %d), ContextArg)
i> F80000C0 (DbgPrint) rs-bots: host player d997e0b0 team 1
d> F80000C0 DbgPrint(82AAA61C(rs-bots: bot %08x team %d kit %08x), ContextArg)
i> F80000C0 (DbgPrint) rs-bots: bot dc7dc3e0 team 2 kit 00000003
i> F80000C0 (DbgPrint) rs-bots: bot dc7de5b0 team 1 kit 00000001
i> F80000C0 (DbgPrint) rs-bots: spawned dc7dc3e0 team 2 at spawn id 00000045 soldier dda24d90
i> F80000C0 (DbgPrint) rs-bots: bot dc7dc3e0 soldier position {bits(-150.0, 230.25, -555.5)} limit 3f800000 throttle 00000000 dt 3d088a48
i> F80000C0 (DbgPrint) rs-bots: host d997e0b0 soldier position {bits(134.33, 218.88, 239.5)}
i> 0000733C Cheap-skate exit!
"""

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'xenia.log')
        open(p, 'w', encoding='utf-8').write(LOG)
        r = xeniarun.read_log(p)
        check(r['build'].startswith('canary_experimental') and r['title'] == 'Battlefield 1943' and len(r['patches']) == 2,
              f'build, title and the {len(r["patches"])} patches applied')
        check(r['warnings'] == 1, 'warnings counted')
        check(r['host'] == {'player': 'd997e0b0', 'team': 1} and r['host_position'] == [134.33, 218.88, 239.5],
              f'the host\'s team and last position {r["host_position"]}')
        b = r['bots']
        check(set(b) == {'dc7dc3e0', 'dc7de5b0'} and b['dc7dc3e0']['team'] == 2 and b['dc7dc3e0']['position'] == [-150.0, 230.25, -555.5],
              'each bot\'s team, kit and last position')
        check(len(r['spawns']) == 1 and r['spawns'][0]['spawn_id'] == '00000045', 'the spawn reports')
        r.update(log_time='-', crashes=[], normal_exit=any('Cheap-skate exit' in l for l in r['last_lines']))
        text = xeniarun.report(r)
        check(r['normal_exit'] and 'exited normally' in text and 'bots: 2' in text, 'the report: bots, a normal exit')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
