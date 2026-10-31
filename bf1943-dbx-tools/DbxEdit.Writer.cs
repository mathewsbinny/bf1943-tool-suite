// DbxEdit: writing a DbxFile back to bytes.
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
    // A parsed binary .dbx file (DbxEdit.Format.cs): written back.
    sealed partial class DbxFile
    {
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
            // one byte holds both: a 16th attribute would overflow into the type and corrupt the file
            if (e.Kind < 0 || e.Kind > 15 || e.Attributes.Count > MaxAttributes)
                throw new InvalidDataException("an element with type " + e.Kind + " and " + e.Attributes.Count + " attributes cannot be written (at most type 15 and "
                                               + MaxAttributes + " attributes: both share one byte)");
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
    }
}
