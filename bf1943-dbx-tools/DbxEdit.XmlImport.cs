// DbxEdit: XML -> binary .dbx (FORMAT.md section 6): reading the XML and matching it to the reference file.
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
    // XML -> binary (FORMAT.md section 6). Elements are matched to a reference file (the original .dbx): unchanged
    // values keep their exact bytes, changed ones take the reference's types. New elements are typed from the
    // TypeBook; whatever is left is guessed from the XML and reported.
    sealed partial class XmlImport
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
            p.RefuseNul();
            p.SkipProlog();
            if (!p.At("<")) throw new InvalidDataException("no root element in " + path);
            var root = p.Element();
            p.End();
            return root;
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

            // A string of the binary file ends at its zero byte: a NUL in the XML would cut the value short.
            public void RefuseNul()
            {
                int at = s.IndexOf('\0');
                if (at < 0) return;
                Advance(at - pos);
                throw Fail("a NUL character, which a .dbx string cannot hold (it ends at a zero byte)");
            }

            // After the root element only white space, comments and <?...?> may follow: anything else (a second
            // root, a stray tag or text) would be dropped without a word.
            public void End()
            {
                while (true)
                {
                    SkipSpace();
                    if (pos >= s.Length) return;
                    if (At("<!--")) SkipTo("-->");
                    else if (At("<?")) SkipTo("?>");
                    else throw Fail("content after the root element (a file holds one root element; this would be lost)");
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
                    else if (e.StartsWith("#"))
                    {
                        bool hex = e.StartsWith("#x") || e.StartsWith("#X");
                        string digits = e.Substring(hex ? 2 : 1);
                        int code;
                        if (digits.Length == 0 || digits.Length > 8
                            || !int.TryParse(digits, hex ? NumberStyles.AllowHexSpecifier : NumberStyles.None, CultureInfo.InvariantCulture, out code))
                            throw Fail("&" + e + "; is not a character reference (&#<decimal>; or &#x<hex>;)");
                        if (code == 0) throw Fail("&" + e + "; is a NUL character, which a .dbx string cannot hold (it ends at a zero byte)");
                        if (code < 0 || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF))
                            throw Fail("&" + e + "; is not a Unicode character (a surrogate or past U+10FFFF)");
                        b.Append(char.ConvertFromUtf32(code));
                    }
                    else throw Fail("unknown entity &" + e + ";");
                    i = semi;
                }
                return b.ToString();
            }

            // an element and its children (recursive: nesting is limited as for the binary commands, which count
            // containers - an element with children, or the empty container an element with only a line break is -
            // so a value element may sit inside the deepest container)
            public Node Element()
            {
                depth++;
                try { return ElementBody(); }
                finally { depth--; }
            }

            InvalidDataException TooDeep()
            {
                return Fail("containers nested more than " + DbxFile.MaxEditDepth + " deep (DbxEdit imports files nested up to " + DbxFile.MaxEditDepth + ")");
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
                        if (n.Multiline && depth > DbxFile.MaxEditDepth) throw TooDeep();
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
                        if (depth > DbxFile.MaxEditDepth) throw TooDeep();
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
            if (x.Attributes.Count > DbxFile.MaxAttributes)
                throw new InvalidDataException("line " + x.Line + ": " + path + " has " + x.Attributes.Count + " attributes; a .dbx element holds at most "
                                               + DbxFile.MaxAttributes + " (the count is 4 bits of its head byte)");
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
    }
}
