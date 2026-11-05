r"""NavKit (both variants): hkpack's commands and value summaries on a made-up Havok packfile

hkpack's command line, in-process, on the made-up big-endian Havok 4.6.1 packfile of test_fuzz_navkit.py (no game
data): classes gives the packfile's place, version, byte order, sections and class descriptions, then each object
class with its count; dump lists the objects of a class (none past the count asked for); an unknown command or too
few arguments print the usage (exit code 1); short() summarises decoded values (long lists cut to three items,
pointers as their target's class or address, floats in brief).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import io

import fuzzcheck
import hkpack

FUZZ = fuzzcheck.fixtures('bf1943-auto-navkit-common/tests/test_fuzz_navkit.py')


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hkpack.main(['hkpack.py', *argv])
    return code, out.getvalue()


def made_up(tmp_path):
    path = tmp_path / 'level_Havok.res'
    path.write_bytes(FUZZ.packfile())
    return str(path)


def test_classes(tmp_path):
    code, out = run('classes', made_up(tmp_path))
    lines = out.splitlines()
    assert code == 0 and lines[0] == ('Havok packfile at 0x20, version 5, Havok-4.6.1-r1, big-endian, 3 sections, '
                                      '0 class descriptions'), lines[0]
    assert sorted(line.split() for line in lines[1:]) == [['1', FUZZ.CONVEX], ['1', FUZZ.STORAGE]], lines


def test_dump(tmp_path):
    path = made_up(tmp_path)
    code, out = run('dump', path, FUZZ.STORAGE)
    assert code == 0 and out.strip() == '@0x140 {}', out
    assert run('dump', path, FUZZ.STORAGE, '0') == (0, '')
    assert run('dump', path, 'hkNoSuchClass') == (0, '')


def test_usage(tmp_path):
    path = made_up(tmp_path)
    for argv in (['classes'], ['list', path]):
        code, out = run(*argv)
        assert code == 1 and out.strip() == (hkpack.__doc__ or '').strip(), argv


def test_short():
    assert hkpack.short([1, 2, 3, 4, 5, 6]) == '[1, 2, 3, 4, 5, 6]'
    assert hkpack.short(list(range(7))) == '[7 items: 0, 1, 2, ...]'
    assert hkpack.short(('ptr', 0x40, 'hkClass')) == '->hkClass' and hkpack.short(('ptr', 0x40, None)) == '->0x40'
    assert hkpack.short((1.0, 2, 0.12345)) == '(1.000, 2, 0.123)' and hkpack.short(()) == '()'
    assert hkpack.short(3.14159) == '3.142' and hkpack.short('text') == 'text' and hkpack.short(7) == '7'
    assert hkpack.short({'a': [0.5], 'b': {'c': ('ptr', 16, None)}}) == '{a: [0.5], b: {c: ->0x10}}'
