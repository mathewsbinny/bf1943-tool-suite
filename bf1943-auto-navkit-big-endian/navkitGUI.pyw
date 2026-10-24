"""NavKit (BF1943) - a small Windows front end for navkit.py (double-click to start; needs Python 3 with numpy).

Every button runs the matching navkit.py step for the chosen level of your BF1943 copy and shows its output. Nothing
is started in the game: the samples are computed from the level's data.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'core'))
from bfbc2core import errors, game1943  # noqa: E402
import navkit  # noqa: E402

PYTHON = os.path.join(os.path.dirname(sys.executable), 'python.exe')   # console python for the steps
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable
STUDIO = os.path.join(HERE, '..', 'bf1943-navigation-studio', 'studio.py')

STEPS = [('prepare', '1. Prepare', 'Objects, collision, flags, ladders, terrain and water from the level\'s bundles'),
         ('sample', '2. Sample', 'Collision samples computed offline: 0.8 m, then 0.2 m in detailed sectors (about 30 min)'),
         ('build', '3. Build', 'The navigation build with BF1943\'s soldier (about 15 min)'),
         ('check', '4. Check', 'Structure, and every spawn and pair of flags joined by a route')]
DONE_KEY = {'prepare': 'prepared', 'sample': 'sampled', 'build': 'built', 'check': 'checked'}


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.lines = queue.Queue()
        root.title('BF1943 NavKit')
        root.minsize(780, 520)
        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        try:
            self.game = game1943.find_game()
        except FileNotFoundError as e:
            messagebox.showerror('NavKit', str(e))
            root.destroy()
            return

        top = ttk.Frame(root, padding=10)
        top.pack(fill='both', expand=True)
        row = ttk.Frame(top)
        row.pack(fill='x')
        ttk.Label(row, text='Level:').pack(side='left')
        self.level = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.level, state='readonly', width=30,
                                  values=game1943.levels(self.game))
        self.combo.pack(side='left', padx=(6, 6))
        self.combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        ttk.Button(row, text='Open output folder', command=self.open_folder).pack(side='right')
        self.view = ttk.Button(row, text='Show in 3D', command=self.show_3d)
        self.view.pack(side='right', padx=6)
        self.info = ttk.Label(top, text='', foreground='#555')
        self.info.pack(fill='x', pady=(8, 0))

        steps = ttk.LabelFrame(top, text='Steps', padding=8)
        steps.pack(fill='x', pady=(10, 0))
        self.buttons, self.marks = [], {}
        for r, (cmd, label, tip) in enumerate(STEPS):
            b = ttk.Button(steps, text=label, width=16, command=lambda c=cmd: self.run_step(c))
            b.grid(row=r, column=0, sticky='w', pady=2)
            self.buttons.append(b)
            m = ttk.Label(steps, text='', width=3)
            m.grid(row=r, column=1)
            self.marks[cmd] = m
            ttk.Label(steps, text=tip, foreground='#555').grid(row=r, column=2, sticky='w')
        side = ttk.Frame(steps)
        side.grid(row=0, column=3, rowspan=4, sticky='ne', padx=(20, 0))
        steps.columnconfigure(2, weight=1)
        b = ttk.Button(side, text='Run all (1-4)', width=16, command=lambda: self.run_step('all'))
        b.pack(pady=2)
        self.buttons.append(b)
        self.cancel = ttk.Button(side, text='Cancel', width=16, command=self.stop, state='disabled')
        self.cancel.pack(pady=(10, 2))

        logf = ttk.Frame(top)
        logf.pack(fill='both', expand=True, pady=(10, 0))
        self.log = tk.Text(logf, height=12, wrap='none', font=('Consolas', 9), state='disabled')
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.status = ttk.Label(top, text='Ready', anchor='w')
        self.status.pack(fill='x', pady=(6, 0))
        if self.combo['values']:
            self.level.set(self.combo['values'][0])
        self.refresh()
        root.after(100, self.pump)
        root.protocol('WM_DELETE_WINDOW', self.close)

    # --- levels and state ------------------------------------------------------------------------------
    def refresh(self):
        lv = self.level.get()
        if not lv:
            return
        p = navkit.paths(lv)
        s = json.load(open(p['settings'], encoding='utf-8')) if os.path.isfile(p['settings']) else {}
        for cmd, m in self.marks.items():
            v = s.get(DONE_KEY[cmd])
            m['text'] = ('✔' if 'FAILED' not in v else '✘') if v else ''
        if s:
            ds = s.get('detailed_sectors')
            self.info['text'] = (f'Sea level {s.get("sea_level")} m, sector offsets {s["sector_offsets"][0]} / '
                                 f'{s["sector_offsets"][1]}' + (f', {len(ds)} detailed sectors' if ds is not None else '') +
                                 (f'.  Checked {s["checked"]}.' if 'checked' in s else '.'))
        else:
            self.info['text'] = 'Not prepared yet.'
        self.view['state'] = 'normal' if os.path.isfile(p['hpd']) else 'disabled'

    # --- running steps ---------------------------------------------------------------------------------
    def run_step(self, cmd):
        lv = self.level.get()
        if not lv or self.proc:
            return
        args = [PYTHON, '-B', '-u', os.path.join(HERE, 'navkit.py'), cmd, lv, '--game', self.game]
        self.write(f'\n> navkit.py {cmd} {lv}\n')
        self.set_busy(True, f'Running {cmd} on {lv} ...')
        self.proc = subprocess.Popen(args, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self.reader, args=(self.proc, cmd), daemon=True).start()

    def reader(self, proc, cmd):
        self.tail = []
        for line in proc.stdout:
            self.lines.put(line)
            self.tail = (self.tail + [line.rstrip()])[-10:]
        rc = proc.wait()
        self.lines.put(('done', cmd, rc))

    def pump(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    _, cmd, rc = item
                    cancelled, self.cancelled = getattr(self, 'cancelled', False), False
                    self.proc = None
                    self.set_busy(False, f'{cmd}: ' + ('finished' if rc == 0 else 'failed - see the log above'))
                    self.refresh()
                    errors.logger().info(f'{cmd} {self.level.get()}: exit {rc}')
                    if rc != 0 and not cancelled:
                        said = '\n'.join(l for l in getattr(self, 'tail', []) if l.strip())[-1500:]
                        errors.logger().warning(f'{cmd} failed; last lines:\n{said}')
                        messagebox.showwarning('NavKit', f'{cmd} did not finish (exit code {rc}).\n\nIt said last:\n'
                                                         f'{said or "(no output)"}\n\nThe whole output is above; the log:\n'
                                                         f'{errors._state.get("path")}')
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def stop(self):
        if not self.proc:
            return
        self.cancelled = True
        navkit.end(self.proc)                   # the step and the samplers it started
        self.write('\ncancelled\n')

    def set_busy(self, busy, text):
        for b in self.buttons:
            b['state'] = 'disabled' if busy else 'normal'
        self.combo['state'] = 'disabled' if busy else 'readonly'
        self.cancel['state'] = 'normal' if busy else 'disabled'
        self.status['text'] = text

    def write(self, text):
        self.log['state'] = 'normal'
        self.log.insert('end', text)
        self.log.see('end')
        self.log['state'] = 'disabled'

    def open_folder(self):
        lv = self.level.get()
        d = navkit.paths(lv)['work'] if lv else game1943.work_root()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def show_3d(self):
        lv = self.level.get()
        p = navkit.paths(lv)
        subprocess.Popen([PYTHON, '-B', STUDIO, lv, '--hpd', p['hpd'], '--game', self.game],
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def close(self):
        if self.proc and not messagebox.askyesno('NavKit', 'A step is still running. Cancel it and quit?'):
            return
        self.stop()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on high-DPI screens
    except Exception:
        pass
    errors.setup('navkit-gui')
    with errors.guard():
        root = tk.Tk()
        root.report_callback_exception = lambda t, v, tb: errors.report(v, tb=tb, where='a button')
        App(root)
        root.mainloop()


if __name__ == '__main__':
    main()
