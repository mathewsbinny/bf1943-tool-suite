r"""The suite window's end of a tool (BF1943 Tool Suite.pyw Hub.ended, no window: a stand-in for the Hub's state)

Only the end of the tab's current tool (pid == self.proc.pid) frees the window (status Ready, Stop disabled): the end
of an earlier run, still queued when the next run started, leaves the running tool's status and Stop alone
(2026-11-05), while its line in the pane and its failure are still told; a launched window's end never frees it.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)


def hub():
    """BF1943 Tool Suite.pyw as a module (its name has spaces; it is not on sys.path)."""
    name = 'test_hub_ended_hub'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SUITE, 'BF1943 Tool Suite.pyw'))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class Var:
    def __init__(self, value=''):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class Button:
    def __init__(self):
        self.state = 'normal'

    def configure(self, state):
        self.state = state


def window(running_pid):
    """A stand-in for a Hub whose tab runs the process running_pid (status Running, Stop enabled)."""
    pane, failures, levels = [], [], []
    return SimpleNamespace(
        stopped_pids=set(), proc=SimpleNamespace(pid=running_pid), status=Var('Running: the new run'),
        stop_btn=Button(), pane=pane, failures=failures, levels=levels, write=pane.append,
        failed=lambda label, code, main: failures.append((label, code, main)), fill_levels=lambda: levels.append(1))


def test_stale_end_leaves_the_running_tool_alone():
    h = window(200)
    hub().Hub.ended(h, 'the earlier run', 2, True, (0,), 100)
    assert h.status.get() == 'Running: the new run' and h.stop_btn.state == 'normal', 'the running tool keeps its state'
    assert h.pane == ['finished with exit code 2: the earlier run\n'], 'its end is still in the pane'
    assert h.failures == [('the earlier run', 2, True)], 'and its failure is still told'


def test_current_end_frees_the_window():
    h = window(200)
    hub().Hub.ended(h, 'the run', 0, True, (0,), 200)
    assert h.status.get() == 'Ready.' and h.stop_btn.state == 'disabled' and h.failures == []


def test_no_tool_running_is_left_alone():
    h = window(200)
    h.proc = None
    hub().Hub.ended(h, 'a run', 0, True, (0,), 200)
    assert h.status.get() == 'Running: the new run' and h.stop_btn.state == 'normal'


def test_launched_window_end_never_frees_the_window():
    h = window(200)
    hub().Hub.ended(h, 'a window', 0, False, (0,), 200)
    assert h.status.get() == 'Running: the new run' and h.stop_btn.state == 'normal'


def test_stopped_stale_end_is_no_failure():
    h = window(200)
    h.stopped_pids.add(100)
    hub().Hub.ended(h, 'the stopped run', 1, True, (0,), 100)
    assert h.failures == [] and 100 not in h.stopped_pids and h.status.get() == 'Running: the new run'
