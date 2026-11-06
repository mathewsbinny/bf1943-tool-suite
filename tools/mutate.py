r"""Mutation testing of the suite's core modules: does a test fail when the code is wrong? (README "Mutation testing")

Line and branch coverage say a test ran a line; a mutant says whether a test noticed it was wrong. Each mutant is the
module with one small change (a comparison flipped, a constant one off, `and` for `or`, a condition removed or negated,
a `raise` removed, `+` for `-`); the quick-tier tests that run the changed line are run against it, and the mutant is
killed when one of them fails (or runs past its time limit), survived when all pass. A module's score is the killed
share of its mutants, the equivalent ones left out (a change no test could see, listed with its reason in
tools/mutation_equivalents.toml).

  python -B tools\mutate.py [<module> ...] [--workers <n>] [--list] [--report <file.json>]

    <module>        a module of pyproject.toml [tool.bf1943-mutation] modules, by file name (dbx.py) or stem
                    (dbx); default: all of them
    --workers <n>   test processes at once (default: the config's workers, 2); each has its own copy of the suite
    --list          list the mutants (with their keys) and run nothing
    --report <f>    write the results as JSON (default: work\mutation\report.json)

Exit code 1 when a module scores below the threshold ([tool.bf1943-mutation] threshold, percent), 2 on a usage error.

How a run goes. The suite's files (git's tracked and new ones; no work folder, no game data) are copied to
work\mutation\copy-<n>, one copy per worker: the suite itself is never changed, so a tool running from it meanwhile
(a level build) never imports a mutant. The quick tier's tests are taken from the copy's run_tests.py and run once,
unchanged, each under coverage that measures only the chosen modules (coverage is in the "ci" group; its .pth file
starts it in every child process too): that run gives each test's time and the lines of each module it runs. A test
that fails unchanged is left out (reported), and one whose coverage data does not read (a child it stopped while
the child wrote it) counts as running every line. Each module then gets a canary, a mutant that raises on import, which
its fastest test must kill in every copy (else the tests do not run that copy: 2026-11-05, a work folder given
with / ran every test from the first copy). Then each mutant: written into a worker's copy, the tests
that run its line (fastest first) until one fails; the module is restored before the next. A test's time limit is
`timeout_factor` x its unchanged time + `timeout_extra` seconds; a test past it is killed with its children, and the
mutant counts as killed (a hang is a change the tests saw). A mutant on a line no test runs is "not covered" and
counts as survived without running anything. Tests run as run_tests.py runs them, with BF1943_GAME and
BF1943_RESEARCH pointing at an empty folder as in CI, and never write bytecode (-B and PYTHONDONTWRITEBYTECODE), so
no stale cache can hide a mutant.

Mutation operators (standard library ast; the mutated module is written back with ast.unparse, so comments and
layout differ from the source, behaviour does not: the unchanged run checks that through the canary's tests):
  compare     < <=, > >=, == !=, in / not in, is / is not: each swapped for its neighbour
  constant    an integer literal n becomes n + 1 (off by one); True / False are left alone
  boolop      `a and b` becomes `a or b` and back
  drop        one operand of `and` / `or` removed (a condition removed)
  negate      the test of if, while, a conditional expression, assert, or a comprehension's if, negated
  not         `not x` becomes `x`
  raise       a raise statement becomes `pass`
  arith       + -, * /, // /, << >>, & |: swapped (also in +=, ...); not where an operand is a string
Left alone: docstrings, annotations, f-strings, `if __name__ == '__main__':` and `if TYPE_CHECKING:` blocks,
`while True`, and any line ending in `# no mutate`.

A mutant's key names it across edits that move its line: `<file>:<function>:<operator>:<before> -> <after>`, with
`#<n>` when the same change occurs more than once in one function.
"""
import argparse
import ast
import copy
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType
from typing import Any, cast

SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CANARY = "raise RuntimeError('mutation canary: the tests run this copy')\n"
NO_MUTATE = '# no mutate'

COMPARE: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
ARITH: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Div,
    ast.LShift: ast.RShift, ast.RShift: ast.LShift, ast.BitAnd: ast.BitOr, ast.BitOr: ast.BitAnd,
}


@dataclasses.dataclass(frozen=True)
class Mutant:
    """One change to one module: its place in the module's mutant order (index), where, and what."""
    file: str                   # the module's path relative to the suite, with /
    index: int
    line: int
    end_line: int
    function: str               # the enclosing function's qualified name ('<module>' at the top level)
    operator: str
    before: str
    after: str
    key: str


# --- finding and applying mutants -------------------------------------------------------------------------------

class _Walker(ast.NodeTransformer):
    """Walks a module in a fixed order, numbering each possible mutation. With target None it records every one
    (as Mutant fields); with a target number it applies that one and leaves the rest."""

    def __init__(self, lines: list[str], target: int | None) -> None:
        self.lines = lines
        self.target = target
        self.found: list[tuple[int, int, str, str, str, str]] = []     # (line, end, function, operator, before, after)
        self.count = 0
        self.applied: tuple[str, str] | None = None
        self.scope: list[str] = []

    # -- the one decision: record, apply, or pass -- #
    def _offer(self, node: ast.AST, operator: str, make: Callable[[], ast.AST]) -> ast.AST | None:
        """Mutation number self.count at node: returns the replacement when it is the target, else None."""
        line = getattr(node, 'lineno', 0)
        end = getattr(node, 'end_lineno', None) or line
        if line and self.lines[line - 1].rstrip().endswith(NO_MUTATE):
            return None
        number = self.count
        self.count += 1
        if self.target is None:
            after = make()
            self.found.append((line, end, '.'.join(self.scope) or '<module>', operator, _text(node), _text(after)))
            return None
        if number != self.target:
            return None
        after = make()
        self.applied = (_text(node), _text(after))
        return ast.copy_location(after, node) if isinstance(after, (ast.expr, ast.stmt)) else after

    def _visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> ast.AST:
        self.scope.append(node.name)
        node.decorator_list = [self.visit(d) for d in node.decorator_list]
        if isinstance(node, ast.ClassDef):
            node.bases = [self.visit(b) for b in node.bases]
        else:
            node.args = self.visit(node.args)
        node.body = self._body(node.body)
        self.scope.pop()
        return node

    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope
    visit_ClassDef = _visit_scope

    def _body(self, body: list[ast.stmt]) -> list[ast.stmt]:
        """A statement list, its docstring left alone."""
        out: list[ast.stmt] = []
        for k, stmt in enumerate(body):
            if k == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, str):
                out.append(stmt)
                continue
            new = self.visit(stmt)
            out.extend(new if isinstance(new, list) else [new] if new is not None else [])
        return out

    def visit_Module(self, node: ast.Module) -> ast.AST:
        node.body = self._body(node.body)
        return node

    # -- left alone -- #
    def visit_arg(self, node: ast.arg) -> ast.AST:
        return node                                     # its annotation

    def visit_arguments(self, node: ast.arguments) -> ast.AST:
        node.defaults = [self.visit(d) for d in node.defaults]
        node.kw_defaults = [self.visit(d) if d is not None else None for d in node.kw_defaults]  # pyright: ignore[reportAttributeAccessIssue]  # None stands for a keyword without a default
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        if node.value is not None:
            node.value = self.visit(node.value)
        return node

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        return node

    def visit_TypeAlias(self, node: ast.TypeAlias) -> ast.AST:
        return node

    def visit_If(self, node: ast.If) -> ast.AST:
        test = ast.unparse(node.test)
        if test in ("__name__ == '__main__'", 'TYPE_CHECKING', 'typing.TYPE_CHECKING'):
            return node
        return self._negate_test(node)

    # -- the operators -- #
    def _negate_test(self, node: ast.If | ast.While | ast.IfExp | ast.Assert) -> ast.AST:
        constant = isinstance(node.test, ast.Constant)  # `while True`, `if 0`: no condition to negate
        if not constant:
            new = self._offer(node.test, 'negate', lambda: ast.UnaryOp(ast.Not(), copy.deepcopy(node.test)))
            if new is not None:
                node.test = cast(ast.expr, new)         # a test's replacement is an expression
                return node
        return self.generic_visit(node)

    visit_While = _negate_test
    visit_IfExp = _negate_test
    visit_Assert = _negate_test

    def visit_comprehension(self, node: ast.comprehension) -> ast.AST:
        for k, cond in enumerate(node.ifs):
            new = self._offer(cond, 'negate', lambda c=cond: ast.UnaryOp(ast.Not(), copy.deepcopy(c)))
            if new is not None:
                node.ifs[k] = cast(ast.expr, new)       # a test's replacement is an expression
                return node
        return self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        for k, op in enumerate(node.ops):
            swap = COMPARE.get(type(op))
            if swap is None:
                continue

            def make(k: int = k, swap: type[ast.cmpop] = swap) -> ast.AST:
                new = copy.deepcopy(node)
                new.ops[k] = swap()
                return new
            new = self._offer(node, 'compare', make)
            if new is not None:
                return new
        return self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        value = node.value
        if type(value) is int:
            new = self._offer(node, 'constant', lambda: ast.Constant(cast(int, value) + 1))
            if new is not None:
                return new
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        other = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        new = self._offer(node, 'boolop', lambda: ast.BoolOp(other, copy.deepcopy(node.values)))
        if new is not None:
            return new
        for k in range(len(node.values)):
            def make(k: int = k) -> ast.AST:
                rest = [copy.deepcopy(v) for j, v in enumerate(node.values) if j != k]
                return rest[0] if len(rest) == 1 else ast.BoolOp(copy.deepcopy(node.op), rest)
            new = self._offer(node, 'drop', make)
            if new is not None:
                return new
        return self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        if isinstance(node.op, ast.Not):
            new = self._offer(node, 'not', lambda: copy.deepcopy(node.operand))
            if new is not None:
                return new
        return self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> ast.AST:
        new = self._offer(node, 'raise', ast.Pass)
        if new is not None:
            return new
        return self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        swap = ARITH.get(type(node.op))
        if swap is not None and not (_stringy(node.left) or _stringy(node.right)):
            new = self._offer(node, 'arith', lambda: ast.BinOp(copy.deepcopy(node.left), swap(),
                                                               copy.deepcopy(node.right)))
            if new is not None:
                return new
        return self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AST:
        swap = ARITH.get(type(node.op))
        if swap is not None and not _stringy(node.value):
            new = self._offer(node, 'arith', lambda: ast.AugAssign(copy.deepcopy(node.target), swap(),
                                                                   copy.deepcopy(node.value)))
            if new is not None:
                return new
        return self.generic_visit(node)


def _stringy(node: ast.expr) -> bool:
    """A string literal or f-string: + and % on it are joining and formatting, not arithmetic."""
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)))


def _text(node: ast.AST) -> str:
    text = 'pass' if isinstance(node, ast.Pass) else ast.unparse(node)
    text = ' '.join(text.split())
    return text if len(text) <= 120 else text[:117] + '...'


def mutants(source: str, file: str) -> list[Mutant]:
    """Every mutant of a module's source, in a fixed order (index); file names it in the keys."""
    walker = _Walker(source.splitlines(), None)
    walker.visit(ast.parse(source))
    seen: dict[str, int] = {}
    found: list[Mutant] = []
    for index, (line, end, function, operator, before, after) in enumerate(walker.found):
        base = f'{os.path.basename(file)}:{function}:{operator}:{before} -> {after}'
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f'{base} #{seen[base]}'
        found.append(Mutant(file, index, line, end, function, operator, before, after, key))
    return found


def apply(source: str, mutant: Mutant) -> str:
    """The module's source with the one mutant applied (written with ast.unparse)."""
    walker = _Walker(source.splitlines(), mutant.index)
    tree = ast.fix_missing_locations(walker.visit(ast.parse(source)))
    if walker.applied != (mutant.before, mutant.after):
        raise ValueError(f'mutant {mutant.index} of {mutant.file} does not match its source any more: {mutant.key}')
    return ast.unparse(tree) + '\n'


# --- configuration ----------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Config:
    modules: list[str]
    threshold: float = 80.0
    workers: int = 2
    timeout_factor: float = 3.0
    timeout_extra: float = 20.0
    tier: str = 'quick'
    equivalents: dict[str, str] = dataclasses.field(default_factory=dict)   # key: reason


def load_config(suite: str) -> Config:
    """pyproject.toml [tool.bf1943-mutation], and its equivalents file (a [[equivalent]] list of key and reason)."""
    with open(os.path.join(suite, 'pyproject.toml'), 'rb') as f:
        table = tomllib.load(f).get('tool', {}).get('bf1943-mutation')
    if not table or not table.get('modules'):
        raise ValueError('pyproject.toml has no [tool.bf1943-mutation] modules')
    config = Config(modules=list(table['modules']))
    for name in ('threshold', 'workers', 'timeout_factor', 'timeout_extra', 'tier'):
        if name in table:
            setattr(config, name, table[name])
    if table.get('equivalents'):
        with open(os.path.join(suite, table['equivalents']), 'rb') as f:
            for entry in tomllib.load(f).get('equivalent', []):
                if not entry.get('reason', '').strip():
                    raise ValueError(f'equivalent mutant without a reason: {entry.get("key")}')
                config.equivalents[entry['key']] = entry['reason']
    return config


# --- running tests ----------------------------------------------------------------------------------------------

type Test = tuple[str, list[str]]        # (name, argv with {suite} for the suite's folder)


def suite_tests(suite: str, tier: str) -> list[Test]:
    """The tier's tests from a suite's run_tests.py (its scripts and pytest-style files), each argv with the suite's
    folder as {suite}, so that it runs in any copy."""
    module = _load(os.path.join(suite, 'run_tests.py'), 'mutate_run_tests')
    chosen = [(name, args) for name, test_tier, args in module.tests() if test_tier in module.SELECT[tier]]
    # the runner's own spelling of its folder (abspath: backslashes, as its argv has them); compared ignoring case,
    # as Windows does. 2026-11-05: a work folder given with / was not found in the argv, so every test ran from
    # the first copy whichever copy held the mutant
    root = module.SUITE
    return [(name, [_template(a, root) for a in args]) for name, args in chosen]


def _template(arg: str, root: str) -> str:
    """arg with the folder root (and anything under it) written as {suite}."""
    if os.path.normcase(arg) == os.path.normcase(root):
        return '{suite}'
    if os.path.normcase(arg).startswith(os.path.normcase(root) + os.sep):
        return '{suite}' + arg[len(root):]
    return arg


def _load(path: str, name: str) -> ModuleType:
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f'{path} cannot be loaded')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_suite(suite: str, dest: str) -> None:
    """The suite's files copied to dest, which is replaced: as git lists them (tracked, and new ones not ignored),
    or all of them outside work, output and caches when the suite is not a clone."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    if os.path.isdir(os.path.join(suite, '.git')):
        listed = subprocess.run(['git', '-C', suite, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                capture_output=True, check=True).stdout.decode('utf-8').split('\0')
    else:                                               # not a clone (an exported tree): every file, the folders
        listed = []                                     # the runner skips left out
        for folder, dirs, names in os.walk(suite):
            dirs[:] = [d for d in dirs if d not in ('.git', 'work', 'output', '__pycache__', '.pytest_cache')]
            listed += [os.path.relpath(os.path.join(folder, n), suite) for n in names]
    for rel in filter(None, listed):
        src = os.path.join(suite, rel)
        if os.path.isfile(src):                         # a tracked file deleted in the working copy is not there
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, rel))


def kill_tree(process: 'subprocess.Popen[Any]') -> None:
    """The process and every child it started."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True)
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)          # pyright: ignore[reportAttributeAccessIssue]  # POSIX only
    process.wait()


def run_test(args: list[str], copy_dir: str, env: dict[str, str], limit: float | None) -> tuple[str, float]:
    """('pass' | 'fail' | 'timeout', seconds) of one test run in a copy of the suite."""
    argv = [a.replace('{suite}', copy_dir) for a in args]
    flags: dict[str, Any] = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
                             else {'start_new_session': True})
    start = time.monotonic()
    process = subprocess.Popen(argv, cwd=copy_dir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, **flags)
    try:
        code = process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        return 'timeout', time.monotonic() - start
    return ('pass' if code == 0 else 'fail'), time.monotonic() - start


def test_env(empty: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment tests run in: no game copy and no research workspace (an empty folder, as in CI), no
    bytecode, UTF-8 output, and no coverage unless extra asks for it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('COVERAGE_')}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', BF1943_GAME=empty, BF1943_RESEARCH=empty)
    env.update(extra or {})
    return env


@dataclasses.dataclass
class Baseline:
    """Each test's unchanged run: its time, and the lines of each chosen module it ran (None: coverage missing,
    so every passing test counts as running every line)."""
    seconds: dict[str, float]
    lines: dict[str, dict[str, set[int]]] | None        # test name: {module file: lines}
    failed: list[str]
    # tests whose coverage data did not read (a child process stopped by its test while writing it, 2026-11-05):
    # counted as running every line, so that no mutant misses them
    unknown: set[str] = dataclasses.field(default_factory=set)

    def runs(self, test: str, file: str, span: set[int] | None = None) -> bool:
        """Whether the test runs the file (any of span's lines, when given)."""
        if self.lines is None or test in self.unknown:
            return True
        ran = self.lines.get(test, {}).get(file, set())
        return bool(ran & span) if span is not None else bool(ran)


def baseline(tests: list[Test], copies: list[str], files: list[str], work: str, empty: str,
             log: Callable[[str], None]) -> Baseline:
    """Runs every test once, unchanged, measuring the chosen modules' lines per test when coverage is installed."""
    try:
        import coverage
    except ImportError:
        coverage = None
        log('coverage is not installed (the "ci" group): every test is run for every mutant')
    rc = os.path.join(work, 'coveragerc')
    with open(rc, 'w', encoding='utf-8', newline='\n') as f:
        f.write('[run]\nparallel = true\nbranch = false\ninclude =\n'
                + ''.join(f'    */{file}\n' for file in files))
    seconds: dict[str, float] = {}
    lines: dict[str, dict[str, set[int]]] = {}
    failed: list[str] = []
    unknown: set[str] = set()
    lock = threading.Lock()
    free = list(copies)

    def one(k: int, test: Test) -> None:
        name, args = test
        with lock:
            copy_dir = free.pop()
        try:
            data = os.path.join(work, 'baseline', str(k))
            os.makedirs(data, exist_ok=True)
            extra = {'COVERAGE_PROCESS_START': rc, 'COVERAGE_FILE': os.path.join(data, '.coverage')} if coverage else {}
            status, took = run_test(args, copy_dir, test_env(empty, extra), None)
            ran: dict[str, set[int]] = {}
            unreadable = False
            # coverage records the long, resolved path: compared with the copy's resolved path too, as a short
            # (8.3) one, such as GitHub's runner temp C:\Users\RUNNER~1, never matched (2026-11-05)
            real_copy = os.path.realpath(copy_dir)
            if coverage and status == 'pass':
                # each process's data file read as it is: no Coverage object here, as one made in a process that
                # coverage measures (this tool's own test under CI's coverage job) kept that process's data from
                # being saved (2026-11-05)
                for name_ in sorted(os.listdir(data)):
                    if not name_.startswith('.coverage.'):
                        continue
                    measured = coverage.CoverageData(basename=os.path.join(data, name_))
                    try:
                        measured.read()
                        for path in measured.measured_files():
                            rel = os.path.relpath(os.path.realpath(path), real_copy).replace(os.sep, '/')
                            if rel in files:
                                ran.setdefault(rel, set()).update(measured.lines(path) or [])
                    except (coverage.CoverageException, OSError):
                        unreadable = True
                    finally:
                        measured.close()        # the database file, so that it can be removed (Windows)
            with lock:
                seconds[name] = took
                lines[name] = ran
                if unreadable:
                    unknown.add(name)
                    log(f'  (a coverage data file of {name!r} did not read: it counts as running every line)')
                if status != 'pass':
                    failed.append(name)
                log(f'  {status:7s} {took:6.1f} s  {name}' + (f'  ({", ".join(sorted(os.path.basename(f) for f in ran))})' if ran else ''))
        finally:
            with lock:
                free.append(copy_dir)

    with ThreadPoolExecutor(max_workers=len(copies)) as pool:
        for future in [pool.submit(one, k, test) for k, test in enumerate(tests)]:
            future.result()
    return Baseline(seconds, lines if coverage else None, failed, unknown)


def covering(mutant: Mutant, tests: list[Test], base: Baseline) -> list[Test]:
    """The passing tests that run the mutant's lines, fastest first."""
    span = set(range(mutant.line, mutant.end_line + 1))
    chosen = [t for t in tests if t[0] not in base.failed and base.runs(t[0], mutant.file, span)]
    return sorted(chosen, key=lambda t: base.seconds.get(t[0], 0.0))


def try_mutant(source_text: str, copy_dir: str, file: str, tests: list[Test], base: Baseline, config: Config,
               empty: str) -> tuple[str, str]:
    """(outcome, the test that decided it) of a module text written into a copy: 'killed' (a test failed),
    'timeout' (a test ran past its limit), 'survived' (every test passed) or 'not covered' (no test runs it)."""
    if not tests:
        return 'not covered', ''
    path = os.path.join(copy_dir, file)
    with open(path, 'rb') as f:
        original = f.read()
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(source_text)
        for name, args in tests:
            limit = config.timeout_factor * base.seconds.get(name, 10.0) + config.timeout_extra
            status, _ = run_test(args, copy_dir, test_env(empty), limit)
            if status == 'fail':
                return 'killed', name
            if status == 'timeout':
                return 'timeout', name
        return 'survived', ''
    finally:
        with open(path, 'wb') as f:
            f.write(original)


# --- a run ------------------------------------------------------------------------------------------------------

@dataclasses.dataclass
class Score:
    file: str
    total: int = 0
    killed: int = 0
    timeout: int = 0
    survived: list[Mutant] = dataclasses.field(default_factory=list)
    not_covered: list[Mutant] = dataclasses.field(default_factory=list)
    equivalent: list[Mutant] = dataclasses.field(default_factory=list)

    @property
    def counted(self) -> int:
        return self.total - len(self.equivalent)

    @property
    def percent(self) -> float:
        return 100.0 * (self.killed + self.timeout) / self.counted if self.counted else 100.0


def resolve(names: list[str], modules: list[str]) -> list[str]:
    """The config's module files that the command line names (by file name or stem); all of them when none."""
    if not names:
        return list(modules)
    chosen: list[str] = []
    for name in names:
        match = [m for m in modules if os.path.basename(m) in (name, name + '.py') or m == name]
        if not match:
            raise ValueError(f'{name}: not a module of [tool.bf1943-mutation] modules '
                             f'({", ".join(os.path.basename(m) for m in modules)})')
        chosen += [m for m in match if m not in chosen]
    return chosen


def run(suite: str, files: list[str], config: Config, workers: int, work: str,
        tests: list[Test] | None = None, log: Callable[[str], None] = print) -> list[Score]:
    """Scores each module: copies, the unchanged run, the canaries, then every mutant."""
    work = os.path.abspath(work)
    os.makedirs(work, exist_ok=True)
    empty = os.path.join(work, 'empty')
    os.makedirs(empty, exist_ok=True)
    copies = [os.path.join(work, f'copy-{k}') for k in range(max(1, workers))]
    for copy_dir in copies:
        copy_suite(suite, copy_dir)
    if tests is None:
        tests = suite_tests(copies[0], config.tier)
    sources = {}
    for file in files:
        with open(os.path.join(suite, file), encoding='utf-8') as f:
            sources[file] = f.read()
    log(f'unchanged run: {len(tests)} test(s), {len(copies)} at once')
    base = baseline(tests, copies, files, work, empty, log)
    if base.failed:
        log(f'left out, failing unchanged: {", ".join(base.failed)}')

    scores: list[Score] = []
    lock = threading.Lock()
    for file in files:
        found = mutants(sources[file], file)
        score = Score(file, total=len(found))
        users = [t for t in tests if t[0] not in base.failed and base.runs(t[0], file)]
        log(f'\n{file}: {len(found)} mutants, {len(users)} test(s) run it')
        if not users:
            raise RuntimeError(f'{file}: no passing test runs it')
        users.sort(key=lambda t: base.seconds.get(t[0], 0.0))
        for copy_dir in copies:                         # each copy: a mutant written there must be what runs
            outcome, _ = try_mutant(CANARY + sources[file], copy_dir, file, users[:1], base, config, empty)
            if outcome not in ('killed', 'timeout'):
                raise RuntimeError(f'{file}: the canary in {copy_dir} survived {users[0][0]!r}: the test does not '
                                   f'run that copy')
        free = list(copies)
        start = time.monotonic()

        def one(mutant: Mutant) -> None:
            if mutant.key in config.equivalents:
                with lock:
                    score.equivalent.append(mutant)
                return
            text = apply(sources[file], mutant)
            with lock:
                copy_dir = free.pop()
            try:
                outcome, _ = try_mutant(text, copy_dir, file, covering(mutant, tests, base), base, config, empty)
            finally:
                with lock:
                    free.append(copy_dir)
            with lock:
                if outcome == 'killed':
                    score.killed += 1
                elif outcome == 'timeout':
                    score.timeout += 1
                elif outcome == 'survived':
                    score.survived.append(mutant)
                else:
                    score.not_covered.append(mutant)
                done = score.killed + score.timeout + len(score.survived) + len(score.not_covered) \
                    + len(score.equivalent)
                if outcome in ('survived', 'not covered'):
                    log(f'  {outcome:11s} line {mutant.line}: {mutant.key}')
                elif done % 25 == 0:
                    log(f'  {done}/{len(found)} ({time.monotonic() - start:.0f} s)')

        with ThreadPoolExecutor(max_workers=len(copies)) as pool:
            for future in [pool.submit(one, m) for m in found]:
                future.result()
        score.survived.sort(key=lambda m: m.index)
        score.not_covered.sort(key=lambda m: m.index)
        log(f'{file}: {score.percent:.1f} % killed ({score.killed} killed, {score.timeout} timed out, '
            f'{len(score.survived)} survived, {len(score.not_covered)} not covered, '
            f'{len(score.equivalent)} equivalent) in {time.monotonic() - start:.0f} s')
        scores.append(score)
    return scores


def report(scores: list[Score], config: Config, all_keys: set[str]) -> dict[str, Any]:
    stale = sorted(k for k in config.equivalents if k not in all_keys)
    return {
        'threshold': config.threshold,
        'modules': [{
            'file': s.file, 'percent': round(s.percent, 1), 'mutants': s.total, 'killed': s.killed,
            'timeout': s.timeout, 'equivalent': len(s.equivalent),
            'survived': [{'line': m.line, 'key': m.key} for m in s.survived],
            'not_covered': [{'line': m.line, 'key': m.key} for m in s.not_covered],
        } for s in scores],
        'stale_equivalents': stale,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='mutate.py', description='Mutation testing of the suite\'s core modules '
                                     '(README "Mutation testing").')
    parser.add_argument('modules', nargs='*', help='modules of [tool.bf1943-mutation] modules (default: all)')
    parser.add_argument('--workers', type=int, help='test processes at once (default: the config\'s)')
    parser.add_argument('--list', action='store_true', help='list the mutants and run nothing')
    parser.add_argument('--report', help='the JSON report (default: work\\mutation\\report.json)')
    parser.add_argument('--work', help=argparse.SUPPRESS)                  # the work folder (tests)
    args = parser.parse_args(argv[1:])
    try:
        config = load_config(SUITE)
        files = resolve(args.modules, config.modules)
    except (ValueError, OSError) as e:
        print(f'error: {e}')
        return 2
    if args.workers is not None and args.workers < 1:
        print('error: --workers must be 1 or more')
        return 2
    sources = {}
    for file in files:
        with open(os.path.join(SUITE, file), encoding='utf-8') as f:
            sources[file] = f.read()
    keys = {m.key for file in files for m in mutants(sources[file], file)}
    if args.list:
        for file in files:
            for m in mutants(sources[file], file):
                mark = '  (equivalent)' if m.key in config.equivalents else ''
                print(f'{file}:{m.line}: {m.key}{mark}')
        print(f'{len(keys)} mutants')
        return 0
    work = args.work or os.path.join(SUITE, 'work', 'mutation')
    digest = hashlib.sha256(''.join(sources[f] for f in files).encode('utf-8')).hexdigest()[:12]
    print(f'mutation testing {len(files)} module(s) (sources {digest}), threshold {config.threshold:g} %')
    scores = run(SUITE, files, config, args.workers or config.workers, work)
    result = report(scores, config, keys)
    path = args.report or os.path.join(work, 'report.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(result, f, indent=1)
        f.write('\n')
    print('\nmodule                             score   killed  timeout  survived  not covered  equivalent')
    for s in scores:
        print(f'{s.file:34s} {s.percent:5.1f} %  {s.killed:6d}  {s.timeout:7d}  {len(s.survived):8d}  '
              f'{len(s.not_covered):11d}  {len(s.equivalent):10d}')
    if len(files) == len(config.modules) and result['stale_equivalents']:
        print('equivalents that match no mutant now (remove them from the list):')
        for key in result['stale_equivalents']:
            print(f'  {key}')
    print(f'report: {path}')
    low = [s.file for s in scores if s.percent < config.threshold]
    if low:
        print(f'below {config.threshold:g} %: {", ".join(low)}')
    return 1 if low else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
