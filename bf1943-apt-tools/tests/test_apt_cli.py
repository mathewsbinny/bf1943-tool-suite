r"""apt.py's validation exit code on a made-up menu .res (no game data): a movie whose one action block is sound
exits 0; the same movie with a branch into the middle of an instruction reports the problem and exits 1.

  python -B bf1943-apt-tools\tests\test_apt_cli.py
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(HERE, 'apt.py')
sys.path.insert(0, HERE)
import apt as aptmod  # noqa: E402

PY = sys.executable
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def made_up(branch):
    """menu.res: a BIGF of menu.apt and menu.const. The movie (at 0x10) has one frame with one Action, whose block at
    0x70 is BranchAlways <branch> (operand at 0x74, so the target is 0x78 + branch), then End at 0x78."""
    a = bytearray(0x80)
    m = 0x10
    struct.pack_into('>4I', a, m, 9, aptmod.SIGNATURE, 1, 0x50)       # movie: frame count 1, frame table at 0x50
    struct.pack_into('>2I', a, 0x50, 1, 0x58)                         # frame 0: one item, its list at 0x58
    struct.pack_into('>I', a, 0x58, 0x60)
    struct.pack_into('>2I', a, 0x60, 1, 0x70)                         # item: Action, bytecode at 0x70
    a[0x70] = 0x99
    struct.pack_into('>i', a, 0x74, branch)
    a[0x78] = 0x00
    const = b'Apt constant file'.ljust(20, b'\0') + struct.pack('>3I', m, 0, 32)
    files = [('menu.apt', bytes(a)), ('menu.const', const)]
    head = 16 + sum(8 + len(n) + 1 for n, _ in files)
    out = bytearray(b'BIGF' + bytes(4) + struct.pack('>I', len(files)) + bytes(4))
    p, body = 16, bytearray()
    out += bytes(head - 16)
    for name, data in files:
        while (head + len(body)) % 4:
            body.append(0)
        struct.pack_into('>II', out, p, head + len(body), len(data))
        out[p + 8:p + 8 + len(name) + 1] = name.encode('ascii') + b'\0'
        p += 8 + len(name) + 1
        body += data
    out += body
    struct.pack_into('<I', out, 4, len(out))
    return bytes(out)


def res_of(apt_bytes):
    """made_up()'s archive around another .apt (the same .const)."""
    good = made_up(0)
    return aptmod.write_bigf([('menu.apt', apt_bytes), aptmod.bigf(good)[1]], good)


def second_review(tmp, env):
    """2026-10-30 second review: a movie that cannot be read exits 2 with a message (a traceback and exit 1 before);
    a branch to the byte after the block's End is a problem (it passed, and the rewriter then failed on it); a crafted
    movie whose frames name every word many times over is refused at once (it was read for minutes); an archive whose
    file runs past its end is refused; write_bigf refuses other names than the template's."""
    import time
    base = bytearray(aptmod.bigf(made_up(0))[0][1])

    def cli(data):
        path = os.path.join(tmp, 'sr.res')
        open(path, 'wb').write(data)
        t = time.time()
        r = subprocess.run([PY, '-B', TOOL, path], capture_output=True, text=True, errors='replace', env=env)
        return r.returncode, r.stdout + r.stderr, time.time() - t

    a1 = bytearray(base)
    struct.pack_into('>I', a1, 0x10 + 12, 0x7000)                      # the frame table past the .apt
    code, out, _ = cli(res_of(bytes(a1)))
    check(code == 2 and 'Traceback' not in out and 'could not be read' in out,
          f'a frame table past the .apt: exit {code}, a message, no traceback ({out.strip()[-90:]})')
    a2 = bytearray(base)
    struct.pack_into('>i', a2, 0x74, 1)                                 # BranchAlways -> 0x79, the byte after End
    code, out, _ = cli(res_of(bytes(a2)))
    check(code == 1 and 'not an instruction' in out, f'a branch past the block\'s End is a problem: exit {code}')
    try:
        aptmod.Rewriter(aptmod.Apt(res_of(bytes(a2)))).block(0x70)
        why = 'relinked'
    except ValueError as e:
        why = str(e)
    except Exception as e:
        why = f'{type(e).__name__}: {e}'
    check('not an instruction' in why, f'the rewriter refuses it in words ({why[:80]})')
    size = 0x10000                                                      # 64 KB: 30 s before
    a3 = bytearray(size)
    a3[:len(base)] = base
    half = size // 2
    for o in range(0x100, half, 4):                                     # item pointers to a word of 0 (kind 0)
        struct.pack_into('>I', a3, o, 0x80)
    for o in range(half, size, 8):                                      # every frame lists every item
        struct.pack_into('>II', a3, o, (half - 0x100) // 4, 0x100)
    struct.pack_into('>II', a3, 0x10 + 8, half // 8, half)
    code, out, took = cli(res_of(bytes(a3)))
    check(code == 2 and took < 10, f'frames naming every word many times over: refused at once (exit {code}, {took:.1f} s)')
    cut = bytearray(made_up(0))
    struct.pack_into('>I', cut, 16 + 4, 0x10000)                        # menu.apt's size past the archive
    code, out, _ = cli(bytes(cut))
    check(code == 2 and 'Traceback' not in out, f'an archive whose file runs past its end: exit {code}')
    good = made_up(0)
    try:
        aptmod.write_bigf([('menu.const', b'x'), ('menu.apt', b'y')], good)
        why = 'written'
    except ValueError as e:
        why = str(e)
    check('differ' in why, f'write_bigf refuses names other than the template\'s, in its order ({why[:60]})')


def main():
    tmp = tempfile.mkdtemp(prefix='apt_cli_')
    try:
        env = {**os.environ, 'LOCALAPPDATA': tmp}
        for label, branch, code_wanted, says in (('a sound movie', 0, 0, '0 problems'),
                                                 ('a branch into the middle of an instruction', -2, 1, 'not an instruction')):
            path = os.path.join(tmp, 'menu.res')
            open(path, 'wb').write(made_up(branch))
            r = subprocess.run([PY, '-B', TOOL, path], capture_output=True, text=True, errors='replace', env=env)
            check(r.returncode == code_wanted and says in r.stdout,
                  f'{label}: exit {r.returncode}, "{says}"' + ('' if r.returncode == code_wanted else '\n       ' + (r.stdout + r.stderr).strip()))
        second_review(tmp, env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
