#!/bin/sh
# The idle clock for a second monitor; dotd moves the window to the right output.
BROWSER=$(command -v chromium || command -v chromium-browser)
exec "$BROWSER" --app=http://127.0.0.1:7777/clock.html --user-data-dir=/var/lib/dot/chromium-clock \
  --ozone-platform=wayland --no-first-run --noerrdialogs --disable-infobars --password-store=basic
