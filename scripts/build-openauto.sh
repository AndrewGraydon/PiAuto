#!/bin/bash
# build-openauto.sh — Build and install aasdk + openauto on Raspberry Pi 4 (Debian 13 Trixie)
#
# Run from the root of the PiAuto repo after cloning with --recurse-submodules:
#   git clone --recurse-submodules https://github.com/AndrewGraydon/PiAuto.git
#   cd PiAuto
#   bash scripts/build-openauto.sh
#
# Requires: ~20 min build time on Pi 4B, ~1 GB free disk space
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AASDK_DIR="$REPO_ROOT/aasdk"
OPENAUTO_DIR="$REPO_ROOT/openauto"
BUILD_INFO_FILE="/data/build-info.txt"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[build]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
die()  { echo -e "${RED}[error]${NC} $*"; exit 1; }

# ── 1. Pre-flight checks ──────────────────────────────────────────────────────

log "Checking environment..."

[ "$(uname -m)" = "aarch64" ] || die "This script must run on a 64-bit Raspberry Pi (aarch64)."
[ -d "$AASDK_DIR/.git" ] || die "aasdk submodule not found. Did you clone with --recurse-submodules?"
[ -d "$OPENAUTO_DIR/.git" ] || die "openauto submodule not found. Did you clone with --recurse-submodules?"

AASDK_COMMIT=$(git -C "$AASDK_DIR" rev-parse --short HEAD)
OPENAUTO_COMMIT=$(git -C "$OPENAUTO_DIR" rev-parse --short HEAD)
log "aasdk:    $AASDK_COMMIT"
log "openauto: $OPENAUTO_COMMIT"

# ── 2. Install build dependencies ────────────────────────────────────────────

log "Installing build dependencies..."
sudo apt-get update -qq
sudo apt-get install -y \
    cmake \
    build-essential \
    git \
    protobuf-compiler \
    libprotobuf-dev \
    libssl-dev \
    libboost-all-dev \
    libusb-1.0-0-dev \
    qtmultimedia5-dev \
    qtconnectivity5-dev \
    libqt5websockets5-dev \
    libqt5svg5-dev \
    librtaudio-dev \
    libtag-dev \
    libgps-dev \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-bad1.0-dev \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    pkg-config

# ── 3. Build h264bitstream (not packaged in Debian 13) ───────────────────────

if [ ! -f /usr/local/lib/libh264bitstream.so ]; then
    log "Building h264bitstream from source..."
    H264BS_DIR=$(mktemp -d)
    git clone --depth 1 https://github.com/aizvorski/h264bitstream.git "$H264BS_DIR"
    sudo gcc -shared -fPIC -o /usr/local/lib/libh264bitstream.so \
        "$H264BS_DIR/h264_nal.c" \
        "$H264BS_DIR/h264_stream.c" \
        "$H264BS_DIR/h264_sei.c"
    sudo cp "$H264BS_DIR/h264_stream.h" "$H264BS_DIR/h264_nal.h" /usr/local/include/
    sudo ldconfig
    rm -rf "$H264BS_DIR"
    log "h264bitstream installed."
else
    log "h264bitstream already installed — skipping."
fi

# ── 4. Build aasdk ───────────────────────────────────────────────────────────

log "Building aasdk ($AASDK_COMMIT)..."
AASDK_BUILD="$AASDK_DIR/build"
rm -rf "$AASDK_BUILD"
mkdir "$AASDK_BUILD"
cmake -S "$AASDK_DIR" -B "$AASDK_BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DSKIP_BUILD_PROTOBUF=ON \
    -DSKIP_BUILD_ABSL=ON
make -C "$AASDK_BUILD" -j3
sudo make -C "$AASDK_BUILD" install
sudo ldconfig
log "aasdk installed."

# ── 5. Build openauto ────────────────────────────────────────────────────────

log "Building openauto ($OPENAUTO_COMMIT)..."
OPENAUTO_BUILD="$OPENAUTO_DIR/build"
rm -rf "$OPENAUTO_BUILD"
mkdir "$OPENAUTO_BUILD"
cmake -S "$OPENAUTO_DIR" -B "$OPENAUTO_BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DNOPI=ON \
    -DGST_BUILD=TRUE
make -C "$OPENAUTO_BUILD" -j3
sudo make -C "$OPENAUTO_BUILD" install
log "openauto installed."

# ── 6. Verify the binary ─────────────────────────────────────────────────────

AUTOAPP=/usr/local/bin/autoapp
[ -x "$AUTOAPP" ] || die "autoapp binary not found after install."

log "Verifying GStreamer linkage..."
if ldd "$AUTOAPP" | grep -q libgstreamer; then
    log "GStreamer linked: OK"
else
    warn "GStreamer not found in ldd output — check build flags."
fi

# ── 7. Record build info ─────────────────────────────────────────────────────

sudo mkdir -p /data
{
    echo "aasdk:      $AASDK_COMMIT  ($(git -C "$AASDK_DIR" rev-parse HEAD))"
    echo "openauto:   $OPENAUTO_COMMIT  ($(git -C "$OPENAUTO_DIR" rev-parse HEAD))"
    echo "build-date: $(date -Is)"
    echo "host:       $(uname -a)"
} | sudo tee "$BUILD_INFO_FILE" > /dev/null
log "Build info written to $BUILD_INFO_FILE"

echo ""
echo -e "${GREEN}Build complete.${NC} Run: sudo systemctl restart piauto"
