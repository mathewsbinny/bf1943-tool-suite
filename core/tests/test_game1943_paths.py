r"""Game loader (game1943): finding the game copy, its levels and the work folder (made-up game folders)

game1943 on made-up folders (no game data): find_game takes a folder given (refusing one without levels\, never
looking elsewhere), else BF1943_GAME, else the Xenia kit's output, and says where it looked; levels lists the level
folders that hold bundles, multiplayer maps first in their own order; work_root and level_work follow BF1943_WORK;
Game refuses a level that is not there (naming the levels that are), opens a level without the shared bundle, and
looks names up ignoring case.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import os

import fuzzcheck

from bfbc2core import game1943

BUNDLES = fuzzcheck.fixtures('core/tests/test_game1943_bundles.py')


@contextlib.contextmanager
def environment(**values):
    """Environment variables set (None: unset) and the kit's default folder moved, restored afterwards."""
    saved = {k: os.environ.get(k) for k in values}
    default = game1943.DEFAULT_GAME
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        game1943.DEFAULT_GAME = default
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def game(root, levels):
    for lv, files in levels.items():
        os.makedirs(os.path.join(root, 'levels', lv))
        for f in files:
            open(os.path.join(root, 'levels', lv, f), 'wb').write(BUNDLES.bundle({f'{lv}/{f}.res': b'x'}))
    return str(root)


def test_find_game(tmp_path):
    g = game(tmp_path / 'g', {'lv': ['a-00.fbrb']})
    other = game(tmp_path / 'other', {'lv': ['a-00.fbrb']})
    with environment(BF1943_GAME=other):
        game1943.DEFAULT_GAME = str(tmp_path / 'none')
        assert game1943.find_game(g) == os.path.abspath(g), 'the folder given first'
        assert game1943.find_game() == os.path.abspath(other), 'then BF1943_GAME'
        try:
            game1943.find_game(str(tmp_path))
        except FileNotFoundError as e:
            assert 'is not a BF1943 game folder' in str(e), e
        else:
            raise AssertionError('a folder without levels was taken, or another one looked for')
    with environment(BF1943_GAME=str(tmp_path / 'missing')):
        game1943.DEFAULT_GAME = g
        assert game1943.find_game() == os.path.abspath(g), 'then the kit\'s output'
        game1943.DEFAULT_GAME = str(tmp_path / 'none')
        try:
            game1943.find_game()
        except FileNotFoundError as e:
            assert 'looked at -, BF1943_GAME' in str(e) and str(tmp_path / 'none') in str(e), e
        else:
            raise AssertionError('no game folder found, and no error')
    with environment(BF1943_GAME=''):
        game1943.DEFAULT_GAME = g
        assert game1943.find_game() == os.path.abspath(g), 'an empty BF1943_GAME is not set'


def test_levels_order(tmp_path):
    g = game(tmp_path, {'zz_test': ['a.fbrb'], 'guadal_canal': ['b.fbrb'], 'wake_island_s': ['c.fbrb'],
                        'aa_menu': ['d.fbrb'], 'no_bundles': []})
    open(os.path.join(g, 'levels', 'no_bundles', 'readme.txt'), 'w').close()
    open(os.path.join(g, 'levels', 'loose.fbrb'), 'wb').close()
    assert game1943.levels(g) == ['wake_island_s', 'guadal_canal', 'aa_menu', 'zz_test']


def test_work_folder(tmp_path):
    with environment(BF1943_WORK=str(tmp_path / 'w')):
        assert game1943.work_root() == str(tmp_path / 'w')
        assert game1943.level_work('lv') == os.path.join(str(tmp_path / 'w'), 'levels', 'lv')
    with environment(BF1943_WORK=None):
        assert game1943.work_root() == os.path.join(game1943.SUITE, 'work')


def test_game_levels_and_names(tmp_path):
    g = game(tmp_path, {'lv': ['level-00.fbrb', 'level-01.fbrb', 'notes.txt'], 'other': ['x.fbrb']})
    try:
        game1943.Game(g, 'missing')
    except FileNotFoundError as e:
        assert 'missing: no such level' in str(e) and '(levels: lv, other)' in str(e), e
    else:
        raise AssertionError('a missing level opened')
    lv = game1943.Game(g, 'lv')                                  # no shared bundle (levels-common-00.fbrb)
    assert lv.names() == ['lv/level-00.fbrb.res', 'lv/level-01.fbrb.res'], lv.names()
    assert lv.names('LV/LEVEL-01') == ['lv/level-01.fbrb.res'] and lv.names('x') == []
    assert lv.data('LV/Level-00.fbrb.res') == b'x' and lv.data('nothing') is None
    assert lv.partition('nothing') is None


def test_find_game_without_the_variable(tmp_path):
    g = game(tmp_path / 'g', {'lv': ['a-00.fbrb']})
    with environment(BF1943_GAME=None):
        game1943.DEFAULT_GAME = g
        assert game1943.find_game() == os.path.abspath(g), 'BF1943_GAME unset: the kit\'s output'
