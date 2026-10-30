r"""navkitGUI.pyw's Cancel and close while a step runs (a made-up project and a sleeping child process stand in for a
step; the game is never looked for or started): the window does not freeze while the clean-up (ending the step and what
it started, closing the game, restoring opfor.ini) runs, the clean-up runs off the window's thread, the step's process
ends, and closing the window during a step closes it after the clean-up.

  python -B test_navkit_gui.py
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
loader = importlib.machinery.SourceFileLoader('navkitGUI', os.path.join(KIT, 'navkitGUI.pyw'))
spec = importlib.util.spec_from_loader('navkitGUI', loader)
gui = importlib.util.module_from_spec(spec)
loader.exec_module(gui)
navkit = gui.navkit

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def fake_project(root):
    return SimpleNamespace(root=root, work=os.path.join(root, 'work'), pathdata=lambda lv: os.path.join(root, 'none.hpd'),
                           installed=lambda lv: False, dice_pathdata=lambda lv: None,
                           samples=lambda lv, suffix='': os.path.join(root, 'none.bin'))


CHILD = r'''
import subprocess, sys, time
g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])    # what a step starts: the game launcher
open(sys.argv[1], 'w').write(str(g.pid))
time.sleep(60)
'''


def alive(pid):
    r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return str(pid) in r.stdout


def step(app, pid_file=None):
    """A running step: a child process that would run for a minute (and, with pid_file, a process it started), read as
    run_step reads it."""
    args = [sys.executable, '-c', CHILD, pid_file] if pid_file else [sys.executable, '-c', 'import time; time.sleep(60)']
    proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    app.proc = proc
    app.set_busy(True, 'Running test step ...')
    threading.Thread(target=app.reader, args=(proc, 'sample'), daemon=True).start()
    return proc


def pump_until(root, cond, timeout=20.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        try:
            root.update()
        except tk.TclError:
            break
        time.sleep(0.02)
    return cond()


def main():
    calls = []

    def slow_close():
        calls.append(('close_game', threading.current_thread() is threading.main_thread()))
        time.sleep(2.0)                                       # as long as taskkill and its waits can take

    saved = (navkit.Project, navkit.level_names, navkit.close_game, navkit.recover_ini)
    navkit.Project = lambda *a, **k: fake_project(os.path.dirname(KIT))
    navkit.level_names = lambda pr: [('sp_y', True)]
    navkit.close_game = slow_close
    navkit.recover_ini = lambda pr, log: calls.append(('recover_ini',))
    root = tk.Tk()
    root.withdraw()
    tmp = tempfile.mkdtemp()
    pid_file = os.path.join(tmp, 'grandchild.pid')
    started = None
    try:
        app = gui.App(root)
        proc = step(app, pid_file)
        t1 = time.time()
        while not os.path.isfile(pid_file) or not open(pid_file).read().strip():
            if time.time() - t1 > 15:
                break
            time.sleep(0.05)
        with open(pid_file) as f:
            started = int(f.read().strip())
        t0 = time.time()
        app.stop()
        took = time.time() - t0
        check(took < 0.5, f'Cancel returns at once ({took:.2f} s; the clean-up takes 2 s)')
        check(pump_until(root, lambda: not getattr(app, 'cleaning', True)), 'the clean-up finishes')
        check(proc.poll() is not None, 'the step\'s process has ended')
        check(not alive(started), 'so has the process the step started (the game launcher)')
        check(('close_game', False) in calls and ('recover_ini',) in calls,
              f'the game is closed and opfor.ini restored off the window\'s thread ({calls})')
        check(str(app.buttons[0]['state']) == 'normal' and app.proc is None, 'the buttons work again after the clean-up')
        calls.clear()
        proc = step(app)
        gui.messagebox.askyesno = lambda *a, **k: True        # "A step is still running. Cancel it and quit?" - yes
        destroyed = []
        root.destroy = lambda: (destroyed.append(('recover_ini',) in calls), tk.Tk.destroy(root))
        t0 = time.time()
        app.close()
        took = time.time() - t0
        check(took < 0.5 and not destroyed, f'closing during a step returns at once, the window still open ({took:.2f} s)')
        pump_until(root, lambda: bool(destroyed))
        check(destroyed == [True] and proc.poll() is not None,
              f'the window closes after the clean-up (opfor.ini restored first), the step ended ({destroyed})')
    finally:
        (navkit.Project, navkit.level_names, navkit.close_game, navkit.recover_ini) = saved
        try:
            root.destroy()
        except tk.TclError:
            pass
        if started and alive(started):                       # only this test's own process
            subprocess.run(['taskkill', '/F', '/PID', str(started)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
