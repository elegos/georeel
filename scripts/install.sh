#!/usr/bin/env bash
# GeoReel installer — Linux and macOS
# Downloads the latest release wheel from GitHub and installs it.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/elegos/georeel/main/scripts/install.sh | bash
#   — or —
#   bash scripts/install.sh

set -euo pipefail

REPO="elegos/georeel"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=14

# ── Colour helpers ────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BOLD}[georeel]${NC} $*"; }
success() { echo -e "${GREEN}[georeel]${NC} $*"; }
warn()    { echo -e "${YELLOW}[georeel] WARNING:${NC} $*"; }
die()     { echo -e "${RED}[georeel] ERROR:${NC} $*" >&2; exit 1; }

# ── OS detection ─────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Linux)  PLATFORM="linux"  ;;
  Darwin) PLATFORM="macos"  ;;
  *)      die "Unsupported platform: $OS. Use the Windows script (install.ps1) on Windows." ;;
esac

info "GeoReel installer — platform: $PLATFORM"
echo

# ── Python 3.14 check ────────────────────────────────────────────────
find_python() {
  for cmd in python3.14 python3 python; do
    if command -v "$cmd" &>/dev/null; then
      local ver
      ver="$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
      local major minor
      major="${ver%%.*}"; minor="${ver#*.}"
      if [[ "$major" -gt "$MIN_PYTHON_MAJOR" ]] || \
         [[ "$major" -eq "$MIN_PYTHON_MAJOR" && "$minor" -ge "$MIN_PYTHON_MINOR" ]]; then
        echo "$cmd"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON=""
if ! PYTHON="$(find_python)"; then
  echo
  warn "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ not found."
  if [[ "$PLATFORM" == "linux" ]]; then
    echo "  Install it via your package manager, for example:"
    echo "    Debian/Ubuntu:  sudo apt install python3.14 python3.14-venv"
    echo "                    (add ppa:deadsnakes/ppa if not available)"
    echo "    Fedora:         sudo dnf install python3.14"
    echo "    Arch:           sudo pacman -S python"
    echo "    Other:          https://www.python.org/downloads/"
  else
    echo "  Install it via Homebrew:  brew install python@3.14"
    echo "  Or download from:         https://www.python.org/downloads/"
  fi
  exit 1
fi

PYTHON_VERSION="$("$PYTHON" -c 'import sys; print(sys.version)')"
success "Found Python: $PYTHON_VERSION"

# ── Linux: system dependencies for PySide6 ───────────────────────────
install_linux_sys_deps() {
  if command -v apt-get &>/dev/null; then
    info "Installing PySide6 system dependencies (apt)…"
    local pkgs=(
      libxcb-cursor0
      libxcb-icccm4
      libxcb-image0
      libxcb-keysyms1
      libxcb-randr0
      libxcb-render-util0
      libxcb-xfixes0
      libxcb-xkb1
      libxkbcommon0
      libxkbcommon-x11-0
      libx11-xcb1
      libegl1
      libgl1
      libglib2.0-0
      libdbus-1-3
      libfontconfig1
    )
    sudo apt-get install -y --no-install-recommends "${pkgs[@]}"

  elif command -v dnf &>/dev/null; then
    info "Installing PySide6 system dependencies (dnf)…"
    local pkgs=(
      libxcb-cursor
      xcb-util-wm
      xcb-util-image
      xcb-util-keysyms
      xcb-util-renderutil
      libxkbcommon
      libxkbcommon-x11
      libX11-xcb
      mesa-libGL
      mesa-libEGL
      glib2
      dbus-libs
      fontconfig
    )
    sudo dnf install -y "${pkgs[@]}"

  elif command -v pacman &>/dev/null; then
    info "Installing PySide6 system dependencies (pacman)…"
    local pkgs=(
      xcb-util-cursor
      xcb-util-wm
      xcb-util-image
      xcb-util-keysyms
      xcb-util-renderutil
      libxkbcommon
      libxkbcommon-x11
      libxcb
      mesa
      libglvnd
      glib2
      dbus
      fontconfig
    )
    sudo pacman -S --needed --noconfirm "${pkgs[@]}"

  elif command -v zypper &>/dev/null; then
    info "Installing PySide6 system dependencies (zypper)…"
    local pkgs=(
      libxcb-cursor0
      xcb-util-wm
      xcb-util-image
      xcb-util-keysyms
      xcb-util-renderutil
      libxkbcommon0
      libxkbcommon-x11-0
      libX11-xcb1
      Mesa-libGL1
      Mesa-libEGL1
      libglib-2_0-0
      libdbus-1-3
      fontconfig
    )
    sudo zypper install -y "${pkgs[@]}"

  else
    warn "Could not detect your package manager."
    warn "Make sure the following libraries are installed before running GeoReel:"
    warn "  libxcb-cursor, libxcb-icccm, libxcb-image, libxcb-keysyms,"
    warn "  libxcb-randr, libxcb-render-util, libxcb-xfixes, libxcb-xkb,"
    warn "  libxkbcommon, libxkbcommon-x11, libegl1, libgl1"
  fi
}

if [[ "$PLATFORM" == "linux" ]]; then
  install_linux_sys_deps
fi

# ── macOS: check FFmpeg via Homebrew ─────────────────────────────────
if [[ "$PLATFORM" == "macos" ]]; then
  if ! command -v ffmpeg &>/dev/null; then
    if command -v brew &>/dev/null; then
      info "Installing FFmpeg via Homebrew…"
      brew install ffmpeg
    else
      warn "FFmpeg not found and Homebrew is not installed."
      warn "Install Homebrew first: https://brew.sh/"
      warn "Then run: brew install ffmpeg"
    fi
  else
    success "FFmpeg found: $(ffmpeg -version 2>&1 | head -1)"
  fi
fi

# ── Linux: check FFmpeg ───────────────────────────────────────────────
if [[ "$PLATFORM" == "linux" ]]; then
  if ! command -v ffmpeg &>/dev/null; then
    warn "FFmpeg not found. Install it before using GeoReel:"
    echo "  Debian/Ubuntu:  sudo apt install ffmpeg"
    echo "  Fedora:         sudo dnf install ffmpeg"
    echo "  Arch:           sudo pacman -S ffmpeg"
  else
    success "FFmpeg found: $(ffmpeg -version 2>&1 | head -1)"
  fi
fi

# ── Download latest release wheel ────────────────────────────────────
info "Fetching latest release from GitHub…"
WHL_URL="$("$PYTHON" -c "
import urllib.request, json, sys

url = 'https://api.github.com/repos/${REPO}/releases/latest'
req = urllib.request.Request(url, headers={
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'georeel-installer',
})
try:
    with urllib.request.urlopen(req) as r:
        data = json.load(r)
except Exception as e:
    print(f'ERROR: failed to reach GitHub API: {e}', file=sys.stderr)
    sys.exit(1)

assets = [a['browser_download_url'] for a in data.get('assets', []) if a['name'].endswith('.whl')]
if not assets:
    print('ERROR: no .whl asset found in latest release', file=sys.stderr)
    sys.exit(1)
print(assets[0])
")"

WHL_NAME="${WHL_URL##*/}"
info "Downloading $WHL_NAME…"
TMP_WHL="$(mktemp --suffix=".whl" 2>/dev/null || mktemp -t georeel.XXXXXX.whl)"
trap 'rm -f "$TMP_WHL"' EXIT

"$PYTHON" -c "
import urllib.request
urllib.request.urlretrieve('${WHL_URL}', '${TMP_WHL}')
"

# ── Install ───────────────────────────────────────────────────────────
info "Installing GeoReel…"
"$PYTHON" -m pip install --upgrade "$TMP_WHL"

echo
success "GeoReel installed successfully!"
echo
info "Run it with:"
echo "    georeel"
echo "  or:"
echo "    python3 -m georeel.main"
echo
info "Blender (4.2 LTS, 4.4, or 4.5 LTS) is required for 3D rendering."
info "GeoReel can download it automatically from Options → Blender…"
info "Or install it from: https://www.blender.org/download/"
