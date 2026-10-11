#!/usr/bin/env bash
# Dot installer: turns a fresh Raspberry Pi OS Lite (64-bit) into a Dot thin client.
#
#   curl -fsSL https://raw.githubusercontent.com/madmaxvoltro/dotos/main/install.sh | sudo bash
#
# Options (after `sudo bash -s --`):
#   --tailscale     also install Tailscale (for servers on other networks)
#   --no-reboot     don't reboot at the end
#   --no-splash     keep the normal text boot instead of the Dot splash
#   --branch NAME   install from another branch (default: main)
#   --update        re-install the code only; keeps settings, skips the reboot
set -euo pipefail

REPO="${DOT_REPO:-madmaxvoltro/dotos}"
BRANCH="main"
TAILSCALE=0
REBOOT=1
SPLASH=1
UPDATE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tailscale) TAILSCALE=1 ;;
    --no-reboot) REBOOT=0 ;;
    --no-splash) SPLASH=0 ;;
    --branch) BRANCH="$2"; shift ;;
    --update) UPDATE=1; REBOOT=0 ;;
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

if [ "$UPDATE" -eq 0 ] && systemctl is-active -q display-manager 2>/dev/null; then
  die "This looks like the desktop edition of Raspberry Pi OS. Flash Raspberry Pi OS Lite (64-bit) instead."
fi

# packages ------------------------------------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive
apt_has() { apt-cache show "$1" >/dev/null 2>&1; }

if [ "$UPDATE" -eq 0 ]; then
  step "Installing packages (this takes a few minutes)"
  apt-get update -q
  PKGS="sway python3 curl ca-certificates network-manager xwayland wlr-randr plymouth plymouth-themes fonts-dejavu-core"
  # monitor control (DDC/CI + HDMI-CEC) and a mixer we can mute (HDMI audio on the Pi has none)
  PKGS="$PKGS ddcutil v4l-utils pipewire wireplumber pipewire-pulse pipewire-alsa pulseaudio-utils"
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
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $PKGS

  if [ "$TAILSCALE" -eq 1 ] && ! command -v tailscale >/dev/null; then
    step "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
fi

# code ------------------------------------------------------------------------------------------------

step "Installing Dot"
SRC=""
SCRIPT_DIR=""
# only when run as a file from a checkout (not when piped from curl)
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi
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
chmod +x "$PREFIX/system/session.sh" "$PREFIX/system/clock.sh"
cp "$SRC/install.sh" "$SRC/uninstall.sh" "$PREFIX/"
chmod +x "$PREFIX/install.sh" "$PREFIX/uninstall.sh"
[ -f "$SRC/VERSION" ] && cp "$SRC/VERSION" "$PREFIX/VERSION"

cat > "$PREFIX/update.sh" <<EOF
#!/bin/sh
# Started from Settings > About > Update Dot.
curl -fsSL "https://raw.githubusercontent.com/$REPO/$BRANCH/install.sh" | DOT_REPO="$REPO" bash -s -- --update --branch "$BRANCH"
EOF
chmod +x "$PREFIX/update.sh"

mkdir -p "$PREFIX/ui/fonts"
for f in geist-sans/Geist-Variable.woff2 geist-mono/GeistMono-Variable.woff2; do
  out="$PREFIX/ui/fonts/$(basename "$f")"
  [ -s "$out" ] || curl -fsSL -o "$out" "https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/$f" || info "font $f not downloaded, using a fallback"
done

# kiosk user, folders, services --------------------------------------------------------------------------

step "Setting up the kiosk"
if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /usr/sbin/nologin "$KIOSK_USER"
fi
for g in video render input audio plugdev netdev; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$KIOSK_USER"
done

# DDC/CI talks to the monitor over the HDMI cable's I2C lines
echo i2c-dev > /etc/modules-load.d/dot.conf
modprobe i2c-dev 2>/dev/null || true

install -d -m 700 /etc/dot
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" /var/lib/dot /var/lib/dot/chromium /var/lib/dot/chromium-clock /media/dot
[ -f /var/lib/dot/sway-outputs.conf ] || : > /var/lib/dot/sway-outputs.conf
[ -f /etc/dot/kiosk.env ] || printf 'XKB_DEFAULT_LAYOUT=us\nDOT_SCALE=1.00\n' > /etc/dot/kiosk.env

install -m 644 "$PREFIX/system/pam-dot-kiosk" /etc/pam.d/dot-kiosk
install -m 644 "$PREFIX/system/dotd.service" /etc/systemd/system/dotd.service
install -m 644 "$PREFIX/system/dot-kiosk.service" /etc/systemd/system/dot-kiosk.service
install -m 644 "$PREFIX/system/99-dot-usb.rules" /etc/udev/rules.d/99-dot-usb.rules
udevadm control --reload 2>/dev/null || true

systemctl daemon-reload
systemctl enable -q NetworkManager dotd.service dot-kiosk.service
systemctl disable -q getty@tty1.service 2>/dev/null || true
systemctl set-default -q graphical.target

# boot splash ----------------------------------------------------------------------------------------------

if [ "$SPLASH" -eq 1 ] && [ "$UPDATE" -eq 0 ]; then
  step "Installing the boot splash"
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

if [ "$UPDATE" -eq 1 ]; then
  systemctl restart dotd.service dot-kiosk.service
  step "Dot is up to date"
  exit 0
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<EOF

  ${bold}●  Dot is installed.${reset}
  After the restart, the screen connected to this Pi walks you through setup:
  network, your Windows server and who signs in.
  ${dim}Logs: journalctl -u dotd -u dot-kiosk -f   ·   Address: ${IP:-unknown}${reset}

EOF

if [ "$REBOOT" -eq 1 ]; then
  info "Restarting in 5 seconds (Ctrl+C to stay)…"
  sleep 5
  systemctl reboot
fi
