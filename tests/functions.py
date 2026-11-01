r"""Runs a pytest-style test file without pytest, as run_tests.py does (README "Tests"): each top-level function
whose name starts with "test", in the order the file defines them, one line each ("ok", "FAIL" with its traceback,
or "skip" with its reason), then a summary line ("all passed", "<n> failed", or "skip ..." when every test skipped:
run_tests.py shows that as SKIP). Exit code 1 if any failed.

What a test may use, so that it runs the same here and under pytest: plain `assert`; the `tmp_path` argument (a new
empty folder, removed afterwards); `raise unittest.SkipTest(reason)` to skip (its data is missing). Anything else
pytest offers (other fixtures, marks, pytest.raises) needs pytest, which run_tests.py does not require: write those
checks in plain Python.

The suite's folders are on sys.path as under pytest (conftest.py): the shared ones and both NavKit variants, so a
test imports any suite module plainly.

  python -B tests\functions.py <test file>
"""
import sys

sys.dont_write_bytecode = True
import importlib.util  # noqa: E402
import inspect  # noqa: E402
import pathlib  # noqa: E402
import tempfile  # noqa: E402
import traceback  # noqa: E402
import unittest  # noqa: E402
from types import ModuleType  # noqa: E402

FIXTURES = {'tmp_path'}


def load(path: str) -> ModuleType:
    """The test file as a module of its own name (its folder is not put on sys.path)."""
    name = 'functions_' + pathlib.Path(path).stem
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f'{path} cannot be loaded as a Python module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(module: ModuleType) -> tuple[int, int, list[str]]:
    """(passed, failed, skipped) after running and reporting each test function."""
    passed, failed = 0, 0
    skipped: list[str] = []
    for name, function in list(vars(module).items()):      # a list: a test may add a global (a warning registry)
        if not (name.startswith('test') and inspect.isfunction(function) and function.__module__ == module.__name__):
            continue
        params = list(inspect.signature(function).parameters)
        unknown = [param for param in params if param not in FIXTURES]
        if unknown:
            print(f'FAIL {name}: needs pytest (fixture {", ".join(unknown)}; here only {", ".join(sorted(FIXTURES))})')
            failed += 1
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='bf1943test_') as tmp:
                function(**({'tmp_path': pathlib.Path(tmp)} if 'tmp_path' in params else {}))
        except unittest.SkipTest as e:
            print(f'skip {name}: {e}')
            skipped.append(f'{name}: {e}')
        except Exception:
            print(f'FAIL {name}')
            print('       ' + traceback.format_exc().strip().replace('\n', '\n       '))
            failed += 1
        else:
            print(f'ok   {name}')
            passed += 1
    return passed, failed, skipped


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('usage: python -B tests\\functions.py <test file>')
        return 2
    passed, failed, skipped = run(load(argv[1]))
    if failed:
        print(f'{failed} failed')
    elif skipped and not passed:
        print('skip: ' + '; '.join(skipped))
    elif not passed:
        print(f'FAIL: no test functions in {argv[1]}')
        return 1
    else:
        print('all passed')
    return 1 if failed else 0


if __name__ == '__main__':
    # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use(suitepath.NAVKIT_BE, suitepath.NAVKIT_LE)
    sys.exit(main(sys.argv))
