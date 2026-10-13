#!/usr/bin/env bash
# Dot installer: turns a fresh Raspberry Pi OS Lite (64-bit) into a Dot thin client.
#
#   curl -fsSL https://raw.githubusercontent.com/madmaxvoltro/dotos/main/install.sh | sudo bash
#
# Run it again on a Dot to reinstall: everything is installed again, but your servers, people,
# Wi-Fi, Tailscale and settings are kept, and the options you chose last time are the defaults.
#
# Options (after `sudo bash -s --`):
#   --tailscale     also install Tailscale (for servers on other networks)
#   --no-games      skip Moonlight (game mode)
#   --no-reboot     don't reboot at the end
#   --no-splash     keep the normal text boot instead of the Dot splash
#   --branch NAME   install from another branch (default: main)
#   --update        update the code only (no packages, no reboot); keeps everything
#   --gui           show it on the Pi's own screen: the options as a menu (keyboard), then
#                   progress, time left and the log. Works with --update too.
#   --setup         after a reinstall, walk through the on-screen setup again (prefilled)
set -euo pipefail

REPO="${DOT_REPO:-madmaxvoltro/dotos}"
BRANCH="main"
TAILSCALE=0
GAMES=1
REBOOT=1
SPLASH=1
UPDATE=0
GUI=0
PROGRESS=0  # write progress for the Dot UI (Settings > About > Update) without drawing the console
SETUP_AGAIN=0

# what was chosen last time, so a reinstall starts from there (flags below still win)
if [ -f /etc/dot/install.conf ]; then
  # shellcheck disable=SC1091
  . /etc/dot/install.conf
elif command -v tailscale >/dev/null 2>&1; then
  TAILSCALE=1
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --tailscale) TAILSCALE=1 ;;
    --no-games) GAMES=0 ;;
    --no-reboot) REBOOT=0 ;;
    --no-splash) SPLASH=0 ;;
    --branch) BRANCH="$2"; shift ;;
    --update) UPDATE=1; REBOOT=0 ;;
    --gui) GUI=1 ;;
    --progress) PROGRESS=1 ;;
    --setup) SETUP_AGAIN=1 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PREFIX=/usr/lib/dot
KIOSK_USER=dot

bold=$(tput bold 2>/dev/null || true); dim=$(tput dim 2>/dev/null || true); reset=$(tput sgr0 2>/dev/null || true)
step() { printf '\n%s●%s %s\n' "$bold" "$reset" "$*"; }
info() { printf '  %s%s%s\n' "$dim" "$*" "$reset"; }
die()  { printf '\n%s✕ %s%s\n' "$bold" "$*" "$reset" >&2; exit 1; }

# install, reinstall or update -------------------------------------------------------------------------

INSTALLED_VERSION=""
if [ -f "$PREFIX/VERSION" ] && [ -f /etc/systemd/system/dotd.service ]; then
  INSTALLED_VERSION=$(cat "$PREFIX/VERSION")
fi
if [ "$UPDATE" -eq 1 ]; then MODE=update
elif [ -n "$INSTALLED_VERSION" ]; then MODE=reinstall
else MODE=install
fi

GUI_DIR=/run/dot-install
STATE=0
{ [ "$GUI" -eq 1 ] || [ "$PROGRESS" -eq 1 ]; } && STATE=1
CURRENT_STEP="Getting ready"

# progress as a little JSON file: the console installer (--gui) and the Dot UI both read it
gui_state() {  # gui_state PCT LABEL [extra json]
  [ "$STATE" -eq 1 ] || return 0
  local pct=$1 label=${2//\\/}; label=${label//\"/}
  if [ "$MODE" = update ] && [ "${3:-}" = "" ]; then
    # an update only runs the last part of the install (from 78%): stretch it to 0-100
    pct=$(awk "BEGIN{p=($pct - 78) * 100 / 22; printf \"%.1f\", (p < 0 ? 0 : p)}")
  fi
  printf '{"pct": %s, "step": "%s", "mode": "%s"%s}\n' "$pct" "$label" "$MODE" "${3:-}" > "$GUI_DIR/state.tmp" \
    && mv "$GUI_DIR/state.tmp" "$GUI_DIR/state.json"
}
progress() { CURRENT_STEP=$2; gui_state "$1" "$2"; }

# apt with real percentages: downloading is the first half of a step, unpacking the second
apt_get() {  # apt_get FROM TO apt-get-arguments...
  local from=$1 to=$2; shift 2
  if [ "$STATE" -ne 1 ]; then apt-get "$@"; return; fi
  apt-get -o APT::Status-Fd=3 "$@" 3> >(while IFS=: read -r kind _ pct msg; do
    [[ "$pct" =~ ^[0-9.]+$ ]] || continue
    case "$kind" in
      dlstatus) gui_state "$(awk "BEGIN{printf \"%.1f\", $from + ($to - $from) * 0.5 * $pct / 100}")" "Downloading: ${msg:-packages}" ;;
      pmstatus) gui_state "$(awk "BEGIN{printf \"%.1f\", $from + ($to - $from) * (0.5 + 0.5 * $pct / 100)}")" "${msg:-Installing}" ;;
    esac
  done)
}

GUI_TTY=1
gui_done() {  # hand the screen back: to the kiosk if it runs, otherwise to the login prompt
  [ "$GUI" -eq 1 ] || return 0
  if [ "$GUI_TTY" -ne 1 ]; then
    chvt 1 2>/dev/null || true
    systemctl unmask --runtime "getty@tty$GUI_TTY.service" >/dev/null 2>&1 || true
  fi
}

on_error() {
  gui_state 0 "$CURRENT_STEP" ", \"error\": \"Stopped while: $CURRENT_STEP\""
  # give the screen back once the error has been shown
  if [ "$GUI" -eq 1 ]; then
    (sleep 32; gui_done; [ "$GUI_TTY" -eq 1 ] && systemctl start getty@tty1 2>/dev/null) >/dev/null 2>&1 &
  fi
}
trap on_error ERR

SCRIPT_DIR=""
# only when run as a file from a checkout (not when piped from curl)
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi

# checks ----------------------------------------------------------------------------------------

[ "$(id -u)" -eq 0 ] || die "Run as root: curl -fsSL … | sudo bash"
[ "$(uname -m)" = "aarch64" ] || die "Dot needs the 64-bit Raspberry Pi OS (found $(uname -m))."
. /etc/os-release
case "${VERSION_CODENAME:-}" in
  trixie|bookworm) ;;
  *) die "Dot supports Raspberry Pi OS based on Debian 12 (bookworm) or 13 (trixie); found ${PRETTY_NAME:-unknown}." ;;
esac
MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "unknown device")

cat <<EOF

  ${bold}●  Dot${reset}
  ${dim}${MODEL} · ${PRETTY_NAME}${reset}
EOF
case "$MODE" in
  reinstall) info "Dot $INSTALLED_VERSION is installed: reinstalling. Your servers, people and settings are kept." ;;
  update) info "Updating Dot${INSTALLED_VERSION:+ $INSTALLED_VERSION}. Your settings are kept." ;;
esac

if [ "$MODE" = install ] && systemctl is-active -q display-manager 2>/dev/null; then
  die "This looks like the desktop edition of Raspberry Pi OS. Flash Raspberry Pi OS Lite (64-bit) instead."
fi

if [ "$STATE" -eq 1 ]; then
  mkdir -p "$GUI_DIR"
  : > "$GUI_DIR/log.txt"
  rm -f "$GUI_DIR/choices.json"
  gui_state 0 "Getting ready" ", \"pct_raw\": 0"
fi

# --gui: take over a console on the Pi's screen ---------------------------------------------------------

if [ "$GUI" -eq 1 ]; then
  if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/system/install-gui.py" ]; then
    cp "$SCRIPT_DIR/system/install-gui.py" "$GUI_DIR/"
  else
    curl -fsSL "https://raw.githubusercontent.com/$REPO/$BRANCH/system/install-gui.py" -o "$GUI_DIR/install-gui.py"
  fi
  if systemctl is-active -q dot-kiosk.service; then
    # Dot is on tty1: draw on tty2 (without its login prompt), switch back when done
    GUI_TTY=2
    systemctl mask --runtime getty@tty2.service >/dev/null 2>&1 || true
    systemctl stop getty@tty2.service 2>/dev/null || true
  else
    systemctl stop getty@tty1.service 2>/dev/null || true
  fi
  chvt "$GUI_TTY" 2>/dev/null || true
  for font in Uni3-TerminusBold32x16 Lat15-TerminusBold32x16 Uni3-TerminusBold28x14 Lat15-TerminusBold28x14; do
    f=/usr/share/consolefonts/$font.psf.gz
    if [ -f "$f" ] && setfont -C "/dev/tty$GUI_TTY" "$f" 2>/dev/null; then break; fi
  done
  tf() { [ "$1" -eq 1 ] && echo true || echo false; }
  defaults=$(printf '{"games": %s, "tailscale": %s, "splash": %s, "setup": %s}' \
    "$(tf "$GAMES")" "$(tf "$TAILSCALE")" "$(tf "$SPLASH")" "$(tf "$([ "$MODE" = reinstall ] && echo 1 || echo "$SETUP_AGAIN")")")
  python3 "$GUI_DIR/install-gui.py" --tty "/dev/tty$GUI_TTY" --mode "$MODE" --version "$INSTALLED_VERSION" \
    --state "$GUI_DIR/state.json" --log "$GUI_DIR/log.txt" --choices "$GUI_DIR/choices.json" --defaults "$defaults" &
  GUI_PID=$!
  if [ "$MODE" != update ]; then
    step "Choose the options on the Pi's screen"
    info "(arrow keys, Space and Enter on the Pi's keyboard; it starts by itself after 90 seconds)"
    while [ ! -f "$GUI_DIR/choices.json" ] && kill -0 "$GUI_PID" 2>/dev/null; do sleep 0.5; done
    if [ -f "$GUI_DIR/choices.json" ]; then
      pick() { python3 -c "import json,sys; print(1 if json.load(open(sys.argv[1])).get(sys.argv[2]) else 0)" "$GUI_DIR/choices.json" "$1"; }
      GAMES=$(pick games); TAILSCALE=$(pick tailscale); SPLASH=$(pick splash)
      [ "$MODE" = reinstall ] && SETUP_AGAIN=$(pick setup)
      info "game mode: $GAMES   tailscale: $TAILSCALE   boot splash: $SPLASH${SETUP_AGAIN:+   setup again: $SETUP_AGAIN}"
    else
      info "the on-screen installer stopped; continuing here with the options given"
      GUI=0
    fi
  fi
fi

# everything from here also goes to the log the screens show
[ "$STATE" -eq 1 ] && exec > >(tee -a "$GUI_DIR/log.txt") 2>&1

# packages ------------------------------------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive
apt_has() { apt-cache show "$1" >/dev/null 2>&1; }

if [ "$UPDATE" -eq 0 ]; then
  step "Installing packages (this takes a few minutes)"
  progress 2 "Updating the package lists"
  apt_get 2 6 update -q
fi
  PKGS="sway python3 curl ca-certificates network-manager xwayland wlr-randr plymouth plymouth-themes fonts-dejavu-core"
  # monitor control (DDC/CI + HDMI-CEC) and a mixer we can mute (HDMI audio on the Pi has none)
  PKGS="$PKGS ddcutil v4l-utils pipewire wireplumber pipewire-pulse pipewire-alsa pulseaudio-utils"
  # Bluetooth keyboards, mice, headsets (PipeWire's Bluetooth audio) and game controllers
  PKGS="$PKGS bluez libspa-0.2-bluetooth"
  # mirror mode for a second monitor
  if apt_has wl-mirror; then PKGS="$PKGS wl-mirror"; else info "wl-mirror not available: mirror mode disabled"; fi
  if apt_has chromium; then PKGS="$PKGS chromium"; else PKGS="$PKGS chromium-browser"; fi
  if apt_has freerdp3-sdl; then
    PKGS="$PKGS freerdp3-sdl freerdp3-x11"; info "FreeRDP 3 (SDL, Wayland native)"
  elif apt_has freerdp3-wayland; then
    PKGS="$PKGS freerdp3-wayland freerdp3-x11"; info "FreeRDP 3 (Wayland)"
  else
    PKGS="$PKGS freerdp2-wayland freerdp2-x11"; info "FreeRDP 2. Passwords go on the FreeRDP command line; upgrade to Trixie for FreeRDP 3."
  fi
if [ "$UPDATE" -eq 1 ]; then
  # newer Dot versions can need packages an older install doesn't have (Bluetooth, for one)
  MISSING=""
  for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
  if [ -n "$MISSING" ]; then
    step "Adding packages this version needs:$MISSING"
    apt-get update -q
    # shellcheck disable=SC2086
    apt_get 6 62 install -y -q --no-install-recommends $MISSING
  fi
else
  progress 6 "Installing packages"
  # shellcheck disable=SC2086
  apt_get 6 62 install -y -q --no-install-recommends $PKGS

  if [ "$GAMES" -eq 1 ] && ! command -v moonlight-qt >/dev/null; then
    step "Installing Moonlight (game mode)"
    progress 62 "Installing Moonlight"
    if ! apt_has moonlight-qt; then
      # Moonlight's own repository has builds for Raspberry Pi OS
      curl -1sLf https://dl.cloudsmith.io/public/moonlight-game-streaming/moonlight-qt/setup.deb.sh \
        | distro=raspbian codename="$VERSION_CODENAME" bash >/dev/null 2>&1 || true
    fi
    apt_get 62 72 install -y -q moonlight-qt || info "Moonlight isn't available for this OS yet: game mode is off"
  fi

  if [ "$TAILSCALE" -eq 1 ] && ! command -v tailscale >/dev/null; then
    step "Installing Tailscale"
    progress 72 "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
fi

# code ------------------------------------------------------------------------------------------------

step "Installing Dot"
progress 78 "Installing Dot"
SRC=""
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/dotd" ] && [ -d "$SCRIPT_DIR/ui" ]; then
  SRC="$SCRIPT_DIR"; info "from local copy $SRC"
else
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  info "from github.com/$REPO ($BRANCH)"
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$TMP" --strip-components=1 \
    || die "Couldn't download github.com/$REPO. Check the repository name (set DOT_REPO=user/repo)."
  SRC="$TMP"
fi

mkdir -p "$PREFIX"
rm -rf "$PREFIX/dotd" "$PREFIX/system"
cp -r "$SRC/dotd" "$SRC/system" "$PREFIX/"
# keep downloaded fonts across updates
mkdir -p "$PREFIX/ui"
find "$PREFIX/ui" -mindepth 1 -maxdepth 1 ! -name fonts -exec rm -rf {} +
cp -r "$SRC/ui/." "$PREFIX/ui/"
chmod +x "$PREFIX/system/session.sh" "$PREFIX/system/clock.sh" "$PREFIX/system/side.sh"
install -m 755 "$PREFIX/system/dot" /usr/local/bin/dot  # sudo dot diag / test-standby / ddc / logs / update
cp "$SRC/install.sh" "$SRC/uninstall.sh" "$PREFIX/"
chmod +x "$PREFIX/install.sh" "$PREFIX/uninstall.sh"
[ -f "$SRC/VERSION" ] && cp "$SRC/VERSION" "$PREFIX/VERSION"

cat > "$PREFIX/update.sh" <<EOF
#!/bin/sh
# Started from Settings > About > Update Dot.
curl -fsSL "https://raw.githubusercontent.com/$REPO/$BRANCH/install.sh" | DOT_REPO="$REPO" bash -s -- --update --progress --branch "$BRANCH"
EOF
chmod +x "$PREFIX/update.sh"

progress 84 "Downloading fonts"
mkdir -p "$PREFIX/ui/fonts"
for f in geist-sans/Geist-Variable.woff2 geist-mono/GeistMono-Variable.woff2; do
  out="$PREFIX/ui/fonts/$(basename "$f")"
  [ -s "$out" ] || curl -fsSL -o "$out" "https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/$f" || info "font $f not downloaded, using a fallback"
done

# kiosk user, folders, services --------------------------------------------------------------------------

step "Setting up the kiosk"
progress 86 "Setting up the kiosk"
if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /usr/sbin/nologin "$KIOSK_USER"
fi
for g in video render input audio plugdev netdev bluetooth; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$KIOSK_USER"
done

# DDC/CI talks to the monitor over the HDMI cable's I2C lines
echo i2c-dev > /etc/modules-load.d/dot.conf
modprobe i2c-dev 2>/dev/null || true

install -d -m 700 /etc/dot
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" /var/lib/dot /var/lib/dot/chromium /var/lib/dot/chromium-clock \
  /var/lib/dot/chromium-browser /var/lib/dot/chromium-panel /media/dot
# \\tsclient\dot inside Windows: how Game.exe talks to Dot (recreated at every boot, /run is in memory)
printf 'd /run/dot 0755 root root -\nd /run/dot/inbox 0770 %s %s -\n' "$KIOSK_USER" "$KIOSK_USER" > /etc/tmpfiles.d/dot.conf
systemd-tmpfiles --create /etc/tmpfiles.d/dot.conf
[ -f /var/lib/dot/sway-outputs.conf ] || : > /var/lib/dot/sway-outputs.conf
if [ ! -f /var/lib/dot/sway-input.conf ]; then
  layout=$(sed -n 's/^XKB_DEFAULT_LAYOUT=//p' /etc/dot/kiosk.env 2>/dev/null); layout=${layout:-us}
  echo "input type:keyboard xkb_layout $layout" > /var/lib/dot/sway-input.conf
fi
[ -f /etc/dot/kiosk.env ] || printf 'XKB_DEFAULT_LAYOUT=us\nDOT_SCALE=1.00\n' > /etc/dot/kiosk.env

# Dot's screen is a local page in Chromium: no password manager, autofill, translate or sign-in prompts
for dir in /etc/chromium/policies/managed /etc/chromium-browser/policies/managed; do
  install -D -m 644 "$PREFIX/system/chromium-policy.json" "$dir/dot.json"
done
# and forget anything an earlier version let Chromium save
rm -f /var/lib/dot/chromium*/Default/"Login Data"* /var/lib/dot/chromium*/Default/"Web Data"* 2>/dev/null || true

install -m 644 "$PREFIX/system/pam-dot-kiosk" /etc/pam.d/dot-kiosk
install -m 644 "$PREFIX/system/dotd.service" /etc/systemd/system/dotd.service
install -m 644 "$PREFIX/system/dot-kiosk.service" /etc/systemd/system/dot-kiosk.service
install -m 644 "$PREFIX/system/99-dot-usb.rules" /etc/udev/rules.d/99-dot-usb.rules
udevadm control --reload 2>/dev/null || true

systemctl daemon-reload
systemctl enable -q NetworkManager dotd.service dot-kiosk.service
systemctl enable -q bluetooth.service 2>/dev/null || true
systemctl disable -q getty@tty1.service 2>/dev/null || true
systemctl set-default -q graphical.target

# boot splash ----------------------------------------------------------------------------------------------

if [ "$SPLASH" -eq 1 ] && [ "$UPDATE" -eq 0 ]; then
  step "Installing the boot splash"
  progress 90 "Installing the boot splash"
  install -d /usr/share/plymouth/themes/dot
  install -m 644 "$PREFIX/system/plymouth/"* /usr/share/plymouth/themes/dot/
  plymouth-set-default-theme dot >/dev/null 2>&1 || true
  command -v update-initramfs >/dev/null && update-initramfs -u >/dev/null 2>&1 || true

  CMDLINE=/boot/firmware/cmdline.txt; [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
  CONFIG=/boot/firmware/config.txt; [ -f "$CONFIG" ] || CONFIG=/boot/config.txt
  if [ -f "$CMDLINE" ]; then
    cp -n "$CMDLINE" "$CMDLINE.before-dot" || true
    for opt in quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 consoleblank=0 loglevel=3; do
      grep -qw -- "$opt" "$CMDLINE" || sed -i "1 s|\$| $opt|" "$CMDLINE"
    done
  fi
  if [ -f "$CONFIG" ] && ! grep -q '^disable_splash=1' "$CONFIG"; then
    cp -n "$CONFIG" "$CONFIG.before-dot" || true
    printf '\n# Dot\ndisable_splash=1\n' >> "$CONFIG"
  fi
fi

# done -------------------------------------------------------------------------------------------------------

trap - ERR

if [ "$UPDATE" -eq 1 ]; then
  progress 96 "Restarting Dot"
  gui_state 100 "Restarting the screen" ', "done": true'
  step "Dot is up to date"
  sleep 3  # let the screens show it
  gui_done
  systemctl restart dotd.service
  # never restart the screen session itself: logind may not hand it the keyboard again until a reboot.
  # Reload sway's config in place and restart just the browsers (session.sh brings the main one back).
  if systemctl is-active -q dot-kiosk.service; then
    uid=$(id -u "$KIOSK_USER")
    sock=$(sed -n 's/^SWAYSOCK=//p' "/run/user/$uid/dot-session.env" 2>/dev/null)
    if [ -n "$sock" ]; then
      runuser -u "$KIOSK_USER" -- env SWAYSOCK="$sock" XDG_RUNTIME_DIR="/run/user/$uid" swaymsg reload >/dev/null 2>&1 || true
    fi
    pkill -u "$KIOSK_USER" -f -- "--user-data-dir=/var/lib/dot/chromium( |\$)" || true
  else
    systemctl start dot-kiosk.service
  fi
  exit 0
fi

# remember the choices: the next reinstall starts from them
printf 'GAMES=%s
TAILSCALE=%s
SPLASH=%s
' "$GAMES" "$TAILSCALE" "$SPLASH" > /etc/dot/install.conf

if [ "$MODE" = reinstall ] && [ "$SETUP_AGAIN" -eq 1 ] && [ -f /etc/dot/config.json ]; then
  # back to the on-screen setup, with every server, person and setting already filled in
  python3 - /etc/dot/config.json <<'PY'
import json, os, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
cfg["setupComplete"] = False
with open(path + ".tmp", "w") as f:
    json.dump(cfg, f, indent=2)
os.chmod(path + ".tmp", 0o600)
os.replace(path + ".tmp", path)
PY
  info "setup will run again after the restart, with your settings filled in"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ "$MODE" = reinstall ]; then
  title="Dot is reinstalled."
  next=$([ "$SETUP_AGAIN" -eq 1 ] && echo "After the restart, setup runs again with everything you set up already filled in."     || echo "After the restart, Dot is back at the login screen with all your settings.")
else
  title="Dot is installed."
  next="After the restart, the screen connected to this Pi walks you through setup:
  network, your Windows server and who signs in."
fi
cat <<EOF

  ${bold}●  $title${reset}
  $next
  ${dim}Logs: journalctl -u dotd -u dot-kiosk -f   ·   Address: ${IP:-unknown}${reset}

EOF

gui_state 100 "$([ "$REBOOT" -eq 1 ] && echo "Restarting in a few seconds" || echo "Restart the Pi to start Dot")" ', "done": true'
if [ "$REBOOT" -eq 1 ]; then
  info "Restarting in 5 seconds (Ctrl+C to stay)…"
  sleep 5
  systemctl reboot
else
  sleep 3
  gui_done
fi
