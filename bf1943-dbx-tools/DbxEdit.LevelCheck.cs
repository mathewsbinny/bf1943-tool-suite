// DbxEdit: the level check and fixslots (FORMAT.md section 7): placed objects, cull slots, LevelCheck.
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
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
}
