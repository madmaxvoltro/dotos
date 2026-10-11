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


def tailscale_status():
    if DEV:
        return {"installed": True, "running": False, "ip": None, "loginUrl": _ts_login["url"], "qr": _ts_login["qr"]}
    if not shutil.which("tailscale"):
        return {"installed": False, "running": False, "ip": None, "loginUrl": "", "qr": ""}
    code, out, _ = run(["tailscale", "status", "--json"], timeout=5)
    try:
        data = json.loads(out) if out else {}
    except ValueError:
        data = {}
    running = data.get("BackendState") == "Running"
    ips = (data.get("Self") or {}).get("TailscaleIPs") or []
    return {
        "installed": True,
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


def apply_kiosk_env(keyboard, scale):
    """Values the kiosk session reads at start (see system/session.sh)."""
    if DEV:
        return
    os.makedirs(os.path.dirname(KIOSK_ENV), exist_ok=True)
    with open(KIOSK_ENV, "w") as f:
        f.write(f"XKB_DEFAULT_LAYOUT={re.sub(r'[^a-z_,()-]', '', keyboard) or 'us'}\n")
        f.write(f"DOT_SCALE={float(scale):.2f}\n")


def restart_kiosk():
    if not DEV:
        subprocess.Popen(["systemctl", "restart", "dot-kiosk.service"])


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


def start_update():
    """Re-run the installer in its own transient unit so it outlives dotd's restart."""
    if DEV:
        print("[dev] update requested")
        return
    subprocess.Popen(["systemd-run", "--unit", "dot-update", "--collect", "/usr/lib/dot/update.sh"])
