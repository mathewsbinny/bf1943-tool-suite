// DbxEdit - reads, converts and edits Frostbite 1 binary .dbx files, Battlefield 1943's and Bad Company 2's (data as a tokenised
// XML tree). Written from FORMAT.md, which describes the format and everything this tool outputs.
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
                foreach (string n in report.Notes.Take(50)) Console.Error.WriteLine("note: " + n);
                if (report.Notes.Count > 0)
                    Console.Error.WriteLine(report.Notes.Count + " value(s) written as another kind than their field's (integer / decimal), or as a NaN or infinity - check them");
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
                        string note;
                        try
                        {
                            if (!editor.SetField(p[0], p[1], value, out note)) { Console.Error.WriteLine("setfield: no settable field " + p[1] + " in " + p[0]); return 1; }
                        }
                        catch (InvalidDataException e) { Console.Error.WriteLine("setfield " + p[0] + "|" + p[1] + ": " + e.Message); return 1; }
                        if (note != null) Console.WriteLine("setfield " + p[1] + ": " + note);
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
}
