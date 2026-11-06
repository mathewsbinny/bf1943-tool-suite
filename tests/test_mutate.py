r"""Mutation testing tool (tools/mutate.py): operators, keys, configuration and a run on a made-up module

tools/mutate.py on made-up sources (no game data): each operator finds its changes and leaves docstrings,
annotations, f-strings, `__main__` and TYPE_CHECKING blocks, `while True` and `# no mutate` lines alone; a mutant
applies to its own source only and compiles; keys repeat with #<n>; the configuration and its equivalents are read
(a reason is required); module names resolve; argv paths become {suite} however the folder is spelt (2026-11-05: a
work folder given with / ran every test from the first copy); and a whole run on a made-up suite of one module and
one test script scores it: killed, survived, timed out (an endless loop, killed with its process), not covered and
equivalent mutants each counted, the canary killed in every copy, and the suite itself never changed.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import ast
import contextlib
import importlib.util
import io
import os
import pathlib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)


def tool():
    """tools/mutate.py as a module (tools/ is not on sys.path)."""
    name = 'test_mutate_tool'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SUITE, 'tools', 'mutate.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SOURCE = '''"""A docstring: 1 < 2 is left alone."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    LIMIT = 1

COUNT: int = 3


def f(a: int, b: list[int] = [1]) -> int:
    """Not mutated either."""
    if a < b[0] and a != 0:
        return a + 1
    while True:
        break
    x = not a
    y = a if a >= 2 else -a
    z = [v for v in b if v is None]
    assert a in b
    s = 'n' + str(a) + f'{a + 1}'
    a <<= 2
    q = a // 3 & 1  # no mutate
    if a == 5:
        raise ValueError('five')
    return a == 5 or x or y or z or s or q


if __name__ == '__main__':
    f(1 + 1)
'''


def test_operators_and_exclusions():
    m = tool()
    found = m.mutants(SOURCE, 'pkg/sample.py')
    changes = [(x.operator, x.before, x.after) for x in found]
    want = [
        ('constant', '3', '4'),                                           # COUNT's value, not its annotation
        ('constant', '1', '2'),                                           # b's default
        ('negate', 'a < b[0] and a != 0', 'not (a < b[0] and a != 0)'),
        ('boolop', 'a < b[0] and a != 0', 'a < b[0] or a != 0'),
        ('drop', 'a < b[0] and a != 0', 'a != 0'),
        ('drop', 'a < b[0] and a != 0', 'a < b[0]'),
        ('compare', 'a < b[0]', 'a <= b[0]'),
        ('constant', '0', '1'),
        ('compare', 'a != 0', 'a == 0'),
        ('constant', '0', '1'),
        ('arith', 'a + 1', 'a - 1'),
        ('constant', '1', '2'),
        ('not', 'not a', 'a'),
        ('negate', 'a >= 2', 'not a >= 2'),
        ('compare', 'a >= 2', 'a > 2'),
        ('constant', '2', '3'),
        ('negate', 'v is None', 'not v is None'),
        ('compare', 'v is None', 'v is not None'),
        ('negate', 'a in b', 'not a in b'),
        ('compare', 'a in b', 'a not in b'),
        ('arith', 'a <<= 2', 'a >>= 2'),
        ('constant', '2', '3'),
        ('negate', 'a == 5', 'not a == 5'),
        ('compare', 'a == 5', 'a != 5'),
        ('constant', '5', '6'),
        ('raise', "raise ValueError('five')", 'pass'),
        ('boolop', 'a == 5 or x or y or z or s or q', 'a == 5 and x and y and z and s and q'),
    ]
    assert changes[:len(want)] == want, [c for c in changes if c not in want][:5] or changes[len(want):len(want) + 3]
    rest = changes[len(want):]
    assert [c[0] for c in rest] == ['drop'] * 6 + ['compare', 'constant'], rest
    assert not any('str(a)' in c[1] and c[0] == 'arith' for c in changes), 'string joining is no arithmetic'
    assert not any(c[1] in ('True', '1 < 2', '2') and c[0] == 'negate' for c in changes)
    assert not any(c[1].startswith('a // 3') for c in changes), '# no mutate'
    assert all(x.function == ('f' if 9 < x.line < 27 else '<module>') for x in found), \
        [(x.line, x.function) for x in found]
    keys = [x.key for x in found]
    assert len(set(keys)) == len(keys), 'every key unique'
    assert 'sample.py:f:constant:0 -> 1 #2' in keys and 'sample.py:f:constant:0 -> 1' in keys
    assert keys[0] == 'sample.py:<module>:constant:3 -> 4'


def test_apply():
    m = tool()
    found = m.mutants(SOURCE, 'pkg/sample.py')
    texts = set()
    for x in found:
        text = m.apply(SOURCE, x)
        compile(text, 'sample.py', 'exec')
        assert ast.dump(ast.parse(text)) != ast.dump(ast.parse(SOURCE)), x.key
        texts.add(text)
    assert len(texts) == len(found), 'each mutant its own text'
    raised = m.apply(SOURCE, next(x for x in found if x.operator == 'raise'))
    assert "raise ValueError('five')" not in raised and 'pass' in raised
    other = SOURCE.replace('a < b[0]', 'a > b[0]')
    try:
        m.apply(other, found[6])
    except ValueError as e:
        assert 'does not match its source any more' in str(e), e
    else:
        raise AssertionError('a mutant applied to a source it was not found in')


def test_config(tmp_path):
    m = tool()
    (tmp_path / 'pyproject.toml').write_text('[tool.other]\nx = 1\n', encoding='utf-8')
    try:
        m.load_config(str(tmp_path))
    except ValueError as e:
        assert 'no [tool.bf1943-mutation] modules' in str(e)
    else:
        raise AssertionError('a config without modules read')
    (tmp_path / 'pyproject.toml').write_text(
        '[tool.bf1943-mutation]\nmodules = ["a/b.py", "a/c.py"]\nthreshold = 90\nworkers = 3\n'
        'equivalents = "eq.toml"\n', encoding='utf-8')
    (tmp_path / 'eq.toml').write_text('[[equivalent]]\nkey = "b.py:f:x"\nreason = "why"\n', encoding='utf-8')
    c = m.load_config(str(tmp_path))
    assert (c.modules, c.threshold, c.workers, c.timeout_factor, c.equivalents) == \
        (['a/b.py', 'a/c.py'], 90, 3, 3.0, {'b.py:f:x': 'why'})
    (tmp_path / 'eq.toml').write_text('[[equivalent]]\nkey = "b.py:f:x"\nreason = " "\n', encoding='utf-8')
    try:
        m.load_config(str(tmp_path))
    except ValueError as e:
        assert 'without a reason: b.py:f:x' in str(e), e
    else:
        raise AssertionError('an equivalent without its reason read')
    assert m.resolve([], c.modules) == c.modules
    assert m.resolve(['c', 'b.py', 'c.py'], c.modules) == ['a/c.py', 'a/b.py']
    try:
        m.resolve(['d'], c.modules)
    except ValueError as e:
        assert 'd: not a module' in str(e) and '(b.py, c.py)' in str(e), e
    else:
        raise AssertionError('an unknown module resolved')


def test_suite_config_and_list():
    m = tool()
    c = m.load_config(SUITE)
    assert c.threshold >= 80 and len(c.modules) >= 10 and all(os.path.isfile(os.path.join(SUITE, f)) for f in c.modules)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert m.main(['mutate.py', '--list', 'game1943']) == 0
    lines = out.getvalue().splitlines()
    assert lines[-1].endswith(' mutants') and int(lines[-1].split()[0]) == len(lines) - 1 > 10, lines[-1]
    assert all(line.startswith('core/bfbc2core/game1943.py:') for line in lines[:-1])
    for argv, message in ((['nosuch'], 'nosuch: not a module'), (['--workers', '0', 'dbx'], 'must be 1 or more')):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert m.main(['mutate.py', *argv]) == 2
        assert message in out.getvalue(), out.getvalue()


def test_template():
    m = tool()
    root = os.path.join('C:' + os.sep, 'Suite', 'Copy-0')
    assert m._template(os.path.join(root, 'core', 't.py'), root) == '{suite}' + os.sep + os.path.join('core', 't.py')
    assert m._template(root, root) == '{suite}'
    if os.name == 'nt':
        assert m._template(root.lower() + os.sep + 'x.py', root) == '{suite}' + os.sep + 'x.py', 'case ignored'
    assert m._template(root + '-other' + os.sep + 'x.py', root) == root + '-other' + os.sep + 'x.py'
    assert m._template('--flag', root) == '--flag'


TOY = '''def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def count_to(n):
    i = 0
    while i < n:
        i += 1
    return i


def check(x):
    if x is None:
        raise ValueError('none')
    return x


def unused(a):
    return a * 2
'''

TOY_TEST = '''import toy

assert toy.clamp(5, 0, 10) == 5
assert toy.clamp(-1, 0, 10) == 0
assert toy.clamp(11, 0, 10) == 10
assert toy.count_to(3) == 3
try:
    toy.check(None)
except ValueError:
    pass
else:
    raise SystemExit(1)
print('all passed')
'''


def test_a_run(tmp_path):
    m = tool()
    suite = tmp_path / 'suite'
    suite.mkdir()
    (suite / 'toy.py').write_text(TOY, encoding='utf-8', newline='\n')
    (suite / 'test_toy.py').write_text(TOY_TEST, encoding='utf-8', newline='\n')
    (suite / 'work').mkdir()
    (suite / 'work' / 'big.bin').write_bytes(b'not copied')
    before = (suite / 'toy.py').read_bytes()
    equivalent = 'toy.py:clamp:compare:x < lo -> x <= lo'
    config = m.Config(modules=['toy.py'], timeout_factor=1.0, timeout_extra=4.0,
                      equivalents={equivalent: 'x == lo returns lo, which is x', 'toy.py:gone:raise:x -> pass': 'old'})
    lines = []
    tests = [('toy', [sys.executable, '-B', os.path.join('{suite}', 'test_toy.py')])]
    scores = m.run(str(suite), ['toy.py'], config, 2, str(tmp_path / 'work'), tests=tests, log=lines.append)
    assert (suite / 'toy.py').read_bytes() == before, 'the suite is never changed'
    assert not (tmp_path / 'work' / 'copy-0' / 'work').exists(), 'no work folder copied'
    [s] = scores
    found = {x.key: x for x in m.mutants(TOY, 'toy.py')}
    survived = {x.key for x in s.survived}
    not_covered = {x.key for x in s.not_covered}
    assert [x.key for x in s.equivalent] == [equivalent]
    assert 'toy.py:count_to:constant:0 -> 1' in survived, 'count_to(0) is not tested'
    assert 'toy.py:clamp:compare:x > hi -> x >= hi' in survived, 'clamp(10, 0, 10) is not tested'
    assert s.timeout >= 1, 'i -= 1 loops for ever'
    unused = {k for k in found if k.startswith('toy.py:unused:')}
    assert unused and unused <= (not_covered | survived), 'nothing tests unused()'
    import importlib.util as iu
    if iu.find_spec('coverage') is not None:
        assert unused <= not_covered, 'coverage shows no test runs unused()'
    assert s.total == len(found) == s.killed + s.timeout + len(survived) + len(not_covered) + 1
    assert abs(s.percent - 100.0 * (s.killed + s.timeout) / (len(found) - 1)) < 1e-9
    result = m.report(scores, config, set(found))
    assert result['stale_equivalents'] == ['toy.py:gone:raise:x -> pass']
    assert result['modules'][0]['equivalent'] == 1 and result['modules'][0]['mutants'] == len(found)
    assert any('toy.py: ' in line and '% killed' in line for line in lines), lines[-3:]


JUNK_TEST = '''import os
import toy

data = os.environ.get('COVERAGE_FILE')
if data:                                    # a coverage data file cut short (a child stopped while writing it)
    with open(data + '.junk', 'wb') as f:
        f.write(b'SQLite format 3' + bytes(40))
assert toy.clamp(5, 0, 10) == 5
'''


def test_a_run_by_short_path(tmp_path):
    """The run in a folder named by its 8.3 short path, as GitHub's runner temp (C:\\Users\\RUNNER~1): coverage records
    the long path, and before 2026-11-05 no measured file matched its copy ("no passing test runs it")."""
    if os.name != 'nt':
        return
    import ctypes
    long_dir = tmp_path / 'a folder with a long name'
    long_dir.mkdir()
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buffer, 1024)
    if not buffer.value or os.path.normcase(buffer.value) == os.path.normcase(str(long_dir)):
        print('skip: this volume makes no 8.3 names')
        return
    test_a_run(pathlib.Path(buffer.value))


def test_unreadable_coverage_data(tmp_path):
    # 2026-11-05: a test whose child left a damaged data file stopped the whole run (sqlite3.DatabaseError); such a
    # test now counts as running every line
    m = tool()
    suite = tmp_path / 'suite'
    suite.mkdir()
    (suite / 'toy.py').write_text(TOY, encoding='utf-8', newline='\n')
    (suite / 'test_junk.py').write_text(JUNK_TEST, encoding='utf-8', newline='\n')
    (tmp_path / 'empty').mkdir()
    lines = []
    tests = [('junk', [sys.executable, '-B', os.path.join('{suite}', 'test_junk.py')])]
    base = m.baseline(tests, [str(suite)], ['toy.py'], str(tmp_path), str(tmp_path / 'empty'), lines.append)
    assert base.failed == [] and base.runs('junk', 'toy.py', {9999})
    import importlib.util as iu
    if iu.find_spec('coverage') is not None:
        assert base.unknown == {'junk'} and any('did not read' in line for line in lines), lines
