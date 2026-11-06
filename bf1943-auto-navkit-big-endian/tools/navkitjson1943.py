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
  nj.game_heightfield(st, lv, game_data)       # the game copy's height field, refused when the copy has none
  nj.stale(st, stage, ...)     # the stage table's pull: the first stage on the way to stage not current, and why (or None)
  nj.is_current(st, stage, ...)                # stale(...) is None
  nj.record_stage(st, stage, values, cls)      # a stage's stamps set, after dropping it and every stage after it
  nj.drop_stage(st, stage, cls)                # a stage's stamps and every later stage's removed

The stages (STAGES, 2026-11-06; one table instead of each step's own checks and drops). A stage is current when the
stages it needs are (stale() asks them first, from the preparation on) and its own stamp holds; recording or
dropping a stage drops every stage that needs it or drops with it, transitively (after()). Every write goes through
update() (the lock, a fresh read, an atomic replace).

  stage              needs       drops with                 keys (navkit.json)                 holds when
  prepared           -           -                          level, game, prepared, sea_level,  'preparation' is set
                                                            sector_offsets, objects_sha256,    (navkit.py prepare writes a
                                                            preparation                        new file: every stamp goes)
  sampled            prepared    -                          samples/0.8, sampled,              the 0.8 m pass is there, of
                                                            detailed_sectors, detailed_counts  this preparation and game
                                                                                               (files: samples.bin there)
  detailed           prepared    sampled                    samples/0.2                        the 0.2 m pass is of this
                                                                                               preparation, over the
                                                                                               current detailed sectors
  built              prepared    sampled, detailed          built, build_from                  'built', of this preparation,
                                                                                               hpd_sha256 stamped (files:
                                                                                               the .hpd that one; its
                                                                                               jump_links.json stamped and
                                                                                               unchanged)
  checked            built       -                          checked                            'checked' (passed or not:
                                                                                               check_passed tells)
  exported           built       -                          exported                           the check passed and the
                                                                                               stamp names this build's
                                                                                               hpd_sha256 (files: the
                                                                                               export is the stamped one)
  vehicle_collision  prepared    -                          vehicles/collision                 the stamp holds want
  vehicle_samples    prepared    -                          vehicles/samples/<class>           the stamp holds want (files:
                                                                                               the samples file there)
  vehicle_worlds     prepared    built, vehicle_samples     vehicles/worlds/<class>            the stamp holds want (files:
                                                                                               the world is the stamped one)

'drops with' is for a stage that took what the other made but names it in its own stamp (the 0.2 m pass's sectors),
or took it only when it was current (the build's 0.2 m pass), or names the build only through the soldier world's
SHA-256 (a vehicle world): a new one drops it, but stale() does not ask it, as no step did. A check run again keeps
the export (it needs only the build, and the passed check is part of its own rule); the audit stamps nothing in
navkit.json (its masks carry the SHA-256 of their samples and build). A per-class stage dropped for one class drops
that class's later per-class stages only; a level-wide stage drops every class's.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, NoReturn, Self

type Settings = dict[str, Any]      # navkit.json: the level's settings and every step's stamp

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


def paths(lv: str) -> dict[str, str]:
    """The level's work, objects, samples and navkit folders, its navkit.json ('settings') and its build ('hpd')."""
    from bfbc2core import game1943
    work = game1943.level_work(lv)
    return dict(work=work, objects=os.path.join(work, 'objects'), samples=os.path.join(work, 'samples'),
                navkit=os.path.join(work, 'navkit'), settings=os.path.join(work, 'navkit', 'navkit.json'),
                hpd=os.path.join(work, 'navkit', f'{lv}.hpd'))


def _retrying[T](f: Callable[[], T]) -> T:
    """f(), retried for RETRY_WAIT on PermissionError (Windows: another process has the file open this moment)."""
    end = time.time() + RETRY_WAIT
    while True:
        try:
            return f()
        except PermissionError:
            if time.time() >= end:
                raise
            time.sleep(0.02)


def read(lv: str) -> Settings:
    """navkit.json (NotPrepared when the level is not prepared, Unreadable when it is damaged)."""
    path = paths(lv)['settings']

    def load() -> str:
        with open(path, encoding='utf-8') as f:
            return f.read()
    try:
        text = _retrying(load)
    except FileNotFoundError:
        raise _not_prepared(lv, path) from None
    except OSError as e:
        raise Unreadable(f'{lv}: {path} cannot be read ({e})') from None
    try:
        st = json.loads(text)
    except ValueError as e:
        raise Unreadable(f'{lv}: {path} is damaged ({e}): prepare the level again (navkit.py prepare {lv})') from None
    if not isinstance(st, dict):
        raise Unreadable(f'{lv}: {path} holds no settings object: prepare the level again (navkit.py prepare {lv})')
    return st


class _Lock:
    """navkit.json.lock: an OS lock (msvcrt.locking on Windows, fcntl.flock elsewhere) on a file that stays in place,
    held by one read-modify-write at a time. The system releases it when its process ends, however it ends, so a killed
    step leaves nothing to clean up. 2026-10-30 second review: the lock was the file itself (O_EXCL), and one older than
    120 s was removed as left behind: two waiters could both remove it and both hold the lock, a clock jump made a live
    lock look old, and a stale lock that could not be removed (read-only) made every step spin forever."""

    def __init__(self, lv: str, wait: float | None = None) -> None:
        self.path = paths(lv)['settings'] + '.lock'
        self.lv, self.wait = lv, LOCK_WAIT if wait is None else wait
        self.fd: int | None = None

    def _give_up(self, why: str | None) -> NoReturn:
        try:
            with open(self.path, 'rb', buffering=0) as f:      # only the text before the locked byte
                holder = f.read(200).decode('utf-8', 'replace').strip() or '?'
        except OSError:
            holder = '?'
        raise Locked(f'{self.lv}: navkit.json is locked by another NavKit step ({self.path}: {holder}) for over '
                     f'{self.wait:.0f} s; run this step again when that one is done' + (f' ({why})' if why else ''))

    def _open(self) -> tuple[int, bool]:
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
    def _try(fd: int) -> bool:
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
    def _release(fd: int) -> None:
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

    def __enter__(self) -> Self:
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

    def __exit__(self, *exc: object) -> Literal[False]:
        fd, self.fd = self.fd, None
        if fd is not None:
            self._release(fd)
        return False


def _write(lv: str, st: Settings) -> None:
    path = paths(lv)['settings']
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(st, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        _retrying(lambda: os.replace(tmp, path))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write(lv: str, st: Settings) -> None:
    """navkit.json made st, whole (navkit.py prepare: a new preparation)."""
    with _Lock(lv):
        _write(lv, st)


def _not_prepared(lv: str, path: str) -> 'NotPrepared':
    return NotPrepared(f'{lv}: the level is not prepared (missing {path}): run navkit.py prepare {lv}')


def update(lv: str, change: Callable[[Settings], object]) -> Settings:
    """Re-reads navkit.json, applies change(st) and writes it, holding the lock; returns st. When change raises,
    nothing is written. NotPrepared before the lock is taken when the level has no navkit.json (2026-10-30 second
    review: without its folder, the lock file's FileNotFoundError came out)."""
    path = paths(lv)['settings']
    if not os.path.isfile(path):
        raise _not_prepared(lv, path)
    with _Lock(lv):
        st = read(lv)
        change(st)
        _write(lv, st)
    return st


def remove(lv: str) -> None:
    """Removes navkit.json (navkit.py prepare, before it makes the objects again), under the lock."""
    path = paths(lv)['settings']
    if not os.path.isdir(os.path.dirname(path)):
        return                                          # nothing to remove
    with _Lock(lv):
        try:
            _retrying(lambda: os.remove(path))
        except FileNotFoundError:
            pass


def sha256(path: str, block: int = 1 << 22) -> str:
    """A file's SHA-256 (hex), read in blocks of block bytes."""
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            digest.update(chunk)
    return digest.hexdigest()


def same_file(a: str | None, b: str | None) -> bool:
    """Whether a and b name the same file or folder: os.path.samefile when both exist (links and other spellings of one
    file count), else their resolved, case-folded paths."""
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def build_refusal(st: Mapping[str, Any], lv: str, hpd: str | None = None) -> str | None:
    """Why the build may not be read as the level's current one (None when it may): it must be of the current
    preparation (navkit.json's 'build_from'), stamped with its .hpd's SHA-256 (and, given hpd, be that file) and with its
    jump_links.json's (and that file be unchanged), and checked. 2026-10-30: a preparation or sampling made again drops
    the build's stamps but leaves the old <level>.hpd, and the exporter (and the audit, until that day's review), which
    looked only for the file, put that graph with the current terrain. The stage table's pull of 'checked'."""
    # the jump links the export and the audit read beside the build (2026-10-30 second review: jumplinks1943.py run
    # alone replaced them, unseen)
    jump_links_path = os.path.join(paths(lv)['navkit'], 'jump_links.json')
    files = {'jump_links': jump_links_path} if hpd is None else {'jump_links': jump_links_path, 'hpd': hpd}
    why = stale(st, 'checked', files=files)
    if why is None:
        return None
    again = f'build and check again (navkit.py build {lv}; navkit.py check {lv})'
    if why.stage == 'prepared':
        return f'{lv}: prepared without a preparation stamp (an older NavKit): run navkit.py all {lv}'
    if why.stage == 'built' and why.reason == 'hpd':
        return f'{lv}: {hpd} is not the build stamped in navkit.json: {again}'
    if why.stage == 'built' and why.reason == 'jump_links unstamped':
        return f'{lv}: the build is stamped without its jump links (an older NavKit): {again}'
    if why.stage == 'built' and why.reason == 'jump_links':
        return (f'{lv}: {jump_links_path} is not the one the build used (written again since, by jumplinks1943.py?): '
                f'{again}')
    if why.stage == 'built':                    # missing, of another preparation, or without its .hpd's SHA-256
        return f'{lv}: the build on disk is not of the current preparation: {again}'
    return f'{lv}: the build is not checked: navkit.py check {lv}'


def prepared_heightfield(st: Mapping[str, Any], lv: str, game_data: bytes | None = None) -> bytes:
    """The height field the level was prepared with (objects\\heightfield.res, written by navkit.py prepare), as
    bytes: refused (NavKitError) when the preparation's stamp records none, when that file is missing or not the one
    stamped ('objects_sha256'), and when game_data (the game copy's height field now) differs from it - the copy
    changed since the preparation. 2026-10-30 second review: the export reread the game's height field, so a copy
    changed after the preparation put another terrain under the prepared build."""
    want = (st.get('objects_sha256') or {}).get('heightfield.res')
    path = os.path.join(paths(lv)['objects'], 'heightfield.res')
    if not want:
        raise NavKitError(f'{lv}: the preparation records no height field: prepare again (navkit.py prepare {lv})')
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError as e:
        raise NavKitError(f'{lv}: the prepared height field cannot be read ({e}): prepare again '
                          f'(navkit.py prepare {lv})') from None
    if hashlib.sha256(data).hexdigest() != want:
        raise NavKitError(f'{lv}: {path} is not the height field the level was prepared with: prepare again '
                          f'(navkit.py prepare {lv})')
    if game_data is not None and bytes(game_data) != data:
        raise NavKitError(f'{lv}: the game copy\'s height field ({st.get("game")}) changed since the level was prepared: '
                          f'prepare, sample, build and check again (navkit.py all {lv})')
    return data


def game_heightfield(st: Mapping[str, Any], lv: str, game_data: bytes | None) -> bytes:
    """game_data, the game copy's height field of the level (game1943.Game.data's result), refused (NavKitError) when
    it is None: the copy navkit.json names ('game') has no such level. 2026-11-01: the export and the audit passed the
    None on (prepared_heightfield then skips its game-copy check) and failed later with a TypeError."""
    if game_data is None:
        raise NavKitError(f'{lv}: the game copy ({st.get("game")}) has no height field of the level: is it the copy the '
                          f'level was prepared from? (prepare again: navkit.py prepare {lv})')
    return game_data


def check_passed(st: Mapping[str, Any]) -> bool:
    """Whether navkit.json's check stamp says the check passed ('<time> ok'; '<time> FAILED' otherwise)."""
    return str(st.get('checked') or '').endswith(' ok')


# ---- the stages ----------------------------------------------------------------------------------------------------
# 2026-11-06: each step checked by hand which earlier stamps it needed and dropped by hand the later ones it made
# stale (navkit.invalidate, vehicleclasses1943.drop, the guards of navexport1943 and the vehicle builders): one state
# machine, written in eight places. STAGES declares it once (the table in this module's docstring); the steps ask
# stale() / is_current() and write through record_stage() / drop_stage(). The rules are the ones the steps had.

type Reason = str        # why a stage is not current: 'missing', 'stale', or a stage's own word ('hpd', 'check', ...)


class Stale(NamedTuple):
    """The first stage, walking from the preparation, that is not current, and why."""
    stage: str
    reason: Reason


@dataclass(frozen=True)
class Ask:
    """What a stage is validated with. cls: the class of a per-class stage. want: what the asked stage's stamp must
    hold (a vehicle stamp: vehicleclasses1943.inputs); the stages it needs are not given one. files: paths of files
    the stamps name ('hpd', 'jump_links', 'samples', 'rsnv', 'vehicle_samples', 'world'), checked only when given;
    digests: the SHA-256 of such a file when the caller has it ('rsnv')."""
    cls: str | None = None
    want: Mapping[str, Any] | None = None
    files: Mapping[str, str] = field(default_factory=dict)
    digests: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Stage:
    """One stage of a level's work. needs: the stages that must be current for this one to be (stale() asks them
    first), and whose recording or dropping drops this one. drops_with: stages whose recording or dropping drops this
    one too, but which stale() does not ask (the module docstring says why). keys: the navkit.json keys it owns, as
    paths ('a/b'; '<class>' is a per-class stage's slot). validate(st, ask): None when its own stamp holds, else why."""
    name: str
    needs: tuple[str, ...]
    drops_with: tuple[str, ...]
    keys: tuple[str, ...]
    validate: Callable[[Mapping[str, Any], Ask], Reason | None]
    per_class: bool = False


def _get(st: Mapping[str, Any], path: str, cls: str | None = None) -> Any:
    """The value at a key path, None when a part is missing (or is not an object)."""
    value: Any = st
    for part in path.split('/'):
        key = cls if part == '<class>' else part
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value


def _prepared(st: Mapping[str, Any], ask: Ask) -> Reason | None:
    return None if st.get('preparation') else 'missing'


def _sampled(st: Mapping[str, Any], ask: Ask) -> Reason | None:
    pass_08 = _get(st, 'samples/0.8')
    samples = ask.files.get('samples')
    if pass_08 is None or (samples is not None and not os.path.isfile(samples)):
        return 'missing'
    if pass_08.get('preparation') != st.get('preparation') or pass_08.get('game') != st.get('game'):
        return 'stale'
    return None


def _detailed(st: Mapping[str, Any], ask: Ask) -> Reason | None:
    pass_02 = _get(st, 'samples/0.2')
    if pass_02 is None:
        return 'missing'
    if pass_02.get('preparation') != st.get('preparation') or pass_02.get('sectors') != st.get('detailed_sectors'):
        return 'stale'
    return None


def _built(st: Mapping[str, Any], ask: Ask) -> Reason | None:
    hpd = ask.files.get('hpd')
    if (hpd is not None and not os.path.isfile(hpd)) or 'built' not in st:
        return 'missing'
    built = st.get('build_from') or {}
    if built.get('preparation') != st.get('preparation'):
        return 'stale'
    if not built.get('hpd_sha256'):
        return 'unstamped'
    if hpd is not None and sha256(hpd) != built['hpd_sha256']:
        return 'hpd'
    jump_links = ask.files.get('jump_links')
    if jump_links is not None:
        if not built.get('jump_links_sha256'):
            return 'jump_links unstamped'
        if not os.path.isfile(jump_links) or sha256(jump_links) != built['jump_links_sha256']:
            return 'jump_links'
    return None


def _checked(st: Mapping[str, Any], ask: Ask) -> Reason | None:
    return None if 'checked' in st else 'missing'


def _exported(st: Mapping[str, Any], ask: Ask) -> Reason | None:
    if not check_passed(st):
        return 'check'
    exported = st.get('exported') or {}
    if not exported:
        return 'missing'
    if exported.get('hpd_sha256') != (st.get('build_from') or {}).get('hpd_sha256'):
        return 'stale'
    digest = ask.digests.get('rsnv')
    if digest is None and 'rsnv' in ask.files:
        digest = sha256(ask.files['rsnv'])
    if digest is not None and exported.get('sha256') != digest:
        return 'file'
    return None


def _vehicle(path: str, file: str | None = None) -> Callable[[Mapping[str, Any], Ask], Reason | None]:
    """A vehicle step's stamp: there, and holding every value of ask.want (its time and what it found aside); given
    files[file], the samples file there, or the world the one stamped (its SHA-256)."""
    def validate(st: Mapping[str, Any], ask: Ask) -> Reason | None:
        on_disk = ask.files.get(file) if file else None
        if file == 'vehicle_samples' and on_disk is not None and not os.path.isfile(on_disk):
            return 'missing'
        have = _get(st, path, ask.cls)
        if have is None:
            return 'missing'
        if ask.want is not None and any(have.get(key) != wanted for key, wanted in ask.want.items()):
            return 'stale'
        if file == 'world' and on_disk is not None and have.get('sha256') != sha256(on_disk):
            return 'file'
        return None
    return validate


STAGES: dict[str, Stage] = {stage.name: stage for stage in (
    Stage('prepared', (), (), ('level', 'game', 'prepared', 'sea_level', 'sector_offsets', 'objects_sha256',
                               'preparation'), _prepared),
    Stage('sampled', ('prepared',), (), ('samples/0.8', 'sampled', 'detailed_sectors', 'detailed_counts'), _sampled),
    Stage('detailed', ('prepared',), ('sampled',), ('samples/0.2',), _detailed),
    Stage('built', ('prepared',), ('sampled', 'detailed'), ('built', 'build_from'), _built),
    Stage('checked', ('built',), (), ('checked',), _checked),
    Stage('exported', ('built',), (), ('exported',), _exported),
    Stage('vehicle_collision', ('prepared',), (), ('vehicles/collision',), _vehicle('vehicles/collision')),
    Stage('vehicle_samples', ('prepared',), (), ('vehicles/samples/<class>',),
          _vehicle('vehicles/samples/<class>', 'vehicle_samples'), per_class=True),
    Stage('vehicle_worlds', ('prepared',), ('built', 'vehicle_samples'), ('vehicles/worlds/<class>',),
          _vehicle('vehicles/worlds/<class>', 'world'), per_class=True),
)}


def stale(st: Mapping[str, Any], stage: str, cls: str | None = None, want: Mapping[str, Any] | None = None,
          files: Mapping[str, str] | None = None, digests: Mapping[str, str] | None = None) -> Stale | None:
    """None when the stage is current, else the first stage on its way that is not, and why: the stages it needs
    first (depth first, in their order), then its own stamp (the pull). want applies to the asked stage only; files
    and digests to whichever stage names that file."""
    files, digests = files or {}, digests or {}
    seen: set[str] = set()

    def walk(name: str) -> Stale | None:
        if name in seen:
            return None
        seen.add(name)
        for need in STAGES[name].needs:
            why = walk(need)
            if why:
                return why
        reason = STAGES[name].validate(st, Ask(cls, want if name == stage else None, files, digests))
        return Stale(name, reason) if reason else None
    return walk(stage)


def is_current(st: Mapping[str, Any], stage: str, cls: str | None = None, want: Mapping[str, Any] | None = None,
               files: Mapping[str, str] | None = None, digests: Mapping[str, str] | None = None) -> bool:
    """Whether the stage is current: stale(...) is None."""
    return stale(st, stage, cls, want, files, digests) is None


def after(stage: str) -> list[str]:
    """The stages a recording or dropping of stage drops: those that need it or drop with it, directly or through
    another, in the table's order."""
    found: set[str] = set()
    todo = [stage]
    while todo:
        name = todo.pop()
        for later in STAGES.values():
            if (name in later.needs or name in later.drops_with) and later.name not in found:
                found.add(later.name)
                todo.append(later.name)
    return [name for name in STAGES if name in found and name != stage]


def _pop(st: Settings, path: str, cls: str | None) -> None:
    """Removes a key path; a per-class slot without a class: every class's (the slot's parent)."""
    parts = path.split('/')
    if parts[-1] == '<class>':
        parts = parts[:-1] if cls is None else parts[:-1] + [cls]
    holder: Any = st
    for part in parts[:-1]:
        holder = holder.get(part) if isinstance(holder, dict) else None
    if isinstance(holder, dict):
        holder.pop(parts[-1], None)


def drop_stage(st: Settings, stage: str, cls: str | None = None) -> Settings:
    """Removes a stage's stamps and every later stage's (after()). cls: one class of a per-class stage, whose later
    per-class stages lose that class's stamps only (a level-wide stage's later per-class stages lose every class's).
    Returns st."""
    for name in [stage] + after(stage):
        one = cls if STAGES[name].per_class and STAGES[stage].per_class else None
        for key in STAGES[name].keys:
            _pop(st, key, one)
    return st


def record_stage(st: Settings, stage: str, values: Mapping[str, Any], cls: str | None = None) -> Settings:
    """Records a stage: drop_stage(st, stage, cls), then each of values set (keyed by the stage's own key paths as
    STAGES declares them, '<class>' standing for cls). Returns st."""
    unknown = set(values) - set(STAGES[stage].keys)
    if unknown:
        raise ValueError(f'{stage}: not its keys: {sorted(unknown)} (its keys: {", ".join(STAGES[stage].keys)})')
    if STAGES[stage].per_class and cls is None:
        raise ValueError(f'{stage}: a per-class stage is recorded for a class')
    drop_stage(st, stage, cls)
    for path, value in values.items():
        parts = [cls if part == '<class>' else part for part in path.split('/')]
        holder = st
        for part in parts[:-1]:
            holder = holder.setdefault(str(part), {})
        holder[str(parts[-1])] = value
    return st
