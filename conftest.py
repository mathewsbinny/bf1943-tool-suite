r"""pytest's view of the suite (README "Tests"): `python -B -m pytest` from the suite folder runs

- the pytest-style test files (test_*.py with plain test functions, no `__main__` block), collected as pytest
  collects any test file, in the chosen tier (a file's TIER, default quick);
- the script tests (run_tests.py scripts()), through tests/test_suite_tiers.py: one pytest test per script in the
  chosen tier, each run as run_tests.py runs it. The scripts themselves are not collected (they are not pytest
  modules: their imports work only behind their bootstrap).

  --tier quick | bf1943   the tier, as run_tests.py takes it (default quick)
  --strict-skips          a script or file that skipped fails, as run_tests.py --strict

The suite's folders go on sys.path here as in tests/functions.py: the shared ones (pyproject.toml puts core there
first, pytest's `pythonpath`) and both NavKit variants, so a test imports any suite module plainly.
"""
import os
import sys

sys.dont_write_bytecode = True
import suitepath  # noqa: E402  (core: pyproject.toml [tool.pytest.ini_options] pythonpath)

suitepath.use(suitepath.NAVKIT_BE, suitepath.NAVKIT_LE, '.')
import run_tests  # noqa: E402


def pytest_addoption(parser):
    group = parser.getgroup('bf1943-tool-suite')
    group.addoption('--tier', choices=run_tests.TIERS, default='quick',
                    help='the tests of this tier, as run_tests.py takes it (default quick)')
    group.addoption('--strict-skips', action='store_true',
                    help='a test that skipped, all of it or some checks, fails (run_tests.py --strict)')


def pytest_ignore_collect(collection_path, config):
    """Scripts are not collected (test_suite_tiers.py runs them); nor pytest-style files of another tier."""
    path = str(collection_path)
    name = os.path.basename(path)
    if not (name.startswith('test_') and name.endswith('.py')) or os.path.normcase(path) in run_tests.PYTEST_ONLY:
        return None
    test = run_tests.function_test(path)
    if test is None:
        return True                                     # a script (or no test functions)
    return True if test[1] not in run_tests.SELECT[config.getoption('--tier')] else None
