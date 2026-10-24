"""Watch a local BF1943 multiplayer join in Xenia from the host side.

  python -B udp_join_probe.py <session folder> [minutes]      (e.g. a run_xenia_probe.ps1 session folder)

1. Catchers listen on 0.0.0.0 at the ports the client could be sending to by mistake (the game's original
   1001/1003 and Xenia's +10000 remaps 11001/11003); anything received is logged with its source and bytes.
2. When the game's server port 127.0.0.1:25003 becomes bound (detected by a failing test bind), the probe
   sends test datagrams to it from its own socket and logs any reply.
Results go to <session folder>/udp-join-probe.txt. Only loopback traffic; nothing leaves the PC.
"""
import socket
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
SERVER = ('127.0.0.1', 25003)
CATCH = [1001, 1003, 11001, 11003]


def server_bound():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(SERVER)
        return False
    except OSError:
        return True
    finally:
        s.close()


def main():
    out = Path(sys.argv[1]) / 'udp-join-probe.txt'
    minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    log = out.open('a', encoding='utf-8')

    def note(msg):
        line = f'{time.strftime("%H:%M:%S")} {msg}'
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
    main()
