r"""Harness: guestmem's process access and the command line's other paths (no Xenia)

guestmem.Guest on this test's own process only (never Xenia: Guest is opened on this process's id, or made with the
Xenia process lookup replaced): reading and writing a buffer of this process through ReadProcessMemory and
WriteProcessMemory, an address that cannot be read or written, a process that cannot be opened, no Xenia and two
Xenias, and the guest base search over this process (no MZ at 0x82000000 here). Then the command line on a made-up
guest memory (as tests/test_guestmem.py lays out mailbox v2): a bot with a soldier (health, aim) and one whose soldier
cannot be read, the flags hidden for one slot, watch with --json and a pause between snapshots, and each argument
refusal (a slot out of range, throttle without a value, an unknown option or button), a hold the match ends.
"""
import contextlib
import ctypes
import io
import json
import os
import struct
import unittest

import bots
import guestmem as gm

BLOCK = 0x40001000
SOLDIER, AIM = 0x40100000, 0x40100400


def test_own_process_memory():
    if os.name != 'nt':
        raise unittest.SkipTest('Windows only')
    g = gm.Guest.__new__(gm.Guest)
    g.handle = gm.k32.OpenProcess(gm.PROCESS_QUERY_INFORMATION | gm.PROCESS_VM_READ | gm.PROCESS_VM_WRITE |
                                  gm.PROCESS_VM_OPERATION, False, os.getpid())
    assert g.handle, 'this process can open itself'
    try:
        buf = ctypes.create_string_buffer(b'\x12\x34\x56\x78' + bytes(12), 16)
        g.base = ctypes.addressof(buf)
        assert g.read(0, 4) == b'\x12\x34\x56\x78' and g.u32(0) == 0x12345678
        g.write_u32(4, 0xCAFEF00D)
        g.write(8, b'\x01\x02')
        assert buf.raw[4:10] == b'\xca\xfe\xf0\x0d\x01\x02'
        g.base = 0
        assert g.raw_read(0, 4) is None
        for fn, args in ((g.read, (0, 4)), (g.write, (0, b'abcd'))):
            try:
                fn(*args)
                failed = False
            except OSError:
                failed = True
            assert failed, fn.__name__
    finally:
        gm.k32.CloseHandle(g.handle)


def test_open_and_find():
    if os.name != 'nt':
        raise unittest.SkipTest('Windows only')
    found = gm.xenia_processes()
    assert isinstance(found, list) and all(name.lower() in gm.XENIA_NAMES for _pid, name in found)
    real = gm.xenia_processes
    try:
        for procs, words in (([], 'Xenia is not running'), ([(1, 'a'), (2, 'b')], 'more than one Xenia')):
            gm.xenia_processes = lambda procs=procs: procs
            try:
                gm.Guest()
                why = None
            except RuntimeError as e:
                why = str(e)
            assert why and words in why, why
    finally:
        gm.xenia_processes = real
    try:
        gm.Guest(pid=0)
        why = None
    except PermissionError as e:
        why = str(e)
    assert why and 'cannot open Xenia (process 0)' in why
    try:
        g = gm.Guest(pid=os.getpid())       # this process: no guest memory, unless a mapped file here starts with MZ
        assert g.how == 'a mapped region starting with MZ' and g.exe == '?'
    except RuntimeError as e:
        assert 'no guest memory with MZ' in str(e)


class FakeGuest:
    base = 0x100000000
    pid, exe, how = 1234, 'xenia_canary_netfix.exe', 'made up'

    def __init__(self):
        self.mem = {}
        words, code, tail = bots.build()
        self.put(bots.CODE, code + tail)
        self.put(bots.HOOK, struct.pack('>I', dict((a, w) for a, w, _ in words)[bots.HOOK]))
        self.put(bots.DATA, bytes(bots.DATA_END - bots.DATA))
        self.put(BLOCK, bytes(bots.BLOCK_SIZE))
        self.put(SOLDIER, bytes(0x400))
        self.put(AIM, bytes(16))

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

    def write(self, address, data):
        buf, at = self.region(address, len(data))
        buf[at:at + len(data)] = data

    def write_u32(self, address, value):
        self.write(address, struct.pack('>I', value))


def match(g, tick=500):
    w = g.write_u32
    w(bots.DATA + bots.TICK, tick)
    for name, value in (('H_MAGIC', bots.MAGIC), ('H_VERSION', bots.VERSION), ('H_BOTS', bots.MAX_BOTS),
                        ('H_BOT_SIZE', bots.BOT_SIZE), ('H_FLAGS', 1)):
        w(BLOCK + getattr(bots, name), value)
    w(bots.DATA + bots.MBOX, BLOCK)
    w(bots.DATA + bots.MBOX_MAGIC, bots.MAGIC)
    for k, soldier in enumerate((SOLDIER, 0x7000)):                 # the second soldier cannot be read
        r = BLOCK + bots.BOT0 + bots.BOT_SIZE * k
        w(r + bots.R_SEQ, tick)
        w(r + bots.R_SEQ_END, tick)
        w(r + bots.R_PLAYER, 0x90000000 + k)
        w(r + bots.R_SOLDIER, soldier)
        w(r + bots.R_TEAM, 1 + k)
    w(SOLDIER + 0x3C, gm.bits32(87.5))
    w(SOLDIER + 0x230, AIM)
    w(AIM + 4, gm.bits32(1.25))
    w(AIM + 8, gm.bits32(-0.5))
    f = BLOCK + bots.FLAG0
    w(f + bots.F_SEQ, tick)
    w(f + bots.F_SEQ_END, tick)
    w(f + bots.F_RADIUS, gm.bits32(20.0))


def run(g, *args, sleep=lambda s: None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gm.main(['guestmem.py', *args], guest=g, sleep=sleep)
    return code, out.getvalue()


def refused(g, *args):
    try:
        run(g, *args)
    except (ValueError, RuntimeError) as e:
        return str(e)
    return None


def test_snapshot_soldiers_and_watch(tmp_path):
    g = FakeGuest()
    match(g)
    s = gm.snapshot(g)
    first, second = s['bots']
    assert first['soldier'] == f'{SOLDIER:08x}' and first['health'] == 87.5
    assert (first['aim_yaw'], first['aim_pitch']) == (1.25, -0.5)
    assert second['soldier'] == 'unreadable' and 'health' not in second
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        gm.show(s, only=0)
    text = out.getvalue()
    assert 'health 87.5' in text and 'aim +1.250 -0.500' in text and 'flag 0' not in text and 'slot  1' not in text
    pauses = []
    clock = [0.0]
    real_time = gm.time.time

    def sleep(seconds):
        pauses.append(seconds)
        clock[0] += seconds
    gm.time.time = lambda: 1000.0 + clock[0]
    try:
        out_json = tmp_path / 'watch.json'
        code, text = run(g, 'watch', '2', '--every', '1', '--slot', '0', '--json', str(out_json), sleep=sleep)
    finally:
        gm.time.time = real_time
    assert code == 0 and pauses == [1.0, 1.0]
    saved = json.loads(out_json.read_text(encoding='utf-8'))
    assert saved['pid'] == 1234 and saved['base'] == '0x100000000' and len(saved['snapshots']) == 3


def test_argument_refusals():
    g = FakeGuest()
    match(g)
    assert 'needs a value' in refused(g, 'throttle')
    assert 'needs a value' in refused(g, 'throttle', '--slot', '1')
    assert '--slot: 0 to' in refused(g, 'throttle', '1', '--slot', str(bots.MAX_BOTS))
    assert 'input: options are' in refused(g, 'input', '--speed', '1')
    assert 'input: options are' in refused(g, 'input', '--throttle')
    assert 'unknown button Fly' in refused(g, 'input', '--buttons', 'Jump,Fly')
    code, text = run(g, 'input', '--buttons', 'none', '--hold', '0', '--slot', '0')
    assert code == 0 and 'held 0 s' in text
    real = gm.still_block
    gm.still_block = lambda g_, block: False                      # the match ended before the first write
    try:
        code, text = run(g, 'input', '--throttle', '1', '--hold', '1')
    finally:
        gm.still_block = real
    assert code == 1 and gm.MATCH_CHANGED in text


def test_command_layout_guard():
    g = FakeGuest()
    match(g)
    saved = bots.C_BUTTONS_LO
    bots.C_BUTTONS_LO = saved + 4
    try:
        gm.write_command(g, BLOCK, 0, 1)
        why = None
    except RuntimeError as e:
        why = str(e)
    finally:
        bots.C_BUTTONS_LO = saved
    assert why and 'not seven consecutive words' in why
