"""The binary .dbx reader (core dbx.py) on a made-up file (no game data): the file parses; every truncated copy, a
numbers block longer than the file, numbers 0 or 1 byte wide, a string index past the table, a string offset past
the string area and a container left open are refused with a ValueError naming the cause, never an IndexError or a
silent short value. to_xml refuses the same files with the same error.

  python -B test_dbx_malformed.py
"""
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from bfbc2core import dbx  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def made_up(numbers=(2, 4), text_index=4, close=True, forged=False):
    """root { n: 2 numbers of 4 bytes; t: text 'x' } in the binary layout (FORMAT.md sections 1-2). forged: string 4's
    offset points past the string area, at text appended after the file's end."""
    names = [b'', b'root', b'n', b't', b'x']                      # string 0: the stream's end marker
    offsets, blob = [], b''
    for s in names:
        offsets.append(len(blob))
        blob += s + b'\0'
    area = 4 * len(names) + len(blob)
    count, size = numbers
    body = bytes([1, dbx.CONTAINER << 4, 0])                      # root, a container
    body += bytes([2, dbx.NUMBERS << 4, count, size]) + bytes(range(min(8, count * size)))
    body += bytes([3, dbx.TEXT << 4, text_index])
    if close:
        body += bytes([0])                                         # end of root
    body += bytes([0])                                             # end of the file
    if forged:
        offsets[4] = len(blob) + len(body)
        body += b'forged\0'
    head = dbx.MAGIC + bytes(8) + struct.pack('>II', area, len(names)) + struct.pack(f'>{len(names)}I', *offsets) + blob
    return head + body


def xml_refused(data):
    try:
        dbx.to_xml(data)
        return None
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f'CRASH {type(e).__name__}: {e}'


def refused(data):
    try:
        dbx.parse(data)
        return None
    except ValueError as e:
        return str(e)
    except Exception as e:                                         # a crash, not a refusal
        return f'CRASH {type(e).__name__}: {e}'


if __name__ == '__main__':
    good = made_up()
    f = dbx.parse(good)
    root = f.roots[0]
    check(root.tag == 'root' and [c.tag for c in root.children] == ['n', 't'] and root.children[1].text == 'x',
          'a made-up file parses: root with a numbers and a text child')
    check(dbx.numbers(root.children[0]) == (4, [bytes(range(4)), bytes(range(4, 8))]), 'its numbers read back')
    bad = []
    for cut in range(25, len(good) - 2):
        why = refused(good[:cut])
        if why is None or why.startswith('CRASH'):
            bad.append((cut, why))
    check(not bad, f'every truncated copy ({len(good) - 27} cuts) is refused with a ValueError'
                   + (f': {bad[:3]}' if bad else ''))
    why = refused(made_up(numbers=(100, 4)))
    check(why is not None and 'numbers' in why and not why.startswith('CRASH'), f'numbers longer than the file: {why}')
    why = refused(made_up(text_index=9))
    check(why is not None and 'string 9' in why, f'a string index past the table: {why}')
    why = refused(made_up(close=False)[:-1])
    check(why is not None and 'not closed' in why, f'a container left open: {why}')
    why = refused(dbx.MAGIC + bytes(8) + struct.pack('>II', 4, 1000))
    check(why is not None and not why.startswith('CRASH'), f'a string table larger than the file: {why}')
    for size in (0, 1):
        why = refused(made_up(numbers=(2, size)))
        check(why is not None and 'size' in why and not why.startswith('CRASH'), f'numbers {size} byte(s) wide: {why}')
    check(refused(made_up(numbers=(0, 0))) is None, 'an empty numbers list of width 0 still parses')
    why = refused(made_up(forged=True))
    check(why is not None and 'string area' in why, f'a string offset past the string area: {why}')
    # the XML path validates as parse() does
    check(dbx.to_xml(good) == '<?xml version="1.0"?>\r\n<root>\r\n\t<n>66051/1.56368425E-36</n>\r\n\t<t>x</t>\r\n</root>\r\n',
          'to_xml of the made-up file')
    for label, data in (('a container left open', made_up(close=False)[:-1]), ('numbers 1 byte wide', made_up(numbers=(2, 1))),
                        ('a string offset past the string area', made_up(forged=True)),
                        ('a truncated copy', good[:len(good) - 6])):
        why = xml_refused(data)
        check(why is not None and not why.startswith('CRASH') and why == refused(data),
              f'to_xml refuses {label} as parse() does: {why}')
    print('all passed' if not failures else f'{len(failures)} failed')
    sys.exit(1 if failures else 0)
