r"""Regression checks of DbxEdit, DbxEditGUI and LevelDiff on made-up files (no game data), run by
test_csharp_tools.py (quick tier). Each group is a function check_*(t) taking a Tools object (the exes, a temporary
folder, check()); a scratch copy of older exes can be passed to see the checks fail on the code they guard against.

- reader: DbxEdit refuses exactly the files core dbx.py refuses (every truncated copy of dbx.py's made-up file, numbers
  0 or 1 byte wide, a string index past the table, a string offset past the string area, a container left open, a
  count x size past the file, a varint longer than 64 bits, no root element) with exit 1 (xml, roundtrip) or 2
  (check, edit), and its XML equals dbx.py's to_xml on every file both accept, nesting deeper than 1,100 included;
- nesting: 200,000 nested containers round-trip, and xml, check, edit and LevelDiff refuse them by name (no stack
  overflow); 1,000 (the limit) are edited, checked, imported and compared;
- writes: never into a made-up game folder (BF1943_GAME), however cased, through a junction, by xml, edit, import,
  fixslots or LevelDiff --json; a hard link at the output leaves the file it linked to unchanged; a failed write
  leaves no temporary file; "*.dbx" no longer takes foo.dbxold (whose XML overwrote foo.xml);
- edit commands: setfield keeps a decimal field decimal ("2" is 2.0, not the integer 2), takes "1E-05", integers,
  bools and references, and refuses a value its field cannot hold (exit 1, nothing written); an empty <old> in
  replace or replaceprefix is refused (exit 2); replaceprefix with no match fails (exit 1); drop leaves references
  into other partitions that share the GUID; a command missing its arguments is a usage error (exit 2);
- level check: a 100,000-node cull tree chain is an error, not a crash; a placed object without ReferencedObject
  says the tiling was not checked; a --slots-from file that is not binary is exit 2; fixslots gives a copy of an
  object at the same place slot 0, not the original's slot;
- LevelDiff: partition attributes, other instance attributes and an integer turned decimal are differences; the
  exit code is 2 for a crash (an unwritable --json) and for an option without its value; duplicate GUIDs pair
  in order and instances without a GUID pair with none; --all on 12,000 instances only in A is fast (linear);
- DbxEditGUI (through reflection, no window shown): opening a damaged file keeps the open document; an undo past a
  save marks the document modified; saving over the opened file under another spelling of its path keeps a .bak;
  saving into the game folder is refused; the edit script's commands replay to the window's own bytes.

Second review (each failed on the code of ff18792, 2026-10-30):
- values: setfield and import write a decimal into a decimal field holding 0 (not the integer, a denormal); setfield
  refuses 1.5, 2.0 and 3000000000 in an integer field, takes a hash's unsigned form, refuses a decimal there, says
  NaN instead of "rounded", keeps "-0"; import notes a decimal written into an integer field;
- import xml: 16 attributes, a NUL (raw or &#0;), a second root or garbage after the root, &#xZZ;, &#x110000; and
  &#xD800; are refused with their line (exit 1, nothing written); a value inside the 1,000th container imports back;
- writes2: the game folder through \\localhost\<drive>$ is refused; a 250-character target is written; a target held
  open by another program is explained as in use, not read-only; 32 edits writing into one folder at once all succeed
  (the short temporary names first came from a time-seeded Random and collided across processes; passes on ff18792);
- level2: a null and an other-partition MemberDatas entry are errors;
- LevelDiff (in check_leveldiff): a reference list whose entry's other attribute, entry's value or own attribute
  differs is a change, named, and a list against itself is none;
- gui: the edit script records values holding | or an end space as comments, set's value as DbxEdit reads it; drop
  of a GUID-less instance removes nothing; a decimal 0 edited to 2 in the window is 2.0.
"""
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SUITE, 'core'))
from bfbc2core import dbx  # noqa: E402

CSC = r'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class Tools:
    def __init__(self, exe, ldiff, gui, tmp, check):
        self.exe, self.ldiff, self.gui, self.tmp, self.check = exe, ldiff, gui, tmp, check
        self.env = {**os.environ, 'LOCALAPPDATA': tmp}
        self.env.pop('BF1943_GAME', None)

    def run(self, *args, env=None, tool=None):
        r = subprocess.run([tool or self.exe, *args], capture_output=True, text=True, errors='replace', env=env or self.env,
                           creationflags=NO_WINDOW, timeout=600)
        return r.returncode, r.stdout + r.stderr

    def folder(self, name):
        p = os.path.join(self.tmp, name)
        shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p)
        return p


# ------------------------------------------------------------------------------------------------ made-up files

def varint(v):
    out = bytearray()
    while True:
        b, v = v & 0x7F, v >> 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


class Made:
    """A binary partition built element by element (FORMAT.md sections 1-2)."""

    def __init__(self):
        self.table, self.idx = [''], {'': 0}

    def s(self, x):
        if x not in self.idx:
            self.idx[x] = len(self.table)
            self.table.append(x)
        return self.idx[x]

    def head(self, tag, kind, attrs):
        return varint(self.s(tag)) + bytes([kind << 4 | len(attrs)]) + b''.join(varint(self.s(k)) + varint(self.s(v)) for k, v in attrs)

    def c(self, tag, attrs, children):
        return self.head(tag, 0xA, attrs) + b'\0' + b''.join(children) + b'\0'

    def t(self, tag, attrs, value):
        return self.head(tag, 2, attrs) + varint(self.s(value))

    def ints(self, tag, attrs, values):
        return self.head(tag, 7, attrs) + varint(len(values)) + varint(4) + b''.join(struct.pack('>I', v & 0xFFFFFFFF) for v in values)

    def floats(self, tag, attrs, values):
        return self.head(tag, 7, attrs) + varint(len(values)) + varint(4) + b''.join(struct.pack('>f', v) for v in values)

    def bool(self, tag, attrs, v):
        return self.head(tag, 6, attrs) + bytes([1, v])

    def file(self, body):
        offs, blob = [], b''
        for t in self.table:
            offs.append(len(blob))
            blob += t.encode('utf-8') + b'\0'
        area = 4 * len(self.table) + len(blob)
        return (b'{binary}' + struct.pack('>IIII', 24 + area, 0, area, len(self.table)) + struct.pack(f'>{len(self.table)}I', *offs)
                + blob + body)


P0 = 'P0000000-0000-0000-0000-000000000000'
G1, G2, G3 = (f'A0000000-0000-0000-0000-00000000000{k}' for k in (1, 2, 3))


def thing(scale=2.0, refs=(G2,), partguid=P0, primary=G1, extra_attr=None, count_bits=None):
    """partition { G1 Test.Thing: Scale (decimal), Count (integer), On (bool), Target (reference), Name, List [refs];
    G2 Test.Other }"""
    m = Made()
    attrs = [('guid', G1), ('type', 'Test.Thing')] + ([extra_attr] if extra_attr else [])
    scale_el = m.floats('field', [('name', 'Scale')], [scale]) if count_bits is None else \
        m.head('field', 7, [('name', 'Scale')]) + bytes([1, 4]) + struct.pack('>I', count_bits)
    g1 = m.c('instance', attrs, [scale_el, m.ints('field', [('name', 'Count')], [3]), m.bool('field', [('name', 'On')], 1),
                                 m.t('field', [('ref', G2), ('name', 'Target')], ''), m.t('field', [('name', 'Name')], 'x'),
                                 m.c('array', [('name', 'List')], [m.t('item', [('ref', r)], '') for r in refs])])
    g2 = m.c('instance', [('guid', G2), ('type', 'Test.Other')], [m.ints('field', [('name', 'Count')], [7])])
    return m.file(m.c('partition', [('guid', partguid), ('primaryInstance', primary)], [g1, g2]))


def nested(depth):
    """A partition holding a chain of `depth - 1` nested complex containers (depth containers in all)."""
    m = Made()
    tag = m.s('complex')
    return m.file(m.c('partition', [('guid', P0)], [bytes([tag, 0xA0, 0]) * (depth - 1) + b'\0' * (depth - 1)]))


def field_raw(path, guid, name):
    e = dbx.read(path).instance(guid).child(name)
    return e.raw if e.kind != dbx.TEXT else e.text


def malformed_module():
    spec = importlib.util.spec_from_file_location('test_dbx_malformed', os.path.join(SUITE, 'core', 'tests', 'test_dbx_malformed.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def py_refuses(data):
    try:
        dbx.parse(data)
        return False
    except ValueError:
        return True


# ------------------------------------------------------------------------------------------------ checks

def check_reader(t):
    """DbxEdit's reader against core dbx.py's on the same made-up files."""
    mal = malformed_module()
    good = mal.made_up()
    cases = {f'cut{k:03d}': good[:k] for k in range(25, len(good) + 1)}
    cases.update({
        'long-numbers': mal.made_up(numbers=(100, 4)), 'string-past-table': mal.made_up(text_index=9),
        'left-open': mal.made_up(close=False)[:-1], 'big-table': dbx.MAGIC + bytes(8) + struct.pack('>II', 4, 1000) + bytes(8),
        'width0': mal.made_up(numbers=(2, 0)), 'width1': mal.made_up(numbers=(2, 1)), 'empty-width0': mal.made_up(numbers=(0, 0)),
        'forged': mal.made_up(forged=True), 'good': good,
        'count-wraps': good[:good.index(bytes([2, 0x70]))] + bytes([2, 0x70, 0x81, 0x80, 0x80, 0x80, 0x04, 4]) + bytes(4) + bytes([3, 0x20, 4, 0, 0]),
        'varint-65-bits': good[:good.index(bytes([2, 0x70]))] + bytes([2, 0x70]) + bytes([0x80] * 10) + bytes([1, 4]) + bytes(4) + bytes([0, 0]),
        'no-root': good[:good.index(bytes([1, 0xA0]))] + b'\0',
    })
    deep_n = 1100
    deep = (dbx.MAGIC + bytes(8) + struct.pack('>II', 4 * 2 + 3, 2) + struct.pack('>2I', 0, 1) + b'\0c\0'
            + bytes([1, 0xA0, 0]) * deep_n + bytes(deep_n) + b'\0')
    cases['deep1100'] = deep
    folder = t.folder('reader')
    for name, data in cases.items():
        open(os.path.join(folder, name + '.dbx'), 'wb').write(data)
    code, out = t.run('xml', folder)
    wrong, crashes = [], []
    for name, data in cases.items():
        x = os.path.join(folder, name + '.xml')
        refused = py_refuses(data)
        if os.path.isfile(x) == refused:
            wrong.append(f'{name} (dbx.py {"refuses" if refused else "accepts"})')
        elif not refused and open(x, 'rb').read() != dbx.to_xml(data).encode('utf-8'):
            wrong.append(f'{name}: XML differs from dbx.py\'s')
        for ln in out.splitlines():
            if name + '.dbx:' in ln and ('Index was outside' in ln or 'Exception' in ln):
                crashes.append(ln.strip()[-90:])
    n_refused = sum(py_refuses(d) for d in cases.values())
    t.check(not wrong and not crashes and code == 1,
            f'reader: DbxEdit xml refuses the {n_refused} files core dbx.py refuses and gives its XML on the other {len(cases) - n_refused} '
            f'(exit {code})' + (f': {wrong[:4]} {crashes[:2]}' if wrong or crashes else ''))
    code, out = t.run('roundtrip', folder)
    named = {os.path.basename(ln.split(': ', 1)[0])[:-4] for ln in out.splitlines() if ln.startswith('ERROR ')}
    t.check(named == {n for n, d in cases.items() if py_refuses(d)},
            f'reader: roundtrip refuses the same {n_refused} files ({len(named)}), and only those')
    code, out = t.run('check', os.path.join(folder, 'width0.dbx'))
    t.check(code == 2 and 'size 0' in out, f'reader: check on a damaged file explains it and exits 2 (exit {code})')


def check_nesting(t):
    folder = t.folder('nesting')
    deep = os.path.join(folder, 'deep.dbx')
    open(deep, 'wb').write(nested(200000))
    code, out = t.run('roundtrip', deep)
    t.check(code == 0 and '1 identical' in out, f'nesting: 200,000 nested containers round-trip (exit {code})')
    for args, tool in ((('check', deep), None), (('edit', deep, os.path.join(folder, 'o.dbx'), 'partguid=' + G3), None),
                       (('xml', deep), None), ((deep, deep), t.ldiff)):
        code, out = t.run(*args, tool=tool)
        name = os.path.basename(tool or t.exe) + ' ' + (args[0] if tool is None else '')
        t.check(code in (1, 2) and ('nests containers' in out or 'indentation tabs' in out) and code != 3221225725,
                f'nesting: {name.strip()} refuses 200,000 nested containers by name, without a stack overflow (exit {code})')
    limit = os.path.join(folder, 'limit.dbx')
    open(limit, 'wb').write(nested(1000))
    results = [t.run('check', limit)[0], t.run('edit', limit, os.path.join(folder, 'l2.dbx'), 'dropfield=x')[0],
               t.run('xml', limit)[0], t.run(limit, limit, tool=t.ldiff)[0]]
    results.append(t.run('import', os.path.join(folder, 'limit.xml'), os.path.join(folder, 'l3.dbx'), '--like', limit)[0])
    same = os.path.isfile(os.path.join(folder, 'l3.dbx')) and open(os.path.join(folder, 'l3.dbx'), 'rb').read() == open(limit, 'rb').read()
    t.check(results == [1, 0, 0, 0, 0] and same, f'nesting: 1,000 deep (the limit) is checked, edited, converted, compared and imported back byte for byte {results}')


def check_writes(t):
    root = t.folder('writes')
    game = os.path.join(root, 'Game')
    os.makedirs(os.path.join(game, 'levels', 'sub'))
    env = {**t.env, 'BF1943_GAME': game}
    src = os.path.join(root, 'in.dbx')
    open(src, 'wb').write(thing())
    victim = os.path.join(game, 'levels', 'victim.dbx')
    open(victim, 'wb').write(thing())
    before = open(victim, 'rb').read()

    def refused(args, what, codes, tool=None, target=None):
        code, out = t.run(*args, env=env, tool=tool)
        t.check(code in codes and 'game folder' in out and (target is None or not os.path.exists(target)),
                f'writes: {what} is refused (exit {code})' + ('' if 'game folder' in out else ': ' + out.strip()[-200:]))

    out_in_game = os.path.join(game, 'levels', 'out.dbx')
    refused(('edit', src, out_in_game, 'partguid=' + G3), 'edit into the game folder', (2,), target=out_in_game)
    swapped = out_in_game.swapcase()
    refused(('edit', src, swapped, 'partguid=' + G3), 'edit into the game folder, the path cased otherwise', (2,), target=out_in_game)
    junction = os.path.join(root, 'j')
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', junction, os.path.join(game, 'levels', 'sub')], capture_output=True).returncode == 0
    if made:
        refused(('edit', src, os.path.join(junction, 'o.dbx'), 'partguid=' + G3), 'edit through a junction into the game folder', (2,),
                target=os.path.join(game, 'levels', 'sub', 'o.dbx'))
    else:
        print('skip writes: no junction could be made')
    refused(('xml', victim), 'xml of a file in the game folder (its .xml would go beside it)', (1,), target=victim[:-4] + '.xml')
    refused(('fixslots', src, out_in_game, '--from', src), 'fixslots into the game folder', (2,), target=out_in_game)
    code, out = t.run('xml', src, env=env)
    refused(('import', src[:-4] + '.xml', out_in_game, '--like', src), 'import into the game folder', (1,), target=out_in_game)
    refused((src, src, '--json', os.path.join(game, 'd.json')), 'LevelDiff --json into the game folder', (2,), tool=t.ldiff,
            target=os.path.join(game, 'd.json'))
    t.check(open(victim, 'rb').read() == before and os.listdir(os.path.join(game, 'levels')) == ['sub', 'victim.dbx'],
            'writes: the made-up game folder is unchanged')
    link = os.path.join(root, 'link.dbx')
    os.link(victim, link)
    code, out = t.run('edit', src, link, 'partguid=' + G3, env=env)
    t.check(code == 0 and open(victim, 'rb').read() == before and open(link, 'rb').read() != before,
            f'writes: a hard link at the output to a game file is replaced, the game file left as it was (exit {code})')
    ro = os.path.join(root, 'ro.dbx')
    open(ro, 'wb').write(thing())
    os.chmod(ro, 0o444)
    code, out = t.run('edit', src, ro, 'partguid=' + G3, env=env)
    os.chmod(ro, 0o666)
    leftovers = [f for f in os.listdir(root) if f.endswith('.tmp')]
    t.check(code == 2 and open(ro, 'rb').read() == thing() and not leftovers,
            f'writes: a write that fails (a read-only output) leaves the old file and no temporary file (exit {code}, {leftovers})')
    q = os.path.join(root, 'q')
    os.makedirs(q)
    open(os.path.join(q, 'foo.dbx'), 'wb').write(thing(scale=1.0))
    open(os.path.join(q, 'foo.dbxold'), 'wb').write(thing(scale=7.5))
    code, out = t.run('xml', q)
    xml = open(os.path.join(q, 'foo.xml'), encoding='utf-8').read()
    t.check(code == 0 and 'converted 1 file' in out and '>1</field>' in xml and '7.5' not in xml,
            f'writes: xml of a folder takes foo.dbx only, not foo.dbxold (which overwrote foo.xml) ({out.strip()})')


def check_edit(t):
    folder = t.folder('edit')
    src = os.path.join(folder, 'a.dbx')
    open(src, 'wb').write(thing(refs=(G2, f'levels/other/part/{G2}')))
    out = os.path.join(folder, 'o.dbx')

    def edit(*commands):
        if os.path.exists(out):
            os.remove(out)
        return t.run('edit', src, out, *commands)

    code, msg = edit(f'setfield={G1}|Scale|2')
    t.check(code == 0 and field_raw(out, G1, 'Scale') == bytes([1, 4]) + struct.pack('>f', 2.0),
            f'edit: setfield "2" on a decimal field writes the decimal 2.0, not the integer 2 (exit {code})')
    code, msg = edit(f'setfield={G1}|Scale|1E-05')
    t.check(code == 0 and field_raw(out, G1, 'Scale') == bytes([1, 4]) + struct.pack('>f', 1e-05), f'edit: setfield takes "1E-05" (exit {code})')
    code, msg = edit(f'setfield={G1}|Count|12', f'setfield={G1}|On|false', f'setfield={G1}|Target|{G1}')
    f = dbx.read(out).instance(G1) if code == 0 else None
    t.check(code == 0 and dbx.numbers(f.child('Count')) == (4, [struct.pack('>I', 12)]) and f.child('On').raw == bytes([1, 0])
            and f.child('Target').attr('ref') == G1,
            f'edit: setfield sets an integer, a bool and a reference (the ref attribute) (exit {code})')
    code, msg = edit(f'setfield={G1}|Scale|abc')
    t.check(code == 1 and 'not a number' in msg and not os.path.exists(out), f'edit: setfield refuses a value its field cannot hold, writes nothing (exit {code})')
    for cmd, want, what in (('replaceprefix=|X', 2, 'replaceprefix with an empty <old>'), ('replace=|X', 2, 'replace with an empty <old>'),
                            ('replaceprefix=nothing/|x', 1, 'replaceprefix with no match'), ('replace=x', 2, 'replace without <new>'),
                            ('copyfield=a', 2, 'copyfield without its arguments'), ('set=On', 2, 'set without a value'),
                            ('set=On|maybe', 2, 'set with a value not true, false or 0-255')):
        code, msg = edit(cmd)
        t.check(code == want and not os.path.exists(out) and 'Exception' not in msg and 'damaged' not in msg,
                f'edit: {what} is {"a usage error" if want == 2 else "a failure"} (exit {code}), nothing written' + ('' if code == want else ': ' + msg.strip()[-160:]))
    code, msg = edit(f'drop={G2}')
    refs = [c.attr('ref') for c in dbx.read(out).instance(G1).child('List').children] if code == 0 else None
    t.check(refs == [f'levels/other/part/{G2}'] and dbx.read(out).instance(G1).child('Target').attr('ref') == 'null',
            f'edit: drop removes the local references only, not one into another partition with the same GUID ({refs})')


def check_level(t):
    folder = t.folder('level')
    LG, TG = 'L0000000-0000-0000-0000-000000000000', 'T0000000-0000-0000-0000-000000000000'
    bp = 'levels/t/bp/B0000000-0000-0000-0000-000000000000'
    gid = lambda n: f'R0000000-0000-0000-0000-{n:012d}'  # noqa: E731

    def level(objs, tree=None):
        m = Made()
        lvl = [m.c('array', [('name', 'Objects')], [m.t('item', [('ref', g)], '') for g, *_ in objs])]
        if tree:
            lvl.append(m.t('field', [('ref', tree), ('name', 'ExclusiveStaticCullTree')], ''))
        insts = [m.c('instance', [('guid', LG), ('type', 'Levels.LevelData'), ('id', 't')], lvl)]
        for g, slot, pos, with_ref in objs:
            ch = [m.floats('complex', [('name', 'Transform')], [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, pos[0], pos[1], pos[2], 1]),
                  m.ints('field', [('name', 'Enumeration')], [slot])]
            if with_ref:
                ch.insert(0, m.t('field', [('ref', bp), ('name', 'ReferencedObject')], ''))
            insts.append(m.c('instance', [('guid', g), ('type', 'Entity.ReferenceObjectData')], ch))
        return m.file(m.c('partition', [('guid', P0), ('primaryInstance', LG)], insts))

    def cull_tree(rights):
        m = Made()
        nodes = [m.c('complex', [], [m.ints('field', [('name', 'Right')], [r]), m.floats('field', [('name', 'Max')], [0.0])]) for r in rights]
        inst = m.c('instance', [('guid', TG), ('type', 'World.StaticCullTreeData')],
                   [m.ints('field', [('name', 'EnumOffset')], [0]), m.c('array', [('name', 'Nodes')], nodes)])
        return m.file(m.c('partition', [('guid', P0), ('primaryInstance', TG)], [inst]))

    def put(rel, data):
        p = os.path.join(folder, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(data)
        return p

    theirs = put('theirs.dbx', level([(gid(1), 7, (10, 0, 10), True), (gid(2), 8, (90, 0, 90), True)]))
    mine = put('mine.dbx', level([(gid(1), 7, (10, 0, 10), True), (gid(2), 8, (90, 0, 90), True), (gid(5), 0, (10.02, 0, 10), True)]))
    fixed = os.path.join(folder, 'fixed.dbx')
    code, out = t.run('fixslots', mine, fixed, '--from', theirs)
    slots = {i.attr('guid'): dbx.numbers(i.child('Enumeration'))[1][0] for i in dbx.read(fixed).instances[1:]} if code == 0 else {}
    t.check(slots.get(gid(5)) == bytes(4) and slots.get(gid(1)) == struct.pack('>I', 7),
            f'level: fixslots gives a copy of an object at the same place slot 0, not the original\'s slot (exit {code})')
    put('b/levels/t/tree.dbx', cull_tree([1] * 100000))
    deep = put('deep.dbx', level([], tree='levels/t/tree/' + TG))
    code, out = t.run('check', deep, '--bundle', os.path.join(folder, 'b'))
    t.check(code == 1 and 'not one pre-order tree' in out, f'level: a 100,000-node cull tree chain is an error, not a stack overflow (exit {code})')
    put('c/levels/t/tree.dbx', cull_tree([2, 0x80000001, 0x80000002] + [0] * 256))
    put('c/levels/t/bp.dbx', cull_tree([0]))
    tiling = put('tiling.dbx', level([(gid(1), 1, (0, 0, 0), True), (gid(2), 2, (0, 0, 0), False)], tree='levels/t/tree/' + TG))
    code, out = t.run('check', tiling, '--bundle', os.path.join(folder, 'c'))
    t.check('not checked against the exclusive cull tree\'s leaves: placed object ' + gid(2) + ' has no ReferencedObject' in out
            and 'do not cover' not in out, 'level: a placed object without ReferencedObject: the tiling is not checked, and says why')
    plain = put('plain.txt', b'not a dbx file at all, but long enough to be read')
    code, out = t.run('check', theirs, '--slots-from', plain)
    t.check(code == 2 and 'not a binary dbx' in out, f'level: --slots-from a file that is not binary is exit 2 (exit {code})')


def check_leveldiff(t):
    folder = t.folder('leveldiff')

    def put(name, data):
        p = os.path.join(folder, name)
        open(p, 'wb').write(data)
        return p

    a = put('a.dbx', thing())
    for name, data, says in (('part.dbx', thing(partguid='P9999999-0000-0000-0000-000000000000', primary=G2), '(primaryInstance)'),
                             ('attr.dbx', thing(extra_attr=('exported', 'true')), '(exported)'),
                             ('bits.dbx', thing(count_bits=2), 'Scale: 2  [1 number(s) of 4 bytes 40000000]')):
        code, out = t.run(a, put(name, data), tool=t.ldiff)
        t.check(code == 1 and says in out, f'LevelDiff: {says.split(":")[0]} is a difference (exit {code})' + ('' if says in out else ': ' + out[-300:]))
    code, out = t.run(a, a, '--json', os.path.join(folder, 'no', 'such', 'x.json'), tool=t.ldiff)
    t.check(code == 2, f'LevelDiff: an unwritable --json is exit 2, not 1 (differences) (exit {code})')
    for args in (('--type',), ('--json',), ('--sumary',)):
        code, out = t.run(a, a, *args, tool=t.ldiff)
        t.check(code == 2 and 'usage' in out, f'LevelDiff: {args[0]} {"without its value" if args[0] != "--sumary" else "(unknown)"} is a usage error (exit {code})')
    # duplicate and missing GUIDs
    m = Made()

    def dup(values, guids):
        m2 = Made()
        insts = [m2.c('instance', ([('guid', g)] if g else []) + [('type', 'Test.Thing')], [m2.ints('field', [('name', 'V')], [v])])
                 for g, v in zip(guids, values)]
        return m2.file(m2.c('partition', [('guid', P0)], insts))
    da = put('dup-a.dbx', dup([1, 2, 5], [G1, G1, None]))
    db = put('dup-b.dbx', dup([1, 2, 6], [G1, G1, None]))
    code, out = t.run(da, db, '--summary', tool=t.ldiff)
    t.check(code == 1 and 'same instance in both: 2; changed: 0; only in A: 1; only in B: 1' in out,
            f'LevelDiff: duplicate GUIDs pair in order, instances without a GUID pair with none ({[ln for ln in out.splitlines() if "same" in ln]})')
    # linear listing
    n = 12000
    m = Made()
    big = put('big.dbx', m.file(m.c('partition', [('guid', P0)], [m.c('instance', [('guid', f'I{k:07d}'), ('type', 'Test.Thing')], [])
                                                                   for k in range(n)])))
    small = put('small.dbx', thing())
    t0 = time.time()
    code, out = t.run(big, small, '--all', tool=t.ldiff)
    took = time.time() - t0
    t.check(code == 1 and out.count('Thing  ') >= n and took < 8, f'LevelDiff: --all lists {n} instances only in A in {took:.1f} s (linear)')

    # reference lists: the same references, but an item's other attribute, an item's value or the list's own
    # attribute differ (until 2026-10-31 only the references were compared: "changed: 0", exit 0)
    def reflist(item_attr='1', item_value='', list_attr='a'):
        m2 = Made()
        items = [m2.t('item', [('ref', G2), ('flag', item_attr)], item_value), m2.t('item', [('ref', G3)], '')]
        inst = m2.c('instance', [('guid', G1), ('type', 'Test.Thing')], [m2.c('array', [('name', 'List'), ('tag', list_attr)], items)])
        return m2.file(m2.c('partition', [('guid', P0)], [inst]))
    base = put('rl-a.dbx', reflist())
    for name, data, says in (('rl-attr.dbx', reflist(item_attr='2'), 'List[0] -> ' + G2),
                             ('rl-value.dbx', reflist(item_value='v'), 'List[0] -> ' + G2),
                             ('rl-list.dbx', reflist(list_attr='b'), 'List: (list)')):
        code, out = t.run(base, put(name, data), tool=t.ldiff)
        t.check(code == 1 and says in out and 'changed: 1' in out,
                f'LevelDiff: a reference list whose {name[3:-4]} differs is a change, named ({says}) (exit {code})' + ('' if says in out else ': ' + out[-300:]))
    code, out = t.run(base, base, '--summary', tool=t.ldiff)
    t.check(code == 0 and 'changed: 0' in out, f'LevelDiff: a reference list against itself has no change (exit {code})')


def values_file():
    """G1 Test.Thing: Scale 0.0 (decimal), Pos 1/0/0 (decimals), Count 3 (integer), Id 0x9abcdef0 (a hash), F 1.5."""
    m = Made()
    inst = m.c('instance', [('guid', G1), ('type', 'Test.Thing')], [
        m.floats('field', [('name', 'Scale')], [0.0]), m.floats('field', [('name', 'Pos')], [1.0, 0.0, 0.0]),
        m.ints('field', [('name', 'Count')], [3]), m.ints('field', [('name', 'Id')], [0x9ABCDEF0]),
        m.floats('field', [('name', 'F')], [1.5])])
    return m.file(m.c('partition', [('guid', P0), ('primaryInstance', G1)], [inst]))


def check_values(t):
    """setfield and import type a 4-byte value by its field's kind; a stored 0 says nothing about it."""
    folder = t.folder('values')
    src = os.path.join(folder, 'a.dbx')
    open(src, 'wb').write(values_file())
    out = os.path.join(folder, 'o.dbx')

    def edit(*commands):
        if os.path.exists(out):
            os.remove(out)
        return t.run('edit', src, out, *commands)

    def nums(path, name):
        return dbx.numbers(dbx.read(path).instance(G1).child(name))[1]

    code, msg = edit(f'setfield={G1}|Scale|2', f'setfield={G1}|Pos|1/0/5')
    t.check(code == 0 and nums(out, 'Scale') == [struct.pack('>f', 2.0)] and nums(out, 'Pos')[2] == struct.pack('>f', 5.0),
            f'values: setfield on a decimal field holding 0 writes decimals (2.0, 5.0), not the integers 2 and 5 (denormals) (exit {code})')
    t.run('xml', src)
    x = open(src[:-4] + '.xml', encoding='utf-8').read().replace('name="Scale">0<', 'name="Scale">2<').replace('name="Pos">1/0/0<', 'name="Pos">1/0/5<')
    xp = os.path.join(folder, 'edited.xml')
    open(xp, 'w', encoding='utf-8', newline='').write(x)
    imp = os.path.join(folder, 'imp.dbx')
    code, msg = t.run('import', xp, imp, '--like', src)
    t.check(code == 0 and nums(imp, 'Scale') == [struct.pack('>f', 2.0)] and nums(imp, 'Pos')[2] == struct.pack('>f', 5.0),
            f'values: import of a decimal 0 edited to 2 and 5 writes decimals (exit {code})')
    for value, what in (('1.5', 'a decimal'), ('2.0', 'a decimal written whole'), ('3000000000', 'an integer past its range')):
        code, msg = edit(f'setfield={G1}|Count|{value}')
        t.check(code == 1 and not os.path.exists(out), f'values: setfield refuses {what} ("{value}") in an integer field (exit {code})')
    code, msg = edit(f'setfield={G1}|Id|2596069104')
    t.check(code == 0 and nums(out, 'Id') == [bytes.fromhex('9abcdef0')], f'values: a hash field takes its unsigned form (exit {code})')
    code, msg = edit(f'setfield={G1}|Id|7.5')
    t.check(code == 1, f'values: a hash field refuses a decimal (exit {code})')
    code, msg = edit(f'setfield={G1}|F|NaN')
    t.check(code == 0 and 'NaN' in msg and 'rounded' not in msg, f'values: setfield NaN is written and said so, not called rounded ({msg.strip()[-120:]})')
    code, msg = edit(f'setfield={G1}|F|-0')
    t.check(code == 0 and nums(out, 'F') == [bytes.fromhex('80000000')], f'values: setfield "-0" keeps its sign (exit {code})')
    x = open(src[:-4] + '.xml', encoding='utf-8').read().replace('name="Count">3<', 'name="Count">1.5<')
    open(xp, 'w', encoding='utf-8', newline='').write(x)
    code, msg = t.run('import', xp, imp, '--like', src)
    t.check(code == 0 and 'note:' in msg and "Count]: '1.5' written as a decimal in an integer field" in msg,
            f'values: import notes a decimal written into an integer field ({msg.strip()[-160:]})')


def check_import_xml(t):
    """import refuses XML it cannot write faithfully: 16 attributes, NUL, content after the root, bad character
    references (exit 1, 'import failed: line N'); a value inside the 1,000th container imports."""
    folder = t.folder('importxml')
    head = f'<?xml version="1.0"?>\n<partition guid="{P0}">\n\t<instance guid="{G1}" type="T.X">\n'
    tail = '\t</instance>\n</partition>\n'
    cases = {
        'attrs16': head + '\t\t<field ' + ' '.join(f'a{k}="v{k}"' for k in range(16)) + '>x</field>\n' + tail,
        'nul-ref': head + '\t\t<field name="Name">ab&#0;cd</field>\n' + tail,
        'nul-raw': head + '\t\t<field name="Name">ab\0cd</field>\n' + tail,
        'second-root': head + tail + f'<partition guid="{P0}"><field name="X">y</field></partition>\n',
        'garbage': head + tail + '<<<\n',
        'bad-hex': head + '\t\t<field name="Name">&#xZZ;</field>\n' + tail,
        'past-unicode': head + '\t\t<field name="Name">&#x110000;</field>\n' + tail,
        'surrogate': head + '\t\t<field name="Name">&#xD800;</field>\n' + tail,
    }
    for name, xml in cases.items():
        xp = os.path.join(folder, name + '.xml')
        open(xp, 'w', encoding='utf-8', newline='').write(xml)
        out = os.path.join(folder, name + '.dbx')
        code, msg = t.run('import', xp, out)
        t.check(code == 1 and 'import failed: line' in msg and not os.path.exists(out),
                f'import xml: {name} is refused with its line, nothing written (exit {code})' + ('' if code == 1 else ': ' + msg.strip()[-160:]))
    xp = os.path.join(folder, 'ok15.xml')
    open(xp, 'w', encoding='utf-8', newline='').write(head + '\t\t<field ' + ' '.join(f'a{k}="v{k}"' for k in range(15)) + '>x</field>\n' + tail)
    code, msg = t.run('import', xp, os.path.join(folder, 'ok15.dbx'))
    code2, msg2 = t.run('xml', os.path.join(folder, 'ok15.dbx'))
    t.check(code == 3 and code2 == 0, f'import xml: 15 attributes import and read back (exit {code}, xml {code2})')
    # 1,000 containers with a value in the innermost: edit takes it, so import must too
    m = Made()
    tag = m.s('complex')
    deep = os.path.join(folder, 'deep.dbx')
    open(deep, 'wb').write(m.file(m.c('partition', [('guid', P0)], [bytes([tag, 0xA0, 0]) * 999 + m.t('field', [('name', 'Leaf')], 'v') + b'\0' * 999])))
    t.run('xml', deep)
    back = os.path.join(folder, 'deep2.dbx')
    code, msg = t.run('import', os.path.join(folder, 'deep.xml'), back, '--like', deep)
    t.check(code == 0 and open(back, 'rb').read() == open(deep, 'rb').read(),
            f'import xml: a value inside the 1,000th container imports back byte for byte, as edit takes the file (exit {code})')


def check_writes2(t):
    """the game folder through a loopback share, a target near 260 characters, a target open elsewhere."""
    root = t.folder('writes2')
    game = os.path.join(root, 'Game')
    os.makedirs(os.path.join(game, 'levels'))
    env = {**t.env, 'BF1943_GAME': game}
    src = os.path.join(root, 'in.dbx')
    open(src, 'wb').write(thing())
    drive, rest = os.path.splitdrive(game)
    unc = '\\\\localhost\\' + drive[0] + '$' + rest + '\\levels\\unc.dbx'
    if os.path.isdir(os.path.dirname(unc)):
        code, out = t.run('edit', src, unc, 'partguid=' + G3, env=env)
        t.check(code == 2 and 'game folder' in out and not os.path.exists(os.path.join(game, 'levels', 'unc.dbx')),
                f'writes: the game folder through \\\\localhost\\{drive[0]}$ is refused (exit {code})')
    else:
        print('skip writes: no administrative share \\\\localhost\\' + drive[0] + '$')
    base = os.path.join(root, 'L')
    need = 250 - len(base) - len('\\out.dbx') - 1
    if need > 10:
        deep = base + '\\' + 'a' * need
        os.makedirs('\\\\?\\' + deep)
        target = deep + '\\out.dbx'
        code, out = t.run('edit', src, target, 'partguid=' + G3)
        t.check(code == 0 and os.path.isfile('\\\\?\\' + target), f'writes: a {len(target)}-character target is written (its temporary file fits) (exit {code})')
    else:
        print('skip writes: the temporary folder is too deep for a 250-character target')
    held = os.path.join(root, 'held.dbx')
    open(held, 'wb').write(thing())
    with open(held, 'rb'):
        code, out = t.run('edit', src, held, 'partguid=' + G3)
    t.check(code == 2 and 'in use' in out and 'read-only' not in out,
            f'writes: a target open in another program is explained as in use, not read-only (exit {code})')
    # many writers into one folder at once: their temporary files must not collide (a time-seeded name did)
    par = os.path.join(root, 'par')
    os.makedirs(par)
    procs = [subprocess.Popen([t.exe, 'edit', src, os.path.join(par, f'o{k}.dbx'), 'partguid=' + G3], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, env=t.env, creationflags=NO_WINDOW) for k in range(32)]
    codes = []
    for p in procs:
        p.communicate(timeout=300)
        codes.append(p.returncode)
    t.check(codes == [0] * 32 and sorted(os.listdir(par)) == sorted(f'o{k}.dbx' for k in range(32)),
            f'writes: 32 edits writing into one folder at once all succeed, no temporary file left ({codes.count(0)} of 32)')


def check_level2(t):
    """BF1943's check: a null entry in a model group's MemberDatas is an error."""
    folder = t.folder('level2')
    m = Made()
    lvl = m.c('instance', [('guid', G1), ('type', 'Entity.LevelData'), ('id', 'x')], [m.c('array', [('name', 'Objects')], [m.t('item', [('ref', G2)], '')])])
    grp = m.c('instance', [('guid', G2), ('type', 'Entity.StaticModelGroupEntityData')], [
        m.t('field', [('ref', 'a/b/' + G3), ('name', 'PhysicsData')], ''), m.t('field', [('ref', 'a/b/' + G3), ('name', 'MeshEntityType')], ''),
        m.c('array', [('name', 'MemberDatas')], [m.t('item', [('ref', 'null')], ''), m.t('item', [('ref', 'a/b/' + G3)], '')])])
    p = os.path.join(folder, 'lvl.dbx')
    open(p, 'wb').write(m.file(m.c('partition', [('guid', P0), ('primaryInstance', G1)], [lvl, grp])))
    code, out = t.run('check', p)
    t.check(code == 1 and 'MemberDatas[0]: is null' in out and 'MemberDatas[1]: refers to a/b/' in out,
            f'level: a null and an other-partition entry in MemberDatas are errors (exit {code})')


GUI_HARNESS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

static class Harness
{
    const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

    [STAThread]
    static int Main(string[] a)
    {
        var asm = Assembly.LoadFrom(a[0]);
        string good = a[1], bad = a[2], game = a[3];
        var notices = new List<string>();
        var gp = asm.GetType("Bfbc2Dbx.Gui.GuiProgram");
        gp.GetField("Notice", Any).SetValue(null, (Action<IWin32Window, string, string, bool>)((o, text, title, e) => notices.Add(title)));
        var mw = asm.GetType("Bfbc2Dbx.Gui.MainWindow");
        object w = Activator.CreateInstance(mw, Any, null, new object[] { null }, null);
        Func<string, object[], object> call = (n, args) => mw.GetMethod(n, Any).Invoke(w, args);
        Func<string, object> get = n => { var f = mw.GetField(n, Any); return f != null ? f.GetValue(w) : mw.GetProperty(n, Any).GetValue(w, null); };
        Action edit = () =>
        {
            call("Snapshot", new object[0]);
            var doc = get("doc");
            var ed = asm.GetType("Bfbc2Dbx.Editor");
            object editor = Activator.CreateInstance(ed, Any, null, new[] { doc }, null);
            ed.GetMethod("SetPartitionGuid", Any).Invoke(editor, new object[] { "PEEEEEEE-0000-0000-0000-000000000000" });
        };

        call("Open", new object[] { good });
        object before = get("doc");
        call("Open", new object[] { bad });
        Console.WriteLine("open-damaged-keeps=" + (get("doc") == before && (string)get("path") == good && notices.Count == 1));

        edit();
        call("Save", new object[] { good.ToUpperInvariant() });
        Console.WriteLine("bak-other-spelling=" + File.Exists(good + ".bak"));
        Console.WriteLine("saved-clean=" + !(bool)get("modified"));
        call("Undo", new object[0]);
        Console.WriteLine("undo-past-save-modified=" + (bool)get("modified"));

        notices.Clear();
        call("Save", new object[] { Path.Combine(game, "levels", "saved.dbx") });
        Console.WriteLine("save-game-refused=" + (!File.Exists(Path.Combine(game, "levels", "saved.dbx")) && notices.Count == 1));

        // the edit script's commands for a decimal value, a reference and a two-line text
        var doc2 = get("doc");
        var dt = doc2.GetType();
        var inst = dt.GetMethod("Instance", Any).Invoke(doc2, new object[] { a[4] });
        var children = (System.Collections.IList)inst.GetType().GetField("Children", Any).GetValue(inst);
        var ec = mw.GetMethod("EditCommand", Any);
        Func<string, object> child = name =>
        {
            foreach (var c in children) if ((string)dt.GetMethod("Get", Any).Invoke(doc2, new object[] { c, "name" }) == name) return c;
            return null;
        };
        Console.WriteLine("cmd-value=" + ec.Invoke(null, new object[] { doc2, inst, child("Scale"), new List<string>(), true, "Scale" }));
        Console.WriteLine("cmd-ref=" + ec.Invoke(null, new object[] { doc2, inst, child("Target"), new List<string> { "ref" }, false, "Target" }));
        Console.WriteLine("cmd-bool=" + ec.Invoke(null, new object[] { doc2, inst, child("On"), new List<string>(), true, "On" }));

        // review 2: commands that cannot carry their values, set's value as DbxEdit reads it, drop without a GUID,
        // a decimal field holding 0 edited in the window
        Action<string, Func<object>> say = (key, f) => { object v; try { v = f(); } catch (Exception) { v = "False"; } Console.WriteLine(key + "=" + v); };
        say("script-pipe", () => ((string)mw.GetMethod("ScriptCommand", Any).Invoke(null, new object[] { "replace=", new[] { "a|b", "c" } })).StartsWith("#"));
        say("script-space", () => ((string)mw.GetMethod("ScriptCommand", Any).Invoke(null, new object[] { "replace=", new[] { "a", "c " } })).StartsWith("#"));
        say("script-plain", () => (string)mw.GetMethod("ScriptCommand", Any).Invoke(null, new object[] { "replace=", new[] { "a", "c" } }) == "replace=a|c");
        say("script-set", () => (string)mw.GetMethod("SetCommand", Any).Invoke(null, new object[] { "On", "+7", (byte)7 }) == "set=On|7");
        var dbxType = asm.GetType("Bfbc2Dbx.DbxFile");
        Func<object> zeroFile = () => dbxType.GetMethod("Read", Any).Invoke(null, new object[] { a[5] });
        say("drop-null", () =>
        {
            object zero = zeroFile();
            var ed = asm.GetType("Bfbc2Dbx.Editor");
            object editor = Activator.CreateInstance(ed, Any, null, new[] { zero }, null);
            var args = new object[] { null, 0 };
            int n = (int)ed.GetMethod("Drop", Any, null, new[] { typeof(string), typeof(int).MakeByRefType() }, null).Invoke(editor, args);
            return n == 0;
        });
        say("zero-decimal", () =>
        {
            object zero = zeroFile();
            var zi = dbxType.GetMethod("Instance", Any).Invoke(zero, new object[] { a[4] });
            object scale = null;
            foreach (var c in (System.Collections.IList)zi.GetType().GetField("Children", Any).GetValue(zi))
                if ((string)dbxType.GetMethod("Get", Any).Invoke(zero, new object[] { c, "name" }) == "Scale") scale = c;
            var args = new object[] { zero, scale, "2", null };
            var payload = (byte[])asm.GetType("Bfbc2Dbx.XmlImport").GetMethod("EncodeValue", Any).Invoke(null, args);
            return BitConverter.ToString(payload) == "01-04-40-00-00-00";
        });
        return 0;
    }
}
'''


def check_gui(t):
    folder = t.folder('gui')
    exe_dir = os.path.join(folder, 'bin')
    os.makedirs(exe_dir)
    shutil.copy(t.gui, os.path.join(exe_dir, 'DbxEditGUI.exe'))
    src = os.path.join(exe_dir, 'Harness.cs')
    open(src, 'w', encoding='utf-8', newline='\n').write(GUI_HARNESS)
    harness = os.path.join(exe_dir, 'Harness.exe')
    r = subprocess.run([CSC, '-nologo', '-out:' + harness, '-r:System.Windows.Forms.dll', src], capture_output=True, text=True, errors='replace')
    if r.returncode != 0:
        t.check(False, 'gui: the test harness builds: ' + r.stdout[-300:])
        return
    good = os.path.join(folder, 'good.dbx')
    open(good, 'wb').write(thing(scale=2.0))
    bad = os.path.join(folder, 'bad.dbx')
    open(bad, 'wb').write(thing()[:-3])
    game = os.path.join(folder, 'Game')
    os.makedirs(os.path.join(game, 'levels'))
    env = {**t.env, 'BF1943_GAME': game}
    # a decimal Scale holding 0.0, and two instances without a GUID
    zm = Made()
    zfile = zm.file(zm.c('partition', [('guid', P0)], [
        zm.c('instance', [('guid', G1), ('type', 'Test.Thing')], [zm.floats('field', [('name', 'Scale')], [0.0])]),
        zm.c('instance', [('type', 'Test.Other')], []), zm.c('instance', [('type', 'Test.Other')], [])]))
    zero = os.path.join(folder, 'zero.dbx')
    open(zero, 'wb').write(zfile)
    r = subprocess.run([harness, os.path.join(exe_dir, 'DbxEditGUI.exe'), good, bad, game, G1, zero], capture_output=True, text=True,
                       errors='replace', env=env, timeout=300)
    got = dict(ln.split('=', 1) for ln in r.stdout.splitlines() if '=' in ln)
    for key, what in (('open-damaged-keeps', 'opening a damaged file keeps the open document'),
                      ('bak-other-spelling', 'saving over the opened file under another spelling of its path keeps a .bak'),
                      ('saved-clean', 'a saved document is not modified'),
                      ('undo-past-save-modified', 'an undo past a save marks the document modified'),
                      ('save-game-refused', 'saving into the game folder is refused, with a message')):
        t.check(got.get(key) == 'True', f'gui: {what}' + ('' if key in got else ': ' + (r.stdout + r.stderr).strip()[-300:]))
    want = {'cmd-value': f'setfield={G1}|Scale|2', 'cmd-ref': f'setfield={G1}|Target|{G2}', 'cmd-bool': f'setfield={G1}|On|true'}
    t.check(all(got.get(k) == v for k, v in want.items()), f'gui: the edit script records setfield for a value, a reference and a bool {[got.get(k) for k in want]}')
    # replayed, the decimal stays a decimal: the bytes the window writes (EncodeValue keeps the stored type)
    out = os.path.join(folder, 'replayed.dbx')
    code, msg = t.run('edit', good, out, want['cmd-value'], f'setfield={G1}|Target|{G1}')
    t.check(code == 0 and field_raw(out, G1, 'Scale') == bytes([1, 4]) + struct.pack('>f', 2.0),
            f'gui: the recorded setfield replays to the window\'s bytes (a decimal 2.0) (exit {code})')
    for key, what in (('script-pipe', 'a value holding | is recorded as a comment, not a command that splits it'),
                      ('script-space', 'a value with a space at an end (the script reader trims lines) is recorded as a comment'),
                      ('script-plain', 'a plain replace is recorded as the command'),
                      ('script-set', 'set everywhere with "+7" records set=On|7, which DbxEdit reads (it refuses "+7")'),
                      ('drop-null', 'drop of an instance without a GUID removes nothing (it took every GUID-less instance)'),
                      ('zero-decimal', 'a decimal field holding 0.0 edited to "2" in the window becomes 2.0, not the integer 2')):
        t.check(got.get(key) == 'True', f'gui: {what}' + ('' if key in got else ': ' + (r.stdout + r.stderr).strip()[-300:]))


ALL = (check_reader, check_nesting, check_writes, check_edit, check_level, check_leveldiff, check_values, check_import_xml,
       check_writes2, check_level2, check_gui)
