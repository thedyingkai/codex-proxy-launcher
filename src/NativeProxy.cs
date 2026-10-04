using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

// Transparent standard-stream transport. The Windows job owns all descendants.
internal static class NativeProxy
{
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits limits, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    private static Thread Pump(Stream source, Stream destination, bool closeDestination)
    {
        Thread thread = new Thread(delegate() {
            try {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = source.Read(buffer, 0, buffer.Length)) > 0) {
                    destination.Write(buffer, 0, count);
                    destination.Flush();
                }
            }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
            finally { if (closeDestination) try { destination.Close(); } catch (IOException) { } }
        });
        thread.IsBackground = true;
        thread.Start();
        return thread;
    }

    private static int Main()
    {
        IntPtr job = IntPtr.Zero;
        Process child = null;
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            job = CreateJobObject(IntPtr.Zero, null);
            ExtendedLimits limits = new ExtendedLimits();
            limits.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (job == IntPtr.Zero || !SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)))
                throw new IOException("Cannot create native tool process job.");
            ProcessStartInfo start = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"));
            start.Arguments = "-I -X utf8 \"" + Path.Combine(root, "src", "backend.py") + "\" native-mcp";
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardInput = start.RedirectStandardOutput = start.RedirectStandardError = true;
            child = Process.Start(start);
            if (!AssignProcessToJobObject(job, child.Handle)) {
                child.Kill();
                throw new IOException("Cannot contain native tool child processes.");
            }
            Pump(Console.OpenStandardInput(), child.StandardInput.BaseStream, true);
            Thread output = Pump(child.StandardOutput.BaseStream, Console.OpenStandardOutput(), false);
            Thread error = Pump(child.StandardError.BaseStream, Console.OpenStandardError(), false);
            child.WaitForExit();
            output.Join();
            error.Join();
            return child.ExitCode;
        }
        catch (Exception error) {
            Console.Error.WriteLine("Codex Proxy Launcher: " + error.Message);
            return 1;
        }
        finally {
            if (job != IntPtr.Zero) CloseHandle(job);
            if (child != null) child.Dispose();
        }
    }
}
