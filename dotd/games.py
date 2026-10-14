"""Game mode: Moonlight on the main monitor, streaming a game from a PC running
Sunshine or Apollo, while the second monitor shows Windows, a browser, a game
panel or the clock.

Three setups, chosen per server (Settings > Games):
  same    the RDP server is the gaming PC. Game.exe moves the Windows session to
          the GPU (tscon), RDP closes, and comes back after the game.
  other   a separate gaming PC. RDP moves to the second monitor and keeps going.
  server  Windows Server: games in the console session, work in RDP, side by side.

Windows talks to Dot through a folder FreeRDP shares into the session as
\\\\tsclient\\dot (see INBOX): Game.exe drops a small JSON command there and reads
status.json back. No network port, no plugin, and it only works from inside the
signed-in session.
"""

import csv
import io
import json
import os
import random
import shutil
import socket
import subprocess
import threading
import time
import uuid

from . import kiosk, system

DEV = system.DEV
INBOX = os.environ.get("DOT_INBOX", "/run/dot/inbox")
MOONLIGHT_BINARIES = ("moonlight-qt", "moonlight")
# Dot Play (github.com/madmaxvoltro/dot-play): Moonlight with Dot's own launcher, installed by install.sh.
# Its command line is Moonlight's (pair, list, stream, quit) and it shares Moonlight's pairing.
DOT_PLAY = "/opt/dot/play/bin/dot-play"
SUNSHINE_PORT = 47989  # Sunshine / Apollo HTTP port, used to see whether the PC is up
FLOWS = ("off", "same", "other", "server")
MONITOR2 = ("rdp", "browser", "panel", "clock", "off")


def has_dot_play():
    return DEV or os.access(DOT_PLAY, os.X_OK)


def moonlight_bin():
    if DEV:
        return "moonlight-qt"
    if has_dot_play():
        return DOT_PLAY
    return next((shutil.which(b) for b in MOONLIGHT_BINARIES if shutil.which(b)), None)


# Wake-on-LAN -----------------------------------------------------------------------------------------

def learn_mac(ip):
    """The PC's MAC address from the ARP table, once it has been reachable."""
    try:
        with open("/proc/net/arp") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[0] == ip and parts[3] != "00:00:00:00:00:00":
                    return parts[3]
    except OSError:
        pass
    return None


def wake(mac):
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + raw * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):
            s.sendto(packet, ("255.255.255.255", port))


def resolve_ip(address):
    try:
        return socket.gethostbyname(address)
    except OSError:
        return None


# Moonlight -------------------------------------------------------------------------------------------

class Moonlight:
    """moonlight-qt's command line, run as the kiosk user (it keeps its pairing in ~dot/.config)."""

    def __init__(self):
        self.dev_paired = set()

    def available(self):
        return moonlight_bin() is not None

    def pair(self, address, pin, timeout=180):
        if DEV:
            time.sleep(3)
            self.dev_paired.add(address)
            return True
        code, out, err = kiosk.run([moonlight_bin(), "pair", address, "--pin", pin], timeout=timeout)
        return code == 0

    def apps(self, address):
        """App names on the PC, or None when it isn't paired or reachable."""
        if DEV:
            return ["Desktop", "Steam Big Picture", "Cyberpunk 2077"] if address in self.dev_paired else None
        code, out, _ = kiosk.run([moonlight_bin(), "list", address, "--csv"], timeout=20)
        if code != 0:
            return None
        rows = list(csv.reader(io.StringIO(out)))
        return [r[0].strip() for r in rows[1:] if r and r[0].strip()]

    def launcher(self, game=None):
        """Dot Play's launcher on the main monitor; with a game name (Game.exe "Elden Ring"), straight into it.
        Quitting Dot Play (B on Home, or Back to work in the game menu) ends game mode."""
        env = kiosk.env()
        if game:
            env["DOT_PLAY_GAME"] = game
        if DEV:
            print(f"[dev] {DOT_PLAY}" + (f" (DOT_PLAY_GAME={game})" if game else ""), flush=True)
            return None
        return kiosk.popen([DOT_PLAY], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stream(self, address, app, width, height, fps, codec, bitrate):
        cmd = [moonlight_bin(), "stream", address, app,
               "--resolution", f"{width}x{height}", "--fps", str(fps),
               "--display-mode", "fullscreen", "--video-decoder", "hardware",
               "--capture-system-keys", "fullscreen"]
        if codec != "auto":
            cmd += ["--video-codec", codec]
        if bitrate != "auto":
            cmd += ["--bitrate", str(int(bitrate) * 1000)]
        if DEV:
            print(f"[dev] {' '.join(cmd)}", flush=True)
            return None
        return kiosk.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def quit(self, address):
        if not DEV:
            kiosk.run([moonlight_bin(), "quit", address], timeout=20)


# game mode ---------------------------------------------------------------------------------------------

class Games:
    def __init__(self, dot):
        self.dot = dot  # server.Dot: config, session control, displays
        self.cfg = dot.cfg
        self.moonlight = Moonlight()
        self.lock = threading.RLock()
        self.state = "idle"  # idle | starting | playing | stopping
        self.info = {}
        self.proc = None
        self.side = []  # what we opened on the other monitors
        self.restore_mode = None
        self.resume_rdp = False
        self.pairing = None  # {target, pin, status}
        self.helper = None  # last hello from Dot Helper on Windows
        self.apps_cache = {}
        self.last_error = None  # why the last command from Windows didn't start game mode
        self._setup_inbox()
        threading.Thread(target=self._watch_inbox, daemon=True).start()

    # targets: a server with game mode on, or a separate gaming PC ------------------------------------------

    def target(self, target_id):
        """{id, name, address, mac, kind} for a server id or a gaming PC id."""
        c = self.cfg.get()
        server = next((s for s in c["servers"] if s["id"] == target_id), None)
        if server:
            g = server.get("games") or {}
            if g.get("mode") == "other":
                return self.target(g.get("host"))
            return {"id": server["id"], "name": server["name"], "address": server["host"],
                    "mac": g.get("mac"), "kind": "server"}
        host = next((h for h in c["games"]["hosts"] if h["id"] == target_id), None)
        if host:
            return {**host, "kind": "host"}
        return None

    def flow(self, server_id):
        server = self.dot.cfg.server(server_id) if server_id else None
        return ((server or {}).get("games") or {}).get("mode", "off")

    # hosts -----------------------------------------------------------------------------------------------

    def upsert_host(self, payload):
        name = (payload.get("name") or "").strip()
        address = (payload.get("address") or "").strip()
        if not address:
            raise ValueError("Address is required")

        def fn(d):
            hosts = d["games"]["hosts"]
            h = next((x for x in hosts if x["id"] == payload.get("id")), None)
            if h:
                h.update(name=name or address, address=address)
                return h
            h = {"id": uuid.uuid4().hex[:8], "name": name or address, "address": address, "mac": None}
            hosts.append(h)
            return h

        return self.cfg.mutate(fn)

    def delete_host(self, host_id):
        def fn(d):
            d["games"]["hosts"] = [h for h in d["games"]["hosts"] if h["id"] != host_id]
            for s in d["servers"]:
                if (s.get("games") or {}).get("host") == host_id:
                    s["games"] = {"mode": "off"}

        self.cfg.mutate(fn)

    def apps(self, target_id, refresh=False):
        t = self.target(target_id)
        if not t:
            return None
        if refresh or t["address"] not in self.apps_cache:
            self.apps_cache[t["address"]] = self.moonlight.apps(t["address"])
        return self.apps_cache[t["address"]]

    # pairing ---------------------------------------------------------------------------------------------

    def pair(self, target_id):
        t = self.target(target_id)
        if not t:
            raise ValueError("Unknown gaming PC")
        pin = f"{random.randint(0, 9999):04d}"
        self.pairing = {"target": target_id, "name": t["name"], "pin": pin, "status": "waiting"}
        self._write_status()  # Dot Helper on that PC enters the PIN by itself

        def run():
            ok = self.moonlight.pair(t["address"], pin)
            self.pairing = {**self.pairing, "status": "paired" if ok else "failed"}
            self.apps_cache.pop(t["address"], None)
            self._remember_mac(t)
            self._write_status()

        threading.Thread(target=run, daemon=True).start()
        return self.pairing

    def _remember_mac(self, t):
        ip = resolve_ip(t["address"])
        mac = learn_mac(ip) if ip and not DEV else None
        if not mac:
            return

        def fn(d):
            for s in d["servers"]:
                if s["id"] == t["id"]:
                    s.setdefault("games", {})["mac"] = mac
            for h in d["games"]["hosts"]:
                if h["id"] == t["id"]:
                    h["mac"] = mac

        self.cfg.mutate(fn)

    # starting and stopping ----------------------------------------------------------------------------------

    def start(self, server_id, app=None, source="windows"):
        """Enter game mode for a server (its flow decides what happens to RDP)."""
        with self.lock:
            if self.state != "idle":
                return {"ok": False, "error": "busy"}
            flow = self.flow(server_id)
            if flow == "off":
                return {"ok": False, "error": "off"}
            if not self.moonlight.available():
                return {"ok": False, "error": "nomoonlight"}
            t = self.target(server_id)
            if not t:
                return {"ok": False, "error": "nohost"}
            if flow == "same" and self.dot.session:
                # Game.exe moves the Windows session as soon as it sees "starting": that ending is expected
                self.dot.session.expected = "game"
            self.last_error = None
            self.state = "starting"
            self.info = {"flow": flow, "target": t["id"], "host": t["name"], "address": t["address"],
                         "app": app or "Desktop", "source": source, "step": "Switching to game mode",
                         "started": time.time(), "error": None}
        threading.Thread(target=self._enter, args=(server_id, t, flow, app or "Desktop"), daemon=True).start()
        return {"ok": True}

    def _step(self, text):
        self.info = {**self.info, "step": text}
        self._write_status()

    def _enter(self, server_id, t, flow, app):
        try:
            self._write_status()
            time.sleep(1.2)  # let the shutter close
            # 1. wake the PC if it's asleep
            if system.probe(t["address"], SUNSHINE_PORT) is None:
                if t.get("mac"):
                    self._step(f"Waking {t['name']}")
                    wake(t["mac"])
                    for _ in range(45):
                        if system.probe(t["address"], SUNSHINE_PORT) is not None:
                            break
                        time.sleep(2)
                if system.probe(t["address"], SUNSHINE_PORT) is None:
                    raise RuntimeError(f"{t['name']} isn't answering. Is Sunshine or Apollo running?")

            # 2. what happens to the RDP session
            session = self.dot.session
            self.resume_rdp = bool(session and self.dot.secret)
            many = len(self.dot.displays.others()) > 0
            m2 = self.cfg.get()["games"]["monitor2"]
            if session:
                if flow == "same":
                    # Game.exe moves the Windows session to the GPU, which ends RDP; wait for that
                    session.expected = "game"
                    self._step("Moving Windows to the game stream")
                    for _ in range(2 if DEV else 30):
                        if self.dot.session is None:
                            break
                        time.sleep(0.5)
                    if self.dot.session is not None:
                        self.dot.session.stop("game")
                else:
                    session.stop("game")
                    time.sleep(1)

            # 3. the other monitors
            if many:
                self._open_side(m2, flow)

            # 4. Moonlight on the main monitor at its fastest mode
            main = self.dot.displays.main()
            width, height, fps = 1920, 1080, 60
            if main:
                fastest = self.dot.displays.fastest_mode(main["name"])
                if fastest and fastest != main["mode"]:
                    self.restore_mode = (main["name"], main["mode"])
                    self.dot.displays.set_mode(main["name"], fastest)
                mode = (fastest or main["mode"] or "1920x1080@60Hz").split(":", 1)[-1]
                res, _, hz = mode.partition("@")
                width, height = (int(v) for v in res.split("x"))
                fps = round(float(hz.rstrip("Hz") or 60))
                if main["transform"] in ("90", "270"):
                    width, height = height, width
                self.dot.displays.focus(main["name"])
            g = self.cfg.get()["games"]
            if g["fps"] != "max":
                fps = int(g["fps"])
            if has_dot_play():
                # Dot Play picks the PC, the game and its stream profile itself
                self._step("Opening Dot Play" if app == "Desktop" else f"Starting {app}")
                self.proc = self.moonlight.launcher(None if app == "Desktop" else app)
            else:
                self._step(f"Starting {app}")
                self.proc = self.moonlight.stream(t["address"], app, width, height, fps, g["codec"], g["bitrate"])
            with self.lock:
                self.state = "playing"
                self._step(f"{app} on {t['name']}")
            if self.proc:
                self.proc.wait()
            else:  # dev: play until stopped
                while self.state == "playing":
                    time.sleep(0.3)
        except Exception as e:
            self.info = {**self.info, "error": str(e)}
            print(f"games: {e}", flush=True)
            time.sleep(4)  # show the error on the shutter screen
        finally:
            self._leave()

    def stop(self, quit_app=False):
        with self.lock:
            if self.state not in ("starting", "playing"):
                return
            self.state = "stopping"
            self._step("Back to work")
        if quit_app and self.info.get("address"):
            self.moonlight.quit(self.info["address"])
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _leave(self):
        with self.lock:
            self.state = "stopping"
        self._close_side()
        if self.restore_mode:
            self.dot.displays.set_mode(*self.restore_mode)
            self.restore_mode = None
        self.dot.displays.apply()
        # the side session on monitor 2 (other / server flows) makes way for the full one
        if self.dot.session and self.dot.session.single:
            self.dot.session.stop("game")
            time.sleep(1)
        if self.resume_rdp and self.info.get("source") != "login":
            self.dot.reconnect()
        elif self.info.get("source") == "login":
            self.dot.forget_secret()
        with self.lock:
            self.state = "idle"
            self.proc = None
            self.resume_rdp = False
        self._write_status()

    # second monitor ---------------------------------------------------------------------------------------

    def _open_side(self, choice, flow):
        others = self.dot.displays.others()
        if choice == "rdp" and flow == "same":
            choice = "panel"  # Windows is busy streaming the game; RDP can't stay
        for o in others:
            if choice == "rdp" and o is others[0] and self.dot.secret:
                self.dot.displays.focus(o["name"])
                self.dot.reconnect(single=True)
            elif choice in ("browser", "panel"):
                self.dot.displays.focus(o["name"])
                url = self.cfg.get()["games"]["browserUrl"] if choice == "browser" else "http://127.0.0.1:7777/panel.html"
                self.dot.displays.exec_side(choice, url)
                self.side.append(choice)
            elif choice == "off":
                self.dot.displays.power(o["name"], False)
                self.side.append(("off", o["name"]))
            # "clock": the idle clock is already there

    def _close_side(self):
        for item in self.side:
            if isinstance(item, tuple):
                self.dot.displays.power(item[1], True)
            else:
                self.dot.displays.close_side(item)
        self.side = []

    # \\tsclient\dot: commands from Windows ------------------------------------------------------------------

    def _setup_inbox(self):
        try:
            os.makedirs(INBOX, exist_ok=True)
            pw = kiosk.user()
            if pw is not None:
                os.chown(INBOX, pw.pw_uid, pw.pw_gid)  # FreeRDP shares it as the kiosk user
        except OSError:
            pass

    def _watch_inbox(self):
        while True:
            try:
                for name in sorted(os.listdir(INBOX)):
                    if not name.startswith("cmd-") or not name.endswith(".json"):
                        continue
                    path = os.path.join(INBOX, name)
                    try:
                        if os.path.getsize(path) > 4096:
                            raise ValueError("too big")
                        with open(path, encoding="utf-8-sig") as f:
                            msg = json.load(f)
                    except (OSError, ValueError):
                        msg = None
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    if isinstance(msg, dict):
                        self.command(msg)
            except OSError:
                pass
            time.sleep(0.5)

    def command(self, msg):
        """A command from Windows (Game.exe / Dot Helper). Only ever acted on during a signed-in session."""
        cmd = msg.get("cmd")
        session = self.dot.session
        if cmd == "hello":
            self.helper = {"at": time.time(), "version": str(msg.get("version", ""))[:20],
                           "host": str(msg.get("host", ""))[:20]}
        elif cmd == "game" and session:
            app = str(msg.get("app") or "Desktop")[:100]
            res = self.start(session.server_id, app, source="windows")
            if not res["ok"]:
                self.last_error = res["error"]
        elif cmd == "exit-game":
            self.stop(quit_app=bool(msg.get("quit")))
        elif cmd == "pin-entered" and self.pairing:
            self.pairing = {**self.pairing, "status": "entered"}
        self._write_status()

    def _write_status(self):
        """status.json in the shared folder, read by Dot Helper."""
        session = self.dot.session
        flow = self.flow(session.server_id) if session else "off"
        pair = None
        if self.pairing and self.pairing["status"] == "waiting" and session:
            t = self.target(self.pairing["target"])
            server = self.dot.cfg.server(session.server_id)
            # only hand the PIN to the PC it belongs to
            if t and server and t["address"] == server["host"]:
                pair = {"pin": self.pairing["pin"], "name": "Dot"}
        status = {"state": self.state, "flow": flow, "step": self.info.get("step"),
                  "error": self.info.get("error") or self.last_error, "pair": pair, "at": time.time()}
        try:
            tmp = os.path.join(INBOX, ".status.tmp")
            with open(tmp, "w") as f:
                json.dump(status, f)
            os.replace(tmp, os.path.join(INBOX, "status.json"))
        except OSError:
            pass

    def status(self):
        c = self.cfg.get()
        return {
            "state": self.state,
            "info": self.info,
            "pairing": self.pairing,
            "helper": self.helper,
            "moonlight": self.moonlight.available(),
            "hosts": c["games"]["hosts"],
        }
