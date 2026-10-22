"""The Quick Match browser: edits to BF1943's main menu movie (Menu/main_nav.res in menu/main/mainmenu-00.fbrb).

Everything the browser needs already ships in main_nav; these edits reconnect it (details and evidence:
operation-rising-sun/research/AGENT_PROGRESS.md, 2026-10-22):
1. The Quick Match item (char 51 frame 0, four constructions) gets the hidden dev item's shape,
   [label, 'dev', 0, false], so it opens the dev page (char 64) with no online check. Same-size patches.
2. The dev page's frame-0 setup (block 0x20c70) becomes this.gotoAndStop("hg"): it opens on its host list.
3. The host list (char 64 frame 1, block 0x20f2c): literal rows of [label, level] instead of extern.mapList
   (undefined in retail), hostMultiplayerLocal instead of hostMultiplayer, Back to the main menu, a heading,
   and an initial updateList() call.
4. Layout: the list and its selection marker go below the rows, the dev row text moves onto the marker,
   the seven rows are evenly spaced. Same-size PlaceObject patches.
"""
import os
import struct
import sys

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(SUITE, 'bf1943-apt-tools'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
from apt import Apt, Rewriter, op
from fbrb1943 import Bundle

BUNDLE = 'dist/xenon/menu/main/mainmenu-00.fbrb'
ENTRY = 'Menu/main_nav.res'

# Level names from each level's levels/<name>_description.dbx (LevelName = "Levels/<name>"). The island maps
# are Conquest; Coral Sea is the separate air mode (startCoralSea / gm_coralsea in the executable).
LEVELS = [
    ('Wake Island - Conquest', 'Wake_island_s'),
    ('Guadalcanal - Conquest', 'Guadal_Canal'),
    ('Iwo Jima - Conquest', 'Iwo_Jima_s'),
    ('Coral Sea - Air Superiority', 'Coral_sea'),
]


def pool_index(apt, ins, value):
    pool = next(i.args['items'] for i in ins if i.op == 0x88)
    return next(k for k, c in enumerate(pool) if apt.constants[c] == value)


def block_item(apt, owner, start=None):
    hits = [(i, f, s) for o, i, f, s in apt.action_items() if o == owner and (start is None or s == start)]
    if len(hits) != 1:
        raise ValueError(f'{owner}: expected one action block, found {len(hits)} (a different game version?)')
    return hits[0]


def quickmatch_items(apt, rw):
    item, field, start = block_item(apt, 'char 51 frame 0 action')
    ins = apt.walk(start)
    pool, count = None, 0
    for k, i in enumerate(ins):
        if i.op == 0x88:
            pool = i.args['items']
        name = lambda j: apt.constants[pool[ins[j].args['value']]] if ins[j].op == 0xA2 else None
        if i.op == 0x73 and name(k + 1) == 'quickmatch' and name(k + 2) == 'event':
            dev = next(n for n, c in enumerate(pool) if apt.constants[c] == 'dev')
            expected = bytes([0x73, 0xA2, ins[k + 1].args['value'], 0xA2, ins[k + 2].args['value']])
            rw.patch(i.pc, expected, bytes([0x74, 0xB5, 0x00, 0xA2, dev]),
                     "Quick Match item -> [label, 'dev', 0, false]: opens the dev page, no online check")
            count += 1
    if count != 4:
        raise ValueError(f'expected 4 Quick Match item constructions, found {count}')


def dev_page_opens_host_list(apt, rw):
    item, field, start = block_item(apt, 'char 64 frame 0 action', 0x20C70)
    block = [op('PushString', string='hg'), op('PushOne'), op('PushThis'), op('PushString', string='gotoAndStop'),
             op('CallMethodPop'), op('End')]
    rw.repoint(item, field, start, rw.assemble(block), 'dev page frame 0: this.gotoAndStop("hg") instead of the dev menu')


def host_list_is_local(apt, rw):
    item, field, start = block_item(apt, 'char 64 frame 1 action', 0x20F2C)
    ins = rw.block(start)
    pool = next(i.args['items'] for i in ins if i.op == 0x88)
    name = lambda i: apt.constants[pool[i.args['value']]]
    menu_common = lambda: [op('PushValueOfVar', value=pool_index(apt, ins, '_root')),
                           op('GetNamedMember', value=pool_index(apt, ins, 'menuCommon'))]
    out, k, done = [], 0, set()
    while k < len(ins):
        i, nxt = ins[k], ins[k + 1] if k + 1 < len(ins) else None
        if i.op == 0xA2 and name(i) == 'hostMultiplayer':
            out.append(op('PushString', string='hostMultiplayerLocal'))
            done.add('local')
        elif i.op == 0xAE and name(i) == 'extern' and nxt.op == 0xAF and name(nxt) == 'mapList':
            # [[label, level], ...]; InitArray takes the last pushed value as element 0
            for label, level in reversed(LEVELS):
                out += [op('PushString', string=level), op('PushString', string=label), op('PushByte', value=2), op('InitArray')]
            out += [op('PushByte', value=len(LEVELS)), op('InitArray')]
            k += 2
            done.add('rows')
            continue
        elif (i.op == 0xAE and name(i) == 'p' and nxt.op == 0xA2 and name(nxt) == 'currentFrame'
              and ins[k + 2].op == 0xA2 and name(ins[k + 2]) == 'dev' and ins[k + 3].op == 0x4F
              and ins[k + 4].op == 0x8C and ins[k + 4].args['string'] == 'dev'):
            # Back: p._parent.gotoAndStop("start") (p is the page sprite) instead of returning to the dev menu
            back = [op('PushString', string='start'), op('PushOne'), op('PushValueOfVar', value=i.args['value']),
                    op('PushString', string='_parent'), op('GetMember'), op('PushString', string='gotoAndStop'),
                    op('CallMethodPop')]
            replaced = ins[k:k + 5]
            for j in ins:  # anything that pointed into the replaced run now points at its first new instruction
                for key in ('target', 'end'):
                    if any(j.args.get(key) is r for r in replaced):
                        j.args[key] = back[0]
            out += back
            k += 5
            done.add('back')
            continue
        elif i.op == 0xB2 and name(i) == 'startGameArrayMasterUpdates':
            out += [i, op('PushZero'), op('PushString', string='updateList'), op('CallFunctionPop')]
            done.add('initial')
        elif i.op == 0xB2 and name(i) == 'enterMenu':
            # heading as the awards page sets it: _root.menuCommon.setHeading(key, "Black"); setSubHeading("")
            out += [i, op('PushString', string='Black'), op('PushString', string='MENU_TITLE_QUICKMATCH'), op('PushByte', value=2),
                    *menu_common(), op('PushString', string='setHeading'), op('CallMethodPop'),
                    op('PushString', string=''), op('PushOne'), *menu_common(), op('PushString', string='setSubHeading'),
                    op('CallMethodPop')]
            done.add('heading')
        else:
            out.append(i)
        k += 1
    if done != {'local', 'rows', 'back', 'initial', 'heading'}:
        raise ValueError(f'host list block differs from the expected shape: {sorted(done)}')
    kept = {id(i) for i in out}
    for j in out:  # a reference to a removed instruction would silently mis-assemble
        for key in ('target', 'end'):
            if key in j.args and id(j.args[key]) not in kept:
                raise ValueError(f'{j.label} refers to a removed instruction')
    rw.repoint(item, field, start, rw.assemble(out),
               'host list: literal rows, hostMultiplayerLocal, Back to the main menu, heading, initial updateList()')


def placements(apt, char, frame):
    """{instance name: PlaceObject address} on one frame of a sprite."""
    out = {}
    for f, items in apt.frames(apt.characters()[char]):
        for it, kind in items:
            if f == frame and kind == 3:
                for j in range(10, 18):  # the name pointer follows the matrix and colour transform
                    x = apt.u(it + 4 * j)
                    if 0x100 < x < len(apt.apt) and (apt.s(x) or '').isidentifier():
                        out[apt.s(x)] = it
                        break
    return out


def set_float(rw, at, old, new, why):
    rw.patch(at, struct.pack('>f', old), struct.pack('>f', new), why)


def tidy_host_list_layout(apt, rw):
    rows = placements(apt, 64, 1)
    if not all(n in rows for n in [f'hg{k}' for k in range(7)] + ['hgList']):
        raise ValueError('host list placements differ from the expected shape')
    depth = lambda n: apt.u(rows[n] + 8)
    if (depth('hg0'), depth('hgList')) != (1, 15):
        raise ValueError('host list depths differ from the expected shape')
    rw.patch(rows['hg0'] + 8, struct.pack('>I', 1), struct.pack('>I', 15), 'row hg0 above the list (depth 1 -> 15)')
    rw.patch(rows['hgList'] + 8, struct.pack('>I', 15), struct.pack('>I', 1), 'list and its marker below the rows (depth 15 -> 1)')
    for k in range(7):
        at = rows[f'hg{k}'] + 16
        tx, ty = struct.unpack('>2f', apt.apt[at + 16:at + 24])
        set_float(rw, at + 16, tx, -270.0, f'row hg{k} x aligned')
        set_float(rw, at + 20, ty, 65.0 + 19.0 * k, f'row hg{k} y evenly spaced')
    text = placements(apt, 57, 0)['thetxt'] + 16
    ty = struct.unpack('>f', apt.apt[text + 20:text + 24])[0]
    set_float(rw, text + 20, ty, ty - 14.0, 'dev row text up 14 px onto the selection marker (char 57 is only on the dev page)')


def build(bundle_bytes):
    """(new bundle bytes, new main_nav.res bytes, change log) from the original mainmenu-00.fbrb."""
    bundle = Bundle(bundle_bytes)
    res = bundle.data(ENTRY)
    apt = Apt(res)
    if apt.validate()[2]:
        raise ValueError('the original menu movie does not validate')
    rw = Rewriter(apt)
    quickmatch_items(apt, rw)
    dev_page_opens_host_list(apt, rw)
    host_list_is_local(apt, rw)
    tidy_host_list_layout(apt, rw)
    new_res = rw.res(res)
    problems = Apt(new_res).validate()[2]
    if problems:
        raise ValueError(f'the edited menu movie does not validate: {problems[:3]}')
    new_bundle = bundle.build({ENTRY: new_res})[0]
    again = Bundle(new_bundle)
    if again.data(ENTRY) != new_res or any(again.data(e['name']) != bundle.data(e['name'])
                                           for e in bundle.entries if e['name'] != ENTRY):
        raise ValueError('the rebuilt bundle does not read back as intended')
    return new_bundle, new_res, rw.log
