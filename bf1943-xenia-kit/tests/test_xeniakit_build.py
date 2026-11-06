r"""Xenia kit: build, verify and launch on a made-up package and Xenia

xeniakit.py's build on made-up inputs (no game data, no Xenia): a stand-in content package (the stfs reader replaced
by one serving made-up files in 4 KB blocks with their SHA-1s), a stand-in xenia_canary.exe, and the known hashes and
the three edits (menu, in-game menu, config) and the Xenia fix replaced by made-up ones, so the steps between them run
as they do on the real release. Checked: a build extracts every file, writes the edited ones, the patch file, the fixed
Xenia, the manifests and launch.cmd, and verify accepts it; launch verifies and starts the fixed Xenia (a recorder, not
a process); verify checks launch.cmd (in a normal, a d3d12 and a bots build: as written it passes, edited or missing it
fails; a manifest from before the GPU backend was recorded passes with either one); the bots and team experiments; each
refusal (an output already there, no xenia_canary.exe, another package
or Xenia, a foreign bots patch, a package that is not one or holds other files, a broken block chain, a damaged block,
a file that differs, an unsafe path, an edit or fix that differs from the verified result, a changed kit patch file, a
patch file of other contents in Xenia) stops the build in words.
"""
import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import types

import bots
import xeniakit

BLOCK = 4096
FILES = {'default.xex': b'xex' * 3000,                              # more than one block
         'dist/xenon/menu/main/mainmenu-00.fbrb': b'main menu bundle',
         'dist/xenon/menu/hud/hud_multiplayer_cq-00.fbrb': b'in-game menu bundle',
         'dist/xenon/config.zz': b'config archive'}


def sha(b):
    return hashlib.sha256(b).hexdigest()


class FakePackage:
    """What xeniakit.extract reads of stfs.Package: magic, entries, the block chain, block addresses and hashes."""
    files = FILES
    magic = b'LIVE'
    damage = None           # a path whose first block's hash entry is wrong
    loop = None             # a path whose block chain names a block twice

    def __init__(self, path):
        blob, self.entries, self.hashes = bytearray(), [], {}
        self.entries.append(dict(path='dist', dir=True))
        for name, data in self.files.items():
            first = len(blob) // BLOCK
            for k in range(0, max(len(data), 1), BLOCK):
                block = data[k:k + BLOCK].ljust(BLOCK, b'\0')
                self.hashes[len(blob) // BLOCK] = hashlib.sha1(block).digest() + bytes(4)
                blob += block
            count = len(blob) // BLOCK - first
            self.entries.append(dict(path=name, dir=False, start=first, count=count, consecutive=True, size=len(data)))
            if name == self.damage:
                self.hashes[first] = bytes(24)
        self.f = io.BytesIO(bytes(blob))

    def _blocks(self, start, count, consecutive):
        out = list(range(start, start + count))
        if self.loop and any(e.get('path') == self.loop and e['start'] == start for e in self.entries):
            out = out + out
        return out

    def _addr(self, b):
        return b * BLOCK

    def _hash_entry(self, b):
        return self.hashes[b]


class Setup:
    """The made-up inputs, the known hashes for them and the stand-in edits, installed into xeniakit."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.package = tmp / 'package'
        self.package.write_bytes(b'made-up package')
        self.xenia = tmp / 'xenia'
        self.xenia.mkdir()
        (self.xenia / 'xenia_canary.exe').write_bytes(b'made-up xenia')
        self.out = tmp / 'out'
        real = xeniakit.KNOWN
        self.known = dict(
            game=dict(files=[dict(path=p, sha256=sha(d)) for p, d in FILES.items()], package_sha256=sha(b'made-up package'),
                      title_id=real['game']['title_id']),
            xenia=dict(build='made-up', exe_sha256=sha(b'made-up xenia'), netfix_sha256=sha(b'made-up xenia fixed')),
            expected=dict(main_nav_res_sha256=sha(b'nav res'), main_root_res_sha256=sha(b'root res'),
                          game_settings_cfg_sha256=sha(b'settings'), igm_mp_cq_res_sha256=sha(b'igm res')),
            patch=dict(real['patch']))
        self.saved = {k: getattr(xeniakit, k) for k in ('KNOWN', 'Package', 'menu_edits', 'igm_edits', 'config_edits',
                                                        'netfix', 'subprocess', 'log')}
        self.started = []
        xeniakit.KNOWN = self.known
        xeniakit.Package = FakePackage
        xeniakit.log = logging.getLogger('test_xeniakit_build')
        self.results = dict(nav=b'nav res', root=b'root res', settings=b'settings', igm=b'igm res', fixed=b'made-up xenia fixed')
        r = self.results
        xeniakit.menu_edits = types.SimpleNamespace(
            BUNDLE='dist/xenon/menu/main/mainmenu-00.fbrb',
            build=lambda data: (data + b' edited', r['nav'], [dict(kind='relocate', why='menu')], r['root']))
        xeniakit.config_edits = types.SimpleNamespace(
            ARCHIVE='dist/xenon/config.zz', build=lambda data: (data + b' edited', r['settings'], [dict(why='ports')]))
        xeniakit.igm_edits = types.SimpleNamespace(
            BUNDLE='dist/xenon/menu/hud/hud_multiplayer_cq-00.fbrb',
            build=lambda data, team: (data + b' edited', r['igm'] if not team else b'team', [dict(why=f'igm {team}')]))
        xeniakit.netfix = types.SimpleNamespace(EXE='xenia_canary.exe', FIXED='xenia_canary_netfix.exe',
                                                build=lambda data: (r['fixed'], [dict(before='00', after='90')]))
        xeniakit.subprocess = types.SimpleNamespace(Popen=lambda argv, cwd=None: self.started.append((argv, cwd)))

    def restore(self):
        for k, v in self.saved.items():
            setattr(xeniakit, k, v)
        FakePackage.files, FakePackage.magic, FakePackage.damage, FakePackage.loop = FILES, b'LIVE', None, None

    def args(self, out=None, **kw):
        return argparse.Namespace(**{**dict(package=str(self.package), xenia=str(self.xenia), out=str(out or self.out),
                                            gpu='vulkan', replace_patch=False, experiment_team=None,
                                            experiment_bots=False), **kw})


@contextlib.contextmanager
def made_up(tmp):
    s = Setup(tmp)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield s
    finally:
        s.restore()


def refusal(fn, *args):
    try:
        fn(*args)
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        return f'{type(e).__name__}: {e}'
    return None


def test_build_verify_launch(tmp_path):
    with made_up(tmp_path) as s:
        xeniakit.build(s.args())
        title = s.out / 'title'
        for p, d in FILES.items():
            want = d + b' edited' if p != 'default.xex' else d
            assert (title / p).read_bytes() == want, p
        m = json.loads((s.out / 'kit-manifest.json').read_text(encoding='utf-8'))
        assert m['package_sha256'] == s.known['game']['package_sha256'] and m['bots_patch'] is None
        assert sorted(m['edits']) == sorted(p for p in FILES if p != 'default.xex')
        tm = json.loads((title / 'title-manifest.json').read_text(encoding='utf-8'))
        assert sorted(f['path'] for f in tm['files'] if f['replaced']) == sorted(m['edits'])
        assert all('original_sha256' in f for f in tm['files'] if f['replaced'])
        assert (s.xenia / 'xenia_canary_netfix.exe').read_bytes() == b'made-up xenia fixed'
        assert (s.xenia / 'patches' / s.known['patch']['file']).is_file()
        assert (s.out / 'launch.cmd').read_bytes().startswith(b'@echo off')
        xeniakit.verify(argparse.Namespace(out=str(s.out)))
        xeniakit.launch(argparse.Namespace(out=str(s.out), gpu='d3d12'))
        argv, cwd = s.started[0]
        assert argv[0].endswith('xenia_canary_netfix.exe') and argv[1] == '--gpu=d3d12' and cwd == str(s.xenia)
        assert xeniakit.main(['xeniakit.py', 'verify', '--out', str(s.out)]) == 0
        # a second build against the same Xenia: an identical fixed exe is kept as it is, the patch file is replaced
        before = os.stat(s.xenia / 'xenia_canary_netfix.exe').st_mtime_ns
        xeniakit.build(s.args(tmp_path / 'out2'))
        assert os.stat(s.xenia / 'xenia_canary_netfix.exe').st_mtime_ns == before
        # a changed title file, a missing fixed exe
        with open(title / 'default.xex', 'ab') as f:
            f.write(b'x')
        assert 'changed or missing since the build' in (refusal(xeniakit.verify, argparse.Namespace(out=str(s.out))) or '')
        assert 'FileNotFoundError' in (refusal(xeniakit.load, str(tmp_path / 'nothing')) or '')


def test_build_experiments(tmp_path):
    with made_up(tmp_path) as s:
        xeniakit.build(s.args(experiment_bots=True, experiment_team=2))
        m = json.loads((s.out / 'kit-manifest.json').read_text(encoding='utf-8'))
        assert m['experiment_team'] == 2 and m['bots_patch'].endswith(bots.PATCH_FILE)
        assert m['bots_patch_sha256'] == sha(bots.patch_toml().encode())
        assert (s.out / 'title' / 'dist/xenon/menu/hud/hud_multiplayer_cq-00.fbrb').is_file()
        xeniakit.verify(argparse.Namespace(out=str(s.out)))


def test_build_refusals(tmp_path):
    with made_up(tmp_path) as s:
        n = [0]

        def fresh():
            n[0] += 1
            return tmp_path / f'o{n[0]}'

        def build(**kw):
            return refusal(xeniakit.build, s.args(fresh(), **kw))

        busy = fresh()
        (busy / 'title').mkdir(parents=True)
        (busy / 'title' / 'x').write_bytes(b'x')
        assert 'already exists' in refusal(xeniakit.build, s.args(busy))
        assert 'xenia_canary.exe not found' in build(xenia=str(tmp_path))
        s.known['game']['package_sha256'] = '0' * 64
        assert 'not the known Battlefield 1943 release' in build()
        s.known['game']['package_sha256'] = sha(b'made-up package')
        s.known['xenia']['exe_sha256'] = '0' * 64
        assert 'not the build the kit was made for' in build()
        s.known['xenia']['exe_sha256'] = sha(b'made-up xenia')
        patches = s.xenia / 'patches'
        patches.mkdir(exist_ok=True)
        (patches / bots.PATCH_FILE).write_text('# the user\'s own\n', encoding='utf-8')
        assert 'was not made by this kit' in build()
        (patches / bots.PATCH_FILE).unlink()
        FakePackage.magic = b'XXXX'
        assert 'not an Xbox 360 content package' in build()
        FakePackage.magic = b'LIVE'
        FakePackage.files = dict(FILES, extra=b'more')
        assert 'different files from the known release' in build()
        FakePackage.files = FILES
        FakePackage.loop = 'default.xex'
        assert 'invalid block chain: default.xex' in build()
        FakePackage.loop = None
        FakePackage.damage = 'dist/xenon/config.zz'
        assert 'data block SHA-1 mismatch in dist/xenon/config.zz' in build()
        FakePackage.damage = None
        s.known['game']['files'][0]['sha256'] = '0' * 64
        assert 'default.xex differs from the known release' in build()
        s.known['game']['files'][0]['sha256'] = sha(FILES['default.xex'])
        for key, words in (('nav', 'the edited menu movies differ'), ('settings', 'GameSettings.cfg differs'),
                           ('igm', 'the edited in-game menu differs'), ('fixed', 'the fixed Xenia differs')):
            good = s.results[key]
            s.results[key] = b'other'
            assert words in build(), key
            s.results[key] = good
        assert build(experiment_team=1) is None, 'the team experiment is not checked against the verified menu'
        real_patch = dict(s.known['patch'])
        s.known['patch']['sha256'] = '0' * 64
        assert 'patch file was changed' in build()
        s.known['patch'] = real_patch
        (patches / real_patch['file']).write_text('# another patch\n', encoding='utf-8')
        assert 'pass --replace-patch' in build()
        assert build(replace_patch=True) is None


def test_verify_launch_cmd(tmp_path):
    """verify checks launch.cmd against the text the build writes for the manifest (before 2026-11-05 it did not look
    at it, so an edited or deleted launch.cmd passed): in a normal, a d3d12 and a bots build, the file as written passes,
    an edited one (another option, the other GPU backend) and a missing one fail in words."""
    with made_up(tmp_path) as s:
        for name, kw in (('normal', {}), ('d3d12', dict(gpu='d3d12')), ('bots', dict(experiment_bots=True))):
            out = tmp_path / name
            xeniakit.build(s.args(out, **kw))
            launcher = out / 'launch.cmd'
            written = launcher.read_bytes()
            m = json.loads((out / 'kit-manifest.json').read_text(encoding='utf-8'))
            assert m['gpu'] == kw.get('gpu', 'vulkan') and bool(m['bots_patch']) == (name == 'bots')
            assert written == xeniakit.launcher_text(m, m['gpu']).encode(xeniakit.LAUNCHER_ENCODING)
            verify = argparse.Namespace(out=str(out))
            assert refusal(xeniakit.verify, verify) is None, name
            launcher.write_bytes(written.replace(b'--license_mask=1', b'--license_mask=0'))
            assert f'{launcher} differs from what the build wrote' in (refusal(xeniakit.verify, verify) or ''), name
            other = 'vulkan' if m['gpu'] == 'd3d12' else 'd3d12'
            launcher.write_bytes(xeniakit.launcher_text(m, other).encode(xeniakit.LAUNCHER_ENCODING))
            assert 'differs from what the build wrote' in (refusal(xeniakit.verify, verify) or ''), f'{name}: other GPU'
            launcher.unlink()
            assert f'{launcher} is missing' in (refusal(xeniakit.verify, verify) or ''), name
            launcher.write_bytes(written)
            assert refusal(xeniakit.verify, verify) is None, name
        # a manifest from before the GPU backend was recorded: launch.cmd for either backend passes, another text not
        # (the last build's: the bots build left its patch in Xenia, which the normal builds' verify now refuses)
        out = tmp_path / 'bots'
        m = json.loads((out / 'kit-manifest.json').read_text(encoding='utf-8'))
        del m['gpu']
        (out / 'kit-manifest.json').write_text(json.dumps(m), encoding='utf-8')
        verify = argparse.Namespace(out=str(out))
        for gpu in xeniakit.GPUS:
            (out / 'launch.cmd').write_bytes(xeniakit.launcher_text(m, gpu).encode(xeniakit.LAUNCHER_ENCODING))
            assert refusal(xeniakit.verify, verify) is None, gpu
        (out / 'launch.cmd').write_bytes(b'@echo off')
        assert 'differs from what the build wrote' in (refusal(xeniakit.verify, verify) or '')


def test_extract_and_paths(tmp_path):
    with made_up(tmp_path) as s:
        assert xeniakit.extract(str(s.package), str(tmp_path / 't')) == len(FILES)
        assert (tmp_path / 't' / 'dist' / 'xenon' / 'config.zz').read_bytes() == FILES['dist/xenon/config.zz']
    for bad in ('a/../b', '/abs', 'a\\b', 'c:x', 'a//b', './a'):
        assert 'unsafe path' in (refusal(xeniakit.safe_join, str(tmp_path), bad) or ''), bad
    assert xeniakit.safe_join(str(tmp_path), 'a/b') == os.path.join(str(tmp_path), 'a', 'b')
    assert xeniakit.sha256(b'') == hashlib.sha256(b'').hexdigest()
    assert xeniakit.kit_bots_patches(str(tmp_path / 'none')) == []
