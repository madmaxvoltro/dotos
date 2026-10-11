"""Monitors as the kiosk compositor (sway) sees them: arrangement, modes, rotation,
which one is main, and which window goes where.

The arrangement set here is the one FreeRDP sends to Windows with /multimon, so
the mouse crosses screen edges in the same place on both sides.
"""

import glob
import json
import math
import os
import threading
import time

from . import kiosk, system

DEV = system.DEV
OUTPUTS_CONF = os.environ.get("DOT_SWAY_OUTPUTS", "/var/lib/dot/sway-outputs.conf")
UI_TITLE = "Dot"
CLOCK_TITLE = "Dot Clock"
TRANSFORMS = ("normal", "90", "180", "270")

DEV_OUTPUTS = [
    {"name": "HDMI-A-1", "make": "Dell Inc.", "model": "DELL U2720Q", "serial": "DEV001", "active": True,
     "rect": {"x": 0, "y": 0, "width": 3840, "height": 2160}, "transform": "normal",
     "current_mode": {"width": 3840, "height": 2160, "refresh": 60000},
     "modes": [{"width": 3840, "height": 2160, "refresh": 60000}, {"width": 2560, "height": 1440, "refresh": 60000}, {"width": 1920, "height": 1080, "refresh": 60000}]},
    {"name": "HDMI-A-2", "make": "ASUSTek COMPUTER INC", "model": "VG249Q", "serial": "DEV002", "active": True,
     "rect": {"x": 3840, "y": 0, "width": 1920, "height": 1080}, "transform": "normal",
     "current_mode": {"width": 1920, "height": 1080, "refresh": 144001},
     "modes": [{"width": 1920, "height": 1080, "refresh": 144001}, {"width": 1920, "height": 1080, "refresh": 120000}, {"width": 1920, "height": 1080, "refresh": 60000}]},
]


def swaymsg(*args, raw=False):
    """Run swaymsg as the kiosk user. Returns parsed JSON (or text with raw=True), None on failure."""
    if DEV or "SWAYSOCK" not in kiosk.env():
        return None
    code, out, _ = kiosk.run(["swaymsg", *args], timeout=8)
    if code != 0:
        return None
    if raw:
        return out
    try:
        return json.loads(out)
    except ValueError:
        return None


DEV_PHYSICAL = {"HDMI-A-1": (597, 336), "HDMI-A-2": (527, 296)}  # 27" 4K, 24" 1080p
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


def physical_size(connector):
    """(width_mm, height_mm) from the monitor's EDID, or None. Resolution says nothing about size."""
    if DEV:
        return DEV_PHYSICAL.get(connector)
    for path in glob.glob(f"/sys/class/drm/card*-{connector}/edid"):
        try:
            with open(path, "rb") as f:
                edid = f.read()
        except OSError:
            continue
        if len(edid) < 128:
            continue
        # the first detailed timing descriptor has millimetres; bytes 21-22 only centimetres
        d = edid[54:72]
        w = d[12] | ((d[14] & 0xF0) << 4)
        h = d[13] | ((d[14] & 0x0F) << 8)
        if w > 50 and h > 50:
            return w, h
        if edid[21] and edid[22]:
            return edid[21] * 10, edid[22] * 10
    return None


def auto_scale(px_w, px_h, mm):
    """Scale that makes this monitor's layout size match its real size (96 dpi = 100%)."""
    if not mm:
        return 1.0
    ppi = math.hypot(px_w, px_h) / (math.hypot(*mm) / 25.4)
    return min(SCALES, key=lambda s: abs(s - ppi / 96))


def identifier(o):
    """sway's stable name for a monitor: make model serial, so cables can be swapped."""
    return f"{o.get('make', '')} {o.get('model', '')} {o.get('serial', '')}".strip()


def mode_str(m):
    return f"{m['width']}x{m['height']}@{m['refresh'] / 1000:.3f}Hz"


class Displays:
    def __init__(self, cfg):
        self.cfg = cfg
        self.outputs = []
        self.lock = threading.RLock()
        self._last_shape = None
        self._dev_outputs = [dict(o) for o in DEV_OUTPUTS]
        threading.Thread(target=self._watch, daemon=True).start()

    # reading ------------------------------------------------------------------------------------

    def read(self):
        if DEV:
            raw = [dict(o) for o in self._dev_outputs]
        else:
            raw = swaymsg("-t", "get_outputs", "-r") or []
        outs = []
        for o in raw:
            if not o.get("name"):
                continue
            cm = o.get("current_mode") or {}
            modes = sorted({mode_str(m): m for m in o.get("modes", [])}.values(), key=lambda m: (-m["width"] * m["height"], -m["refresh"]))
            mm = physical_size(o["name"])
            px_w, px_h = (cm.get("width") or o["rect"]["width"]), (cm.get("height") or o["rect"]["height"])
            if o.get("transform", "normal") in ("90", "270"):
                px_w, px_h = px_h, px_w
                mm = (mm[1], mm[0]) if mm else None
            outs.append({
                "name": o["name"],
                "pxWidth": px_w, "pxHeight": px_h,
                "mm": list(mm) if mm else None,
                "inches": round(math.hypot(*mm) / 25.4, 1) if mm else None,
                "scale": o.get("scale", 1.0),
                "autoScale": auto_scale(px_w, px_h, mm),
                "id": identifier(o),
                "label": (o.get("model") or o["name"]).strip(),
                "active": o.get("active", False),
                "x": o["rect"]["x"], "y": o["rect"]["y"],
                "width": o["rect"]["width"], "height": o["rect"]["height"],
                "transform": o.get("transform", "normal"),
                "mode": mode_str(cm) if cm else None,
                "modes": [{"value": mode_str(m), "label": f"{m['width']} × {m['height']} · {round(m['refresh'] / 1000)} Hz"} for m in modes],
            })
        return outs

    def main(self):
        """The main monitor: login and the Windows taskbar live here."""
        want = self.cfg.get()["screen"].get("main")
        active = [o for o in self.outputs if o["active"]]
        return next((o for o in active if o["id"] == want), active[0] if active else None)

    def status(self):
        main = self.main()
        outs = [{**o, "main": bool(main) and o["name"] == main["name"]} for o in self.outputs]
        return {"outputs": outs, "main": main["name"] if main else None}

    # applying -------------------------------------------------------------------------------------

    def _auto_layout(self, outs):
        """Saved positions where known; new monitors go to the right of the others, top-aligned."""
        layout = self.cfg.get()["screen"].get("layout", {})
        placed, right = {}, 0
        for o in outs:
            saved = layout.get(o["id"])
            if saved:
                placed[o["id"]] = saved
                right = max(right, saved["x"] + o["width"])
        for o in outs:
            if o["id"] not in placed:
                placed[o["id"]] = {"x": right, "y": 0, "mode": None, "transform": "normal", "scale": "auto"}
                right += o["width"]
        return placed

    def _close_gaps(self, outs, placed):
        """A monitor's size changes with its mode or scale; slide monitors left so none floats apart."""
        def size(o):
            p = placed[o["id"]]
            mode = p.get("mode") or o["mode"] or ""
            w, h = (int(v) for v in mode.split("@")[0].split("x")) if "x" in mode else (o["pxWidth"], o["pxHeight"])
            if p.get("transform", "normal") in ("90", "270"):
                w, h = h, w
            scale = auto_scale(w, h, o["mm"]) if p.get("scale", "auto") == "auto" else float(p["scale"])
            return w / scale, h / scale

        sizes = {o["id"]: size(o) for o in outs}
        for o in sorted(outs, key=lambda o: placed[o["id"]]["x"]):
            p = placed[o["id"]]
            w, h = sizes[o["id"]]
            left = [q for q in outs if q is not o and placed[q["id"]]["x"] < p["x"]
                    and placed[q["id"]]["y"] < p["y"] + h and p["y"] < placed[q["id"]]["y"] + sizes[q["id"]][1]]
            if left:
                edge = max(placed[q["id"]]["x"] + sizes[q["id"]][0] for q in left)
                if p["x"] > edge:
                    p["x"] = round(edge)

    def apply(self):
        """Push arrangement and modes to sway, persist them, and put each window on its monitor."""
        with self.lock:
            self.outputs = self.read()
            outs = [o for o in self.outputs if o["active"]]
            placed = self._auto_layout(outs)
            self._close_gaps(outs, placed)
            lines = []
            for o in outs:
                p = placed[o["id"]]
                parts = [f'output "{o["id"]}" enable']
                if p.get("mode"):
                    parts.append(f"mode {p['mode']}")
                scale = o["autoScale"] if p.get("scale", "auto") == "auto" else float(p["scale"])
                parts.append(f"pos {int(p['x'])} {int(p['y'])} transform {p.get('transform', 'normal')} scale {scale:g}")
                lines.append(" ".join(parts))
            for line in lines:
                swaymsg(line, raw=True)
            if DEV:
                self._dev_apply(placed)
            try:
                os.makedirs(os.path.dirname(OUTPUTS_CONF), exist_ok=True)
                with open(OUTPUTS_CONF, "w") as f:
                    f.write("# written by dotd: Settings > Screen\n" + "\n".join(lines) + "\n")
            except OSError:
                pass
            self.outputs = self.read()
            self.place_windows()

    def _dev_apply(self, placed):
        for o in self._dev_outputs:
            p = placed.get(identifier(o))
            if not p:
                continue
            mode = next((m for m in o["modes"] if mode_str(m) == p.get("mode")), o["current_mode"])
            w, h = mode["width"], mode["height"]
            mm = physical_size(o["name"])
            if p.get("transform") in ("90", "270"):
                w, h = h, w
                mm = (mm[1], mm[0]) if mm else None
            scale = auto_scale(w, h, mm) if p.get("scale", "auto") == "auto" else float(p["scale"])
            o.update(current_mode=mode, transform=p.get("transform", "normal"), scale=scale,
                     rect={"x": int(p["x"]), "y": int(p["y"]), "width": round(w / scale), "height": round(h / scale)})

    def place_windows(self):
        """Login UI on the main monitor, the idle clock on every other one."""
        main = self.main()
        if not main or DEV:
            return
        others = [o for o in self.outputs if o["active"] and o["name"] != main["name"]]
        swaymsg(f'[title="^{UI_TITLE}$"] fullscreen disable, move container to output {main["name"]}, fullscreen enable', raw=True)
        swaymsg(f'[title="^{CLOCK_TITLE}$"] kill', raw=True)
        for o in others:
            swaymsg(f"focus output {o['name']}", raw=True)
            swaymsg("exec /usr/lib/dot/system/clock.sh", raw=True)
            time.sleep(2.5)
            swaymsg(f'[title="^{CLOCK_TITLE}$"] fullscreen disable, move container to output {o["name"]}, fullscreen enable', raw=True)
        swaymsg(f'[title="^{UI_TITLE}$"] focus', raw=True)

    def session_started(self, mode):
        """Mirror mode: copy the main monitor onto the others while signed in."""
        main = self.main()
        if mode != "mirror" or not main or DEV:
            return
        for o in self.outputs:
            if o["active"] and o["name"] != main["name"]:
                swaymsg(f"exec wl-mirror --fullscreen-output {o['name']} {main['name']}", raw=True)

    def session_ended(self):
        swaymsg('[app_id="at.yrlf.wl_mirror"] kill', raw=True)

    def others(self):
        main = self.main()
        return [o for o in self.outputs if o["active"] and (not main or o["name"] != main["name"])]

    def focus(self, name):
        swaymsg(f"focus output {name}", raw=True)

    def fastest_mode(self, name):
        """For game mode: the main monitor's highest refresh at its current resolution (e.g. 1080p144)."""
        o = next((x for x in self.outputs if x["name"] == name), None)
        if not o or not o["mode"]:
            return None
        res = o["mode"].split("@")[0]
        same = [m["value"] for m in o["modes"] if m["value"].startswith(res + "@")]
        return max(same, key=lambda v: float(v.split("@")[1].rstrip("Hz")), default=o["mode"])

    def set_mode(self, name, mode):
        o = next((x for x in self.outputs if x["name"] == name), None)
        if o and mode:
            swaymsg(f'output "{o["id"]}" mode {mode}', raw=True)
            if DEV:
                for d in self._dev_outputs:
                    if d["name"] == name:
                        r = mode.split("@")[1].rstrip("Hz")
                        d["current_mode"] = {**d["current_mode"], "refresh": round(float(r) * 1000)}
            self.outputs = self.read()

    def exec_side(self, kind, url):
        """A browser or the game panel, full screen on the focused monitor (see system/side.sh)."""
        if DEV:
            print(f"[dev] side {kind} {url}", flush=True)
            return
        swaymsg(f"exec /usr/lib/dot/system/side.sh {kind} {url}", raw=True)

    def close_side(self, kind):
        if not DEV:
            kiosk.run(["pkill", "-f", f"chromium-{kind}"], timeout=5)

    def power(self, name, on):
        swaymsg(f"output {name} power {'on' if on else 'off'}", raw=True)

    def focus_main(self):
        main = self.main()
        if main:
            swaymsg(f"focus output {main['name']}", raw=True)

    def save_layout(self, payload):
        """payload: {main: id, layout: {id: {x, y, mode, transform}}} from the arrangement editor."""
        known = {o["id"] for o in self.outputs}
        layout = {}
        for oid, p in (payload.get("layout") or {}).items():
            if oid not in known:
                continue
            layout[oid] = {
                "x": int(p.get("x", 0)), "y": int(p.get("y", 0)),
                "mode": p.get("mode") or None,
                "transform": p.get("transform") if p.get("transform") in TRANSFORMS else "normal",
                "scale": p.get("scale") if p.get("scale") == "auto" or (isinstance(p.get("scale"), (int, float)) and 0.5 <= p["scale"] <= 4) else "auto",
            }
        if layout:  # top-left monitor at 0,0
            mx = min(p["x"] for p in layout.values())
            my = min(p["y"] for p in layout.values())
            for p in layout.values():
                p["x"] -= mx
                p["y"] -= my

        def fn(d):
            d["screen"]["layout"].update(layout)
            if payload.get("main") in known:
                d["screen"]["main"] = payload["main"]

        self.cfg.mutate(fn)
        self.apply()

    # watching ---------------------------------------------------------------------------------------

    def _watch(self):
        while True:
            try:
                outs = self.read()
                shape = [(o["id"], o["active"]) for o in outs]
                if shape != self._last_shape:
                    self._last_shape = shape
                    self.apply()  # a monitor came or went
                else:
                    self.outputs = outs
            except Exception as e:
                print(f"displays: {e}", flush=True)
            time.sleep(4)
