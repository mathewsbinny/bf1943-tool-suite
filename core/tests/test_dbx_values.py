r"""DBX reader: values as DbxEdit writes them, instances by guid (made-up partitions)

dbx's value text on made-up elements and partitions (no game data): .NET's "R" formatting of 4- and 8-byte
numbers (shortest text that reads back, scientific notation outside 1E-05 .. 1E+07 for singles, a rounding that
carries into a new digit, negative values, NaN and the infinities), 4-byte values read as whole numbers (named
hash fields, small values, NaN patterns), 2-byte values, booleans and other single bytes; floats() refusing
values that are not 4 bytes; a number longer than 64 bits refused; and a partition's instances found by guid
(case ignored) with their attributes, and to_xml's empty text element.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
import struct

from bfbc2core import dbx


def varint(n):
    out = b''
    while True:
        if n < 0x80:
            return out + bytes([n])
        out += bytes([n & 0x7F | 0x80])
        n >>= 7


def partition(strings, body):
    """A binary DBX file: the string table (string 0 is the end marker '') and the element stream."""
    names = [''] + strings
    offsets, blob = [], b''
    for s in names:
        offsets.append(len(blob))
        blob += s.encode('utf-8') + b'\0'
    area = 4 * len(names) + len(blob)
    return (dbx.MAGIC + bytes(8) + struct.pack('>II', area, len(names)) + struct.pack(f'>{len(names)}I', *offsets)
            + blob + body)


def numbers_element(values, size, name=''):
    raw = varint(len(values)) + varint(size) + b''.join(values)
    return dbx.Element('field', dbx.NUMBERS, [('name', name)] if name else [], raw=raw)


def single(v):
    return struct.pack('>f', v)


def double(v):
    return struct.pack('>d', v)


def test_singles_as_dotnet_r():
    cases = {
        0.5: '0.5', 1.0: '1', -2.25: '-2.25', 0.1: '0.1', 100.0: '100', 1234567.0: '1234567',
        12345678.0: '12345678', 1e8: '1E+08', 0.0001: '0.0001', 0.00001: '1E-05', 3.4e38: '3.4E+38', 16777216.0: '16777216',
        1.0e-30: '1E-30', 9.999999e-5: '9.999999E-05', 0.3: '0.3', 123.456: '123.456',
    }
    got = {v: dbx.value(numbers_element([single(v)], 4)) for v in cases}
    assert got == cases, {v: (got[v], want) for v, want in cases.items() if got[v] != want}


def test_singles_that_need_nine_digits():
    # a value whose 7-digit text reads back as another single needs 9 digits (the "R" rule); 12345678 above too:
    # 1.234568E+07 reads back as 12345680
    v = struct.unpack('>f', struct.pack('>I', 0x3F800001))[0]          # the next single after 1.0
    assert dbx.value(numbers_element([single(v)], 4)) == '1.00000012'
    v = struct.unpack('>f', struct.pack('>I', 0x4B7FFFFF))[0]          # 16777215, 8 digits
    assert dbx.value(numbers_element([single(v)], 4)) == '16777215'


def test_rounding_carries_into_a_new_digit():
    assert dbx._net_g(9.9999999, 7) == '10'
    assert dbx._net_g(-9999999.5, 7) == '-1E+07'
    assert dbx._net_g(0.000099999999, 7) == '0.0001'
    assert dbx._net_g(0.0, 7) == '0' and dbx._net_g(-0.0, 7) == '0'
    assert dbx._net_g(1.5, 1) == '2' and dbx._net_g(2.5, 1) == '3', 'a tie goes away from zero'
    assert dbx._net_g(-0.00015, 2) == '-0.00015' and dbx._net_g(0.000015, 2) == '1.5E-05'


def test_doubles_as_dotnet_r():
    cases = [(0.1, '0.1'), (1 / 3, '0.33333333333333331'), (-1e300, '-1E+300'), (2.0 ** 60, '1.152921504606847E+18'),
             (123456789012345.0, '123456789012345'), (1e15, '1E+15'), (0.0, '0'),
             (float('nan'), 'NaN'), (float('inf'), 'Infinity'), (float('-inf'), '-Infinity')]
    for v, want in cases:
        got = dbx.value(numbers_element([double(v)], 8))
        assert got == want, (v, got, want)


def test_whole_number_singles():
    big = struct.pack('>i', 0x12345678)                                 # as a single it is 5.69e-28
    assert dbx.value(numbers_element([big], 4, 'Hash')) == str(0x12345678), 'a named hash field'
    assert dbx.value(numbers_element([big], 4, 'Position')) == '5.69045661E-28', 'another field: a single (9 digits read back)'
    assert dbx.value(numbers_element([struct.pack('>i', 0x00FFFFFF)], 4)) == str(0xFFFFFF), 'high byte 0'
    assert dbx.value(numbers_element([struct.pack('>i', 0x01000000)], 4)) == '2.3509887E-38', 'high byte 1'
    assert dbx.value(numbers_element([struct.pack('>i', -1)], 4)) == '-1', 'a NaN pattern is a whole number'
    assert dbx.value(numbers_element([struct.pack('>I', 0x7F800000)], 4)) == str(0x7F800000), 'infinity\'s bits too'
    assert dbx.value(numbers_element([struct.pack('>I', 0x7F000000)], 4)) == '1.70141183E+38', 'exponent below 0xFF'
    assert dbx.value(numbers_element([single(1.0), single(2.0)], 4)) == '1/2', 'values joined by /'


def test_two_byte_and_single_byte_values():
    assert dbx.value(numbers_element([b'\x01\x02', b'\xff\xfe'], 2)) == '258/65534'
    for byte, text in ((0, 'false'), (1, 'true'), (2, '2'), (255, '255')):
        assert dbx.value(dbx.Element('flag', 1, raw=bytes([0, byte]))) == text, byte
    assert dbx.value(dbx.Element('t', dbx.TEXT, text='hello')) == 'hello'


def test_floats():
    assert dbx.floats(numbers_element([single(1.5), single(-2.0)], 4)) == [1.5, -2.0]
    for size, values in ((8, [double(1.0)]), (2, [b'\0\1'])):
        try:
            dbx.floats(numbers_element(values, size))
        except ValueError as e:
            assert str(e) == 'not 4-byte numbers'
        else:
            raise AssertionError(f'{size}-byte numbers read as floats')
    size, raw = dbx.numbers(numbers_element([b'\1\2\3'], 3))
    assert (size, raw) == (3, [b'\1\2\3'])


def test_varint_limits():
    assert dbx._varint(b'\x7f', 0) == (127, 1)
    assert dbx._varint(b'\x80\x01', 0) == (128, 2)
    nine = b'\xff' * 9 + b'\x01'                                       # 64 bits: the most a number may hold
    assert dbx._varint(nine, 0) == ((1 << 64) - 1, 10)
    try:
        dbx._varint(b'\xff' * 10 + b'\x01', 0)
    except ValueError as e:
        assert 'longer than 64 bits at 0xa' in str(e), e
    else:
        raise AssertionError('a 71-bit number read')


def test_is_binary():
    assert not dbx.is_binary(dbx.MAGIC + bytes(16)), '24 bytes are too few'
    assert dbx.is_binary(dbx.MAGIC + bytes(17))
    assert not dbx.is_binary(b'<binary}' + bytes(17))


def test_instances_by_guid():
    strings = ['partition', 'instance', 'guid', 'AB-12', 'cd-34', 'type', 'Thing', 'field', 'name', 'Empty', 'x']
    s = {name: k + 1 for k, name in enumerate(strings)}
    attr = lambda k, v: varint(s[k]) + varint(s[v])                         # noqa: E731
    body = varint(s['partition']) + bytes([dbx.CONTAINER << 4, 0])
    body += varint(s['instance']) + bytes([dbx.CONTAINER << 4 | 2]) + attr('guid', 'AB-12') + attr('type', 'Thing') + b'\0'
    body += varint(s['field']) + bytes([dbx.TEXT << 4 | 1]) + attr('name', 'Empty') + varint(0)   # text '' (string 0)
    body += b'\0'                                                            # end of the first instance
    body += varint(s['field']) + bytes([dbx.TEXT << 4 | 1]) + attr('name', 'x') + varint(s['x'])  # not an instance
    body += varint(s['instance']) + bytes([dbx.CONTAINER << 4 | 1]) + attr('guid', 'cd-34') + b'\0' + b'\0'
    body += b'\0' + b'\0'
    data = partition(strings, body)
    f = dbx.parse(data)
    assert [e.attr('guid') for e in f.instances] == ['AB-12', 'cd-34'], 'the field between them is no instance'
    first = f.instance('ab-12')
    assert first is not None and f.get(first, 'type') == 'Thing' and f.get(first, 'missing') is None
    assert f.instance('CD-34') is f.instances[1] and f.instance('ef-56') is None
    assert first.child('Empty') is not None and first.child('x') is None
    assert first.attr('missing', 'default') == 'default'
    xml = dbx.to_xml(data)
    assert '\t\t<field name="Empty" />\r\n' in xml, xml
    assert '\t<field name="x">x</field>\r\n' in xml, xml


def refusal(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return e
    raise AssertionError('read')


def test_string_tables():
    head = dbx.MAGIC + bytes(8)
    assert dbx._strings(head + struct.pack('>II', 0, 0)) == ([], 24), 'no strings: the table ends at 24'
    shared = head + struct.pack('>II', 9, 2) + struct.pack('>2I', 0, 0) + b'\0'
    assert dbx._strings(shared) == (['', ''], 33), 'two offsets to one string, the file ending with the area'
    assert str(refusal(dbx._strings, head)) == 'truncated .dbx: no string table header'
    assert 'past the file (0x21)' in str(refusal(dbx._strings, head + struct.pack('>II', 10, 2) + bytes(9)))
    assert '2 string offsets do not fit in the string area of 7 bytes' in \
        str(refusal(dbx._strings, head + struct.pack('>II', 7, 2) + bytes(9)))
    assert 'does not end inside the string area' in \
        str(refusal(dbx._strings, head + struct.pack('>II', 6, 1) + bytes(4) + b'ab' + b'\0'))
    assert 'a table of 3 string offsets' in str(refusal(dbx._strings, head + struct.pack('>II', 12, 3) + bytes(8)))


def test_refusals_by_kind():
    assert type(refusal(dbx.parse, b'')) is dbx.DeletedEntry
    assert str(refusal(dbx.parse, b'<?xml version="1.0"?><partition/>')) == 'not a binary .dbx file'
    strings = ['root', 'n']
    root = bytes([1, dbx.CONTAINER << 4])
    assert 'an element header at' in str(refusal(dbx.parse, partition(strings, bytes([1]))))
    assert 'a container at' in str(refusal(dbx.parse, partition(strings, root)))
    assert "1 container(s) not closed at the end of the file (innermost 'root')" in \
        str(refusal(dbx.parse, partition(strings, root + b'\0')))
    numbers = bytes([2, dbx.NUMBERS << 4, 1, 1, 7])
    assert '1 numbers of size 1' in str(refusal(dbx.parse, partition(strings, root + b'\0' + numbers + b'\0\0')))


def test_two_byte_numbers_and_a_value_at_the_end():
    strings = ['root', 'n', 'v']
    body = bytes([1, dbx.CONTAINER << 4, 0]) + bytes([2, dbx.NUMBERS << 4, 2, 2, 0, 1, 0, 2]) + b'\0'
    body += bytes([3, 1 << 4, 0, 1])                    # a value after the root closed, the file's last two bytes
    f = dbx.parse(partition(strings, body))
    assert f.partition is f.roots[0] and f.partition.tag == 'root'
    assert dbx.value(cast_children(f)[0]) == '1/2' and dbx.value(f.roots[1]) == 'true'


def cast_children(f):
    return f.partition.children or []


def test_an_instance_without_a_guid():
    strings = ['partition', 'instance', 'guid', 'g1']
    body = bytes([1, dbx.CONTAINER << 4, 0])
    body += bytes([2, dbx.CONTAINER << 4, 0, 0])        # no attributes
    body += bytes([2, dbx.CONTAINER << 4 | 1, 3, 4, 0, 0]) + b'\0'
    f = dbx.parse(partition(strings, body))
    assert len(f.instances) == 2 and f.instance('G1') is f.instances[1], 'searched past an instance without one'
