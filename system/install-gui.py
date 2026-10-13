#!/usr/bin/env python3
"""The installer on the Pi's own screen (install.sh --gui).

Draws on the text console (tty1), because on a fresh Raspberry Pi OS Lite there
is nothing graphical yet:

    left:  the install log, scrolling quietly
    right: the Dot mark, a progress bar, the percentage and time left

Before installing it asks for options on that screen (arrow keys, Space, Enter)
and writes them to --choices for install.sh. Progress comes from --state, a small
JSON file install.sh rewrites as it goes; the log from --log.

Standard library only. `--frame out.txt` renders one frame to a file instead of
the console, for checking the layout on any computer.
"""

import argparse
import json
import math
import os
import re
import select
import sys
import time

try:
    import fcntl
    import struct
    import termios
except ImportError:  # --frame on a non-Linux computer
    fcntl = termios = None

ESC = "\x1b["
WHITE, GRAY, DIM, ACCENT, RESET, BOLD = ESC + "97m", ESC + "37m", ESC + "90m", ESC + "91m", ESC + "0m", ESC + "1m"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")

OPTIONS = [
    ("games", "Game mode", "Moonlight, to play games from your PC"),
    ("tailscale", "Tailscale", "reach servers on other networks"),
    ("splash", "Boot splash", "the white dot while starting"),
]
AUTOSTART = 90  # seconds before installing with the defaults when nobody's at the keyboard


class Screen:
    def __init__(self, tty, frame_file=None, size=None):
        self.frame_file = frame_file
        self.fd = None
        if frame_file:
            self.cols, self.rows = size or (120, 34)
            return
        self.fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)
        rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(self.fd, termios.TIOCGWINSZ, b"\0" * 8))
        self.cols, self.rows = cols or 100, rows or 30
        self.saved = termios.tcgetattr(self.fd)
        raw = termios.tcgetattr(self.fd)
        raw[3] &= ~(termios.ICANON | termios.ECHO)  # keys one at a time, not echoed
        termios.tcsetattr(self.fd, termios.TCSANOW, raw)
        self.write(ESC + "?25l" + ESC + "2J")  # hide the cursor, clear

    def write(self, text):
        if self.fd is not None:
            os.write(self.fd, text.encode("utf-8", "replace"))

    def close(self):
        if self.fd is not None:
            self.write(RESET + ESC + "2J" + ESC + "H" + ESC + "?25h")
            termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
            os.close(self.fd)

    def key(self, timeout):
        """One key ('up', 'down', 'space', 'enter' or a character), or None after timeout."""
        if self.fd is None:
            return None
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(self.fd, 8).decode("utf-8", "ignore")
        return {"\x1b[A": "up", "\x1b[B": "down", " ": "space", "\n": "enter", "\r": "enter"}.get(data, data)

    def draw(self, cells):
        """cells: list of (row, col, text-with-ansi). Draws a whole frame at once (no flicker)."""
        lines = [[] for _ in range(self.rows)]
        for row, col, text in cells:
            if 0 <= row < self.rows:
                lines[row].append((col, text))
        out = [ESC + "H"]
        for r, parts in enumerate(lines):
            out.append(ESC + f"{r + 1};1H" + ESC + "2K")
            for col, text in sorted(parts):
                out.append(ESC + f"{r + 1};{col + 1}H" + text + RESET)
        if self.frame_file:
            with open(self.frame_file, "w", encoding="utf-8") as f:
                plain = [[" "] * self.cols for _ in range(self.rows)]
                for row, col, text in cells:
                    for i, ch in enumerate(ANSI.sub("", text)):
                        if 0 <= row < self.rows and 0 <= col + i < self.cols:
                            plain[row][col + i] = ch
                f.write("\n".join("".join(r).rstrip() for r in plain) + "\n")
        else:
            self.write("".join(out))


# drawing ---------------------------------------------------------------------------------------------

def dot(cx, top, radius):
    """The white circle in half-block characters. A console cell is about twice as tall as it is
    wide, so splitting each cell into a top and bottom half gives square 'pixels'."""
    cells = []
    height = radius * 2  # half-cell rows
    for r in range(radius):
        line = ""
        for c in range(-radius, radius + 1):
            x = c
            hits = []
            for half in (0, 1):
                y = (r * 2 + half + 0.5) - height / 2
                hits.append(x * x + y * y <= (radius - 0.15) ** 2)
            line += {(True, True): "█", (True, False): "▀", (False, True): "▄"}.get(tuple(hits), " ")
        cells.append((top + r, cx - radius, WHITE + line))
    return cells


def bar(width, pct):
    filled = pct / 100 * width
    full = int(filled)
    half = "▌" if filled - full >= 0.5 and full < width else ""
    return WHITE + "█" * full + half + DIM + "─" * (width - full - len(half))


def fmt_left(seconds):
    if seconds is None:
        return "estimating time"
    if seconds < 60:
        return "less than a minute left"
    m = math.ceil(seconds / 60)
    return f"about {m} min left"


def log_cells(screen, lines, width):
    cells = []
    usable = screen.rows - 2
    tail = [ANSI.sub("", l).replace("\t", "  ") for l in lines if l.strip()][-usable:]
    for i, line in enumerate(tail):
        shade = DIM if i < len(tail) - 3 else GRAY  # the newest lines a little brighter
        cells.append((1 + i, 2, shade + line[: width - 4]))
    for r in range(screen.rows):
        cells.append((r, width, DIM + "│"))
    return cells


def frame(screen, log_lines, state, options=None, cursor=0, countdown=None):
    left_w = max(30, int(screen.cols * 0.42))
    cx = left_w + (screen.cols - left_w) // 2
    cells = log_cells(screen, log_lines, left_w)
    radius = max(3, min(6, screen.rows // 6))
    top = max(1, screen.rows // 2 - radius - 6)
    cells += dot(cx, top, radius)
    y = top + radius + 2

    def center(row, text):
        cells.append((row, cx - len(ANSI.sub("", text)) // 2, text))

    if options is not None:
        center(y, BOLD + WHITE + "Install Dot")
        center(y + 1, GRAY + "Choose what to install")
        width = 46
        for i, (key, name, desc) in enumerate(OPTIONS):
            on = options[key]
            mark = (WHITE + "[x]") if on else (DIM + "[ ]")
            pointer = (ACCENT + "› ") if cursor == i else "  "
            cells.append((y + 3 + i, cx - width // 2, f"{pointer}{mark} {WHITE}{name:<13}{DIM}{desc}"))
        go = len(OPTIONS)
        label = " Install " if cursor != go else "› Install ‹"
        center(y + 4 + go, (BOLD + WHITE if cursor == go else GRAY) + label)
        center(y + 6 + go, DIM + "↑ ↓ move   Space change   Enter install")
        if countdown is not None:
            center(y + 7 + go, DIM + f"Starts by itself in {countdown} s")
        return cells

    pct = max(0.0, min(100.0, float(state.get("pct") or 0)))
    if state.get("error"):
        center(y, BOLD + ACCENT + "Something went wrong")
        center(y + 2, GRAY + str(state["error"])[: screen.cols - left_w - 4])
        center(y + 4, DIM + "The log on the left shows what happened.")
        return cells
    if state.get("done"):
        center(y, BOLD + WHITE + "Dot is installed")
        center(y + 2, GRAY + (state.get("step") or "Restarting"))
        return cells
    center(y, BOLD + WHITE + "Installing Dot")
    bw = min(48, screen.cols - left_w - 8)
    center(y + 2, bar(bw, pct))
    center(y + 3, WHITE + f"{pct:.0f}%" + DIM + "  ·  " + GRAY + fmt_left(state.get("eta")))
    center(y + 5, GRAY + (state.get("step") or ""))
    return cells


# running ------------------------------------------------------------------------------------------------

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def tail(path, n=200):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


class Eta:
    """Time left from the rate of progress, smoothed so it doesn't jump around."""

    def __init__(self):
        self.start = time.time()
        self.value = None

    def update(self, pct):
        if pct < 3:
            return None
        elapsed = time.time() - self.start
        raw = elapsed * (100 - pct) / pct
        self.value = raw if self.value is None else self.value * 0.9 + raw * 0.1
        return self.value


def ask_options(screen, args, defaults):
    options = dict(defaults)
    cursor = len(OPTIONS)  # start on Install
    deadline = time.time() + AUTOSTART
    while True:
        left = max(0, int(deadline - time.time()))
        screen.draw(frame(screen, ["Ready to install.", "Choose options on the right, then press Enter."], {},
                          options=options, cursor=cursor, countdown=left))
        if left == 0:
            return options
        k = screen.key(0.5)
        if k is None:
            continue
        deadline = time.time() + AUTOSTART  # someone's there: no rush
        if k == "up":
            cursor = (cursor - 1) % (len(OPTIONS) + 1)
        elif k == "down":
            cursor = (cursor + 1) % (len(OPTIONS) + 1)
        elif k == "space" and cursor < len(OPTIONS):
            key = OPTIONS[cursor][0]
            options[key] = not options[key]
        elif k == "enter":
            if cursor < len(OPTIONS):
                key = OPTIONS[cursor][0]
                options[key] = not options[key]
            else:
                return options


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tty", default="/dev/tty1")
    ap.add_argument("--state", default="/run/dot-install/state.json")
    ap.add_argument("--log", default="/run/dot-install/log.txt")
    ap.add_argument("--choices", default="/run/dot-install/choices.json")
    ap.add_argument("--defaults", default='{"games": true, "tailscale": false, "splash": true}')
    ap.add_argument("--frame", help="render one frame to this file and exit (layout check)")
    ap.add_argument("--frame-state", default="{}")
    ap.add_argument("--options", action="store_true", help="with --frame: draw the options screen")
    args = ap.parse_args()

    if args.frame:
        s = Screen(None, frame_file=args.frame)
        lines = [f"Get:{i} http://archive.raspberrypi.com/debian trixie/main arm64 package-{i} [{i * 13} kB]" for i in range(40)]
        state = json.loads(args.frame_state)
        s.draw(frame(s, lines, state, options=json.loads(args.defaults) if args.options else None,
                     cursor=len(OPTIONS), countdown=AUTOSTART if args.options else None))
        return

    screen = Screen(args.tty)
    try:
        options = ask_options(screen, args, json.loads(args.defaults))
        tmp = args.choices + ".tmp"
        with open(tmp, "w") as f:
            json.dump(options, f)
        os.replace(tmp, args.choices)

        eta = Eta()
        while True:
            state = read_json(args.state)
            pct = float(state.get("pct") or 0)
            state["eta"] = eta.update(pct)
            screen.draw(frame(screen, tail(args.log), state))
            if state.get("done") or state.get("error"):
                time.sleep(30 if state.get("error") else 8)
                break
            time.sleep(0.25)
    finally:
        screen.close()


if __name__ == "__main__":
    main()
