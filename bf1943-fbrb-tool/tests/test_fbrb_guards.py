r"""fbrb1943.py's write guards on a made-up bundle (no game data): a made-up game folder (BF1943_GAME) is never
written, by replace or by extract, however the path is cased; replace never writes over its input, however that is
cased; an output on another drive than the game folder is allowed, not an error.

  python -B bf1943-fbrb-tool\tests\test_fbrb_guards.py
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(HERE)
TOOL = os.path.join(HERE, 'fbrb1943.py')
sys.path[:0] = [HERE, os.path.join(SUITE, 'core')]
import fbrb1943  # noqa: E402

PY = sys.executable
HEADER = b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x0b'          # mtime 0, OS byte 0x0b, as the game's bundles
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def made_up():
    """A stored (not gzip) bundle of two entries, a/one.bin and two.bin, in the layout fbrb1943.py reads."""
    entries = [('a/one.bin', b'first entry'), ('two.bin', b'second')]
    strings, offs = b'', {}
    for s in [n for n, _ in entries] + ['RawFileAsset']:
        offs[s] = len(strings)
        strings += s.encode('ascii') + b'\0'
    d = struct.pack('>II', 2, len(strings)) + strings + struct.pack('>I', len(entries))
    pos = 0
    for name, data in entries:
        d += struct.pack('>6I', offs[name], 0, pos, len(data), len(data), offs['RawFileAsset'])
        pos += len(data)
    d += b'\0' + struct.pack('>I', pos)
    directory = fbrb1943.gzip_member(d, HEADER)
    return b'FbRB' + struct.pack('>I', len(directory)) + directory + b''.join(x for _, x in entries)


def run(env, *args):
    r = subprocess.run([PY, '-B', TOOL, *args], capture_output=True, text=True, errors='replace', env=env)
    return r.returncode, r.stdout + r.stderr


def flip(path):
    """The same path with every letter's case swapped (the same file on Windows)."""
    return path.swapcase()


def main():
    tmp = tempfile.mkdtemp(prefix='fbrb_guards_')
    try:
        game = os.path.join(tmp, 'Game')
        os.makedirs(os.path.join(game, 'levels'))
        env = {**os.environ, 'BF1943_GAME': game, 'LOCALAPPDATA': tmp}
        raw = made_up()
        check(fbrb1943.Bundle(raw).data('a/one.bin') == b'first entry', 'the made-up bundle reads back')
        src = os.path.join(tmp, 'Input.fbrb')
        open(src, 'wb').write(raw)
        new = os.path.join(tmp, 'new.bin')
        open(new, 'wb').write(b'replaced')

        code, out = run(env, 'replace', src, os.path.join(tmp, 'copy.fbrb'), f'two.bin={new}')
        check(code == 0 and fbrb1943.Bundle(open(os.path.join(tmp, 'copy.fbrb'), 'rb').read()).data('two.bin') == b'replaced',
              'replace writes a copy elsewhere')
        if os.name == 'nt':
            code, out = run(env, 'replace', src, flip(src), f'two.bin={new}')
            check(code != 0 and open(src, 'rb').read() == raw, f'replace refuses its input named in another case (exit {code})')
            for target in (os.path.join(game, 'levels', 'x.fbrb'), flip(os.path.join(game, 'levels', 'x.fbrb'))):
                code, out = run(env, 'replace', src, target, f'two.bin={new}')
                check(code != 0 and os.listdir(os.path.join(game, 'levels')) == [],
                      f'replace refuses the game folder as {target[len(tmp):]} (exit {code})')
        for target in (game, os.path.join(game, 'levels', 'out'), flip(os.path.join(game, 'levels', 'out'))):
            code, out = run(env, 'extract', src, target)
            check(code != 0 and os.listdir(game) == ['levels'] and os.listdir(os.path.join(game, 'levels')) == [],
                  f'extract refuses the game folder as {target[len(tmp):] or "itself"} (exit {code})')
        out_dir = os.path.join(tmp, 'out')
        code, out = run(env, 'extract', src, out_dir)
        check(code == 0 and open(os.path.join(out_dir, 'a', 'one.bin'), 'rb').read() == b'first entry', 'extract elsewhere writes the entries')

        # another drive than the game folder: not inside it (os.path.commonpath raises for paths on two drives)
        if os.name == 'nt':
            from bfbc2core import game1943
            saved = game1943.find_game
            try:
                drive = os.path.splitdrive(game)[0].upper()
                other = ('D:' if drive != 'D:' else 'E:') + '\\mods\\x.fbrb'
                game1943.find_game = lambda given=None: game
                try:
                    inside = fbrb1943.inside_game(other)
                except ValueError as e:
                    inside = f'ValueError: {e}'
                check(inside is False, f'an output on another drive is not in the game folder ({inside})')
                check(fbrb1943.inside_game(flip(os.path.join(game, 'levels'))) is True,
                      'the game folder named in another case is the game folder')
            finally:
                game1943.find_game = saved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
