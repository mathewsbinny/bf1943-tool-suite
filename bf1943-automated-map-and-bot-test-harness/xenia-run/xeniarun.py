"""BF1943 run reader: what happened in a Xenia session of the Xenia kit's build - from Xenia's log, the bots'
reports in it, and Windows' record of a Xenia crash.

  python xeniarun.py [--xenia <Xenia folder>] [--log <xenia.log>] [--json out.json] [--positions]

The Xenia folder defaults to the one bf1943-xenia-kit's last build used (output\\kit-manifest.json). Read:
- the session: Xenia's build, the title, the patches the patcher applied (!> lines), warnings (w> lines), and the
  log's last lines (where a hang or crash left it);
- the bots experiment (bf1943-xenia-kit --experiment-bots; its DbgPrint lines "rs-bots: ..."): the host's player and
  team, each bot's team and kit, the spawns, and the last reported soldier positions (floats, big-endian bits)
  of the host and every bot; --positions lists them;
- a crash: Windows' Application log (Application Error, event 1000) for the Xenia executable within
  CRASH_WINDOW_MIN of the log's last write (a crash ends the session) - faulting module, offset and exception
  code. Xenia writes no crash report of its own; its normal exit ends the log with "Cheap-skate exit!". A crash
  record is not this session's when the log ends with that normal exit, or when it is older than the session's
  start (session.json beside the log, as run_xenia_probe.ps1 writes it); such records are listed as another
  session's.
Nothing is changed: the log and the event log are only read. Exit code 1 when Xenia crashed in that session, 2 when
the event log could not be read (crash status unknown), else 0.
"""
import datetime
import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
KIT_MANIFEST = os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'kit-manifest.json')
CRASH_WINDOW_MIN = 5            # a Windows crash record this close to the log's last write is this session's
# the printed report; at log level 3 Xenia also logs each call with its format string ("d> ... DbgPrint(82AAA5F8(
# rs-bots: host player %08x ...), ContextArg)"), which is not a report
BOT = re.compile(r'\(DbgPrint\) rs-bots: (.*)$')


def xenia_folder(given=None):
    if given:
        return given
    if os.path.isfile(KIT_MANIFEST):
        return json.load(open(KIT_MANIFEST, encoding='utf-8')).get('xenia')
    raise FileNotFoundError(f'no Xenia folder: pass --xenia, or build the Xenia setup (bf1943-xenia-kit) so that '
                            f'{KIT_MANIFEST} names it')


def floats(words, count):
    if len(words) != count:
        raise IndexError(f'{count} values expected, {len(words)} found')
    return [round(struct.unpack('>f', bytes.fromhex(w))[0], 2) for w in words]


def read_log(path):
    """The session's facts; bot reports that are cut short or garbled are kept, as lines, in 'malformed'."""
    out = dict(log=path, build=None, title=None, patches=[], warnings=0, last_lines=[], host=None, bots={}, spawns=[],
               host_position=None, malformed=[])
    lines = open(path, 'rb').read().decode('utf-8', errors='replace').splitlines()
    for line in lines:
        if line.startswith('w>'):
            out['warnings'] += 1
        if 'Build: ' in line and out['build'] is None:
            out['build'] = line.split('Build: ', 1)[1].strip()
        elif 'Title name: ' in line:
            out['title'] = line.split('Title name: ', 1)[1].strip()
        elif line.startswith('!>') and 'Applying patch for:' in line:
            out['patches'].append(line.split('Applying patch for:', 1)[1].strip())
        m = BOT.search(line)
        if m:
            try:
                bot_report(out, m.group(1).split())
            except (IndexError, ValueError, struct.error):     # cut short (a log still being written, a crash) or garbled
                out['malformed'].append(line.strip())
    out['last_lines'] = [l for l in lines if l.strip()][-8:]
    return out


def bot_report(out, w):
    """One "rs-bots:" report's words into the result; IndexError, ValueError or struct.error when it is incomplete."""
    if w[:2] == ['host', 'player']:
        out['host'] = dict(player=w[2], team=int(w[4]))
    elif w[0] == 'host' and w[2:4] == ['soldier', 'position']:
        out['host_position'] = floats(w[4:7], 3)
    elif w[0] == 'bot' and w[2] == 'team':
        out['bots'].setdefault(w[1], {}).update(team=int(w[3]), kit=w[5])
    elif w[0] == 'bot' and w[2:4] == ['soldier', 'position']:
        out['bots'].setdefault(w[1], {})['position'] = floats(w[4:7], 3)
    elif w[0] == 'spawned':
        out['spawns'].append(dict(bot=w[1], team=int(w[3]), spawn_id=w[7], soldier=w[9]))


class CrashQueryError(RuntimeError):
    """The event log could not be read: whether Xenia crashed is unknown."""


def crashes(exe_names, since):
    """Windows' Application Error events (id 1000) for these executables since a time: [{time, module, offset,
    code, exe}]. Read with PowerShell's Get-WinEvent (only reads). No matching events is an empty list; any other
    failure (no PowerShell, an unreadable log) raises CrashQueryError."""
    if os.name != 'nt':
        return []
    names = '|'.join(re.escape(n) for n in exe_names)
    ps = ("$s=[datetime]'" + since.strftime('%Y-%m-%dT%H:%M:%S') + "'; "
          "try { Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=$s} -ErrorAction Stop | "
          "ForEach-Object { $p=$_.Properties; '{0}|{1}|{2}|{3}|{4}' -f $_.TimeCreated.ToString('s'),$p[0].Value,$p[3].Value,$p[7].Value,$p[6].Value } } "
          "catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { exit 0 }; "
          "[Console]::Error.WriteLine($_.Exception.Message); exit 3 }")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True, errors='replace',
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as e:
        raise CrashQueryError(f'cannot run PowerShell to read the event log: {e}') from None
    if r.returncode != 0:
        raise CrashQueryError(f'reading the event log failed (exit {r.returncode}): {(r.stderr or "").strip()[:200]}')
    out = []
    for line in r.stdout.splitlines():
        parts = line.strip().split('|')
        if len(parts) == 5 and re.fullmatch(names, parts[1], re.I):
            out.append(dict(time=parts[0], exe=parts[1], module=parts[2], offset=parts[3], code=parts[4]))
    return out


def report(r, positions=False):
    s = [f"log: {r['log']} (written {r['log_time']})", f"Xenia {r['build']}; title {r['title']}"]
    s.append(f"patches applied: {len(r['patches'])}" + ''.join(f'\n  {p}' for p in r['patches']))
    s.append(f"warnings: {r['warnings']}")
    if r['host'] or r['bots']:
        teams = {}
        for b in r['bots'].values():
            teams[b.get('team')] = teams.get(b.get('team'), 0) + 1
        s.append(f"bots: {len(r['bots'])} (by team {dict(sorted((k, v) for k, v in teams.items() if k is not None))}), "
                 f"{len(r['spawns'])} spawns; host team {r['host']['team'] if r['host'] else '-'}, "
                 f"last position {r['host_position']}")
        if positions:
            for k, b in sorted(r['bots'].items()):
                s.append(f"  bot {k} team {b.get('team')} kit {b.get('kit')}: {b.get('position')}")
    else:
        s.append('no bots experiment in this session')
    if r.get('malformed'):
        s.append(f"bot reports cut short or garbled (skipped): {len(r['malformed'])}, the last: {r['malformed'][-1][:160]}")
    if r.get('crash_query_error'):
        s.append(f"crash status UNKNOWN: {r['crash_query_error']}")
    elif r['crashes']:
        for c in r['crashes']:
            s.append(f"CRASH {c['time']}: {c['exe']} in {c['module']} at +{c['offset']} (exception {c['code']})")
    else:
        s.append('no Xenia crash recorded by Windows at the session\'s end' +
                 ('; Xenia exited normally' if r.get('normal_exit') else ''))
    for c in r.get('other_crashes', []):
        s.append(f"another session's crash near this one (not counted): {c['time']} {c['exe']} at +{c['offset']}")
    s.append('last lines:' + ''.join(f'\n  {l[:160]}' for l in r['last_lines']))
    return '\n'.join(s)


def main(argv):
    if any(a in ('-h', '--help') for a in argv[1:]):
        print(__doc__)
        return 0
    opts = dict(zip(argv[1:], argv[2:]))
    log = opts.get('--log')
    # the folder names the executables Windows' crash records are matched on; a log given on its own is read with
    # the usual Xenia executable names
    folder = opts.get('--xenia') or (os.path.dirname(os.path.abspath(log)) if log else xenia_folder())
    log = log or os.path.join(folder, 'xenia.log')
    if not os.path.isfile(log):
        raise FileNotFoundError(log)
    r = read_log(log)
    written = os.path.getmtime(log)
    r['log_time'] = datetime.datetime.fromtimestamp(written).strftime('%Y-%m-%d %I:%M %p')
    exes = [f for f in os.listdir(folder) if f.lower().startswith('xenia') and f.lower().endswith('.exe')] if folder else []
    # a crash ends the session: Windows records it within moments of the log's last write (the file's creation
    # time is no guide - each session overwrites it in place)
    end = datetime.datetime.fromtimestamp(written)
    window = datetime.timedelta(minutes=CRASH_WINDOW_MIN)
    r['normal_exit'] = any('Cheap-skate exit' in l for l in r['last_lines'])
    start = session_start(log)
    r['crashes'], r['other_crashes'], r['crash_query_error'] = [], [], None
    try:
        near = [c for c in crashes(exes or ['xenia_canary.exe', 'xenia_canary_netfix.exe'], end - window)
                if datetime.datetime.fromisoformat(c['time']) <= end + window]
    except CrashQueryError as e:
        near, r['crash_query_error'] = [], str(e)
    for c in near:
        # a session that exited normally did not crash; a record older than the session's start is an earlier one's
        ours = not r['normal_exit'] and (start is None or datetime.datetime.fromisoformat(c['time']) >= start)
        r['crashes' if ours else 'other_crashes'].append(c)
    print(report(r, '--positions' in argv))
    if '--json' in opts:
        with open(opts['--json'], 'w', encoding='utf-8', newline='\n') as f:
            json.dump(r, f, indent=1)
    return 2 if r['crash_query_error'] else 1 if r['crashes'] else 0


def session_start(log):
    """The session's start (local time, to the second) from session.json beside the log (run_xenia_probe.ps1), or None."""
    path = os.path.join(os.path.dirname(os.path.abspath(log)), 'session.json')
    try:
        started = json.load(open(path, encoding='utf-8-sig'))['started']
        return datetime.datetime.fromisoformat(started[:19])
    except (OSError, ValueError, KeyError, TypeError):
        return None


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(SUITE, 'core'))
    from bfbc2core import errors
    errors.setup('xeniarun', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
