r"""Copy folders or files from OPFOR's BFBC2 tool suite into this suite, recording where they came from.

Reads OPFOR's committed files through git; writes them under this suite and records each import or update in
provenance/opfor-imports.json.

  python -B tools\import_opfor.py <path in bfbc2-tool-suite> <path in this suite> [--note "why"]
  python -B tools\import_opfor.py --plan tools\opfor-import-plan.json
  python -B tools\import_opfor.py --update <path in this suite> [...]

Files are taken from the source suite's committed state (HEAD) through read-only git commands, so
uncommitted work there and ignored build outputs are never copied. No code from the source suite is
imported or run, nothing is written there, and git runs with --no-optional-locks so not even its index
is refreshed. Destinations must not exist yet. Every written file is checked against its git blob id,
and the commit, blob ids and SHA-256 hashes are recorded in provenance/opfor-imports.json.

A folder renamed in this suite is recorded as {"kind": "rename", "from", "to", "date"}; --update follows renames.
Files moved out of an import (merged into a copy elsewhere in this suite) are recorded as {"kind": "move",
"destination", "paths": {path: where it is now}, "moved", "note"}: --update never brings them back to the import, and
a change to one in the source is reported as a conflict to merge by hand at its new place.
--update brings earlier imports up to the source suite's current commit. For each file, its last recorded
version (import or update) is compared with the source: a file changed there is replaced only where this
suite's copy is still exactly that recorded version; a file changed in both places is left alone and reported
as a conflict, to merge by hand; files new in the source are added. The update is recorded like an import.

Every item of a plan (sources committed, destinations free, named once and none inside another) and every --update
destination is checked before anything is written. Each file is written beside its place, checked against its blob
id, moved into place, and then recorded, so a file is either in place and recorded or not there at all (a failed write
leaves no partial file). A run that stops part-way records what it wrote or created (a destination folder too), with
the error under "incomplete"; `--update <destination>` then finishes it.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_SUITE = os.environ.get('BFBC2_TOOL_SUITE', r'C:\Games\Project OPFOR Toolkit\bfbc2-tool-suite')
MANIFEST = os.path.join(SUITE, 'provenance', 'opfor-imports.json')

# provenance/opfor-imports.json and its records, as JSON objects
type Manifest = dict[str, Any]
type Record = dict[str, Any]


def git(*args: str) -> bytes:
    """The output of a read-only git command in the source suite."""
    return subprocess.run(['git', '--no-optional-locks', '-C', SOURCE_SUITE, *args],
                          check=True, capture_output=True).stdout


def blob_id(data: bytes) -> str:
    """The git blob id of data."""
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def file_record(rel: str, data: bytes, blob: str) -> Record:
    """A file's record in the manifest: path, size, git blob id and SHA-256."""
    return {'path': rel, 'bytes': len(data), 'git_blob': blob, 'sha256': hashlib.sha256(data).hexdigest()}


def now() -> str:
    """The local time, to the second, with its UTC offset."""
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def uncommitted() -> list[str]:
    """The source suite's tracked files with uncommitted changes (they are never copied)."""
    status = git('status', '--porcelain', '--untracked-files=no').decode('utf-8')
    return [line[3:] for line in status.splitlines()]


def place(path: str, data: bytes, blob: str, new: bool) -> None:
    """path holding data: written to a temporary file beside it, checked against blob, then moved into place
    (os.replace). new: path must not exist (an import). A failure leaves path as it was and no temporary file
    (2026-10-30 second review: a write that failed part-way left a cut file, never recorded)."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.import-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        with open(tmp, 'rb') as f:
            if blob_id(f.read()) != blob:
                sys.exit(f'written file differs from its source: {path}')
        if new and os.path.exists(path):
            sys.exit(f'destination appeared while importing, refusing to overwrite: {path}')
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def tracked(commit: str, source: str) -> list[tuple[str, str, str]]:
    """(path, path relative to source, blob id) for every file committed under source."""
    out: list[tuple[str, str, str]] = []
    for line in git('ls-tree', '-r', '-z', commit, '--', source).split(b'\0'):
        if not line:
            continue
        meta, path = line.split(b'\t', 1)
        mode, kind, blob = meta.split()
        if kind != b'blob' or mode == b'120000':
            continue
        name = path.decode('utf-8')
        rel = os.path.relpath(name, source).replace(os.sep, '/') if name != source else os.path.basename(name)
        out.append((name, rel, blob.decode()))
    return out


def check_import(commit: str, source: str, destination: str) -> tuple[str, str, list[tuple[str, str, str]]]:
    """(source, destination folder, files) of one import, or SystemExit before anything is written: a destination
    outside this suite or already there, nothing committed at the source."""
    source = source.replace('\\', '/').strip('/')
    dst = os.path.normpath(os.path.join(SUITE, destination))
    if os.path.commonpath([dst, SUITE]) != SUITE or dst == SUITE:
        sys.exit(f'destination is outside this suite: {dst}')
    if os.path.exists(dst):
        sys.exit(f'destination exists, refusing to overwrite: {dst}')
    files = tracked(commit, source)
    if not files:
        sys.exit(f'nothing committed at {source} in {commit}')
    return source, dst, files


def import_one(commit: str, source: str, destination: str, note: str, item: Record | None = None) -> Record:
    """Copies one import; its record is filled in as each file is written (item: the dict to fill, already in the
    manifest's list, so a failure part-way still leaves the files written on record)."""
    source, dst, files = check_import(commit, source, destination)
    single = len(files) == 1 and files[0][0] == source
    copied: list[Record] = []
    item = item if item is not None else {}
    item.update({'source': source, 'destination': os.path.relpath(dst, SUITE).replace(os.sep, '/'), 'note': note,
                 'files': copied})
    for path, rel, blob in files:
        data = git('cat-file', 'blob', blob)
        if blob_id(data) != blob:
            sys.exit(f'blob mismatch reading {path}')
        target = dst if single else os.path.join(dst, rel)
        if not single and not os.path.isdir(dst):
            item['created'] = True                      # the destination folder exists from here on: recorded
        place(target, data, blob, new=True)
        copied.append(file_record(rel, data, blob))
    print(f'{len(copied):4d} files  {source} -> {os.path.relpath(dst, SUITE)}')
    return item


def recorded(manifest: Manifest, destination: str,
             moves: dict[str, str] | None = None) -> tuple[str, dict[str, Record], set[str]]:
    """(source, {path: file record}, removed paths): the last recorded version of each file imported to destination,
    and the files removed here since (records {"kind": "remove", "destination", "paths"}) or moved elsewhere in this
    suite (records {"kind": "move", "destination", "paths": {path: new place}}), which updates leave out. moves: a dict
    that gets {path: new place} of the moved ones."""
    names = {destination}                               # and the names it had before a rename
    for entry in reversed(manifest['imports']):
        if entry.get('kind') == 'rename' and entry['to'] in names:
            names.add(entry['from'])
    source: str | None = None
    files: dict[str, Record] = {}
    removed: set[str] = set()
    for entry in manifest['imports']:
        if entry.get('kind') == 'remove' and entry['destination'] in names:
            removed.update(entry['paths'])
        if entry.get('kind') == 'move' and entry['destination'] in names:
            removed.update(entry['paths'])
            if moves is not None:
                moves.update(entry['paths'])
        for item in entry.get('items', ()):
            if item['destination'] in names:
                source = item['source']
                for file in item['files']:
                    files[file['path']] = file
    if source is None:
        sys.exit(f'nothing was imported to {destination}')
    return source, files, removed


def update_one(commit: str, manifest: Manifest, destination: str, item: Record | None = None) -> Record:
    """Brings one import up to commit; its record (item: the dict to fill, already in the manifest's list) is filled
    in as each file is written, so a failure part-way still leaves the files written on record."""
    destination = destination.replace('\\', '/').strip('/')
    dst = os.path.normpath(os.path.join(SUITE, destination))
    moves: dict[str, str] = {}
    source, known, removed = recorded(manifest, destination, moves)
    updated: list[Record] = []
    conflicts: list[str] = []
    item = item if item is not None else {}
    item.update({'source': source, 'destination': destination, 'note': 'update', 'files': updated,
                 'conflicts': conflicts})
    for path, rel, blob in tracked(commit, source):
        if rel in moves:
            old = known.get(rel)
            if not old or old['git_blob'] != blob:      # changed in the source since: merge it by hand where it went
                conflicts.append(f'{rel} (moved to {moves[rel]})')
            continue
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
        place(local, data, blob, new=False)             # never a cut file, never written through a hard link
        updated.append({**file_record(rel, data, blob),
                        **({'previous_git_blob': old['git_blob']} if old else {'new': True})})
    print(f'{len(updated):4d} files updated, {len(conflicts)} to merge by hand  {source} -> {destination}')
    for conflict in conflicts:
        print(f'       conflict: {conflict}')
    return item


def save_manifest(manifest: Manifest) -> None:
    """The manifest written beside itself and moved into place (UTF-8, \\n newlines)."""
    tmp = MANIFEST + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    os.replace(tmp, MANIFEST)


def run_recorded(manifest: Manifest, entry: Record, work: Callable[[list[Record]], None]) -> None:
    """Runs work(items) - which appends each item's record to items before writing its files - and records entry in
    the manifest whatever happens: a failure part-way (2026-10-30: the manifest was written only after the last item,
    so files already copied or updated were left without a record) is recorded with the files written so far and the
    error under 'incomplete'."""
    items = entry['items']
    try:
        work(items)
    except BaseException as e:
        # anything written or created is recorded (2026-10-30 second review: a destination folder made before its
        # first file failed was left unrecorded, and both a new import and --update then refused it)
        if any(i.get('files') or i.get('created') for i in items):
            entry['incomplete'] = str(e) if isinstance(e, SystemExit) else f'{type(e).__name__}: {e}'
            manifest['imports'].append(entry)
            save_manifest(manifest)
            dests = ' '.join(i['destination'] for i in items if 'destination' in i)
            print(f'stopped part-way; the {sum(len(i.get("files", ())) for i in items)} file(s) written so far are '
                  f'recorded in {MANIFEST} (marked incomplete); finish with: --update {dests}', file=sys.stderr)
        raise
    manifest['imports'].append(entry)
    save_manifest(manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('source', nargs='?')
    parser.add_argument('destination', nargs='?')
    parser.add_argument('--note', default='')
    parser.add_argument('--plan', help='JSON list of {source, destination, note}')
    parser.add_argument('--update', nargs='+', metavar='DESTINATION',
                        help='bring earlier imports up to the source HEAD')
    args = parser.parse_args()
    if args.update:
        commit = git('rev-parse', 'HEAD').decode().strip()
        with open(MANIFEST, encoding='utf-8') as f:
            manifest = json.load(f)
        for destination in args.update:                 # every destination known before anything is written
            recorded(manifest, destination.replace('\\', '/').strip('/'))
        dirty = uncommitted()
        entry = {'commit': commit, 'imported': now(),
                 'kind': 'update', 'source_uncommitted_not_copied': dirty, 'items': []}

        def update_all(items: list[Record]) -> None:
            for destination in args.update:
                items.append({})
                update_one(commit, manifest, destination, items[-1])
        run_recorded(manifest, entry, update_all)
        print(f'{sum(len(i["files"]) for i in entry["items"])} files updated to {commit[:7]}; manifest updated')
        return
    if args.plan:
        with open(args.plan, encoding='utf-8') as f:
            plan = json.load(f)
    elif args.source and args.destination:
        plan = [{'source': args.source, 'destination': args.destination, 'note': args.note}]
    else:
        parser.error('give a source and destination, or --plan')
    commit = git('rev-parse', 'HEAD').decode().strip()
    # every item checked before anything is written (2026-10-30: a later item's missing source stopped the run after
    # the earlier items were copied, and those were never recorded)
    seen: dict[str, str] = {}           # destination folder (normcased) -> as the plan names it
    for i in plan:
        _, dst, _ = check_import(commit, i['source'], i['destination'])
        key = os.path.normcase(dst)
        if key in seen:
            sys.exit(f"destination named twice in the plan: {i['destination']}")
        for other, name in seen.items():
            # 2026-10-30 second review: 'a' and 'a/b' passed, and the second stopped the run after the first was copied
            if os.path.commonpath([key, other]) in (key, other):
                sys.exit(f"destination {i['destination']} lies inside {name}, or the other way round: one import each")
        seen[key] = i['destination']
    dirty = uncommitted()
    with open(MANIFEST, encoding='utf-8') as f:
        manifest = json.load(f)
    entry = {'commit': commit, 'imported': now(),
             'source_uncommitted_not_copied': dirty, 'items': []}

    def import_all(items: list[Record]) -> None:
        for i in plan:
            items.append({})
            import_one(commit, i['source'], i['destination'], i.get('note', ''), items[-1])
    run_recorded(manifest, entry, import_all)
    print(f'{sum(len(i["files"]) for i in entry["items"])} files from {commit[:7]}; manifest updated')


if __name__ == '__main__':
    main()
