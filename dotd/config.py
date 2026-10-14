"""Persistent device configuration: servers, profiles, preferences.

Stored as one JSON file (root-only, 0600). Nothing secret lives here except
the optional settings-PIN hash; Windows passwords are never written to disk.
"""

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid

DEFAULTS = {
    "version": 1,
    "setupComplete": False,
    "device": {
        "keyboard": "us",
        "timezone": "",
        "use24h": True,
        "idleMinutes": 5,
        "verboseBoot": False,
        "accent": "#e8305e",
        "adminPin": None,
    },
    "display": {
        "scale": 1.0,
    },
    "rdp": {
        "quality": "auto",  # auto | lan | broadband | low
        "sound": True,
        "microphone": False,
        "clipboard": True,
        "usbDrives": False,
        "client": "auto",  # auto | x11 | sdl: which FreeRDP client
        "graphics": "auto",  # auto "Smooth" (AVC420; "light" means the same) | sharp (RemoteFX) | compatible (no GFX)
        "debugLog": False,  # FreeRDP's detailed log in Dot's log, for "why doesn't it connect?"
        "hwDecode": False,  # experimental: FreeRDP from `sudo dot hwdecode`, H.264 on the Pi's hardware decoder
    },
    "screen": {
        "enabled": True,
        "delay": 5,  # seconds the monitor must stay off
        "session": "lock",  # lock | pause | none
        "mutePi": True,
        "muteMonitor": True,
        "usb": True,
        "inputs": True,  # keyboard and mouse too: the monitor's button wakes Dot
        "mode": "extend",  # extend | main | mirror: how Windows sees several monitors
        "winScale": "auto",  # Windows scaling in the session: auto | 100 | 125 | 150 | 175 | 200
        "main": None,  # sway identifier of the main monitor
        "layout": {},  # identifier -> {x, y, mode, transform}
    },
    "audio": {
        "followWindows": True,  # Windows' volume moves the real volume (RDP volume messages)
        "target": "auto",  # auto | monitor | pi: where "the volume" lives
    },
    "games": {
        "hosts": [],  # separate gaming PCs: {id, name, address, mac}
        "monitor2": "rdp",  # rdp | browser | panel | clock | off, while a game plays
        "browserUrl": "https://www.youtube.com",
        "codec": "auto",  # auto | HEVC | H.264 | AV1
        "bitrate": "auto",  # auto or Mbps
        "fps": "max",  # max (the main monitor's fastest refresh) or a number
        "pinHash": None,  # game mode from the sign-in screen asks for this PIN (set in Settings › Games)
        "pinLength": 4,
    },
    "tailscale": {"enabled": False},
    "features": {"gameMode": True},
    "servers": [],
    "profiles": [],
    "lastProfile": None,
}

SERVER_FIELDS = {"name", "host", "port", "domain", "gateway", "games"}
GAME_FLOWS = ("off", "same", "other", "server")
PROFILE_FIELDS = {"name", "username", "domain", "servers", "defaultServer", "color", "accounts"}


def account_for(profile, server):
    """The Windows account a person uses on one server: their own user name and domain, unless that
    server has its own (profile["accounts"][server id]). Dot shows the same person either way."""
    acct = (profile.get("accounts") or {}).get(server["id"] if server else None) or {}
    return {**profile,
            "username": acct.get("username") or profile.get("username", ""),
            "domain": acct.get("domain") if acct.get("username") else profile.get("domain", "")}
PROFILE_COLORS = ["#2c2c2e", "#3a2a5c", "#1f3d5a", "#1f4a3a", "#5a3a1f", "#5a1f33"]


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def hash_pin(pin):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000)
    return f"pbkdf2${salt}${digest.hex()}"


def check_pin(stored, pin):
    try:
        _, salt, want = stored.split("$")
    except (AttributeError, ValueError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(got.hex(), want)


class Config:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return _merge(DEFAULTS, json.load(f))
        except FileNotFoundError:
            return copy.deepcopy(DEFAULTS)

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def get(self):
        with self._lock:
            return copy.deepcopy(self._data)

    def public(self):
        """Config without the PIN hash, safe to hand to the UI."""
        data = self.get()
        data["device"]["hasPin"] = bool(data["device"].pop("adminPin", None))
        data["games"].pop("pinHash", None)
        return data

    def mutate(self, fn):
        with self._lock:
            result = fn(self._data)
            self._save()
            return result

    # servers -------------------------------------------------------------

    def server(self, server_id):
        return next((s for s in self.get()["servers"] if s["id"] == server_id), None)

    def upsert_server(self, payload):
        clean = {k: payload[k] for k in SERVER_FIELDS if k in payload}
        clean["host"] = str(clean.get("host", "")).strip()
        if not clean["host"]:
            raise ValueError("Address is required")
        clean["port"] = int(clean.get("port") or 3389)
        clean["name"] = (clean.get("name") or "").strip() or clean["host"]
        if "games" in clean:
            g = clean["games"] if isinstance(clean["games"], dict) else {}
            clean["games"] = {"mode": g.get("mode") if g.get("mode") in GAME_FLOWS else "off",
                              "host": g.get("host"), "mac": g.get("mac")}

        def fn(d):
            sid = payload.get("id")
            existing = next((s for s in d["servers"] if s["id"] == sid), None)
            if existing:
                existing.update(clean)
                return existing
            server = {"id": uuid.uuid4().hex[:8], "domain": "", "gateway": "", **clean}
            d["servers"].append(server)
            # a lone profile picks up new servers automatically
            if len(d["profiles"]) == 1:
                p = d["profiles"][0]
                p["servers"].append(server["id"])
                p["defaultServer"] = p["defaultServer"] or server["id"]
            return server

        return self.mutate(fn)

    def delete_server(self, server_id):
        def fn(d):
            d["servers"] = [s for s in d["servers"] if s["id"] != server_id]
            for p in d["profiles"]:
                p["servers"] = [s for s in p["servers"] if s != server_id]
                if p["defaultServer"] == server_id:
                    p["defaultServer"] = p["servers"][0] if p["servers"] else None

        self.mutate(fn)

    # profiles ------------------------------------------------------------

    def profile(self, profile_id):
        return next((p for p in self.get()["profiles"] if p["id"] == profile_id), None)

    def upsert_profile(self, payload):
        clean = {k: payload[k] for k in PROFILE_FIELDS if k in payload}
        clean["username"] = str(clean.get("username", "")).strip()
        if not clean["username"]:
            raise ValueError("Windows user name is required")
        clean["name"] = (clean.get("name") or "").strip() or clean["username"]

        def fn(d):
            known = {s["id"] for s in d["servers"]}
            servers = [s for s in clean.get("servers", []) if s in known]
            if not servers and "servers" not in payload:
                servers = [s["id"] for s in d["servers"]]
            clean["servers"] = servers
            if clean.get("defaultServer") not in servers:
                clean["defaultServer"] = servers[0] if servers else None
            if "accounts" in clean:
                accounts = clean["accounts"] if isinstance(clean["accounts"], dict) else {}
                clean["accounts"] = {
                    sid: {"username": str(a.get("username", "")).strip(), "domain": str(a.get("domain", "")).strip()}
                    for sid, a in accounts.items()
                    if sid in known and isinstance(a, dict) and str(a.get("username", "")).strip()
                }
            pid = payload.get("id")
            existing = next((p for p in d["profiles"] if p["id"] == pid), None)
            if existing:
                existing.update(clean)
                return existing
            profile = {
                "id": uuid.uuid4().hex[:8],
                "domain": "",
                "color": PROFILE_COLORS[len(d["profiles"]) % len(PROFILE_COLORS)],
                **clean,
            }
            d["profiles"].append(profile)
            d["lastProfile"] = d["lastProfile"] or profile["id"]
            return profile

        return self.mutate(fn)

    def delete_profile(self, profile_id):
        def fn(d):
            d["profiles"] = [p for p in d["profiles"] if p["id"] != profile_id]
            if d["lastProfile"] == profile_id:
                d["lastProfile"] = d["profiles"][0]["id"] if d["profiles"] else None

        self.mutate(fn)

    # settings ------------------------------------------------------------

    def patch_settings(self, payload):
        allowed = {"device", "display", "rdp", "screen", "audio", "games", "tailscale", "features"}

        def fn(d):
            for section, values in payload.items():
                if section not in allowed or not isinstance(values, dict):
                    continue
                values = {k: v for k, v in values.items() if k not in ("adminPin", "hosts", "pinHash", "pinLength")}
                d[section].update(values)

        self.mutate(fn)

    def set_pin(self, pin):
        def fn(d):
            d["device"]["adminPin"] = hash_pin(pin) if pin else None

        self.mutate(fn)

    def verify_pin(self, pin):
        stored = self.get()["device"].get("adminPin")
        return stored is None or check_pin(stored, pin or "")
