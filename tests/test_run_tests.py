r"""The test runner's command line (run_tests.py), with its test list replaced by two made-up tests (nothing else
runs): a known tier runs its tests; a misspelled tier or option is a usage error with a nonzero exit, never a run of
the quick tier; the value after --only is a name, not a tier. Pytest-style test files: found, named and tiered from
their source, and run by tests/functions.py with the runner's statuses (pass, fail, all or some skipped, a fixture
only pytest has).

  python -B tests\test_run_tests.py
"""
import contextlib
import io
import os
import sys
import tempfile

sys.dont_write_bytecode = True
if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use('.')
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
    # 2026-10-31: pytest-style files are found (not listed), named by their docstring, tiered by TIER, and run by
    # tests/functions.py with the runner's statuses
    with tempfile.TemporaryDirectory() as tmp:
        def made_up(name, text):
            path = os.path.join(tmp, name)
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(text)
            return path
        plain = made_up('test_plain.py', '"""made-up: plain functions\n\nmore"""\n\n\ndef test_one():\n    assert 1\n')
        later = made_up('test_later.py', '"""made-up later"""\nTIER = \'bf1943\'\n\n\ndef test_one():\n    pass\n')
        script = made_up('test_script.py', 'def test_x():\n    pass\n\n\nif __name__ == \'__main__\':\n    test_x()\n')
        wrong = made_up('test_wrong.py', 'TIER = \'bf19433\'\n\n\ndef test_x():\n    pass\n')
        check(run_tests.function_test(plain) == ('made-up: plain functions', 'quick'),
              f'a pytest-style file: named by its docstring\'s first line, quick ({run_tests.function_test(plain)})')
        check(run_tests.function_test(later) == ('made-up later', 'bf1943'), 'TIER = \'bf1943\' puts it in bf1943')
        check(run_tests.function_test(script) is None, 'a file with a __main__ block is a script, not found')
        try:
            run_tests.function_test(wrong)
            check(False, 'a misspelled TIER is an error')
        except ValueError as e:
            check('bf19433' in str(e), f'a misspelled TIER is an error ({e})')
        cases = [('pass', 'def test_a():\n    assert 1 + 1 == 2\n\n\ndef test_b(tmp_path):\n    assert tmp_path.is_dir()\n',
                  'ok  ', 'all passed'),
                 ('fail', 'def test_a():\n    assert 1 == 2, "made-up failure"\n\n\ndef test_b():\n    pass\n',
                  'FAIL', '1 failed'),
                 ('all skip', 'import unittest\n\n\ndef test_a():\n    raise unittest.SkipTest("no data")\n', 'SKIP', 'skip'),
                 ('some skip', 'import unittest\n\n\ndef test_a():\n    raise unittest.SkipTest("no data")\n\n\n'
                  'def test_b():\n    pass\n', 'ok* ', 'all passed'),
                 ('need a fixture only pytest has', 'def test_a(monkeypatch):\n    pass\n', 'FAIL', '1 failed')]
        for k, (what, text, status_wanted, last_wanted) in enumerate(cases):
            path = made_up(f'test_case{k}.py', text)
            status, last, out, _ = run_tests.run_one([PY, run_tests.FUNCTIONS, path])
            check(status == status_wanted and last.startswith(last_wanted),
                  f'functions.py, a file whose tests {what}: {status.strip()} ({last})')
    names = [name for name, _, argv in run_tests.function_tests() if argv[1] == run_tests.FUNCTIONS]
    check('water mesh reader: triangles past their vertices refused' in names
          and 'NavKit (both variants): headroom band' in names,
          f'the suite\'s pytest-style files are found ({len(names)})')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
