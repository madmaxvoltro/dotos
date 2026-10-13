#!/usr/bin/env bash
# Builds FreeRDP with Dot's patch for the Raspberry Pi's hardware H.264 decoder (h264-v4l2m2m.patch)
# into /opt/dot/freerdp. Run through `sudo dot hwdecode`; about 45 minutes on a Pi 4.
#
# Only the X11 client is built. Dot uses it when Settings > Connection > Hardware video decoding is on,
# and sets FREERDP_H264_V4L2M2M=1 for it. The system's own FreeRDP stays installed and untouched.
#
#   build.sh           build, unless this version and patch are already built
#   build.sh --force   build again anyway
#   build.sh --remove  remove the build
set -euo pipefail

VERSION=3.15.0  # Debian Trixie's FreeRDP, the one Dot already runs
HERE=$(cd "$(dirname "$0")" && pwd)
DEST=/opt/dot/freerdp
WORK=/var/cache/dot/freerdp-build
STAMP="$VERSION+$(sha256sum "$HERE/h264-v4l2m2m.patch" | cut -c1-12)"

step() { printf '\n\033[1m● %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo dot hwdecode"; exit 1; }

if [ "${1:-}" = --remove ]; then
  rm -rf "$DEST" "$WORK"
  echo "Removed. Dot uses the system's FreeRDP again."
  exit 0
fi
if [ "${1:-}" != --force ] && [ "$(cat "$DEST/STAMP" 2>/dev/null)" = "$STAMP" ]; then
  echo "Already built ($STAMP). Rebuild with: sudo dot hwdecode --force"
  exit 0
fi

step "Build tools and libraries"
apt-get install -y --no-install-recommends \
  git build-essential cmake ninja-build pkg-config \
  libssl-dev zlib1g-dev libcjson-dev libicu-dev \
  libx11-dev libxext-dev libxinerama-dev libxcursor-dev libxdamage-dev libxv-dev libxkbfile-dev \
  libxrandr-dev libxi-dev libxrender-dev libxfixes-dev libxtst-dev libxkbcommon-dev \
  libpulse-dev libavcodec-dev libavutil-dev libswscale-dev libswresample-dev

step "FreeRDP $VERSION, with Dot's patch"
rm -rf "$WORK"
mkdir -p "$WORK"
git clone --quiet --depth 1 --branch "$VERSION" https://github.com/FreeRDP/FreeRDP.git "$WORK/src"
patch -d "$WORK/src" -p1 < "$HERE/h264-v4l2m2m.patch"

step "Configure"
cmake -S "$WORK/src" -B "$WORK/build" -G Ninja -Wno-dev \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$DEST" -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_RPATH="$DEST/lib" \
  -DWITH_CLIENT=ON -DWITH_X11=ON -DWITH_WAYLAND=OFF \
  -DWITH_CLIENT_SDL=OFF -DWITH_CLIENT_SDL2=OFF -DWITH_CLIENT_SDL3=OFF \
  -DWITH_SERVER=OFF -DWITH_SHADOW=OFF -DWITH_PROXY=OFF -DWITH_PLATFORM_SERVER=OFF -DWITH_SAMPLE=OFF \
  -DWITH_SERVER_CHANNELS=OFF -DWITH_WINPR_TOOLS=OFF -DWITH_MANPAGES=OFF -DBUILD_TESTING=OFF \
  -DWITH_FFMPEG=ON -DWITH_DSP_FFMPEG=ON -DWITH_SWSCALE=ON -DWITH_VAAPI=OFF -DWITH_OPENH264=OFF \
  -DWITH_PULSE=ON -DWITH_ALSA=OFF -DWITH_OSS=OFF \
  -DWITH_CUPS=OFF -DWITH_PCSC=OFF -DWITH_PKCS11=OFF -DWITH_KRB5=OFF -DWITH_FUSE=OFF \
  -DWITH_WEBVIEW=OFF -DCHANNEL_URBDRC=OFF \
  -DWITH_INTERNAL_RC4=ON -DWITH_INTERNAL_MD4=ON -DWITH_INTERNAL_MD5=ON

step "Compile (about 45 minutes on a Pi 4; the screen keeps working)"
nice -n 10 ninja -C "$WORK/build"

step "Install to $DEST"
rm -rf "$DEST"
cmake --install "$WORK/build" > /dev/null
echo "$STAMP" > "$DEST/STAMP"
rm -rf "$WORK"
"$DEST/bin/xfreerdp" --version | head -1

echo
echo "Done. Turn it on in Settings > Connection > Hardware video decoding, then sign in again."
