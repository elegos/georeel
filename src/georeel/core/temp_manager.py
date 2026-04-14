"""Central manager for GeoReel temporary directories and files.

All temp directories created by GeoReel use the ``georeel_`` prefix so they
can be recognised and cleaned up on restart if a previous run crashed before
its ``atexit`` handlers fired.

Usage
-----
    from georeel.core import temp_manager

    # At application startup (after reading settings):
    temp_manager.set_base_dir(Path("/fast/scratch"))   # or None for system /tmp

    # Clean up left-overs from crashed previous runs:
    n = temp_manager.cleanup_stale()
    if n:
        _log.info("Removed %d stale GeoReel temp entries", n)

    # Inside any module that needs a temp dir:
    work_dir = temp_manager.make_temp_dir("georeel_scene_")
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Sequence

_log = logging.getLogger(__name__)

# Every georeel temp dir starts with this prefix.
_DIR_PREFIX = "georeel_"

# Stale loose *files* that can be left behind (mkstemp / mktemp callers).
# All live in the same directory as the temp dirs.
_FILE_GLOBS = [
    "georeel_preview_*.mp4",
    "*_georeel_settings.json",
]

# Module-level custom base directory.  None → use the OS default (gettempdir).
_base_dir: Path | None = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def set_base_dir(path: Path | None) -> None:
    """Set the root directory in which GeoReel creates all temp dirs/files.

    Pass *None* to revert to the OS default (``tempfile.gettempdir()``).
    The directory is created on first use (not here) so that a bad path
    raises at ``make_temp_dir`` time, not at settings-load time.
    """
    global _base_dir
    _base_dir = path
    if path is not None:
        _log.info("[temp] Custom temp dir: %s", path)
    else:
        _log.info("[temp] Using system temp dir: %s", tempfile.gettempdir())


def get_base_dir() -> Path | None:
    """Return the configured base dir, or None if using the system default."""
    return _base_dir


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_temp_dir(prefix: str) -> Path:
    """Create and return a new temp directory.

    The directory is created inside the configured base dir (or the OS
    default when none is set).  The directory is guaranteed to exist and
    be empty when returned.

    Raises ``OSError`` if the base dir does not exist and cannot be created.
    """
    if _base_dir is not None:
        _base_dir.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=_base_dir))
    return Path(tempfile.mkdtemp(prefix=prefix))


# ---------------------------------------------------------------------------
# Stale-entry cleanup
# ---------------------------------------------------------------------------

def cleanup_stale(extra_dirs: Sequence[Path] | None = None) -> int:
    """Remove GeoReel temp entries left by previous crashed sessions.

    Scans:
    - The OS default temp directory (``tempfile.gettempdir()``).
    - The configured custom base dir (if set and different from the above).
    - Any paths in *extra_dirs*.

    Returns the number of entries successfully removed.
    """
    scan_dirs: list[Path] = [Path(tempfile.gettempdir())]
    if _base_dir is not None and _base_dir != scan_dirs[0]:
        scan_dirs.append(_base_dir)
    if extra_dirs:
        for d in extra_dirs:
            if d not in scan_dirs:
                scan_dirs.append(d)

    removed = 0
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        removed += _sweep_dirs(scan_dir)
        removed += _sweep_files(scan_dir)

    if removed:
        _log.info("[temp] Cleaned up %d stale GeoReel temp entries", removed)
    else:
        _log.debug("[temp] No stale GeoReel temp entries found")
    return removed


def _sweep_dirs(scan_dir: Path) -> int:
    """Remove all ``georeel_*`` subdirectories inside *scan_dir*."""
    removed = 0
    for entry in scan_dir.glob(f"{_DIR_PREFIX}*"):
        if not entry.is_dir():
            continue
        try:
            shutil.rmtree(entry, ignore_errors=False)
            _log.debug("[temp] Removed stale dir: %s", entry)
            removed += 1
        except Exception as exc:
            _log.warning("[temp] Could not remove stale dir %s: %s", entry, exc)
    return removed


def _sweep_files(scan_dir: Path) -> int:
    """Remove stale georeel loose files (previews, settings attachments)."""
    removed = 0
    for glob in _FILE_GLOBS:
        for entry in scan_dir.glob(glob):
            if not entry.is_file():
                continue
            try:
                entry.unlink()
                _log.debug("[temp] Removed stale file: %s", entry)
                removed += 1
            except Exception as exc:
                _log.warning("[temp] Could not remove stale file %s: %s", entry, exc)
    return removed
