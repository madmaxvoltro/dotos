"""Running things inside the kiosk session (as the "dot" user, on its Wayland display)."""

import os
import subprocess

try:
    import grp
    import pwd
except ImportError:  # dev mode on Windows
    grp = pwd = None

KIOSK_USER = os.environ.get("DOT_USER", "dot")


def user():
    if pwd is None:
        return None
    try:
        return pwd.getpwnam(KIOSK_USER)
    except KeyError:
        return None


def env():
    """The kiosk session's environment; session.sh writes it when sway starts."""
    e = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": os.environ.get("LANG", "C.UTF-8")}
    pw = user()
    if pw is None:
        return e
    e.update(HOME=pw.pw_dir, USER=KIOSK_USER, XDG_RUNTIME_DIR=f"/run/user/{pw.pw_uid}")
    try:
        with open(f"/run/user/{pw.pw_uid}/dot-session.env") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k:
                    e[k] = v
    except OSError:
        e.setdefault("WAYLAND_DISPLAY", "wayland-0")
    e.setdefault("SDL_VIDEODRIVER", "wayland")
    e.setdefault("QT_QPA_PLATFORM", "wayland")
    return e


def popen(cmd, **kwargs):
    """Start cmd as the kiosk user in its session (when running as root)."""
    kwargs.setdefault("env", env())
    pw = user()
    if pw is not None and hasattr(os, "geteuid") and os.geteuid() == 0:
        groups = [g.gr_gid for g in grp.getgrall() if KIOSK_USER in g.gr_mem]
        kwargs.update(user=pw.pw_uid, group=pw.pw_gid, extra_groups=groups, cwd=pw.pw_dir)
    return subprocess.Popen(cmd, **kwargs)


def run(cmd, timeout=10, input=None):
    """Like system.run, but as the kiosk user in its session. Returns (code, stdout, stderr)."""
    try:
        p = popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return 127, "", str(e)
    try:
        out, err = p.communicate(input=input, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        p.kill()
        return 124, "", "timed out"
