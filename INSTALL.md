# Installing GeoReel

This document covers all installation paths:

- [Quick install (scripts)](#quick-install-scripts) — recommended for most users
- [Manual install from wheel](#manual-install-from-wheel) — when you prefer full control
- [Build from source](#build-from-source) — for contributors and developers
- [System dependencies reference](#system-dependencies-reference) — PySide6 details per distro

---

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.14+ | Required for all install paths |
| **FFmpeg** | Any recent | Must be on `PATH`; used for video encoding |
| **Blender** | 4.2 LTS, 4.4, or 4.5 LTS | Used for 3D rendering; can be auto-downloaded from inside GeoReel |

---

## Quick install (scripts)

The installer scripts automatically:
1. Check for Python 3.14
2. Install PySide6 system dependencies (Linux only)
3. Check for FFmpeg (offering to install it where possible)
4. Download the latest `.whl` from GitHub Releases
5. Install GeoReel with `pip`

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/elegos/georeel/main/scripts/install.sh | bash
```

Or, if you have already cloned the repository:

```bash
bash scripts/install.sh
```

The script detects your Linux package manager (`apt`, `dnf`, `pacman`, `zypper`) and installs the PySide6 system libraries automatically.

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/elegos/georeel/main/scripts/install.ps1 | iex
```

Or, if you have already cloned the repository:

```powershell
.\scripts\install.ps1
```

The script will offer to install Python 3.14 and FFmpeg via `winget` if they are not found.

---

## Manual install from wheel

If you prefer not to run a script, or the script fails, follow these steps.

### 1. Install system dependencies (Linux only)

PySide6 requires several low-level X11/XCB libraries that may not be present on minimal or server installs. See the [System dependencies reference](#system-dependencies-reference) section for the full list per distro.

**Debian / Ubuntu:**

```bash
sudo apt install \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-xfixes0 libxcb-xkb1 \
  libxkbcommon0 libxkbcommon-x11-0 libx11-xcb1 \
  libegl1 libgl1 libglib2.0-0 libdbus-1-3 libfontconfig1
```

### 2. Install FFmpeg

**Debian / Ubuntu:**

```bash
sudo apt install ffmpeg
```

**Fedora:**

```bash
sudo dnf install ffmpeg
```

**macOS:**

```bash
brew install ffmpeg
```

**Windows (winget):**

```powershell
winget install Gyan.FFmpeg
```

### 3. Download the latest wheel

Go to the [Releases page](https://github.com/elegos/georeel/releases) and download the latest `georeel-*.whl` file.

Alternatively, download it from the terminal:

**Linux / macOS:**

```bash
WHL_URL=$(python3 -c "
import urllib.request, json
with urllib.request.urlopen('https://api.github.com/repos/elegos/georeel/releases/latest') as r:
    data = json.load(r)
print(next(a['browser_download_url'] for a in data['assets'] if a['name'].endswith('.whl')))
")
curl -L -o georeel.whl "$WHL_URL"
```

**Windows (PowerShell):**

```powershell
$rel = Invoke-RestMethod https://api.github.com/repos/elegos/georeel/releases/latest
$url = ($rel.assets | Where-Object { $_.name -like "*.whl" })[0].browser_download_url
Invoke-WebRequest $url -OutFile georeel.whl
```

### 4. Install with pip

```bash
pip install georeel.whl
```

Or, if you want to install it only for your user (no `sudo`):

```bash
pip install --user georeel.whl
```

### 5. Run

```bash
georeel
```

---

## Build from source

Use this path if you want to contribute, modify the code, or test an unreleased version.

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) — the project's package manager

Install `uv`:

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

### Clone and set up

```bash
git clone https://github.com/elegos/georeel.git
cd georeel
uv sync
```

### Run from source

```bash
uv run main.py
```

### Build a wheel

```bash
uv build
# Output: dist/georeel-*.whl
```

### Run the test suite

```bash
uv run pytest
# With coverage:
uv run pytest --cov=georeel.core --cov-report=term-missing
```

---

## System dependencies reference

### Why are these needed?

PySide6 uses the Qt XCB platform plugin to render its UI on Linux. This plugin dynamically links against several system libraries at runtime. These are **not** bundled inside the PySide6 wheel, so they must be present on the host system.

The most common cause of a blank window or the error `could not load the Qt platform plugin "xcb"` is a missing `libxcb-cursor.so.0` (`libxcb-cursor0` on Debian/Ubuntu).

### Debian / Ubuntu

```bash
sudo apt install \
  libxcb-cursor0 \
  libxcb-icccm4 \
  libxcb-image0 \
  libxcb-keysyms1 \
  libxcb-randr0 \
  libxcb-render-util0 \
  libxcb-xfixes0 \
  libxcb-xkb1 \
  libxkbcommon0 \
  libxkbcommon-x11-0 \
  libx11-xcb1 \
  libegl1 \
  libgl1 \
  libglib2.0-0 \
  libdbus-1-3 \
  libfontconfig1
```

### Fedora / RHEL / CentOS Stream

```bash
sudo dnf install \
  libxcb-cursor \
  xcb-util-wm \
  xcb-util-image \
  xcb-util-keysyms \
  xcb-util-renderutil \
  libxkbcommon \
  libxkbcommon-x11 \
  libX11-xcb \
  mesa-libGL \
  mesa-libEGL \
  glib2 \
  dbus-libs \
  fontconfig
```

### Arch Linux / Manjaro

```bash
sudo pacman -S --needed \
  xcb-util-cursor \
  xcb-util-wm \
  xcb-util-image \
  xcb-util-keysyms \
  xcb-util-renderutil \
  libxkbcommon \
  libxkbcommon-x11 \
  libxcb \
  mesa \
  libglvnd \
  glib2 \
  dbus \
  fontconfig
```

### openSUSE

```bash
sudo zypper install \
  libxcb-cursor0 \
  xcb-util-wm \
  xcb-util-image \
  xcb-util-keysyms \
  xcb-util-renderutil \
  libxkbcommon0 \
  libxkbcommon-x11-0 \
  libX11-xcb1 \
  Mesa-libGL1 \
  Mesa-libEGL1 \
  libglib-2_0-0 \
  libdbus-1-3 \
  fontconfig
```

### macOS

PySide6 bundles all required Qt libraries on macOS. No additional system packages are needed beyond Xcode Command Line Tools (which Python 3.14 installation will prompt you to install if missing).

### Windows

PySide6 bundles all required DLLs on Windows. No additional system packages are needed.

---

## Troubleshooting

**`qt.qpa.plugin: Could not load the Qt platform plugin "xcb"`**

Install the missing XCB libraries. On Debian/Ubuntu, the most common culprit is:

```bash
sudo apt install libxcb-cursor0
```

Then re-run the full dependency block from the [System dependencies reference](#system-dependencies-reference) to ensure nothing else is missing.

**`ModuleNotFoundError: No module named 'georeel'` after pip install**

Your Python `bin` directory may not be on `PATH`. Check:

```bash
python3 -m site --user-base
# Add <output>/bin to PATH, e.g. in ~/.bashrc:
export PATH="$HOME/.local/bin:$PATH"
```

**`georeel: command not found`**

Same as above — the entry point was installed into a directory not on `PATH`. See above.

**Blender not found**

GeoReel can download a portable Blender automatically via *Options → Blender…*.
Alternatively, install Blender from https://www.blender.org/download/ and ensure the executable is on `PATH`, or point GeoReel to it in the same dialog.
