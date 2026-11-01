r"""The coverage threshold only goes up (tests/test_repo.py coverage_ratchet), on made-up histories

A git repository made here with a pyproject.toml per commit: a threshold that rises, stays or is first set passes;
one lowered in a later commit, or in the working copy only, is reported with the commit that set the higher value,
unless the commit's message gives "Coverage-threshold-lowered: <reason>" (then later commits are held to its value);
a file that does not read is reported, not skipped.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import importlib.util
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def repo_rules():
    """tests/test_repo.py as a module of its own (its folder is not on sys.path)."""
    spec = importlib.util.spec_from_file_location('ratchet_test_repo', os.path.join(HERE, 'test_repo.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pyproject(value):
    head = '[project]\nname = "x"\n\n[tool.coverage.report]\nprecision = 1\n'
    return head + (f'fail_under = {value}\n' if value is not None else '')


def history(root, values, working=None, messages=None):
    """A repository whose pyproject.toml took the given fail_under values, one commit each (None: not set), with the
    given commit messages; the working copy then gets `working` when given."""
    run = lambda *a: subprocess.run(['git', '-C', str(root), *a], check=True, capture_output=True)  # noqa: E731
    run('init', '-q')
    run('config', 'user.name', 'test')
    run('config', 'user.email', 'test@example.invalid')
    path = os.path.join(root, 'pyproject.toml')
    for k, v in enumerate(values):
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(pyproject(v))
        run('add', 'pyproject.toml')
        run('commit', '-q', '-m', (messages or {}).get(k, f'commit {k}'))
    if working is not None:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(working if isinstance(working, str) else pyproject(working))


def ratchet(root):
    rules = repo_rules()
    rules.SUITE = str(root)
    return rules.coverage_ratchet()


def test_rising_threshold_passes(tmp_path):
    history(tmp_path, [None, 60, 60.0, 65.5])
    name, problems = ratchet(tmp_path)
    assert problems == [] and 'now 65.5' in name, (name, problems)


def test_no_threshold_yet_passes(tmp_path):
    history(tmp_path, [None])
    name, problems = ratchet(tmp_path)
    assert problems == [] and 'now 0' in name, (name, problems)


def test_lowered_in_a_commit_fails(tmp_path):
    history(tmp_path, [60, 70, 69])
    _name, problems = ratchet(tmp_path)
    assert len(problems) == 1 and 'fail_under 69 is below 70 (set in ' in problems[0], problems


def test_removed_threshold_fails(tmp_path):
    history(tmp_path, [70, None])
    _name, problems = ratchet(tmp_path)
    assert len(problems) == 1 and 'fail_under 0 is below 70' in problems[0], 'removing it lowers it to 0'


def test_lowered_in_the_working_copy_fails(tmp_path):
    history(tmp_path, [70], working=65)
    _name, problems = ratchet(tmp_path)
    assert problems == ['working copy: fail_under 65 is below 70 (set in ' + problems[0].split('(set in ')[1]], problems
    history_ok = tmp_path / 'ok'
    history_ok.mkdir()
    history(history_ok, [70], working=71)
    assert ratchet(history_ok)[1] == [], 'raised in the working copy: fine'


def test_unreadable_pyproject_is_reported(tmp_path):
    history(tmp_path, [70], working='[tool.coverage.report\nfail_under = 80\n')
    _name, problems = ratchet(tmp_path)
    assert len(problems) == 1 and problems[0].startswith('working copy: pyproject.toml does not read'), problems


def test_lowering_with_a_reason_passes(tmp_path):
    lowered = 'remove the old viewer\n\nCoverage-threshold-lowered: its tests went with it\n'
    history(tmp_path, [70, 66, 67], messages={1: lowered})
    name, problems = ratchet(tmp_path)
    assert problems == [] and 'now 67' in name, (name, problems)
    again = tmp_path / 'again'
    again.mkdir()
    history(again, [70, 66, 65], messages={1: lowered})
    problems = ratchet(again)[1]
    assert len(problems) == 1 and 'fail_under 65 is below 66' in problems[0], 'held to the lowered value from then on'
    plain = tmp_path / 'plain'
    plain.mkdir()
    history(plain, [70, 66], messages={1: 'Coverage-threshold-lowered:\n'})
    assert len(ratchet(plain)[1]) == 1, 'a marker without a reason does not count'
