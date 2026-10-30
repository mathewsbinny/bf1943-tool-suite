"""Tests for guestmem.py against a made-up guest memory (no Xenia): mailbox v2 as bots.py lays it out.

  python -B test_guestmem.py

A fake guest holds the bot code (bots.build()'s bytes), the data area with the mailbox's address and magic, and a
block with two bots and two capture points. Checked: attach finds the mailbox; the snapshot reads records, flags
and torn records; input and throttle write the fields and the stamp (the guest's tick) under the sequence lock and
zero them at the end; stop zeroes every command; a wrong block version is refused. Then: wherever the writes land
between the guest's loads (bots.guest_command, the bot code's read), the guest takes a whole command or none;
wherever the bot code's record writes (bots.guest_record) land between guestmem's loads, its state and flag reads
give the old record, the new one, or torn, never an unmarked mix; the bot code without its hook is not "applied"; a
hold stops writing once the match's mailbox is replaced or cleared.
"""
import contextlib
import io
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import guestmem as gm  # noqa: E402

bots = gm.bots
failures = []
BLOCK = 0x40001000


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


class FakeGuest:
    base = 0x100000000

    def __init__(self):
        self.mem = {}
        self.writes = []
        _, code, tail = bots.build()
        self.put(bots.CODE, code + tail)
        hook = [w for a, w, _ in bots.build()[0] if a == bots.HOOK][0]
        self.put(bots.HOOK, struct.pack('>I', hook))
        self.put(bots.DATA, bytes(bots.DATA_END - bots.DATA))
        self.put(BLOCK, bytes(bots.BLOCK_SIZE))

    def put(self, address, data):
        self.mem[address] = bytearray(data)

    def region(self, address, n):
        for start, buf in self.mem.items():
            if start <= address and address + n <= start + len(buf):
                return buf, address - start
        raise OSError(f'cannot read guest {address:#010x} ({n} bytes)')

    def read(self, address, n):
        buf, at = self.region(address, n)
        return bytes(buf[at:at + n])

    def u32(self, address):
        return struct.unpack('>I', self.read(address, 4))[0]

    def write_u32(self, address, value):
        buf, at = self.region(address, 4)
        struct.pack_into('>I', buf, at, value)
        self.writes.append((address, value))


def make_match(g, tick=500):
    w = g.write_u32
    w(bots.DATA + bots.STATE, 1)
    w(bots.DATA + bots.TICK, tick)
    for name, value in (('H_MAGIC', bots.MAGIC), ('H_VERSION', bots.VERSION), ('H_BOTS', bots.MAX_BOTS),
                        ('H_BOT_SIZE', bots.BOT_SIZE), ('H_FLAGS_MAX', bots.MAX_FLAGS), ('H_FLAG_SIZE', bots.FLAG_SIZE),
                        ('H_TICK', tick), ('H_FLAGS', 2)):
        w(BLOCK + getattr(bots, name), value)
    w(bots.DATA + bots.MBOX, BLOCK)
    w(bots.DATA + bots.MBOX_MAGIC, bots.MAGIC)
    for k, (team, pos, seq_end) in enumerate([(1, (10.0, 182.0, -20.0), tick), (2, (-5.5, 190.25, 300.0), tick - 1)]):
        r = BLOCK + bots.BOT0 + bots.BOT_SIZE * k
        w(r + bots.R_SEQ, tick)
        w(r + bots.R_PLAYER, 0x90000000 + k)
        w(r + bots.R_SOLDIER, 0)
        w(r + bots.R_TEAM, team)
        for name, v in zip(('R_X', 'R_Y', 'R_Z'), pos):
            w(r + getattr(bots, name), gm.bits32(v))
        w(r + bots.R_SEQ_END, seq_end)
    for f, (owner, control, pos) in enumerate([(0, 0.0, (-147.89, 202.07, -24.15)), (1, 100.0, (-80.27, 205.16, 53.58))]):
        r = BLOCK + bots.FLAG0 + bots.FLAG_SIZE * f
        w(r + bots.F_SEQ, tick)
        w(r + bots.F_ENTITY, 0xA0000000 + f)
        w(r + bots.F_OWNER, owner)
        w(r + bots.F_CONTROL, gm.bits32(control))
        w(r + bots.F_ENABLED, 1)
        w(r + bots.F_RADIUS, gm.bits32(20.0))
        for name, v in zip(('F_X', 'F_Y', 'F_Z'), pos):
            w(r + getattr(bots, name), gm.bits32(v))
        w(r + bots.F_SEQ_END, tick)


def run(g, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gm.main(['guestmem.py', *args], guest=g, sleep=lambda s: None)
    return code, out.getvalue()


def command(g, k):
    r = gm.bot_record(g, BLOCK, k)
    return r['C_STAMP'], gm.f32(r['C_THROTTLE']), gm.f32(r['C_STRAFE']), gm.f32(r['C_YAW']), \
        r['C_BUTTONS_HI'] << 32 | r['C_BUTTONS_LO']


def main():
    g = FakeGuest()
    code, text = run(g, 'attach')
    check(code == 0 and 'applied' in text and 'not made yet' in text, 'attach: the bot code, no mailbox before a match')
    make_match(g)
    code, text = run(g, 'attach')
    check(code == 0 and f'at {BLOCK:#010x}' in text, 'attach: the mailbox once a match runs')
    s = gm.snapshot(g)
    check(s['mailbox'] == f'{BLOCK:08x}' and len(s['bots']) == 2 and s['bots'][0]['position'] == [10.0, 182.0, -20.0]
          and s['bots'][1]['team'] == 2, 'snapshot: both bots\' records, positions and teams')
    check(not s['bots'][0]['torn'] and s['bots'][1]['torn'], 'snapshot: a record whose two ticks differ is torn')
    check(len(s['flags']) == 2 and s['flags'][1]['owner'] == 1 and s['flags'][1]['control'] == 100.0
          and s['flags'][0]['radius'] == 20.0, 'snapshot: the capture points\' owners, control and radius')
    code, text = run(g, 'bots')
    check(code == 0 and 'slot  1' in text and '(torn)' in text and 'flag 1  owner 1' in text, 'bots prints them')
    # the header's flag count is not locked with the records: a record left from a tick that found more capture
    # points (its tick older than the newest read) is marked stale, the current ones are not
    f1 = BLOCK + bots.FLAG0 + bots.FLAG_SIZE
    g.write_u32(f1 + bots.F_SEQ, 499)
    g.write_u32(f1 + bots.F_SEQ_END, 499)
    fl = gm.flag_records(g, BLOCK)
    check(not fl[0]['stale'] and fl[1]['stale'] and not fl[1]['torn'], 'a flag record older than the newest one is stale')
    code, text = run(g, 'bots')
    check('(stale)' in text, 'bots prints it as stale')
    g.write_u32(f1 + bots.F_SEQ, 500)
    g.write_u32(f1 + bots.F_SEQ_END, 500)

    g.writes.clear()
    code, _ = run(g, 'input', '--throttle', '1', '--yaw', '1.5', '--buttons', 'Sprint', '--slot', '1', '--hold', '0')
    r1 = BLOCK + bots.BOT0 + bots.BOT_SIZE
    stamps = [(a, v) for a, v in g.writes if a == r1 + bots.C_STAMP]
    first = g.writes[:9]
    order_ok = (first[0] == (r1 + bots.C_SEQ, 1) and first[8] == (r1 + bots.C_SEQ, 2)
                and {a for a, _ in first[1:8]} == {r1 + getattr(bots, n) for n in ('C_STAMP', 'C_THROTTLE', 'C_STRAFE',
                                                                                  'C_YAW', 'C_PITCH', 'C_BUTTONS_HI',
                                                                                  'C_BUTTONS_LO')})
    check(code == 0 and stamps[0][1] == 500 and order_ok and all(r1 <= a < r1 + bots.BOT_SIZE for a, _ in g.writes),
          'input: slot 1 only; the sequence odd, the stamp (the guest tick) and fields, the sequence even')
    vals = {a - r1: v for a, v in first}
    check(gm.f32(vals[bots.C_THROTTLE]) == 1.0 and abs(gm.f32(vals[bots.C_YAW]) - 1.5) < 1e-6
          and vals[bots.C_BUTTONS_HI] == 1 << (gm.FACING_VALID - 32)
          and vals[bots.C_BUTTONS_LO] == 1 << gm.BUTTONS['Sprint'], 'input: throttle, yaw, the facing bit and the button')
    check(command(g, 1) == (0, 0.0, 0.0, 0.0, 0), 'input: zeroed at the end of the hold')

    g.writes.clear()
    code, _ = run(g, 'throttle', '0.5', '--hold', '0')
    slots = {(a - BLOCK - bots.BOT0) // bots.BOT_SIZE for a, _ in g.writes}
    thr = [v for a, v in g.writes if (a - BLOCK - bots.BOT0) % bots.BOT_SIZE == bots.C_THROTTLE and v]
    check(code == 0 and slots == set(range(bots.MAX_BOTS)) and thr and gm.f32(thr[0]) == 0.5,
          'throttle: every slot, the value given')

    for k in range(bots.MAX_BOTS):
        gm.write_command(g, BLOCK, k, 499, 1.0, 1.0, 1.0, 0.0, 7)
    code, _ = run(g, 'stop')
    check(code == 0 and all(command(g, k) == (0, 0.0, 0.0, 0.0, 0) for k in range(bots.MAX_BOTS)), 'stop zeroes every command')

    g.write_u32(BLOCK + bots.H_VERSION, 1)
    try:
        gm.snapshot(g)
        check(False, 'a block of another version is refused')
    except RuntimeError:
        check(True, 'a block of another version is refused')
    g.write_u32(bots.DATA + bots.MBOX_MAGIC, 0)
    check(gm.snapshot(g)['mailbox'] is None, 'no magic in the data area: no mailbox (a new match is starting)')
    try:
        run(g, 'stop')
        check(False, 'stop without a mailbox is refused in words')
    except RuntimeError as e:
        check('no mailbox' in str(e), 'stop without a mailbox is refused in words')
    code, text = run(g, '--help')
    check(code == 0 and 'mailbox v2' in text.lower(), '--help')


def whole_commands():
    """write_command against the bot code's read (bots.guest_command, which test_bots.py checks against the code):
    wherever its writes land between the guest's loads, the guest takes the old command, the new one or none."""
    g = FakeGuest()
    make_match(g)
    at = BLOCK + bots.BOT0
    gm.write_command(g, BLOCK, 0, 499, 1.0, 0.5, 1.5, 0.25, 1 << 40 | 1 << 22)
    before = {o: g.u32(at + o) for o in range(bots.C_STAMP, bots.BOT_SIZE, 4)}
    g.writes.clear()
    gm.write_command(g, BLOCK, 0, 500, -1.0, -0.5, -1.5, -0.25, 1 << 12)
    steps = [(a - at, v) for a, v in g.writes]
    after = {o: g.u32(at + o) for o in range(bots.C_STAMP, bots.BOT_SIZE, 4)}
    whole = lambda m: bots.guest_command(m.get, 500)
    seen = set()
    for delay in range(13):
        for gap in (1, 2, 4, 9):
            mem, pending, loads = dict(before), list(steps), [0]

            def word(off):
                v = mem[off]
                loads[0] += 1
                if pending and loads[0] >= delay and (loads[0] - delay) % gap == 0:
                    o, w = pending.pop(0)
                    mem[o] = w
                return v
            seen.add(bots.guest_command(word, 500))
    check(seen == {whole(before), whole(after), None} and whole(before) != whole(after),
          f'write_command: the guest takes the old command, the new one or none, never a mix ({len(seen)} outcomes)')


class RacingGuest(FakeGuest):
    """A fake guest whose bot code writes a record while guestmem reads it: reads go a word at a time in address
    order, and after the n-th word of the record read, the next schedule(n) of the guest's writes land
    (bots.guest_record: the code's order, which test_bots.py checks against the code)."""

    def race(self, record, size, steps, schedule):
        self.record, self.size, self.steps, self.schedule, self.loads = record, size, list(steps), schedule, 0

    def read(self, address, n):
        out = b''
        for a in range(address, address + n, 4):
            out += FakeGuest.read(self, a, min(4, address + n - a))
            if getattr(self, 'steps', None) and self.record <= a < self.record + self.size:
                self.loads += 1
                for _ in range(self.schedule(self.loads)):
                    if self.steps:
                        o, w = self.steps.pop(0)
                        FakeGuest.write_u32(self, self.record + o, w)
        return out


def schedules(steps, loads=40):
    """When the guest's writes land: steady paces (pace writes per word read, after delay words), and two bursts (k
    writes after word a, the rest after word b: a reader preempted mid-read, the case that mixed)."""
    for delay in range(loads):
        for pace in (0.25, 0.5, 1, 2, 3):
            yield lambda n, d=delay, p=pace: int(max(0, n - d) * p) - int(max(0, n - 1 - d) * p)
    for a in range(1, 30):
        for k in range(1, steps):
            for b in range(a + 1, loads):
                yield lambda n, a=a, k=k, b=b: k if n == a else steps if n == b else 0


def whole_records():
    """bot_record and flag_records against the bot code's writes landing between their loads: each gives the old
    record, the new one, or torn, never a mix that is not marked torn (before 2026-10-30 it compared the two ticks of
    one copy, and took a mix: the old opening tick, new fields, the old closing tick)."""
    at = BLOCK + bots.BOT0
    fl = BLOCK + bots.FLAG0
    old_state = {bots.R_PLAYER: 0x90000000, bots.R_SOLDIER: 0x50000001, bots.R_TEAM: 1, bots.R_X: gm.bits32(10.0),
                 bots.R_Y: gm.bits32(182.0), bots.R_Z: gm.bits32(-20.0)}
    new_state = {**old_state, bots.R_SOLDIER: 0x50000002, bots.R_X: gm.bits32(11.0), bots.R_Y: gm.bits32(183.0),
                 bots.R_Z: gm.bits32(-21.0)}
    old_flag = {bots.F_ENTITY: 0xA0000000, bots.F_OWNER: 2, bots.F_CONTROL: gm.bits32(100.0), bots.F_ENABLED: 1,
                bots.F_RADIUS: gm.bits32(20.0), bots.F_X: gm.bits32(-80.0), bots.F_Y: gm.bits32(205.0),
                bots.F_Z: gm.bits32(53.0)}
    new_flag = {**old_flag, bots.F_OWNER: 1, bots.F_CONTROL: gm.bits32(50.0), bots.F_X: gm.bits32(-81.0),
                bots.F_Z: gm.bits32(54.0)}
    state_of = lambda r: tuple(r[n] for n in ('R_SEQ', 'R_SOLDIER', 'R_X', 'R_Y', 'R_Z', 'R_SEQ_END'))
    flag_of = lambda f: (f['owner'], f['control'], tuple(f['position']))
    for what, base, size, first, last, old, new, read in (
            ('bot_record', at, bots.C_STAMP, bots.R_SEQ, bots.R_SEQ_END, old_state, new_state,
             lambda g: (lambda r: (state_of(r), r['torn']))(gm.bot_record(g, BLOCK, 0))),
            ('flag_records', fl, bots.FLAG_SIZE, bots.F_SEQ, bots.F_SEQ_END, old_flag, new_flag,
             lambda g: (lambda f: (flag_of(f), f['torn']))(gm.flag_records(g, BLOCK)[0]))):
        g = RacingGuest()
        make_match(g)
        g.write_u32(BLOCK + bots.H_FLAGS, 1)

        def guest(tick, fields):
            g.steps = None
            for o, w in bots.guest_record(first, last, tick, fields):
                g.write_u32(base + o, w)
            return g
        want = {read(guest(499, old))[0], read(guest(500, new))[0]}
        seen, mixed = set(), []
        steps = bots.guest_record(first, last, 500, new)
        for schedule in schedules(len(steps)):
            guest(499, old).race(base, size, steps, schedule)
            got, torn = read(g)
            if not torn:
                seen.add(got)
                if got not in want:
                    mixed.append(got)
        check(not mixed and seen == want and len(want) == 2,
              f'{what}: the old record or the new one, never an unmarked mix, wherever the guest\'s writes land '
              f'between its loads ({len(seen)} whole outcomes{", mixes " + str(mixed[:2]) if mixed else ""})')


def unhooked():
    g = FakeGuest()
    g.put(bots.HOOK, bytes(4))
    code, text = run(g, 'attach')
    check(gm.bot_code_state(g) == ('unhooked', 0) and code == 1 and 'unhooked' in text,
          'attach: the bot code without its hook (zeroed) is not applied, exit 1')
    g.put(bots.HOOK, struct.pack('>I', bots.HOOK_ORIGINAL))
    check(gm.bot_code_state(g)[0] == 'unhooked', 'nor with the game\'s own bl at the hook')
    make_match(g)
    try:
        run(g, 'stop')
        check(False, 'commands are refused while the hook is not the bot code\'s')
    except RuntimeError as e:
        check('unhooked' in str(e), 'commands are refused while the hook is not the bot code\'s')
    g = FakeGuest()
    g.mem[bots.CODE] = bytearray(len(g.mem[bots.CODE]))
    g.put(bots.HOOK, struct.pack('>I', bots.HOOK_ORIGINAL))
    check(gm.bot_code_state(g)[0] == 'absent', 'a zero cave with the game\'s own hook: absent (a normal build)')


def match_change():
    """A hold that outlives its match: once the data area names another block, nothing more goes to the old one
    (not even the zeroing), and the command says so."""
    g = FakeGuest()
    make_match(g)
    new = BLOCK + 0x4000
    g.put(new, bytes(bots.BLOCK_SIZE))
    rounds = []

    def sleep(s):
        rounds.append(s)
        if len(rounds) == 2:
            g.write_u32(bots.DATA + bots.MBOX, new)
    g.writes.clear()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gm.main(['guestmem.py', 'throttle', '1', '--slot', '0', '--hold', '10'], guest=g, sleep=sleep)
    swap = g.writes.index((bots.DATA + bots.MBOX, new))
    late = [a for a, _ in g.writes[swap + 1:]]
    check(code == 1 and 'replaced' in out.getvalue() and len(rounds) == 2 and not late,
          f'a new match during a hold: no writes after it to either block ({len(late)}), exit 1')
    g = FakeGuest()
    make_match(g)
    g.writes.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        code = gm.main(['guestmem.py', 'throttle', '1', '--slot', '0', '--hold', '10'], guest=g,
                       sleep=lambda s: g.write_u32(bots.DATA + bots.MBOX_MAGIC, 0))
    check(code == 1 and g.writes[-1] == (bots.DATA + bots.MBOX_MAGIC, 0), 'the match ending (magic cleared): the same')


if __name__ == '__main__':
    main()
    whole_commands()
    whole_records()
    unhooked()
    match_change()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
