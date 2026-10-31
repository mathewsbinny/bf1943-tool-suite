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
  nj.prepared_heightfield(st, lv, game_data)   # the prepared height field, checked against its stamp and the game copy

update() exists because the long steps (sampling, the build, the check) ran for minutes on a copy read at their
start and wrote that copy back whole, so they dropped what the vehicle steps stamped meanwhile (2026-10-30). The
write is a temporary file in the same folder replaced over navkit.json (a reader never sees half a file), and every
read-modify-write holds an OS lock on navkit.json.lock (two steps finishing at once lost one's stamps, or left the file
unreadable: 2026-10-30 review, two processes updating it 400 times each). The lock file stays in place; the system
releases a lock when its process ends, so no age rule removes a lock (second review: it removed live ones).
"""
import hashlib
import json
import os
import time

LOCK_WAIT = 30.0          # s a step waits for another step's read-modify-write before it gives up
LOCK_POLL = 0.01          # s between tries for the lock
LOCK_BYTE = 4096          # the byte of navkit.json.lock that is locked (the holder's pid is written before it)
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
        raise _not_prepared(lv, p) from None
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
    """navkit.json.lock: an OS lock (msvcrt.locking on Windows, fcntl.flock elsewhere) on a file that stays in place,
    held by one read-modify-write at a time. The system releases it when its process ends, however it ends, so a killed
    step leaves nothing to clean up. 2026-10-30 second review: the lock was the file itself (O_EXCL), and one older than
    120 s was removed as left behind: two waiters could both remove it and both hold the lock, a clock jump made a live
    lock look old, and a stale lock that could not be removed (read-only) made every step spin forever."""

    def __init__(self, lv, wait=None):
        self.path = paths(lv)['settings'] + '.lock'
        self.lv, self.wait = lv, LOCK_WAIT if wait is None else wait
        self.fd = None

    def _give_up(self, why):
        try:
            with open(self.path, 'rb', buffering=0) as f:      # only the text before the locked byte
                holder = f.read(200).decode('utf-8', 'replace').strip() or '?'
        except OSError:
            holder = '?'
        raise Locked(f'{self.lv}: navkit.json is locked by another NavKit step ({self.path}: {holder}) for over '
                     f'{self.wait:.0f} s; run this step again when that one is done' + (f' ({why})' if why else ''))

    def _open(self):
        """The lock file's descriptor; a lock file that may not be written (read-only) is opened to read: it locks too."""
        flags = getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0)
        try:
            return os.open(self.path, os.O_RDWR | os.O_CREAT | flags, 0o666), True
        except FileNotFoundError:
            raise NotPrepared(f'{self.lv}: the level is not prepared (missing {os.path.dirname(self.path)}): run '
                              f'navkit.py prepare {self.lv}') from None
        except PermissionError:
            return os.open(self.path, os.O_RDONLY | flags), False

    @staticmethod
    def _try(fd):
        """Whether the lock on fd was taken (False: another process holds it)."""
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _release(fd):
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass                                            # closing the file releases it too
        finally:
            os.close(fd)

    def __enter__(self):
        end = time.time() + self.wait
        why = None
        while True:                                         # every path below checks the deadline and sleeps
            try:
                fd, writable = self._open()
            except NotPrepared:
                raise
            except OSError as e:                            # cannot be opened at all (a folder of its name, no access)
                why = f'it cannot be opened: {e}'
            else:
                if self._try(fd):
                    self.fd = fd
                    if writable:
                        try:                                # who holds it, for another step's message
                            os.lseek(fd, 0, os.SEEK_SET)
                            text = f'pid {os.getpid()} since {time.strftime("%Y-%m-%d %I:%M:%S %p")}'.encode()
                            os.write(fd, text.ljust(80)[:LOCK_BYTE])
                        except OSError:
                            pass
                    return self
                os.close(fd)
                why = None
            if time.time() >= end:
                self._give_up(why)
            time.sleep(LOCK_POLL)

    def __exit__(self, *exc):
        fd, self.fd = self.fd, None
        if fd is not None:
            self._release(fd)
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


def _not_prepared(lv, p):
    return NotPrepared(f'{lv}: the level is not prepared (missing {p}): run navkit.py prepare {lv}')


def update(lv, change):
    """Re-reads navkit.json, applies change(st) and writes it, holding the lock; returns st. When change raises,
    nothing is written. NotPrepared before the lock is taken when the level has no navkit.json (2026-10-30 second
    review: without its folder, the lock file's FileNotFoundError came out)."""
    p = paths(lv)['settings']
    if not os.path.isfile(p):
        raise _not_prepared(lv, p)
    with _Lock(lv):
        st = read(lv)
        change(st)
        _write(lv, st)
    return st


def remove(lv):
    """Removes navkit.json (navkit.py prepare, before it makes the objects again), under the lock."""
    p = paths(lv)['settings']
    if not os.path.isdir(os.path.dirname(p)):
        return                                          # nothing to remove
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
    preparation (navkit.json's 'build_from'), stamped with its .hpd's SHA-256 (and, given hpd, be that file) and with its
    jump_links.json's (and that file be unchanged), and checked. 2026-10-30: a preparation or sampling made again drops the build's stamps but leaves the old <level>.hpd,
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
    # the jump links the export and the audit read beside the build (2026-10-30 second review: jumplinks1943.py run
    # alone replaced them, unseen)
    jp = os.path.join(paths(lv)['navkit'], 'jump_links.json')
    if not built.get('jump_links_sha256'):
        return (f'{lv}: the build is stamped without its jump links (an older NavKit): build and check again '
                f'(navkit.py build {lv}; navkit.py check {lv})')
    if not os.path.isfile(jp) or sha256(jp) != built['jump_links_sha256']:
        return (f'{lv}: {jp} is not the one the build used (written again since, by jumplinks1943.py?): build and check '
                f'again (navkit.py build {lv}; navkit.py check {lv})')
    if 'checked' not in st:
        return f'{lv}: the build is not checked: navkit.py check {lv}'
    return None


def prepared_heightfield(st, lv, game_data=None):
    """The height field the level was prepared with (objects\\heightfield.res, written by navkit.py prepare), as
    bytes: refused (NavKitError) when the preparation's stamp records none, when that file is missing or not the one
    stamped ('objects_sha256'), and when game_data (the game copy's height field now) differs from it - the copy
    changed since the preparation. 2026-10-30 second review: the export reread the game's height field, so a copy
    changed after the preparation put another terrain under the prepared build."""
    want = (st.get('objects_sha256') or {}).get('heightfield.res')
    p = os.path.join(paths(lv)['objects'], 'heightfield.res')
    if not want:
        raise NavKitError(f'{lv}: the preparation records no height field: prepare again (navkit.py prepare {lv})')
    try:
        with open(p, 'rb') as f:
            data = f.read()
    except OSError as e:
        raise NavKitError(f'{lv}: the prepared height field cannot be read ({e}): prepare again (navkit.py prepare {lv})') from None
    if hashlib.sha256(data).hexdigest() != want:
        raise NavKitError(f'{lv}: {p} is not the height field the level was prepared with: prepare again '
                          f'(navkit.py prepare {lv})')
    if game_data is not None and bytes(game_data) != data:
        raise NavKitError(f'{lv}: the game copy\'s height field ({st.get("game")}) changed since the level was prepared: '
                          f'prepare, sample, build and check again (navkit.py all {lv})')
    return data


def check_passed(st):
    """Whether navkit.json's check stamp says the check passed ('<time> ok'; '<time> FAILED' otherwise)."""
    return str(st.get('checked') or '').endswith(' ok')
