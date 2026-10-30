"""The suite's C# tools, built with their build.bat (the .NET Framework 4 compiler that ships with Windows) whenever
the exe would not be the current code.

    exe = csbuild.ensure('leveldiff')         # LevelDiff.exe, rebuilt first if needed; RuntimeError if it cannot be

An exe is rebuilt when it is missing, when any of its sources (compiled-in ones too: LevelDiff compiles DbxEdit.cs
in) or its build.bat is newer than it, or when their SHA-256 differs from the one recorded at its last build here
(<exe>.sources next to it, not committed). So a test or the hub never runs an exe built from older code.
"""
import hashlib
import json
import os
import subprocess

SUITE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DBX = os.path.join(SUITE, 'bf1943-dbx-tools')
LEVEL_DIFF = os.path.join(SUITE, 'bf1943-diff-workbench', 'level-diff')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def tools():
    """name -> (exe, build.bat, sources compiled into it). LevelDiff's DbxEdit.cs follows DBX_TOOLS, as its build.bat."""
    dbx_tools = os.environ.get('DBX_TOOLS') or DBX
    return {
        'dbxedit': (os.path.join(DBX, 'DbxEdit.exe'), os.path.join(DBX, 'build.bat'), [os.path.join(DBX, 'DbxEdit.cs')]),
        'dbxgui': (os.path.join(DBX, 'DbxEditGUI.exe'), os.path.join(DBX, 'build.bat'),
                   [os.path.join(DBX, 'DbxEditGUI.cs'), os.path.join(DBX, 'DbxEdit.cs')]),
        'leveldiff': (os.path.join(LEVEL_DIFF, 'LevelDiff.exe'), os.path.join(LEVEL_DIFF, 'build.bat'),
                      [os.path.join(LEVEL_DIFF, 'LevelDiff.cs'), os.path.join(dbx_tools, 'DbxEdit.cs')]),
    }


def sources_hash(files):
    h = hashlib.sha256()
    for f in files:
        with open(f, 'rb') as fh:
            data = fh.read()
        h.update(os.path.basename(f).encode('utf-8') + b'\0' + str(len(data)).encode('ascii') + b'\0' + data)
    return h.hexdigest()


def stamp_path(exe):
    return exe + '.sources'


def stale(exe, bat, sources):
    """Why exe must be rebuilt (words), or None when it is current."""
    if not os.path.isfile(exe):
        return 'not built yet'
    newer = [os.path.basename(f) for f in sources + [bat] if os.path.getmtime(f) > os.path.getmtime(exe)]
    if newer:
        return 'older than ' + ', '.join(newer)
    try:
        with open(stamp_path(exe), encoding='utf-8') as f:
            recorded = json.load(f).get('sha256')
    except (OSError, ValueError):
        return 'no record of the sources it was built from'
    if recorded != sources_hash(sources + [bat]):
        return 'built from other sources'
    return None


def build(bat, log=None):
    """Runs build.bat and records, for each exe it builds, the sources it was built from."""
    r = subprocess.run(['cmd', '/c', bat], capture_output=True, text=True, errors='replace', creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f'{bat} failed (the C# compiler that comes with Windows, .NET Framework 4, is needed):\n'
                           f'{r.stdout}{r.stderr}'.strip())
    for exe, b, sources in tools().values():
        if os.path.normcase(b) == os.path.normcase(bat) and os.path.isfile(exe):
            tmp = stamp_path(exe) + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                json.dump({'sha256': sources_hash(sources + [b]), 'sources': [os.path.basename(s) for s in sources + [b]]}, f)
            os.replace(tmp, stamp_path(exe))
    if log:
        log(r.stdout.strip())


def ensure(name, log=None):
    """The tool's exe, built first if it is missing or not from the current sources."""
    exe, bat, sources = tools()[name]
    why = stale(exe, bat, sources)
    if why:
        if log:
            log(f'building {os.path.basename(exe)} ({why})')
        build(bat, log)
        why = stale(exe, bat, sources)
        if why:
            raise RuntimeError(f'{os.path.basename(exe)} is still {why} after {bat}; run it to see why')
    return exe
