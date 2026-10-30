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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
