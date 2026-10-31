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
    // - never into the BF1943 game folder (core game1943.find_game: BF1943_GAME, else the Xenia kit's dist\xenon in
    //   the suite; a folder counts when it has levels\), however the path is cased and through whatever link or
    //   junction it leads there (both sides resolved as os.path.realpath + normcase do);
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

        // The folder core game1943.find_game() finds, or null when there is none.
        public static string GameFolder()
        {
            var candidates = new List<string>();
            string env = Environment.GetEnvironmentVariable("BF1943_GAME");
            if (!string.IsNullOrEmpty(env)) candidates.Add(env);
            string suite = SuiteRoot();
            if (suite != null) candidates.Add(Path.Combine(suite, "bf1943-xenia-kit", "output", "title", "dist", "xenon"));
            foreach (string p in candidates)
            {
                try { if (Directory.Exists(Path.Combine(p, "levels"))) return Path.GetFullPath(p); }
                catch (ArgumentException) { }
                catch (NotSupportedException) { }
            }
            return null;
        }

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
            string full = Path.GetFullPath(path);
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

        // per process: the game folder and the identities of the folders written into (a folder conversion writes
        // thousands of files into a few folders)
        static string gameFolder, gameIdentity;
        static bool gameKnown;
        static readonly Dictionary<string, string> folderIdentity = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        static string FolderIdentity(string folder)
        {
            string id;
            if (!folderIdentity.TryGetValue(folder, out id)) folderIdentity[folder] = id = Identity(folder);
            return id;
        }

        // True when full (an absolute path) lies in the game folder however it is spelled. The string comparison
        // (Contains) only ever refuses; then the identity of every folder above the path's nearest existing part, as
        // written and as Windows resolves it, is compared with the game folder's - which catches what no spelling
        // rule does, such as \\localhost\C$\<game>.
        public static bool InGameFolder(string full, out string game)
        {
            if (!gameKnown)
            {
                gameFolder = GameFolder();
                gameIdentity = gameFolder == null ? null : Identity(gameFolder);
                gameKnown = true;
            }
            game = gameFolder;
            if (game == null) return false;
            if (Contains(game, full)) return true;
            if (gameIdentity == null) return false;
            string cur = full;
            while (cur != null && !File.Exists(cur) && !Directory.Exists(cur)) cur = Path.GetDirectoryName(cur);
            if (cur == null) return false;
            string real = Final(cur);
            foreach (string start in real != null && real != cur ? new[] { cur, real } : new[] { cur })
                for (string d = start; !string.IsNullOrEmpty(d); d = Path.GetDirectoryName(d))
                    if ((d == start && File.Exists(d) ? null : FolderIdentity(d)) == gameIdentity) return true;
            return false;
        }

        static void Write(string path, byte[] data)
        {
            string full = Path.GetFullPath(path);
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
