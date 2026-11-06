r"""Game folder lookup and write guard: every spelling of a made-up game folder (no game data)

game1943.find_game takes BF1943_GAME however it is spelled - a trailing separator, upper case, forward slashes, a
trailing \., a .., \\?\, relative to the current folder, an 8.3 short name, a junction, \\localhost\<drive>$ - and
returns it as os.path.abspath spells it; a spelling that is not a folder to Windows (a space before \levels, quotes)
is passed over for the kit's output. Through each spelling of the game folder the fbrb tool's write guard
(fbrb1943.inside_game and checked) refuses a path in it, and allows one outside. DbxEdit's SafeWrite follows the same
rule (bf1943-dbx-tools/tests/csharp_regressions.py check_game_lookup compares the two on the same spellings).

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import contextlib
import ctypes
import os
import subprocess

import fbrb1943
from bfbc2core import game1943


@contextlib.contextmanager
def environment(game_variable, default, cwd=None):
    """BF1943_GAME set (None: unset), the kit's default folder moved and the current folder changed, restored after."""
    saved, saved_default, saved_cwd = os.environ.get('BF1943_GAME'), game1943.DEFAULT_GAME, os.getcwd()
    try:
        if game_variable is None:
            os.environ.pop('BF1943_GAME', None)
        else:
            os.environ['BF1943_GAME'] = game_variable
        game1943.DEFAULT_GAME = default
        if cwd:
            os.chdir(cwd)
        yield
    finally:
        os.chdir(saved_cwd)
        game1943.DEFAULT_GAME = saved_default
        if saved is None:
            os.environ.pop('BF1943_GAME', None)
        else:
            os.environ['BF1943_GAME'] = saved


def made_up(tmp_path):
    """(root, game folder, kit's stand-in folder, outside folder, {what: spelling of the game folder})"""
    root = str(tmp_path)
    game = os.path.join(root, 'Game Folder')
    kit = os.path.join(root, 'kit')
    outside = os.path.join(root, 'out')
    for folder in (os.path.join(game, 'levels', 'sub'), os.path.join(kit, 'levels'), outside):
        os.makedirs(folder)
    drive, rest = os.path.splitdrive(game)
    spellings = {'as made': game, 'a trailing separator': game + '\\', 'two trailing separators': game + '\\\\',
                 'upper case': game.upper(), 'forward slashes': game.replace('\\', '/'), 'a trailing \\.': game + '\\.',
                 'a ..': os.path.join(game, 'levels', '..'), '\\\\?\\': '\\\\?\\' + game, 'relative': 'Game Folder'}
    buf = ctypes.create_unicode_buffer(1024)
    if 0 < ctypes.windll.kernel32.GetShortPathNameW(game, buf, 1024) < 1024 and buf.value.lower() != game.lower():
        spellings['8.3 short name'] = buf.value
    junction = os.path.join(root, 'j')
    if subprocess.run(['cmd', '/c', 'mklink', '/J', junction, game], capture_output=True).returncode == 0:
        spellings['a junction'] = junction
    unc = '\\\\localhost\\' + drive[0] + '$' + rest
    if os.path.isdir(unc):
        spellings['\\\\localhost\\' + drive[0] + '$'] = unc
    return root, game, kit, outside, spellings


def test_find_game_spellings(tmp_path):
    root, game, kit, _, spellings = made_up(tmp_path)
    for what, spelling in spellings.items():
        with environment(spelling, kit, cwd=root):
            found = game1943.find_game()
            assert found == os.path.abspath(spelling), (what, found)
            assert os.path.samefile(found, game), (what, found)
            assert found[-1] not in '\\/' and '\\.' not in found[2:], (what, found)
    for what, spelling in (('a trailing space', game + ' '), ('quotes', '"' + game + '"')):
        with environment(spelling, kit, cwd=root):
            assert game1943.find_game() == kit, f'BF1943_GAME with {what} is not a folder to Windows: the kit\'s output'


def test_guard_refuses_every_spelling(tmp_path):
    root, game, kit, outside, spellings = made_up(tmp_path)
    targets = {what: os.path.join(spelling, 'levels', 'o.bin') for what, spelling in spellings.items()}
    for game_what, game_spelling in spellings.items():
        with environment(game_spelling, kit, cwd=root):
            for what, target in targets.items():
                assert fbrb1943.inside_game(target), f'BF1943_GAME {game_what}: a path in it spelled {what} is refused'
                try:
                    fbrb1943.checked(root, target, fbrb1943.game_folder(), 'o.bin')
                except ValueError as e:
                    assert 'game folder' in str(e), e
                else:
                    raise AssertionError(f'BF1943_GAME {game_what}: checked() lets a path spelled {what} through')
            for what, path in (('as made', os.path.join(outside, 'o.bin')), ('\\\\?\\', '\\\\?\\' + os.path.join(outside, 'o.bin')),
                               ('relative', os.path.join('out', 'o.bin'))):
                assert not fbrb1943.inside_game(path), f'BF1943_GAME {game_what}: a path outside it ({what}) is allowed'
                assert fbrb1943.checked(root, path, fbrb1943.game_folder(), 'o.bin') == path
    junction = spellings.get('a junction')
    if junction:
        os.rmdir(junction)          # the junction only, not its target
