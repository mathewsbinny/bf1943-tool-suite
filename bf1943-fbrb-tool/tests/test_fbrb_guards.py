r"""fbrb1943.py's write guards on a made-up bundle (no game data): a made-up game folder (BF1943_GAME) is never
written, by replace or by extract, however the path is cased; replace never writes over its input, however that is
cased; an output on another drive than the game folder is allowed, not an error; a junction already inside the output
folder cannot send an entry into the game folder or anywhere else outside the output (skipped where no junction can
be made); a hard link in the output to a game file (an extracted entry, or replace's output) leaves the game file
unchanged; a write that fails part-way leaves the old file and no temporary file; the manifest has \n newlines only.

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


def made_up(entries=(('a/one.bin', b'first entry'), ('two.bin', b'second'))):
    """A stored (not gzip) bundle of the entries (default: a/one.bin and two.bin), in the layout fbrb1943.py reads."""
    strings, offs = b'', {}
    for s in [n for n, _ in entries] + ['RawFileAsset']:
        if s in offs:
            continue
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


def junction(target, link):
    """A directory junction link -> target (Windows; no privilege needed); False when one cannot be made."""
    if os.name != 'nt':
        return False
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
    except (ImportError, AttributeError, OSError):
        return False
    return os.path.isdir(link) and fbrb1943.canonical(link) == fbrb1943.canonical(target)


def foreign(entries=(('a.bin', b'aa'), ('b.bin', b'bb'))):
    """A bundle whose directory lists its entries out of payload order, as the game's build_overlay-00.fbrb does (the
    reader refuses it; no bundle this tool writes is laid out so)."""
    strings, offs = b'', {}
    for s in [n for n, _ in entries] + ['RawFileAsset']:
        offs[s] = len(strings)
        strings += s.encode('ascii') + b'\0'
    d = struct.pack('>II', 2, len(strings)) + strings + struct.pack('>I', len(entries))
    sizes = [len(x) for _, x in entries]
    starts = [sum(sizes[k + 1:]) for k in range(len(entries))]          # the last entry's data first
    for (name, data), at in zip(entries, starts):
        d += struct.pack('>6I', offs[name], 0, at, len(data), len(data), offs['RawFileAsset'])
    d += b'\0' + struct.pack('>I', sum(sizes))
    directory = fbrb1943.gzip_member(d, HEADER)
    return b'FbRB' + struct.pack('>I', len(directory)) + directory + b''.join(x for _, x in reversed(entries))


def game_markers(root):
    """root made a game copy as own_game_root recognises one."""
    os.makedirs(os.path.join(root, 'levels'), exist_ok=True)
    open(os.path.join(root, 'config.zz'), 'wb').write(b'made-up config')
    open(os.path.join(root, 'levels', 'levels-common-00.fbrb'), 'wb').write(made_up())
    open(os.path.join(root, 'build_overlay-00.fbrb'), 'wb').write(foreign())


def second_review(tmp, env, game, raw, new):
    r"""2026-10-30 second review: a mod folder laid out as the game is not a game copy; another spelling of the game
    folder (\\localhost\C$\...) is the game folder; a gzip template with flags is refused; replace refuses a name two
    entries hold."""
    # a mod folder: replaced bundles in levels\<level>\, even a replaced levels-common-00.fbrb, a config.zz and a
    # build_overlay-00.fbrb that replace wrote (contiguous): outputs inside it are allowed
    mod = os.path.join(tmp, 'mods', 'my-mod')
    lvl = os.path.join(mod, 'levels', 'wake_island_s')
    os.makedirs(lvl)
    modded = os.path.join(lvl, 'wake_island_s-00.fbrb')
    for p in (modded, os.path.join(mod, 'levels', 'levels-common-00.fbrb'), os.path.join(mod, 'build_overlay-00.fbrb')):
        open(p, 'wb').write(raw)
    open(os.path.join(mod, 'config.zz'), 'wb').write(b'a ported config')
    code, out = run(env, 'extract', modded, os.path.join(mod, 'extracted'))
    check(code == 0 and os.path.isfile(os.path.join(mod, 'extracted', 'two.bin')),
          f'a bundle in a mod folder laid out as the game extracts into that folder (exit {code}: {out.strip()[-120:]})')
    code, out = run(env, 'replace', modded, os.path.join(lvl, 'v2.fbrb'), f'two.bin={new}')
    check(code == 0 and os.path.isfile(os.path.join(lvl, 'v2.fbrb')),
          f'a bundle in a mod folder is replaced into a second file beside it (exit {code}: {out.strip()[-120:]})')
    check(fbrb1943.own_game_root(modded) is None and fbrb1943.is_game_copy(os.path.join(tmp, 'OtherCopy')),
          'own_game_root: a mod folder is no game copy, a folder with the game\'s markers is one')

    # another spelling of the game folder: the administrative share and the \\?\ prefix
    drive, rest = os.path.splitdrive(os.path.abspath(game))
    if os.name == 'nt' and len(drive) == 2:
        spellings = [('\\\\localhost\\' + drive[0] + '$' + rest, 'the \\\\localhost\\C$ share'),
                     ('\\\\127.0.0.1\\' + drive[0] + '$' + rest, 'the \\\\127.0.0.1\\C$ share'),
                     ('\\\\?\\' + drive + rest, 'a \\\\?\\ path')]
        for alias, label in spellings:
            if not os.path.isdir(alias):
                print(f'skip extract into the game folder as {label}: that spelling cannot be opened here')
                continue
            code, out = run(env, 'extract', os.path.join(tmp, 'Input.fbrb'), os.path.join(alias, 'levels', 'via'))
            check(code != 0 and os.listdir(os.path.join(game, 'levels')) == [],
                  f'extract refuses the game folder spelled as {label} (exit {code})')
            code, out = run(env, 'replace', os.path.join(tmp, 'Input.fbrb'), os.path.join(alias, 'levels', 'x.fbrb'),
                            f'two.bin={new}')
            check(code != 0 and os.listdir(os.path.join(game, 'levels')) == [],
                  f'replace refuses the game folder spelled as {label} (exit {code})')

    # a gzip template with FNAME set: refused, not rebuilt into a bundle that does not read back
    import zlib
    payload = b'onetwo'
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    body = b'\x1f\x8b\x08\x08\x00\x00\x00\x00\x00\x0bpayload\0' + c.compress(payload) + c.flush() + \
        struct.pack('<II', zlib.crc32(payload), len(payload))
    strings = b'a.bin\0b.bin\0RawFileAsset\0'
    d = struct.pack('>II', 2, len(strings)) + strings + struct.pack('>I', 2)
    d += struct.pack('>6I', 0, 0, 0, 3, 3, 12) + struct.pack('>6I', 6, 0, 3, 3, 3, 12) + b'\1' + struct.pack('>I', 6)
    directory = fbrb1943.gzip_member(d, HEADER)
    named = b'FbRB' + struct.pack('>I', len(directory)) + directory + body
    try:
        out_raw, _, _ = fbrb1943.Bundle(named).build({'a.bin': b'ONE'})
        try:
            got = fbrb1943.Bundle(out_raw).data('a.bin')
            why = f'rebuilt, reads {got!r}'
        except Exception as e:
            why = f'rebuilt into a bundle that does not read ({type(e).__name__})'
    except ValueError as e:
        why = str(e)
    check('flag byte' in why, f'a gzip template with its name flag set is refused ({why[:90]})')

    # replace of a name two entries hold (equal, or equal but for case): refused, nothing written
    for label, entries in (('two entries of one name', [('x.dbx', b'FIRST'), ('x.dbx', b'second')]),
                           ('two names equal but for case', [('x.dbx', b'FIRST'), ('X.DBX', b'second')])):
        path = os.path.join(tmp, 'dup.fbrb')
        open(path, 'wb').write(made_up(entries))
        dst = os.path.join(tmp, 'dup_out.fbrb')
        if os.path.exists(dst):
            os.remove(dst)
        code, out = run(env, 'replace', path, dst, f'x.dbx={new}')
        check(code != 0 and not os.path.exists(dst), f'replace refuses {label}, nothing written (exit {code})')


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
        manifest = open(os.path.join(out_dir, 'fbrb.manifest.json'), 'rb').read()
        check(b'\n' in manifest and b'\r' not in manifest and manifest.decode('utf-8').startswith('{'),
              'fbrb.manifest.json is UTF-8 with \\n newlines (no \\r)')

        # a hard link already in the output (out\a\one.bin, or the replace output) to a file in the game folder:
        # realpath cannot see a hard link, so only writing a new file and moving it into place keeps the game file
        game_file = os.path.join(game, 'levels', 'linked.bin')
        for label in ('extract', 'replace'):
            original = b'game data, never written'
            open(game_file, 'wb').write(original)
            hout = os.path.join(tmp, 'hout')
            shutil.rmtree(hout, ignore_errors=True)
            os.makedirs(os.path.join(hout, 'a'))
            dest = os.path.join(hout, 'a', 'one.bin') if label == 'extract' else os.path.join(hout, 'copy.fbrb')
            try:
                os.link(game_file, dest)
            except (OSError, AttributeError, NotImplementedError):
                print(f'skip {label} over a hard link: no hard link could be made here')
                continue
            if label == 'extract':
                code, out = run(env, 'extract', src, hout)
                wrote = open(dest, 'rb').read() == b'first entry'
            else:
                code, out = run(env, 'replace', src, dest, f'two.bin={new}')
                wrote = open(dest, 'rb').read()[:4] == b'FbRB' and fbrb1943.Bundle(open(dest, 'rb').read()).data('two.bin') == b'replaced'
            check(open(game_file, 'rb').read() == original and code == 0 and wrote
                  and os.listdir(os.path.join(game, 'levels')) == ['linked.bin'],
                  f'{label} over a hard link to a game file writes the output and leaves the game file unchanged '
                  f'(exit {code})')
            check(not [f for _, _, fs in os.walk(hout) for f in fs if f.endswith('.tmp')],
                  f'{label} leaves no temporary file')
        os.remove(game_file)

        # a write that fails part-way leaves the destination as it was and no temporary file behind
        fdir = os.path.join(tmp, 'fail')
        os.makedirs(fdir)
        fdst = os.path.join(fdir, 'x.bin')
        open(fdst, 'wb').write(b'before')
        saved_fdopen = os.fdopen

        def failing_fdopen(fd, *a, **k):
            f = saved_fdopen(fd, *a, **k)

            class Failing:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    f.close()

                def write(self, data):
                    f.write(data[:2])
                    raise OSError('made-up write failure')
            return Failing()
        os.fdopen = failing_fdopen
        try:
            fbrb1943.write_file(fdst, b'after')
            failed = False
        except OSError:
            failed = True
        finally:
            os.fdopen = saved_fdopen
        check(failed and open(fdst, 'rb').read() == b'before' and os.listdir(fdir) == ['x.bin'],
              f'a failed write leaves the destination as it was and no temporary file ({os.listdir(fdir)})')

        # a junction already inside the output folder (out\a -> the game folder, or -> another folder): the entry
        # a/one.bin stays inside the output folder by name, but would be written through the junction
        elsewhere = os.path.join(tmp, 'elsewhere')
        os.makedirs(elsewhere)
        for label, target in (('the game folder', os.path.join(game, 'levels')), ('a folder outside the output', elsewhere)):
            jout = os.path.join(tmp, 'jout')
            shutil.rmtree(jout, ignore_errors=True)
            os.makedirs(jout)
            link = os.path.join(jout, 'a')
            if not junction(target, link):
                print(f'skip extract through a junction to {label}: no junction could be made here')
                continue
            try:
                code, out = run(env, 'extract', src, jout)
                check(code != 0 and os.listdir(target) == [],
                      f'extract refuses an entry that a junction in the output sends to {label} (exit {code}, '
                      f'{os.listdir(target)} there)')
            finally:
                os.rmdir(link)                                          # the junction only, not its target

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

        # 2026-10-30 review: entries that would be one file here (the second replaced the first, exit 0, and the
        # manifest listed both), a file where a folder goes, an entry named as the manifest: refused, nothing written
        for label, entries in (('two entries of one name', [('x.dbx', b'FIRST'), ('x.dbx', b'second')]),
                               ('two names equal but for case', [('levels/x.dbx', b'FIRST'), ('Levels/X.dbx', b'second')]),
                               ('a file where a folder goes', [('a', b'file'), ('a/b.bin', b'in a folder a')]),
                               ('an entry named as the manifest', [('fbrb.manifest.json', b'{}')])):
            path = os.path.join(tmp, 'collide.fbrb')
            open(path, 'wb').write(made_up(entries))
            cout = os.path.join(tmp, 'collide_out')
            shutil.rmtree(cout, ignore_errors=True)
            code, out = run(env, 'extract', path, cout)
            check(code != 0 and not os.path.exists(cout), f'extract refuses {label}, nothing written (exit {code})')

        # another game copy than BF1943_GAME's: the input bundle's own copy is only read too (a game copy holds
        # config.zz, levels\levels-common-00.fbrb and a build_overlay-00.fbrb in the game's own layout)
        other = os.path.join(tmp, 'OtherCopy')
        os.makedirs(os.path.join(other, 'levels', 'lv'))
        game_markers(other)
        own = os.path.join(other, 'levels', 'lv', 'level-00.fbrb')
        open(own, 'wb').write(raw)
        before = sorted(os.listdir(os.path.join(other, 'levels', 'lv')))
        code, out = run(env, 'extract', own, os.path.join(other, 'levels'))
        check(code != 0 and sorted(os.listdir(os.path.join(other, 'levels'))) == ['levels-common-00.fbrb', 'lv'],
              f'extract refuses the input bundle\'s own game copy (exit {code})')
        code, out = run(env, 'replace', own, os.path.join(other, 'levels', 'lv', 'level-01.fbrb'), f'two.bin={new}')
        check(code != 0 and sorted(os.listdir(os.path.join(other, 'levels', 'lv'))) == before,
              f'replace refuses the input bundle\'s own game copy (exit {code})')
        code, out = run(env, 'extract', own, os.path.join(tmp, 'from_other'))
        check(code == 0 and os.path.isfile(os.path.join(tmp, 'from_other', 'two.bin')),
              f'a bundle of another copy extracts elsewhere (exit {code})')
        second_review(tmp, env, game, raw, new)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
