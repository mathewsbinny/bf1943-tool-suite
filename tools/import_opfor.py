r"""Copy folders or files from OPFOR's BFBC2 tool suite into this suite, recording where they came from.

  python -B tools\import_opfor.py <path in bfbc2-tool-suite> <path in this suite> [--note "why"]
  python -B tools\import_opfor.py --plan tools\opfor-import-plan.json

Files are taken from the source suite's committed state (HEAD) through read-only git commands, so
uncommitted work there and ignored build outputs are never copied. No code from the source suite is
imported or run, nothing is written there, and git runs with --no-optional-locks so not even its index
is refreshed. Destinations must not exist yet. Every written file is checked against its git blob id,
and the commit, blob ids and SHA-256 hashes are recorded in provenance/opfor-imports.json.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', nargs='?')
    ap.add_argument('destination', nargs='?')
    ap.add_argument('--note', default='')
    ap.add_argument('--plan', help='JSON list of {source, destination, note}')
    a = ap.parse_args()
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
