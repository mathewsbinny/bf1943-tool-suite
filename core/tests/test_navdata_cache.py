r"""Navigation reader (navdata): its cache, cell comparison and area checks on made-up files

navdata on a made-up pathdata.hpd and made-up cells (no game data): load with a cache folder writes its entries and
reads them back, rebuilds a damaged entry, and caches nothing for a height source without contents to key on; a
cache write that fails leaves no file, its temporary one included; compare_cells pairs cells of two files (one floor
on each side is the same ground whatever its height, stacked floors pair within navdiff.STACK_TOL, 4 m included);
_check_area passes an area without boundary points and refuses a point that is not finite or one too far out;
summary counts a file.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import os
from types import SimpleNamespace

import fuzzcheck
import navdiff
import numpy as np

from bfbc2core import navdata

FORMATS = fuzzcheck.fixtures('bf1943-auto-navkit-common/tests/test_formats.py')


def made_up(tmp_path):
    path = str(tmp_path / 'pathdata.hpd')
    with open(path, 'wb') as f:
        f.write(FORMATS.hpd())
    return path


def entries(folder):
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def test_cache_written_and_read_back(tmp_path):
    path, cache = made_up(tmp_path), str(tmp_path / 'cache')
    plain = navdata.load(path)
    first = navdata.load(path, cache_dir=cache)
    names = entries(cache)
    assert len(names) == 2 and sum(n.endswith('.navdata.pkl') for n in names) == 1 \
        and sum(n.endswith('.npz') for n in names) == 1, f'the whole result and its cells: {names}'
    assert navdata.summary(first) == navdata.summary(plain)
    pkl = next(os.path.join(cache, n) for n in names if n.endswith('.navdata.pkl'))
    stamp = os.path.getmtime(pkl)
    again = navdata.load(path, cache_dir=cache)
    assert navdata.summary(again) == navdata.summary(plain) and os.path.getmtime(pkl) == stamp, 'read, not rebuilt'
    assert np.array_equal(again.cell_y, plain.cell_y) and np.array_equal(again.edges, plain.edges)


def test_damaged_cache_entries_rebuilt(tmp_path):
    path, cache = made_up(tmp_path), str(tmp_path / 'cache')
    plain = navdata.load(path)
    navdata.load(path, cache_dir=cache)
    pkl = next(os.path.join(cache, n) for n in entries(cache) if n.endswith('.navdata.pkl'))
    npz = next(os.path.join(cache, n) for n in entries(cache) if n.endswith('.npz'))
    for damaged in (pkl, npz):
        with open(damaged, 'wb') as f:
            f.write(b'not a cache entry')
    if os.path.exists(pkl):
        os.remove(pkl)                                  # the cell cache alone is read when the whole entry is gone
    nav = navdata.load(path, cache_dir=cache)
    assert navdata.summary(nav) == navdata.summary(plain) and np.array_equal(nav.cell_y, plain.cell_y)
    with np.load(npz) as z:
        assert np.array_equal(z['xz'], plain.cell_xz), 'the cell cache was written again'
    with open(pkl, 'wb') as f:
        f.write(b'\x80\x05 not a pickle')
    assert navdata.summary(navdata.load(path, cache_dir=cache)) == navdata.summary(plain)
    with open(pkl, 'rb') as f:
        assert f.read(2) == b'\x80\x05' and os.path.getsize(pkl) > 100, 'the whole entry was written again'


def test_cell_cache_used(tmp_path):
    path, cache = made_up(tmp_path), str(tmp_path / 'cache')
    plain = navdata.load(path)
    navdata.load(path, cache_dir=cache)
    for n in entries(cache):
        if n.endswith('.navdata.pkl'):
            os.remove(os.path.join(cache, n))
    npz = next(os.path.join(cache, n) for n in entries(cache) if n.endswith('.npz'))
    with np.load(npz) as z:
        saved = {k: z[k] for k in z.files}
    saved['est'] = saved['est'] + 0.25                  # a marked cell cache: the result shows it was read
    np.savez(npz, **saved)
    nav = navdata.load(path, cache_dir=cache)
    assert np.allclose(nav.cell_y, plain.cell_y + 0.25), 'the cells came from the cell cache'


def test_no_cache_for_a_height_source_without_contents(tmp_path):
    path, cache = made_up(tmp_path), str(tmp_path / 'cache')

    class Flat:
        def height(self, x, z):
            return np.zeros(np.shape(x))
    assert navdata.terrain_digest(Flat()) is None
    navdata.load(path, Flat(), cache_dir=cache)
    assert entries(cache) == [], entries(cache)


def test_failed_cache_write_leaves_nothing(tmp_path):
    target = str(tmp_path / 'deep' / 'entry.npz')

    def broken(fh):
        fh.write(b'part')
        raise OSError('disk full')
    try:
        navdata._write_cache(target, broken)
    except OSError as e:
        assert str(e) == 'disk full'
    else:
        raise AssertionError('the failure was swallowed')
    assert entries(tmp_path / 'deep') == [], 'no entry and no temporary file'
    navdata._write_cache(target, lambda fh: fh.write(b'whole'))
    assert entries(tmp_path / 'deep') == ['entry.npz'] and open(target, 'rb').read() == b'whole'


def cells(*rows):
    """Made-up NavData cells: (i, j, y) per cell, i and j in 0.8 m grid steps."""
    return SimpleNamespace(cell_xz=np.array([[i * navdata.GRID, j * navdata.GRID] for i, j, _ in rows],
                                            float).reshape(-1, 2),
                           cell_y=np.array([y for _, _, y in rows], float))


def test_compare_cells():
    tol = navdiff.STACK_TOL
    assert tol == 4.0
    cand = cells((0, 0, 0.0),                       # one floor each side: the same ground, 10 m apart or not
                 (1, 0, 0.0), (1, 0, 8.0),          # two floors here, one there (0.5): 0.0 pairs, 8.0 does not
                 (2, 0, 0.0),                       # not in the other file
                 (3, 0, 0.0), (3, 0, 4.0),          # 4 m from the other file's floor: pairs (the limit included)
                 (-5, -7, 1.0))                     # negative cell indices
    ref = cells((0, 0, 10.0), (1, 0, 0.5), (3, 0, 0.0), (9, 9, 0.0), (-5, -7, 30.0), (-5, -7, 1.5))
    mine, theirs = navdata.compare_cells(cand, ref)
    assert mine.tolist() == [True, True, False, False, True, True, True], mine
    assert theirs.tolist() == [True, True, True, False, False, True], theirs
    empty = cells()
    mine, theirs = navdata.compare_cells(cand, empty)
    assert not mine.any() and len(theirs) == 0
    just_over = cells((3, 0, 0.0), (3, 0, 4.01))
    mine, _ = navdata.compare_cells(just_over, cells((3, 0, 0.0)))
    assert mine.tolist() == [True, False], mine


def test_check_area():
    area = lambda outline, portals=(): SimpleNamespace(outline=outline, portals=list(portals), border_portals=[])  # noqa: E731
    navdata._check_area('p', 0x101, 0, area([]))
    navdata._check_area('p', 0x101, 0, area([[(0, 0, 0), (navdata.AREA_EXTENT, 0, 0)]]))
    for outline, why in (([[(0, 0, 0), (float('inf'), 0, 0)]], 'not a finite number'),
                         ([[(0, 0, 0), (0, 0, float('nan'))]], 'not a finite number'),
                         ([[(0, 0, 0), (0, 0, navdata.AREA_EXTENT + 0.5)]], 'spans')):
        try:
            navdata._check_area('p', 0x101, 3, area(outline))
        except ValueError as e:
            assert why in str(e) and 'sector 0x0101 area 3' in str(e), e
        else:
            raise AssertionError(f'{outline} passed')
    portal = SimpleNamespace(p0=(0, 0, 0), p1=(500, 0, 0))
    try:
        navdata._check_area('p', 0x101, 1, area([], [portal]))
    except ValueError:
        pass
    else:
        raise AssertionError('a portal 500 m long passed')


def test_summary(tmp_path):
    nav = navdata.load(made_up(tmp_path))
    s = navdata.summary(nav)
    assert s['nodes'] == len(nav.node_pos) == 4 and s['edges'] == 4 and s['sectors'] == 2, s
    assert s['islands'] == len(nav.islands) and s['ladder links'] == 0, s
