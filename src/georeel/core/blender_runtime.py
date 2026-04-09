import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# ------------------------------------------------------------------
# Available versions
# ------------------------------------------------------------------

@dataclass(frozen=True)
class BlenderVersion:
    label: str    # shown in UI, e.g. "4.5 LTS"
    version: str  # full version string, e.g. "4.5.0"
    minor: str    # major.minor used in download URL, e.g. "4.5"


AVAILABLE_VERSIONS: list[BlenderVersion] = [
    BlenderVersion("4.5 LTS", "4.5.0", "4.5"),
    BlenderVersion("4.4",     "4.4.3", "4.4"),
    BlenderVersion("4.2 LTS", "4.2.8", "4.2"),
]

DEFAULT_VERSION = AVAILABLE_VERSIONS[0]

_BLENDER_RELEASE_BASE = "https://download.blender.org/release"


# ------------------------------------------------------------------
# Platform helpers
# ------------------------------------------------------------------

def _platform_bits(version: BlenderVersion) -> tuple[str, str] | None:
    """Returns (archive_stem, extension) for the current platform, or None."""
    stem = f"blender-{version.version}"
    if sys.platform.startswith("linux"):
        return f"{stem}-linux-x64", "tar.xz"
    if sys.platform == "win32":
        return f"{stem}-windows-x64", "zip"
    return None   # macOS: DMG not auto-extractable


def data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "georeel" / "blender"
    if sys.platform == "win32":
        import os
        return Path(os.environ.get("APPDATA", Path.home())) / "georeel" / "blender"
    return Path.home() / ".local" / "share" / "georeel" / "blender"


def portable_executable(version: BlenderVersion) -> Path | None:
    bits = _platform_bits(version)
    if bits is None:
        return None
    stem, _ = bits
    exe = "blender.exe" if sys.platform == "win32" else "blender"
    return data_dir() / stem / exe


def download_url(version: BlenderVersion) -> str | None:
    bits = _platform_bits(version)
    if bits is None:
        return None
    stem, ext = bits
    return f"{_BLENDER_RELEASE_BASE}/Blender{version.minor}/{stem}.{ext}"


# ------------------------------------------------------------------
# Detection
# ------------------------------------------------------------------

def find_blender(custom_path: str | None = None) -> str | None:
    """Return path to a working blender executable, or None."""
    candidates: list[str] = []

    if custom_path:
        candidates.append(custom_path)

    for v in AVAILABLE_VERSIONS:
        p = portable_executable(v)
        if p:
            candidates.append(str(p))

    sys_path = shutil.which("blender")
    if sys_path:
        candidates.append(sys_path)

    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def query_version(executable: str) -> str | None:
    """Run `blender --version` and return the first output line."""
    try:
        result = subprocess.run(
            shlex.join([executable, "--version"]),
            capture_output=True, text=True, timeout=10,
            shell=True,
        )
        return result.stdout.strip().splitlines()[0] if result.stdout else None
    except Exception:
        return None


# ------------------------------------------------------------------
# Download
# ------------------------------------------------------------------

class BlenderDownloadError(Exception):
    pass


class BlenderPlatformError(BlenderDownloadError):
    pass


def download_blender(
    version: BlenderVersion,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Download and extract a portable Blender. Returns executable path."""
    import requests

    bits = _platform_bits(version)
    if bits is None:
        raise BlenderPlatformError(
            "Automatic download is not supported on macOS (DMG format). "
            "Please install Blender from https://www.blender.org/download/ "
            "and point GeoReel to the executable."
        )

    stem, ext = bits
    url = f"{_BLENDER_RELEASE_BASE}/Blender{version.minor}/{stem}.{ext}"
    dest = data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"{stem}.{ext}"

    # Download
    try:
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(archive, "wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if cancel_check and cancel_check():
                        archive.unlink(missing_ok=True)
                        raise BlenderDownloadError("Download cancelled.")
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
    except requests.RequestException as e:
        archive.unlink(missing_ok=True)
        raise BlenderDownloadError(f"Download failed: {e}") from e

    # Extract
    try:
        if ext == "tar.xz":
            with tarfile.open(archive, "r:xz") as tar:
                tar.extractall(dest, filter="data")
        else:
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(dest)
    except Exception as e:
        raise BlenderDownloadError(f"Extraction failed: {e}") from e
    finally:
        archive.unlink(missing_ok=True)

    exe = portable_executable(version)
    if not exe or not exe.is_file():
        raise BlenderDownloadError("Extraction succeeded but executable not found.")

    return str(exe)
