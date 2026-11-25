// dytool.exe - chuong trinh mo app dytool (cua so rieng + icon khay), dat o thu muc cai dat.
// Build: installer\build.ps1 (dung csc.exe co san trong .NET Framework 4 cua Windows, khong can cai them gi).
// Chay: .venv\Scripts\pythonw.exe -m dytool.desktop (khong hien cua so den).
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("dytool")]
[assembly: System.Reflection.AssemblyProduct("dytool")]
[assembly: System.Reflection.AssemblyDescription("Douyin -> edit -> long tieng Viet -> Facebook / TikTok")]
// AssemblyVersion / FileVersion: build.ps1 sinh build/version.cs theo pyproject.toml

static class Launcher
{
    [STAThread]
    static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = Path.Combine(root, ".venv\\Scripts\\pythonw.exe");
        string setup = Path.Combine(root, "scripts\\setup_app.ps1");

        if (!File.Exists(pythonw))
        {
            DialogResult r = MessageBox.Show(
                "dytool chưa cài xong (thiếu thư viện Python).\n\nChạy bước cài đặt bây giờ? (cần mạng, lần đầu mất 15–30 phút)",
                "dytool", MessageBoxButtons.YesNo, MessageBoxIcon.Information);
            string ui = Path.Combine(root, "dytool-setup.exe");   // cua so cai dat (installer\setup_ui.cs)
            if (r == DialogResult.Yes && File.Exists(ui))
            {
                Process.Start(new ProcessStartInfo(ui, "/repair /RELAUNCH") { WorkingDirectory = root, UseShellExecute = false });
            }
            else if (r == DialogResult.Yes && File.Exists(setup))
            {
                Process.Start(new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + setup + "\" -Launch")
                { WorkingDirectory = root, UseShellExecute = false });
            }
            return 1;
        }

        try
        {
            ProcessStartInfo psi = new ProcessStartInfo(pythonw, "-m dytool.desktop")
            {
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            Process.Start(psi);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("Không mở được dytool:\n" + e.Message, "dytool", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
    }
}
