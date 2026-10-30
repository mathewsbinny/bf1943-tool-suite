"""Tests for guestmem.py against a made-up guest memory (no Xenia): mailbox v2 as bots.py lays it out.

  python -B test_guestmem.py

A fake guest holds the bot code (bots.build()'s bytes), the data area with the mailbox's address and magic, and a
block with two bots and two capture points. Checked: attach finds the mailbox; the snapshot reads records, flags
and torn records; input and throttle write the fields, the stamp (the guest's tick) and zero them at the end; stop
zeroes every command; a wrong block version is refused.
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

    g.writes.clear()
    code, _ = run(g, 'input', '--throttle', '1', '--yaw', '1.5', '--buttons', 'Sprint', '--slot', '1', '--hold', '0')
    stamps = [(a, v) for a, v in g.writes if a == BLOCK + bots.BOT0 + bots.BOT_SIZE + bots.C_STAMP]
    fields = [a for a, v in g.writes if a != BLOCK + bots.BOT0 + bots.BOT_SIZE + bots.C_STAMP]
    order_ok = g.writes[6][0] == BLOCK + bots.BOT0 + bots.BOT_SIZE + bots.C_STAMP
    check(code == 0 and stamps[0][1] == 500 and order_ok and all(BLOCK + bots.BOT0 + bots.BOT_SIZE <= a
                                                                  < BLOCK + bots.BOT0 + 2 * bots.BOT_SIZE for a in fields),
          'input: slot 1 only, the fields first and the stamp (the guest tick) last')
    first = g.writes[:7]
    vals = {a - BLOCK - bots.BOT0 - bots.BOT_SIZE: v for a, v in first}
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


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
