// DbxEdit - reads, converts and edits Battlefield: Bad Company 2 binary .dbx files (Frostbite 1 data as a
// tokenised XML tree). Written from FORMAT.md, which describes the format and everything this tool outputs.
// Anything an edit does not change is written back byte for byte.
//
// usage: DbxEdit xml <file.dbx|folder> [...]               write <name>.xml next to every binary .dbx
//        DbxEdit roundtrip <file.dbx|folder>               read + write every file, report any that change
//        DbxEdit edit <in.dbx> <out.dbx> <command> ...      apply commands in order (FORMAT.md section 5):
//            replace=<old>|<new>     every string exactly <old> becomes <new> (re-points references)
//            drop=<guid>             removes the instance with that guid; array items referring to it (locally) are
//                                    removed, single references to it become "null"
//            replaceprefix=<old>|<new>[|<except>...]  strings starting with <old> get <new> as prefix
//            partguid=<guid>         sets the partition's own guid (a copy under a new name gets a new one)
//            copyfield=<src.dbx>|<src guid>|<field>|<guid>  copies a field (with arrays) from another file's instance
//            set=<field>|<true|false|0-255>  sets a bool / byte field
//            setfield=<guid>|<field>|<value>  sets a field of that instance, typed as stored (text, numbers, bool /
//                                    byte), or a reference field's reference
//            additem=<guid>|<array>|<ref>[|<before ref>]  adds an item referring to <ref> to array <array> of
//                                    instance <guid> (at the end, or before the item referring to <before ref>)
//            dropfield=<name>        removes every field named <name> (fields the PC build does not have)
//            graft=<src.dbx>|<guid>[|<before guid>]  copies (inserted before that instance if given) that instance of another file, with every instance of that file it
//                                    references in-partition (members), strings remapped
//        a command argument "@file" reads commands from a file (one per line, # comments)
// Files are read as core dbx.py reads them (a damaged one is refused) and never written into the game folder
// (SafeWrite); exit 2 = bad usage or the command could not run (FORMAT.md sections 2, 3 and 5).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
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
    sealed class DbxFile
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

        public byte[] Serialize()
        {
            var stringArea = originalStringArea;
            var offsets = originalOffsets;
            if (TableChanged)
            {
                var bytes = new List<byte>();
                offsets = new List<int>(Table.Count);
                foreach (string s in Table)
                {
                    offsets.Add(bytes.Count);
                    bytes.AddRange(Encoding.UTF8.GetBytes(s));
                    bytes.Add(0);
                }
                stringArea = bytes.ToArray();
            }
            var tree = new List<byte>();
            foreach (var e in TopLevel) WriteElement(tree, e);
            tree.AddRange(Trailer);

            uint areaSize = (uint)(4 * Table.Count + stringArea.Length);
            var output = new List<byte>(Encoding.ASCII.GetBytes(Magic));
            BigEndian.Put(output, 24 + areaSize);
            BigEndian.Put(output, KeptHeaderValue);
            BigEndian.Put(output, areaSize);
            BigEndian.Put(output, (uint)Table.Count);
            foreach (int offset in offsets) BigEndian.Put(output, (uint)offset);
            output.AddRange(stringArea);
            output.AddRange(tree);
            return output.ToArray();
        }

        // An element and everything below it, written with a stack (no recursion).
        static void WriteElement(List<byte> output, Element root)
        {
            WriteHead(output, root);
            if (!root.IsContainer) return;
            var open = new Stack<KeyValuePair<Element, int>>();     // a container and its next child
            open.Push(new KeyValuePair<Element, int>(root, 0));
            while (open.Count > 0)
            {
                var top = open.Pop();
                if (top.Value >= top.Key.Children.Count) { output.Add(0); continue; }
                open.Push(new KeyValuePair<Element, int>(top.Key, top.Value + 1));
                var c = top.Key.Children[top.Value];
                WriteHead(output, c);
                if (c.IsContainer) open.Push(new KeyValuePair<Element, int>(c, 0));
            }
        }

        // tag, head byte, attributes, then the value bytes or a container's byte
        static void WriteHead(List<byte> output, Element e)
        {
            Varint.Write(output, e.Tag);
            output.Add((byte)(e.Kind << 4 | e.Attributes.Count));
            foreach (var a in e.Attributes)
            {
                Varint.Write(output, a.Name);
                Varint.Write(output, a.Value);
            }
            if (e.Kind != Element.Container) output.AddRange(e.Payload);
            else output.Add(e.ContainerByte);
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

    // FORMAT.md section 3: binary .dbx -> readable XML.
    static class XmlExport
    {
        static readonly HashSet<string> WholeNumberFields = new HashSet<string> {
            "OriginalHashedWaveName", "HashedName", "HashedWaveName", "OnRoadMaterialNameHashes", "Hash", "Id",
            "CompositeMeshPartNames" };

        // The XML of a binary file. The file is read (and checked) by DbxFile as core dbx.py reads it, so a file
        // dbx.py refuses is refused here too (InvalidDataException) and every other file gives dbx.py's to_xml.
        public static string Convert(byte[] data)
        {
            var f = new DbxFile(data);
            if (!f.IsBinary) throw new InvalidDataException("not a binary .dbx file (no {binary} header of 24 bytes)");
            return Convert(f);
        }

        // indentation tabs an export may hold: past it only absurd nesting (each level adds a tab to every line below)
        const long MaxIndent = 1L << 28;

        sealed class Item
        {
            public Element E; public int Depth; public bool Close;
            public Item(Element e, int depth, bool close) { E = e; Depth = depth; Close = close; }
        }

        // Written with a stack, not recursion: any nesting the reader takes is written.
        public static string Convert(DbxFile f)
        {
            long tabs = 0;
            var walk = new Stack<Item>();
            foreach (var e in f.TopLevel) walk.Push(new Item(e, 0, false));
            while (walk.Count > 0)
            {
                var it = walk.Pop();
                tabs += it.E.IsContainer ? 2L * it.Depth : it.Depth;
                if (tabs > MaxIndent)
                    throw new InvalidDataException("the XML would hold more than " + MaxIndent + " indentation tabs (containers nested " + f.Depth + " deep)");
                if (it.E.IsContainer) foreach (var c in it.E.Children) walk.Push(new Item(c, it.Depth + 1, false));
            }
            var xml = new StringBuilder("<?xml version=\"1.0\"?>\r\n");
            var todo = new Stack<Item>();
            for (int i = f.TopLevel.Count - 1; i >= 0; i--) todo.Push(new Item(f.TopLevel[i], 0, false));
            while (todo.Count > 0)
            {
                var it = todo.Pop();
                var e = it.E;
                string name = f.TagOf(e);
                if (it.Close)
                {
                    xml.Append('\t', it.Depth).Append("</").Append(name).Append(">\r\n");
                    continue;
                }
                xml.Append('\t', it.Depth).Append('<').Append(name);
                foreach (var a in e.Attributes)
                    xml.Append(' ').Append(f.Table[a.Name]).Append("=\"").Append(Escape(f.Table[a.Value])).Append('"');
                if (e.IsContainer)
                {
                    xml.Append(">\r\n");
                    todo.Push(new Item(e, it.Depth, true));
                    for (int i = e.Children.Count - 1; i >= 0; i--) todo.Push(new Item(e.Children[i], it.Depth + 1, false));
                    continue;
                }
                string content = Escape(ValueText(f, e));
                if (e.Kind == Element.Text && content.Length == 0)
                {
                    xml.Append(" />\r\n");
                    continue;
                }
                xml.Append('>').Append(content).Append("</").Append(name).Append(">\r\n");
            }
            return xml.ToString();
        }

        // The value of a non-container element as its XML shows it (unescaped): text, numbers joined by '/', or
        // true / false / a byte. field = the element's first attribute value (decides whole-number fields).
        public static string ValueText(DbxFile f, Element e)
        {
            string field = e.Attributes.Count > 0 ? f.Table[e.Attributes[0].Value] : "";
            int pos = 0;
            if (e.Kind == Element.Text) return f.Table[Varint.Read(e.Payload, ref pos)];
            if (e.Kind == Element.Numbers)
            {
                int n, size;
                pos = Element.NumbersHeader(e.Payload, out n, out size);
                var values = new string[n];
                for (int i = 0; i < n; i++, pos += size) values[i] = Number(e.Payload, pos, size, field);
                return string.Join("/", values);
            }
            byte v = e.Payload[1];
            return v == 0 ? "false" : v == 1 ? "true" : v.ToString();
        }

        public static bool IsWholeNumberField(string field) { return WholeNumberFields.Contains(field); }

        internal static string Number(byte[] data, int at, int size, string field)
        {
            if (size == 4)
            {
                int whole = (int)BigEndian.U32(data, at);
                int exponent = (whole >> 23) & 0xFF;
                if (WholeNumberFields.Contains(field) || (whole >> 24) == 0 || exponent == 0xFF)
                    return whole.ToString(CultureInfo.InvariantCulture);
                return BitConverter.ToSingle(BitConverter.GetBytes(whole), 0).ToString("R", CultureInfo.InvariantCulture);
            }
            if (size == 8)
            {
                long bits = (long)BigEndian.U32(data, at) << 32 | BigEndian.U32(data, at + 4);
                return BitConverter.Int64BitsToDouble(bits).ToString("R", CultureInfo.InvariantCulture);
            }
            return (data[at] << 8 | data[at + 1]).ToString();
        }

        static string Escape(string s)
        {
            if (s.IndexOfAny(new[] { '&', '<', '>', '"' }) < 0) return s;
            var b = new StringBuilder(s.Length + 16);
            foreach (char c in s)
            {
                switch (c)
                {
                    case '&': b.Append("&amp;"); break;
                    case '<': b.Append("&lt;"); break;
                    case '>': b.Append("&gt;"); break;
                    case '"': b.Append("&quot;"); break;
                    default: b.Append(c); break;
                }
            }
            return b.ToString();
        }
    }

    // Field types learned from binary files: per "tag/first attribute value", the element kind, number size,
    // whether 4-byte values are decimals or integers, and the first byte of bool / byte values.
    sealed class TypeBook
    {
        public sealed class Entry
        {
            public int Kind = -1, Size, FirstByte = 1, Floats, Ints;
            public byte ContainerByte;
        }

        readonly Dictionary<string, Entry> entries = new Dictionary<string, Entry>();

        public static string Key(string tag, string firstValue) { return tag + "/" + firstValue; }

        public void Learn(DbxFile f)
        {
            foreach (var e in f.TopLevel) Learn(f, e);
        }

        void Learn(DbxFile f, Element e)
        {
            string key = Key(f.TagOf(e), e.Attributes.Count > 0 ? f.Table[e.Attributes[0].Value] : "");
            Entry t;
            if (!entries.TryGetValue(key, out t)) entries[key] = t = new Entry { Kind = e.Kind };
            if (e.IsContainer)
            {
                t.ContainerByte = e.ContainerByte;
                foreach (var c in e.Children) Learn(f, c);
                return;
            }
            if (e.Kind == Element.Numbers)
            {
                int n, size;
                int pos = Element.NumbersHeader(e.Payload, out n, out size);
                if (n > 0 || t.Size == 0) t.Size = size;
                if (size == 4)
                    for (int i = 0; i < n; i++, pos += 4)
                    {
                        // evidence only: zero is the same either way, all-ones exponents are padding patterns
                        uint bits = BigEndian.U32(e.Payload, pos);
                        if (!XmlImport.PrintsAsWhole(bits, "")) t.Floats++;
                        else if (bits != 0 && (bits >> 24) == 0) t.Ints++;
                    }
            }
            else if (e.Kind != Element.Text) t.FirstByte = e.Payload[0];
        }

        public Entry Find(string tag, string firstValue)
        {
            Entry t;
            return entries.TryGetValue(Key(tag, firstValue), out t) ? t : null;
        }
    }

    // XML -> binary (FORMAT.md section 6). Elements are matched to a reference file (the original .dbx): unchanged
    // values keep their exact bytes, changed ones take the reference's types. New elements are typed from the
    // TypeBook; whatever is left is guessed from the XML and reported.
    sealed class XmlImport
    {
        sealed class Node
        {
            public string Tag, Text = "";
            public readonly List<KeyValuePair<string, string>> Attributes = new List<KeyValuePair<string, string>>();
            public List<Node> Children;   // null: no child elements
            public bool SelfClosing, Multiline;
            public int Line;
            public string FirstValue { get { return Attributes.Count > 0 ? Attributes[0].Value : ""; } }
        }

        public int Kept, Changed, Added, Removed, Rounded;
        public readonly List<string> Guesses = new List<string>();
        readonly DbxFile file;
        readonly DbxFile reference;
        readonly TypeBook types;

        XmlImport(DbxFile file, DbxFile reference, TypeBook types)
        {
            this.file = file;
            this.reference = reference;
            this.types = types;
        }

        // Reads <xml> into a binary file. reference: the original .dbx (or null); typeFiles: more files to learn
        // field types from. The result reuses the reference's string table (new strings are appended).
        public static DbxFile Import(string xmlPath, DbxFile reference, IEnumerable<DbxFile> typeFiles, out XmlImport report)
        {
            var types = new TypeBook();
            if (reference != null) types.Learn(reference);
            foreach (var t in typeFiles) types.Learn(t);
            var target = reference != null ? new DbxFile(reference.Original) : DbxFile.CreateEmpty();
            report = new XmlImport(target, reference != null ? target : null, types);
            var root = Parse(xmlPath);
            var old = target.TopLevel.Count > 0 ? target.TopLevel[0] : null;
            var top = report.Build(root, old != null && target.TagOf(old) == root.Tag ? old : null, root.Tag);
            target.TopLevel = new List<Element> { top };
            return target;
        }

        // A small XML reader of our own (not System.Xml): the export can contain what a strict parser rejects -
        // the same attribute twice (game files have it), control characters - and values must keep their exact
        // line breaks. Handles what editors write too: comments, <?...?>, ' or " quotes, entity and character
        // references, CDATA.
        static Node Parse(string path)
        {
            var p = new TextParser(File.ReadAllText(path));
            p.SkipProlog();
            if (!p.At("<")) throw new InvalidDataException("no root element in " + path);
            return p.Element();
        }

        sealed class TextParser
        {
            readonly string s;
            int pos, line = 1, depth;
            public TextParser(string text) { s = text; }

            public bool At(string t) { return string.CompareOrdinal(s, pos, t, 0, t.Length) == 0; }

            InvalidDataException Fail(string what) { return new InvalidDataException("line " + line + ": " + what); }

            void Advance(int n)
            {
                for (int i = 0; i < n && pos < s.Length; i++, pos++) if (s[pos] == '\n') line++;
            }

            void SkipTo(string end)
            {
                int at = s.IndexOf(end, pos, StringComparison.Ordinal);
                if (at < 0) throw Fail("'" + end + "' missing");
                Advance(at + end.Length - pos);
            }

            void SkipSpace() { while (pos < s.Length && char.IsWhiteSpace(s[pos])) Advance(1); }

            public void SkipProlog()
            {
                while (true)
                {
                    SkipSpace();
                    if (At("﻿")) Advance(1);
                    else if (At("<?")) SkipTo("?>");
                    else if (At("<!--")) SkipTo("-->");
                    else if (At("<!DOCTYPE")) SkipTo(">");
                    else return;
                }
            }

            string Name()
            {
                int start = pos;
                while (pos < s.Length && !char.IsWhiteSpace(s[pos]) && s[pos] != '/' && s[pos] != '>' && s[pos] != '=') Advance(1);
                if (pos == start) throw Fail("a name was expected");
                return s.Substring(start, pos - start);
            }

            string Decode(string raw)
            {
                if (raw.IndexOf('&') < 0) return raw;
                var b = new StringBuilder(raw.Length);
                for (int i = 0; i < raw.Length; i++)
                {
                    if (raw[i] != '&') { b.Append(raw[i]); continue; }
                    int semi = raw.IndexOf(';', i);
                    if (semi < 0) throw Fail("'&' without ';'");
                    string e = raw.Substring(i + 1, semi - i - 1);
                    if (e == "amp") b.Append('&');
                    else if (e == "lt") b.Append('<');
                    else if (e == "gt") b.Append('>');
                    else if (e == "quot") b.Append('"');
                    else if (e == "apos") b.Append('\'');
                    else if (e.StartsWith("#x") || e.StartsWith("#X")) b.Append(char.ConvertFromUtf32(Convert.ToInt32(e.Substring(2), 16)));
                    else if (e.StartsWith("#")) b.Append(char.ConvertFromUtf32(int.Parse(e.Substring(1), CultureInfo.InvariantCulture)));
                    else throw Fail("unknown entity &" + e + ";");
                    i = semi;
                }
                return b.ToString();
            }

            // an element and its children (recursive: nesting is limited as for the binary commands)
            public Node Element()
            {
                if (++depth > DbxFile.MaxEditDepth)
                    throw Fail("elements nested more than " + DbxFile.MaxEditDepth + " deep (DbxEdit imports files nested up to " + DbxFile.MaxEditDepth + ")");
                try { return ElementBody(); }
                finally { depth--; }
            }

            Node ElementBody()
            {
                Advance(1);   // '<'
                var n = new Node { Line = line, Tag = Name() };
                while (true)
                {
                    SkipSpace();
                    if (pos >= s.Length) throw Fail("<" + n.Tag + "> is not closed");
                    if (At("/>")) { Advance(2); n.SelfClosing = true; return n; }
                    if (At(">")) { Advance(1); break; }
                    string key = Name();
                    SkipSpace();
                    if (!At("=")) throw Fail("'=' expected after " + key);
                    Advance(1);
                    SkipSpace();
                    if (pos >= s.Length || (s[pos] != '"' && s[pos] != '\'')) throw Fail("a quoted value expected for " + key);
                    char q = s[pos];
                    Advance(1);
                    int end = s.IndexOf(q, pos);
                    if (end < 0) throw Fail("value of " + key + " not closed");
                    string raw = s.Substring(pos, end - pos);
                    Advance(end + 1 - pos);
                    n.Attributes.Add(new KeyValuePair<string, string>(key, Decode(raw)));
                }
                var text = new StringBuilder();
                bool realText = false;
                while (true)
                {
                    if (pos >= s.Length) throw Fail("<" + n.Tag + "> is not closed");
                    if (At("</"))
                    {
                        Advance(2);
                        string close = Name();
                        if (close != n.Tag) throw Fail("</" + close + "> closes <" + n.Tag + ">");
                        SkipSpace();
                        if (!At(">")) throw Fail("'>' expected");
                        Advance(1);
                        if (n.Children != null && realText) throw Fail("<" + n.Tag + "> has both text and child elements");
                        n.Text = n.Children == null ? text.ToString() : "";
                        n.Multiline = n.Children == null && !realText && n.Text.IndexOf('\n') >= 0;
                        return n;
                    }
                    if (At("<!--")) { SkipTo("-->"); continue; }
                    if (At("<?")) { SkipTo("?>"); continue; }
                    if (At("<![CDATA["))
                    {
                        Advance(9);
                        int end = s.IndexOf("]]>", pos, StringComparison.Ordinal);
                        if (end < 0) throw Fail("CDATA not closed");
                        text.Append(s, pos, end - pos);
                        realText = true;
                        Advance(end + 3 - pos);
                        continue;
                    }
                    if (At("<"))
                    {
                        if (n.Children == null) n.Children = new List<Node>();
                        n.Children.Add(Element());
                        continue;
                    }
                    int next = s.IndexOf('<', pos);
                    if (next < 0) next = s.Length;
                    string chunk = s.Substring(pos, next - pos);
                    if (chunk.Any(c => !char.IsWhiteSpace(c))) realText = true;
                    text.Append(Decode(chunk));
                    Advance(next - pos);
                }
            }
        }

        static string Norm(string s) { return s.Replace("\r\n", "\n").Replace('\r', '\n'); }
        static string NormAttribute(string s) { return s.Replace("\r\n", " ").Replace('\r', ' ').Replace('\n', ' ').Replace('\t', ' '); }

        // What identifies an element among its siblings: its guid, else its name, else its reference, else all
        // attributes (elements without attributes, like array entries, match by position).
        static string Key(string tag, IList<KeyValuePair<string, string>> attrs)
        {
            foreach (string id in new[] { "guid", "name", "ref" })
                foreach (var a in attrs)
                    if (a.Key == id) return tag + "\u0001" + id + "=" + NormAttribute(a.Value);
            return tag + "\u0001" + string.Join("\u0001", attrs.Select(a => a.Key + "=" + NormAttribute(a.Value)));
        }

        string KeyOf(Element e)
        {
            return Key(reference.TagOf(e), e.Attributes.Select(a => new KeyValuePair<string, string>(reference.Table[a.Name], reference.Table[a.Value])).ToList());
        }

        Element Build(Node x, Element old, string path)
        {
            var e = new Element { Tag = old != null ? old.Tag : file.Intern(x.Tag) };
            for (int i = 0; i < x.Attributes.Count; i++)
            {
                var a = x.Attributes[i];
                if (old != null && i < old.Attributes.Count && file.Table[old.Attributes[i].Name] == a.Key
                    && NormAttribute(file.Table[old.Attributes[i].Value]) == NormAttribute(a.Value))
                    e.Attributes.Add(new Attribute(old.Attributes[i].Name, old.Attributes[i].Value));
                else e.Attributes.Add(new Attribute(file.Intern(a.Key), file.Intern(a.Value)));
            }
            var learned = types.Find(x.Tag, x.FirstValue);
            int kind = KindOf(x, old, learned, path);
            e.Kind = kind;
            if (kind == Element.Container)
            {
                if (x.Children == null && x.Text.Trim().Length > 0)
                    throw new InvalidDataException("line " + x.Line + ": " + path + " holds other elements, not the value '" + x.Text.Trim() + "'");
                e.ContainerByte = old != null && old.IsContainer ? old.ContainerByte : learned != null && learned.Kind == Element.Container ? learned.ContainerByte : (byte)0;
                e.Children = new List<Element>();
                var children = x.Children ?? new List<Node>();
                var oldChildren = old != null && old.IsContainer ? old.Children : new List<Element>();
                foreach (var pair in Align(children, oldChildren, path))
                    e.Children.Add(Build(pair.Key, pair.Value, path + "/" + Describe(pair.Key)));
                if (old == null) Added++;
                return e;
            }
            if (x.Children != null) throw new InvalidDataException("line " + x.Line + ": " + path + " has child elements but is a value");
            if (old != null && old.Kind == kind && Norm(XmlExport.ValueText(reference, old)) == Norm(x.Text))
            {
                e.Payload = old.Payload;
                Kept++;
                return e;
            }
            e.Payload = Encode(kind, x, old, learned, path);
            if (old != null) Changed++; else Added++;
            return e;
        }

        static string Describe(Node n)
        {
            return n.Tag + (n.Attributes.Count > 0 ? "[" + n.FirstValue + "]" : "");
        }

        // Pairs each XML child with the reference child it corresponds to (or null): the next unused reference child
        // with the same key, in order. Reference children left over were removed.
        List<KeyValuePair<Node, Element>> Align(List<Node> xs, List<Element> olds, string path)
        {
            var queues = new Dictionary<string, Queue<int>>();
            for (int i = 0; i < olds.Count; i++)
            {
                string k = KeyOf(olds[i]);
                Queue<int> q;
                if (!queues.TryGetValue(k, out q)) queues[k] = q = new Queue<int>();
                q.Enqueue(i);
            }
            var result = new List<KeyValuePair<Node, Element>>();
            int next = 0, used = 0;
            foreach (var x in xs)
            {
                Queue<int> q;
                Element match = null;
                if (queues.TryGetValue(Key(x.Tag, x.Attributes), out q))
                {
                    while (q.Count > 0 && q.Peek() < next) q.Dequeue();
                    if (q.Count > 0)
                    {
                        int i = q.Dequeue();
                        match = olds[i];
                        next = i + 1;
                        used++;
                    }
                }
                result.Add(new KeyValuePair<Node, Element>(x, match));
            }
            Removed += olds.Count - used;
            return result;
        }

        int KindOf(Node x, Element old, TypeBook.Entry learned, string path)
        {
            if (x.Children != null) return Element.Container;
            if (old != null) return old.Kind;
            // where the XML's shape is unambiguous it decides (the export writes <x/> only for empty text, a line
            // break inside an empty element only for a container, <x></x> only for an empty number list)
            if (x.SelfClosing) return Element.Text;
            if (x.Multiline) return Element.Container;
            if (x.Text.Length == 0) return Element.Numbers;
            if (learned != null && learned.Kind >= 0 && learned.Kind != Element.Container) return learned.Kind;
            // nothing to go by: the XML's own shape
            if (x.Multiline) { Guess(x, path, "container"); return Element.Container; }
            if (x.SelfClosing) { Guess(x, path, "empty text"); return Element.Text; }
            if (x.Text == "true" || x.Text == "false") { Guess(x, path, "bool"); return 6; }
            if (x.Text.Length == 0 || x.Text.Split('/').All(t => { double d; return double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out d); }))
            {
                Guess(x, path, "numbers (4 bytes each)");
                return Element.Numbers;
            }
            Guess(x, path, "text");
            return Element.Text;
        }

        void Guess(Node x, string path, string what)
        {
            Guesses.Add("line " + x.Line + ": " + path + ": no type known, taken as " + what);
        }

        // The value bytes for a new text of an existing element, typed like it (what the import does for a changed
        // value). rounded: true if a decimal had to be rounded to the nearest 4-byte value.
        public static byte[] EncodeValue(DbxFile f, Element e, string text, out bool rounded)
        {
            var imp = new XmlImport(f, f, new TypeBook());
            var x = new Node { Tag = f.TagOf(e), Text = text };
            foreach (var a in e.Attributes) x.Attributes.Add(new KeyValuePair<string, string>(f.Table[a.Name], f.Table[a.Value]));
            byte[] payload;
            try { payload = imp.Encode(e.Kind, x, e, null, x.Tag + "[" + x.FirstValue + "]"); }
            catch (InvalidDataException ex)
            {
                const string noLine = "line 0: ";                     // a value typed in, not read from an XML file
                if (!ex.Message.StartsWith(noLine)) throw;
                throw new InvalidDataException(ex.Message.Substring(noLine.Length), ex);
            }
            rounded = imp.Rounded > 0;
            return payload;
        }

        byte[] Encode(int kind, Node x, Element old, TypeBook.Entry learned, string path)
        {
            string text = x.Text;
            if (kind == Element.Text) return Varint.Bytes(file.Intern(text));
            if (kind != Element.Numbers)
            {
                byte first = old != null && old.Payload != null && old.Payload.Length == 2 ? old.Payload[0] : learned != null ? (byte)learned.FirstByte : (byte)1;
                byte value;
                string t = text.Trim();
                if (t == "true") value = 1;
                else if (t == "false") value = 0;
                else if (!byte.TryParse(t, NumberStyles.None, CultureInfo.InvariantCulture, out value))
                    throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + text + "' is not true, false or 0-255");
                return new[] { first, value };
            }
            string[] tokens = text.Trim().Length == 0 ? new string[0] : text.Trim().Split('/');
            int oldCount = 0, oldSize = 0, oldAt = 0;
            if (old != null && old.Kind == Element.Numbers)
            {
                oldCount = Varint.Read(old.Payload, ref oldAt);
                oldSize = Varint.Read(old.Payload, ref oldAt);
            }
            int size = oldSize > 0 ? oldSize : learned != null && learned.Size > 0 ? learned.Size : 4;
            if (oldSize == 0 && (learned == null || learned.Size == 0) && tokens.Length > 0) Guess(x, path, "4-byte numbers");
            var output = new List<byte>();
            Varint.Write(output, tokens.Length);
            Varint.Write(output, size);
            for (int i = 0; i < tokens.Length; i++)
            {
                string token = tokens[i].Trim();
                uint? oldBits = size == 4 && i < oldCount ? (uint?)BigEndian.U32(old.Payload, oldAt + 4 * i) : null;
                if (size == 4)
                {
                    BigEndian.Put(output, FourBytes(token, x.FirstValue, oldBits, learned, x, path));
                }
                else if (size == 8)
                {
                    double d;
                    if (!double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out d))
                        throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not a number");
                    long bits = BitConverter.DoubleToInt64Bits(d);
                    BigEndian.Put(output, (uint)(bits >> 32));
                    BigEndian.Put(output, (uint)bits);
                }
                else if (size == 2)
                {
                    ushort v;
                    if (!ushort.TryParse(token, NumberStyles.None, CultureInfo.InvariantCulture, out v))
                        throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not 0-65535");
                    output.Add((byte)(v >> 8));
                    output.Add((byte)v);
                }
                else throw new InvalidDataException("line " + x.Line + ": " + path + ": numbers of " + size + " bytes cannot be written");
            }
            return output.ToArray();
        }

        // True if the XML shows these 4 bytes as a whole number (FORMAT.md section 3).
        public static bool PrintsAsWhole(uint bits, string field)
        {
            return XmlExport.IsWholeNumberField(field) || (bits >> 24) == 0 || ((bits >> 23) & 0xFF) == 0xFF;
        }

        // A 4-byte value: the integer or the decimal reading of the token, whichever the XML would show as exactly
        // this token. When both would (a whole decimal like 1.0 also shows as "1"), the original value's kind
        // decides, else the field's usual kind, else decimal (reported).
        uint FourBytes(string token, string field, uint? oldBits, TypeBook.Entry learned, Node x, string path)
        {
            var options = new List<uint>();
            int whole;
            if (int.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)
                && XmlExport.Number(Be((uint)whole), 0, 4, field) == token) options.Add((uint)whole);
            float f;
            if (float.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out f))
            {
                uint bits = BitConverter.ToUInt32(BitConverter.GetBytes(f), 0);
                if (XmlExport.Number(Be(bits), 0, 4, field) == token && !options.Contains(bits)) options.Add(bits);
            }
            if (options.Count == 0)
            {
                // not stored exactly either way (e.g. more digits than a 4-byte decimal holds): an integer for a whole
                // number in an integer field, else the nearest decimal
                bool integerField = oldBits.HasValue ? PrintsAsWhole(oldBits.Value, field) && oldBits.Value != 0
                                  : learned != null && learned.Ints > learned.Floats;
                if (integerField && int.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)) return (uint)whole;
                if (float.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out f))
                {
                    Rounded++;
                    return BitConverter.ToUInt32(BitConverter.GetBytes(f), 0);
                }
                throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not a number");
            }
            if (options.Count == 1) return options[0];
            // two readings: [0] integer, [1] decimal
            if (oldBits.HasValue) return PrintsAsWhole(oldBits.Value, field) ? options[0] : options[1];
            if (learned != null && learned.Floats + learned.Ints > 0) return learned.Floats >= learned.Ints ? options[1] : options[0];
            Guess(x, path, "decimal for '" + token + "'");
            return options[1];
        }

        static byte[] Be(uint v) { return new[] { (byte)(v >> 24), (byte)(v >> 16), (byte)(v >> 8), (byte)v }; }
    }

    // FORMAT.md section 5: the edit commands. Each returns a count or a success flag; Program prints.
    sealed class Editor
    {
        readonly DbxFile file;
        public Editor(DbxFile file) { this.file = file; }

        public int Replace(string from, string to)
        {
            int hits = 0;
            for (int i = 0; i < file.Table.Count; i++)
            {
                if (file.Table[i] != from) continue;
                file.Table[i] = to;
                file.TableChanged = true;
                hits++;
            }
            return hits;
        }

        public int ReplacePrefix(string from, string to, IList<string> except)
        {
            int hits = 0;
            for (int i = 0; i < file.Table.Count; i++)
            {
                string s = file.Table[i];
                if (!s.StartsWith(from, StringComparison.OrdinalIgnoreCase)) continue;
                if (except.Any(x => x.Length > 0 && s.StartsWith(x, StringComparison.OrdinalIgnoreCase))) continue;
                string head = s.Substring(0, from.Length);
                file.Table[i] = (head == head.ToLowerInvariant() ? to.ToLowerInvariant() : to) + s.Substring(from.Length);
                file.TableChanged = true;
                hits++;
            }
            return hits;
        }

        // Returns instances removed; references counts the references met.
        public int Drop(string guid, out int references)
        {
            references = 0;
            return Drop(file.TopLevel, guid, ref references);
        }

        int Drop(List<Element> list, string guid, ref int references)
        {
            int removed = 0;
            for (int i = list.Count - 1; i >= 0; i--)
            {
                var e = list[i];
                string tag = file.TagOf(e);
                if (tag == "instance" && Guid.Same(file.Get(e, "guid"), guid))
                {
                    list.RemoveAt(i);
                    removed++;
                    continue;
                }
                // a reference to the instance: a local one (its GUID). "<partition>/<guid>" names an instance of
                // another partition, which may share the GUID (graft copies keep their source's).
                string target = file.Get(e, "ref");
                if (target != null && Guid.Same(target, guid))
                {
                    references++;
                    if (tag == "item")
                    {
                        list.RemoveAt(i);
                        continue;
                    }
                    foreach (var a in e.Attributes.Where(a => file.Table[a.Name] == "ref")) a.Value = file.Intern("null");
                }
                if (e.IsContainer) removed += Drop(e.Children, guid, ref references);
            }
            return removed;
        }

        public int DropField(string name) { return DropField(file.TopLevel, name); }

        int DropField(List<Element> list, string name)
        {
            int removed = 0;
            for (int i = list.Count - 1; i >= 0; i--)
            {
                var e = list[i];
                if (file.TagOf(e) != "instance" && file.Get(e, "name") == name)
                {
                    list.RemoveAt(i);
                    removed++;
                }
                else if (e.IsContainer) removed += DropField(e.Children, name);
            }
            return removed;
        }

        public int SetByte(string name, byte value) { return SetByte(file.TopLevel, name, value); }

        int SetByte(List<Element> list, string name, byte value)
        {
            int set = 0;
            foreach (var e in list)
            {
                if (e.IsContainer) set += SetByte(e.Children, name, value);
                else if (e.Kind != Element.Text && e.Kind != Element.Numbers && e.Payload != null && e.Payload.Length == 2
                         && file.Get(e, "name") == name)
                {
                    e.Payload = new[] { e.Payload[0], value };
                    set++;
                }
            }
            return set;
        }

        // The first direct child of the instance named <field> that is not a container. A reference field (with a
        // ref attribute) gets <value> as its reference; any other value is typed like the stored one, as the import
        // types a changed value: text, numbers of the stored width ('/'-separated), integer or decimal as the stored
        // value is, bool or byte. False when there is no such field; InvalidDataException when the value does not
        // fit it. rounded: a decimal was rounded to the nearest 4-byte value.
        public bool SetField(string guid, string field, string value, out bool rounded)
        {
            rounded = false;
            var instance = file.Instance(guid);
            if (instance == null || !instance.IsContainer) return false;
            var e = instance.Children.FirstOrDefault(c => file.Get(c, "name") == field && !c.IsContainer);
            if (e == null) return false;
            var reference = e.Attributes.FirstOrDefault(a => file.Table[a.Name] == "ref");
            if (reference != null)
            {
                reference.Value = file.Intern(value);
                return true;
            }
            e.Payload = XmlImport.EncodeValue(file, e, value, out rounded);
            return true;
        }

        public void SetPartitionGuid(string guid)
        {
            foreach (var a in file.TopLevel[0].Attributes.Where(a => file.Table[a.Name] == "guid")) a.Value = file.Intern(guid);
        }

        public bool AddItem(string guid, string arrayName, string reference, string before)
        {
            var instance = file.Instance(guid);
            if (instance == null) return false;
            var array = instance.Children.FirstOrDefault(c => file.TagOf(c) == "array" && file.Get(c, "name") == arrayName);
            if (array == null || !array.IsContainer) return false;
            var shape = array.Children.FirstOrDefault(c => file.TagOf(c) == "item");
            if (shape == null) return false;
            var item = new Element { Tag = shape.Tag, Kind = shape.Kind, ContainerByte = shape.ContainerByte, Payload = shape.Payload };
            item.Attributes.Add(new Attribute(file.Intern("ref"), file.Intern(reference)));
            if (shape.IsContainer) item.Children = new List<Element>();
            int at = before == null ? -1 : array.Children.FindIndex(c => Guid.Same(file.Get(c, "ref"), before));
            array.Children.Insert(at >= 0 ? at : array.Children.Count, item);
            return true;
        }

        public bool CopyField(DbxFile source, string sourceGuid, string field, string guid)
        {
            var from = source.Instance(sourceGuid);
            var to = file.Instance(guid);
            if (from == null || to == null) return false;
            var original = FindField(source, from, field);
            if (original == null) return false;
            var copy = Import(source, original);
            if (!ReplaceField(to, field, copy)) to.Children.Add(copy);
            return true;
        }

        // First element named <field> below e that is not an instance (each element checked before its children).
        static Element FindField(DbxFile f, Element e, string field)
        {
            if (!e.IsContainer) return null;
            foreach (var c in e.Children)
            {
                if (f.TagOf(c) != "instance" && f.Get(c, "name") == field) return c;
                var deeper = FindField(f, c, field);
                if (deeper != null) return deeper;
            }
            return null;
        }

        bool ReplaceField(Element e, string field, Element replacement)
        {
            if (!e.IsContainer) return false;
            for (int i = 0; i < e.Children.Count; i++)
            {
                if (file.Get(e.Children[i], "name") == field)
                {
                    e.Children[i] = replacement;
                    return true;
                }
                if (ReplaceField(e.Children[i], field, replacement)) return true;
            }
            return false;
        }

        // Copies an element of another file into this one. Strings are interned in this order: tag, each
        // attribute's name then value, a text value, then the children (FORMAT.md section 5).
        Element Import(DbxFile source, Element e)
        {
            var copy = new Element { Tag = file.Intern(source.Table[e.Tag]), Kind = e.Kind, ContainerByte = e.ContainerByte };
            foreach (var a in e.Attributes)
            {
                int name = file.Intern(source.Table[a.Name]);
                copy.Attributes.Add(new Attribute(name, file.Intern(source.Table[a.Value])));
            }
            if (e.Kind == Element.Text)
            {
                int pos = 0;
                copy.Payload = Varint.Bytes(file.Intern(source.Table[Varint.Read(e.Payload, ref pos)]));
            }
            else copy.Payload = e.Payload;
            if (e.IsContainer) copy.Children = e.Children.Select(c => Import(source, c)).ToList();
            return copy;
        }

        // Copies instance <guid> of source and, depth first, every source instance it references locally that
        // this file does not have yet; inserted before instance <before>, or at the end of the partition.
        public int Graft(DbxFile source, string guid, string before)
        {
            var partition = file.TopLevel[0].Children;
            int at = partition.Count;
            if (before != null)
            {
                int i = partition.FindIndex(e => file.TagOf(e) == "instance" && Guid.Same(file.Get(e, "guid"), before));
                if (i >= 0) at = i;
            }
            var available = new Dictionary<string, Element>();
            foreach (var e in source.Instances())
            {
                string g = source.Get(e, "guid");
                if (g != null && !available.ContainsKey(g.ToUpperInvariant())) available[g.ToUpperInvariant()] = e;
            }
            var present = new HashSet<string>(file.Instances().Select(e => file.Get(e, "guid")).Where(g => g != null).Select(g => g.ToUpperInvariant()));
            var pending = new Stack<string>();
            pending.Push(guid.ToUpperInvariant());
            int copied = 0;
            while (pending.Count > 0)
            {
                string next = pending.Pop();
                Element e;
                if (present.Contains(next) || !available.TryGetValue(next, out e)) continue;
                present.Add(next);
                partition.Insert(at++, Import(source, e));
                copied++;
                foreach (string r in LocalReferences(source, e)) pending.Push(r);
            }
            return copied;
        }

        // Local references (36 characters, no '/') below e, upper-cased, each once, in document order.
        static List<string> LocalReferences(DbxFile f, Element e)
        {
            var found = new List<string>();
            var seen = new HashSet<string>();
            Collect(f, e, found, seen);
            return found;
        }

        static void Collect(DbxFile f, Element e, List<string> found, HashSet<string> seen)
        {
            string r = f.Get(e, "ref");
            if (r != null && r.Length == 36 && r.IndexOf('/') < 0 && seen.Add(r.ToUpperInvariant())) found.Add(r.ToUpperInvariant());
            if (e.IsContainer) foreach (var c in e.Children) Collect(f, c, found, seen);
        }
    }

    // Placed objects (ReferenceObjectData): blueprint name, position and cull slot (Enumeration).
    sealed class PlacedObject
    {
        public Element Instance, Slot;
        public string Guid, Blueprint;
        public float X, Y, Z;
        public int SlotValue { get { return Slot == null ? 0 : (int)BigEndian.U32(Slot.Payload, 2); } }

        public static List<PlacedObject> All(DbxFile f)
        {
            var list = new List<PlacedObject>();
            foreach (var i in f.Instances())
            {
                string type = f.Get(i, "type") ?? "";
                if (!type.EndsWith(".ReferenceObjectData")) continue;
                var o = new PlacedObject { Instance = i, Guid = f.Get(i, "guid") };
                foreach (var c in i.Children)
                {
                    string name = f.Get(c, "name");
                    if (name == "Enumeration" && c.Kind == Element.Numbers && c.Payload.Length == 6) o.Slot = c;
                    else if (name == "ReferencedObject")
                    {
                        string r = f.Get(c, "ref") ?? "";
                        var parts = r.ToLowerInvariant().Split('/');
                        o.Blueprint = parts.Length >= 2 ? parts[parts.Length - 2] : "";
                    }
                    else if (name == "Transform" && c.Kind == Element.Numbers && c.Payload.Length >= 2 + 16 * 4)
                    {
                        o.X = Float(c.Payload, 2 + 12 * 4);
                        o.Y = Float(c.Payload, 2 + 13 * 4);
                        o.Z = Float(c.Payload, 2 + 14 * 4);
                    }
                }
                list.Add(o);
            }
            return list;
        }

        static float Float(byte[] p, int at) { return BitConverter.ToSingle(BitConverter.GetBytes(BigEndian.U32(p, at)), 0); }

        public string PositionKey
        {
            get
            {
                var c = CultureInfo.InvariantCulture;
                return Blueprint + "@" + Math.Round(X, 1).ToString(c) + "," + Math.Round(Y, 1).ToString(c) + "," + Math.Round(Z, 1).ToString(c);
            }
        }

        public double Distance(PlacedObject o)
        {
            double dx = X - o.X, dy = Y - o.Y, dz = Z - o.Z;
            return Math.Sqrt(dx * dx + dy * dy + dz * dz);
        }

        public void SetSlot(int value)
        {
            Slot.Payload = new byte[] { 1, 4, (byte)(value >> 24), (byte)(value >> 16), (byte)(value >> 8), (byte)value };
        }
    }

    // Cull slots (FORMAT.md section 7): each object takes the slot of the same object in the level whose static cull
    // trees the file uses - same guid, else same blueprint and position, else the nearest unused same-blueprint
    // object within 50 m - and 0 (outside the cull tree) when there is none.
    static class SlotFixer
    {
        public sealed class Result { public int ByGuid, ByPosition, ByNearest, NoCounterpart, Changed; public readonly List<string> Unmatched = new List<string>(); }

        public static Result Fix(DbxFile file, DbxFile cullLevel)
        {
            var mine = PlacedObject.All(file).Where(o => o.Slot != null).ToList();
            var theirs = PlacedObject.All(cullLevel).Where(o => o.Slot != null).ToList();
            var byGuid = new Dictionary<string, PlacedObject>(StringComparer.OrdinalIgnoreCase);
            foreach (var o in theirs) if (o.Guid != null && !byGuid.ContainsKey(o.Guid)) byGuid[o.Guid] = o;
            var byPosition = new Dictionary<string, List<PlacedObject>>();
            foreach (var o in theirs)
            {
                List<PlacedObject> l;
                if (!byPosition.TryGetValue(o.PositionKey, out l)) byPosition[o.PositionKey] = l = new List<PlacedObject>();
                l.Add(o);
            }
            var r = new Result();
            var assigned = new Dictionary<PlacedObject, int>();
            // each counterpart, and each non-zero slot, is given once: an object matched by guid takes its slot first,
            // so a copy at the same place (another guid) cannot share it
            var taken = new HashSet<PlacedObject>();
            var used = new HashSet<int>();
            Action<PlacedObject, PlacedObject> give = (o, t) => { assigned[o] = t.SlotValue; taken.Add(t); if (t.SlotValue != 0) used.Add(t.SlotValue); };
            Func<PlacedObject, bool> free = t => !taken.Contains(t) && (t.SlotValue == 0 || !used.Contains(t.SlotValue));
            var byPlace = new List<PlacedObject>();
            foreach (var o in mine)
            {
                PlacedObject t;
                if (o.Guid != null && byGuid.TryGetValue(o.Guid, out t) && free(t)) { give(o, t); r.ByGuid++; }
                else byPlace.Add(o);
            }
            var left = new List<PlacedObject>();
            foreach (var o in byPlace)
            {
                List<PlacedObject> l;
                var t = byPosition.TryGetValue(o.PositionKey, out l) ? l.FirstOrDefault(free) : null;
                if (t != null) { give(o, t); r.ByPosition++; }
                else left.Add(o);
            }
            foreach (var o in left)
            {
                var best = theirs.Where(t => t.Blueprint == o.Blueprint && t.SlotValue != 0 && free(t))
                                 .OrderBy(t => t.Distance(o)).FirstOrDefault();
                if (best != null && best.Distance(o) < 50)
                {
                    give(o, best);
                    r.ByNearest++;
                }
                else
                {
                    assigned[o] = 0;
                    r.NoCounterpart++;
                    r.Unmatched.Add(o.Blueprint + " " + o.Guid);
                }
            }
            foreach (var kv in assigned)
                if (kv.Key.SlotValue != kv.Value) { kv.Key.SetSlot(kv.Value); r.Changed++; }
            return r;
        }
    }

    // BEGIN GENERATED BF1943 SCHEMA
    // Written by schema1943.py from bf1943-reflection-schema-full.json (SHA-256 4a45b0fb83a52adc5c47dc4904c95a89a06eebcd3a4a054e48393e7517f60e14),
    // read from the executable with SHA-256 cbb152d10bee2e9c0ed28732a1328791b5510672a83786a5daf67f7b5abff27a (1746 member records
    // recovered from the start-up code, value types and array element types added). Do not edit by hand.
    static class Bf1943Schema
    {
        // "<name> <c|s|e> <base class|-> <member>..." for 952 classes, 211 structs and 188 enums;
        // a member (6154 in all) is "name", "name:<type>" (a reflected class, struct or enum), "name[]" or
        // "name[]:<element type>"; an enum lists its value names
        public static readonly string[] Types = {
            "AIInteractionEntityData c InteractionEntityData AnimationId FireWeapon Weapon:SoldierWeaponAsset FireDelay FireFromBoneName FireAngleXZ FireElevationAngle FireInitialSpeed Probability UsableInRelaxed UsableInCombat AllowBotsOutsideUseAngle AttractiveDistance DisableEntity:ReferenceObjectData DisablePartNames[]",
            "AISettingsData c Asset SensingConstants:SensingConstantData SoundEnvironmentConstants:SoundEnvironmentConstantData CoverConstants:CoverConstantData NavigationConstants:NavigationConstantData SoldierControlConstants:SoldierControlConstantData CarControlConstants:CarControlConstantData TankControlConstants:TankControlConstantData HelicopterControlConstants:HelicopterControlConstantData TurretControlConstants:TurretControlConstantData TargetEvaluationConstants:TargetEvaluationConstantData VOConstants:VOConstantData CombatConstants:CombatConstantData EnvironmentConstants:EnvironmentConstantData FollowConstants:FollowConstantData PositionEvaluationConstants:PositionEvaluationConstantData TimingConstants:TimingConstantData LookConstants:LookConstantData DebugConstants:DebugConstantData KynapseConstants:KynapseConstantData TraversalConstants:TraversalConstantData ReadinessLevels:ReadinessLevels DefaultSensingTemplate:SensingTemplateData DefaultBehaviourTemplate:BehaviourTemplateData DefaultVehicleType:AIVehicleBehaviourData",
            "AIStateAnimationData c - AnimTree:AnimTreeDataAsset",
            "AIVehicleBehaviourData c - Name Controls[] Behaviours[]:BehaviourMappingData Goals[] Navigation:NavigationType PathfindingType:PathfindingType",
            "AIVehicleTypes c Asset VehicleTypes[]:AIVehicleBehaviourData Goals[]:BehaviourData SquadBehaviours[]:BehaviourData",
            "AabbGeometryEntityData c GeometryEntityData Extents:Vec3 Weight",
            "AbstractMovementNodeData c BaseNodeData MovementAngleParameter:FloatParameter",
            "AchievementData c - AchievementName AchievementId TrophyId Award:AwardData Group:AchievementGroup FriendlyName",
            "AchievementGroup e - AchievementGroup_singlePlayer AchievementGroup_multiPlayer",
            "AchievementsDataAsset c Asset Achievements[]:AchievementData",
            "ActionSuppressor s - ActionToSuppress:EntryInputActionEnum SuppressingValue",
            "ActivePart s - partName active",
            "AdsrNode c SoundNode Trigger:SoundNodePort Release:SoundNodePort A:SoundNodePort D:SoundNodePort S:SoundNodePort R:SoundNodePort Value:SoundNodePort",
            "AdvertisementComponentData c PartComponentData Identifier AdTexture",
            "AdvertisementZoneData s - Identifier Members[]:AdvertisementZoneMember",
            "AdvertisementZoneMember s - Identifier Filename",
            "AeroDynamicPhysicsData c - BodyDrag:Vec3 BodyDragOffsetYZ:Vec3 BodyDragOffsetXZ:Vec3 BodyDragOffsetXY:Vec3",
            "AimAnimationAsset c BaseAnimationAsset",
            "AimAtOrderEntityData c OrderEntityData Target:SphereGeometryEntityData",
            "AimControlData s - Animation:AimAnimationAsset InfluenceMap:InfluenceMapData PositionAnimatedBones:InfluenceMapData PrimaryBones[] PrimaryAnimationBones[] MinPrimaryAnimationBone AnimationFramesPerRow AimBones[]:AimNode2AimBoneData AnimationLimitLeft:Vec3 AnimationLimitRight:Vec3 AnimationLimitUp:Vec3 AnimationLimitDown:Vec3 LimitALeft LimitARight LimitAUp LimitADown ExponentialDelay AimStrength AimDirections[]:Vec3 LodNear:LodInterval LodFar:LodInterval LodProceduralCorrection:LodInterval PrimaryBonesProcCount PrimaryInfluenceMapBonesProcCount",
            "AimNode2AimBoneData c AnimNodePortGroupData AimBoneName AimPositionBoneName AimPositionBaseBoneName CorrectDirection AlwaysAnimate AimBoneWorldRotation:LinearTransform AimPositionBoneBaseTransforms[]:Vec3 AimBoneIndex AimPositionBoneIndex AimPositionBaseBoneIndex AimDirections[]:Vec3",
            "AimNode2Data c BaseNodeData AimBones[]:AimNode2AimBoneData AimTargetPositionParameter:Vec3Parameter PositionAnimatedBones:InfluenceMapData ExponentialDelay AimStrength LodNear:LodInterval LodFar:LodInterval LodProceduralCorrection:LodInterval AimAnimation:AimAnimationAsset",
            "AimOrigin e - AoDefault AoCamera AoWeapon",
            "AimingConstraintsData s - MinYaw MaxYaw MinPitch MaxPitch",
            "AimingPoseData s - MinimumPitch MaximumPitch TargetingFov AimSteadiness SpeedMultiplier RecoilMultiplier",
            "AirRaidEffectsData c WeaponEffectsData",
            "AirResistanceData c ProcessorData DragFactor",
            "AlphaResultType e - ArtUnchanged ArtHeight ArtSetToZero ArtSetToOne",
            "AlternateCameraViewData c - FieldOfView FovTransitionTime Mesh:RigidMeshAsset MeshOffset:Vec3 Hud:HudData FadeToBlack FadeInDuration FadeOutDuration BlackDuration InputSuppression:InputSuppressionData ToggleViewChange",
            "AlternateSpawnEntityData c EntityData Priority",
            "AlwaysNodeGroupData c AnimNodePortGroupData NodeData:BaseNodeData",
            "AmbientSoundsAsset c Editable GlobalAmbientSounds[]:SoundAsset BigWorld[]:BigWorldSettingsAsset MaterialSounds[]:MaterialSoundMapData MaterialBigWorld[]:MaterialBigWorldMapData SoundMaxHeight",
            "AmmoConfigData s - MagazineCapacity NumberOfMagazines TraceFrequency AmmoPickupMinAmount AmmoPickupMaxAmount AutoReplenishMagazine AutoReplenishDelay AutoReplenishMagazineCount ResetReplenishOnReload DisplayInHud",
            "AmmoCrateEntityData c GameEntityData Model:StaticModelEntityData Marker:MapMarkerEntityData Radius RefillDelay EnableReplenish",
            "AndExpressionData c CompositeExpressionData",
            "AnimNodePortGroupData c -",
            "AnimTreeData c AnimTreeDataAsset NodeList[]:BaseNodeData RootNode:BaseNodeData ParameterGroupData:ParameterGroupData Preprocessors[]:BasePreprocessorData ReplacementAnimationPairs[]:ReplacementAnimationPairData Skeleton:SkeletonAsset AbsoluteTimeParameter:FloatParameter DeltaTimeParameter:FloatParameter PoseParameter:IntParameter IgnorePriority",
            "AnimTreeDataAsset c Editable ResultAnimTrees[]:ResultAnimTreeData Priority BlendInTime",
            "AnimTreeReplacementData c Asset AnimTree:AnimTreeData ReplacementAnimations[]:BaseAnimationAsset Nodes[]:AnimationNodeData AnimationList[]:BaseAnimationAsset Controls[]:BaseAnimationControlData ControlMap[]",
            "AnimatedAimingEnum e - AnimatedAimingTwoHanded AnimatedAimingRightHanded AnimatedAimingDisabled",
            "AnimatedCameraData c CameraData Skeleton:SkeletonAsset AnimTree:AnimTreeData CameraBone FovBone UseWorldTransform LoadAnimTreeOnCreation",
            "AnimatedFireEnum e - AnimatedFireAutomatic AnimatedFireSingle AnimatedFireHoldAndRelease AnimatedFireSimple",
            "AnimatedMeshEntityData c EntityData Mesh:SkinnedMeshAsset AnimationController:AnimationControllerAsset MeshShaderSet:MeshShaderSetAsset",
            "AnimatedPropertyData s - BoneName DataIndex ValueOn ValueOff",
            "AnimatedSoldierRagdollSettingsData c - Ragdoll:RagdollAsset AnimatedRagdollBones[]",
            "AnimationAsset c BaseAnimationAsset MotionType:AnimationMotionType",
            "AnimationControlledStateData c CharacterStateData UpNormalTolerance",
            "AnimationControllerAsset c Asset AnimationGroups[]:AnimationGroupData Skeleton:SkeletonAsset",
            "AnimationEntityData c GameEntityData AnimTree:AnimTreeDataAsset BlendOutAnimTree Soldier:SoldierSpawnBaseEntityData StaticModel:ReferenceObjectData",
            "AnimationExportQuality c Asset Oversampling Uncompressed DefaultPositionTolerance DefaultOrientationTolerance PositionTolerance:QualityMapData OrientationTolerance:QualityMapData",
            "AnimationGroupData s - Animations[]:AnimationAsset",
            "AnimationMotionType e - AMT_CONST_SPEED AMT_REMOVE_MOTION AMT_WORLDSPACE AMT_LOCALSPACE AMT_LOCALSPACE2D",
            "AnimationNodeData c BaseNodeData Animation:BaseAnimationAsset ScaledAnimationTimeParameter:FloatParameter IsCyclic:TripleBool PlayFromBegining:TripleBool CallbackId NormalizedTimeParameter:FloatParameter HandsIk:PropertyCurveType WeaponIkDelta:PropertyCurveType WeaponIkAnimation:PropertyCurveType LegsIk:PropertyCurveType Aiming:PropertyCurveType FixLegsDistanceFromGround AimingManualInput:DisablePropertyCurve_ManualInput",
            "AnimationParamType e - APT_Bool APT_Int APT_Float APT_Vec3 APT_Mat4 APT_Count",
            "AnimationSettings s - EnableStats EnableJobs EnableFullCulling PreferSPUSampling",
            "AnimationSoundCategoryData s - AnimSound[]:AnimationSoundData Name",
            "AnimationSoundData s - Animation:AnimationAsset Sound:SoundAsset",
            "AnimationSoundMapData c Asset Categories[]:AnimationSoundCategoryData",
            "AntiSpamConfig s - TimeBetweenOrderExecution WarningSound:WaveAsset",
            "AreaGeometryEntityData c GeometryEntityData Next:AreaGeometryEntityData Previous:AreaGeometryEntityData Height Weight Waypoint:WaypointData",
            "AreaOrderEntityData c OrderEntityData Area[]:GeometryEntityData AreaType:AreaOrderType",
            "AreaOrderType e - AreaMoveTo AreaHoldGround",
            "AreaTriggerEntityData c TriggerEntityData Soldiers[]:SoldierSpawnBaseEntityData Geometry[]:GeometryEntityData ObjectToFollow:SpawnEntityData Include:AreaTriggerInclude Radius InsideAreaEventRepeatTime OneInsideAreaEventPerSoldier TriggerOnlyOnLeave",
            "AreaTriggerInclude e - ATNone ATAll ATPlayers ATAI ATAI_Alies ATAI_Enemies",
            "AreaValueTeam s - AreaValue Team:TeamId",
            "ArmDisarmAwardEntityData c GameEntityData",
            "ArmamentData c - ThreatLength IsAntiAircraft TurretData:TurretData",
            "ArtilleryStrikeEntityData c GameEntityData Geometry[]:GeometryEntityData MissileData:MissileEntityData Squad:SquadBaseEntityData Soldier:SoldierSpawnBaseEntityData MissileSpawnRadius DurationOfArtilleryStrike MaxDelayBetweenProjectiles MinDelayBetweenProjectiles MaxDelayBetweenStrikes MinDelayBetweenStrikes MinRadiusToSoldier RadiusWhenFollowingSoldier HeightOffsetWhenFollowingSoldier NumberOfStrikes MaxProjectilesPerStrike MinProjectilesPerStrike FollowSoldier",
            "ArtilleryStrikeFiringEffectsData c WeaponEffectsData MaxStrikeDistance StrikeRadius SpawnHeight MaxRandomSpawnHeight Camera:TargetCameraData AimingCameraHeight StrikeCameraHeight AimingCameraOffset StrikeCameraOffset AimingCameraFov DelayBeforeAimingCamera StrikeCameraFov FireCameraTime StrikeCameraTime ValidMinDistance ValidMaxDistance ValidMaxAngle EnableProjectileTrails EnableCameraRotation",
            "Asset c - Name",
            "AudioMasterSettingsAsset c Asset SoundSystemSettings[]:SoundSystemSettings",
            "AudioMaterialDefinitions c Editable OnRoadMaterialNameHashes[]",
            "AutoAimData s - AutoAimOuterBoxOffset:Vec3 AutoAimOuterBoxExtends:Vec3 AutoAimInnerBoxOffset:Vec3 AutoAimInnerBoxExtends:Vec3 PoseType:CharacterEntityPoseEnum",
            "AutoEndingEnumeratedSwitchNodeData c BaseNodeData Nodes[]:AutoEndingEnumeratedSwitchNodeGroupData AnimationStartParameter:BoolParameter NodeSelectorParameter:IntParameter ElseNode:BaseNodeData",
            "AutoEndingEnumeratedSwitchNodeGroupData c AnimNodePortGroupData NodeData:BaseNodeData",
            "AutoEndingLoopNodeData c BaseNodeData Node:BaseNodeData ElseNode:BaseNodeData Parameter:BoolParameter NodeNormalizedBlendOutTime RestartAnimation",
            "AutoTeamEntityData c GameEntityData PlayerCountNeededToAutoBalance TeamDifferenceToAutoBalance TeamDifferencePercentageToAutoBalance ForcePlayersToTeam1",
            "AwardData c TreeNodeBase Criteria[]:CriteriaData Code ChildAwards[]:AwardData Description UIName Image:TextureAsset ImageDescription ImageName Sound:WaveAsset SoundName VO:DialogLineNode Visible Score Repeat:AwardType Dependencies[]:CriteriaAward Group:AwardGroup Bucket:ScoringBucket",
            "AwardDataTree c AwardableTreeBase Awards[]:AwardData",
            "AwardGridAsset c Asset Width Height Awards[]:AwardGridNode",
            "AwardGridNode c - X Y Award:AwardData",
            "AwardGroup e - AwardGroup_Achievement AwardGroup_PatchRound AwardGroup_PatchShield AwardGroup_Trophy AwardGroup_Wildcard AwardGroup_Rank",
            "AwardType e - AwardType_OnceGlobally AwardType_OncePerRound AwardType_Unlimited",
            "AwardableTreeBase c TreeBase CategoryTrees[]:StatCategoriesTree GeneralStatistics Scales[]:EventScaleData GlobalScaleFactor",
            "AxisAlignedBox s - min:Vec3 max:Vec3",
            "BangerEntityBaseData c DynamicPhysicsEntityData Mesh:MeshAsset MeshShaderSet:MeshShaderSetAsset ConstructiblePreviewData:ConstructiblePreviewData Explosion:ExplosionEntityData MaxHealth TimeToLive",
            "BangerEntityData c BangerEntityBaseData",
            "BaseAnimationAsset c Asset Skeleton:SkeletonAsset",
            "BaseAnimationControlData s - AnimationIndex InfluenceMap:InfluenceMapData NodeIndex",
            "BaseEmitterData c ProcessorData EmitterAsset:EmitterDocument",
            "BaseExpressionData c -",
            "BaseHandsIkNodeData c BaseNodeData DisabledHandsIk[] Pose:AnimationNodeData HandsIkInfo[]:HandIkInfo",
            "BaseInfluenceMapData c Asset Skeleton:BaseSkeletonAsset ResultInfluenceMaps[]:ResultInfluenceMapData",
            "BaseNodeData c - Skeletons[]:SkeletonAsset InfluenceMapEffects[]:InfluenceMapEffectData InfluenceMapData:InfluenceMapData OriginalInfluenceMap:InfluenceMapData NodeWeightParameter:FloatParameter BlendInTime BlendOutTime NodeIndex ParentNodeIndex EnabledBySkeleton",
            "BaseParameter c - Name DataIndex",
            "BasePreprocessorData c -",
            "BaseShaderAsset c Asset SourceFile Source FunctionName Ps3ExtraCmdLine",
            "BaseSkeletonAsset c Asset",
            "BehaviourData c UrgencyUserData Name",
            "BehaviourMappingData s - Behaviour Plan",
            "BehaviourTemplateData c Asset StandProbability CrouchProbability StunnedWhenHealthPercentageBelow StunnedInVehicles TurnAgainstTargetDistance InvestigateStopDelay ExplosiveObjectChance MinAttackBursts MaxAttackBursts AttackUrgencyModifier AltAttackProbability ReadinessReactionTimeMultiplier BaseTimeInCover SuppressedTimeAdd MinEnemyDistanceToUseCover LowHealthThreshold UseSameCoverModifier RollOutOfCoverProbability ExplosionEmergentTakeCoverProbability MeleeStartDistance MeleeDamage MeleePushForce MeleeDelay MeleeAnimationId FrontLineDistance ObstructorRespondDistance ObstructorMinMoveDistance ObstructorMaxMoveDistance AlwaysUpdate AllowSleep TargetDistanceFactor TargetStrengthFactor TargetDangerFactor TargetIsAttackingFactor TargetIsMarkedFactor LeaveVehicleOnSearchAndDestroyOrder RPGSkill:RpgSkill AccuracyModifier AccuracyIncreaseTime SuppressIndirectProbability MaxSuppressTime ReloadBelowPercent",
            "BigWorldSetting c - Sound:SoundTemplate MinDistance MaxDistance MinDelayTimeInMinutes MaxDelayTimeInMinutes",
            "BigWorldSettingsAsset c Editable Settings[]:BigWorldSetting",
            "BinarySetting s - Name",
            "BinkTextureAsset c TextureBaseAsset Gamma:TextureAssetGamma Usage:TextureUsage",
            "BlendMode e - BmNone BmLerp BmAdditive BmSubtractive BmRevSubtractive BmMultiply BmMin BmMax BmAdditiveDestOpacity",
            "BlendShapeAnimationAsset c Asset SourceFile RootNode",
            "BlendShapeSkeletonAsset c Asset SourceFile RootNode",
            "BlurEffectData s - DispersionStrength DispersionThreshhold ExplosionStrength BulletStrength ExplosionFalloffSpeed BulletFalloffSpeed",
            "BlurFilter e - BfNone BfGaussian3Pixels BfGaussian5Pixels BfGaussian7Pixels BfGaussian9Pixels BfGaussian15Pixels BfGaussian31Pixels",
            "BoatFloatPhysicsData c HullFloatPhysicsData FrontRatio",
            "BoltActionData s - BoltActionDelay BoltActionTime HoldBoltActionUntilFireRelease",
            "BoneCollisionData s - DebugDrawColor:Vec4 CapsuleOffset:Vec3 BoneName Material BoneAxis CapsuleLength CapsuleRadius Modifiers[]:BoneCollisionReplacementData",
            "BoneCollisionReplacementData c UnlockPartData ReplacementMaterial",
            "BoneFakePhysicsData c - FakePhysics:FakePhysicsData BoneName BoneId",
            "BonePriority c - BoneName EualizeToBoneName BoneIndex EualizeToBoneIndex",
            "BoneToBoneIkNodeData c BaseNodeData Pose:BaseAnimationAsset BoneName TargetBoneName LocalPosition:LinearTransform BoneIndex TargetBoneIndex",
            "BoolNodeGroupData c AnimNodePortGroupData NodeData:BaseNodeData Parameter:BoolParameter",
            "BoolParameter c BaseParameter InitialValue",
            "BoolParameterExpressionData c BaseExpressionData Parameter:BoolParameter Value",
            "BoolPropertyTrackData c PropertyTrackData Values[]",
            "BoolValueGroupData c AnimNodePortGroupData Parameter:BoolParameter Invert",
            "BotPriorityConfigData c - AngleOffset Distance Period AppliesToSensing AppliesToDecisions AppliesToExecution DebugColor:Vec3",
            "BoundingBoxAsset c Asset Boxes[]:OrientedBoundingBox",
            "BoundingBoxAssetMetaData c - Boxes[]:OrientedBoundingBox",
            "BoundingBoxExportResult c - OutputFileName",
            "BoxEvaluatorData c EvaluatorData Dimensions:Vec3 Pivot:Vec3",
            "BoxFloatPhysicsData c FloatPhysicsData",
            "BreakWindowInteractionEntityData c AIInteractionEntityData WindowPartIndex",
            "BulletEntityData c MeshProjectileEntityData FlyBySound:SoundAsset Gravity ImpactImpulse DetonationTimeVariation VehicleDetonationRadius VehicleDetonationActivationDelay FlyBySoundRadius FlyBySoundSpeed Stamina DistributeDamageOverTime StartDamage EndDamage DamageFalloffStartDistance DamageFalloffEndDistance TimeToArmExplosion HasVehicleDetonation VehicleDetonationTargets[]:ControllableEntityData InstantHit StopTrailEffectOnUnspawn",
            "BundleMountPoint s - Bundle VfsPath IsResourcePath",
            "BusConnection e - GameMixMain GameMixAlt DirectOut",
            "CallbackWeaponEffectsData c WeaponEffectsData CallbackEntityName",
            "CameraComponentData c ComponentData Camera:TargetCameraData FieldOfView AlternateView:AlternateCameraViewData FreezeHeight IsSnapViewCamera IsFirstPerson ExcludeFromCameraCycle ReceiveImpulsesAsThirdPerson UseCameraTransition CameraTransitionPos:Vec3 CameraTransitionTime Mesh:RigidMeshAsset MeshOffset:Vec3 MeshParentComponentNumber UseForegroundBlur InputSuppression:InputSuppressionData",
            "CameraData c - OcclusionRayOffset:Vec3 PreFadeTime FadeTime FadeWaitTime StayFadedWhileStreaming HudFx:HudFxData ShadowViewDistance",
            "CameraEntityData c GameEntityData fixforbadddfgenerationwithonlyoverrideproperties",
            "CameraImpulseCurveData s - MinDuration MaxDuration MinFrequency MaxFrequency LeadInTime Damping MaxAngle MaxActiveImpulses AverageFilterSize",
            "CameraImpulseShakeData s - YawCurve:CameraImpulseCurveData PitchCurve:CameraImpulseCurveData RollCurve:CameraImpulseCurveData",
            "CameraLeapData s - Transform:LinearTransform Time FovModifier",
            "CameraRecoilData c - SpringConstant SpringDamping SpringMinThresholdAngle",
            "CampaignMapEntityData c GamePhysicsEntityData CombatAreaName",
            "CampaignRoundCounterEntityData c GameEntityData BleedStartAtAreaValue BaseBleedPerMin BleedAtEndPerMin UiMaxBleedPerMin",
            "CancelOrderEntityData c OrderEntityData",
            "CapturePointEntityData c GameEntityData FlagTemplates[]:StaticModelEntityData Spawners[]:SpawnEntityData Geometry[]:GeometryEntityData MapMarker:MapMarkerEntityData CapturePoint:StaticModelEntityData LocationName ControlPointName InitialOwnerTeam:TeamId CaptureRadius IsCapturedInUpperSphere AreaValueTeam1 AreaValueTeam2 AreaValues[]:AreaValueTeam TimeToGetControl TimeToLoseControl ReturnMultiplier OnlyTakeableByTeam MinNrToTakeControl MaxCaptureMultiplier AddedMultiplierPerPlayer EnemyTicketLossWhenCaptured Index IntruderWarning IntruderWarningSID DisableIfEnemyInside DisableWhenLosingControl LoseControlWhenNotClose UnableToChangeTeam IsVisible ShowOnMinimap HoistFlag StartAtBottom RemoveWhenCaptured SeesawCapturing",
            "CarControlConstantData c - YawStrength",
            "CarHornComponentData c ComponentData SoundEffect:SoundAsset",
            "CarNode c SoundNode Start:WaveAsset Stop:WaveAsset IdleLoop:WaveAsset Gear1:WaveAsset Gear2:WaveAsset Gear3:WaveAsset CruiseLoop:WaveAsset Gear1Release:WaveAsset Gear2Release:WaveAsset Gear3Release:WaveAsset Release:WaveAsset TransmissionLoop:WaveAsset InteriorLoop:WaveAsset InteriorStrainLoop:WaveAsset StuckLoop:WaveAsset PipeAttacks[]:WaveAsset SuspensionBounces[]:WaveAsset Throttle:SoundNodePort RPM:SoundNodePort Velocity:SoundNodePort InDriverPos:SoundNodePort InteriorCam:SoundNodePort SpringCompression:SoundNodePort Gear:SoundNodePort Tilt:SoundNodePort Slip:SoundNodePort AngularVelocity:SoundNodePort ReleaseEvent:SoundNodePort StartOut:SoundNodePort StopOut:SoundNodePort IdleOut:SoundNodePort AccellerateOut:SoundNodePort DeaccellerateOut:SoundNodePort TransmissionOut:SoundNodePort InteriorOut:SoundNodePort InteriorStrainOut:SoundNodePort PipeAttackOut:SoundNodePort SuspensionBounceOut:SoundNodePort StuckOut:SoundNodePort Gear1MaxVelocity Gear2MaxVelocity Gear3MaxVelocity Reverse_Idle_Rpm_Pitch:Vec4 Reverse_Idle_Tilt_Pitch:Vec4 Reverse_Idle_Rpm_Amp:Vec4 Idle_Tilt_Pitch:Vec4 Idle_Rpm_Pitch:Vec4 Accellerate_Tilt_Pitch:Vec4 Accellerate_Slip_Pitch:Vec4 Cruise_Rpm_Pitch:Vec4 Accellerate_AngularVel_Pitch:Vec4 Release_Tilt_Pitch:Vec4 Trans_Vel_Pitch:Vec4 Trans_Vel_Amp:Vec4 Trans_Reverse_Amp_Factor InteriorStrain_Vel_Pitch:Vec4 InteriorStrain_Vel_Amp:Vec4 Stuck_Vel_Amp:Vec4 Stuck_Rpm_Amp:Vec4 StuckVelocityThreshold PipeAttackMaxVelocity PipeAtatckThrottleDeltaThreshold StuckIdleTimeThreshold",
            "ChangeFormationOrderEntityData c OrderEntityData Formation:FormationData Scale",
            "ChangeGameSettingType e - CGSNone CGSInvertVerticalLookAxis CGSCount",
            "ChangeOrderEntityData c OrderEntityData Vehicle:VehicleSpawnEntityData DisallowedIndices[] Instant",
            "ChangeReadinessOrderEntityData c OrderEntityData Pulse:ReadinessLevel Minimum:ReadinessLevel AllowDecrease",
            "CharacterEntityPoseEnum e - Stand Crouch Prone",
            "CharacterNode c VoiceOverRootNode Children[]:VoiceOverChildNode",
            "CharacterPhysicsData c Asset Poses[]:CharacterPoseData States[]:CharacterStateData Sprint:CharacterSprintData Material PushableObjectWeight Mass MaxAscendAngle PhysicalRadius WaterDepthLimit InputAcceleration LadderAcceptAngle LadderAcceptAnglePitch JumpPenaltyTime JumpPenaltyFactor Rumble:CharacterRumbleData SpeedPenaltyOnDamage SpeedPenaltyOnDamageTime",
            "CharacterPoseConstraintsData s - StandPose CrouchPose PronePose",
            "CharacterPoseData c - EyePosition:Vec3 ThrottleModifierCurve[]:Vec2 StepHeight Height TransitionTime LookConstraints:LookConstraintsData PoseType:CharacterEntityPoseEnum",
            "CharacterPoseType e - CPTNoAction CPTForceToStand CPTForceToCroch",
            "CharacterRumbleData s - LowRumbleModifier HighRumbleModifier RumbleDuration RumbleImpulseClamp",
            "CharacterSprintData c - SprintPowerDecreasePerSecond SprintPowerIncreasePerSecond SprintMinimumPower",
            "CharacterStateData c - PoseInfo[]:CharacterStatePoseInfo",
            "CharacterStatePoseInfo c - PoseType:CharacterEntityPoseEnum Velocity AccelerationGain DecelerationGain SprintGain SprintMultiplier",
            "CharacterStateTriggerEntityData c TriggerEntityData Vehicles[]:VehicleSpawnEntityData PlayerToCheck:SoldierSpawnBaseEntityData NewPosition:AlternateSpawnEntityData NewVehiclePositionOnCharacterMove:AlternateSpawnEntityData UsePitch Pitch CharacterPose:CharacterPoseType ExcludeConnectedVehicles Weapon:SoldierWeaponAsset CheckAllWeapons ContinueToCheckIfHoldingWrongWeapon VehiclePosition CheckVehiclePosition EntryInputAction:EntryInputActionEnum SuppressInputOnInputTriggerd HealthToSet HealthToCheck CheckHealthOnAndAbove AmmoToSet",
            "ChaseCameraData c TargetCameraData TargetOffset:Vec3 KeepTargetPitch ToWantedPositionScale:Vec3 MaxViewRotationAngleDeg WantedAngleDeg WantedDistance MaxDistance SnapDistance ForceFieldRadius CollisionRadius ForceFieldForceScale AwayFromTargetForceScale VelocityDrag MaxVelocity PillMinimumCollisionRadius PillMaximumCollisionRadius PillMinimumCollisionLength PillExpandSizeSpeedAcceleration LookDistanceScale LookDistanceInFrontOfTarget ShouldRollWithTarget",
            "ChildComponentData c PartComponentData SoundEffect:SoundAsset MovingBody:MovingBodyData HealthZone:VehicleHealthZoneData SoundEffectStartRpm SoundEffectStopRpm",
            "ChildMovingBodyData c LinearMovingBodyData InputDelay Speed InputAction IsOneShotInput TriggerImpulse",
            "ChildRotationBodyData c RotationBodyData ForceModifier ResetForceModifier ResetForceInputThreshold UseLinearInput RotationInput WorldSpaceLock WorldSpaceLockEfficiency",
            "ClearAreaTriggerEntityData c TriggerEntityData AreaToEffect:GeometryEntityData Action:ClearType EffectSoldiers EffectVehicles EffectBangers SoldiersToExclude[]:SoldierSpawnBaseEntityData ExcludeImmortalSoldiersInTeam TeamOfImmortalSoldiers:TeamId",
            "ClearType e - CTDestroy CTUnspawn",
            "ClientSettings c - IsFastStartupEnabled IsTerrainDisabled IsSootEnabled IsWaterPhysicsEnabled IsSoundEnabled Ps3SoundCellCacheSize Ps3SoundMaxRsxMemoryUsage Ps3SoundMinRsxAllocation Ps3OnDemandSoundInRsxEnable MaxSoundCount EnableAudioTweakHack IsOnDemandSoundsEnabled IsSubtitlesEnabled IsVariedVoiceOversEnabled IsOvergrowthEnabled IsMultiplayer IsStatsEnabled IsNetworkStatsEnabled PauseGameOnStartUp IsEffectsDisabled IsEmittersDisabled IsHavokEmittersDisabled IsPadRumbleEnabled IsLipSyncEnabled IsUITracesEnabled IsMenuDevOptsEnabled ScreenshotToFile ScreenshotFilename IsHudDisabled DisableMovies DisableHudMovies DisableMenuFlow LoadMenu IsPresenceEnabled IsPresenceSilent IsHintsEnabled BypassMatchmaking DebugMatchmaking DebugMenuOnLThumb InvertFreeCamera RenderTags Team Kit Level SpawnPoint ServerIp SecondaryServerIp InvertPitch InvertYaw AimScale IncomingFrequency IsInternetSimulationEnabled MinLatency MaxLatency PacketDrops DropSpikeChance MinDropDuration MaxDropDuration ReorderingChance DuplicationChance CorruptionChance HavokVisualDebugger HavokVDBShowsEffectsWorld HavokCaptureToFile MemoryStatsEnable UseMouseAndKeyboardSystem UseGlobalGamePadInput PcMouseSensitivity PcPadSensitivity IsAchievementsEnabled DebugAdvertisements EnableAdvertisements IsSpecialEdition IsNorthAmerica PreloadingEnabled ThreadedLoadingEnable ShowBuildId ExtractPersistenceInformation TextureBakerEnable UnrestrictedUnlocks LoadingTimeout LoadedTimeout IngameTimeout EnableRestTool KeepEffects LocalVehicleSimulationEnabled AsyncClientBulletEntity AsyncFollowWheels AutoUnspawnDynamicObjects StreamingSoundPath UsePS3VoipCompression ResetStatsOnConnect DisableTelemetry DebugCoralSea",
            "ClimbingStateData c CharacterStateData LateralInputScale DownAngleLimit DropOffAngle AttractionVelocity PushAwayVelocity ClimbHeightOffset",
            "CollectibleWeapon s - Sid Texture:TextureAsset Persistence:PersistentWeapon",
            "CollectibleWeaponData s - Sound:WaveAsset Weapons[]:CollectibleWeapon",
            "ColorTintData s - Contrast:Vec3 Brightness:Vec3 Saturation:Vec3 Hue",
            "ColorTintScopeFilterData c ScopeFilterData ColorTintData:ColorTintData",
            "ColorTransformData s - Brightness:Vec3 BrightnessModifier:Vec3 BrightnessFrequency:Vec3 Contrast:Vec3 ContrastModifier:Vec3 ContrastFrequency:Vec3 Saturation:Vec3 SaturationModifier:Vec3 SaturationFrequency:Vec3 Hue",
            "ColorUnlockPartCollection c Asset ShaderNodeName ColorUnlockParts[]:ColorUnlockPartData",
            "ColorUnlockPartData c UnlockPartData Color:Vec3",
            "CombatActionTriggerEntityData c TriggerEntityData Geometry:GeometryEntityData TimeToTriggerOnNoAction",
            "CombatAreaCamera s - Data:MinimapData",
            "CombatAreaGridNodeEntityData c GameEntityData ForwardNodes[]:CombatAreaGridNodeEntityData BackwardNodes[]:CombatAreaGridNodeEntityData CombatArea:CombatAreaTriggerEntityData CampaignMapDatas[]:CampaignMapEntityData InitialOwnerTeam:TeamId",
            "CombatAreaTriggerEntityData c GameEntityData Geometry[]:GeometryEntityData Height SurroundingGeometry[]:GeometryEntityData TimeToReturn DeserterProjectile:BulletEntityData SurroundingHeight AllowedVehicles[]:VehicleAsset SpawnHeight MaxRandomSpawnHeight StrikeRadius InitalSpeed IsAiAllowedOutside Camera:CombatAreaCamera",
            "CombatConstantData c - PoseTransitionFireDelay ExplosiveObjectSearchRadius ExplosiveKnockdownDistance ExplosiveBlastMultiplier MeleeTargetAngle MoveOutOfCoverLineOfSightTest MoveOutOfCoverTargetAngle MoveOutOfCoverWaypointRadius MaxSuppressPositionTime MaxSuppressDistance MaxSuppressorsRatio SuppressMinLOFDistance SuppressMinLOFOffsetDistance InvestigateObeyFrontLineDistance InvestigateCoverSearchWidth InvestigateAroundTargetRadius MoveInCombatDelay NewEnemyDecideMaxDistance AvoidC4Time StandStillMinTime StationaryBailDistance PrecisionTimeout CloseRangeAccuracyDistance AllowCrouchFiringWaterLevel",
            "CombustionEngineConfigData c EngineConfigData",
            "CommanderManagerEntityData c GameEntityData",
            "ComparisonType e - CT_Equals CT_NotEquals CT_Less CT_More CT_LessEqual CT_MoreEqual",
            "ComponentData c LogicEntityData",
            "ComponentUnlockOverrideMode e - CUOMNotOverridden CUOMNeverActive CUOMAlwaysActive",
            "CompositeExpressionData c BaseExpressionData SubExpressions[]:BaseExpressionData",
            "CompositeHavokAsset c Asset Transforms[]:LinearTransform Assets[]:HavokAsset Enums[]",
            "CompositeMeshAsset c MeshAsset SootType:SootType MaterialMaskingMesh:SkinnedMeshAsset Sectorization:MeshSectorizationAsset",
            "CompositeMeshEntityData c EntityData LightMapEnable Mesh:CompositeMeshAsset MeshShaderSet:MeshShaderSetAsset EnableGroundmapLighting",
            "CompositeSkeletonAsset c BaseSkeletonAsset Skeletons[]:SkeletonAsset",
            "CompressorSettings c - Ratio Threshold Attack Release",
            "ConditionGroup c SoundNodePortGroup X:SoundNodePort Y:SoundNodePort True:SoundNodePort Condition:ConditionType",
            "ConditionNode c SoundNode Conditions[]:ConditionGroup",
            "ConditionType e - And Or XOr Equal Less Greater LessOrEqual GreaterOrEqual",
            "ConnectionPair s - From To",
            "ConsoleCommandEntityData c TestEntityData Commands[]",
            "ConstantEvaluatorData c EvaluatorData Scale",
            "ConstructibleConnectionEdge s - ConnectionType:ConstructibleConnectionType EdgeId",
            "ConstructibleConnectionType e - CctTankBarrier CctBarbedWire",
            "ConstructibleControlEntityData c GameEntityData CreationGeometry[]:GeometryEntityData TimeToLoseControl",
            "ConstructibleHexType e - ChtOneHex ChtThreeHex ChtFourHex",
            "ConstructibleModelEntityData c GameEntityData ConstructibleName HexAmountType:ConstructibleHexType ConnectionEdges[]:ConstructibleConnectionEdge BasePersistenceValue FortifiesCapturePoint Unlock:UnlockGraphUnlock ObjectSets[]:ObjectSet AlignWithTerrain BoundingBox:AxisAlignedBox",
            "ConstructiblePreviewData s - PreviewBuildableShaderSet:MeshShaderSetAsset PreviewUnbuildableShaderSet:MeshShaderSetAsset",
            "ConstructibleToolWeaponEffectsData c WeaponEffectsData",
            "ContinueEntityData c GameEntityData WeaponAmmo[]:ContinueWeaponAmmoData VehicleImmortalTimer SoldierImmortalTimer FadeToBlack StayBlack FadeFromBlack LookAtNearestMapMarker",
            "ContinueWeaponAmmoData s - MinMags WeaponSlot",
            "ControllableEntityData c GamePhysicsEntityData UsePrediction LowHealthTreshold AimWarnSoundEffect:SoundAsset MissileWarnSoundEffect:SoundAsset LowHealthWarnSoundEffect:SoundAsset LockingWarnSoundEffect:SoundAsset LockedWarnSoundEffect:SoundAsset SpottedTime LastKnownSpottedTime",
            "CoreLogLevel e - CllNone CllEventRecord CllError CllAssert CllFatalAssert CllValidate CllWarning CllInfo CllOutput CllDebug",
            "CoreSettings s - EnableDbErrorPolicy EnableResolveErrorTracking EnableSparseSerialization EnableDrone EnableTweaker EnableMemoryDump EnableBitstreamTypeVerification EnableBitstreamExtendedVerification EnableInstanceDirectory DiskLayoutEmulation DiskLayoutPathPatterns[] EnableRemoteLog LogMaster StudioBus Host HostUser SessionId LogLevel:CoreLogLevel RandomTimeSeed EnableResourceFileChangeListener FinalBundles",
            "CounterEntityData c LogicEntityData InitialValue OneShot",
            "CoverConstantData c - SlotSize RaysPerSlot RaysPerDetailSearch UseRaycastCache EnableSparseSlots SearchRadius BaseHeight StandHeight HeightIncrement MaxCoverLength SearchDirections SearchDirectionsPerTimeSlice ForcedDirections SearchRange DistanceStep MaxNodesForProgression MovedResetDistance MaxCoversInMemory ForceRegenerateDestroyedCovers CoverSelectionMethod:CoverSelectionMethod ThreatRadius InCoverDistance InCoverDeviation BaseTimeHealthModifier BaseTimeUrgencyModifier IntermediateCoverSlotModifier MinAttackableEnemyDistance MinDistanceToMoveStanding CrouchCoverModifier StandCoverModifier FinishDueToSeeingDelay",
            "CoverData c - BottomLeft:Vec3 TopRight:Vec3 Right:CoverData Left:CoverData Material:CoverType RightEdge:CoverEdge LeftEdge:CoverEdge",
            "CoverEdge e - CENeighbor CESpace CEBlocked",
            "CoverEntityData c EntityData Material:CoverType LeftBlocked RightBlocked",
            "CoverManipulationType e - CMTRemoveCovers CMTBlockEdges",
            "CoverModifierData c EntityData Manipulation:CoverManipulationType",
            "CoverSelectionMethod e - CSMAgainstThreatPosition CSMAgainstAllEnemies CSMAgainstAllEnemiesWithFallback",
            "CoverType e - CTSoft CTMedium CTHard",
            "CreditsCategory s - Sid Headlines[]:CreditsHeadline",
            "CreditsData s - NumRowsPerPage Categories[]:CreditsCategory Images[]:TextureAsset",
            "CreditsEntity s - Title Name",
            "CreditsHeadline s - Sid Entities[]:CreditsEntity",
            "CriteriaAward c - Award:AwardData Count SID",
            "CriteriaData c - Value Measuring:StatEvent ParamX:StatsCategoryData ParamY:StatsCategoryData CriteriaType:CriteriaType CriteriaDescription ScaleFactor Scale ShouldSummarize ShouldHide",
            "CriteriaType e - CriteriaType_IAR_InARound CriteriaType_IAS_InASpawn CriteriaType_GLOBAL_AllTimeTotal CriteriaType_IfNotTrue CriteriaType_IAD_InADeathStreak",
            "Crossfader2Node c SoundNode Ctrl:SoundNodePort CtrlOut1:SoundNodePort CtrlOut2:SoundNodePort",
            "CrossfaderNode c SoundNode Ctrl:SoundNodePort In1:SoundNodePort In2:SoundNodePort Out:SoundNodePort",
            "CrtAnalogLinkHudFxData c HudFxData SegmentHeight Noise:CrtNoiseData ScanLines:CrtScanLinesData Ghosts:CrtGhostsData ColorTint:ColorTintData RollingBar:CrtRollingBarData InterpolatedBrightness:CrtInterpolatedBrightnessData",
            "CrtGhostData s - Strength Displacement:Vec2 SubValue",
            "CrtGhostsData s - Ghost0:CrtGhostData Ghost1:CrtGhostData Ghost2:CrtGhostData",
            "CrtHudFxData c HudFxData SegmentHeight Noise:CrtNoiseData ScanLines:CrtScanLinesData ColorTint:ColorTintData RollingBar:CrtRollingBarData InterpolatedBrightness:CrtInterpolatedBrightnessData",
            "CrtInterpolatedBrightnessData s - BaseStrength StrengthDeviation StrengthRecursiveScale",
            "CrtNoiseData s - StrengthBase StrengthDeviation TimeToLiveBase TimeToLiveDeviation HeightBase HeightDeviation NumBars OnScreenRatio MinStrength MaxStrength Texture:TextureAsset TextureSTScale:Vec2 TextureFilter",
            "CrtRollingBarData s - Velocity WrapPos Strength Texture:TextureAsset TextureSTScale:Vec2",
            "CrtScanLinesData s - Strength Texture:TextureAsset TextureSTScale:Vec2 TextureSTOffset:Vec2 TextureFilter",
            "Curve2D c - Curve[]:Vec2",
            "CustomColorSet c - EyeColors[]:CustomEyeColor SkinColors[]:CustomSkinColor",
            "CustomEyeColor c - Color:Vec3",
            "CustomHead s - Mesh:SkinnedMeshAsset ColorSets[]:CustomColorSet",
            "CustomSkinColor c - Color:Vec3",
            "CustomizationOptions c Asset Heads[]:CustomHead ColorSets[]:CustomColorSet EyeColors[]:CustomEyeColor SkinColors[]:CustomSkinColor SoldierTextureUnlockPartCollections[]:TextureUnlockPartCollection SoldierColorUnlockPartCollections[]:ColorUnlockPartCollection",
            "CutsceneCameraData c AnimatedCameraData DofBone TotalLength CameraLeaps[]:CameraLeapData",
            "CutsceneEntityData c GameEntityData CutsceneCamera:CutsceneCameraData CameraPeekAroundMaxYaw CameraPeekAroundMaxPitch Sound:WaveAsset LocalizedSound[]:WaveAsset SoundTemplate Sequence:SequenceData Radius TotalLength Subtitle:SubtitleDataTree SyncTimingWithSound IsSkippable MuteGameSound KeepGameSoundMuted VehicleSpawnPoint:VehicleSpawnEntityData EntitySpace:LogicEntityData LoadOnLevelStart FadeInAfterExit CutsceneMenuId:MenuIdEnum AfterCutsceneMenuId:MenuIdEnum",
            "CutsceneObjectEntityBaseData c GameEntityData AnimTree:AnimTreeData EntitySpace:LogicEntityData TotalLength",
            "CutsceneObjectEntityData c CutsceneObjectEntityBaseData Model:StaticModelEntityData RemoveModelWhenAnimationEnds VehicleSpawnPoint:VehicleSpawnEntityData CutsceneSoldier:CutsceneSoldierEntityData HandType:CutsceneObjectHandType UseObjectHandIKBone",
            "CutsceneObjectHandType e - COHTNone COHTRightHand COHTLeftHand",
            "CutsceneSoldierEntityData c CutsceneObjectEntityBaseData Weapon:SoldierWeaponAsset SpawnPoint:SoldierSpawnBaseEntityData VehicleSpawnPoint:VehicleSpawnEntityData SoldierPositionAfterCutscene:StaticPositionEntityData VehiclePositionToEnter AnimationCullingEnabled",
            "CutsceneVehicleEntityData c CutsceneObjectEntityBaseData PartBones[] Skeleton:SkeletonAsset SpawnPoint:VehicleSpawnEntityData RootBone",
            "D3DFORMAT e - D3DFMT_UNKNOWN",
            "D3DSAMPLERSTATETYPE e - D3DSAMP_ADDRESSU D3DSAMP_ADDRESSV D3DSAMP_ADDRESSW D3DSAMP_BORDERCOLOR D3DSAMP_MAGFILTER D3DSAMP_MINFILTER D3DSAMP_MIPFILTER D3DSAMP_MIPMAPLODBIAS D3DSAMP_MAXMIPLEVEL D3DSAMP_MAXANISOTROPY D3DSAMP_MAGFILTERZ D3DSAMP_MINFILTERZ D3DSAMP_SEPARATEZFILTERENABLE D3DSAMP_MINMIPLEVEL D3DSAMP_TRILINEARTHRESHOLD D3DSAMP_ANISOTROPYBIAS D3DSAMP_HGRADIENTEXPBIAS D3DSAMP_VGRADIENTEXPBIAS D3DSAMP_WHITEBORDERCOLORW D3DSAMP_POINTBORDERENABLE",
            "DeMuxNode c SoundNode Outputs[]:DeMuxOutput Trigger:SoundNodePort Value:SoundNodePort WrapValue",
            "DeMuxOutput c SoundNodePortGroup Trigger:SoundNodePort",
            "DeathAreaTriggerEntityData c GameEntityData Geometry[]:GeometryEntityData Damage TimeToReturn",
            "DebugConstantData c - BotDebugDistance PathdataViewDistance InViewModeFOV InaccuracyEnabled MinDecisionTime",
            "DebugEventInput c SoundNodePortGroup E:SoundNodePort Name",
            "DebugRenderType e - Text Plot Bar",
            "DebugTestEntityData c GameEntityData",
            "DebugTextEntityData c TestEntityData DebugText TextColor:Vec3 Centered",
            "DebugValueInput c SoundNodePortGroup V:SoundNodePort Name RenderType:DebugRenderType Min Max",
            "DecalSettings c - Enable DrawEnable UseVisualMeshes DumpDebugInfo NormalOffset Bias ClipAngle ProximityDistance AlphaFadeAt DecalSetRingBufferSize ActiveDecalsRingBufferSize PendingDecalsRingBufferSize PendingImpactsRingBufferSize DrawDebugMemUsage DrawDebugTransforms",
            "DecalTemplateAsset c Asset",
            "DecalTemplateData c DecalTemplateAsset Size RandomSize Rotation RandomRotation TimeToLive ShaderAssets[]:ShaderAsset IsStatic",
            "DelayEntityData c LogicEntityData Delay",
            "DelayNode c SoundNode DelayTime:SoundNodePort Feedback:SoundNodePort In:SoundNodePort Out:SoundNodePort",
            "DelayTriggerEntityData c TriggerEventEntityData AutoTriggerEvent",
            "DeltaAnimationAsset c BaseAnimationAsset ReferencePose:BaseAnimationAsset ReferenceFrame",
            "DeltaGroup c SoundNodePortGroup In:SoundNodePort Delta:SoundNodePort",
            "DeltaNode c SoundNode Deltas[]:DeltaGroup",
            "DemoSettings c - TimeDemo PauseOnStartup RecordDemoFileName PlaybackDemoFileName",
            "DetailedTreeBangerEntityData c TreeBangerEntityData",
            "DetonatedWeaponEffectsData c WeaponEffectsData LockingController:LockingControllerData Range",
            "DialogLineNode c VoiceOverChildNode TalkTimes[]:TimePair",
            "DirLightEntityData c LightEntityData Color:Vec3 Intensity ContributionName DiffuseEnable SpecularEnable",
            "DisableInterval s - EnableTime1 DisableTime1 DisableTime2 EnableTime2",
            "DisablePropertyCurve s - DisableIntervals[]:DisableInterval PropertyName",
            "DisablePropertyCurve_ManualInput c -",
            "DisablePropertyCurve_ManualInput_EndsAbsoluteTime c DisablePropertyCurve_ManualInput DisableInterval:DisableInterval",
            "DisablePropertyCurve_ManualInput_NormalizedTime c DisablePropertyCurve_ManualInput DisableIntervals[]:DisableInterval",
            "DisablePropertyCurve_ManualInput_OrAnimated c DisablePropertyCurve_ManualInput Curve:DisablePropertyCurve_ManualInput",
            "DisableTeamEntityData c GameEntityData DisableType:DisableTeamType Team:TeamId Geometry:GeometryEntityData",
            "DisableTeamType e - DttDisable DttUnspawn",
            "DistanceAttenuationType e - DatLinear DatLogarithmic",
            "DmzEntityData c GameEntityData TeamId:TeamId Geometry[]:GeometryEntityData",
            "DogTagData c - UIName Rank ImageName SoundName VO:DialogLineNode",
            "DynamicCloudLayerData c Asset UV0WindInfluence UV1WindInfluence UV2WindInfluence UV3WindInfluence LinearMaskMin LinearMaskMax LinearMaskRotation DistanceFadeRatio UV01Offset:Vec4 UV23Offset:Vec4 Transform:LinearTransform SimpleCloudPlane:DynamicCloudPlaneLayerData VolumetricCloudPlane:DynamicCloudVolumeLayerData",
            "DynamicCloudPlaneLayerData s - Ka Ksc GlowSize AmbientColorThin:Vec3 AmbientColorDense:Vec3 LevelLow LevelHigh LevelMid Gradient:Vec4 SimpleLayerTiling:Vec2",
            "DynamicCloudVolumeLayerData s - Ka Kd Kg Ksc Krim GlowSize WrapAmount AmbientColorThin:Vec3 AmbientColorDense:Vec3 LevelLow LevelHigh LevelMid ParallaxScale Gradient:Vec4 MinSamples MaxSamples NormalMipBias NormalWrapBias VolumetricLayerTiling:Vec2",
            "DynamicFogData c Asset Start End Clamp Color:Vec3 ColorSun:Vec3 ColorSunPower Density HeightFalloff HeightOffset Eccentrity",
            "DynamicModelEntityData c DynamicPhysicsEntityData Part:PartComponentData Mesh:MeshAsset MeshShaderSet:MeshShaderSetAsset",
            "DynamicOutdoorLightData c Asset KeyColor:Vec3 TopColor:Vec3 BottomColor:Vec3 KeyColorIntensity KeySpecularScale TopColorIntensity BottomColorIntensity HemisphereLightAngleFactor ReflectionEnvmapScale DiffuseEnvmapScale ShadowScale CloudShadowScale CloudShadowIntensity Direction:Vec3 Transform:LinearTransform",
            "DynamicPhysicsEntityData c GamePhysicsEntityData",
            "DynamicPostProcessData c Asset BloomScale:Vec3 Brightness:Vec3 Contrast:Vec3 Saturation:Vec3 BloomExponent Gamma:Vec3 MiddleGray MinExposure MaxExposure TonemapAdjustTime Hue BlurAdd DebugBlurStep DofBlurFilter:BlurFilter DofBlurFilterDeviation DofFocusDistance DofNearDistanceScale DofFarDistanceScale DofScale DofEnable DepthFactors:Vec4 FadeParams:Vec2",
            "DynamicSkyData c Asset HorizontalCoefficientsR:Vec4 HorizontalCoefficientsG:Vec4 HorizontalCoefficientsB:Vec4 HorizontalGain HaloColor:Vec3 HaloGain HaloAlphaPower HaloAlphaGain HaloBleedGain HaloBleedPower HaloBackReflectionGain Mu:Vec2 Sigma:Vec2 HazeColor:Vec3 HazeFadeMin HazeFadeMax GroundColorReflection:Vec3 GroundFadeMinReflection GroundFadeMaxReflection ZenithHeight EarthRadius HazeHeight Exposure Eccentrity LambdaR LambdaG LambdaB JungeExponent Turbidity Nair Pair",
            "DynamicSunData c Asset Theta Phi Size Color:Vec3 Gain Direction:Vec3",
            "DynamicWeaponPickupEntityData c PickupEntityData WeaponSlots[] KeepAmmoState",
            "EdgeModelBuilderSettings c Asset VolumeMap[]:EdgeModelVolumeMapItemData PartExclusionPatterns[] CornerPartExclusionPatterns[] MinSpacing MaxSpacing EnableEdgeModels",
            "EdgeModelGenerationSettings c - VolumeMap[]:EdgeModelVolumeMapItemData SharedSettings:EdgeModelBuilderSettings OrientationVolumes:EdgeModelVolumeAsset",
            "EdgeModelInstance s - Transform:LinearTransform Normal:Vec3 EdgeModelIndex InstanceSetIndex",
            "EdgeModelInstances c - Meshes[]:EdgeModelMeshData MultiLinks[]:EdgeModelMultiLinkData Links[]:EdgeModelLinkData",
            "EdgeModelLinkData s - Instances[]:EdgeModelInstance Link:PartLinkData",
            "EdgeModelMeshData c Asset Mesh:RigidMeshAsset",
            "EdgeModelMultiLinkData s - Instances[]:EdgeModelInstance PartComponentIndices[]",
            "EdgeModelPlacementSettings s - HorizontalAngles[]:FloatRange VerticalAngles[]:FloatRange InwardOffsets[]:FloatRange PlacementType:EdgeModelType Probability",
            "EdgeModelType e - EmtGeneral EmtCorner",
            "EdgeModelVolumeAsset c Asset SourceFile RootNode",
            "EdgeModelVolumeExportResult c - IncludeGroup:EdgeModelVolumeGroup ExcludeGroup:EdgeModelVolumeGroup StripCutterGroup:EdgeModelVolumeGroup",
            "EdgeModelVolumeGroup s - OutputFileName Groups[]",
            "EdgeModelVolumeMapItemData s - Meshes[]:EdgeModelMeshData VolumeGroupName",
            "Editable c Asset",
            "EffectAsset c ObjectBlueprint",
            "EffectComponentData c ComponentData Effect:EffectAsset StartTrigger:EffectComponentTrigger StopTrigger:EffectComponentTrigger",
            "EffectComponentTrigger e - ectInit ectSpawn ectUnSpawn ectPlayerEnter ectPlayerExit ectCameraChange ectCameraChange1P ectCameraChange3P ectCriticallyDamaged",
            "EffectEntityData c EntityData MaxInstanceCount CullDistance ResetInstanceWhenStarted",
            "EffectId e - EiReceive EiSend EiOutput EiMonophonicSampler EiFourChannelSampler EiSampler EiFollowEnvelope EiOscillator EiLfo EiMixer EiCrossfader EiCrossfader2 EiMultiCrossfader EiSimpleTransform EiLinearTransform EiNumberGenerator EiScaleClamp EiPhysics EiLowPassIir2 EiHighPassIir2 EiDelay EiUserMusicArbiter EiDeMux EiSequenceGenerator EiAdsr EiMinMax EiCondition EiDelta EiParameterFilterLp EiParameterFilterLinear EiCar EiSimpleCar EiTank EiHelicopter EiJetPlane EiValueDebug EiEventDebug EiJoypad EiNone EffectCount",
            "EloParameters s - KWinner[]:FunctionPoint KLoser[]:FunctionPoint KNewbie[]:FunctionPoint KCompetitor[]:FunctionPoint Expected[]:ExpectedFunctionPoint",
            "EmittableField e - EfSpawnAnimationSpeed EfSpawnAnimationFrameIndex EfVelocity EfParameters EfNormTime EfRotation EfSpeed EfUserDefined EfConstantFloat EfConstantVec EfNone",
            "EmittableType e - Point Quad ScreenAlignedQuad DirectionAlignedQuad Trail ParticleMesh EmittableTypeCount",
            "EmitterAsset c Editable",
            "EmitterComponentConnection c - Source:EmitterComponentData Dest:EmitterComponentData",
            "EmitterComponentData c - Pre:EmitterComponentData",
            "EmitterConstants e - EmitterMaxCellCount1D",
            "EmitterData c ProcessorData Emitter:EmitterDocument Mesh:RigidMeshAsset",
            "EmitterDocument c EmitterAsset EmitterComponents[]:EmitterComponentData RootProcessor:ProcessorData TemplateData:EmitterTemplateData",
            "EmitterEntityData c EffectEntityData MoveEmitterInSpiral ShootEmitterFromCamera SpawnCompositeMeshPart CompositeMeshPartIndex StartStop Pause Emitter:EmitterAsset",
            "EmitterHavokInfo s - HavokAsset:HavokAsset ReserveCount",
            "EmitterParameter e - EmitterParameterNone EmitterParameter1 EmitterParameter2 EmitterParameter3 EmitterParameter4",
            "EmitterProcessorGroup e - SpawnProcessor LifetimeProcessor LifetimePostProcessor VisualProcessor",
            "EmitterShaderAsset c Asset Filename",
            "EmitterSystemAsset c Asset HavokMeshes[]:EmitterHavokInfo HavokMeshCount",
            "EmitterSystemMetrics s - HavokParticleCount HavokParticlePartCount",
            "EmitterSystemSettings c - Enable TimeScale GlobalResetStartTimeInterval EnableFixedTimeStep EnableFixedDelta EnableJobs EnableRendering DebugOverdrawCount DrawStats DrawBoundingBoxes MinScreenArea QuadEnableRendering QuadNiceRenderingEnable QuadSimpleRenderingEnable QuadEnableOpaque QuadEnableOverdrawRendering QuadColorShaderCostsEnable QuadEnableSorting QuadEnableWireframe QuadEnableProjectionScaleCulling QuadPs3CullingFactorMultiplier QuadEnableZOcclusion QuadZOcclusionBias QuadHalfResEnable QuadHalfResSlice1ThresholdLow QuadHalfResSlice1ThresholdHigh QuadHalfResSlice2ThresholdLow QuadHalfResSlice2ThresholdHigh QuadGroupsJoinAll QuadGroupsJoinNone QuadGroupsJoinNiceAndSimple QuadTechnique QuadVertexShadowsEnable QuadKeptParticlesAlphaBoost QuadProjectionScaleRampStart QuadProjectionScaleRampEnd MeshRenderingEnable MeshHavokRenderingEnable MeshDrawTransforms MeshDrawBoundingBoxes MeshShadowEnable MeshViewCullingEnable MeshCullingDistance MeshDrawCountLimit MeshStreamingPriorityMultiplier MeshDrawCullStats",
            "EmitterTemplateData c - RootProcessor:ProcessorData MaxCount Name Lifetime LifetimeFrameCount FollowSpawnSource KillParticlesWithEmitter VisibleAfterDistance EmittableType:EmittableType Mesh:MeshAsset CompositeMeshPartNames[] Physics:HavokAsset PhysicsData:PhysicsEntityData Emissive Opaque VertexPixelLightingBlendFactor GlobalLocalNormalBlendFactor AmbientLightBlendFactor AlphaAsColorBlendFactor SoftParticlesFadeDistanceMultiplier ForceNiceSorting ActAsPointLight PointLightColor:Vec3 PointLightRadius PointLightPivot:Vec3 PointLightRandomIntensityMin PointLightRandomIntensityMax PointLightIntensity:Vec4 PointLightMinClamp PointLightMaxClamp ParticleCullingFactor ClipScaleAlphaThreshold MaxSpawnDistance MinScreenArea OnCollisionEnable OnCollisionSpeedThreshold KillParticleOnCollision ZOcclusionLookup[]",
            "EmitterTextureAsset c Asset EmitterAssets[]:EmitterAsset Metrics:EmitterSystemMetrics ScenePath",
            "EmitterTextureInfo s - AnimationFrameCount AnimationFrameColumnCount TextureOriginU TextureOriginV AnimationStepU AnimationStepV NmTextureOriginU NmTextureOriginV NmAnimationStepU NmAnimationStepV EnableAnimation",
            "EndOfRoundSoldierSpawnEntityData c GameEntityData Rank Team:TeamId",
            "EndPointData s - Pos EndDamping SpringLength SpringAcceleration SpringDamping",
            "EngineComponentData c ComponentData Config:EngineConfigData SoundEffect:SoundAsset SimpleEngineSoundEffect:SoundAsset SurfaceSoundEffect:SoundAsset AudioMaterials:AudioMaterialDefinitions",
            "EngineConfigData c - Position:Vec3 RpmCurvePoints[] TorqueCurvePoints[] RpmMin RpmMax RpmCut EnginePowerMultiplier InternalAccFactor InternalDecFactor BoostStrengthModifier BoostDuration BoostRecovery NeverSlowDown",
            "EnterVehicleRestriction e - EVRNoRestriction EVRAiOnly EVRSeatSpecific",
            "Entitlement s - License Id Product Tag",
            "EntitlementAsset c - Queries[]:EntitlementQuery",
            "EntitlementQuery c - License CountOnly Id Tag Global GroupName ProductCatalog ProductId Entitlements[]:Entitlement",
            "EntityBusData c Editable Objects[]:GameObjectData EventConnections[]:EventConnection",
            "EntityData c GameObjectData Transform:LinearTransform",
            "EntryClass e - ecPrimary ecSecondary",
            "EntryComponentData c ComponentData EntryClass:EntryClass AIData:ObjectData InputActionMaps:EntryInputActionMapsData InputCurves[]:InputCurveData HudData:EntryComponentHudData VOTypes[]:VOVehicle EntryOrderNumber EnterImpulse EntryRadius IsAllowedToExitInAir IsAllowedToEnterWillReloadingWeapon IsShielded LockSoldierAimingToEntry ShowSoldierInEntry ShowSoldierWeaponInEntry ShowSoldierGearInEntry PoseConstraints:PoseConstraintsData SoldierOffset:Vec3 AnimationOffset:Vec3",
            "EntryComponentHudData s - Index Frustum Visible VehicleTrackerData:HudVehicleTrackerData",
            "EntryEnterRestriction e - eerNoRestriction eerOnlyPrimaryEntry",
            "EntryInputActionEnum e - EIAThrottle EIAStrafe EIABrake EIAHandBrake EIAYaw EIAPitch EIARoll EIAFire EIAFireCountermeasure EIAAltFire EIACycleRadioChannel EIAZoom EIAJump EIAChangeVehicle EIAChangeEntry EIAChangePose EIAInteract EIAToggleParachute EIACyclePrimaryInventory EIACycleSecondaryInventory EIASelectMeleeWeapon EIAReload EIAToggleCamera EIASprint EIAIngameMenu EIAScoreboardMenu EIACarHorn EIAGearUp EIAGearDown EIAGiveOrder EIASelectWeapon1 EIASelectWeapon2 EIASelectWeapon3 EIASelectWeapon4 EIAMapZoom EIASnapView EIASelectAll EIASelect1 EIASelect2 EIASelect3 EIUndefined",
            "EntryInputActionMapData c InputActionMapData",
            "EntryInputActionMapsData c Asset ActionMapSettingsScheme DefaultInputActionMap InputActionMaps[]:EntryInputActionMapData",
            "EnumeratedSwitchNodeData c BaseNodeData Nodes[]:EnumeratedSwitchNodeGroupData Transitions[]:TransitionInfoData NodeSelectorParameter:IntParameter DisableTransitionsParameter:BoolParameter",
            "EnumeratedSwitchNodeGroupData c AnimNodePortGroupData NodeData:BaseNodeData",
            "EnvironmentConstantData c - BangerIncludeDistance",
            "EnvmapLightEntityData c LightEntityData Color:Vec3 Texture:TextureAsset EnvmapType:EnvmapLightType ContributionName",
            "EnvmapLightType e - EltDiffuse EltSpecular EltReflection",
            "EqualizerSettings c - LowShelfFrequency LowShelfGain HighShelfFrequency HighShelfGain HpCutoffFrequency",
            "EulaTexture s - Language:LanguageFormat Image:TextureAsset",
            "EvaluatorData c EmitterComponentData EvaluatorType:EvaluatorType EmitterParameter:EmitterParameter",
            "EvaluatorType e - EtDefault EtBox EtMesh EtPolynomial EtPolynomialOperator EtRandom EtRandomXYZ EtRotateVector EtSampleTexture EtSphere EtConstant EtPolynomialColorInterp EvaluatorTypeCount",
            "EventConnection s - Source:GameObjectData Target:GameObjectData SourceEvent:EventSpec TargetEvent:EventSpec",
            "EventDebugNode c SoundNode Events[]:DebugEventInput",
            "EventGateEntityData c GameEntityData Timer PassThrough",
            "EventIfSwitchEntityData c GameEntityData StartState RunOnce",
            "EventMemoryEntityData c GameEntityData",
            "EventScaleData s - Event:StatEvent Scale",
            "EventSequenceTriggerEntityData c TriggerEventEntityData Random",
            "EventSpec s - Name Id",
            "EventSplitterEntityData c GameEntityData RunOnce",
            "ExpectedFunctionPoint c - XValue YValue",
            "ExplosionEntityData c GameEntityData DetonationEffect:EffectAsset SootDecal Material EmpTime InnerBlastRadius BlastDamage BlastRadius BlastImpulse ShockwaveDamage ShockwaveRadius ShockwaveImpulse ShockwaveTime DisableOcclusion UseAsPreDestuctionEntity IsCausingSuppression",
            "ExplosionImpulseTestEntityData c TestEntityData fireRate explosionStrength Explosion:ExplosionEntityData",
            "ExplosionPackEntityData c MeshProjectileEntityData VehicleDetonationActivationDelay VehicleDetonationRadius HasRemoteDetonator HasVehicleDetonation TimeToLiveOnPlayerDeath IsDestructible ReceivesExplosionDamage Health SpottedTime",
            "ExplosiveObjectData c - Transform:LinearTransform BoxMin:Vec3 BoxMax:Vec3 Enumeration ExplosionRadius ShockwaveRadius",
            "Expression c - Components[]:ExpressionComponent",
            "ExpressionCollectionData c Editable Expressions[]:Expression",
            "ExpressionComponent c - ExpressionType:ExpressionType Operator:ExpressionOperator Coefficients:Vec4 ScaleValue MinClamp MaxClamp",
            "ExpressionOperator e - ExpressionOperatorNone ExpressionOperatorAdd ExpressionOperatorSub ExpressionOperatorMul",
            "ExpressionType e - PolynomialExpression SineExpression CosineExpression",
            "ExtraMoviesConfig s - TutorialMovies[]:UIMovieAsset SPCutScenes[]:UIMovieAsset OtherGames[]:UIMovieAsset Trailers[]:UIMovieAsset",
            "FPSCameraData c TargetCameraData ImpulseSettings:CameraImpulseShakeData BulletImpulseSettings:CameraImpulseShakeData TurnEffectSettings:TurnEffectData BlurEffectSettings:BlurEffectData",
            "FacialSoundMapData s - SoundAsset:Asset AnimIndex",
            "FactionId e - FactionNeutral FactionUS FactionRUS FactionMEC FactionIdCount",
            "FaderType e - FtLinear FtCosine",
            "FakeHingeData c FakePhysicsData Pivot:Vec3 RotationAxis:Vec3 ExtensionAxis:Vec3 MinAngle MaxAngle AngularDampening PullbackAcceleration ProgressiveExponent InertiaModifier",
            "FakePhysicsData c - ChildFakePhysics:FakePhysicsData StartSpeed GravityModifier StartDampening EndDampening MinSpeed MaxSpeed Mass",
            "FakeSpringData c FakePhysicsData Direction:Vec3 Length Acceleration ProgressiveExponent Damping",
            "FallingStateData c CharacterStateData",
            "FenceModelEntityData c StaticModelEntityData Width Angle",
            "FileQueryData c Asset Results[]",
            "FireAtAreaOrderEntityData c OrderEntityData Area[]:GeometryEntityData StopIfPlayerIsSensed",
            "FireEffectData s - Effect:EffectAsset Offset:Vec3 Rotation:Vec3 ZoomOffset:Vec3 UseZoomOffset ZoomRotation:Vec3 UseZoomRotation DisableDuringZoom",
            "FireLogicData s - HoldAndRelease:HoldAndReleaseData BoltAction:BoltActionData TriggerPullWeight RateOfFire ClientFireRateMultiplier ReloadDelay ReloadTime ReloadThreshold PreFireDelay AutomaticDelay MaxRecoilAngleX MinRecoilAngleX MaxRecoilAngleY MinRecoilAngleY MaxRecoilAngleZ MinRecoilAngleZ MaxRecoilFov MinRecoilFov RecoilFollowsDispersion WaitForTriggerRelease ReloadLogic:ReloadLogic ReloadType:ReloadType FireLogicType:FireLogicType",
            "FireLogicType e - fltSingleFire fltSingleFireWithBoltAction fltAutomaticFire fltHoldAndRelease fltDetonatedFiring",
            "FiringDispersionData s - MinAngle MaxAngle IncreasePerShot DecreasePerSecond",
            "FiringFunctionData c - Dispersion[]:FiringDispersionData WeaponDispersion:SoldierWeaponDispersion FireEffects1p[]:FireEffectData FireEffects3p[]:FireEffectData FireSoundEffect:EffectAsset OutOfAmmoSoundEffect:SoundAsset ReflectionSound:SoundAsset Shot:ShotConfigData FireLogic:FireLogicData Ammo:AmmoConfigData OverHeat:OverHeatData UsePrimaryAmmo UnlimitedAmmoForAI LoopingFireSound AmmoCrateReloadDelay SelfHealTimeWhenDeployed IsCounterMeasureWeapon AttackOnWeaponSwitch SwitchToPreviousWeapon AutoFireOnActivetWeapon AutoFireDelay",
            "FirstPersonCameraData c - Offset:Vec3 Rotation:Vec3 WeaponSpringEffect:WeaponLagSpringEffectData CameraSpringEffect:WeaponLagSpringEffectData MoveStrafeModifier MoveForwardModifier RotateYawModifier RotatePitchModifier RotateRollModifier ZoomMoveStrafeModifier ZoomMoveForwardModifier ZoomRotateYawModifier ZoomRotatePitchModifier ZoomRotateRollModifier ReleaseModifier",
            "FlagConstructibleModelEntityData c ConstructibleModelEntityData",
            "FloatNodeGroupData c AnimNodePortGroupData NodeData:BaseNodeData Parameter:FloatParameter Max Min",
            "FloatParameter c BaseParameter InitialValue",
            "FloatParameterExpressionData c BaseExpressionData Parameter:FloatParameter Value Comparison:ComparisonType",
            "FloatPhysicsData c - Density FilledDensity",
            "FloatPropertyTrackData c PropertyTrackData Values[]",
            "FloatRange s - Min Max Probability",
            "FloatSetting s - Name Value",
            "FloatValueGroupData c AnimNodePortGroupData Parameter:FloatParameter Max Min Invert",
            "FogFunctionType e - FftNone FftLinear FftExponential FftExponentialSquared FftAnalytical FftHybrid FftVolumetricHeight",
            "FollowAISoldierOrderEntityData c OrderEntityData Target[]:SoldierSpawnBaseEntityData",
            "FollowClosestHumanOrderEntityData c OrderEntityData",
            "FollowConstantData c - DistanceFromPlayerBeforeTeleport FollowHumanMaxDistance FollowHumanInCombatMaxDistance FollowAiMaxDistance FollowAiInCombatMaxDistance FollowMimicPoseDistance FollowInertia SlowStartDistance SlowStartMinTime SlowStopDistance LookAwayAngle",
            "FollowEnvelopeNode c SoundNode X:SoundNodePort Y:SoundNodePort Base Scale",
            "FootStepTrigger s - Time",
            "ForbiddenAIAreaData c LogicEntityData Geometry:GeometryEntityData",
            "ForceMagnitudeInputType e - FMITYaw FMITPitch FMITRoll FMITThrottle",
            "ForestEntityData c GroupEntityData",
            "FormationData c - Positions[]:Vec3 Name",
            "FormationHolder c Asset Formations[]:FormationData",
            "FourChannelSamplerGroup c SamplerGroup",
            "FourChannelSamplerNode c SamplerNode",
            "FunctionPoint c - XValue YValue",
            "GameAnimationSettings c - RespawnOnResourceReload",
            "GameEffectEntityData c GameEntityData Effect:EffectAsset Soldier:SoldierSpawnBaseEntityData SoldierBoneName Multiplier PlayFromClient",
            "GameEntityData c LogicEntityData",
            "GameInteractionEntityData c InteractionEntityData InteractionEntityType:InteractionEntityType OnInteractionSendDelay DelayBetweenUses TeamId:TeamId SendOnInteractionOnAnimationStart IsHumanAllowed HoldToInteractTime StaticModel:ReferenceObjectData",
            "GameModeObjective s - Attacker Defender",
            "GameModeTypes e - SinglePlayer Multiplayer_Day1 Multiplayer_Day2",
            "GameObjectData c - Components[]:GameObjectData Enumeration Name",
            "GamePhysicsEntityData c GameEntityData PhysicsData:PhysicsEntityData Material",
            "GameRenderSettings c - MultisampleCount Fullscreen ForceVSyncEnable Dx10Enable Dx10DebugInfoEnable Dx9AdapterIndex Dx9RefreshRate XenonRingBufferSize XenonBufferTwoFramesEnable XenonPresentImmediateThreshold Ps3FrameMainBufferSize Ps3FrameLocalBufferSize Ps3LinearFrameCmdBufEnable Ps3CellMemoryTexturesEnable GcmHudEnable PerfOverlayEnable PerfOverlayVisible SkipMipmapCount DrawShaderStats DrawInfo JobEnable JobThreadIndex JobPriority BuildJobSyncEnable DecalsEnable EmittersEnable EntityRenderEnable DebugRendererEnable InitialClearEnable GpuProfilerEnable NearPlane ViewDistance ForceFov RigidMeshBatchingEnable CompositeMeshBatchingEnable EdgeModelsEnable EdgeModelCastShadowsEnable EdgeModelScreenAreaScale EdgeModelDefaultMinScreenArea EdgeModelSpuInstancingEnable EdgeModelUseMainLodEnable EdgeModelForceLod EdgeModelUseLodBox EdgeModelLodScale EdgeModelMaxVisibleInstanceCount EdgeModelCullEnable EdgeModelFrustumCullEnable EdgeModelOcclusionCullEnable EdgeModelAdditionalCullEnable EdgeModelDrawBoxes EdgeModelDrawStats StaticModelPartCullEnable StaticModelPartFrustumCullEnable StaticModelPartOcclusionCullEnable StaticModelPartShadowCullEnable StaticModelDrawBoxes StaticModelDrawStats StaticModelPartOcclusionMaxScreenArea StaticModelCullJobCount StaticModelCullSpuJobEnable LockView ResetLockedView FadeEnable FadeWaitingEnable ForceBlurAmount ForceWorldFadeAmount ForceGlobalFadeAmount ShadowsEnable NiceShadowsEnable ForegroundBlurEnable BlurEnable",
            "GameSettingEntityData c GameEntityData ChangeSetting:ChangeGameSettingType",
            "GameSettings c - GameVersion MaxPlayerCount LogFileEnable VsyncEnable MaxFps ForceDeltaTime ForceDeltaTicks ResourceReloadingEnable FastStartupEnable UseSingleWeaponSelector UseCustomKit UseBundles BundleMountPoints[]:BundleMountPoint Ps3HDDGameDataEnable XenonJobThread0Enable XenonJobThread1Enable XenonJobThread2Enable XenonJobThread3Enable XenonJobThread4Enable XenonJobThread5Enable ReplayDebug UIConstructibleList RetailServers AutoAimEnabled PS3ContentRatingAge NoInteractivityTimeoutTime NoInteractivityThresholdLimit TeamKillCountForKick TeamKillValueForKick TeamKillValueIncrease TeamKillValueDecreasePerSecond RotateLogs LogHistory AdjustVehicleCenterOfMass",
            "GameSettingsAsset c Asset Categories[]:GameSettingsCategory",
            "GameSettingsCategory s - Category:GameSettingsType Floats[]:FloatSetting Ints[]:IntSetting Strings[]:StringSetting Binaries[]:BinarySetting",
            "GameSettingsType e - GstAudio GstRender GstInput GstAI GstGameplay GstPlayerProfile GstPersistence GstBinary GstCount",
            "GameSootSettings c - ExpandAroundPartMin ExpandAroundPartMax ExpandAroundExplosionMin ExpandAroundExplosionMax SootForExplosionsEnable SootForBulletsEnable DrawPartBounds",
            "GameSoundEntityData c GameEntityData Sound:SoundAsset PlayFromClient",
            "GameTestEntityData c TestEntityData TimeoutTime",
            "GameTipAsset c Asset Tips[]:GameTipData",
            "GameTipData s - Text",
            "GarrisonEntityData c GameEntityData CreateSoldierOnSpawn",
            "GearboxConfigData c - ForwardGearRatios[] ForwardGearSpeeds[] ReverseGearRatios[] ReverseGearSpeeds[] GearboxType GearboxMode GearChangeTime GearDownSpeedFactor OppositeDirGearChangeMaxSpeed OppositeDirGearChangeTime ClutchSpeedFactor TransmissionEfficiency UseAutoClutch UseClassicGearBoxAutoClutch UseNeutralGear",
            "GenericHandsIkNodeData c BaseHandsIkNodeData TargetObjectWorldPosition:Mat4Parameter TargetObjectName AnimationPose:AnimationAsset ObjectPose:AnimationAsset PoseFrame",
            "GenericPropertyTrackData c PropertyTrackData Values[]",
            "GeometryEntityData c EntityData",
            "GlobalPostProcessSettings c - DebugMode:PostProcessDebugMode HdrBlurEnable HdrBloomEnable Ldr16BitBloomEnable LdrBloomRange ExposureClampEnable DirectExposureEnable DrawDebugInfo QuarterDownsamplingEnable BlurBlendEnable BloomBlurEnable DownsampleLogAverageEnable DownsampleAverageFromBloomEnable DownsampleAverageStartMipmap DownsampleBeforeBlurEnable PostTonemapBlurEnable Ps3BackBufferAsLdrTargetEnable ForceBlurAdd ForceDofEnable ForceDofFocusDistance ForceDofNearDistanceScale ForceDofFarDistanceScale ForceDofScale ForceDofBlurFilterDeviation ForceBloomScale:Vec3 ForceVignetteScale:Vec2 ForceVignetteExponent ForceVignetteColor:Vec4 FilmGrainEnable FilmGrainTextureScale:Vec2 FilmGrainColorScale:Vec3 FilmGrainLinearFilteringEnable Brightness:Vec3 Contrast:Vec3 Saturation:Vec3 Hue Ps3Brightness:Vec3 Ps3Contrast:Vec3 Ps3Saturation:Vec3 Ps3Hue",
            "GravityData c ProcessorData Gravity",
            "GrenadeEntityData c MeshProjectileEntityData Gravity CollisionSpeedMultiplier MinBounceSpeed GrenadeProximityRadius GrenadeProximityTime",
            "GroundmapLightEntityData c LightEntityData Color:Vec3 Size:Vec2 Offset:Vec2 Texture:TextureAsset SideScale Intensity AttenuationSize BleedSize ContributionName",
            "GroupEntityData c GamePhysicsEntityData MemberDatas[]:GroupMemberData",
            "GroupMemberData c - SpawnDatas[]:GroupMemberInstanceSpawnData MemberType:GamePhysicsEntityData Name FirstPhysicsPart PhysicsPartCount PhysicsPartCountPerInstance InstanceCount",
            "GroupMemberInstanceSpawnData s - Enumeration MeshShaderSet:Asset",
            "GroupNodeData c BaseNodeData Nodes[]:GroupNodeGroupData",
            "GroupNodeGroupData c AnimNodePortGroupData Node:BaseNodeData",
            "GunSwayBaseData s - BaseValue:GunSwayDispersionData Recoil:GunSwayRecoilData GunSwayLag:GunSwayLagData",
            "GunSwayBaseMoveData s - BaseValue:GunSwayDispersionData Moving:GunSwayDispersionData Recoil:GunSwayRecoilData GunSwayLag:GunSwayLagData",
            "GunSwayBaseMoveJumpData s - BaseValue:GunSwayDispersionData Moving:GunSwayDispersionData Jumping:GunSwayDispersionData VaultingSmallObject:GunSwayDispersionData VaultingMediumObject:GunSwayDispersionData Recoil:GunSwayRecoilData GunSwayLag:GunSwayLagData",
            "GunSwayCrouchProneData s - NoZoom:GunSwayBaseMoveData Zoom:GunSwayBaseMoveData",
            "GunSwayData c WeaponSwayData Stand:GunSwayStandData Crouch:GunSwayCrouchProneData Prone:GunSwayCrouchProneData ProneToCrouch:GunSwayStanceTransition ProneToStand:GunSwayStanceTransition CrouchToProne:GunSwayStanceTransition CrouchToStand:GunSwayStanceTransition StandToProne:GunSwayStanceTransition StandToCrouch:GunSwayStanceTransition SuppressionModifier:GunSwayStanceZoomModifierData Modifiers[]:GunSwayModifierData DeviationScaleFactorZoom GameplayDeviationScaleFactorZoom DeviationScaleFactorNoZoom GameplayDeviationScaleFactorNoZoom CameraRecoil:CameraRecoilData",
            "GunSwayDispersionData s - MinAngle MaxAngle IncreasePerShot DecreasePerSecond",
            "GunSwayDispersionModData s - MinAngleModifier MaxAngleModifier IncreasePerShotModifier DecreasePerSecondModifier",
            "GunSwayLagData s - MoveStrafeModifier MoveForwardModifier RotateYawModifier RotatePitchModifier ReleaseModifier",
            "GunSwayModifierData c UnlockPartData StandZoomModifier:GunSwayStanceZoomModifierData StandNoZoomModifier:GunSwayStanceZoomModifierData CrouchZoomModifier:GunSwayStanceZoomModifierData CrouchNoZoomModifier:GunSwayStanceZoomModifierData ProneZoomModifier:GunSwayStanceZoomModifierData ProneNoZoomModifier:GunSwayStanceZoomModifierData OnlyInSupportedShooting",
            "GunSwayRecoilData s - RecoilAmplitudeMax RecoilAmplitudeIncPerShot RecoilAngleMax RecoilAngleIncPerShot RecoilAmplitudeDecreaseFactor",
            "GunSwayStanceTransition s - MaxPenaltyValue:GunSwayDispersionData CoolDown",
            "GunSwayStanceZoomModifierData s - DispersionMod:GunSwayDispersionModData RecoilMagnitudeMod RecoilAngleMod LagYawMod LagPitchMod",
            "GunSwayStandData s - NoZoom:GunSwayBaseMoveJumpData Zoom:GunSwayBaseMoveJumpData",
            "GunrackArmy e - GunrackArmy_US GunrackArmy_MEC GunrackArmy_RU",
            "GunrackCamera s - Offset:Vec3 Distance",
            "GunrackCategory s - Category:GunrackCategoryType Slots[]:GunrackSlot",
            "GunrackCategoryType e - GunrackCategoryType_AssaultRifle GunrackCategoryType_SubMachineGun GunrackCategoryType_LightMachineGun GunrackCategoryType_SniperRifle GunrackCategoryType_Shotgun GunrackCategoryType_RocketLauncher GunrackCategoryType_Items GunrackGategoryType_Count",
            "GunrackConfig s - Categories[]:GunrackCategory CameraInterpolationSpeed SmallCamera:GunrackCamera BigCamera:GunrackCamera",
            "GunrackSlot s - WeaponName WeaponDescription WeaponTexture:TextureAsset RetailerLogo:TextureAsset Army:GunrackArmy Kit:KitCategory Accuracy RateOfFire ClipSize Damage Handling Persistence:PersistentWeapon Scene:SceneData CameraOffset:Vec3 CameraOffsetZoomedIn:Vec3 CollectibleDescription",
            "HaloAimerData c - InputPolynomial[] ZoomedInputPolynomial[] SuckDistanceFallOff[] AccelerationInputThreshold AccelerationMultiplier AccelerationDamping YawSpeed PitchSpeed SuckSoftZone SuckUserInputMultiplier SuckOwnSpeedInfluence SuckTargetSpeedInfluence SuckOwnRequiredMovementForMaximumSuck SuckStartInputThreshold SuckZoomingMultiplier SuckZoomingPostTime SuckYaw SuckPitch UseYawAcceleration UsePitchAcceleration",
            "HandIkInfo s - LocalPosition:LinearTransform HandIndex",
            "HandInfoData s - HandBoneName HandIkProperty:AnimatedPropertyData HandInfluenceMap:InfluenceMapData BoneIndices[]",
            "HavokAsset c Asset MaterialMappings[] PhysicsMaterialSets[]:PhysicsMaterialSetAsset PartMaterialDatas[]:PartMaterialData FaceMaterials[] ParticleAllotment IsStatic",
            "HdrSetting c - RmsReleaseTime RmsAttackTime RmsSmoothFactor WindowMinTop WindowMinBottom WindowReleaseTime WindowAttackTime WindowSize CompressFactor Headroom",
            "HealerData s - Radius Health HealingTime PickUpDelay",
            "HealthStateData c - Effects[]:EffectAsset Explosion:ExplosionEntityData LoosePartPhysicsArray[]:NetworkableLoosePartPhysicsData SpawnedBanger:DynamicPhysicsEntityData LoosePartPhysics:NetworkableLoosePartPhysicsData MeshName PreferedShapeType:ShapeType Health",
            "HeightConversionMethod e - HcmAlphaChannel HcmAverageRGB HcmBiasedRGB HcmRed HcmGreen HcmBlue HcmMaxRGB HcmColorSpace HcmNormalize HcmNormalMapToHeightMap",
            "HeightfieldProxy c -",
            "HelicopterControlConstantData c - HeightToSpeedScale MinSpeedForHeightSpeedRestriction HeightSpeedDamper HeightSpeedToThrottleScale HeightSpeedToBrakeScale HeightTiltInfluence MaxThrottle MaxBrake AlwaysAttainHeightDistance MovementToVelocityScale ForwardsSpeedDamper SidewaysSpeedDamper ForwardsSpeedToPitchScale SidewaysSpeedToRollScale MaxMovementPitch MaxAimPitch PitchToAngularSpeedScale PitchAngularSpeedDamper PitchAngularSpeedToPitchInputScale MaxPitchInput PitchAimOverride MaxRoll RollToAngularSpeedScale RollAngularSpeedDamper RollAngularSpeedToRollInputScale MaxRollInput YawDifferenceToAngularSpeedScale YawAngularSpeedDamper YawAngularSpeedToYawInputScale MaxYawInput AttackDistance AttackSideSearchDistance MaxAttackHeightDiff MinAttackDistance PostAttackClearDistance AttackHeight CombatMovementRadius TargetProximity TargetTooClose AttackHoverTime AttackPilotHeight PilotHeightSpan AttackPilotMaxSpeed PilotMoveStep TurnGunnerCheckAngle EvasiveMiniumSpeed EvasiveEstimatedImpactTime EvasiveDeflectionAngle EvasiveAltitude EvasiveWaypointRadius",
            "HelicopterNode c SoundNode Start:WaveAsset Stop:WaveAsset Engine:WaveAsset RotorClose:WaveAsset RotorDistant:WaveAsset HfNoise:WaveAsset Turbine:WaveAsset Interior:WaveAsset StartOut:SoundNodePort StopOut:SoundNodePort EngineOut:SoundNodePort RotorCloseOut:SoundNodePort RotorDistantOut:SoundNodePort HfNoiseOut:SoundNodePort TurbineOut:SoundNodePort InteriorOut:SoundNodePort Velocity:SoundNodePort InDriverPos:SoundNodePort InteriorCam:SoundNodePort Tilt:SoundNodePort Distance:SoundNodePort ReleaseEvent:SoundNodePort Rotor_Close_Distant_Fade:Vec2 Engine_Velocity_Pitch:Vec4 Engine_Tilt_Pitch:Vec4 Rotor_Velocity_Pitch:Vec4 Rotor_Velocity_Amp:Vec4 Turbine_Velocity_Pitch:Vec4 Turbine_Velocity_Amp:Vec4 HfNoise_Velocity_Pitch:Vec4",
            "HidableSoldierMeshWeaponPart s - WeaponMesh:SkinnedMeshAsset BoneName HideAlways HideInVehicleEntries",
            "HighPassIir2Node c SoundNode Frequency:SoundNodePort In:SoundNodePort Out:SoundNodePort",
            "HoldAndReleaseData s - MaxHoldTime MinPowerModifier MaxPowerModifier PowerIncreasePerSecond Delay",
            "HotItemAsset c ObjectBlueprint",
            "HotZoneEntityData c GameEntityData UpdateDelay Geometry[]:GeometryEntityData",
            "HovercraftFloatPhysicsData c HullFloatPhysicsData LandResistanceAxisMod:Vec3 LandFrictionAxisMod:Vec3 FrontLength SideLength",
            "HudConfigAsset c Asset OutOfAreaVO:DialogLineNode AntiSpam:AntiSpamConfig Objective:GameModeObjective Vignettes[]:VignetteData AmmoPickupSound:WaveAsset WeaponPickupSound:WaveAsset NewObjectiveSound:WaveAsset ObjectiveCompletedSound:WaveAsset ObjectiveUpdatedSound:WaveAsset MPVictorySoundMec:WaveAsset MPVictorySoundUs:WaveAsset MPVictorySoundRu:WaveAsset MPDefeatSoundMec:WaveAsset MPDefeatSoundUs:WaveAsset MPDefeatSoundRu:WaveAsset ArtilleryAiming:WaveAsset ArtilleryAimingOOA:WaveAsset MortarStrikeLocking:WaveAsset LaserDesignatorLocked:WaveAsset LaserDesignatorLocking:WaveAsset ShowSpawnPointsOnMinimap Collectibles:CollectibleWeaponData SpawnScreenWeapons[]:SpawnScreenWeaponData",
            "HudData s - CrosshairScaleMin CrosshairScaleMax CrosshairOpacityMin CrosshairOpacityMax CrosshairOpacityModifier CrosshairTypeId WeaponClass HintAction1:EntryInputActionEnum HintAction2:EntryInputActionEnum HintLine1 HintLine2 HintDuration InfiniteAmmo HideCrosshairWhenAimOnFriend",
            "HudEntityData c GameEntityData",
            "HudFxData c - ScissorRegion:Vec4",
            "HudVehicleTrackerData c - EnemyTrackerIcon:UIHudIcon OutsideScreenTrackerIcon:UIHudIcon EnemyBehindTrackerIcon:UIHudIcon EnableEdgePointers ShowTracker TrackSpecificVehicles VehicleTypes[]:VehicleAsset MaxDistance IconSize",
            "HullFloatPhysicsData c FloatPhysicsData WaterResistanceAxisMod:Vec3 WaterFrictionAxisMod:Vec3 Offset:Vec3 SubSurfaceSplits Depth Width Length FrontCurveDegree SideCurveDegree NonEngineSteer WaterDampeningMod LiftModifier SupportSizeMod AngularDampening FrictionThrottleModifier",
            "HumanPlayerEntityData c GameEntityData PlayerKilledDelay LastManStandingSid DisplayTime",
            "IfNodeData c BaseNodeData BoolValues[]:BoolValueGroupData FloatValues[]:FloatValueGroupData IntValues[]:IntValueGroupData ThenNode:BaseNodeData ElseNode:BaseNodeData Expression:BaseExpressionData",
            "InAirStateData c CharacterStateData FreeFallVelocity",
            "IndexBufferFormat e - Ibf16Bit Ibf32Bit",
            "IndexedAnimationData c - AnimTree:AnimTreeDataAsset IndexParameter StartParameter StopParameter ExternalAnimParameter AddOnDemand BlendOutAnimTree",
            "InfluenceMapBoneData c - BoneName Weight",
            "InfluenceMapData c BaseInfluenceMapData InfluenceMapEffects[]:InfluenceMapEffectData Dynamic",
            "InfluenceMapEffectData c - InfluenceMap:InfluenceMapData EffectToInfluenceMap:InfluenceMapData",
            "InputActionData c - ActionName",
            "InputActionMapData c - Actions[]:InputActionsData PlatformSpecific:InputActionMapPlatform Slot:InputActionMapSlot",
            "InputActionMapPlatform e - IAMPWin32 IAMPXenon IAMPPs3 IAMPAllPlatforms",
            "InputActionMapSlot e - IAMESlot1 IAMESlot2 IAMESlot3 IAMESlot4 IAMESlot5 IAMESlot6 IAMESlot7 IAMESlot8 IAMESlotCount",
            "InputActionsData c - InputActions[]:InputActionData",
            "InputCurveData c - AffectedInputs[]:EntryInputActionEnum InputModifierCurve[]:Vec2 HandleMultipleInputsAsSquare",
            "InputRestrictionEntityData c GameEntityData OverridePreviousInputRestriction Throttle Strafe Brake HandBrake CarHorn Yaw Pitch Roll Fire FireCountermeasure AltFire CycleRadioChannel SelectMeleeWeapon Zoom Jump ChangeVehicle ChangeEntry ChangePose ToggleParachute CyclePrimaryInventory CycleSecondaryInventory Reload ToggleCamera Sprint IngameMenu ScoreboardMenu MapZoom GearUp GearDown GiveOrder SnapView SelectAll Select1 Select2 Select3",
            "InputSuppressionData s - SuppressVehicleInput[]:ActionSuppressor",
            "IntNodeGroupData c AnimNodePortGroupData NodeData:BaseNodeData Parameter:IntParameter Max Min",
            "IntParameter c BaseParameter InitialValue",
            "IntParameterExpressionData c BaseExpressionData Parameter:IntParameter Value Comparison:ComparisonType",
            "IntPropertyTrackData c PropertyTrackData Values[]",
            "IntSetting s - Name Value",
            "IntValueGroupData c AnimNodePortGroupData Parameter:IntParameter Max Min Invert",
            "InteractableStaticModelEntityData c StaticModelEntityData",
            "InteractionEntityData c GameEntityData SoldierAnimTree:AnimTreeDataAsset UseWithinRadius UseWithinAngle TestIfOccluded MaxUses PreInteractionSoundEffect:SoundAsset",
            "InteractionEntityType e - IEDefault IEGold IEArmSDK IEDefuseSDK",
            "InterludeOrderEntityData c OrderEntityData IsCutscene Crouch",
            "InterpolationType e - None Linear",
            "IntroMovieData s - Movies[]:UIMovieAsset",
            "IrAsset c WaveAsset",
            "IrradianceVolumeSettings c - Enable Debug ScaleSoldier ScaleVehicle ScaleDefault MinVal Fade",
            "JetEngineConfigData c EngineConfigData DirectionVectorIndex ForceMagnitudeMultiplier AngleInputYMultiplier AngleInputPitchMultiplier MaxVelocity IsTurnable IsWaterJetEngine PowerFadeOutRange:Vec2",
            "JetPlaneNode c SoundNode Ext_Startup:WaveAsset Ext_Shutdown:WaveAsset Ext_Idle:WaveAsset Ext_Front_Turbine:WaveAsset Ext_Rear_Exhaust:WaveAsset Ext_Fly_Near:WaveAsset Ext_Fly_Far:WaveAsset Ext_Afterburner:WaveAsset Int_Startup:WaveAsset Int_Idle:WaveAsset Int_Cockpit:WaveAsset Int_Full_Throttle:WaveAsset Int_Afterburner:WaveAsset Int_Dive_Turbine:WaveAsset Int_Turbine_Attack:WaveAsset Ext_Startup_Out:SoundNodePort Ext_Shutdown_Out:SoundNodePort Ext_Idle_Out:SoundNodePort Ext_Front_Turbine_Out:SoundNodePort Ext_Rear_Exhaust_Out:SoundNodePort Ext_Fly_Near_Out:SoundNodePort Ext_Fly_Far_Out:SoundNodePort Ext_Afterburner_Out:SoundNodePort Int_Startup_Out:SoundNodePort Int_Idle_Out:SoundNodePort Int_Cockpit_Out:SoundNodePort Int_Full_Throttle_Out:SoundNodePort Int_Afterburner_Out:SoundNodePort Int_Dive_Turbine_Out:SoundNodePort Int_Turbine_Attack_Out:SoundNodePort Velocity:SoundNodePort InDriverPos:SoundNodePort InteriorCam:SoundNodePort Tilt:SoundNodePort Distance:SoundNodePort Throttle:SoundNodePort GroundContact:SoundNodePort ReleaseEvent:SoundNodePort Fly_Near_Far_Fade:Vec2 Fly_Idle_Throttle_Fade:Vec2 Fly_Idle_Velocity_Fade:Vec2 Dive_Tilt_Amp:Vec4 ThrottleChangeSpeed",
            "JoinSquadOrderEntityData c OrderEntityData SquadToJoin:SquadBaseEntityData",
            "JoypadNode c SoundNode LeftAnalogX:SoundNodePort LeftAnalogY:SoundNodePort RightAnalogX:SoundNodePort RightAnalogY:SoundNodePort AButton:SoundNodePort BButton:SoundNodePort",
            "JumpStateData c CharacterStateData JumpHeight JumpEffectSize",
            "KillAllEntityData c GameEntityData KillAllHumanSoldiers",
            "KitCategory e - KCAssault KCDemolition KCRecon KCSpecialist KCSupport KCRecruit KCRifleman KCAntitank KCMachinegunner KCSpecialforces KCSniper KCFieldworks KCCombatmedic KCUnknown KCUCount",
            "KitPickupEntityData c PickupEntityData Kit:SoldierAsset KeepAdditionalWeapons KeepAmmoState",
            "KynapseConstantData c - KynapseLicenseServerName MaxEntityCount",
            "LadderEntityData c StaticModelEntityData",
            "LandingFlapData c - ActivationHeight HeightTolerance ActivationVelocity VelocityTolerance",
            "LandingGearEntryComponentData c PlayerEntryComponentData LandingGearRetractionData:LandingGearRetractionData",
            "LandingGearRetractionData s - LandingGearInput:EntryInputActionEnum EnablePitchCondition PitchLimit EnableRollCondition RollLimit EnableHeightCondition HeightLimit EnableSpeedCondition SpeedLimit",
            "LanguageFormat e - LanguageFormat_English LanguageFormat_French LanguageFormat_German LanguageFormat_Spanish LanguageFormat_Italian LanguageFormat_Japanese LanguageFormat_Count",
            "LaserDesignatorEffectsData c LockingWeaponEffectsData PostLockTime BomberTime BombWarnTime BomberSound:SoundAsset",
            "LegInfoData s - LegBone1LowestBones[]:Vec3 LegBone2LowestBones[]:Vec3 LegBone1Name LegBone2Name LegIkProperty:AnimatedPropertyData BoneIndices[]",
            "LerpNodeData c BaseNodeData Node1:BaseNodeData Node2:BaseNodeData LerpParameter:FloatParameter MinValue MaxValue",
            "LevelData c WorldData DynamicObjects[]:GameObjectData Covers[]:CoverData VoiceOvers[]:VOAsset VoiceOverLines[]:DialogLineNode TriggerAnimTree:AnimTreeData TriggerAnimations[]:AnimationAsset WaypointAnimTree:AnimTreeData HasAI HasFlyingAI StartLevelFadedToBlack AerialHeightmapQuality AerialHeightmapCellSize PathfindingTypes[]:PathfindingSettings ExplosiveObjects[]:ExplosiveObjectData ExclusiveStaticCullTree:StaticCullTreeData ClientStaticCullTree:StaticCullTreeData ClientTreeStaticCullTree:StaticCullTreeData SpawnPointManager:SpawnPointManagerData LevelDescription:LevelDescription EmitterSystemMetrics:EmitterSystemMetrics DefaultFOV InfantryFOVMultiplier FriendlyDamageModifier MinimapSettings:MinimapSettings AmbientSounds:AmbientSoundsAsset ReverbSettings:ReverbSettingsAsset SoundStates:SoundStateSettingsAsset MaxVehicleHeight IconFarScaleDist IconTargetMinScale IsTutorial",
            "LevelDescription s - Name Description Tip:GameTipAsset LoadingScreens[]:LoadingScreenData AttackerTeam:TeamId LoadingTime:LoadingTimeData Music:WaveAsset MinimapTexture:TextureAsset AchivementEasyCompleted AchivementEasyDate AchivementMediumCompleted AchivementMediumDate AchivementHardCompleted AchivementHardDate RichPresence:RichPresenceConnection Advertisement:AdvertisementZoneData GoldNuggets[] GatherCollectibles Collectibles[] MinimapCameraOffset:MinimapCameraOffsetData",
            "LevelDescriptionAsset c Asset LevelName Description:LevelDescription",
            "LevelMovie s - LevelName Movie:UIMovieAsset",
            "LevelReferencedAssetsData c - ReferencedAssets[]",
            "LevelSaveData c - SaveSize SaveNames[]",
            "LfoGroup c SoundNodePortGroup Hz:SoundNodePort Amplitude:SoundNodePort Out:SoundNodePort Mode:OscillatorMode Min Max",
            "LfoNode c SoundNode LfoGroups[]:LfoGroup",
            "LifeCounterEntityData c GameEntityData TeamId:TeamId LifeTresholdValues[] UiShowCountLowerThreshold UiShowCountUpperThreshold Attacker SimpleReinforce SetBestSquadSpawner StartingLifeCount BaseCount ReinforceThreshold ReinforceTable[] FallbackTime FightHarderMessageSid FightHarderTreshold ReinforceMessageSid DisplayTime",
            "LightEntityData c EntityData Visible",
            "LightingModelType e - Lmt3Point LmtSH",
            "LimbCount e - LC_MAX_HAND_COUNT LC_MAX_LEG_COUNT",
            "LinearMovingBodyData c MovingBodyData Axis:Vec3 Start:EndPointData End:EndPointData",
            "LinearTransform s - right:Vec3 up:Vec3 forward:Vec3 trans:Vec3",
            "LinearTransformNode c SoundNode X:SoundNodePort K:SoundNodePort M:SoundNodePort Y:SoundNodePort",
            "LiveSessionEntityData c GameEntityData MinimumPlayersForArbitrary",
            "LoadingScreenData s - Text Texture:TextureAsset",
            "LoadingTimeData s - Win32Time XenonTime Ps3Time",
            "LocalForceData c ProcessorData LocalForce:Vec3",
            "LocalizationAsset c Asset LocalizedTexts[]:UITextDatabase FontMaps[]:UIFontMapAsset",
            "LockType e - LockAlways LockOnTagged LockNever",
            "LockingControllerData c ComponentData ZoomLevelLock[]:ZoomLevelLockData LockTime ReleaseTime ReleaseOnNewTargetTime SampleRate HoldStillThreshold RayLength LockOnVisibleTargetsOnly PositionOnly LockOnWorldSpacePos MinimumLockTime",
            "LockingWeaponEffectsData c WeaponEffectsData LockingController:LockingControllerData IsHoming IsGuided WarnLock:WarnTarget",
            "LodInterval s - Min Max",
            "LogicEntityData c EntityData Enabled",
            "LookAtTriggerEntityData c TriggerEventEntityData ObjectToFollow:SoldierSpawnBaseEntityData FOV Weapon:SoldierWeaponAsset ZoomLevel MinDistanceToObject MaxDistanceToObject",
            "LookConstantData c - EyeTargeterMinOffsetTime EyeTargeterMaxOffsetTime EyeTargeterMinOffsetTime2 EyeTargeterMaxOffsetTime2 EyeTargeterMinPitchOffset EyeTargeterMaxPitchOffset EyeTargeterMinYawOffset EyeTargeterMaxYawOffset EyeTargeterSpringModifier LookAroundClampAngle LookAroundMinRandomWaitTime LookAroundMaxRandomWaitTime LookAroundRandomAfterObjectModifier PlayerInteractDistance PlayerInteractAimAngle PlayerInteractMinOffsetAngle PlayerInteractMaxOffsetAngle PlayerInteractMinTime PlayerInteractMaxTime PlayerInteractBreakTime",
            "LookConstraintsData s - MinLookYaw MaxLookYaw MinLookPitch MaxLookPitch",
            "LoopType e - LtNone LtForward LtRelease LtInstantRelease LtBounce",
            "LoosePartPhysicsData c - FakePhysics:FakePhysicsData",
            "LowPassIir2Node c SoundNode Frequency:SoundNodePort In:SoundNodePort Out:SoundNodePort",
            "MPModeData s - VehiclePoints",
            "MapMarkerBlinkType e - MMBTHold MMBTBlink MMBTCount",
            "MapMarkerColorType e - MMCTRed MMCTBlue MMCTGreen",
            "MapMarkerEntityData c GameEntityData Controllable:SpawnEntityData StaticModel:ReferenceObjectData MultipleTrigger:MultipleTriggerEntityData Sid Radius BlinkTime MarkerType:MapMarkerType ColorType:MapMarkerColorType VisibleForTeam:TeamId HudIcon:UIHudIcon DisplayOnMinimap ShowAirTargetBox",
            "MapMarkerType e - MMTMissionObjective MMTSecondaryMissionObjective MMTSoldier MMTVehicle MMTAmmoCrate MMTGeneric",
            "Mat4 s - m11 m12 m13 m14 m21 m22 m23 m24 m31 m32 m33 m34 m41 m42 m43 m44",
            "Mat4Parameter c BaseParameter InitialValue:Mat4",
            "MatchMakerAsset c Asset EloParams:EloParameters Sessions[]:MatchMakingSession Maps[]:MatchMakingMap Playlists[]:MatchMakingPlaylist",
            "MatchMakingFilter c MatchMakingRule PossibleValues[] DefaultValues[]",
            "MatchMakingFitThreshold c - Seconds MinimumFitScore",
            "MatchMakingIntegerPreference c MatchMakingRule Weight Scale DefaultValue MatchingOption:MatchMakingMatchingOption FitThresholds[]:MatchMakingFitThreshold",
            "MatchMakingMap c - UIName MapName License",
            "MatchMakingMapLink c - Current:MatchMakingMap Next:MatchMakingMap",
            "MatchMakingMatchingOption e - MatchMakingMatchingOption_Normal MatchMakingMatchingOption_MatchOnly",
            "MatchMakingPlaylist c - Name Rule:MatchMakingPlaylistRule RoundsPerMap Maps[]:MatchMakingMapLink",
            "MatchMakingPlaylistRule e - MatchMakingPlaylistRule_Random MatchMakingPlaylistRule_Linear",
            "MatchMakingPoolPlayerCountThreshold c - Seconds TargetPlayerCount",
            "MatchMakingPreferenceVariant e - MatchMakingPreferenceVariant_Normal MatchMakingPreferenceVariant_PingSite MatchMakingPreferenceVariant_Dynamic",
            "MatchMakingRule c - Rule Match SkipIfDefault HideOnRuleVisible",
            "MatchMakingSession c - SessionIdentifier SessionType:MatchMakingSessionType Filters[]:MatchMakingFilter IntegerPreferences[]:MatchMakingIntegerPreference StringPreferences[]:MatchMakingStringPreference PoolPlayerCountThresholds[]:MatchMakingPoolPlayerCountThreshold CountServersAvailableForReset HostSetupTimeout ListServersMaxCount PoolMaxPlayers PoolPlayerTimeout",
            "MatchMakingSessionType e - MatchMakingSessionType_FindServer MatchMakingSessionType_AssignHost MatchMakingSessionType_ResetServer MatchMakingSessionType_ListServers",
            "MatchMakingStringPreference c MatchMakingRule Weight Labels[] Fit[] DefaultValues[] MatchingOption:MatchMakingMatchingOption VotingMethod:MatchMakingVotingMethod FitThresholds[]:MatchMakingFitThreshold Variant:MatchMakingPreferenceVariant",
            "MatchMakingVotingMethod e - MatchMakingVotingMethod_Plurality MatchMakingVotingMethod_Lottery",
            "MaterialBigWorldMapData s - MaterialName BigWorld[]:BigWorldSettingsAsset",
            "MaterialData c - DebugColor:Vec3 Category Name Penetrable ClientDestructible SeeThrough NoCollisionResponse NoCollisionResponseCombined Hash",
            "MaterialGridAsset c Asset",
            "MaterialGridData c Editable Materials[]:MaterialData MaterialGridLayers[]:MaterialGridLayerData MaterialRelations[]:MaterialRelationData",
            "MaterialGridLayerData c - MaterialRelations[]:MaterialRelationData",
            "MaterialMapAsset c Asset",
            "MaterialMapData c - Data[] Size",
            "MaterialPropertyAIData c MaterialRelationPropertyData SoftCoverHeight",
            "MaterialPropertyEffectData c MaterialRelationPropertyData ImpactEffect:EffectAsset",
            "MaterialPropertyHealthData c MaterialRelationPropertyData Health",
            "MaterialPropertyImpulseData c MaterialRelationPropertyData ImpulseAbsorptionMultiplier",
            "MaterialPropertyPhysicsData c MaterialRelationPropertyData DynamicFrictionModifier StaticFrictionModifier RestitutionModifier",
            "MaterialPropertySootData c MaterialRelationPropertyData Soot:SootMaterialData",
            "MaterialPropertySoundData c MaterialRelationPropertyData ImpactSound:SoundAsset ScrapeSound:SoundAsset ScrapeLength SoldierSettings:MaterialSoldierSoundSettings Softness",
            "MaterialPropertyTerrainData c MaterialRelationPropertyData DirtTriggerColor:Vec3 DestructionEffect:EffectAsset DirtTriggerFactor",
            "MaterialRelationDamageData c MaterialRelationPropertyData CollisionDamageMultiplier CollisionDamageThreshold CollisionImpulseThreshold DamageProtectionMultiplier DamagePenetrationMultiplier DamageProtectionThreshold ExplosionCoverDamageModifier NeverPenetrate InflictsDemolitionDamage",
            "MaterialRelationData c - Data[]:MaterialRelationPropertyData Material1:MaterialData Material2:MaterialData",
            "MaterialRelationDecalData c MaterialRelationPropertyData Decal:DecalTemplateData",
            "MaterialRelationEffectData c MaterialRelationPropertyData ImpactEffect:EffectAsset",
            "MaterialRelationPropertyData c - SourceMaterial:MaterialData",
            "MaterialRelationSoundData c MaterialRelationPropertyData ImpactSound:SoundAsset ScrapeSound:SoundAsset ScrapeLength",
            "MaterialRelationTerrainDestructionData c MaterialRelationPropertyData Width Depth",
            "MaterialRelationVehicleData c MaterialRelationPropertyData ChassiEffect:EffectAsset TrackEffect:EffectAsset WheelEffect:EffectAsset GroundEffect:EffectAsset",
            "MaterialSoldierSoundSettings c - FootStepSound:SoundAsset ProneSound:SoundAsset LandSound:SoundAsset",
            "MaterialSoundMapData s - MaterialName Sound:SoundAsset",
            "MathOp e - Add Sub Mult Div",
            "MedicBagEntityData c ExplosionPackEntityData Healer:HealerData",
            "MedicBagWeaponEffectsData c WeaponEffectsData Healer:HealerData HealthPointsRefillSpeed HealthPointsPerBag",
            "MedkitWeaponEffectsData c WeaponEffectsData",
            "MeleeWeaponEffectsData c WeaponEffectsData AttackOnDeploy AttackOnDeployDelay",
            "MenuConfigAsset c Asset Gunrack:GunrackConfig Credits:CreditsData ExtraMovies:ExtraMoviesConfig Eulas[]:EulaTexture AttractionIdleTime AttractionMovie:UIMovieAsset IntroMovie:IntroMovieData IntroMovieMultiplayer:IntroMovieData LevelMovies[]:LevelMovie GameOverMovies[]:UIMovieAsset PreGameOverMovie:UIMovieAsset TutorialLevelName",
            "MenuEntityData c GameEntityData MenuId:MenuIdEnum HeaderSid BodySid",
            "MenuIdEnum e - MieSpawnMenu MiePlaying MieSpectate MieKilledMenu MieZoomMinimap MiePreSpawn MiePostSpawn MieDeath MieVictoryScreen MieLetterBox MieMovie MieMissionFailure MieVoid MieDefault MieCreateSoldier MieTutorial MieLastMenuId",
            "MeshAsset c Asset LodSettings:MeshLodSettingsAsset StreamingEnable IrradianceVolumeEnable OccluderMeshEnable Ps3EdgeEnable Ps3EdgeScoreErrorWorkaroundEnable Ps3SootEdgeEnable Ps3SootEdgeScoreErrorWorkaroundEnable ShaderSets[]:MeshShaderSetAsset",
            "MeshEvaluatorData c EvaluatorData DistributionData[]:Vec3 MeshAsset:MeshAsset UniformPointCount MinNormalizedIsoValue MaxNormalizedIsoValue UseDirectionData",
            "MeshLodSettingsAsset c Asset Enable LodDistance0 LodDistance1 LodDistance2 LodDistance3 LodDistance4 Lod0Enabled Lod1Enabled Lod2Enabled Lod3Enabled Lod4Enabled MinScreenArea Ps3LodScale",
            "MeshPortalDesc s - Winding[]:Vec3 SectorA SectorB",
            "MeshProjectileEntityData c ProjectileEntityData Mesh:MeshAsset TrailEffect:EffectAsset IsAttachable ExtraDamping InitialAngularVelocity:Vec3",
            "MeshSectorDesc s - Id Parts[] Box:AxisAlignedBox",
            "MeshSectorizationAsset c Asset Portals[]:MeshPortalDesc Sectors[]:MeshSectorDesc WorldScale",
            "MeshShaderSetAsset c Asset",
            "MeshStreamingSettings c - Enable UpdateEnable PoolSize Ps3CellPoolSize MaxPendingLoadCount PrioritizeVisibleMeshesFirstEnable PrioritizeVisibleLodsFirstEnable PrioritizeVisibleLoadsEnable ClosestPointDistanceEnable DrawStatsEnable DrawMissingListEnable DrawPriorityListEnable DrawLoadingListEnable DrawMeshListEnable DrawNonStreamedListEnable ListViewPageIndex ListViewSortOrder SimulationEnable SimulatedSeekTime SimulatedMbPerSecond",
            "MeshSurfaceShader s - Name Shader:ShaderAsset",
            "MessageEntityData c GameEntityData MessageSid SecondMessageSid Reciever:MessageReciever TeamId:TeamId SquadId:SquadId DisplayTime",
            "MessageReciever e - MrAll MrTeam MrTeamAndSquad MrPlayer",
            "MiddlegrowthModelEntityData c GamePhysicsEntityData Mesh:TreeMeshAsset DestructionEffects[]:EffectAsset",
            "MiddlegrowthModelGroupEntityData c GroupEntityData",
            "MiddlegrowthModelGroupMemberData c GroupMemberData MeshEntityType:TreeMeshEntityData",
            "MinMaxNode c SoundNode X:SoundNodePort Y:SoundNodePort Max:SoundNodePort Min:SoundNodePort",
            "MinimapCameraOffsetData s - Position:Vec2 CameraFov",
            "MinimapData s - ShowText Position:Vec2 Size:Vec2 CameraFov CameraDistance CameraLookDistance OverlayColor:Vec3 OverlayAlpha CameraPosition:Vec3 CameraRotation RotationFromPlayer PositionFromPlayer",
            "MinimapSettings s - GenerateMinimap MinObjectBoundingRadius ObjectBoundingBoxMesh:MeshAsset ObjectsToReplace[]:MeshAsset MinimapObjects[]:MeshAsset VegetationObjectsToReplace[]:MeshAsset MinimapVegetationObjects[]:MeshAsset VegetationDensity:VegetationDensity RoadHeightOverTerrain GenerateRiverMesh GenerateLakeMesh WaterHeightModifier TerrainColor:Vec3 ObjectColor:Vec3 RoadColor:Vec3 WaterColor:Vec3 VegetationColor:Vec3",
            "MipmapFilterMode e - MfmBox MfmRenormalize MfmPoisson13 MfmPoisson13Clamped",
            "MissileEntityData c MeshProjectileEntityData EngineEffect:EffectAsset FlyBySound:SoundAsset EngineStrength MaxSpeed EngineTimeToIgnition EngineTimeToLive TimeToActivateGuidingSystem TimeToArm MaxTurnAngle Drag Gravity ApplyGravityWhenUnguided FlyBySoundRadius FlyBySoundSpeed ImpactImpulse Damage WarnTarget:WarnTarget",
            "MissionEntityData c GameEntityData BriefingSid SuccessSid FailureSid NextLevelToRun RollCreditsOnSuccess",
            "MissionObjectiveEntityData c GameEntityData MapMarkers[]:MapMarkerEntityData ChildObjective[]:MissionObjectiveEntityData ParentObjective:MissionObjectiveEntityData HudData:MissionObjectiveHudData ObjectiveType:ObjectiveType DisplayTime DelayTime ObjectiveSid ObjectiveBriefSid SuccessSid Reciever:MessageReciever TeamId:TeamId SquadId:SquadId DisplaySuccess",
            "MissionObjectiveHudData c - CameraPosition:Vec3 CameraRotation CameraFov CameraDistance CameraLookDistance VerticalBoundaries:Vec2 HorizontalBoundaries:Vec2",
            "MixGroup c - Path Gain Parent:MixGroup IsWildcard Wildcard",
            "MixType e - MtGeneral MtMusic MtDialogue MtCarRadio",
            "MixerAsset c Editable Groups[]:MixGroup",
            "MixerGroup c SoundNodePortGroup In:SoundNodePort Amplitude:SoundNodePort",
            "MixerNode c SoundNode MixerGroups[]:MixerGroup Out:SoundNodePort",
            "MobilityData c - MaxSpeed Use3DWaypoints",
            "MonitoringSettings c - Enable LogEnable HubAddress HubPort ConnectionRetryInterval ReceiveTransactionTimeout LogBufferSize",
            "MonophonicSamplerGroup c SamplerGroup",
            "MonophonicSamplerNode c SamplerNode",
            "MortarStrikeFiringEffectsData c WeaponEffectsData HoldingTolerance MaxStrikeDistance StrikeRadius MaxRandomSpawnHeight",
            "MotionDampingData c - LinearModifier:Vec3 Pitch Yaw Roll Linear",
            "MotionSensorEntityData c GrenadeEntityData Radius TimeToShowEnemies OnlyShowEnemies ShowSoldiers ShowVehicles TeamToEffect:TeamId DisplayInMiniMap",
            "Movement4WayNodeData c AbstractMovementNodeData Forward:BaseNodeData Backward:BaseNodeData LeftNormal:BaseNodeData RightNormal:BaseNodeData",
            "MovementBlendNodeData c AbstractMovementNodeData Nodes[]:MovementBlendNodeGroupData",
            "MovementBlendNodeGroupData c AnimNodePortGroupData Animation:BaseNodeData WeightParameter:FloatParameter",
            "MovementNodeData c AbstractMovementNodeData Forward:BaseNodeData Backward:BaseNodeData LeftNormal:BaseNodeData LeftInverse:BaseNodeData RightNormal:BaseNodeData RightInverse:BaseNodeData FrontParameter:BoolParameter",
            "MovingBodyData c - Mass Inertia",
            "MultiCrossfaderGroup c SoundNodePortGroup Amplitude:SoundNodePort Start:SoundNodePort Stop:SoundNodePort FadeAmplitude FadeBegin FadeEnd FadeType:FaderType",
            "MultiCrossfaderNode c SoundNode CrossfaderGroups[]:MultiCrossfaderGroup Start:SoundNodePort Stop:SoundNodePort Control:SoundNodePort LockControlValue",
            "MultipleTriggerEntityData c TriggerEventEntityData EntityTriggerAmount[]:GameEntityData TriggerAmount OutputEventAmount",
            "MultiplyData c EvaluatorData",
            "NavigationConstantData c - SpeedThresholdForBeingStuck TimeStuckBeforeTeleport WaterCostModifier RoadCostModifier RandomDestinationHeightCheck UseTimeslicedSearch MaxNodesToTrace RandomDestinationMethod MaxAnimationDirectionDifference MinAnimationDistanceGain MinAnimationPartAlongPath MinSpeedForMovingAnimationStart HelicopterWaypointInterpolationDistance HelicopterPathfindingSearchSteps MinimumHelicopterHeight HelicopterPathRadius HelicopterRecalculateRadius ValidAerialPositionHeight AerialRandomDestinationHeight MoveToValidWPRadius AvoidVehicleDistance AvoidVehicleSize AvoidVehicleMoveRadius AvoidVehicleMoveDistance",
            "NavigationType e - NTGround NTHelicopter NTNone",
            "NetworkSettings c - Version ProtocolVersion TitleId ClientPort ServerPort MaxGhostCount MaxClientCount ClientConnectionDebugFilePrefix ServerConnectionDebugFilePrefix",
            "NetworkableLoosePartPhysicsData c LoosePartPhysicsData Networked",
            "NodeAligmentData s - NodeType Name Size Fields[]:NodeFieldAligmentData",
            "NodeFieldAligmentData s - Size Offset Name",
            "NodesAligmentHelperAsset c Asset Nodes[]:NodeAligmentData",
            "NoiseTextureAsset c TextureBaseAsset NoiseType:ProceduralNoiseType OutputFormat:TextureOutputFormat Dimensions Size Octaves Period Frequency Turbulence GenerateMipmaps",
            "NormalMapFilter e - Nmf4x Nmf3x3 Nmf5x5 Nmf7x7 Nmf9x9 NmfDuDv",
            "NormalMapGenerationParams s - NormalMapConversionEnabled MinZ Scale FilterKernel:NormalMapFilter HeightConversionMethod:HeightConversionMethod AlphaResult:AlphaResultType Wrap InvertX InvertY InvertZ AddHeightMap NormalMapSwapRGB",
            "NotExpressionData c BaseExpressionData SubExpression:BaseExpressionData",
            "NumberGeneratorNode c SoundNode Trigger:SoundNodePort Min Max NumberType:RandomType Y:SoundNodePort",
            "ObjectBlueprint c EntityBusData Object:GameObjectData",
            "ObjectData c - EquipmentType:AIVehicleBehaviourData Armament:ArmamentData Mobility:MobilityData StrengthType:StrengthType ReuseTime Forbidden ForbiddenForHuman HasExposedSoldier InterludeOnly",
            "ObjectSet c ComponentData Connections[]:ConnectionPair",
            "ObjectiveType e - OTPrimary OTTactical OTChildMisson",
            "OcclusionSettings c - Enable NormalViewDistance TerrainViewDistance SkipClipTriangleDistance ScreenWidth ScreenHeigth JobsEnable OccluderMeshEnable OccluderMeshJobCount RasterizeEnable RasterizeJobCount DrawZbuffer DrawOccluderMeshesWireframe DrawProcessedOccluders DrawOccludedStaticEntities DrawOccludedDynamicEntities",
            "OnGroundStateData c CharacterStateData JumpDelay JumpStaminaPenalty AllowedDistanceFromGround GroundHugging LimitDownwardVelocity",
            "OnlineProviderAsset c Asset Configurations[]:OnlineProviderConfiguration",
            "OnlineProviderConfiguration s - Platform:OnlineProviderPlatform IsServer DefaultUnsecure Client SKU Version Partition ServiceId Username Password PacketSize Port TheaterPort",
            "OnlineProviderPlatform e - OnlineProviderPlatform_Win32 OnlineProviderPlatform_Xenon OnlineProviderPlatform_PS3",
            "OptionDescriptor s - Name Category Description ValueType DefaultValue TargetPlatforms[] IsCommandLineOption IsFlag",
            "OrExpressionData c CompositeExpressionData",
            "OrderEntityData c GameEntityData Squads[]:SquadBaseEntityData Soldiers[]:SoldierSpawnBaseEntityData IssueOrderDelay OnCompleteTimeOut OnFailedTimeOut IsUrgent ReissueIfSoldierIsKilled",
            "OrderReadiness c UrgencyUserData OrderType Initial:ReadinessState Minimum:ReadinessState",
            "OrderSettings c Asset Orders[]:OrderReadiness",
            "OrderType e - OrderType_Attack_Enemy OrderType_Attack_Neutral OrderType_Defend OrderType_Count",
            "OrientedBoundingBox s - Transform:LinearTransform BoundingBox:AxisAlignedBox Name",
            "OscillatorGroup c SoundNodePortGroup Hz:SoundNodePort Shape:SoundNodePort Amplitude:SoundNodePort Out:SoundNodePort Min Max PhaseOffset Phase PreviousOutput",
            "OscillatorMode e - OmRandom",
            "OscillatorNode c SoundNode OscillatorGroups[]:OscillatorGroup",
            "OutdoorLightEntityData c LightEntityData ShCoefficientsLight[]:Vec4 ShCoefficientsLightDelta[]:Vec4 ShCoefficientsShadow[]:Vec4 HemisphereLightAngleFactor KeyColor:Vec3 KeySpecularColor:Vec3 TopColor:Vec3 BottomColor:Vec3 ReflectionEnvmap:TextureAsset DiffuseEnvmap:TextureAsset LightingModel:LightingModelType KeyColorIntensity KeySpecularColorIntensity TopColorIntensity BottomColorIntensity CloudShadowScale CloudShadowIntensity",
            "OutputEntry c SoundNodePortGroup In:SoundNodePort Output:SoundOutput",
            "OutputNode c SoundNode Entries[]:OutputEntry Lfe:SoundNodePort LfeSoundEmitter:SoundSourceLfeEmitter DialogSoundEmitter:SoundSourceDialogEmitter Dialog:SoundNodePort BypassHeadroom:SoundNodePort ConnectTo:BusConnection",
            "OverHeatData s - HeatPerBullet HeatDropPerSecond OverHeatPenaltyTime OverHeatThreshold OverHeatEffect:FireEffectData",
            "OvergrowthSettings c - Enable CastShadowsEnable ShadowMaxLod HiQualityLod ZeroViewportEnable InstancingEnable LodBlendEnable LodBlendOverlap LodBlendBillboardOverlap ZPassLod ForceBendingType ForceTechnique MaxBillboardCount BillboardsEnable BillboardScale BillboardShadowOffset AdaptiveLodMVertexBudget AdaptiveLodMargin AdaptiveLodSpeed AdaptiveLodSpeed2 AdaptiveLodMinModifier AdaptiveLodMaxModifier AdaptiveLodEnable JobsEnable InputsPerJob InputsPerSpuJob HiLodEnable OnlyBillboardsEnable AlmostOnlyBillboardsEnable GlobalLodScale WindEnable BranchWindPower BranchConstWind BranchWindAnimationSpeed UseMaxShadowLOD",
            "ParachuteStateData c CharacterStateData VerticalVelocity VerticalGain",
            "ParameterFilterLinearNode c SoundNode In:SoundNodePort Out:SoundNodePort AttackSpeed:SoundNodePort ReleaseSpeed:SoundNodePort",
            "ParameterFilterLpNode c SoundNode In:SoundNodePort Out:SoundNodePort Hz:SoundNodePort",
            "ParameterGroupData c - Parameters[]:BaseParameter ParametersTypeGroups[]:ParameterTypeGroupData",
            "ParameterTypeGroupData s - Parameters[]:BaseParameter",
            "PartComponentData c ComponentData HealthStates[]:HealthStateData NeighbourLinks[] IsSupported IsFragile EffectChildsOnHealthStateChanged",
            "PartLinkData c - PartComponentIndex1 PartComponentIndex2",
            "PartMaterialData s - Name MaterialName",
            "PathdataCollisionModel e - PCMSphere PCMSweptSphere PCMCylinder",
            "PathdataGenerator e - PGCMapBuilder PGCMapBuilderForVehicle",
            "PathfindingSettings c Asset StepMax StepLength HoleMax GroundSlopeMax GroundSlopeMin MaxDeltaHeight InfantrySeeds VehicleSeeds Pitch VerticalPitch GraphAccuracy NbSectors EdgeRadius OverConnectionRatio ForbidCollidingEdge EntityRadius EntityHeight BuildCompositeShapes MaxPitch BoxSize VerticalBoxSize DetailedZoneSettings:PathfindingZoneSettings OutputType:PathfindingType BuildRoads BuildBridges ForbidWater Generator:PathdataGenerator CollisionModel:PathdataCollisionModel UseBoxApproximation UseExclusions UseForbid ForbidHeight WaterLevelAdjustment",
            "PathfindingType c Asset OutputName MaxGraphHandles SubgraphsToLoad SearchRadius DiagramHalfWidth MaxGaps DistEdgeMax CanGoMaxEntityRadius",
            "PathfindingZoneSettings c - Pitch GraphAccuracy NbSectors",
            "PatrolOrderEntityData c OrderEntityData RouteWaypoints[]:AreaGeometryEntityData TypeOfRoute:RouteType MinDistance UsePathFinding DefaultSpeed",
            "PersistenceData c Asset Partitions[]:PersistentPartition Final[]:PersistentValueTemplateData Remapping[]:PersistentValueNameRemapping Weapons:PersistenceWeaponData Entitlements:EntitlementAsset GoldNuggets Collectibles",
            "PersistenceGameData c Asset Assets[]:Asset",
            "PersistenceWeaponData c Asset Weapons[]:PersistentWeapon",
            "PersistentDerivedValueTemplateData c PersistentValueTemplateData Formula",
            "PersistentPartition c - Domain Title",
            "PersistentValueNameRemapping c - Internal External",
            "PersistentValueProtection e - PersistentValueProtection_ReadOnly PersistentValueProtection_WriteOnly PersistentValueProtection_ReadAndWrite",
            "PersistentValueTemplateData c - Name Description DefaultValue ValueType:PersistentValueType ClientFlags:PersistentValueProtection ServerFlags:PersistentValueProtection WritePolicy:PersistentValueWritePolicy UploadOnUnranked",
            "PersistentValueType e - PersistentValueType_Set PersistentValueType_High PersistentValueType_Low PersistentValueType_Increment PersistentValueType_Decrement",
            "PersistentValueWritePolicy e - PersistentValueWritePolicy_Checkpoint PersistentValueWritePolicy_Finalize PersistentValueWritePolicy_Disconnect",
            "PersistentWeapon c Asset WeaponType:PersistentWeaponType Cost Id Dependency:PersistentWeapon Collectible UnlockBundle",
            "PersistentWeaponType e - PersistentWeaponType_Normal PersistentWeaponType_Unlock PersistentWeaponType_LimitedEdition PersistentWeaponType_Retail",
            "PhysicsEntityData c - Asset:HavokAsset FloatPhysics:FloatPhysicsData Mass AllowZeroMayaMass InertiaModifier:Vec3 Friction Restitution Proximity:ProximityData",
            "PhysicsMaterialMapping s - From To",
            "PhysicsMaterialSetAsset c Asset MaterialMappings[]:PhysicsMaterialMapping",
            "PhysicsNode c SoundNode Distance:SoundNodePort Velocity:SoundNodePort",
            "PhysicsReportingAsset c Asset ReportCount",
            "PhysicsSettings c - NumberOfThreads IntegrateJobCount CollideJobCount EnableAIRigidBody ForestEnable EnableJobs HeightfieldRSXStreaming SingleStepCharacter",
            "PickupEntityData c GameEntityData Mesh:MeshAsset MeshShaderSet:MeshShaderSetAsset UseTeamShaderSet UseWeaponMesh TimeToLive UnspawnOnPickup ContentIsStatic PositionIsStatic IgnoreNullWeaponSlots ForceWeaponSlotSelection DisplayInMiniMap HasAutomaticAmmoPickup InteractionRadius",
            "PickupSettingsData s - MeshRenderOffset:Vec3 MeshRenderRotation:Vec3",
            "PingPongNodeData c BaseNodeData StateANodeData:BaseNodeData StateBNodeData:BaseNodeData TransitionNodeData:BaseNodeData StateParameter:FloatParameter",
            "PixelShaderAsset c BaseShaderAsset",
            "Plane s - x y z w",
            "PlayVideoEntityData c GameEntityData BinkMovie:BinkTextureAsset KeepBlackScreen",
            "PlayerEntryComponentData c EntryComponentData AnimTree:AnimTreeDataAsset",
            "PointEnvelope c - Points[]:PointEnvelopePoint",
            "PointEnvelopePoint s - X Y",
            "PointLightEntityData c LightEntityData Color:Vec3 Intensity Radius ShadowbufferOffset ShadowbufferResolution ShadowmapResolution ContributionName DiffuseEnable SpecularEnable QuadraticAttenuation Shadowbuffer Shadowmap",
            "PointSystemParamsAsset c Asset MeleeKillWeapon:StatsCategoryData StringForMeleeKill PointsForMeleeKill StringForKill PointsForKill StringForHeadshot PointsForHeadshot StringForHotVehicleDestroy PointsForHotVehicleDestroy StringForTeamKill PointsForTeamKill StringForSuicide PointsForSuicide StringForDriverAssist PointsForDriverAssist StringForSquadDriverAssist PointsForSquadDriverAssist StringForVehicleAssist PointsForVehicleAssist StringForSquadVehicleAssist PointsForSquadVehicleAssist StringForAvengerKill PointsForAvengerKill StringForSaviorKill PointsForSaviorKill StringForRepairTeamVehicle PointsForRepairTeamVehicle StringForRepairSquadVehicle PointsForRepairSquadVehicle StringForDamageTeamVehicle PointsForDamageTeamVehicle StringForTeamHealing PointsForTeamHealing StringForTeamDamage PointsForTeamDamage StringForSquadHealing PointsForSquadHealing StringForKillAssist PointsForKillAssist StringForSquadKillAssist PointsForSquadKillAssist StringForTaggedDestroyAssist PointsForTaggedDestroyAssist StringForTaggedKillAssist PointsForTaggedKillAssist StringForObjectiveDamage PointsForObjectiveDamage StringForDisarmObjective PointsForDisarmObjective StringForCapturedCapturePoint PointsForCapturedCapturePoint StringForCapturedCapturePointAssist PointsForCapturedCapturePointAssist StringForObjectiveTeamDamage PointsForObjectiveTeamDamage StringForObjectiveDestruction PointsForObjectiveDestruction StringForObjectiveTeamDestruction PointsForObjectiveTeamDestruction StringForDefensiveKillBonus PointsForDefensiveKillBonus StringForOffensiveKillBonus PointsForOffensiveKillBonus StringForHostileDmzKill PointsForHostileDmzKill StringForDmzCampingKill PointsForDmzCampingKill StringForVehicleKillAssist PointsForVehicleKillAssist StringForVehicleDestroyAssist PointsForVehicleDestroyAssist HotVehicleDestroyTimeout AvengerKillTimeout SaviorKillTimeout RepairTeamVehicleLimit DamageTeamVehicleLimit TeamHealingLimit TeamDamageLimit SaviorDamageLimit KillAssistLimit ObjectiveDamageLimit VehicleKillAssistLimit VehicleDestroyAssistLimit DefensiveKillBonusRadius OffensiveKillBonusRadius VehicleAirTimeLimit SoldierAirTimeLimit DriverTimeLimit IngameTimeLimit StrikeKillLimit StrikeKillTimeLimit SquadKillLimit Ranks[]:RankLevelData DogTags[]:DogTagData",
            "PolynomialColorInterpData c EvaluatorData Color0:Vec3 Color1:Vec3 Coefficients:Vec4",
            "PolynomialData c EvaluatorData Coefficients:Vec4 ScaleValue MinClamp MaxClamp",
            "PolynomialOperation e - Multiplication Addition Subtraction",
            "PolynomialOperatorData c EvaluatorData FirstOperand:PolynomialTempData SecondOperand:PolynomialTempData Operation:PolynomialOperation MinClampResult MaxClampResult",
            "PolynomialTempData s - Coefficients:Vec4 ScaleValue MinClamp MaxClamp",
            "PoseConstraintsData s - StandPose CrouchPose PronePose",
            "PositionEvaluationConstantData c - MaxCachedNodeIds FrontLineSearchSensitivity FrontLineSearchDepth FrontLineAccuracy RandomPositionCount OwnRandomPositionsRadius",
            "PostProcessDebugMode e - PpdmNormal PpdmBloom PpdmDofBlur PpdmBlur PpdmBlurStep PpdmDepth",
            "PowerToolWeaponEffectsData c WeaponEffectsData RepairSound:SoundAsset RepairCompletedSound:SoundAsset DamageSound:SoundAsset RepairMaterial",
            "PreRoundEntityData c GameEntityData PlayerCountNeeded NotEnoughPlayersRestartTimer",
            "PrimitiveConfigData c -",
            "ProceduralLeaningNodeData c BaseNodeData UphillMaxTranslation:Vec3 DownhillMaxTranslation:Vec3 MaxAccelerationTranslation:Vec3 MaxTurnTranslation:Vec3 MovementSpeedParameter:FloatParameter MovementAngleParameter:FloatParameter RotationSpeedParameter:FloatParameter UphillMaxRotation UphillMaxSlope UphillMinSlope DownhillMaxRotation DownhillMaxSlope DownhillMinSlope MaxAccelerationRotation AccelerationLimit AccelerationLeaningTime MaxTurnRotation MaxRotationSpeed MinRotationSpeed",
            "ProceduralLevelCreateDesc c Asset SaveToDiskEnable OutputName OutputPath",
            "ProceduralNoiseType e - PntPerlin PntSimplex PntSimulation",
            "ProcessorConnection c - Source:ProcessorData Dest:ProcessorData",
            "ProcessorData c EmitterComponentData NextProcessor:ProcessorData Edited ProcessorType:ProcessorType ProcessorGroup:EmitterProcessorGroup Enable EvaluatorInput:EmittableField AcceptsEvaluator DefaultValues:Vec4",
            "ProcessorType e - PtBaseEmitter PtSpawnRate PtPreSpawn PtSpawnCompositeMeshParts PtSpawnSpeed PtSpawnPosition PtSpawnDirection PtSpawnSize PtSpawnAnimation PtSpawnAnimationFrame PtSpawnRotation PtSpawnRotationSpeed PtUpdatePosition PtUpdateAge PtTurbulance PtGravity PtLocalForce PtAirResistance PtUpdateLinearVelocity PtUpdateCollision PtUpdateOrientation PtEmitter PtUpdateBoundingBox PtUpdateColor PtUpdateColorLeaf PtUpdateTransparency PtUpdateTextureCoords PtCycleAnimations PtUpdateRotation PtUpdateSizeX PtUpdateSizeY PtUpdateSizeZ PtUpdateSize PtUpdateAlphaLevelMin PtUpdateAlphaLevelMax PtUpdateAlphaLevelScale PtUpdateClipScale ProcessorTypeCount",
            "ProjectileEntityData c GamePhysicsEntityData InitialSpeed TimeToLive InitMeshHideTime VisualConvergeDistance DetonateOnTimeout Explosion:ExplosionEntityData SuppressionData:WeaponSuppressionData",
            "ProjectileImpulseTestEntityData c TestEntityData fireRate Projectile:ProjectileEntityData",
            "ProjectorLightEntityData c LightEntityData Color:Vec3 Texture:TextureAsset Projection:ProjectorLightProjection ContributionName ShadowmapResolution ShadowbufferResolution ShadowbufferOffset Intensity OrthoWidth OrthoHeight FieldOfView Aspect NearPlane ViewDistance DiffuseEnable SpecularEnable QuadraticAttenuation Shadowbuffer Shadowmap",
            "ProjectorLightProjection e - PlpPerspective PlpOrthographic",
            "PropellerEngineConfigData c EngineConfigData HorisontalForceOffset:Vec3 PropellerType:PropellerType ForceMagnitudeInputType:ForceMagnitudeInputType DirectionVectorIndex ForceMagnitudeMultiplier LiftForceSpringConstant LiftForceDampingConstant CyclicInputScaleRoll CyclicRollLiftMod CyclicRollStrafeMod CyclicInputScalePitch CyclicPitchLiftMod CyclicPitchStrafeMod CyclicPitchStrafeBrakeMod CollectiveInputIdle CollectiveInputScale DefaultAngleOfAttack AttackAngleMod StabilizerMod HorisontalMinEffectVelocity HorisontalMinEffectMod EnableNewHelicopter SPAllowed SPDefault SPForwardStrength SPSidewaysStrength SPVerticalStrength PitchStrength PitchLimit PitchFromVel VelocityFromPitch RollStrength BankingStrength BankingLimit PitchUpWhenBankStrength PitchUpWhenBankLimit GravityMod",
            "PropellerType e - Regular Rotor",
            "PropertyCurveType e - PCT_NO_EFFECT PCT_ALWAYS_ON PCT_ALWAYS_OFF PCT_ANIMATED PCT_MANUAL_INPUT",
            "PropertyKeyData s - InterpolationType:InterpolationType Time SortKey",
            "PropertyTrackData c - PropertyKeys[]:PropertyKeyData Target PropertyName",
            "ProximityData c - ProximityType:ProximityObjectType",
            "ProximityObjectType e - PotProximityDisabled PotVaultableLow PotVaultableHigh PotSupportedShooting PotInteractWith",
            "Ps3MemoryType e - PmtDefault PmtCell PmtRsx",
            "PulseEntityData c LogicEntityData Interval",
            "QualityMapData c BaseInfluenceMapData",
            "Quat s - x y z w",
            "RadarSweepComponentData c ComponentData SweepForControllables ControllableSweepRadius ControllableSweepInterval SweepForMines MineSweepRadius MineSweepInterval OnlySpootControllablesForLocalPlayer",
            "RadioComponentData c ComponentData SoundEffect:SoundAsset",
            "RagdollAsset c Asset UseServerRagdoll",
            "RagdollImpulse s - BoneName RandomMinImpulse:Vec3 RandomMaxImpulse:Vec3",
            "RagdollNodeData c BaseNodeData",
            "RandomEvaluatorData c EvaluatorData Max Min",
            "RandomPulseEntityData c LogicEntityData MinInterval MaxInterval",
            "RandomSelectorGroupData c AnimNodePortGroupData NodeData:BaseNodeData LowerTime UpperTime",
            "RandomSelectorMainGroupData c AnimNodePortGroupData NodeData:BaseNodeData LowerTime UpperTime RelativeValue",
            "RandomSelectorNodeData c BaseNodeData MainNodes[]:RandomSelectorMainGroupData RandomNodes[]:RandomSelectorGroupData ResetRandomTimes",
            "RandomTriggerEntityData c TriggerEventEntityData RandomEventWeight[]",
            "RandomType e - GTRandomUniform",
            "RandomWalkOrderEntityData c OrderEntityData Geometry[]:GeometryEntityData",
            "RandomXYZEvaluatorData c EvaluatorData MaxX MinX MaxY MinY MaxZ MinZ",
            "RankLevelData c - RankName PointsNeeded UnlockCredits Image:TextureAsset ImageName Sound:WaveAsset SoundName VO:DialogLineNode VOEvent:VOEventType",
            "ReadinessLevel e - RLPatrol RLReady RLCombat RLNone",
            "ReadinessLevelCharacteristics s - MinReactionTime MaxReactionTime ReductionTime",
            "ReadinessLevels s - Patrol:ReadinessLevelCharacteristics Ready:ReadinessLevelCharacteristics Combat:ReadinessLevelCharacteristics",
            "ReadinessState e - RSPatrol RSReady RSCombat",
            "ReceiveEntry c SoundNodePortGroup Out:SoundNodePort Source:SoundGraphParameter Parameter SavedValue",
            "ReceiveNode c SoundNode Entries[]:ReceiveEntry",
            "ReferenceObjectData c GameObjectData Transform:LinearTransform ReferencedObject:ObjectBlueprint ObjectSets[]:ObjectSet MeshShaderSet:Asset PhysicsMaterialSet:Asset",
            "RegroupOrderEntityData c OrderEntityData Areas[]:GeometryEntityData FightWhileRegrouping CrouchOnArrival",
            "ReloadLogic e - rlWeaponSwitchCancelsUnfinishedReload rlReloadUnaffectedByWeaponSwitch",
            "ReloadType e - rtSingleBullet rtMagazine",
            "RemoteEntryComponentData c EntryComponentData Dummy",
            "RenderMapMode e - RmmRead RmmWrite RmmReadWrite RmmWriteDiscard RmmWriteNoOverwrite",
            "RenderResourceUsage e - RruImmutable RruStatic RruDynamic",
            "RepairStationEntityData c GameEntityData TeamId:TeamId RepairValue AllowedVehicles[]:VehicleAsset Geometry[]:GeometryEntityData",
            "RepairToolWeaponEffectsData c PowerToolWeaponEffectsData RechargeSpeed",
            "ReplacementAnimationPairData s - Animation:BaseAnimationAsset ReplacementAnimation:BaseAnimationAsset",
            "ResamplingQuality e - SincBest SincMedium SincFastest ZeroOrderHold LinearQuality",
            "ResourceManagerSettings c - DeferredLoadingEnable",
            "ResultAnimTreeData c - Nodes[]:BaseNodeData AnimationList[]:BaseAnimationAsset AimAnimationList[]:AimAnimationAsset Controls[]:BaseAnimationControlData AimControls[]:AimControlData EffectInfluenceMaps[]:InfluenceMapData Preprocessors[]:BasePreprocessorData RootNode:BaseNodeData Skeleton:SkeletonAsset ParameterGroupData:ParameterGroupData AbsoluteTimeParameter:FloatParameter DeltaTimeParameter:FloatParameter PoseParameter:IntParameter Name Priority BlendInTime IgnorePriority",
            "ResultInfluenceMapData c - Weights[] Skeleton:SkeletonAsset",
            "ReverbAsset c - ImpulseResponse:IrAsset Indoor MaxArea Gain",
            "ReverbSettingsAsset c Editable Reverbs[]:ReverbAsset",
            "RichPresenceConnection c - RichPresenceIndex ContextPairs[]:RichPresenceContextPair PropertyPairs[]:RichPresencePropertyPair",
            "RichPresenceContext c - Name Index PossibleValues[]:RichPresenceContextValue ContextType:RichPresenceContextType",
            "RichPresenceContextPair c - ContextIndex ValueIndex ContextType:RichPresenceContextType",
            "RichPresenceContextType e - RichPresenceContextType_Normal RichPresenceContextType_RankedMatch",
            "RichPresenceContextValue c - Name Index",
            "RichPresenceData c Asset Strings[]:RichPresenceString Default:RichPresenceString Inactive:RichPresenceString Contexts[]:RichPresenceContext Properties[]:RichPresenceContext",
            "RichPresenceProperty c - Name Index PropertyType:RichPresencePropertyType",
            "RichPresencePropertyPair c - Property:RichPresenceProperty",
            "RichPresencePropertyType e - RichPresencePropertyType_Float RichPresencePropertyType_Integer RichPresencePropertyType_LongFloat RichPresencePropertyType_LongInteger",
            "RichPresenceString c - Name Index",
            "RigidMeshAsset c MeshAsset SootType:SootType SootVoxelSize SootSplitableTriangleEdgeLength Sectorization:MeshSectorizationAsset",
            "RigidMeshEntityData c EntityData LightMapEnable Mesh:RigidMeshAsset MeshShaderSet:MeshShaderSetAsset EnableGroundmapLighting",
            "RigidModelEntityData c GamePhysicsEntityData Mesh:RigidMeshAsset MeshShaderSet:MeshShaderSetAsset",
            "RigidModelGroupEntityData c GroupEntityData",
            "RigidModelGroupMemberData c GroupMemberData MeshEntityType:RigidMeshEntityData",
            "RotateVectorData c EvaluatorData Angle RotateWithinPlane",
            "RotationAxis e - raX raY raZ",
            "RotationBodyData c MovingBodyData AngularMomentumMultiplier AngularMomentumThreshold AngularMomentumDamping AngularConstraintMin AngularConstraintMax RotationAxis UseAngularMomentumThreshold UseAngularMomentumDamping UseAngularConstraint",
            "RotorComponentData c ComponentData LowRpmModel:RotorModelData HighRpmModel:RotorModelData RotationAxis:RotationAxis RotationMultiplier ChangeModelRpm BlowEffect:EffectAsset OnlyTriggerBlowEffectInWater TriggerGroundEffectHeight CriticallyDamagedRotationForce",
            "RotorModelData s - RotationRpm MeshName",
            "RouteType e - RouteStop RouteCircular RoutePingPong",
            "RpgSkill e - AccurateWithTracer AlwaysAccurate OnlyWithTracer",
            "RumbleFiringData s - LowRumble HighRumble RumbleDuration",
            "RuntimeDialogLineNode s - HashedName Path WaveCount",
            "SampleTextureData c EvaluatorData GradientData[]:Vec4 ColorIntensityMax:Vec3 ColorIntensityMin:Vec3 TextureDimensions:Vec2 Texture:TextureAsset TextureOriginU TextureOriginV",
            "SamplerGroup c SoundNodePortGroup Wave:SoundNodePort Random RandomOnDemandVariationCount ZeroLatency Offset:SoundNodePort Delay:SoundNodePort Pitch:SoundNodePort Amplitude:SoundNodePort Trigger:SoundNodePort Release:SoundNodePort Output:SoundNodePort Finished:SoundNodePort WaveMap[]:SamplerWaveMapEntry ChannelCount",
            "SamplerNode c SoundNode Groups[]:SamplerGroup",
            "SamplerWaveMapEntry c - Wave:WaveAsset BasePitch Loop:LoopType",
            "SaveEntityData c GameEntityData SaveScreenTexture:TextureAsset LevelName SaveFileName SaveNameSID",
            "ScaleClampNode c SoundNode In:SoundNodePort InMin:SoundNodePort InMax:SoundNodePort OutMin:SoundNodePort OutMax:SoundNodePort Out:SoundNodePort",
            "SceneData c WorldData InputController:SceneObjectData CameraController:SceneObjectData Settings:SceneSettings StaticCullTree:StaticCullTreeData",
            "SceneNode c VoiceOverChildNode Children[]:VoiceOverChildNode",
            "SceneObjectData c EntityData Visible",
            "SceneSettings c - FieldOfView NearPlane ViewDistance CameraTranslationScale CameraForceHeight CameraForceHeightEnable LockCamera ResetLockedCamera UseCamera CameraTransform:LinearTransform MouseSensitivityY MouseSensitivityX EmitterSystemMetrics:EmitterSystemMetrics RenderSettings:WorldRenderSettings",
            "ScopeFilterData c Asset ScissorRegion:Vec4",
            "ScorePartComponentData c PartComponentData Score ScoreString",
            "ScoringAction e - ScoringAction_CaptureCapturePoint ScoringAction_Create ScoringAction_Damage ScoringAction_DamageAssist ScoringAction_Destroy ScoringAction_DestroyHeadshot ScoringAction_DestroyRoadKill ScoringAction_NeutralizeCapturePoint ScoringAction_Repair ScoringAction_Resupply ScoringAction_Spot ScoringAction_Suppress ScoringAction_LastScoringActionItem",
            "ScoringBucket e - ScoringBucket_General ScoringBucket_Teamwork ScoringBucket_Bonus ScoringBucket_Squad ScoringBucket_Objective ScoringBucket_LastScoringBucketItem",
            "ScoringData c Asset ScoreTypes[]:ScoringTypeData CsvFile",
            "ScoringRelationship e - ScoringRelationship_Any ScoringRelationship_Friendly ScoringRelationship_Enemies",
            "ScoringTypeData c Asset Subject:StatsCategoryData Tool:StatsCategoryData Action:ScoringAction Object:StatsCategoryData Relationship:ScoringRelationship DescriptionSid ScoringBucket:ScoringBucket CsvIdentifier Score PercentageLimit NumberOfTicks TickInterval TickScoreValue",
            "ScreenFadeEntityData c GameEntityData FadeTime ControlledByClient WaitOnBlackForStreaming",
            "SearchAndDestroyOrderEntityData c OrderEntityData TargetBotSoldier[]:SoldierSpawnBaseEntityData Area[]:GeometryEntityData TargetPosKnowledge:TargetPosKnowledgeType FindTargetTime TargetAreaFollowsTarget",
            "SelectorByPoseNodeData c BaseNodeData Idle:AnimationNodeData Transition:AnimationNodeData BlendNodes LegPositionsIdle[]:Vec3 LegPositionsTransition[]:Vec3",
            "SelectorNodeData c BaseNodeData AlwaysNodes[]:AlwaysNodeGroupData BoolNodes[]:BoolNodeGroupData FloatNodes[]:FloatNodeGroupData IntNodes[]:IntNodeGroupData",
            "SensingConstantData c - SensingTimeSpan FireKeepTime AttackerTrackTime LineOfSightTestTime MaxRays VisionBoundingBoxSize VisionHumanBoxOffset MaximumMergeDistance CloseDistance CloseFireImpactDistance CloseExplosiveImpactDistance CloseShellDistance VolumeModifier MovementModifier LuckModifier SmokeSphereRadius SmokeSphereDuration",
            "SensingTemplateData c Asset AwareForgetTime LostForgetTime TimeUntilUnseenIsLost HearingDistance ViewAngle PeripheralViewAngle SeeingDistance ReadyFOVMultiplier CombatFOVMultiplier SensingSkill",
            "SensitivityAtVelocity s - SteeringSensitivity Velocity",
            "SequenceData c LogicEntityData Targets[] PropertyTracks[]:PropertyTrackData Events[]:SequenceEventData SequenceLength Looping",
            "SequenceEventData s - Target Event:EventSpec Time SortKey",
            "SequenceGeneratorNode c SoundNode Trigger:SoundNodePort Reset:SoundNodePort Min:SoundNodePort Max:SoundNodePort Step:SoundNodePort Value:SoundNodePort",
            "ServerConnectTriggerEntityData c GameEntityData",
            "ServerSettings c - IsFastStartupEnabled IsRenderPhysicsWater IsWaterPhysicsEnabled IsGodMode IsRenderDamageEvents HasUnlimitedAmmo HasUnlimitedMags RespawnOnDeathPosition IsStatsEnabled IsNetworkStatsEnabled IsAiEnabled IsDestructionEnabled IsTreeDestructionEnabled IsSoldierAnimationEnabled IsSoldierDetailedCollisionEnabled IsFragalyzerLogEnabled IsFragalyzerLocalLogEnabled IsStaticModelsIterable Level LoadSavePoint SavePoint DisableCutscenes HavokVisualDebugger HavokCaptureToFile ShowTriggerDebugText TimeoutGame TimeoutTime DifficultyMode PlayerCountNeededForMultiplayer AILooksIntoCamera AllowDestructionOutsideCombatArea IsSpotWhenZoomedEnabled IsInternetSimulationEnabled MinLatency MaxLatency PacketDrops DropSpikeChance MinDropDuration MaxDropDuration ReorderingChance DuplicationChance CorruptionChance PreloadingEnabled ThreadedLoadingEnable JobEnable DrawActivePhysicsObjects UnrestrictedUnlocks IsRanked ScoringLogEnabled LoadingTimeout IngameTimeout OutgoingFrequency IsReconfigurable Playlist ForcePlaylist DedicatedServerCpu AutoUnspawnBangers AutokillSoldiers RegulatedAIThrottle DisableTelemetry EnableMixedPlay",
            "ShaderAsset c Editable",
            "ShaderColorizeMode e - ScmNone ScmDrawBlock ScmSurfaceShader ScmVertexShader ScmPixelShader",
            "ShaderContainerAsset c Asset VertexShaders[]:ShaderContainerElement PixelShaders[]:ShaderContainerElement SourceFile Source",
            "ShaderContainerElement s - Name Ps3ExtraCmdLine",
            "ShaderDatabaseAsset c Asset LevelName",
            "ShaderDatabaseCollection c Asset Groups[]:ShaderDatabaseGroup",
            "ShaderDatabaseGroup s - Name Path:ShaderRenderPath",
            "ShaderGeometrySpace e - SgsObject SgsWorld SgsScreen",
            "ShaderInstancingMethod e - ShaderInstancingMethod_None ShaderInstancingMethod_WorldTransform4x3 ShaderInstancingMethod_Ps3SpuWorldTransform4x3",
            "ShaderRenderMode e - SrmNormal SrmShadowbuffer SrmShadowbufferZTrans SrmShadowmap SrmPointShadowbuffer SrmZOnly SrmVelocity SrmSimple SrmSimpleLighting SrmOverdraw SrmShowOpacity SrmShowVertexNormal SrmShowVertexTangent",
            "ShaderRenderPath e - SrpAll SrpDx9Sm2bAti SrpDx9Sm3Ati SrpDx9Sm3Nv SrpDx10 SrpXenon SrpPs3",
            "ShaderShadowmapMethod e - ShaderShadowmapMethod_None ShaderShadowmapMethod_ManualPcf ShaderShadowmapMethod_HardwarePcf ShaderShadowmapMethod_CascadedBox3 ShaderShadowmapMethod_CascadedBox4 ShaderShadowmapMethodCount ShaderShadowmapMethodBitCount",
            "ShaderShadowmapQuality e - ShaderShadowmapQuality_Pcf2x2 ShaderShadowmapQuality_Pcf4x4 ShaderShadowmapQualityCount",
            "ShaderSkinningMethod e - SsmNone Ssm1Bone Ssm2Bone Ssm3Bone Ssm4Bone Ssm8Bone SsmNull",
            "ShaderSystemSettings c - Path:ShaderRenderPath FrameMemoryBufferSize FlushEnable DrawZOnly DrawOpaque DrawTransparent DrawTransparentDecal DrawTransparentDepthWrite DrawOffset ColorizeMode:ShaderColorizeMode StreamingSimpleShadingEnable AlphaTestEnable AlphaBlendEnable AlphaToMaskEnable ForceAlphaTest ForceAlphaToMask ForceDoubleSided ForcePointFiltering ForceAlphaRef ZeroViewportEnable DepthEnable DepthWriteEnable DepthTestEnable ClipPlanesEnable MipmapBias MaxAnisotropy ZOnlyMaxAnisotropy XenonTrilinearThreshold SinglePrimitiveEnable DrawCallEnable DrawCallMultiplier UseHasZPass XenonZPassEnable XenonDirectConstantEnable XenonGprChangeEnable PixBlockEventsEnable GcmReplayMarkersEnable SortBlocksEnable SimpleTexturesEnable SimpleTextureFilteringEnable DebugNonFiniteColor:Vec3 LogEnable SingleFrameLogEnable SingleFrameBlockLogEnable ShaderPixScopeEnable OverdrawMaxLayerCount SimplePixelShaderEnable SimpleVertexShaderEnable PseudoInstancingEnable StatesEnable PixelShaderSrgbConversionEnable BoneConstantOptimizationEnable SortJobEnable SortSpuJobEnable PixelConstantsEnable VertexConstantsEnable PixelConstantRedundancyCheckingEnable VertexConstantRedundancyCheckingEnable DispatchExecuteEnable XenonDispatchJobEnable XenonDispatchJobCount XenonDispatchBlocksPerJob XenonDispatchMinJobBlockCount XenonDispatchOnlyJob XenonDispatchBlockOnCmdBufEnable XenonDispatchCmdBufferSegmentSize XenonDispatchCmdBufferSize Ps3PixelShaderRedundancyCheckingEnable Ps3PixelShaderHashStoreEnable Ps3GeometryJobEnable Ps3GeometryJobCount Ps3GeometryJobSpuCount Ps3GeometryJobWithoutDispatchJobEnable Ps3GeometryDebugRenderEnable Ps3DrawEdgeGeometryEnable Ps3DrawNonEdgeGeometryEnable Ps3TriangleCullEnable Ps3TriangleFrustumCullEnable Ps3TriangleBackfaceCullEnable Ps3TriangleCullZPassEnable Ps3TriangleCullDoubleSidedEnable Ps3TriangleCull1BoneSkinningEnable Ps3TriangleCull4BoneSkinningEnable Ps3TrianglePartCullEnable Ps3DrawBlockCullEnable Ps3PartitionFrustumCullEnable Ps3PartitionClipPlaneCullEnable Ps3PartitionVolumeCullEnable Ps3SpuInstancingEnable Ps3SpuClipPlaneRejectEnable Ps3SpuCullVolumesEnable Ps3DispatchJobEnable Ps3DispatchJobCount Ps3DispatchJobSpuCount Ps3DispatchOnlyJob Ps3DispatchBlocksPerJob Ps3DispatchMinJobBlockCount Ps3DispatchSpuBufferLinkingEnable Ps3DispatchFrameBlockEnable Ps3DispatchFrameGpuBlockEnable Ps3SCullEnable Ps3ZCullEnable Ps3ZCullInFrontLimit Ps3ZCullBehindLimit Ps3MinMipmap Ps3MaxMipmap Ps3TxpDemotionEnable Ps3CmdBufVertexShaderInlineEnable Ps3CmdBufTexturesEnable Ps3CmdBufTexturesInlineEnable Ps3CmdBufClearEnable Ps3FlushEnable Ps3FinishEnable",
            "ShapeType e - STBox STSphere STCapsule STCapsuleX STCapsuleY STCapsuleZ STCylinder STCylinderX STCylinderY STCylinderZ ST_SIZE",
            "SharedSoldierAnimationTreeData c - AnimTrees[]:AnimTreeDataAsset Skeleton:SkeletonAsset BoneCollision:SoldierBoneCollisionData LocalAnimations:IndexedAnimationData AIStateAnimation:AIStateAnimationData RagdollSettings:AnimatedSoldierRagdollSettingsData UpperBodyRotation:SoldierBodyRotationData WeaponBoneName HeadBoneName LowestBoneName HipBoneName CameraBoneName CameraFovBoneName EyesBoneName NeckBoneName OneClimbCycleDistance MinMoveSpeed MovementCycleScaleFactor",
            "ShootAtObjectOrderEntityData c OrderEntityData TargetGroup:TargetObjectDataGroup",
            "ShotConfigData s - InitialPosition:Vec3 InitialDirection:Vec3 InitialSpeed:Vec3 InheritWeaponSpeedAmount MuzzleExplosion:ExplosionEntityData ProjectileData:ProjectileEntityData SpawnDelay NumberOfBulletsPerShell NumberOfBulletsPerShot RelativeTargetAiming ForceSpawnToCamera ActiveForceSpawnToCamera MaxProjectilesInWorld",
            "SimpleCarNode c SoundNode Start:WaveAsset Stop:WaveAsset Idle:WaveAsset Drive:WaveAsset Velocity:SoundNodePort InDriverPos:SoundNodePort Tilt:SoundNodePort ReleaseEvent:SoundNodePort StartOut:SoundNodePort StopOut:SoundNodePort IdleOut:SoundNodePort DriveOut:SoundNodePort Drive_Tilt_Pitch:Vec4 Drive_Vel_Pitch:Vec4 Idle_Vel_Pitch:Vec4 Idle_Drive_Vel_Fade:Vec2",
            "SimpleTransformNode c SoundNode X:SoundNodePort Y:SoundNodePort Z:SoundNodePort Operation:MathOp",
            "SkeletonAsset c BaseSkeletonAsset ExportBonesSelection:InfluenceMapData IkInfo:SoldierIKData PropertiesRootBoneName AimingProperty:AnimatedPropertyData ProceduralLeaningInfo:SoldierProceduralLeaningData MinMainBonesUpdateTime MaxMainBonesUpdateTime MainChildBones[] MinBoneName LODErrorFactors:QualityMapData AccumulateMotion MinBoneIndex MaxMainBoneIndex",
            "SkeletonPoseData s - Name Translation:Vec3 Rotation:Vec3",
            "SkinnedMeshAsset c MeshAsset SkinDecals",
            "SkinnedMeshComponentData c ComponentData BoneFakePhysics[]:BoneFakePhysicsData Mesh:SkinnedMeshAsset Skeleton:SkeletonAsset",
            "SlidingStateData c CharacterStateData HorizontalInputScale GravityScale",
            "SniperLensScopeFilterData c ScopeFilterData BlurScale BlurCenter:Vec2 ChromaticAberrationColor1:Vec3 ChromaticAberrationColor2:Vec3 ChromaticAberrationStrengths:Vec2 ChromaticAberrationDisplacement1:Vec2 ChromaticAberrationDisplacement2:Vec2 RadialBlendDistanceCoefficients:Vec2 ColorTintData:ColorTintData",
            "SoldierAimingConstraintsData c - SwimmingConstraints:AimingConstraintsData",
            "SoldierAimingSimulationData c - ZoomLevels[]:ZoomLevelData ZoomType:ZoomTypeEnum HaloAimer:HaloAimerData StandPose:AimingPoseData CrouchPose:AimingPoseData PronePose:AimingPoseData AimingRange AimerType",
            "SoldierAnimationTreeData c - FacialAnimations:IndexedAnimationData FacialSoundMap[]:FacialSoundMapData KitJointsAnimTree:AnimTreeDataAsset Shared:SharedSoldierAnimationTreeData",
            "SoldierAsset c ObjectBlueprint",
            "SoldierAutoAimData c - Poses[]:AutoAimData",
            "SoldierBodyRotationData s - MaxAngle MaxAngleRelaxed ReturnOnIdleTime ReturnOnIdleThreshold ReturnSpeed ReturnSpeedRelaxed UseUpperBodyRotation",
            "SoldierBoneCollisionData c - BoneCollisionData[]:BoneCollisionData",
            "SoldierControlConstantData c - MaximumHeadTurnAngle RelaxedTurningSpeed SearchHeadTurningSpeed SlowHeadTurningSpeed NormalHeadTurningSpeed FastHeadTurningSpeed NormalTurningSpeed CrouchTurningSpeed Acceleration MoveAngularAcceleration MoveAngularSpeedFactor AimMinPrecisionTolerance PitchSpeed SlowSpeed NormalSpeed FastSpeed CrouchSpeed CatchupSpeed AimForwardSpeed AimStrafeSpeed MaxForwardDir MaxStrafeDir",
            "SoldierEntityData c ControllableEntityData FootMaterial HeadMaterial Weapons[]:SoldierWeaponAsset WeaponUnlocks[]:SoldierWeaponUnlockData UnlockableWeapons[]:UnlockableSoldierWeaponData WeaponDataOverride:SoldierWeaponOverrideData MeleeWeaponIndex BoneFakePhysics[]:BoneFakePhysicsData Cameras[]:TargetCameraData AutoAim:SoldierAutoAimData AimingConstraints:SoldierAimingConstraintsData HeadCollision:SoldierHeadCollisionData RagdollSettings:SoldierRagdollSettingsData CharacterPhysics:CharacterPhysicsData SprintSettings:SoldierSprintSettingsData AnimTree1p:SoldierAnimationTreeData AnimTree3p:SoldierAnimationTreeData Meshes1p[]:SoldierMeshData Meshes3p[]:SoldierMeshData HidableMeshParts[]:HidableSoldierMeshWeaponPart ParachuteData:SoldierParachuteData DeathPickup:PickupEntityData Sound:SoldierSoundData StreamGroup1p:StreamGroupData ImpulseReactionTime MaxHealth RegenGracePeriod RegenerateHealth ImmortalTimeAfterSpawn CameraStrafeTiltStrength CameraPitchTiltStrength CameraExplosionImpulseMultiplier WaterDamage BelowWaterDamageDelay TimeForCorpse Category:KitCategory VOTypes[]:VOPerson EnableGroundmapLighting LowerGunOnOwnTeam ProximityCheck ReplenishInVehicle CustomOptions:CustomizationOptions GearPoints[]:SoldierGearPointData",
            "SoldierEntryComponentData c EntryComponentData AimingConstraints:AimingConstraintsData AnimTree:AnimTreeDataAsset SoldierLowerBodyFixed",
            "SoldierGearData c UnlockPartData Mesh:RigidMeshAsset Priority Transform:LinearTransform",
            "SoldierGearPointData c - BoneName BoneId PossibleGear[]:SoldierGearData",
            "SoldierGripComponentData c ComponentData",
            "SoldierHeadCollisionData c - HeadRadius PoseStates[]:SoldierHeadCollisionPoseData",
            "SoldierHeadCollisionPoseData s - IdleOffset:Vec3 MovingForwardOffset:Vec3",
            "SoldierIKData c - Legs[]:LegInfoData Hands[]:HandInfoData WeaponBoneName FeetAngularCorrection FeetMinAngularY FeetMaxNegativeCorrection FeetMaxPositiveCorrection[] WeaponIkDeltaProperty:AnimatedPropertyData WeaponIkAnimationProperty:AnimatedPropertyData WeaponInfluenceMap:InfluenceMapData WeaponHandsIkInfluenceMap:InfluenceMapData HandsIkLod:LodInterval FeetIkLod:LodInterval WeaponBoneIndex WeaponHandsIkInfluenceMapEffects[]:InfluenceMapEffectData PrimaryHandLegBones[] PrimaryHandBones[] PrimaryLegBones[]",
            "SoldierMeshData s - Mesh:SkinnedMeshAsset MeshShaderSet:MeshShaderSetAsset RandomGroup",
            "SoldierOnSpottedType e - OSPlayer OSEnemyBotsOnly OSPlayerAndEnemyBots",
            "SoldierParachuteData c - ParachuteMesh:RigidMeshAsset ParachuteMeshShaderSet:MeshShaderSetAsset ParachuteOffset1p:Vec3 ParachuteOffset3p:Vec3 AimingConstraints:AimingConstraintsData ParachuteTurningSpeed MaxTiltAngle TiltSpeed MaxTiltAtSpeed RandomMaxTiltAngle RandomTiltFrequency RandomTiltAxisOffset",
            "SoldierProceduralLeaningData c - HipBoneName Lod:LodInterval HipBoneIndex PrimaryBones[]",
            "SoldierRagdollSettingsData c - StaticDeathImpulse:Vec3 StaticExplosionImpulse:Vec3 StaticBulletImpulse:Vec3 StaticCollisionImpulse:Vec3 DeathSpasms[]:RagdollImpulse Material DeathImpulseMultiplier ExplosionImpulseMultiplier CollisionImpulseMultiplier BulletImpulseMultiplier MaxImpulse HitBoneImpulseModifier",
            "SoldierRole e - SRCommander SRSquadLeader",
            "SoldierSoundData c - ClothesSound:SoundAsset SwimSound:SoundAsset ClimbSound:SoundAsset JumpSound:SoundAsset FreeFallingSound:SoundAsset ParachuteActiveSound:SoundAsset MeleeAttackSound:SoundAsset WeaponDeploySound:SoundAsset WeaponZoomSound:SoundAsset ChangePoseSound:SoundAsset BreathSound:SoundAsset DeathSound:SoundAsset BulletImpactSound:SoundAsset HealingSound:SoundAsset SprintSound:SoundAsset SprintRecoverySound:SoundAsset TimeBetweenTriggers:SoundTrigInfo FootStepAnimTrigPositions3P[]:FootStepTrigger SprintTimeForRecovery",
            "SoldierSpawnAppearanceTemplateData c Asset Body",
            "SoldierSpawnBaseEntityData c SpawnEntityData",
            "SoldierSpawnEntityData c SoldierSpawnBaseEntityData Vehicle[]:VehicleSpawnEntityData Squad[]:SquadEntityData AlternativeSpawnPoints[]:AlternateSpawnEntityData SoldierKit:SoldierAsset Template:SoldierSpawnTemplateData PlayerType:SoldierSpawnInclude SpawnToCheck VehiclePos HumanTargetPreference LodDistance LocationNameSid UseAsAlternateSpawnPoint TryToSpawnAwayFromSight SpawnSoldierFromBeginning OnSpottedType:SoldierOnSpottedType SoldierCreationPoint SoldierSquadId:SquadId",
            "SoldierSpawnGhostEntityData c SoldierSpawnBaseEntityData Soldier:SoldierSpawnEntityData",
            "SoldierSpawnInclude e - SSHumanPlayer SSAiPlayer",
            "SoldierSpawnKitTemplateData c Asset Kit MainGun",
            "SoldierSpawnTemplateData c Asset AllowedKits[]:SoldierSpawnKitTemplateData AllowedAppearances[]:SoldierSpawnAppearanceTemplateData Sensing:SensingTemplateData Behaviour:BehaviourTemplateData Rank",
            "SoldierSprintSettingsData c - Fov FovInDelay FovInTime FovOutDelay FovOutTime ThrottleInputRequired RecoverTime InterruptingActions[]:EntryInputActionEnum",
            "SoldierWeaponAsset c ObjectBlueprint",
            "SoldierWeaponData c GameObjectData WeaponClass:WeaponClassEnum WeaponStates[]:WeaponStateData WeaponFiring:WeaponFiringData AimingController:SoldierAimingSimulationData Effects:WeaponEffectsData FirstPersonCamera:FirstPersonCameraData AIData:WeaponData Hud:HudData PickupSettings:PickupSettingsData StreamGroup1p:StreamGroupData AnimBaseSet:WeaponAnimBaseSetEnum RenderFov ZoomRenderFov HideWhenOutOfAmmo AllowSwitchingToWeaponOutOfAmmo SwitchToPrimaryWhenOutOfAmmo LowerOnOwnTeam WeaponAssetName Persistence:PersistentWeapon",
            "SoldierWeaponDispersion s - JumpDispersionAngle ProneTransitionDispersionAngle MoveDispersionAngle MoveZoomedDispersionAngle DecreasePerSecond",
            "SoldierWeaponOverrideData c - Overrides[]:WeaponOverrideData",
            "SoldierWeaponUnlockData s - Weapon:SoldierWeaponAsset WeaponSlot IsSelectable IsBundledWithSelectable",
            "SootMaterialData c - MaterialMaskingThreshold NotDarkerThan SootEnabled",
            "SootTemplateAsset c Asset",
            "SootTemplateData c SootTemplateAsset Name ShaderAsset TextureAsset Radius RandomRadius Rotation RandomRotation",
            "SootType e - StDisabled StPerVertex StPerPixel",
            "SoundAreaEntityData c GameEntityData Geometry[]:AreaGeometryEntityData OuterGeometry[]:AreaGeometryEntityData Sound:SoundTemplate BigWorld[]:BigWorldSettingsAsset FadeWidth MinY Height IsUrbanArea",
            "SoundAsset c Editable",
            "SoundEntityData c EffectEntityData SoundTemplate:SoundTemplate",
            "SoundEnvironmentConstantData c - MemoryTime MinimumLoudness",
            "SoundGraphEvent c SoundGraphParameter",
            "SoundGraphParameter c - Id WireIndex",
            "SoundNode c - EffectDescCachedIndex",
            "SoundNodeConnection c - SourceNode:SoundNode TargetNode:SoundNode SourcePort:SoundNodePort TargetPort:SoundNodePort",
            "SoundNodePort c - WireIndex UnconnectedValue",
            "SoundNodePortGroup c -",
            "SoundNodePortType e - SgptAudio SgptEvent SgptParam",
            "SoundOutput c - Emitter:SoundSourceEmitter Source:SoundSource",
            "SoundSource c - Emitters[]:SoundSourceEmitter",
            "SoundSourceBypassHdrEmitter c SoundSourceEmitter",
            "SoundSourceConeEmitter c SoundSourceEmitter Position:Vec3 Direction:Vec3 InnerAngle OuterAngle OutsideVolume",
            "SoundSourceDialogEmitter c SoundSourceEmitter DialogLoudness",
            "SoundSourceEmitter c GameObjectData MinDistance DistanceSlope AttenuationType:DistanceAttenuationType Solo HFDampingDistance HFDampingObstruction HFDampingAngle Gain",
            "SoundSourceFlatEmitter c SoundSourceEmitter Angle Bleed",
            "SoundSourceLfeEmitter c SoundSourceEmitter LfeLoudness",
            "SoundState c - Name LpCutoffFrequency SendLpCutoffFrequencyToVo Duration FadeInTime FadeOutTime BypassSound:SoundTemplate FadeVolume GameMixMainFadeTime HdrSetting:HdrSetting",
            "SoundStateSettingsAsset c Asset SoundStates[]:SoundState ImpairedHearingImpulseThreshold ScreamThreshold SupressionThreshold",
            "SoundSystemSettings c - Equalizer:EqualizerSettings Compressor:CompressorSettings MasterVolume MasterLfeGain MasterDialogGain ReverbVolume MainMixVolume DistortionClipLevel PostEffectsGain",
            "SoundTemplate c SoundAsset Outputs[]:SoundOutput Nodes[]:SoundNode PublicParameters[]:SoundGraphParameter PublicEvents[]:SoundGraphEvent WireCount SoundSource:SoundSource ReceiveNode:SoundNode MaxInstanceCount Loudness AILoudness ObstructionRadius DopplerFactor LfeLoudness DialogLoudness ReverbLoudness MasterPitch MasterMinDistance MasterDistanceSlope SkipDuplicateSpawns",
            "SoundTrigInfo s - Stand Crouch Prone Swim Climb",
            "SpawnAnimationData c ProcessorData AnimationSpeed",
            "SpawnAnimationFrameData c ProcessorData AnimationFrame",
            "SpawnCamera c CameraData",
            "SpawnCompositeMeshPartsData c ProcessorData WaitingForUbbe",
            "SpawnDirectionData c ProcessorData WaitingForUbbe InheritSpeedAndDirectionFromEmitter",
            "SpawnEntityData c GameEntityData Team:TeamId Amount SpawnDelay InitializedSpawnDelay AllowMulipleSpawn Immortal",
            "SpawnPointManagerData c Asset UseAiLodSystem",
            "SpawnPositionData c ProcessorData WaitingForUbbe",
            "SpawnRateData c ProcessorData SpawnRate TrailSegmentLength DynamicFrequencyAreaClamp DistributeOverTime DistributeOverDistance",
            "SpawnRotationData c ProcessorData Rotation",
            "SpawnRotationSpeedData c ProcessorData RotationSpeed",
            "SpawnScreenWeaponData s - WeaponName Texture:TextureAsset",
            "SpawnSizeData c ProcessorData Size",
            "SpawnSpeedData c ProcessorData Speed",
            "SpawnableMeshComponentData c ComponentData Mesh:MeshAsset",
            "SpecateMode e - SmStatic SmLookAt SmFollow SmFace",
            "SpecateTarget e - StNone StAllPlayers StTeamPlayers StSquadPlayers StOwnPlayer StOwnPlayerAndRagdoll StOwnPlayerAndVehicleRagdoll StBestSquadPlayer",
            "SpecificAnimTreeData c AnimTreeDataAsset ReplacmentAnimations[]:BaseAnimationAsset AnimTree:AnimTreeData Skeleton:SkeletonAsset",
            "SpectateCameraEntityData c GameEntityData Range IsFollowingAction",
            "SpectateEntityData c GameEntityData Mode:SpecateMode Target:SpecateTarget Cameras[]:TargetCameraData SoldierCameraIndex VehicleCameraIndex",
            "SpeedEventGateEntityData c GameEntityData MinSpeed MaxSpeed",
            "SphereCollisionData s - ExtraRadius CounterNormalBrakeForceMod Enabled",
            "SphereEvaluatorData c EvaluatorData Scale:Vec3 Radius Pivot:Vec3",
            "SphereGeometryEntityData c GeometryEntityData Radius Weight",
            "SplashData s - SplashImage:TextureAsset DisplayTime FadeTime",
            "SplashFlow c Asset screens[]:SplashData Sound:WaveAsset",
            "SpottingType e - STSquadLeader STDefault STSnapshot STSquadUav STTypesCount",
            "SpringData s - Length Stiffness Damping ProgressiveStartRatio ProgressiveExponent VisualClipOffset AttachOffsetY",
            "SquadBaseEntityData c GameEntityData",
            "SquadData c - MaxPlayerCount",
            "SquadDebugMenuEntityData c GameEntityData",
            "SquadEntityData c SquadBaseEntityData Soldiers[]:SoldierSpawnEntityData Formation:FormationData SquadId BaseReadiness:ReadinessLevel SquadStartDispersed",
            "SquadGhostEntityData c SquadBaseEntityData Squad:SquadEntityData",
            "SquadId e - Squad1 Squad2 Squad3 Squad4 Squad5 Squad6 Squad7 Squad8 Squad9 Squad10 Squad11 Squad12 Squad13 Squad14 Squad15 Squad16 Squad17 Squad18 Squad19 Squad20 Squad21 Squad22 Squad23 Squad24 SquadNone SquadIdCount",
            "SquadManagerEntityData c GameEntityData",
            "StabilizerData c - PitchStrength RollStrength Advanced YawStrength AdvancedYaw VerticalVelocityStrength",
            "StatCategoriesTree c TreeBase Categories[]:StatsCategoryData ParamX[]:CriteriaData ParamY[]:CriteriaData",
            "StatEvent e - StatEvent_KillOf_X_As_Y StatEvent_KillOf_X_In_Y StatEvent_DeathAs_X_By_Y StatEvent_DeathBy_X_In_Y StatEvent_SecondsAs_X StatEvent_SecondsWith_X StatEvent_SecondsIn_X StatEvent_Destroy_X_As_Y StatEvent_Destroy_X_In_Y StatEvent_Destroy_X_With_Y StatEvent_CleanDestroy_X_In_Y StatEvent_VehicleKillAssist_X StatEvent_KillWith_X_As_Y StatEvent_KillWith_X_In_Y StatEvent_DeathWith_X_As_Y StatEvent_DeathWith_X_In_Y StatEvent_TaggedKill_X_With_Y StatEvent_Hit_X_With_Y StatEvent_Level_X_Completed_Y_Difficulty StatEvent_CleanLevel_X_Completed StatEvent_Headshot_X_With_Y StatEvent_HeadshotAs_X_With_Y StatEvent_Outcome_X_As_Y StatEvent_SelfHeal_With_X StatEvent_BestSquad_On_X StatEvent_ShotsFired_With_X StatEvent_ShotsHit_With_X StatEvent_Collectible_X StatEvent_AllInSquadGainAward_X StatEvent_AllInSquadKilled_X StatEvent_GoldNugget StatEvent_Heal StatEvent_KillAssist StatEvent_Repair StatEvent_Avenger StatEvent_Saviour StatEvent_FlagCaptured StatEvent_FlagCapturedAssist StatEvent_FirstMissionComplete StatEvent_CleanMission StatEvent_TreeDestroyed StatEvent_VehicleAirTime StatEvent_SoldierAirTime StatEvent_HighestPointInLevel StatEvent_BaseDestroyed StatEvent_LastBaseDestroyed StatEvent_CaptureCapturePoint StatEvent_DefensiveKill StatEvent_RankUp StatEvent_UniqueAward StatEvent_UniqueDogtag StatEvent_WallDestroyed StatEvent_FirstPosition StatEvent_HighCard StatEvent_Playgroup StatEvent_SquadSpawn StatEvent_Suicide StatEvent_PhotoTaken StatEvent_SquadAssist StatEvent_SquadAvenger StatEvent_SquadHeal StatEvent_TwoPair StatEvent_ThreeOfAKind StatEvent_FiveOfAKind StatEvent_FullHouse StatEvent_SnakeEyes StatEvent_CabDriver StatEvent_Strike StatEvent_LAST_ITEM",
            "StatEventTriggerEntityData c GameEntityData StatEvent:StatEvent",
            "StateContainer c - Entries[]:StateContainerEntry",
            "StateContainerEntry s - Instance Identifier",
            "StaticCameraData c TargetCameraData LeftCurve[]:Vec3 RightCurve[]:Vec3 UpCurve[]:Vec3 DownCurve[]:Vec3 ImpulseSettings:CameraImpulseShakeData LoosePartPhysicsArray[]:LoosePartPhysicsData LeftYawAngle RightYawAngle UpPitchAngle DownPitchAngle AverageFilterFrames AccumulateInput YawSensitivityNonZoomed YawSensitivityZoomed PitchSensitivityNonZoomed PitchSensitivityZoomed MirrorCurve",
            "StaticCloudLayerData c Asset SimpleCloudPlane:StaticCloudPlaneLayerData VolumetricCloudPlane:StaticCloudVolumeLayerData",
            "StaticCloudPlaneLayerData s - Alphamap:TextureAsset",
            "StaticCloudVolumeLayerData s - Alphamap:TextureAsset",
            "StaticCullNode s - Right Max SphereKeyX SphereKeyY SphereKeyZ SphereKeyW",
            "StaticCullSettings c - Enable MergePassCount UseOldCullAgainstOcclusion",
            "StaticCullTreeData c - Nodes[]:StaticCullNode EnumOffset",
            "StaticEnvmapEntityData c EntityData StaticEnvmap:TextureAsset InfluenceRadiusInner InfluenceRadiusOuter",
            "StaticFogData c Asset FunctionType:FogFunctionType",
            "StaticModelEntityData c GamePhysicsEntityData ObjectSets[]:ObjectSet PartLinks[]:PartLinkData TreeBanger:TreeBangerEntityData Mesh:MeshAsset ConstructiblePreviewData:ConstructiblePreviewData MeshShaderSet:MeshShaderSetAsset PhysicsMaterialSet:PhysicsMaterialSetAsset LightMapEnable IrradianceVolumeReceiver Skeleton:SkeletonAsset StartAnimationOnCreation AnimTree:AnimTreeDataAsset LoopAnimation AnimationCullDistance RootBone EdgeModels:EdgeModelInstances EdgeModelLodBox:BoundingBoxAsset CanServeAsConstructibleGround SkinnedMeshTransforms[]:LinearTransform",
            "StaticModelGroupEntityData c GroupEntityData",
            "StaticModelGroupMemberData c GroupMemberData MeshEntityType:EntityData FirstNetworkId NetworkIdCount NetworkIdCountPerInstance FirstPartHealthStateNetworkIds[] FirstPartHealthStateIndices[]",
            "StaticModelPartToBangerParts s - BangerPartIndices[]",
            "StaticModelTriggerEntityData c TriggerEventEntityData StaticModel:ReferenceObjectData PartName PartCountInPerCent PartCount UseCurrentPartDestroyedOnEnable",
            "StaticOutdoorLightData c Asset LightingModel:LightingModelType",
            "StaticPositionEntityData c GameEntityData",
            "StaticSkyData c Asset",
            "StaticSunData c Asset",
            "StatsCategoryData c TreeNodeBase Categories[]:StatsCategoryData Members[] CheckedMembers[]:StatsCategoryReferencedData Code",
            "StatsCategoryReferencedData c -",
            "StatsCategoryReferencedObjectData c StatsCategoryReferencedData Object:GameObjectData ObjectClassName ObjectInstanceGuid",
            "StatsCategoryReferencedSoldierKitData c StatsCategoryReferencedData KitCategory:KitCategory",
            "StatsCategoryReferencedSoldierRoleData c StatsCategoryReferencedData Role:SoldierRole",
            "StencilMaskState e - SmsNone SmsWrite SmsRead",
            "StrafePreprocessorData c BasePreprocessorData RightFrontAngle RightBackAngle LeftFrontAngle LeftBackAngle MovementAngleParameter:FloatParameter FrontParameter:BoolParameter",
            "StreamGroupData c - Assets[]:Asset",
            "StreamPool c Editable StreamPoolName StreamBufferSizeXenon StreamBufferSizePs3 StreamBufferSizeWin32 StreamCountXenon StreamCountPs3 StreamCountWin32",
            "StreamPools c Editable StreamPool[]:StreamPool",
            "StrengthType e - Infantry LightArmour HeavyArmour Helicopter NumberOfStrengthTypes",
            "Strengths s - Infantry LightArmour HeavyArmour Helicopter",
            "StringSelectorNodeData c BaseNodeData NodesInfoMap[]:StringSelectorNodeGroupData RealNodeData[]:BaseNodeData TemplateNodeData:BaseNodeData SelectionParameter:IntParameter FolderSubstringTemplate",
            "StringSelectorNodeGroupData c AnimNodePortGroupData SelectionIndex FolderSubstring",
            "StringSetting s - Name Value",
            "SubsystemTimingConfigData s - Pathfinding Traversals AreaDistribution DynamicObjectEnvironment Sensing Evaluation GoalUpdates BehaviourSelection BehaviourExecution SquadUpdate",
            "SubtitleDataNode c TreeNodeBase Time DisplayTime SID1 SID2",
            "SubtitleDataTree c TreeBase Subtitles[]:SubtitleMainDataNode",
            "SubtitleMainDataNode c TreeNodeBase Language:LanguageFormat Subtitles[]:SubtitleDataNode",
            "SweepSelectionType e - SstTargetLost SstFirstTime",
            "SwimmingStateData c CharacterStateData BodyUnderWater",
            "TacticalObjectiveEntityData c GameEntityData MissionSid BriefingSidTeam1 BriefingSidTeam2 Team1Timer Team2Timer",
            "TankControlConstantData c - YawStrength SmoothTurnSpeedAngle SmoothTurnSpeedLimit TurnSpeedLimit",
            "TankNode c SoundNode Start:WaveAsset Stop:WaveAsset Idle:WaveAsset Loaded:WaveAsset UnLoaded:WaveAsset Interior:WaveAsset InteriorStrain:WaveAsset TracksSlow:WaveAsset TracksFast:WaveAsset PipeAttacks[]:WaveAsset IdleOut:SoundNodePort StartOut:SoundNodePort StopOut:SoundNodePort LoadedOut:SoundNodePort UnLoadedOut:SoundNodePort InteriorOut:SoundNodePort InteriorStrainOut:SoundNodePort TracksFastOut:SoundNodePort TracksSlowOut:SoundNodePort PipeAttackOut:SoundNodePort Throttle:SoundNodePort RPM:SoundNodePort Velocity:SoundNodePort AngularVelocity:SoundNodePort InDriverPos:SoundNodePort InteriorCam:SoundNodePort ReleaseEvent:SoundNodePort Idle_TheRest_Vel_Fade:Vec2 Idle_RPM_Pitch:Vec4 Load_UnLoad_Throttle_Fade:Vec2 Load_UnLoad_Vel_Pitch:Vec4 Load_UnLoad_RPM_Pitch:Vec4 Int_IntStrain_Vel_Fade:Vec2 IntStrain_Vel_Pitch:Vec4 TrackSlow_Fast_Vel_Fade:Vec2 TrackSlow_Fast_Vel_Pitch:Vec4 AngularVelocityToVelocityScale AngularVelocityToThrottleScale",
            "TargetCameraData c CameraData Hud:HudData CriticalHudFx:HudFxData",
            "TargetEntityData c GamePhysicsEntityData",
            "TargetEvaluationConstantData c - KeepTargetTime MaxBattleStrength UpdateInterval",
            "TargetObjectData c LogicEntityData StrengthType:StrengthType SequenceNumber Area:GeometryEntityData SpawnEntity:SpawnEntityData",
            "TargetObjectDataGroup c LogicEntityData TargetObjects[]:TargetObjectData",
            "TargetOrderEntityData c OrderEntityData Target[]:SoldierSpawnBaseEntityData",
            "TargetPosKnowledgeType e - TargetPosKnown TargetPosUnknown TargetPosFind",
            "TeamAsset c Asset",
            "TeamData c TeamAsset Kits[]:SoldierAsset Faction:FactionId MaxPlayerCount MaxPlayersPerSquad",
            "TeamEntityData c GameEntityData Order_AttackDefend:VoiceOverEntityData Order_Go:VoiceOverEntityData Order_NeedBackup:VoiceOverEntityData Order_NeedPickup:VoiceOverEntityData Order_NeedAmmo:VoiceOverEntityData Order_NeedMedic:VoiceOverEntityData Order_FollowMe:VoiceOverEntityData Team:TeamData Id:TeamId SquadSpawnCamera:ChaseCameraData",
            "TeamFilterEntityData c GameEntityData AllowTeam:TeamId",
            "TeamId e - TeamNeutral Team1 Team2 Team3 Team4 Team5 Team6 Team7 Team8 Team9 Team10 Team11 Team12 Team13 Team14 Team15 Team16 TeamIdCount",
            "TeamVehicleAsset s - Vehicle:VehicleAsset Team:TeamId",
            "TeamVehicleProjectileEntityData c ProjectileEntityData Vehicles[]:VehicleAsset EnterDelay ExitAfterFireDelay OnProjectileDestroyedUnspawnVehicle",
            "TerrainAsset c Asset",
            "TerrainData c TerrainAsset Heightfields[]:TerrainHeightfieldData TerrainSplines[]:TerrainSplineData ConvertedTerrainSplines[]:TerrainSplineData MaterialMap:TerrainMaterialMapAsset Water:WaterData RootDecal:TerrainGroupDecal MaskTileSize MaterialMaskStepSize DefaultFullDepthEnable",
            "TerrainDecal c - Material:TerrainDecalMaterial Name Enable",
            "TerrainDecalData c -",
            "TerrainDecalMaterial c Asset Enable BaseMaterialEnable OpaqueMaskEnable UndergrowthEnable UndergrowthTypes[]:TerrainUndergrowthType Shader:ShaderAsset ZOnlyShader:ShaderAsset DebugColor:Vec3 MaskFunctions[]:TerrainMaskFunction",
            "TerrainDecalTexCoordParams s - TileIndexX TileIndexY TileCountX TileCountY FlipX FlipY",
            "TerrainEntityData c GamePhysicsEntityData TerrainAsset:TerrainData OutsideTerrainMaterial",
            "TerrainFullDecal c TerrainDecal MaskTextures[]:TerrainMaskTexture MaskTextureType:TerrainMaskTextureType MaskPixelsPerMeter GridCellSize GridCellResolution",
            "TerrainGroupDecal c TerrainDecal Decals[]:TerrainDecal",
            "TerrainHeightfieldData c - Files[] FileGrid[]:TerrainHeightfieldGridCellInfo DepthFiles[] Resolution InnerResolution SizeXZ SizeY",
            "TerrainHeightfieldGridCellInfo c - File TopLeftCorner:Vec3 BottomRightCorner:Vec3",
            "TerrainLayerData c -",
            "TerrainMaskFunction s - MinAltitude MaxAltitude MinSlope MaxSlope",
            "TerrainMaskTexture s - PosMin:Vec2 PosMax:Vec2 File Enable",
            "TerrainMaskTextureType e - TmttNone TmttStandard TmttArea",
            "TerrainMaterialMapAsset c Asset MaterialNames[] MaterialMapFileName",
            "TerrainQuadDecal c TerrainDecal Transform:LinearTransform UserMasks:Vec4 Template:TerrainQuadDecalTemplate TexCoord:TerrainDecalTexCoordParams",
            "TerrainQuadDecalConnection s - Point1:Vec2 Point2:Vec2",
            "TerrainQuadDecalTemplate c Asset Connections[]:TerrainQuadDecalConnection TexCoord:TerrainDecalTexCoordParams",
            "TerrainQuadGeometryData c -",
            "TerrainSplineData c GameObjectData Points[]:TerrainSplinePointData Planes[]:TerrainSplinePlaneData AttachedStart:TerrainSplinePointData AttachedEnd:TerrainSplinePointData InnerSpline:TerrainSplineData",
            "TerrainSplineDecal c TerrainDecal Spline:TerrainSplineData InnerSpline:TerrainSplineData UseInnerSpline",
            "TerrainSplinePlaneData c - DefaultUserMasksLeft:Vec4 DefaultUserMasksRight:Vec4 Points[]:TerrainSplinePlanePointData Parent:TerrainSplineData PlaneType:TerrainSplinePlaneType StartIndex EndIndex DefaultSegments DefaultAlpha DefaultLeftEdge DefaultRightEdge DefaultTextureStretch DefaultLift DefaultFoamLeft DefaultFoamRight DefaultWaveHeightLeft DefaultWaveHeightRight BaseSpeed NoiseWidth FilledSplineRepeatX FilledSplineRepeatY FilledSplineRotation",
            "TerrainSplinePlanePointData c - UserMasks:Vec4 UserMasksLeft:Vec4 UserMasksRight:Vec4 AffectedPoint:TerrainSplinePointData Segments LeftEdge RightEdge WaterSpeed",
            "TerrainSplinePlaneType e - Road River FillConvex FilledInner Lake",
            "TerrainSplinePointData c - Position:Vec3 Parent:TerrainSplineData",
            "TerrainUndergrowthType c - MinScale:Vec2 MaxScale:Vec2 MinMaskScaleFactor:Vec2 Mesh:RigidMeshAsset MeshShaderSet:MeshShaderSetAsset CacheShader:ShaderAsset Density Variation MinMask MaxMask WindScale ViewDistance Enable RotateEnable SkewEnable",
            "TerrainViewMode e - TvmDefault TvmSimple TvmMaterialCount TvmMaterialDebugColor TvmMaterialCombo TvmMaskCount TvmOverdrawMask TvmOverdrawCoverage",
            "TestEntityData c GameEntityData",
            "TextureAsset c TextureBaseAsset",
            "TextureAssetGamma e - TagDefault TagLinear TagSrgb",
            "TextureBaseAsset c Asset",
            "TextureOutputFormat e - TofDefault TofDXT1 TofDXT3 TofDXT5 TofRGB565 TofARGB1555 TofARGB4444 TofARGB8888 TofA8 TofL8 TofL16 TofABGR16 TofABGR16F TofABGR32F TofR16F TofR32F TofDXN TofNormalmapDXT TofNormalmapDXT1 TofNormalmapDXT5RGA TofRGBE_8888 TofDXT5A TofGR16",
            "TextureUnlockPartCollection c Asset ShaderNodeName TextureFormat:TextureOutputFormat TextureUsage:TextureUsage TextureUnlockParts[]:TextureUnlockPartData",
            "TextureUnlockPartData c UnlockPartData Texture:TextureAsset",
            "TextureUsage e - TuDefault TuTangentSpaceNormalmap TuObjectSpaceNormalmap TuWorldSpaceNormalmap",
            "TicketCountPercentage s - MaxPercentOfTicketsLeft TicketLossPerMin VOEvent:VOEventType",
            "TicketCounterEntityData c GameEntityData TeamId:TeamId TicketLossWhenLostAllBases TicketLossPerMin TicketPercentages[]:TicketCountPercentage TicketLossPerMinPointCountScale TicketLossStart InitialTicketCount SetBestSquadSpawner HaltTicketLossOnEqualPointCount",
            "TimeControllerNodeData c BaseNodeData Animation:BaseNodeData Parameter:FloatParameter MinValue MaxValue",
            "TimePair s - StartTime StopTime",
            "TimeSynchronizationType e - TST_SYNCH TST_TRANSLATION TST_ROTATION",
            "TimeSynchronizerNodeData c BaseNodeData Node:BaseNodeData SpeedParameter:FloatParameter SynchType:TimeSynchronizationType IsCyclic:TripleBool PlayFromBegining:TripleBool NormalizedTimeParameter:FloatParameter",
            "TimeSynchronizerToParentNodeData c BaseNodeData Node:BaseNodeData",
            "TimingConstantData c - UpdateTimePerFrame SubsystemTimeParts:SubsystemTimingConfigData PriorityClasses[]:BotPriorityConfigData",
            "TooltipEntityData c GameEntityData EntryInputAction:EntryInputActionEnum TooltipSid",
            "TracerDartEntityData c MeshProjectileEntityData TransmissionEffect:EffectAsset Gravity",
            "TrackComponentData c SkinnedMeshComponentData ScrollFactor",
            "TrackWheelComponentData c WheelComponentData BoneName",
            "TransitionInfoData c AnimNodePortGroupData Transition:BaseNodeData FromValue ToValue",
            "TransitionNodeData c BaseNodeData Transitions[]:TransitionInfoData ElseNode:BaseNodeData PoseSelectionParameter:IntParameter PoseAfterTransitionParameter:IntParameter DisableTransitionsParameter:BoolParameter",
            "TraversalConfigData c - Name TimePart MaxDistance",
            "TraversalConstantData c - BehaviourTestTraversalConstants:TraversalConfigData CoverSearchTraversalConstants:TraversalConfigData EnvironmentTraversalConstants:TraversalConfigData FrontLineTraversalConstants:TraversalConfigData",
            "TreeBangerEntityData c BangerEntityBaseData PartLinks[]:PartLinkData SpawnEffects[]:EffectAsset",
            "TreeBase c Asset",
            "TreeHealthStateData c HealthStateData BranchEffects[]:EffectAsset ShakeEffects[]:EffectAsset",
            "TreeMemberData c GroupMemberData MeshEntityType:TreeMeshEntityData PartsToBangerParts[]:StaticModelPartToBangerParts",
            "TreeMeshAsset c CompositeMeshAsset BillboardShader:ShaderAsset BillboardResolutionX BillboardResolutionY Bendable AffectedByWind BranchesAffectedByWind LodScale CutMaterialCount",
            "TreeMeshDummyData c -",
            "TreeMeshEntityData c EntityData Mesh:TreeMeshAsset EnableLeaves",
            "TreeNodeBase c - NodeName",
            "TrialValidatorEntityData c GameEntityData ValidationStat ValidationMaxValue TrialDisabledContent",
            "TriggerEntityData c GameEntityData Delay RunOnce",
            "TriggerEventEntityData c TriggerEntityData",
            "TripleBool e - TB_FALSE TB_TRUE TB_NONE",
            "TripleStateNodeData c BaseNodeData PreNode:BaseNodeData LoopNode:BaseNodeData PostNode:BaseNodeData ElseNode:BaseNodeData ActivateNodeParameter:BoolParameter PreNodeNormalizedBlendOutTime PostNodeNormalizedBlendOutTime ContinuousTime",
            "TurbulanceData c ProcessorData Intensity",
            "TurnEffectData s - MaxRollAngle MaxPitchAngle YawVelocityThreshhold VelocityThreshhold SafeTime BeginTime HoldTime EndTime",
            "TurnNodeData c BaseNodeData Idle:BaseNodeData TurnLeft:AnimationNodeData TurnRight:AnimationNodeData TurnSpeedParameter:FloatParameter TurnAngleParameter:FloatParameter TurnTimeParameter:FloatParameter",
            "TurretControlConstantData c - SearchLookSpeed SlowLookSpeed NormalLookSpeed FastLookSpeed",
            "TurretData c - PitchP YawP FakeShootSpaceMinDistance FakeShootSpaceMaxDistance",
            "UI3DCameraAnimation s - FromName ToName ProjectionMatrix:Mat4 Samples[]:UI3DCameraSampledFrame",
            "UI3DCameraSampledFrame s - Time Position:Vec3 Rotation:Quat",
            "UI3DGeometryAnimation s - Name Samples[]:UI3DGeometrySampledFrame MinBoundingCorner:Vec3 MaxBoundingCorner:Vec3",
            "UI3DGeometrySampledFrame s - Time Position:Vec3 Rotation:Quat Scale:Vec3",
            "UI3DSystemAsset c Asset CameraAnimations[]:UI3DCameraAnimation GeometryAnimations[]:UI3DGeometryAnimation",
            "UIAnimatedTextureData c - Groups[]:UIAnimatedTextureDataGroup",
            "UIAnimatedTextureDataGroup c - Infos[]:UIAnimatedTextureDataInfo BlinkInfos[]:UIAnimatedTextureDataInfo Color:Vec3 Icon:UIHudIcon Width Height FrameRate Rotation StickOnBorder VerticalTextAnchor:UITextAnchor HorizontalTextAnchor:UITextAnchor ScaleIconSizeDist",
            "UIAnimatedTextureDataInfo c - Name MinU MaxU MinV MaxV Delay",
            "UIAnimatedTextureFrame c - Texture Delay",
            "UIAnimatedTextureGroup c - Color:Vec3 Frames[]:UIAnimatedTextureFrame BlinkFrames[]:UIAnimatedTextureFrame Icon:UIHudIcon Width Height FrameRate Rotation StickOnBorder VerticalTextAnchor:UITextAnchor HorizontalTextAnchor:UITextAnchor ScaleIconSizeDist",
            "UIAnimatedTextureManagerAsset c Asset Groups[]:UIAnimatedTextureGroup OutputAtlasFile",
            "UIAsset c Asset SourceFile AtlasTextureFormat:TextureOutputFormat AtlasCount Config:UIConfig",
            "UIConfig c - Menu:UIMenuConfig Hud:UIHudConfig",
            "UIConstructibleEntitiesData c GameEntityData MenuObjects[]:ObjectBlueprint",
            "UIFontAsset c Asset SourceFile PointSize Style:UIFontStyle TextDatabase[]:UITextDatabase",
            "UIFontData c - Glyphs[]:UIFontGlyph Kerning[]:UIFontGlyphKerning TextureFile KerningFile Height TextureWidth TextureHeight",
            "UIFontGlyph c - Glyph X Y Width Pitch Rows MinX MaxX MinY MaxY YOffset Advance",
            "UIFontGlyphKerning c - First Second Kerning",
            "UIFontMapAsset c Asset Language:LanguageFormat Proxies[]:UIFontMapProxy",
            "UIFontMapProxy s - Language:LanguageFormat Fonts[]:UIFontProxyAsset",
            "UIFontProxyAsset c - Font:UIFontAsset FontName PointSize Style:UIFontStyle Tracking StartOffset RowOffset",
            "UIFontStyle e - UIFontStyle_Regular UIFontStyle_Bold UIFontStyle_Italic",
            "UIHudConfig c - MaxOrderIconDistance Nametags[]:UINametag NametagHealthVisibleForAll Fade3DIcons InnerNametagRadius OuterNametagRadius GrenadeDistanceThreshold TaggedVehicleCrosshair:UITaggedVehicleCrosshair Minimap:UIMinimapConfig CriticalHealthThreshold ShowMedicHealthThreshold RepairVehicleThreshold",
            "UIHudIcon e - UIHudIcon_LocalPlayer UIHudIcon_FriendlyPlayer UIHudIcon_EnemyPlayer UIHudIcon_NeutralPlayer UIHudIcon_SquadMember UIHudIcon_SquadLeader UIHudIcon_Vehicle UIHudIcon_PrimaryObjective UIHudIcon_PrimaryObjectiveBlink UIHudIcon_SecondaryObjective UIHudIcon_AreaMapMarker UIHudIcon_ObjectiveDestroy UIHudIcon_ObjectiveScout UIHudIcon_ObjectiveDefend UIHudIcon_ObjectiveGoto UIHudICon_UAV UIHudIcon_AmmoCrate UIHUDIcon_C4 UIHUDIcon_C4_small UIHUDIcon_ATMine UIHudIcon_StationaryWeapon UIHudIcon_North UIHudIcon_South UIHudIcon_West UIHudIcon_East UIHudIcon_NeutralFlag UIHudIcon_FriendlyFlag UIHudIcon_EnemyFlag UIHudIcon_NeutralFlagLit UIHudIcon_FriendlyFlagLit UIHudIcon_EnemyFlagLit UIHudIcon_SelectedSpawnPoint UIHudIcon_FriendlyFlagUnderAttack UIHudIcon_EnemyFlagUnderAttack UIHudIcon_OrderAttack UIHudIcon_OrderDefend UIHudIcon_OrderAttackSmall UIHudIcon_OrderDefendSmall UIHudIcon_BoatNeutral UIHudIcon_BoatFriendly UIHudIcon_BoatEnemy UIHudIcon_BoatSquad UIHudIcon_CarNeutral UIHudIcon_CarFriendly UIHudIcon_CarEnemy UIHudIcon_CarSquad UIHudIcon_HeliNeutral UIHudIcon_HeliFriendly UIHudIcon_HeliEnemy UIHudIcon_HeliSquad UIHudIcon_TankNeutral UIHudIcon_TankFriendly UIHudIcon_TankEnemy UIHudIcon_TankSquad UIHudIcon_StationaryNeutral UIHudIcon_StationaryFriendly UIHudIcon_StationaryEnemy UIHudIcon_StationarySquad UIHudIcon_StrategicNeutral UIHudIcon_StrategicFriendly UIHudIcon_StrategicEnemy UIHudIcon_StrategicSquad UIHudIcon_MotionRadarSweep UIHudIcon_NeedBackup UIHudIcon_NeedAmmo UIHudIcon_NeedMedic UIHudIcon_NeedPickup UIHudIcon_NeedRepair UIHudIcon_KitAssault UIHudIcon_KitDemolition UIHudIcon_KitRecon UIHudIcon_KitSpecialist UIHudIcon_KitSupport UIHudIcon_KitPickupAssault UIHudIcon_KitPickupDemolition UIHudIcon_KitPickupRecon UIHudIcon_KitPickupSpecialist UIHudIcon_KitPickupSupport UIHudIcon_Pickup UIHudIcon_TaggedVehicle UIHudIcon_HeliTargetEnemy UIHudIcon_HeliTargetFriendly UIHudIcon_ArtilleryTarget UIHudIcon_NeutralFlagAttacker UIHudIcon_FriendlyFlagAttacker UIHudIcon_EnemyFlagAttacker UIHudIcon_LaserTarget UIHudIcon_ObjectiveAttacker UIHudIcon_ObjectiveDefender UIHudIcon_HealthbarBackground UIHudIcon_Healthbar UIHudIcon_RadarSweepComponent UIHudIcon_Blank UIHudIcon_LocalPlayerBigIcon UIHudIcon_LocalPlayerOutOfMap UIHudIcon_PrimaryObjectiveLarge UIHudIcon_RpgTargetUnlocked UIHudIcon_RpgTargetLocked UIHudIcon_RpgTargetLocking UIHudIcon_ArtilleryStrikeNametag UIHudIcon_ArtilleryStrikeMinimap UIHudIcon_ConquestFlagNeutral UIHudIcon_ConquestFlagFriendly UIHudIcon_ConquestFlagEnemy UIHudIcon_ConquestFlagFriendlyUncap UIHudIcon_ConquestFlagEnemyUncap UIHudIcon_ConquestFlagFriendlySelected UIHudIcon_ConquestFlagFriendlySquadSelected UIHudIcon_ConquestFlagHotzoneSmall UIHudIcon_ConquestFlagHotzone UIHudIcon_BombRaidNeutral UIHudIcon_BombRaidFriendly UIHudIcon_BombRaidEnemy UIHudIcon_BombRaidSquad UIHudIcon_BunkerNeutral UIHudIcon_BunkerFriendly UIHudIcon_BunkerEnemy UIHudIcon_BunkerSquad UIHudIcon_Grenade UIHudIcon_GrenadeArrow UIHudIcon_FocusPointAim UIHudIcon_FocusPointLocked UIHudIcon_FocusPointAttack UIHudIcon_FocusPointDefend UIHudIcon_FlynnCrate UIHudIcon_TeamObjectMedikit UIHudIcon_TeamObjectAmmo UIHudIcon_TeamObjectDeadSoldier UIHudIcon_TeamObjectArrow UIHudIcon_RadioOrderLetMeHeal UIHudIcon_RadioOrderRequestHeal UIHudIcon_RadioOrderRequestAmmo UIHudIcon_RadioOrderLetMeGiveAmmo UIHudIcon_RadioOrderLetMeRepair UIHudIcon_RadioOrderRequestRide UIHudIcon_RadioOrderSocialise UIHudIcon_RadioOrderHighlight UIHudIcon_ConquestFlagNeutralSmall UIHudIcon_ConquestFlagFriendlySmall UIHudIcon_ConquestFlagEnemySmall UIHudIcon_ConquestFlagFriendlyUncapSmall UIHudIcon_ConquestFlagEnemyUncapSmall UIHudIcon_Count",
            "UIMenuConfig c - IntroMovies[]:UIMovieAsset BackgroundMovies[]:BinkTextureAsset BackgroundMusic:WaveAsset DefaultScene",
            "UIMinimapConfig s - CombatAreaCameraDelay CameraInterpolationTime CombatAreaFadeSpeed ZoomStates[]:UIMinimapZoomConfig CameraPanSensitivity MinVelocity MaxVelocity VelocityModifier ZoomConversion:UIMinimapZoomConversion CompassTexture:TextureAsset CombatAreaDetailTexture:TextureAsset BorderSize",
            "UIMinimapShape s - ShapeType:UIMinimapShapeType AlphaMaskTexture:TextureAsset",
            "UIMinimapShapeType e - UIMinimapShapeType_Rectangle UIMinimapShapeType_Circle UIMinimapShapeType_Count",
            "UIMinimapZoomConfig s - State:UIMinimapZoomState Data:MinimapData Shape:UIMinimapShape",
            "UIMinimapZoomConversion s - CameraMinDistance MinZoom CameraMaxDistance MaxZoom",
            "UIMinimapZoomState e - UIMinimapZoomState_Default UIMinimapZoomState_Overview UIMinimapZoomState_CombatArea UIMinimapZoomState_Mission UIMinimapZoomState_Artillery UIMinimapZoomState_Spawn UIMinimapZoomState_None",
            "UIMovieAsset c Asset Language:LanguageFormat Description Movie:UIMovieData SubtitleMovie:UIMovieData Music:WaveAsset Texture:TextureAsset OverrideBackgroundMusic Looping IsSpecialEdition",
            "UIMovieData c - Movie:BinkTextureAsset Viewport:UIMovieViewport",
            "UIMovieViewport s - X Y Width Height TargetWidth TargetHeight",
            "UINametag c - Icon:UIHudIcon IconFadeDistance IconMaxDistance TextFadeDistance TextMaxDistance HealthFadeDistance HealthMaxDistance SnapToScreenEdge TextColor:Vec3",
            "UIShaderAsset c Asset",
            "UITaggedVehicleCrosshair s - MinDistance MaxDistance MinSize MaxSize",
            "UITextAnchor e - UITextAnchor_None UITextAnchor_Left UITextAnchor_Right UITextAnchor_Top UITextAnchor_Bottom UITextAnchor_Center",
            "UITextDatabase c Asset Language:LanguageFormat BinaryFile HistogramFile",
            "UITextureAtlasData c - Infos[]:UITextureAtlasInfo",
            "UITextureAtlasInfo c - TextureFile TextureWidth TextureHeight MinU MaxU MinV MaxV",
            "UnlockCategoryLabel e - UCL_Unknown UCL_Infantry UCL_Vehicles UCL_Team UCL_Appearance UCL_Attire UCL_Recruit UCL_MachineGunner UCL_CombatMedic UCL_FieldWorks UCL_AntiTank UCL_Sniper UCL_SpecOps UCL_Rifleman",
            "UnlockComponentData c ComponentData Unlock:UnlockGraphUnlock OverrideMode:ComponentUnlockOverrideMode InverseRelation",
            "UnlockConnection c - SourceNode:UnlockGraphNode DestNode:UnlockGraphNode",
            "UnlockGraphClass c Asset Nodes[]:UnlockGraphNode Connections[]:UnlockConnection RootNode:UnlockGraphNode CsvFile",
            "UnlockGraphCompound c UnlockGraphUnlock",
            "UnlockGraphCondition c UnlockGraphNode RequiredAward RequiredRank RequiredUnlock:UnlockGraphNode",
            "UnlockGraphHeader c UnlockGraphNode",
            "UnlockGraphLabelUnlock c UnlockGraphUnlock Label:UnlockCategoryLabel",
            "UnlockGraphNode c - Name Enabled XPos YPos Parents[]:UnlockGraphNode Children[]:UnlockGraphNode",
            "UnlockGraphSubCompound c UnlockGraphUnlock",
            "UnlockGraphUnlock c UnlockGraphNode NetworkID IconID Cost CostMultiplierByLevel UniqueName Levels",
            "UnlockPartData c Asset Unlock:UnlockGraphUnlock",
            "UnlockTreeCategory c UnlockTreeNode Children[]:UnlockTreeNode CategoryLabel:UnlockCategoryLabel",
            "UnlockTreeClass c TreeBase Root[]:UnlockTreeNode StartingUnlocks[]:UnlockGraphUnlock",
            "UnlockTreeGraph c UnlockTreeNode Graph:UnlockGraphClass",
            "UnlockTreeNode c TreeNodeBase Parent:UnlockTreeCategory",
            "UnlockableSoldierWeaponData c UnlockPartData Weapon:SoldierWeaponAsset WeaponSlot",
            "UpdateAgeData c ProcessorData Lifetime",
            "UpdateAlphaLevelMaxData c ProcessorData MaxLevel",
            "UpdateAlphaLevelMinData c ProcessorData MinLevel",
            "UpdateAlphaLevelScaleData c ProcessorData Exponent",
            "UpdateBoundingBoxData c ProcessorData WaitingForUbbe",
            "UpdateClipScaleData c ProcessorData Lookup[]",
            "UpdateCollisionData c ProcessorData Penetration Elasticity",
            "UpdateColorData c ProcessorData Color:Vec3",
            "UpdateLinearVelocityData c ProcessorData WaitingForUbbe",
            "UpdateOrientationData c ProcessorData WaitingForUbbe",
            "UpdateRotationData c ProcessorData TextureInfo1:EmitterTextureInfo",
            "UpdateSizeData c ProcessorData Pivot:Vec2",
            "UpdateSizeXData c ProcessorData WaitingForUbbe",
            "UpdateSizeYData c ProcessorData WaitingForUbbe",
            "UpdateSizeZData c ProcessorData WaitingForUbbe",
            "UpdateTextureCoordsData c ProcessorData TextureInfo1:EmitterTextureInfo TextureBlendValue",
            "UpdateTransparencyData c ProcessorData CullThreshold",
            "UrgencyModifierData s - Name Modifier",
            "UrgencySetData c - Name UrgencyModifiers[]:UrgencyModifierData",
            "UrgencyUserData c - Base:UrgencyUserData UrgencyModifiers[]:UrgencyModifierData ModifierSets[]:UrgencySetData",
            "UserMusicArbiterNode c SoundNode In:SoundNodePort Out:SoundNodePort",
            "VOAsset c TreeBase Events[]:VOEvent",
            "VOConstantData c - EnemyBehindPlayerRadius EnemyBehindPlayerArc MaxEnemyBehindPlayerHeightDiff MaxPlayerHurtDistance PlayerHurtThreshold TimeBetweenLookAtPlayersMin TimeBetweenLookAtPlayersMax TimeBeforeFirstLookAtPlayerMin TimeBeforeFirstLookAtPlayerMax MinTimeAfterInterludeBeforeLookAtPlayer LoiteringWaitTime LoiteringPauseTime",
            "VOEvent c TreeNodeBase Rows[]:VORow EventType:VOEventType PauseTime",
            "VOEventType e - ETReceivedNewOrder ETRpgAimedAtPlayer ETGrenadeAimedAtPlayer ETThrowGrenade ETVehicleHealthLow ETIncomingGrenade ETSoldierFire ETPlacedExplosive ETProjectileImpact ETProjectileImpactNearSoldier ETSoldierKilled ETSaviourKill ETAvengeKill ETHeadshotKill ETRoadkill ETEnterVehicle ETVehicleKilled ETAllEnemiesDead ETSoldierReloading ETSoldierEnableParachute ETSeenExplosion ETArtilleryFired ETLookAtPlayer ETPlayerHurt ETEnemySpottedBehindPlayer ETLastManStanding ETGainedReinforcements ETCommentFightHarder ETAlertLeavingArea ETSpottedObject ETSpottedExplosionPack ETSpottedMine ETVehicleRepaired ETRequestMedic ETOrderBailOut ETRequestRepair ETKnockedOver ETMeleeAttack ETWonCapturePoint ETLostCapturePoint ETRoundWon ETRoundLost ETTicketsLow ETReachTicketPercentage ETTicketsMirror ETHomeGrief ETAwayGrief ETOrderAttackNeutral ETOrderAttackEnemy ETOrderDefend ETSoldierSpawned ETRank1 ETRank2 ETRank3 ETRank4 ETRank5 ETRank6 ETRank7 ETRank8 ETRank9 ETRank10 ETRank11 ETRank12 ETRank13 ETRank14 ETRank15 ETRank16 ETRank17 ETRank18 ETRank19 ETRank20 ETRank21 ETRank22 ETRank23 ETRank24 ETRank25 ETRank26 ETNone",
            "VOFacialAnimMapData c - WaveAssets[]:WaveAsset",
            "VOGroup c VORowData Lines[]:VOLine Chance PauseTime",
            "VOLine c TreeNodeBase TalkDialogLine:DialogLineNode ScreamDialogLine:DialogLineNode RadioDialogLine:DialogLineNode Source:VOSource SourcePerson:VOPerson UrgencyLevel Delay CanSkip",
            "VOListenerParam c VOParam Listener:VOListenerParamType",
            "VOListenerParamType e - LptInflictor LptNotInflictor LptVictim LptNotVictim",
            "VOOrderParam c VOParam Order:OrderReadiness",
            "VOParam c VORowData ParameterType:VOParamType",
            "VOParamGroup c VORowData GroupType:VOParamGroupType Parameters[]:VOParam",
            "VOParamGroupType e - PGTRadio PGTVoice",
            "VOParamType e - PEInflictor PEVictim PEListener",
            "VOPerson c - Name",
            "VOPersonParam c VOParam Person:VOPerson",
            "VOReadiness e - RRelaxed RCombat",
            "VOReadinessParam c VOParam Readiness:VOReadiness",
            "VORow c TreeNodeBase RowData[]:VORowData",
            "VORowData c TreeNodeBase",
            "VOSource e - SInflictor SInflictorFriend SInflictorLeader SVictim SVictimFriend SVictimLeader SPerson",
            "VOTargetDistanceParam c VOParam MaxDistance",
            "VOTypesAsset c Asset Persons[]:VOPerson Weapons[]:VOWeapon Vehicles[]:VOVehicle",
            "VOVehicle c - Name",
            "VOVehicleParam c VOParam Vehicle:VOVehicle",
            "VOWeapon c - Name",
            "VOWeaponParam c VOParam Weapon:VOWeapon",
            "ValueDebugNode c SoundNode Values[]:DebugValueInput",
            "Vec2 s - x y",
            "Vec3 s - x y z",
            "Vec3Parameter c BaseParameter InitialValue:Vec3",
            "Vec4 s - x y z w",
            "VegetationDensity e - All EveryOther EveryFourth EverySixth EveryEighth",
            "VehicleAsset c ObjectBlueprint",
            "VehicleComponentData c PartComponentData VehicleConfig:VehicleConfigData GearboxConfig:GearboxConfigData AlwaysFullThrottle WaterEffects[]:VehicleWaterEffectData WaterStreakEffects[]:VehicleWaterEffectData TriggerGroundEffectImpulse",
            "VehicleConfigData c - CenterOfMass:Vec3 CenterOfMassHandlingOffset:Vec3 InertiaModifier:Vec3 AeroDynamicPhysics:AeroDynamicPhysicsData MotionDamping:MotionDampingData Input:VehicleInputData FloatPhysics:FloatPhysicsData Stabilizer:StabilizerData VehicleModeAtReset:VehicleMode BodyMass GravityModifier YawMin YawMax DownForceBaseFactor DownForceWheelFactor VehicleModeChangeEnteringTime VehicleModeChangeStartingTime VehicleModeChangeStoppingTime VehicleModeChangeLeavingTime StandStillLowSpeedTimeLimit StaticFrictionBreakCollisionMod StaticFrictionBreakVelocityMod CoefficientOfAirFriction AirDensity AirDragArea WindResistanceBaseFactor WindResistanceVelocityFactor WindResistanceVelocityFactorMin WindResistanceVelocityFactorMax UseDownForce UseDownForceWheelFactor UseGearbox UseStandStillBrake UseStandStillSleep UseTurnAroundForce UseMotorcycleControl InvertPitchAllowed UseWindResistance",
            "VehicleEntityData c ControllableEntityData CriticallyDamagedEffectPosition:Vec3 PreExplosionEffectPosition:Vec3 MeshShaderSet:MeshShaderSetAsset Explosion:ExplosionEntityData ExplosionOffset:Vec3 Mesh:CompositeMeshAsset ConstructiblePreviewData:ConstructiblePreviewData CriticallyDamagedEffect:EffectAsset PreExplosionEffect:EffectAsset FrontHealthZone:VehicleHealthZoneData RearHealthZone:VehicleHealthZoneData LeftHealthZone:VehicleHealthZoneData RightHealthZone:VehicleHealthZoneData HealthZonesShareDamage HudData:VehicleHudData NameSid MaxPlayersInVehicle MinSpeedForMineActivation WaterDamage BelowWaterDamageDelay UpsideDownAngel UpsideDownDamage UpsideDownDamageDelay CriticalDamageTime PreExplosionTime WaterDamageOffset ThrowOutSoldierInsideOnWaterDamage KillSoldierCollisionSpeedThreshold ExitSpeedThreshold EnableGroundmapLighting IsAffectedByEMP MPMode:MPModeData Rumble:VehicleRumbleData EnterExitFadeWaitTime EnterExitFadeInTime ExitCameraSwitchDelay GiveScore AllowNeutralTeamChange AllowSpawnNextToVehicle",
            "VehicleExitPointComponentData c ComponentData",
            "VehicleGarageEntityData c GameEntityData BelongsToTeam:TeamId OccupyingRadius CanSpawn[]:VehicleAsset",
            "VehicleHealthZoneData s - EffectPosition:Vec3 Effect:EffectAsset MaxHealth",
            "VehicleHudData s - VehicleType:VehicleHudType Texture:TextureAsset",
            "VehicleHudType e - VehicleHudType_Car VehicleHudType_Tank VehicleHudType_Boat VehicleHudType_Helicopter VehicleHudType_Stationary VehicleHudType_Strategic VehicleHudType_BombRaid VehicleHudType_Bunker VehicleHudType_Dummy VehicleHudType_Count",
            "VehicleInput e - Throttle RPM Velocity InDriverPos InteriorCam Distance Tilt Gear SpringCompression Slip AngularVelocity",
            "VehicleInputData s - ThrottleDeadzone BrakeDeadzone YawDeadzone PitchDeadzone RollDeadzone YawInertiaOutDuration YawInertiaInDuration YawInertiaMinRatio PitchInertiaOutDuration PitchInertiaInDuration PitchInertiaMinRatio RollInertiaOutDuration RollInertiaInDuration RollInertiaMinRatio",
            "VehicleInputTweakData c - InputTweakType:VehicleInputTweakType MinSpeed MaxSpeed MinSpeedScale MaxSpeedScale",
            "VehicleInputTweakType e - CombinedTimedSpeedTweakType CombinedSpeedTweakType SpeedTweakType ScaledSpeedTweakType",
            "VehicleMode e - VmIdle VmEntering VmEntered VmStarting VmStarted VmStopping VmLeaving",
            "VehicleOrderingDebugUIEntityData c GameEntityData Vehicle:VehicleAsset Team:TeamId",
            "VehicleOrderingEntityData c GameEntityData TeamData[]:VehicleOrderingTeamData",
            "VehicleOrderingQueueData c UnlockPartData VehicleToSpawn:VehicleAsset SimultaneousVehicles CooldownTime TimeUntilAbandoned TimeUntilAbandonedIsDestroyed KeepAliveRadius QueueType:VehicleOrderingQueueType",
            "VehicleOrderingQueueType e - VOQTTankQueue VOQTAirplaneQueue VOQTHeliQueue VOQTQueueCount",
            "VehicleOrderingTeamData c - Queues[]:VehicleOrderingQueueData Team:TeamId",
            "VehicleProjectileEntityData c ProjectileEntityData Vehicle:VehicleAsset EnterDelay OnProjectileDestroyedUnspawnVehicle",
            "VehicleRumbleData s - SpeedThreshold RumbleRadius LowRumbleModifier HighRumbleModifier LowRumbleImpulseModifier HighRumbleImpulseModifier RumbleImpulseDuration RumbleImpulseClamp EngineStartLowRumbleModifier EngineStartHighRumbleModifier EngineStartRumbleDuration",
            "VehicleSpawnEntityData c SpawnEntityData Vehicles[]:TeamVehicleAsset Vehicle:VehicleAsset RotationRoll RotationPitch Throttle RespawnRange SpawnAreaRadius TimeUntilAbandoned TimeUntilAbandonedIsDestroyed BotBailWhenHealthBelow BotBailOutDelay KeepAliveRadius WreckDuration MaxVehicles MaxVehiclesPerMap MeshShaderSetNumber AutoSpawn ApplyDamageToAbandondedVehicles SnapToSurface SpawnVehicleFromBeginning DestroyVehiclesOnDisable SetTeamOnSpawn EffectedByImpulse LodDistance EnterRestriction:EntryEnterRestriction OnlySendEventForHumanPlayers SendWeaponEvents",
            "VehicleStateTriggerEntityData c TriggerEntityData VehicleToCheck:VehicleSpawnEntityData LockAmount EntryInputAction:EntryInputActionEnum InputVehiclePosition Restriction:EnterVehicleRestriction SeatSpecificToEnter BulletsAmount HealthToSet HealthToCheck TriggerCriticalOnHealthSet CheckHealthOnAndAbove EffectRadius EffectVehicles EffectBangers VehiclesToExclude[]:VehicleSpawnEntityData",
            "VehicleWaterEffectData c - Position:Vec3 Effect:EffectAsset",
            "VersionData c Asset disclaimer Version DateTime BranchId FinalBundles",
            "VertexSemantics e - ParticleColor ParticleWidthHeight ParticleTextureCoords1 ParticleTextureCoords2 ParticleTextureCoords3 ParticleTextureCoords4",
            "VertexShaderAsset c BaseShaderAsset",
            "VertexShaderFragmentAsset c Asset SourceFile Function",
            "VignetteData s - Mode:VignetteMode HealthTriggerValue Scale:Vec2 Exponent Color:Vec3 Opacity ColorTransform:ColorTransformData BlurAmount",
            "VignetteMode e - VMOff VMWeakHealth VMBadHealth VMCriticalHealth VMInGameMenu VMPreRound VMDeath VMHitIndicator VMShockWave VMPreset1 VMPreset2",
            "VisualTerrainEntityData c EntityData Terrain:TerrainData Settings:VisualTerrainSettings",
            "VisualTerrainSettings c - ViewMode:TerrainViewMode WireframeEnable Enable DrawEnable DrawPatchesEnable DrawSurroundingPatchesEnable DrawMainPatchesEnable DrawDecalEnable DrawDecalZPassEnable DrawOnlyDecalZPassEnable DecalZPassDistance DecalBias DecalZPassBias DecalOffsetY DrawUndergrowthDecalsEnable DrawPatchBoxesEnable DrawTextureTileBoxesEnable DrawDebugTextEnable DrawDebugTexturesEnable DrawWaterEnable ZeroViewportEnable GeometryLodScale ForceGeometryLodLevel ZPassDistance SlotReuseWaitCount SlotDebugOutputEnable BuildDrawJobEnable UpdateJobsEnable CullSpuJobEnable PrepareBuildSpuJobEnable BuildSpuJobEnable BuildJobCount PrioritizeCullJobsEnable RegenerateTexturesEnable ModifiersEnable HeightModificationEnable DynamicMaskEnable DynamicMaskTileSize DynamicMaskPoolWidth DynamicMaskPoolHeight DynamicMaskPixelsPerMeter DynamicMaskDecalWidthScale MaxNonVisibleTextureUpdateCount PatchSampleSize PatchSlotCount PatchLodTransitionsEnable PatchMaterialSortingEnable CullSampleBoundingBoxHeightEnable HeightfieldMipmapsEnable CastShadowsEnable MemoryBufferSize UndergrowthEnable UndergrowthCastShadowsEnable DrawUndergrowthEnable DrawUndergrowthCellBoxesEnable DrawUndergrowthDebugMaskTexturesEnable DrawUndergrowthDebugCacheTexturesEnable DrawUndergrowthStatsEnable UndergrowthTerrainColoringEnable UndergrowthGenerateEnable UndergrowthRegenerateEnable UndergrowthZPassEnable UndergrowthSimpleShaderEnable UndergrowthPoplessDistanceRatio UndergrowthUpdateJobsEnable UndergrowthBuildSpuJobEnable UndergrowthBuildJobCount UndergrowthUpdateCellCount UndergrowthCellSize UndergrowthCellMaskTextureSize UndergrowthCellCacheTextureSize UndergrowthForceCellSideCount UndergrowthMaxHeight UndergrowthWindSpeed UndergrowthDensityScale UndergrowthOverlapDensityScale UndergrowthSpuInstancingEnable UndergrowthInstanceCullEnable UndergrowthInstanceCullJobCount UndergrowthInstanceCullSpuJobEnable UndergrowthInstanceCullBoxTestEnable UndergrowthInstanceFrustumCullEnable UndergrowthInstanceOcclusionCullEnable UndergrowthInstanceAdditionalCullEnable DrawUndergrowthInstanceBoxesEnable UndergrowthMaxCullInstanceCount UndergrowthInstanceCullDynamicAllocEnable Ps3UndergrowthInstanceCellMemoryEnable OccluderJobEnable OccluderSpuJobEnable OccludedEnable OccludedMinDistance",
            "VisualWorldState c - FogColor:Vec3 FogStart FogEnd FogClamp FogEnable",
            "VoiceChannel e - VoiceChannel_Automatic VoiceChannel_Team VoiceChannel_Squad VoiceChannel_Count",
            "VoiceOverChildNode c VoiceOverNodeBase",
            "VoiceOverEntityData c GameEntityData VoiceOverLines[]:VoiceOverLineAsset Team:TeamId Subtitle:SubtitleDataTree UseSoldierGestures OverRadio RunOnce IsLowPrio MinDelayTime",
            "VoiceOverLeafNode c VoiceOverNodeBase",
            "VoiceOverLineAsset c - Soldier:SoldierSpawnBaseEntityData DialogLineNode:DialogLineNode OverRadio",
            "VoiceOverNodeBase c TreeNodeBase",
            "VoiceOverRootNode c VoiceOverNodeBase",
            "VoiceOverTree c TreeBase VoiceOverEntries[]:RuntimeDialogLineNode TalkSoundTemplate:SoundTemplate ScreamSoundTemplate:SoundTemplate ScriptedSoundTemplate:SoundTemplate ScriptedUnpositionedSoundTemplate:SoundTemplate RadioSoundTemplate:SoundTemplate Language:LanguageFormat",
            "WarnTarget e - wtWarnSoldierAndVehicle wtWarnSoldier wtWarnVehicle wtWarnNone",
            "WaterData c Asset ShallowDepth ShallowExtrude DeepDepth DeepExtrude OceanRadius OceanLevel OceanEnable RiverShader:ShaderAsset LakeShader:ShaderAsset LakeOutletShader:ShaderAsset OceanShader:ShaderAsset",
            "WaveAsset c Asset IsStreaming Seekable LengthInSeconds NameHash",
            "WaveAssetNode c VoiceOverLeafNode WaveAsset:WaveAsset",
            "WaveClassification c Editable WaveProperty:WaveProperty",
            "WaveMetaData c - LengthInSeconds",
            "WavePriority e - WpNone WpLow WpNormal WpHigh WpPermanent",
            "WaveProperty c - Name StreamPool:StreamPool",
            "WaveSegment e - WsLeadIn WsLoop WsLeadOut WaveSegmentCount",
            "WaypointData c - Speed Radius",
            "WeaponAnimBaseSetEnum e - wabsRif wabsHgr wabsAT wabsShg wabsCount wabsNone",
            "WeaponCallbackEntityData c GameEntityData",
            "WeaponClassEnum e - wcAssault wcShotgun wcSmg wcLmg wcSniper wcUgl wcHgr wcAt wcPistol wcATMine wcAutoInjector wcC4 wcKnife wcLaserDesignator wcMedKit wcMortarStrike wcMotionSensor wcPowerTool wcTracerDart wcCount wcNone",
            "WeaponComponentData c PartComponentData ProjectileSpawnOffset:Vec3 WeaponMesh:MeshAsset AnimTree:AnimTreeDataAsset Skeleton:SkeletonAsset WeaponFiring:WeaponFiringData AIData:WeaponData Effects:WeaponEffectsData ImpulseStrength",
            "WeaponData c - Strengths:Strengths Indirect IsAiAllowed MinRange MaxRange OptimalRangePercentage MinExtraReloadTime MaxExtraReloadTime AllowedAgainstImmortals CanSuppress CanAltAttack IsStandStill IsMelee BurstLimitNear BurstLimitFar MinBurstCoolDownTime MaxBurstCoolDownTime LostTargetBurstCoolDownModifier AimTransitionFireDelay AimAngularVelocity AngleToTargetSnap AimWhileReloading UseZoom AimOrigin:AimOrigin GravityModifier MinimumAccuracyBeforeFiring AdditionalMinimumAccuracyAtMinRange SweepFirstAttack SweepType:SweepSelectionType SweepDistance SweepTime InnerInaccurateDistance OuterInaccurateDistance InaccuracyFollowsTarget StartAccuracyPercentage FinalAccuracyPercentage",
            "WeaponEffectsData c -",
            "WeaponFiringData c - PrimaryFire:FiringFunctionData DeployTime AltDeployId ShotLimit UseAutoAiming WeaponSway:WeaponSwayData InflictSelfDamage ClearShotLimitOnKilled ReplenishWhenOutOfShots ReloadOnZoomButton VOTypes[]:VOWeapon Rumble:RumbleFiringData",
            "WeaponHandsIkNodeData c BaseHandsIkNodeData PositionAligmentBoneName GeneralPose:AnimationAsset ApplyWeaponDelta:BaseExpressionData EnableWeaponDelta EnableWeaponAnimation WeaponBoneDelta:LinearTransform",
            "WeaponInputRouterComponentData c ComponentData MaxFireRate RotationCount",
            "WeaponLagEffectForceData s - OffsetForce:Vec3 RotationForce:Vec3",
            "WeaponLagEffectSpringData s - Constant Damping",
            "WeaponLagEffectSpringVector s - SpringX:WeaponLagEffectSpringData SpringY:WeaponLagEffectSpringData SpringZ:WeaponLagEffectSpringData",
            "WeaponLagSpringEffectData c - OffsetSprings:WeaponLagEffectSpringVector RotationSprings:WeaponLagEffectSpringVector JumpForces:WeaponLagEffectForceData LandForces:WeaponLagEffectForceData PoseUpForces:WeaponLagEffectForceData PoseDownForces:WeaponLagEffectForceData",
            "WeaponOverrideData c - Data Values[]:WeaponOverrideValue",
            "WeaponOverrideValue s - ValueType:WeaponOverrideValueType Value",
            "WeaponOverrideValueType e - OverrideValue_BulletEntity_DistributeDamageOverTime OverrideValue_BulletEntity_StartDamage OverrideValue_BulletEntity_EndDamage OverrideValue_BulletEntity_DamageFalloffStartDistance OverrideValue_BulletEntity_DamageFalloffEndDistance",
            "WeaponPickupData s - Weapon:SoldierWeaponAsset WeaponSlot MinAmmo MaxAmmo",
            "WeaponPickupEntityData c PickupEntityData Weapons[]:WeaponPickupData UseForPersistence",
            "WeaponStateData s - BoneFakePhysics[]:BoneFakePhysicsData Mesh1p:SkinnedMeshAsset MeshZoom1p:SkinnedMeshAsset Mesh3p:SkinnedMeshAsset AnimTree1p:AnimTreeDataAsset AnimTree3p:AnimTreeDataAsset ZoomMeshTransitionFactor ZoomedScopeFilter:ScopeFilterData NonZoomedScopeFilter:ScopeFilterData KeepAimingTime AlwaysAimHead AnimatedFireType:AnimatedFireEnum AnimatedAimingType:AnimatedAimingEnum IsOneHanded PlayDeployAfterFire SkipDeployAnimation SkipReloadAnimation SkipFireAnimation HideProjectileAfterFireTime ProjectileBoneName Unlocks[]:WeaponUnlockVisualData Mesh3pTransforms[]:LinearTransform",
            "WeaponSuppressionData c - MaxMultiplier MinDistance MaxDistance",
            "WeaponSwayData c -",
            "WeaponUnlockVisualData c UnlockPartData Mesh1p:SkinnedMeshAsset MeshZoom1p:SkinnedMeshAsset Mesh3p:SkinnedMeshAsset AnimTreeReplacement1p:AnimTreeReplacementData AnimTreeReplacement3p:AnimTreeReplacementData",
            "WeatherStateEntityData c GameEntityData DefaultState State FadeTime TriggerEventFromClient StartTransitionOnSpawn",
            "WeatherSystemData c Asset Setup:WeatherSystemSetup States[]:WeatherSystemState",
            "WeatherSystemEntityData c EntityData WeatherSystem:WeatherSystemData Settings:WeatherSystemSettings",
            "WeatherSystemInstanceData c - WeatherSystem:WeatherSystemData Settings:WeatherSystemSettings",
            "WeatherSystemSettings c Asset InterpolationMode:WeatherstateInterpolationMode ActiveState StateFadeTimeSeconds Enable SkydomeEnable VolumetricCloudsEnable",
            "WeatherSystemSetup c Asset R Sky:StaticSkyData Sun:StaticSunData Clouds:StaticCloudLayerData OutdoorLight:StaticOutdoorLightData Fog:StaticFogData",
            "WeatherSystemState c Asset Sky:DynamicSkyData Sun:DynamicSunData Clouds:DynamicCloudLayerData OutdoorLight:DynamicOutdoorLightData Fog:DynamicFogData PostProcess:DynamicPostProcessData Wind:Vec2 ViewDistance ShadowViewDistance WorldViewDistance Zmin Zmax FadeTransparentStart FadeTransparentEnd FadeTransparentClamp YOffset ApplyFog",
            "WeatherstateInterpolationMode e - WimLinear WimCubic WimCatmullRom",
            "WeightFactorNodeData c BaseNodeData ParameterValues[]:FloatValueGroupData Node:BaseNodeData Factor Invert",
            "WheelComponentData c PartComponentData Config:WheelConfigData PhysicsType:WheelPhysicsType",
            "WheelConfigData c - Offset:Vec3 Inertia:Vec3 RotationBody:RotationBodyData SphereCollision:SphereCollisionData Spring:SpringData Mass Radius Width RollingResistanceInternalBaseFactor RollingResistanceBaseFactor RollingResistanceVelocityFactor EngineBrakeVelocityFactor EngineBrakeMinFactor EngineBrakeMaxFactor DiffGearRatio LowSpeedSteeringSensitivity MidSpeedSteeringSensitivity HighSpeedSteeringSensitivity SensitivityRangeScale LowSpeedSteeringSensitivityLimit MidSpeedSteeringSensitivityLimit HighSpeedSteeringSensitivityLimit TrackedTurnSpeedLimit TrackedForwardSpeedLimit SteeringSensitivity[]:SensitivityAtVelocity SlopeGripMinAngle SlopeGripMaxAngle SlopeGripExponent OffGroundGravityModifier SideSlipAngleMaxSlipCondition AngularVelocityMinSlipCondition WheelVelocityZMinSlipCondition WheelVelocityXMinSlipCondition WheelSlipRatioMaxSlipCondition LongitudinalFrictionForceMaxFactor LateralFrictionForceMaxFactor FxTorqueRadiusMultiplier FrictionMomentVelocityMin FrictionMomentVelocityMax FrictionMomentMultiplier FrictionMomentMaxFactor BrakeFactor BrakeForce HandBrakeFactor HandBrakeForce LateralPosK LateralNegK LongitudinalPosK LongitudinalNegK AlignMomScale WheelBaseLateral WheelBaseLongitudinal DrivingType SteeringType FrictionMethod RotationDirectionIndex SteeringAngleIndex PacejkaConfigIndex EngineIndex AckermanDeviceType UseRollingResistanceVelocityFactor UseRollingResistanceBaseFactor UseEngineBrake IsAllowedToSpin HasSteeringInverted UseFrictionMoment UseLowSpeedAutoBrake AdjustWheelRotation CollisionMaterial",
            "WheelPhysicsType e - wptNormal wptFollow wptStatic",
            "WindowSettings s - Position:Vec2 Size:Vec2 Fullscreen",
            "WingComponentData c PartComponentData Config:WingPhysicsData",
            "WingPhysicsData c - Lift FlapLift LiftCoefficient:Curve2D Drag FlapDrag DragCoefficient:Curve2D BaseAngleOfAttack InputForFlap FlapTurnSpeed VisualFlapTurnSpeed MaxFlapAngleScaleFactor:Curve2D LandingFlapLift LandingFlapLogic:LandingFlapData",
            "WireConnection e - WireNotConnectedIndex",
            "WorldBuildView c Asset Level:WorldData Sections[]:WorldSubSection",
            "WorldData c EntityBusData AuxAssets[]:Asset Modes[] MaterialGrid:MaterialGridData",
            "WorldInteractionComponentData c ComponentData MiddlegrowthSpeedThreshold",
            "WorldObjectListData c - Objects[]:GameObjectData Name",
            "WorldPartData c EntityBusData ObjectLists[]:WorldObjectListData Parts[]:WorldPartData GameModes[]:GameModeTypes Parent:WorldPartData Enabled EnabledPlatforms[]:WorldPartPlatform",
            "WorldPartPlatform e - WppWin32 WppXenon WppPs3",
            "WorldRenderSettings c - ClearColor:Vec3 XenonFastHdrColorScale XenonFixedHdrColorScale MinScreenArea ShadowMinScreenArea ViewportScale ShadowViewportScale ShadowmapMinFov ShadowmapFixedMovementEnable ShadowmapFixedDepthEnable ShadowmapSizeZScale ShadowmapWidth ShadowmapHeight ShadowmapSliceCount ShadowmapSliceSchemeWeight ShadowmapSliceOverlapFactor ShadowmapFirstSliceScale ShadowmapViewDistance ShadowmapExtrusionLength ShadowmapMinScreenArea Ps3Shadowmap16BitEnable Ps3ShadowmapTilingEnable CascadedBoxShadowmapEnable CascadedBoxShadowmapClipMethodEnable ApplyShadowmapsEnable GenerateShadowmapsEnable SimpleShadowmapsEnable ColoredShadowmapSlicesEnable MultisampleCount ReflectionCubemapSize MaxPointLightCount DrawDebugDynamicCubemapMipLevel OnlyShadowmapSlice ViewMode:WorldViewMode Enable HdrEnable XenonFastHdrEnable XenonFastHdrResolveScaleEnable XenonFloatDepthBufferEnable Ps3TilingEnable Ps3ZCullEnable Ps3SCullEnable GammaCorrectionEnable ShadowmapsEnable TonemapEnable ShadowExtrusionCullingEnable DrawLightSources DrawSolidBoundingBoxes DrawLineBoundingBoxes DrawBoundingSpheres DrawFrustums DrawShadowFrustums DrawDebugShadowmaps DrawDebugDynamicCubemap DrawDebugLUT DrawDebugHalfResEnvironment DrawDebugStaticEnvmaps DrawDebugVisibleEntityTypes WireframeEnable WeatherSystemHalfResEnable BackgroundEnable ForegroundEnable ForegroundBlurEnable ForegroundBlurFilterEnable ForegroundDepthClearEnable ForegroundZPassEnable Ps3ShadowDepthBias Ps3ShadowSlopeScaleBias Ps3ZPassDepthBias Ps3ZPassSlopeScaleBias Dx9ShadowSlopeScaleBias Dx9ZPassDepthBias Dx9ZPassSlopeScaleBias ScopeFilterEnable ScopeFilterColorScale DynamicCubemapEnable DynamicCubemapUpdateEnable DynamicCubemapSidesPerFrameCount DynamicCubemapFilterMode:MipmapFilterMode DynamicCubemapFilterWidth DynamicCubemapMipmapGenEnable PointLightViewDistance IrradianceVolumeEnable FogEnable JobsEnable MemoryBufferSize GenericEntityRendererEnable GenericEntityMaxStaticEntityCount GenericEntityMaxDynamicEntityCount DynamicCullSpuJobEnable DynamicCullZBufferTestEnable DynamicCullDrawOccludedBoxesEnable ManualTilingEnable ColoredTilingEnable TilePredicationEnable ZBufferShadowTestEnable",
            "WorldSubSection s - Min:Vec2 Max:Vec2",
            "WorldViewMode e - WvmNormal WvmSimple WvmOverdraw",
            "XenonShaderTessellationMode e - XstmDiscrete XstmContinuous XstmPerEdge",
            "XorSelectorNodeData c BaseNodeData Nodes[]:BoolNodeGroupData ElseNode:BaseNodeData",
            "ZRotate2DData c EvaluatorData",
            "ZoomLevelData c - FieldOfView LookSpeedMultiplier SprintLookSpeedMultiplier MoveSpeedMultiplier SwayPitchMultiplier SwayYawMultiplier TimePitchMultiplier TimeYawMultiplier DispersionMultiplier RecoilMultiplier RecoilFovMultiplier FadeToBlackInZoomTransition StartFadeToBlackAtTime FadeToBlackDuration StartFadeFromBlackAtTime FadeFromBlackDuration TransitionTime FovDelayTime FovTransitionTime ForegroundBlurFilter:BlurFilter ForegroundBlurFilterDeviation ForegroundBlurViewDistance SpottingConeFov SpottingConeLength SpottingConeFalloff",
            "ZoomLevelLockData s - OutlineTaggedDistance LockType:LockType",
            "ZoomTypeEnum e - ztHoldButtonToZoom ztPressButtonToSwitchZoomLevel",
        };
    }
    // END GENERATED BF1943 SCHEMA

    // BF1943's reflected classes (Bf1943Schema, from the game's executable): base classes, and the class a reference
    // in a class-typed member must point to.
    static class Bf1943Types
    {
        // a member of a class or struct: its type when that is a reflected class, struct or enum (else null), and
        // for an array its element type
        public sealed class Member { public string Type, Element; public bool IsArray; }

        static Dictionary<string, string> bases, kinds;
        static Dictionary<string, Dictionary<string, Member>> members;
        static Dictionary<string, HashSet<string>> enums;

        static void Load()
        {
            if (bases != null) return;
            bases = new Dictionary<string, string>();
            kinds = new Dictionary<string, string>();
            members = new Dictionary<string, Dictionary<string, Member>>();
            enums = new Dictionary<string, HashSet<string>>();
            foreach (string line in Bf1943Schema.Types)
            {
                var p = line.Split(' ');
                string name = p[0], kind = p[1];
                kinds[name] = kind;
                if (kind == "e") { enums[name] = new HashSet<string>(p.Skip(3)); continue; }
                if (kind == "c") bases[name] = p[2] == "-" ? null : p[2];
                var ms = new Dictionary<string, Member>();
                foreach (string token in p.Skip(3))
                {
                    int colon = token.IndexOf(':');
                    string n = colon < 0 ? token : token.Substring(0, colon), t = colon < 0 ? null : token.Substring(colon + 1);
                    bool array = n.EndsWith("[]");
                    if (array) n = n.Substring(0, n.Length - 2);
                    if (!ms.ContainsKey(n)) ms[n] = new Member { IsArray = array, Type = array ? null : t, Element = array ? t : null };
                }
                members[name] = ms;
            }
        }

        public static bool Known(string cls) { Load(); return bases.ContainsKey(cls); }

        // "c" class, "s" struct (a value type), "e" enum, or null
        public static string Kind(string name) { Load(); string k; return name != null && kinds.TryGetValue(name, out k) ? k : null; }

        public static HashSet<string> EnumValues(string name) { Load(); HashSet<string> v; return name != null && enums.TryGetValue(name, out v) ? v : null; }

        // cls is ancestor or derives from it
        public static bool IsA(string cls, string ancestor)
        {
            Load();
            for (int guard = 0; cls != null && guard < 64; guard++)
            {
                if (cls == ancestor) return true;
                string b;
                cls = bases.TryGetValue(cls, out b) ? b : null;
            }
            return false;
        }

        // A member of a struct, or of a class or one of its bases; null when it has none of that name.
        public static Member Find(string owner, string field)
        {
            Load();
            for (int guard = 0; owner != null && guard < 64; guard++)
            {
                Dictionary<string, Member> ms;
                Member m;
                if (members.TryGetValue(owner, out ms) && ms.TryGetValue(field, out m)) return m;
                string b;
                owner = bases.TryGetValue(owner, out b) ? b : null;
            }
            return null;
        }

        // The declared class of a class-typed member, looked up on owner and its bases; null when the member is not
        // class-typed or not reflected.
        public static string FieldClass(string owner, string field)
        {
            var m = Find(owner, field);
            return m != null && !m.IsArray && Kind(m.Type) == "c" ? m.Type : null;
        }
    }

    // Problems that crash or break a level, found without starting the game (FORMAT.md section 7). Two rule sets:
    // BFBC2's (derived from crashes of BFBC2 PC) and BF1943's (what every shipped BF1943 level satisfies; README).
    static class LevelCheck
    {
        public sealed class Finding
        {
            public bool Error;
            public string Where, Message;
            public override string ToString() { return (Error ? "ERROR  " : "WARN   ") + Where + ": " + Message; }
        }

        public const string Bf1943 = "bf1943", Bfbc2 = "bfbc2";

        // model groups and their members need these parts (terrain has its own collision and may leave PhysicsData empty)
        static readonly string[] WorldTypes = { "ModelGroupEntityData", "ModelGroupMemberData", "ForestEntityData", "TreeMemberData" };
        // the level's world: must be listed in LevelData Objects, or it is not loaded
        static readonly string[] ListedWorldTypes = { "TerrainEntityData", "ModelGroupEntityData", "ForestEntityData", "StaticEnvmapEntityData" };
        static readonly string[] ListedWorldTypes1943 = { "TerrainEntityData", "ModelGroupEntityData", "ForestEntityData" };
        static readonly string[] WorldParts = { "PhysicsData", "MeshEntityType" };
        static readonly string[] WorldParts1943 = { "PhysicsData", "MeshEntityType", "MemberType" };

        // The rule set for a file: BFBC2's when its LevelData has BFBC2's fields (DataConnections, Descriptor,
        // MemoryReportGroup: every BFBC2 PC level has them, no BF1943 level does), else BF1943's.
        public static string Detect(DbxFile f)
        {
            var level = f.Instances().FirstOrDefault(i => (f.Get(i, "type") ?? "").EndsWith(".LevelData"));
            if (level == null || !level.IsContainer) return Bf1943;
            var names = new HashSet<string>(level.Children.Select(c => f.Get(c, "name") ?? ""));
            return names.Contains("DataConnections") || names.Contains("Descriptor") || names.Contains("MemoryReportGroup") ? Bfbc2 : Bf1943;
        }

        public static List<Finding> Run(DbxFile f, DbxFile slotsFrom, IList<string> bundles)
        {
            return Run(f, slotsFrom, bundles, Detect(f));
        }

        // slotsFrom: the level whose cull trees the file uses (or null); bundles: unpacked folders to resolve
        // references into other files (or none); rules: Bf1943 or Bfbc2.
        public static List<Finding> Run(DbxFile f, DbxFile slotsFrom, IList<string> bundles, string rules)
        {
            bool bf1943 = rules == Bf1943;
            var found = new List<Finding>();
            var guids = new HashSet<string>(f.Instances().Select(i => f.Get(i, "guid") ?? ""), StringComparer.OrdinalIgnoreCase);
            var external = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
            var wanted = new Dictionary<string, List<string[]>>(StringComparer.OrdinalIgnoreCase);   // BF1943: declared class per external reference
            var parts = bf1943 ? WorldParts1943 : WorldParts;
            foreach (var inst in f.Instances())
            {
                string type = f.Get(inst, "type") ?? "", where = Short(type) + " " + f.Get(inst, "guid");
                bool levelData = type.EndsWith(".LevelData");
                bool world = WorldTypes.Any(w => type.EndsWith(w));
                Walk(f, inst, "", (e, path) =>
                {
                    string r = f.Get(e, "ref");
                    if (r == null) return;
                    bool inObjects = levelData && path.StartsWith("Objects");
                    bool isLocal = r.Length == 36 && r.IndexOf('/') < 0;
                    if (r == "null")
                    {
                        if (inObjects && f.TagOf(e) == "item")
                            found.Add(new Finding { Error = true, Where = where + " " + path, Message = "empty entry in the level's object list (the game reads it without a check and crashes)" });
                        else if (world && parts.Contains(f.Get(e, "name")))
                            found.Add(new Finding { Error = true, Where = where + " " + path, Message = "world object without " + f.Get(e, "name") + " (the model-group set-up crashes on it)" });
                    }
                    else if (isLocal && !guids.Contains(r))
                        found.Add(new Finding { Error = true, Where = where + " " + path, Message = "refers to instance " + r + ", which is not in this file" + (inObjects ? " (an empty entry in the level's object list: crash)" : "") });
                    else if (r.IndexOf('/') > 0)
                    {
                        int cut = r.LastIndexOf('/');
                        List<string> users;
                        string key = r.Substring(0, cut) + "|" + r.Substring(cut + 1);
                        if (!external.TryGetValue(key, out users)) external[key] = users = new List<string>();
                        if (users.Count < 3) users.Add(where + " " + path);
                        string want = bf1943 && path.IndexOf('.') < 0 && path.IndexOf('[') < 0 ? Bf1943Types.FieldClass(Short(type), path) : null;
                        if (want != null)
                        {
                            List<string[]> w;
                            if (!wanted.TryGetValue(key, out w)) wanted[key] = w = new List<string[]>();
                            if (!w.Any(x => x[0] == want)) w.Add(new[] { want, where + " " + path });
                        }
                    }
                });
            }
            CheckWorldListed(f, found, bf1943 ? ListedWorldTypes1943 : ListedWorldTypes);
            CheckSlots(f, slotsFrom, found);
            if (bf1943) LevelRules1943.Check(f, found);
            if (bundles.Count > 0)
            {
                var index = BundleIndex(bundles);
                CheckExternal(external, wanted, index, found);
                if (bf1943) LevelRules1943.CheckCullTrees(f, index, found);
            }
            return found;
        }

        static void CheckWorldListed(DbxFile f, List<Finding> found, string[] listedTypes)
        {
            var level = f.Instances().FirstOrDefault(i => (f.Get(i, "type") ?? "").EndsWith(".LevelData"));
            if (level == null) return;
            var objects = level.Children.FirstOrDefault(c => f.Get(c, "name") == "Objects" && c.IsContainer);
            var listed = new HashSet<string>(objects == null ? new string[0] : objects.Children.Select(c => f.Get(c, "ref") ?? ""), StringComparer.OrdinalIgnoreCase);
            foreach (var i in f.Instances())
            {
                string type = f.Get(i, "type") ?? "", guid = f.Get(i, "guid");
                if (listedTypes.Any(w => type.EndsWith(w)) && !listed.Contains(guid))
                    found.Add(new Finding { Error = false, Where = Short(type) + " " + guid, Message = "not in the level's object list, so it is not loaded (missing terrain or buildings in game)" });
            }
        }

        internal static string Short(string type) { int dot = type.LastIndexOf('.'); return dot >= 0 ? type.Substring(dot + 1) : type; }

        internal static void Walk(DbxFile f, Element e, string path, Action<Element, string> visit)
        {
            if (!e.IsContainer) return;
            var seen = new Dictionary<string, int>();
            foreach (var c in e.Children)
            {
                string name = f.Get(c, "name") ?? f.TagOf(c);
                int n;
                seen.TryGetValue(name, out n);
                seen[name] = n + 1;
                string p = path.Length == 0 ? name : path + (f.TagOf(c) == "item" ? "[" + n + "]" : "." + name);
                visit(c, p);
                Walk(f, c, p, visit);
            }
        }

        static void CheckSlots(DbxFile f, DbxFile slotsFrom, List<Finding> found)
        {
            var objects = PlacedObject.All(f).Where(o => o.Slot != null && o.SlotValue != 0).ToList();
            foreach (var g in objects.GroupBy(o => o.SlotValue).Where(g => g.Count() > 1).Take(20))
                found.Add(new Finding { Error = false, Where = "cull slot " + g.Key, Message = "shared by " + g.Count() + " objects (" + string.Join(", ", g.Take(3).Select(o => o.Blueprint)) + "): they are shown and hidden together - see-through objects" });
            if (slotsFrom == null) return;
            var theirs = new Dictionary<string, PlacedObject>(StringComparer.OrdinalIgnoreCase);
            foreach (var o in PlacedObject.All(slotsFrom).Where(o => o.Slot != null && o.Guid != null))
                if (!theirs.ContainsKey(o.Guid)) theirs[o.Guid] = o;
            var wrong = PlacedObject.All(f).Where(o => o.Slot != null && o.Guid != null).Where(o => { PlacedObject t; return theirs.TryGetValue(o.Guid, out t) && t.SlotValue != o.SlotValue; }).ToList();
            if (wrong.Count > 0)
                found.Add(new Finding { Error = false, Where = "cull slots", Message = wrong.Count + " objects have another slot than the same object in the cull trees' level (e.g. " + string.Join(", ", wrong.Take(3).Select(o => o.Blueprint + " " + o.SlotValue)) + ") - see-through objects; fix with: DbxEdit fixslots" });
        }

        // partition name ("levels/x/y", no extension) -> file, over the unpacked bundle folders (the first one wins)
        static Dictionary<string, string> BundleIndex(IList<string> bundles)
        {
            var index = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string b in bundles)
                foreach (string p in Program.DbxFiles(b))
                {
                    string rel = p.Substring(b.Length).TrimStart('\\', '/').Replace('\\', '/');
                    rel = rel.Substring(0, rel.Length - 4);
                    if (!index.ContainsKey(rel)) index[rel] = p;
                }
            return index;
        }

        static void CheckExternal(Dictionary<string, List<string>> external, Dictionary<string, List<string[]>> wanted,
                                  Dictionary<string, string> index, List<Finding> found)
        {
            var loaded = new Dictionary<string, Dictionary<string, string>>(StringComparer.OrdinalIgnoreCase);   // guid -> type
            int missingFiles = 0;
            foreach (var kv in external)
            {
                var parts = kv.Key.Split('|');
                string partition = parts[0], guid = parts[1], path;
                if (!index.TryGetValue(partition, out path))
                {
                    if (missingFiles++ < 30)
                        found.Add(new Finding { Error = false, Where = kv.Value[0], Message = "refers to " + partition + ", which is not in the given folders" });
                    continue;
                }
                Dictionary<string, string> g;
                if (!loaded.TryGetValue(path, out g))
                {
                    var d = DbxFile.Read(path);
                    g = null;
                    if (d.IsBinary)
                    {
                        g = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                        foreach (var i in d.Instances()) g[d.Get(i, "guid") ?? ""] = Short(d.Get(i, "type") ?? "");
                    }
                    loaded[path] = g;
                }
                string targetType = null;
                if (g != null && !g.TryGetValue(guid, out targetType))
                    found.Add(new Finding { Error = true, Where = kv.Value[0], Message = "refers to instance " + guid + " in " + partition + ", which that file does not have" });
                else if (g != null && wanted.ContainsKey(kv.Key))
                    foreach (var w in wanted[kv.Key])
                        if (!Bf1943Types.IsA(targetType, w[0]))
                            found.Add(new Finding { Error = true, Where = w[1], Message = "refers to a " + targetType + " in " + partition + "; BF1943's schema declares this field a " + w[0] + " reference" });
            }
            if (missingFiles > 30)
                found.Add(new Finding { Error = false, Where = "references to other files", Message = (missingFiles - 30) + " more files not in the given folders" });
        }
    }

    // BF1943's own rules (FORMAT.md section 7, README "Check a level"): each one holds in every shipped BF1943 level
    // (wake_island_s, guadal_canal, iwo_jima_s, coral_sea, wake_island_tutorial), as derived and recorded by the research
    // workspace's src/assetresearch/derive_level_rules1943.py (research/evidence/bf1943-level-check-rules.json).
    // Errors: the file is inconsistent with itself or with BF1943's classes. Warnings: the level is unlike every shipped one.
    static class LevelRules1943
    {
        // a group's members are its kind's
        static readonly Dictionary<string, string> GroupMember = new Dictionary<string, string> {
            { "StaticModelGroupEntityData", "StaticModelGroupMemberData" }, { "RigidModelGroupEntityData", "RigidModelGroupMemberData" },
            { "MiddlegrowthModelGroupEntityData", "MiddlegrowthModelGroupMemberData" }, { "ForestEntityData", "TreeMemberData" } };

        // "<owner>.<array path> <class>": what the items of these arrays hold - the nearest common base, in BF1943's schema,
        // of everything the shipped levels put there (the schema does not give array element types)
        static readonly string[] ArrayTypes = {
            "AreaTriggerEntityData.Geometry GeometryEntityData", "AreaTriggerEntityData.Soldiers SoldierSpawnEntityData",
            "CapturePointEntityData.Geometry GeometryEntityData", "CapturePointEntityData.Spawners SpawnEntityData",
            "CombatAreaTriggerEntityData.Geometry AreaGeometryEntityData", "CombatAreaTriggerEntityData.SurroundingGeometry AreaGeometryEntityData",
            "DmzEntityData.Geometry AreaGeometryEntityData", "ForestEntityData.MemberDatas TreeMemberData",
            "HotZoneEntityData.Geometry AreaGeometryEntityData", "LevelData.EventConnections.Source GameObjectData",
            "LevelData.EventConnections.Target GameObjectData", "LevelData.Objects GameObjectData",
            "MiddlegrowthModelGroupEntityData.MemberDatas MiddlegrowthModelGroupMemberData", "MissionObjectiveEntityData.MapMarkers MapMarkerEntityData",
            "ObjectSet.Components ReferenceObjectData", "ReferenceObjectData.ObjectSets ObjectSet",
            "RepairStationEntityData.Geometry AreaGeometryEntityData", "RichPresenceConnection.ContextPairs RichPresenceContextPair",
            "RigidModelGroupEntityData.MemberDatas RigidModelGroupMemberData", "SoldierSpawnEntityData.AlternativeSpawnPoints AlternateSpawnEntityData",
            "SoundAreaEntityData.Geometry AreaGeometryEntityData", "SoundAreaEntityData.OuterGeometry AreaGeometryEntityData",
            "SpectateEntityData.Cameras TargetCameraData", "StaticModelGroupEntityData.MemberDatas StaticModelGroupMemberData",
            "TeamEntityData.Squads SquadData", "TerrainSplineData.Planes TerrainSplinePlaneData", "TerrainSplineData.Points TerrainSplinePointData",
            "TerrainSplinePlaneData.Points TerrainSplinePlanePointData", "VoiceOverEntityData.VoiceOverLines VoiceOverLineAsset" };

        // references from listed entities to entities the level must create: they point at listed instances in every shipped level
        static readonly HashSet<string> CreatedFields = new HashSet<string> { "Source", "Target", "Spawners", "AlternativeSpawnPoints",
            "MapMarker", "MapMarkers", "Geometry", "SurroundingGeometry", "Soldiers", "ObjectToFollow", "PlayerToCheck", "VehicleToCheck" };

        // listed exactly once in every shipped level
        static readonly string[] Singletons = { "TerrainEntityData", "CombatAreaTriggerEntityData", "HumanPlayerEntityData", "AutoTeamEntityData",
            "PreRoundEntityData", "MissionEntityData", "HotZoneEntityData" };

        static Dictionary<string, string> arrayTypes;

        sealed class Ctx
        {
            public DbxFile F;
            public List<LevelCheck.Finding> Found;
            public readonly Dictionary<string, Element> ByGuid = new Dictionary<string, Element>(StringComparer.OrdinalIgnoreCase);
            public HashSet<string> Listed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            public string TypeOf(Element i) { return LevelCheck.Short(F.Get(i, "type") ?? ""); }
            public string TypeOf(string guid) { Element e; return guid != null && ByGuid.TryGetValue(guid, out e) ? TypeOf(e) : null; }
            public string Where(Element i) { return TypeOf(i) + " " + F.Get(i, "guid"); }
            public void Error(string where, string message) { Found.Add(new LevelCheck.Finding { Error = true, Where = where, Message = message }); }
            public void Warn(string where, string message) { Found.Add(new LevelCheck.Finding { Error = false, Where = where, Message = message }); }
        }

        public static void Check(DbxFile f, List<LevelCheck.Finding> found)
        {
            if (arrayTypes == null)
            {
                var t = new Dictionary<string, string>();
                foreach (string line in ArrayTypes) { var p = line.Split(' '); t[p[0]] = p[1]; }
                arrayTypes = t;
            }
            var c = new Ctx { F = f, Found = found };
            var all = f.Instances().ToList();

            // GUIDs unique; classes BF1943 has
            var unknown = new HashSet<string>();
            foreach (var i in all)
            {
                string g = f.Get(i, "guid") ?? "";
                if (c.ByGuid.ContainsKey(g)) c.Error(c.Where(i), "another instance has the same GUID: references to it are ambiguous (GUIDs are unique in every shipped level)");
                else c.ByGuid[g] = i;
                string type = c.TypeOf(i);
                if (!Bf1943Types.Known(type) && unknown.Add(type))
                    c.Error(c.Where(i), type + " is not a class of BF1943 (its reflection schema has no such class), so the game cannot create it");
            }

            // one LevelData, the partition's primary instance
            var levels = all.Where(i => c.TypeOf(i) == "LevelData").ToList();
            if (levels.Count != 1)
                c.Error("partition", levels.Count + " LevelData instances (every shipped level partition has exactly one)");
            var level = levels.FirstOrDefault();
            if (level != null)
            {
                string primary = f.Get(f.TopLevel[0], "primaryInstance");
                if (!Guid.Same(primary, f.Get(level, "guid")))
                    c.Error("partition", "primaryInstance is " + (primary ?? "missing") + ", not the LevelData " + f.Get(level, "guid") + " (as in every shipped level)");
                var objects = Items(f, level, "Objects");
                var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                for (int k = 0; k < objects.Count; k++)
                {
                    string r = f.Get(objects[k], "ref");
                    if (r == null || r == "null") continue;
                    if (!seen.Add(r)) c.Warn(c.Where(level) + " Objects[" + k + "]", r + " is listed twice (each shipped level lists an object once)");
                    c.Listed.Add(r);
                }
            }

            CheckReferenceTypes(c, all);
            CheckSchema(c, all);
            CheckGroups(c, all);
            CheckSlots(c, all);
            if (level != null) CheckGameplay(c, all, level);
        }

        // class-typed fields hold their declared class (schema); arrays hold what the shipped levels' do
        static void CheckReferenceTypes(Ctx c, List<Element> all)
        {
            var f = c.F;
            foreach (var i in all)
            {
                string type = c.TypeOf(i), where = c.Where(i);
                LevelCheck.Walk(f, i, "", (e, path) =>
                {
                    string r = f.Get(e, "ref");
                    if (r == null || r == "null" || r.Length != 36 || r.IndexOf('/') >= 0) return;
                    string target = c.TypeOf(r);
                    if (target == null) return;                              // unresolved: reported already
                    bool top = path.IndexOf('.') < 0 && path.IndexOf('[') < 0;
                    string want = top ? Bf1943Types.FieldClass(type, path) : null;
                    if (want == null && !top) arrayTypes.TryGetValue(type + "." + Unindexed(path), out want);
                    if (want != null && !Bf1943Types.IsA(target, want))
                        c.Error(where + " " + path, "refers to a " + target + " (" + r + "); " + (top
                            ? "BF1943's schema declares this field a " + want + " reference"
                            : "in every shipped level these items are " + want + "s"));
                });
            }
        }

        // model groups and the forest: member kind, InstanceCount, the physics-part chain
        static void CheckGroups(Ctx c, List<Element> all)
        {
            var f = c.F;
            foreach (var g in all)
            {
                string kind;
                if (!GroupMember.TryGetValue(c.TypeOf(g), out kind)) continue;
                var members = Items(f, g, "MemberDatas");
                int run = 0;
                for (int k = 0; k < members.Count; k++)
                {
                    string where = c.Where(g) + " MemberDatas[" + k + "]";
                    Element m;
                    string r = f.Get(members[k], "ref");
                    if (r == null || !c.ByGuid.TryGetValue(r, out m)) continue;
                    if (c.TypeOf(m) != kind) { c.Error(where, "a " + c.TypeOf(g) + " holds " + kind + "s, not a " + c.TypeOf(m)); continue; }
                    int fp = Int(f, m, "FirstPhysicsPart"), pc = Int(f, m, "PhysicsPartCount"), per = Int(f, m, "PhysicsPartCountPerInstance"),
                        count = Int(f, m, "InstanceCount"), spawns = Items(f, m, "SpawnDatas").Count;
                    string mw = c.Where(m);
                    if (count != spawns)
                        c.Error(mw, "InstanceCount " + count + ", but " + spawns + " SpawnDatas (one per instance in every shipped level)");
                    if (fp != run)
                        c.Error(mw, "FirstPhysicsPart " + fp + ", expected " + run + ": a group's members' physics parts follow each other (the first starts at 0, each next at the previous one's PhysicsPartCount)");
                    if (pc - fp != count * per)
                        c.Error(mw, "PhysicsPartCount " + pc + " - FirstPhysicsPart " + fp + " = " + (pc - fp) + ", expected InstanceCount " + count + " x PhysicsPartCountPerInstance " + per + " = " + count * per + " (PhysicsPartCount is the end of the member's parts)");
                    run = pc;
                }
            }
        }

        // the cull slots: group instances 1..N, placed objects above them, a blueprint's objects all slotted or none
        static void CheckSlots(Ctx c, List<Element> all)
        {
            var f = c.F;
            var slots = new Dictionary<int, int>();
            foreach (var i in all)
                foreach (var s in Items(f, i, "SpawnDatas"))
                {
                    int v = Int(f, s, "Enumeration");
                    int n;
                    slots.TryGetValue(v, out n);
                    slots[v] = n + 1;
                }
            int total = slots.Values.Sum();
            if (total > 0)
            {
                foreach (var kv in slots.Where(kv => kv.Value > 1).Take(20))
                    c.Warn("group instance slot " + kv.Key, "used by " + kv.Value + " model-group or forest instances (SpawnDatas Enumeration): they are shown and hidden together");
                var missing = Enumerable.Range(1, total).Where(v => !slots.ContainsKey(v)).ToList();
                if (missing.Count > 0)
                    c.Warn("group instance slots", "the " + total + " model-group and forest instances' slots (SpawnDatas Enumeration) are not 1.." + total
                           + " (e.g. " + string.Join(", ", missing.Take(5)) + " missing), as in every shipped level: the cull trees number them so");
            }
            var placed = PlacedObject.All(f).Where(o => o.Slot != null).ToList();
            var low = placed.Where(o => o.SlotValue != 0 && o.SlotValue <= total).ToList();
            if (low.Count > 0)
                c.Warn("cull slots", low.Count + " placed objects have a model-group instance's slot (1.." + total + "; e.g. " + string.Join(", ", low.Take(3).Select(o => o.Blueprint + " " + o.SlotValue))
                       + "): in every shipped level placed objects' slots come after the group instances'");
            foreach (var b in placed.GroupBy(o => o.Blueprint))
            {
                int zero = b.Count(o => o.SlotValue == 0);
                if (zero > 0 && zero < b.Count())
                    c.Warn("blueprint " + b.Key, zero + " of its " + b.Count() + " placed objects have slot 0 and the others have slots (e.g. " + b.First(o => o.SlotValue == 0).Guid
                           + "); every shipped level slots all or none of a blueprint's objects - fix with: DbxEdit fixslots");
            }
        }

        // teams, tickets, bases, spawns, what the level creates
        static void CheckGameplay(Ctx c, List<Element> all, Element level)
        {
            var f = c.F;
            string lw = c.Where(level);
            var listed = all.Where(i => c.Listed.Contains(f.Get(i, "guid") ?? "")).ToList();
            var teams = listed.Where(i => c.TypeOf(i) == "TeamEntityData").Select(i => Text(f, i, "Id") ?? "?").OrderBy(x => x, StringComparer.Ordinal).ToList();
            if (string.Join(",", teams) != "Team1,Team2,TeamNeutral")
                c.Warn(lw + " Objects", "lists TeamEntityData " + (teams.Count == 0 ? "none" : string.Join(", ", teams)) + "; every shipped level lists TeamNeutral, Team1 and Team2, once each");
            var tickets = listed.Where(i => c.TypeOf(i) == "TicketCounterEntityData").Select(i => Text(f, i, "TeamId") ?? "?").OrderBy(x => x, StringComparer.Ordinal).ToList();
            if (string.Join(",", tickets) != "Team1,Team2")
                c.Warn(lw + " Objects", "lists ticket counters for " + (tickets.Count == 0 ? "no team" : string.Join(", ", tickets)) + "; every shipped level has one for Team1 and one for Team2");
            var cps = listed.Where(i => c.TypeOf(i) == "CapturePointEntityData").ToList();
            foreach (string team in new[] { "Team1", "Team2" })
                if (!cps.Any(p => Text(f, p, "UnableToChangeTeam") == "true" && Text(f, p, "InitialOwnerTeam") == team))
                    c.Warn(lw + " Objects", team + " has no base: no listed capture point with UnableToChangeTeam true and InitialOwnerTeam " + team + " (every shipped level has one per team)");
            var owned = new HashSet<string>(cps.SelectMany(p => Items(f, p, "Spawners")).Select(it => f.Get(it, "ref") ?? ""), StringComparer.OrdinalIgnoreCase);
            foreach (var s in listed.Where(i => c.TypeOf(i) == "SoldierSpawnEntityData"))
                if (!owned.Contains(f.Get(s, "guid")))
                    c.Warn(c.Where(s), "no listed capture point has this soldier spawn in its Spawners: BF1943's spawns are TeamNeutral and take their capture point's side (every shipped level's spawns have one)");
            foreach (string t in Singletons)
            {
                int n = listed.Count(i => c.TypeOf(i) == t);
                if (n != 1) c.Warn(lw + " Objects", "lists " + n + " " + t + " (every shipped level lists exactly one)");
            }
            string name = Text(f, level, "Name"), id = f.Get(level, "id");
            if (name != "Levels/" + id)
                c.Warn(lw + " Name", "is " + (name ?? "missing") + ", the instance's id " + (id ?? "missing") + ": in every shipped level the Name is Levels/<id>");
            // references from listed entities (and LevelData) to entities the level must create
            foreach (var i in listed.Concat(new[] { level }))
            {
                string type = c.TypeOf(i), where = c.Where(i);
                LevelCheck.Walk(f, i, "", (e, path) =>
                {
                    string r = f.Get(e, "ref");
                    if (r == null || r == "null" || r.Length != 36 || r.IndexOf('/') >= 0 || c.TypeOf(r) == null) return;
                    string p = Unindexed(path), field = p.Substring(p.LastIndexOf('.') + 1);
                    if (!CreatedFields.Contains(field) || (type == "SoundAreaEntityData" && p.StartsWith("OuterGeometry"))) return;
                    if (!c.Listed.Contains(r))
                        c.Warn(where + " " + path, "refers to " + c.TypeOf(r) + " " + r + ", which LevelData Objects does not list, so the level does not create it (in every shipped level it is listed)");
                });
            }
        }

        // Fields the shipped levels carry (6,060 times) that BF1943's classes and structs do not have. The game loads
        // those levels, so it skips them; six of the names are nowhere in the executable.
        static readonly HashSet<string> DataOnlyFields = new HashSet<string> {
            "ConnectionPair.FromData", "ConnectionPair.ToData", "ForestEntityData.IsObsolete", "ForestEntityData.ReplacementObject",
            "HavokAsset.RootNode", "HavokAsset.SourceFile", "LevelData.Parts", "MiddlegrowthModelGroupEntityData.IsObsolete",
            "MiddlegrowthModelGroupEntityData.ReplacementObject", "ReferenceObjectData.IsInObjectSet", "RichPresenceConnection.RichPresence",
            "RichPresenceContextPair.Context", "RichPresenceContextPair.Value", "RigidModelGroupEntityData.IsObsolete",
            "RigidModelGroupEntityData.ReplacementObject", "StaticModelGroupEntityData.IsObsolete", "StaticModelGroupEntityData.ReplacementObject",
            "TeamEntityData.Squads", "TerrainEntityData.IsObsolete", "TerrainEntityData.ReplacementObject" };

        // every field a member of its class or struct; array elements of the declared class; enum fields hold a value name
        static void CheckSchema(Ctx c, List<Element> all)
        {
            int unknown = 0;
            foreach (var i in all)
            {
                string t = c.TypeOf(i);
                if (Bf1943Types.Known(t)) Visit(c, t, i, c.Where(i), "", ref unknown);
            }
            if (unknown > 50) c.Warn("fields", (unknown - 50) + " more fields that BF1943's classes do not have");
        }

        static void Visit(Ctx c, string owner, Element e, string where, string path, ref int unknown)
        {
            var f = c.F;
            if (!e.IsContainer) return;
            foreach (var ch in e.Children)
            {
                string name = f.Get(ch, "name");
                if (name == null) continue;
                string p = path.Length == 0 ? name : path + "." + name, tag = f.TagOf(ch);
                var m = Bf1943Types.Find(owner, name);
                if (m == null)
                {
                    if (!DataOnlyFields.Contains(owner + "." + name) && unknown++ < 50)
                        c.Warn(where + " " + p, owner + " has no member " + name + " in BF1943's executable (its reflection schema), so the game does not read it");
                    continue;
                }
                if (tag == "complex" && Bf1943Types.Kind(m.Type) == "s") Visit(c, m.Type, ch, where, p, ref unknown);
                else if (tag == "array" && ch.IsContainer)
                {
                    string ek = Bf1943Types.Kind(m.Element);
                    for (int k = 0; k < ch.Children.Count; k++)
                    {
                        var it = ch.Children[k];
                        string ip = p + "[" + k + "]";
                        if (f.TagOf(it) == "complex" && ek == "s") Visit(c, m.Element, it, where, ip, ref unknown);
                        string r = f.Get(it, "ref");
                        if (ek == "c" && r != null && r != "null" && r.Length == 36 && r.IndexOf('/') < 0)
                        {
                            string target = c.TypeOf(r);
                            if (target != null && !Bf1943Types.IsA(target, m.Element))
                                c.Error(where + " " + ip, "refers to a " + target + " (" + r + "); BF1943's schema declares the elements of " + owner + "." + name + " " + m.Element + "s");
                        }
                    }
                }
                else if (tag == "field" && !ch.IsContainer && f.Get(ch, "ref") == null && Bf1943Types.Kind(m.Type) == "e")
                {
                    string v = XmlExport.ValueText(f, ch);
                    var values = Bf1943Types.EnumValues(m.Type);
                    if (!values.Contains(v))
                        c.Error(where + " " + p, "\"" + v + "\" is not a value of " + m.Type + " (BF1943's executable has " + string.Join(", ", values.Take(6)) + (values.Count > 6 ? ", ..." : "") + ")");
                }
            }
        }

        // With --bundle: the LevelData's static cull trees (World.StaticCullTreeData: Nodes, EnumOffset). Nodes are a
        // pre-order binary tree: an inner node's Right is the offset to its right child (the left one follows it); a
        // leaf's Right has bit 31 set and its leaf number 1..L below; 256 zero nodes end the list. Leaf n is slot
        // EnumOffset + n. The game's traversal (0x825c3730) reads a node's sphere as centre SphereKeyX/Y/Z / 16 and
        // radius SphereKeyW / 4; Max (a node's largest object radius, the maximum over its subtree) is its distance test.
        sealed class CullTree
        {
            public string Partition; public int EnumOffset, Nodes; public int Duplicates;
            public Dictionary<int, int[]> Leaves = new Dictionary<int, int[]>();
            public Dictionary<int, float> LeafMax = new Dictionary<int, float>();
            public List<uint> Right = new List<uint>(); public List<float> Max = new List<float>(); public List<int> W = new List<int>();
        }

        static CullTree LoadTree(Ctx c, Element level, string field, Dictionary<string, string> index)
        {
            var f = c.F;
            var x = Child(f, level, field);
            string r = x == null ? null : f.Get(x, "ref");
            if (r == null || r == "null" || r.IndexOf('/') < 0) return null;
            string part = r.Substring(0, r.LastIndexOf('/')), guid = r.Substring(r.LastIndexOf('/') + 1), path;
            if (!index.TryGetValue(part, out path)) return null;                        // reported as a missing file
            var d = DbxFile.Read(path);
            if (!d.IsBinary) return null;
            var t = d.Instance(guid);
            if (t == null) return null;                                                   // reported as a missing instance
            var tree = new CullTree { Partition = part, EnumOffset = Int(d, t, "EnumOffset") };
            foreach (var node in Items(d, t, "Nodes"))
            {
                tree.Nodes++;
                uint right = (uint)Int(d, node, "Right");
                var key = new int[4];
                for (int k = 0; k < 4; k++) key[k] = Short16(d, node, "SphereKey" + "XYZW"[k]);
                float max = BitConverter.ToSingle(BitConverter.GetBytes(Int(d, node, "Max")), 0);
                tree.Right.Add(right);
                tree.Max.Add(max);
                tree.W.Add(key[3]);
                if ((right >> 31) == 0) continue;
                int n = (int)(right & 0x7fffffff);
                if (tree.Leaves.ContainsKey(n)) tree.Duplicates++;
                else { tree.Leaves[n] = key; tree.LeafMax[n] = max; }
            }
            return tree;
        }

        static bool Numbered(CullTree t) { return t.Duplicates == 0 && t.Leaves.Keys.All(n => n >= 1 && n <= t.Leaves.Count); }

        // The tree's own consistency, as in every shipped tree: the pre-order walk from node 0 uses every node but the
        // 256 zero ones that end the list; an inner node's Max is the larger of its children's; a leaf's SphereKeyW is
        // max(5, ceil(4 x (Max + 1/8))) or one more (the radius is Max plus 1/8 m, rounded up to 1/4 m, at least 1.25 m;
        // the one more presumably from the centre's 1/16 m rounding).
        static void CheckTree(Ctx c, CullTree t, string which)
        {
            int count = t.Right.Count, badMax = 0, badRadius = 0;
            bool broken = false;
            // The pre-order walk from node 0, with a stack of the inner nodes being walked (no recursion: a tree of
            // any depth is walked). enter(i) takes node i: a leaf, or a node that breaks the walk, gives the index
            // after it; an inner node is pushed (-1) and its subtree's end comes back once both children are walked.
            var frames = new List<int[]>();                     // { node, 0: left next / 1: right next / 2: done }
            Func<long, int> enter = at =>
            {
                if (at < 0 || at >= count || broken) { broken = true; return count; }
                int i = (int)at;
                uint r = t.Right[i];
                if ((r >> 31) != 0)
                {
                    double w = Math.Max(5.0, Math.Ceiling(4.0 * ((double)t.Max[i] + 0.125)));
                    if (t.W[i] != w && t.W[i] != w + 1) badRadius++;
                    return i + 1;
                }
                if (r == 0) { broken = true; return count; }
                frames.Add(new[] { i, 0 });
                return -1;
            };
            int end = count > 0 ? enter(0) : 0;
            while (frames.Count > 0)
            {
                var fr = frames[frames.Count - 1];
                int i = fr[0];
                long right = (long)i + t.Right[i];
                if (fr[1] == 0)
                {
                    fr[1] = 1;
                    end = enter(i + 1);
                    if (end < 0) continue;
                }
                if (fr[1] == 1)
                {
                    if (end != right) broken = true;           // the left subtree must end where the right one starts
                    fr[1] = 2;
                    end = enter(right);
                    if (end < 0) continue;
                }
                if (!broken && t.Max[i] != Math.Max(t.Max[i + 1], t.Max[(int)right])) badMax++;
                frames.RemoveAt(frames.Count - 1);                // end: the index after this subtree
            }
            int last = end;
            bool zeros = !broken && last == count - 256 && Enumerable.Range(last, 256).All(k => t.Right[k] == 0 && t.Max[k] == 0 && t.W[k] == 0);
            if (broken || !zeros)
                c.Error(t.Partition, "the " + which + " cull tree's nodes are not one pre-order tree followed by 256 zero nodes (as in every shipped tree)");
            if (badMax > 0)
                c.Warn(t.Partition, badMax + " inner nodes of the " + which + " cull tree have a Max that is not the larger of their children's (as it is in every shipped tree)");
            if (badRadius > 0)
                c.Warn(t.Partition, badRadius + " leaves of the " + which + " cull tree have a SphereKeyW that is not max(5, ceil(4 x (Max + 1/8))) or one more, as in all 23,989 shipped leaves");
        }

        public static void CheckCullTrees(DbxFile f, Dictionary<string, string> index, List<LevelCheck.Finding> found)
        {
            var c = new Ctx { F = f, Found = found };
            var all = f.Instances().ToList();
            foreach (var i in all) { string g = f.Get(i, "guid") ?? ""; if (!c.ByGuid.ContainsKey(g)) c.ByGuid[g] = i; }
            var level = all.FirstOrDefault(i => c.TypeOf(i) == "LevelData");
            if (level == null) return;
            int groups = all.Sum(i => Items(f, i, "SpawnDatas").Count);
            var client = LoadTree(c, level, "ClientStaticCullTree", index);
            if (client != null)
            {
                if (!Numbered(client))
                    c.Error(client.Partition, "the client cull tree's leaves are not numbered 1.." + client.Leaves.Count + ", each once (as in every shipped level)");
                CheckTree(c, client, "client");
                if (client.Leaves.Count != groups || client.EnumOffset != 0)
                    c.Error(client.Partition, "the client cull tree has " + client.Leaves.Count + " leaves and EnumOffset " + client.EnumOffset + "; this level has "
                            + groups + " model-group and forest instances (SpawnDatas): in every shipped level it has one leaf per instance and EnumOffset 0");
            }
            var tree = LoadTree(c, level, "ExclusiveStaticCullTree", index);
            if (tree == null) return;
            int last = tree.EnumOffset + tree.Leaves.Count;
            CheckTree(c, tree, "exclusive");
            if (!Numbered(tree))
                c.Error(tree.Partition, "the exclusive cull tree's leaves are not numbered 1.." + tree.Leaves.Count + ", each once (as in every shipped level)");
            if (tree.EnumOffset != groups)
                c.Error(tree.Partition, "the exclusive cull tree's EnumOffset is " + tree.EnumOffset + ", not the level's " + groups
                        + " model-group and forest instances (as in every shipped level: placed objects' slots follow them)");
            var placed = PlacedObject.All(f).Where(o => o.Slot != null && o.SlotValue != 0).OrderBy(o => o.SlotValue).ToList();
            var outside = placed.Where(o => o.SlotValue <= tree.EnumOffset || o.SlotValue > last).ToList();
            if (outside.Count > 0)
                c.Error("cull slots", outside.Count + " placed objects have a slot outside the exclusive cull tree's " + (tree.EnumOffset + 1) + ".." + last
                        + " (e.g. " + string.Join(", ", outside.Take(3).Select(o => o.Blueprint + " " + o.SlotValue + " " + o.Guid))
                        + "): the tree has no leaf for them. A new static object needs a rebuilt tree; slot 0 leaves it out of the trees");
            // tiling: two leaves for a blueprint with physics, one without
            int next = tree.EnumOffset + 1;
            string missingBlueprint = null, gap = null;
            int unequal = 0;
            foreach (var o in placed)
            {
                var refEl = Child(f, o.Instance, "ReferencedObject");
                string r = refEl == null ? null : f.Get(refEl, "ref"), path;
                if (r == null || r.IndexOf('/') < 0 || !index.TryGetValue(r.Substring(0, r.LastIndexOf('/')), out path))
                {
                    missingBlueprint = r == null ? "placed object " + o.Guid + " has no ReferencedObject"
                                     : r.IndexOf('/') < 0 ? "placed object " + o.Guid + " refers to " + r + ", not to a blueprint in another file"
                                     : r + " is not in the given folders";
                    break;
                }
                int width = Width(path);
                int[] k1, k2;
                if (width == 2 && tree.Leaves.TryGetValue(o.SlotValue - tree.EnumOffset, out k1) && tree.Leaves.TryGetValue(o.SlotValue - tree.EnumOffset + 1, out k2)
                    && (!k1.SequenceEqual(k2) || tree.LeafMax[o.SlotValue - tree.EnumOffset] != tree.LeafMax[o.SlotValue - tree.EnumOffset + 1]))
                    unequal++;
                if (o.SlotValue != next && gap == null)
                    gap = o.Blueprint + " " + o.Guid + " has slot " + o.SlotValue + ", expected " + next;
                next = o.SlotValue + width;
            }
            if (missingBlueprint != null)
                c.Warn("cull slots", "not checked against the exclusive cull tree's leaves: " + missingBlueprint);
            else if (gap != null || next != last + 1)
                c.Warn("cull slots", "placed objects' slots do not cover the exclusive cull tree's leaves " + (tree.EnumOffset + 1) + ".." + last + " exactly ("
                       + (gap ?? "the last ends at " + (next - 1)) + "); in every shipped level each slotted object takes two leaves when its blueprint has a PhysicsEntityData, else one");
            if (unequal > 0)
                c.Warn(tree.Partition, unequal + " objects with physics have two leaves that differ; in every shipped tree an object's two leaves are the same sphere and Max");
            // each leaf's sphere at its object
            var moved = new List<PlacedObject>();
            foreach (var o in placed)
            {
                int[] k;
                if (!tree.Leaves.TryGetValue(o.SlotValue - tree.EnumOffset, out k)) continue;
                double dx = k[0] / 16.0 - o.X, dy = k[1] / 16.0 - o.Y, dz = k[2] / 16.0 - o.Z;
                if (Math.Sqrt(dx * dx + dy * dy + dz * dz) > 1.55 * k[3] / 4.0 + 0.1) moved.Add(o);
            }
            if (moved.Count > 0)
                c.Warn("cull slots", moved.Count + " placed objects are away from their slot's cull-tree leaf (e.g. " + string.Join(", ", moved.Take(3).Select(o => o.Blueprint + " " + o.Guid))
                       + "): moved since the tree was built, or given another object's slot; in every shipped level an object's origin lies within 1.55 radii (SphereKeyW / 4) of its leaf's centre");
        }

        static readonly Dictionary<string, int> widths = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        static int Width(string path)
        {
            int w;
            if (widths.TryGetValue(path, out w)) return w;
            var d = DbxFile.Read(path);
            w = d.IsBinary && d.Instances().Any(i => (d.Get(i, "type") ?? "").EndsWith(".PhysicsEntityData")) ? 2 : 1;
            widths[path] = w;
            return w;
        }

        // a 2-byte signed number (0 when the field is missing or not one)
        static int Short16(DbxFile f, Element e, string name)
        {
            var x = Child(f, e, name);
            if (x == null || x.Kind != Element.Numbers) return 0;
            int pos = 0, n = Varint.Read(x.Payload, ref pos), size = Varint.Read(x.Payload, ref pos);
            return n == 1 && size == 2 ? (short)(x.Payload[pos] << 8 | x.Payload[pos + 1]) : 0;
        }

        static string Unindexed(string path)
        {
            var b = new StringBuilder();
            int depth = 0;
            foreach (char ch in path)
            {
                if (ch == '[') depth++;
                else if (ch == ']') depth--;
                else if (depth == 0) b.Append(ch);
            }
            return b.ToString();
        }

        static Element Child(DbxFile f, Element e, string name)
        {
            return e.IsContainer ? e.Children.FirstOrDefault(x => f.Get(x, "name") == name) : null;
        }

        static List<Element> Items(DbxFile f, Element e, string name)
        {
            var a = Child(f, e, name);
            return a != null && a.IsContainer ? a.Children : new List<Element>();
        }

        static string Text(DbxFile f, Element e, string name)
        {
            var x = Child(f, e, name);
            return x == null || x.IsContainer ? null : XmlExport.ValueText(f, x);
        }

        // a 4-byte whole number (int.MinValue when the field is missing or not one)
        static int Int(DbxFile f, Element e, string name)
        {
            var x = Child(f, e, name);
            if (x == null || x.Kind != Element.Numbers) return int.MinValue;
            int pos = 0, n = Varint.Read(x.Payload, ref pos), size = Varint.Read(x.Payload, ref pos);
            return n == 1 && size == 4 ? (int)BigEndian.U32(x.Payload, pos) : int.MinValue;
        }
    }

    static class Program
    {
        // exit codes: each command's own (0 done; 1 failed or, for check, errors found; 3 import with guesses), 2 = bad
        // usage, or the command could not run (a file damaged, missing or in use, a write refused): explained on stderr
        static int Main(string[] args)
        {
            ToolLog.Start("dbxedit");
            try { return Run(args); }
            catch (Exception e) { return ToolLog.Fail(e); }       // unexpected: logged and explained, exit code 2
        }

        static int Run(string[] args)
        {
            if (args.Length >= 2 && args[0] == "xml") return Xml(args.Skip(1));
            if (args.Length >= 2 && args[0] == "roundtrip") return Roundtrip(args[1]);
            if (args.Length >= 3 && args[0] == "edit") return Edit(args);
            if (args.Length >= 3 && args[0] == "import") return Import(args);
            if (args.Length >= 2 && args[0] == "check") return Check(args);
            if (args.Length >= 3 && args[0] == "fixslots") return FixSlots(args);
            Console.Error.WriteLine("usage: DbxEdit xml <file|folder> ... | roundtrip <file|folder> | edit <in.dbx> <out.dbx> <command|@file> ...");
            Console.Error.WriteLine("       DbxEdit import <in.xml> <out.dbx> [--like <original.dbx>] [--types <file.dbx|folder>] ...");
            Console.Error.WriteLine("       DbxEdit check <level.dbx> [--rules bf1943|bfbc2] [--slots-from <level.dbx>] [--bundle <folder>] ...");
            Console.Error.WriteLine("       DbxEdit fixslots <in.dbx> <out.dbx> --from <level.dbx>");
            Console.Error.WriteLine("  edit commands: replace= replaceprefix= drop= dropfield= graft= copyfield= additem= setfield= set= partguid= (README, FORMAT.md section 5)");
            return 2;
        }

        // The .dbx files of a folder (recursively), or the file named. Only the extension .dbx: Windows' "*.dbx"
        // pattern also matches longer extensions (.dbxold), whose XML would take the .dbx file's name.
        internal static string[] DbxFiles(string path)
        {
            if (!Directory.Exists(path)) return new[] { path };
            return Directory.GetFiles(path, "*.dbx", SearchOption.AllDirectories)
                            .Where(p => string.Equals(Path.GetExtension(p), ".dbx", StringComparison.OrdinalIgnoreCase)).ToArray();
        }

        // A zero-byte file is a bundle's "*deleted*" entry, as older FbrbTool versions unpacked them (current
        // ones list them in fbrb.manifest only): there is nothing to read, and it is not an error.
        static bool DeletedEntry(string f) { return new FileInfo(f).Length == 0; }

        static string DeletedNote(int n)
        {
            return n == 0 ? "" : ", skipped " + n + " empty file(s) (*deleted* bundle entries)";
        }

        // A binary .dbx file the recursive commands can take; null (and a message on stderr) when it is not binary.
        static DbxFile Binary(string path)
        {
            var f = DbxFile.Read(path);
            if (!f.IsBinary) { Console.Error.WriteLine("not a binary dbx: " + path); return null; }
            return f.Walkable(path);
        }

        // exit 0 = every binary file converted, 1 = one or more failed (each named on stderr)
        static int Xml(IEnumerable<string> paths)
        {
            int converted = 0, deleted = 0, notBinary = 0, failed = 0;
            foreach (string f in paths.SelectMany(DbxFiles))
            {
                try
                {
                    if (DeletedEntry(f)) { deleted++; continue; }
                    byte[] data = File.ReadAllBytes(f);
                    if (data.Length < 8 || Encoding.ASCII.GetString(data, 0, 8) != "{binary}") { notBinary++; continue; }
                    SafeWrite.Text(Path.ChangeExtension(f, ".xml"), XmlExport.Convert(data));
                    converted++;
                }
                catch (Exception e)
                {
                    failed++;
                    Console.Error.WriteLine(f + ": " + e.Message);
                    ToolLog.Warn("xml: " + f + ": " + e.Message);
                }
            }
            Console.WriteLine("converted " + converted + " file(s)" + (notBinary > 0 ? ", " + notBinary + " not binary (left as they are)" : "")
                              + (failed > 0 ? ", " + failed + " FAILED" : "") + DeletedNote(deleted));
            return failed == 0 ? 0 : 1;
        }

        // import <in.xml> <out.dbx> [--like <original.dbx>] [--types <file.dbx|folder>]...
        // Reference: --like, else <in>.dbx next to the XML. Exit 0 = done, 1 = failed, 3 = written with guessed types.
        static int Import(string[] args)
        {
            string xml = args[1], output = args[2], like = null;
            var typePaths = new List<string>();
            for (int i = 3; i < args.Length; i++)
            {
                if (args[i] == "--like" && i + 1 < args.Length) like = args[++i];
                else if (args[i] == "--types" && i + 1 < args.Length) typePaths.Add(args[++i]);
                else { Console.Error.WriteLine("import: unknown option " + args[i]); return 2; }
            }
            if (like == null && File.Exists(Path.ChangeExtension(xml, ".dbx"))) like = Path.ChangeExtension(xml, ".dbx");
            try
            {
                DbxFile reference = null;
                if (like != null)
                {
                    reference = DbxFile.Read(like);
                    if (!reference.IsBinary) { Console.Error.WriteLine("import: not a binary dbx: " + like); return 1; }
                    reference.Walkable(like);
                }
                var typeFiles = typePaths.SelectMany(DbxFiles).Select(p => new KeyValuePair<string, DbxFile>(p, DbxFile.Read(p)))
                                         .Where(d => d.Value.IsBinary).Select(d => d.Value.Walkable(d.Key)).ToList();
                XmlImport report;
                var result = XmlImport.Import(xml, reference, typeFiles, out report);
                SafeWrite.Bytes(output, result.Serialize());
                Console.WriteLine("import " + xml + " -> " + output + (like != null ? " (types from " + like + ")" : "") + ": "
                                  + report.Kept + " unchanged, " + report.Changed + " changed, " + report.Added + " new, "
                                  + report.Removed + " removed"
                                  + (report.Rounded > 0 ? "; " + report.Rounded + " decimal(s) rounded to the nearest 4-byte value" : ""));
                if (report.Guesses.Count == 0) return 0;
                foreach (string g in report.Guesses.Take(50)) Console.Error.WriteLine("guess: " + g);
                Console.Error.WriteLine(report.Guesses.Count + " value(s) typed by guess - check them, or give the original file (--like) or more files (--types)");
                return 3;
            }
            catch (Exception e)
            {
                if (!(e is InvalidDataException || e is System.Xml.XmlException || e is IOException || e is UnauthorizedAccessException)) throw;
                Console.Error.WriteLine("import failed: " + e.Message);
                ToolLog.Error(e, "import");
                return 1;
            }
        }

        // check <level.dbx> [--rules bf1943|bfbc2] [--slots-from <level.dbx>] [--bundle <folder>]...
        // exit 0 = no errors, 1 = errors found, 2 = the check could not run (bad usage, a file damaged or missing).
        // The rules are chosen from the file (LevelCheck.Detect) unless given.
        static int Check(string[] args)
        {
            DbxFile slotsFrom = null;
            string rules = null;
            var bundles = new List<string>();
            for (int i = 2; i < args.Length; i++)
            {
                if (args[i] == "--slots-from" && i + 1 < args.Length)
                {
                    slotsFrom = Binary(args[++i]);
                    if (slotsFrom == null) return 2;
                }
                else if (args[i] == "--bundle" && i + 1 < args.Length)
                {
                    if (!Directory.Exists(args[i + 1])) { Console.Error.WriteLine("check: --bundle " + args[i + 1] + " is not a folder"); return 2; }
                    bundles.Add(args[++i]);
                }
                else if (args[i] == "--rules" && i + 1 < args.Length && (args[i + 1] == LevelCheck.Bf1943 || args[i + 1] == LevelCheck.Bfbc2)) rules = args[++i];
                else { Console.Error.WriteLine("check: unknown option " + args[i] + " (options: --rules bf1943|bfbc2, --slots-from <level.dbx>, --bundle <folder>)"); return 2; }
            }
            var f = Binary(args[1]);
            if (f == null) return 2;
            string chosen = rules ?? LevelCheck.Detect(f);
            var found = LevelCheck.Run(f, slotsFrom, bundles, chosen);
            foreach (var x in found.OrderBy(x => x.Error ? 0 : 1)) Console.WriteLine(x);
            int errors = found.Count(x => x.Error);
            Console.WriteLine("check " + args[1] + " (" + RulesName(chosen) + " rules" + (rules == null ? ", from the file" : "") + "): "
                              + errors + " error(s), " + (found.Count - errors) + " warning(s)");
            return errors > 0 ? 1 : 0;
        }

        internal static string RulesName(string rules) { return rules == LevelCheck.Bfbc2 ? "BFBC2" : "BF1943"; }

        // fixslots <in.dbx> <out.dbx> --from <level.dbx>
        static int FixSlots(string[] args)
        {
            if (args.Length != 5 || args[3] != "--from") { Console.Error.WriteLine("usage: DbxEdit fixslots <in.dbx> <out.dbx> --from <level.dbx>"); return 2; }
            var f = Binary(args[1]);
            var from = f == null ? null : Binary(args[4]);
            if (f == null || from == null) return 2;
            var r = SlotFixer.Fix(f, from);
            Console.WriteLine("fixslots: " + r.Changed + " slot(s) changed; matched by guid " + r.ByGuid + ", by blueprint and position "
                              + r.ByPosition + ", nearest within 50 m " + r.ByNearest + ", no counterpart (slot 0) " + r.NoCounterpart);
            foreach (string u in r.Unmatched.Take(30)) Console.WriteLine("  no counterpart: " + u);
            SafeWrite.Bytes(args[2], f.Serialize());
            return 0;
        }

        static int Roundtrip(string path)
        {
            int same = 0, different = 0, skipped = 0, deleted = 0;
            foreach (string f in DbxFiles(path))
            {
                if (DeletedEntry(f)) { deleted++; continue; }
                DbxFile d;
                try { d = new DbxFile(f); }
                catch (Exception e)
                {
                    Console.WriteLine("ERROR " + f + ": " + e.Message);
                    ToolLog.Warn("roundtrip: " + f + ": " + e.Message);
                    different++;
                    continue;
                }
                if (!d.IsBinary) { skipped++; continue; }
                byte[] written = d.Serialize();
                if (written.SequenceEqual(d.Original)) same++;
                else if (++different <= 10) Console.WriteLine("DIFF " + f + " (" + d.Original.Length + " -> " + written.Length + ")");
            }
            Console.WriteLine("roundtrip: " + same + " identical, " + different + " different, " + skipped + " not binary" + DeletedNote(deleted));
            return different == 0 ? 0 : 1;
        }

        // each command's arguments ('|'-separated after '='): how many it takes, and how many of the first must not be empty
        static readonly Dictionary<string, string> Usage = new Dictionary<string, string> {
            { "replace=", "replace=<old>|<new>" }, { "replaceprefix=", "replaceprefix=<old>|<new>[|<except>...]" },
            { "drop=", "drop=<guid>" }, { "dropfield=", "dropfield=<name>" }, { "partguid=", "partguid=<guid>" },
            { "copyfield=", "copyfield=<src.dbx>|<src guid>|<field>|<guid>" }, { "set=", "set=<field>|<true|false|0-255>" },
            { "setfield=", "setfield=<guid>|<field>|<value>" }, { "additem=", "additem=<guid>|<array>|<ref>[|<before ref>]" },
            { "graft=", "graft=<src.dbx>|<guid>[|<before guid>]" } };

        static bool Arguments(string verb, string[] p, int min, int max, int nonEmpty)
        {
            if (p.Length >= min && p.Length <= max && p.Take(nonEmpty).All(x => x.Length > 0)) return true;
            Console.Error.WriteLine("edit: " + verb + string.Join("|", p) + " - usage: " + Usage[verb]);
            return false;
        }

        // exit 0 = written; 1 = a command found nothing to change (nothing written); 2 = bad usage, or the edit could not run
        static int Edit(string[] args)
        {
            var file = Binary(args[1]);
            if (file == null) return 2;
            var commands = new List<string>();
            foreach (string a in args.Skip(3))
            {
                if (a.StartsWith("@"))
                    commands.AddRange(File.ReadAllLines(a.Substring(1)).Select(l => l.Trim()).Where(l => l.Length > 0 && !l.StartsWith("#")));
                else commands.Add(a);
            }
            var editor = new Editor(file);
            var sources = new Dictionary<string, DbxFile>();
            Func<string, DbxFile> source = path =>
            {
                DbxFile s;
                if (sources.TryGetValue(path, out s)) return s;
                s = DbxFile.Read(path);
                if (!s.IsBinary) throw new InvalidDataException("not a binary dbx: " + path);
                return sources[path] = s.Walkable(path);
            };
            int fieldsSet = 0;
            foreach (string c in commands)
            {
                int eq = c.IndexOf('=');
                string verb = eq < 0 ? c : c.Substring(0, eq + 1);
                string rest = eq < 0 ? "" : c.Substring(eq + 1);
                string[] p = eq < 0 ? new string[0] : rest.Split('|');
                switch (verb)
                {
                    case "replace=":
                    {
                        if (!Arguments(verb, p, 2, 2, 1)) return 2;
                        int hits = editor.Replace(p[0], p[1]);
                        Console.WriteLine("replace " + p[0] + " -> " + p[1] + ": " + hits + " string(s)");
                        if (hits == 0) { Console.Error.WriteLine("not found: " + p[0]); return 1; }
                        break;
                    }
                    case "drop=":
                    {
                        if (!Arguments(verb, new[] { rest }, 1, 1, 1)) return 2;
                        int references;
                        int removed = editor.Drop(rest, out references);
                        Console.WriteLine("drop " + rest + ": " + removed + " instance(s), " + references + " reference(s)");
                        if (removed == 0) { Console.Error.WriteLine("no instance " + rest); return 1; }
                        break;
                    }
                    case "replaceprefix=":
                    {
                        if (!Arguments(verb, p, 2, int.MaxValue, 1)) return 2;
                        int hits = editor.ReplacePrefix(p[0], p[1], p.Skip(2).ToList());
                        Console.WriteLine("replaceprefix " + p[0] + " -> " + p[1] + ": " + hits + " string(s)");
                        if (hits == 0) { Console.Error.WriteLine("no string starts with " + p[0]); return 1; }
                        break;
                    }
                    case "partguid=":
                        if (!Arguments(verb, new[] { rest }, 1, 1, 1)) return 2;
                        editor.SetPartitionGuid(rest);
                        Console.WriteLine("partguid " + rest);
                        break;
                    case "copyfield=":
                    {
                        if (!Arguments(verb, p, 4, 4, 4)) return 2;
                        bool ok = editor.CopyField(source(p[0]), p[1], p[2], p[3]);
                        Console.WriteLine("copyfield " + p[2] + ": " + (ok ? "copied" : "NOT FOUND"));
                        if (!ok) return 1;
                        break;
                    }
                    case "set=":
                    {
                        if (!Arguments(verb, p, 2, 2, 2)) return 2;
                        byte v;
                        if (p[1] == "true") v = 1;
                        else if (p[1] == "false") v = 0;
                        else if (!byte.TryParse(p[1], NumberStyles.None, CultureInfo.InvariantCulture, out v))
                        { Console.Error.WriteLine("set: " + p[1] + " is not true, false or 0-255"); return 2; }
                        int n = editor.SetByte(p[0], v);
                        Console.WriteLine("set " + p[0] + " = " + p[1] + ": " + n + " field(s)");
                        if (n == 0) { Console.Error.WriteLine("no bool/byte field " + p[0]); return 1; }
                        break;
                    }
                    case "setfield=":
                    {
                        if (!Arguments(verb, p, 3, int.MaxValue, 2)) return 2;
                        string value = string.Join("|", p.Skip(2));          // a value may hold '|'
                        bool rounded;
                        try
                        {
                            if (!editor.SetField(p[0], p[1], value, out rounded)) { Console.Error.WriteLine("setfield: no settable field " + p[1] + " in " + p[0]); return 1; }
                        }
                        catch (InvalidDataException e) { Console.Error.WriteLine("setfield " + p[0] + "|" + p[1] + ": " + e.Message); return 1; }
                        if (rounded) Console.WriteLine("setfield " + p[1] + ": rounded to the nearest 4-byte value");
                        fieldsSet++;
                        break;
                    }
                    case "additem=":
                    {
                        if (!Arguments(verb, p, 3, 4, 3)) return 2;
                        bool ok = editor.AddItem(p[0], p[1], p[2], p.Length > 3 && p[3].Length > 0 ? p[3] : null);
                        Console.WriteLine("additem " + p[2] + " -> " + p[1] + ": " + (ok ? "added" : "NOT FOUND"));
                        if (!ok) return 1;
                        break;
                    }
                    case "dropfield=":
                        if (!Arguments(verb, new[] { rest }, 1, 1, 1)) return 2;
                        Console.WriteLine("dropfield " + rest + ": " + editor.DropField(rest) + " element(s)");
                        break;
                    case "graft=":
                    {
                        if (!Arguments(verb, p, 2, 3, 2)) return 2;
                        int n = editor.Graft(source(p[0]), p[1], p.Length > 2 && p[2].Length > 0 ? p[2] : null);
                        Console.WriteLine("graft " + p[1] + " from " + Path.GetFileName(p[0]) + ": " + n + " instance(s)");
                        if (n == 0 && file.Instance(p[1]) == null) { Console.Error.WriteLine("nothing grafted for " + p[1]); return 1; }
                        break;
                    }
                    default:
                        Console.Error.WriteLine("unknown command " + c);
                        return 2;
                }
            }
            if (fieldsSet > 0) Console.WriteLine("setfield: " + fieldsSet + " field(s)");
            SafeWrite.Bytes(args[2], file.Serialize());
            return 0;
        }
    }

    // A write the tools refuse: into the game folder, which they only read.
    public sealed class WriteRefusedException : IOException
    {
        public WriteRefusedException(string message) : base(message) { }
    }

    // Every file the C# tools write goes through here, as core fbrb1943.py's checked() and write_file() do:
    // - never into the BF1943 game folder (core game1943.find_game: BF1943_GAME, else the Xenia kit's dist\xenon in
    //   the suite; a folder counts when it has levels\), however the path is cased and through whatever link or
    //   junction it leads there (both sides resolved as os.path.realpath + normcase do);
    // - written to a temporary file beside the target and moved into its place (MoveFileEx, as os.replace): a write
    //   that fails leaves the old file, and a hard link at the target only loses its name - the file it linked to,
    //   in the game folder or anywhere, is left as it was. Writing over the input is therefore safe (it was read whole).
    public static class SafeWrite
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool MoveFileEx(string from, string to, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFinalPathNameByHandle(IntPtr handle, StringBuilder path, uint size, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);

        const uint MoveReplaceExisting = 1, MoveWriteThrough = 8, ShareAll = 7, OpenExisting = 3, BackupSemantics = 0x02000000;

        public static void Bytes(string path, byte[] data) { Write(path, data); }

        // UTF-8 without a byte-order mark (as File.WriteAllText)
        public static void Text(string path, string text) { Write(path, new UTF8Encoding(false).GetBytes(text)); }

        // The folder core game1943.find_game() finds, or null when there is none.
        public static string GameFolder()
        {
            var candidates = new List<string>();
            string env = Environment.GetEnvironmentVariable("BF1943_GAME");
            if (!string.IsNullOrEmpty(env)) candidates.Add(env);
            string suite = SuiteRoot();
            if (suite != null) candidates.Add(Path.Combine(suite, "bf1943-xenia-kit", "output", "title", "dist", "xenon"));
            foreach (string p in candidates)
            {
                try { if (Directory.Exists(Path.Combine(p, "levels"))) return Path.GetFullPath(p); }
                catch (ArgumentException) { }
                catch (NotSupportedException) { }
            }
            return null;
        }

        // the suite folder this exe was built in (the nearest folder above it with core\bfbc2core\game1943.py), or null
        static string SuiteRoot()
        {
            for (string d = AppDomain.CurrentDomain.BaseDirectory; !string.IsNullOrEmpty(d); d = Path.GetDirectoryName(d))
                if (File.Exists(Path.Combine(d, "core", "bfbc2core", "game1943.py"))) return d;
            return null;
        }

        // path as Windows resolves it: links and junctions followed for the part that exists, case folded
        public static string Canonical(string path)
        {
            string full = Path.GetFullPath(path);
            var tail = new List<string>();
            string cur = full;
            while (cur != null && !File.Exists(cur) && !Directory.Exists(cur))
            {
                tail.Insert(0, Path.GetFileName(cur));
                cur = Path.GetDirectoryName(cur);
            }
            string resolved = cur == null ? full : (Final(cur) ?? cur);
            foreach (string t in tail) resolved = Path.Combine(resolved, t);
            return resolved.TrimEnd('\\', '/').ToLowerInvariant();
        }

        static string Final(string path)
        {
            IntPtr h = CreateFile(path, 0, ShareAll, IntPtr.Zero, OpenExisting, BackupSemantics, IntPtr.Zero);
            if (h == new IntPtr(-1)) return null;
            try
            {
                var b = new StringBuilder(1024);
                uint n = GetFinalPathNameByHandle(h, b, (uint)b.Capacity, 0);
                if (n >= b.Capacity)
                {
                    b = new StringBuilder((int)n + 1);
                    n = GetFinalPathNameByHandle(h, b, (uint)b.Capacity, 0);
                }
                if (n == 0 || n >= b.Capacity) return null;
                string s = b.ToString();
                if (s.StartsWith(@"\\?\UNC\")) return @"\\" + s.Substring(8);
                if (s.StartsWith(@"\\?\")) return s.Substring(4);
                return s;
            }
            finally { CloseHandle(h); }
        }

        // True when path is folder or lies in it (both resolved); false on another drive.
        public static bool Contains(string folder, string path)
        {
            string f = Canonical(folder), p = Canonical(path);
            return p == f || p.StartsWith(f + "\\", StringComparison.Ordinal);
        }

        static void Write(string path, byte[] data)
        {
            string full = Path.GetFullPath(path);
            string game = GameFolder();
            if (game != null && Contains(game, full))
                throw new WriteRefusedException(full + " would be written in the game folder (" + game + ", also through a link or junction), "
                                                + "which the tools only read; not written");
            if (Directory.Exists(full)) throw new IOException(full + " is a folder; not written");
            string tmp = Path.Combine(Path.GetDirectoryName(full), ".dbxedit-" + System.Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                using (var fs = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    fs.Write(data, 0, data.Length);
                    fs.Flush(true);
                }
                if (!MoveFileEx(tmp, full, MoveReplaceExisting | MoveWriteThrough))
                {
                    int err = Marshal.GetLastWin32Error();
                    string why = new System.ComponentModel.Win32Exception(err).Message;
                    if (err == 5) throw new UnauthorizedAccessException("cannot replace " + full + ": " + why);
                    throw new IOException("cannot replace " + full + ": " + why, unchecked((int)0x80070000) | err);
                }
            }
            catch
            {
                try { if (File.Exists(tmp)) File.Delete(tmp); } catch (Exception) { }
                throw;
            }
        }
    }

    // Logging and error reports, as the suite's Python tools keep them (core/bfbc2core/errors.py): a log per tool
    // in %LOCALAPPDATA%\bf1943-tool-suite\logs\<tool>-<date>.log (the 20 newest kept), and errors explained in
    // words - the likely cause and what to do - with the log's path.
    public static class ToolLog
    {
        static string tool = "tool", path;
        const int Keep = 20;

        public static string LogPath { get { return path; } }

        public static void Start(string name)
        {
            tool = name;
            try
            {
                // LOCALAPPDATA as the Python tools read it (the same folder; tests point it elsewhere)
                string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
                if (string.IsNullOrEmpty(local)) local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string dir = Path.Combine(Path.Combine(local,
                                                       "bf1943-tool-suite"), "logs");
                Directory.CreateDirectory(dir);
                path = Path.Combine(dir, name + "-" + DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + ".log");
                foreach (string old in Directory.GetFiles(dir, name + "-*.log").OrderByDescending(p => p, StringComparer.OrdinalIgnoreCase).Skip(Keep))
                    try { File.Delete(old); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                Info("--- " + name + " started: " + string.Join(" ", Environment.GetCommandLineArgs().Skip(1)) + " ---");
            }
            catch (Exception) { path = null; }               // no log folder: the tool still runs
        }

        static void Write(string level, string message)
        {
            if (path == null) return;
            try
            {
                File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss,fff", CultureInfo.InvariantCulture) + " " +
                                         level.PadRight(7) + " " + message + Environment.NewLine);
            }
            catch (Exception) { }
        }

        public static void Info(string message) { Write("INFO", message); }
        public static void Warn(string message) { Write("WARNING", message); }

        public static void Error(Exception e, string where)
        {
            string[] ce = Explain(e);
            Write("ERROR", tool + " error" + (where != null ? " in " + where : "") + ": " + ce[0] + Environment.NewLine + e);
        }

        // { cause, what to do }
        public static string[] Explain(Exception e)
        {
            if (e is WriteRefusedException)
                return new[] { e.Message, "Write the output outside the game folder: the tools only read it (extract or copy what you edit elsewhere)." };
            var io = e as IOException;
            int code = io != null ? (io.HResult & 0xFFFF) : 0;
            if (io != null && (code == 32 || code == 33))
                return new[] { "A file is in use by another program: " + e.Message, "Close the program using it (the game, an editor) and try again." };
            if (e is UnauthorizedAccessException)
                return new[] { "A file or folder could not be written or read: " + e.Message, "Check it is not read-only, or choose another folder." };
            if (e is FileNotFoundException || e is DirectoryNotFoundException)
                return new[] { "A file the tool needs is missing: " + e.Message, "Check the path." };
            if (e is InvalidDataException || e is System.IO.EndOfStreamException || e is ArgumentOutOfRangeException || e is IndexOutOfRangeException)
                return new[] { "The file is not the kind the tool reads, or it is damaged: " + e.Message, "Check it is the right file; unpack it again from the game if it is damaged." };
            if (e is OutOfMemoryException)
                return new[] { "The PC ran out of memory.", "Close other programs, or work on fewer files at once." };
            return new[] { e.GetType().Name + ": " + e.Message, "If it happens again, send the log file to the tool's author." };
        }

        // The text a user sees: cause, what to do, where the details are.
        public static string Report(Exception e)
        {
            string[] ce = Explain(e);
            return ce[0] + Environment.NewLine + Environment.NewLine + "What to do: " + ce[1] + Environment.NewLine + Environment.NewLine +
                   "Details were saved to:" + Environment.NewLine + (path ?? "(no log file)");
        }

        // A command-line tool's last word on an unexpected error: logged, explained on stderr, exit code 2 (DbxEdit's
        // and LevelDiff's "could not run"; 1 is a result: failed edits, a level's errors, differences).
        public static int Fail(Exception e)
        {
            Error(e, null);
            string[] ce = Explain(e);
            Console.Error.WriteLine("error: " + ce[0]);
            Console.Error.WriteLine("  what to do: " + ce[1]);
            Console.Error.WriteLine("  details: " + (path ?? "(no log file)"));
            return 2;
        }
    }
}
