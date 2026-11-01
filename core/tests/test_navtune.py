r"""Core: navigation side files (navtune.py) - operations, refusals, undo/redo, save and load

Operations are built, validated (each refusal's message), added, undone and redone; a side file round-trips through
save and load, a changed base is noticed, and load refuses another format, a newer version and invalid operations.
"""
import json

from bfbc2core import navtune


def test_op_rounds_positions_and_records_author():
    o = navtune.op('seed', by='tester', when='2026-10-31T10:00:00', at=(1.23456, 2.0, -3.00049))
    assert o == {'op': 'seed', 'at': [1.235, 2.0, -3.0], 'by': 'tester', 'when': '2026-10-31T10:00:00'}
    auto = navtune.op('note', at=(0, 0, 0), text='x')
    assert auto['by'] == navtune.author() and auto['by']
    assert len(auto['when']) == 19 and auto['when'][10] == 'T', 'ISO time to the second'


def test_region_helpers():
    assert navtune.box(5, 1, 7, 2, 4, 3) == {'box': [2.0, 1.0, 3.0, 5.0, 4.0, 7.0]}, 'corners sorted per axis'
    p = navtune.polygon([(0, 0), (1.00049, 0), (0, 1)], -1, 2)
    assert p == {'polygon': [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], 'y': [-1.0, 2.0]}


def test_validate_good_operations():
    ok = [
        navtune.op('walkable', region=navtune.box(0, 0, 0, 1, 1, 1)),
        navtune.op('unwalkable', region=navtune.polygon([(0, 0), (1, 0), (0, 1)], 0, 1)),
        navtune.op('link-areas', a=(0, 0, 0), b=(1, 1, 1), **{'two-way': False}),
        navtune.op('route-edge', a=(0, 0, 0), b=(1, 1, 1), action='remove'),
        navtune.op('traversal', kind='ladder', bottom=(0, 0, 0), top=(0, 5, 0)),
        navtune.op('note', at=(0, 0, 0), text='a note'),
    ]
    for o in ok:
        assert navtune.validate(o) == [], o


def test_validate_refusals():
    v = navtune.validate
    assert v({'op': 'fly'}) == ["unknown operation 'fly'"]
    assert v({'op': 'note', 'at': [0, 0, 0]}) == ['note: text missing', 'note: text is empty']
    assert v(navtune.op('seed', at=(0, 0, 0)), kind='dice') == \
        ['seed: not allowed on a DICE base (region edits need a NavKit base)']
    assert v({'op': 'seed', 'at': [0, 0]}) == ['seed: at must be [x, y, z]']
    assert v({'op': 'seed', 'at': [0, 'a', 0]}) == ['seed: at must be [x, y, z]']
    assert v({'op': 'walkable', 'region': {'polygon': [[0, 0], [1, 1]], 'y': [0, 1]}}) == \
        ['walkable: a polygon needs 3 or more [x, z] points']
    assert v({'op': 'walkable', 'region': {'polygon': [[0, 0], [1, 1], [2]], 'y': [2, 1]}}) == \
        ['walkable: a polygon needs 3 or more [x, z] points', 'walkable: y must be [low, high]']
    assert v({'op': 'walkable', 'region': {'polygon': [[0, 0], [1, 1], [1, 0]]}}) == ['walkable: y must be [low, high]']
    assert v({'op': 'walkable', 'region': {'box': [0, 0, 5, 1, 1, 1]}}) == \
        ['walkable: box must be [x0, y0, z0, x1, y1, z1] with each low <= high']
    assert v({'op': 'walkable', 'region': {'box': [0, 0, 0]}}) == \
        ['walkable: box must be [x0, y0, z0, x1, y1, z1] with each low <= high']
    assert v({'op': 'walkable', 'region': {'circle': 1}}) == ['walkable: region must be a polygon or a box']
    assert v({'op': 'link-areas', 'a': [0, 0, 0], 'b': [1, 1, 1], 'two-way': 'yes'}) == \
        ['link-areas: two-way must be true or false']
    assert v({'op': 'route-edge', 'a': [0, 0, 0], 'b': [1, 1, 1], 'action': 'flip'}) == \
        ['route-edge: action must be add or remove']
    assert v({'op': 'traversal', 'kind': 'swim', 'bottom': [0, 0, 0], 'top': [0, 1, 0]}) == \
        ['traversal: kind must be one of ladder, jump, vault, drop']
    assert v({'op': 'note', 'at': [0, 0, 0], 'text': '   '}) == ['note: text is empty']


def base_file(tmp_path, data=b'navigation'):
    p = tmp_path / 'level.hpd'
    p.write_bytes(data)
    return str(p)


def test_new_and_kind(tmp_path):
    base = base_file(tmp_path)
    t = navtune.NavTune.new('Levels/MP_001', base)
    assert t.kind == 'navkit' and t.base['file'] == 'level.hpd' and len(t.base['sha1']) == 40
    assert t.allowed('walkable') and not t.dirty
    d = navtune.NavTune.new('Levels/MP_001', base, kind='dice')
    assert d.kind == 'dice' and not d.allowed('walkable') and d.allowed('note')
    try:
        navtune.NavTune.new('x', base, kind='other')
    except ValueError as e:
        assert 'base kind must be one of' in str(e)
    else:
        raise AssertionError('an unknown base kind was accepted')
    assert navtune.NavTune('x', {}).kind == 'navkit', 'no kind recorded: a NavKit base'


def test_add_undo_redo_remove(tmp_path):
    t = navtune.NavTune.new('lv', base_file(tmp_path), kind='dice')
    try:
        t.add(navtune.op('walkable', region=navtune.box(0, 0, 0, 1, 1, 1)))
    except ValueError as e:
        assert 'not allowed on a DICE base' in str(e)
    else:
        raise AssertionError('a region edit was added on a DICE base')
    assert not t.operations and not t.dirty
    a = t.add(navtune.op('note', at=(0, 0, 0), text='one'))
    b = t.add(navtune.op('note', at=(1, 0, 0), text='two'))
    assert t.dirty and t.operations == [a, b]
    assert t.undo() is b and t.operations == [a] and t.redo_stack == [b]
    assert t.redo() is b and t.operations == [a, b] and t.redo() is None
    t.undo()
    t.add(navtune.op('note', at=(2, 0, 0), text='three'))
    assert t.redo_stack == [], 'a new edit clears the redo stack'
    t.undo()
    assert t.remove(0) is a and t.redo_stack == [] and t.operations == []
    assert t.undo() is None


def test_save_load_round_trip(tmp_path):
    base = base_file(tmp_path)
    t = navtune.NavTune.new('lv', base)
    t.add(navtune.op('unwalkable', by='me', when='w', region=navtune.box(0, 0, 0, 2, 3, 4)))
    t.add(navtune.op('traversal', by='me', when='w', kind='jump', bottom=(0, 0, 0), top=(1, 2, 3)))
    t.add(navtune.op('route-edge', by='me', when='w', a=(0, 0, 0), b=(1, 0, 0), action='add'))
    t.add(navtune.op('unwalkable', by='me', when='w', region=navtune.polygon([(0, 0), (1, 0), (0, 1)], 0, 1.5)))
    t.add(navtune.op('note', by='me', when='w', at=(1, 2, 3), text='hi'))
    t.add(navtune.op('link-areas', by='me', when='w', a=(0, 0, 0), b=(1, 0, 0)))
    path = str(tmp_path / 'sub' / 'lv.navtune.json')
    t.save(path)
    assert not t.dirty
    raw = open(path, 'rb').read()
    assert raw.endswith(b'\n') and b'\r\n' not in raw
    u = navtune.load(path)
    assert u.level == 'lv' and u.base == t.base and u.operations == t.operations
    assert u.summary(0) == 'unwalkable: box 2.0 x 3.0 x 4.0 m  [me, w]'
    assert u.summary(1) == 'traversal: jump (two-way) (0.0, 0.0, 0.0) -> (1.0, 2.0, 3.0)  [me, w]'
    assert u.summary(2) == 'route-edge: (0.0, 0.0, 0.0) -> (1.0, 0.0, 0.0) (add)  [me, w]'
    assert u.summary(3) == 'unwalkable: polygon of 3 points, y 0.0..1.5  [me, w]'
    assert u.summary(4) == 'note: (1.0, 2.0, 3.0): hi  [me, w]'
    assert u.summary(5) == 'link-areas: (0.0, 0.0, 0.0) -> (1.0, 0.0, 0.0)  [me, w]'
    assert not u.base_changed(base)
    open(base, 'wb').write(b'regenerated')
    assert u.base_changed(base)
    assert not navtune.NavTune('lv', {'kind': 'navkit'}).base_changed(base), 'no recorded hash: not changed'


def test_load_refusals(tmp_path):
    def write(d):
        p = tmp_path / 'x.json'
        p.write_text(json.dumps(d), encoding='utf-8')
        return str(p)

    def refusal(d):
        try:
            navtune.load(write(d))
        except ValueError as e:
            return str(e)
        return None
    assert 'not a navtune side file' in refusal({'format': 'other'})
    assert 'version 2 is newer than this tool reads (1)' in refusal({'format': navtune.FORMAT, 'version': 2})
    bad = refusal({'format': navtune.FORMAT, 'version': 1, 'base': {'kind': 'dice'},
                   'operations': [{'op': 'seed', 'at': [0, 0, 0]}]})
    assert 'operation 0: seed: not allowed on a DICE base' in bad
    assert refusal({'format': navtune.FORMAT}) is None, 'a file without version or operations loads'


def test_default_path():
    p = navtune.default_path('root', 'lv')
    assert p.replace('\\', '/') == 'root/work/navkit/lv/lv.navtune.json'
