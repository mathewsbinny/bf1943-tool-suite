"""BF1943 guest memory from outside Xenia (probe N0): attach to a running Xenia, find the guest's memory, check the
game's image and the bots experiment's code in it, read the bots' mailbox and write its command word.

  python -B guestmem.py attach                  find Xenia and the guest base; check MZ at 0x82000000 and the bot code
  python -B guestmem.py bots                    the bot code's state, its tick, each bot's player and last position
  python -B guestmem.py throttle <value>        write the bots' forward input (a float: 0 stands, 1 walks) and read it back
  python -B guestmem.py input [--throttle F] [--strafe F] [--yaw RAD] [--pitch RAD] [--buttons Jump,Sprint,...]
                              [--slot N|all] [--press SECONDS]
                                                write the bots' commands; only the options given change. --yaw or
                                                --pitch also sets button bit 40 (the facing is valid), --yaw off
                                                clears it; --buttons none clears the buttons; --press holds the
                                                buttons that long and then releases them (edge-triggered actions)
  python -B guestmem.py stop                    every command back to zero (the bots stand), all slots
  python -B guestmem.py watch [seconds] [--every S] [--slot N] [--json out.json]
                                                the tick, positions, health and aim every S seconds (default 1)
  options: --pid <Xenia process id>

Guest memory is a named file mapping inside a 4 GB reservation (Xenia, memory.cc); this reads and writes it with
ReadProcessMemory / WriteProcessMemory at reservation base + guest address. The base is found by trying the
addresses Xenia reserves at (1 << n for n = 32..46) and then every mapped region, and is accepted only where the
guest's 0x82000000 starts with MZ. Guest memory is big-endian. Only the bots experiment's mailbox in its own data
area is written (bf1943-xenia-kit/bots.py: DATA + CMD_*), and only by `throttle`, `input` and `stop`; nothing else in
Xenia or the game is changed. Soldier health (soldier + 0x3c) and aim (the object at soldier + 0x230: yaw + 4, pitch
+ 8, radians) are read as research/evidence/bot-interfaces.json gives them (operation-rising-sun).
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


def mailbox(name):
    return bots.DATA + getattr(bots, name)


def buttons_of(g):
    return g.u32(mailbox('CMD_BUTTONS_HI')) << 32 | g.u32(mailbox('CMD_BUTTONS_LO'))


def set_buttons(g, mask):
    g.write_u32(mailbox('CMD_BUTTONS_HI'), mask >> 32)
    g.write_u32(mailbox('CMD_BUTTONS_LO'), mask & 0xFFFFFFFF)


def button_names(mask):
    names = [n for n, b in BUTTONS.items() if mask >> b & 1] + (['facing'] if mask >> FACING_VALID & 1 else [])
    return ','.join(names) or 'none'


def bot_code_state(g):
    """'applied', 'absent' (the cave is zero: a normal build) or 'other' (neither), and the hook word."""
    _, code, tail = bots.build()
    blob = code + tail
    have = g.read(bots.CODE, len(blob))
    hook = g.u32(bots.HOOK)
    if have == blob:
        return 'applied', hook
    return ('absent' if not any(have) else 'other'), hook


def snapshot(g):
    d = bots.DATA
    slot_word = g.u32(d + bots.CMD_SLOT)
    s = dict(time=time.strftime('%I:%M:%S %p'), clock=time.time(), state=g.u32(d + bots.STATE),
             manager=g.u32(d + bots.MANAGER), tick=g.u32(d + bots.TICK), throttle_bits=g.u32(d + bots.CMD_THROTTLE),
             strafe=f32(g.u32(d + bots.CMD_STRAFE)), yaw=f32(g.u32(d + bots.CMD_YAW)),
             pitch=f32(g.u32(d + bots.CMD_PITCH)), buttons=button_names(buttons_of(g)),
             slot='all' if not slot_word else (slot_word - d - bots.SLOTS) // 4, bots=[])
    for k in range(bots.MAX_BOTS):
        player = g.u32(d + bots.SLOTS + 4 * k)
        if not player:
            continue
        x, y, z = (g.u32(d + bots.POSITIONS + 12 * k + 4 * i) for i in range(3))
        b = dict(slot=k, player=f'{player:08x}', position_bits=f'{x:08x} {y:08x} {z:08x}',
                 position=[round(f32(v), 3) for v in (x, y, z)])
        try:
            soldier = g.u32(player + 0x120)
            if soldier:
                aim = g.u32(soldier + 0x230)
                b.update(soldier=f'{soldier:08x}', health=round(f32(g.u32(soldier + 0x3c)), 2))
                if aim:
                    b.update(aim_yaw=round(f32(g.u32(aim + 4)), 4), aim_pitch=round(f32(g.u32(aim + 8)), 4))
        except OSError:
            b['soldier'] = 'unreadable'
        s['bots'].append(b)
    return s


def show(s, only=None):
    print(f'{s["time"]}  tick {s["tick"]}  throttle {f32(s["throttle_bits"]):g} strafe {s["strafe"]:g} '
          f'yaw {s["yaw"]:g} pitch {s["pitch"]:g} buttons {s["buttons"]} slot {s["slot"]}  bots {len(s["bots"])}')
    for b in s['bots']:
        if only is not None and b['slot'] != only:
            continue
        x, y, z = b['position']
        extra = (f'  health {b["health"]:g}' if 'health' in b else '  no soldier') + \
            (f'  aim {b["aim_yaw"]:+.3f} {b["aim_pitch"]:+.3f}' if 'aim_yaw' in b else '')
        print(f'  slot {b["slot"]:2d}  player {b["player"]}  position {x:9.3f} {y:8.3f} {z:9.3f}{extra}')


def main(argv):
    if any(a in ('-h', '--help') for a in argv[1:]) or len(argv) < 2 or \
            argv[1] not in ('attach', 'bots', 'throttle', 'input', 'stop', 'watch'):
        print(__doc__)
        return 0 if len(argv) > 1 and argv[1] in ('-h', '--help') else 1
    opts = dict(zip(argv[2:], argv[3:]))
    pid = int(opts['--pid']) if '--pid' in opts else None
    g = Guest(pid)
    state, hook = bot_code_state(g)
    cmd = argv[1]
    if cmd == 'attach':
        print(f'Xenia {g.exe} process {g.pid}: guest base {g.base:#x} ({g.how}); MZ at {IMAGE:#010x}')
        print(f'bot code at {bots.CODE:#010x}: {state}; hook {bots.HOOK:#010x} = {hook:#010x}')
        return 0 if state == 'applied' else 1
    if state != 'applied':
        raise RuntimeError(f'the bots experiment is not in guest memory ({state}): build the kit with --experiment-bots')
    if cmd == 'bots':
        show(snapshot(g))
    elif cmd == 'throttle':
        if len(argv) < 3:
            raise ValueError('throttle needs a value, such as 0 or 1')
        bits = struct.unpack('>I', struct.pack('>f', float(argv[2])))[0]
        g.write_u32(bots.DATA + bots.CMD_THROTTLE, bits)
        back = g.u32(bots.DATA + bots.CMD_THROTTLE)
        print(f'throttle {float(argv[2]):g} written ({bits:08x}); read back {back:08x}')
        return 0 if back == bits else 1
    elif cmd == 'stop':
        for name in ('CMD_THROTTLE', 'CMD_STRAFE', 'CMD_BUTTONS_HI', 'CMD_BUTTONS_LO', 'CMD_YAW', 'CMD_PITCH', 'CMD_SLOT'):
            g.write_u32(mailbox(name), 0)
        show(snapshot(g), only=-1)
    elif cmd == 'input':
        given = argv[2::2]
        allowed = ('--throttle', '--strafe', '--yaw', '--pitch', '--buttons', '--slot', '--press', '--pid')
        if any(a not in allowed for a in given) or len(argv) % 2:
            raise ValueError(f'input: options are {", ".join(allowed[:-1])} (each with a value); got {" ".join(argv[2:])}')
        if '--slot' in opts:
            n = opts['--slot']
            g.write_u32(mailbox('CMD_SLOT'), 0 if n == 'all' else bots.DATA + bots.SLOTS + 4 * int(n))
        for opt, name in (('--throttle', 'CMD_THROTTLE'), ('--strafe', 'CMD_STRAFE')):
            if opt in opts:
                g.write_u32(mailbox(name), bits32(float(opts[opt])))
        mask = buttons_of(g)
        if opts.get('--yaw') == 'off':
            mask &= ~(1 << FACING_VALID)
        else:
            for opt, name in (('--yaw', 'CMD_YAW'), ('--pitch', 'CMD_PITCH')):
                if opt in opts:
                    g.write_u32(mailbox(name), bits32(float(opts[opt])))
                    mask |= 1 << FACING_VALID
        if '--buttons' in opts:
            mask &= 1 << FACING_VALID
            for name in opts['--buttons'].split(','):
                if name == 'none':
                    continue
                if name not in BUTTONS:
                    raise ValueError(f'unknown button {name}: one of {", ".join(BUTTONS)}')
                mask |= 1 << BUTTONS[name]
        set_buttons(g, mask)
        show(snapshot(g), only=-1)
        if '--press' in opts:
            time.sleep(float(opts['--press']))
            set_buttons(g, mask & (1 << FACING_VALID))
            print(f'released after {float(opts["--press"]):g} s')
    elif cmd == 'watch':
        seconds = float(argv[2]) if len(argv) > 2 and not argv[2].startswith('--') else 10
        every = float(opts.get('--every', 1))
        only = int(opts['--slot']) if '--slot' in opts else None
        shots = []
        end = time.time() + seconds
        while time.time() < end:
            shots.append(snapshot(g))
            show(shots[-1], only)
            time.sleep(every)
        if '--json' in opts:
            with open(opts['--json'], 'w', encoding='utf-8') as f:
                json.dump(dict(pid=g.pid, exe=g.exe, base=f'{g.base:#x}', snapshots=shots), f, indent=1)
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(SUITE, 'core'))
    from bfbc2core import errors
    errors.setup('guestmem', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
