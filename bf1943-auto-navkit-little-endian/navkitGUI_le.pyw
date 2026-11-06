"""NavKit - a small Windows front end for navkit_le.py (double-click to start; needs Python 3 with numpy).

Every button runs the matching navkit_le.py step for the chosen level and shows its output; see QUICKSTART.md.
"""
import enum
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from typing import IO, cast
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_LE)
from bfbc2core import errors  # noqa: E402
import navkit_le as navkit  # noqa: E402

PYTHON = os.path.join(os.path.dirname(sys.executable), 'python.exe')   # console python for the steps
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable

STEPS = [('prepare', '1. Prepare', 'Unpack the level, extract its objects, choose the sampling box'),
         ('sample', '2. Sample in game', 'Starts the game twice to sample the level\'s collision (a few minutes)'),
         ('build', '3. Build', 'Generate pathdata.hpd from the samples'),
         ('check', '4. Check', 'Score the result (against the original path data if the level has one)')]
AREA_FIELDS = ('minx', 'minz', 'maxx', 'maxz', 'ybot', 'ytop')     # the sampling box, then its bottom and top

# what the step's reader thread and the clean-up put on the window's queue: an output line, ('done', step, exit
# code) when the step ended, or ('cleaned',) when a cancelled step's clean-up finished
type Message = str | tuple[str, str, int] | tuple[str]


class RunState(enum.Enum):
    """Where the window's step is (one step at a time). A cancelled step is over once two things have happened, in
    either order: its process ended ('done') and the clean-up finished ('cleaned') (2026-11-05: a clean-up's end
    handled before the step's freed the window while the step's process was still recorded, so the buttons did
    nothing, and the step's late end then said 'failed')."""
    IDLE = 'idle'                  # no step: the buttons work
    RUNNING = 'running'            # a step runs: Cancel works
    CANCELLING = 'cancelling'      # cancelled: the clean-up runs, the step's end not handled yet
    CLEANING = 'cleaning'          # cancelled: the step's end handled, the clean-up still runs
    ENDING = 'ending'              # cancelled: the clean-up finished, the step's end not handled yet


# (state, event) -> the next state. Events: 'start' (a step started), 'cancel' (Cancel, or closing the window during
# a step), 'done' (the step's process ended), 'cleaned' (a cancelled step's clean-up finished)
TRANSITIONS: dict[tuple[RunState, str], RunState] = {
    (RunState.IDLE, 'start'): RunState.RUNNING,
    (RunState.RUNNING, 'done'): RunState.IDLE,
    (RunState.RUNNING, 'cancel'): RunState.CANCELLING,
    (RunState.CANCELLING, 'done'): RunState.CLEANING,
    (RunState.CANCELLING, 'cleaned'): RunState.ENDING,
    (RunState.CLEANING, 'cleaned'): RunState.IDLE,
    (RunState.ENDING, 'done'): RunState.IDLE,
}


def transition(state: RunState, event: str) -> RunState:
    """The state after event; ValueError for an event that cannot happen in state."""
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise ValueError(f'{event!r} cannot happen while the window is {state.value}') from None


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: subprocess.Popen[str] | None = None
        self.state = RunState.IDLE     # the step's run state: changed only through transition()
        self.after_cleanup: Callable[[], object] | None = None
        self.saved: list[str] = []
        self.tail: list[str] = []      # the running step's last output lines, for its failure message
        self.lines: queue.Queue[Message] = queue.Queue()
        root.title('BC2 NavKit')
        root.minsize(760, 520)
        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        try:
            self.project = navkit.Project()
        except navkit.NavKitError as e:
            messagebox.showerror('NavKit', str(e))
            root.destroy()
            return

        top = ttk.Frame(root, padding=10)
        top.pack(fill='both', expand=True)

        row = ttk.Frame(top)
        row.pack(fill='x')
        ttk.Label(row, text='Level:').pack(side='left')
        self.level = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.level, state='readonly', width=34)
        self.combo.pack(side='left', padx=(6, 6))
        self.combo.bind('<<ComboboxSelected>>', lambda event: self.refresh())
        self.only_ai = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text='Only levels with AI', variable=self.only_ai,
                        command=self.fill_levels).pack(side='left')
        ttk.Button(row, text='Open output folder', command=self.open_folder).pack(side='right')

        box = ttk.LabelFrame(top, text='Sampling area (filled in by Prepare; edit to override)', padding=8)
        box.pack(fill='x', pady=(10, 0))
        self.vars: dict[str, tk.StringVar] = {}
        labels = ('min X', 'min Z', 'max X', 'max Z', 'bottom Y', 'top Y')
        for i, (key, label) in enumerate(zip(AREA_FIELDS, labels)):
            ttk.Label(box, text=label).grid(row=0, column=i * 2, sticky='e', padx=(8 if i else 0, 4))
            var = tk.StringVar()
            ttk.Entry(box, textvariable=var, width=8).grid(row=0, column=i * 2 + 1)
            self.vars[key] = var
        self.area_note = ttk.Label(box, text='', foreground='#555')
        self.area_note.grid(row=1, column=0, columnspan=12, sticky='w', pady=(6, 0))

        steps = ttk.LabelFrame(top, text='Steps', padding=8)
        steps.pack(fill='x', pady=(10, 0))
        self.buttons: list[ttk.Button] = []
        self.marks: dict[str, ttk.Label] = {}
        for row_index, (cmd, label, tip) in enumerate(STEPS):
            button = ttk.Button(steps, text=label, width=20, command=lambda step=cmd: self.run_step(step))
            button.grid(row=row_index, column=0, sticky='w', pady=2)
            self.buttons.append(button)
            mark = ttk.Label(steps, text='', width=3)
            mark.grid(row=row_index, column=1)
            self.marks[cmd] = mark
            ttk.Label(steps, text=tip, foreground='#555').grid(row=row_index, column=2, sticky='w')
        side = ttk.Frame(steps)
        side.grid(row=0, column=3, rowspan=4, sticky='ne', padx=(20, 0))
        steps.columnconfigure(2, weight=1)
        for label, cmd in (('Run all (1-4)', 'all'), ('Install in game', 'install'), ('Restore original', 'restore')):
            button = ttk.Button(side, text=label, width=18, command=lambda step=cmd: self.run_step(step))
            button.pack(pady=2)
            self.buttons.append(button)
        self.cancel = ttk.Button(side, text='Cancel', width=18, command=self.stop, state='disabled')
        self.cancel.pack(pady=(10, 2))
        self.installed = ttk.Label(steps, text='', foreground='#555')
        self.installed.grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 0))

        log_frame = ttk.Frame(top)
        log_frame.pack(fill='both', expand=True, pady=(10, 0))
        self.log = tk.Text(log_frame, height=12, wrap='none', font=('Consolas', 9), state='disabled')
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.status = ttk.Label(top, text='Ready', anchor='w')
        self.status.pack(fill='x', pady=(6, 0))

        self.fill_levels()
        root.after(100, self.pump)
        root.protocol('WM_DELETE_WINDOW', self.close)

    # --- levels and state ------------------------------------------------------------------------------
    def fill_levels(self) -> None:
        names = navkit.level_names(self.project)
        shown = [lv for lv, has_ai in names if has_ai or not self.only_ai.get()]
        self.combo['values'] = shown
        if self.level.get() not in shown:
            self.level.set(shown[0] if shown else '')
        self.refresh()

    def refresh(self) -> None:
        """The chosen level's settings into the sampling-area fields, its steps' ticks and its installed state."""
        lv = self.level.get()
        if not lv:
            return
        out = os.path.join(self.project.work, 'navkit', lv)
        settings = None
        self.saved = []
        if os.path.isfile(os.path.join(out, 'navkit.json')):
            settings = json.load(open(os.path.join(out, 'navkit.json')))
            for key, value in zip(('minx', 'minz', 'maxx', 'maxz'), settings['box']):
                self.vars[key].set(f'{value:g}')
            self.vars['ybot'].set(f'{settings["y"][0]:g}')
            self.vars['ytop'].set(f'{settings["y"][1]:g}')
            self.saved = [self.vars[key].get() for key in AREA_FIELDS]
            self.area_note['text'] = (f'Box from: {settings["box_source"]}.  '
                                      f'Sector offsets {settings["xoff"]}, {settings["zoff"]} '
                                      f'({settings["offsets_source"]}).')
        else:
            for var in self.vars.values():
                var.set('')
            self.area_note['text'] = 'Run Prepare to fill these in (or type your own and Prepare keeps them).'
        done = {'prepare': settings is not None,
                'sample': all(os.path.isfile(self.project.samples(lv, suffix)) for _, _, suffix in navkit.AGENTS),
                'build': os.path.isfile(os.path.join(out, 'pathdata.hpd')),
                'check': os.path.isfile(os.path.join(out, 'check.txt'))}
        for step, mark in self.marks.items():
            mark['text'] = '✔' if done[step] else ''
        if self.project.installed(lv):
            self.installed['text'] = 'NavKit\'s path data is INSTALLED in the game for this level.'
        elif self.project.dice_pathdata(lv):
            self.installed['text'] = 'The game uses the level\'s original path data.'
        else:
            self.installed['text'] = ('This level has no AI path data in the game '
                                      '(see QUICKSTART.md: levels without AI).')

    def area_args(self) -> list[str]:
        """--box and --y for the step from the sampling-area fields; none when they are empty or as saved."""
        values = [self.vars[key].get().strip() for key in AREA_FIELDS]
        if not all(values) or values == self.saved:
            return []   # empty or unchanged: navkit_le.py uses its own / the saved area
        try:
            [float(value) for value in values]
        except ValueError as e:
            raise ValueError('The sampling area must be numbers.') from e
        return ['--box', ','.join(values[:4]), '--y', ','.join(values[4:])]

    # --- running steps ---------------------------------------------------------------------------------
    def run_step(self, cmd: str) -> None:
        """Starts navkit_le.py <cmd> on the chosen level (after a confirmation where the step needs one)."""
        lv = self.level.get()
        if not lv or self.state is not RunState.IDLE:
            return
        args = [PYTHON, '-u', os.path.join(HERE, 'navkit_le.py'), cmd, lv]
        try:
            if cmd in ('prepare', 'sample', 'all'):
                args += self.area_args()
        except ValueError as e:
            messagebox.showerror('NavKit', str(e))
            return
        if cmd in ('sample', 'all'):
            if not messagebox.askokcancel('NavKit', f'NavKit will now start the game twice to sample {lv.upper()}.\n\n'
                                          'Each run takes a few minutes; the game window may stay black. '
                                          'Do not use the game while it runs - NavKit closes it when done.\n\n'
                                          'Close the game first if it is open.'):
                return
            args.append('--yes')
        if cmd == 'install' and not messagebox.askokcancel(
                'NavKit', f'Install NavKit\'s path data for {lv.upper()} into the game?\n\n'
                          'The original file is kept and can be put back with "Restore original".'):
            return
        if cmd == 'restore' and not messagebox.askokcancel(
                'NavKit', f'Put the original path data of {lv.upper()} back?'):
            return
        self.write(f'\n> navkit_le.py {" ".join(args[3:])}\n')
        self.set_busy(True, f'Running {cmd} on {lv} ...')
        self.proc = subprocess.Popen(args, cwd=self.project.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, errors='replace',
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.state = transition(self.state, 'start')
        threading.Thread(target=self.reader, args=(self.proc, cmd), daemon=True).start()

    def reader(self, proc: 'subprocess.Popen[str]', cmd: str) -> None:
        """The step's output, line by line, onto the queue (a worker thread), then its end."""
        self.tail = []
        for line in cast('IO[str]', proc.stdout):   # started with stdout=PIPE: never None
            self.lines.put(line)
            self.tail = (self.tail + [line.rstrip()])[-10:]
        returncode = proc.wait()
        self.lines.put(('done', cmd, returncode))

    def pump(self) -> None:
        """Every 100 ms on the window's thread: the queue's output lines into the log, and a step's end handled."""
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple) and item[0] == 'cleaned':
                    if self.cleaned():
                        return                   # the window is gone
                elif isinstance(item, tuple) and len(item) == 3:
                    _, cmd, returncode = item
                    self.step_ended(cmd, returncode)
                elif isinstance(item, str):
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def step_ended(self, cmd: str, returncode: int) -> None:
        """A step's process ended: the window is free again (a cancelled step's once its clean-up has finished too),
        and a failure that was not a cancel is shown with the step's last lines."""
        was = self.state
        self.state = transition(was, 'done')
        self.proc = None
        if was is RunState.CANCELLING:   # stays busy until the clean-up has finished
            return
        if was is RunState.ENDING:       # cancelled, and its clean-up has finished: over
            self.set_busy(False, 'cancelled')
            self.refresh()
            return
        self.set_busy(False, f'{cmd}: ' + ('finished' if returncode == 0 else 'failed - see the log above'))
        self.refresh()
        errors.logger().info(f'{cmd} {self.level.get()}: exit {returncode}')
        if returncode != 0:
            said = '\n'.join(line for line in self.tail if line.strip())[-1500:]
            errors.logger().warning(f'{cmd} failed; last lines:\n{said}')
            log_path = errors._state.get("path")     # the log's path (errors has no public name for it)
            messagebox.showwarning('NavKit', f'{cmd} did not finish (exit code {returncode}).\n\nIt said last:\n'
                                             f'{said or "(no output)"}\n\nThe whole output is above; the log:\n'
                                             f'{log_path}')

    def stop(self, then: Callable[[], object] | None = None) -> None:
        # Cancels the running step without blocking the window: the step and everything it started (the game
        # launcher, the game) are ended and opfor.ini restored in a worker thread; then (if given) runs after that
        if self.state is not RunState.RUNNING or self.proc is None:
            return
        self.state = transition(self.state, 'cancel')
        self.after_cleanup = then
        self.set_busy(True, 'Cancelling ...')
        self.cancel['state'] = 'disabled'
        self.write('\ncancelling ...\n')
        threading.Thread(target=self.cleanup, args=(self.proc,), daemon=True).start()

    def cleanup(self, proc: 'subprocess.Popen[str]') -> None:
        try:
            kill_tree(proc)
            # a sampling run killed mid-way leaves the game open and opfor.ini changed
            navkit.close_game()
            navkit.recover_ini(self.project, lambda message: self.lines.put(message + '\n'))
            self.lines.put('cancelled\n')
        except Exception as e:                   # noqa: BLE001 - said in the log; the window stays usable
            self.lines.put(f'cancelling failed: {e}\n')
        finally:
            self.lines.put(('cleaned',))

    def cleaned(self) -> bool:
        """A cancelled step's clean-up finished (on the window's thread): the window is free again once the step's end
        has been handled too; a close waiting for the clean-up now happens. True when the window is gone."""
        self.state = transition(self.state, 'cleaned')
        if self.state is RunState.IDLE:
            self.set_busy(False, 'cancelled')
            self.refresh()
        if self.after_cleanup:
            self.after_cleanup()
            return True
        return False

    def set_busy(self, busy: bool, text: str) -> None:
        for button in self.buttons:
            button['state'] = 'disabled' if busy else 'normal'
        self.combo['state'] = 'disabled' if busy else 'readonly'
        self.cancel['state'] = 'normal' if busy else 'disabled'
        self.status['text'] = text

    def write(self, text: str) -> None:
        self.log['state'] = 'normal'
        self.log.insert('end', text)
        self.log.see('end')
        self.log['state'] = 'disabled'

    def open_folder(self) -> None:
        lv = self.level.get()
        folder = os.path.join(self.project.work, 'navkit', lv) if lv else os.path.join(self.project.work, 'navkit')
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def close(self) -> None:
        if self.state in (RunState.CANCELLING, RunState.CLEANING):
            self.after_cleanup = self.root.destroy   # the window closes once the clean-up has finished
            return
        if self.state is RunState.RUNNING:
            if not messagebox.askyesno('NavKit', 'A step is still running. Cancel it and quit?'):
                return
            self.stop(then=self.root.destroy)    # the window closes once the clean-up has finished
            return
        self.root.destroy()


def kill_tree(proc: 'subprocess.Popen[str]') -> None:
    # ends a step and every process it started (taskkill /T), then the step itself if it is still there
    if proc.poll() is None:
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if proc.poll() is None:
        proc.kill()


def main() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # sharp text on high-DPI screens
    except Exception:
        pass
    errors.setup('navkit_le-gui')
    with errors.guard():
        root = tk.Tk()
        root.report_callback_exception = lambda exc_type, value, tb: errors.report(value, tb=tb, where='a button')
        App(root)
        root.mainloop()


if __name__ == '__main__':
    main()
