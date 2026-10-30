"""Tests for the BF1943 Xenia kit.

  python -B test_xeniakit.py           no game data: known.json, the patch file, the port edit on a built ZIP,
                                       the Xenia fix on built executables (right bytes, wrong bytes), and
                                       verify's bot mode on a made-up build
  python -B test_xeniakit.py --game    + a full build from your package into a temporary folder, checked
                                       against the known hashes (BF1943_PACKAGE and XENIA_DIR, or the
                                       operation-rising-sun locations next to the suite)
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
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


def verify_bot_mode():
    """verify enforces the build's bot mode on a made-up build (no game data): a normal build fails while Xenia's
    patches folder holds a bots experiment patch (as another build against the same Xenia leaves it), under its
    own name or another; a bots build passes with its own patch and fails with a second one."""
    import logging
    import bots
    import xeniakit
    xeniakit.log = logging.getLogger('test_xeniakit')
    with tempfile.TemporaryDirectory() as d:
        title, patches = os.path.join(d, 'title'), os.path.join(d, 'xenia', 'patches')
        os.makedirs(title)
        os.makedirs(patches)
        json.dump(dict(files=[]), open(os.path.join(title, 'title-manifest.json'), 'w', encoding='utf-8'))
        made = {}
        for name, text in (('netfix', b'exe'), ('patch', b'patch')):
            path = os.path.join(d, 'xenia', netfix.FIXED) if name == 'netfix' else os.path.join(patches, 'kit.patch.toml')
            open(path, 'wb').write(text)
            made[name], made[name + '_sha256'] = path, hashlib.sha256(text).hexdigest()
        bots_path = os.path.join(patches, bots.PATCH_FILE)
        text = bots.patch_toml().encode()

        def verify(bots_build):
            m = dict(made, title=title, bots_patch=bots_path if bots_build else None,
                     bots_patch_sha256=hashlib.sha256(text).hexdigest() if bots_build else None)
            json.dump(m, open(os.path.join(d, 'kit-manifest.json'), 'w', encoding='utf-8'))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    xeniakit.verify(argparse.Namespace(out=d))
                return True
            except ValueError:
                return False
        check(verify(False), 'verify: a normal build without a bots patch passes')
        open(bots_path, 'wb').write(text)
        check(not verify(False), 'verify: a normal build fails while Xenia has the bots patch (another build wrote it)')
        open(bots_path, 'wb').write(text.replace(b'is_enabled = true', b'is_enabled = false'))
        check(not verify(False), 'verify: and while it is there disabled')
        os.replace(bots_path, os.path.join(patches, 'renamed.patch.toml'))
        check(not verify(False), 'verify: and under another name')
        os.remove(os.path.join(patches, 'renamed.patch.toml'))
        open(bots_path, 'wb').write(text)
        check(verify(True), 'verify: a bots build passes with its own bots patch')
        open(os.path.join(patches, 'copy.patch.toml'), 'wb').write(text)
        check(not verify(True), 'verify: a bots build fails with a second bots patch')


def full_build():
    research = os.path.join(os.path.dirname(os.path.dirname(KIT)), 'operation-rising-sun')
    package = os.environ.get('BF1943_PACKAGE', os.path.join(research, 'Battlefield 1943 (XBLA)', '5841097E', '000D0000',
                                                            '945CD965D6B2B2584A9C06CE16F38A075A6D390558'))
    xenia = os.environ.get('XENIA_DIR', os.path.join(research, 'Tools', 'xenia_canary_windows'))
    if not (os.path.isfile(package) and os.path.isdir(xenia)):
        print(f'skip full build: no package at {package} or no Xenia at {xenia}')
        return
    before = install_state(xenia)
    with tempfile.TemporaryDirectory() as work:
        # the builder writes into the Xenia folder (the patch files, the fixed exe) and a normal build removes a
        # bots patch: build against a scratch folder holding only a copy of the exe, never the real install
        scratch = os.path.join(work, 'xenia')
        os.makedirs(scratch)
        shutil.copyfile(os.path.join(xenia, netfix.EXE), os.path.join(scratch, netfix.EXE))
        tmp = os.path.join(work, 'out')
        py = [sys.executable, '-B', os.path.join(KIT, 'xeniakit.py')]
        r = subprocess.run(py + ['build', package, scratch, '--out', tmp], capture_output=True, text=True)
        check(r.returncode == 0, 'a full build from the package succeeds' + ('' if r.returncode == 0 else ': ' + r.stdout[-400:] + r.stderr[-400:]))
        if r.returncode == 0:
            r = subprocess.run(py + ['verify', '--out', tmp], capture_output=True, text=True)
            check(r.returncode == 0, 'verify accepts the fresh build')
            m = json.load(open(os.path.join(tmp, 'title', 'title-manifest.json'), encoding='utf-8'))
            check(sorted(f['path'] for f in m['files'] if f['replaced']) == ['dist/xenon/config.zz', 'dist/xenon/menu/hud/hud_multiplayer_cq-00.fbrb',
                                                                             'dist/xenon/menu/main/mainmenu-00.fbrb'],
                  'exactly the main menu, in-game menu and config.zz are edited')
            check(os.path.isfile(os.path.join(scratch, 'patches', json.load(open(os.path.join(KIT, 'known.json'), encoding='utf-8'))['patch']['file'])), 'the patch went to the scratch Xenia')
            with open(os.path.join(tmp, 'title', 'default.xex'), 'ab') as f:
                f.write(b'x')
            r = subprocess.run(py + ['verify', '--out', tmp], capture_output=True, text=True)
            check(r.returncode == 1, 'verify notices a changed title file')
    check(install_state(xenia) == before, f'the real Xenia install is untouched ({xenia}: patches and fixed exe unchanged)')


def install_state(d):
    """Every file the builder may write in a Xenia folder (patches/, the fixed exe), by SHA-256."""
    out = {}
    for root, _dirs, files in os.walk(os.path.join(d, 'patches')):
        for f in files:
            p = os.path.join(root, f)
            out[os.path.relpath(p, d)] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    fixed = os.path.join(d, netfix.FIXED)
    if os.path.isfile(fixed):
        out[netfix.FIXED] = hashlib.sha256(open(fixed, 'rb').read()).hexdigest()
    return out


if __name__ == '__main__':
    known_and_patch()
    port_edit()
    xenia_fix()
    verify_bot_mode()
    if '--game' in sys.argv:
        full_build()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
