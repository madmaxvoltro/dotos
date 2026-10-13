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

from . import edid, kiosk, system

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
    return edid.physical_mm(edid.read(connector))


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


# Offered when a monitor doesn't tell the Pi its resolutions (no EDID: monitor off at boot, adapters,
# KVM switches). Linux then only lists safe modes up to 1024x768; sway can drive these anyway.
CUSTOM_MODES = [(3840, 2160, 60), (3840, 2160, 30), (2560, 1440, 144), (2560, 1440, 60), (2560, 1080, 60),
                (1920, 1200, 60), (1920, 1080, 144), (1920, 1080, 120), (1920, 1080, 60), (1680, 1050, 60),
                (1600, 900, 60), (1440, 900, 60), (1366, 768, 60), (1280, 1024, 60), (1280, 720, 60)]


def edid_present(connector):
    if DEV:
        return connector != "HDMI-A-3"
    for path in glob.glob(f"/sys/class/drm/card*-{connector}/edid"):
        try:
            return os.path.getsize(path) >= 128
        except OSError:
            pass
    return False


def plain_mode(mode):
    """'custom:1920x1080@60.000Hz' -> '1920x1080@60.000Hz'."""
    return (mode or "").split(":", 1)[-1]


class Displays:
    def __init__(self, cfg):
        self.cfg = cfg
        self.outputs = []
        self.lock = threading.RLock()
        self._last_shape = None
        self._dev_outputs = [dict(o) for o in DEV_OUTPUTS]
        self.trial = None  # {"until", "previous": screen config}: a custom mode waiting to be kept
        self._dev_detected = {}  # dev mode: a monitor "without EDID" and what it would report over I2C
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
            choices = [{"value": mode_str(m), "label": f"{m['width']} × {m['height']} · {round(m['refresh'] / 1000)} Hz"} for m in modes]
            has_edid = edid_present(o["name"])
            biggest = max((m["width"] * m["height"] for m in modes), default=0)
            needs_custom = not has_edid or biggest <= 1024 * 768
            detected = None
            if needs_custom:
                known = {(m["width"], m["height"], round(m["refresh"] / 1000)) for m in modes}
                # the monitor's own preferred mode, read over the cable when the kernel has no EDID
                pref = edid.preferred_mode(edid.from_monitor(o["name"])) if not DEV else self._dev_detected.get(o["name"])
                if pref and pref not in known:
                    detected = f"custom:{pref[0]}x{pref[1]}@{pref[2]:.3f}Hz"
                    choices.append({"value": detected, "label": f"{pref[0]} × {pref[1]} · {pref[2]} Hz (detected)"})
                choices += [{"value": f"custom:{w}x{h}@{r:.3f}Hz", "label": f"{w} × {h} · {r} Hz (custom)"}
                            for w, h, r in CUSTOM_MODES if (w, h, r) not in known and (w, h, r) != pref]
            current = mode_str(cm) if cm else None
            if current and current not in [c["value"] for c in choices]:
                # a custom mode is running: match it by resolution (sway's timings round the refresh rate)
                same = [c["value"] for c in choices if plain_mode(c["value"]).split("@")[0] == current.split("@")[0]]
                current = same[0] if same else current
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
                "mode": current,
                "modes": choices,
                "edid": has_edid,
                "customModes": needs_custom,
                "detectedMode": detected,
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
        trial = {"secondsLeft": max(0, round(self.trial["until"] - time.time()))} if self.trial else None
        return {"outputs": outs, "main": main["name"] if main else None, "trial": trial}

    # applying -------------------------------------------------------------------------------------

    def _auto_layout(self, outs):
        """Saved positions where known; new monitors go to the right of the others, top-aligned."""
        layout = self.cfg.get()["screen"].get("layout", {})
        placed, right = {}, 0
        for o in outs:
            saved = layout.get(o["id"])
            if saved and not saved.get("mode") and o.get("detectedMode"):
                saved = {**saved, "mode": o["detectedMode"]}
            if saved:
                placed[o["id"]] = saved
                right = max(right, saved["x"] + o["width"])
        for o in outs:
            if o["id"] not in placed:
                # no EDID from the kernel: start at the mode the monitor itself reported, if we got one
                placed[o["id"]] = {"x": right, "y": 0, "mode": o.get("detectedMode"), "transform": "normal", "scale": "auto"}
                right += o["width"]
        return placed

    def _close_gaps(self, outs, placed):
        """A monitor's size changes with its mode or scale; slide monitors left so none floats apart."""
        def size(o):
            p = placed[o["id"]]
            mode = p.get("mode") or o["mode"] or ""
            mode = plain_mode(mode)
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
                    if p["mode"].startswith("custom:"):
                        parts.append(f"mode --custom {plain_mode(p['mode'])}")
                    else:
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
            mode = next((m for m in o["modes"] if mode_str(m) == plain_mode(p.get("mode"))), o["current_mode"])
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

    def session_windows(self):
        """con ids of FreeRDP's windows (title "Dot Session", or an app id / class naming FreeRDP)."""
        tree = swaymsg("-t", "get_tree", "-r") or {}
        found = []

        def walk(node):
            name = (node.get("name") or "").lower()
            app = (node.get("app_id") or (node.get("window_properties") or {}).get("class") or "").lower()
            if node.get("pid") and ("dot session" in name or "freerdp" in app or "freerdp" in name):
                found.append(node["id"])
            for child in node.get("nodes", []) + node.get("floating_nodes", []):
                walk(child)

        walk(tree)
        return found

    def raise_session(self, wait=20):
        """Put the session in front: Dot's own screen is a full-screen window too, and sway keeps a
        full-screen window on top, so FreeRDP could otherwise open behind it."""
        if DEV:
            return True
        end = time.time() + wait
        while time.time() < end:
            ids = self.session_windows()
            if ids:
                for cid in ids:
                    swaymsg(f"[con_id={cid}] fullscreen enable, focus", raw=True)
                print(f"displays: session window{'s' if len(ids) > 1 else ''} brought to the front", flush=True)
                return True
            time.sleep(0.5)
        print("displays: no session window appeared", flush=True)
        return False

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
        if o["mode"].startswith("custom:"):
            return None  # a monitor without EDID: don't guess at faster modes for game mode
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

        previous = self.cfg.get()["screen"]
        # a custom mode the monitor never advertised might not show a picture: try it, and go back
        # by itself unless someone presses Keep (dotd does this, so it works on a black screen too)
        risky = any((p.get("mode") or "").startswith("custom:") and p.get("mode") != (previous["layout"].get(oid) or {}).get("mode")
                    for oid, p in layout.items())
        self.cfg.mutate(fn)
        self.apply()
        if risky:
            self.trial = {"until": time.time() + 15, "previous": {"layout": previous["layout"], "main": previous["main"]}}
            timer = threading.Timer(15.5, self._end_trial, args=(self.trial,))
            timer.daemon = True
            timer.start()

    def keep(self):
        self.trial = None

    def _end_trial(self, trial):
        if self.trial is not trial:
            return  # kept, or replaced by a newer try
        self.trial = None

        def fn(d):
            d["screen"]["layout"] = trial["previous"]["layout"]
            d["screen"]["main"] = trial["previous"]["main"]

        self.cfg.mutate(fn)
        self.apply()
        print("displays: custom mode not kept, switched back", flush=True)

    # watching ---------------------------------------------------------------------------------------

    def _reprobe_missing(self):
        """Monitors that were off or slow at boot: ask the kernel to read their EDID again."""
        for path in glob.glob("/sys/class/drm/card*-HDMI-A-*/status"):
            connector = os.path.basename(os.path.dirname(path)).split("-", 1)[1]
            try:
                connected = open(path).read().strip() == "connected"
            except OSError:
                continue
            if connected and not edid_present(connector):
                edid.reprobe(connector)

    def _watch(self):
        if not DEV:
            self._reprobe_missing()
            time.sleep(1)
        while True:
            try:
                outs = self.read()
                shape = [(o["id"], o["active"]) for o in outs]
                if shape != self._last_shape:
                    if not DEV and self._last_shape is not None:
                        self._reprobe_missing()
                    self._last_shape = shape
                    self.apply()  # a monitor came or went
                else:
                    self.outputs = outs
            except Exception as e:
                print(f"displays: {e}", flush=True)
            time.sleep(4)
