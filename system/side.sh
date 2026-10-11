#!/bin/sh
# Game mode's second monitor: a browser or the game panel, full screen on the focused output.
#   side.sh browser https://www.youtube.com
#   side.sh panel   http://127.0.0.1:7777/panel.html
KIND=$1
URL=$2
BROWSER=$(command -v chromium || command -v chromium-browser)
case "$KIND" in
  panel) exec "$BROWSER" --app="$URL" --kiosk --user-data-dir=/var/lib/dot/chromium-panel \
           --ozone-platform=wayland --no-first-run --noerrdialogs --password-store=basic ;;
  *)     exec "$BROWSER" --kiosk "$URL" --user-data-dir=/var/lib/dot/chromium-browser \
           --ozone-platform=wayland --no-first-run --noerrdialogs --password-store=basic ;;
esac
