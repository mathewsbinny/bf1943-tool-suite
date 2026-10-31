// DbxEdit: BF1943's level rules (DbxEdit.LevelRules1943.cs): the static cull trees, checked with --bundle.
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
    static partial class LevelRules1943
    {
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
    }
}
