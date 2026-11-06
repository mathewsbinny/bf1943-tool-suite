r"""NavKit (big-endian): the navigation audit's command line - argument errors and exit codes of both subcommands

navaudit1943.py masks / audit (2026-11-06: one parser, the level defined once for both, and one way to open a level):
no subcommand, an unknown one, a missing level, an unknown --stage, an extra argument and a --json without its value
are usage errors (exit 2, argparse's message naming the subcommand); an unknown variant exits 1 naming the known
ones; a level not prepared, and one prepared but not built, exit 1 with the refusal in words (the subcommands share
navaudit1943.open_level, so both say the same); --help exits 0 and lists each subcommand's options. Run in a scratch
BF1943_WORK.
"""
import json
import os
import subprocess
import sys

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'navaudit1943.py')


def run(tmp_path, *args):
    env = dict(os.environ, BF1943_WORK=str(tmp_path), PYTHONDONTWRITEBYTECODE='1')
    r = subprocess.run([sys.executable, '-B', TOOL, *args], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    return r.returncode, r.stdout, r.stderr


def test_usage_errors(tmp_path):
    for args, words in (([], 'the following arguments are required: cmd'),
                        (['bogus'], "argument cmd: invalid choice: 'bogus'"),
                        (['masks'], 'navaudit1943.py masks: error: the following arguments are required: level'),
                        (['audit'], 'navaudit1943.py audit: error: the following arguments are required: level'),
                        (['masks', 'lv', '--stage', 'bogus'], "argument --stage: invalid choice: 'bogus' (choose from"),
                        (['masks', 'lv', 'extra'], 'unrecognized arguments: extra'),
                        (['audit', 'lv', '--bogus'], 'unrecognized arguments: --bogus'),
                        (['audit', 'lv', '--json'], 'navaudit1943.py audit: error: argument --json: expected one argument')):
        code, out, err = run(tmp_path, *args)
        assert code == 2 and words in err, (args, code, err)


def test_unknown_variant(tmp_path):
    code, _out, err = run(tmp_path, 'masks', 'lv', '--variants', 'base,bogus')
    assert code == 1 and "unknown variant(s) ['bogus']; known: base, headroom, lips, water, ladders" in err, err


def test_levels_refused(tmp_path):
    for command in ('masks', 'audit'):
        code, _out, err = run(tmp_path, command, 'no_such_level')
        assert code == 1 and 'no_such_level: the level is not prepared' in err, (command, err)
    nav = os.path.join(str(tmp_path), 'levels', 'made_up', 'navkit')
    os.makedirs(nav)
    with open(os.path.join(nav, 'navkit.json'), 'w', encoding='utf-8', newline='') as f:
        json.dump({'level': 'made_up', 'preparation': 'p'}, f)
    for args in (['masks', 'made_up'], ['masks', 'made_up', '--variants', ','], ['audit', 'made_up']):
        code, _out, err = run(tmp_path, *args)
        assert code == 1 and 'made_up: the build on disk is not of the current preparation' in err, (args, err)
    assert not os.path.isdir(os.path.join(str(tmp_path), 'levels', 'made_up', 'audit')), 'a refused level is not written'


def test_help(tmp_path):
    code, out, _err = run(tmp_path, '--help')
    assert code == 0 and 'navaudit1943.py masks <level>' in out
    code, out, _err = run(tmp_path, 'masks', '--help')
    assert code == 0 and '--variants' in out and '--stage {normal,detailed,both}' in out and 'level' in out
    code, out, _err = run(tmp_path, 'audit', '--help')
    assert code == 0 and '--json' in out and '--variants' not in out
