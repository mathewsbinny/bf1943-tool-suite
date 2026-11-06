r"""NavKit (big-endian): navkit.json's stage table - dependencies, cascading drops and older files' refusals

navkitjson1943.STAGES (2026-11-06) in place of each step's own checks and drops: the table's dependencies (every one
a stage, no cycle, what each recording drops); the pull (stale() asks the stages a stage needs first); recording the
build drops the check, the export and every vehicle world - and nothing of the samples, the vehicle collision or
the vehicle samples - as navkit.invalidate('built', 'checked') did (the audit stamps nothing in navkit.json: its
masks carry the SHA-256 of their samples and build); a new 0.8 m pass drops the 0.2 m pass and everything after; a
class's samples drop that class's world only; and a navkit.json of an older NavKit (no preparation digest, no
.hpd SHA-256, no jump links stamp, no pass ids, no export stamp, a world without its SHA-256) gets the refusal it
got before the table, word for word.
"""
import copy
import os

import navkit
import navkitjson1943 as nj
import vehicleclasses1943 as vc

LV = 'made_up_stages'
PREP = 'p' * 64
GAME = 'G:/copy'

# a level every step has stamped (the shape of the real levels' navkit.json)
FULL = {
    'level': LV, 'game': GAME, 'prepared': 't', 'sea_level': 1.0, 'sector_offsets': [0, 0],
    'objects_sha256': {'heightfield.res': 'h'}, 'preparation': PREP,
    'detailed_sectors': ['0a0b'], 'detailed_counts': {'flag': 1},
    'samples': {'0.8': {'preparation': PREP, 'game': GAME, 'id': 'a', 'time': 't'},
                '0.2': {'preparation': PREP, 'game': GAME, 'sectors': ['0a0b'], 'id': 'b', 'time': 't'}},
    'sampled': 't',
    'vehicles': {'collision': {'preparation': PREP, 'game': GAME, 'time': 't'},
                 'samples': {'jeep': {'preparation': PREP}, 'tank': {'preparation': PREP}},
                 'worlds': {'jeep': {'preparation': PREP}, 'plane': {'preparation': PREP}}},
    'built': 't', 'build_from': {'preparation': PREP, 'game': GAME, 'samples_08_id': 'a', 'samples_02_id': 'b',
                                 'hpd_sha256': 'H', 'jump_links_sha256': 'J'},
    'checked': 't ok',
    'exported': {'preparation': PREP, 'built': 't', 'hpd_sha256': 'H', 'sha256': 'S'},
}


def invalidate_at_head(s, *stages):
    """navkit.invalidate as it was before the table (the reference for what a drop removes)."""
    for k in stages:
        s.pop(k, None)
    if 'built' in stages:
        s.pop('build_from', None)
        s.pop('exported', None)
        (s.get('vehicles') or {}).pop('worlds', None)


def test_table_dependencies():
    for stage in nj.STAGES.values():
        for other in stage.needs + stage.drops_with:
            assert other in nj.STAGES, f'{stage.name} names {other}, no stage'
            assert list(nj.STAGES).index(other) < list(nj.STAGES).index(stage.name), 'declared after what it needs'
    assert {name: (s.needs, s.drops_with) for name, s in nj.STAGES.items()} == {
        'prepared': ((), ()),
        'sampled': (('prepared',), ()),
        'detailed': (('prepared',), ('sampled',)),
        'built': (('prepared',), ('sampled', 'detailed')),
        'checked': (('built',), ()),
        'exported': (('built',), ()),
        'vehicle_collision': (('prepared',), ()),
        'vehicle_samples': (('prepared',), ()),
        'vehicle_worlds': (('prepared',), ('built', 'vehicle_samples')),
    }
    assert nj.after('prepared') == [name for name in nj.STAGES if name != 'prepared']
    assert nj.after('sampled') == ['detailed', 'built', 'checked', 'exported', 'vehicle_worlds']
    assert nj.after('detailed') == ['built', 'checked', 'exported', 'vehicle_worlds']
    assert nj.after('built') == ['checked', 'exported', 'vehicle_worlds']
    assert nj.after('checked') == [] and nj.after('exported') == [], 'a check or export run again keeps the rest'
    assert nj.after('vehicle_samples') == ['vehicle_worlds'] and nj.after('vehicle_collision') == []
    # every key has one owner
    keys = [key for stage in nj.STAGES.values() for key in stage.keys]
    assert len(keys) == len(set(keys))


def test_pull():
    st = copy.deepcopy(FULL)
    for stage in nj.STAGES:
        assert nj.is_current(st, stage, 'jeep' if nj.STAGES[stage].per_class else None), stage
    # the stages a stage needs are asked first
    assert nj.stale(dict(st, preparation=None), 'checked') == nj.Stale('prepared', 'missing')
    assert nj.stale(dict(st, build_from=dict(st['build_from'], preparation='q')), 'checked') == nj.Stale('built', 'stale')
    assert nj.stale({k: v for k, v in st.items() if k != 'checked'}, 'checked') == nj.Stale('checked', 'missing')
    assert nj.stale(dict(st, checked='t FAILED'), 'exported') == nj.Stale('exported', 'check')
    assert nj.stale(dict(st, checked='t FAILED'), 'checked') is None, 'a failed check is stamped (check_passed tells)'
    # 'drops with' is not asked: a build is current without the samples' stamps (the steps never checked them)
    assert nj.is_current({k: v for k, v in st.items() if k not in ('samples', 'detailed_sectors')}, 'built')
    # want applies to the asked stage
    assert nj.stale(st, 'vehicle_worlds', 'jeep', want={'preparation': 'q'}) == nj.Stale('vehicle_worlds', 'stale')
    assert nj.stale(st, 'vehicle_worlds', 'tank') == nj.Stale('vehicle_worlds', 'missing')


def test_cascade_build_recorded():
    """Recording the build drops the check, the export and the vehicle worlds, as before (and nothing else)."""
    st, head = copy.deepcopy(FULL), copy.deepcopy(FULL)
    invalidate_at_head(head, 'built', 'checked')
    head.update(built='t2', build_from={'preparation': PREP, 'hpd_sha256': 'H2'})
    nj.record_stage(st, 'built', {'built': 't2', 'build_from': {'preparation': PREP, 'hpd_sha256': 'H2'}})
    assert st == head
    assert 'checked' not in st and 'exported' not in st and 'worlds' not in st['vehicles']
    assert st['vehicles']['collision'] and sorted(st['vehicles']['samples']) == ['jeep', 'tank']
    assert st['samples'] == FULL['samples'] and st['sampled'] == 't'
    # dropping it (navkit.py build before it runs) does the same, without the new stamp
    st, head = copy.deepcopy(FULL), copy.deepcopy(FULL)
    invalidate_at_head(head, 'built', 'checked')
    assert nj.drop_stage(st, 'built') == head
    # navkit.invalidate, kept for its callers, is the table's drop
    st = copy.deepcopy(FULL)
    navkit.invalidate(st, 'built', 'checked')
    assert st == head


def test_cascade_other_stages():
    # a new 0.8 m pass (navkit.py sample): the 0.2 m pass, the build, check, export and worlds go; vehicle samples stay
    st, head = copy.deepcopy(FULL), copy.deepcopy(FULL)
    invalidate_at_head(head, 'sampled', 'built', 'checked', 'detailed_sectors', 'detailed_counts')
    for k in ('0.8', '0.2'):
        head['samples'].pop(k)
    assert nj.drop_stage(st, 'sampled') == head
    # a new 0.2 m pass: the build and after
    st, head = copy.deepcopy(FULL), copy.deepcopy(FULL)
    invalidate_at_head(head, 'built', 'checked')
    head['samples'].pop('0.2')
    assert nj.drop_stage(st, 'detailed') == head
    # the check run again keeps the export and the worlds
    st = copy.deepcopy(FULL)
    nj.record_stage(st, 'checked', {'checked': 't2 ok'})
    assert st == dict(copy.deepcopy(FULL), checked='t2 ok')
    # a class's samples: that class's world only; the collision: nothing else
    st = copy.deepcopy(FULL)
    nj.record_stage(st, 'vehicle_samples', {'vehicles/samples/<class>': {'preparation': PREP, 'new': 1}}, 'jeep')
    assert st['vehicles']['worlds'] == {'plane': {'preparation': PREP}} and st['vehicles']['samples']['jeep']['new'] == 1
    st = copy.deepcopy(FULL)
    nj.drop_stage(st, 'vehicle_collision')
    assert 'collision' not in st['vehicles'] and st['vehicles']['worlds'] == FULL['vehicles']['worlds']
    # a stage records only its own keys
    try:
        nj.record_stage(copy.deepcopy(FULL), 'checked', {'exported': {}})
        raise AssertionError('a stage wrote another stage\'s key')
    except ValueError:
        pass


class Work:
    """A temporary BF1943_WORK with the level's navkit folder, its .hpd, jump links and soldier world."""

    def __init__(self, tmp):
        self.saved = os.environ.get('BF1943_WORK')
        os.environ['BF1943_WORK'] = str(tmp)
        self.p = nj.paths(LV)
        os.makedirs(self.p['navkit'])
        self.hpd, self.jump = self.p['hpd'], os.path.join(self.p['navkit'], 'jump_links.json')
        self.soldier = os.path.join(self.p['navkit'], f'{LV}.rsnv')
        for path, data in ((self.hpd, b'a build'), (self.jump, b'[]'), (self.soldier, b'a soldier world')):
            with open(path, 'wb') as f:
                f.write(data)
        self.st = copy.deepcopy(FULL)
        self.st['build_from'].update(hpd_sha256=nj.sha256(self.hpd), jump_links_sha256=nj.sha256(self.jump))
        self.st['exported'].update(hpd_sha256=nj.sha256(self.hpd), sha256=nj.sha256(self.soldier))

    def close(self):
        if self.saved is None:
            os.environ.pop('BF1943_WORK', None)
        else:
            os.environ['BF1943_WORK'] = self.saved


def message(f, *a):
    try:
        out = f(*a)
    except (nj.NavKitError, vc.StaleError) as e:
        return str(e)
    return out


def test_older_navkit_json(tmp_path):
    """A navkit.json written before a key existed gets the refusal it got before the table (the words are HEAD's)."""
    w = Work(tmp_path)
    try:
        st, hpd, again = w.st, w.hpd, f'build and check again (navkit.py build {LV}; navkit.py check {LV})'
        assert nj.build_refusal(st, LV, hpd) is None
        assert vc.soldier_world(st, LV, w.soldier) == nj.sha256(w.soldier)
        # before 2026-10-30: no preparation digest
        old = {k: v for k, v in st.items() if k != 'preparation'}
        assert nj.build_refusal(old, LV, hpd) == \
            f'{LV}: prepared without a preparation stamp (an older NavKit): run navkit.py all {LV}'
        assert message(vc.inputs, old, LV) == \
            f'{LV}: prepared without a preparation stamp (an older NavKit): prepare again (navkit.py prepare {LV})'
        assert message(navkit.preparation, old, LV) == message(vc.inputs, old, LV)
        assert message(vc.soldier_world, old, LV, w.soldier) == (
            f'{LV}: the soldier build is not of the current preparation: build and export it again '
            f'(navkit.py build {LV}; navexport1943.py {LV})')
        # a build stamped without its .hpd's SHA-256, or without its jump links' (2026-10-30, second review)
        old = dict(st, build_from={k: v for k, v in st['build_from'].items() if k != 'hpd_sha256'})
        assert nj.build_refusal(old, LV, hpd) == f'{LV}: the build on disk is not of the current preparation: {again}'
        old = dict(st, build_from={k: v for k, v in st['build_from'].items() if k != 'jump_links_sha256'})
        assert nj.build_refusal(old, LV, hpd) == \
            f'{LV}: the build is stamped without its jump links (an older NavKit): {again}'
        # passes and builds without ids (before the ids): still current
        old = copy.deepcopy(st)
        for stamp in old['samples'].values():
            stamp.pop('id')
        for k in ('samples_08_id', 'samples_02_id'):
            old['build_from'].pop(k)
        assert nj.build_refusal(old, LV, hpd) is None and nj.is_current(old, 'sampled') and nj.is_current(old, 'detailed')
        # no export stamp, or one without the file's SHA-256
        for old in ({k: v for k, v in st.items() if k != 'exported'},
                    dict(st, exported={k: v for k, v in st['exported'].items() if k != 'sha256'})):
            assert message(vc.soldier_world, old, LV, w.soldier) == \
                f'{LV}: {w.soldier} is not the export of the current build: export again (navexport1943.py {LV})'
            assert vc.soldier_changed(old, LV, nj.sha256(w.soldier)) is not None
        # a vehicle world stamped without its file's SHA-256: the file is not the stamped one ('file', as before)
        world = os.path.join(w.p['navkit'], f'{LV}.jeep.rsnv')
        with open(world, 'wb') as f:
            f.write(b'a jeep world')
        assert nj.stale(st, 'vehicle_worlds', 'jeep', files={'world': world}) == nj.Stale('vehicle_worlds', 'file')
        # no check stamp
        old = {k: v for k, v in st.items() if k != 'checked'}
        assert nj.build_refusal(old, LV, hpd) == f'{LV}: the build is not checked: navkit.py check {LV}'
        assert message(vc.soldier_world, old, LV, w.soldier) == (
            f'{LV}: the soldier build\'s check did not pass (not checked): no vehicle world is built on it '
            f'(navkit.py check {LV})')
        # the files: another .hpd, jump links written again
        with open(w.jump, 'wb') as f:
            f.write(b'[1]')
        assert nj.build_refusal(st, LV, hpd) == (
            f'{LV}: {w.jump} is not the one the build used (written again since, by jumplinks1943.py?): {again}')
        with open(hpd, 'ab') as f:
            f.write(b'!')
        assert nj.build_refusal(st, LV, hpd) == f'{LV}: {hpd} is not the build stamped in navkit.json: {again}'
    finally:
        w.close()
