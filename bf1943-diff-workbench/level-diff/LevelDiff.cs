// LevelDiff - compares two Battlefield: Bad Company 2 binary .dbx files (levels, or any partition) instance by
// instance: what was added, removed or changed, field by field. Reads files with the toolkit's DbxEdit code
// (..\..\bf1943-dbx-tools\DbxEdit.cs in the suite, compiled in by build.bat).
//
// usage: LevelDiff <a.dbx> <b.dbx> [options]
//   --match-objects     also pair placed objects (ReferenceObjectData) whose GUIDs differ by blueprint + position
//                       (builds of one map often re-create them: PC vs console vs Linux server)
//   --type <text>       only instances whose type contains <text> (e.g. --type ReferenceObjectData)
//   --ignore <field>    leave this field (or attribute) out (repeatable; e.g. --ignore Enumeration)
//   --summary           counts only
//   --all               list every instance (default: the first 40 per section)
//   --json <file>       also write the full result as JSON
// exit code: 0 = no differences, 1 = differences, 2 = bad input or the comparison could not run
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Bfbc2Dbx;

static class LevelDiff
{
    sealed class Options
    {
        public bool MatchObjects, Summary, All;
        public string Type, Json;
        public readonly HashSet<string> Ignore = new HashSet<string>();
    }

    sealed class Change { public string Path, A, B; }

    sealed class InstanceDiff
    {
        public string Guid, GuidB, Type, Id;
        public readonly List<Change> Changes = new List<Change>();
        public readonly List<string> Added = new List<string>(), Removed = new List<string>();   // list entries
    }

    // a value as shown (Text) and what it is exactly (Raw: kind, count and width, bytes, other attributes). Values
    // are equal when their Raw is: the text alone hides an integer turned decimal (both "2"), a width or kind change.
    sealed class Value { public string Text, Raw; }

    static int Main(string[] args)
    {
        ToolLog.Start("leveldiff");
        try { return MainCore(args); }
        catch (Exception e) { return ToolLog.Fail(e); }       // unexpected: logged and explained, exit code 2
    }

    const string UsageText = "usage: LevelDiff <a.dbx> <b.dbx> [--match-objects] [--type <text>] [--ignore <field>] [--summary] [--all] [--json <file>]";

    static int Usage(string why)
    {
        if (why != null) Console.Error.WriteLine(why);
        Console.Error.WriteLine(UsageText);
        return 2;
    }

    static int MainCore(string[] args)
    {
        var o = new Options();
        var files = new List<string>();
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            if (a == "--match-objects") o.MatchObjects = true;
            else if (a == "--summary") o.Summary = true;
            else if (a == "--all") o.All = true;
            else if (a == "--type" || a == "--ignore" || a == "--json")
            {
                if (i + 1 >= args.Length || args[i + 1].Length == 0) return Usage(a + " needs a value");
                string v = args[++i];
                if (a == "--type") o.Type = v;
                else if (a == "--ignore") o.Ignore.Add(v);
                else o.Json = v;
            }
            else if (a.StartsWith("--")) return Usage("unknown option " + a);
            else files.Add(a);
        }
        if (files.Count != 2) return Usage(null);
        DbxFile fa, fb;
        try
        {
            fa = DbxFile.Read(files[0]);
            fb = DbxFile.Read(files[1]);
            if (!fa.IsBinary || !fb.IsBinary) { Console.Error.WriteLine("both files must be binary .dbx"); return 2; }
            fa.Walkable(files[0]);
            fb.Walkable(files[1]);
        }
        catch (Exception e)
        {
            ToolLog.Error(e, "reading the files");
            string[] ce = ToolLog.Explain(e);
            Console.Error.WriteLine(e.Message);
            Console.Error.WriteLine("  what to do: " + ce[1] + "  (details: " + (ToolLog.LogPath ?? "no log") + ")");
            return 2;
        }
        return Run(fa, fb, files[0], files[1], o);
    }

    static string TypeOf(DbxFile f, Element i)
    {
        string t = f.Get(i, "type") ?? "";
        int dot = t.LastIndexOf('.');
        return dot >= 0 ? t.Substring(dot + 1) : t;
    }

    static int Run(DbxFile a, DbxFile b, string nameA, string nameB, Options o)
    {
        Func<DbxFile, List<Element>> pick = f => f.Instances().Where(i => o.Type == null || (f.Get(i, "type") ?? "").IndexOf(o.Type, StringComparison.OrdinalIgnoreCase) >= 0).ToList();
        var ia = pick(a);
        var ib = pick(b);
        // pairs by GUID: each B instance pairs once (the n-th A instance of a GUID with the n-th B one); an instance
        // without a GUID pairs with none
        var byGuidB = new Dictionary<string, Queue<Element>>(StringComparer.OrdinalIgnoreCase);
        foreach (var i in ib)
        {
            string g = b.Get(i, "guid");
            if (g == null) continue;
            Queue<Element> q;
            if (!byGuidB.TryGetValue(g, out q)) byGuidB[g] = q = new Queue<Element>();
            q.Enqueue(i);
        }
        var pairs = new List<KeyValuePair<Element, Element>>();
        var onlyA = new List<Element>();
        var matchedB = new HashSet<Element>();
        foreach (var i in ia)
        {
            string g = a.Get(i, "guid");
            Queue<Element> q;
            if (g != null && byGuidB.TryGetValue(g, out q) && q.Count > 0)
            {
                var j = q.Dequeue();
                pairs.Add(new KeyValuePair<Element, Element>(i, j));
                matchedB.Add(j);
            }
            else onlyA.Add(i);
        }
        var onlyB = ib.Where(j => !matchedB.Contains(j)).ToList();
        // placed objects, once per file (listing and pairing look them up)
        var objA = PlacedObject.All(a).ToDictionary(x => x.Instance, x => x);
        var objB = PlacedObject.All(b).ToDictionary(x => x.Instance, x => x);
        int pairedByPosition = 0;
        if (o.MatchObjects)
        {
            var posB = new Dictionary<string, Queue<Element>>();
            foreach (var j in onlyB)
            {
                PlacedObject po;
                if (!objB.TryGetValue(j, out po)) continue;
                Queue<Element> q;
                if (!posB.TryGetValue(po.PositionKey, out q)) posB[po.PositionKey] = q = new Queue<Element>();
                q.Enqueue(j);
            }
            var pairedA = new HashSet<Element>();
            var pairedB = new HashSet<Element>();
            foreach (var i in onlyA)
            {
                PlacedObject po;
                Queue<Element> q;
                if (!objA.TryGetValue(i, out po) || !posB.TryGetValue(po.PositionKey, out q) || q.Count == 0) continue;
                var j = q.Dequeue();
                pairs.Add(new KeyValuePair<Element, Element>(i, j));
                pairedA.Add(i);
                pairedB.Add(j);
                pairedByPosition++;
            }
            onlyA = onlyA.Where(i => !pairedA.Contains(i)).ToList();
            onlyB = onlyB.Where(j => !pairedB.Contains(j)).ToList();
        }

        var diffs = new List<InstanceDiff>();
        if (o.Type == null)
        {
            var p = ComparePartition(a, b, o);
            if (p.Changes.Count + p.Added.Count + p.Removed.Count > 0) diffs.Add(p);
        }
        foreach (var p in pairs)
        {
            var d = Compare(a, p.Key, b, p.Value, o);
            if (d.Changes.Count + d.Added.Count + d.Removed.Count > 0) diffs.Add(d);
        }

        Action<string> say = s => { Console.WriteLine(s); };
        say("A: " + nameA + "  (" + ia.Count + " instances)");
        say("B: " + nameB + "  (" + ib.Count + " instances)");
        say("same instance in both: " + pairs.Count + (pairedByPosition > 0 ? " (" + pairedByPosition + " placed objects paired by blueprint + position)" : "")
            + "; changed: " + diffs.Count + "; only in A: " + onlyA.Count + "; only in B: " + onlyB.Count);
        Section("only in A (removed in B)", onlyA, a, objA, o, say);
        Section("only in B (added in B)", onlyB, b, objB, o, say);
        if (!o.Summary && diffs.Count > 0)
        {
            say("");
            say("== changed (" + diffs.Count + ")");
            foreach (var d in o.All ? diffs : diffs.Take(40))
            {
                say(d.Type + "  " + (d.Id ?? "") + "  " + d.Guid + (d.GuidB != null && !string.Equals(d.GuidB, d.Guid, StringComparison.OrdinalIgnoreCase) ? " = " + d.GuidB + " in B" : ""));
                foreach (var c in d.Changes.Take(o.All ? int.MaxValue : 25)) say("    " + c.Path + ": " + Clip(c.A) + "  ->  " + Clip(c.B));
                if (!o.All && d.Changes.Count > 25) say("    ... " + (d.Changes.Count - 25) + " more field changes");
                foreach (var r in d.Removed.Take(o.All ? int.MaxValue : 15)) say("    - " + r);
                foreach (var r in d.Added.Take(o.All ? int.MaxValue : 15)) say("    + " + r);
                if (!o.All && (d.Removed.Count > 15 || d.Added.Count > 15)) say("    ... list entries: " + d.Removed.Count + " removed, " + d.Added.Count + " added in all");
            }
            if (!o.All && diffs.Count > 40) say("... " + (diffs.Count - 40) + " more changed instances (--all lists them)");
        }
        if (o.Json != null) SafeWrite.Text(o.Json, Json(a, b, nameA, nameB, onlyA, onlyB, diffs, pairs.Count));
        return onlyA.Count + onlyB.Count + diffs.Count == 0 ? 0 : 1;
    }

    static string Clip(string s)
    {
        s = (s ?? "(none)").Replace("\r", " ").Replace("\n", " ");
        return s.Length > 120 ? s.Substring(0, 120) + "..." : s;
    }

    static void Section(string title, List<Element> list, DbxFile f, Dictionary<Element, PlacedObject> placed, Options o, Action<string> say)
    {
        if (list.Count == 0) return;
        say("");
        say("== " + title + ": " + list.Count + "  (" + string.Join(", ", list.GroupBy(i => TypeOf(f, i)).OrderByDescending(g => g.Count()).Take(8).Select(g => g.Key + " " + g.Count())) + ")");
        if (o.Summary) return;
        foreach (var i in o.All ? list : list.Take(40)) say("    " + TypeOf(f, i) + "  " + (f.Get(i, "id") ?? "") + "  " + f.Get(i, "guid") + Placement(placed, i));
        if (!o.All && list.Count > 40) say("    ... " + (list.Count - 40) + " more (--all lists them)");
    }

    static string Placement(Dictionary<Element, PlacedObject> placed, Element i)
    {
        PlacedObject po;
        return !placed.TryGetValue(i, out po) ? "" : "  " + po.Blueprint + " at " + Math.Round(po.X, 1) + ", " + Math.Round(po.Y, 1) + ", " + Math.Round(po.Z, 1);
    }

    static string Hex(byte[] b, int from) { return BitConverter.ToString(b, from).Replace("-", "").ToLowerInvariant(); }

    // What an element is exactly: its kind, for numbers their count and width, its value bytes (a text's string),
    // and its attributes other than name.
    static string Raw(DbxFile f, Element e) { return Raw(f, e, "name", true); }

    // skip: the attribute left out; count: whether a container's child count is part of it
    static string Raw(DbxFile f, Element e, string skip, bool count)
    {
        string attrs = string.Join(" ", e.Attributes.Where(x => f.Table[x.Name] != skip).Select(x => f.Table[x.Name] + "=" + f.Table[x.Value]));
        string v;
        if (e.IsContainer) v = (count ? "container of " + e.Children.Count + ", byte " : "container, byte ") + e.ContainerByte;
        else if (e.Kind == Element.Text) v = "text '" + XmlExport.ValueText(f, e) + "'";
        else if (e.Kind == Element.Numbers)
        {
            int n, size;
            int at = Element.NumbersHeader(e.Payload, out n, out size);
            v = n + " number(s) of " + size + " bytes" + (at < e.Payload.Length ? " " + Hex(e.Payload, at) : "");
        }
        else v = "kind " + e.Kind + " " + Hex(e.Payload, 0);
        return v + (attrs.Length > 0 ? ", " + attrs : "");
    }

    static Value ValueOf(DbxFile f, Element e)
    {
        string r = f.Get(e, "ref");
        string text = e.IsContainer ? "(" + e.Children.Count + " entries)" : r != null ? "-> " + r : XmlExport.ValueText(f, e);
        return new Value { Text = text, Raw = Raw(f, e) };
    }

    // A list item exactly, but for its reference (compared as the list's membership and order): its tag, kind and
    // value bytes, its other attributes, and everything below it.
    static string ItemShape(DbxFile f, Element e)
    {
        string s = f.TagOf(e) + " " + Raw(f, e, "ref", true);
        if (!e.IsContainer || e.Children.Count == 0) return s;
        return s + " {" + string.Join("; ", e.Children.Select(c => f.TagOf(c) + " " + Raw(f, c, null, true) + (c.IsContainer ? " " + ItemShape(f, c) : ""))) + "}";
    }

    // Fields of one instance as path -> value; lists of references as entries (reference, the item's shape), the
    // list element itself (its attributes and container byte) as a value. skipInstances: leave out instance children
    // (the partition's own fields).
    static void Flatten(DbxFile f, Element e, string path, Options o, Dictionary<string, Value> values, Dictionary<string, List<KeyValuePair<string, string>>> lists, bool skipInstances)
    {
        var seen = new Dictionary<string, int>();
        foreach (var c in e.Children)
        {
            string name = f.Get(c, "name");
            string tag = f.TagOf(c), key = name ?? tag;
            if (skipInstances && tag == "instance") continue;
            if (name != null && o.Ignore.Contains(name)) continue;
            int n;
            seen.TryGetValue(key, out n);
            seen[key] = n + 1;
            string p = path.Length == 0 ? key : path + "." + key;
            if (n > 0 || (name == null && tag != "item")) p += "[" + n + "]";
            if (c.IsContainer)
            {
                bool refList = c.Children.Count > 0 && c.Children.All(x => f.TagOf(x) == "item" && f.Get(x, "ref") != null);
                if (refList)
                {
                    List<KeyValuePair<string, string>> l;
                    if (!lists.TryGetValue(p, out l)) lists[p] = l = new List<KeyValuePair<string, string>>();
                    l.AddRange(c.Children.Select(x => new KeyValuePair<string, string>(f.Get(x, "ref"), ItemShape(f, x))));
                    // the list element's own tag, attributes and byte (its entries are compared as a list)
                    values[p] = new Value { Text = "(list)", Raw = f.TagOf(c) + " " + Raw(f, c, "name", false) };
                }
                else
                {
                    values[p] = ValueOf(f, c);
                    Flatten(f, c, p, o, values, lists, false);
                }
                continue;
            }
            values[p] = ValueOf(f, c);
        }
    }

    // an element's attributes by name (a repeated name gets [n]) but those skipped or ignored
    static Dictionary<string, string> AttributeMap(DbxFile f, Element e, Options o, params string[] skip)
    {
        var m = new Dictionary<string, string>();
        foreach (var at in e.Attributes)
        {
            string k = f.Table[at.Name];
            if (skip.Contains(k) || o.Ignore.Contains(k)) continue;
            string key = "(" + k + ")";
            for (int n = 1; m.ContainsKey(key); n++) key = "(" + k + "[" + n + "])";
            m[key] = f.Table[at.Value];
        }
        return m;
    }

    static void CompareAttributes(Dictionary<string, string> x, Dictionary<string, string> y, InstanceDiff d)
    {
        foreach (var k in x.Keys.Union(y.Keys))
        {
            string va, vb;
            x.TryGetValue(k, out va);
            y.TryGetValue(k, out vb);
            if (va != vb) d.Changes.Add(new Change { Path = k, A = va, B = vb });
        }
    }

    // The partition element itself: its attributes (guid, primaryInstance, ...), its children other than instances,
    // and the number of top-level elements.
    static InstanceDiff ComparePartition(DbxFile a, DbxFile b, Options o)
    {
        var pa = a.TopLevel[0];
        var pb = b.TopLevel[0];
        var d = new InstanceDiff { Guid = a.Get(pa, "guid"), GuidB = b.Get(pb, "guid"), Type = "(" + a.TagOf(pa) + ")" };
        if (a.TopLevel.Count != b.TopLevel.Count)
            d.Changes.Add(new Change { Path = "(top-level elements)", A = a.TopLevel.Count.ToString(), B = b.TopLevel.Count.ToString() });
        if (a.TagOf(pa) != b.TagOf(pb)) d.Changes.Add(new Change { Path = "(tag)", A = a.TagOf(pa), B = b.TagOf(pb) });
        CompareAttributes(AttributeMap(a, pa, o), AttributeMap(b, pb, o), d);
        if (pa.IsContainer && pb.IsContainer) CompareFields(a, pa, b, pb, o, d, true);
        else if (pa.IsContainer != pb.IsContainer) d.Changes.Add(new Change { Path = "(kind)", A = Raw(a, pa), B = Raw(b, pb) });
        return d;
    }

    static InstanceDiff Compare(DbxFile a, Element ia, DbxFile b, Element ib, Options o)
    {
        var d = new InstanceDiff { Guid = a.Get(ia, "guid"), GuidB = b.Get(ib, "guid"), Type = TypeOf(a, ia), Id = a.Get(ia, "id") };
        CompareAttributes(AttributeMap(a, ia, o, "guid"), AttributeMap(b, ib, o, "guid"), d);
        if (ia.IsContainer && ib.IsContainer) CompareFields(a, ia, b, ib, o, d, false);
        else if (ia.IsContainer != ib.IsContainer) d.Changes.Add(new Change { Path = "(kind)", A = Raw(a, ia), B = Raw(b, ib) });
        return d;
    }

    static void CompareFields(DbxFile a, Element ia, DbxFile b, Element ib, Options o, InstanceDiff d, bool skipInstances)
    {
        var va = new Dictionary<string, Value>();
        var vb = new Dictionary<string, Value>();
        var la = new Dictionary<string, List<KeyValuePair<string, string>>>();
        var lb = new Dictionary<string, List<KeyValuePair<string, string>>>();
        Flatten(a, ia, "", o, va, la, skipInstances);
        Flatten(b, ib, "", o, vb, lb, skipInstances);
        foreach (var k in va.Keys.Union(vb.Keys))
        {
            Value x, y;
            va.TryGetValue(k, out x);
            vb.TryGetValue(k, out y);
            if (x != null && y != null && x.Raw == y.Raw) continue;
            string ta = x == null ? null : x.Text, tb = y == null ? null : y.Text;
            if (x != null && y != null && ta == tb) { ta += "  [" + x.Raw + "]"; tb += "  [" + y.Raw + "]"; }   // the same text: show what differs
            d.Changes.Add(new Change { Path = k, A = ta, B = tb });
        }
        foreach (var k in la.Keys.Union(lb.Keys))
        {
            List<KeyValuePair<string, string>> ex, ey;
            if (!la.TryGetValue(k, out ex)) ex = new List<KeyValuePair<string, string>>();
            if (!lb.TryGetValue(k, out ey)) ey = new List<KeyValuePair<string, string>>();
            var x = ex.Select(e => e.Key).ToList();
            var y = ey.Select(e => e.Key).ToList();
            // the items in both (the n-th entry of a reference in A with the n-th in B): the same item exactly - its
            // reference as written, its other attributes, value and anything below it
            var nth = new Dictionary<string, Queue<int>>(StringComparer.OrdinalIgnoreCase);
            for (int j = 0; j < ey.Count; j++)
            {
                Queue<int> q;
                if (!nth.TryGetValue(ey[j].Key, out q)) nth[ey[j].Key] = q = new Queue<int>();
                q.Enqueue(j);
            }
            for (int i = 0; i < ex.Count; i++)
            {
                Queue<int> q;
                if (!nth.TryGetValue(ex[i].Key, out q) || q.Count == 0) continue;
                int j = q.Dequeue();
                if (ex[i].Key == ey[j].Key && ex[i].Value == ey[j].Value) continue;
                d.Changes.Add(new Change { Path = k + "[" + i + (i != j ? "->" + j : "") + "] -> " + ex[i].Key,
                                           A = "ref=" + ex[i].Key + ", " + ex[i].Value, B = "ref=" + ey[j].Key + ", " + ey[j].Value });
            }
            var cx = Counts(x);
            var cy = Counts(y);
            int entries = d.Removed.Count + d.Added.Count;          // this list's own changes, not the earlier lists'
            foreach (var kv in cx) for (int n = (cy.ContainsKey(kv.Key) ? cy[kv.Key] : 0); n < kv.Value; n++) d.Removed.Add(k + ": " + kv.Key);
            foreach (var kv in cy) for (int n = (cx.ContainsKey(kv.Key) ? cx[kv.Key] : 0); n < kv.Value; n++) d.Added.Add(k + ": " + kv.Key);
            if (d.Removed.Count + d.Added.Count == entries && !x.SequenceEqual(y, StringComparer.OrdinalIgnoreCase))
                d.Changes.Add(new Change { Path = k, A = "same " + x.Count + " entries", B = "in another order" });
        }
    }

    static Dictionary<string, int> Counts(List<string> l)
    {
        var c = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var s in l) { int n; c.TryGetValue(s, out n); c[s] = n + 1; }
        return c;
    }

    static string Js(string s)
    {
        if (s == null) return "null";
        var b = new StringBuilder("\"");
        foreach (char ch in s)
        {
            if (ch == '"' || ch == '\\') b.Append('\\').Append(ch);
            else if (ch < 32) b.Append("\\u").Append(((int)ch).ToString("x4"));
            else b.Append(ch);
        }
        return b.Append('"').ToString();
    }

    static string Json(DbxFile a, DbxFile b, string nameA, string nameB, List<Element> onlyA, List<Element> onlyB, List<InstanceDiff> diffs, int same)
    {
        var s = new StringBuilder("{\n");
        s.Append(" \"a\": ").Append(Js(nameA)).Append(",\n \"b\": ").Append(Js(nameB)).Append(",\n \"same_instances\": ").Append(same).Append(",\n");
        Func<DbxFile, List<Element>, string> list = (f, l) => "[" + string.Join(",", l.Select(i => "\n  {\"type\": " + Js(TypeOf(f, i)) + ", \"id\": " + Js(f.Get(i, "id")) + ", \"guid\": " + Js(f.Get(i, "guid")) + "}")) + "]";
        s.Append(" \"only_in_a\": ").Append(list(a, onlyA)).Append(",\n \"only_in_b\": ").Append(list(b, onlyB)).Append(",\n \"changed\": [");
        s.Append(string.Join(",", diffs.Select(d =>
            "\n  {\"type\": " + Js(d.Type) + ", \"id\": " + Js(d.Id) + ", \"guid\": " + Js(d.Guid) + ", \"guid_b\": " + Js(d.GuidB)
            + ", \"fields\": [" + string.Join(", ", d.Changes.Select(c => "{\"path\": " + Js(c.Path) + ", \"a\": " + Js(c.A) + ", \"b\": " + Js(c.B) + "}")) + "]"
            + ", \"removed\": [" + string.Join(", ", d.Removed.Select(Js)) + "], \"added\": [" + string.Join(", ", d.Added.Select(Js)) + "]}")));
        s.Append("]\n}\n");
        return s.ToString();
    }
}
