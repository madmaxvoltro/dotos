"""Sound: outputs, one volume, and Windows' volume over RDP.

Windows sends its volume to the client over RDP's audio channel (the RDPEA
"Volume PDU"); FreeRDP applies it to its own PipeWire stream. We watch that
stream and move the real volume instead (the monitor's speakers over DDC/CI,
or the Pi's own output), then put the stream back to 100% so the sound isn't
turned down twice. Windows' slider, its volume keys and Dot's slider all end up
moving the same thing.
"""

import json
import os
import re
import subprocess
import threading
import time

from . import system

try:
    import pwd
except ImportError:
    pwd = None

DEV = system.DEV
KIOSK_USER = os.environ.get("DOT_USER", "dot")
FREERDP_BINARIES = ("sdl-freerdp3", "sdl-freerdp", "xfreerdp3", "wlfreerdp3", "xfreerdp", "wlfreerdp")


def _env_prefix():
    if pwd is None:
        return []
    try:
        uid = pwd.getpwnam(KIOSK_USER).pw_uid
    except KeyError:
        return []
    return ["runuser", "-u", KIOSK_USER, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{uid}"]


def pactl(*args, parse=False):
    code, out, _ = system.run(_env_prefix() + ["pactl", *(["-f", "json"] if parse else []), *args], timeout=5)
    if code != 0:
        return None
    if not parse:
        return out.strip()
    try:
        return json.loads(out or "null")
    except ValueError:
        return None


def _percent(volume):
    """Average of a pactl JSON volume map, in %."""
    vals = [int(str(ch.get("value_percent", "0")).rstrip("%")) for ch in (volume or {}).values()]
    return round(sum(vals) / len(vals)) if vals else 100


def hdmi_connector(sink):
    """HDMI sink -> the monitor it plays on (vc4hdmi0 -> HDMI-A-1), else None."""
    text = " ".join([sink.get("name", ""), json.dumps(sink.get("properties", {}))]).lower()
    m = re.search(r"vc4-?hdmi-?(\d)", text) or re.search(r"hdmi[^0-9]{0,12}(\d)", text)
    return f"HDMI-A-{int(m.group(1)) + 1}" if m and "hdmi" in text else None


class Audio:
    def __init__(self, cfg, monitor):
        self.cfg = cfg
        self.monitor = monitor  # for the monitors' speaker volume over DDC/CI
        self.lock = threading.RLock()
        self.windows_volume = None  # last level Windows asked for
        self._reset_stream = set()  # stream ids we just put back to 100%
        self.dev = {"sinks": [
            {"name": "hdmi0", "label": "DELL U2720Q speakers", "connector": "HDMI-A-1"},
            {"name": "hdmi1", "label": "VG249Q speakers", "connector": "HDMI-A-2"},
            {"name": "jack", "label": "Headphones", "connector": None},
        ], "default": "hdmi0", "volume": 40, "muted": False, "stream": None}
        self.cached = {"volume": None, "muted": False, "using": "pi", "connector": None, "sinks": [], "sink": None, "windows": None}
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._refresh_loop, daemon=True).start()

    # outputs and the Pi's own volume ------------------------------------------------------------------

    def sinks(self):
        if DEV:
            return [dict(s) for s in self.dev["sinks"]]
        out = []
        for s in pactl("list", "sinks", parse=True) or []:
            conn = hdmi_connector(s)
            label = s.get("description") or s.get("name")
            if conn:
                model = next((d["model"] for d in self.monitor.status()["displays"] if d["connector"] == conn and d["model"]), None)
                label = f"{model} speakers" if model else f"Monitor on {conn}"
            out.append({"name": s["name"], "label": label, "connector": conn,
                        "volume": _percent(s.get("volume")), "muted": bool(s.get("mute"))})
        return out

    def default_sink(self):
        if DEV:
            return self.dev["default"]
        return pactl("get-default-sink")

    def set_default_sink(self, name):
        if DEV:
            self.dev["default"] = name
            return
        pactl("set-default-sink", name)
        # move what's already playing (a running session) to the new output
        for si in pactl("list", "sink-inputs", parse=True) or []:
            pactl("move-sink-input", str(si["index"]), name)

    def pi_volume(self):
        if DEV:
            return self.dev["volume"], self.dev["muted"]
        sink = next((s for s in self.sinks() if s["name"] == self.default_sink()), None)
        return (sink["volume"], sink["muted"]) if sink else (100, False)

    def set_pi_volume(self, pct=None, muted=None):
        if DEV:
            if pct is not None:
                self.dev["volume"] = pct
            if muted is not None:
                self.dev["muted"] = muted
            return
        if pct is not None:
            pactl("set-sink-volume", "@DEFAULT_SINK@", f"{int(pct)}%")
        if muted is not None:
            pactl("set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0")

    # one volume ------------------------------------------------------------------------------------------

    def target(self):
        """Where 'the volume' lives: the monitor's speakers or the Pi's output."""
        want = self.cfg.get()["audio"]["target"]
        sink = next((s for s in self.sinks() if s["name"] == self.default_sink()), None)
        conn = sink["connector"] if sink else None
        has_ddc = bool(conn) and "volume" in self.monitor.controls(conn)
        if want == "monitor" or (want == "auto" and has_ddc):
            return ("monitor", conn) if has_ddc else ("pi", None)
        return "pi", None

    def volume(self):
        kind, conn = self.target()
        _, muted = self.pi_volume()
        if kind == "monitor":
            c = self.monitor.controls(conn).get("volume")
            if c:
                return {"volume": round(c["value"] * 100 / c["max"]), "muted": muted, "using": kind, "connector": conn}
        vol, muted = self.pi_volume()
        return {"volume": vol, "muted": muted, "using": "pi", "connector": None}

    def set_volume(self, pct=None, muted=None):
        """Dot's own slider and volume keys (and Windows, via _follow) all land here."""
        kind, conn = self.target()
        if pct is not None:
            pct = max(0, min(100, int(pct)))
            if kind == "monitor":
                c = self.monitor.controls(conn).get("volume")
                self.monitor.set_control(conn, "volume", round(pct * c["max"] / 100) if c else pct)
                self.set_pi_volume(pct=100)  # the monitor does the work; the Pi stays at full level
            else:
                self.set_pi_volume(pct=pct)
        if muted is not None:
            self.set_pi_volume(muted=muted)

    # following Windows -----------------------------------------------------------------------------------

    def _freerdp_streams(self):
        if DEV:
            s = self.dev["stream"]
            return [s] if s else []
        out = []
        for si in pactl("list", "sink-inputs", parse=True) or []:
            props = si.get("properties", {})
            binary = props.get("application.process.binary", "")
            if binary in FREERDP_BINARIES or "freerdp" in props.get("application.name", "").lower():
                out.append({"id": si["index"], "volume": _percent(si.get("volume")), "muted": bool(si.get("mute"))})
        return out

    def _follow(self):
        if not self.cfg.get()["audio"]["followWindows"]:
            return
        for s in self._freerdp_streams():
            if s["volume"] == 100 and not s["muted"]:
                self._reset_stream.discard(s["id"])
                continue
            with self.lock:
                self.windows_volume = s["volume"]
                self.set_volume(pct=s["volume"], muted=s["muted"] or s["volume"] == 0)
                # put FreeRDP's own stream back to full level so it isn't turned down twice
                if DEV:
                    self.dev["stream"] = {**s, "volume": 100, "muted": False}
                else:
                    pactl("set-sink-input-volume", str(s["id"]), "100%")
                    pactl("set-sink-input-mute", str(s["id"]), "0")
                self._reset_stream.add(s["id"])
            self.refresh()

    def _watch(self):
        """React to stream changes as they happen (pactl subscribe), with a slow poll as backup."""
        if DEV:
            while True:
                self._follow()
                time.sleep(0.5)
        while True:
            try:
                proc = subprocess.Popen(_env_prefix() + ["pactl", "subscribe"], stdout=subprocess.PIPE, text=True)
                last = 0.0
                for line in proc.stdout:
                    if "sink-input" in line and time.time() - last > 0.1:
                        last = time.time()
                        self._follow()
            except Exception as e:  # PipeWire not up yet, or it restarted
                print(f"audio: {e}", flush=True)
            time.sleep(3)
            self._follow()

    def refresh(self):
        """Re-read everything (pactl + DDC are slow, so /api/state serves this cached copy)."""
        try:
            self.cached = {**self.volume(), "sinks": [{"name": s["name"], "label": s["label"]} for s in self.sinks()],
                           "sink": self.default_sink(), "windows": self.windows_volume}
        except Exception as e:
            print(f"audio: {e}", flush=True)
        return self.cached

    def _refresh_loop(self):
        while True:
            self.refresh()
            time.sleep(10)

    def status(self):
        return self.cached
