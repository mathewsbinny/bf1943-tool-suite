"""Edits to BF1943's in-game menu movie (Menu/igm_mp_cq.res in menu/hud/hud_multiplayer_cq-00.fbrb).

build() takes the original hud_multiplayer_cq-00.fbrb (and, for the experiment, a team) and gives the edited bundle,
its new igm_mp_cq.res and a change log (xeniakit.py checks the result against known.json).

The deploy screen ("ENLIST NOW") is the movie's frame 6 ('spawn', block 0x2be50): kit choice, spawn map and
A to deploy (onSelect -> getURL "FSCommand:doSpawn"). The native command selectTeam (0x8, handler 8246c870
case 2) asks the server for a team (1 = USMC, 2 = Imperial Japan) once the local player exists; the deploy
screen then redraws its team visuals (background flag, kit portraits, carrier) by itself. Nothing in the
shipped UI calls selectTeam. In a local match the server honours it: the levels' AutoTeamEntity has
ForcePlayersToTeam1 off and auto-balances only from 6 players (user-tested 2026-10-22).

team_selector(): the first time the deploy screen opens in a match, a CHOOSE YOUR SIDE window built from the
screen's own parts shows two boxes, USMC and JAPAN (kit cards with each side's soldier and the game's team flags).
Left/right (stick or D-pad) picks one, A confirms; the class menu stays hidden until the server has switched the
team, then the screen redraws itself for it. The player cannot enter battle before choosing.
frontline_without_leaderboards(): the in-game menu FRONTLINE REPORT without its Leaderboards row.
no_gamer_card(): no SEE GAMER CARD button on My Squad, the scoreboard or the friends list.
force_team(): experiment only; requests one team every time the deploy screen opens.
"""
import struct
import sys
from typing import Any

sys.dont_write_bytecode = True
from apt import Apt, Ins, Rewriter, op, be32  # noqa: E402
from fbrb1943 import Bundle  # noqa: E402
from menu_edits import ActionItem, constant_namer, retarget  # noqa: E402

BUNDLE = 'dist/xenon/menu/hud/hud_multiplayer_cq-00.fbrb'
ENTRY = 'Menu/igm_mp_cq.res'
DEPLOY_BLOCK = 0x2BE50
DEPLOY_FRAME = 6

# The CHOOSE YOUR SIDE window, built from the deploy screen's own parts and placed on its frame (depths above the
# deploy screen, 19-345; popupsMC is hidden in a live match, where the HUD root draws dialogs). Stage 1280x720.
# (name, template instance on the movie timeline, depth, scale x, scale y, x, y)
WINDOW = [
    ('rsPanel',   'PANEL', 401, 2.65, 1.29, 435.0, 148.0),      # new sprite: the kit card's paper and border (see PANEL)
    ('rsCaption', 'mapHeading', 403, 1.0, 1.0, 467.0, 141.0),   # the brush-tape heading, as AUTO-SPAWN (char 352)
    ('rsCardUS',  'RIFLEMAN', 405, 1.0, 1.0, 465.0, 205.0),     # the two boxes: kit cards with each side's soldier
    ('rsCardJP',  'RIFLEMAN', 407, 1.0, 1.0, 655.0, 205.0),
    ('rsFlagUS',  'teamFlag', 409, 1.0, 1.0, 583.0, 233.0),     # the game's team flags (char 227: US / JAP)
    ('rsFlagJP',  'teamFlag', 411, 1.0, 1.0, 773.0, 233.0),
]
# The window panel: a new sprite holding only a kit card's background (shape 262, placed as in the card, char 290)
# and paper face with border (shape 267), each laid three times because they are partly transparent; the card's
# subheading boxes (shape 289) are left out. (shape, scale x, scale y, x, y)
PANEL = [(262, 0.4667, 9.034, 1.85, -39.15)] * 3 + [(267, 1.0, 1.0, 0.0, 0.0)] * 3
SIDES = [  # (team, card, flag, flag frame, portrait team, heading, subheading 1, subheading 2)
    (1, 'rsCardUS', 'rsFlagUS', 'US', 'us', 'USMC', 'UNITED STATES', 'MARINE CORPS'),
    (2, 'rsCardJP', 'rsFlagJP', 'JAP', 'jp', 'JAPAN', 'IMPERIAL', 'JAPANESE ARMY'),
]
# The class menu (kit cards, CHOOSE KIT) and the carrier name show the team the server picked on joining:
# hidden while the side is being chosen, so that first automatic team is never seen.
CLASS_MENU = ['RIFLEMAN', 'INFANTRY', 'SCOUT', 'selectKitText', 'button_8', 'spawnPointName', 'mapHeading',
              'selectSpawnText']
HOLD_MS = 100        # the screen's own updates show some of these again: hidden (and transparent) this often
CHECK_MS = 100       # how often the new team is checked after a choice
SETTLE_CHECKS = 10   # after extern.playerTeam shows the new team, the rest of the switch (the team's kit list)
                     # arrives later: the deploy screen is redrawn this many checks (1 s) afterwards
CHECK_LIMIT = 50     # redraw anyway after this many checks (5 s)
# After that redraw (a second init() on the same screen) every class card is left on kitInfo frame kit_0, the
# Rifleman art (seen 2026-10-22, session kits-2); a newly opened screen sets each card's own frame. The cards'
# kit frames (kitInfo, char 288: kit_1, kit_0, kit_2, each with its own character art) are set again directly,
# with init()'s portrait rule (setKitInfo: the chosen class in colour, the others '_black'), on each check for
# KIT_TICKS checks while the screen settles.
KIT_CARDS = 3
KIT_TICKS = 10
# The HUD's spawn banner and the scoreboard headers show _root.factionSIDs[0] / [1], built once when the HUD loads
# (hud_root_cq 0x3e965) from extern.teamFactions. That pair is UiManager +0x421ec/+0x421f0, written only on joining
# (8245d798: faction of my team, faction of the other), so after a team switch it still names the joining team.
# Rebuilt from the live extern.playerTeam: factionNames is [neutral, USMC, Japan, Japan] by faction, and team 1 is
# faction 1 (USMC), team 2 faction 2 (Japan).
FACTION_ARRAYS = [('factionSIDs', 'factionNames'), ('factionSIDsWIN', 'factionNamesWIN'),
                  ('factionSIDsLOSE', 'factionNamesLOSE')]

type Code = list[Ins]       # a run of AS2 instructions


def action_block(apt: Apt, owner: str, start: int, what: str) -> ActionItem:
    """(item, field, block start) of owner's one action block at start; ValueError naming what otherwise."""
    hits = [(item, field, block) for block_owner, item, field, block in apt.action_items()
            if block_owner == owner and block == start]
    if len(hits) != 1:
        raise ValueError(f'{what} is not where expected (a different game version?)')
    return hits[0]


def deploy_block(apt: Apt) -> ActionItem:
    return action_block(apt, 'char movie frame 6 action', DEPLOY_BLOCK, 'the deploy screen block')


def instance_name(apt: Apt, placement: int) -> str | None:
    """A PlaceObject's instance name (its string pointer at +0x34); None when it has none."""
    pointer = apt.u(placement + 0x34)
    return apt.s(pointer) if pointer else None


# --- small AS2 builders (stack order as the game's own code uses it); every call makes fresh instructions

def push_string(text: str) -> Ins:
    return op('PushString', string=text)


def var(name: str) -> Code:
    return [push_string(name), op('GetVariable')]


def member(obj: Code, name: str) -> Code:
    return obj + [push_string(name), op('GetMember')]


def path(*names: str) -> Code:
    """this.a.b.c"""
    out = [op('PushThis')]
    for name in names:
        out = member(out, name)
    return out


def call(obj: Code, method: str, *args: Code) -> Code:
    out: Code = []
    for argument in reversed(args):
        out += argument
    return out + [op('PushByte', value=len(args))] + obj + [push_string(method), op('CallMethodPop')]


def set_member(obj: Code, name: str, value: Code) -> Code:
    return obj + [push_string(name)] + value + [op('SetMember')]


def set_this(name: str, value: Code) -> Code:
    return set_member([op('PushThis')], name, value)


def visible(names: list[str], on: bool) -> Code:
    out: Code = []
    for name in names:
        out += set_member(path(name), '_visible', [op('PushTrue' if on else 'PushFalse')])
    return out


def hidden_hard(names: list[str], on: bool) -> Code:
    """Hidden by _alpha only. The screen's updates set _visible again but leave _alpha alone; and a clip hidden by
    _visible does not apply frame changes to its children (the kit cards' kitInfo.gotoAndPlay("kit_N") in init()
    would be lost, leaving every card on kit_1)."""
    out: Code = []
    for name in names:
        out += set_member(path(name), '_alpha', [op('PushByte', value=0 if on else 100)])
    return out


def sound(name: str) -> Code:
    def ui() -> Code:
        return member(var('dice'), 'UISound')
    return call(ui(), 'play', member(ui(), name))


def menu_common() -> Code:
    return member(var('_root'), 'menuCommon')


def button(name: str) -> Code:
    return member(member(var('dice'), 'MenuCommon'), name)


def fscommand(command: str, arg: str) -> Code:
    return [op('GetURL', strings=(f'FSCommand:{command}', arg))]


def set_interval(milliseconds: int, function: str) -> Code:
    """setInterval(this, function, milliseconds), its timer id left on the stack."""
    return [op('PushByte', value=milliseconds), push_string(function), op('PushThis'), op('PushByte', value=3),
            push_string('setInterval'), op('CallFunction')]


def clear_interval(timer: str) -> Code:
    """clearInterval(this.<timer>)"""
    return path(timer) + [op('PushByte', value=1), push_string('clearInterval'), op('CallFunctionPop')]


def if_else(condition: Code, then: Code, otherwise: Code) -> Code:
    """condition; Not; BranchIfTrue else; then; BranchAlways end; else: otherwise; end (no-op targets)."""
    end, other = op('PushUndefined'), op('PushUndefined')
    return (condition + [op('Not'), op('BranchIfTrue', target=other)] + then + [op('BranchAlways', target=end)]
            + [other, op('Pop')] + otherwise + [end, op('Pop')])


def sprite(apt: Apt, rw: Rewriter, frames: list[list[int]]) -> int:
    """A sprite structure (laid out as the kit card's, char 290) with the given frame item lists."""
    card = apt.characters()[290]
    header = bytearray(apt.apt[card:card + 24])  # type 5, signature, frames, table, 0, ...
    if be32(header, 0) != 5:
        raise ValueError('char 290 is not a sprite')
    table = rw.alloc(b''.join(struct.pack('>II', len(items), rw.words(items)) for items in frames))
    struct.pack_into('>II', header, 8, len(frames), table)
    return rw.alloc(bytes(header))


def panel_sprite(apt: Apt, rw: Rewriter) -> int:
    """The window panel: the PANEL shapes, placed as the card places its face."""
    template = next(placement for _frame, items in apt.frames(apt.characters()[290]) for placement, kind in items
                    if kind == 3 and apt.u(placement + 12) == 267)
    records = []
    for depth, (shape, scale_x, scale_y, x, y) in enumerate(PANEL, start=1):
        record = bytearray(apt.apt[template:template + 0x40])
        if be32(record, 4) != 0x6 or be32(record, 0x34) != 0 or be32(record, 0x3C) != 0:
            raise ValueError('the card face placement differs from the expected shape')
        struct.pack_into('>II', record, 8, depth, shape)
        struct.pack_into('>6f', record, 16, scale_x, 0.0, 0.0, scale_y, x, y)
        records.append(rw.alloc(bytes(record)))
    return sprite(apt, rw, [records])


def add_characters(apt: Apt, rw: Rewriter, sprites: list[int]) -> list[int]:
    """Append sprites to the movie's character table; returns their character ids."""
    movie = apt.movie
    count, table = apt.u(movie + 20), apt.u(movie + 24)
    chars = apt.characters()
    rw.patch(movie + 20, struct.pack('>II', count, table),
             struct.pack('>II', count + len(sprites), rw.words(chars + sprites)),
             f'character {count}: the CHOOSE YOUR SIDE window panel')
    return list(range(count, count + len(sprites)))


def add_window(apt: Apt, rw: Rewriter) -> None:
    """The window's parts as new placements on the deploy frame, copied from named template placements."""
    records = {}
    for _frame, items in apt.frames(apt.movie):
        for placement, kind in items:
            name = instance_name(apt, placement) if kind == 3 else None
            if name and name not in records:
                records[name] = placement
    table = apt.u(apt.movie + 12) + 8 * DEPLOY_FRAME
    count, items = apt.u(table), apt.u(table + 4)
    used = {apt.u(apt.u(items + 4 * k) + 8) for k in range(count)}
    new = []
    panel, = add_characters(apt, rw, [panel_sprite(apt, rw)])
    for name, template, depth, scale_x, scale_y, x, y in WINDOW:
        source = records['RIFLEMAN' if template == 'PANEL' else template]
        record = bytearray(apt.apt[source:source + 0x40])
        if be32(record, 0) != 3 or be32(record, 0x3C) != 0 or depth in used:
            raise ValueError(f'{name}: template {template} or depth {depth} differs from the expected shape')
        struct.pack_into('>I', record, 8, depth)
        if template == 'PANEL':
            struct.pack_into('>I', record, 12, panel)
        struct.pack_into('>6f', record, 16, scale_x, 0.0, 0.0, scale_y, x, y)
        struct.pack_into('>I', record, 0x34, rw.string(name))  # name: relocated by the loader (8298a3a0, +0x34)
        new.append(rw.alloc(bytes(record)))
    pointers = [apt.u(items + 4 * k) for k in range(count)] + new
    rw.patch(table, struct.pack('>II', count, items), struct.pack('>II', len(pointers), rw.words(pointers)),
             'deploy frame: the CHOOSE YOUR SIDE window (panel, caption, two side cards, two flags)')


def selector_code() -> tuple[list[tuple[str, Code]], Code, Code]:
    """([(function name, body)] for the deploy timeline, code for the start of init(), code for its end).
    All state lives on the deploy timeline (this), so it starts afresh with each match's in-game menu."""
    parts = [name for name, *_ in WINDOW]

    def texts() -> Code:  # the cards' text components fill themselves in from their instance names: set ours again
        out: Code = []
        for _team, card, _flag, _frame, _portrait, heading, sub1, sub2 in SIDES:
            for text in ('text1', 'text2'):
                out += call(path(card, 'kitInfo', 'kitHeading', text), 'setValue', [push_string(heading)])
            out += call(path(card, 'kitInfo', 'kitSubHeading1'), 'setValue', [push_string(sub1)])
            out += call(path(card, 'kitInfo', 'kitSubHeading2'), 'setValue', [push_string(sub2)])
        # the heading's own setValue: sets the text and sizes its brush tape (char 351 picks a frame per width)
        out += call(path('rsCaption'), 'setValue', [push_string('CHOOSE YOUR SIDE')])
        return out

    def cards() -> Code:  # draw both side cards; the chosen one in the game's 'selected' (green) state
        out: Code = []
        for team, card, flag, frame, portrait, _heading, _sub1, _sub2 in SIDES:
            out += if_else(path('rsSel') + [op('PushByte', value=team), op('Equals2')],
                           call(path(card), 'gotoAndStop', [push_string('selected')]),
                           call(path(card), 'gotoAndStop', [push_string('unselected')]))
            out += call(path(card, 'kitInfo'), 'gotoAndStop', [push_string('kit_1')])
            out += call(path(card, 'kitInfo'), 'setTeam', [push_string(portrait)])
            out += call(path(flag), 'gotoAndStop', [push_string(frame)])
        return out + texts()

    def choose(team: int, sound_name: str) -> Code:
        return set_this('rsSel', [op('PushByte', value=team)]) + sound(sound_name) + cards()

    # the class menu stays transparent until the deploy screen is redrawn for the chosen side (rsCheckTeam): shown
    # earlier, it flickers with the team it had before (USMC, or with no team yet, the same USMC art)
    confirm = (clear_interval('rsHold') + sound('SND_MENU_SELECT') + visible(parts, False)
               + if_else(path('rsSel') + [op('PushByte', value=1), op('Equals2')],
                         fscommand('selectTeam', '1'), fscommand('selectTeam', '2'))
               + set_this('rsSideChosen', [op('PushTrue')]) + set_this('rsWantTeam', path('rsSel'))
               + set_this('rsChecks', [op('PushZero')]) + set_this('rsSettled', [op('PushZero')])
               + call(menu_common(), 'removeAllActions')   # nothing to press until the screen is redrawn
               + set_this('rsTimer', set_interval(CHECK_MS, 'rsCheckTeam')))

    def factions() -> Code:  # _root.factionSIDs = [factionNames[my team], factionNames[3 - my team]] (and WIN/LOSE)
        out: Code = []
        for sids, names in FACTION_ARRAYS:
            mine = member(var('_root'), names) + member(var('extern'), 'playerTeam') + [op('GetMember')]
            other = (member(var('_root'), names) + [op('PushByte', value=3)] + member(var('extern'), 'playerTeam')
                     + [op('Subtract'), op('GetMember')])
            out += (var('_root') + [push_string(sids)] + other + mine
                    + [op('PushByte', value=2), op('InitArray'), op('SetMember')])
        return out

    def kit_art() -> Code:  # each class card on its own kit frame, in the new team's colour (chosen) or black portrait
        out: Code = []
        for i in range(KIT_CARDS):
            def kit_info() -> Code:
                card = var('m_kits') + [op('PushByte', value=i), op('GetMember'), op('PushByte', value=2),
                                        op('GetMember')]
                return member(card, 'kitInfo')
            out += call(kit_info(), 'gotoAndStop', [push_string(f'kit_{i}')])
            out += if_else(path('m_currentKitIdx') + [op('PushByte', value=i), op('Equals2')],
                           call(kit_info(), 'setTeam', var('m_teamStr')),
                           call(kit_info(), 'setTeam', var('m_teamStr') + [push_string('_black'), op('Add2')]))
        return out

    kits_done = op('PushUndefined')
    kits = (kit_art() + set_this('rsKitTicks', path('rsKitTicks') + [op('Increment')])
            + path('rsKitTicks') + [op('PushByte', value=KIT_TICKS - 1), op('Greater'), op('Not'),
                                    op('BranchIfTrue', target=kits_done)]
            + clear_interval('rsKitTimer')
            + [kits_done, op('Pop')])

    not_yet = op('PushUndefined')
    check = (set_this('rsChecks', path('rsChecks') + [op('Increment')])
             # once the server reports the new team, count settle checks
             + if_else(member(var('extern'), 'playerTeam') + path('rsWantTeam') + [op('Equals2')],
                       set_this('rsSettled', path('rsSettled') + [op('Increment')]), [])
             + path('rsSettled') + [op('PushByte', value=SETTLE_CHECKS - 1), op('Greater')]
             + path('rsChecks') + [op('PushByte', value=CHECK_LIMIT), op('Greater'), op('Or'),
                                   op('Not'), op('BranchIfTrue', target=not_yet)]
             + clear_interval('rsTimer')
             + factions()
             + call([op('PushThis')], 'init')    # the deploy screen's own setup, now for the new team
             + kit_art() + hidden_hard(CLASS_MENU, False) + set_this('rsKitTicks', [op('PushZero')])
             + set_this('rsKitTimer', set_interval(CHECK_MS, 'rsKitArt'))
             + [not_yet, op('Pop')])
    functions = [('rsLeft', choose(1, 'SND_NAV_UP')), ('rsRight', choose(2, 'SND_NAV_DOWN')),
                 ('rsConfirm', confirm), ('rsCheckTeam', check),
                 ('rsHoldHidden', hidden_hard(CLASS_MENU, True) + texts()),
                 ('rsKitArt', kits)]

    ask = (hidden_hard(CLASS_MENU, True) + visible(parts, True)
           + set_member(path('rsPanel', 'kitInfo'), '_visible', [op('PushFalse')])
           + call(path('rsPanel'), 'gotoAndStop', [push_string('unselected')])
           + set_this('rsHold', set_interval(HOLD_MS, 'rsHoldHidden'))
           + set_this('rsSel', [op('PushByte', value=1)]) + cards()
           # this screen's controls while choosing: left/right on the stick and the D-pad, and A
           + call(menu_common(), 'removeAllActions')
           + call(menu_common(), 'setAction', button('BUTTON_DOWN'), [push_string('BUTTON_SELECT')],
                  [op('PushThis')], [push_string('rsConfirm')])
           + call(menu_common(), 'setLanalogAction', [op('PushThis')], [push_string('rsLeft')],
                  [op('PushUndefined')], [push_string('rsRight')], [op('PushUndefined')])
           + call(menu_common(), 'setDpadHAction', [op('PushThis')], [push_string('rsLeft')],
                  [push_string('rsRight')]))
    after = op('PushUndefined')
    init_start = visible(parts, False)
    init_end = path('rsSideChosen') + [op('BranchIfTrue', target=after)] + ask + [after, op('Pop')]
    return functions, init_start, init_end


def deploy_init(ins: list[Ins]) -> int:
    """The index of the deploy screen's one init() definition."""
    inits = [k for k, instruction in enumerate(ins) if instruction.op == 0x8E and instruction.args['name'] == 'init']
    if len(inits) != 1:
        raise ValueError(f'expected one init() on the deploy screen, found {len(inits)}')
    return inits[0]


def team_selector(apt: Apt, rw: Rewriter) -> None:
    add_window(apt, rw)
    item, field, start = deploy_block(apt)
    ins = rw.block(start)
    init_at = deploy_init(ins)
    functions, init_start, init_end = selector_code()
    define = ins[init_at]
    body = ins[init_at + 1:ins.index(define.args['end'])]
    if any(i.op == 0x3E for i in body) or body[-1].op in (0x99, 0x9D, 0xB8):
        raise ValueError('init() returns early or ends in a branch: the end code would not always run')
    end_at = ins.index(define.args['end'])
    ins[end_at:end_at] = init_end
    define.args['end'] = ins[end_at + len(init_end)]
    ins[init_at + 1:init_at + 1] = init_start
    head = []
    for function_name, function_body in functions:
        head += [op('DefineFunction', name=function_name, params=[])] + function_body
    k = next(k for k, i in enumerate(ins) if i.op != 0x88)  # right after the ConstantPool
    ins[k:k] = head
    for function_name, function_body in functions:  # each new function ends at the instruction after its body
        define_at = next(k for k, i in enumerate(ins) if i.op == 0x9B and i.args.get('name') == function_name)
        ins[define_at].args['end'] = ins[define_at + 1 + len(function_body)]
    rw.repoint(item, field, start, rw.assemble(ins),
               'deploy screen: CHOOSE YOUR SIDE window (two side cards, left/right and A; class menu hidden), '
               'refreshed for the chosen team once the server confirms')


FRONTLINE_BLOCK = 0x2B048   # the in-game menu page "FRONTLINE REPORT" (movie frame 5, 'start')
ROW_STEP = 31.0             # its rows menuitem_0..6 stand 31 px apart


def frontline_without_leaderboards(apt: Apt, rw: Rewriter) -> None:
    """FRONTLINE REPORT lists My Squad, Scoreboard, Help & Options, Leaderboards, Achievements, Exit Game and
    Respawn/Suicide, one fixed row clip each (menuitem_0..6). Leaderboards (menuitem_3) is dropped from menuItems,
    its row is never shown (getMenuTxt's table sets each row's _visible), and the three rows below move up a row."""
    item, field, start = action_block(apt, 'char movie frame 5 action', FRONTLINE_BLOCK, 'the FRONTLINE REPORT block')
    ins = rw.block(start)
    name = constant_namer(apt, ins, ops=(0xA2, 0xAE, 0xAF, 0xB2))
    done = set()
    k = 0
    while k < len(ins):
        i = ins[k]
        # menuItems: [... [menuitem_3, 'leaderboard', true] ...] (6 instructions), then the list's count 7
        if (i.op == 0x73 and name(ins[k + 1]) == 'leaderboard' and ins[k + 2].op == 0x70
                and name(ins[k + 3]) == 'menuitem_3'
                and ins[k + 4].op == 0xB5 and ins[k + 4].args['value'] == 3 and ins[k + 5].op == 0x42):
            removed = ins[k:k + 6]
            if any(j.args.get(key) is r for j in ins for key in ('target', 'end') for r in removed):
                raise ValueError('a branch or function ends inside the Leaderboards item')
            del ins[k:k + 6]
            count = next(c for c in range(k, len(ins))
                         if ins[c].op == 0xB5 and ins[c + 1].op == 0x42 and ins[c + 2].op == 0x1D)
            if ins[count].args['value'] != 7:
                raise ValueError('the in-game menu list is not 7 items long')
            new = op('PushByte', value=6)
            retarget(ins, [ins[count]], new)
            ins[count] = new
            done.add('item')
            continue
        # getMenuTxt: {menuitem_3: [!m_isTutorial, 'MENU_TITLE_LEADERBOARD'], ...}: the row is never visible
        if (i.op == 0xA2 and name(i) == 'menuitem_3' and name(ins[k + 1]) == 'm_isTutorial' and ins[k + 2].op == 0x12
                and name(ins[k + 3]) == 'MENU_TITLE_LEADERBOARD'):
            new = op('PushFalse')
            retarget(ins, ins[k + 1:k + 3], new)
            ins[k + 1:k + 3] = [new]
            done.add('row')
        k += 1
    if done != {'item', 'row'}:
        raise ValueError(f'the FRONTLINE REPORT block differs from the expected shape: {sorted(done)}')
    rw.repoint(item, field, start, rw.assemble(ins), 'FRONTLINE REPORT: Leaderboards removed')
    rows = {}
    for frame, items in apt.frames(apt.movie):
        for placement, kind in items:
            row = instance_name(apt, placement) if frame == 5 and kind == 3 else None
            if (row or '').startswith('menuitem_'):
                rows[row] = placement
    # its own row, never shown: also placed off-screen in case it draws before getMenuTxt
    at = rows['menuitem_3'] + 16 + 16
    x = struct.unpack('>f', apt.apt[at:at + 4])[0]
    rw.patch(at, struct.pack('>f', x), struct.pack('>f', -2000.0), 'FRONTLINE REPORT: Leaderboards row off-screen')
    for n in (4, 5, 6):
        at = rows[f'menuitem_{n}'] + 16 + 20
        y = struct.unpack('>f', apt.apt[at:at + 4])[0]
        rw.patch(at, struct.pack('>f', y), struct.pack('>f', y - ROW_STEP),
                 f'FRONTLINE REPORT: row menuitem_{n} up one row')


# Every offer of SEE GAMER CARD (Y; FSCommand:showGamerCard, an Xbox LIVE overlay with nothing to show offline):
# (block owner, block, setAction calls, button hints)
GAMER_CARD = [
    ('char movie frame 10 action', 0x2DF38, 1, 0),   # MY SQUAD
    ('char 57 frame 0 action', 0x2F9C8, 0, 2),       # the friends list's button hints (pushed, and in a list of 3)
    ('char 232 frame 0 action', 0x316D4, 1, 0),      # the scoreboard's player lists
    ('char 234 frame 0 action', 0x31DD8, 1, 0),
]


def remove(ins: list[Ins], start: int, end: int) -> None:
    """Delete ins[start:end]; branches and function ends that pointed into it point to the instruction after it."""
    retarget(ins, ins[start:end], ins[end])
    del ins[start:end]


def no_gamer_card(apt: Apt, rw: Rewriter) -> None:
    items = {(owner, block): (item, field) for owner, item, field, block in apt.action_items()}
    for owner, start, calls, hints in GAMER_CARD:
        if (owner, start) not in items:
            raise ValueError(f'{owner}: the block is not where expected (a different game version?)')
        item, field = items[(owner, start)]
        ins = rw.block(start)
        name = constant_namer(apt, ins)
        found = [0, 0]
        k = 0
        while k < len(ins) - 10:
            # setAction(BUTTON_UP, 'BUTTON_SEE_GAMERCARD', this, 'seeGamerCard'): 10 instructions, args pushed last first
            if (name(ins[k]) == 'seeGamerCard' and name(ins[k + 2]) == 'BUTTON_SEE_GAMERCARD'
                    and name(ins[k + 9]) == 'setAction'):
                remove(ins, k, k + 10)
                found[0] += 1
                continue
            # {which: 'y', txt: 'BUTTON_SEE_GAMERCARD'}, then ctx.btns.push(it) or further entries of a list literal
            if (name(ins[k]) == 'which' and name(ins[k + 1]) == 'y' and name(ins[k + 3]) == 'BUTTON_SEE_GAMERCARD'
                    and ins[k + 5].op == 0x43):
                if ins[k + 6].op == 0x5A and name(ins[k + 9]) == 'push':
                    remove(ins, k, k + 10)
                else:
                    count = next(c for c in range(k + 6, len(ins)) if ins[c].op == 0xB5 and ins[c + 1].op == 0x42)
                    if ins[count].args['value'] != 3:
                        raise ValueError(f'{owner}: the button hint list is not 3 long')
                    ins[count].args['value'] = 2
                    remove(ins, k, k + 6)
                found[1] += 1
                continue
            k += 1
        if found != [calls, hints]:
            raise ValueError(f'{owner}: expected {calls} SEE GAMER CARD actions and {hints} hints, found {found}')
        rw.repoint(item, field, start, rw.assemble(ins), f'{owner}: SEE GAMER CARD removed')


def force_team(apt: Apt, rw: Rewriter, team: int) -> None:
    item, field, start = deploy_block(apt)
    ins = rw.block(start)
    k = deploy_init(ins) + 1
    ins[k:k] = [op('GetURL', strings=('FSCommand:selectTeam', str(team)))]
    spawns = [k for k, i in enumerate(ins)
              if i.op == 0x83 and i.args.get('strings', (None,))[0] == 'FSCommand:doSpawn']
    if len(spawns) != 1:
        raise ValueError(f'expected one doSpawn on the deploy screen, found {len(spawns)}')
    k = spawns[0]
    request = op('GetURL', strings=('FSCommand:selectTeam', str(team)))
    retarget(ins, [ins[k]], request)
    ins[k:k] = [request]
    rw.repoint(item, field, start, rw.assemble(ins),
               f'experiment: deploy screen sends selectTeam({team}) on init() and before doSpawn')


def build(bundle_bytes: bytes, team: int | None = None) -> tuple[bytes, bytes, list[Any]]:
    """(new bundle bytes, new igm_mp_cq.res bytes, change log): the in-game menu with the team selector; with team=1
    or 2, the force_team experiment instead."""
    bundle = Bundle(bundle_bytes)
    res = bundle.data(ENTRY)
    apt = Apt(res)
    if apt.validate()[2]:
        raise ValueError('the original in-game menu movie does not validate')
    rw = Rewriter(apt)
    if team:
        force_team(apt, rw, team)
    else:
        team_selector(apt, rw)
    frontline_without_leaderboards(apt, rw)
    no_gamer_card(apt, rw)
    new_res = rw.res(res)
    problems = Apt(new_res).validate()[2]
    if problems:
        raise ValueError(f'the edited in-game menu does not validate: {problems[:3]}')
    new_bundle = bundle.build({ENTRY: new_res})[0]
    if Bundle(new_bundle).data(ENTRY) != new_res:
        raise ValueError('the rebuilt bundle does not read back as intended')
    return new_bundle, new_res, rw.log
