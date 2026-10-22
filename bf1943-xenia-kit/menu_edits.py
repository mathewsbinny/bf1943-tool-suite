"""The Quick Match browser: edits to BF1943's main menu movie (Menu/main_nav.res in menu/main/mainmenu-00.fbrb).

Everything the browser needs already ships in main_nav; these edits reconnect it (details and evidence:
operation-rising-sun/research/AGENT_PROGRESS.md, 2026-10-22):
1. Main menu (char 51 frame 0): the Quick Match item gets the hidden dev item's shape, [label, 'dev', 0,
   false], so it opens the dev page (char 64) with no online check; it is labelled LOCAL MATCH; the
   Leaderboards item is removed.
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
ROOT_ENTRY = 'Menu/main_root.res'

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


TITLE = 'LOCAL MATCH'


def main_menu(apt, rw):
    """The main menu's item lists (char 51 frame 0), relocated:
    - Quick Match items [label, 'event', 'quickmatch', true] become [label, 'dev', 0, false] (open the browser);
    - each Leaderboards item [label, 0, 'leaderboard', true] is dropped and its list's count lowered;
    - the label key MENU_TITLE_QUICKMATCH becomes the text LOCAL MATCH (menu labels show plain text as is,
      as the dev item's '* DEV *' does);
    - the first time the menu opens it logged in to EA Online by itself (presenceRequestOnline "1", when
      !_root.hasConnectedOnce && !isPresenceConnected && extern.canEnterMultiplayer): that request is dropped, so the
      menu opens without a connection check (the online items still report their failure when chosen)."""
    item, field, start = block_item(apt, 'char 51 frame 0 action')
    ins = rw.block(start)
    pool = next(i.args['items'] for i in ins if i.op == 0x88)
    name = lambda i: apt.constants[pool[i.args['value']]] if i.op in (0xA2, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3) else None
    dev = next(n for n, c in enumerate(pool) if apt.constants[c] == 'dev')
    out, counts, k = [], dict(quickmatch=0, leaderboard=0, label=0, online=0), 0
    pending_lists = 0  # lists that lost an item and still need their count lowered
    referenced = {id(j.args.get(key)) for j in ins for key in ('target', 'end') if key in j.args}
    while k < len(ins):
        i = ins[k]
        if i.op == 0x73 and name(ins[k + 1]) == 'quickmatch' and name(ins[k + 2]) == 'event':
            out += [op('PushFalse'), op('PushByte', value=0), op('PushConstant', value=dev)]
            for j in ins:  # keep any reference to the replaced run valid
                for key in ('target', 'end'):
                    if any(j.args.get(key) is r for r in ins[k:k + 3]):
                        j.args[key] = out[-3]
            counts['quickmatch'] += 1
            k += 3
            continue
        if (i.op in (0x73, 0x74) and ins[k + 1].op == 0x59 and name(ins[k + 2]) == 'leaderboard'
                and ins[k + 3].op == 0x96 and ins[k + 4].op == 0xAF and ins[k + 5].op == 0xB5
                and ins[k + 5].args['value'] == 4 and ins[k + 6].op == 0x42):
            if any(id(r) in referenced for r in ins[k:k + 7]):
                raise ValueError('a branch or function ends inside a Leaderboards item')
            counts['leaderboard'] += 1
            pending_lists += 1
            k += 7
            continue
        if pending_lists and i.op == 0xB5 and ins[k + 1].op == 0x42 and ins[k + 2].op == 0x1D:
            # the list's own count: the last PushByte/InitArray before SetVariable menuItems
            out += [op('PushByte', value=i.args['value'] - 1), ins[k + 1]]
            for j in ins:
                for key in ('target', 'end'):
                    if j.args.get(key) is i:
                        j.args[key] = out[-2]
            pending_lists -= 1
            k += 2
            continue
        if (name(i) == '1' and name(ins[k + 1]) == 'presenceRequestOnline' and ins[k + 2].op == 0xB5
                and ins[k + 3].op == 0xAE and name(ins[k + 3]) == '_root' and ins[k + 4].op == 0xB2
                and name(ins[k + 4]) == 'fscommandProxy' and out[-7:] and [x.op for x in out[-7:]] == [0xAE, 0xAF, 0xAF, 0x5A, 0xAE, 0xAF, 0xB2]):
            # the menu's own first-time EA Online login (presenceRequestOnline "1") and its select sound: dropped
            removed = out[-7:] + ins[k:k + 5]
            del out[-7:]
            after = ins[k + 5]
            for j in ins:
                for key in ('target', 'end'):
                    if any(j.args.get(key) is r for r in removed):
                        j.args[key] = after
            counts['online'] += 1
            k += 5
            continue
        if name(i) == 'MENU_TITLE_QUICKMATCH':
            new = op('PushString', string=TITLE)
            for j in ins:
                for key in ('target', 'end'):
                    if j.args.get(key) is i:
                        j.args[key] = new
            out.append(new)
            counts['label'] += 1
            k += 1
            continue
        out.append(i)
        k += 1
    if counts['quickmatch'] != 4 or counts['leaderboard'] < 1 or counts['label'] < 1 or counts['online'] != 1 or pending_lists:
        raise ValueError(f'main menu block differs from the expected shape: {counts}, unclosed lists {pending_lists}')
    kept = {id(i) for i in out}
    for j in out:
        for key in ('target', 'end'):
            if key in j.args and id(j.args[key]) not in kept:
                raise ValueError(f'{j.label} refers to a removed instruction')
    rw.repoint(item, field, start, rw.assemble(out),
               f'main menu: Quick Match opens the local browser ({counts["quickmatch"]} lists), '
               f'Leaderboards removed ({counts["leaderboard"]} lists), label "{TITLE}" ({counts["label"]}), '
               f'no EA Online login on opening the menu')
    return counts


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
            out += [i, op('PushString', string='Black'), op('PushString', string=TITLE), op('PushByte', value=2),
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


def no_autosave_notice(apt, rw):
    """main_root (movie frame 0): onProfileLoadingSavingDone shows the "when you see this icon, the game is saving"
    dialog when extern.displayAutoSaveWarning; it now always continues as after that dialog (autoSaveWarningDone)."""
    hits = [(i, f, s) for o, i, f, s in apt.action_items() if o == 'char movie frame 0 action' and s == 0x3E10]
    if len(hits) != 1:
        raise ValueError('main_root frame 0 block is not where expected (a different game version?)')
    item, field, start = hits[0]
    ins = rw.block(start)
    pool = next(i.args['items'] for i in ins if i.op == 0x88)
    name = lambda i: apt.constants[pool[i.args['value']]] if i.op in (0xA2, 0xAE, 0xAF) else None
    sites = [k for k in range(len(ins) - 1) if name(ins[k]) == 'extern' and name(ins[k + 1]) == 'displayAutoSaveWarning']
    if len(sites) != 1:
        raise ValueError(f'expected one displayAutoSaveWarning check, found {len(sites)}')
    k = sites[0]
    new = op('PushFalse')
    for j in ins:
        for key in ('target', 'end'):
            if any(j.args.get(key) is r for r in ins[k:k + 2]):
                j.args[key] = new
    ins[k:k + 2] = [new]
    rw.repoint(item, field, start, rw.assemble(ins), 'startup: no "the game is saving" icon notice')


def build(bundle_bytes):
    """(new bundle bytes, new main_nav.res bytes, change log) from the original mainmenu-00.fbrb."""
    bundle = Bundle(bundle_bytes)
    res = bundle.data(ENTRY)
    apt = Apt(res)
    if apt.validate()[2]:
        raise ValueError('the original menu movie does not validate')
    rw = Rewriter(apt)
    main_menu(apt, rw)
    dev_page_opens_host_list(apt, rw)
    host_list_is_local(apt, rw)
    tidy_host_list_layout(apt, rw)
    new_res = rw.res(res)
    problems = Apt(new_res).validate()[2]
    if problems:
        raise ValueError(f'the edited menu movie does not validate: {problems[:3]}')
    root_res = bundle.data(ROOT_ENTRY)
    root = Apt(root_res)
    if root.validate()[2]:
        raise ValueError('the original main_root movie does not validate')
    rrw = Rewriter(root)
    no_autosave_notice(root, rrw)
    new_root = rrw.res(root_res)
    if Apt(new_root).validate()[2]:
        raise ValueError('the edited main_root movie does not validate')
    replaced = {ENTRY: new_res, ROOT_ENTRY: new_root}
    new_bundle = bundle.build(replaced)[0]
    again = Bundle(new_bundle)
    if any(again.data(e['name']) != replaced.get(e['name'], bundle.data(e['name'])) for e in bundle.entries):
        raise ValueError('the rebuilt bundle does not read back as intended')
    return new_bundle, new_res, rw.log + rrw.log, new_root
