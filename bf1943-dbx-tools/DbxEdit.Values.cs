// DbxEdit: value typing - field types learned from files (TypeBook) and the bytes of a typed value (XmlImport.EncodeValue).
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
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

    // XML -> binary (DbxEdit.XmlImport.cs): the value bytes of a text, typed like its field (EncodeValue, the import's
    // Encode).
    sealed partial class XmlImport
    {
        // The value bytes for a new text of an existing element, typed like it (setfield, DbxEditGUI): text; numbers of
        // the stored width; a 4-byte number stays the kind its field holds - an integer field takes only integers (a
        // whole-number field such as a hash also 2147483648-4294967295, its unsigned form), a decimal field only
        // decimals. The kind is the stored values' (a stored 0 is either, so it says nothing), else the field's
        // elsewhere in the file, else decimal. note: null, or what the caller should tell ("rounded to the nearest
        // 4-byte value", a NaN or an infinity written). InvalidDataException when the value does not fit the field.
        public static byte[] EncodeValue(DbxFile f, Element e, string text, out string note)
        {
            var imp = new XmlImport(f, f, null) { strict = true };
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
            var notes = imp.Notes.Select(n => n.StartsWith("line 0: ") ? n.Substring(8) : n).ToList();
            if (imp.Rounded > 0) notes.Insert(0, "rounded to the nearest 4-byte value");
            note = notes.Count == 0 ? null : string.Join("; ", notes);
            return payload;
        }

        // setfield / DbxEditGUI: typed values keep their field's kind (refused otherwise), not the import's reading
        bool strict;

        // the field types of a file, learned once (only a field whose stored values are all 0 needs them)
        static readonly System.Runtime.CompilerServices.ConditionalWeakTable<DbxFile, TypeBook> fileTypes =
            new System.Runtime.CompilerServices.ConditionalWeakTable<DbxFile, TypeBook>();

        static TypeBook TypesOf(DbxFile f)
        {
            return fileTypes.GetValue(f, k => { var b = new TypeBook(); b.Learn(k); return b; });
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
            // the kind of a 4-byte list: its stored values' (zeros say nothing), else the field's elsewhere
            int listKind = Unknown;
            bool known = false;
            if (size == 4 && tokens.Length > 0)
            {
                var olds = new List<uint>();
                for (int i = 0; i < oldCount && oldSize == 4; i++) olds.Add(BigEndian.U32(old.Payload, oldAt + 4 * i));
                if (strict && learned == null && ListKind(olds, x.FirstValue, null) == Unknown) learned = TypesOf(file).Find(x.Tag, x.FirstValue);
                listKind = ListKind(olds, x.FirstValue, learned);
                known = old != null || (learned != null && learned.Floats + learned.Ints > 0);
            }
            for (int i = 0; i < tokens.Length; i++)
            {
                string token = tokens[i].Trim();
                if (i < oldCount && size == oldSize && (size == 2 || size == 4 || size == 8)
                    && XmlExport.Number(old.Payload, oldAt + size * i, size, x.FirstValue) == token)
                {
                    // a value shown as it was keeps its exact bytes (a -0, a NaN's payload)
                    for (int k = 0; k < size; k++) output.Add(old.Payload[oldAt + size * i + k]);
                    continue;
                }
                uint? oldBits = size == 4 && i < oldCount ? (uint?)BigEndian.U32(old.Payload, oldAt + 4 * i) : null;
                if (size == 4)
                {
                    int valueKind = oldBits.HasValue ? KindOfBits(oldBits.Value, x.FirstValue) : Unknown;
                    if (valueKind == Unknown) valueKind = listKind;
                    BigEndian.Put(output, strict ? FourBytesTyped(token, x.FirstValue, valueKind, x, path)
                                                 : FourBytes(token, x.FirstValue, valueKind, known, x, path));
                }
                else if (size == 8)
                {
                    double d;
                    if (!double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out d))
                        throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not a number (or out of the range of an 8-byte decimal)");
                    if (d == 0 && token.StartsWith("-")) d = BitConverter.Int64BitsToDouble(long.MinValue);    // -0
                    if ((double.IsNaN(d) || double.IsInfinity(d)) && (strict || i >= oldCount))
                        Note(x, path, "'" + token + "' written as " + (double.IsNaN(d) ? "a NaN" : "an infinity"));
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

        const int Unknown = 0, Integer = 1, Decimal = 2;

        // What one stored 4-byte value says about its field's kind. A stored 0 (or -0) is the integer 0 and the
        // decimal 0.0 alike, so it says nothing; a whole-number field (a hash, an id) is always integer.
        static int KindOfBits(uint bits, string field)
        {
            if (XmlExport.IsWholeNumberField(field)) return Integer;
            if (bits == 0 || bits == 0x80000000) return Unknown;
            return PrintsAsWhole(bits, field) ? Integer : Decimal;
        }

        // The kind of a list's values: most of its stored non-zero values', else the same field's elsewhere
        // (learned), else Unknown.
        static int ListKind(List<uint> olds, string field, TypeBook.Entry learned)
        {
            if (XmlExport.IsWholeNumberField(field)) return Integer;
            int ints = 0, floats = 0;
            foreach (uint b in olds)
            {
                int k = KindOfBits(b, field);
                if (k == Integer) ints++;
                else if (k == Decimal) floats++;
            }
            if (ints + floats > 0) return floats >= ints ? Decimal : Integer;
            if (learned != null && learned.Floats + learned.Ints > 0) return learned.Floats >= learned.Ints ? Decimal : Integer;
            return Unknown;
        }

        // Kind changes, NaNs and infinities the import or setfield wrote: told, not refused.
        public readonly List<string> Notes = new List<string>();

        void Note(Node x, string path, string what) { Notes.Add("line " + x.Line + ": " + path + ": " + what); }

        // a decimal token as a 4-byte float ("-0" keeps its sign); false when it is not one or out of range
        static bool ParseFloat(string token, out float f)
        {
            if (!float.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out f)) return false;
            if (f == 0 && token.StartsWith("-")) f = BitConverter.ToSingle(BitConverter.GetBytes(0x80000000u), 0);
            return true;
        }

        static uint Bits(float f) { return BitConverter.ToUInt32(BitConverter.GetBytes(f), 0); }

        // A 4-byte value read from XML: the integer or the decimal reading of the token, whichever the XML would show
        // as exactly this token. When both would (a whole decimal like 1.0 also shows as "1"), kind decides (the
        // stored value's, else its list's or field's: FORMAT.md section 6), else decimal (a guess, for a new element
        // with nothing known). A reading of another kind than the field's is written and noted.
        uint FourBytes(string token, string field, int kind, bool known, Node x, string path)
        {
            uint? asInt = null, asFloat = null;
            int whole;
            if (int.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)
                && XmlExport.Number(Be((uint)whole), 0, 4, field) == token) asInt = (uint)whole;
            float f;
            if (ParseFloat(token, out f))
            {
                uint bits = Bits(f);
                if (XmlExport.Number(Be(bits), 0, 4, field) == token && bits != asInt) asFloat = bits;
            }
            if (asInt.HasValue && asFloat.HasValue)
            {
                if (kind == Integer) return asInt.Value;
                if (kind == Unknown && !known) Guess(x, path, "decimal for '" + token + "'");
                return asFloat.Value;
            }
            if (asInt.HasValue || asFloat.HasValue)
            {
                int got = asInt.HasValue ? Integer : Decimal;
                if (kind != Unknown && kind != got && (asInt ?? asFloat.Value) != 0) Note(x, path, "'" + token + "' written as " + (got == Integer ? "an integer" : "a decimal") + " in " + (kind == Integer ? "an integer" : "a decimal") + " field");
                return asInt ?? asFloat.Value;
            }
            // not stored exactly either way (more digits than a 4-byte decimal holds, a NaN, ...): an integer in an
            // integer field (a whole-number field also takes the unsigned form), else the nearest decimal
            uint u;
            if (kind == Integer && int.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)) return (uint)whole;
            if (kind == Integer && XmlExport.IsWholeNumberField(field) && uint.TryParse(token, NumberStyles.None, CultureInfo.InvariantCulture, out u)) return u;
            if (ParseFloat(token, out f))
            {
                if (float.IsNaN(f) || float.IsInfinity(f)) Note(x, path, "'" + token + "' written as " + (float.IsNaN(f) ? "a NaN" : "an infinity"));
                else
                {
                    Rounded++;
                    if (kind == Integer) Note(x, path, "'" + token + "' written as a decimal in an integer field");
                }
                return Bits(f);
            }
            throw new InvalidDataException("line " + x.Line + ": " + path + ": '" + token + "' is not a number");
        }

        // A 4-byte value typed in (setfield, DbxEditGUI): of the field's kind only. Unknown (only zeros stored, the
        // field nowhere else in the file) is decimal.
        uint FourBytesTyped(string token, string field, int kind, Node x, string path)
        {
            string where = "line " + x.Line + ": " + path + ": '" + token + "' ";
            if (kind == Integer)
            {
                int whole;
                uint u;
                if (int.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out whole)) return (uint)whole;
                bool unsigned = XmlExport.IsWholeNumberField(field);
                if (unsigned && uint.TryParse(token, NumberStyles.None, CultureInfo.InvariantCulture, out u)) return u;
                long big;
                if (long.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out big))
                    throw new InvalidDataException(where + "is out of the range of this 4-byte integer field ("
                                                   + (unsigned ? "-2147483648 to 4294967295" : "-2147483648 to 2147483647") + ")");
                throw new InvalidDataException(where + "is not an integer, and the field holds integers");
            }
            float f;
            if (!ParseFloat(token, out f))
                throw new InvalidDataException(where + "is not a number (or out of the range of a 4-byte decimal), and the field holds decimals");
            uint bits = Bits(f);
            if (float.IsNaN(f) || float.IsInfinity(f))
                Note(x, path, "'" + token + "' written as " + (float.IsNaN(f) ? "a NaN" : "an infinity") + " (the XML shows it as the integer " + (int)bits + ")");
            else
            {
                double d;
                if (XmlExport.Number(Be(bits), 0, 4, field) != token && double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out d) && (double)f != d)
                    Rounded++;
            }
            return bits;
        }

        static byte[] Be(uint v) { return new[] { (byte)(v >> 24), (byte)(v >> 16), (byte)(v >> 8), (byte)v }; }
    }
}
