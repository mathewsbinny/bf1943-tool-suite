r"""Harness: udp_join_probe's watch loop on loopback with stand-ins (no Xenia)

udp_join_probe.main() in this process, its ports moved to free ones (a catcher port, one held by another socket so its
catcher cannot bind, and a stand-in server this test runs), its clock replaced so the loop runs its rounds at once, and
the server's bound state given round by round. Checked, in the session log: the catcher that could not bind, a datagram
caught, the server bound, the test datagrams sent, the stand-in's reply, the ICMP port-unreachable once the stand-in is
closed, the server released, done; and the usage text with no folder or --help. Loopback only; Windows only (the
port-unreachable report is Windows' behaviour).
"""
import contextlib
import io
import os
import socket
import sys
import time
import types
import unittest

import udp_join_probe as udp


def free_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    return s


def test_usage():
    saved = sys.argv
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            sys.argv = ['udp_join_probe.py']
            assert udp.main() == 1
            sys.argv = ['udp_join_probe.py', '--help']
            assert udp.main() == 0
    finally:
        sys.argv = saved
    assert 'udp-join-probe.txt' in out.getvalue()
    assert udp.stamp(time.localtime(0)).endswith(('AM', 'PM'))


def test_watch_loop(tmp_path):
    if os.name != 'nt':
        raise unittest.SkipTest('Windows only (ICMP port unreachable as ConnectionResetError)')
    server = free_udp()
    server.setblocking(False)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(('0.0.0.0', 0))
    blocked = blocker.getsockname()[1]
    probe = free_udp()
    catch_port = probe.getsockname()[1]
    probe.close()                                       # free again: the probe's catcher binds it
    sender = free_udp()
    rounds = iter([True, True, True, True, False] + [False] * 100)
    clock = [0.0]
    step = [0]

    def sleep(seconds):
        clock[0] += seconds
        step[0] += 1
        if step[0] == 1:
            sender.sendto(b'lost datagram', ('127.0.0.1', catch_port))
        time.sleep(0.05)                                # loopback delivery (and the ICMP report) take a moment
        if step[0] <= 2:
            try:
                data, src = server.recvfrom(4096)
                server.sendto(b'pong:' + data, src)
                time.sleep(0.05)
            except BlockingIOError:
                pass
        if step[0] == 2:
            server.close()                              # later datagrams find nothing listening
    fake_time = types.SimpleNamespace(time=lambda: 1000.0 + clock[0], sleep=sleep, strftime=time.strftime,
                                      localtime=time.localtime)
    saved = udp.CATCH, udp.SERVER, udp.server_bound, udp.time, sys.argv
    udp.CATCH = [catch_port, blocked]
    udp.SERVER = server.getsockname()
    udp.server_bound = lambda: next(rounds)
    udp.time = fake_time
    sys.argv = ['udp_join_probe.py', str(tmp_path), '0.05']
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            udp.main()
    finally:
        udp.CATCH, udp.SERVER, udp.server_bound, udp.time, sys.argv = saved
        for s in (blocker, sender, server):
            s.close()
    log = (tmp_path / 'udp-join-probe.txt').read_text(encoding='utf-8')
    for words in (f'catcher {blocked}: could not bind', f'catchers on [{catch_port}]',
                  f'CAUGHT on :{catch_port}', 'server port is bound', '(round 1)', 'REPLY to tester',
                  'ICMP port unreachable', 'server port released', 'done'):
        assert words in log, (words, log)
    assert log.count('sent test datagrams') == 4
