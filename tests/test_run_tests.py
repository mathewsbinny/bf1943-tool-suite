r"""The test runner's command line (run_tests.py), with its test list replaced by two made-up tests (nothing else
runs): a known tier runs its tests; a misspelled tier or option is a usage error with a nonzero exit, never a run of
the quick tier; the value after --only is a name, not a tier.

  python -B tests\test_run_tests.py
"""
import contextlib
import io
import os
import sys

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SUITE)
import run_tests  # noqa: E402

PY = sys.executable
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def run(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run_tests.main(['run_tests.py', *args])
    return code, buf.getvalue()


if __name__ == '__main__':
    run_tests.tests = lambda: [('made-up quick test', 'quick', [PY, '-c', 'print("all passed")']),
                               ('made-up bf1943 test', 'bf1943', [PY, '-c', 'print("all passed")'])]
    code, out = run('quick', '--strict')
    check(code == 0 and '1 of 1 passed' in out, f'quick --strict runs the quick test (exit {code})')
    code, out = run('bf1943', '--list')
    check(code == 0 and 'made-up bf1943 test' in out and 'made-up quick test' in out, f'bf1943 --list lists both (exit {code})')
    code, out = run('--only', 'bf1943 test', 'bf1943')
    check(code == 0 and '1 of 1 passed' in out, f'--only takes a name that holds a tier\'s name (exit {code})')
    code, out = run('quick', '--only', 'no such test name')
    check(code == 1 and 'matches no quick test' in out, f'--only naming no test is an error, exit {code}')
    for args in (['bf19433', '--strict'], ['quik'], ['--strcit'], ['quick', 'bf1943'], ['quick', '--only']):
        try:
            code, out = run(*args)
        except Exception as e:                      # 2026-10-30: --only without a value raised IndexError
            code, out = f'{type(e).__name__}', ''
        check(code == 2 and 'usage' in out.lower() and 'passed' not in out, f'{" ".join(args)}: a usage error, exit {code}'
              + ('' if code == 2 else '\n       ' + out.strip().replace('\n', '\n       ')[:400]))
    # 2026-10-30 second review: a test's output reaches the runner in UTF-8 (it is decoded so), whatever the ANSI code
    # page: a test printing a Cyrillic path passed only when started from the hub
    for k in ('PYTHONIOENCODING', 'PYTHONUTF8'):
        os.environ.pop(k, None)
    text = 'made-up path C:/Users/\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c caf\u00e9'
    run_tests.tests = lambda: [('prints outside the code page', 'quick', [PY, '-c', f'print({text!r})'])]
    code, out = run('quick')
    check(code == 0 and text in out, f'a test printing outside the ANSI code page passes and its line reads back (exit {code})')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
