using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Threading;
using System.Windows.Forms;

internal static class CloudProxy
{
    [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit; public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong A, B, C, D, E, F; }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
        public BasicLimits Basic; public IoCounters Io;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimits limits, uint size);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);

    [STAThread] static void Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string state = Path.Combine(root, "state", "cloud-network");
        Directory.CreateDirectory(state);
        string stop = Path.Combine(state, "stop");
        if (Array.IndexOf(args, "--stop") >= 0) { File.WriteAllText(stop, "stop"); return; }
        var identity = new WindowsPrincipal(WindowsIdentity.GetCurrent());
        if (!identity.IsInRole(WindowsBuiltInRole.Administrator)) {
            try {
                Process.Start(new ProcessStartInfo(Application.ExecutablePath, "--elevated") {
                    UseShellExecute=true, Verb="runas", WindowStyle=ProcessWindowStyle.Hidden,
                    WorkingDirectory=root });
            } catch (Exception error) { File.WriteAllText(Path.Combine(state, "elevation-error.txt"), error.Message); }
            return;
        }
        bool created;
        using (var mutex = new Mutex(true, "Local\\CodexCloudProxy-UserNetwork", out created)) {
            if (!created) return;
            IntPtr job = IntPtr.Zero;
            Process child = null;
            using (var tray = new NotifyIcon()) {
                try {
                    job = CreateJobObject(IntPtr.Zero, null);
                    var limits = new ExtendedLimits(); limits.Basic.LimitFlags = 0x2000;
                    if (job == IntPtr.Zero || !SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)))
                        throw new IOException("无法建立网络进程管理。没有修改网络。");
                    var start = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"),
                        "-I -X utf8 \"" + Path.Combine(root, "src", "cloud_network.py") + "\" run") {
                        UseShellExecute=false, CreateNoWindow=true, WindowStyle=ProcessWindowStyle.Hidden,
                        WorkingDirectory=root, RedirectStandardInput=true,
                        RedirectStandardOutput=true, RedirectStandardError=true };
                    child = Process.Start(start);
                    if (!AssignProcessToJobObject(job, child.Handle)) {
                        child.Kill(); throw new IOException("无法管理网络子进程。没有修改网络。");
                    }
                    child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
                    child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) {
                        if (e.Data != null) try { File.AppendAllText(Path.Combine(state,"supervisor-error.log"),e.Data+Environment.NewLine); } catch { }
                    };
                    child.BeginOutputReadLine(); child.BeginErrorReadLine();
                    child.StandardInput.WriteLine("start"); child.StandardInput.Flush();
                    tray.Icon = SystemIcons.Application; tray.Text = "Codex 云端网络代理";
                    var menu = new ContextMenuStrip();
                    menu.Items.Add("查看网络状态", null, delegate {
                        string path=Path.Combine(state,"status.json");
                        MessageBox.Show(File.Exists(path)?File.ReadAllText(path):"正在准备", "Codex 云端网络代理");
                    });
                    menu.Items.Add("关闭网络代理并恢复路由", null, delegate {
                        File.WriteAllText(stop,"stop"); tray.Text="正在关闭 Codex 网络代理";
                    });
                    tray.ContextMenuStrip=menu; tray.Visible=true;
                    var timer = new System.Windows.Forms.Timer(); timer.Interval=500;
                    timer.Tick += delegate {
                        if (child.HasExited) {
                            timer.Stop(); Application.ExitThread();
                        }
                    };
                    timer.Start(); Application.Run(); timer.Dispose();
                } catch (Exception error) {
                    File.WriteAllText(Path.Combine(state,"supervisor-error.log"),error.ToString());
                } finally {
                    tray.Visible=false;
                    if (child != null && !child.HasExited) {
                        File.WriteAllText(stop,"stop"); child.WaitForExit(10000);
                    }
                    if (job!=IntPtr.Zero) CloseHandle(job);
                    if (child!=null) child.Dispose();
                }
            }
        }
    }
}
