using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Codex Proxy Launcher")]
[assembly: System.Reflection.AssemblyDescription("Local proxy launcher for Codex; independent community utility")]
[assembly: System.Reflection.AssemblyVersion("1.0.3.0")]

namespace CodexProxyLauncher
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            bool created;
            using (var mutex = new Mutex(true, "Local\\CodexProxyLauncher-UserWindow", out created))
            {
                if (!created) { MessageBox.Show("启动器已打开，请使用现有窗口。", "Codex 代理启动"); return; }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new LauncherForm(args));
            }
        }
    }

    class LauncherForm : Form
    {
        readonly string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        readonly JavaScriptSerializer json = new JavaScriptSerializer();
        readonly Color blue = Color.FromArgb(35, 89, 196);
        Label status;
        TextBox details;
        Button start, diagnose, settings, logs;
        ProgressBar progress;
        bool busy, restartNeeded;

        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr handle);
        [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr handle, int command);

        public LauncherForm(string[] args)
        {
            Text = "Codex 代理启动";
            ClientSize = new Size(820, 545);
            MinimumSize = new Size(760, 575);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(246, 248, 252);
            Font = new Font("Microsoft YaHei UI", 10F);
            AutoScaleMode = AutoScaleMode.Dpi;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);

            var title = new Label { Text = "Codex 代理启动", Font = new Font(Font.FontFamily, 24, FontStyle.Bold),
                ForeColor = Color.FromArgb(24, 39, 65), Location = new Point(28, 24), AutoSize = true };
            var caption = new Label { Text = "一次启动 · 检查代理 · 自动识别更新后的 Codex", ForeColor = Color.FromArgb(90, 103, 124),
                Location = new Point(31, 78), AutoSize = true };
            status = new Label { Text = "准备就绪", Location = new Point(31, 119), Size = new Size(755, 65),
                Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right, ForeColor = blue };
            progress = new ProgressBar { Location = new Point(31, 187), Size = new Size(755, 4),
                Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right, Style = ProgressBarStyle.Continuous };
            details = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
                BorderStyle = BorderStyle.FixedSingle, BackColor = Color.White, ForeColor = Color.FromArgb(49, 62, 80),
                Location = new Point(31, 210), Size = new Size(755, 206),
                Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right };
            start = MakeButton("配置并启动", 31, 437, 218);
            start.BackColor = blue; start.ForeColor = Color.White;
            diagnose = MakeButton("仅检查", 261, 437, 130);
            settings = MakeButton("代理设置", 404, 437, 130);
            logs = MakeButton("打开日志", 547, 437, 130);
            var restore = new LinkLabel { Text = "撤销工具配置", AutoSize = true, Location = new Point(31, 503),
                Anchor = AnchorStyles.Bottom | AnchorStyles.Left, LinkColor = Color.FromArgb(95, 108, 128) };
            var note = new Label { Text = "本地独立工具  v1.0.3  ·  配置与日志保存在软件目录", AutoSize = true,
                Location = new Point(322, 503), ForeColor = Color.FromArgb(120, 130, 145),
                Anchor = AnchorStyles.Bottom | AnchorStyles.Left };
            Controls.AddRange(new Control[] {title, caption, status, progress, details, start, diagnose, settings, logs, restore, note});
            start.Click += async delegate { await Run(restartNeeded ? "restart" : "launch"); };
            diagnose.Click += async delegate { await Run("diagnose"); };
            settings.Click += delegate { if (!busy) using (var f = new SettingsForm(root)) f.ShowDialog(this); };
            logs.Click += delegate { Directory.CreateDirectory(Path.Combine(root, "logs")); Process.Start("explorer.exe", Quote(Path.Combine(root, "logs"))); };
            restore.LinkClicked += async delegate { if (!busy) await Run("restore"); };
            Shown += async delegate {
                if (Array.IndexOf(args, "--no-auto") >= 0) return;
                await Run(Array.IndexOf(args, "--diagnose") >= 0 ? "diagnose" : "launch");
            };
            FormClosing += delegate(object sender, FormClosingEventArgs e) {
                if (busy) { e.Cancel = true; status.Text = "操作正在完成，请稍等后关闭窗口。"; }
            };
        }

        Button MakeButton(string text, int x, int y, int width)
        {
            var b = new Button { Text = text, Location = new Point(x, y), Size = new Size(width, 44),
                Anchor = AnchorStyles.Bottom | AnchorStyles.Left, FlatStyle = FlatStyle.Flat,
                BackColor = Color.White, ForeColor = Color.FromArgb(46, 61, 84), Cursor = Cursors.Hand };
            b.FlatAppearance.BorderColor = Color.FromArgb(215, 222, 234);
            return b;
        }

        static string Quote(string s) { return "\"" + s.Replace("\"", "\\\"") + "\""; }
        void Ui(Action action)
        {
            if (!IsDisposed && IsHandleCreated) try { BeginInvoke(action); } catch (InvalidOperationException) { }
        }
        void AddLine(string message) { details.AppendText(DateTime.Now.ToString("HH:mm:ss") + "  " + message + Environment.NewLine + Environment.NewLine); }

        async Task Run(string action)
        {
            if (busy) return;
            busy = true;
            start.Enabled = diagnose.Enabled = settings.Enabled = false;
            progress.Style = ProgressBarStyle.Marquee;
            status.ForeColor = blue;
            status.Text = "正在检查，请稍等……";
            bool failed = false;
            string resultStatus = "";
            int appPid = 0;
            try
            {
                string python = Path.Combine(root, "runtime", "python.exe");
                string backend = Path.Combine(root, "src", "backend.py");
                if (!File.Exists(python) || !File.Exists(backend)) throw new IOException("软件文件不完整。请解压整个文件夹，不要只移动 EXE。" );
                var info = new ProcessStartInfo(python, "-I -X utf8 " + Quote(backend) + " " + action) {
                    WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
                    RedirectStandardOutput = true, RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
                using (var process = new Process { StartInfo = info })
                {
                    process.Start();
                    Task<string> errors = process.StandardError.ReadToEndAsync();
                    string line;
                    while ((line = await process.StandardOutput.ReadLineAsync()) != null)
                    {
                        Dictionary<string, object> evt;
                        try { evt = json.Deserialize<Dictionary<string, object>>(line); }
                        catch { AddLine(line); continue; }
                        string message = Convert.ToString(evt["message"]);
                        string kind = Convert.ToString(evt["type"]);
                        status.Text = message;
                        AddLine(message);
                        if (kind == "error") { failed = true; status.ForeColor = Color.FromArgb(179, 63, 43); }
                        if (evt.ContainsKey("status")) resultStatus = Convert.ToString(evt["status"]);
                        if (evt.ContainsKey("pid")) appPid = Convert.ToInt32(evt["pid"]);
                    }
                    await Task.Run(() => process.WaitForExit());
                    var error = await errors;
                    if (process.ExitCode != 0 && !failed) throw new IOException(String.IsNullOrWhiteSpace(error) ? "操作未完成，请查看日志。" : error.Trim());
                    if (!String.IsNullOrWhiteSpace(error)) AddLine(error.Trim());
                }
                restartNeeded = resultStatus == "running";
                start.Text = restartNeeded ? "正常关闭并重启" : "配置并启动";
                if (restartNeeded) status.ForeColor = Color.FromArgb(170, 105, 20);
                if (resultStatus == "partial") status.ForeColor = Color.FromArgb(179, 63, 43);
                if (resultStatus == "launched" || resultStatus == "already")
                {
                    try { var p = Process.GetProcessById(appPid); ShowWindow(p.MainWindowHandle, 9); SetForegroundWindow(p.MainWindowHandle); } catch { }
                    await Task.Delay(1800);
                    busy = false;
                    Close();
                }
            }
            catch (Exception ex) { status.Text = ex.Message; status.ForeColor = Color.FromArgb(179, 63, 43); AddLine(ex.Message); }
            finally
            {
                busy = false;
                if (!IsDisposed) { start.Enabled = diagnose.Enabled = settings.Enabled = true; progress.Style = ProgressBarStyle.Continuous; }
            }
        }
    }

    class SettingsForm : Form
    {
        public SettingsForm(string root)
        {
            Text = "代理设置"; ClientSize = new Size(670, 365); FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false; StartPosition = FormStartPosition.CenterParent;
            Font = new Font("Microsoft YaHei UI", 10); BackColor = Color.White;
            var json = new JavaScriptSerializer();
            string path = Path.Combine(root, "settings.json");
            var data = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
            var mode = new ComboBox { Location = new Point(185, 28), Width = 435, DropDownStyle = ComboBoxStyle.DropDownList };
            mode.Items.AddRange(new object[] {"自动跟随 Windows 系统代理", "固定使用下面的代理地址"});
            mode.SelectedIndex = Convert.ToString(data["proxy_mode"]) == "manual" ? 1 : 0;
            var url = new TextBox { Location = new Point(185, 81), Width = 435, Text = Convert.ToString(data["proxy_url"]) };
            var exe = new TextBox { Location = new Point(185, 134), Width = 353, Text = Convert.ToString(data["proxy_app_path"]) };
            var browse = new Button { Text = "选择…", Location = new Point(548, 132), Size = new Size(72, 31) };
            var auto = new CheckBox { Text = "代理端口没开启时，自动启动代理软件", Location = new Point(185, 185), Width = 430,
                Checked = Convert.ToBoolean(data["auto_start_proxy"]) };
            var note = new Label { Text = "自动模式优先读取系统代理；系统代理关闭时使用保存地址。\n请填写 HTTP / mixed 端口。设置在下次启动时生效。", Location = new Point(26, 232), Size = new Size(616, 53), ForeColor = Color.DimGray };
            var save = new Button { Text = "保存", DialogResult = DialogResult.None, Location = new Point(510, 307), Size = new Size(110, 36) };
            Controls.AddRange(new Control[] {mode,url,exe,browse,auto,note,save});
            string[] titles = { "代理模式", "保存的代理地址", "代理软件程序" };
            for(int i=0;i<3;i++) Controls.Add(new Label {Text=titles[i], Location=new Point(27,31+53*i), AutoSize=true});
            browse.Click += delegate { using (var f = new OpenFileDialog { Filter = "程序 (*.exe)|*.exe" }) if(f.ShowDialog(this)==DialogResult.OK) exe.Text=f.FileName; };
            save.Click += delegate {
                Uri u;
                if (!Uri.TryCreate(url.Text.Trim(), UriKind.Absolute, out u) || u.Scheme != "http" || u.Port < 1 ||
                    !String.IsNullOrEmpty(u.UserInfo) || u.AbsolutePath != "/" || !String.IsNullOrEmpty(u.Query) || !String.IsNullOrEmpty(u.Fragment))
                { MessageBox.Show("请填写 HTTP 代理地址，如 http://127.0.0.1:7890。", Text); return; }
                data["proxy_mode"] = mode.SelectedIndex == 0 ? "auto" : "manual";
                data["proxy_url"] = "http://" + u.Authority;
                data["proxy_app_path"] = exe.Text.Trim();
                data["auto_start_proxy"] = auto.Checked;
                string temp=path+".tmp";
                File.WriteAllText(temp,json.Serialize(data),new UTF8Encoding(false));
                File.Replace(temp,path,null);
                DialogResult=DialogResult.OK; Close();
            };
        }
    }
}
