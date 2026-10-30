r"""Copy folders or files from OPFOR's BFBC2 tool suite into this suite, recording where they came from.

  python -B tools\import_opfor.py <path in bfbc2-tool-suite> <path in this suite> [--note "why"]
  python -B tools\import_opfor.py --plan tools\opfor-import-plan.json
  python -B tools\import_opfor.py --update <path in this suite> [...]

Files are taken from the source suite's committed state (HEAD) through read-only git commands, so
uncommitted work there and ignored build outputs are never copied. No code from the source suite is
imported or run, nothing is written there, and git runs with --no-optional-locks so not even its index
is refreshed. Destinations must not exist yet. Every written file is checked against its git blob id,
and the commit, blob ids and SHA-256 hashes are recorded in provenance/opfor-imports.json.

A folder renamed in this suite is recorded as {"kind": "rename", "from", "to", "date"}; --update follows renames.
--update brings earlier imports up to the source suite's current commit. For each file, its last recorded
version (import or update) is compared with the source: a file changed there is replaced only where this
suite's copy is still exactly that recorded version; a file changed in both places is left alone and reported
as a conflict, to merge by hand; files new in the source are added. The update is recorded like an import.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_SUITE = os.environ.get('BFBC2_TOOL_SUITE', r'C:\Games\Project OPFOR Toolkit\bfbc2-tool-suite')
MANIFEST = os.path.join(SUITE, 'provenance', 'opfor-imports.json')


def git(*args):
    return subprocess.run(['git', '--no-optional-locks', '-C', SOURCE_SUITE, *args],
                          check=True, capture_output=True).stdout


def blob_id(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def tracked(commit, source):
    """(path relative to source, blob id) for every file committed under source."""
    out = []
    for line in git('ls-tree', '-r', '-z', commit, '--', source).split(b'\0'):
        if not line:
            continue
        meta, path = line.split(b'\t', 1)
        mode, kind, blob = meta.split()
        if kind != b'blob' or mode == b'120000':
            continue
        path = path.decode('utf-8')
        rel = os.path.relpath(path, source).replace(os.sep, '/') if path != source else os.path.basename(path)
        out.append((path, rel, blob.decode()))
    return out


def import_one(commit, source, destination, note):
    source = source.replace('\\', '/').strip('/')
    dst = os.path.normpath(os.path.join(SUITE, destination))
    if os.path.commonpath([dst, SUITE]) != SUITE or dst == SUITE:
        sys.exit(f'destination is outside this suite: {dst}')
    if os.path.exists(dst):
        sys.exit(f'destination exists, refusing to overwrite: {dst}')
    files = tracked(commit, source)
    if not files:
        sys.exit(f'nothing committed at {source} in {commit}')
    single = len(files) == 1 and files[0][0] == source
    copied = []
    for path, rel, blob in files:
        data = git('cat-file', 'blob', blob)
        if blob_id(data) != blob:
            sys.exit(f'blob mismatch reading {path}')
        d = dst if single else os.path.join(dst, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(d, 'xb') as f:
            f.write(data)
        with open(d, 'rb') as f:
            if blob_id(f.read()) != blob:
                sys.exit(f'written file differs from its source: {d}')
        copied.append({'path': rel, 'bytes': len(data), 'git_blob': blob, 'sha256': hashlib.sha256(data).hexdigest()})
    print(f'{len(copied):4d} files  {source} -> {os.path.relpath(dst, SUITE)}')
    return {'source': source, 'destination': os.path.relpath(dst, SUITE).replace(os.sep, '/'), 'note': note, 'files': copied}


def recorded(manifest, destination):
    """(source, {path: file record}, removed paths): the last recorded version of each file imported to destination,
    and the files removed here since (records {"kind": "remove", "destination", "paths"}), which updates leave out."""
    names = {destination}                               # and the names it had before a rename
    for entry in reversed(manifest['imports']):
        if entry.get('kind') == 'rename' and entry['to'] in names:
            names.add(entry['from'])
    source, files, removed = None, {}, set()
    for entry in manifest['imports']:
        if entry.get('kind') == 'remove' and entry['destination'] in names:
            removed.update(entry['paths'])
        for item in entry.get('items', ()):
            if item['destination'] in names:
                source = item['source']
                for f in item['files']:
                    files[f['path']] = f
    if source is None:
        sys.exit(f'nothing was imported to {destination}')
    return source, files, removed


def update_one(commit, manifest, destination):
    destination = destination.replace('\\', '/').strip('/')
    dst = os.path.normpath(os.path.join(SUITE, destination))
    source, known, removed = recorded(manifest, destination)
    updated, conflicts = [], []
    for path, rel, blob in tracked(commit, source):
        if rel in removed:
            continue                                    # removed here on purpose
        old = known.get(rel)
        if old and old['git_blob'] == blob:
            continue                                    # unchanged in the source
        local = dst if path == source else os.path.join(dst, rel)     # a single-file import is the file itself
        if old and os.path.isfile(local) and hashlib.sha256(open(local, 'rb').read()).hexdigest() != old['sha256']:
            conflicts.append(rel)                       # changed here too: merge by hand
            continue
        if not old and os.path.exists(local):
            conflicts.append(rel)
            continue
        data = git('cat-file', 'blob', blob)
        if blob_id(data) != blob:
            sys.exit(f'blob mismatch reading {path}')
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, 'wb') as f:
            f.write(data)
        updated.append({'path': rel, 'bytes': len(data), 'git_blob': blob, 'sha256': hashlib.sha256(data).hexdigest(),
                        **({'previous_git_blob': old['git_blob']} if old else {'new': True})})
    print(f'{len(updated):4d} files updated, {len(conflicts)} to merge by hand  {source} -> {destination}')
    for c in conflicts:
        print(f'       conflict: {c}')
    return {'source': source, 'destination': destination, 'note': 'update', 'files': updated, 'conflicts': conflicts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', nargs='?')
    ap.add_argument('destination', nargs='?')
    ap.add_argument('--note', default='')
    ap.add_argument('--plan', help='JSON list of {source, destination, note}')
    ap.add_argument('--update', nargs='+', metavar='DESTINATION', help='bring earlier imports up to the source HEAD')
    a = ap.parse_args()
    if a.update:
        commit = git('rev-parse', 'HEAD').decode().strip()
        with open(MANIFEST, encoding='utf-8') as f:
            manifest = json.load(f)
        items = [update_one(commit, manifest, d) for d in a.update]
        dirty = [l[3:] for l in git('status', '--porcelain', '--untracked-files=no').decode('utf-8').splitlines()]
        manifest['imports'].append({'commit': commit, 'imported': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                                    'kind': 'update', 'source_uncommitted_not_copied': dirty, 'items': items})
        with open(MANIFEST, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(manifest, f, indent=2)
            f.write('\n')
        print(f'{sum(len(i["files"]) for i in items)} files updated to {commit[:7]}; manifest updated')
        return
    if a.plan:
        with open(a.plan, encoding='utf-8') as f:
            plan = json.load(f)
    elif a.source and a.destination:
        plan = [{'source': a.source, 'destination': a.destination, 'note': a.note}]
    else:
        ap.error('give a source and destination, or --plan')
    for item in plan:
        if os.path.exists(os.path.join(SUITE, item['destination'])):
            sys.exit(f"destination exists, refusing to overwrite: {item['destination']}")
    commit = git('rev-parse', 'HEAD').decode().strip()
    dirty = [l[3:] for l in git('status', '--porcelain', '--untracked-files=no').decode('utf-8').splitlines()]
    imports = [import_one(commit, i['source'], i['destination'], i.get('note', '')) for i in plan]
    with open(MANIFEST, encoding='utf-8') as f:
        manifest = json.load(f)
    manifest['imports'].append({
        'commit': commit,
        'imported': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'source_uncommitted_not_copied': dirty,
        'items': imports,
    })
    with open(MANIFEST, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    print(f'{sum(len(i["files"]) for i in imports)} files from {commit[:7]}; manifest updated')


if __name__ == '__main__':
    main()
