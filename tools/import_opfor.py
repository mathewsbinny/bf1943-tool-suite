r"""Copy one folder or file from OPFOR's BFBC2 tool suite into this suite, recording where it came from.

  python -B tools\import_opfor.py <path in bfbc2-tool-suite> <path in this suite> [--note "why"]

The source suite is only read: no code from it is imported or run, and no caches are written there.
The destination must not exist yet. Every copied file's SHA-256 is checked against its source after
copying and recorded in provenance/opfor-imports.json, so later changes to the copy stay visible.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_SUITE = os.environ.get('BFBC2_TOOL_SUITE', r'C:\Games\Project OPFOR Toolkit\bfbc2-tool-suite')
MANIFEST = os.path.join(SUITE, 'provenance', 'opfor-imports.json')
SKIP_DIRS = {'__pycache__', '.git'}
SKIP_SUFFIXES = ('.pyc', '.ini')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def files_under(root):
    if os.path.isfile(root):
        yield ''
        return
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            if not fn.endswith(SKIP_SUFFIXES):
                yield os.path.relpath(os.path.join(dp, fn), root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('destination')
    ap.add_argument('--note', default='')
    a = ap.parse_args()
    src = os.path.normpath(os.path.join(SOURCE_SUITE, a.source))
    dst = os.path.normpath(os.path.join(SUITE, a.destination))
    if os.path.commonpath([src, SOURCE_SUITE]) != os.path.normpath(SOURCE_SUITE):
        sys.exit(f'source is outside the BFBC2 tool suite: {src}')
    if os.path.commonpath([dst, SUITE]) != SUITE or dst == SUITE:
        sys.exit(f'destination is outside this suite: {dst}')
    if not os.path.exists(src):
        sys.exit(f'source not found: {src}')
    if os.path.exists(dst):
        sys.exit(f'destination exists, refusing to overwrite: {dst}')
    copied = []
    for rel in files_under(src):
        s = os.path.join(src, rel) if rel else src
        d = os.path.join(dst, rel) if rel else dst
        os.makedirs(os.path.dirname(d), exist_ok=True)
        before = sha256(s)
        shutil.copyfile(s, d)
        if sha256(d) != before or sha256(s) != before:
            sys.exit(f'hash mismatch while copying {s}')
        copied.append({'path': rel.replace(os.sep, '/') or os.path.basename(src), 'bytes': os.path.getsize(d), 'sha256': before})
    with open(MANIFEST, encoding='utf-8') as f:
        manifest = json.load(f)
    manifest['imports'].append({
        'source': os.path.relpath(src, SOURCE_SUITE).replace(os.sep, '/'),
        'destination': os.path.relpath(dst, SUITE).replace(os.sep, '/'),
        'imported': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'note': a.note,
        'files': copied,
    })
    with open(MANIFEST, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    print(f'{len(copied)} files -> {dst}')


if __name__ == '__main__':
    main()
