"""The Quick Match browser: edits to BF1943's main menu movie (Menu/main_nav.res in menu/main/mainmenu-00.fbrb).

build() takes the original mainmenu-00.fbrb and gives the edited bundle, its new main_nav.res and main_root.res and a
change log (xeniakit.py checks each result against known.json).

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
import struct
import sys
from collections.abc import Callable, Collection, Sequence
from typing import Any

sys.dont_write_bytecode = True
from apt import Apt, Ins, Rewriter, op  # noqa: E402
from fbrb1943 import Bundle  # noqa: E402

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
POOL_OPS = (0xA2, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3)     # the instructions whose operand is a constant pool index
type ActionItem = tuple[int, int, int]                    # (item, field, block start), as Apt.action_items gives


def pool_index(apt: Apt, ins: Sequence[Ins], value: str) -> int:
    pool = next(i.args['items'] for i in ins if i.op == 0x88)
    return next(k for k, constant in enumerate(pool) if apt.constants[constant] == value)


def constant_namer(apt: Apt, ins: Sequence[Ins], ops: Collection[int] | None = POOL_OPS) -> Callable[[Ins], Any]:
    """name(instruction): the constant a pool-index instruction pushes, from the block's ConstantPool; None for an
    instruction not in ops (ops None: every instruction is read as one)."""
    pool = next(i.args['items'] for i in ins if i.op == 0x88)

    def name(instruction: Ins) -> Any:
        if ops is not None and instruction.op not in ops:
            return None
        return apt.constants[pool[instruction.args['value']]]
    return name


def retarget(ins: Sequence[Ins], replaced: Sequence[Ins], new: Ins) -> None:
    """Branches and function ends that point at any of the replaced instructions point at new instead."""
    for instruction in ins:
        for key in ('target', 'end'):
            if any(instruction.args.get(key) is old for old in replaced):
                instruction.args[key] = new


def check_targets(out: Sequence[Ins]) -> None:
    """Refuses a block in which a branch or function end names an instruction no longer in it: it would silently
    mis-assemble."""
    kept = {id(i) for i in out}
    for instruction in out:
        for key in ('target', 'end'):
            if key in instruction.args and id(instruction.args[key]) not in kept:
                raise ValueError(f'{instruction.label} refers to a removed instruction')


def block_item(apt: Apt, owner: str, start: int | None = None) -> ActionItem:
    hits = [(item, field, block) for block_owner, item, field, block in apt.action_items()
            if block_owner == owner and (start is None or block == start)]
    if len(hits) != 1:
        raise ValueError(f'{owner}: expected one action block, found {len(hits)} (a different game version?)')
    return hits[0]


TITLE = 'LOCAL MATCH'


def main_menu(apt: Apt, rw: Rewriter) -> dict[str, int]:
    """The main menu's item lists (char 51 frame 0), relocated:
    - Quick Match items [label, 'event', 'quickmatch', true] become [label, 'dev', 0, false] (open the browser);
    - each Leaderboards item [label, 0, 'leaderboard', true] and RETURN TO GAME LIBRARY item [label, 'event', 'quit',
      false] is dropped and its list's count lowered;
    - the label key MENU_TITLE_QUICKMATCH becomes the text LOCAL MATCH (menu labels show plain text as is,
      as the dev item's '* DEV *' does);
    - the first time the menu opens it logged in to EA Online by itself (presenceRequestOnline "1", when
      !_root.hasConnectedOnce && !isPresenceConnected && extern.canEnterMultiplayer): that request is dropped, so the
      menu opens without a connection check (the online items still report their failure when chosen)."""
    item, field, start = block_item(apt, 'char 51 frame 0 action')
    ins = rw.block(start)
    pool = next(i.args['items'] for i in ins if i.op == 0x88)
    name = constant_namer(apt, ins)
    dev = next(n for n, constant in enumerate(pool) if apt.constants[constant] == 'dev')
    out: list[Ins] = []
    counts = dict(quickmatch=0, leaderboard=0, quit=0, label=0, online=0)
    k = 0
    pending_lists = 0  # lists that lost an item and still need their count lowered
    referenced = {id(j.args.get(key)) for j in ins for key in ('target', 'end') if key in j.args}
    while k < len(ins):
        i = ins[k]
        if i.op == 0x73 and name(ins[k + 1]) == 'quickmatch' and name(ins[k + 2]) == 'event':
            out += [op('PushFalse'), op('PushByte', value=0), op('PushConstant', value=dev)]
            retarget(ins, ins[k:k + 3], out[-3])    # keep any reference to the replaced run valid
            counts['quickmatch'] += 1
            k += 3
            continue
        # an item [label, kind, id, needs-online] built from 7 instructions, ending PushByte 4; InitArray
        whole = (k + 6 < len(ins) and i.op in (0x73, 0x74) and ins[k + 3].op == 0x96 and ins[k + 4].op == 0xAF
                 and ins[k + 5].op == 0xB5 and ins[k + 5].args['value'] == 4 and ins[k + 6].op == 0x42)
        drop = None
        if whole and ins[k + 1].op == 0x59 and name(ins[k + 2]) == 'leaderboard':
            drop = 'leaderboard'    # [label, 0, 'leaderboard', true]: a page needing EA Online
        elif whole and name(ins[k + 1]) == 'quit' and name(ins[k + 2]) == 'event':
            drop = 'quit'           # [label, 'event', 'quit', false]: RETURN TO GAME LIBRARY
        if drop:
            if any(id(r) in referenced for r in ins[k:k + 7]):
                raise ValueError(f'a branch or function ends inside a {drop} item')
            counts[drop] += 1
            pending_lists += 1
            k += 7
            continue
        if pending_lists and i.op == 0xB5 and ins[k + 1].op == 0x42 and ins[k + 2].op == 0x1D:
            # the list's own count: the last PushByte/InitArray before SetVariable menuItems
            out += [op('PushByte', value=i.args['value'] - pending_lists), ins[k + 1]]
            retarget(ins, [i], out[-2])
            pending_lists = 0
            k += 2
            continue
        if online_login(name, ins, k, out):
            # the menu's own first-time EA Online login (presenceRequestOnline "1") and its select sound: dropped
            removed = out[-7:] + ins[k:k + 5]
            del out[-7:]
            retarget(ins, removed, ins[k + 5])
            counts['online'] += 1
            k += 5
            continue
        if name(i) == 'MENU_TITLE_QUICKMATCH':
            new = op('PushString', string=TITLE)
            retarget(ins, [i], new)
            out.append(new)
            counts['label'] += 1
            k += 1
            continue
        out.append(i)
        k += 1
    if (counts['quickmatch'] != 4 or counts['leaderboard'] < 1 or counts['quit'] < 1 or counts['label'] < 1
            or counts['online'] != 1 or pending_lists):
        raise ValueError(f'main menu block differs from the expected shape: {counts}, unclosed lists {pending_lists}')
    check_targets(out)
    rw.repoint(item, field, start, rw.assemble(out),
               f'main menu: Quick Match opens the local browser ({counts["quickmatch"]} lists), '
               f'Leaderboards removed ({counts["leaderboard"]} lists), '
               f'Return to Game Library removed ({counts["quit"]} lists), '
               f'label "{TITLE}" ({counts["label"]}), '
               f'no EA Online login on opening the menu')
    return counts


def online_login(name: Callable[[Ins], Any], ins: Sequence[Ins], k: int, out: Sequence[Ins]) -> bool:
    """Whether ins[k:k + 5] is _root.fscommandProxy("presenceRequestOnline", "1"), with its select sound the last
    seven instructions written to out."""
    sound_ops = [0xAE, 0xAF, 0xAF, 0x5A, 0xAE, 0xAF, 0xB2]
    return bool(name(ins[k]) == '1' and name(ins[k + 1]) == 'presenceRequestOnline' and ins[k + 2].op == 0xB5
                and ins[k + 3].op == 0xAE and name(ins[k + 3]) == '_root' and ins[k + 4].op == 0xB2
                and name(ins[k + 4]) == 'fscommandProxy' and out[-7:] and [x.op for x in out[-7:]] == sound_ops)


def dev_page_opens_host_list(apt: Apt, rw: Rewriter) -> None:
    item, field, start = block_item(apt, 'char 64 frame 0 action', 0x20C70)
    block = [op('PushString', string='hg'), op('PushOne'), op('PushThis'), op('PushString', string='gotoAndStop'),
             op('CallMethodPop'), op('End')]
    rw.repoint(item, field, start, rw.assemble(block),
               'dev page frame 0: this.gotoAndStop("hg") instead of the dev menu')


def host_list_is_local(apt: Apt, rw: Rewriter) -> None:
    item, field, start = block_item(apt, 'char 64 frame 1 action', 0x20F2C)
    ins = rw.block(start)
    name = constant_namer(apt, ins, ops=None)    # every instruction it is asked about names a constant

    def menu_common() -> list[Ins]:
        return [op('PushValueOfVar', value=pool_index(apt, ins, '_root')),
                op('GetNamedMember', value=pool_index(apt, ins, 'menuCommon'))]
    out: list[Ins] = []
    k = 0
    done = set()
    while k < len(ins):
        i, following = ins[k], ins[k + 1] if k + 1 < len(ins) else None
        if i.op == 0xA2 and name(i) == 'hostMultiplayer':
            out.append(op('PushString', string='hostMultiplayerLocal'))
            done.add('local')
        elif (i.op == 0xAE and name(i) == 'extern' and following is not None and following.op == 0xAF
              and name(following) == 'mapList'):
            # [[label, level], ...]; InitArray takes the last pushed value as element 0
            for label, level in reversed(LEVELS):
                out += [op('PushString', string=level), op('PushString', string=label), op('PushByte', value=2),
                        op('InitArray')]
            out += [op('PushByte', value=len(LEVELS)), op('InitArray')]
            k += 2
            done.add('rows')
            continue
        elif (i.op == 0xAE and name(i) == 'p' and following is not None and following.op == 0xA2
              and name(following) == 'currentFrame'
              and ins[k + 2].op == 0xA2 and name(ins[k + 2]) == 'dev' and ins[k + 3].op == 0x4F
              and ins[k + 4].op == 0x8C and ins[k + 4].args['string'] == 'dev'):
            # Back: p._parent.gotoAndStop("start") (p is the page sprite) instead of returning to the dev menu
            back = [op('PushString', string='start'), op('PushOne'), op('PushValueOfVar', value=i.args['value']),
                    op('PushString', string='_parent'), op('GetMember'), op('PushString', string='gotoAndStop'),
                    op('CallMethodPop')]
            # anything that pointed into the replaced run now points at its first new instruction
            retarget(ins, ins[k:k + 5], back[0])
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
    check_targets(out)
    rw.repoint(item, field, start, rw.assemble(out),
               'host list: literal rows, hostMultiplayerLocal, Back to the main menu, heading, initial updateList()')


def placements(apt: Apt, char: int, frame: int) -> dict[str, int]:
    """{instance name: PlaceObject address} on one frame of a sprite."""
    out = {}
    for frame_index, items in apt.frames(apt.characters()[char]):
        for placement, kind in items:
            if frame_index == frame and kind == 3:
                for j in range(10, 18):  # the name pointer follows the matrix and colour transform
                    pointer = apt.u(placement + 4 * j)
                    if 0x100 < pointer < len(apt.apt) and (apt.s(pointer) or '').isidentifier():
                        out[apt.s(pointer)] = placement
                        break
    return out


def set_float(rw: Rewriter, at: int, old: float, new: float, why: str) -> None:
    rw.patch(at, struct.pack('>f', old), struct.pack('>f', new), why)


def tidy_host_list_layout(apt: Apt, rw: Rewriter) -> None:
    rows = placements(apt, 64, 1)
    if not all(row in rows for row in [f'hg{k}' for k in range(7)] + ['hgList']):
        raise ValueError('host list placements differ from the expected shape')

    def depth(row: str) -> int:
        return apt.u(rows[row] + 8)
    if (depth('hg0'), depth('hgList')) != (1, 15):
        raise ValueError('host list depths differ from the expected shape')
    rw.patch(rows['hg0'] + 8, struct.pack('>I', 1), struct.pack('>I', 15), 'row hg0 above the list (depth 1 -> 15)')
    rw.patch(rows['hgList'] + 8, struct.pack('>I', 15), struct.pack('>I', 1),
             'list and its marker below the rows (depth 15 -> 1)')
    for k in range(7):
        at = rows[f'hg{k}'] + 16
        old_x, old_y = struct.unpack('>2f', apt.apt[at + 16:at + 24])
        set_float(rw, at + 16, old_x, -270.0, f'row hg{k} x aligned')
        set_float(rw, at + 20, old_y, 65.0 + 19.0 * k, f'row hg{k} y evenly spaced')
    text = placements(apt, 57, 0)['thetxt'] + 16
    old_y = struct.unpack('>f', apt.apt[text + 20:text + 24])[0]
    set_float(rw, text + 20, old_y, old_y - 14.0,
              'dev row text up 14 px onto the selection marker (char 57 is only on the dev page)')


def no_autosave_notice(apt: Apt, rw: Rewriter) -> None:
    """main_root (movie frame 0): onProfileLoadingSavingDone shows the "when you see this icon, the game is saving"
    dialog when extern.displayAutoSaveWarning; it now always continues as after that dialog (autoSaveWarningDone)."""
    hits = [(item, field, block) for owner, item, field, block in apt.action_items()
            if owner == 'char movie frame 0 action' and block == 0x3E10]
    if len(hits) != 1:
        raise ValueError('main_root frame 0 block is not where expected (a different game version?)')
    item, field, start = hits[0]
    ins = rw.block(start)
    name = constant_namer(apt, ins, ops=(0xA2, 0xAE, 0xAF))
    sites = [k for k in range(len(ins) - 1)
             if name(ins[k]) == 'extern' and name(ins[k + 1]) == 'displayAutoSaveWarning']
    if len(sites) != 1:
        raise ValueError(f'expected one displayAutoSaveWarning check, found {len(sites)}')
    k = sites[0]
    new = op('PushFalse')
    retarget(ins, ins[k:k + 2], new)
    ins[k:k + 2] = [new]
    rw.repoint(item, field, start, rw.assemble(ins), 'startup: no "the game is saving" icon notice')


def build(bundle_bytes: bytes) -> tuple[bytes, bytes, list[Any], bytes]:
    """(new bundle bytes, new main_nav.res bytes, change log, new main_root.res bytes) from the original
    mainmenu-00.fbrb."""
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
    root_rewriter = Rewriter(root)
    no_autosave_notice(root, root_rewriter)
    new_root = root_rewriter.res(root_res)
    if Apt(new_root).validate()[2]:
        raise ValueError('the edited main_root movie does not validate')
    replaced = {ENTRY: new_res, ROOT_ENTRY: new_root}
    new_bundle = bundle.build(replaced)[0]
    again = Bundle(new_bundle)
    if any(again.data(entry['name']) != replaced.get(entry['name'], bundle.data(entry['name']))
           for entry in bundle.entries):
        raise ValueError('the rebuilt bundle does not read back as intended')
    return new_bundle, new_res, rw.log + root_rewriter.log, new_root
