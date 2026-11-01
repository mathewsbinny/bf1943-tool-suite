r"""The repository's own rules, checked before anything is pushed (no game data needed):

- no game file types and no large files anywhere in the history (EA / DICE content is never published);
- no co-author or generated-by lines in commit messages (the author is the only author);
- no build output or caches tracked (.exe, .pyc, __pycache__);
- a README in every tool folder, a LICENSE at the root;
- every Python file compiles;
- sys.path is changed only by the suite's bootstrap (core/suitepath.py): no other edit in any Python file, and the
  bootstrap only where a script runs (under `if __name__ == '__main__':`); the folders it puts on sys.path share no
  module name, the two NavKit variants included (their shared modules are one copy in bf1943-auto-navkit-common
  since 2026-10-31), so the order a script names them in changes nothing it imports;
- the lint (ruff) leaves out exactly the VMX fingerprint's Python inputs, which are never edited;
- the coverage threshold (pyproject.toml [tool.coverage.report] fail_under) never goes down: in no commit of the
  history (unless its message gives a "Coverage-threshold-lowered: <reason>" line) and not in the working copy.

  python tests\test_repo.py
"""
import ast
import os
import py_compile
import re
import subprocess
import sys
import tempfile

SUITE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
GAME_TYPES = re.compile(r'\.(dbx|res|fbrb|hpd|sb|raw|bik|fsb|fev|dds|tga|hkx)$', re.I)
MAX_BLOB = 1_000_000
LARGE_ALLOWED = {'bf1943-dbx-tools/tests/fingerprints_795745.txt'}       # SHA-1 hashes only
LOWERED = re.compile(r'^Coverage-threshold-lowered: \S', re.M)     # a commit allowed to lower fail_under (its reason)
ATTRIBUTION = re.compile(r'co-authored-by|generated with \[?claude|noreply@anthropic', re.I)
TRACKED_NEVER = re.compile(r'(\.exe|\.pyc|\.dll|\.pdb)$|(^|/)__pycache__/', re.I)
BOOTSTRAP = ("sys.path.insert(0, str(next((p for p in pathlib.Path(__file__).resolve().parents "
             "if (p / 'core' / 'suitepath.py').is_file())) / 'core'))")
# files allowed their own sys.path edits: the bootstrap's module, and the VMX language builder, an input of the VMX
# fingerprint (bf1943-xex-tools/vmx_inputs.py INPUTS), which must stay byte for byte as it is
PATH_EDITS_ALLOWED = {'core/suitepath.py', 'bf1943-xex-tools/build_vmx_language.py'}


def git(*args):
    return subprocess.run(['git', '-C', SUITE, *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', check=True).stdout


def is_path(node):
    """sys.path, or sys.path under another name for sys (`import sys as _sys`)."""
    return (isinstance(node, ast.Attribute) and node.attr == 'path' and isinstance(node.value, ast.Name)
            and node.value.id in ('sys', '_sys'))


def path_edits(tree):
    """(line, statement) of each change to sys.path that is not the bootstrap where a script runs."""
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'":
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and ast.unparse(stmt) == BOOTSTRAP:
                    guarded.add(id(stmt.value))
    found = []
    for node in ast.walk(tree):
        changed = None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and is_path(node.func.value)
                and node.func.attr in ('insert', 'append', 'extend', 'remove', 'pop', 'clear')):
            changed = node
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            if any(is_path(t) or (isinstance(t, ast.Subscript) and is_path(t.value)) for t in targets):
                changed = node
        if changed is not None and id(changed) not in guarded:
            found.append((changed.lineno, ast.unparse(changed)[:100]))
    return sorted(found)


def shared_names():
    """Module names (a .py / .pyw file, or a package folder) found in more than one of the bootstrap's folders: the
    shared ones and both NavKit variants' (until 2026-10-31 the variants were let share 14 names)."""
    import suitepath
    seen, clashes = {}, []
    for rel in suitepath.SHARED + suitepath.NAVKIT_BE + suitepath.NAVKIT_LE:
        folder = suitepath.folder(rel)
        for n in sorted(os.listdir(folder)):
            name, ext = os.path.splitext(n)
            if ext not in ('.py', '.pyw') and not os.path.isfile(os.path.join(folder, n, '__init__.py')):
                continue
            if name in seen:
                clashes.append(f'{name}: {seen[name]} and {rel}')
            seen.setdefault(name, rel)
    return clashes


def fail_under(text):
    """pyproject.toml's [tool.coverage.report] fail_under (0 when it has none), from the file's text."""
    import tomllib
    return float(tomllib.loads(text).get('tool', {}).get('coverage', {}).get('report', {}).get('fail_under', 0))


def coverage_ratchet():
    """(check, problems): the coverage threshold only goes up. Each commit that changed pyproject.toml (oldest first,
    the whole history: CI checks it out whole), then the working copy: a fail_under below the one before it is a
    problem, so a lowered threshold fails whether it is committed or not (README "Coverage"). The one exception: a
    commit whose message has a line "Coverage-threshold-lowered: <reason>" may lower it (a well-tested tool removed);
    the working copy never may."""
    log = git('log', '--reverse', '--format=%H%x00%B%x01', '--', 'pyproject.toml').split('\x01')
    commits = [entry.strip('\n').split('\x00', 1) for entry in log if '\x00' in entry]
    versions = [(sha[:10], git('show', f'{sha}:pyproject.toml'), bool(LOWERED.search(message)))
                for sha, message in commits]
    with open(os.path.join(SUITE, 'pyproject.toml'), encoding='utf-8', newline='') as f:
        working = f.read()
    if not versions or working != versions[-1][1]:      # an unchanged working copy is its last commit (reported once)
        versions.append(('working copy', working, False))
    problems, best, since = [], 0.0, None
    for name, text, lowered in versions:
        try:
            value = fail_under(text)
        except (ValueError, TypeError) as e:            # tomllib.TOMLDecodeError is a ValueError
            problems.append(f'{name}: pyproject.toml does not read ({e})')
            continue
        if value < best and not lowered:
            problems.append(f'{name}: fail_under {value:g} is below {best:g} (set in {since}); raise coverage instead')
        elif value > best or lowered:
            best, since = value, name
    return (f'the coverage threshold only goes up (pyproject [tool.coverage.report] fail_under, now {best:g})',
            problems)


def main(argv):
    results = []

    blobs = git('rev-list', '--objects', '--all').splitlines()
    sizes = subprocess.run(['git', '-C', SUITE, 'cat-file', '--batch-check=%(objecttype) %(objectsize) %(rest)'],
                           input='\n'.join(l.split(' ')[0] for l in blobs), capture_output=True, text=True).stdout
    paths = {}
    for line in blobs:
        sha, _, path = line.partition(' ')
        if path:
            paths[sha] = path
    game_files, large = set(), set()
    for line, sha in zip(sizes.splitlines(), (l.split(' ')[0] for l in blobs)):
        kind, size, _ = (line.split(' ', 2) + [''])[:3]
        path = paths.get(sha, '')
        if kind != 'blob' or not path:
            continue
        if GAME_TYPES.search(path):
            game_files.add(path)
        if int(size) > MAX_BLOB and path not in LARGE_ALLOWED:
            large.add(f'{path} ({int(size):,} bytes)')
    results.append(('no game file types in the history', sorted(game_files)))
    results.append((f'no files over {MAX_BLOB:,} bytes in the history (allowed: {", ".join(sorted(LARGE_ALLOWED))})', sorted(large)))

    messages = git('log', '--all', '--format=%H%x00%B%x01').split('\x01')
    bad = [m.split('\x00')[0][:10] for m in messages if '\x00' in m and ATTRIBUTION.search(m.split('\x00', 1)[1])]
    results.append(('no co-author / generated-by lines in commit messages', bad))
    authors = sorted(set(git('log', '--all', '--format=%an <%ae>').splitlines()))
    results.append((f'one author ({authors[0] if authors else "none"})', authors[1:]))

    tracked = git('ls-files').splitlines()
    results.append(('no build output or caches tracked', [t for t in tracked if TRACKED_NEVER.search(t)]))

    folders = [d for d in sorted(os.listdir(SUITE)) if os.path.isdir(os.path.join(SUITE, d)) and (d.startswith('bf1943-') or d == 'core')]
    results.append(('a README.md in every tool folder', [d for d in folders if not os.path.isfile(os.path.join(SUITE, d, 'README.md'))]))
    results.append(('LICENSE at the root', [] if os.path.isfile(os.path.join(SUITE, 'LICENSE')) else ['LICENSE missing']))

    broken = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in tracked:
            if t.endswith(('.py', '.pyw')) and os.path.isfile(os.path.join(SUITE, t)):    # not one deleted, uncommitted
                try:
                    py_compile.compile(os.path.join(SUITE, t), cfile=os.path.join(tmp, 'x.pyc'), doraise=True)
                except py_compile.PyCompileError as e:
                    broken.append(f'{t}: {str(e).strip().splitlines()[-1]}')
    results.append((f'every Python file compiles ({sum(t.endswith((".py", ".pyw")) for t in tracked)} files)', broken))

    edits = []
    for t in tracked:
        if t.endswith(('.py', '.pyw')) and t not in PATH_EDITS_ALLOWED and os.path.isfile(os.path.join(SUITE, t)):
            try:
                tree = ast.parse(open(os.path.join(SUITE, t), encoding='utf-8').read())
            except SyntaxError:
                continue                                    # reported above
            edits += [f'{t}:{line}: {what}' for line, what in path_edits(tree)]
    results.append(('sys.path changed only by the bootstrap (core/suitepath.py), and only where a script runs', edits))
    results.append(("the bootstrap's folders share no module name (the two NavKit variants included)", shared_names()))
    import suitepath
    import tomllib
    with open(os.path.join(SUITE, 'pyproject.toml'), 'rb') as f:
        dev_dirs = tomllib.load(f)['tool']['hatch']['build']['targets']['wheel']['dev-mode-dirs']
    results.append(('an editable install puts the shared folders on sys.path (pyproject dev-mode-dirs = suitepath.SHARED)',
                    [] if dev_dirs == suitepath.SHARED else [f'pyproject: {dev_dirs}', f'suitepath: {suitepath.SHARED}']))
    import vmx_inputs
    with open(os.path.join(SUITE, 'pyproject.toml'), 'rb') as f:
        lint_skips = set(tomllib.load(f)['tool']['ruff']['extend-exclude'])
    vmx_py = {i for i in vmx_inputs.INPUTS if i.endswith(('.py', '.pyw'))}
    results.append(("the lint skips the VMX fingerprint's Python inputs, and only those (pyproject [tool.ruff] "
                    'extend-exclude = vmx_inputs.INPUTS .py files, besides work and output)',
                    sorted(vmx_py ^ (lint_skips - {'work', 'output'}))))

    results.append(coverage_ratchet())

    failed = 0
    for name, problems in results:
        failed += bool(problems)
        print(f'{"FAIL" if problems else "ok  "} {name}')
        for p in problems[:15]:
            print('       ' + p)
    print('all passed' if not failed else f'{failed} check(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
    sys.exit(main(sys.argv))
