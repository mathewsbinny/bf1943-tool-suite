r"""The suite window (BF1943 Tool Suite.pyw) on a made-up game folder (no game data; its message windows replaced):

- a navkit.json that is valid JSON but not NavKit's record does not stop the window opening;
- an error while the output pane is filled (the game folder gone when a Levels run ends) is reported, and the pane
  goes on (2026-10-30: the 100 ms pump stopped for good, and no output reached the pane again);
- LevelDiff's exit code 1 (the files differ) is not a failure; its 2 (bad input, a crash) is;
- a Python tool's output is UTF-8: an extract into a folder named in Cyrillic finishes (it died on its last print);
- Stop is per process: another tool's failure is still told, and the stopped one is not called a failure.

  python -B tests\test_hub.py
"""
import importlib.util
import os
import shutil
import struct
import sys
import tempfile
import time

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def bundle(entries):
    """A stored FbRB v2 bundle of [(name, bytes)] (fbrb1943.py's layout)."""
    sys.path.insert(0, os.path.join(SUITE, 'bf1943-fbrb-tool'))
    import fbrb1943
    strings, offs = b'', {}
    for s in [n for n, _ in entries] + ['RawFileAsset']:
        if s not in offs:
            offs[s] = len(strings)
            strings += s.encode('ascii') + b'\0'
    d = struct.pack('>II', 2, len(strings)) + strings + struct.pack('>I', len(entries))
    pos = 0
    for name, data in entries:
        d += struct.pack('>6I', offs[name], 0, pos, len(data), len(data), offs['RawFileAsset'])
        pos += len(data)
    d += b'\0' + struct.pack('>I', pos)
    z = fbrb1943.gzip_member(d, b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x0b')
    return b'FbRB' + struct.pack('>I', len(z)) + z + b''.join(x for _, x in entries)


def main():
    tmp = tempfile.mkdtemp(prefix='hub_test_')
    saved_env = dict(os.environ)
    root = None
    try:
        os.environ['LOCALAPPDATA'] = tmp
        game = os.path.join(tmp, 'game')
        os.makedirs(os.path.join(game, 'levels', 'lv'))
        with open(os.path.join(game, 'levels', 'lv', 'level-00.fbrb'), 'wb') as f:
            f.write(bundle([('a.res', b'a')]))
        os.environ['BF1943_GAME'] = game
        os.environ['BF1943_WORK'] = os.path.join(tmp, 'work')
        nk = os.path.join(tmp, 'work', 'levels', 'lv', 'navkit')
        os.makedirs(nk)
        with open(os.path.join(nk, 'navkit.json'), 'w', encoding='utf-8', newline='\n') as f:
            f.write('["not", "a", "record"]\n')
        spec = importlib.util.spec_from_file_location('hub', os.path.join(SUITE, 'BF1943 Tool Suite.pyw'))
        hub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hub)
        hub.errors.setup('test-hub-quick', gui=False)
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        shown = []
        hub.Hub.notify = lambda self, kind, title, text: shown.append(text.splitlines()[0])
        try:
            h = hub.Hub(root)
            opened = list(h.tree.get_children()) == ['lv']
        except Exception as e:
            h, opened = None, f'{type(e).__name__}: {e}'
        check(opened is True, f'the window opens with a navkit.json that is not a record ({opened})')
        if h is None:                                   # the other checks still run, on a window that opens
            os.remove(os.path.join(nk, 'navkit.json'))
            h = hub.Hub(root)
        shown.clear()

        def pane():
            return h.text.get('1.0', 'end')

        def drive(until, timeout=60):
            t0 = time.time()
            while not until() and time.time() - t0 < timeout:
                root.update()
                time.sleep(0.05)
            root.update()

        # the pump after an error: the game folder gone when a Levels run ends
        moved = game + '-moved'
        os.rename(game, moved)
        calls = []
        real_after = root.after
        root.after = lambda ms, fn=None, *a: calls.append(getattr(fn, '__name__', fn))
        h.lines.put(('done', 'Levels: navigation of lv', 0, True, (0,), -1))
        h.lines.put(('done', 'a malformed end'))            # an item that raises whatever the code
        h.lines.put('a later tool line\n')
        try:
            h.pump()
            raised = None
        except Exception as e:
            raised = f'{type(e).__name__}: {e}'
        root.after = real_after
        os.rename(moved, game)
        check(raised is None and 'pump' in calls and 'a later tool line' in pane(),
              f'an error while filling the pane is reported and the pump goes on (raised {raised}, rescheduled '
              f'{"pump" in calls})')

        # LevelDiff: 1 = the files differ (not a failure), 2 = bad input or a crash (a failure)
        a, b = os.path.join(tmp, 'a.dbx'), os.path.join(tmp, 'b.dbx')
        for p in (a, b):
            with open(p, 'wb') as f:
                f.write(b'made-up')
        for code, failure in ((1, False), (2, True)):
            fake = os.path.join(tmp, f'leveldiff{code}.bat')
            with open(fake, 'w', encoding='ascii', newline='\r\n') as f:
                f.write(f'@echo made-up LevelDiff\n@exit /b {code}\n')
            hub.tool = lambda name, fake=fake: fake
            shown.clear()
            h.ld_a.set(a)
            h.ld_b.set(b)
            h.diff()
            drive(lambda: h.proc.poll() is not None and h.lines.empty() and h.status.get() == 'Ready.')
            check(bool(shown) == failure, f'LevelDiff exit {code}: {"a failure" if failure else "not a failure"} '
                                          f'(windows: {shown})')

        # a Python tool's output is UTF-8: fbrb1943.py extract into a Cyrillic folder
        src = os.path.join(tmp, 'in.fbrb')
        with open(src, 'wb') as f:
            f.write(bundle([('a.res', b'a')]))
        out = os.path.join(tmp, 'Карта')
        shown.clear()
        h.run([hub.PYTHON, hub.SCRIPTS['fbrb'], 'extract', src, out], 'Bundle extract')
        drive(lambda: h.proc.poll() is not None and h.lines.empty() and h.status.get() == 'Ready.')
        check(h.proc.returncode == 0 and not shown and out in pane(),
              f'an extract into a Cyrillic folder finishes and its output is shown (exit {h.proc.returncode})')

        # Stop is per process: a launched tool fails, then the tab's tool is stopped
        shown.clear()
        h.launch([hub.PYTHON, '-c', 'import sys; sys.exit(3)'], 'Made-up window')
        time.sleep(1.5)                              # its end is queued first (the pane not pumped meanwhile)
        h.run([hub.PYTHON, '-c', 'import time; time.sleep(60)'], 'Made-up long run')
        time.sleep(0.5)
        h.stop()
        drive(lambda: h.proc.poll() is not None and h.lines.empty() and h.status.get() == 'Ready.')
        check('Made-up window closed with an error' in pane() and not shown,
              f'Stop is per process: the other tool\'s failure is told, the stopped one is no failure (windows: {shown})')
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        os.environ.clear()
        os.environ.update(saved_env)
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
