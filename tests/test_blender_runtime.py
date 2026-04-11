"""Tests for blender_runtime pure data/helper functions."""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch

from georeel.core.blender_runtime import (
    AVAILABLE_VERSIONS,
    DEFAULT_VERSION,
    BlenderVersion,
    _platform_bits,
    data_dir,
    portable_executable,
    download_url,
    find_blender,
    query_version,
)


# ── AVAILABLE_VERSIONS ────────────────────────────────────────────────

class TestAvailableVersions:
    def test_non_empty(self):
        assert len(AVAILABLE_VERSIONS) > 0

    def test_all_have_label(self):
        assert all(v.label for v in AVAILABLE_VERSIONS)

    def test_all_have_version(self):
        assert all(v.version for v in AVAILABLE_VERSIONS)

    def test_all_have_minor(self):
        assert all(v.minor for v in AVAILABLE_VERSIONS)

    def test_default_version_is_first(self):
        assert DEFAULT_VERSION is AVAILABLE_VERSIONS[0]

    def test_versions_are_frozen(self):
        v = AVAILABLE_VERSIONS[0]
        with pytest.raises((TypeError, AttributeError)):
            v.label = "changed"  # type: ignore[misc]


# ── _platform_bits ────────────────────────────────────────────────────

class TestPlatformBits:
    def test_linux_returns_tar_xz(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "linux"):
            result = _platform_bits(v)
        assert result is not None
        stem, ext = result
        assert ext == "tar.xz"
        assert "linux-x64" in stem
        assert "4.5.0" in stem

    def test_windows_returns_zip(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "win32"):
            result = _platform_bits(v)
        assert result is not None
        stem, ext = result
        assert ext == "zip"
        assert "windows-x64" in stem

    def test_macos_returns_none(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "darwin"):
            result = _platform_bits(v)
        assert result is None

    def test_stem_contains_version(self):
        v = BlenderVersion("4.2 LTS", "4.2.8", "4.2")
        with patch("sys.platform", "linux"):
            result = _platform_bits(v)
        assert result is not None
        assert "4.2.8" in result[0]


# ── data_dir ──────────────────────────────────────────────────────────

class TestDataDir:
    def test_linux_returns_dot_local(self):
        with patch("sys.platform", "linux"):
            d = data_dir()
        assert "georeel" in str(d)
        assert "blender" in str(d)

    def test_windows_uses_appdata(self):
        with patch("sys.platform", "win32"), \
             patch.dict("os.environ", {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}):
            d = data_dir()
        assert "georeel" in str(d)
        assert "blender" in str(d)

    def test_macos_returns_library(self):
        with patch("sys.platform", "darwin"):
            d = data_dir()
        assert "Library" in str(d)
        assert "georeel" in str(d)

    def test_returns_path_instance(self):
        d = data_dir()
        assert isinstance(d, Path)


# ── portable_executable ───────────────────────────────────────────────

class TestPortableExecutable:
    def test_linux_returns_path_to_blender(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "linux"):
            p = portable_executable(v)
        assert p is not None
        assert p.name == "blender"

    def test_windows_returns_blender_exe(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "win32"), \
             patch.dict("os.environ", {"APPDATA": "C:\\Users\\x\\AppData\\Roaming"}):
            p = portable_executable(v)
        assert p is not None
        assert p.name == "blender.exe"

    def test_macos_returns_none(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "darwin"):
            p = portable_executable(v)
        assert p is None

    def test_path_contains_version_stem(self):
        v = BlenderVersion("4.2 LTS", "4.2.8", "4.2")
        with patch("sys.platform", "linux"):
            p = portable_executable(v)
        assert p is not None
        assert "4.2.8" in str(p)


# ── download_url ──────────────────────────────────────────────────────

class TestDownloadUrl:
    def test_linux_returns_tar_xz_url(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "linux"):
            url = download_url(v)
        assert url is not None
        assert url.endswith(".tar.xz")
        assert "4.5.0" in url
        assert "blender.org" in url

    def test_windows_returns_zip_url(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "win32"), \
             patch.dict("os.environ", {"APPDATA": "C:\\Users\\x"}):
            url = download_url(v)
        assert url is not None
        assert url.endswith(".zip")

    def test_macos_returns_none(self):
        v = BlenderVersion("4.5 LTS", "4.5.0", "4.5")
        with patch("sys.platform", "darwin"):
            url = download_url(v)
        assert url is None

    def test_url_contains_minor_in_path(self):
        v = BlenderVersion("4.2 LTS", "4.2.8", "4.2")
        with patch("sys.platform", "linux"):
            url = download_url(v)
        assert url is not None
        assert "Blender4.2" in url

    def test_url_starts_with_https(self):
        v = AVAILABLE_VERSIONS[0]
        with patch("sys.platform", "linux"):
            url = download_url(v)
        assert url is not None
        assert url.startswith("https://")


# ── find_blender ──────────────────────────────────────────────────────

class TestFindBlender:
    def test_returns_none_when_nothing_found(self):
        with patch("shutil.which", return_value=None), \
             patch("pathlib.Path.is_file", return_value=False):
            result = find_blender()
        assert result is None

    def test_custom_path_checked_first(self, tmp_path):
        fake_blender = tmp_path / "blender"
        fake_blender.write_bytes(b"")
        result = find_blender(custom_path=str(fake_blender))
        assert result == str(fake_blender)

    def test_system_blender_used_when_available(self, tmp_path):
        fake_blender = tmp_path / "blender"
        fake_blender.write_bytes(b"")
        with patch("shutil.which", return_value=str(fake_blender)), \
             patch("pathlib.Path.is_file", return_value=True):
            result = find_blender()
        # system path was provided to which() and it exists as a file
        assert result is not None

    def test_nonexistent_custom_path_skipped(self):
        with patch("shutil.which", return_value=None), \
             patch("pathlib.Path.is_file", return_value=False):
            result = find_blender(custom_path="/nonexistent/blender")
        assert result is None


# ── query_version ─────────────────────────────────────────────────────

class TestQueryVersion:
    def test_returns_none_on_exception(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = query_version("/nonexistent/blender")
        assert result is None

    def test_returns_first_line_of_stdout(self):
        from unittest.mock import MagicMock
        mock = MagicMock()
        mock.stdout = "Blender 4.5.0 (hash abc123)\nextra line\n"
        with patch("subprocess.run", return_value=mock):
            result = query_version("/fake/blender")
        assert result == "Blender 4.5.0 (hash abc123)"

    def test_returns_none_when_no_stdout(self):
        from unittest.mock import MagicMock
        mock = MagicMock()
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            result = query_version("/fake/blender")
        assert result is None
