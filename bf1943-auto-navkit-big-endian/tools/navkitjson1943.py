"""NavKit (big-endian): a level's navkit.json (its settings and every step's stamp) and the file helpers the stamps
use, shared by navkit.py and the tools (vehicleclasses1943, navexport1943, the vehicle builders, the audit) so each
rule is written once.

  import navkitjson1943 as nj
  nj.paths(lv)                 # work, objects, samples, navkit, settings (navkit.json) and hpd of a level
  nj.read(lv)                  # navkit.json (NotPrepared when the level is not prepared, Unreadable when damaged)
  nj.update(lv, change)        # under the lock: a fresh read, change(settings), an atomic write
  nj.write(lv, st)             # under the lock: an atomic write of the whole file (navkit.py prepare)
  nj.sha256(path)              # a file's SHA-256, read in blocks
  nj.same_file(a, b)           # whether two paths name one file (os.path.samefile; the resolved paths otherwise)
  nj.build_refusal(st, lv)     # why the build on disk is not the checked build of the current preparation (or None)

update() exists because the long steps (sampling, the build, the check) ran for minutes on a copy read at their
start and wrote that copy back whole, so they dropped what the vehicle steps stamped meanwhile (2026-10-30). The
write is a temporary file in the same folder replaced over navkit.json (a reader never sees half a file), and every
read-modify-write holds navkit.json.lock (two steps finishing at once lost one's stamps, or left the file unreadable:
2026-10-30 review, two processes updating it 400 times each).
"""
import hashlib
import json
import os
import time

LOCK_WAIT = 30.0          # s a step waits for another step's read-modify-write before it gives up
LOCK_STALE = 120.0        # s: a lock file this old is left from a step that was killed (a write takes milliseconds)
RETRY_WAIT = 5.0          # s: Windows refuses a replace / an open for a moment while another process has the file open


class NavKitError(Exception):
    """A NavKit step refused, with the reason in words (navkit.py prints it and exits with code 1)."""


class NotPrepared(NavKitError, FileNotFoundError):
    """navkit.json is missing: the level is not prepared (or navkit.py prepare is running)."""


class Unreadable(NavKitError):
    """navkit.json is there but is no JSON object (damaged)."""


class Locked(NavKitError):
    """Another step held navkit.json's lock for longer than LOCK_WAIT."""


def paths(lv):
    from bfbc2core import game1943
    w = game1943.level_work(lv)
    return dict(work=w, objects=os.path.join(w, 'objects'), samples=os.path.join(w, 'samples'),
                navkit=os.path.join(w, 'navkit'), settings=os.path.join(w, 'navkit', 'navkit.json'),
                hpd=os.path.join(w, 'navkit', f'{lv}.hpd'))


def _retrying(f):
    """f(), retried for RETRY_WAIT on PermissionError (Windows: another process has the file open this moment)."""
    end = time.time() + RETRY_WAIT
    while True:
        try:
            return f()
        except PermissionError:
            if time.time() >= end:
                raise
            time.sleep(0.02)


def read(lv):
    p = paths(lv)['settings']

    def load():
        with open(p, encoding='utf-8') as f:
            return f.read()
    try:
        text = _retrying(load)
    except FileNotFoundError:
        raise NotPrepared(f'{lv}: the level is not prepared (missing {p}): run navkit.py prepare {lv}') from None
    except OSError as e:
        raise Unreadable(f'{lv}: {p} cannot be read ({e})') from None
    try:
        st = json.loads(text)
    except ValueError as e:
        raise Unreadable(f'{lv}: {p} is damaged ({e}): prepare the level again (navkit.py prepare {lv})') from None
    if not isinstance(st, dict):
        raise Unreadable(f'{lv}: {p} holds no settings object: prepare the level again (navkit.py prepare {lv})')
    return st


class _Lock:
    """navkit.json.lock, made with O_EXCL: held by one read-modify-write at a time."""

    def __init__(self, lv, wait=None):
        self.path = paths(lv)['settings'] + '.lock'
        self.lv, self.wait = lv, LOCK_WAIT if wait is None else wait

    def _give_up(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                holder = f.read().strip()
        except OSError:
            holder = '?'
        raise Locked(f'{self.lv}: navkit.json is locked by another NavKit step ({self.path}: {holder}) for over '
                     f'{self.wait:.0f} s; run this step again when that one is done (a lock older than '
                     f'{LOCK_STALE:.0f} s is taken as left behind by a stopped step and removed)')

    def __enter__(self):
        end = time.time() + self.wait
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    continue                                    # released meanwhile
                if age > LOCK_STALE:                            # left by a step that was killed
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() >= end:
                    self._give_up()
                time.sleep(0.01)
                continue
            except PermissionError:                             # Windows: the lock file is being removed this moment
                if time.time() >= end:
                    self._give_up()
                time.sleep(0.01)
                continue
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(f'pid {os.getpid()} since {time.strftime("%Y-%m-%d %I:%M:%S %p")}\n')
            return self

    def __exit__(self, *exc):
        try:
            _retrying(lambda: os.remove(self.path))
        except FileNotFoundError:
            pass
        return False


def _write(lv, st):
    p = paths(lv)['settings']
    tmp = f'{p}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(st, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        _retrying(lambda: os.replace(tmp, p))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write(lv, st):
    """navkit.json made st, whole (navkit.py prepare: a new preparation)."""
    with _Lock(lv):
        _write(lv, st)


def update(lv, change):
    """Re-reads navkit.json, applies change(st) and writes it, holding the lock; returns st. When change raises,
    nothing is written."""
    with _Lock(lv):
        st = read(lv)
        change(st)
        _write(lv, st)
    return st


def remove(lv):
    """Removes navkit.json (navkit.py prepare, before it makes the objects again), under the lock."""
    p = paths(lv)['settings']
    with _Lock(lv):
        try:
            _retrying(lambda: os.remove(p))
        except FileNotFoundError:
            pass


def sha256(path, block=1 << 22):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(block), b''):
            h.update(b)
    return h.hexdigest()


def same_file(a, b):
    """Whether a and b name the same file or folder: os.path.samefile when both exist (links and other spellings of one
    file count), else their resolved, case-folded paths."""
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def build_refusal(st, lv, hpd=None):
    """Why the build may not be read as the level's current one (None when it may): it must be of the current
    preparation (navkit.json's 'build_from'), stamped with its .hpd's SHA-256 (and, given hpd, be that file), and
    checked. 2026-10-30: a preparation or sampling made again drops the build's stamps but leaves the old <level>.hpd,
    and the exporter (and the audit, until that day's review), which looked only for the file, put that graph with the
    current terrain."""
    if not st.get('preparation'):
        return f'{lv}: prepared without a preparation stamp (an older NavKit): run navkit.py all {lv}'
    built = st.get('build_from') or {}
    if 'built' not in st or built.get('preparation') != st['preparation'] or not built.get('hpd_sha256'):
        return (f'{lv}: the build on disk is not of the current preparation: build and check again (navkit.py build {lv}; '
                f'navkit.py check {lv})')
    if hpd is not None and sha256(hpd) != built['hpd_sha256']:
        return f'{lv}: {hpd} is not the build stamped in navkit.json: build and check again (navkit.py build {lv}; navkit.py check {lv})'
    if 'checked' not in st:
        return f'{lv}: the build is not checked: navkit.py check {lv}'
    return None


def check_passed(st):
    """Whether navkit.json's check stamp says the check passed ('<time> ok'; '<time> FAILED' otherwise)."""
    return str(st.get('checked') or '').endswith(' ok')
