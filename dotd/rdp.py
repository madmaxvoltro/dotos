"""FreeRDP: find the client, check a password (auth-only), run a session.

The password only ever travels to FreeRDP over stdin (FreeRDP 3,
/args-from:stdin), so it never shows up in the process list. FreeRDP 2 has no
such option and gets it on the command line; Bookworm users should move to
Trixie / FreeRDP 3.
"""

import os
import re
import shutil
import subprocess
import threading
import time

from . import kiosk, system

INBOX = os.environ.get("DOT_INBOX", "/run/dot/inbox")  # shared into Windows as \\tsclient\dot

# preference order: native Wayland first, X11 (through Xwayland) as fallback
# FreeRDP's X11 client is the mature one (it runs on sway's Xwayland); the SDL client is marked
# experimental by FreeRDP itself and stalled after sign-in on a Pi 4. Settings > Connection can choose.
CLIENTS = {
    "auto": ["xfreerdp3", "sdl-freerdp3", "wlfreerdp3", "xfreerdp", "sdl-freerdp", "wlfreerdp"],
    "x11": ["xfreerdp3", "xfreerdp"],
    "sdl": ["sdl-freerdp3", "sdl-freerdp"],
}

QUALITY = {
    "auto": ["/network:auto"],
    "lan": ["/network:lan"],
    "broadband": ["/network:broadband-high"],
    "low": ["/network:broadband-low", "/bpp:16"],
}

# (stderr marker, result). First match wins, so specific causes go first.
ERROR_MARKERS = [
    ("PASSWORD_MUST_CHANGE", "expired"),
    ("PASSWORD_EXPIRED", "expired"),
    ("PASSWORD_CERTAINLY_EXPIRED", "expired"),
    ("ACCOUNT_LOCKED_OUT", "locked"),
    ("ACCOUNT_DISABLED", "disabled"),
    ("ACCOUNT_EXPIRED", "disabled"),
    ("LOGON_TYPE_NOT_GRANTED", "denied"),
    ("ACCOUNT_RESTRICTION", "denied"),
    ("ACCESS_DENIED", "denied"),
    ("LOGON_FAILURE", "wrong"),
    ("WRONG_PASSWORD", "wrong"),
    ("NO_OR_MISSING_CREDENTIALS", "wrong"),
    ("HOST KEY", "cert"),
    ("certificate", "cert"),
    ("DNS_NAME_NOT_FOUND", "server"),
    ("CONNECT_TRANSPORT_FAILED", "server"),
    ("CONNECT_FAILED", "server"),
    ("TLS_CONNECT_FAILED", "server"),
]

# FreeRDP client exit codes (client/common, shared by v2 and v3)
EXIT_CODES = {
    131: "server", 147: "server", 132: "wrong", 154: "wrong", 160: "wrong",
    148: "expired", 149: "expired", 152: "expired", 157: "locked",
    151: "disabled", 158: "disabled", 155: "denied", 156: "denied", 159: "denied",
}

# exit codes that mean the user ended the session on purpose
NORMAL_EXITS = {0, 1, 2, 12}  # success, disconnect, logoff, disconnect-by-user


def find_client(preference="auto"):
    if system.DEV:
        return {"bin": "xfreerdp3" if preference != "sdl" else "sdl-freerdp3", "major": 3}
    has_x11 = "DISPLAY" in kiosk.env()  # sway's Xwayland; without it the X11 client can't open a window
    for name in CLIENTS.get(preference, CLIENTS["auto"]) + CLIENTS["auto"]:
        path = shutil.which(name)
        if not path or (name.startswith("xfreerdp") and not has_x11):
            continue
        code, out, err = system.run([path, "--version"], timeout=5)
        m = re.search(r"version (\d+)\.", out + err)
        return {"bin": path, "major": int(m.group(1)) if m else 3}
    return None


def classify(code, output):
    if "Authentication only, exit status 0" in output:
        return "ok"
    for marker, result in ERROR_MARKERS:
        if marker.lower() in output.lower():
            return result
    if code == 0:
        return "ok"
    return EXIT_CODES.get(code, "unknown")


def windows_scale(screen, outputs):
    """Windows' scaling for the session. Auto uses the main monitor's real-size scale
    (from its EDID); with several monitors FreeRDP also passes each monitor's own scale."""
    if screen.get("winScale", "auto") != "auto":
        return int(screen["winScale"])
    main = next((o for o in outputs if o.get("main")), outputs[0] if outputs else None)
    pct = round((main or {}).get("scale", 1.0) * 100) if main else 100
    return max(100, min(500, pct))  # what Windows accepts


def cert_args(client, fingerprint):
    """FreeRDP accepts exactly the certificate Dot checked (or the user approved), nothing else."""
    if not fingerprint:
        return ["/cert:tofu" if client["major"] >= 3 else "/cert-tofu"]
    if fingerprint == "ignore" or client["major"] < 3:
        # Dot checked the certificate itself moments ago (FreeRDP 2 can't pin at all)
        return ["/cert:ignore" if client["major"] >= 3 else "/cert-ignore"]
    colons = ":".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2))
    return [f"/cert:fingerprint:sha256:{fingerprint},fingerprint:sha256:{colons}"]


def build_args(client, server, profile, password, cfg, auth_only=False, outputs=(), single=False, fingerprint=None):
    v3 = client["major"] >= 3
    rdp, screen = cfg["rdp"], cfg["screen"]
    domain = profile.get("domain") or server.get("domain")
    args = [f"/v:{server['host']}:{server.get('port') or 3389}", f"/u:{profile['username']}"]
    if domain:
        args.append(f"/d:{domain}")
    args.append(f"/p:{password}")
    if server.get("gateway"):
        args += [f"/g:{server['gateway']}", "/gu:" + profile["username"], "/gp:" + password]
        if domain:
            args.append(f"/gd:{domain}")
    args += cert_args(client, fingerprint)
    args.append("/log-level:INFO" if (rdp.get("debugLog") and not auth_only) else "/log-level:ERROR")
    if auth_only:
        args.append("/auth-only" if v3 else "+auth-only")
        return args
    # GFX (the H.264 graphics pipeline) is fast but some Windows / client combinations show only black;
    # "compatible" uses the older bitmap updates instead
    compatible = rdp.get("graphics") == "compatible"
    gfx = {"compatible": "-gfx", "light": "/gfx:AVC420"}.get(rdp.get("graphics"), "/gfx")
    args += ["/f", gfx, "/title:Dot Session"]
    if "xfreerdp" in os.path.basename(client["bin"]):
        args.append("/wm-class:dot-session")  # X11 only; sway's window rules match it
    args += QUALITY.get(rdp.get("quality"), QUALITY["auto"])
    args.append("/auto-reconnect" if v3 else "+auto-reconnect")
    args.append("/auto-reconnect-max-retries:20")
    if rdp.get("sound"):
        args.append("/sound:sys:pulse")  # PipeWire; dotd follows its volume (Windows sends it over RDP)
    if rdp.get("microphone"):
        args.append("/microphone")
    if rdp.get("clipboard"):
        args.append("/clipboard" if v3 else "+clipboard")
    if rdp.get("usbDrives"):
        args.append("/drive:USB,/media/dot")
    if (server.get("games") or {}).get("mode", "off") != "off":
        args.append(f"/drive:dot,{INBOX}")  # Game.exe talks to Dot through this folder
    active = [o for o in outputs if o.get("active")]
    if not single and screen.get("mode", "extend") == "extend" and len(active) > 1:
        args.append("/multimon")  # sends Windows each monitor's size, position and which one is primary
    elif not compatible:
        args.append("/dynamic-resolution")  # follows resolution changes; FreeRDP pairs it badly with /multimon
    scale = windows_scale(screen, active)
    if scale != 100:
        args.append(f"/scale-desktop:{scale}")
    return args


def _popen(client, args, quiet=False):
    """FreeRDP in the kiosk session. FreeRDP 3 reads its arguments (password included) from stdin.
    quiet: no windows at all (the password check), so a dialog can never sit waiting off-screen."""
    v3 = client["major"] >= 3
    cmd = [client["bin"], "/args-from:stdin"] if v3 else [client["bin"], *args]
    env = kiosk.env()
    if quiet and "sdl" in os.path.basename(client["bin"]):
        env["SDL_VIDEODRIVER"] = "dummy"
    proc = kiosk.popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if v3:
        proc.stdin.write("\n".join(args) + "\n")
    proc.stdin.close()
    proc.stdin = None  # closed on purpose: communicate() must not try to flush it
    return proc


def check_password(client, server, profile, password, cfg, timeout=20, fingerprint=None):
    """(result, details). result is 'ok' or an error kind: wrong, expired, locked, disabled, denied, cert,
    server, noanswer, unknown. details is the end of FreeRDP's own output, for the "Details" button."""
    if system.DEV:
        time.sleep(1.2)
        if "off" in server["host"]:
            return "server", "[ERROR] ERRCONNECT_CONNECT_TRANSPORT_FAILED (dev)"
        if password == "hang":
            time.sleep(3)
            return "noanswer", "(dev) FreeRDP gave no answer in time"
        result = {"dot": "ok", "expired": "expired", "locked": "locked"}.get(password, "wrong")
        return result, "" if result == "ok" else f"[ERROR][com.freerdp.core] - ERRCONNECT_{result.upper()} (dev)"
    proc = _popen(client, build_args(client, server, profile, password, cfg, auth_only=True, fingerprint=fingerprint),
                  quiet=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return "noanswer", ((out or "") + f"\n(no answer from FreeRDP after {timeout} s)")[-2000:]
    out = out or ""
    return classify(proc.returncode, out), out[-2000:]


class Session:
    """One running RDP session; calls on_end(reason) when FreeRDP exits."""

    def __init__(self, client, server, profile, password, cfg, on_end, outputs=(), single=False, fingerprint=None):
        self.server_id = server["id"]
        self.profile_id = profile["id"]
        self.started = time.time()
        self.on_end = on_end
        self.stop_reason = None
        self.expected = None  # an ending Dot asked for (game mode taking the session over)
        self.single = single
        self.output = []  # FreeRDP's last lines, for the session screen's Details
        self.fingerprint = fingerprint
        if system.DEV:
            self.proc = None
            threading.Thread(target=self._dev_wait, args=(password,), daemon=True).start()
            return
        self.proc = _popen(client, build_args(client, server, profile, password, cfg, outputs=outputs, single=single,
                                              fingerprint=fingerprint))
        threading.Thread(target=self._wait, daemon=True).start()

    def _wait(self):
        # line by line, so a session that's stuck connecting shows why in Dot's log right away
        for line in self.proc.stdout or ():
            line = line.rstrip()
            if line:
                self.output = (self.output + [line])[-80:]
                print(f"freerdp: {line}", flush=True)
        out = "\n".join(self.output)
        code = self.proc.wait()
        print(f"freerdp: exited with code {code}", flush=True)
        if self.stop_reason or self.expected:
            self.on_end(self.stop_reason or self.expected, "")
        elif code in NORMAL_EXITS:
            self.on_end("signed_out", "")
        else:
            self.on_end(classify(code, out), out[-2000:])

    def _dev_wait(self, password):
        if password != "dot":  # like a real session: Windows refuses within a second or two
            time.sleep(1.5)
            self.output = ["[ERROR][com.freerdp.core] - nla_recv_pdu: ERRCONNECT_LOGON_FAILURE [0x00020014] (dev)"]
            self.on_end("expired" if password == "expired" else "locked" if password == "locked" else "wrong", self.output[0])
            return
        end = time.time() + 45
        while time.time() < end and not self.stop_reason:
            time.sleep(0.2)
        self.on_end(self.stop_reason or self.expected or "signed_out", "")

    def stop(self, reason="stopped"):
        """End the session from Dot's side; Windows keeps it as a disconnected session."""
        self.stop_reason = reason
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
