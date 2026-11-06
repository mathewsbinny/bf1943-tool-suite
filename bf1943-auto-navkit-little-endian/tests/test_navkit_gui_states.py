r"""navkitGUI_le.pyw's run states (RunState, transition) and a cancelled step's two ends in either order (no window: a
stand-in App whose widgets record what the window would show)

The transition table is the whole of what may happen: a step starts only when idle, Cancel only while it runs, and a
cancelled step is over once both its process's end ('done') and its clean-up's end ('cleaned') are handled, in either
order. 2026-11-05: 'cleaned' handled before 'done' freed the window while the step's process was still recorded (the
buttons did nothing), and the late 'done' then said the step had failed. Closing the window waits for a clean-up that
still runs and no longer than that.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import importlib.machinery
import importlib.util
import os
import queue
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)


def gui():
    """navkitGUI_le.pyw as a module (a .pyw: loaded by its path)."""
    name = 'test_navkit_gui_states_gui'
    if name not in sys.modules:
        loader = importlib.machinery.SourceFileLoader(name, os.path.join(KIT, 'navkitGUI_le.pyw'))
        spec = importlib.util.spec_from_loader(name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loader.exec_module(module)
    return sys.modules[name]


def app(state):
    """An App without a window in state, its step's process a stand-in; .shown records the status line's texts and
    whether the window was busy, .destroyed whether it closed, .warnings the failure messages."""
    g = gui()
    a = g.App.__new__(g.App)
    a.state = state
    a.proc = SimpleNamespace(pid=1, poll=lambda: None)
    a.after_cleanup = None
    a.lines = queue.Queue()
    a.tail = []
    a.shown, a.destroyed, a.warnings, a.written = [], [], [], []
    a.root = SimpleNamespace(after=lambda ms, fn: None, destroy=lambda: a.destroyed.append(True))
    a.level = SimpleNamespace(get=lambda: 'sp_y')
    a.set_busy = lambda busy, text: a.shown.append((busy, text))
    a.refresh = lambda: None
    a.write = a.written.append
    return a


def pumped(a, *items):
    """Each item through the window's queue, as the reader thread and the clean-up put them."""
    g = gui()
    saved = g.messagebox.showwarning
    g.messagebox.showwarning = lambda *args, **kw: a.warnings.append(args)
    try:
        for item in items:
            a.lines.put(item)
            a.pump()
    finally:
        g.messagebox.showwarning = saved


def test_transitions():
    g = gui()
    S = g.RunState
    assert g.transition(S.IDLE, 'start') is S.RUNNING
    assert g.transition(S.RUNNING, 'done') is S.IDLE
    assert g.transition(S.RUNNING, 'cancel') is S.CANCELLING
    assert g.transition(g.transition(S.CANCELLING, 'done'), 'cleaned') is S.IDLE
    assert g.transition(g.transition(S.CANCELLING, 'cleaned'), 'done') is S.IDLE
    for state, event in ((S.IDLE, 'done'), (S.IDLE, 'cancel'), (S.RUNNING, 'start'), (S.RUNNING, 'cleaned'),
                         (S.CANCELLING, 'cancel'), (S.CANCELLING, 'start'), (S.CLEANING, 'done'), (S.ENDING, 'cleaned'),
                         (S.ENDING, 'start')):
        try:
            g.transition(state, event)
        except ValueError:
            continue
        raise AssertionError(f'{event} accepted while {state.value}')


def test_cleaned_before_done():
    """The clean-up's end first: the window stays busy, then the step's end frees it as cancelled, no failure."""
    S = gui().RunState
    a = app(S.CANCELLING)
    pumped(a, ('cleaned',))
    assert a.state is S.ENDING and a.proc is not None and a.shown == [], 'still busy, the step not ended yet'
    pumped(a, ('done', 'sample', 1))
    assert a.state is S.IDLE and a.proc is None
    assert a.shown == [(False, 'cancelled')] and a.warnings == [], f'cancelled, not failed ({a.shown}, {a.warnings})'


def test_done_before_cleaned():
    S = gui().RunState
    a = app(S.CANCELLING)
    pumped(a, ('done', 'sample', 1))
    assert a.state is S.CLEANING and a.proc is None and a.shown == [], 'busy until the clean-up has finished'
    pumped(a, ('cleaned',))
    assert a.state is S.IDLE and a.shown == [(False, 'cancelled')] and a.warnings == []


def test_failure_told_when_not_cancelled():
    S = gui().RunState
    a = app(S.RUNNING)
    pumped(a, ('done', 'build', 2))
    assert a.state is S.IDLE and a.shown == [(False, 'build: failed - see the log above')] and len(a.warnings) == 1


def test_no_step_while_busy():
    S = gui().RunState
    for state in (S.RUNNING, S.CANCELLING, S.CLEANING, S.ENDING):
        a = app(state)
        a.run_step('build')                         # returns before anything (no window, no process started)
        assert a.state is state and a.shown == []


def test_cancel_only_while_running():
    S = gui().RunState
    for state in (S.IDLE, S.CANCELLING, S.CLEANING, S.ENDING):
        a = app(state)
        a.stop()
        assert a.state is state and a.shown == []


def test_close_waits_only_for_a_running_clean_up():
    S = gui().RunState
    for state in (S.CANCELLING, S.CLEANING):
        a = app(state)
        a.close()
        assert a.destroyed == [] and a.after_cleanup is not None, f'{state.value}: closes after the clean-up'
        pumped(a, ('cleaned',))
        assert a.destroyed == [True], f'{state.value}: closed once the clean-up finished'
    a = app(S.ENDING)                               # the clean-up has finished: nothing left to wait for
    a.close()
    assert a.destroyed == [True]
