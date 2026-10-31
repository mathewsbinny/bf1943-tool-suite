r"""The script tests under pytest: one pytest test per script that run_tests.py lists (scripts()) in the chosen tier
(--tier, default quick), run as run_tests.py runs it (run_tests.run_one: the same command, folder and environment).
A script that fails fails here with its last 25 lines; one that skipped all its checks skips with its reason; one that
skipped some passes with a warning. --strict-skips (run_tests.py --strict) fails both instead.

  python -B -m pytest tests/test_suite_tiers.py [--tier bf1943] [--strict-skips] [-k <part of a name>]
"""
import warnings

import pytest

import run_tests


class PartlySkipped(UserWarning):
    """A script passed, but some of its checks were skipped (their data was missing)."""


def pytest_generate_tests(metafunc):
    if 'script' in metafunc.fixturenames:
        tier = metafunc.config.getoption('--tier')
        chosen = [t for t in run_tests.scripts() if t[1] in run_tests.SELECT[tier]]
        metafunc.parametrize('script', chosen, ids=[name for name, _, _ in chosen])


def test_script(script, request):
    name, _tier, argv = script
    status, last, out, skips = run_tests.run_one(argv)
    strict = request.config.getoption('--strict-skips')
    if status == 'FAIL':
        pytest.fail(f'{name} failed; its last 25 lines:\n' + '\n'.join(out.splitlines()[-25:]), pytrace=False)
    if status == 'SKIP':
        if strict:
            pytest.fail(f'{name} skipped (--strict-skips): {last}', pytrace=False)
        pytest.skip(last)
    if status == 'ok* ':
        if strict:
            pytest.fail(f'{name} skipped some checks (--strict-skips): ' + '; '.join(skips), pytrace=False)
        warnings.warn(PartlySkipped(f'{name}: ' + '; '.join(skips)), stacklevel=1)
