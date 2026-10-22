"""Edits to BF1943's in-game menu movie (Menu/igm_mp_cq.res in menu/hud/hud_multiplayer_cq-00.fbrb).

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
force_team(): experiment only; requests one team every time the deploy screen opens.
"""
import os
import struct
import sys

sys.dont_write_bytecode = True
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(SUITE, 'bf1943-apt-tools'), os.path.join(SUITE, 'bf1943-fbrb-tool')]
from apt import Apt, Rewriter, op, be32
from fbrb1943 import Bundle

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
CHECK_LIMIT = 50     # refresh anyway after this many checks (5 s) if the server did not confirm


def deploy_block(apt):
    hits = [(i, f, s) for o, i, f, s in apt.action_items() if o == 'char movie frame 6 action' and s == DEPLOY_BLOCK]
    if len(hits) != 1:
        raise ValueError('the deploy screen block is not where expected (a different game version?)')
    return hits[0]


# --- small AS2 builders (stack order as the game's own code uses it); every call makes fresh instructions

def s(text):
    return op('PushString', string=text)


def var(name):
    return [s(name), op('GetVariable')]


def member(obj, name):
    return obj + [s(name), op('GetMember')]


def path(*names):
    """this.a.b.c"""
    out = [op('PushThis')]
    for name in names:
        out = member(out, name)
    return out


def call(obj, method, *args):
    out = []
    for a in reversed(args):
        out += a
    return out + [op('PushByte', value=len(args))] + obj + [s(method), op('CallMethodPop')]


def set_member(obj, name, value):
    return obj + [s(name)] + value + [op('SetMember')]


def set_this(name, value):
    return set_member([op('PushThis')], name, value)


def visible(names, on):
    out = []
    for name in names:
        out += set_member(path(name), '_visible', [op('PushTrue' if on else 'PushFalse')])
    return out


def hidden_hard(names, on):
    """_visible and _alpha together: the screen's updates set _visible again, but leave _alpha alone."""
    out = []
    for name in names:
        out += set_member(path(name), '_visible', [op('PushFalse' if on else 'PushTrue')])
        out += set_member(path(name), '_alpha', [op('PushByte', value=0 if on else 100)])
    return out


def sound(name):
    def ui():
        return member(var('dice'), 'UISound')
    return call(ui(), 'play', member(ui(), name))


def menu_common():
    return member(var('_root'), 'menuCommon')


def button(name):
    return member(member(var('dice'), 'MenuCommon'), name)


def fscommand(command, arg):
    return [op('GetURL', strings=(f'FSCommand:{command}', arg))]


def if_else(condition, then, otherwise):
    """condition; Not; BranchIfTrue else; then; BranchAlways end; else: otherwise; end (no-op targets)."""
    end, other = op('PushUndefined'), op('PushUndefined')
    return (condition + [op('Not'), op('BranchIfTrue', target=other)] + then + [op('BranchAlways', target=end)]
            + [other, op('Pop')] + otherwise + [end, op('Pop')])


def sprite(apt, rw, frames):
    """A sprite structure (laid out as the kit card's, char 290) with the given frame item lists."""
    header = bytearray(apt.apt[apt.characters()[290]:apt.characters()[290] + 24])  # type 5, signature, frames, table, 0, ...
    if be32(header, 0) != 5:
        raise ValueError('char 290 is not a sprite')
    table = rw.alloc(b''.join(struct.pack('>II', len(items), rw.words(items)) for items in frames))
    struct.pack_into('>II', header, 8, len(frames), table)
    return rw.alloc(bytes(header))


def panel_sprite(apt, rw):
    """The window panel: the PANEL shapes, placed as the card places its face."""
    template = next(it for f, items in apt.frames(apt.characters()[290]) for it, k in items if k == 3 and apt.u(it + 12) == 267)
    records = []
    for depth, (shape, sx, sy, x, y) in enumerate(PANEL, start=1):
        r = bytearray(apt.apt[template:template + 0x40])
        if be32(r, 4) != 0x6 or be32(r, 0x34) != 0 or be32(r, 0x3C) != 0:
            raise ValueError('the card face placement differs from the expected shape')
        struct.pack_into('>II', r, 8, depth, shape)
        struct.pack_into('>6f', r, 16, sx, 0.0, 0.0, sy, x, y)
        records.append(rw.alloc(bytes(r)))
    return sprite(apt, rw, [records])


def add_characters(apt, rw, sprites):
    """Append sprites to the movie's character table; returns their character ids."""
    m = apt.movie
    count, table = apt.u(m + 20), apt.u(m + 24)
    chars = apt.characters()
    rw.patch(m + 20, struct.pack('>II', count, table), struct.pack('>II', count + len(sprites), rw.words(chars + sprites)),
             f'character {count}: the CHOOSE YOUR SIDE window panel')
    return list(range(count, count + len(sprites)))


def add_window(apt, rw):
    """The window's parts as new placements on the deploy frame, copied from named template placements."""
    records = {}
    for f, items in apt.frames(apt.movie):
        for it, k in items:
            name = apt.s(apt.u(it + 0x34)) if k == 3 and apt.u(it + 0x34) else None
            if name and name not in records:
                records[name] = it
    table = apt.u(apt.movie + 12) + 8 * DEPLOY_FRAME
    count, items = apt.u(table), apt.u(table + 4)
    used = {apt.u(apt.u(items + 4 * k) + 8) for k in range(count)}
    new = []
    panel, = add_characters(apt, rw, [panel_sprite(apt, rw)])
    for name, template, depth, sx, sy, x, y in WINDOW:
        source = records['RIFLEMAN' if template == 'PANEL' else template]
        r = bytearray(apt.apt[source:source + 0x40])
        if be32(r, 0) != 3 or be32(r, 0x3C) != 0 or depth in used:
            raise ValueError(f'{name}: template {template} or depth {depth} differs from the expected shape')
        struct.pack_into('>I', r, 8, depth)
        if template == 'PANEL':
            struct.pack_into('>I', r, 12, panel)
        struct.pack_into('>6f', r, 16, sx, 0.0, 0.0, sy, x, y)
        struct.pack_into('>I', r, 0x34, rw.string(name))  # name: relocated by the loader (8298a3a0, +0x34)
        new.append(rw.alloc(bytes(r)))
    pointers = [apt.u(items + 4 * k) for k in range(count)] + new
    rw.patch(table, struct.pack('>II', count, items), struct.pack('>II', len(pointers), rw.words(pointers)),
             'deploy frame: the CHOOSE YOUR SIDE window (panel, caption, two side cards, two flags)')


def selector_code():
    """([(function name, body)] for the deploy timeline, code for the start of init(), code for its end).
    All state lives on the deploy timeline (this), so it starts afresh with each match's in-game menu."""
    parts = [name for name, *_ in WINDOW]

    def texts():  # the cards' text components fill themselves in from their instance names: set ours again
        out = []
        for team, card, flag, frame, portrait, heading, sub1, sub2 in SIDES:
            for text in ('text1', 'text2'):
                out += call(path(card, 'kitInfo', 'kitHeading', text), 'setValue', [s(heading)])
            out += call(path(card, 'kitInfo', 'kitSubHeading1'), 'setValue', [s(sub1)])
            out += call(path(card, 'kitInfo', 'kitSubHeading2'), 'setValue', [s(sub2)])
        # the heading's own setValue: sets the text and sizes its brush tape (char 351 picks a frame per width)
        out += call(path('rsCaption'), 'setValue', [s('CHOOSE YOUR SIDE')])
        return out

    def cards():  # draw both side cards; the chosen one in the game's 'selected' (green) state
        out = []
        for team, card, flag, frame, portrait, heading, sub1, sub2 in SIDES:
            out += if_else(path('rsSel') + [op('PushByte', value=team), op('Equals2')],
                           call(path(card), 'gotoAndStop', [s('selected')]),
                           call(path(card), 'gotoAndStop', [s('unselected')]))
            out += call(path(card, 'kitInfo'), 'gotoAndStop', [s('kit_1')])
            out += call(path(card, 'kitInfo'), 'setTeam', [s(portrait)])
            out += call(path(flag), 'gotoAndStop', [s(frame)])
        return out + texts()

    def choose(team, sound_name):
        return set_this('rsSel', [op('PushByte', value=team)]) + sound(sound_name) + cards()

    stop_hold = path('rsHold') + [op('PushByte', value=1), s('clearInterval'), op('CallFunctionPop')]
    confirm = (stop_hold + sound('SND_MENU_SELECT') + visible(parts, False) + hidden_hard(CLASS_MENU, False)
               + if_else(path('rsSel') + [op('PushByte', value=1), op('Equals2')],
                         fscommand('selectTeam', '1'), fscommand('selectTeam', '2'))
               + set_this('rsSideChosen', [op('PushTrue')]) + set_this('rsWantTeam', path('rsSel'))
               + set_this('rsChecks', [op('PushZero')])
               + set_this('rsTimer', [op('PushByte', value=CHECK_MS), s('rsCheckTeam'), op('PushThis'),
                                      op('PushByte', value=3), s('setInterval'), op('CallFunction')]))
    done = op('PushUndefined')
    check = (set_this('rsChecks', path('rsChecks') + [op('Increment')])
             + member(var('extern'), 'playerTeam') + path('rsWantTeam') + [op('Equals2')]
             + path('rsChecks') + [op('PushByte', value=CHECK_LIMIT), op('Greater'), op('Or'),
                                   op('Not'), op('BranchIfTrue', target=done)]
             + path('rsTimer') + [op('PushByte', value=1), s('clearInterval'), op('CallFunctionPop')]
             + call([op('PushThis')], 'init')    # the deploy screen's own setup, now for the new team
             + [done, op('Pop')])
    functions = [('rsLeft', choose(1, 'SND_NAV_UP')), ('rsRight', choose(2, 'SND_NAV_DOWN')),
                 ('rsConfirm', confirm), ('rsCheckTeam', check), ('rsHoldHidden', hidden_hard(CLASS_MENU, True) + texts())]

    ask = (hidden_hard(CLASS_MENU, True) + visible(parts, True)
           + set_member(path('rsPanel', 'kitInfo'), '_visible', [op('PushFalse')])
           + call(path('rsPanel'), 'gotoAndStop', [s('unselected')])
           + set_this('rsHold', [op('PushByte', value=HOLD_MS), s('rsHoldHidden'), op('PushThis'),
                                 op('PushByte', value=3), s('setInterval'), op('CallFunction')])
           + set_this('rsSel', [op('PushByte', value=1)]) + cards()
           # this screen's controls while choosing: left/right on the stick and the D-pad, and A
           + call(menu_common(), 'removeAllActions')
           + call(menu_common(), 'setAction', button('BUTTON_DOWN'), [s('BUTTON_SELECT')], [op('PushThis')], [s('rsConfirm')])
           + call(menu_common(), 'setLanalogAction', [op('PushThis')], [s('rsLeft')], [op('PushUndefined')],
                  [s('rsRight')], [op('PushUndefined')])
           + call(menu_common(), 'setDpadHAction', [op('PushThis')], [s('rsLeft')], [s('rsRight')]))
    after = op('PushUndefined')
    init_start = visible(parts, False)
    init_end = path('rsSideChosen') + [op('BranchIfTrue', target=after)] + ask + [after, op('Pop')]
    return functions, init_start, init_end


def team_selector(apt, rw):
    add_window(apt, rw)
    item, field, start = deploy_block(apt)
    ins = rw.block(start)
    inits = [k for k, i in enumerate(ins) if i.op == 0x8E and i.args['name'] == 'init']
    if len(inits) != 1:
        raise ValueError(f'expected one init() on the deploy screen, found {len(inits)}')
    functions, init_start, init_end = selector_code()
    define = ins[inits[0]]
    body = ins[inits[0] + 1:ins.index(define.args['end'])]
    if any(i.op == 0x3E for i in body) or body[-1].op in (0x99, 0x9D, 0xB8):
        raise ValueError('init() returns early or ends in a branch: the end code would not always run')
    end_at = ins.index(define.args['end'])
    ins[end_at:end_at] = init_end
    define.args['end'] = ins[end_at + len(init_end)]
    ins[inits[0] + 1:inits[0] + 1] = init_start
    head = []
    for fname, fbody in functions:
        head += [op('DefineFunction', name=fname, params=[])] + fbody
    k = next(k for k, i in enumerate(ins) if i.op != 0x88)  # right after the ConstantPool
    ins[k:k] = head
    for fname, fbody in functions:  # each new function ends at the instruction after its body
        d = next(k for k, i in enumerate(ins) if i.op == 0x9B and i.args.get('name') == fname)
        ins[d].args['end'] = ins[d + 1 + len(fbody)]
    rw.repoint(item, field, start, rw.assemble(ins),
               'deploy screen: CHOOSE YOUR SIDE window (two side cards, left/right and A; class menu hidden), '
               'refreshed for the chosen team once the server confirms')


def force_team(apt, rw, team):
    item, field, start = deploy_block(apt)
    ins = rw.block(start)
    inits = [k for k, i in enumerate(ins) if i.op == 0x8E and i.args['name'] == 'init']
    if len(inits) != 1:
        raise ValueError(f'expected one init() on the deploy screen, found {len(inits)}')
    k = inits[0] + 1
    ins[k:k] = [op('GetURL', strings=('FSCommand:selectTeam', str(team)))]
    spawns = [k for k, i in enumerate(ins) if i.op == 0x83 and i.args.get('strings', (None,))[0] == 'FSCommand:doSpawn']
    if len(spawns) != 1:
        raise ValueError(f'expected one doSpawn on the deploy screen, found {len(spawns)}')
    k = spawns[0]
    request = op('GetURL', strings=('FSCommand:selectTeam', str(team)))
    for j in ins:
        for key in ('target', 'end'):
            if j.args.get(key) is ins[k]:
                j.args[key] = request
    ins[k:k] = [request]
    rw.repoint(item, field, start, rw.assemble(ins), f'experiment: deploy screen sends selectTeam({team}) on init() and before doSpawn')


def build(bundle_bytes, team=None):
    """The in-game menu with the team selector; with team=1 or 2, the force_team experiment instead."""
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
    new_res = rw.res(res)
    problems = Apt(new_res).validate()[2]
    if problems:
        raise ValueError(f'the edited in-game menu does not validate: {problems[:3]}')
    new_bundle = bundle.build({ENTRY: new_res})[0]
    if Bundle(new_bundle).data(ENTRY) != new_res:
        raise ValueError('the rebuilt bundle does not read back as intended')
    return new_bundle, new_res, rw.log
