r"""Core: the level picker's list (pick.levels) on a made-up game folder

The levels of a made-up game copy (BF1943_GAME): multiplayer maps first in their own order, then the others sorted;
folders without a bundle are left out; a level whose navigation was built (BF1943_WORK\levels\<lv>\navkit\<lv>.hpd)
says so. The tkinter picker itself is not tested.
"""
import os

from bfbc2core import pick


def test_levels_of_a_made_up_copy(tmp_path):
    game, work = tmp_path / 'game', tmp_path / 'work'
    for lv in ('zz_test', 'coral_sea', 'aa_test', 'wake_island_s'):
        (game / 'levels' / lv).mkdir(parents=True)
        (game / 'levels' / lv / 'level-00.fbrb').write_bytes(b'')
    (game / 'levels' / 'no_bundles').mkdir()
    (game / 'levels' / 'no_bundles' / 'readme.txt').write_bytes(b'')
    (game / 'levels' / 'levels-common-00.fbrb').write_bytes(b'')        # a file, not a level
    (work / 'levels' / 'coral_sea' / 'navkit').mkdir(parents=True)
    (work / 'levels' / 'coral_sea' / 'navkit' / 'coral_sea.hpd').write_bytes(b'hpd')
    (work / 'levels' / 'aa_test' / 'navkit').mkdir(parents=True)        # a folder, no build
    saved = {k: os.environ.get(k) for k in ('BF1943_GAME', 'BF1943_WORK')}
    os.environ['BF1943_GAME'], os.environ['BF1943_WORK'] = str(game), str(work)
    try:
        got = pick.levels()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert got == [('wake_island_s', 'multiplayer map'),
                   ('coral_sea', 'multiplayer map, navigation built'),
                   ('aa_test', 'other level'),
                   ('zz_test', 'other level')], got
