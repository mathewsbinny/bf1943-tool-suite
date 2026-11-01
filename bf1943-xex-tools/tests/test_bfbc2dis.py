r"""XEX tools: bfbc2dis on a made-up image

bfbc2dis.Image on a made-up 12 KB image (no game data; the module is imported with BF1943_RESEARCH pointing at a
made-up workspace, and its IMAGES entry for the test points at the made-up file and its hash): .pdata functions,
the normalized fingerprint (each opcode class of normalize()), direct calls and callers, capstone disassembly,
C strings and D-form accesses; a file whose hash differs is refused.
"""
import hashlib
import importlib
import os
import struct

BASE = 0x82000000
F1, F2 = 0x82001000, 0x82001040


def made_up_image():
    d = bytearray(0x3000)
    d[:2] = b'MZ'
    pe = 0x80
    struct.pack_into('<I', d, 60, pe)
    struct.pack_into('<I', d, pe + 24 + 28, BASE)
    struct.pack_into('<II', d, pe + 24 + 96 + 3 * 8, 0x400, 8 * 4)
    records = [(F1, 6), (F2, 3), (F1 + 2, 4),            # a misaligned start is left out
               (BASE + 0x2FF0, 8)]                       # runs past the image: left out
    for k, (start, words) in enumerate(records):
        struct.pack_into('>II', d, 0x400 + 8 * k, start, words << 8)
    f1 = [0x48000041,                                    # bl F2 (F1 + 0x40)
          0x80630010,                                    # lwz r3, 0x10(r3)
          0x9081FFF8,                                    # stw r4, -8(r1)
          0x7C0802A6,                                    # mflr r0
          0x4BFFFFF1,                                    # bl -16 (F1)
          0x48000003]                                    # bla 0 (absolute)
    f2 = [0x38600001, 0x13E0F9C3, 0x4E800020]           # li r3, 1; a VMX128 word capstone does not know; blr
    struct.pack_into('>6I', d, F1 - BASE, *f1)
    struct.pack_into('>3I', d, F2 - BASE, *f2)
    d[0x800:0x80C] = b'hello world\0'
    d[0x820:0x823] = b'\x01\x02\0'
    return bytes(d)


def load(tmp):
    """bfbc2dis imported with a made-up workspace (it looks one up when imported), and the made-up image's entry."""
    (tmp / 'work/xex-analysis').mkdir(parents=True)
    (tmp / 'research').mkdir()
    saved = os.environ.get('BF1943_RESEARCH')
    os.environ['BF1943_RESEARCH'] = str(tmp)
    try:
        module = importlib.import_module('bfbc2dis')
    finally:
        if saved is None:
            os.environ.pop('BF1943_RESEARCH', None)
        else:
            os.environ['BF1943_RESEARCH'] = saved
    data = made_up_image()
    path = tmp / 'made-up.bin'
    path.write_bytes(data)
    return module, path, hashlib.sha256(data).hexdigest()


def test_normalize(tmp_path):
    dis, _, _ = load(tmp_path)
    n = dis.normalize
    assert n(0x7C0802A6) == (31, 339) and n(0x4E800020) == (19, 16) and n(0xFC00002A) == (63, 21)
    assert n(0xEC00002A) == (59, 21) and n(0x78630020) == (30, 0) and n(0xF8210001) == (62, 1)
    assert n(0x13E0F9C3) == (4, 0x1C3) and n(0x48000041) == (18, 1) and n(0x38600001) == (14,)


def test_image(tmp_path):
    dis, path, digest = load(tmp_path)
    saved = dict(dis.IMAGES)
    try:
        dis.IMAGES['made-up'] = (path, digest)
        image = dis.Image('made-up')
        assert image.base == BASE and image.functions == [(F1, F1 + 24), (F2, F2 + 12)]
        assert image.containing(F1 + 8) == (F1, F1 + 24) and image.containing(F1 + 24) is None
        assert image.containing(BASE) is None and image.valid(BASE) and not image.valid(BASE + 0x3000)
        assert image.calls(F1) == [(F1, F2), (F1 + 16, F1), (F1 + 20, 0)]
        assert image.callers()[F2] == [(F1, F1)] and image.callers() is image.callers()
        assert len(image.norm_seq(F1)) == 6 and image.norm_seq(F2, F2 + 4) == [(14,)]
        prints = image.fingerprints()
        assert prints[F1] == image.fingerprint(F1) and prints is image.fingerprints() and len(prints) == 2
        assert image.insn(F2) == ('li', 'r3, 1')
        assert image.insn(F2 + 4) == ('.long', '0x13e0f9c3'), 'a word capstone cannot decode'
        lines = list(image.lines(F2, 3))
        assert [ln[2] for ln in lines] == ['li', '.long', 'blr'] and lines[0][1] == 0x38600001
        assert image.cstring(BASE + 0x800) == 'hello world'
        assert image.cstring(BASE + 0x820) is None and image.cstring(BASE + 0x3000) is None
        assert image.cstring(BASE + 0x10) is None, 'an empty string is no string'
        assert image.disp_accesses(F1) == [(F1 + 4, 'lwz', 3, 0x10), (F1 + 8, 'stw', 1, -8)]
        dis.IMAGES['made-up'] = (path, '0' * 64)
        try:
            dis.Image('made-up')
            refused = False
        except AssertionError:
            refused = True
        assert refused, 'a file whose hash differs is refused'
    finally:
        dis.IMAGES.clear()
        dis.IMAGES.update(saved)
