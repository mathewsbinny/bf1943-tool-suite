"""Tests for the bots experiment (bots.py).

  python -B test_bots.py             the code assembles, fits its cave, and the hook branches to it
  python -B test_bots.py --image     + against the decoded BF1943 image (BF1943_IMAGE, or operation-rising-sun's
                                     work/xex-analysis/default.memory.bin): the hook's original word, and the cave
                                     and data areas unused (zero)
"""
import functools
import os
import re
import struct
import sys
import types

sys.dont_write_bytecode = True
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
import bots
import ppcasm

failures = []
BASE = 0x82000000


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def layout():
    words, code, tail = bots.build()
    hook = words[0]
    check(hook[0] == bots.HOOK and hook[1] >> 26 == 18 and hook[1] & 1
          and bots.HOOK + ((hook[1] & 0x3FFFFFC) ^ 0x2000000) - 0x2000000 == bots.CODE,
          'the hook is a bl to the bot code')
    check(all(bots.CODE <= a < bots.CODE_END for a, _, _ in words[1:]) and len(words) - 1 == (len(code) + len(tail)) // 4,
          f'the bot code, table and names fit the cave ({len(code) + len(tail):#x} of {bots.CODE_END - bots.CODE:#x} bytes)')
    saves = max(int(m, 16) for m in re.findall(r'std r\d+, -0x([0-9a-f]+)\(r1\)', bots.CODE_SOURCE))
    check(bots.FRAME - saves >= bots.BUF + 0x100 and bots.FRAME % 16 == 0 and bots.BUF % 16 == 0
          and bots.BUF + 0x100 <= bots.DT and bots.DT + 4 <= bots.FRAME - saves,
          'the stack frame holds the input frame buffer and the time step below the saved registers, 16-aligned')
    text = bots.patch_toml()
    check(text.startswith(bots.HEADER) and text.count('[[patch.be32]]') == len(words) and 'hash = "872BBE31C0FBA21B"' in text,
          'the patch file names the module and holds every word')
    for bad in ([], [(3, 'Rifleman')], [(1, 'Medic')], [(1, 'Rifleman')] * (bots.MAX_BOTS + 1)):
        try:
            bots.build(bad)
            check(False, f'refuses bots {bad[:2]}')
        except ValueError:
            check(True, f'refuses bots {bad[:2]}')


def mailbox():
    """Mailbox v2: the layout fits, and capstone (an independent decoder) reads the code's mailbox accesses as the
    layout names them."""
    check(bots.DATA + bots.MAILBOX_END <= bots.DATA_END, f'the mailbox address and magic fit the data area '
          f'(to {bots.DATA + bots.MAILBOX_END:#x} of {bots.DATA_END:#x})')
    check(bots.BOT0 >= bots.H_FLAGS + 4 and bots.FLAG0 == bots.BOT0 + bots.BOT_SIZE * bots.MAX_BOTS
          and bots.C_BUTTONS_LO + 4 <= bots.BOT_SIZE and bots.R_SEQ_END < bots.C_STAMP and bots.F_SEQ_END + 4 <= bots.FLAG_SIZE
          and bots.F_Z < bots.F_SEQ_END and bots.BLOCK_SIZE <= 0x7FFF and bots.BLOCK_SIZE % 4 == 0,
          f'the block layout: header, {bots.MAX_BOTS} bot records of {bots.BOT_SIZE:#x}, {bots.MAX_FLAGS} flag records '
          f'of {bots.FLAG_SIZE:#x}, {bots.BLOCK_SIZE:#x} bytes')
    try:
        import capstone
    except ImportError:
        print('skip capstone checks: capstone is not installed')
        return
    d = decoded()
    end = d.names['FMT_HOST'] - bots.CODE        # the instructions; the format strings follow them
    ins = d.listing
    check(len(ins) == end // 4, f'capstone decodes every instruction ({len(ins)} of {end // 4})')
    text = [f'{m} {o}'.strip() for m, o in ins]

    def offsets(op, base):
        found = set()
        for m, o in ins:
            mm = re.fullmatch(r'r\d+, (-?(?:0x)?[0-9a-f]+)\((r\d+)\)', o)
            if m == op and mm and mm.group(2) == base:
                found.add(int(mm.group(1), 0))
        return found
    check(offsets('stw', 'r22') == {bots.R_SEQ, bots.R_PLAYER, bots.R_SOLDIER, bots.R_TEAM, bots.R_X, bots.R_Y, bots.R_Z,
                                    bots.R_SEQ_END}, f'the guest writes exactly the state record\'s words: {sorted(offsets("stw", "r22"))}')
    check(offsets('lwz', 'r22') == {bots.C_STAMP, bots.C_THROTTLE, bots.C_STRAFE, bots.C_YAW, bots.C_PITCH, bots.C_BUTTONS_HI,
                                    bots.C_BUTTONS_LO, bots.C_SEQ}, f'and reads exactly the command record\'s: {sorted(offsets("lwz", "r22"))}')
    header = {bots.H_MAGIC, bots.H_VERSION, bots.H_BOTS, bots.H_BOT_SIZE, bots.H_FLAGS_MAX, bots.H_FLAG_SIZE, bots.H_MANAGER}
    flags = {bots.F_SEQ, bots.F_ENTITY, bots.F_OWNER, bots.F_CONTROL, bots.F_ENABLED, bots.F_RADIUS, bots.F_X, bots.F_Y,
             bots.F_Z, bots.F_SEQ_END}
    check(offsets('stw', 'r29') == header | flags, f'the header (at creation) and flag records: {sorted(offsets("stw", "r29"))}')
    check(offsets('stw', 'r3') >= {bots.H_FLAGS, bots.H_TICK}, 'the tick\'s flag count and tick go to the header')
    want = [f'std r22, -0x58(r1)', f'ld r22, -0x58(r1)', f'cmplwi r5, {bots.STALE:#x}', f'li r3, {bots.BLOCK_SIZE:#x}',
            'bl 0x82627d10', 'bl 0x825c5470', 'bl 0x825c1290', 'lbz r5, 0xad(r27)', 'lwz r5, 0x80(r27)', 'lwz r5, 0xa0(r27)',
            'lwz r5, 0x114(r5)', f'lis r4, {bots.MAGIC >> 16:#x}', f'ori r4, r4, {bots.MAGIC & 0xFFFF:#x}',
            f'lis r5, {((bots.CP_TYPE + 0x8000) >> 16) - 0x10000:#x}', f'addi r5, r5, {bots.CP_TYPE & 0xFFFF:#x}']
    missing = [w for w in want if w not in text]
    check(not missing, f'the new instructions decode as written (stale check, allocation, capture-point iteration, '
          f'magic, saved r22){": missing " + str(missing) if missing else ""}')
    k = text.index(f'cmplwi r5, {bots.STALE:#x}')
    check(text[k + 1].startswith('bgt ') and text[k - 1] == 'subf r5, r6, r5', 'a command older than STALE ticks branches '
          'past the copy (tick - stamp, unsigned)')
    k = text.index(f'li r3, {bots.BLOCK_SIZE:#x}')
    check(text[k + 1] == 'bl 0x82627d10', 'the block is allocated with the game allocator')
    command_snapshot()
    match_key()
    flag_radius_and_header()


CTX, MANAGER, PLAYERS, HOST, OTHER = 0x40000000, 0x41000000, 0x42000000, 0x43000000, 0x44000000


def run_entry(mem, calls=None, limit=1000, until=('forget', 'make', 'respawn', 'out', 'create')):
    """The bot code from its start (after the server's own player update) on the interpreter, to the first of the
    paths it takes: 'forget', 'make' (make the bots), 'respawn' (bots exist: per-bot work), 'out' or 'create'."""
    d = decoded()
    start = [a for a, (m, o) in d.ins.items() if m == 'bl' and o == ['0x8238eb68']][0] + 4
    reg = {'r1': 0x70000000}
    stops = {d.names[n]: n for n in until}
    every = {0x82349d78: lambda r: CTX, 0x829fa7ec: lambda r: 0, **(calls or {})}
    for _ in interpret(start, set(stops), reg, lambda a: mem.get(a, 0), mem.__setitem__, every, limit=limit):
        pass
    return stops[reg['_stop']], reg


def match(state=1, manager=MANAGER, recorded=MANAGER, players=(HOST,), host=HOST):
    """A guest memory: the server context's player manager and its players, and the bot code's data area (its state,
    the manager and host player it recorded, a bot in slot 0, a mailbox)."""
    d = bots.DATA
    mem = {CTX + 0x28: manager, d + bots.STATE: state, d + bots.MANAGER: recorded, d + bots.HOST_PLAYER: host if state else 0,
           d + bots.SLOTS: 0x45000000 if state else 0, d + bots.MBOX: 0x46000000 if state else 0,
           d + bots.MBOX_MAGIC: bots.MAGIC if state else 0}
    if manager:
        mem.update({manager + 0x54: PLAYERS, manager + 0x58: PLAYERS + 4 * len(players)})
        mem.update({PLAYERS + 4 * k: p for k, p in enumerate(players)})
    return mem


def match_key():
    """Which match the bots belong to: the player manager and its first player (the host's), as recorded when they were
    made. Before 2026-10-30 a missing manager left everything in place, and only the manager was compared: a new match
    whose manager reused the old address took the old match's freed bot players as its own."""
    cases = [('the same match (manager and first player as recorded)', match(), 'respawn'),
             ('no player manager (between matches)', match(manager=0), 'forget'),
             ('the manager\'s address reused by a new match (its first player another)',
              match(players=(OTHER, HOST)), 'forget'),
             ('an empty player list', match(players=()), 'forget'),
             ('another manager', match(manager=0x41100000), 'forget'),
             ('no bots yet in this match', match(state=0), 'make')]
    for what, mem, want in cases:
        got, _ = run_entry(mem)
        check(got == want, f'match key: {what}: {got} (expected {want})')
    # forget: everything of the old match cleared, the new manager (0 when none) remembered
    names = decoded().names
    for manager in (0, 0x41100000):
        mem = match(manager=manager)
        for _ in interpret(names['forget'], names['out'], {'r30': manager, 'r31': bots.DATA, 'r1': 0x70000000},
                           lambda a: mem.get(a, 0), mem.__setitem__):
            pass
        d = bots.DATA
        check(all(mem.get(d + o) == 0 for o in (bots.STATE, bots.MBOX, bots.MBOX_MAGIC, bots.HOST_PLAYER))
              and not any(mem.get(d + bots.SLOTS + 4 * k) for k in range(bots.MAX_BOTS)) and mem[d + bots.MANAGER] == manager,
              f'forget (manager {manager:#x}): state, mailbox, key and slots cleared, the manager remembered')
    # making the bots records the host's player as the match's key (run to the creation loop: the block is cleared
    # and its header written on the way)
    mem = match(state=0)
    mem[HOST + 0x120] = 0x47000000                            # the host has a soldier
    got, reg = run_entry(mem, {0x82627d10: lambda r: 0x48000000}, limit=10000, until=('forget', 'respawn', 'out', 'create'))
    check(got == 'create' and mem.get(bots.DATA + bots.HOST_PLAYER) == HOST and mem.get(bots.DATA + bots.STATE) == 1
          and mem.get(bots.DATA + bots.MBOX) == 0x48000000 and mem.get(0x48000000 + bots.H_MAGIC) == bots.MAGIC,
          'making the bots records the host\'s player with the mailbox and the state')


def flag_radius_and_header():
    """A capture point without data gets radius 0 (before 2026-10-30 its record kept an earlier tick's radius); the
    header's flag count is written before its tick, lwsync between."""
    names = decoded().names
    rec, block, entity, xf, tick = 0x60000000, 0x60000000 - bots.FLAG0, 0x5A000000, 0x58000000, 1000
    found = [entity, 0]
    mem = {bots.DATA + bots.TICK: tick, bots.DATA + bots.MBOX: block, entity + 0x14: 0, rec + bots.F_RADIUS: 0x41A00000}
    trace = []

    def store(at, v):
        mem[at] = v
        trace.append(at)
    for _ in interpret(names['flag_next'], names['out'], {'r1': 0x70000000, 'r29': rec, 'r28': 0, 'r31': bots.DATA},
                       lambda a: mem.get(a, 0), store, {0x825c1290: lambda r: found.pop(0), 0x822a7bb0: lambda r: xf},
                       lambda: trace.append('lwsync')):
        pass
    check(mem[rec + bots.F_RADIUS] == 0 and mem[rec + bots.F_SEQ_END] == tick,
          'a capture point without data: radius 0 in this tick\'s record, not the earlier radius')
    i, j = trace.index(block + bots.H_FLAGS), trace.index(block + bots.H_TICK)
    check(i < j and 'lwsync' in trace[i + 1:j], 'the header: flag count, lwsync, then the tick')


@functools.lru_cache(maxsize=None)
def decoded():
    """The bot code assembled and capstone-decoded once (the interpreter runs it about 1,500 times): code (bytes),
    names (its labels), listing [(mnemonic, op_str)] of the instructions (the format strings that follow them left
    out), and ins {address: (mnemonic, [operand])} for the interpreter."""
    import capstone
    _, code, _ = bots.build()
    names = ppcasm.assemble(bots.CODE_SOURCE, bots.CODE, bots.assembler_symbols())[1]
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_64 | capstone.CS_MODE_BIG_ENDIAN)
    found = list(md.disasm(code[:names['FMT_HOST'] - bots.CODE], bots.CODE))
    ins = {i.address: (i.mnemonic, [a.strip() for a in i.op_str.split(',')] if i.op_str else []) for i in found}
    return types.SimpleNamespace(code=code, names=names, listing=[(i.mnemonic, i.op_str) for i in found], ins=ins)


BRANCHES = {'b': lambda cr: True, 'beq': lambda cr: cr == 0, 'bne': lambda cr: cr != 0, 'bgt': lambda cr: cr > 0,
            'blt': lambda cr: cr < 0, 'bge': lambda cr: cr >= 0, 'ble': lambda cr: cr <= 0}


def interpret(start, stop, reg, load, store, calls=None, sync=None, limit=1000):
    """The bot code from address start to address stop (or the first of a set of addresses: reg['_stop'] names it) on
    a small interpreter of the instructions it uses, as capstone decodes them, one instruction per step (a generator).
    load(address) -> word and store(address, word) are the memory (the host may change it between steps), calls
    {target: f(reg) -> r3} stand for the game's functions, and sync() is called at each lwsync."""
    ins = decoded().ins
    num = lambda t: int(t, 0)
    stops = {stop} if isinstance(stop, int) else set(stop)
    pc, cr = start, 0
    for _ in range(limit):
        if pc in stops:
            reg['_stop'] = pc
            return
        m, o = ins[pc]
        pc += 4
        if m in ('lwz', 'stw', 'lbz'):
            off, base = o[1][:-1].split('(')
            at = (reg[base] + num(off)) & 0xFFFFFFFF
            if m == 'lwz':
                reg[o[0]] = load(at)
            elif m == 'lbz':
                reg[o[0]] = load(at & ~3) >> 8 * (3 - (at & 3)) & 0xFF
            else:
                store(at, reg[o[0]])
        elif m == 'lwsync':
            if sync:
                sync()
        elif m == 'bl':
            reg['r3'] = calls[num(o[0])](reg)
        elif m == 'mr':
            reg[o[0]] = reg[o[1]]
        elif m == 'li':
            reg[o[0]] = num(o[1]) & 0xFFFFFFFF
        elif m == 'addi':
            reg[o[0]] = (reg[o[1]] + num(o[2])) & 0xFFFFFFFF
        elif m == 'lis':
            reg[o[0]] = num(o[1]) << 16 & 0xFFFFFFFF
        elif m == 'ori':
            reg[o[0]] = reg[o[1]] | num(o[2])
        elif m == 'add':
            reg[o[0]] = (reg[o[1]] + reg[o[2]]) & 0xFFFFFFFF
        elif m == 'subf':
            reg[o[0]] = (reg[o[2]] - reg[o[1]]) & 0xFFFFFFFF
        elif m in ('cmplw', 'cmplwi', 'cmpwi', 'andi.'):
            if m == 'andi.':
                reg[o[0]] = a = reg[o[1]] & num(o[2])
                b = 0
            else:
                a, b = reg[o[0]], reg[o[1]] if m == 'cmplw' else num(o[1]) & 0xFFFFFFFF
            if m == 'cmpwi':
                a, b = a - (a >> 31 << 32), b - (b >> 31 << 32)
            cr = (a > b) - (a < b)
        elif m in BRANCHES:
            if BRANCHES[m](cr):
                pc = num(o[-1])
        else:
            raise AssertionError(f'the interpreter does not know {m} {o} at {pc - 4:#x}')
        yield
    raise AssertionError(f'the bot code from {start:#x} did not reach {sorted(hex(s) for s in stops)}')


def run_command_read(word, tick):
    """Runs the bot code's command read (from after the frame's + 0x84 copy to push) on the interpreter: word(offset)
    gives the command record's words (the host may write between loads). The frame buffer's command fields as stored
    ((throttle, strafe, yaw, pitch, hi, lo)), or None when it stays neutral."""
    d = decoded()
    start = [a for a, (m, o) in d.ins.items() if m == 'stw' and o == ['r5', f'{bots.BUF + 0x84:#x}(r1)']][0] + 4
    stack, rec = 0x70000000, 0x60000000
    reg = {'r1': stack, 'r22': rec, 'r31': bots.DATA}
    buf = {}

    def load(at):
        return word(at - rec) if rec <= at < rec + bots.BOT_SIZE else {bots.DATA + bots.TICK: tick}[at]

    def store(at, v):
        buf[at - stack - bots.BUF] = v
    for _ in interpret(start, d.names['push'], reg, load, store):
        pass
    if not buf:
        return None
    return tuple(buf.get(o) for o in (8, 0x0C, 0x88, 0x8C, 0x78, 0x7C))


def command_snapshot():
    """Mailbox v2's sequence lock: whatever the host's writes land between, the bot code takes a whole command (the
    old one or the new one) or none, never a mix; an odd or ever-changing sequence and a stale stamp give none."""
    tick = 1000
    fields = ('C_THROTTLE', 'C_STRAFE', 'C_YAW', 'C_PITCH', 'C_BUTTONS_HI', 'C_BUTTONS_LO')
    old = dict(zip(fields, (0x3F800000, 0x3F000000, 0x3FC00000, 0x3E000000, 0x100, 0x1000)), C_STAMP=tick - 1, C_SEQ=6)
    new = dict(zip(fields, (0xBF800000, 0xBF000000, 0xBFC00000, 0xBE000000, 0x200, 0x400000)), C_STAMP=tick)
    # the host's writes, as guestmem and navhost make them: the sequence odd, the record, the sequence even and new
    steps = [(bots.C_SEQ, 7)] + [(getattr(bots, n), new[n]) for n in ('C_STAMP',) + fields] + [(bots.C_SEQ, 8)]
    whole = lambda c: tuple(c[n] for n in fields)
    seen, model_agrees = set(), True
    # the host's write begins after the guest's first `delay` loads, then one step lands after every `gap` loads
    for delay in range(13):
        for gap in (1, 2, 4, 9):
            for runner in ('ppc', 'model'):
                mem = {getattr(bots, n): v for n, v in old.items()}
                pending = list(steps)
                loads = [0]

                def word(off):
                    v = mem.get(off, 0)
                    loads[0] += 1
                    if pending and loads[0] >= delay and (loads[0] - delay) % gap == 0:
                        o, w = pending.pop(0)
                        mem[o] = w
                    return v
                if runner == 'ppc':
                    got = run_command_read(word, tick)
                    seen.add(got)
                else:
                    ref = bots.guest_command(word, tick)
                    model_agrees &= (None if ref is None else tuple(ref[1:])) == got
    check(seen <= {whole(old), whole(new), None} and seen >= {whole(old), whole(new), None},
          f'the guest takes the old command or the new one, never a mix, whatever the host\'s writes land between '
          f'({len(seen)} outcomes: {sorted(str(s) for s in seen)})')
    check(model_agrees, 'bots.guest_command (the hosts\' tests\' model) takes what the bot code takes, in every case')
    rest = {getattr(bots, n): v for n, v in new.items()}
    rest[bots.C_SEQ] = 8
    check(run_command_read(rest.get, tick) == whole(new), 'a whole, fresh command is used')
    for what, change in (('an odd sequence (the host stopped mid-write)', {bots.C_SEQ: 9}),
                         (f'a stamp more than STALE ({bots.STALE}) ticks old', {bots.C_STAMP: tick - bots.STALE - 1}),
                         ('a stamp ahead of the tick', {bots.C_STAMP: tick + 1})):
        check(run_command_read({**rest, **change}.get, tick) is None, f'{what} gives the neutral frame')
    # a stamp of 0 (a new block, or a host's zeroing) answered nothing, also while the tick is still at most STALE (the
    # tick counts from 1 in a session's first match; before 2026-10-30 such a record was taken)
    zeroed = {**rest, bots.C_STAMP: 0}
    check(run_command_read(zeroed.get, 5) is None and bots.guest_command(zeroed.get, 5) is None,
          'a stamp of 0 gives the neutral frame at tick 5 (code and model)')
    churn = [0]

    def changing(off):
        if off == bots.C_SEQ:
            churn[0] += 2
            return churn[0]
        return rest[off]
    check(run_command_read(changing, tick) is None and churn[0] == 2 * 2 * bots.TRIES,
          f'a sequence that changes on every read: {bots.TRIES} tries, then the neutral frame ({churn[0] // 4} tries)')
    record_snapshots()


def run_guest(start, stop, mem, reg, calls, trace):
    """The bot code from label start to label stop on the interpreter, one instruction per step (a generator): mem
    {address: word} is shared with the host, calls {target: f(reg) -> r3} stand for the game's functions, trace gets
    each store (address, word) and each 'lwsync'."""
    names = decoded().names

    def store(at, v):
        mem[at] = v
        trace.append((at, v))
    return interpret(names[start], names[stop], reg, lambda at: mem.get(at, 0), store, calls,
                     lambda: trace.append('lwsync'))


def one_copy(word, first, last, offsets):
    """The read before 2026-10-30: one copy of the record (its words in address order), whole when its two ticks agree."""
    got = {o: word(o) for o in sorted({first, last, *offsets})}
    return got if got[first] == got[last] else None


def record_snapshots():
    """State and flag records' two-word lock: the bot code writes the opening tick, lwsync, the fields, lwsync, the
    closing tick (as bots.guest_record has it), and a host that reads the closing tick, the fields, then the opening
    tick (bots.host_record) gets the old record, the new one or none, wherever the code's steps fall between its loads.
    The read of one copy, comparing its two ticks, takes a mix (the finding this fixed)."""
    tick, rec, player, xf = 1000, 0x60000000, 0x90000010, 0x58000000
    # a bot slot: the player (no input state: the command part is skipped), its new soldier, team and position
    state = {bots.R_PLAYER: player, bots.R_SOLDIER: 0x50000002, bots.R_TEAM: 2,
             bots.R_X: 0xC1200000, bots.R_Y: 0x43360000, bots.R_Z: 0x42C80000}
    old_state = {**state, bots.R_SOLDIER: 0x50000001, bots.R_X: 0x41200000, bots.R_Y: 0x43350000, bots.R_Z: 0x42C00000}

    def bot_writer(mem, trace):
        mem.update({bots.DATA + bots.TICK: tick, bots.DATA + bots.SLOTS: player, player + 0x118: 0,
                    player + 0x120: state[bots.R_SOLDIER], player + 0x30: state[bots.R_TEAM],
                    xf + 0x30: state[bots.R_X], xf + 0x34: state[bots.R_Y], xf + 0x38: state[bots.R_Z]})
        reg = {'r1': 0x70000000, 'r22': rec, 'r28': bots.DATA + bots.SLOTS, 'r29': bots.MAX_BOTS, 'r31': bots.DATA}
        return run_guest('spawn', 'spawn_it', mem, reg, {0x822a7bb0: lambda r: xf}, trace)
    # a capture point: its entity, owner, control, enabled (a byte at + 0xad), radius (its data's + 0x114), position
    entity, data, block = 0x5A000000, 0x5B000000, rec - bots.FLAG0
    flag = {bots.F_ENTITY: entity, bots.F_OWNER: 1, bots.F_CONTROL: 0x42480000, bots.F_ENABLED: 1, bots.F_RADIUS: 0x41A00000,
            bots.F_X: 0x43000000, bots.F_Y: 0x42000000, bots.F_Z: 0xC3000000}
    old_flag = {**flag, bots.F_OWNER: 2, bots.F_CONTROL: 0x42C80000, bots.F_X: 0x43010000, bots.F_Z: 0xC3010000}

    def flag_writer(mem, trace):
        found = [entity, 0]
        mem.update({bots.DATA + bots.TICK: tick, bots.DATA + bots.MBOX: block, entity + 0x80: flag[bots.F_OWNER],
                    entity + 0xa0: flag[bots.F_CONTROL], entity + 0xac: flag[bots.F_ENABLED] << 16, entity + 0x14: data,
                    data + 0x114: flag[bots.F_RADIUS], xf + 0x30: flag[bots.F_X], xf + 0x34: flag[bots.F_Y],
                    xf + 0x38: flag[bots.F_Z]})
        reg = {'r1': 0x70000000, 'r29': rec, 'r28': 0, 'r31': bots.DATA}
        return run_guest('flag_next', 'out', mem, reg, {0x825c1290: lambda r: found.pop(0),
                                                                  0x822a7bb0: lambda r: xf}, trace)
    for what, writer, first, last, new, old in (('state', bot_writer, bots.R_SEQ, bots.R_SEQ_END, state, old_state),
                                                ('flag', flag_writer, bots.F_SEQ, bots.F_SEQ_END, flag, old_flag)):
        trace = []
        for _ in writer({}, trace):
            pass
        stores = [(a - rec, v) for a, v in (t for t in trace if t != 'lwsync') if rec <= a < rec + 0x40]
        check(stores == bots.guest_record(first, last, tick, new),
              f'{what} record: the code writes the opening tick, the fields in order, the closing tick (bots.guest_record)')
        at = [k for k, t in enumerate(trace) if t != 'lwsync' and t[0] - rec in (first, last, min(new), max(new))]
        check(len(at) == 4 and 'lwsync' in trace[at[0] + 1:at[1]] and 'lwsync' in trace[at[2] + 1:at[3]],
              f'{what} record: lwsync after the opening tick and before the closing tick')
        whole = lambda t, f: {**f, first: t, last: t}
        seen, mixed = [], []
        # the host's first load comes after the code's first `delay` steps, then the code takes `pace` steps per load
        for delay in range(40):
            for pace in (0.25, 0.5, 1, 1.5, 2, 2.5, 3, 4, 6):
                for reader, out in ((bots.host_record, seen), (one_copy, mixed)):
                    mem = {rec + o: v for o, v in whole(tick - 1, old).items()}
                    steps = writer(mem, [])
                    for _ in zip(range(delay), steps):
                        pass
                    credit = [0.0]

                    def word(off):
                        v = mem.get(rec + off, 0)
                        credit[0] += pace
                        while credit[0] >= 1:
                            credit[0] -= 1
                            next(steps, None)
                        return v
                    out.append(reader(word, first, last, list(new)))
        allowed = [whole(tick - 1, old), whole(tick, new), None]
        check(all(s in allowed for s in seen) and all(a in seen for a in allowed[:2]),
              f'{what} record: the host takes the old record or the new one, never a mix, wherever the code\'s writes '
              f'fall between its loads ({len(seen)} reads, {sum(s is None for s in seen)} with none whole)')
        check(any(s not in allowed for s in mixed), f'{what} record: one copy whose two ticks agree can still be a mix '
              f'(the old read; {sum(s not in allowed for s in mixed)} of {len(mixed)} reads)')


# The decoded BF1943 image's SHA-256, as operation-rising-sun's research/evidence/xex-image-integrity.json records it
# (every XEX page digest verified against it); that file's value is used when it is there.
IMAGE_SHA256 = 'cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a'


def image():
    research = os.path.join(os.path.dirname(os.path.dirname(KIT)), 'operation-rising-sun')
    path = os.environ.get('BF1943_IMAGE', os.path.join(research, 'work', 'xex-analysis', 'default.memory.bin'))
    if not os.path.isfile(path):
        print(f'skip image checks: no decoded image at {path}')
        return
    data = open(path, 'rb').read()
    expected, source = IMAGE_SHA256, 'the hash recorded in this test'
    evidence = os.path.join(research, 'research', 'evidence', 'xex-image-integrity.json')
    if os.path.isfile(evidence):
        import json
        expected, source = json.load(open(evidence, encoding='utf-8'))['inputs']['image']['sha256'], 'xex-image-integrity.json'
    import hashlib
    got = hashlib.sha256(data).hexdigest()
    check(got == expected, f'the image is the checked BF1943 image (SHA-256 {got[:16]}..., against {source})')
    if got != expected:
        return
    word = lambda a: struct.unpack_from('>I', data, a - BASE)[0]
    check(word(bots.HOOK) == bots.HOOK_ORIGINAL, 'the hook site holds the expected bl 0x8238eb68')
    check(not any(data[bots.CODE - BASE:bots.CODE_END - BASE]), 'the code cave is zero in the image')
    check(not any(data[bots.DATA - BASE:bots.DATA_END - BASE]), 'the data area is zero in the image')
    words, _, _ = bots.build()
    targets = []
    for a, w, _ in words[1:]:
        if w >> 26 == 18 and w & 1:
            targets.append(a + ((w & 0x3FFFFFC) ^ 0x2000000) - 0x2000000)
    # a function start: its prologue (mflr r12), or a leaf right after another function's blr or padding
    # (or an import thunk, after the previous thunk's bctr)
    starts = [t for t in targets if word(t) == 0x7D8802A6 or word(t - 4) in (0x4E800020, 0x4E800420, 0)]
    check(len(starts) == len(targets), f'every call in the bot code targets a function start ({len(starts)} of {len(targets)})')
    syncs = sum(1 for k in range(0, len(data) - 3, 4) if data[k:k + 4] == struct.pack('>I', bots.LWSYNC))
    check(syncs > 0, f'the game\'s own code uses lwsync, as the command read does ({syncs} in the image)')


if __name__ == '__main__':
    layout()
    mailbox()
    if '--image' in sys.argv:
        image()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
