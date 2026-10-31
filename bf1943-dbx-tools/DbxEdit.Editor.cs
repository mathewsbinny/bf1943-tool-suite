// DbxEdit: the edit commands (FORMAT.md section 5).
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
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
            if (string.IsNullOrEmpty(guid)) return 0;          // not an instance's: a missing guid matches every guid-less one
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
        // ref attribute) gets <value> as its reference; any other value is typed like the stored one
        // (XmlImport.EncodeValue): text, numbers of the stored width ('/'-separated), an integer field integers only
        // and a decimal field decimals only, bool or byte. False when there is no such field; InvalidDataException
        // when the value does not fit it. note: what to tell (a decimal rounded, a NaN or infinity written), or null.
        public bool SetField(string guid, string field, string value, out string note)
        {
            note = null;
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
            e.Payload = XmlImport.EncodeValue(file, e, value, out note);
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
}
