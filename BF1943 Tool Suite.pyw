"""BF1943 Tool Suite - one window for every tool (double-click to start; needs Python 3 with numpy, and for the 3D
tools moderngl, glfw and imgui-bundle).

A tkinter window: each tab gathers a tool's inputs and runs the tool as a process of its own, its output streamed to
the pane at the bottom; the suite's log records each run.

Tabs: Levels (your BF1943 copy's levels: open one in the Map Inspector or the Navigation Studio, build its
navigation), Bundles (fbrb1943.py), Level diff (LevelDiff), Xenia run (what happened in the last session), Xbox
files (STFS content packages), Tests (run_tests.py), More tools (DbxEditGUI, NavKit's window). Every tool runs as
its own process; its output appears in the pane at the bottom. The tools read your own game copy (the folder
bf1943-xenia-kit extracts, or BF1943_GAME) - nothing is sent anywhere, and the copy is never changed.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Sequence
from tkinter import filedialog, messagebox, ttk
from typing import Any

SUITE = os.path.dirname(os.path.abspath(__file__))
NAVKIT = os.path.join(SUITE, 'bf1943-auto-navkit-big-endian')
if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
from bfbc2core import csbuild, errors, game1943  # noqa: E402

# library -> what needs it (checked when the window opens)
LIBRARIES = [('numpy', 'every tool'), ('matplotlib', 'screenshots'), ('moderngl', 'the 3D tools'),
             ('glfw', 'the 3D tools'), ('imgui_bundle', 'the 3D tools'),
             ('capstone', 'the Xenia kit\'s assembler test')]
PYTHON = os.path.join(os.path.dirname(sys.executable), 'python.exe')        # console python for the tools
if not os.path.isfile(PYTHON):
    PYTHON = sys.executable
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# a file dialog's file types: (description, pattern)
type FileTypes = tuple[tuple[str, str], ...]

SCRIPTS = {
    'inspector': os.path.join(SUITE, 'bf1943-map-inspector', 'inspector.py'),
    'studio': os.path.join(SUITE, 'bf1943-navigation-studio', 'studio.py'),
    'navkit': os.path.join(NAVKIT, 'navkit.py'),
    'navkitgui': os.path.join(NAVKIT, 'navkitGUI.pyw'),
    'xeniarun': os.path.join(SUITE, 'bf1943-automated-map-and-bot-test-harness', 'xenia-run', 'xeniarun.py'),
    'fbrb': os.path.join(SUITE, 'bf1943-fbrb-tool', 'fbrb1943.py'),
    'stfs': os.path.join(SUITE, 'bf1943-console-readers', 'stfs', 'stfs.py'),
    'tests': os.path.join(SUITE, 'run_tests.py'),
}
NAV_STEPS = ('prepared', 'sampled', 'built', 'checked')          # navkit.json keys, in order
LEVELDIFF_OK = (0, 1)               # LevelDiff: 0 no differences, 1 differences found (2 = bad input or a crash)


def tool(name: str) -> str:
    """The tool's exe ('leveldiff', 'dbxgui'), built first (with the C# compiler Windows ships) if it is missing or
    older than its sources (core csbuild.py)."""
    exe, bat, _ = csbuild.tools()[name]
    try:
        return csbuild.ensure(name, log=errors.logger().info)
    except RuntimeError as e:
        errors.logger().error(f'building {os.path.basename(exe)} failed:\n{e}')
        raise RuntimeError(f'{os.path.basename(exe)} could not be built from its current sources (the C# compiler that '
                           f'comes with Windows, .NET Framework 4, is needed). Run {bat} to see why.') from e


class Hub:
    """The suite's window: the tabs, the output pane and the tools it runs (one at a time per tab's Run, any number of
    windows launched alongside)."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        # output lines, and ('done', label, code, main, ok, pid) when a tool ends; read by pump()
        self.lines: queue.Queue[str | tuple[Any, ...]] = queue.Queue()
        self.proc: subprocess.Popen[str] | None = None     # the tab's running tool (one at a time)
        self.stopped_pids: set[int] = set()    # processes the user stopped: their end is not a failure
        root.title('BF1943 Tool Suite')
        root.minsize(900, 640)
        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        self.game: str | None = None
        try:
            self.game = game1943.find_game()
        except FileNotFoundError as e:         # the suite still opens; the Levels tab explains
            self.game_error = str(e)

        pane = ttk.PanedWindow(root, orient='vertical')
        pane.pack(fill='both', expand=True, padx=8, pady=8)
        self.tabs = ttk.Notebook(pane)
        pane.add(self.tabs, weight=3)
        out = ttk.Frame(pane)
        pane.add(out, weight=2)
        bar = ttk.Frame(out)
        bar.pack(fill='x')
        self.status = tk.StringVar(value='Ready.')
        ttk.Label(bar, textvariable=self.status).pack(side='left')
        ttk.Button(bar, text='Clear', command=lambda: self.text.delete('1.0', 'end')).pack(side='right')
        self.stop_btn = ttk.Button(bar, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.pack(side='right', padx=4)
        self.text = tk.Text(out, height=12, wrap='none', font=('Consolas', 9))
        scrollbar = ttk.Scrollbar(out, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)

        self.tab_levels()
        self.tab_bundles()
        self.tab_diff()
        self.tab_xenia()
        self.tab_xbox()
        self.tab_tests()
        self.tab_more()
        self.tails: dict[str, list[str]] = {}  # label -> the last lines of a tool's output (for error reports)
        root.report_callback_exception = lambda kind, value, tb: errors.report(value, tb=tb, where='a button')
        self.write(f'Log: {errors._state.get("path")}\n')
        self.check_libraries()
        root.after(100, self.pump)

    def notify(self, kind: str, title: str, text: str) -> None:
        """A window for the user (tests replace it)."""
        (messagebox.showerror if kind == 'error' else messagebox.showwarning)(title, text, parent=self.root)

    def check_libraries(self) -> None:
        import importlib.util
        missing = [(name, used) for name, used in LIBRARIES if importlib.util.find_spec(name) is None]
        if not missing:
            return
        pip = ' '.join(errors.PIP_NAMES.get(name, name) for name, _ in missing)
        lines = '\n'.join(f'  {name} - for {used}' for name, used in missing)
        errors.warn(f'libraries not installed: {", ".join(name for name, _ in missing)}')
        self.write(f'Missing libraries:\n{lines}\nInstall them with: pip install {pip}\n')
        self.notify('warning', 'BF1943 Tool Suite', f'Some libraries the tools use are not installed:\n\n{lines}\n\n'
                                                   f'Install them with:\npip install {pip}')

    # --- running tools ------------------------------------------------------------------------------
    @staticmethod
    def child_env(args: Sequence[object]) -> tuple[dict[str, str], str | None]:
        """(environment, encoding to read its output with): a Python tool writes UTF-8 (2026-10-30: through a pipe
        it wrote the ANSI code page and died on a path outside it, a Cyrillic folder name); others (LevelDiff) keep
        the system's. No Python it starts writes __pycache__ folders into the suite (as `python -B`)."""
        env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        if args and os.path.normcase(str(args[0])) in (os.path.normcase(PYTHON), os.path.normcase(sys.executable)):
            env['PYTHONIOENCODING'] = 'utf-8'
            return env, 'utf-8'
        return env, None

    def run(self, args: Sequence[str], label: str, cwd: str | None = None, ok: Sequence[int] = (0,)) -> None:
        """One tool in the background (the tab's), its output streamed to the pane. ok: the exit codes that are not a
        failure (LevelDiff: 1 = the files differ)."""
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showinfo('BF1943 Tool Suite', 'A tool is still running - wait for it or press Stop.')
            return
        self.write(f'\n> {label}\n')
        errors.logger().info(f'run: {label}: {subprocess.list2cmdline([str(arg) for arg in args])}')
        self.tails[label] = []
        env, encoding = self.child_env(args)
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding=encoding, errors='replace', cwd=cwd or SUITE, creationflags=NO_WINDOW,
                                         env=env)
        except OSError as e:
            errors.report(e, where=f'starting {label}')
            return
        self.status.set(f'Running: {label}')
        self.stop_btn.configure(state='normal')
        threading.Thread(target=self.reader, args=(self.proc, label, True, tuple(ok)), daemon=True).start()

    def launch(self, args: Sequence[str], label: str) -> None:
        """A window of its own (3D tools, other GUIs): runs alongside, and on after this window closes. Its log goes
        to a file this window follows, so closing the suite never cuts the tool's output off."""
        import tempfile
        self.write(f'\n> {label}\n')
        errors.logger().info(f'launch: {label}: {subprocess.list2cmdline([str(arg) for arg in args])}')
        self.tails[label] = []
        log = tempfile.NamedTemporaryFile(prefix='bf1943suite_', suffix='.log', delete=False)
        try:
            proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=SUITE, creationflags=NO_WINDOW,
                                    env=self.child_env(args)[0])
        except OSError as e:
            log.close()
            errors.report(e, where=f'starting {label}')
            return
        log.close()
        threading.Thread(target=self.follow, args=(proc, log.name, label), daemon=True).start()

    def follow(self, proc: 'subprocess.Popen[bytes]', path: str, label: str) -> None:
        """A launched tool's log file into the pane as it grows, until the tool ends (then the file is removed)."""
        import time
        with open(path, encoding='utf-8', errors='replace') as log:
            while True:
                line = log.readline()
                if line:
                    self.lines.put(line)
                    tail = self.tails.setdefault(label, [])
                    tail.append(line.rstrip())
                    del tail[:-12]
                    continue
                if proc.poll() is not None:
                    rest = log.read()
                    if rest:
                        self.lines.put(rest)
                    break
                time.sleep(0.2)
        self.lines.put(('done', label, proc.returncode, False, (0,), proc.pid))
        try:
            os.remove(path)
        except OSError:
            pass

    def reader(self, proc: 'subprocess.Popen[str]', label: str, main: bool, ok: Sequence[int] = (0,)) -> None:
        """A run tool's output into the pane, line by line, then its end."""
        for line in proc.stdout or ():
            self.lines.put(line)
            tail = self.tails.setdefault(label, [])
            tail.append(line.rstrip())
            del tail[:-12]
        code = proc.wait()
        self.lines.put(('done', label, code, main, ok, proc.pid))

    def pump(self) -> None:
        """The tools' output into the pane, every 100 ms. One item's error is reported and the rest go on, and the
        next round is always scheduled (2026-10-30: an error here - the game folder gone when a Levels run ended -
        stopped the pump for good, and no output reached the pane again)."""
        try:
            while True:
                try:
                    item = self.lines.get_nowait()
                except queue.Empty:
                    break
                try:
                    if isinstance(item, tuple):
                        self.ended(*item[1:])
                    else:
                        self.write(item)
                except Exception as e:
                    errors.report(e, where='the output pane')
        finally:
            self.root.after(100, self.pump)

    def ended(self, label: str, code: int | None, main: bool, ok: Sequence[int], pid: int) -> None:
        """A tool's end: a line in the pane, and a failure told unless it was an accepted code or the user stopped
        that very process (2026-10-30: one flag for all, so another tool's end could take it)."""
        stopped = pid in self.stopped_pids
        self.stopped_pids.discard(pid)
        self.write(f'{"done" if code in ok else f"finished with exit code {code}"}: {label}\n')
        errors.logger().info(f'exit {code}: {label}')
        if main:
            self.status.set('Ready.')
            self.stop_btn.configure(state='disabled')
        if code not in ok and code is not None and not stopped:
            self.failed(label, code, main)
        if main and label.startswith('Levels'):
            self.fill_levels()

    def failed(self, label: str, code: int, main: bool) -> None:
        """A tool ended with an error: logged, and the user told what it said last. The 3D tools and other
        windows report their own errors in a window of their own, so for them the pane is enough."""
        tail = [line for line in self.tails.get(label, []) if line.strip()]
        errors.logger().warning(f'{label} failed (exit code {code}); last lines:\n  ' + '\n  '.join(tail))
        if not main:
            self.write(f'{label} closed with an error - see its message, and the log for details.\n')
            return
        said = '\n'.join(tail[-8:]) or '(no output)'
        self.notify('warning', 'BF1943 Tool Suite',
                    f'{label} did not finish (exit code {code}).\n\nIt said last:\n{said}\n\n'
                    f'The whole output is in the pane below; the suite\'s log:\n{errors._state.get("path")}')

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.stopped_pids.add(self.proc.pid)
            # the tool and what it started (NavKit's samplers): ending the tool alone leaves those running
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.proc.pid)], capture_output=True,
                           creationflags=NO_WINDOW)
            self.write('stopped.\n')
            errors.logger().info('stopped by the user')

    def write(self, text: str) -> None:
        self.text.insert('end', text)
        self.text.see('end')

    def form(self, title: str, help_text: str) -> ttk.Frame:
        """A new tab: its help text on top, column 1 stretching."""
        frame = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(frame, text=title)
        help_label = ttk.Label(frame, text=help_text, wraplength=820, foreground='#444')
        help_label.grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        return frame

    def file_row(self, frame: ttk.Frame, row: int, label: str, var: tk.StringVar, kind: str = 'open',
                 types: FileTypes = (('All files', '*.*'),)) -> None:
        """A labelled path entry with a Browse button (kind: 'open', 'save' or 'dir')."""
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=2)
        ttk.Entry(frame, textvariable=var).grid(row=row, column=1, sticky='we', padx=6)

        def browse() -> None:
            if kind == 'dir':
                path = filedialog.askdirectory(title=label)
            elif kind == 'save':
                path = filedialog.asksaveasfilename(title=label, filetypes=types)
            else:
                path = filedialog.askopenfilename(title=label, filetypes=types)
            if path:
                var.set(os.path.normpath(path))
        ttk.Button(frame, text='Browse...', command=browse).grid(row=row, column=2, sticky='w')

    @staticmethod
    def need(*values: tuple[str, tk.Variable]) -> bool:
        """True when every (name, variable) holds text; otherwise the user is asked to choose the missing ones."""
        missing = [name for name, var in values if not var.get().strip()]
        if missing:
            messagebox.showwarning('BF1943 Tool Suite', 'Choose ' + ' and '.join(missing) + ' first.')
            return False
        return True

    # --- Levels: the 3D tools -----------------------------------------------------------------------
    def tab_levels(self) -> None:
        frame = self.form(
            'Levels',
            'Your BF1943 copy\'s levels. Open one in the Map Inspector (the level in 3D: every object, its properties '
            'and links; bases, flags and spawns; measuring) or in the Navigation Studio (its navigation build: '
            'islands, spawns and flags on the network, the path test). The Studio needs a build: "Build navigation" '
            'runs NavKit\'s steps (prepare, sample, build, check; about 45 minutes, computed from the level\'s data). '
            'Progress shows below.')
        columns = ('kind',) + NAV_STEPS
        self.tree = ttk.Treeview(frame, columns=columns, height=10, selectmode='browse')
        self.tree.heading('#0', text='Level')
        for column, title in zip(columns, ('', 'Prepared', 'Sampled', 'Built', 'Checked')):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=130, anchor='center')
        self.tree.column('#0', width=170)
        self.tree.grid(row=1, column=0, columnspan=3, sticky='nsew')
        self.tree.bind('<Double-1>', lambda event: self.open_3d('inspector'))
        frame.rowconfigure(1, weight=1)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=3, sticky='w', pady=8)
        ttk.Button(buttons, text='Open in Map Inspector', command=lambda: self.open_3d('inspector')).pack(side='left')
        ttk.Button(buttons, text='Open in Navigation Studio',
                   command=lambda: self.open_3d('studio')).pack(side='left', padx=6)
        ttk.Button(buttons, text='Build navigation', command=self.build_navigation).pack(side='left', padx=6)
        ttk.Button(buttons, text='Open level folder', command=self.open_level_folder).pack(side='left', padx=6)
        ttk.Button(buttons, text='Refresh', command=self.fill_levels).pack(side='left', padx=6)
        self.nav_file = tk.StringVar()
        self.file_row(frame, 3, 'Navigation build for the Studio (optional; default: NavKit\'s):', self.nav_file,
                      types=(('Navigation builds', '*.hpd'), ('All files', '*.*')))
        self.fill_levels()

    def fill_levels(self) -> None:
        """The Levels list: each level of the game copy, with when NavKit's steps last ran on it (navkit.json)."""
        self.tree.delete(*self.tree.get_children())
        if self.game is None:
            self.tree.insert('', 'end', text='(no game copy found)', values=('',) * 5)
            self.write(f'Levels: {getattr(self, "game_error", "no BF1943 game folder")}\n')
            return
        try:
            found = game1943.levels(self.game)
        except OSError as e:                    # the copy moved or rebuilt since the window opened: said, not raised
            self.tree.insert('', 'end', text='(game copy not readable)', values=('',) * 5, tags=('none',))
            self.write(f'Levels: the game copy could not be read: {e}\n')
            return
        for level in found:
            path = os.path.join(game1943.level_work(level), 'navkit', 'navkit.json')
            steps = {}
            if os.path.isfile(path):
                try:
                    with open(path, encoding='utf-8') as record:
                        steps = json.load(record)
                except (OSError, ValueError):
                    steps = {}
            if not isinstance(steps, dict):     # valid JSON but not NavKit's record: shown as not run
                steps = {}
            kind = 'multiplayer map' if level in game1943.MP_LEVELS else 'other level'
            done = tuple(steps.get(step, '-') for step in NAV_STEPS)
            self.tree.insert('', 'end', iid=level, text=level, values=(kind,) + done)

    def chosen_level(self) -> str | None:
        """The level chosen in the list, or None (the user is told to choose one)."""
        selection = self.tree.selection()
        if not selection or self.game is None or 'none' in self.tree.item(selection[0], 'tags'):
            messagebox.showinfo('BF1943 Tool Suite', 'Choose a level in the list first.')
            return None
        return selection[0]

    def open_3d(self, which: str) -> None:
        level = self.chosen_level()
        if not level:
            return
        args = [PYTHON, SCRIPTS[which], level]
        if which == 'studio' and self.nav_file.get().strip():
            args += ['--hpd', self.nav_file.get().strip()]
        name = 'Map Inspector' if which == 'inspector' else 'Navigation Studio'
        self.launch(args, f'{name}: {level} (the window opens when the level is loaded)')

    def build_navigation(self) -> None:
        level = self.chosen_level()
        question = (f'Build the navigation of {level}?\n\nNavKit prepares, samples, builds and checks it from the '
                    'level\'s data: about 45 minutes, using every core. The game is not started.')
        if level and messagebox.askokcancel('BF1943 Tool Suite', question):
            self.run([PYTHON, '-u', SCRIPTS['navkit'], 'all', level], f'Levels: navigation of {level}')

    def open_level_folder(self) -> None:
        level = self.chosen_level()
        if level:
            folder = game1943.level_work(level)
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)

    # --- Bundles: fbrb1943 --------------------------------------------------------------------------
    def tab_bundles(self) -> None:
        frame = self.form(
            'Bundles',
            'Look inside your copy\'s .fbrb bundles (BF1943\'s FbRB v2), or extract their entries to a folder - all, '
            'or only names containing the filter text - with a manifest of their sizes and hashes (fbrb1943.py). The '
            'bundles are only read.')
        self.fb_file, self.fb_dir, self.fb_filter = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.file_row(frame, 1, 'Bundle (.fbrb):', self.fb_file, types=(('Bundles', '*.fbrb'), ('All files', '*.*')))
        self.file_row(frame, 2, 'Folder to extract into:', self.fb_dir, kind='dir')
        ttk.Label(frame, text='Filter (optional):').grid(row=3, column=0, sticky='w')
        ttk.Entry(frame, textvariable=self.fb_filter, width=30).grid(row=3, column=1, sticky='w', padx=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=1, sticky='w', pady=6)
        ttk.Button(buttons, text='Info', command=lambda: self.bundle('info')).pack(side='left')
        ttk.Button(buttons, text='List entries', command=lambda: self.bundle('list')).pack(side='left', padx=6)
        ttk.Button(buttons, text='Extract', command=lambda: self.bundle('extract')).pack(side='left')

    def bundle(self, command: str) -> None:
        """fbrb1943.py info, list or extract on the chosen bundle (list and extract: with the filter, if any)."""
        if command == 'extract':
            if not self.need(('a bundle', self.fb_file), ('a folder', self.fb_dir)):
                return
        elif not self.need(('a bundle', self.fb_file)):
            return
        args = [PYTHON, SCRIPTS['fbrb'], command, self.fb_file.get()]
        if command == 'extract':
            args.append(self.fb_dir.get())
        flt = self.fb_filter.get().strip()
        if command != 'info' and flt:
            args.append(flt)
        label = {'info': 'Bundle info', 'list': 'Bundle list', 'extract': 'Bundle extract'}[command]
        self.run(args, label)

    # --- Level diff ---------------------------------------------------------------------------------
    def tab_diff(self) -> None:
        frame = self.form(
            'Level diff',
            'Compare two binary .dbx files (two builds of a level, or any partition) instance by instance: what was '
            'added, removed or changed, field by field (LevelDiff).')
        self.ld_a, self.ld_b, self.ld_type, self.ld_ignore, self.ld_json = (tk.StringVar() for _ in range(5))
        types = (('DBX', '*.dbx'), ('All files', '*.*'))
        self.file_row(frame, 1, 'File A:', self.ld_a, types=types)
        self.file_row(frame, 2, 'File B:', self.ld_b, types=types)
        ttk.Label(frame, text='Only types containing:').grid(row=3, column=0, sticky='w')
        ttk.Entry(frame, textvariable=self.ld_type, width=30).grid(row=3, column=1, sticky='w', padx=6)
        ttk.Label(frame, text='Leave out fields (comma-separated):').grid(row=4, column=0, sticky='w')
        ttk.Entry(frame, textvariable=self.ld_ignore, width=30).grid(row=4, column=1, sticky='w', padx=6)
        self.ld_match, self.ld_summary, self.ld_all = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
        options = ttk.Frame(frame)
        options.grid(row=5, column=1, sticky='w', pady=4)
        ttk.Checkbutton(options, text='Pair re-created objects by blueprint + position',
                        variable=self.ld_match).pack(side='left')
        ttk.Checkbutton(options, text='Counts only', variable=self.ld_summary).pack(side='left', padx=8)
        ttk.Checkbutton(options, text='List everything', variable=self.ld_all).pack(side='left')
        self.file_row(frame, 6, 'Also save as JSON (optional):', self.ld_json, kind='save', types=(('JSON', '*.json'),))
        ttk.Button(frame, text='Compare', command=self.diff).grid(row=7, column=1, sticky='w', pady=6)

    def diff(self) -> None:
        if not self.need(('file A', self.ld_a), ('file B', self.ld_b)):
            return
        args = [tool('leveldiff'), self.ld_a.get(), self.ld_b.get()]
        if self.ld_match.get():
            args.append('--match-objects')
        if self.ld_summary.get():
            args.append('--summary')
        if self.ld_all.get():
            args.append('--all')
        if self.ld_type.get().strip():
            args += ['--type', self.ld_type.get().strip()]
        for field in (name.strip() for name in self.ld_ignore.get().split(',')):
            if field:
                args += ['--ignore', field]
        if self.ld_json.get().strip():
            args += ['--json', self.ld_json.get().strip()]
        self.run(args, 'LevelDiff', ok=LEVELDIFF_OK)

    # --- Xenia run ----------------------------------------------------------------------------------
    def tab_xenia(self) -> None:
        frame = self.form(
            'Xenia run',
            'What happened in the last Xenia session of the Xenia kit\'s build: Xenia\'s version, the patches '
            'applied, warnings, the bots experiment\'s reports (teams, kits, spawns, the last positions of you and '
            'every bot), and whether Xenia crashed - from Windows\' record of it, since Xenia writes no crash report '
            '(xeniarun.py). Only reads.')
        self.xr_log = tk.StringVar()
        self.file_row(frame, 1, 'xenia.log (optional; default: the kit\'s Xenia folder):', self.xr_log,
                      types=(('Logs', '*.log'), ('All files', '*.*')))
        self.xr_pos = tk.BooleanVar()
        ttk.Checkbutton(frame, text='List every bot\'s last position',
                        variable=self.xr_pos).grid(row=2, column=1, sticky='w')
        ttk.Button(frame, text='Read the last session',
                   command=self.xenia_run).grid(row=3, column=1, sticky='w', pady=6)

    def xenia_run(self) -> None:
        args = [PYTHON, SCRIPTS['xeniarun']]
        if self.xr_log.get().strip():
            args += ['--log', self.xr_log.get().strip()]
        if self.xr_pos.get():
            args.append('--positions')
        self.run(args, 'Xenia run')

    # --- Xbox files ---------------------------------------------------------------------------------
    def tab_xbox(self) -> None:
        frame = self.form(
            'Xbox files',
            'Read your own Xbox 360 content packages (STFS, read-only) - BF1943\'s game package among them. List what '
            'is inside, or extract it - all, or only paths containing the filter text. (The Xenia kit extracts the '
            'game package for you.)')
        self.xb_file, self.xb_dir, self.xb_filter = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.file_row(frame, 2, 'Package:', self.xb_file)
        self.file_row(frame, 3, 'Extract into:', self.xb_dir, kind='dir')
        ttk.Label(frame, text='Filter (optional):').grid(row=4, column=0, sticky='w')
        ttk.Entry(frame, textvariable=self.xb_filter, width=30).grid(row=4, column=1, sticky='w', padx=6)
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=1, sticky='w', pady=6)
        ttk.Button(buttons, text='Info', command=lambda: self.xbox('info')).pack(side='left')
        ttk.Button(buttons, text='List', command=lambda: self.xbox('list')).pack(side='left', padx=6)
        ttk.Button(buttons, text='Extract', command=lambda: self.xbox('extract')).pack(side='left')

    def xbox(self, cmd: str) -> None:
        if not self.need(('a package', self.xb_file)):
            return
        args = [PYTHON, SCRIPTS['stfs'], cmd, self.xb_file.get()]
        if cmd == 'extract':
            if not self.need(('a folder to extract into', self.xb_dir)):
                return
            args.append(self.xb_dir.get())
        if cmd != 'info' and self.xb_filter.get().strip():
            args.append(self.xb_filter.get().strip())
        self.run(args, f'Package {cmd}')

    # --- Tests --------------------------------------------------------------------------------------
    def tab_tests(self) -> None:
        frame = self.form(
            'Tests',
            'Check that the tools work on this PC: quick (one to two minutes, no game data) or bf1943 (on your BF1943 '
            'copy as the Xenia kit extracts it, a few minutes). One line per test; a failure shows its output at the '
            'end.')
        self.t_tier = tk.StringVar(value='quick')
        tiers = ttk.Frame(frame)
        tiers.grid(row=1, column=1, sticky='w')
        for tier in ('quick', 'bf1943'):
            ttk.Radiobutton(tiers, text=tier, value=tier, variable=self.t_tier).pack(side='left', padx=(0, 10))
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=1, sticky='w', pady=6)
        ttk.Button(buttons, text='Run the tests', command=self.run_tests).pack(side='left')
        ttk.Button(buttons, text='What would run', command=self.list_tests).pack(side='left', padx=6)

    def run_tests(self) -> None:
        self.run([PYTHON, '-u', SCRIPTS['tests'], self.t_tier.get()], f'Tests ({self.t_tier.get()})')

    def list_tests(self) -> None:
        self.run([PYTHON, SCRIPTS['tests'], self.t_tier.get(), '--list'], 'Tests: list')

    # --- More tools ---------------------------------------------------------------------------------
    def tab_more(self) -> None:
        frame = self.form(
            'More tools',
            'Tools with windows of their own.')
        rows = [('DbxEditGUI', 'View, edit and check binary .dbx partitions (BF1943\'s too; extract them with the '
                               'Bundles tab first); XML export and import.',
                 lambda: self.launch([tool('dbxgui')], 'DbxEditGUI')),
                ('NavKit', 'Build a level\'s navigation step by step: prepare, sample, build, check; show it in 3D.',
                 lambda: self.launch([PYTHON, SCRIPTS['navkitgui']], 'NavKit')),
                ('Suite folder', 'The suite\'s files and READMEs.', lambda: os.startfile(SUITE))]
        for k, (name, text, cmd) in enumerate(rows, 1):
            ttk.Button(frame, text=f'Open {name}', command=cmd, width=22).grid(row=k, column=0, sticky='w', pady=4)
            ttk.Label(frame, text=text).grid(row=k, column=1, sticky='w', padx=8)


def dpi_aware() -> None:
    """Sharp text on scaled displays (125 %, 150 %): Windows otherwise draws a Tk window blurred and enlarged."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    errors.setup('suite')
    dpi_aware()
    with errors.guard():
        root = tk.Tk()
        Hub(root)
        root.mainloop()


if __name__ == '__main__':
    main()
