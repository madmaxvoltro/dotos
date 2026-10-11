#!/bin/sh
# Started by sway as the kiosk user: shares the session environment with dotd
# (so FreeRDP and the clock windows open here), waits for dotd, then keeps the UI up.
set -u

env | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XDG_RUNTIME_DIR|SWAYSOCK|XKB_DEFAULT_LAYOUT)=' > "$XDG_RUNTIME_DIR/dot-session.env" || true

i=0
until curl -fs -o /dev/null http://127.0.0.1:7777/api/state || [ $i -ge 60 ]; do
  i=$((i + 1))
  sleep 0.5
done

BROWSER=$(command -v chromium || command -v chromium-browser)

# if the browser ever exits, bring it straight back
while true; do
  "$BROWSER" \
    --app=http://127.0.0.1:7777/ \
    --kiosk \
    --ozone-platform=wayland \
    --user-data-dir=/var/lib/dot/chromium \
    --password-store=basic \
    --no-first-run \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=Translate,TranslateUI,OverscrollHistoryNavigation \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required
  sleep 1
done
