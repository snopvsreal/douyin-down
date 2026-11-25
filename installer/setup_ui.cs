// dytool-Setup.exe - cua so cai dat dytool (tu ve, khong vien, giong giao dien dytool).
// Build: installer\build.ps1 (csc.exe cua .NET Framework 4, C# 5: KHONG dung $"", ?., =>-member, out var).
//
// 2 ban build tu cung file nay:
//  - dytool-Setup-<ver>.exe: nhung san trinh cai Inno (build\dytool-core-<ver>.exe, resource "core.exe") -> che do cai
//  - {app}\dytool-setup.exe (khong co resource): che do "cai dat lai thu vien" (/repair), thu muc = noi chua exe
// Inno chi con chay ngam (/VERYSILENT /NOLIB) de chep file, tao loi tat, dang ky go cai. Phan tai thu vien do
// scripts\setup_app.ps1 -Machine lam; cua so nay doc tung dong no in ra:
//   "▸ N/7  ten buoc"  -> sang buoc N        "@@p|da tai|tong|ten file" -> tien do tai (MB, toc do)
//   "√ ..." xong 1 viec  "! ..." luu y (vang)  "× LỖI: ..." loi
// Tham so (giong Inno de updates.py dung duoc): /SILENT (khong trang chao, tu chay) /VERYSILENT (khong cua so)
// /RELAUNCH (xong thi mo lai dytool) /LOG=<file> /DIR=<thu muc> /repair. /SUPPRESSMSGBOXES /NORESTART: bo qua.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Globalization;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("dytool - cài đặt")]
[assembly: AssemblyProduct("dytool")]
[assembly: AssemblyDescription("Trình cài đặt dytool")]

class Opts
{
    public bool Silent, VerySilent, Relaunch, Repair;
    public string Log = "", Dir = "", Snapshot = "";

    public static Opts Parse(string[] args)
    {
        Opts o = new Opts();
        foreach (string a0 in args)
        {
            string a = a0.Trim();
            string u = a.ToUpperInvariant();
            if (u == "/SILENT") o.Silent = true;
            else if (u == "/VERYSILENT") { o.Silent = true; o.VerySilent = true; }
            else if (u == "/RELAUNCH") o.Relaunch = true;
            else if (u == "/REPAIR") o.Repair = true;
            else if (u.StartsWith("/LOG=")) o.Log = a.Substring(5).Trim('"');
            else if (u.StartsWith("/DIR=")) o.Dir = a.Substring(5).Trim('"');
            else if (u.StartsWith("/SNAPSHOT=")) o.Snapshot = a.Substring(10).Trim('"');   // (phat trien) ve 4 man hinh ra PNG
        }
        return o;
    }
}

enum St { Wait, Run, Ok, Warn, Err, Skip }

class Step
{
    public string Key, Name, Desc, Source;
    public double SizeMB;          // dung luong tai ve (uoc luong), 0 = khong tai gi
    public string SizeText;
    public double Weight;          // phut uoc luong -> chia % tong
    public int LibNo;              // so buoc trong setup_app.ps1 (1..7), 0 = khong phai
    public bool Have;              // da co tren may (cai lai / cap nhat) -> nhanh
    public string HaveText = "";
    public St State = St.Wait;
    public string Detail = "";
    public double Frac = -1;       // 0..1 khi biet, -1 khi khong
    public DateTime StartedAt;
    public bool Warned;
}

static class Fmt
{
    public static string Size(double bytes)
    {
        if (bytes >= 1024.0 * 1024 * 1024) return Num(bytes / (1024.0 * 1024 * 1024), 2) + " GB";
        if (bytes >= 1024.0 * 1024) return Num(bytes / (1024.0 * 1024), bytes >= 100.0 * 1024 * 1024 ? 0 : 1) + " MB";
        return Num(bytes / 1024.0, 0) + " KB";
    }
    public static string Mb(double mb)
    {
        if (mb >= 1000) return "~" + Num(mb / 1024.0, 1) + " GB";
        return "~" + Num(mb, 0) + " MB";
    }
    public static string Num(double v, int dec)
    {
        return v.ToString(dec == 0 ? "0" : (dec == 1 ? "0.0" : "0.00"), CultureInfo.InvariantCulture).Replace('.', ',');
    }
    public static string Dur(TimeSpan t)
    {
        if (t.TotalHours >= 1) return ((int)t.TotalHours) + " giờ " + t.Minutes + " phút";
        if (t.TotalMinutes >= 1) return ((int)t.TotalMinutes) + " phút " + t.Seconds.ToString("00") + " giây";
        return t.Seconds + " giây";
    }
}

// ============================ phan chay cai dat (luong nen) ============================
class Engine
{
    public const string AppId = "{FDB72689-8F89-4575-A7EA-90412B92121C}";
    public List<Step> Steps = new List<Step>();
    public Opts O;
    public string Dir;
    public bool Desktop = true;
    public bool WantWebView2;
    public bool Running, Finished, Failed, Cancelled;
    public string Error = "";
    public List<string> Notes = new List<string>();
    public DateTime Started, Ended;
    public event Action Changed;
    public StringBuilder LogText = new StringBuilder();
    public string LogPath;
    readonly object logLock = new object();
    Process cur;
    Step libCur;
    long lastBytes; DateTime lastBytesAt; double speed;

    public bool HasPayload { get { return Assembly.GetExecutingAssembly().GetManifestResourceStream("core.exe") != null; } }

    public Engine(Opts o)
    {
        O = o;
        if (!HasPayload && O.Snapshot == "") O.Repair = true;
        Dir = O.Dir;
        if (O.Repair) Dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        if (string.IsNullOrEmpty(Dir)) Dir = InstalledDir();
        if (string.IsNullOrEmpty(Dir)) Dir = @"C:\dytool";
        LogPath = !string.IsNullOrEmpty(O.Log) ? O.Log : Path.Combine(Path.GetTempPath(), "dytool-setup.log");
        Build();
    }

    public static string InstalledDir()
    {
        foreach (RegistryKey root in new RegistryKey[] { Registry.CurrentUser, Registry.LocalMachine })
        {
            try
            {
                using (RegistryKey k = root.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\" + AppId + "_is1"))
                {
                    if (k != null)
                    {
                        string v = k.GetValue("InstallLocation") as string;
                        if (!string.IsNullOrEmpty(v)) return v.TrimEnd('\\');
                    }
                }
            }
            catch { }
        }
        return "";
    }

    public static bool HasWebView2()
    {
        string[] keys = {
            @"HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            @"HKEY_CURRENT_USER\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" };
        foreach (string k in keys)
        {
            try
            {
                string v = Registry.GetValue(k, "pv", null) as string;
                if (!string.IsNullOrEmpty(v) && v != "0.0.0.0") return true;
            }
            catch { }
        }
        return false;
    }

    static bool VoiceStudioModel()
    {
        string[] roots = { @"C:\VoiceStudio\runtime\VoiceStudio" };
        foreach (string r in roots)
            if (File.Exists(Path.Combine(r, @"models\models--k2-fsa--OmniVoice\snapshots\c5fdb5ccb189668d56333f77ba2629f4cd7535f4\model.safetensors")))
                return true;
        return false;
    }

    Step Add(string key, string name, string desc, string source, double mb, string sizeText, double weight, int lib)
    {
        Step s = new Step();
        s.Key = key; s.Name = name; s.Desc = desc; s.Source = source; s.SizeMB = mb;
        s.SizeText = sizeText ?? Fmt.Mb(mb); s.Weight = weight; s.LibNo = lib;
        Steps.Add(s);
        return s;
    }

    // Danh sach nhung gi se tai. Dung luong la uoc luong (thu vien Python tuy co card NVIDIA hay khong).
    public void Build()
    {
        Steps.Clear();
        WantWebView2 = !HasWebView2();
        if (WantWebView2)
            Add("webview2", "Microsoft Edge WebView2", "Thành phần Windows để hiện cửa sổ dytool", "microsoft.com", 150, null, 3, 0);
        if (!O.Repair)
            Add("core", "Tệp chương trình dytool", "Mã nguồn, lối tắt Start Menu / Desktop, mục gỡ cài đặt", "có sẵn trong trình cài", 0, "3 MB", 1, 0);
        Add("uv", "Trình quản lý Python (uv)", "Cài Python và thư viện đúng phiên bản", "astral.sh", 40, null, 1, 1);
        Add("py", "Python 3.12 và thư viện AI", "PyTorch, Whisper, Demucs, OmniVoice… (có card NVIDIA thì bản GPU)", "pypi.org · download.pytorch.org", 3500, "1–3,5 GB", 25, 2);
        Add("ffmpeg", "FFmpeg", "Cắt ghép video, ghi phụ đề", "github.com/BtbN/FFmpeg-Builds", 200, null, 3, 3);
        Add("shardx", "Trình duyệt ShardX", "Mở Facebook / TikTok để đăng bài", "github.com/snopvsreal/shardx-engine", 440, null, 5, 4);
        Add("tdl", "Bộ tải video Douyin", "TikTokDownloader và thư viện của nó", "github.com/JoeanAmier/TikTokDownloader", 60, null, 3, 5);
        Add("models", "Model AI nghe và đọc chữ", "Whisper (nghe lời nói), RapidOCR (đọc phụ đề), nhận giọng nam / nữ", "huggingface.co", 1500, "~1,5 GB", 8, 6);
        Add("voice", "Model giọng nói OmniVoice", "Đọc lời lồng tiếng Việt", "huggingface.co/k2-fsa/OmniVoice", 3270, "3,3 GB", 20, 7);
        DetectHave();
    }

    public void DetectHave()
    {
        foreach (Step s in Steps)
        {
            s.Have = false; s.HaveText = "";
            switch (s.Key)
            {
                case "uv":
                    s.Have = File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".local\bin\uv.exe"));
                    break;
                case "py":
                    s.Have = File.Exists(Path.Combine(Dir, @".venv\Scripts\pythonw.exe"));
                    if (s.Have) s.HaveText = "chỉ tải phần thay đổi";
                    break;
                case "ffmpeg": s.Have = File.Exists(Path.Combine(Dir, @"tools\ffmpeg\bin\ffmpeg.exe")); break;
                case "shardx": s.Have = Directory.Exists(Path.Combine(Dir, @"data\shardx")); if (s.Have) s.HaveText = "chỉ tải bản mới nếu có"; break;
                case "tdl": s.Have = Directory.Exists(Path.Combine(Dir, @"vendor\TikTokDownloader\.venv")); break;
                case "voice":
                    s.Have = File.Exists(Path.Combine(Dir, @"data\models\omnivoice\.ready"));
                    if (!s.Have && VoiceStudioModel()) { s.Have = true; s.HaveText = "dùng lại model của VoiceStudio, không tải"; }
                    break;
            }
            if (s.Have && s.HaveText == "") s.HaveText = "đã có trên máy";
        }
    }

    public double DownloadMB()
    {
        double t = 0;
        foreach (Step s in Steps) if (!s.Have) t += s.SizeMB;
        return t;
    }

    public double Percent()
    {
        double tot = 0, done = 0;
        foreach (Step s in Steps)
        {
            double w = s.Have ? Math.Max(0.5, s.Weight * 0.15) : s.Weight;
            tot += w;
            if (s.State == St.Ok || s.State == St.Warn || s.State == St.Skip) done += w;
            else if (s.State == St.Run)
            {
                double f = s.Frac;
                if (f < 0)   // khong biet tien do: nhich theo thoi gian, toi da 90%
                    f = Math.Min(0.9, (DateTime.Now - s.StartedAt).TotalMinutes / Math.Max(0.5, w) * 0.7);
                done += w * Math.Max(0, Math.Min(1, f));
            }
        }
        return tot <= 0 ? 0 : done / tot;
    }

    public Step Current()
    {
        foreach (Step s in Steps) if (s.State == St.Run) return s;
        return null;
    }

    void Fire() { Action a = Changed; if (a != null) a(); }

    public void Log(string line)
    {
        lock (logLock)
        {
            LogText.AppendLine(line);
            try { File.AppendAllText(LogPath, DateTime.Now.ToString("HH:mm:ss") + "  " + line + Environment.NewLine, Encoding.UTF8); }
            catch { }
        }
    }

    public string LogSnapshot() { lock (logLock) { return LogText.ToString(); } }

    public void Start()
    {
        if (Running) return;
        Running = true; Finished = false; Failed = false; Cancelled = false; Error = ""; Notes.Clear();
        foreach (Step s in Steps) { if (s.State != St.Skip) { s.State = St.Wait; s.Detail = ""; s.Frac = -1; s.Warned = false; } }
        Started = DateTime.Now;
        Thread t = new Thread(RunAll);
        t.IsBackground = true;
        t.Start();
    }

    public void RunAll()
    {
        Log("===== dytool-setup " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " — thư mục " + Dir + (O.Repair ? " (cài lại thư viện)" : ""));
        try
        {
            foreach (Step s in Steps)
            {
                if (Cancelled) break;
                if (s.Key == "webview2") DoWebView2(s);
                else if (s.Key == "core") DoCore(s);
                else if (s.LibNo == 1) { DoLib(); break; }
                if (Failed) break;
            }
        }
        catch (Exception e)
        {
            Failed = true;
            if (Error == "") Error = e.Message;
            Log("LỖI: " + e);
            Step c = Current();
            if (c != null) c.State = St.Err;
        }
        if (Cancelled) { Failed = true; Error = "Đã dừng cài đặt."; }
        Ended = DateTime.Now;
        Running = false;
        Finished = !Failed;
        Log(Finished ? "XONG" : "CHƯA XONG: " + Error);
        if (Finished && O.Relaunch) Launch();
        Fire();
    }

    public void Launch()
    {
        try
        {
            ProcessStartInfo p = new ProcessStartInfo(Path.Combine(Dir, "dytool.exe"));
            p.WorkingDirectory = Dir; p.UseShellExecute = true;
            Process.Start(p);
        }
        catch (Exception e) { Log("Không mở được dytool: " + e.Message); }
    }

    void Begin(Step s, string detail)
    {
        s.State = St.Run; s.StartedAt = DateTime.Now; s.Frac = -1; s.Detail = detail;
        lastBytes = 0; lastBytesAt = DateTime.Now; speed = 0;
        Log("▸ " + s.Name);
        Fire();
    }

    void Finish(Step s)
    {
        if (s.State == St.Run) s.State = s.Warned ? St.Warn : St.Ok;
        s.Frac = -1;
        if (s.State == St.Ok) s.Detail = s.Have ? "đã kiểm tra" : "xong";
        Fire();
    }

    void Progress(Step s, long done, long total, string what)
    {
        DateTime now = DateTime.Now;
        double dt = (now - lastBytesAt).TotalSeconds;
        if (done < lastBytes) { lastBytes = 0; speed = 0; }
        if (dt >= 0.3)
        {
            double inst = (done - lastBytes) / dt;
            speed = speed <= 0 ? inst : speed * 0.7 + inst * 0.3;
            lastBytes = done; lastBytesAt = now;
        }
        string sp = speed > 1024 ? " · " + Fmt.Size(speed) + "/s" : "";
        if (total > 0)
        {
            s.Frac = Math.Min(1.0, (double)done / total);
            s.Detail = what + " — " + Fmt.Size(done) + " / " + Fmt.Size(total) + sp;
        }
        else s.Detail = what + " — " + Fmt.Size(done) + sp;
        Fire();
    }

    // ---------- WebView2 ----------
    void DoWebView2(Step s)
    {
        Begin(s, "Đang tải bộ cài của Microsoft…");
        string exe = Path.Combine(Path.GetTempPath(), "MicrosoftEdgeWebview2Setup.exe");
        try
        {
            Download("https://go.microsoft.com/fwlink/p/?LinkId=2124703", exe, s, "MicrosoftEdgeWebview2Setup.exe");
            s.Detail = "Đang cài…"; s.Frac = -1; Fire();
            int rc = RunWait(exe, "/silent /install", null);
            if (rc != 0 || !HasWebView2()) throw new Exception("mã " + rc);
            Finish(s);
        }
        catch (Exception e)
        {
            s.Warned = true; s.State = St.Warn;
            s.Detail = "Chưa cài được (" + e.Message + ") — dytool vẫn chạy, mở giao diện trong trình duyệt";
            Notes.Add(s.Name + ": " + s.Detail);
            Log("! WebView2: " + e.Message);
            Fire();
        }
    }

    void Download(string url, string path, Step s, string what)
    {
        HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
        req.UserAgent = "dytool-setup";
        req.Timeout = 60000; req.ReadWriteTimeout = 120000;
        using (WebResponse resp = req.GetResponse())
        using (Stream inp = resp.GetResponseStream())
        using (FileStream fs = File.Create(path))
        {
            long total = resp.ContentLength, done = 0;
            byte[] buf = new byte[262144];
            int n;
            DateTime last = DateTime.MinValue;
            while ((n = inp.Read(buf, 0, buf.Length)) > 0)
            {
                if (Cancelled) throw new Exception("đã dừng");
                fs.Write(buf, 0, n); done += n;
                if ((DateTime.Now - last).TotalMilliseconds > 300) { last = DateTime.Now; Progress(s, done, total, what); }
            }
        }
    }

    // ---------- tep chuong trinh (Inno chay ngam) ----------
    void DoCore(Step s)
    {
        Begin(s, "Đang chép tệp vào " + Dir + "…");
        string exe = Path.Combine(Path.GetTempPath(), "dytool-core-setup.exe");
        using (Stream r = Assembly.GetExecutingAssembly().GetManifestResourceStream("core.exe"))
        using (FileStream f = File.Create(exe)) r.CopyTo(f);
        string coreLog = Path.Combine(Path.GetTempPath(), "dytool-core.log");
        string args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOLIB /DIR=\"" + Dir + "\" /TASKS=\"" +
                      (Desktop ? "desktopicon" : "") + "\" /LOG=\"" + coreLog + "\"";
        s.Detail = "Đóng dytool nếu đang mở, chép tệp, tạo lối tắt…"; Fire();
        int rc = RunWait(exe, args, null);
        try { File.Delete(exe); } catch { }
        if (rc != 0)
        {
            s.State = St.Err; Failed = true;
            Error = "Chép tệp chương trình lỗi (mã " + rc + "). Nhật ký: " + coreLog;
            s.Detail = Error; Fire();
            return;
        }
        Finish(s);
    }

    int RunWait(string exe, string args, string cwd)
    {
        ProcessStartInfo p = new ProcessStartInfo(exe, args);
        p.UseShellExecute = false; p.CreateNoWindow = true;
        if (cwd != null) p.WorkingDirectory = cwd;
        Log("$ " + Path.GetFileName(exe) + " " + args);
        cur = Process.Start(p);
        cur.WaitForExit();
        int rc = cur.ExitCode;
        cur = null;
        return rc;
    }

    // ---------- thu vien: scripts\setup_app.ps1 -Machine ----------
    void DoLib()
    {
        string ps1 = Path.Combine(Dir, @"scripts\setup_app.ps1");
        if (!File.Exists(ps1)) throw new Exception("Không thấy " + ps1 + " — tệp chương trình chưa được chép");
        string psExe = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
        // -Wizard: trình cài Inno đã đóng dytool; cài lại thư viện (/repair) thì để script tự đóng dytool đang mở
        string args = "-NoProfile -ExecutionPolicy Bypass -File \"" + ps1 + "\" -Machine -NoPrompt" + (O.Repair ? "" : " -Wizard");
        ProcessStartInfo p = new ProcessStartInfo(psExe, args);
        p.UseShellExecute = false; p.CreateNoWindow = true; p.WorkingDirectory = Dir;
        p.RedirectStandardOutput = true; p.RedirectStandardError = true;
        p.StandardOutputEncoding = Encoding.UTF8; p.StandardErrorEncoding = Encoding.UTF8;
        Log("$ powershell " + args);
        libCur = null;
        cur = new Process();
        cur.StartInfo = p;
        cur.OutputDataReceived += delegate(object o, DataReceivedEventArgs e) { if (e.Data != null) OnLibLine(e.Data); };
        cur.ErrorDataReceived += delegate(object o, DataReceivedEventArgs e) { if (e.Data != null) OnLibLine(e.Data); };
        cur.Start();
        cur.BeginOutputReadLine(); cur.BeginErrorReadLine();
        cur.WaitForExit();
        int rc = cur.ExitCode;
        cur = null;
        if (Cancelled) { if (libCur != null) libCur.State = St.Err; return; }
        if (rc != 0)
        {
            Failed = true;
            if (Error == "") Error = "Cài thư viện lỗi (mã " + rc + ")";
            if (libCur != null) { libCur.State = St.Err; libCur.Detail = Error; libCur.Frac = -1; }
            Fire();
            return;
        }
        if (libCur != null) Finish(libCur);
        foreach (Step s in Steps) if (s.LibNo > 0 && s.State == St.Wait) { s.State = St.Ok; s.Detail = "xong"; }
        Fire();
    }

    void OnLibLine(string raw)
    {
        string line = raw.Trim();
        if (line.Length == 0) return;
        if (!line.StartsWith("@@p|")) Log(line);
        if (line.StartsWith("▸"))
        {
            string rest = line.Substring(1).Trim();
            int slash = rest.IndexOf('/');
            int n;
            if (slash > 0 && int.TryParse(rest.Substring(0, slash), out n))
            {
                if (libCur != null) Finish(libCur);
                foreach (Step s in Steps)
                    if (s.LibNo == n)
                    {
                        libCur = s;
                        Begin(s, s.Have ? "Kiểm tra bản đã có…" : "Đang chuẩn bị…");
                    }
            }
            return;
        }
        if (libCur == null) return;
        Step c = libCur;
        if (line.StartsWith("@@p|"))
        {
            string[] f = line.Split(new char[] { '|' }, 4);
            long d, t;
            if (f.Length == 4 && long.TryParse(f[1], out d) && long.TryParse(f[2], out t)) Progress(c, d, t, f[3]);
            return;
        }
        if (line.StartsWith("×"))
        {
            string m = line.Substring(1).Trim();
            if (m.StartsWith("LỖI:")) m = m.Substring(4).Trim();
            Error = m; c.Detail = m; Fire();
            return;
        }
        if (line.StartsWith("!"))
        {
            string m = line.Substring(1).Trim();
            c.Warned = true; c.Detail = m;
            Notes.Add(c.Name + ": " + m);
            Fire();
            return;
        }
        if (line.StartsWith("√"))
        {
            string m = line.Substring(1).Trim();
            if (m.StartsWith("CÀI ĐẶT XONG")) return;
            c.Detail = m; Fire();
            return;
        }
        // dong uv: "Downloading torch (2.4GiB)" / "Downloaded torch" / "Installed 120 packages in 38s"
        if (line.StartsWith("Downloading ")) { c.Detail = "Đang tải " + line.Substring(12); Fire(); return; }
        if (line.StartsWith("Installed ") || line.StartsWith("Prepared ") || line.StartsWith("Resolved "))
        { c.Detail = line; Fire(); return; }
        if (line.StartsWith("Mở dytool:") || line.StartsWith("Kiểm tra mạng") || line.StartsWith("Nhật ký:") ||
            line.StartsWith("**") || line.StartsWith("Transcript") || line.StartsWith("PS>") || line.StartsWith("Windows PowerShell"))
            return;
        if (line.Length > 140) line = line.Substring(0, 140) + "…";
        c.Detail = line; Fire();
    }

    public void Cancel()
    {
        Cancelled = true;
        Process p = cur;
        if (p != null)
        {
            try
            {
                ProcessStartInfo k = new ProcessStartInfo("taskkill", "/T /F /PID " + p.Id);
                k.UseShellExecute = false; k.CreateNoWindow = true;
                Process.Start(k).WaitForExit(15000);
            }
            catch { }
        }
        Log("Người dùng dừng cài đặt");
    }
}

// ============================ giao dien ============================
static class Theme
{
    public static Color Bg = Color.FromArgb(255, 255, 255);
    public static Color Canvas = Color.FromArgb(242, 243, 241);
    public static Color Line = Color.FromArgb(226, 229, 226);
    public static Color Border = Color.FromArgb(207, 212, 208);
    public static Color T1 = Color.FromArgb(28, 31, 29);
    public static Color T2 = Color.FromArgb(84, 90, 86);
    public static Color T3 = Color.FromArgb(128, 134, 130);
    public static Color Primary = Color.FromArgb(15, 118, 110);
    public static Color PrimaryHover = Color.FromArgb(11, 95, 88);
    public static Color PrimarySoft = Color.FromArgb(225, 242, 239);
    public static Color Ok = Color.FromArgb(21, 128, 61);
    public static Color Warn = Color.FromArgb(180, 83, 9);
    public static Color Err = Color.FromArgb(185, 28, 28);
    public static Color Track = Color.FromArgb(231, 235, 232);

    static PrivateFontCollection pfc;
    static Dictionary<string, Font> cache = new Dictionary<string, Font>();
    public static float S = 1f;   // he so DPI

    public static void LoadFonts()
    {
        pfc = new PrivateFontCollection();
        foreach (string n in new string[] { "BeVietnamPro-Regular.ttf", "BeVietnamPro-Medium.ttf", "BeVietnamPro-SemiBold.ttf", "BeVietnamPro-Bold.ttf" })
        {
            try
            {
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream(n))
                {
                    if (s == null) continue;
                    byte[] b = new byte[s.Length];
                    s.Read(b, 0, b.Length);
                    IntPtr mem = Marshal.AllocCoTaskMem(b.Length);   // giu suot doi tien trinh (GDI+ can bo nho nay)
                    Marshal.Copy(b, 0, mem, b.Length);
                    pfc.AddMemoryFont(mem, b.Length);
                }
            }
            catch { }
        }
    }

    static FontFamily Family(string want)
    {
        if (pfc != null)
        {
            foreach (FontFamily f in pfc.Families) if (f.Name == want) return f;
            foreach (FontFamily f in pfc.Families) if (f.Name == "Be Vietnam Pro") return f;
        }
        return new FontFamily("Segoe UI");
    }

    // w: 400 / 500 / 600 / 700
    public static Font F(float px, int w)
    {
        string key = px + ":" + w;
        Font f;
        if (cache.TryGetValue(key, out f)) return f;
        FontFamily fam; FontStyle st = FontStyle.Regular;
        if (w >= 700) { fam = Family("Be Vietnam Pro"); st = FontStyle.Bold; }
        else if (w >= 600) fam = Family("Be Vietnam Pro SemiBold");
        else if (w >= 500) fam = Family("Be Vietnam Pro Medium");
        else fam = Family("Be Vietnam Pro");
        if (!fam.IsStyleAvailable(st)) st = fam.IsStyleAvailable(FontStyle.Regular) ? FontStyle.Regular : FontStyle.Bold;
        f = new Font(fam, px * S, st, GraphicsUnit.Pixel);
        cache[key] = f;
        return f;
    }

    public static GraphicsPath Round(RectangleF r, float rad)
    {
        GraphicsPath p = new GraphicsPath();
        float d = rad * 2;
        if (d <= 0) { p.AddRectangle(r); return p; }
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }
}

class FlatButton : Control
{
    public bool Primary, Danger;
    bool hover, down;

    public FlatButton(string text, bool primary)
    {
        Text = text; Primary = primary;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint |
                 ControlStyles.SupportsTransparentBackColor | ControlStyles.ResizeRedraw, true);
        BackColor = Color.Transparent;
        Cursor = Cursors.Hand;
        TabStop = true;
    }

    public void FitWidth()
    {
        using (Graphics g = CreateGraphics())
        {
            SizeF sz = g.MeasureString(Text, Theme.F(13.5f, 500));
            Width = (int)Math.Ceiling(sz.Width + 34 * Theme.S);
        }
        Height = (int)(38 * Theme.S);
    }

    protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { hover = false; down = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { down = true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
    protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }
    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (e.KeyCode == Keys.Enter || e.KeyCode == Keys.Space) OnClick(EventArgs.Empty);
        base.OnKeyDown(e);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
        RectangleF r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
        Color bg, fg, bd;
        if (Primary)
        {
            bg = !Enabled ? Color.FromArgb(150, 190, 185) : (hover ? Theme.PrimaryHover : Theme.Primary);
            if (down) bg = Color.FromArgb(9, 80, 74);
            fg = Color.White; bd = bg;
        }
        else
        {
            bg = down ? Color.FromArgb(226, 230, 227) : (hover ? Color.FromArgb(238, 241, 238) : Color.White);
            fg = Enabled ? (Danger ? Theme.Err : Theme.T1) : Theme.T3; bd = Theme.Border;
        }
        using (GraphicsPath p = Theme.Round(r, 8 * Theme.S))
        {
            using (SolidBrush b = new SolidBrush(bg)) g.FillPath(b, p);
            using (Pen pen = new Pen(bd)) g.DrawPath(pen, p);
        }
        if (Focused && ShowFocusCues)
            using (GraphicsPath p = Theme.Round(new RectangleF(2.5f, 2.5f, Width - 5.5f, Height - 5.5f), 6 * Theme.S))
            using (Pen pen = new Pen(Primary ? Color.FromArgb(180, 255, 255, 255) : Theme.Primary, 1.2f)) g.DrawPath(pen, p);
        using (StringFormat sf = new StringFormat())
        using (SolidBrush b = new SolidBrush(fg))
        {
            sf.Alignment = StringAlignment.Center; sf.LineAlignment = StringAlignment.Center;
            g.DrawString(Text, Theme.F(13.5f, 500), b, new RectangleF(0, 0, Width, Height), sf);
        }
    }
}

class SetupForm : Form
{
    [DllImport("user32.dll")] static extern bool ReleaseCapture();
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int v, int size);

    enum Scr { Welcome, Install, Done, Failed }
    Scr screen;
    Engine eng;
    float S;
    int W, H;
    int pad;
    FlatButton btnPrimary, btnSecondary, btnLeft, btnFolder;
    TextBox logBox;
    bool showLog;
    Rectangle rcMin, rcClose, rcCheck;
    int hoverTitle;   // 0 khong, 1 min, 2 close
    System.Windows.Forms.Timer anim;
    float spin;
    Icon appIcon, smallIcon;
    volatile bool dirty;

    public SetupForm(Engine e)
    {
        eng = e;
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        DoubleBuffered = true;
        SetStyle(ControlStyles.ResizeRedraw | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        BackColor = Theme.Bg;
        Text = eng.O.Repair ? "Cài đặt lại thư viện dytool" : "Cài đặt dytool";
        try { appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); Icon = appIcon; } catch { }
        using (Graphics g = CreateGraphics()) S = g.DpiX / 96f;
        Theme.S = S;
        W = (int)(760 * S);
        H = (int)(684 * S);
        pad = (int)(40 * S);
        ClientSize = new Size(W, H);
        KeyPreview = true;

        btnPrimary = new FlatButton("Cài đặt", true);
        btnSecondary = new FlatButton("Huỷ", false);
        btnLeft = new FlatButton("Nhật ký", false);
        btnFolder = new FlatButton("Đổi…", false);
        foreach (FlatButton b in new FlatButton[] { btnPrimary, btnSecondary, btnLeft, btnFolder }) { b.FitWidth(); Controls.Add(b); }
        btnPrimary.Click += delegate { OnPrimary(); };
        btnSecondary.Click += delegate { OnSecondary(); };
        btnLeft.Click += delegate { ToggleLog(); };
        btnFolder.Click += delegate { PickFolder(); };

        logBox = new TextBox();
        logBox.Multiline = true; logBox.ReadOnly = true; logBox.ScrollBars = ScrollBars.Vertical;
        logBox.BorderStyle = BorderStyle.None; logBox.BackColor = Color.FromArgb(247, 248, 247);
        logBox.ForeColor = Theme.T2; logBox.Font = new Font("Consolas", 9f);
        logBox.Visible = false; logBox.WordWrap = true;
        Controls.Add(logBox);

        anim = new System.Windows.Forms.Timer();
        anim.Interval = 40;
        anim.Tick += delegate
        {
            if (eng.Running) { spin = (spin + 12) % 360; Invalidate(ListRect()); Invalidate(HeaderRect()); }
            if (dirty) { dirty = false; Sync(); Invalidate(); }
            if (showLog && logBox.Visible && Environment.TickCount % 500 < 45) RefreshLog();
        };
        anim.Start();
        eng.Changed += delegate { dirty = true; };

        SetScreen(eng.O.Silent ? Scr.Install : Scr.Welcome);
        if (eng.O.Silent) eng.Start();
    }

    // (phat trien) ve cac man hinh voi du lieu gia ra PNG de xem giao dien ma khong cai that
    public void Snapshot(string dir)
    {
        StartPosition = FormStartPosition.Manual;
        Location = new Point(-30000, -30000);       // hien ngoai man hinh de nut con duoc ve
        ShowInTaskbar = false;
        Show();
        Application.DoEvents();
        Directory.CreateDirectory(dir);
        Save(Path.Combine(dir, "1-welcome.png"));
        eng.Started = DateTime.Now.AddMinutes(-7.4);
        eng.Running = true;
        string[] det = { "xong", "", "xong", "", "", "", "", "", "" };
        int k = 0;
        foreach (Step st in eng.Steps)
        {
            st.State = k < 3 ? St.Ok : (k == 3 ? St.Run : St.Wait);
            if (st.State == St.Run) { st.StartedAt = DateTime.Now.AddMinutes(-1); st.Frac = 0.42; st.Detail = "ffmpeg-master-latest-win64-gpl.zip — 84 MB / 201 MB · 6,2 MB/s"; }
            else st.Detail = "xong";
            k++;
        }
        SetScreen(Scr.Install);
        Save(Path.Combine(dir, "2-install.png"));
        eng.Running = false; eng.Finished = true; eng.Ended = DateTime.Now;
        foreach (Step st in eng.Steps) { st.State = St.Ok; st.Frac = -1; st.Detail = "xong"; }
        Step w = eng.Steps[eng.Steps.Count - 3];
        w.State = St.Warn; w.Detail = "Một số model chưa tải xong — tool tự tải khi dùng lần đầu";
        eng.Notes.Add(w.Name + ": " + w.Detail);
        SetScreen(Scr.Done);
        Save(Path.Combine(dir, "3-done.png"));
        eng.Finished = false; eng.Failed = true;
        eng.Error = "Cài thư viện Python thất bại (mã lỗi 2) — kiểm tra mạng rồi bấm Thử lại.";
        foreach (Step st in eng.Steps) { st.State = St.Wait; st.Detail = ""; }
        int j = 0;
        foreach (Step st in eng.Steps) { if (j < 2) st.State = St.Ok; else if (j == 2 + (eng.WantWebView2 ? 1 : 0)) { st.State = St.Err; st.Detail = eng.Error; } j++; }
        SetScreen(Scr.Failed);
        Save(Path.Combine(dir, "4-failed.png"));
    }

    void Save(string path)
    {
        Invalidate(true); Update(); Application.DoEvents();
        using (Bitmap b = new Bitmap(W, H))
        {
            DrawToBitmap(b, new Rectangle(0, 0, W, H));
            b.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        }
    }

    protected override CreateParams CreateParams
    {
        get
        {
            CreateParams cp = base.CreateParams;
            cp.ClassStyle |= 0x20000;            // CS_DROPSHADOW
            cp.Style |= 0x20000;                 // WS_MINIMIZEBOX: bam icon taskbar de thu nho
            return cp;
        }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        try { int v = 2; DwmSetWindowAttribute(Handle, 33, ref v, 4); } catch { }   // Windows 11: bo goc
    }

    Rectangle HeaderRect() { return new Rectangle(0, (int)(44 * S), W, (int)(110 * S)); }
    Rectangle ListRect()
    {
        int top = (int)((screen == Scr.Welcome ? 232 : 176) * S);
        return new Rectangle(pad, top, W - 2 * pad, FooterTop() - top - (int)(14 * S));
    }
    int FooterTop() { return H - (int)(70 * S); }
    int RowH() { return (int)(44 * S); }

    void SetScreen(Scr s)
    {
        screen = s;
        int fy = FooterTop() + (int)(16 * S);
        btnFolder.Visible = s == Scr.Welcome && !eng.O.Repair;
        btnFolder.Location = new Point(W - pad - btnFolder.Width - (int)(10 * S), (int)(142 * S));
        btnLeft.Visible = s != Scr.Welcome;
        if (s == Scr.Welcome)
        {
            btnPrimary.Text = eng.O.Repair ? "Cài lại thư viện" : "Cài đặt";
            btnSecondary.Text = "Huỷ";
        }
        else if (s == Scr.Install) { btnSecondary.Text = "Dừng"; btnSecondary.Danger = true; }
        else if (s == Scr.Done) { btnPrimary.Text = "Mở dytool"; btnSecondary.Text = "Đóng"; btnSecondary.Danger = false; }
        else { btnPrimary.Text = "Thử lại"; btnSecondary.Text = "Đóng"; btnSecondary.Danger = false; }
        // KHONG dua vao .Visible: luc cua so chua hien thi, Visible cua nut con luon false
        bool hasPrimary = s != Scr.Install;
        btnPrimary.Visible = hasPrimary;
        foreach (FlatButton b in new FlatButton[] { btnPrimary, btnSecondary, btnLeft }) b.FitWidth();
        int x = W - pad;
        if (hasPrimary) { x -= btnPrimary.Width; btnPrimary.Location = new Point(x, fy); x -= (int)(10 * S); }
        x -= btnSecondary.Width; btnSecondary.Location = new Point(x, fy);
        btnLeft.Location = new Point(pad, fy);
        btnLeft.Text = showLog ? "Ẩn nhật ký" : "Nhật ký"; btnLeft.FitWidth();
        Rectangle lr = ListRect();
        logBox.Bounds = new Rectangle(lr.X + (int)(12 * S), lr.Y + (int)(10 * S), lr.Width - (int)(24 * S), lr.Height - (int)(20 * S));
        logBox.Visible = showLog && s != Scr.Welcome;
        if (hasPrimary) btnPrimary.Focus(); else btnSecondary.Focus();
        Invalidate();
    }

    void Sync()
    {
        if (screen == Scr.Install && !eng.Running && eng.Started != DateTime.MinValue)
        {
            if (eng.Finished)
            {
                if (eng.O.Silent) { Close(); return; }   // cap nhat tu dong: xong la dong (+ /RELAUNCH da mo lai dytool)
                SetScreen(Scr.Done);
            }
            else SetScreen(Scr.Failed);
            Activate();
        }
    }

    void ToggleLog()
    {
        showLog = !showLog;
        if (showLog) RefreshLog();
        SetScreen(screen);
    }

    void RefreshLog()
    {
        string t = eng.LogSnapshot();
        if (t.Length > 60000) t = t.Substring(t.Length - 60000);
        if (logBox.Text.Length != t.Length)
        {
            logBox.Text = t.Replace("\r\n", "\n").Replace("\n", "\r\n");
            logBox.SelectionStart = logBox.Text.Length; logBox.ScrollToCaret();
        }
    }

    void PickFolder()
    {
        using (FolderBrowserDialog d = new FolderBrowserDialog())
        {
            d.Description = "Chọn thư mục cài dytool (nên để ở ổ C:, không để trong AppData)";
            d.SelectedPath = Path.GetDirectoryName(eng.Dir) ?? @"C:\";
            if (d.ShowDialog(this) != DialogResult.OK) return;
            string p = d.SelectedPath.TrimEnd('\\');
            if (!p.ToLowerInvariant().EndsWith("dytool")) p = Path.Combine(p, "dytool");
            if (p.IndexOf("AppData", StringComparison.OrdinalIgnoreCase) >= 0)
                MessageBox.Show(this, "Một số máy Windows chặn chạy chương trình trong AppData — nên chọn thư mục khác, ví dụ C:\\dytool.",
                    "dytool", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            eng.Dir = p;
            eng.DetectHave();
            Invalidate();
        }
    }

    void OnPrimary()
    {
        if (screen == Scr.Welcome)
        {
            if (!eng.O.Repair)
            {
                try
                {
                    DriveInfo di = new DriveInfo(Path.GetPathRoot(eng.Dir));
                    if (di.AvailableFreeSpace < eng.DownloadMB() * 1024 * 1024 * 1.6)
                    {
                        DialogResult r = MessageBox.Show(this, "Ổ " + di.Name + " chỉ còn " + Fmt.Size(di.AvailableFreeSpace) +
                            " — có thể không đủ chỗ (cần khoảng " + Fmt.Size(eng.DownloadMB() * 1024 * 1024 * 1.6) + " sau khi giải nén). Vẫn cài?",
                            "dytool", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                        if (r != DialogResult.Yes) return;
                    }
                }
                catch { }
            }
            SetScreen(Scr.Install);
            eng.Start();
        }
        else if (screen == Scr.Done) { eng.Launch(); Close(); }
        else if (screen == Scr.Failed) { eng.DetectHave(); SetScreen(Scr.Install); eng.Start(); }
    }

    void OnSecondary()
    {
        if (screen == Scr.Install) { if (AskStop()) eng.Cancel(); }
        else Close();
    }

    bool AskStop()
    {
        return MessageBox.Show(this, "Dừng cài đặt?\n\nPhần đã tải được giữ lại — lần sau mở trình cài sẽ cài tiếp nhanh hơn.",
            "dytool", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) == DialogResult.Yes;
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (eng.Running)
        {
            if (!AskStop()) { e.Cancel = true; return; }
            eng.Cancel();
            for (int i = 0; i < 100 && eng.Running; i++) { Thread.Sleep(100); Application.DoEvents(); }
        }
        base.OnFormClosing(e);
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (e.KeyCode == Keys.Escape && screen != Scr.Install) Close();
        base.OnKeyDown(e);
    }

    // ---------- thanh tieu de tu ve ----------
    protected override void OnMouseMove(MouseEventArgs e)
    {
        int h = rcClose.Contains(e.Location) ? 2 : (rcMin.Contains(e.Location) ? 1 : 0);
        if (h != hoverTitle) { hoverTitle = h; Invalidate(new Rectangle(0, 0, W, (int)(44 * S))); }
        Cursor = (rcCheck.Contains(e.Location) && screen == Scr.Welcome && !eng.O.Repair) ? Cursors.Hand : Cursors.Default;
        base.OnMouseMove(e);
    }

    protected override void OnMouseLeave(EventArgs e)
    {
        if (hoverTitle != 0) { hoverTitle = 0; Invalidate(new Rectangle(0, 0, W, (int)(44 * S))); }
        base.OnMouseLeave(e);
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        if (e.Button == MouseButtons.Left)
        {
            if (rcClose.Contains(e.Location)) { Close(); return; }
            if (rcMin.Contains(e.Location)) { WindowState = FormWindowState.Minimized; return; }
            if (screen == Scr.Welcome && !eng.O.Repair && rcCheck.Contains(e.Location)) { eng.Desktop = !eng.Desktop; Invalidate(rcCheck); return; }
            if (e.Y < 44 * S) { ReleaseCapture(); SendMessage(Handle, 0xA1, (IntPtr)2, IntPtr.Zero); return; }
        }
        base.OnMouseDown(e);
    }

    // ---------- ve ----------
    protected override void OnPaint(PaintEventArgs e)
    {
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
        g.Clear(Theme.Bg);
        DrawTitle(g);
        DrawHeader(g);
        if (screen == Scr.Welcome) DrawWelcome(g);
        else if (!showLog) DrawList(g, ListRect(), false);
        else using (SolidBrush b = new SolidBrush(Color.FromArgb(247, 248, 247))) using (GraphicsPath p = Theme.Round(ListRect(), 10 * S)) g.FillPath(b, p);
        using (Pen pen = new Pen(Theme.Line)) g.DrawLine(pen, 0, FooterTop(), W, FooterTop());
        if (screen == Scr.Welcome && !eng.O.Repair) DrawCheck(g);
        using (Pen pen = new Pen(Theme.Border)) g.DrawRectangle(pen, 0, 0, W - 1, H - 1);
    }

    void Txt(Graphics g, string s, Font f, Color c, float x, float y)
    {
        using (SolidBrush b = new SolidBrush(c)) g.DrawString(s, f, b, x, y);
    }

    void TextIn(Graphics g, string s, Font f, Color c, RectangleF r, StringAlignment al)
    {
        using (SolidBrush b = new SolidBrush(c))
        using (StringFormat sf = new StringFormat(StringFormatFlags.NoWrap))
        {
            sf.Alignment = al; sf.LineAlignment = StringAlignment.Near; sf.Trimming = StringTrimming.EllipsisCharacter;
            g.DrawString(s, f, b, r, sf);
        }
    }

    void DrawTitle(Graphics g)
    {
        float th = 44 * S;
        using (SolidBrush b = new SolidBrush(Theme.Canvas)) g.FillRectangle(b, 0, 0, W, th);
        if (appIcon != null && smallIcon == null) smallIcon = new Icon(appIcon, (int)(20 * S), (int)(20 * S));
        if (smallIcon != null) g.DrawIcon(smallIcon, new Rectangle((int)(16 * S), (int)(12 * S), (int)(20 * S), (int)(20 * S)));
        Txt(g, "dytool", Theme.F(13.5f, 600), Theme.T1, 44 * S, 12 * S);
        Txt(g, eng.O.Repair ? "· cài đặt lại thư viện" : "· trình cài đặt", Theme.F(13f, 400), Theme.T3, 92 * S, 12.5f * S);
        int bw = (int)(46 * S);
        rcClose = new Rectangle(W - bw, 0, bw, (int)th);
        rcMin = new Rectangle(W - 2 * bw, 0, bw, (int)th);
        if (hoverTitle == 2) using (SolidBrush b = new SolidBrush(Color.FromArgb(196, 43, 28))) g.FillRectangle(b, rcClose);
        if (hoverTitle == 1) using (SolidBrush b = new SolidBrush(Color.FromArgb(226, 229, 226))) g.FillRectangle(b, rcMin);
        using (Pen p = new Pen(hoverTitle == 2 ? Color.White : Theme.T2, 1.1f * S))
        {
            float cx = rcClose.X + bw / 2f, cy = th / 2f, k = 5 * S;
            g.DrawLine(p, cx - k, cy - k, cx + k, cy + k); g.DrawLine(p, cx - k, cy + k, cx + k, cy - k);
        }
        using (Pen p = new Pen(Theme.T2, 1.1f * S))
        {
            float cx = rcMin.X + bw / 2f, cy = th / 2f;
            g.DrawLine(p, cx - 5 * S, cy, cx + 5 * S, cy);
        }
        using (Pen pen = new Pen(Theme.Line)) g.DrawLine(pen, 0, th, W, th);
    }

    string Ver()
    {
        try
        {
            object[] a = Assembly.GetExecutingAssembly().GetCustomAttributes(typeof(AssemblyInformationalVersionAttribute), false);
            if (a.Length > 0) return ((AssemblyInformationalVersionAttribute)a[0]).InformationalVersion;
        }
        catch { }
        return "";
    }

    void DrawHeader(Graphics g)
    {
        float x = pad, y = 64 * S;
        string ver = Ver();
        string title, sub;
        Color tc = Theme.T1;
        if (screen == Scr.Welcome)
        {
            title = eng.O.Repair ? "Cài đặt lại thư viện" : "Cài đặt dytool " + ver;
            sub = eng.O.Repair ? "Tải lại / sửa các phần dytool cần (Python, FFmpeg, model…). Phần nào đã đúng thì giữ nguyên."
                               : "Tải video Douyin, edit, lồng tiếng Việt rồi đăng lên Facebook / TikTok — chạy ngay trên máy bạn.";
        }
        else if (screen == Scr.Install)
        {
            title = eng.O.Repair ? "Đang cài lại thư viện" : "Đang cài dytool " + ver;
            sub = "Cần mạng ổn định. Lần đầu mất khoảng 15–30 phút — cứ để máy chạy, đừng tắt.";
        }
        else if (screen == Scr.Done)
        {
            title = "Đã cài xong";
            sub = "Mất " + Fmt.Dur(eng.Ended - eng.Started) + "." +
                  (eng.Notes.Count > 0 ? " Có " + eng.Notes.Count + " lưu ý — xem dòng màu cam." : " Mọi thứ đã sẵn sàng.");
            tc = Theme.Ok;
        }
        else
        {
            title = "Cài chưa xong";
            sub = eng.Error;
            tc = Theme.Err;
        }
        float tx = x;
        if (screen == Scr.Done || screen == Scr.Failed)
        {
            DrawStatus(g, screen == Scr.Done ? St.Ok : St.Err, x + 12 * S, y + 17 * S, 12 * S);
            tx += 34 * S;
        }
        Txt(g, title, Theme.F(24f, 600), tc, tx - 2 * S, y);
        RectangleF sr = new RectangleF(x, y + 40 * S, W - 2 * pad, 40 * S);
        using (SolidBrush b = new SolidBrush(screen == Scr.Failed ? Theme.T1 : Theme.T2)) g.DrawString(sub, Theme.F(13.5f, 400), b, sr);

        if (screen != Scr.Welcome)
        {
            double pct = eng.Finished ? 1 : eng.Percent();
            float by = 140 * S, bh = 8 * S;
            RectangleF track = new RectangleF(x, by, W - 2 * pad - 230 * S, bh);
            using (SolidBrush b = new SolidBrush(Theme.Track)) using (GraphicsPath p = Theme.Round(track, bh / 2)) g.FillPath(b, p);
            if (pct > 0.005)
            {
                RectangleF fill = new RectangleF(track.X, track.Y, Math.Max(bh, (float)(track.Width * pct)), bh);
                Color fc = screen == Scr.Failed ? Theme.Err : (screen == Scr.Done ? Theme.Ok : Theme.Primary);
                using (SolidBrush b = new SolidBrush(fc)) using (GraphicsPath p = Theme.Round(fill, bh / 2)) g.FillPath(b, p);
            }
            TimeSpan el = (eng.Running ? DateTime.Now : eng.Ended) - eng.Started;
            string right = ((int)Math.Floor(pct * 100)) + "% · đã chạy " + Fmt.Dur(el);
            TextIn(g, right, Theme.F(12.5f, 500), Theme.T2, new RectangleF(track.Right + 10 * S, by - 6 * S, 220 * S, 20 * S), StringAlignment.Far);
        }
    }

    void DrawWelcome(Graphics g)
    {
        float x = pad, w = W - 2 * pad;
        // thu muc cai
        RectangleF box = new RectangleF(x, 132 * S, w, 58 * S);
        using (SolidBrush b = new SolidBrush(Color.FromArgb(249, 250, 249))) using (GraphicsPath p = Theme.Round(box, 10 * S)) g.FillPath(b, p);
        using (Pen pen = new Pen(Theme.Line)) using (GraphicsPath p = Theme.Round(box, 10 * S)) g.DrawPath(pen, p);
        Txt(g, eng.O.Repair ? "Thư mục dytool" : "Cài vào thư mục", Theme.F(12f, 400), Theme.T3, x + 16 * S, box.Y + 9 * S);
        TextIn(g, eng.Dir, Theme.F(14.5f, 500), Theme.T1, new RectangleF(x + 16 * S, box.Y + 27 * S, w - 140 * S, 24 * S), StringAlignment.Near);

        // tieu de danh sach + tong dung luong
        float ly = 206 * S;
        Txt(g, "Sẽ tải về trong lúc cài", Theme.F(14f, 600), Theme.T1, x, ly);
        string total = "Tổng cần tải " + Fmt.Mb(eng.DownloadMB());
        try
        {
            DriveInfo di = new DriveInfo(Path.GetPathRoot(eng.Dir));
            total += " · ổ " + di.Name.TrimEnd('\\') + " còn trống " + Fmt.Size(di.AvailableFreeSpace);
        }
        catch { }
        TextIn(g, total, Theme.F(12.5f, 400), Theme.T2, new RectangleF(x + 200 * S, ly + 2 * S, w - 200 * S, 20 * S), StringAlignment.Far);
        DrawList(g, ListRect(), true);
    }

    void DrawCheck(Graphics g)
    {
        float y = FooterTop() + 25 * S, x = pad, b = 18 * S;
        rcCheck = new Rectangle((int)x, (int)(y - 2 * S), (int)(260 * S), (int)(24 * S));
        RectangleF r = new RectangleF(x, y, b, b);
        using (GraphicsPath p = Theme.Round(r, 4 * S))
        {
            if (eng.Desktop) using (SolidBrush br = new SolidBrush(Theme.Primary)) g.FillPath(br, p);
            else using (Pen pen = new Pen(Theme.Border, 1.4f)) g.DrawPath(pen, p);
        }
        if (eng.Desktop)
            using (Pen pen = new Pen(Color.White, 2f * S))
            {
                pen.StartCap = LineCap.Round; pen.EndCap = LineCap.Round;
                g.DrawLines(pen, new PointF[] { new PointF(x + 4.5f * S, y + 9.5f * S), new PointF(x + 8 * S, y + 13 * S), new PointF(x + 14 * S, y + 5.5f * S) });
            }
        Txt(g, "Tạo biểu tượng trên Desktop", Theme.F(13.5f, 400), Theme.T1, x + 28 * S, y - 1 * S);
    }

    void DrawStatus(Graphics g, St st, float cx, float cy, float r)
    {
        RectangleF c = new RectangleF(cx - r, cy - r, 2 * r, 2 * r);
        switch (st)
        {
            case St.Wait:
                using (Pen p = new Pen(Theme.Border, 1.6f * S)) g.DrawEllipse(p, c);
                break;
            case St.Run:
                using (Pen p = new Pen(Theme.Track, 2.2f * S)) g.DrawEllipse(p, c);
                using (Pen p = new Pen(Theme.Primary, 2.2f * S)) { p.StartCap = LineCap.Round; p.EndCap = LineCap.Round; g.DrawArc(p, c, spin, 100); }
                break;
            case St.Ok:
            case St.Skip:
                using (SolidBrush b = new SolidBrush(st == St.Ok ? Theme.Ok : Theme.T3)) g.FillEllipse(b, c);
                using (Pen p = new Pen(Color.White, 1.9f * S))
                {
                    p.StartCap = LineCap.Round; p.EndCap = LineCap.Round;
                    g.DrawLines(p, new PointF[] { new PointF(cx - r * 0.45f, cy + r * 0.02f), new PointF(cx - r * 0.1f, cy + r * 0.38f), new PointF(cx + r * 0.48f, cy - r * 0.35f) });
                }
                break;
            case St.Warn:
                using (SolidBrush b = new SolidBrush(Theme.Warn)) g.FillEllipse(b, c);
                using (Pen p = new Pen(Color.White, 2f * S))
                {
                    p.StartCap = LineCap.Round; p.EndCap = LineCap.Round;
                    g.DrawLine(p, cx, cy - r * 0.45f, cx, cy + r * 0.1f);
                    g.DrawLine(p, cx, cy + r * 0.42f, cx, cy + r * 0.43f);
                }
                break;
            case St.Err:
                using (SolidBrush b = new SolidBrush(Theme.Err)) g.FillEllipse(b, c);
                using (Pen p = new Pen(Color.White, 1.9f * S))
                {
                    p.StartCap = LineCap.Round; p.EndCap = LineCap.Round;
                    float k = r * 0.38f;
                    g.DrawLine(p, cx - k, cy - k, cx + k, cy + k); g.DrawLine(p, cx - k, cy + k, cx + k, cy - k);
                }
                break;
        }
    }

    void DrawList(Graphics g, Rectangle area, bool welcome)
    {
        float x = area.X, w = area.Width, rh = RowH();
        float y = area.Y;
        float iconW = welcome ? 0 : 30 * S;
        float sizeW = 150 * S;
        int i = 0;
        foreach (Step s in eng.Steps)
        {
            if (y + rh > area.Bottom + 1) break;
            if (i++ > 0) using (Pen pen = new Pen(Theme.Line)) g.DrawLine(pen, x, y, x + w, y);
            bool run = s.State == St.Run;
            if (run)
                using (SolidBrush b = new SolidBrush(Color.FromArgb(246, 251, 250))) g.FillRectangle(b, x - 8 * S, y + 1, w + 16 * S, rh - 1);
            if (!welcome) DrawStatus(g, s.State, x + 9 * S, y + rh / 2, 8.5f * S);
            float tx = x + iconW;
            Color nameC = (!welcome && s.State == St.Wait) ? Theme.T2 : Theme.T1;
            TextIn(g, s.Name, Theme.F(13.5f, 500), nameC, new RectangleF(tx, y + 5 * S, w - iconW - sizeW - 10 * S, 20 * S), StringAlignment.Near);

            // dong 2: mo ta · nguon (trang chao) / tien do chi tiet (khi cai)
            string line2; Color c2 = Theme.T3;
            if (welcome) line2 = s.Desc + "  ·  " + s.Source;
            else if (run || s.State == St.Err || s.State == St.Warn)
            {
                line2 = s.Detail != "" ? s.Detail : s.Source;
                c2 = s.State == St.Err ? Theme.Err : (s.State == St.Warn ? Theme.Warn : Theme.T2);
            }
            else if (s.State == St.Ok) line2 = s.Detail == "xong" || s.Detail == "" ? s.Source : s.Detail;
            else line2 = s.Desc + "  ·  " + s.Source;
            TextIn(g, line2, Theme.F(12f, 400), c2, new RectangleF(tx, y + 24 * S, w - iconW - (run ? 10 * S : sizeW + 10 * S), 18 * S), StringAlignment.Near);

            // cot phai: dung luong
            string size; Color sc = Theme.T1;
            if (s.Have) { size = welcome ? "đã có" : (s.State == St.Wait ? "đã có" : ""); sc = Theme.Ok; }
            else size = s.SizeText;
            if (!(run && !welcome) && size != "")
                TextIn(g, size, Theme.F(13f, 500), sc, new RectangleF(x + w - sizeW, y + 5 * S, sizeW, 20 * S), StringAlignment.Far);
            if (welcome && s.Have)
                TextIn(g, s.HaveText, Theme.F(11.5f, 400), Theme.T3, new RectangleF(x + w - sizeW - 60 * S, y + 24 * S, sizeW + 60 * S, 18 * S), StringAlignment.Far);
            else if (!welcome && run && s.Have)
                TextIn(g, s.HaveText, Theme.F(11.5f, 400), Theme.T3, new RectangleF(x + w - sizeW - 60 * S, y + 5 * S, sizeW + 60 * S, 18 * S), StringAlignment.Far);

            // thanh tien do nho duoi dong dang chay
            if (run && s.Frac >= 0)
            {
                RectangleF tr = new RectangleF(tx, y + rh - 5 * S, w - iconW, 3 * S);
                using (SolidBrush b = new SolidBrush(Theme.Track)) g.FillRectangle(b, tr);
                using (SolidBrush b = new SolidBrush(Theme.Primary)) g.FillRectangle(b, tr.X, tr.Y, (float)(tr.Width * s.Frac), tr.Height);
            }
            y += rh;
        }
    }
}

static class Program
{
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    [STAThread]
    static int Main(string[] args)
    {
        Opts o = Opts.Parse(args);
        bool created;
        using (Mutex m = new Mutex(true, "Local\\dytool-setup-ui", out created))
        {
            if (!created)
            {
                if (!o.VerySilent) MessageBox.Show("Trình cài dytool đang chạy rồi.", "dytool", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 2;
            }
            Engine e = new Engine(o);
            if (o.VerySilent)
            {
                e.Start();
                while (e.Running || e.Started == DateTime.MinValue) Thread.Sleep(300);
                return e.Finished ? 0 : 1;
            }
            try { SetProcessDPIAware(); } catch { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Theme.LoadFonts();
            if (o.Snapshot != "") { using (SetupForm f = new SetupForm(e)) f.Snapshot(o.Snapshot); return 0; }
            Application.Run(new SetupForm(e));
            return e.Finished ? 0 : (e.Running ? 3 : 1);
        }
    }
}
