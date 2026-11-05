r"""Error handling (errors.py): windows, pruning and guards, with the process's hooks restored afterwards

errors.py's paths the script test (test_errors.py) leaves out, in this process with every global it changes put
back (no game data; the logs go to a temporary folder): a cause whose test itself fails is passed over; a GUI tool's
error and a shown warning open a window (here a stand-in), a console tool's do not; the window is skipped quietly
without tkinter; old logs past the kept number are removed, one that cannot be removed is left; guard(tool) sets the
log up for that tool, re-raises SystemExit, and turns Ctrl+C into exit code 130 and any other error into 1.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import logging
import os
import sys
import threading
import warnings

from bfbc2core import errors


@contextlib.contextmanager
def isolated(tmp_path):
    """errors' globals (its state, the excepthooks, the loggers' handlers, warnings) as they were afterwards; logs in
    tmp_path."""
    saved_env = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = str(tmp_path)
    state = dict(errors._state)
    hooks = (sys.excepthook, threading.excepthook)
    loggers = [logging.getLogger('bfbc2'), logging.getLogger('py.warnings')]
    handlers = [list(lg.handlers) for lg in loggers]
    levels = [lg.level for lg in loggers]
    window = errors._window
    shown = []
    errors._window = lambda title, text, kind='error': shown.append((title, kind, text))
    try:
        with warnings.catch_warnings():
            yield shown
    finally:
        errors._window = window
        logging.captureWarnings(False)
        for lg, before, level in zip(loggers, handlers, levels):
            for h in lg.handlers:
                if h not in before:
                    h.close()
            lg.handlers[:] = before
            lg.setLevel(level)
        sys.excepthook, threading.excepthook = hooks
        errors._state.clear()
        errors._state.update(state)
        if saved_env is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = saved_env


def test_a_failing_cause_test_is_passed_over():
    real = errors.CAUSES
    calls = []

    def broken(e):
        calls.append(e)
        raise AttributeError('a test that does not fit this exception')
    errors.CAUSES = [(broken, lambda e: 'never', lambda e: 'never')] + list(real)
    try:
        assert errors.explain(ValueError('plain')) == ('ValueError: plain', 'If it happens again, send the log file '
                                                       'to the tool' + "'" + 's author.')
        assert errors.explain(MemoryError())[0] != 'never' and len(calls) == 2, 'the next causes are still tried'
    finally:
        errors.CAUSES = real


def test_windows_for_gui_tools_only(tmp_path):
    with isolated(tmp_path) as shown:
        errors.setup('gui-tool', gui=True)
        path = errors.report(ValueError('bad input'), where='a test')
        assert path == errors._state['path'] and os.path.isfile(path)
        assert shown and shown[-1][:2] == ('gui-tool - error', 'error') and 'bad input' in shown[-1][2], shown
        assert 'What to do:' in shown[-1][2] and path in shown[-1][2]
        errors.report(ValueError('quiet'), show=False)
        errors.warn('look at this', show=True)
        assert shown[-1] == ('gui-tool - warning', 'warning', 'look at this'), shown
        errors.warn('not shown')
        assert len(shown) == 2, shown
        errors.setup('console-tool', gui=False)
        errors.report(ValueError('printed only'))
        errors.warn('printed only', show=True)
        assert len(shown) == 2, 'a console tool opens no window'
        text = open(path, encoding='utf-8').read()
        assert 'gui-tool error in a test: ValueError: bad input' in text and 'look at this' in text


def test_window_without_tkinter():
    saved = sys.modules.get('tkinter', ...)
    sys.modules['tkinter'] = None                       # import tkinter raises ImportError
    try:
        errors._window('t', 'text')                     # nothing raised, nothing shown
    finally:
        if saved is ...:
            del sys.modules['tkinter']
        else:
            sys.modules['tkinter'] = saved


def test_prune(tmp_path):
    with isolated(tmp_path):
        logs = errors.log_dir()
        assert logs == os.path.join(str(tmp_path), 'bf1943-tool-suite', 'logs')
        names = [f'old-tool-2026-01-{d:02d}.log' for d in range(1, errors.KEEP + 4)]
        for n in names:
            open(os.path.join(logs, n), 'w').close()
        os.makedirs(os.path.join(logs, names[1] + '.dir'))
        open(os.path.join(logs, 'other-tool-2020-01-01.log'), 'w').close()
        held = open(os.path.join(logs, names[0]), 'a')  # noqa: SIM115 - held open: Windows cannot remove it
        try:
            errors._prune('old-tool')
        finally:
            held.close()
        left = sorted(n for n in os.listdir(logs) if n.startswith('old-tool') and n.endswith('.log'))
        kept = names[-errors.KEEP:]
        if os.name == 'nt':
            assert left == [names[0]] + kept, left
        else:
            assert left == kept, left
        assert os.path.isfile(os.path.join(logs, 'other-tool-2020-01-01.log')), 'another tool\'s log is kept'


def test_guard(tmp_path):
    with isolated(tmp_path) as shown:
        with errors.guard('guarded-tool', gui=False):
            pass
        assert errors._state['tool'] == 'guarded-tool' and errors._state['gui'] is False
        for raised, code in ((KeyboardInterrupt(), 130), (ValueError('broken'), 1), (SystemExit(7), 7)):
            try:
                with errors.guard():
                    raise raised
            except SystemExit as e:
                assert e.code == code, (raised, e.code)
            else:
                raise AssertionError(f'{raised!r} did not exit')
        assert not shown, 'a console tool'
        with errors.guard('guarded-tool', gui=True):
            pass
        assert errors._state['gui'] is False, 'the same tool: set up once'
        with errors.guard('windowed-tool'):
            pass
        assert errors._state['gui'] is True, 'a tool is a GUI tool unless it says otherwise'
        text = open(errors._state['path'], encoding='utf-8').read()
        assert '--- windowed-tool started' in text
