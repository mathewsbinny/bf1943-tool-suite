"""BF1943 guest memory from outside Xenia (probe N0; mailbox v2 for milestone N4): attach to a running Xenia, find the
guest's memory, check the game's image and the bots experiment's code in it, read the bots' mailbox and write their
commands.

  python -B guestmem.py attach                  find Xenia and the guest base; check MZ at 0x82000000, the bot code
                                                and the mailbox
  python -B guestmem.py bots                    the bot code's state and tick, each bot's state record (player,
                                                soldier, team, position) and command, and the capture points' records
  python -B guestmem.py throttle <value> [--slot N|all] [--hold SECONDS]
                                                the bots' forward input (a float: 0 stands, 1 walks) for --hold
                                                seconds (default 10), then zero
  python -B guestmem.py input [--throttle F] [--strafe F] [--yaw RAD] [--pitch RAD] [--buttons Jump,Sprint,...]
                              [--slot N|all] [--press SECONDS] [--hold SECONDS]
                                                drive the bots for --hold seconds (default 10), then zero their
                                                commands; options not given are 0. --yaw or --pitch also sets button
                                                bit 40 (the facing is valid); --press holds the buttons that long and
                                                then releases them (edge-triggered actions)
  python -B guestmem.py stop                    every bot's command back to zero (the bots stand)
  python -B guestmem.py watch [seconds] [--every S] [--slot N] [--json out.json]
                                                the tick, records, health and aim every S seconds (default 1)
  options: --pid <Xenia process id>

Guest memory is a named file mapping inside a 4 GB reservation (Xenia, memory.cc); this reads and writes it with
ReadProcessMemory / WriteProcessMemory at reservation base + guest address. The base is found by trying the
addresses Xenia reserves at (1 << n for n = 32..46) and then every mapped region, and is accepted only where the
guest's 0x82000000 starts with MZ. Guest memory is big-endian.

Mailbox v2 (bf1943-xenia-kit/bots.py, its MBOX_MAGIC ... BLOCK_SIZE names): the bot code allocates one block per
match and puts its address in its data area. Each bot has a state record the guest writes every tick (read whole
under its two-word lock: the closing tick, the record, the opening tick, taken when the two agree) and a command
record the host writes under a sequence lock (C_SEQ odd while it is written, even and new once it is whole; the
guest takes only a whole record). A command counts only while its stamp (the tick it answered) is at most STALE
ticks old, so `input` and `throttle` keep rewriting it while they hold, and the bots stand once they stop; they
stop writing when the match's mailbox goes away or is replaced. `attach` reports the bots as applied only when the
code is in place and the hook branches to it. Only command records are written, and only by `throttle`, `input`
and `stop`; nothing else in Xenia or the game is changed. Soldier health (soldier + 0x3c) and aim (the object at soldier + 0x230: yaw + 4, pitch + 8, radians) are
read as research/evidence/bot-interfaces.json gives them (operation-rising-sun). The mailbox's real host is navhost
(project-opfor-bf1943 TnT/Code/Engine/AI/Bots/navhost); this is the probe and test tool.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(SUITE, 'bf1943-xenia-kit'))
sys.dont_write_bytecode = True
import bots  # noqa: E402

XENIA_NAMES = ('xenia_canary_netfix.exe', 'xenia_canary.exe')
IMAGE = 0x82000000
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, PROCESS_VM_WRITE, PROCESS_VM_OPERATION = 0x400, 0x10, 0x20, 0x8
MEM_COMMIT, MEM_MAPPED = 0x1000, 0x40000
# EntryInputActionEnum (the game's reflection): actions 8-39 are bits of the frame's u64 at + 0x78; bit 40 marks the
# absolute facing as valid
BUTTONS = {name: bit for bit, name in enumerate(
    'FireCountermeasure AltFire CycleRadioChannel Zoom Jump ChangeVehicle ChangeEntry ChangePose Interact '
    'ToggleParachute CyclePrimaryInventory CycleSecondaryInventory SelectMeleeWeapon Reload ToggleCamera Sprint '
    'IngameMenu ScoreboardMenu CarHorn GearUp GearDown GiveOrder SelectWeapon1 SelectWeapon2 SelectWeapon3 '
    'SelectWeapon4 MapZoom SnapView SelectAll Select1 Select2 Select3'.split(), 8)}
FACING_VALID = 40
MATCH_CHANGED = 'the match ended or another replaced its mailbox during the hold: stopped, nothing more written'
k32 = ctypes.WinDLL('kernel32', use_last_error=True)


class MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p), ('AllocationProtect', wt.DWORD),
                ('PartitionId', wt.WORD), ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD), ('Protect', wt.DWORD),
                ('Type', wt.DWORD)]


class PE32(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                ('szExeFile', ctypes.c_char * 260)]


k32.OpenProcess.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = k32.ReadProcessMemory.argtypes


def xenia_processes():
    """[(pid, exe name)] of running Xenia processes."""
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    found = []
    entry = PE32()
    entry.dwSize = ctypes.sizeof(PE32)
    ok = k32.Process32First(snap, ctypes.byref(entry))
    while ok:
        name = entry.szExeFile.decode('ascii', 'replace')
        if name.lower() in XENIA_NAMES:
            found.append((entry.th32ProcessID, name))
        ok = k32.Process32Next(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return found


class Guest:
    def __init__(self, pid=None):
        if pid is None:
            procs = xenia_processes()
            if not procs:
                raise RuntimeError('Xenia is not running: start the game (bf1943-xenia-kit launch) first')
            if len(procs) > 1:
                raise RuntimeError(f'more than one Xenia is running ({procs}): pass --pid')
            pid, self.exe = procs[0]
        else:
            self.exe = '?'
        self.pid = pid
        self.handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_WRITE |
                                      PROCESS_VM_OPERATION, False, pid)
        if not self.handle:
            raise PermissionError(f'cannot open Xenia (process {pid}), Windows error {ctypes.get_last_error()}: '
                                  f'run this as the same user as Xenia')
        self.base, self.how = self.find_base()

    def raw_read(self, address, size):
        buf = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(done)) \
                or done.value != size:
            return None
        return buf.raw

    def find_base(self):
        for n in range(32, 47):
            if self.raw_read((1 << n) + IMAGE, 2) == b'MZ':
                return 1 << n, f'reserved at 1 << {n}'
        mbi, address = MBI(), 0
        while k32.VirtualQueryEx(self.handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            start = mbi.BaseAddress or 0
            if mbi.State == MEM_COMMIT and mbi.Type == MEM_MAPPED and start >= IMAGE \
                    and self.raw_read(start, 2) == b'MZ':
                return start - IMAGE, 'a mapped region starting with MZ'
            address = start + mbi.RegionSize
        raise RuntimeError('no guest memory with MZ at 0x82000000 in this Xenia: is a game running?')

    def read(self, guest, size):
        data = self.raw_read(self.base + guest, size)
        if data is None:
            raise OSError(f'cannot read guest {guest:#010x} ({size} bytes)')
        return data

    def u32(self, guest):
        return struct.unpack('>I', self.read(guest, 4))[0]

    def write_u32(self, guest, value):
        data = struct.pack('>I', value)
        done = ctypes.c_size_t()
        if not k32.WriteProcessMemory(self.handle, ctypes.c_void_p(self.base + guest), data, 4, ctypes.byref(done)) \
                or done.value != 4:
            raise OSError(f'cannot write guest {guest:#010x}, Windows error {ctypes.get_last_error()}')


def f32(bits):
    return struct.unpack('>f', struct.pack('>I', bits))[0]


def bits32(value):
    return struct.unpack('>I', struct.pack('>f', value))[0]


def button_names(mask):
    names = [n for n, b in BUTTONS.items() if mask >> b & 1] + (['facing'] if mask >> FACING_VALID & 1 else [])
    return ','.join(names) or 'none'


def bot_code_state(g):
    """'applied' (the bot code, and the hook branching to it), 'unhooked' (the code without that hook: it never
    runs), 'absent' (the cave is zero and the hook the game's own: a normal build) or 'other', and the hook word."""
    words, code, tail = bots.build()
    blob = code + tail
    have = g.read(bots.CODE, len(blob))
    hook = g.u32(bots.HOOK)
    if have == blob:
        return ('applied' if hook == dict((a, w) for a, w, _ in words)[bots.HOOK] else 'unhooked'), hook
    return ('absent' if not any(have) and hook == bots.HOOK_ORIGINAL else 'other'), hook


def block_of(g):
    """The mailbox block's guest address, or None (no match running yet, or not mailbox v2)."""
    magic, block = struct.unpack('>II', g.read(bots.DATA + bots.MBOX_MAGIC, 8))
    if magic != bots.MAGIC or not block:
        return None
    head = struct.unpack('>4I', g.read(block, 16))
    if head != (bots.MAGIC, bots.VERSION, bots.MAX_BOTS, bots.BOT_SIZE):
        raise RuntimeError(f'the mailbox at {block:#010x} is not v{bots.VERSION} as bots.py lays it out: {head}')
    return block


def words(g, address, count):
    return struct.unpack(f'>{count}I', g.read(address, 4 * count))


def record_words(g, address, first, last, count):
    """A state or flag record's count words, read whole under its two-word lock (bots.py; bots.host_record): the
    closing tick (last), the words, then the opening tick (first), each read after the one before; whole when the two
    ticks are the same, else tried again, bots.TRIES times in all. (words, torn): torn when no read was whole (the
    last read's words, which may mix two ticks). The two ticks of one copy are not compared: a copy can take the old
    opening tick, new fields and the old closing tick."""
    for _ in range(bots.TRIES):
        end = g.u32(address + last)
        w = words(g, address, count)
        if g.u32(address + first) == end:
            return w, False
    return w, True


def bot_record(g, block, k):
    """Slot k's state record (read whole, record_words: torn when it could not be) and command record."""
    at = block + bots.BOT0 + bots.BOT_SIZE * k
    w, torn = record_words(g, at, bots.R_SEQ, bots.R_SEQ_END, bots.R_SEQ_END // 4 + 1)
    w += words(g, at + bots.C_STAMP, (bots.BOT_SIZE - bots.C_STAMP) // 4)
    r = {name: w[getattr(bots, name) // 4] for name in ('R_SEQ', 'R_PLAYER', 'R_SOLDIER', 'R_TEAM', 'R_X', 'R_Y', 'R_Z',
                                                        'R_SEQ_END', 'C_STAMP', 'C_THROTTLE', 'C_STRAFE', 'C_YAW',
                                                        'C_PITCH', 'C_BUTTONS_HI', 'C_BUTTONS_LO')}
    r['torn'] = torn
    return r


def flag_records(g, block):
    """The capture points' records, each read whole under its own two ticks. The header's count (H_FLAGS) is not
    locked with them: a record whose tick is older than the newest one read is left from a tick that found more capture
    points (or the count was read mid-update), and is marked stale."""
    n = min(g.u32(block + bots.H_FLAGS), bots.MAX_FLAGS)
    out = []
    for f in range(n):
        w, torn = record_words(g, block + bots.FLAG0 + bots.FLAG_SIZE * f, bots.F_SEQ, bots.F_SEQ_END,
                               bots.FLAG_SIZE // 4)
        get = lambda name: w[getattr(bots, name) // 4]
        out.append(dict(index=f, entity=f'{get("F_ENTITY"):08x}', owner=get('F_OWNER'),
                        control=round(f32(get('F_CONTROL')), 1), enabled=get('F_ENABLED'),
                        radius=round(f32(get('F_RADIUS')), 2), position=[round(f32(get(n)), 2) for n in ('F_X', 'F_Y', 'F_Z')],
                        tick=get('F_SEQ'), torn=torn))
    newest = max((r['tick'] for r in out if not r['torn']), default=0)
    for r in out:
        r['stale'] = not r['torn'] and r['tick'] < newest
    return out


def write_command(g, block, k, stamp, throttle=0.0, strafe=0.0, yaw=0.0, pitch=0.0, buttons=0):
    """Slot k's command under its sequence lock (bots.py, C_SEQ; as navhost writes it): the sequence odd, the stamp
    and fields, then the sequence even and new, aligned 32-bit words. The guest takes none of it until the end."""
    at = block + bots.BOT0 + bots.BOT_SIZE * k
    odd = (g.u32(at + bots.C_SEQ) + 1 | 1) & 0xFFFFFFFF
    g.write_u32(at + bots.C_SEQ, odd)
    for name, value in (('C_STAMP', stamp), ('C_THROTTLE', bits32(throttle)), ('C_STRAFE', bits32(strafe)),
                        ('C_YAW', bits32(yaw)), ('C_PITCH', bits32(pitch)), ('C_BUTTONS_HI', buttons >> 32),
                        ('C_BUTTONS_LO', buttons & 0xFFFFFFFF)):
        g.write_u32(at + getattr(bots, name), value)
    g.write_u32(at + bots.C_SEQ, (odd + 1) & 0xFFFFFFFF)


def snapshot(g):
    d = bots.DATA
    block = block_of(g)
    s = dict(time=time.strftime('%I:%M:%S %p'), clock=time.time(), state=g.u32(d + bots.STATE),
             manager=g.u32(d + bots.MANAGER), tick=g.u32(d + bots.TICK),
             mailbox=f'{block:08x}' if block else None, bots=[], flags=[])
    if not block:
        return s
    s['flags'] = flag_records(g, block)
    for k in range(bots.MAX_BOTS):
        r = bot_record(g, block, k)
        if not r['R_PLAYER']:
            continue
        b = dict(slot=k, player=f'{r["R_PLAYER"]:08x}', team=r['R_TEAM'], record_tick=r['R_SEQ'], torn=r['torn'],
                 position_bits=f'{r["R_X"]:08x} {r["R_Y"]:08x} {r["R_Z"]:08x}',
                 position=[round(f32(r[n]), 3) for n in ('R_X', 'R_Y', 'R_Z')],
                 command=dict(stamp=r['C_STAMP'], age=(s['tick'] - r['C_STAMP']) & 0xFFFFFFFF,
                              throttle=f32(r['C_THROTTLE']), strafe=f32(r['C_STRAFE']), yaw=f32(r['C_YAW']),
                              pitch=f32(r['C_PITCH']), buttons=button_names(r['C_BUTTONS_HI'] << 32 | r['C_BUTTONS_LO'])))
        soldier = r['R_SOLDIER']
        if soldier:
            b['soldier'] = f'{soldier:08x}'
            try:
                b['health'] = round(f32(g.u32(soldier + 0x3c)), 2)
                aim = g.u32(soldier + 0x230)
                if aim:
                    b.update(aim_yaw=round(f32(g.u32(aim + 4)), 4), aim_pitch=round(f32(g.u32(aim + 8)), 4))
            except OSError:
                b['soldier'] = 'unreadable'
        s['bots'].append(b)
    return s


def show(s, only=None):
    print(f'{s["time"]}  tick {s["tick"]}  mailbox {s["mailbox"] or "none"}  bots {len(s["bots"])}  '
          f'capture points {len(s["flags"])}')
    for f in s['flags'] if only is None else []:
        x, y, z = f['position']
        print(f'  flag {f["index"]}  owner {f["owner"]}  control {f["control"]:5.1f}  radius {f["radius"]:g}  '
              f'enabled {f["enabled"]}  at {x:9.2f} {y:8.2f} {z:9.2f}{"  (torn)" if f["torn"] else "  (stale)" if f.get("stale") else ""}')
    for b in s['bots']:
        if only is not None and b['slot'] != only:
            continue
        x, y, z = b['position']
        c = b['command']
        extra = (f'  health {b["health"]:g}' if 'health' in b else '  no soldier') + \
            (f'  aim {b["aim_yaw"]:+.3f} {b["aim_pitch"]:+.3f}' if 'aim_yaw' in b else '')
        cmd = (f'  cmd age {c["age"]} thr {c["throttle"]:+.2f} str {c["strafe"]:+.2f} yaw {c["yaw"]:+.3f} '
               f'{c["buttons"]}' if c['age'] <= bots.STALE else '  cmd stale')
        print(f'  slot {b["slot"]:2d}  team {b["team"]}  player {b["player"]}  position {x:9.3f} {y:8.3f} {z:9.3f}'
              f'{extra}{cmd}{"  (torn)" if b["torn"] else ""}')


def slots_of(opts):
    n = opts.get('--slot', 'all')
    if n == 'all':
        return list(range(bots.MAX_BOTS))
    k = int(n)
    if not 0 <= k < bots.MAX_BOTS:
        raise ValueError(f'--slot: 0 to {bots.MAX_BOTS - 1}, or all')
    return [k]


def still_block(g, block):
    """Whether the data area still names this block (its magic and address): a new match replaces or clears it."""
    return struct.unpack('>II', g.read(bots.DATA + bots.MBOX_MAGIC, 8)) == (bots.MAGIC, block)


def drive(g, block, slots, hold, press, throttle=0.0, strafe=0.0, yaw=None, pitch=None, mask=0, sleep=time.sleep):
    """Keeps the commands fresh for hold seconds (about 60 times a second, stamped with the guest's tick), the
    buttons in mask for the first press seconds (all of hold when press is None), then zeroes them. Each round
    first checks that the data area still names the block: once a match ends or another replaces it, nothing more
    is written (not even the zeroing: the old block is no longer the bots', and a new one starts zeroed). True when
    held to the end, False when the match changed."""
    facing = 1 << FACING_VALID if yaw is not None or pitch is not None else 0
    start = time.time()
    ours = True
    try:
        while True:
            t = time.time() - start
            buttons = facing | (mask if press is None or t < press else 0)
            tick = g.u32(bots.DATA + bots.TICK)
            ours = still_block(g, block)
            if not ours:
                return False
            for k in slots:
                write_command(g, block, k, tick, throttle, strafe, yaw or 0.0, pitch or 0.0, buttons)
            if t >= hold:
                return True
            sleep(1 / 60)
    finally:
        if ours and still_block(g, block):
            for k in slots:
                write_command(g, block, k, 0)


def main(argv, guest=None, sleep=time.sleep):
    if any(a in ('-h', '--help') for a in argv[1:]) or len(argv) < 2 or \
            argv[1] not in ('attach', 'bots', 'throttle', 'input', 'stop', 'watch'):
        print(__doc__)
        return 0 if len(argv) > 1 and argv[1] in ('-h', '--help') else 1
    opts = dict(zip(argv[2:], argv[3:]))
    pid = int(opts['--pid']) if '--pid' in opts else None
    g = guest or Guest(pid)
    state, hook = bot_code_state(g)
    cmd = argv[1]
    if cmd == 'attach':
        print(f'Xenia {getattr(g, "exe", "?")} process {getattr(g, "pid", "?")}: guest base {g.base:#x} '
              f'({getattr(g, "how", "?")}); MZ at {IMAGE:#010x}')
        print(f'bot code at {bots.CODE:#010x}: {state}; hook {bots.HOOK:#010x} = {hook:#010x}')
        if state == 'applied':
            block = block_of(g)
            print(f'mailbox v{bots.VERSION}: ' + (f'at {block:#010x}' if block else 'not made yet (no match with bots)'))
        return 0 if state == 'applied' else 1
    if state != 'applied':
        raise RuntimeError(f'the bots experiment is not in guest memory ({state}): build the kit with --experiment-bots')
    if cmd in ('bots', 'watch'):
        seconds = float(argv[2]) if cmd == 'watch' and len(argv) > 2 and not argv[2].startswith('--') else 0
        every = float(opts.get('--every', 1))
        only = int(opts['--slot']) if '--slot' in opts else None
        shots = []
        end = time.time() + seconds
        while True:
            shots.append(snapshot(g))
            show(shots[-1], only)
            if time.time() + every > end:
                break
            sleep(every)
        if '--json' in opts:
            with open(opts['--json'], 'w', encoding='utf-8', newline='\n') as f:
                json.dump(dict(pid=getattr(g, 'pid', None), exe=getattr(g, 'exe', None), base=f'{g.base:#x}',
                               snapshots=shots), f, indent=1)
        return 0
    block = block_of(g)
    if not block:
        raise RuntimeError('no mailbox yet: the bots are made once a match runs and the host player has a soldier')
    if cmd == 'stop':
        for k in range(bots.MAX_BOTS):
            write_command(g, block, k, 0)
        print(f'every command zeroed ({bots.MAX_BOTS} slots)')
        return 0
    if cmd == 'throttle':
        if len(argv) < 3 or argv[2].startswith('--'):
            raise ValueError('throttle needs a value, such as 0 or 1')
        opts = dict(zip(argv[3:], argv[4:]))
        if not drive(g, block, slots_of(opts), float(opts.get('--hold', 10)), None, throttle=float(argv[2]), sleep=sleep):
            print(MATCH_CHANGED)
            return 1
        print(f'throttle {float(argv[2]):g} held {float(opts.get("--hold", 10)):g} s, then zeroed')
        return 0
    given = argv[2::2]
    allowed = ('--throttle', '--strafe', '--yaw', '--pitch', '--buttons', '--slot', '--press', '--hold', '--pid')
    if any(a not in allowed for a in given) or len(argv) % 2:
        raise ValueError(f'input: options are {", ".join(allowed[:-1])} (each with a value); got {" ".join(argv[2:])}')
    mask = 0
    for name in opts.get('--buttons', 'none').split(','):
        if name == 'none':
            continue
        if name not in BUTTONS:
            raise ValueError(f'unknown button {name}: one of {", ".join(BUTTONS)}')
        mask |= 1 << BUTTONS[name]
    num = lambda k: float(opts[k]) if k in opts else None
    if not drive(g, block, slots_of(opts), float(opts.get('--hold', 10)), num('--press'), throttle=num('--throttle') or 0.0,
                 strafe=num('--strafe') or 0.0, yaw=num('--yaw'), pitch=num('--pitch'), mask=mask, sleep=sleep):
        print(MATCH_CHANGED)
        return 1
    print(f'held {float(opts.get("--hold", 10)):g} s, then zeroed')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(SUITE, 'core'))
    from bfbc2core import errors
    errors.setup('guestmem', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
