// DbxEdit: the binary .dbx format - varints, the element tree, DbxFile reading a file (FORMAT.md sections 1-2).
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
    // Varints: 7 bits per byte, low group first, high bit = more bytes follow.
    static class Varint
    {
        // A varint of a value the file has already been checked to hold (a string index, a count, a size). Values
        // that do not fit an int, and varints running past the data, are refused, never wrapped.
        public static int Read(byte[] data, ref int pos)
        {
            long value = ReadLong(data, ref pos, data.Length);
            if (value > int.MaxValue) throw new InvalidDataException("damaged .dbx: a number too large (" + value + ") before 0x" + pos.ToString("x"));
            return (int)value;
        }

        // As core dbx.py reads them: a varint running past end is truncated; one longer than 64 bits is damaged.
        // Values past long.MaxValue come back as long.MaxValue (too large for anything a file can hold).
        public static long ReadLong(byte[] data, ref int pos, int end)
        {
            ulong value = 0;
            bool over = false;
            for (int shift = 0; ; shift += 7)
            {
                if (pos >= end)
                    throw new InvalidDataException("truncated .dbx: a number runs past the end of the file (" + end + " bytes)");
                if (shift > 63)
                    throw new InvalidDataException("damaged .dbx: a number longer than 64 bits at 0x" + pos.ToString("x"));
                byte b = data[pos++];
                ulong bits = (ulong)(b & 0x7F);
                if (shift > 57 && (bits >> (64 - shift)) != 0) over = true;
                value |= bits << shift;
                if (b < 0x80) return over || value > long.MaxValue ? long.MaxValue : (long)value;
            }
        }

        public static void Write(List<byte> output, int value)
        {
            while (value >= 0x80 || value < 0)
            {
                output.Add((byte)(value & 0x7F | 0x80));
                value = (int)((uint)value >> 7);
            }
            output.Add((byte)value);
        }

        public static byte[] Bytes(int value)
        {
            var b = new List<byte>();
            Write(b, value);
            return b.ToArray();
        }
    }

    static class BigEndian
    {
        public static uint U32(byte[] data, int at)
        {
            return (uint)data[at] << 24 | (uint)data[at + 1] << 16 | (uint)data[at + 2] << 8 | data[at + 3];
        }

        public static void Put(List<byte> output, uint value)
        {
            for (int shift = 24; shift >= 0; shift -= 8) output.Add((byte)(value >> shift));
        }
    }

    sealed class Attribute
    {
        public int Name, Value;   // string-table indexes
        public Attribute(int name, int value) { Name = name; Value = value; }
    }

    // One element of the tree (FORMAT.md section 2). Containers have Children; other kinds keep their value
    // bytes exactly as stored (Payload).
    sealed class Element
    {
        public const int Container = 0xA, Text = 2, Numbers = 7;
        public int Tag;
        public int Kind;
        public readonly List<Attribute> Attributes = new List<Attribute>();
        public byte ContainerByte;
        public List<Element> Children;
        public byte[] Payload;

        public bool IsContainer { get { return Children != null; } }

        // A numbers payload's count and value size, and where its values start. A size past int (possible only
        // with no values: the reader checks count x size against the file) comes back as int.MaxValue.
        public static int NumbersHeader(byte[] payload, out int count, out int size)
        {
            int pos = 0;
            long c = Varint.ReadLong(payload, ref pos, payload.Length), s = Varint.ReadLong(payload, ref pos, payload.Length);
            count = (int)Math.Min(c, int.MaxValue);
            size = (int)Math.Min(s, int.MaxValue);
            return pos;
        }
    }

    // A parsed binary .dbx file.
    sealed partial class DbxFile
    {
        const string Magic = "{binary}";

        public readonly byte[] Original;
        public readonly bool IsBinary;
        public List<string> Table;
        public bool TableChanged;
        public List<Element> TopLevel;
        public uint KeptHeaderValue;
        List<int> originalOffsets;
        byte[] originalStringArea;   // offsets table excluded: the string bytes as stored
        public byte[] Trailer;              // the closing 0 of the tree and anything after it

        public DbxFile(string path) : this(File.ReadAllBytes(path)) { }

        // A new, empty file (header value 0, no trailing bytes: what every game file has). String 0 is the empty
        // string, as in every game file: index 0 cannot be a tag (a 0 closes the open element).
        DbxFile()
        {
            Original = new byte[0];
            IsBinary = true;
            Table = new List<string> { "" };
            TableChanged = true;
            TopLevel = new List<Element>();
            Trailer = new byte[0];
        }

        public static DbxFile CreateEmpty() { return new DbxFile(); }

        public DbxFile(byte[] data)
        {
            Original = data;
            IsBinary = data.Length > 24 && Encoding.ASCII.GetString(data, 0, 8) == Magic;
            if (!IsBinary) return;
            // The checks of core dbx.py (_strings, parse), in its order: a file it refuses is refused here.
            long areaSize = BigEndian.U32(data, 16), count = BigEndian.U32(data, 20);
            KeptHeaderValue = BigEndian.U32(data, 12);
            int n = data.Length;
            if (24 + 4 * count > n)
                throw Damaged("truncated .dbx: a table of " + count + " string offsets at 0x18 needs " + 4 * count + " bytes, the file ends at 0x" + n.ToString("x"));
            long stop = 24 + areaSize;                      // the string area: the offset table, then the strings
            if (stop > n)
                throw Damaged("truncated .dbx: the string area ends at 0x" + stop.ToString("x") + ", past the file (0x" + n.ToString("x") + ")");
            long stringsAt = 24 + 4 * count;
            if (stringsAt > stop)
                throw Damaged("damaged .dbx: " + count + " string offsets do not fit in the string area of " + areaSize + " bytes");
            originalOffsets = new List<int>((int)count);
            Table = new List<string>((int)count);
            for (int i = 0; i < count; i++)
            {
                long from = stringsAt + BigEndian.U32(data, 24 + 4 * i);
                int to = from < stop ? Array.IndexOf(data, (byte)0, (int)from, (int)(stop - from)) : -1;
                if (to < 0)
                    throw Damaged("damaged .dbx: string " + i + " at 0x" + from.ToString("x") + " does not end inside the string area (which ends at 0x" + stop.ToString("x") + ")");
                originalOffsets.Add((int)(from - stringsAt));
                Table.Add(Encoding.UTF8.GetString(data, (int)from, to - (int)from));
            }
            originalStringArea = new byte[stop - stringsAt];
            Array.Copy(data, stringsAt, originalStringArea, 0, originalStringArea.Length);

            // the tree, read with a stack (no recursion: any nesting the format allows is read)
            TopLevel = new List<Element>();
            var open = new List<Element>();
            int pos = (int)stop;
            while (pos < n)
            {
                int tagAt = pos;
                long tag = Varint.ReadLong(data, ref pos, n);
                if (tag == 0)
                {
                    if (open.Count == 0) { pos = tagAt; break; }      // the end of the tree: the trailer starts here
                    open.RemoveAt(open.Count - 1);
                    continue;
                }
                Need(data, pos, 1, "an element header");
                byte head = data[pos++];
                var e = new Element { Tag = StringIndex(tag, pos), Kind = head >> 4 };
                for (int k = head & 0xF; k > 0; k--)
                {
                    long name = Varint.ReadLong(data, ref pos, n), value = Varint.ReadLong(data, ref pos, n);
                    e.Attributes.Add(new Attribute(StringIndex(name, pos), StringIndex(value, pos)));
                }
                var siblings = open.Count > 0 ? open[open.Count - 1].Children : TopLevel;
                if (e.Kind == Element.Container)
                {
                    Need(data, pos, 1, "a container");
                    e.ContainerByte = data[pos++];
                    e.Children = new List<Element>();
                    siblings.Add(e);
                    open.Add(e);
                    if (open.Count > Depth) Depth = open.Count;
                    continue;
                }
                int valueAt = pos;
                if (e.Kind == Element.Text) StringIndex(Varint.ReadLong(data, ref pos, n), pos);
                else if (e.Kind == Element.Numbers)
                {
                    long values = Varint.ReadLong(data, ref pos, n), size = Varint.ReadLong(data, ref pos, n);
                    if (values != 0 && size < 2)               // every value reads at least two bytes (FORMAT.md section 3)
                        throw Damaged("damaged .dbx: " + values + " numbers of size " + size + " at 0x" + valueAt.ToString("x") + " (the smallest is 2 bytes)");
                    if (values != 0 && values > (n - pos) / size)
                        throw Damaged("truncated .dbx: " + values + " numbers of " + size + " bytes at 0x" + pos.ToString("x") + ", the file ends at 0x" + n.ToString("x"));
                    pos += (int)(values * size);
                }
                else
                {
                    Need(data, pos, 2, "a value");
                    pos += 2;
                }
                e.Payload = new byte[pos - valueAt];
                Array.Copy(data, valueAt, e.Payload, 0, e.Payload.Length);
                siblings.Add(e);
            }
            if (TopLevel.Count == 0)
                throw Damaged("damaged .dbx: no element after the string table (a partition has at least its root)");
            if (open.Count > 0)
                throw Damaged("truncated .dbx: " + open.Count + " container(s) not closed at the end of the file (innermost '" + TagOf(open[open.Count - 1]) + "')");
            Trailer = new byte[n - pos];
            Array.Copy(data, pos, Trailer, 0, Trailer.Length);
        }

        // The deepest nesting of containers (the partition is 1).
        public int Depth;

        // Editing, checking and comparing walk the tree recursively; they take files nested up to this deep (every
        // game file is far below it). Reading, converting to XML and round-tripping take any nesting.
        public const int MaxEditDepth = 1000;

        // An element's attribute count is the low 4 bits of its head byte (FORMAT.md section 2).
        public const int MaxAttributes = 15;

        // This file, when the recursive commands can take it (else InvalidDataException naming what).
        public DbxFile Walkable(string what)
        {
            if (Depth > MaxEditDepth)
                throw new InvalidDataException((what ?? "the file") + " nests containers " + Depth + " deep; DbxEdit edits, checks and compares files nested up to "
                                               + MaxEditDepth + " deep (xml and roundtrip take any)");
            return this;
        }

        // A .dbx file read from path: a damaged one is refused with its path in the message.
        public static DbxFile Read(string path)
        {
            try { return new DbxFile(path); }
            catch (InvalidDataException e) { throw new InvalidDataException(path + ": " + e.Message, e); }
        }

        static InvalidDataException Damaged(string message) { return new InvalidDataException(message); }

        static void Need(byte[] data, int pos, int size, string what)
        {
            if ((long)pos + size > data.Length)
                throw Damaged("truncated .dbx: " + what + " at 0x" + pos.ToString("x") + " needs " + size + " bytes, the file ends at 0x" + data.Length.ToString("x"));
        }

        int StringIndex(long index, int pos)
        {
            if (index >= Table.Count)
                throw Damaged("damaged .dbx: string " + index + " at 0x" + pos.ToString("x") + ", the table has " + Table.Count);
            return (int)index;
        }

        // The index of a string: the first identical table entry, or a new one at the end.
        public int Intern(string s)
        {
            int at = Table.IndexOf(s);
            if (at >= 0) return at;
            Table.Add(s);
            TableChanged = true;
            return Table.Count - 1;
        }

        public string TagOf(Element e) { return Table[e.Tag]; }

        // Value of the first attribute with that name, or null.
        public string Get(Element e, string attribute)
        {
            var a = e.Attributes.FirstOrDefault(x => Table[x.Name] == attribute);
            return a == null ? null : Table[a.Value];
        }

        public IEnumerable<Element> Instances()
        {
            if (TopLevel.Count == 0 || !TopLevel[0].IsContainer) return Enumerable.Empty<Element>();
            return TopLevel[0].Children.Where(e => TagOf(e) == "instance");
        }

        public Element Instance(string guid)
        {
            return Instances().FirstOrDefault(e => Guid.Same(Get(e, "guid"), guid));
        }
    }

    static class Guid
    {
        public static bool Same(string a, string b) { return string.Equals(a, b, StringComparison.OrdinalIgnoreCase); }
    }
}
