"""Talks to the OS: NetworkManager, Tailscale, power, keyboard, device info.

Every function degrades gracefully when a tool is missing, and has a fake
answer in dev mode so the UI can be built on a laptop.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time

DEV = os.environ.get("DOT_DEV") == "1"
KIOSK_ENV = os.environ.get("DOT_KIOSK_ENV", "/etc/dot/kiosk.env")


def run(cmd, timeout=10, input=None):
    """Run a command, return (code, stdout, stderr). Missing binary -> 127."""
    if not shutil.which(cmd[0]):
        return 127, "", f"{cmd[0]} not found"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def split_terse(line):
    """Split one line of `nmcli -t` output (':' separated, '\\:' escaped)."""
    return [f.replace("\\:", ":").replace("\\\\", "\\") for f in re.split(r"(?<!\\):", line)]


# network -----------------------------------------------------------------

def network_status():
    if DEV:
        return {"connected": True, "kind": "ethernet", "name": "Ethernet", "detail": "Wired, 1 Gbps", "ip": "192.168.1.42"}
    code, out, _ = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    if code != 0:
        return {"connected": False, "kind": None, "name": None, "detail": "NetworkManager unavailable", "ip": None}
    best = None
    for line in out.splitlines():
        dev, kind, state, conn = (split_terse(line) + [""] * 4)[:4]
        if kind not in ("ethernet", "wifi") or not state.startswith("connected"):
            continue
        if best is None or kind == "ethernet":
            best = (dev, kind, conn)
    if not best:
        return {"connected": False, "kind": None, "name": None, "detail": "Not connected", "ip": None}
    dev, kind, conn = best
    detail = "Wired"
    if kind == "ethernet":
        try:
            with open(f"/sys/class/net/{dev}/speed") as f:
                speed = int(f.read().strip())
            detail = f"Wired, {speed // 1000} Gbps" if speed >= 1000 else f"Wired, {speed} Mbps"
        except (OSError, ValueError):
            pass
    else:
        detail = "Wi-Fi"
    return {
        "connected": True,
        "kind": kind,
        "name": "Ethernet" if kind == "ethernet" else conn,
        "detail": detail,
        "ip": _device_ip(dev),
    }


def _device_ip(dev):
    code, out, _ = run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", dev])
    if code != 0:
        return None
    return out.split("/")[0].strip() or None


def wifi_list(rescan=False):
    if DEV:
        return [
            {"ssid": "Home 5 GHz", "signal": 86, "secure": True, "active": False},
            {"ssid": "Home 2.4 GHz", "signal": 64, "secure": True, "active": False},
            {"ssid": "Neighbour", "signal": 22, "secure": True, "active": False},
            {"ssid": "Cafe Guest", "signal": 41, "secure": False, "active": False},
        ]
    code, out, _ = run(
        ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes" if rescan else "auto"],
        timeout=20,
    )
    if code != 0:
        return []
    seen = {}
    for line in out.splitlines():
        inuse, ssid, signal, security = (split_terse(line) + [""] * 4)[:4]
        if not ssid:
            continue
        entry = {"ssid": ssid, "signal": int(signal or 0), "secure": security not in ("", "--"), "active": inuse == "*"}
        if ssid not in seen or entry["signal"] > seen[ssid]["signal"] or entry["active"]:
            seen[ssid] = entry
    return sorted(seen.values(), key=lambda w: (not w["active"], -w["signal"]))


def wifi_connect(ssid, password):
    if DEV:
        time.sleep(1.5)
        return (True, "") if password != "wrong" else (False, "Wrong Wi-Fi password")
    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    code, out, err = run(cmd, timeout=45)
    if code == 0:
        return True, ""
    msg = (err or out).strip()
    if "Secrets were required" in msg or "802-11-wireless-security" in msg:
        return False, "Wrong Wi-Fi password"
    return False, msg or "Could not connect"


# reachability --------------------------------------------------------------

def probe(host, port, timeout=2.5):
    """TCP connect time to host:port in ms, or None."""
    if DEV:
        return None if "off" in host else 14
    start = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return max(1, round((time.monotonic() - start) * 1000))
    except OSError:
        return None


# tailscale -----------------------------------------------------------------

_ts_login = {"proc": None, "qr": "", "url": ""}
_ts_lock = threading.Lock()


_dev_ts = {"installed": False, "installing_until": 0}


def tailscale_installing():
    if DEV:
        return time.time() < _dev_ts["installing_until"]
    _, out, _ = run(["systemctl", "is-active", "dot-tailscale-install"], timeout=4)
    return out.strip() in ("active", "activating")


def tailscale_install():
    """Tailscale's own installer, in its own unit so it survives a dotd restart. Needs internet."""
    if DEV:
        _dev_ts["installing_until"] = time.time() + 4
        _dev_ts["installed"] = True
        return
    if tailscale_installing():
        return
    run(["systemd-run", "--unit", "dot-tailscale-install", "--collect",
         "sh", "-c", "curl -fsSL https://tailscale.com/install.sh | sh"], timeout=10)


def tailscale_status():
    if DEV:
        installing = tailscale_installing()
        return {"installed": _dev_ts["installed"] and not installing, "installing": installing, "running": False,
                "ip": None, "loginUrl": _ts_login["url"], "qr": _ts_login["qr"]}
    if not shutil.which("tailscale"):
        return {"installed": False, "installing": tailscale_installing(), "running": False, "ip": None, "loginUrl": "", "qr": ""}
    code, out, _ = run(["tailscale", "status", "--json"], timeout=5)
    try:
        data = json.loads(out) if out else {}
    except ValueError:
        data = {}
    running = data.get("BackendState") == "Running"
    ips = (data.get("Self") or {}).get("TailscaleIPs") or []
    return {
        "installed": True,
        "installing": False,
        "running": running,
        "ip": next((i for i in ips if "." in i), None),
        "loginUrl": "" if running else _ts_login["url"],
        "qr": "" if running else _ts_login["qr"],
    }


def tailscale_up():
    """Start `tailscale up --qr` in the background and capture its login URL and QR."""
    with _ts_lock:
        if DEV:
            _ts_login["url"] = "https://login.tailscale.com/a/dev123"
            _ts_login["qr"] = "\n".join("█▀▄ " * 8 for _ in range(12))
            return
        if _ts_login["proc"] and _ts_login["proc"].poll() is None:
            return
        _ts_login.update(url="", qr="")
        proc = subprocess.Popen(
            ["tailscale", "up", "--qr", "--hostname", socket.gethostname()],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        _ts_login["proc"] = proc

    def reader():
        qr_lines = []
        for line in proc.stdout:
            m = re.search(r"https://login\.tailscale\.com/\S+", line)
            if m:
                _ts_login["url"] = m.group(0)
            if any(ch in line for ch in "█▀▄"):
                qr_lines.append(line.rstrip("\n"))
                _ts_login["qr"] = "\n".join(qr_lines)

    threading.Thread(target=reader, daemon=True).start()


def tailscale_down():
    if not DEV:
        run(["tailscale", "down"])


# power, keyboard, info -------------------------------------------------------

def power(action):
    if DEV:
        print(f"[dev] power {action}")
        return
    subprocess.Popen(["systemctl", "poweroff" if action == "shutdown" else "reboot"])


SWAY_INPUT = os.environ.get("DOT_SWAY_INPUT", "/var/lib/dot/sway-input.conf")


def apply_kiosk_env(keyboard, scale):
    """Keyboard layout: applied to the running screen right away, and saved for the next start."""
    if DEV:
        return
    layout = re.sub(r"[^a-z_,()-]", "", keyboard) or "us"
    os.makedirs(os.path.dirname(KIOSK_ENV), exist_ok=True)
    with open(KIOSK_ENV, "w") as f:
        f.write(f"XKB_DEFAULT_LAYOUT={layout}\nDOT_SCALE={float(scale):.2f}\n")
    try:
        with open(SWAY_INPUT, "w") as f:
            f.write(f"input type:keyboard xkb_layout {layout}\n")
    except OSError:
        pass
    from . import kiosk  # late: kiosk is only needed here
    if "SWAYSOCK" in kiosk.env():
        kiosk.run(["swaymsg", "input", "type:keyboard", "xkb_layout", layout], timeout=5)


def restart_kiosk():
    """Reload what's on screen without restarting the screen session itself.

    Restarting sway (the systemd unit) can leave the new session without the keyboard until a reboot
    (logind doesn't always hand the input devices over), so: reload sway's config in place and
    restart only the browsers; session.sh brings the main one straight back.
    """
    if DEV:
        return
    from . import kiosk
    if "SWAYSOCK" in kiosk.env():
        kiosk.run(["swaymsg", "reload"], timeout=5)
        kiosk.run(["pkill", "-f", "--", "--user-data-dir=/var/lib/dot/chromium( |$)"], timeout=5)
    else:  # the screen isn't running at all: starting it is safe
        subprocess.Popen(["systemctl", "start", "dot-kiosk.service"])


def set_timezone(tz):
    if tz and not DEV:
        run(["timedatectl", "set-timezone", tz])


def timezones():
    if DEV:
        return ["Europe/Amsterdam", "Europe/Brussels", "Europe/London", "Europe/Paris", "America/New_York", "UTC"]
    code, out, _ = run(["timedatectl", "list-timezones"])
    return out.split() if code == 0 else ["UTC"]


def device_info():
    def read(path, default=""):
        try:
            with open(path) as f:
                return f.read().strip().strip("\x00")
        except OSError:
            return default

    temp = read("/sys/class/thermal/thermal_zone0/temp")
    return {
        "hostname": socket.gethostname(),
        "model": read("/proc/device-tree/model", "Raspberry Pi (dev)" if DEV else "Unknown"),
        "temp": round(int(temp) / 1000) if temp.isdigit() else None,
        "uptime": int(float(read("/proc/uptime", "0 0").split()[0])),
    }


INSTALL_DIR = os.environ.get("DOT_INSTALL_DIR", "/run/dot-install")


def update_progress():
    """What install.sh --progress is doing: {pct, step, done, error} plus the last log lines."""
    state = {}
    try:
        with open(os.path.join(INSTALL_DIR, "state.json")) as f:
            state = json.load(f)
    except (OSError, ValueError):
        pass
    log = []
    try:
        with open(os.path.join(INSTALL_DIR, "log.txt"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            log = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\r", "", l) for l in f.read().decode("utf-8", "replace").splitlines()]
    except OSError:
        pass
    return {**state, "log": [l for l in log if l.strip()][-14:]}


def logs(lines=200):
    """Dot's own log (journald), newest last, for Settings > About > Show Dot's log."""
    if DEV:
        return ["(dev mode: dotd's log is in the terminal it runs in)"]
    _, out, _ = run(["journalctl", "-u", "dotd", "-u", "dot-kiosk", "-n", str(lines), "--no-pager", "-o", "short"], timeout=10)
    return out.splitlines()


def start_update():
    """Re-run the installer in its own transient unit so it outlives dotd's restart."""
    if DEV:
        print("[dev] update requested")
        os.makedirs(INSTALL_DIR, exist_ok=True)

        def fake():
            for pct, step in ((5, "Downloading Dot"), (30, "Installing Dot"), (60, "Setting up the kiosk"), (90, "Restarting Dot")):
                with open(os.path.join(INSTALL_DIR, "state.json"), "w") as f:
                    json.dump({"pct": pct, "step": step, "mode": "update"}, f)
                with open(os.path.join(INSTALL_DIR, "log.txt"), "a", encoding="utf-8") as f:
                    f.write(f"● {step}\n  ...\n")
                time.sleep(1.5)
            with open(os.path.join(INSTALL_DIR, "state.json"), "w") as f:
                json.dump({"pct": 100, "step": "Restarting the screen", "mode": "update", "done": True}, f)

        for name in ("state.json", "log.txt"):
            try:
                os.remove(os.path.join(INSTALL_DIR, name))
            except OSError:
                pass
        threading.Thread(target=fake, daemon=True).start()
        return
    subprocess.Popen(["systemd-run", "--unit", "dot-update", "--collect", "/usr/lib/dot/update.sh"])
