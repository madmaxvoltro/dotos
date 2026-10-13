"""Local HTTP API + static file server for the Dot UI (127.0.0.1 only)."""

import json
import mimetypes
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import audio, displays, games, monitor, rdp, system
from .config import Config

def _version():
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "dev"


VERSION = _version()
ADMIN_TTL = 600  # seconds a settings unlock stays valid without use


class Dot:
    """All device state shared between request threads."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = rdp.find_client()
        self.boot_time = time.time()
        self.network = system.network_status()
        self.tailscale = system.tailscale_status()
        self.reach = {}  # server id -> latency ms | None
        self.session = None
        # the Windows password of whoever is signed in, kept in memory only (never on disk) so Dot can
        # reconnect after game mode; wiped at sign-out, standby, shutdown and whenever dotd restarts
        self.secret = None
        self.connecting = None  # {"profileId", "serverId"} while signing in
        self.last_end = None
        self.admin_tokens = {}
        self.login_lock = threading.Lock()
        self.displays = displays.Displays(cfg)
        self.monitor = monitor.Monitor(cfg, self.lock_session, lambda: (self.displays.main() or {}).get("name"))
        self.audio = audio.Audio(cfg, self.monitor)
        self.games = games.Games(self)
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        """Refresh network, Tailscale and server reachability in the background."""
        while True:
            self.network = system.network_status()
            self.tailscale = system.tailscale_status()
            for s in self.cfg.get()["servers"]:
                self.reach[s["id"]] = system.probe(s["host"], s["port"]) if self.network["connected"] else None
            time.sleep(4 if self.session is None else 15)

    def probe_now(self):
        for s in self.cfg.get()["servers"]:
            self.reach[s["id"]] = system.probe(s["host"], s["port"])

    # admin -----------------------------------------------------------------

    def admin_ok(self, token):
        if not self.cfg.get()["setupComplete"]:
            return True
        now = time.time()
        self.admin_tokens = {t: exp for t, exp in self.admin_tokens.items() if exp > now}
        if token in self.admin_tokens:
            self.admin_tokens[token] = now + ADMIN_TTL
            return True
        return False

    def unlock(self, pin):
        if not self.cfg.verify_pin(pin):
            return None
        token = secrets.token_urlsafe(24)
        self.admin_tokens[token] = time.time() + ADMIN_TTL
        return token

    # state -------------------------------------------------------------------

    def state(self):
        cfg = self.cfg.public()
        servers = [{**s, "latency": self.reach.get(s["id"])} for s in cfg["servers"]]
        session = None
        if self.session:
            session = {"profileId": self.session.profile_id, "serverId": self.session.server_id,
                       "started": self.session.started}
        return {
            "version": VERSION,
            "dev": system.DEV,
            "now": time.time(),
            "bootTime": self.boot_time,
            "setupComplete": cfg["setupComplete"],
            "network": self.network,
            "tailscale": {**self.tailscale, "enabled": cfg["tailscale"]["enabled"]},
            "servers": servers,
            "profiles": cfg["profiles"],
            "lastProfile": cfg["lastProfile"],
            "device": cfg["device"],
            "display": cfg["display"],
            "rdp": cfg["rdp"],
            "screen": cfg["screen"],
            "monitor": self.monitor.status(),
            "displays": self.displays.status(),
            "audio": {**self.audio.status(), **cfg["audio"]},
            "features": cfg["features"],
            "games": {**self.games.status(), **{k: v for k, v in cfg["games"].items() if k != "hosts"}},
            "client": {"name": os.path.basename(self.client["bin"]), "major": self.client["major"]} if self.client else None,
            "session": session,
            "connecting": self.connecting,
            "lastEnd": self.last_end,
        }

    # sign in -------------------------------------------------------------------

    def login(self, profile_id, server_id, password, game=False):
        """Check the password, then start the session (or game mode). Tries other servers if the chosen one is down."""
        profile = self.cfg.profile(profile_id)
        if not profile:
            return {"ok": False, "error": "unknown"}
        if not self.client:
            return {"ok": False, "error": "noclient"}
        if not self.login_lock.acquire(blocking=False):
            return {"ok": False, "error": "busy"}
        try:
            if not system.DEV and not system.network_status()["connected"]:
                return {"ok": False, "error": "offline"}
            order = [server_id] + [s for s in profile["servers"] if s != server_id]
            candidates = [self.cfg.server(sid) for sid in order if sid]
            candidates = [s for s in candidates if s]
            if not candidates:
                return {"ok": False, "error": "noserver"}
            cfg = self.cfg.get()
            for server in candidates:
                if system.probe(server["host"], server["port"]) is None:
                    continue
                self.connecting = {"profileId": profile_id, "serverId": server["id"]}
                result = rdp.check_password(self.client, server, profile, password, cfg)
                if result == "server":
                    continue
                if result != "ok":
                    self.connecting = None
                    return {"ok": False, "error": result, "serverId": server["id"]}
                self.cfg.mutate(lambda d: d.update(lastProfile=profile_id))
                self.last_end = None
                self.connecting = None
                self.secret = {"profileId": profile_id, "serverId": server["id"], "password": password}
                if game:
                    started = self.games.start(server["id"], source="login")
                    if not started["ok"]:
                        self.forget_secret()
                    return {**started, "serverId": server["id"]}
                self._open_session(server, profile, password)
                return {"ok": True, "serverId": server["id"], "failover": server["id"] != server_id}
            self.connecting = None
            return {"ok": False, "error": "server"}
        finally:
            self.login_lock.release()

    def _open_session(self, server, profile, password, single=False):
        outputs = self.displays.status()["outputs"]
        if not single:
            self.displays.focus_main()  # a single-screen session opens on the main monitor
        cfg = self.cfg.get()
        self.session = rdp.Session(self.client, server, profile, password, cfg, self._ended, outputs, single=single)
        if not single:
            self.displays.session_started(cfg["screen"]["mode"])

    def reconnect(self, single=False):
        """Back into Windows with the password from memory (after game mode, or onto monitor 2 during it)."""
        s = self.secret
        server = self.cfg.server(s["serverId"]) if s else None
        profile = self.cfg.profile(s["profileId"]) if s else None
        if not (server and profile and self.client):
            return False
        self._open_session(server, profile, s["password"], single=single)
        return True

    def forget_secret(self):
        self.secret = None

    def lock_session(self):
        self.forget_secret()
        self.games.stop()
        if self.session:
            self.session.stop("screen_off")

    def _ended(self, reason, detail):
        self.displays.session_ended()
        self.session = None
        if reason == "game":  # game mode took the session over; Dot brings it back afterwards
            return
        self.forget_secret()
        self.last_end = {"reason": reason, "at": time.time(), "detail": detail if reason not in ("signed_out", "screen_off") else ""}

    def check_account(self, payload):
        """Setup helper: verify a user name / password pair against one server without connecting."""
        server = self.cfg.server(payload.get("serverId"))
        if not server or not self.client:
            return "noserver" if not server else "noclient"
        profile = {"username": payload.get("username", ""), "domain": payload.get("domain", "")}
        return rdp.check_password(self.client, server, profile, payload.get("password", ""), self.cfg.get())


def make_handler(dot: Dot, ui_dir: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "dotd"

        def log_message(self, fmt, *args):
            if system.DEV:
                super().log_message(fmt, *args)

        # helpers -------------------------------------------------------------

        def send_json(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except ValueError:
                return {}

        def require_admin(self):
            if dot.admin_ok(self.headers.get("X-Dot-Admin", "")):
                return True
            self.send_json({"error": "locked"}, 403)
            return False

        def serve_static(self, path):
            rel = os.path.normpath(path.lstrip("/") or "index.html")
            if rel.startswith("..") or os.path.isabs(rel):
                return self.send_error(404)
            full = os.path.join(ui_dir, rel)
            if not os.path.isfile(full):
                full = os.path.join(ui_dir, "index.html")
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(full)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # routes ----------------------------------------------------------------

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/api/state":
                return self.send_json(dot.state())
            if url.path == "/api/wifi":
                return self.send_json({"networks": system.wifi_list(rescan="rescan" in q)})
            if url.path == "/api/timezones":
                return self.send_json({"timezones": system.timezones()})
            if url.path == "/api/info":
                return self.send_json({**system.device_info(), "version": dot.state()["version"]})
            if url.path == "/api/games/apps":
                target = (q.get("target") or [""])[0]
                return self.send_json({"apps": dot.games.apps(target, refresh="refresh" in q)})
            if url.path == "/api/audio":
                return self.send_json({**dot.audio.refresh(), **dot.cfg.get()["audio"]})
            if url.path == "/api/monitor":
                conn = (q.get("connector") or [dot.monitor.status()["main"]])[0]
                return self.send_json({**dot.monitor.status(), "connector": conn, "controls": dot.monitor.controls(conn), "usb": monitor.usb_devices()})
            if url.path == "/api/admin/check":
                return self.send_json({"ok": dot.admin_ok(self.headers.get("X-Dot-Admin", ""))})
            if url.path.startswith("/api/"):
                return self.send_json({"error": "not found"}, 404)
            return self.serve_static(url.path)

        def do_POST(self):
            path = urlparse(self.path).path
            data = self.body()

            # open to anyone at the device ------------------------------------
            if path == "/api/login":
                return self.send_json(dot.login(data.get("profileId"), data.get("serverId"), data.get("password", ""),
                                                game=bool(data.get("game"))))
            if path == "/api/games/stop":
                dot.games.stop(quit_app=bool(data.get("quit")))
                return self.send_json({"ok": True})
            if path == "/api/power":
                if dot.session is None and dot.games.state == "idle":
                    dot.forget_secret()
                    system.power(data.get("action"))
                return self.send_json({"ok": dot.session is None})
            if path == "/api/dev/game" and system.DEV:
                # stands in for Game.exe dropping a command into \\tsclient\dot
                dot.games.command(data)
                return self.send_json({"ok": True})
            if path == "/api/volume":
                if "sink" in data:
                    dot.audio.set_default_sink(data["sink"])
                dot.audio.set_volume(pct=data.get("volume"), muted=data.get("muted"))
                return self.send_json({"ok": True, **dot.audio.refresh()})
            if path == "/api/dev/stream" and system.DEV:
                dot.audio.dev["stream"] = {"id": 1, "volume": int(data.get("volume", 100)), "muted": bool(data.get("muted"))}
                return self.send_json({"ok": True})
            if path == "/api/wifi":
                ok, err = system.wifi_connect(data.get("ssid", ""), data.get("password", ""))
                return self.send_json({"ok": ok, "error": err})
            if path == "/api/dev/monitor" and system.DEV:
                dot.monitor.set_dev_power(bool(data.get("on")))
                return self.send_json({"ok": True})
            if path == "/api/admin/unlock":
                token = dot.unlock(data.get("pin", ""))
                return self.send_json({"ok": bool(token), "token": token})
            if path == "/api/profile/last":
                if dot.cfg.profile(data.get("profileId")):
                    dot.cfg.mutate(lambda d: d.update(lastProfile=data["profileId"]))
                return self.send_json({"ok": True})

            # settings: unlocked during setup, PIN afterwards -------------------
            if not self.require_admin():
                return
            try:
                if path == "/api/servers":
                    server = dot.cfg.upsert_server(data)
                    dot.reach[server["id"]] = system.probe(server["host"], server["port"])
                    return self.send_json({"ok": True, "server": server})
                if path == "/api/servers/delete":
                    dot.cfg.delete_server(data.get("id"))
                    return self.send_json({"ok": True})
                if path == "/api/servers/test":
                    latency = system.probe(str(data.get("host", "")).strip(), int(data.get("port") or 3389))
                    return self.send_json({"ok": latency is not None, "latency": latency})
                if path == "/api/profiles":
                    return self.send_json({"ok": True, "profile": dot.cfg.upsert_profile(data)})
                if path == "/api/profiles/delete":
                    dot.cfg.delete_profile(data.get("id"))
                    return self.send_json({"ok": True})
                if path == "/api/account/check":
                    return self.send_json({"result": dot.check_account(data)})
                if path == "/api/settings":
                    dot.cfg.patch_settings(data)
                    c = dot.cfg.get()
                    system.apply_kiosk_env(c["device"]["keyboard"], c["display"]["scale"])
                    if "timezone" in data.get("device", {}):
                        system.set_timezone(c["device"]["timezone"])
                    return self.send_json({"ok": True})
                if path == "/api/pin":
                    dot.cfg.set_pin(data.get("pin") or None)
                    return self.send_json({"ok": True})
                if path == "/api/tailscale/install":
                    system.tailscale_install()
                    dot.tailscale = system.tailscale_status()
                    return self.send_json({"ok": True})
                if path == "/api/tailscale/up":
                    dot.cfg.patch_settings({"tailscale": {"enabled": True}})
                    system.tailscale_up()
                    dot.tailscale = system.tailscale_status()
                    return self.send_json({"ok": True})
                if path == "/api/tailscale/down":
                    dot.cfg.patch_settings({"tailscale": {"enabled": False}})
                    system.tailscale_down()
                    return self.send_json({"ok": True})
                if path == "/api/setup/complete":
                    dot.cfg.mutate(lambda d: d.update(setupComplete=True))
                    dot.probe_now()
                    return self.send_json({"ok": True})
                if path == "/api/games/hosts":
                    return self.send_json({"ok": True, "host": dot.games.upsert_host(data)})
                if path == "/api/games/hosts/delete":
                    dot.games.delete_host(data.get("id"))
                    return self.send_json({"ok": True})
                if path == "/api/games/pair":
                    return self.send_json({"ok": True, "pairing": dot.games.pair(data.get("target"))})
                if path == "/api/monitor/set":
                    return self.send_json({"ok": dot.monitor.set_control(data.get("connector"), data.get("control"), data.get("value", 0))})
                if path == "/api/displays":
                    dot.displays.save_layout(data)
                    return self.send_json({"ok": True, **dot.displays.status()})
                if path == "/api/monitor/test":
                    dot.monitor.test(int(data.get("seconds") or 10))
                    return self.send_json({"ok": True})
                if path == "/api/kiosk/restart":
                    threading.Timer(0.5, system.restart_kiosk).start()
                    return self.send_json({"ok": True})
                if path == "/api/system/update":
                    system.start_update()
                    return self.send_json({"ok": True})
                if path == "/api/system/reset":
                    dot.cfg.mutate(lambda d: d.update(setupComplete=False))
                    return self.send_json({"ok": True})
            except ValueError as e:
                return self.send_json({"ok": False, "error": str(e)}, 400)
            return self.send_json({"error": "not found"}, 404)

    return Handler


def serve(host, port, config_path, ui_dir):
    dot = Dot(Config(config_path))
    httpd = ThreadingHTTPServer((host, port), make_handler(dot, ui_dir))
    httpd.daemon_threads = True
    print(f"dotd {VERSION} on http://{host}:{port} (ui: {ui_dir}, config: {config_path}, dev: {system.DEV})", flush=True)
    httpd.serve_forever()
