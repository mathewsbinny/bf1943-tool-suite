"""Watch a local BF1943 multiplayer join in Xenia from the host side.

  python -B udp_join_probe.py <session folder> [minutes]      (e.g. a run_xenia_probe.ps1 session folder)

1. Catchers listen on 0.0.0.0 at the ports the client could be sending to by mistake (the game's original
   1001/1003 and Xenia's +10000 remaps 11001/11003); anything received is logged with its source and bytes.
2. When the game's server port 25003 becomes bound (read from Windows' UDP table: no socket is opened on it), the
   probe sends test datagrams to 127.0.0.1:25003 from its own socket and logs any reply.
Results go to <session folder>/udp-join-probe.txt. Only loopback traffic; nothing leaves the PC. Windows only.

Before 2026-10-30 the probe tested the port by binding 127.0.0.1:25003 itself. Windows lets that bind succeed next to
a server bound to 0.0.0.0:25003, so a running server read as unbound, and for the moment the probe held the bind,
datagrams to 127.0.0.1:25003 went to the probe instead of the server (tests/test_udp_join_probe.py).
"""
import ctypes
import os
import socket
import struct
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
SERVER = ('127.0.0.1', 25003)
CATCH = [1001, 1003, 11001, 11003]
UDP_TABLE_OWNER_PID, AF_INET, ERROR_INSUFFICIENT_BUFFER = 1, 2, 122


def udp_endpoints():
    """Every local IPv4 UDP endpoint, [(address, port, process id)], from Windows' UDP table (GetExtendedUdpTable)."""
    get = ctypes.WinDLL('iphlpapi').GetExtendedUdpTable
    size = ctypes.c_ulong(0)
    for _ in range(5):
        buf = ctypes.create_string_buffer(max(size.value, 4))
        rc = get(buf, ctypes.byref(size), False, AF_INET, UDP_TABLE_OWNER_PID, 0)
        if rc == 0:
            n = struct.unpack_from('<I', buf.raw, 0)[0]
            rows = [struct.unpack_from('<4sII', buf.raw, 4 + 12 * k) for k in range(n)]
            return [(socket.inet_ntoa(addr), socket.ntohs(port & 0xFFFF), pid) for addr, port, pid in rows]
        if rc != ERROR_INSUFFICIENT_BUFFER:
            raise OSError(f'GetExtendedUdpTable failed: Windows error {rc}')
    raise OSError('GetExtendedUdpTable: the table kept growing')


def stamp(when=None):
    """A clock time for the log, 12-hour with AM/PM (the project's time format; 24-hour before 2026-10-31)."""
    return time.strftime('%I:%M:%S %p', time.localtime() if when is None else when)


def server_bound():
    """Whether another process has a UDP socket on the server's port at its address or the wildcard address."""
    return any(port == SERVER[1] and addr in (SERVER[0], '0.0.0.0') and pid != os.getpid()
               for addr, port, pid in udp_endpoints())


def main():
    if len(sys.argv) < 2 or any(a in ('-h', '--help') for a in sys.argv[1:]):
        print(__doc__)
        return 0 if len(sys.argv) > 1 else 1
    out = Path(sys.argv[1]) / 'udp-join-probe.txt'
    minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    log = out.open('a', encoding='utf-8', newline='\n')

    def note(msg):
        line = f'{stamp()} {msg}'
        print(line, flush=True)
        log.write(line + '\n')
        log.flush()

    catchers = []
    for port in CATCH:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(('0.0.0.0', port))
            s.setblocking(False)
            catchers.append((port, s))
        except OSError as e:
            note(f'catcher {port}: could not bind ({e})')
    note(f'catchers on {[p for p, _ in catchers]}; waiting for the server on {SERVER}')
    tester = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tester.bind(('127.0.0.1', 0))
    tester.setblocking(False)
    note(f'tester socket {tester.getsockname()}')
    end, was_bound, sent = time.time() + 60 * minutes, False, 0
    while time.time() < end:
        for port, s in catchers:
            try:
                data, src = s.recvfrom(4096)
                note(f'CAUGHT on :{port} from {src}: {len(data)} bytes {data[:64].hex()}')
            except BlockingIOError:
                pass
        bound = server_bound()
        if bound and not was_bound:
            note('server port is bound')
            sent = 0
        if not bound and was_bound:
            note('server port released')
        was_bound = bound
        if bound and sent < 5:
            for payload in (b'\x00\x00\x00\x00', b'PING'):
                tester.sendto(payload, SERVER)
            sent += 1
            note(f'sent test datagrams to {SERVER} (round {sent})')
        try:
            data, src = tester.recvfrom(4096)
            note(f'REPLY to tester from {src}: {len(data)} bytes {data[:64].hex()}')
        except BlockingIOError:
            pass
        except ConnectionResetError:
            note('tester: ICMP port unreachable (nothing listening at the destination)')
        time.sleep(0.2)
    note('done')


if __name__ == '__main__':
    sys.exit(main())
