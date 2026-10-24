"""Your BF1943 copy as the suite's tools see it: where it is, its levels, its bundles' files, and the suite's work
folder for what the tools make from it.

    game = find_game()                     # the dist\\xenon folder bf1943-xenia-kit extracts (BF1943_GAME overrides)
    levels(game)                           # ['wake_island_s', 'guadal_canal', 'iwo_jima_s', 'coral_sea', 'wake_island_tutorial']
    g = Game(game, 'wake_island_s')        # every file of the level's bundles and the shared level bundle
    g.data('levels/wake_island_s.dbx')     # bytes, or None
    g.partition('levels/wake_island_s')    # a parsed binary DBX partition (dbx.parse), or None
    level_work(lv)                         # <suite>\\work\\levels\\<lv> (BF1943_WORK overrides <suite>\\work)

The game files are only read: the copy stays as the Xenia kit extracted it. Bundles are BF1943's FbRB v2
(bf1943-fbrb-tool/fbrb1943.py); three of the 85 use another layout and are left out.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
DEFAULT_GAME = os.path.join(SUITE, 'bf1943-xenia-kit', 'output', 'title', 'dist', 'xenon')
COMMON = 'levels-common-00.fbrb'          # the bundle every level shares (blueprints, collision)
MP_LEVELS = ['wake_island_s', 'guadal_canal', 'iwo_jima_s', 'coral_sea']


def find_game(given=None):
    """The BF1943 game folder (with levels\\): given, else BF1943_GAME, else bf1943-xenia-kit's output. A folder
    given that is not a game folder is an error, not a reason to look elsewhere."""
    if given:
        if os.path.isdir(os.path.join(given, 'levels')):
            return os.path.abspath(given)
        raise FileNotFoundError(f'{given} is not a BF1943 game folder (it has no levels\\ folder): pass the dist\\xenon '
                                f'folder bf1943-xenia-kit extracts')
    for p in (os.environ.get('BF1943_GAME'), DEFAULT_GAME):
        if p and os.path.isdir(os.path.join(p, 'levels')):
            return os.path.abspath(p)
    raise FileNotFoundError(
        f'no BF1943 game folder (looked at {given or "-"}, BF1943_GAME, {DEFAULT_GAME}): build the Xenia setup from your '
        f'own package first (bf1943-xenia-kit: python xeniakit.py build <package> <xenia folder>), or set BF1943_GAME '
        f'to a dist\\xenon folder')


def work_root():
    """Where the tools write what they make from the game: BF1943_WORK, else <suite>\\work (not committed)."""
    return os.path.abspath(os.environ.get('BF1943_WORK') or os.path.join(SUITE, 'work'))


def level_work(lv):
    return os.path.join(work_root(), 'levels', lv)


def levels(game=None):
    """Level folder names that hold bundles (multiplayer maps first)."""
    game = find_game(game)
    folder = os.path.join(game, 'levels')
    found = sorted(n for n in os.listdir(folder)
                   if os.path.isdir(os.path.join(folder, n)) and any(f.endswith('.fbrb') for f in os.listdir(os.path.join(folder, n))))
    return [lv for lv in MP_LEVELS if lv in found] + [lv for lv in found if lv not in MP_LEVELS]


class Game:
    """Every entry of a level's bundles and the shared level bundle, by lower-case name (the level's own first)."""

    def __init__(self, game, level):
        sys.path.insert(0, os.path.join(SUITE, 'bf1943-fbrb-tool'))
        from fbrb1943 import Bundle
        from . import dbx
        self._dbx = dbx
        self.game, self.level = find_game(game), level
        self.entries = {}
        folder = os.path.join(self.game, 'levels', level)
        if not os.path.isdir(folder):
            raise FileNotFoundError(f'{level}: no such level in {self.game}\\levels (levels: {", ".join(levels(self.game))})')
        paths = [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith('.fbrb')]
        paths.append(os.path.join(self.game, 'levels', COMMON))
        for p in paths:
            if not os.path.isfile(p):
                continue
            try:
                b = Bundle(open(p, 'rb').read())
            except ValueError:                    # one of the three bundles in another layout
                continue
            for e in b.entries:
                self.entries.setdefault(e['name'].lower(), (b, e['name']))

    def data(self, name):
        hit = self.entries.get(name.lower())
        return hit[0].data(hit[1]) if hit else None

    def partition(self, name):
        d = self.data(name + '.dbx')
        return self._dbx.parse(d) if d else None

    def names(self, prefix=''):
        p = prefix.lower()
        return sorted(n for n in self.entries if n.startswith(p))
