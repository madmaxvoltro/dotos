// Dot Helper: the Windows side of Dot's game mode.
//
//   Game.exe [app]          ask the Dot you're signed in from to start game mode (optionally a Sunshine app)
//   Dot Helper.exe          tray icon: game mode menu, says hello to Dot, enters pairing PINs into Sunshine
//   Dot Helper.exe exit     leave game mode
//
// It talks to Dot only through \\tsclient\dot, a folder the Dot shares into this Remote Desktop session:
// it drops cmd-*.json files there and reads status.json back. No network ports, no service.
//
// Built on the PC by install.ps1 with the C# compiler that ships with Windows (.NET Framework 4.x),
// so this file is the whole program: C# 5, no NuGet, no downloads of prebuilt binaries.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Dot Helper")]
[assembly: System.Reflection.AssemblyVersion("0.1.0.0")]

static class DotHelper
{
    public const string Version = "0.1.0";
    // DOT_SHARE points somewhere else for testing against a Dot running in dev mode
    public static readonly string Share = Environment.GetEnvironmentVariable("DOT_SHARE") ?? @"\\tsclient\dot";
    public static readonly string DataDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Dot");
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        string exe = Path.GetFileNameWithoutExtension(Application.ExecutablePath).ToLowerInvariant();
        bool isGameExe = exe.StartsWith("game");

        if (isGameExe)
            return Game(args.Length > 0 ? string.Join(" ", args) : null, true);
        if (args.Length > 0 && args[0] == "game")
            return Game(args.Length > 1 ? args[1] : null, true);
        if (args.Length > 0 && args[0] == "exit")
            return Send(Cmd("exit-game")) ? 0 : 1;

        bool first;
        using (var once = new Mutex(true, @"Local\DotHelperTray", out first))
        {
            if (!first) return 0;
            Application.Run(new Tray());
        }
        return 0;
    }

    // \\tsclient\dot ------------------------------------------------------------------------------------

    /// <summary>True when this is a Remote Desktop session from a Dot (checking the share outside RDP can hang).</summary>
    public static bool InDot()
    {
        bool testing = Environment.GetEnvironmentVariable("DOT_SHARE") != null;
        return (testing || SystemInformation.TerminalServerSession) && Directory.Exists(Share);
    }

    public static Dictionary<string, object> Cmd(string name)
    {
        var d = new Dictionary<string, object>();
        d["cmd"] = name;
        return d;
    }

    public static bool Send(Dictionary<string, object> cmd)
    {
        try
        {
            string id = DateTime.UtcNow.Ticks + "-" + Guid.NewGuid().ToString("N").Substring(0, 6);
            string tmp = Path.Combine(Share, "tmp-" + id);
            File.WriteAllText(tmp, Json.Serialize(cmd), new UTF8Encoding(false));
            File.Move(tmp, Path.Combine(Share, "cmd-" + id + ".json")); // Dot only reads finished files
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static Dictionary<string, object> Status()
    {
        try
        {
            return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(Share, "status.json")));
        }
        catch (Exception)
        {
            return null;
        }
    }

    public static string Str(Dictionary<string, object> d, string key)
    {
        object v;
        return d != null && d.TryGetValue(key, out v) && v != null ? v.ToString() : null;
    }

    // Game.exe --------------------------------------------------------------------------------------------

    static readonly Dictionary<string, string> Errors = new Dictionary<string, string>
    {
        { "off", "Game mode isn't set up for this PC yet.\n\nOn your Dot: Settings > Games, and choose how this server plays games." },
        { "nomoonlight", "Moonlight isn't installed on your Dot.\n\nRun the Dot installer again without --no-games." },
        { "nohost", "This server streams games from another PC, but none is chosen.\n\nOn your Dot: Settings > Games." },
        { "busy", "Game mode is already starting." },
    };

    public static int Game(string app, bool interactive)
    {
        if (!InDot())
        {
            if (interactive)
                MessageBox.Show("Game mode starts from a Dot.\n\nOpen Game from inside a Remote Desktop session on your Dot.",
                    "Dot", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 1;
        }
        var cmd = Cmd("game");
        if (!string.IsNullOrEmpty(app)) cmd["app"] = app;
        if (!Send(cmd))
        {
            if (interactive) MessageBox.Show("Couldn't reach the Dot.", "Dot", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }

        // wait for the Dot to pick it up
        string flow = null, error = null;
        var until = DateTime.UtcNow.AddSeconds(15);
        while (DateTime.UtcNow < until)
        {
            Thread.Sleep(300);
            var st = Status();
            string state = Str(st, "state");
            if (state == "starting" || state == "playing")
            {
                flow = Str(st, "flow");
                break;
            }
            error = Str(st, "error");
            if (error != null && Errors.ContainsKey(error)) break;
        }
        if (flow == null)
        {
            if (interactive)
                MessageBox.Show(error != null && Errors.ContainsKey(error) ? Errors[error]
                    : "The Dot didn't answer. Check Settings > Games on the Dot.",
                    "Dot", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 1;
        }

        // same PC: hand this Windows session to the GPU so Sunshine can stream it. That ends Remote Desktop;
        // the Dot expects it and reconnects after the game.
        if (flow == "same") SwitchToConsole();
        return 0;
    }

    static void SwitchToConsole()
    {
        // a scheduled task created by install.ps1 runs tscon elevated, without a UAC prompt each time
        var psi = new ProcessStartInfo("schtasks.exe", "/run /tn \"Dot\\SwitchToConsole\"");
        psi.CreateNoWindow = true;
        psi.UseShellExecute = false;
        try
        {
            using (var p = Process.Start(psi)) { p.WaitForExit(10000); }
        }
        catch (Exception) { }
    }

    // Sunshine: entering the pairing PIN that the Dot shows ---------------------------------------------------

    public static string SunshineCredentials()
    {
        try
        {
            byte[] data = File.ReadAllBytes(Path.Combine(DataDir, "sunshine.dat"));
            return Encoding.UTF8.GetString(ProtectedData.Unprotect(data, null, DataProtectionScope.LocalMachine));
        }
        catch (Exception)
        {
            return null; // not set up by install.ps1: the user types the PIN in Sunshine's page instead
        }
    }

    public static bool EnterPin(string pin, string name)
    {
        string creds = SunshineCredentials();
        if (creds == null) return false;
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        // Sunshine's web UI on this PC uses a self-signed certificate; only trust it for localhost
        ServicePointManager.ServerCertificateValidationCallback = (s, cert, chain, errors) =>
        {
            var req = s as HttpWebRequest;
            return req != null && req.RequestUri.IsLoopback;
        };
        try
        {
            var req = (HttpWebRequest)WebRequest.Create("https://localhost:47990/api/pin");
            req.Method = "POST";
            req.ContentType = "application/json";
            req.Timeout = 10000;
            req.Headers["Authorization"] = "Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes(creds));
            var body = new Dictionary<string, object>();
            body["pin"] = pin;
            body["name"] = name;
            byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(body));
            using (var s = req.GetRequestStream()) { s.Write(bytes, 0, bytes.Length); }
            using (var res = (HttpWebResponse)req.GetResponse())
            using (var r = new StreamReader(res.GetResponseStream()))
            {
                string text = r.ReadToEnd();
                return (int)res.StatusCode < 300 && !text.Contains("\"status\":false") && !text.Contains("\"status\": false");
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static string GameHost()
    {
        if (Process.GetProcessesByName("sunshine").Length > 0) return "Sunshine";
        return "none";
    }
}

/// <summary>The tray icon: a white dot, like the Dot's own mark.</summary>
class Tray : ApplicationContext
{
    readonly NotifyIcon icon;
    readonly SynchronizationContext ui;
    readonly System.Threading.Timer timer;
    bool helloSent;
    string lastPin;
    int busy;

    public Tray()
    {
        ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        var menu = new ContextMenuStrip();
        menu.Items.Add("Start game mode", null, delegate { ThreadPool.QueueUserWorkItem(delegate { DotHelper.Game(null, true); }); });
        menu.Items.Add("Back to work", null, delegate { DotHelper.Send(DotHelper.Cmd("exit-game")); });
        menu.Items.Add("Quit the game and go back", null, delegate
        {
            var c = DotHelper.Cmd("exit-game");
            c["quit"] = true;
            DotHelper.Send(c);
        });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Close Dot Helper", null, delegate { ExitThread(); });

        icon = new NotifyIcon();
        icon.Icon = MakeIcon();
        icon.Text = "Dot";
        icon.ContextMenuStrip = menu;
        icon.Visible = true;
        icon.DoubleClick += delegate { ThreadPool.QueueUserWorkItem(delegate { DotHelper.Game(null, true); }); };

        timer = new System.Threading.Timer(Tick, null, 1000, 2000);
    }

    void Tick(object _)
    {
        if (Interlocked.Exchange(ref busy, 1) == 1) return;
        try
        {
            if (!DotHelper.InDot())
            {
                helloSent = false;
                return;
            }
            if (!helloSent)
            {
                var hello = DotHelper.Cmd("hello");
                hello["version"] = DotHelper.Version;
                hello["host"] = DotHelper.GameHost();
                helloSent = DotHelper.Send(hello);
            }
            var st = DotHelper.Status();
            object pairObj;
            var pair = st != null && st.TryGetValue("pair", out pairObj) ? pairObj as Dictionary<string, object> : null;
            string pin = DotHelper.Str(pair, "pin");
            if (pin != null && pin != lastPin)
            {
                lastPin = pin;
                if (DotHelper.EnterPin(pin, DotHelper.Str(pair, "name") ?? "Dot"))
                {
                    DotHelper.Send(DotHelper.Cmd("pin-entered"));
                    Balloon("Paired", "Your Dot can now stream games from this PC.");
                }
                else
                {
                    Balloon("Pair your Dot", "Open Sunshine (https://localhost:47990), go to PIN and enter " + pin + ".");
                }
            }
        }
        catch (Exception) { }
        finally
        {
            Interlocked.Exchange(ref busy, 0);
        }
    }

    void Balloon(string title, string text)
    {
        ui.Post(delegate { icon.ShowBalloonTip(8000, title, text, ToolTipIcon.Info); }, null);
    }

    static Icon MakeIcon()
    {
        var bmp = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            g.FillEllipse(Brushes.White, 4, 4, 24, 24);
            g.DrawEllipse(new Pen(Color.FromArgb(160, 0, 0, 0), 1.5f), 4, 4, 24, 24); // visible on light taskbars
        }
        return Icon.FromHandle(bmp.GetHicon());
    }

    protected override void ExitThreadCore()
    {
        timer.Dispose();
        icon.Visible = false;
        icon.Dispose();
        base.ExitThreadCore();
    }
}
