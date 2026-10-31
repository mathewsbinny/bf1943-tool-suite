// DbxEdit: binary .dbx -> XML (FORMAT.md section 3).
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
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
}
