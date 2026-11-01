r"""Mutation fuzzing for the suite's binary readers, with the standard library only (seeded random), so the tests
run with any Python (README "Tests": fuzz tests).

    report = fuzzcheck.run('dbx.parse', dbx.parse, [seed], iterations=800, boundaries=[[24, 40, 52]])
    assert report.ok, report.text()

The contract a reader meets on any input: it returns a result, or raises ValueError (a subclass included: the
module's own error type, UnicodeDecodeError, json's error) with a message. A case breaks it when the reader
  - raises anything else (IndexError, struct.error, ZeroDivisionError, TypeError, KeyError, RecursionError, ...);
  - takes longer than `limit` seconds (a hang: the case runs on a worker thread, which is abandoned);
  - grows the process's peak memory by more than `memory` bytes (Windows: peak commit, so a lazily zeroed array
    counts; elsewhere peak resident size);
  - returns a result for a strict prefix of a seed (a file cut short read as whole: silent truncation), unless the
    format allows that (prefix_ok), or the result holds the same data as the whole seed's (same(a, b): the cut lost
    only bytes the reader does not use, such as padding);
  - returns a result that `check(result, data)` finds wrong (a message; None when it is fine).

Each case is one seed with one to three mutations (four to eight in long mode), drawn from a random.Random seeded
with the run's seed and the case number, so a case replays exactly. Mutations: bit flips; a byte set to 0, 1,
0x7F, 0x80, 0xFF or a random value; a 16-, 32- or 64-bit word (either byte order, mostly 4-byte aligned) set to
0, 1, 2, small counts, 0x7F.., 0x80.., all ones (-1), the file's length +-1, an offset back into the header; a
32-bit float set to NaN, +-infinity, +-1e30 or 0; a cut at a structural boundary (`boundaries`, per seed) or
anywhere; a slice deleted, zeroed, duplicated in place (a repeated block) or replaced by random bytes; junk
appended; and the seed's named fields (`fields`: (offset, struct format) of counts and offsets) set to those words.
Before the random cases every seed is read whole (it must read: a seed the reader refuses is a broken test), cut
at each of its boundaries, and each named field set to each value.

A breaking case is shrunk (slices removed, then bytes zeroed, while it breaks the same way, a few seconds at most)
and reported with what replays it. A run stops at its fifth distinct finding (max_findings) or at a hang (the hung
worker cannot run more cases). Helpers for regression tests: outcome(fn, data), refused(fn, data); fixtures(path)
loads another test file's made-up file builders.

Modes (environment):
  quick (default)       the seed 1943 and the iterations the test gives: fixed, a few seconds per reader (CI)
  BF1943_FUZZ=long      50 x the iterations (BF1943_FUZZ=<n>: n x), a random run seed, printed so a failure can be
                        replayed; BF1943_FUZZ_SEED=<n> sets the run seed (replay)
Each breaking input is also written to <suite>\work\fuzz\<name>-<seed>-<case>.bin (the work folder is not committed).
"""
import ctypes
import os
import queue
import random
import struct
import sys
import threading
import time
import traceback

SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUICK_SEED = 1943
LONG_FACTOR = 50
INTERESTING_BYTES = (0, 1, 0x7F, 0x80, 0xFF)
FLOATS = (float('nan'), float('inf'), float('-inf'), 1e30, -1e30, 0.0, -0.0, 3.4e38)


def mode():
    """(long, run seed, iteration factor) from the environment."""
    want = os.environ.get('BF1943_FUZZ', '').strip().lower()
    long = bool(want) and want not in ('0', 'quick')
    factor = (int(want) if want.isdigit() else LONG_FACTOR) if long else 1
    given = os.environ.get('BF1943_FUZZ_SEED', '').strip()
    seed = int(given, 0) if given else (random.SystemRandom().randrange(1 << 32) if long else QUICK_SEED)
    return long, seed, factor


# ------------------------------------------------------------------------------------------------ memory

class _Counters(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong), ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t), ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t)]


def peak_memory():
    """The process's peak memory in bytes so far (Windows: peak commit charge), or None when unknown."""
    if sys.platform == 'win32':
        try:
            c = _Counters()
            c.cb = ctypes.sizeof(c)
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            if k32.K32GetProcessMemoryInfo(ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(c), c.cb):
                return c.PeakPagefileUsage
        except (AttributeError, OSError):
            return None
        return None
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    except (ImportError, OSError):
        return None


# ------------------------------------------------------------------------------------------------ mutations

def words(n, size):
    """Interesting values for a field of size bytes in a file of n bytes."""
    top = (1 << (8 * size)) - 1
    vals = [0, 1, 2, 3, 4, 7, 8, 15, 16, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 0x7FFFFFFF, 0x80000000,
            0xFFFFFFFE, 0xFFFFFFFF, n, n - 1, n + 1, n // 2, 0x10, 0x18, 0x20, 0x40, top, top - 1, top >> 1]
    return sorted({v & top for v in vals if v >= 0})


def _put(data, at, fmt, value):
    out = bytearray(data)
    size = struct.calcsize(fmt)
    if at < 0 or at + size > len(out):
        return bytes(out)
    if fmt[-1] in 'bhilq':              # a signed field: the same bits
        value = value - (1 << 8 * size) if value >= 1 << (8 * size - 1) else value
    struct.pack_into(fmt, out, at, value)
    return bytes(out)


def mutate(data, rng, boundaries, fields, count):
    """(mutated bytes, [descriptions]) after `count` random mutations."""
    out, what = bytes(data), []
    for _ in range(count):
        n = len(out)
        op = rng.choice(('flip', 'flip', 'byte', 'word', 'word', 'word', 'float', 'cut', 'delete', 'zero', 'dup',
                         'random', 'append', 'field', 'field'))
        if op == 'field' and not fields:
            op = 'word'
        if n == 0:
            op = 'append'
        if op == 'flip':
            b = bytearray(out)
            for _ in range(rng.randint(1, 8)):
                i = rng.randrange(n)
                b[i] ^= 1 << rng.randrange(8)
            out, desc = bytes(b), 'bit flips'
        elif op == 'byte':
            i = rng.randrange(n)
            v = rng.choice(INTERESTING_BYTES + (rng.randrange(256),))
            out, desc = out[:i] + bytes([v]) + out[i + 1:], f'byte {i:#x} = {v:#x}'
        elif op == 'word':
            size = rng.choice((2, 4, 4, 4, 8))
            at = rng.randrange(max(1, n - size + 1))
            if rng.random() < 0.7:
                at -= at % size
            e = rng.choice('<>')
            v = rng.choice(words(n, size))
            out, desc = _put(out, at, e + {2: 'H', 4: 'I', 8: 'Q'}[size], v), f'u{8 * size}{e} {at:#x} = {v:#x}'
        elif op == 'float':
            at = rng.randrange(max(1, n - 3))
            at -= at % 4
            e = rng.choice('<>')
            v = rng.choice(FLOATS)
            out, desc = _put(out, at, e + 'f', v), f'f32{e} {at:#x} = {v}'
        elif op == 'field':
            at, fmt = rng.choice(fields)
            v = rng.choice(words(n, struct.calcsize(fmt)))
            out, desc = _put(out, at, fmt, v), f'field {at:#x} {fmt} = {v:#x}'
        elif op == 'cut':
            inside = [b for b in boundaries if 0 <= b < n]
            at = rng.choice(inside) if inside and rng.random() < 0.6 else rng.randrange(n)
            out, desc = out[:at], f'cut at {at:#x}'
        elif op in ('delete', 'zero', 'dup', 'random'):
            at = rng.randrange(n)
            size = min(n - at, rng.choice((1, 2, 4, 8, 16, 64, max(1, n // 4))))
            if op == 'delete':
                out = out[:at] + out[at + size:]
            elif op == 'zero':
                out = out[:at] + bytes(size) + out[at + size:]
            elif op == 'dup':
                out = out[:at + size] + out[at:at + size] + out[at + size:]
            else:
                out = out[:at] + rng.randbytes(size) + out[at + size:]
            desc = f'{op} {size} bytes at {at:#x}'
        else:
            junk = rng.randbytes(rng.choice((1, 4, 16, 100)))
            out = out + junk if rng.random() < 0.8 else junk + out
            desc = f'{len(junk)} bytes added'
        what.append(desc)
    return out, what


# ------------------------------------------------------------------------------------------------ running

class _Worker:
    """Runs cases on a daemon thread, so a case that hangs can be abandoned after its time limit."""

    def __init__(self):
        self.todo, self.done = queue.Queue(), queue.Queue()
        threading.Thread(target=self._loop, daemon=True, name='fuzzcheck').start()

    def _loop(self):
        while True:
            fn, data = self.todo.get()
            try:
                self.done.put(('ok', fn(data)))
            except BaseException as e:      # noqa: BLE001 - every exception is a result here
                self.done.put(('raised', e))

    def call(self, fn, data, limit):
        self.todo.put((fn, data))
        try:
            return self.done.get(timeout=limit)
        except queue.Empty:
            return ('hang', None)


def _where(e):
    """file:line of the innermost suite frame of an exception's traceback."""
    frames = traceback.extract_tb(e.__traceback__)
    mine = [f for f in frames if os.path.normcase(os.path.abspath(f.filename)).startswith(os.path.normcase(SUITE))
            and os.sep + 'tests' + os.sep not in f.filename and not f.filename.endswith('fuzzcheck.py')]
    f = (mine or frames or [None])[-1]
    if f is None:
        return '?'
    return f'{os.path.relpath(f.filename, SUITE)}:{f.lineno}'


class Finding:
    def __init__(self, kind, signature, detail, data, how):
        self.kind, self.signature, self.detail, self.data, self.how = kind, signature, detail, data, how
        self.shrunk, self.saved = data, None

    def text(self):
        hexed = self.shrunk[:96].hex() + ('...' if len(self.shrunk) > 96 else '')
        return (f'{self.kind}: {self.detail}\n      at {self.signature[1]}; {self.how}\n'
                f'      input ({len(self.shrunk)} bytes, shrunk from {len(self.data)}): {hexed}'
                + (f'\n      saved: {self.saved}' if self.saved else ''))


class Report:
    def __init__(self, name, seed, long):
        self.name, self.seed, self.long = name, seed, long
        self.cases, self.refused, self.read, self.seconds = 0, 0, 0, 0.0
        self.findings = []

    @property
    def ok(self):
        return not self.findings

    def text(self):
        head = (f'{self.name}: {self.cases} cases in {self.seconds:.1f} s ({self.read} read, {self.refused} refused), '
                f'run seed {self.seed}' + (' (long mode)' if self.long else ''))
        if self.ok:
            return head
        replay = f'replay: BF1943_FUZZ_SEED={self.seed}' + (
            f' BF1943_FUZZ={os.environ.get("BF1943_FUZZ", "").strip()}' if self.long else '')
        return head + f'; {len(self.findings)} finding(s), {replay}\n' + '\n'.join('  ' + f.text() for f in self.findings)


def run(name, fn, seeds, iterations=500, boundaries=None, fields=None, prefix_ok=False, same=None, limit=5.0,
        memory=256 << 20, check=None, max_findings=5, shrink_seconds=4.0):
    """Fuzzes fn (bytes -> result) from the seed inputs; a Report (report.ok, report.text()). boundaries / fields:
    one list per seed (or None): structural offsets for cuts; (offset, struct format) of count and offset fields.
    same(a, b): True when two results hold the same data; a cut copy read to the whole seed's result lost nothing
    (padding the reader does not need), so it is no truncation."""
    long, seed, factor = mode()
    seeds = [bytes(s) for s in seeds]
    boundaries = boundaries or [[] for _ in seeds]
    fields = fields or [[] for _ in seeds]
    report = Report(name, seed, long)
    worker = _Worker()
    started = time.perf_counter()
    signatures = set()
    last = [None]                       # the value of the last case read
    whole = {}                          # seed bytes -> the seed's own result

    def attempt(data, how, record=True):
        """The case's outcome: 'read' or 'refused' when the contract holds, else (kind, signature, detail)."""
        before = peak_memory()
        kind, value = worker.call(fn, data, limit)
        after = peak_memory()
        if record:
            report.cases += 1
        if kind == 'hang':
            return 'hang', ('hang', how.split(';')[0]), f'no answer within {limit} s'
        if before is not None and after is not None and after - before > memory:
            return 'memory', ('memory', ''), f'peak memory grew by {(after - before) >> 20} MiB'
        if kind == 'raised':
            if isinstance(value, ValueError):
                if record:
                    report.refused += 1
                return 'refused'
            return 'crash', (type(value).__name__, _where(value)), f'{type(value).__name__}: {str(value)[:200]}'
        if record:
            report.read += 1
        last[0] = value
        if check is not None:
            try:
                problem = check(value, data)
            except Exception as e:      # noqa: BLE001 - a check that cannot run on the result: the result is wrong
                problem = f'the check failed on the result ({type(e).__name__}: {e})'
            if problem:
                return 'wrong', ('wrong', problem[:60]), problem
        return 'read'

    def broke(data, how, prefix_of=None):
        out = attempt(data, how)
        if out == 'read' and prefix_of is not None and not prefix_ok and len(data) < len(prefix_of) and not (
                same is not None and same(last[0], whole[prefix_of])):
            out = ('truncation', ('truncation', ''), f'a copy cut to {len(data)} of {len(prefix_of)} bytes was read '
                                                     f'as whole')
        if isinstance(out, str):
            return False
        kind, sig, detail = out
        if sig in signatures:
            return False                # the same bug again
        signatures.add(sig)
        f = Finding(kind, sig, detail, data, how)
        if kind not in ('hang', 'truncation'):
            f.shrunk = _shrink(data, lambda d: _signature(attempt(d, how, record=False)) == sig, shrink_seconds)
        f.saved = _save(name, seed, report.cases, f.shrunk)
        report.findings.append(f)
        return kind == 'hang'           # a hung worker cannot run more cases

    try:
        for k, s in enumerate(seeds):
            out = attempt(s, f'seed {k} whole')
            if out != 'read':
                out = out if not isinstance(out, str) else ('refused', ('refused', ''), 'refused')
                kind, sig, detail = out
                report.findings.append(Finding(kind, sig, f'the seed itself breaks the contract: {detail}', s,
                                               f'seed {k} whole'))
                return report
            whole[s] = last[0]
            for b in sorted(set(boundaries[k])):
                if 0 <= b < len(s) and broke(s[:b], f'seed {k} cut at {b:#x}', prefix_of=s):
                    return report
            for at, fmt in fields[k]:
                for v in words(len(s), struct.calcsize(fmt)):
                    if broke(_put(s, at, fmt, v), f'seed {k} field {at:#x} {fmt} = {v:#x}'):
                        return report
                    if len(report.findings) >= max_findings:
                        return report
        for case in range(iterations * factor):
            rng = random.Random(f'{seed}:{case}')
            k = rng.randrange(len(seeds))
            data, what = mutate(seeds[k], rng, boundaries[k], fields[k], rng.randint(4, 8) if long and
                                rng.random() < 0.3 else rng.randint(1, 3))
            how = f'case {case} (seed {k}: {"; ".join(what)})'
            prefix = seeds[k] if seeds[k].startswith(data) else None
            if broke(data, how, prefix_of=prefix) or len(report.findings) >= max_findings:
                break
    finally:
        report.seconds = time.perf_counter() - started
        print(report.text())
    return report


def _signature(out):
    return None if isinstance(out, str) else out[1]


def _shrink(data, same, seconds):
    """A smaller input for which same(input) still holds: slices removed (halves down to single bytes), then bytes
    zeroed, within the time given."""
    stop = time.perf_counter() + seconds
    best = data
    size = max(1, len(best) // 2)
    while size >= 1 and time.perf_counter() < stop:
        at, changed = 0, False
        while at < len(best) and time.perf_counter() < stop:
            cand = best[:at] + best[at + size:]
            if same(cand):
                best, changed = cand, True
            else:
                at += size
        if not changed:
            size //= 2
    at = 0
    while at < len(best) and time.perf_counter() < stop:
        if best[at]:
            cand = best[:at] + b'\0' + best[at + 1:]
            if same(cand):
                best = cand
        at += 1
    return best


def _save(name, seed, case, data):
    folder = os.path.join(SUITE, 'work', 'fuzz')
    try:
        os.makedirs(folder, exist_ok=True)
        safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name)
        path = os.path.join(folder, f'{safe}-{seed}-{case}.bin')
        with open(path, 'wb') as f:
            f.write(data)
        return path
    except OSError:
        return None


def fixtures(relpath):
    """Another test file of the suite (path relative to the suite) as a module, for its made-up file builders. Its
    `if __name__ == '__main__':` block does not run; it imports suite modules plainly (the test's sys.path)."""
    import importlib.util
    path = os.path.join(SUITE, relpath)
    name = 'fixtures_' + ''.join(c if c.isalnum() else '_' for c in relpath)
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def outcome(fn, data):
    """'read', 'ValueError: <message>', or '<other exception>: <message>' (a crash) - for regression tests."""
    try:
        fn(data)
        return 'read'
    except ValueError as e:
        return f'ValueError: {e}'
    except Exception as e:      # noqa: BLE001 - a crash, reported as such
        return f'{type(e).__name__}: {e}'


def refused(fn, data):
    """True when fn refuses data with a ValueError (the contract's refusal)."""
    return outcome(fn, data).startswith('ValueError')
