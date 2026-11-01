r"""Xenia kit: the main menu and in-game menu edits on made-up movies

menu_edits.py and igm_edits.py on made-up APT movies inside made-up FbRB bundles (no game data): each movie holds
only the action blocks, at the addresses the edits look for, with the instruction shapes they rewrite (the Quick
Match, Leaderboards and Return to Game Library items, the online login, the host list, the autosave notice, the deploy
screen's init(), FRONTLINE REPORT's Leaderboards row, the SEE GAMER CARD actions and hints) and the placements they
move. Checked: build() rewrites them, the result validates and reads back from the rebuilt bundle; each refusal
(a block not where expected, a shape that differs, a branch into a removed item, placements or depths that differ)
is raised in words.
"""
import struct
import types

import apt as aptmod
import igm_edits
import menu_edits
from apt import op
from fbrb1943 import Bundle, gzip_member

GZIP_HEADER = b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x0b'


# --- a made-up movie ---------------------------------------------------------------------------------------------
class Movie:
    """A .apt laid out by hand: the movie character at 0x10, structures from 0x200 up, action blocks assembled by
    apt.Rewriter at fixed addresses (or after the structures), and its .const table."""

    def __init__(self, size, chars):
        self.a = bytearray(size)
        self.top = 0x200
        self.limit = size
        self.consts = []
        self.chars = [0] * chars
        self.movie_frames = []

    def alloc(self, data, align=4):
        self.top = (self.top + align - 1) & ~(align - 1)
        at = self.top
        self.a[at:at + len(data)] = data
        self.top += len(data)
        assert self.top <= self.limit, 'made-up movie: structures run into the fixed blocks'
        return at

    def string(self, s):
        return self.alloc(s.encode('latin1') + b'\0', 1)

    def words(self, values):
        return self.alloc(b''.join(struct.pack('>I', v) for v in values)) if values else 0

    def const(self, s):
        if s not in self.consts:
            self.consts.append(s)
        return self.consts.index(s)

    def pool(self, names):
        """(ConstantPool instruction, PushConstant-style builder by name)."""
        items = [self.const(n) for n in names]

        def pc(name, label='PushConstant'):
            return op(label, value=names.index(name))
        return op('ConstantPool', items=items), pc

    def block(self, ins, at=None):
        """The instructions assembled at `at` (default: after the structures); returns the address."""
        rw = aptmod.Rewriter(types.SimpleNamespace(apt=b''))
        rw.base = at if at is not None else (self.top + 3) & ~3
        start = rw.assemble(ins)
        assert start == rw.base
        self.a[start:start + len(rw.tail)] = rw.tail
        if at is None:
            self.top = start + len(rw.tail)
        else:
            assert start + len(rw.tail) <= len(self.a), 'made-up movie: a fixed block runs past the end'
            self.used.append((start, start + len(rw.tail)))
        return start

    used = None

    def action(self, address):
        return self.alloc(struct.pack('>II', 1, address))

    def place(self, name, depth, char=0, flags=0, x=0.0, y=0.0):
        r = bytearray(0x40)
        struct.pack_into('>IIII', r, 0, 3, flags, depth, char)
        struct.pack_into('>6f', r, 16, 1.0, 0.0, 0.0, 1.0, x, y)
        if name:
            struct.pack_into('>I', r, 0x34, self.string(name))
        return self.alloc(bytes(r))

    def frame_table(self, frames):
        lists = [self.words(items) for items in frames]
        return self.alloc(b''.join(struct.pack('>II', len(items), p) for items, p in zip(frames, lists)))

    def sprite(self, index, frames):
        table = self.frame_table(frames)
        self.chars[index] = self.alloc(struct.pack('>6I', 5, aptmod.SIGNATURE, len(frames), table, 0, 0))

    def res(self, name='menu'):
        table = self.frame_table(self.movie_frames)
        chars = self.words(self.chars)
        struct.pack_into('>12I', self.a, 0x10, 9, aptmod.SIGNATURE, len(self.movie_frames), table, 0, len(self.chars),
                         chars, 0, 0, 0, 0, 0)
        first = 32
        strings = bytearray()
        entries = bytearray()
        base = first + 8 * len(self.consts)
        for s in self.consts:
            entries += struct.pack('>II', 1, base + len(strings))
            strings += s.encode('latin1') + b'\0'
        const = b'Apt constant file'.ljust(20, b'\0') + struct.pack('>3I', 0x10, len(self.consts), first) + entries + strings
        return bigf([(f'{name}.apt', bytes(self.a)), (f'{name}.const', bytes(const))])


def bigf(files):
    head = 16 + sum(8 + len(n) + 1 for n, _ in files)
    out = bytearray(b'BIGF' + bytes(4) + struct.pack('>I', len(files)) + bytes(4))
    out += bytes(head - 16)
    p, body = 16, bytearray()
    for name, data in files:
        while (head + len(body)) % 4:
            body.append(0)
        struct.pack_into('>II', out, p, head + len(body), len(data))
        out[p + 8:p + 8 + len(name) + 1] = name.encode('ascii') + b'\0'
        p += 8 + len(name) + 1
        body += data
    out += body
    struct.pack_into('<I', out, 4, len(out))
    return bytes(out)


def bundle(entries):
    """A stored (not gzip) FbRB bundle of the entries, as fbrb1943.py reads one."""
    strings, offs = b'', {}
    for s in [n for n, _ in entries] + ['RawFileAsset']:
        if s not in offs:
            offs[s] = len(strings)
            strings += s.encode('ascii') + b'\0'
    d = struct.pack('>II', 2, len(strings)) + strings + struct.pack('>I', len(entries))
    pos = 0
    for name, data in entries:
        d += struct.pack('>6I', offs[name], 0, pos, len(data), len(data), offs['RawFileAsset'])
        pos += len(data)
    d += b'\0' + struct.pack('>I', pos)
    directory = gzip_member(d, GZIP_HEADER)
    return b'FbRB' + struct.pack('>I', len(directory)) + directory + b''.join(x for _, x in entries)


def refused(fn, *args):
    """The ValueError's text, or None when fn(*args) went through."""
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    return None


# --- the main menu (main_nav) ------------------------------------------------------------------------------------
MENU_NAMES = ['dev', 'event', 'quickmatch', 'leaderboard', 'quit', 'MENU_TITLE_QUICKMATCH', '1', 'presenceRequestOnline',
              '_root', 'fscommandProxy', 'menuItems', 'lbl', 'snd', 'x', 'LBL_QUIT']


def main_menu_block(m, quickmatch_runs=4, branch_into_leaderboard=False):
    """char 51 frame 0: one list with a Quick Match, Leaderboards and Return to Game Library item, then lists with one
    Quick Match item each, the first-time online login and its select sound; branches into every replaced run."""
    pool, pc = m.pool(MENU_NAMES)

    def quickmatch():
        return [op('PushTrue'), pc('quickmatch'), pc('event'), pc('MENU_TITLE_QUICKMATCH'), op('PushByte', value=4),
                op('InitArray')]
    first = quickmatch()
    leaderboard = [op('PushTrue'), op('PushZero'), pc('leaderboard'), op('PushData', items=[m.const('lbl')]),
                   pc('x', 'GetNamedMember'), op('PushByte', value=4), op('InitArray')]
    quit_item = [op('PushFalse'), pc('quit'), pc('event'), op('PushData', items=[m.const('LBL_QUIT')]),
                 pc('x', 'GetNamedMember'), op('PushByte', value=4), op('InitArray')]
    count = op('PushByte', value=3)
    lists = [pc('menuItems')] + first + leaderboard + quit_item + [count, op('InitArray'), op('SetVariable')]
    second = quickmatch()
    for run in [second] + [quickmatch() for _ in range(quickmatch_runs - 2)]:
        lists += [pc('menuItems')] + run + [op('PushByte', value=1), op('InitArray'), op('SetVariable')]
    sound = [pc('x', 'PushValueOfVar'), pc('x', 'GetNamedMember'), pc('x', 'GetNamedMember'), op('PushOne'),
             pc('x', 'PushValueOfVar'), pc('x', 'GetNamedMember'), pc('snd', 'CallNamedMethodPop')]
    login = [pc('1'), pc('presenceRequestOnline'), op('PushByte', value=2), pc('_root', 'PushValueOfVar'),
             pc('fscommandProxy', 'CallNamedMethodPop')]
    end = op('End')
    # branches into each run the edit replaces: the Quick Match run, the label, the list's count, the login
    jumps = [op('PushTrue'), op('BranchIfTrue', target=second[0]),
             op('PushTrue'), op('BranchIfTrue', target=first[3]),
             op('PushTrue'), op('BranchIfTrue', target=count),
             op('PushTrue'), op('BranchIfTrue', target=sound[2])]
    if branch_into_leaderboard:
        jumps += [op('PushTrue'), op('BranchIfTrue', target=leaderboard[3])]
    return [pool] + jumps + lists + sound + login + [end]


def host_list_block(m, with_back=True):
    pool, pc = m.pool(['hostMultiplayer', 'extern', 'mapList', 'p', 'currentFrame', 'dev', 'startGameArrayMasterUpdates',
                       'enterMenu', '_root', 'menuCommon'])
    back = [pc('p', 'PushValueOfVar'), pc('currentFrame'), pc('dev'), op('SetMember'), op('GotoLabel', string='dev')]
    local = pc('hostMultiplayer')
    ins = [pool, op('PushTrue'), op('BranchIfTrue', target=back[2] if with_back else local), local,
           pc('extern', 'PushValueOfVar'), pc('mapList', 'GetNamedMember'), op('Pop')]
    ins += back if with_back else []
    ins += [op('PushZero'), pc('startGameArrayMasterUpdates', 'CallNamedMethodPop'),
            op('PushZero'), pc('enterMenu', 'CallNamedMethodPop'), op('End')]
    return ins


def main_nav(quickmatch_runs=4, branch_into_leaderboard=False, with_back=True, rows=7, hg0_depth=1):
    m = Movie(0x22000, 65)
    m.used = []
    m.limit = 0x20000
    menu = m.block(main_menu_block(m, quickmatch_runs, branch_into_leaderboard))
    m.sprite(51, [[m.action(menu)]])
    dev = m.block([op('PushZero'), op('Pop'), op('End')], 0x20C70)
    host = m.block(host_list_block(m, with_back), 0x20F2C)
    hg = [m.place(f'hg{k}', hg0_depth if k == 0 else 2 + k, x=-200.0, y=50.0 + 20 * k) for k in range(rows)]
    m.sprite(64, [[m.action(dev)], [m.action(host)] + hg + [m.place('hgList', 15)]])
    m.sprite(57, [[m.place('thetxt', 3, y=40.0)]])
    m.movie_frames = [[]]
    assert m.used[0][1] <= 0x20F2C, 'the dev page block runs into the host list block'
    return m.res('main_nav')


def main_root(sites=1, at=0x3E10):
    m = Movie(0x4000, 1)
    m.used = []
    m.limit = 0x3000
    pool, pc = m.pool(['extern', 'displayAutoSaveWarning', 'warn'])
    check = []
    for _ in range(sites):
        check += [pc('extern', 'PushValueOfVar'), pc('displayAutoSaveWarning', 'GetNamedMember')]
    after = op('PushZero')
    ins = [pool, op('PushTrue'), op('BranchIfTrue', target=check[1] if check else after)] + check + \
        [op('BranchIfFalse', target=after), op('PushZero'), pc('warn', 'CallNamedMethodPop'), after, op('Pop'), op('End')]
    m.movie_frames = [[m.action(m.block(ins, at))]]
    return m.res('main_root')


def menu_bundle(**kw):
    root = kw.pop('root', None) or main_root()
    return bundle([('Menu/other.res', b'other entry'), (menu_edits.ENTRY, main_nav(**kw)), (menu_edits.ROOT_ENTRY, root)])


def test_menu_build():
    raw = menu_bundle()
    new_bundle, new_res, log, new_root = menu_edits.build(raw)
    again = Bundle(new_bundle)
    assert again.data(menu_edits.ENTRY) == new_res and again.data(menu_edits.ROOT_ENTRY) == new_root
    assert again.data('Menu/other.res') == b'other entry', 'the other entries are kept'
    assert aptmod.Apt(new_res).validate()[2] == [] and aptmod.Apt(new_root).validate()[2] == []
    relocated = [e['why'] for e in log if e['kind'] == 'relocate']
    assert len(relocated) == 4, relocated
    assert '4 lists' in relocated[0] and 'Leaderboards removed (1 lists)' in relocated[0]
    patches = [e for e in log if e['kind'] == 'patch']
    assert len(patches) == 2 + 2 * 7 + 1, f'two depths, x and y of seven rows, the dev text ({len(patches)})'

    apt = aptmod.Apt(new_res)
    items = {o: s for o, _, _, s in apt.action_items()}
    texts = ' '.join(apt.listing(items['char 51 frame 0 action']))
    assert 'quickmatch' not in texts and 'leaderboard' not in texts and "'quit'" not in texts
    assert 'presenceRequestOnline' not in texts and "'LOCAL MATCH'" in texts
    assert "'dev'" in texts, 'Quick Match opens the dev page'
    dev = ' '.join(apt.listing(items['char 64 frame 0 action']))
    assert "'hg'" in dev and "'gotoAndStop'" in dev
    host = ' '.join(apt.listing(items['char 64 frame 1 action']))
    for level in menu_edits.LEVELS:
        assert repr(level[0]) in host and repr(level[1]) in host
    for word in ("'hostMultiplayerLocal'", "'_parent'", "'setHeading'", "'updateList'", "'LOCAL MATCH'"):
        assert word in host, word
    assert 'mapList' not in host
    rows = menu_edits.placements(apt, 64, 1)
    assert apt.u(rows['hg0'] + 8) == 15 and apt.u(rows['hgList'] + 8) == 1
    ys = [struct.unpack('>f', apt.apt[rows[f'hg{k}'] + 36:rows[f'hg{k}'] + 40])[0] for k in range(7)]
    assert ys == [65.0 + 19.0 * k for k in range(7)]
    text = menu_edits.placements(apt, 57, 0)['thetxt']
    assert struct.unpack('>f', apt.apt[text + 36:text + 40])[0] == 26.0
    root = aptmod.Apt(new_root)
    listing = ' '.join(root.listing(next(s for o, _, _, s in root.action_items())))
    assert 'displayAutoSaveWarning' not in listing and 'PushFalse' in listing


def test_menu_refusals():
    why = refused(menu_edits.build, menu_bundle(quickmatch_runs=3))
    assert why and 'differs from the expected shape' in why, why
    why = refused(menu_edits.build, menu_bundle(branch_into_leaderboard=True))
    assert why and 'ends inside a leaderboard item' in why, why
    why = refused(menu_edits.build, menu_bundle(with_back=False))
    assert why and 'host list block differs' in why, why
    why = refused(menu_edits.build, menu_bundle(rows=6))
    assert why and 'placements differ' in why, why
    why = refused(menu_edits.build, menu_bundle(hg0_depth=2))
    assert why and 'depths differ' in why, why
    why = refused(menu_edits.build, menu_bundle(root=main_root(sites=0)))
    assert why and 'expected one displayAutoSaveWarning check, found 0' in why, why
    why = refused(menu_edits.build, menu_bundle(root=main_root(at=0x3E20)))
    assert why and 'not where expected' in why, why


def test_menu_helpers():
    apt = aptmod.Apt(main_nav())
    why = refused(menu_edits.block_item, apt, 'char 99 frame 0 action')
    assert why and 'expected one action block, found 0' in why, why
    why = refused(menu_edits.block_item, apt, 'char 64 frame 1 action', 0x1234)
    assert why and 'found 0' in why, why
    item, field, start = menu_edits.block_item(apt, 'char 64 frame 1 action', 0x20F2C)
    assert field == 4 and start == 0x20F2C and apt.u(item + 4) == start
    ins = aptmod.Rewriter(apt).block(start)
    assert apt.constants[next(i.args['items'] for i in ins if i.op == 0x88)[menu_edits.pool_index(apt, ins, 'menuCommon')]] \
        == 'menuCommon'
    rw = aptmod.Rewriter(apt)
    rows = menu_edits.placements(apt, 64, 1)
    at = rows['hg1'] + 32
    menu_edits.set_float(rw, at, -200.0, -10.0, 'test')
    assert rw.log[-1] == dict(kind='patch', at=at, before=struct.pack('>f', -200.0).hex(),
                              after=struct.pack('>f', -10.0).hex(), why='test')
    assert refused(menu_edits.set_float, rw, at, -200.0, 1.0, 'stale') is not None, 'a value that is not there is refused'


def test_menu_bad_original():
    """A movie that does not validate is refused before anything is edited (each of the two movies)."""
    good_nav, good_root = main_nav(), main_root()
    bad_nav = broken(good_nav)
    bad_root = broken(good_root)
    raw = bundle([(menu_edits.ENTRY, bad_nav), (menu_edits.ROOT_ENTRY, good_root)])
    assert 'the original menu movie does not validate' in (refused(menu_edits.build, raw) or '')
    raw = bundle([(menu_edits.ENTRY, good_nav), (menu_edits.ROOT_ENTRY, bad_root)])
    assert 'the original main_root movie does not validate' in (refused(menu_edits.build, raw) or '')


def broken(res):
    """The movie with one more Action item on the movie's frame 0, at a word that is no instruction."""
    files = aptmod.bigf(res)
    a = bytearray(files[0][1])
    at = len(a)
    a += bytes([0xFF, 0, 0, 0])                                 # an unknown opcode
    item = len(a)
    a += struct.pack('>II', 1, at)
    lst = len(a)
    m = 0x10
    table = struct.unpack_from('>I', a, m + 12)[0]
    n, old = struct.unpack_from('>II', a, table)
    a += b''.join(a[old + 4 * k:old + 4 * k + 4] for k in range(n)) + struct.pack('>I', item)
    struct.pack_into('>II', a, table, n + 1, lst)
    return aptmod.write_bigf([(files[0][0], bytes(a)), files[1]], res)


# --- the in-game menu (igm_mp_cq) --------------------------------------------------------------------------------
def deploy_block(m, inits=1, early_return=False, spawns=1):
    pool, pc = m.pool(['x'])
    end = op('PushUndefined')
    ins = [pool]
    for k in range(inits):
        body = [op('PushZero'), op('Return') if early_return else op('Pop')]
        after = end if k == inits - 1 else op('PushUndefined')
        ins += [op('DefineFunction2', name='init', params=[], regflags=0, end=after)] + body
        if k < inits - 1:
            ins.append(after)
    spawn = [op('GetURL', strings=('FSCommand:doSpawn', '')) for _ in range(spawns)]
    ins += [end, op('Pop'), op('PushTrue'), op('BranchIfTrue', target=spawn[0] if spawn else end)] + spawn + [op('End')]
    return ins


def frontline_block(m, count=7):
    pool, pc = m.pool(['leaderboard', 'menuitem_3', 'm_isTutorial', 'MENU_TITLE_LEADERBOARD', 'menuItems', 'getMenuTxt'])
    size = op('PushByte', value=count)
    tutorial = pc('m_isTutorial', 'GetNamedMember')
    return [pool, op('PushTrue'), op('BranchIfTrue', target=size), op('PushTrue'), op('BranchIfTrue', target=tutorial),
            pc('menuItems'),
            op('PushTrue'), pc('leaderboard'), op('PushThis'), pc('menuitem_3'), op('PushByte', value=3), op('InitArray'),
            size, op('InitArray'), op('SetVariable'),
            pc('menuitem_3'), tutorial, op('Not'), pc('MENU_TITLE_LEADERBOARD'), op('PushByte', value=2), op('InitArray'),
            pc('getMenuTxt'), op('SetVariable'), op('End')]


GAMER_NAMES = ['seeGamerCard', 'BUTTON_SEE_GAMERCARD', 'dice', 'MenuCommon', 'BUTTON_UP', '_root', 'menuCommon',
               'setAction', 'which', 'y', 'txt', 'a', 'BUTTON_A', 'ctx', 'btns', 'push', 'hints']


def gamer_card_block(m, calls, pushed, listed, list_count=3):
    pool, pc = m.pool(GAMER_NAMES)
    ins = [pool]
    for _ in range(calls):
        run = [pc('seeGamerCard'), op('PushThis'), pc('BUTTON_SEE_GAMERCARD'), pc('dice', 'PushValueOfVar'),
               pc('MenuCommon', 'GetNamedMember'), pc('BUTTON_UP', 'GetNamedMember'), op('PushByte', value=4),
               pc('_root', 'PushValueOfVar'), pc('menuCommon', 'GetNamedMember'), pc('setAction', 'CallNamedMethodPop')]
        ins += [op('PushTrue'), op('BranchIfTrue', target=run[3])] + run

    def hint(button, text):
        return [pc('which'), pc(button), pc('txt'), pc(text), op('PushByte', value=2), op('InitObject')]
    for _ in range(pushed):
        ins += hint('y', 'BUTTON_SEE_GAMERCARD') + [op('PushOne'), pc('ctx', 'PushValueOfVar'), pc('btns', 'GetNamedMember'),
                                                   pc('push', 'CallNamedMethodPop')]
    for _ in range(listed):
        ins += [pc('hints')] + hint('y', 'BUTTON_SEE_GAMERCARD') + hint('a', 'BUTTON_A') + hint('a', 'BUTTON_A') + \
            [op('PushByte', value=list_count), op('InitArray'), op('SetVariable')]
    return ins + [op(n) for _ in range(6) for n in ('PushZero', 'Pop')] + [op('End')]


def igm_movie(deploy=None, frontline=None, gamer=None, rows=('menuitem_0', 'menuitem_3', 'menuitem_4', 'menuitem_5',
                                                                'menuitem_6'), face_flags=6, deploy_at=0x2BE50):
    m = Movie(0x32400, 291)
    m.used = []
    m.limit = 0x20000
    deploy = deploy or {}
    frontline = frontline or {}
    gamer = gamer or {}
    blocks = {}
    blocks['frontline'] = m.block(frontline_block(m, **frontline), 0x2B048)
    blocks['deploy'] = m.block(deploy_block(m, **deploy), deploy_at)
    blocks['squad'] = m.block(gamer_card_block(m, gamer.get('squad', 1), 0, 0), 0x2DF38)
    blocks['friends'] = m.block(gamer_card_block(m, 0, 1, 1, gamer.get('list_count', 3)), 0x2F9C8)
    blocks['score1'] = m.block(gamer_card_block(m, 1, 0, 0), 0x316D4)
    blocks['score2'] = m.block(gamer_card_block(m, 1, 0, 0), 0x31DD8)
    for (a0, a1), (b0, _b1) in zip(sorted(m.used), sorted(m.used)[1:]):
        assert a1 <= b0, f'made-up movie: block at 0x{a0:x} runs into 0x{b0:x}'
    m.sprite(57, [[m.action(blocks['friends'])]])
    m.sprite(232, [[m.action(blocks['score1'])]])
    m.sprite(234, [[m.action(blocks['score2'])]])
    m.sprite(290, [[m.place(None, 1, char=262, flags=6), m.place(None, 2, char=267, flags=face_flags)]])
    for c in (262, 267):
        m.chars[c] = m.alloc(struct.pack('>II', 1, 0))       # shapes (not sprites)
    frames = [[] for _ in range(11)]
    frames[0] = [m.place('RIFLEMAN', 30, char=290, flags=6), m.place('teamFlag', 31, char=227, flags=6)]
    frames[5] = [m.action(blocks['frontline'])] + [m.place(n, 10 + k, flags=6, x=100.0, y=200.0 + 31 * k)
                                                  for k, n in enumerate(rows)]
    frames[6] = [m.action(blocks['deploy']), m.place('mapHeading', 20, flags=6), m.place('RIFLEMAN', 21, char=290)]
    frames[10] = [m.action(blocks['squad'])]
    m.movie_frames = frames
    return m.res('igm_mp_cq')


def igm_bundle(**kw):
    return bundle([(igm_edits.ENTRY, igm_movie(**kw)), ('Menu/after.res', b'after')])


def test_igm_build_team_selector():
    new_bundle, new_res, log = igm_edits.build(igm_bundle())
    again = Bundle(new_bundle)
    assert again.data(igm_edits.ENTRY) == new_res and again.data('Menu/after.res') == b'after'
    apt = aptmod.Apt(new_res)
    assert apt.validate()[2] == []
    whys = [e['why'] for e in log]
    assert any('CHOOSE YOUR SIDE window' in w for w in whys) and any('the CHOOSE YOUR SIDE window panel' in w for w in whys)
    assert any('FRONTLINE REPORT: Leaderboards removed' == w for w in whys)
    assert sum('SEE GAMER CARD removed' in w for w in whys) == 4
    assert len(apt.characters()) == 292, 'the window panel is a new character'
    panel = apt.characters()[291]
    assert apt.u(panel) == 5 and len(apt.frames(panel)[0][1]) == len(igm_edits.PANEL)
    deploy = {o: s for o, _, _, s in apt.action_items()}['char movie frame 6 action']
    text = ' '.join(apt.listing(deploy))
    for name in ('rsLeft', 'rsRight', 'rsConfirm', 'rsCheckTeam', 'rsHoldHidden', 'rsKitArt', 'FSCommand:selectTeam',
                 'CHOOSE YOUR SIDE', 'factionSIDs'):
        assert name in text, name
    placed = {apt.s(apt.u(it + 0x34)) for _f, items in apt.frames(apt.movie) for it, k in items if k == 3}
    assert {n for n, *_ in igm_edits.WINDOW} <= placed
    items = {o: s for o, _, _, s in apt.action_items()}
    front = ' '.join(apt.listing(items['char movie frame 5 action']))
    assert 'leaderboard' not in front.replace('MENU_TITLE_LEADERBOARD', '') and 'm_isTutorial' not in front
    for owner in ('char movie frame 10 action', 'char 57 frame 0 action', 'char 232 frame 0 action',
                  'char 234 frame 0 action'):
        assert 'BUTTON_SEE_GAMERCARD' not in ' '.join(apt.listing(items[owner])), owner
    rows = {apt.s(apt.u(it + 0x34)): it for f, its in apt.frames(apt.movie) if f == 5 for it, k in its if k == 3}
    assert struct.unpack('>f', apt.apt[rows['menuitem_3'] + 32:rows['menuitem_3'] + 36])[0] == -2000.0
    assert struct.unpack('>f', apt.apt[rows['menuitem_4'] + 36:rows['menuitem_4'] + 40])[0] == 200.0 + 31 * 2 - 31.0


def test_igm_build_force_team():
    _new_bundle, new_res, log = igm_edits.build(igm_bundle(), 2)
    apt = aptmod.Apt(new_res)
    assert apt.validate()[2] == []
    deploy = {o: s for o, _, _, s in apt.action_items()}['char movie frame 6 action']
    text = apt.listing(deploy)
    assert sum("('FSCommand:selectTeam', '2')" in line for line in text) == 2, 'on init() and before doSpawn'
    assert any('selectTeam(2)' in e['why'] for e in log)
    assert len(apt.characters()) == 291, 'no window in the experiment'


def test_igm_refusals():
    cases = [(dict(deploy=dict(inits=2)), None, 'expected one init() on the deploy screen, found 2'),
             (dict(deploy=dict(inits=2)), 1, 'expected one init() on the deploy screen, found 2'),
             (dict(deploy=dict(early_return=True)), None, 'returns early'),
             (dict(deploy=dict(spawns=2)), 1, 'expected one doSpawn on the deploy screen, found 2'),
             (dict(deploy_at=0x2BE60), None, 'the deploy screen block is not where expected'),
             (dict(frontline=dict(count=6)), None, 'not 7 items long'),
             (dict(gamer=dict(squad=2)), None, 'expected 1 SEE GAMER CARD actions and 0 hints, found [2, 0]'),
             (dict(gamer=dict(list_count=4)), None, 'the button hint list is not 3 long'),
             (dict(face_flags=7), None, 'the card face placement differs')]
    for kw, team, words in cases:
        why = refused(igm_edits.build, igm_bundle(**kw), team)
        assert why and words in why, (kw, why)


def test_igm_block_helpers():
    apt = aptmod.Apt(igm_movie())
    rw = aptmod.Rewriter(apt)
    why = refused(igm_edits.frontline_without_leaderboards, types.SimpleNamespace(action_items=lambda: []), rw)
    assert why and 'FRONTLINE REPORT block is not where expected' in why
    why = refused(igm_edits.no_gamer_card, types.SimpleNamespace(action_items=lambda: []), rw)
    assert why and 'the block is not where expected' in why
    # a branch into the Leaderboards item is refused
    _item, _field, start = next((i, f, s) for o, i, f, s in apt.action_items() if o == 'char movie frame 5 action')
    ins = rw.block(start)
    k = next(k for k, i in enumerate(ins) if i.op == 0x73 and i.args.get('value') is None and ins[k + 2].op == 0x70)
    ins[2].args['target'] = ins[k + 1]
    real_block = rw.block
    rw.block = lambda s: ins
    try:
        why = refused(igm_edits.frontline_without_leaderboards, apt, rw)
    finally:
        rw.block = real_block
    assert why and 'ends inside the Leaderboards item' in why, why
    # remove(): references into the removed run move to the instruction after it
    a, b, c, d = op('PushZero'), op('PushOne'), op('Pop'), op('End')
    jump = op('BranchAlways', target=b)
    seq = [jump, a, b, c, d]
    igm_edits.remove(seq, 1, 3)
    assert seq == [jump, c, d] and jump.args['target'] is c
    # the AS2 builders
    assert [i.label for i in igm_edits.if_else([op('PushTrue')], [op('PushOne')], [op('PushZero')])] == \
        ['PushTrue', 'Not', 'BranchIfTrue', 'PushOne', 'BranchAlways', 'PushUndefined', 'Pop', 'PushZero',
         'PushUndefined', 'Pop']
    assert [i.args.get('strings') for i in igm_edits.fscommand('selectTeam', '1')] == [('FSCommand:selectTeam', '1')]
    functions, start_code, end_code = igm_edits.selector_code()
    assert [n for n, _ in functions] == ['rsLeft', 'rsRight', 'rsConfirm', 'rsCheckTeam', 'rsHoldHidden', 'rsKitArt']
    assert start_code and end_code[-2].label == 'PushUndefined' and end_code[3].label == 'BranchIfTrue'
    # a sprite helper on a movie whose char 290 is not a sprite
    fake = types.SimpleNamespace(apt=bytes(64), characters=lambda: [0] * 290 + [8])
    assert 'char 290 is not a sprite' in (refused(igm_edits.sprite, fake, rw, [[]]) or '')


def test_igm_window_depth_taken():
    """A template placement already at one of the window's depths on the deploy frame is refused."""
    raw = igm_movie()
    apt = aptmod.Apt(raw)
    rw = aptmod.Rewriter(apt)
    frame6 = apt.frames(apt.movie)[6][1]
    placed = next(it for it, k in frame6 if k == 3)
    rw2 = aptmod.Rewriter(apt)
    rw2.patch(placed + 8, struct.pack('>I', apt.u(placed + 8)), struct.pack('>I', igm_edits.WINDOW[0][2]), 'depth')
    taken = aptmod.Apt(rw2.res(raw))
    why = refused(igm_edits.add_window, taken, aptmod.Rewriter(taken))
    assert why and 'differs from the expected shape' in why, why
    igm_edits.add_window(apt, rw)
    assert any('CHOOSE YOUR SIDE window' in e['why'] for e in rw.log)


def test_igm_bad_original():
    raw = bundle([(igm_edits.ENTRY, broken(igm_movie()))])
    assert 'the original in-game menu movie does not validate' in (refused(igm_edits.build, raw) or '')
