// DbxEdit: ToolLog, the C# tools' log and error reports.
// One of DbxEdit's sources (DbxEdit.cs and the DbxEdit.*.cs beside it, compiled together into DbxEdit, DbxEditGUI and LevelDiff).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

namespace Bfbc2Dbx
{
    // Logging and error reports, as the suite's Python tools keep them (core/bfbc2core/errors.py): a log per tool
    // in %LOCALAPPDATA%\bf1943-tool-suite\logs\<tool>-<date>.log (the 20 newest kept), and errors explained in
    // words - the likely cause and what to do - with the log's path.
    public static class ToolLog
    {
        static string tool = "tool", path;
        const int Keep = 20;

        public static string LogPath { get { return path; } }

        public static void Start(string name)
        {
            tool = name;
            try
            {
                // LOCALAPPDATA as the Python tools read it (the same folder; tests point it elsewhere)
                string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
                if (string.IsNullOrEmpty(local)) local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string dir = Path.Combine(Path.Combine(local,
                                                       "bf1943-tool-suite"), "logs");
                Directory.CreateDirectory(dir);
                path = Path.Combine(dir, name + "-" + DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + ".log");
                foreach (string old in Directory.GetFiles(dir, name + "-*.log").OrderByDescending(p => p, StringComparer.OrdinalIgnoreCase).Skip(Keep))
                    try { File.Delete(old); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                Info("--- " + name + " started: " + string.Join(" ", Environment.GetCommandLineArgs().Skip(1)) + " ---");
            }
            catch (Exception) { path = null; }               // no log folder: the tool still runs
        }

        static void Write(string level, string message)
        {
            if (path == null) return;
            try
            {
                File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss,fff", CultureInfo.InvariantCulture) + " " +
                                         level.PadRight(7) + " " + message + Environment.NewLine);
            }
            catch (Exception) { }
        }

        public static void Info(string message) { Write("INFO", message); }
        public static void Warn(string message) { Write("WARNING", message); }

        public static void Error(Exception e, string where)
        {
            string[] ce = Explain(e);
            Write("ERROR", tool + " error" + (where != null ? " in " + where : "") + ": " + ce[0] + Environment.NewLine + e);
        }

        // { cause, what to do }
        public static string[] Explain(Exception e)
        {
            if (e is WriteRefusedException)
                return new[] { e.Message, "Write the output outside the game folder: the tools only read it (extract or copy what you edit elsewhere)." };
            if (e is PathTooLongException)
                return new[] { "A path is longer than Windows allows (260 characters; .NET Framework cannot go past it): " + e.Message,
                               "Use a shorter folder (for example C:\\dbx), or shorter file names." };
            var io = e as IOException;
            int code = io != null ? (io.HResult & 0xFFFF) : 0;
            if (io != null && (code == 32 || code == 33))
                return new[] { "A file is in use by another program: " + e.Message, "Close the program using it (the game, an editor) and try again." };
            if (e is UnauthorizedAccessException)
                return new[] { "A file or folder could not be written or read: " + e.Message, "Check it is not read-only, or choose another folder." };
            if (e is FileNotFoundException || e is DirectoryNotFoundException)
                return new[] { "A file the tool needs is missing: " + e.Message, "Check the path." };
            if (e is InvalidDataException || e is System.IO.EndOfStreamException || e is ArgumentOutOfRangeException || e is IndexOutOfRangeException)
                return new[] { "The file is not the kind the tool reads, or it is damaged: " + e.Message, "Check it is the right file; unpack it again from the game if it is damaged." };
            if (e is OutOfMemoryException)
                return new[] { "The PC ran out of memory.", "Close other programs, or work on fewer files at once." };
            return new[] { e.GetType().Name + ": " + e.Message, "If it happens again, send the log file to the tool's author." };
        }

        // The text a user sees: cause, what to do, where the details are.
        public static string Report(Exception e)
        {
            string[] ce = Explain(e);
            return ce[0] + Environment.NewLine + Environment.NewLine + "What to do: " + ce[1] + Environment.NewLine + Environment.NewLine +
                   "Details were saved to:" + Environment.NewLine + (path ?? "(no log file)");
        }

        // A command-line tool's last word on an unexpected error: logged, explained on stderr, exit code 2 (DbxEdit's
        // and LevelDiff's "could not run"; 1 is a result: failed edits, a level's errors, differences).
        public static int Fail(Exception e)
        {
            Error(e, null);
            string[] ce = Explain(e);
            Console.Error.WriteLine("error: " + ce[0]);
            Console.Error.WriteLine("  what to do: " + ce[1]);
            Console.Error.WriteLine("  details: " + (path ?? "(no log file)"));
            return 2;
        }
    }
}
