// DbxEdit: BF1943's level rules (LevelRules1943) and its reflected classes (Bf1943Types); the cull-tree rules are in DbxEdit.CullTrees1943.cs.
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
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

    // BF1943's own rules (FORMAT.md section 7, README "Check a level"): each one holds in every shipped BF1943 level
    // (wake_island_s, guadal_canal, iwo_jima_s, coral_sea, wake_island_tutorial), as derived and recorded by the research
    // workspace's src/assetresearch/derive_level_rules1943.py (research/evidence/bf1943-level-check-rules.json).
    // Errors: the file is inconsistent with itself or with BF1943's classes. Warnings: the level is unlike every shipped one.
    static partial class LevelRules1943
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
                    string held;
                    // a null entry where the shipped levels always hold an instance (LevelData Objects: reported as the
                    // empty entry the game reads without a check)
                    if (r == "null" && path.IndexOf('[') >= 0 && type + "." + Unindexed(path) != "LevelData.Objects"
                        && arrayTypes.TryGetValue(type + "." + Unindexed(path), out held))
                        c.Error(where + " " + path, "is null; in every shipped level these items are " + held + "s (a null one is read like any other)");
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
                    // null is reported with the arrays' items, a missing local instance with the references
                    if (r == null || r == "null" || (r.Length == 36 && r.IndexOf('/') < 0 && !c.ByGuid.ContainsKey(r))) continue;
                    if (!c.ByGuid.TryGetValue(r, out m))
                    {
                        c.Error(where, "refers to " + r + ", not to an instance of this partition: a group's members are the level's own instances (in every shipped level)");
                        continue;
                    }
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
}
