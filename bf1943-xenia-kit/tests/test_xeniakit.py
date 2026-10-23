"""Tests for the BF1943 Xenia kit.

  python -B test_xeniakit.py           no game data: known.json, the patch file, the port edit on a built ZIP,
                                       the Xenia fix on built executables (right bytes, wrong bytes)
  python -B test_xeniakit.py --game    + a full build from your package into a temporary folder, checked
                                       against the known hashes (BF1943_PACKAGE and XENIA_DIR, or the
                                       operation-rising-sun locations next to the suite)
"""
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
import config_edits
import netfix

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def known_and_patch():
    known = json.load(open(os.path.join(KIT, 'known.json'), encoding='utf-8'))
    files = known['game']['files']
    check(len(files) == 90 and len({f['path'] for f in files}) == 90 and
          all(re.fullmatch(r'[0-9a-f]{64}', f['sha256']) for f in files), 'known.json lists 90 distinct files with SHA-256')
    patch = open(os.path.join(KIT, 'patches', known['patch']['file']), 'rb').read()
    check(hashlib.sha256(patch).hexdigest() == known['patch']['sha256'], 'the patch file matches its recorded hash')
    text = patch.decode('utf-8')
    check(f'title_id = "{known["game"]["title_id"]}"' in text and f'hash = "{known["game"]["module_hash"]}"' in text,
          'the patch file names the game and its module hash (Xenia applies it to that executable only)')
    words = re.findall(r'address = 0x([0-9a-f]{8})\s+value = 0x([0-9a-f]{8})', text)
    check(len(words) == 11 and all(0x82000000 <= int(a, 16) < 0x83000000 for a, _ in words),
          f'the patch file holds 11 code words in the game image ({len(words)})')


def port_edit():
    cfg = b'// NetworkSettingsData\r\nNetwork.ClientPort 1001\r\nNetwork.ServerPort 1003\r\nNetwork.MaxClientCount 24\r\n'
    src = io.BytesIO()
    with zipfile.ZipFile(src, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('Game.cfg', b'Game.LogFileEnable true\r\n')
        z.writestr('GameSettings.cfg', cfg)
        z.writestr('Scripts/Startup.lua', b'-- lua\n' * 50)
    out, settings, changes = config_edits.build(src.getvalue())
    z = zipfile.ZipFile(io.BytesIO(out))
    check(settings == cfg.replace(b'1001', b'25001').replace(b'1003', b'25003') and z.read('GameSettings.cfg') == settings,
          'the port edit changes exactly the two port values')
    check(z.read('Game.cfg') == b'Game.LogFileEnable true\r\n' and z.read('Scripts/Startup.lua') == b'-- lua\n' * 50
          and [i.filename for i in z.infolist()] == ['Game.cfg', 'GameSettings.cfg', 'Scripts/Startup.lua'],
          'the port edit keeps every other entry and the order')
    try:
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, 'w') as zz:
            zz.writestr('GameSettings.cfg', b'Network.ClientPort 2000\r\n')
        config_edits.build(bad.getvalue())
        check(False, 'the port edit refuses a GameSettings.cfg without the known ports')
    except ValueError:
        check(True, 'the port edit refuses a GameSettings.cfg without the known ports')


def fake_exe(right):
    """A minimal PE with one section over the five sites, holding the expected (or other) bytes."""
    rva, size = 0x29B000, 0x5000
    raw = 0x400
    data = bytearray(raw + size)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3C, 0x80)
    struct.pack_into('<4sHH', data, 0x80, b'PE\0\0', 0x8664, 1)
    struct.pack_into('<H', data, 0x80 + 20, 0xF0)
    first = 0x80 + 24 + 0xF0
    struct.pack_into('<8sIIII', data, first, b'.text', size, rva, size, raw)
    for va, expected, _ in netfix.SITES:
        at = va - netfix.BASE - rva + raw
        b = bytes.fromhex(expected)
        data[at:at + len(b)] = b if right else b'\xcc' * len(b)
    return bytes(data)


def xenia_fix():
    fixed, changes = netfix.build(fake_exe(True))
    check(len(changes) == 5 and all(c['after'] == '90' * (len(c['before']) // 2) for c in changes),
          'the Xenia fix NOPs exactly the five sites')
    check(fixed.count(b'\x90') - fake_exe(True).count(b'\x90') == 16, 'the Xenia fix changes 16 bytes and nothing else')
    try:
        netfix.build(fake_exe(False))
        check(False, 'the Xenia fix refuses another Xenia build')
    except ValueError:
        check(True, 'the Xenia fix refuses another Xenia build')


def full_build():
    research = os.path.join(os.path.dirname(os.path.dirname(KIT)), 'operation-rising-sun')
    package = os.environ.get('BF1943_PACKAGE', os.path.join(research, 'Battlefield 1943 (XBLA)', '5841097E', '000D0000',
                                                            '945CD965D6B2B2584A9C06CE16F38A075A6D390558'))
    xenia = os.environ.get('XENIA_DIR', os.path.join(research, 'Tools', 'xenia_canary_windows'))
    if not (os.path.isfile(package) and os.path.isdir(xenia)):
        print(f'skip full build: no package at {package} or no Xenia at {xenia}')
        return
    with tempfile.TemporaryDirectory() as tmp:
        py = [sys.executable, '-B', os.path.join(KIT, 'xeniakit.py')]
        r = subprocess.run(py + ['build', package, xenia, '--out', tmp], capture_output=True, text=True)
        check(r.returncode == 0, 'a full build from the package succeeds' + ('' if r.returncode == 0 else ': ' + r.stdout[-400:] + r.stderr[-400:]))
        if r.returncode:
            return
        r = subprocess.run(py + ['verify', '--out', tmp], capture_output=True, text=True)
        check(r.returncode == 0, 'verify accepts the fresh build')
        m = json.load(open(os.path.join(tmp, 'title', 'title-manifest.json'), encoding='utf-8'))
        check(sorted(f['path'] for f in m['files'] if f['replaced']) == ['dist/xenon/config.zz', 'dist/xenon/menu/hud/hud_multiplayer_cq-00.fbrb',
                                                                         'dist/xenon/menu/main/mainmenu-00.fbrb'],
              'exactly the main menu, in-game menu and config.zz are edited')
        with open(os.path.join(tmp, 'title', 'default.xex'), 'ab') as f:
            f.write(b'x')
        r = subprocess.run(py + ['verify', '--out', tmp], capture_output=True, text=True)
        check(r.returncode == 1, 'verify notices a changed title file')


if __name__ == '__main__':
    known_and_patch()
    port_edit()
    xenia_fix()
    if '--game' in sys.argv:
        full_build()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
