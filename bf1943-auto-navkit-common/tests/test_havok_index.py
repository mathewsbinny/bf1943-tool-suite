r"""NavKit (both variants): collision index

levelobjects.havok_index on a folder built here (no game data): a blueprint's packfile `<name>_Havok.res` and one
named `<name>_Havok_Havok.res` (sp_009's animated aircraft door) are both found under the blueprint's name; the first
base wins for a name in two bases.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import levelobjects


def test_havok_index(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    files = [a / 'Objects' / 'Crate' / 'Crate_Havok.res',
             a / 'Objects' / 'Plane' / 'Door_Animate_Havok_Havok.res',
             b / 'Objects' / 'Crate' / 'Crate_Havok.res']
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'')
    idx = levelobjects.havok_index([str(a), str(b)])
    assert idx.get('objects/crate/crate') == str(files[0]), 'a <name>_Havok.res packfile is found under the blueprint'
    assert idx.get('objects/plane/door_animate') == str(files[1]), \
        'a <name>_Havok_Havok.res packfile is found under the blueprint'
    assert idx.get('objects/plane/door_animate_havok') == str(files[1]), '... and still under its literal name'
