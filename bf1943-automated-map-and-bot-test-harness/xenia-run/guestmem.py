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
import struct
import sys
import time
from collections.abc import Callable
from typing import Any, Protocol

if __name__ == '__main__':      # the suite's bootstrap (core/suitepath.py)
    import pathlib
    import sys
    sys.path.insert(0, str(next(p for p in pathlib.Path(__file__).resolve().parents if (p / 'core' / 'suitepath.py').is_file()) / 'core'))
    import suitepath
    suitepath.use()
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
k32 = ctypes.WinDLL('kernel32', use_last_error=True)    # kernel32.dll

type Record = dict[str, Any]        # a state, command or flag record by field name (bots.py's names), with 'torn'
type Snapshot = dict[str, Any]      # snapshot(): the tick, the mailbox and every bot's and capture point's record


class GuestMemory(Protocol):
    """What the readers here use of a guest: its base and big-endian reads and writes at guest addresses (Guest, or a
    test's made-up guest)."""
    base: int

    def read(self, guest: int, size: int, /) -> bytes: ...
    def u32(self, guest: int, /) -> int: ...
    def write(self, guest: int, data: bytes, /) -> None: ...
    def write_u32(self, guest: int, value: int, /) -> None: ...


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p), ('AllocationProtect', wt.DWORD),
                ('PartitionId', wt.WORD), ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD), ('Protect', wt.DWORD),
                ('Type', wt.DWORD)]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                ('szExeFile', ctypes.c_char * 260)]


k32.OpenProcess.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = k32.ReadProcessMemory.argtypes


def xenia_processes() -> list[tuple[int, str]]:
    """[(pid, exe name)] of running Xenia processes."""
    snapshot_handle = k32.CreateToolhelp32Snapshot(2, 0)
    found = []
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    ok = k32.Process32First(snapshot_handle, ctypes.byref(entry))
    while ok:
        name = entry.szExeFile.decode('ascii', 'replace')
        if name.lower() in XENIA_NAMES:
            found.append((entry.th32ProcessID, name))
        ok = k32.Process32Next(snapshot_handle, ctypes.byref(entry))
    k32.CloseHandle(snapshot_handle)
    return found


class Guest:
    """A running Xenia's guest memory, read and written with ReadProcessMemory / WriteProcessMemory at its base."""

    def __init__(self, pid: int | None = None) -> None:
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
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.handle = k32.OpenProcess(access, False, pid)
        if not self.handle:
            raise PermissionError(f'cannot open Xenia (process {pid}), Windows error {ctypes.get_last_error()}: '
                                  f'run this as the same user as Xenia')
        self.base, self.how = self.find_base()

    def raw_read(self, address: int, size: int) -> bytes | None:
        buf = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(done)) \
                or done.value != size:
            return None
        return buf.raw

    def find_base(self) -> tuple[int, str]:
        """(host address of guest 0, how it was found)."""
        for n in range(32, 47):
            if self.raw_read((1 << n) + IMAGE, 2) == b'MZ':
                return 1 << n, f'reserved at 1 << {n}'
        region, address = MEMORY_BASIC_INFORMATION(), 0
        while k32.VirtualQueryEx(self.handle, ctypes.c_void_p(address), ctypes.byref(region), ctypes.sizeof(region)):
            start = region.BaseAddress or 0
            if region.State == MEM_COMMIT and region.Type == MEM_MAPPED and start >= IMAGE \
                    and self.raw_read(start, 2) == b'MZ':
                return start - IMAGE, 'a mapped region starting with MZ'
            address = start + region.RegionSize
        raise RuntimeError('no guest memory with MZ at 0x82000000 in this Xenia: is a game running?')

    def read(self, guest: int, size: int) -> bytes:
        data = self.raw_read(self.base + guest, size)
        if data is None:
            raise OSError(f'cannot read guest {guest:#010x} ({size} bytes)')
        return data

    def u32(self, guest: int) -> int:
        return struct.unpack('>I', self.read(guest, 4))[0]

    def write(self, guest: int, data: bytes) -> None:
        """data at a guest address in one WriteProcessMemory call."""
        done = ctypes.c_size_t()
        if not k32.WriteProcessMemory(self.handle, ctypes.c_void_p(self.base + guest), data, len(data),
                                      ctypes.byref(done)) or done.value != len(data):
            raise OSError(f'cannot write guest {guest:#010x} ({len(data)} bytes), '
                          f'Windows error {ctypes.get_last_error()}')

    def write_u32(self, guest: int, value: int) -> None:
        self.write(guest, struct.pack('>I', value))


def f32(bits: int) -> float:
    return struct.unpack('>f', struct.pack('>I', bits))[0]


def bits32(value: float) -> int:
    return struct.unpack('>I', struct.pack('>f', value))[0]


def button_names(mask: int) -> str:
    names = [name for name, bit in BUTTONS.items() if mask >> bit & 1]
    names += ['facing'] if mask >> FACING_VALID & 1 else []
    return ','.join(names) or 'none'


def bot_code_state(guest: GuestMemory) -> tuple[str, int]:
    """'applied' (the bot code, and the hook branching to it), 'unhooked' (the code without that hook: it never
    runs), 'absent' (the cave is zero and the hook the game's own: a normal build) or 'other', and the hook word."""
    words, code, tail = bots.build()
    blob = code + tail
    have = guest.read(bots.CODE, len(blob))
    hook = guest.u32(bots.HOOK)
    if have == blob:
        patched = dict((address, word) for address, word, _ in words)[bots.HOOK]
        return ('applied' if hook == patched else 'unhooked'), hook
    return ('absent' if not any(have) and hook == bots.HOOK_ORIGINAL else 'other'), hook


def block_of(guest: GuestMemory) -> int | None:
    """The mailbox block's guest address, or None (no match running yet, or not mailbox v2)."""
    magic, block = struct.unpack('>II', guest.read(bots.DATA + bots.MBOX_MAGIC, 8))
    if magic != bots.MAGIC or not block:
        return None
    head = struct.unpack('>4I', guest.read(block, 16))
    if head != (bots.MAGIC, bots.VERSION, bots.MAX_BOTS, bots.BOT_SIZE):
        raise RuntimeError(f'the mailbox at {block:#010x} is not v{bots.VERSION} as bots.py lays it out: {head}')
    return block


def words(guest: GuestMemory, address: int, count: int) -> tuple[int, ...]:
    return struct.unpack(f'>{count}I', guest.read(address, 4 * count))


def record_words(guest: GuestMemory, address: int, first: int, last: int, count: int) -> tuple[tuple[int, ...], bool]:
    """A state or flag record's count words, read whole under its two-word lock (bots.py; bots.host_record): the
    closing tick (last), the words, then the opening tick (first), each read after the one before; whole when the two
    ticks are the same, else tried again, bots.TRIES times in all. (words, torn): torn when no read was whole (the
    last read's words, which may mix two ticks). The two ticks of one copy are not compared: a copy can take the old
    opening tick, new fields and the old closing tick."""
    record: tuple[int, ...] = ()
    for _ in range(bots.TRIES):
        end = guest.u32(address + last)
        record = words(guest, address, count)
        if guest.u32(address + first) == end:
            return record, False
    return record, True


BOT_FIELDS = ('R_SEQ', 'R_PLAYER', 'R_SOLDIER', 'R_TEAM', 'R_X', 'R_Y', 'R_Z', 'R_SEQ_END', 'C_STAMP', 'C_THROTTLE',
              'C_STRAFE', 'C_YAW', 'C_PITCH', 'C_BUTTONS_HI', 'C_BUTTONS_LO')


def bot_record(guest: GuestMemory, block: int, k: int) -> Record:
    """Slot k's state record (read whole, record_words: torn when it could not be) and command record."""
    at = block + bots.BOT0 + bots.BOT_SIZE * k
    state, torn = record_words(guest, at, bots.R_SEQ, bots.R_SEQ_END, bots.R_SEQ_END // 4 + 1)
    record_words_all = state + words(guest, at + bots.C_STAMP, (bots.BOT_SIZE - bots.C_STAMP) // 4)
    record: Record = {name: record_words_all[getattr(bots, name) // 4] for name in BOT_FIELDS}
    record['torn'] = torn
    return record


def flag_records(guest: GuestMemory, block: int) -> list[Record]:
    """The capture points' records, each read whole under its own two ticks. The header's count (H_FLAGS) is not
    locked with them: a record whose tick is older than the newest one read is left from a tick that found more capture
    points (or the count was read mid-update), and is marked stale."""
    count = min(guest.u32(block + bots.H_FLAGS), bots.MAX_FLAGS)
    out = []
    for index in range(count):
        record, torn = record_words(guest, block + bots.FLAG0 + bots.FLAG_SIZE * index, bots.F_SEQ, bots.F_SEQ_END,
                                    bots.FLAG_SIZE // 4)

        def field(name: str) -> int:
            return record[getattr(bots, name) // 4]
        out.append(dict(index=index, entity=f'{field("F_ENTITY"):08x}', owner=field('F_OWNER'),
                        control=round(f32(field('F_CONTROL')), 1), enabled=field('F_ENABLED'),
                        radius=round(f32(field('F_RADIUS')), 2),
                        position=[round(f32(field(name)), 2) for name in ('F_X', 'F_Y', 'F_Z')],
                        tick=field('F_SEQ'), torn=torn))
    newest = max((flag['tick'] for flag in out if not flag['torn']), default=0)
    for flag in out:
        flag['stale'] = not flag['torn'] and flag['tick'] < newest
    return out


def write_command(guest: GuestMemory, block: int, k: int, stamp: int, throttle: float = 0.0, strafe: float = 0.0,
                  yaw: float = 0.0, pitch: float = 0.0, buttons: int = 0) -> None:
    """Slot k's command under its sequence lock (bots.py, C_SEQ; as navhost writes it): the sequence odd, the stamp
    and fields in one write, then the sequence even and new: three writes, so the record is open as briefly as the
    process calls allow (a guest read that finds it open uses the bot's last whole command; before 2026-10-30 each field
    was its own write, nine in all). The guest takes none of it until the end."""
    at = block + bots.BOT0 + bots.BOT_SIZE * k
    fields = struct.pack('>7I', stamp, bits32(throttle), bits32(strafe), bits32(yaw), bits32(pitch), buttons >> 32,
                         buttons & 0xFFFFFFFF)
    if bots.C_BUTTONS_LO + 4 - bots.C_STAMP != len(fields):
        raise RuntimeError('the command record\'s fields are not seven consecutive words: bots.py changed its layout')
    odd = (guest.u32(at + bots.C_SEQ) + 1 | 1) & 0xFFFFFFFF
    guest.write_u32(at + bots.C_SEQ, odd)
    guest.write(at + bots.C_STAMP, fields)
    guest.write_u32(at + bots.C_SEQ, (odd + 1) & 0xFFFFFFFF)


def bot_entry(guest: GuestMemory, record: Record, slot: int, tick: int) -> dict[str, Any]:
    """One live bot slot as snapshot() lists it: its state, its command and, with a soldier, health and aim."""
    def position_bits(name: str) -> str:
        return f'{record[name]:08x}'
    buttons = button_names(record['C_BUTTONS_HI'] << 32 | record['C_BUTTONS_LO'])
    command = dict(stamp=record['C_STAMP'], age=(tick - record['C_STAMP']) & 0xFFFFFFFF,
                   throttle=f32(record['C_THROTTLE']), strafe=f32(record['C_STRAFE']),
                   yaw=f32(record['C_YAW']), pitch=f32(record['C_PITCH']), buttons=buttons)
    bot: dict[str, Any] = dict(
        slot=slot, player=f'{record["R_PLAYER"]:08x}', team=record['R_TEAM'], record_tick=record['R_SEQ'],
        torn=record['torn'], position_bits=' '.join(position_bits(name) for name in ('R_X', 'R_Y', 'R_Z')),
        position=[round(f32(record[name]), 3) for name in ('R_X', 'R_Y', 'R_Z')], command=command)
    soldier = record['R_SOLDIER']
    if soldier:
        bot['soldier'] = f'{soldier:08x}'
        try:
            bot['health'] = round(f32(guest.u32(soldier + 0x3c)), 2)
            aim = guest.u32(soldier + 0x230)
            if aim:
                bot.update(aim_yaw=round(f32(guest.u32(aim + 4)), 4), aim_pitch=round(f32(guest.u32(aim + 8)), 4))
        except OSError:
            bot['soldier'] = 'unreadable'
    return bot


def snapshot(guest: GuestMemory) -> Snapshot:
    data = bots.DATA
    block = block_of(guest)
    # the bots' lifecycle by name (bots.lifecycle: none, made, held), from its two data-area words
    snap: Snapshot = dict(time=time.strftime('%I:%M:%S %p'), clock=time.time(),
                          lifecycle=bots.lifecycle(guest.u32(data + bots.STATE), guest.u32(data + bots.HOLD)),
                          manager=guest.u32(data + bots.MANAGER), tick=guest.u32(data + bots.TICK),
                          mailbox=f'{block:08x}' if block else None, bots=[], flags=[])
    if not block:
        return snap
    snap['flags'] = flag_records(guest, block)
    for k in range(bots.MAX_BOTS):
        record = bot_record(guest, block, k)
        if not record['R_PLAYER']:
            continue
        snap['bots'].append(bot_entry(guest, record, k, snap['tick']))
    return snap


def show(snap: Snapshot, only: int | None = None) -> None:
    print(f'{snap["time"]}  tick {snap["tick"]}  lifecycle {snap["lifecycle"]}  mailbox {snap["mailbox"] or "none"}  '
          f'bots {len(snap["bots"])}  capture points {len(snap["flags"])}')
    for flag in snap['flags'] if only is None else []:
        x, y, z = flag['position']
        state = '  (torn)' if flag['torn'] else '  (stale)' if flag.get('stale') else ''
        print(f'  flag {flag["index"]}  owner {flag["owner"]}  control {flag["control"]:5.1f}  '
              f'radius {flag["radius"]:g}  enabled {flag["enabled"]}  at {x:9.2f} {y:8.2f} {z:9.2f}{state}')
    for bot in snap['bots']:
        if only is not None and bot['slot'] != only:
            continue
        x, y, z = bot['position']
        command = bot['command']
        health = f'  health {bot["health"]:g}' if 'health' in bot else '  no soldier'
        aim = f'  aim {bot["aim_yaw"]:+.3f} {bot["aim_pitch"]:+.3f}' if 'aim_yaw' in bot else ''
        if command['age'] <= bots.STALE:
            cmd = (f'  cmd age {command["age"]} thr {command["throttle"]:+.2f} str {command["strafe"]:+.2f} '
                   f'yaw {command["yaw"]:+.3f} {command["buttons"]}')
        else:
            cmd = '  cmd stale'
        print(f'  slot {bot["slot"]:2d}  team {bot["team"]}  player {bot["player"]}  '
              f'position {x:9.3f} {y:8.3f} {z:9.3f}{health}{aim}{cmd}{"  (torn)" if bot["torn"] else ""}')


def slots_of(opts: dict[str, str]) -> list[int]:
    slot = opts.get('--slot', 'all')
    if slot == 'all':
        return list(range(bots.MAX_BOTS))
    k = int(slot)
    if not 0 <= k < bots.MAX_BOTS:
        raise ValueError(f'--slot: 0 to {bots.MAX_BOTS - 1}, or all')
    return [k]


def still_block(guest: GuestMemory, block: int) -> bool:
    """Whether the data area still names this block (its magic and address): a new match replaces or clears it."""
    return struct.unpack('>II', guest.read(bots.DATA + bots.MBOX_MAGIC, 8)) == (bots.MAGIC, block)


def drive(guest: GuestMemory, block: int, slots: list[int], hold: float, press: float | None, throttle: float = 0.0,
          strafe: float = 0.0, yaw: float | None = None, pitch: float | None = None, mask: int = 0,
          sleep: Callable[[float], object] = time.sleep) -> bool:
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
            elapsed = time.time() - start
            buttons = facing | (mask if press is None or elapsed < press else 0)
            tick = guest.u32(bots.DATA + bots.TICK)
            ours = still_block(guest, block)
            if not ours:
                return False
            for k in slots:
                write_command(guest, block, k, tick, throttle, strafe, yaw or 0.0, pitch or 0.0, buttons)
            if elapsed >= hold:
                return True
            sleep(1 / 60)
    finally:
        if ours and still_block(guest, block):
            for k in slots:
                write_command(guest, block, k, 0)


COMMANDS = ('attach', 'bots', 'throttle', 'input', 'stop', 'watch')


def main(argv: list[str], guest: GuestMemory | None = None, sleep: Callable[[float], object] = time.sleep) -> int:
    if any(arg in ('-h', '--help') for arg in argv[1:]) or len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 0 if len(argv) > 1 and argv[1] in ('-h', '--help') else 1
    opts = dict(zip(argv[2:], argv[3:]))
    pid = int(opts['--pid']) if '--pid' in opts else None
    guest = guest or Guest(pid)
    state, hook = bot_code_state(guest)
    cmd = argv[1]
    if cmd == 'attach':
        print(f'Xenia {getattr(guest, "exe", "?")} process {getattr(guest, "pid", "?")}: guest base {guest.base:#x} '
              f'({getattr(guest, "how", "?")}); MZ at {IMAGE:#010x}')
        print(f'bot code at {bots.CODE:#010x}: {state}; hook {bots.HOOK:#010x} = {hook:#010x}')
        if state == 'applied':
            block = block_of(guest)
            where = f'at {block:#010x}' if block else 'not made yet (no match with bots)'
            print(f'mailbox v{bots.VERSION}: ' + where)
        return 0 if state == 'applied' else 1
    if state != 'applied':
        raise RuntimeError(f'the bots experiment is not in guest memory ({state}): '
                           'build the kit with --experiment-bots')
    if cmd in ('bots', 'watch'):
        return watch(guest, argv, opts, sleep)
    block = block_of(guest)
    if not block:
        raise RuntimeError('no mailbox yet: the bots are made once a match runs and the host player has a soldier')
    if cmd == 'stop':
        for k in range(bots.MAX_BOTS):
            write_command(guest, block, k, 0)
        print(f'every command zeroed ({bots.MAX_BOTS} slots)')
        return 0
    if cmd == 'throttle':
        if len(argv) < 3 or argv[2].startswith('--'):
            raise ValueError('throttle needs a value, such as 0 or 1')
        opts = dict(zip(argv[3:], argv[4:]))
        slots = slots_of(opts)
        if not drive(guest, block, slots, float(opts.get('--hold', 10)), None, throttle=float(argv[2]), sleep=sleep):
            print(MATCH_CHANGED)
            return 1
        print(f'throttle {float(argv[2]):g} held {float(opts.get("--hold", 10)):g} s, then zeroed')
        return 0
    return drive_input(guest, block, argv, opts, sleep)


def watch(guest: GuestMemory, argv: list[str], opts: dict[str, str], sleep: Callable[[float], object]) -> int:
    """bots and watch: snapshots every --every seconds for the given seconds (bots: one), shown and kept for --json."""
    cmd = argv[1]
    seconds = float(argv[2]) if cmd == 'watch' and len(argv) > 2 and not argv[2].startswith('--') else 0
    every = float(opts.get('--every', 1))
    only = int(opts['--slot']) if '--slot' in opts else None
    shots = []
    end = time.time() + seconds
    while True:
        shots.append(snapshot(guest))
        show(shots[-1], only)
        if time.time() + every > end:
            break
        sleep(every)
    if '--json' in opts:
        with open(opts['--json'], 'w', encoding='utf-8', newline='\n') as f:
            json.dump(dict(pid=getattr(guest, 'pid', None), exe=getattr(guest, 'exe', None), base=f'{guest.base:#x}',
                           snapshots=shots), f, indent=1)
    return 0


def drive_input(guest: GuestMemory, block: int, argv: list[str], opts: dict[str, str],
                sleep: Callable[[float], object]) -> int:
    """input: the given throttle, strafe, facing and buttons held for --hold seconds, then zeroed."""
    given = argv[2::2]
    allowed = ('--throttle', '--strafe', '--yaw', '--pitch', '--buttons', '--slot', '--press', '--hold', '--pid')
    if any(option not in allowed for option in given) or len(argv) % 2:
        raise ValueError(f'input: options are {", ".join(allowed[:-1])} (each with a value); got {" ".join(argv[2:])}')
    mask = 0
    for name in opts.get('--buttons', 'none').split(','):
        if name == 'none':
            continue
        if name not in BUTTONS:
            raise ValueError(f'unknown button {name}: one of {", ".join(BUTTONS)}')
        mask |= 1 << BUTTONS[name]

    def number(option: str) -> float | None:
        return float(opts[option]) if option in opts else None
    if not drive(guest, block, slots_of(opts), float(opts.get('--hold', 10)), number('--press'),
                 throttle=number('--throttle') or 0.0, strafe=number('--strafe') or 0.0, yaw=number('--yaw'),
                 pitch=number('--pitch'), mask=mask, sleep=sleep):
        print(MATCH_CHANGED)
        return 1
    print(f'held {float(opts.get("--hold", 10)):g} s, then zeroed')
    return 0


if __name__ == '__main__':
    from bfbc2core import errors
    errors.setup('guestmem', gui=False)
    with errors.guard():
        sys.exit(main(sys.argv))
