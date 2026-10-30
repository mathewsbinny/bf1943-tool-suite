"""vehicles1943.local_entries on made-up blueprints (no game data): each game object keeps its own entries. 2026-10-30:
the cache was a module dict keyed by (id(game), partition); a new game whose id Python reused from a game already
gone got that game's entries (asked for radius 99, it got the other copy's 1).

  python -B test_vehicles1943.py
"""
import gc
import os
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bfbc2core import vehicles1943  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


class Child:
    def __init__(self, name, value):
        self.name, self.value = name, value

    def attr(self, k):
        return self.name if k == 'name' else None


class Instance:
    """A PlayerEntryComponentData whose EntryRadius is the blueprint's bytes (a number)."""

    def __init__(self, radius):
        self.children = [Child('Transform', '/'.join(['0'] * 12 + ['1', '2', '3', '1'])), Child('EntryRadius', radius),
                         Child('EntryOrderNumber', '0')]

    def attr(self, k):
        return 'PlayerEntryComponentData' if k == 'type' else None


class Copy:
    """A game copy: every vehicle blueprint has one entry point of the given radius."""

    def __init__(self, radius):
        self.radius = radius

    def data(self, name):
        return str(self.radius).encode()


if __name__ == '__main__':
    real = vehicles1943.dbx
    vehicles1943.dbx = SimpleNamespace(parse=lambda data: SimpleNamespace(instances=[Instance(data.decode())]),
                                       value=lambda c: c.value)
    try:
        part = 'objects/vehicles/made_up/jeep'
        # a new copy at the address of one gone: CPython reuses a freed object's id, but not on demand, so the
        # module's id() is made to give every object one id (what a reused address looks like to an id key)
        vehicles1943.id = lambda o: 1
        try:
            a = Copy(1)
            first = vehicles1943.local_entries(a, part)
            check([r for _p, r, _o in first] == [1.0], f'a copy\'s entries read from its blueprint ({first})')
            del a
            gc.collect()
            b = Copy(99)
            got = [r for _p, r, _o in vehicles1943.local_entries(b, part)]
        finally:
            del vehicles1943.id
        check(got == [99.0], f'a new copy at the address of one gone gets its own entries, not the old copy\'s ({got})')
        c = Copy(5)
        got_c = [r for _p, r, _o in vehicles1943.local_entries(c, part)]
        got_b = [r for _p, r, _o in vehicles1943.local_entries(b, part)]
        check(got_c == [5.0] and got_b == [99.0], f'two live copies keep their own entries ({got_c}, {got_b})')
        check(getattr(b, vehicles1943.CACHE_ATTR, {}).get(part) is not None, 'the cache is held by the game object')
        # an object that takes no attributes is read each time, not cached elsewhere
        slotted = type('Slotted', (), {'__slots__': (), 'data': lambda self, n: b'7'})()
        got = [r for _p, r, _o in vehicles1943.local_entries(slotted, part)]
        check(got == [7.0], f'a game object without attributes still gets its entries ({got})')
    finally:
        vehicles1943.dbx = real
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
