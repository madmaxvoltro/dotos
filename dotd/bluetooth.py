"""Bluetooth: keyboards, mice, headsets and game controllers, through BlueZ's bluetoothctl.

Discovery and pairing run in one long-lived bluetoothctl with an agent, so the
PIN prompts some devices need ("type 123456 on the keyboard", "does this code
match?") reach the screen. Everything else is a one-shot bluetoothctl command.
"""

import re
import shutil
import subprocess
import threading
import time

from . import system

DEV = system.DEV
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x01|\x02|\r")
MAC = r"([0-9A-F]{2}(?::[0-9A-F]{2}){5})"
SCAN_SECONDS = 60

# BlueZ "Icon" -> what Dot calls it
KINDS = {
    "input-keyboard": "keyboard", "input-mouse": "mouse", "input-tablet": "mouse",
    "input-gaming": "controller", "audio-headset": "headset", "audio-headphones": "headset",
    "audio-card": "speaker", "phone": "phone",
}


def btctl(*args, timeout=10):
    code, out, _ = system.run(["bluetoothctl", *args], timeout=timeout)
    return code, ANSI.sub("", out)


class Bluetooth:
    def __init__(self):
        self.lock = threading.RLock()
        self.found = {}  # mac -> {name, kind}: seen while scanning, not paired
        self.devices = []  # paired devices, refreshed in the background
        self.scanning = False
        self.prompt = None  # {mac, kind: passkey|confirm, code}
        self.pairing = None  # {mac, status: pairing|paired|failed, error}
        self.proc = None
        self._scan_until = 0
        self.is_available = False
        self.is_powered = False
        self.dev = {"powered": True, "paired": {
            "AA:BB:CC:00:00:01": {"name": "MX Keys", "kind": "keyboard", "connected": True, "battery": 82},
        }}
        threading.Thread(target=self._refresh_loop, daemon=True).start()

    # state --------------------------------------------------------------------------------------------

    def available(self):
        if DEV:
            return True
        if not shutil.which("bluetoothctl"):
            return False
        code, out = btctl("show", timeout=5)
        return code == 0 and "Controller" in out

    def powered(self):
        if DEV:
            return self.dev["powered"]
        return "Powered: yes" in btctl("show", timeout=5)[1]

    def set_power(self, on):
        if DEV:
            self.dev["powered"] = on
        else:
            btctl("power", "on" if on else "off")
        self.refresh()

    def info(self, mac):
        _, out = btctl("info", mac, timeout=5)

        def field(name):
            m = re.search(rf"^\s*{name}:\s*(.+)$", out, re.M)
            return m.group(1).strip() if m else None

        battery = re.search(r"Battery Percentage:\s*0x[0-9a-f]+\s*\((\d+)\)", out, re.I)
        return {
            "mac": mac,
            "name": field("Alias") or field("Name") or mac,
            "kind": KINDS.get(field("Icon") or "", "other"),
            "paired": field("Paired") == "yes",
            "connected": field("Connected") == "yes",
            "battery": int(battery.group(1)) if battery else None,
        }

    def refresh(self):
        """Re-read adapter and paired devices (cached for /api/state, which polls every second)."""
        self.is_available = self.available()
        self.is_powered = self.is_available and self.powered()
        if DEV:
            self.devices = [{"mac": m, "paired": True, **d} for m, d in self.dev["paired"].items()]
            return self.devices
        if not self.is_available:
            self.devices = []
            return []
        _, out = btctl("devices", "Paired", timeout=5)
        if "Device" not in out:  # older BlueZ
            _, out = btctl("paired-devices", timeout=5)
        macs = re.findall(rf"Device {MAC}", out)
        self.devices = [d for d in (self.info(m) for m in macs) if d["paired"]]
        return self.devices

    def _refresh_loop(self):
        while True:
            try:
                self.refresh()
                if self.scanning and time.time() > self._scan_until:
                    self.stop_scan()
            except Exception as e:
                print(f"bluetooth: {e}", flush=True)
            time.sleep(3 if self.scanning else 10)

    # discovery and pairing ------------------------------------------------------------------------------

    def _send(self, line):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def start_scan(self):
        with self.lock:
            self._scan_until = time.time() + SCAN_SECONDS
            if self.scanning:
                return
            self.scanning = True
            self.found = {}
            if DEV:
                threading.Thread(target=self._dev_scan, daemon=True).start()
                return
            if not self.powered():
                btctl("power", "on")
            self.proc = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1)
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
            # KeyboardDisplay lets keyboards show a passkey and other devices confirm a code
            for cmd in ("agent KeyboardDisplay", "default-agent", "scan on"):
                self._send(cmd)

    def stop_scan(self):
        with self.lock:
            self.scanning = False
            if DEV:
                return
            if self.pairing and self.pairing["status"] == "pairing":
                return  # the agent must stay up until pairing finishes
            self._send("scan off")
            self._send("quit")
            proc, self.proc = self.proc, None
        if proc:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _read(self, proc):
        for raw in proc.stdout:
            line = ANSI.sub("", raw).strip()
            m = re.search(rf"\[NEW\] Device {MAC} (.+)$", line)
            if m and not re.fullmatch(r"[0-9A-F-]{17}", m.group(2)):  # skip nameless devices
                self.found.setdefault(m.group(1), {"name": m.group(2), "kind": "other"})
                threading.Thread(target=self._kind_of, args=(m.group(1),), daemon=True).start()
                continue
            m = re.search(rf"\[CHG\] Device {MAC} (?:Name|Alias): (.+)$", line)
            if m and m.group(1) in self.found:
                self.found[m.group(1)]["name"] = m.group(2)
                continue
            m = re.search(r"Passkey: (\d+)", line)
            if m and "Confirm" not in line:
                self.prompt = {"mac": (self.pairing or {}).get("mac"), "kind": "passkey", "code": m.group(1).zfill(6)}
                continue
            m = re.search(r"Confirm passkey (\d+)", line)
            if m:
                self.prompt = {"mac": (self.pairing or {}).get("mac"), "kind": "confirm", "code": m.group(1).zfill(6)}
                continue
            if "Enter PIN code" in line:
                self._send("0000")  # old devices with a fixed PIN
            elif "Authorize service" in line or "Accept pairing" in line:
                self._send("yes")
            elif "Pairing successful" in line and self.pairing:
                mac = self.pairing["mac"]
                self._send(f"trust {mac}")
                self._send(f"connect {mac}")
                self.pairing = {**self.pairing, "status": "paired"}
                self.prompt = None
                self.found.pop(mac, None)
            elif "Failed to pair" in line and self.pairing:
                err = line.split("org.bluez.Error.")[-1] if "org.bluez.Error." in line else "Failed"
                self.pairing = {**self.pairing, "status": "failed", "error": err}
                self.prompt = None

    def _kind_of(self, mac):
        time.sleep(1)
        d = self.info(mac)
        if mac in self.found:
            self.found[mac]["kind"] = d["kind"]

    def pair(self, mac):
        self.pairing = {"mac": mac, "name": self.found.get(mac, {}).get("name", mac), "status": "pairing", "error": None}
        self.prompt = None
        if DEV:
            threading.Thread(target=self._dev_pair, args=(mac,), daemon=True).start()
            return
        if not (self.proc and self.proc.poll() is None):
            self.start_scan()
        self._send("scan off")  # discovery slows pairing down
        self._send(f"pair {mac}")

    def answer(self, yes):
        """The user's answer to a 'does this code match?' prompt."""
        if self.prompt and self.prompt["kind"] == "confirm":
            self._send("yes" if yes else "no")
            if DEV:
                self.dev_answer = yes
        self.prompt = None

    def connect(self, mac, on):
        if DEV:
            if mac in self.dev["paired"]:
                self.dev["paired"][mac]["connected"] = on
        else:
            btctl("connect" if on else "disconnect", mac, timeout=20)
        self.refresh()

    def forget(self, mac):
        if DEV:
            self.dev["paired"].pop(mac, None)
        else:
            btctl("remove", mac)
        self.refresh()

    def status(self):
        return {
            "available": self.is_available,
            "powered": self.is_powered,
            "devices": self.devices,
            "scanning": self.scanning,
            "found": [{"mac": m, **d} for m, d in self.found.items()],
            "prompt": self.prompt,
            "pairing": self.pairing,
        }

    # dev mode -----------------------------------------------------------------------------------------------

    def _dev_scan(self):
        for mac, name, kind in [("AA:BB:CC:00:00:02", "Xbox Wireless Controller", "controller"),
                                ("AA:BB:CC:00:00:03", "WH-1000XM5", "headset"),
                                ("AA:BB:CC:00:00:04", "Logitech K380", "keyboard")]:
            time.sleep(1)
            if not self.scanning:
                return
            self.found[mac] = {"name": name, "kind": kind}

    def _dev_pair(self, mac):
        time.sleep(1)
        kind = self.found.get(mac, {}).get("kind")
        if kind == "keyboard":
            self.prompt = {"mac": mac, "kind": "passkey", "code": "482915"}
            time.sleep(4)
        elif kind == "headset":
            self.dev_answer = None
            self.prompt = {"mac": mac, "kind": "confirm", "code": "730114"}
            for _ in range(40):
                if getattr(self, "dev_answer", None) is not None:
                    break
                time.sleep(0.25)
            if self.dev_answer is False:
                self.pairing = {**self.pairing, "status": "failed", "error": "AuthenticationRejected"}
                self.prompt = None
                return
        self.prompt = None
        d = self.found.pop(mac, {"name": mac, "kind": "other"})
        self.dev["paired"][mac] = {**d, "connected": True, "battery": 100 if kind == "controller" else None}
        self.pairing = {**self.pairing, "status": "paired"}
        self.refresh()
