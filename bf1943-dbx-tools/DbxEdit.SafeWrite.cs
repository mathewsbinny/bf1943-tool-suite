// DbxEdit: SafeWrite, the one way the C# tools write files.
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace Bfbc2Dbx
{
    // A write the tools refuse: into the game folder, which they only read.
    public sealed class WriteRefusedException : IOException
    {
        public WriteRefusedException(string message) : base(message) { }
    }

    // Every file the C# tools write goes through here, as core fbrb1943.py's checked() and write_file() do:
    // - never into the BF1943 game folder, found by core game1943.find_game's rule (GameFolder: BF1943_GAME, else
    //   the Xenia kit's dist\xenon in the suite; a folder counts when <it>\levels is a folder to Windows, links
    //   followed), looked up again for every write as find_game is, however the path is cased or spelled (8.3 short
    //   names, \\?\, a share) and through whatever link or junction it leads there (both sides resolved as
    //   os.path.realpath + normcase do, and compared by folder identity);
    // - written to a temporary file beside the target and moved into its place (MoveFileEx, as os.replace): a write
    //   that fails leaves the old file, and a hard link at the target only loses its name - the file it linked to,
    //   in the game folder or anywhere, is left as it was. Writing over the input is therefore safe (it was read whole).
    public static class SafeWrite
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool MoveFileEx(string from, string to, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFinalPathNameByHandle(IntPtr handle, StringBuilder path, uint size, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);

        const uint MoveReplaceExisting = 1, MoveWriteThrough = 8, ShareAll = 7, OpenExisting = 3, BackupSemantics = 0x02000000;

        public static void Bytes(string path, byte[] data) { Write(path, data); }

        // UTF-8 without a byte-order mark (as File.WriteAllText)
        public static void Text(string path, string text) { Write(path, new UTF8Encoding(false).GetBytes(text)); }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFullPathNameW(string name, uint size, StringBuilder buffer, IntPtr filePart);

        // The folder core game1943.find_game() finds, spelled as it returns it, or null when there is none. The same
        // rule, step for step (2026-11-06: until then Directory.Exists and Path.GetFullPath decided, which refused a
        // \\?\ spelling find_game takes - leaving the kit's folder or none protected - and trimmed spaces Windows keeps):
        // - the candidates in order: BF1943_GAME when set and not empty, then <suite>\bf1943-xenia-kit\output\title\
        //   dist\xenon (the suite this exe was built in; find_game's is the one game1943.py sits in);
        // - a candidate counts when <candidate>\levels (joined as os.path.join does) opens as a folder, links and
        //   junctions followed, with the path given to Windows as written (os.path.isdir);
        // - the first that counts is returned as os.path.abspath spells it (AbsPath).
        public static string GameFolder()
        {
            string env = Environment.GetEnvironmentVariable("BF1943_GAME");
            string suite = SuiteRoot();
            string kit = suite == null ? null : Path.Combine(suite, "bf1943-xenia-kit", "output", "title", "dist", "xenon");
            foreach (string p in new[] { env, kit })
                if (!string.IsNullOrEmpty(p) && IsFolder(JoinLevels(p))) return AbsPath(p);
            return null;
        }

        // os.path.join(p, "levels") on Windows: no separator added after one, or after a bare drive ("C:")
        static string JoinLevels(string p)
        {
            char last = p[p.Length - 1];
            bool bareDrive = p.Length == 2 && p[1] == ':';
            return p + (last == '\\' || last == '/' || bareDrive ? "levels" : "\\levels");
        }

        // os.path.isdir: the path opens (links and junctions followed) and is a folder
        static bool IsFolder(string path)
        {
            if (path.IndexOf('\0') >= 0) return false;
            IntPtr h = CreateFile(path, 0, ShareAll, IntPtr.Zero, OpenExisting, BackupSemantics, IntPtr.Zero);
            if (h == new IntPtr(-1)) return false;
            try
            {
                FileInformation i;
                return GetFileInformationByHandle(h, out i) && (i.Attributes & 0x10) != 0;     // FILE_ATTRIBUTE_DIRECTORY
            }
            finally { CloseHandle(h); }
        }

        // os.path.abspath on Windows: the full path as GetFullPathNameW makes it (relative to the current folder; "."
        // and ".." taken out, / turned into \, a \\?\ prefix kept), without separators after the last name (normpath)
        public static string AbsPath(string path)
        {
            var b = new StringBuilder(1024);
            uint n = GetFullPathNameW(path, (uint)b.Capacity, b, IntPtr.Zero);
            if (n >= b.Capacity)
            {
                b = new StringBuilder((int)n + 1);
                n = GetFullPathNameW(path, (uint)b.Capacity, b, IntPtr.Zero);
            }
            string full = n == 0 || n >= b.Capacity ? path : b.ToString();
            int keep = RootLength(full);
            int end = full.Length;
            while (end > keep && (full[end - 1] == '\\' || full[end - 1] == '/')) end--;
            return full.Substring(0, end);
        }

        // the length of a full path's root, its separator included: C:\, \\server\share\, \\?\C:\, \\?\UNC\server\share\
        static int RootLength(string full)
        {
            string body = full;
            int prefix = 0;
            if (full.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) { prefix = 8; body = full.Substring(8); }
            else if (full.StartsWith(@"\\?\") || full.StartsWith(@"\\.\")) { prefix = 4; body = full.Substring(4); }
            else if (full.StartsWith(@"\\")) { prefix = 2; body = full.Substring(2); }
            if (prefix != 2 && prefix != 8 && body.Length >= 2 && body[1] == ':') return prefix + Math.Min(3, body.Length);
            int server = body.IndexOf('\\');                              // \\server\share\ (or \\?\UNC\server\share\)
            if (server < 0) return full.Length;
            int share = body.IndexOf('\\', server + 1);
            return share < 0 ? full.Length : prefix + share + 1;
        }

        // A full path without its \\?\ prefix (\\?\C:\x is C:\x, \\?\UNC\server\share is \\server\share), which the
        // .NET Framework 4 path functions refuse
        static string Plain(string full)
        {
            if (full.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) return @"\\" + full.Substring(8);
            if (full.StartsWith(@"\\?\")) return full.Substring(4);
            return full;
        }

        // A path as the guard and the write use it: AbsPath, without a \\?\ prefix
        public static string FullPath(string path) { return Plain(AbsPath(path)); }

        // the suite folder this exe was built in (the nearest folder above it with core\bfbc2core\game1943.py), or null
        static string SuiteRoot()
        {
            for (string d = AppDomain.CurrentDomain.BaseDirectory; !string.IsNullOrEmpty(d); d = Path.GetDirectoryName(d))
                if (File.Exists(Path.Combine(d, "core", "bfbc2core", "game1943.py"))) return d;
            return null;
        }

        // path as Windows resolves it: links and junctions followed for the part that exists, case folded
        public static string Canonical(string path)
        {
            string full = FullPath(path);
            var tail = new List<string>();
            string cur = full;
            while (cur != null && !File.Exists(cur) && !Directory.Exists(cur))
            {
                tail.Insert(0, Path.GetFileName(cur));
                cur = Path.GetDirectoryName(cur);
            }
            string resolved = cur == null ? full : (Final(cur) ?? cur);
            foreach (string t in tail) resolved = Path.Combine(resolved, t);
            return resolved.TrimEnd('\\', '/').ToLowerInvariant();
        }

        static string Final(string path)
        {
            IntPtr h = CreateFile(path, 0, ShareAll, IntPtr.Zero, OpenExisting, BackupSemantics, IntPtr.Zero);
            if (h == new IntPtr(-1)) return null;
            try
            {
                var b = new StringBuilder(1024);
                uint n = GetFinalPathNameByHandle(h, b, (uint)b.Capacity, 0);
                if (n >= b.Capacity)
                {
                    b = new StringBuilder((int)n + 1);
                    n = GetFinalPathNameByHandle(h, b, (uint)b.Capacity, 0);
                }
                if (n == 0 || n >= b.Capacity) return null;
                string s = b.ToString();
                if (s.StartsWith(@"\\?\UNC\")) return @"\\" + s.Substring(8);
                if (s.StartsWith(@"\\?\")) return s.Substring(4);
                return s;
            }
            finally { CloseHandle(h); }
        }

        // True when path is folder or lies in it (both resolved); false on another drive.
        public static bool Contains(string folder, string path)
        {
            string f = Canonical(folder), p = Canonical(path);
            return p == f || p.StartsWith(f + "\\", StringComparison.Ordinal);
        }

        [StructLayout(LayoutKind.Sequential)]
        struct FileInformation
        {
            public uint Attributes, CreatedLow, CreatedHigh, AccessedLow, AccessedHigh, WrittenLow, WrittenHigh;
            public uint VolumeSerial, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetFileInformationByHandle(IntPtr handle, out FileInformation info);

        // A file's or folder's identity (its volume's serial number and its file index), following links and
        // junctions: one folder has one identity under every name it has - another case, a junction, a loopback
        // share (\\localhost\C$\...), a mapped drive. Null when it cannot be opened.
        static string Identity(string path)
        {
            IntPtr h = CreateFile(path, 0, ShareAll, IntPtr.Zero, OpenExisting, BackupSemantics, IntPtr.Zero);
            if (h == new IntPtr(-1)) return null;
            try
            {
                FileInformation i;
                if (!GetFileInformationByHandle(h, out i)) return null;
                return i.VolumeSerial.ToString("x8") + ":" + i.IndexHigh.ToString("x8") + i.IndexLow.ToString("x8");
            }
            finally { CloseHandle(h); }
        }

        // True when full (an absolute path) lies in the game folder however it is spelled. The game folder is looked
        // up for every call, as find_game is for every check (2026-11-06: it was looked up once per process, so a
        // window opened before the Xenia kit extracted the game never protected it). The string comparison
        // (Contains) only ever refuses; then the identity of every folder above the path's nearest existing part, as
        // written and as Windows resolves it, is compared with the game folder's - which catches what no spelling
        // rule does, such as \\localhost\C$\<game>. (fbrb1943.contains walks the resolved path only; the folders as
        // written are walked here too, so a junction inside the game folder that leads out of it is refused as well.)
        public static bool InGameFolder(string full, out string game)
        {
            game = GameFolder();
            if (game == null) return false;
            if (Contains(game, full)) return true;
            string gameIdentity = Identity(game);
            if (gameIdentity == null) return false;
            string cur = FullPath(full);
            while (cur != null && !File.Exists(cur) && !Directory.Exists(cur)) cur = Path.GetDirectoryName(cur);
            if (cur == null) return false;
            string real = Final(cur);
            foreach (string start in real != null && real != cur ? new[] { cur, real } : new[] { cur })
                for (string d = start; !string.IsNullOrEmpty(d); d = Path.GetDirectoryName(d))
                    if ((d == start && File.Exists(d) ? null : Identity(d)) == gameIdentity) return true;
            return false;
        }

        static void Write(string path, byte[] data)
        {
            // \\?\ spellings are written by their plain path (the .NET Framework 4 file functions refuse the prefix);
            // the guard checks the very path that is written
            string full = FullPath(path);
            string game;
            if (InGameFolder(full, out game))
                throw new WriteRefusedException(full + " would be written in the game folder (" + game + ", also through a link, a junction or a share), "
                                                + "which the tools only read; not written");
            if (Directory.Exists(full)) throw new IOException(full + " is a folder; not written");
            // a short name beside the target ("~" + 7 hex digits + ".tmp"): a target that fits Windows' 260-character
            // limit leaves its temporary file room. The digits come from a new GUID (not a time-seeded Random:
            // processes started together would pick the same names), and a name another writer holds is skipped
            // by its error code (that writer may already have moved it away).
            string dir = Path.GetDirectoryName(full), tmp = null;
            FileStream fs = null;
            for (int attempt = 0; fs == null; attempt++)
            {
                tmp = Path.Combine(dir, "~" + System.Guid.NewGuid().ToString("N").Substring(0, 7) + ".tmp");
                try { fs = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None); }
                catch (IOException e)
                {
                    int code = Marshal.GetHRForException(e) & 0xFFFF;
                    if (attempt >= 50 || (code != 80 && code != 183 && code != 32 && code != 5)) throw;    // exists, in use
                }
            }
            try
            {
                using (fs)
                {
                    fs.Write(data, 0, data.Length);
                    fs.Flush(true);
                }
                if (!MoveFileEx(tmp, full, MoveReplaceExisting | MoveWriteThrough))
                {
                    int err = Marshal.GetLastWin32Error();
                    string why = new System.ComponentModel.Win32Exception(err).Message;
                    bool readOnly = false;
                    try { readOnly = File.Exists(full) && (File.GetAttributes(full) & FileAttributes.ReadOnly) != 0; } catch (Exception) { }
                    if (err == 5 && readOnly) throw new UnauthorizedAccessException("cannot replace " + full + ": it is read-only (" + why + ")");
                    if (err == 5 && File.Exists(full))       // not read-only: open in a program that does not let it be replaced
                        throw new IOException("cannot replace " + full + ": it is open in another program (or you may not replace it): " + why, unchecked((int)0x80070020));
                    if (err == 5) throw new UnauthorizedAccessException("cannot replace " + full + ": " + why);
                    throw new IOException("cannot replace " + full + ": " + why, unchecked((int)0x80070000) | err);
                }
            }
            catch
            {
                try { if (File.Exists(tmp)) File.Delete(tmp); } catch (Exception) { }
                throw;
            }
        }
    }
}
