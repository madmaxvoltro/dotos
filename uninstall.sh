#!/usr/bin/env bash
# Removes Dot and returns the Pi to a normal console login. Packages stay installed.
#   sudo /usr/lib/dot/uninstall.sh            keep settings in /etc/dot
#   sudo /usr/lib/dot/uninstall.sh --purge    remove settings too
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }

systemctl disable --now -q dot-kiosk.service dotd.service 2>/dev/null || true
rm -f /etc/systemd/system/dot-kiosk.service /etc/systemd/system/dotd.service /etc/pam.d/dot-kiosk /etc/udev/rules.d/99-dot-usb.rules /etc/modules-load.d/dot.conf /etc/tmpfiles.d/dot.conf
systemctl daemon-reload
systemctl enable -q getty@tty1.service
systemctl set-default -q multi-user.target

for f in /boot/firmware/cmdline.txt /boot/cmdline.txt /boot/firmware/config.txt /boot/config.txt; do
  [ -f "$f.before-dot" ] && mv "$f.before-dot" "$f"
done
plymouth-set-default-theme -R bgrt >/dev/null 2>&1 || true
rm -rf /usr/share/plymouth/themes/dot /usr/lib/dot /var/lib/dot

if [ "${1:-}" = "--purge" ]; then
  rm -rf /etc/dot
  userdel -r dot 2>/dev/null || true
fi
echo "Dot removed. Reboot to get the normal console back."
