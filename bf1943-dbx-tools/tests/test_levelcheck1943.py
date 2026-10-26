r"""DbxEdit's level check with BF1943's rules, on your BF1943 copy (BF1943_GAME overrides; skipped when absent).

  python -B bf1943-dbx-tools\tests\test_levelcheck1943.py

- Every shipped level partition (the five levels) passes: BF1943's rules are chosen from the file, 0 errors, 0 warnings.
- Every level with --bundle (the partitions it refers to, unpacked): every reference into another file resolves and
  holds the class BF1943's schema declares, and the static cull trees match the level.
- 25 broken copies of Wake Island (DbxEdit xml, the XML changed, DbxEdit import), and one of its exclusive cull tree:
  each fails with its rule's message - errors exit 1, warnings exit 0.
- --rules bfbc2 and a LevelData with BFBC2's fields choose BFBC2's rules; fixslots repairs shuffled cull slots byte for
  byte, and --slots-from finds them.

The game copy is only read (through game1943); every file is written to a temporary folder in the suite's work folder
(short, for .NET Framework's 260-character limit), removed afterwards. DbxEdit.exe is built (build.bat) when missing
or older than DbxEdit.cs.
"""
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))
from bfbc2core import dbx, game1943  # noqa: E402

EXE = os.path.join(HERE, 'DbxEdit.exe')
failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def run(*args):
    r = subprocess.run([EXE, *args], capture_output=True, text=True, errors='replace')
    return r.returncode, r.stdout + r.stderr


def build():
    src = [os.path.join(HERE, n) for n in ('DbxEdit.cs', 'DbxEditGUI.cs')]
    if not os.path.isfile(EXE) or os.path.getmtime(EXE) < max(os.path.getmtime(s) for s in src):
        subprocess.run(['cmd', '/c', os.path.join(HERE, 'build.bat')], capture_output=True)


class Xml:
    """A partition's XML from DbxEdit (CRLF, tab-indented), edited as text and imported back with the original's types."""

    def __init__(self, work, original):
        self.work, self.original = work, original
        xml = original[:-4] + '.xml'
        run('xml', original)
        self.text = open(xml, encoding='utf-8', newline='').read()
        os.remove(xml)

    def block(self, guid):
        m = re.search(r'<instance guid="' + re.escape(guid.upper()) + r'"[^\n]*\n.*?</instance>', self.text, re.S)
        if not m:
            raise KeyError(guid)
        return m.start(), m.end()

    def edit(self, guid, old, new, count=1):
        """Replace old by new (count times) inside the instance with that guid; returns the edited text."""
        a, b = self.block(guid)
        part = self.text[a:b]
        if old not in part:
            raise KeyError(f'{old!r} not in instance {guid}')
        return self.text[:a] + part.replace(old, new, count) + self.text[b:]

    def write(self, name, text):
        x = os.path.join(self.work, name + '.xml')
        out = os.path.join(self.work, name + '.dbx')
        with open(x, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)
        code, msg = run('import', x, out, '--like', self.original)
        if code not in (0, 3):
            raise RuntimeError(f'import {name}: {msg}')
        return out


def field(e, name):
    c = e.child(name)
    return None if c is None else dbx.value(c)


def main():
    try:
        game1943.find_game()
    except FileNotFoundError as e:
        print(f'skip: {e}')
        return 0
    build()
    work = os.path.join(game1943.work_root(), 'test-lc1943')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        # every shipped level passes
        for lv in game1943.levels():
            g = game1943.Game(None, lv)
            p = os.path.join(work, lv + '.dbx')
            open(p, 'wb').write(g.data(f'levels/{lv}.dbx'))
            code, out = run('check', p)
            check(code == 0 and '(BF1943 rules, from the file): 0 error(s), 0 warning(s)' in out,
                  f'{lv}: BF1943\'s rules chosen from the file, no error and no warning ({out.strip().splitlines()[-1][-60:]})')

        # every level with the partitions it refers to (--bundle): references into other files, the cull trees
        def unpack(lv, f, g, folder):
            parts = set()

            def refs(e):
                r = e.attr('ref')
                if r and '/' in r:
                    parts.add(r.rsplit('/', 1)[0])
                for c in e.children or ():
                    refs(c)
            refs(f.partition)
            for name in sorted(parts):
                d = g.data(name + '.dbx')
                if d:
                    q = os.path.join(folder, *name.split('/')) + '.dbx'
                    os.makedirs(os.path.dirname(q), exist_ok=True)
                    open(q, 'wb').write(d)
            return parts

        for lv in game1943.levels():
            g = game1943.Game(None, lv)
            p = os.path.join(work, lv + '.dbx')
            folder = os.path.join(work, 'b' if lv == 'wake_island_s' else 'b-' + lv[:4])
            parts = unpack(lv, dbx.read(p), g, folder)
            code, out = run('check', p, '--bundle', folder)
            warns = [ln for ln in out.splitlines() if ln.startswith('WARN')]
            # the game's own data: a few partitions the levels name are in none of their bundles (Wake Island: two minimap meshes)
            check(code == 0 and ' 0 error(s)' in out and all('not in the given folders' in w and 'not checked' not in w for w in warns),
                  f'{lv} with --bundle ({len(parts)} partitions it refers to): references into other files and the cull trees '
                  f'hold ({len(warns)} partitions not in its bundles)' + ('' if code == 0 else '\n       ' + out[-600:]))
        lv = 'wake_island_s'
        g = game1943.Game(None, lv)
        wake = os.path.join(work, lv + '.dbx')
        f = dbx.read(wake)
        bundle = os.path.join(work, 'b')

        # instances to break
        by_type = {}
        for i in f.instances:
            by_type.setdefault(i.attr('type').rsplit('.', 1)[-1], []).append(i)
        gid = lambda e: e.attr('guid')                                          # noqa: E731
        level = by_type['LevelData'][0]
        cps = by_type['CapturePointEntityData']
        base1 = next(c for c in cps if field(c, 'UnableToChangeTeam') == 'true' and field(c, 'InitialOwnerTeam') == 'Team1')
        spawner = next(c for c in cps if c.child('Spawners').children)
        first_spawn = spawner.child('Spawners').children[0].attr('ref')
        soldier = by_type['SoldierSpawnEntityData'][0]
        alternate = by_type['AlternateSpawnEntityData'][0]
        team1 = next(t for t in by_type['TeamEntityData'] if field(t, 'Id') == 'Team1')
        ticket2 = next(t for t in by_type['TicketCounterEntityData'] if field(t, 'TeamId') == 'Team2')
        rigid = by_type['RigidModelGroupEntityData'][0]
        rmembers = [x.attr('ref') for x in rigid.child('MemberDatas').children]
        member2 = f.instance(rmembers[1])
        static_member = by_type['StaticModelGroupMemberData'][0]
        rods = [r for r in by_type['ReferenceObjectData'] if field(r, 'Enumeration') != '0']
        combat = by_type['CombatAreaTriggerEntityData'][0]
        flag_template = cps[0].child('FlagTemplates').children[0].attr('ref')
        marker = spawner.child('MapMarker').attr('ref')
        x = Xml(work, wake)
        same = x.write('same', x.text)
        check(open(same, 'rb').read() == open(wake, 'rb').read(), 'xml and import without a change give the partition back byte for byte')

        def broken(name, text, severity, says, also=(), args=()):
            p = x.write(name, text)
            code, out = run('check', p, *args)
            want = 'ERROR  ' if severity == 'error' else 'WARN   '
            lines = [ln for ln in out.splitlines() if ln.startswith(want) and says in ln]
            ok = lines and (code == 1) == (severity == 'error') and all(a in out for a in also)
            check(bool(ok), f'{name}: {severity} "{says}" (exit {code})' + ('' if ok else '\n       ' + out.strip().replace('\n', '\n       ')[:900]))

        s1, s2 = gid(soldier), gid(by_type['SoldierSpawnEntityData'][1])
        broken('typed-ref', x.edit(gid(spawner), f'ref="{marker}" name="MapMarker"', f'ref="{s1.lower()}" name="MapMarker"'),
               'error', "BF1943's schema declares this field a MapMarkerEntityData reference")
        broken('array-type', x.edit(gid(spawner), f'<item ref="{first_spawn}" />', f'<item ref="{gid(alternate).lower()}" />'),
               'error', 'in every shipped level these items are SpawnEntityDatas',
               also=["BF1943's schema declares the elements of CapturePointEntityData.Spawners SpawnEntityDatas"])
        fp = field(member2, 'FirstPhysicsPart')
        broken('physics-chain', x.edit(gid(member2), f'<field name="FirstPhysicsPart">{fp}</field>', f'<field name="FirstPhysicsPart">{int(fp) + 1}</field>'),
               'error', 'FirstPhysicsPart ' + str(int(fp) + 1) + ', expected ' + fp)
        ic = field(member2, 'InstanceCount')
        broken('instance-count', x.edit(gid(member2), f'<field name="InstanceCount">{ic}</field>', f'<field name="InstanceCount">{int(ic) + 1}</field>'),
               'error', f'InstanceCount {int(ic) + 1}, but {ic} SpawnDatas')
        broken('member-kind', x.edit(gid(rigid), f'<item ref="{rmembers[0]}" />', f'<item ref="{gid(static_member).lower()}" />'),
               'error', 'a RigidModelGroupEntityData holds RigidModelGroupMemberDatas, not a StaticModelGroupMemberData')
        broken('duplicate-guid', x.text.replace(f'<instance guid="{s2}"', f'<instance guid="{s1}"', 1),
               'error', 'another instance has the same GUID')
        broken('unknown-class', x.text.replace(f'<instance guid="{s1}" type="GameSharedResources.SoldierSpawnEntityData"',
                                               f'<instance guid="{s1}" type="GameSharedResources.NoSuchEntityData"', 1),
               'error', 'NoSuchEntityData is not a class of BF1943')
        part_tag = re.search(r'<partition [^>]*>', x.text).group(0)
        broken('primary-instance', x.text.replace(part_tag, part_tag.replace(gid(level), s1), 1), 'error', 'primaryInstance is')
        first_obj = level.child('Objects').children[0].attr('ref')
        broken('null-object', x.edit(gid(level), f'<item ref="{first_obj}" />', '<item ref="null" />'),
               'error', "empty entry in the level's object list")
        broken('teams', x.edit(gid(team1), '<field name="Id">Team1</field>', '<field name="Id">Team2</field>'),
               'warning', 'every shipped level lists TeamNeutral, Team1 and Team2')
        broken('tickets', x.edit(gid(ticket2), '<field name="TeamId">Team2</field>', '<field name="TeamId">Team1</field>'),
               'warning', 'every shipped level has one for Team1 and one for Team2')
        broken('no-base', x.edit(gid(base1), '<field name="UnableToChangeTeam">true</field>', '<field name="UnableToChangeTeam">false</field>'),
               'warning', 'Team1 has no base')
        owner = next(c for c in cps if any(it.attr('ref').lower() == s1.lower() for it in c.child('Spawners').children))
        broken('unowned-spawn', x.edit(gid(owner), f'\t\t\t<item ref="{s1.lower()}" />\r\n', ''),
               'warning', 'no listed capture point has this soldier spawn')
        broken('unlisted-target', x.edit(gid(level), f'\t\t\t<item ref="{marker}" />\r\n', ''),
               'warning', 'which LevelData Objects does not list')
        broken('singleton', x.edit(gid(level), f'\t\t\t<item ref="{gid(combat).lower()}" />\r\n', ''),
               'warning', 'lists 0 CombatAreaTriggerEntityData')
        e0 = field(static_member.child('SpawnDatas').children[1], 'Enumeration')
        e1 = field(static_member.child('SpawnDatas').children[0], 'Enumeration')
        broken('group-slot', x.edit(gid(static_member), f'<field name="Enumeration">{e0}</field>', f'<field name="Enumeration">{e1}</field>'),
               'warning', 'used by 2 model-group or forest instances', also=['are not 1..'])
        r0 = rods[0]
        broken('placed-slot-low', x.edit(gid(r0), f'<field name="Enumeration">{field(r0, "Enumeration")}</field>', '<field name="Enumeration">5</field>'),
               'warning', "placed objects have a model-group instance's slot")
        broken('blueprint-slot', x.edit(gid(r0), f'<field name="Enumeration">{field(r0, "Enumeration")}</field>', '<field name="Enumeration">0</field>'),
               'warning', 'have slot 0 and the others have slots')
        broken('level-name', x.edit(gid(level), '<field name="Name">Levels/Wake_island_s</field>', '<field name="Name">Levels/My_wake</field>'),
               'warning', 'in every shipped level the Name is Levels/<id>')
        # the full schema: members, enum values
        broken('unknown-field', x.edit(gid(level), '<field name="IsTutorial">', '<field name="IsTutorialX">'),
               'warning', 'LevelData has no member IsTutorialX in BF1943\'s executable')
        broken('enum-value', x.edit(gid(team1), '<field name="Id">Team1</field>', '<field name="Id">Team99</field>'),
               'error', '"Team99" is not a value of TeamId')
        # the cull trees (--bundle)
        etree = os.path.join(bundle, 'levels', 'wake_island_s', 'staticculltree_exclusive.dbx')
        t = dbx.read(etree).instances[0]
        eoff = int(field(t, 'EnumOffset'))
        leaves = sum(1 for k in t.child('Nodes').children if int.from_bytes(dbx.numbers(k.child('Right'))[1][0], 'big') >> 31)
        first = min(rods, key=lambda r: int(field(r, 'Enumeration')))
        fe = field(first, 'Enumeration')
        bb = ['--bundle', bundle]
        broken('cull-slot-range', x.edit(gid(first), f'<field name="Enumeration">{fe}</field>', f'<field name="Enumeration">{eoff + leaves + 5}</field>'),
               'error', 'placed objects have a slot outside the exclusive cull tree', args=bb)
        broken('cull-slot-tiling', x.edit(gid(first), f'<field name="Enumeration">{fe}</field>', f'<field name="Enumeration">{int(fe) + 1}</field>'),
               'warning', "placed objects' slots do not cover the exclusive cull tree's leaves", args=bb)
        tr = re.search(r'<complex name="Transform">([^<]*)</complex>', x.text[slice(*x.block(gid(first)))]).group(1)
        v = tr.split('/')
        v[12] = str(float(v[12]) + 500)
        broken('cull-leaf-position', x.edit(gid(first), f'<complex name="Transform">{tr}</complex>', f'<complex name="Transform">{"/".join(v)}</complex>'),
               'warning', "placed objects are away from their slot's cull-tree leaf", args=bb)
        tx = Xml(work, etree)
        tx.original = etree
        b2 = os.path.join(work, 'b2', 'levels', 'wake_island_s')
        os.makedirs(b2)

        def broken_tree(name, text, severity, says):
            moved = tx.write(name, text)
            shutil.move(moved, os.path.join(b2, 'staticculltree_exclusive.dbx'))
            code, out = run('check', wake, '--bundle', os.path.join(work, 'b2'), '--bundle', bundle)
            want = 'ERROR  ' if severity == 'error' else 'WARN   '
            ok = any(ln.startswith(want) and says in ln for ln in out.splitlines()) and (code == 1) == (severity == 'error')
            check(ok, f'{name}: {severity} "{says}" in a broken exclusive cull tree (exit {code})'
                  + ('' if ok else '\n       ' + out.strip().replace('\n', '\n       ')[:900]))

        broken_tree('exclusive-offset', tx.text.replace(f'<field name="EnumOffset">{eoff}</field>', f'<field name="EnumOffset">{eoff - 1}</field>', 1),
                    'error', f"the exclusive cull tree's EnumOffset is {eoff - 1}, not the level's {eoff}")
        # the tree's nodes as text: every complex of the partition is a node, in order
        pieces = tx.text.split('<complex>')
        nodes = t.child('Nodes').children
        right = [int.from_bytes(dbx.numbers(k.child('Right'))[1][0], 'big') for k in nodes]
        at = {r & 0x7fffffff: i for i, r in enumerate(right) if r >> 31}

        def node_edit(i, old_re, new):
            p2 = list(pieces)
            p2[i + 1] = re.sub(old_re, new, p2[i + 1], count=1)
            return '<complex>'.join(p2)
        root_max = re.search(r'<field name="Max">([^<]*)</field>', pieces[1]).group(1)
        broken_tree('cull-inner-max', node_edit(0, r'<field name="Max">[^<]*</field>', f'<field name="Max">{float(root_max) / 2}</field>'),
                    'warning', 'have a Max that is not the larger of their children')
        leaf = at[int(fe) - eoff + 1]                                        # the second leaf of the first (physics) object
        w = re.search(r'<field name="SphereKeyW">([^<]*)</field>', pieces[leaf + 1]).group(1)
        broken_tree('cull-leaf-pair', node_edit(leaf, r'<field name="SphereKeyW">[^<]*</field>', f'<field name="SphereKeyW">{int(w) + 3}</field>'),
                    'warning', 'objects with physics have two leaves that differ')
        broken_tree('cull-leaf-radius', node_edit(leaf, r'<field name="SphereKeyW">[^<]*</field>', '<field name="SphereKeyW">0</field>'),
                    'warning', 'have a radius (SphereKeyW / 4) less than 1/8 m above their Max')
        broken_tree('cull-tree-shape', node_edit(0, r'<field name="Right">[^<]*</field>', '<field name="Right">3</field>'),
                    'error', 'are not one pre-order tree followed by 256 zero nodes')
        # a reference into another file with the wrong class (--bundle)
        deserter = combat.child('DeserterProjectile').attr('ref')
        p = x.write('external-type', x.edit(gid(combat), f'ref="{deserter}" name="DeserterProjectile"', f'ref="{flag_template}" name="DeserterProjectile"'))
        code, out = run('check', p, '--bundle', bundle)
        check(code == 1 and "BF1943's schema declares this field a" in out and 'DeserterProjectile' in out,
              f'external-type: error, a reference into another file with the wrong class (--bundle; exit {code})')

        # rule sets: chosen by --rules, or by BFBC2's LevelData fields
        code, out = run('check', wake, '--rules', 'bfbc2')
        check(code == 0 and '(BFBC2 rules): 0 error(s)' in out, '--rules bfbc2: BFBC2\'s rules')
        p = x.write('bfbc2-fields', x.edit(gid(level), '<field name="Name">', '<field name="MemoryReportGroup">Levels</field>\r\n\t\t<field name="Name">'))
        code, out = run('check', p)
        check('(BFBC2 rules, from the file)' in out, 'a LevelData with BFBC2\'s fields (MemoryReportGroup) gets BFBC2\'s rules')

        # cull slots: swap two placed objects' slots; --slots-from finds them, fixslots restores the file
        a, b = rods[0], rods[1]
        ea, eb = field(a, 'Enumeration'), field(b, 'Enumeration')
        t = x.edit(gid(a), f'<field name="Enumeration">{ea}</field>', f'<field name="Enumeration">{eb}</field>')
        a0, b0 = re.search(r'<instance guid="' + gid(b) + r'"[^\n]*\n.*?</instance>', t, re.S).span()
        t = t[:a0] + t[a0:b0].replace(f'<field name="Enumeration">{eb}</field>', f'<field name="Enumeration">{ea}</field>', 1) + t[b0:]
        p = x.write('swapped', t)
        code, out = run('check', p, '--slots-from', wake)
        check('have another slot than the same object' in out, 'swapped cull slots: --slots-from finds them')
        fixed = os.path.join(work, 'fixed.dbx')
        code, out = run('fixslots', p, fixed, '--from', wake)
        check(code == 0 and open(fixed, 'rb').read() == open(wake, 'rb').read() and '2 slot(s) changed' in out,
              f'fixslots gives Wake Island back byte for byte ({out.strip().splitlines()[0][:70]})')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
