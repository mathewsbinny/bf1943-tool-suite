r"""BF1943 Xenia kit: turns your own Battlefield 1943 (XBLA) package into a Xenia setup with offline play.

  python -B xeniakit.py build <package> <xenia folder> [--out <folder>] [--gpu vulkan|d3d12] [--replace-patch]
                              [--experiment-bots] [--experiment-team 1|2]
  python -B xeniakit.py verify [--out <folder>]
  python -B xeniakit.py launch [--out <folder>] [--gpu vulkan|d3d12]

<package> is the game's content package (the file named 945CD965D6B2B2584A9C06CE16F38A075A6D390558, under
5841097E\000D0000\); <xenia folder> holds xenia_canary.exe (Canary build e6cd2e4bc). The output folder,
bf1943-xenia-kit\output\ by default, is never committed: everything in it is made from your own copy.

build:
 1. checks the package against the known release (SHA-256), extracts it verifying every data block's SHA-1,
    and checks each of the 90 files against the known hashes;
 2. edits three files of that copy (each result checked against known.json):
    - menu/main/mainmenu-00.fbrb: the main menu (Local Match opens a browser of locally hosted maps;
      Leaderboards and Return to Game Library removed; no EA Online check when the menu opens; no autosave-icon notice at startup);
    - menu/hud/hud_multiplayer_cq-00.fbrb: the in-game menu (CHOOSE YOUR SIDE before the first deploy;
      FRONTLINE REPORT without Leaderboards; no See Gamer Card);
    - config.zz: GameSettings.cfg network ports above 1024;
 3. installs the patch file (signed in to Xbox LIVE offline, no network) into Xenia's patches folder;
 4. writes xenia_canary_netfix.exe next to xenia_canary.exe (Xenia's UDP address bug fixed; the original
    is only read);
 5. writes title-manifest.json, kit-manifest.json and launch.cmd.
Nothing is downloaded and nothing goes online. The original package and xenia_canary.exe are only read.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
KIT = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(KIT)
sys.path[:0] = [KIT, os.path.join(SUITE, 'core'), os.path.join(SUITE, 'bf1943-console-readers', 'stfs')]
from bfbc2core import errors
import bots
import config_edits
import igm_edits
import menu_edits
import netfix
from stfs import Package

KNOWN = json.load(open(os.path.join(KIT, 'known.json'), encoding='utf-8'))
DEFAULT_OUT = os.path.join(KIT, 'output')
log = None


def sha256_file(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def say(msg):
    print(msg, flush=True)
    log.info(msg)


def safe_join(root, rel):
    parts = rel.split('/')
    if any(p in ('', '.', '..') or any(c in p for c in '\\:') for p in parts):
        raise ValueError(f'unsafe path in the package: {rel!r}')
    path = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([path, os.path.abspath(root)]) != os.path.abspath(root):
        raise ValueError(f'path leaves the output folder: {rel!r}')
    return path


def extract(package, title):
    """Every file, each data block checked against the package's SHA-1, each file against the known hashes."""
    known = {f['path']: f for f in KNOWN['game']['files']}
    pkg = Package(package)
    try:
        if pkg.magic not in (b'LIVE', b'PIRS', b'CON '):
            raise ValueError('this is not an Xbox 360 content package')
        files = [e for e in pkg.entries if not e['dir']]
        if {e['path'] for e in files} != set(known):
            raise ValueError('the package holds different files from the known release')
        for e in files:
            path = safe_join(title, e['path'])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            blocks = pkg._blocks(e['start'], e['count'], e['consecutive'])
            if len(set(blocks)) != len(blocks) or e['size'] > len(blocks) * 4096:
                raise ValueError(f'invalid block chain: {e["path"]}')
            digest, remaining = hashlib.sha256(), e['size']
            with open(path, 'xb') as out:
                for b in blocks:
                    pkg.f.seek(pkg._addr(b))
                    data = pkg.f.read(4096)
                    if len(data) != 4096 or hashlib.sha1(data).digest() != pkg._hash_entry(b)[:20]:
                        raise ValueError(f'data block SHA-1 mismatch in {e["path"]} (a damaged package?)')
                    chunk = data[:min(remaining, 4096)]
                    out.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            if digest.hexdigest() != known[e['path']]['sha256']:
                raise ValueError(f'{e["path"]} differs from the known release')
        return len(files)
    finally:
        pkg.f.close()


def build(args):
    out = os.path.abspath(args.out)
    args.xenia = os.path.abspath(args.xenia)
    title = os.path.join(out, 'title')
    if os.path.exists(title) and os.listdir(title):
        raise FileExistsError(f'{title} already exists: choose another --out, or delete it to rebuild')
    xenia_exe = os.path.join(args.xenia, netfix.EXE)
    if not os.path.isfile(xenia_exe):
        raise FileNotFoundError(f'{xenia_exe} not found: give the folder that holds xenia_canary.exe')
    launcher_text(dict(netfix=os.path.join(args.xenia, netfix.FIXED), title=title), args.gpu)   # refused before any work
    say('1/5 checking the package')
    package_hash = sha256_file(args.package)
    if package_hash != KNOWN['game']['package_sha256']:
        raise ValueError(f'the package is not the known Battlefield 1943 release (SHA-256 {package_hash}); '
                         'the edits are only verified for that version')
    xenia_bytes = open(xenia_exe, 'rb').read()
    if sha256(xenia_bytes) != KNOWN['xenia']['exe_sha256']:
        raise ValueError(f'xenia_canary.exe is not the build the kit was made for ({KNOWN["xenia"]["build"]})')
    foreign_bots_patch(os.path.join(args.xenia, 'patches'))   # refused before anything is written
    os.makedirs(title, exist_ok=True)
    say('2/5 extracting and checking every file (about 560 MB)')
    count = extract(args.package, title)
    say(f'    {count} files match the known release')
    say('3/5 editing the main menu and the network ports')
    replaced = {}
    menu_path = safe_join(title, menu_edits.BUNDLE)
    new_bundle, new_res, menu_log, new_root = menu_edits.build(open(menu_path, 'rb').read())
    if (sha256(new_res) != KNOWN['expected']['main_nav_res_sha256']
            or sha256(new_root) != KNOWN['expected'].get('main_root_res_sha256')):
        raise ValueError('the edited menu movies differ from the verified result')
    cfg_path = safe_join(title, config_edits.ARCHIVE)
    new_cfg, settings, cfg_log = config_edits.build(open(cfg_path, 'rb').read())
    if sha256(settings) != KNOWN['expected']['game_settings_cfg_sha256']:
        raise ValueError('the edited GameSettings.cfg differs from the verified result')
    say('    and the in-game menu: CHOOSE YOUR SIDE before the first deploy'
        if not args.experiment_team else f'    experiment: the deploy screen asks for team {args.experiment_team}')
    igm_path = safe_join(title, igm_edits.BUNDLE)
    new_igm, new_igm_res, igm_log = igm_edits.build(open(igm_path, 'rb').read(), args.experiment_team)
    if not args.experiment_team and sha256(new_igm_res) != KNOWN['expected']['igm_mp_cq_res_sha256']:
        raise ValueError('the edited in-game menu differs from the verified result')
    edited = [(menu_edits.BUNDLE, new_bundle, menu_log), (config_edits.ARCHIVE, new_cfg, cfg_log),
              (igm_edits.BUNDLE, new_igm, igm_log)]
    for rel, data, changes in edited:
        path = safe_join(title, rel)
        with open(path, 'wb') as f:
            f.write(data)
        replaced[rel] = changes
    say('4/5 Xenia: the patch file and the network fix')
    patch_src = os.path.join(KIT, 'patches', KNOWN['patch']['file'])
    if sha256_file(patch_src) != KNOWN['patch']['sha256']:
        raise ValueError('the kit\'s patch file was changed')
    patch_dst = os.path.join(args.xenia, 'patches', KNOWN['patch']['file'])
    os.makedirs(os.path.dirname(patch_dst), exist_ok=True)
    if os.path.exists(patch_dst) and sha256_file(patch_dst) != KNOWN['patch']['sha256'] and not args.replace_patch:
        raise FileExistsError(f'{patch_dst} exists with other contents: pass --replace-patch to replace it')
    shutil.copyfile(patch_src, patch_dst)
    bots_dst = os.path.join(args.xenia, 'patches', bots.PATCH_FILE)
    bots_text = None
    if args.experiment_bots:
        say('    experiment: offline bots (bots.py; they join, take a kit and spawn, and move only on a host program\'s commands: navhost, guestmem.py)')
        bots_text = bots.patch_toml()
    install_bots_patch(os.path.dirname(bots_dst), bots_text)
    fixed, fix_log = netfix.build(xenia_bytes)
    if sha256(fixed) != KNOWN['xenia']['netfix_sha256']:
        raise ValueError('the fixed Xenia differs from the verified result')
    fixed_path = os.path.join(args.xenia, netfix.FIXED)
    if not (os.path.isfile(fixed_path) and sha256_file(fixed_path) == KNOWN['xenia']['netfix_sha256']):
        with open(fixed_path, 'wb') as f:  # an identical copy is kept as is (it may be running)
            f.write(fixed)
    say('5/5 manifests and launch.cmd')
    known = {f['path']: f['sha256'] for f in KNOWN['game']['files']}
    files = []
    for rel in sorted(known):
        h = sha256_file(safe_join(title, rel))
        files.append(dict(path=rel, sha256=h, replaced=rel in replaced, **({'original_sha256': known[rel]} if rel in replaced else {})))
    with open(os.path.join(title, 'title-manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(dict(name='bf1943-xenia-kit', base='package', files=files), f, indent=1)
    manifest = dict(tool='bf1943-xenia-kit/xeniakit.py', built=datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                    package=os.path.abspath(args.package), package_sha256=package_hash,
                    xenia=os.path.abspath(args.xenia), xenia_sha256=KNOWN['xenia']['exe_sha256'],
                    netfix=fixed_path, netfix_sha256=sha256(fixed), netfix_changes=fix_log,
                    patch=patch_dst, patch_sha256=KNOWN['patch']['sha256'], title=title, edits=replaced,
                    experiment_team=args.experiment_team,
                    bots_patch=bots_dst if bots_text else None, bots_patch_sha256=sha256(bots_text.encode()) if bots_text else None)
    with open(os.path.join(out, 'kit-manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, indent=1)
    write_launcher(out, manifest, args.gpu)
    say(f'done: {out}\n    play: {os.path.join(out, "launch.cmd")}  (or: python -B xeniakit.py launch)')


def kit_bots_patches(patches):
    """Every bots experiment patch in a Xenia patches folder: the file under the kit's bots patch name, and any .toml
    the kit made (it starts with bots.HEADER) under another name. verify allows none in a normal build and only its
    own in a bots build; build leaves the folder that way."""
    if not os.path.isdir(patches):
        return []
    return [os.path.join(patches, f) for f in sorted(os.listdir(patches)) if f == bots.PATCH_FILE or f.endswith('.toml')
            and open(os.path.join(patches, f), encoding='utf-8', errors='replace').read().startswith(bots.HEADER)]


def foreign_bots_patch(patches):
    """Refuses a file under the kit's bots patch name that the kit did not make (it is never overwritten or removed)."""
    own = os.path.join(patches, bots.PATCH_FILE)
    if os.path.isfile(own) and not open(own, encoding='utf-8', errors='replace').read().startswith(bots.HEADER):
        raise FileExistsError(f'{own} was not made by this kit: move it away first')


def install_bots_patch(patches, text):
    """A normal build (text None) leaves no bots patch in Xenia's patches folder, a bots build exactly its own: every
    other kit-made bots patch, under any name, is removed (verify would refuse it; before 2026-10-30 only the kit's own
    file name was, so a renamed copy made the build pass and its verify fail). A file under the kit's name that the
    kit did not make is refused, with nothing changed."""
    foreign_bots_patch(patches)
    own = os.path.join(patches, bots.PATCH_FILE)
    for p in kit_bots_patches(patches):
        if text is None or os.path.normcase(p) != os.path.normcase(own):
            os.remove(p)
    if text is not None:
        with open(own, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)


def launch_args(manifest, gpu):
    return [manifest['netfix'], f'--gpu={gpu}', '--license_mask=1', os.path.join(manifest['title'], 'default.xex')]


# cmd.exe reads a batch file in the console's code page, the OEM one unless changed (437 or 850 on most Windows):
# launch.cmd is written in it, and a path it cannot hold is refused. Before 2026-10-30 the file was UTF-8, so a folder
# such as "Xenia José" became another name and launch.cmd failed ("The system cannot find the path specified").
LAUNCHER_ENCODING = 'oem' if os.name == 'nt' else 'ascii'


def cmd_quoted(path):
    """A path as one quoted cmd.exe argument: '%' doubled (cmd expands %...% even inside quotes, so a folder such as
    "50%off" broke the line before 2026-10-30); a path the batch file's code page cannot hold is refused."""
    try:
        if path.encode(LAUNCHER_ENCODING).decode(LAUNCHER_ENCODING) != path:
            raise UnicodeError
    except UnicodeError:
        raise ValueError(f'launch.cmd cannot name {path!r}: cmd.exe reads it in code page {LAUNCHER_ENCODING!r}, which '
                         'cannot hold every character of that path; use a folder whose name it can (for example plain '
                         'letters and digits)') from None
    if '"' in path:
        raise ValueError(f'a path with a double quote cannot be passed on: {path!r}')
    return '"' + path.replace('%', '%%') + '"'


def launcher_text(manifest, gpu):
    """launch.cmd's text (raises ValueError when a path cannot be written in it)."""
    netfix_exe, gpu_flag, license_flag, title = launch_args(manifest, gpu)
    cmd = ' '.join([cmd_quoted(netfix_exe), gpu_flag, license_flag, cmd_quoted(title)])
    return ('@echo off\r\nrem Made by bf1943-xenia-kit: Battlefield 1943 in Xenia, offline with Local Match.\r\n'
            f'cd /d {cmd_quoted(os.path.dirname(netfix_exe))}\r\nstart "" {cmd}\r\n')


def write_launcher(out, manifest, gpu):
    text = launcher_text(manifest, gpu)
    with open(os.path.join(out, 'launch.cmd'), 'wb') as f:
        f.write(text.encode(LAUNCHER_ENCODING))


def load(out):
    path = os.path.join(os.path.abspath(out), 'kit-manifest.json')
    if not os.path.isfile(path):
        raise FileNotFoundError(f'{path} not found: run "xeniakit.py build" first')
    return json.load(open(path, encoding='utf-8'))


def verify(args):
    m = load(args.out)
    bad = []
    title_manifest = json.load(open(os.path.join(m['title'], 'title-manifest.json'), encoding='utf-8'))
    for f in title_manifest['files']:
        if sha256_file(os.path.join(m['title'], *f['path'].split('/'))) != f['sha256']:
            bad.append(f['path'])
    checked = [(m['netfix'], m['netfix_sha256']), (m['patch'], m['patch_sha256'])]
    if m.get('bots_patch'):
        checked.append((m['bots_patch'], m['bots_patch_sha256']))
    for path, expected in checked:
        if not os.path.isfile(path) or sha256_file(path) != expected:
            bad.append(path)
    if bad:
        raise ValueError(f'changed or missing since the build: {bad}')
    # the build's bot mode: Xenia applies every patch file in the folder, so a normal build must find no bots patch
    # there (another build against the same Xenia may have written one), and a bots build only its own
    extra = [p for p in kit_bots_patches(os.path.dirname(m['patch']))
             if not (m.get('bots_patch') and os.path.normcase(p) == os.path.normcase(m['bots_patch']))]
    if extra:
        raise ValueError(f'{"a normal build, but Xenia has" if not m.get("bots_patch") else "Xenia has another"} '
                         f'bots experiment patch: {extra} (rebuild, or move it away)')
    say(f'ok: {len(title_manifest["files"])} title files, the fixed Xenia and the patch file match the build'
        f'{" (with the bots experiment)" if m.get("bots_patch") else "; no bots patch"}')


def launch(args):
    m = load(args.out)
    verify(args)
    subprocess.Popen(launch_args(m, args.gpu), cwd=os.path.dirname(m['netfix']))
    say('Xenia started')


def main(argv):
    p = argparse.ArgumentParser(prog='xeniakit.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('package')
    b.add_argument('xenia')
    b.add_argument('--replace-patch', action='store_true')
    b.add_argument('--experiment-team', type=int, choices=[1, 2], help='experiment: the deploy screen requests this team')
    b.add_argument('--experiment-bots', action='store_true', help='experiment: a second patch file with offline bots')
    for s in (b, sub.add_parser('verify'), sub.add_parser('launch')):
        s.add_argument('--out', default=DEFAULT_OUT)
        s.add_argument('--gpu', default='vulkan', choices=['vulkan', 'd3d12'])
    args = p.parse_args(argv[1:])
    {'build': build, 'verify': verify, 'launch': launch}[args.command](args)
    return 0


if __name__ == '__main__':
    log = errors.setup('xeniakit', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
