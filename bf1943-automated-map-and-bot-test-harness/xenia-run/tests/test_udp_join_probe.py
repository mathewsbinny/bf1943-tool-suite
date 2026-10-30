"""udp_join_probe.server_bound sees a server bound to the wildcard address, and takes none of its datagrams.

  python -B test_udp_join_probe.py

A stand-in server (another process, as Xenia is) binds 0.0.0.0 on a free port, as a game server binds; the probe's
check must report it bound, and a datagram sent to 127.0.0.1 at that port while the probe checks must reach the server.
(The old check bound 127.0.0.1 itself: Windows allows that next to the wildcard bind, so it reported the server
unbound and, while it held the bind, received the server's datagrams.) Loopback only; Windows only.
"""
import os
import socket
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import udp_join_probe as U  # noqa: E402

failures = []
SERVER = r'''import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(('0.0.0.0', 0))
print(s.getsockname()[1], flush=True)
s.settimeout(10)
try:
    print(s.recvfrom(64)[0].decode(), flush=True)
except socket.timeout:
    print('nothing', flush=True)
'''


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def main():
    if os.name != 'nt':
        print('skip udp_join_probe: Windows only')
        return
    server = subprocess.Popen([sys.executable, '-B', '-c', SERVER], stdout=subprocess.PIPE, text=True)
    try:
        port = int(server.stdout.readline())
        U.SERVER = ('127.0.0.1', port)
        check(U.server_bound(), f'a server bound to 0.0.0.0:{port} reads as bound')
        U.server_bound()                          # the probe checks, then the client sends
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.sendto(b'join', ('127.0.0.1', port))
        client.close()
        got = server.stdout.readline().strip()
        check(got == 'join', f'the client\'s datagram reaches the server, not the probe (server got {got!r})')
    finally:
        server.kill()
        server.wait()
    time.sleep(0.2)
    check(not U.server_bound(), 'once the server is gone the port reads as unbound')


if __name__ == '__main__':
    main()
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
