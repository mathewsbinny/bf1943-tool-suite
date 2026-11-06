r"""navkit_le's install markers read in one place, install_state() (made-up game folder, no game data)

Each combination of the game's path data, its kept original (.opfor-orig) and the no-original marker (.opfor-none)
names one state; the kept original wins over a marker beside it. Project.dice_pathdata and Project.installed answer
from that state as they did from the files (the table below is their behaviour before 2026-11-05); install keeps the
original once, or writes the marker where the level had none, and never over an installed state; restore undoes each
installed state and leaves nothing behind, a stray marker beside a kept original included.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import os

import navkit_le as navkit

LV = 'sp_y'
S = navkit.InstallState


def touch(path, data=b''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def project(root):
    """A Project of a made-up game folder (only the paths install and restore use)."""
    pr = navkit.Project.__new__(navkit.Project)
    pr.root = str(root)
    pr.game = os.path.join(str(root), 'game')
    pr.work = os.path.join(str(root), 'work')
    return pr


def placed(pr, original, backup, none):
    """The level's path data, kept original and marker as given (each True: the file exists)."""
    path = pr.pathdata(LV)
    for exists, name, data in ((original, path, b'GAME'), (backup, path + navkit.BACKUP_SUFFIX, b'DICE'),
                               (none, path + navkit.NONE_SUFFIX, b'')):
        if exists:
            touch(name, data)
        elif os.path.exists(name):
            os.remove(name)
    return path


class Replace:
    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for name, value in self.values.items():
            self.saved[name] = getattr(navkit, name)
            setattr(navkit, name, value)

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(navkit, name, value)


def test_each_combination_one_state(tmp_path):
    pr = project(tmp_path)
    # (path data, kept original, marker) -> state, dice_pathdata (suffix of path data, or None), installed
    table = {
        (False, False, False): (S.NO_PATHDATA, None, False),
        (True, False, False): (S.ORIGINAL, '', False),
        (True, True, False): (S.OVER_ORIGINAL, navkit.BACKUP_SUFFIX, True),
        (False, True, False): (S.OVER_ORIGINAL, navkit.BACKUP_SUFFIX, True),
        (True, False, True): (S.WHERE_NONE, None, True),
        (False, False, True): (S.WHERE_NONE, None, True),
        (True, True, True): (S.OVER_ORIGINAL, navkit.BACKUP_SUFFIX, True),
        (False, True, True): (S.OVER_ORIGINAL, navkit.BACKUP_SUFFIX, True),
    }
    for files, (state, dice, installed) in table.items():
        path = placed(pr, *files)
        assert navkit.install_state(path) is state and pr.install_state(LV) is state, files
        assert pr.dice_pathdata(LV) == (None if dice is None else path + dice), files
        assert pr.installed(LV) is installed and state.installed is installed, files


def test_install_records_the_original_once(tmp_path):
    pr = project(tmp_path)
    ours = os.path.join(str(tmp_path), 'ours.hpd')
    touch(ours, b'NAVKIT')
    with Replace(game_running=lambda: False, read_back=lambda p: open(p, 'rb').read()):
        path = placed(pr, True, False, False)
        navkit.install(pr, LV, log=lambda m: None, src=ours)
        assert pr.install_state(LV) is S.OVER_ORIGINAL and open(path + navkit.BACKUP_SUFFIX, 'rb').read() == b'GAME'
        navkit.install(pr, LV, log=lambda m: None, src=ours)        # again: the kept original stays DICE's
        assert open(path + navkit.BACKUP_SUFFIX, 'rb').read() == b'GAME'
        path = placed(pr, False, False, False)
        navkit.install(pr, LV, log=lambda m: None, src=ours)
        assert pr.install_state(LV) is S.WHERE_NONE and open(path, 'rb').read() == b'NAVKIT'
        navkit.install(pr, LV, log=lambda m: None, src=ours)
        assert pr.install_state(LV) is S.WHERE_NONE and not os.path.exists(path + navkit.BACKUP_SUFFIX)


def test_restore_each_installed_state(tmp_path):
    pr = project(tmp_path)
    with Replace(game_running=lambda: False):
        for files, after, said in (((True, True, False), S.ORIGINAL, 'restored the original'),
                                   ((True, True, True), S.ORIGINAL, 'restored the original'),
                                   ((True, False, True), S.NO_PATHDATA, 'the level had no path data'),
                                   ((False, False, True), S.NO_PATHDATA, 'the level had no path data'),
                                   ((True, False, False), S.ORIGINAL, 'nothing to restore'),
                                   ((False, False, False), S.NO_PATHDATA, 'nothing to restore')):
            path = placed(pr, *files)
            lines = []
            navkit.restore(pr, LV, log=lines.append)
            assert pr.install_state(LV) is after and said in lines[-1], (files, lines)
            assert not os.path.exists(path + navkit.NONE_SUFFIX), files
            if after is S.ORIGINAL and files[1]:
                assert open(path, 'rb').read() == b'DICE', files
