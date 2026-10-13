"""Monitors: detect on/off over CEC, DDC/CI and HDMI hot-plug, control them over
DDC/CI, and run standby when the main one turns off (snapshot, mute, unplug USB, lock).

Detection order per monitor, best first:
  1. HDMI hot-plug says unplugged                     explicit
  2. CEC power status (TVs and many monitors)          explicit
  3. DDC/CI power mode, VCP D6                          explicit
  4. DDC/CI that answered before and now stays quiet    implied, never trusted to unplug inputs
  5. HDMI hot-plug says connected                       weak "on"
"""

import glob
import json
import os
import re
import select
import struct
import threading
import time
import traceback

from . import edid, kiosk, system

DEV = system.DEV
SNAPSHOT_FILE = os.environ.get("DOT_STANDBY_FILE", "/var/lib/dot/standby.json")
KIOSK_USER = os.environ.get("DOT_USER", "dot")

VCP = {"brightness": 0x10, "contrast": 0x12, "volume": 0x62, "power": 0xD6}
POLL = 3  # seconds between checks
DDC_SILENT_AFTER = 3  # failed reads in a row before a quiet monitor counts as off


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _write(path, value):
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError:
        return False


# DDC/CI ---------------------------------------------------------------------------------------------

class DDC:
    """One monitor's DDC/CI channel (one I2C bus per HDMI port)."""

    lock = threading.Lock()  # ddcutil is happiest with one transaction at a time

    def __init__(self, bus, model=None, fake=None):
        self.bus = bus
        self.model = model
        self.fake = fake
        self.slow = False  # some monitors on the Pi's HDMI I2C only answer with slower timing

    def _run(self, *args):
        """ddcutil on this bus; when it fails, try once more with slower timing and keep that if it helps."""
        base = ["ddcutil", "--bus", self.bus]
        for slow in ([True] if self.slow else [False, True]):
            cmd = base + (["--sleep-multiplier", "2"] if slow else []) + list(args)
            with DDC.lock:
                rc, out, err = system.run(cmd, timeout=10)
            if rc == 0 and "error" not in (out + err).lower():
                if slow and not self.slow:
                    self.slow = True
                    print(f"monitor: bus {self.bus} needs slower DDC timing", flush=True)
                return rc, out
        return rc, out

    @classmethod
    def detect_all(cls):
        """{connector: DDC} for every monitor that answers DDC/CI."""
        if DEV:
            return {
                "HDMI-A-1": cls("dev1", "DELL U2720Q", {0x10: [70, 100], 0x12: [50, 100], 0x62: [30, 100], 0xD6: [1, None]}),
                "HDMI-A-2": cls("dev2", "VG249Q", {0x10: [80, 100], 0x12: [60, 100], 0xD6: [1, None]}),
            }
        _, out, _ = system.run(["ddcutil", "detect", "--terse"], timeout=30)
        if "I2C bus" not in out:  # nothing answered: try again, slower
            _, out, _ = system.run(["ddcutil", "detect", "--terse", "--sleep-multiplier", "2"], timeout=45)
        found = {}
        for block in re.split(r"\n(?=Display \d+)", out):
            bus = re.search(r"I2C bus:\s+/dev/i2c-(\d+)", block)
            if not bus:
                continue
            conn = re.search(r"DRM connector:\s+card\d+-(\S+)", block)
            mon = re.search(r"Monitor:\s+(.+)", block)
            model = None
            if mon:
                parts = [p.strip() for p in mon.group(1).split(":")]
                model = parts[1] if len(parts) > 1 and parts[1] else parts[0]
            found[conn.group(1) if conn else f"i2c-{bus.group(1)}"] = cls(bus.group(1), model)
        # some ddcutil versions don't name the HDMI port: map those through the kernel's port -> I2C link
        for key in [k for k in found if k.startswith("i2c-")]:
            for path in glob.glob("/sys/class/drm/card*-HDMI-A-*"):
                connector = os.path.basename(path).split("-", 1)[1]
                if edid.i2c_bus(connector) == key[4:] and connector not in found:
                    found[connector] = found.pop(key)
                    break
        return found

    def get(self, code):
        """(value, max) for continuous features, (value, None) for others, or None."""
        if self.fake is not None:
            return tuple(self.fake[code]) if code in self.fake else None
        _, out = self._run("--brief", "getvcp", f"{code:02X}")
        m = re.search(r"VCP [0-9A-F]{2} C (\d+) (\d+)", out, re.I)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r"VCP [0-9A-F]{2} SNC x([0-9a-f]+)", out, re.I)
        if m:
            return int(m.group(1), 16), None
        return None

    def set(self, code, value):
        if self.fake is not None:
            self.fake.setdefault(code, [0, 100])[0] = int(value)
            return True
        rc, _ = self._run("--noverify", "setvcp", f"{code:02X}", str(int(value)))
        return rc == 0


# HDMI-CEC -------------------------------------------------------------------------------------------

class CEC:
    def __init__(self, dev):
        self.dev = dev

    @classmethod
    def detect_all(cls, connectors):
        """{connector: CEC}. The Pi exposes one CEC adapter per HDMI port, in port order."""
        if DEV:
            return {}
        found = {}
        for dev, conn in zip(sorted(glob.glob("/dev/cec*")), sorted(connectors)):
            system.run(["cec-ctl", "-d", dev, "--playback", "--osd-name", "Dot"], timeout=5)
            cec = cls(dev)
            if cec.power() is not None:
                found[conn] = cec
        return found

    def power(self):
        """'on', 'standby', 'to-on', 'to-standby' or None when nothing answers."""
        _, out, _ = system.run(["cec-ctl", "-d", self.dev, "--to", "0", "--give-device-power-status"], timeout=5)
        m = re.search(r"pwr-state:\s*([a-z-]+)", out)
        return m.group(1) if m else None

    def wake(self):
        system.run(["cec-ctl", "-d", self.dev, "--to", "0", "--image-view-on"], timeout=5)


def connectors():
    """{connector: plugged in?} for every HDMI port."""
    if DEV:
        return {"HDMI-A-1": True, "HDMI-A-2": True}
    out = {}
    for path in glob.glob("/sys/class/drm/card*-HDMI-A-*/status"):
        name = os.path.basename(os.path.dirname(path)).split("-", 1)[1]
        out[name] = _read(path) == "connected"
    return out


# audio on the Pi (PipeWire in the kiosk session, ALSA as a fallback) --------------------------------------

def pi_muted():
    if DEV:
        return False
    _, out, _ = kiosk.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=4)
    if "Volume:" in out:
        return "MUTED" in out
    _, out, _ = system.run(["amixer", "get", "Master"], timeout=4)
    return "[off]" in out if "[o" in out else None


def pi_set_muted(muted):
    if DEV:
        return
    rc, _, _ = kiosk.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if muted else "0"], timeout=4)
    if rc != 0:
        system.run(["amixer", "-q", "set", "Master", "mute" if muted else "unmute"], timeout=4)


# USB ----------------------------------------------------------------------------------------------------

def _protected_devices():
    """sysfs paths the system disk hangs off; never unplug these or anything above them."""
    paths = []
    for mount in ("/", "/boot/firmware", "/boot"):
        _, out, _ = system.run(["findmnt", "-no", "SOURCE", mount], timeout=3)
        dev = out.strip()
        if dev.startswith("/dev/"):
            paths.append(os.path.realpath(f"/sys/class/block/{os.path.basename(dev)}"))
    return paths


def usb_devices():
    if DEV:
        return [
            {"id": "1-1.2", "name": "USB Keyboard", "kind": "input", "protected": False},
            {"id": "1-1.3", "name": "Logitech Webcam", "kind": "other", "protected": False},
            {"id": "1-1.4", "name": "SanDisk Ultra", "kind": "storage", "protected": False},
            {"id": "2-1", "name": "Samsung SSD (system disk)", "kind": "storage", "protected": True},
        ]
    protected = _protected_devices()
    devices = []
    for path in sorted(glob.glob("/sys/bus/usb/devices/*")):
        name = os.path.basename(path)
        if ":" in name or name.startswith("usb") or not os.path.exists(f"{path}/authorized"):
            continue
        if _read(f"{path}/bDeviceClass") == "09":  # hubs carry everything behind them
            continue
        real = os.path.realpath(path)
        classes = {_read(p) for p in glob.glob(f"{path}/*:*/bInterfaceClass")}
        if "03" in classes or "e0" in classes:
            kind = "input"  # keyboards, mice, receivers, Bluetooth dongles
        elif classes & {"02", "0a"} or glob.glob(f"{path}/*:*/net"):
            kind = "network"
        elif "08" in classes:
            kind = "storage"
        else:
            kind = "other"
        devices.append({
            "id": name,
            "name": _read(f"{path}/product") or _read(f"{path}/manufacturer") or name,
            "kind": kind,
            "protected": kind == "network" or any(p.startswith(real + "/") for p in protected),
        })
    return devices


def usb_set_authorized(device_id, on):
    if DEV:
        print(f"[dev] usb {device_id} {'connect' if on else 'disconnect'}", flush=True)
        return True
    path = f"/sys/bus/usb/devices/{device_id}"
    if not on:
        # unmount sticks cleanly before they vanish
        for part in glob.glob(f"{path}/**/block/*", recursive=True) + glob.glob(f"{path}/**/block/*/*", recursive=True):
            system.run(["umount", f"/dev/{os.path.basename(part)}"], timeout=10)
    return _write(f"{path}/authorized", "1" if on else "0")


# input events: wake from standby on a key press when inputs stay connected ---------------------------------

EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux
EV_KEY, EV_REL = 1, 2


def watch_input(callback):
    fds = {}
    last_scan = 0
    while True:
        if time.time() - last_scan > 5:
            last_scan = time.time()
            for path in glob.glob("/dev/input/event*"):
                if path not in fds.values():
                    try:
                        fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
                    except OSError:
                        pass
        if not fds:
            time.sleep(5)
            continue
        ready, _, _ = select.select(list(fds), [], [], 5)
        for fd in ready:
            try:
                data = os.read(fd, EVENT.size * 64)
            except OSError:
                os.close(fd)
                fds.pop(fd, None)
                continue
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, _, value = EVENT.unpack_from(data, i)
                if (etype == EV_KEY and value == 1) or etype == EV_REL:
                    callback()
                    break


# the manager ------------------------------------------------------------------------------------------------

class Display:
    """Power state tracking for one monitor."""

    def __init__(self, connector, ddc=None, cec=None):
        self.connector = connector
        self.ddc = ddc
        self.cec = cec
        self.state, self.source, self.explicit = "unknown", None, True
        self.ddc_seen = False
        self.ddc_fails = 0
        self.dev_on = True

    @property
    def model(self):
        return self.ddc.model if self.ddc else None

    def read(self, plugged):
        """(state, source, explicit)"""
        if DEV:
            return ("on" if self.dev_on else "off"), "ddc", True
        if plugged is False:
            return "off", "hotplug", True
        if self.cec:
            p = self.cec.power()
            if p:
                return ("on" if p in ("on", "to-on") else "off"), "cec", True
        if self.ddc:
            r = self.ddc.get(VCP["power"])
            if r is not None:
                self.ddc_seen, self.ddc_fails = True, 0
                return ("on" if r[0] == 1 else "off"), "ddc", True
            self.ddc_fails += 1
            if self.ddc_seen and self.ddc_fails >= DDC_SILENT_AFTER:
                return "off", "ddc-silent", False
        if plugged is True:
            return "on", "hotplug", True
        return "unknown", None, True


class Monitor:
    """All monitors; standby follows the main one."""

    def __init__(self, cfg, lock_session, main_connector):
        self.cfg = cfg
        self.lock_session = lock_session
        self.main_connector = main_connector  # callable -> connector of the main monitor, or None
        self.displays = {}
        self.off_since = None
        self.snapshot = None
        self.test_until = 0
        self.lock = threading.RLock()
        self._restore_leftover()
        threading.Thread(target=self._run, daemon=True).start()
        if not DEV and os.path.isdir("/dev/input"):
            threading.Thread(target=watch_input, args=(self._on_input,), daemon=True).start()

    def detect(self):
        plugs = connectors()
        ddcs = DDC.detect_all()
        cecs = CEC.detect_all([c for c, on in plugs.items() if on])
        with self.lock:
            self.displays = {n: Display(n, ddcs.get(n), cecs.get(n)) for n in sorted(set(plugs) | set(ddcs))}

    def main(self):
        name = self.main_connector()
        if name in self.displays:
            return self.displays[name]
        plugs = connectors()
        plugged = [d for d in self.displays.values() if plugs.get(d.connector)]
        return plugged[0] if plugged else next(iter(self.displays.values()), None)

    # detection ----------------------------------------------------------------------------------------

    def _run(self):
        self.detect()
        last_plugs = connectors()
        while True:
            try:
                plugs = connectors()
                if plugs != last_plugs:  # a monitor was plugged in or out: look again
                    last_plugs = plugs
                    self.detect()
                for d in list(self.displays.values()):
                    d.state, d.source, d.explicit = d.read(plugs.get(d.connector))
                with self.lock:
                    self._react()
            except Exception as e:  # never let a flaky monitor kill the thread
                print(f"monitor: {e}", flush=True)
            time.sleep(POLL)

    def _main_state(self):
        if time.time() < self.test_until:
            return "off", "test", True
        d = self.main()
        return (d.state, d.source, d.explicit) if d else ("unknown", None, True)

    def _react(self):
        cfg = self.cfg.get()
        settings = cfg["screen"]
        if not cfg["setupComplete"]:
            # never unplug anything (keyboard and mouse included) before setup is done
            self.off_since = None
            if self.snapshot:
                self.leave_standby()
            return
        if time.time() < self.test_until:
            return  # Test standby is in charge
        state, _, _ = self._main_state()
        if state == "off":
            self.off_since = self.off_since or time.time()
            if settings["enabled"] and not self.snapshot and time.time() - self.off_since >= settings["delay"]:
                self.enter_standby()
        elif state == "on":
            self.off_since = None
            if self.snapshot:
                self.leave_standby()

    def _on_input(self):
        # only reachable when inputs stayed connected; wakes the main monitor and Dot
        with self.lock:
            if not self.snapshot or time.time() < self.test_until:
                return
            d = self.main()
            if d and d.cec:
                d.cec.wake()
            if d and d.ddc:
                d.ddc.set(VCP["power"], 1)
            self.leave_standby()

    # standby --------------------------------------------------------------------------------------------

    def enter_standby(self):
        s = self.cfg.get()["screen"]
        _, source, explicit = self._main_state()
        snap = {"at": time.time(), "piMuted": None, "monitorVolume": {}, "usb": [], "session": s["session"]}
        if s["mutePi"]:
            snap["piMuted"] = pi_muted()
            pi_set_muted(True)
        if s["muteMonitor"]:
            for d in self.displays.values():
                vol = d.ddc.get(VCP["volume"]) if d.ddc else None
                if vol is not None:
                    snap["monitorVolume"][d.connector] = vol[0]
                    d.ddc.set(VCP["volume"], 0)
        if s["usb"]:
            # inputs only go when the off signal is explicit, so a quiet monitor can't lock anyone out
            take_inputs = s["inputs"] and explicit
            for dev in usb_devices():
                if dev["protected"] or (dev["kind"] == "input" and not take_inputs):
                    continue
                if usb_set_authorized(dev["id"], False):
                    snap["usb"].append(dev["id"])
        self.snapshot = snap
        self._save(snap)
        if s["session"] == "lock":
            self.lock_session()
        print(f"monitor: standby ({source}), unplugged {len(snap['usb'])} USB devices", flush=True)

    def leave_standby(self):
        snap = self.snapshot
        if not snap:
            return
        self._restore(snap)
        self.snapshot = None
        print("monitor: resumed", flush=True)

    def _restore(self, snap):
        for dev_id in snap.get("usb", []):
            usb_set_authorized(dev_id, True)
        if snap.get("piMuted") is not None:
            pi_set_muted(snap["piMuted"])
        for conn, vol in (snap.get("monitorVolume") or {}).items():
            d = self.displays.get(conn)
            if d and d.ddc:
                d.ddc.set(VCP["volume"], vol)
        try:
            os.remove(SNAPSHOT_FILE)
        except OSError:
            pass

    def _save(self, snap):
        try:
            os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)
            with open(SNAPSHOT_FILE, "w") as f:
                json.dump(snap, f)
        except OSError:
            pass

    def _restore_leftover(self):
        """dotd restarted mid-standby: put everything back once the monitors are known."""
        try:
            with open(SNAPSHOT_FILE) as f:
                snap = json.load(f)
        except (OSError, ValueError):
            return

        def later():
            for _ in range(60):
                if self.displays:
                    break
                time.sleep(1)
            self._restore(snap)

        threading.Thread(target=later, daemon=True).start()

    # controls -------------------------------------------------------------------------------------------

    def controls(self, connector):
        d = self.displays.get(connector)
        out = {}
        if d and d.ddc:
            for name in ("brightness", "contrast", "volume"):
                r = d.ddc.get(VCP[name])
                if r is not None and r[1]:
                    out[name] = {"value": r[0], "max": r[1]}
        return out

    def set_control(self, connector, name, value):
        d = self.displays.get(connector)
        if not d or not d.ddc or name not in ("brightness", "contrast", "volume"):
            return False
        return d.ddc.set(VCP[name], max(0, int(value)))

    def test(self, seconds=10):
        """Standby for `seconds`, right now. It doesn't wait for the monitor check loop, which can take
        longer than the whole test when a monitor answers DDC/CI slowly."""
        def run():
            try:
                with self.lock:
                    self.test_until = time.time() + seconds
                    if not self.snapshot:
                        self.enter_standby()
                time.sleep(seconds)
            except Exception:
                print(f"monitor: test standby failed:\n{traceback.format_exc()}", flush=True)
            finally:
                with self.lock:
                    self.test_until = 0
                    if self.snapshot and self._main_state()[0] != "off":
                        self.leave_standby()

        threading.Thread(target=run, daemon=True).start()

    def set_dev_power(self, on):
        d = self.main()
        if d:
            d.dev_on = on

    def status(self):
        main = self.main()
        state, source, _ = self._main_state()
        return {
            "state": state,
            "source": source,
            "main": main.connector if main else None,
            "displays": [{"connector": d.connector, "model": d.model, "ddc": d.ddc is not None,
                          "cec": d.cec is not None, "state": d.state, "source": d.source}
                         for d in self.displays.values()],
            "standby": self.snapshot is not None,
            "testing": time.time() < self.test_until,
        }
