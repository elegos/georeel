"""Tests for georeel.core.temp_manager."""

import shutil
from pathlib import Path

import pytest

import georeel.core.temp_manager as tm


@pytest.fixture(autouse=True)
def reset_base_dir():
    """Restore the module-level base dir after each test."""
    original = tm._base_dir
    yield
    tm._base_dir = original


# ---------------------------------------------------------------------------
# set_base_dir / get_base_dir
# ---------------------------------------------------------------------------

class TestSetGetBaseDir:
    def test_default_is_none(self):
        tm._base_dir = None
        assert tm.get_base_dir() is None

    def test_set_custom_dir(self, tmp_path):
        tm.set_base_dir(tmp_path)
        assert tm.get_base_dir() == tmp_path

    def test_set_none_reverts(self, tmp_path):
        tm.set_base_dir(tmp_path)
        tm.set_base_dir(None)
        assert tm.get_base_dir() is None

    def test_set_returns_none(self, tmp_path):
        result = tm.set_base_dir(tmp_path)
        assert result is None  # no return value


# ---------------------------------------------------------------------------
# make_temp_dir
# ---------------------------------------------------------------------------

class TestMakeTempDir:
    def test_creates_directory(self):
        tm._base_dir = None
        d = tm.make_temp_dir("georeel_test_")
        try:
            assert d.is_dir()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_prefix_used(self):
        tm._base_dir = None
        d = tm.make_temp_dir("georeel_test_")
        try:
            assert d.name.startswith("georeel_test_")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_custom_base_dir(self, tmp_path):
        tm.set_base_dir(tmp_path)
        d = tm.make_temp_dir("georeel_custom_")
        try:
            assert d.is_dir()
            assert str(d).startswith(str(tmp_path))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_returns_path_object(self):
        tm._base_dir = None
        d = tm.make_temp_dir("georeel_test_")
        try:
            assert isinstance(d, Path)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_creates_base_dir_if_missing(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        tm.set_base_dir(nested)
        d = tm.make_temp_dir("georeel_nested_")
        try:
            assert nested.is_dir()
            assert d.is_dir()
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# _sweep_dirs
# ---------------------------------------------------------------------------

class TestSweepDirs:
    def test_removes_georeel_subdir(self, tmp_path):
        stale = tmp_path / "georeel_old_session"
        stale.mkdir()
        removed = tm._sweep_dirs(tmp_path)
        assert removed == 1
        assert not stale.exists()

    def test_ignores_non_georeel_dirs(self, tmp_path):
        other = tmp_path / "other_app_dir"
        other.mkdir()
        removed = tm._sweep_dirs(tmp_path)
        assert removed == 0
        assert other.exists()

    def test_ignores_regular_files(self, tmp_path):
        f = tmp_path / "georeel_not_a_dir.txt"
        f.write_text("data")
        removed = tm._sweep_dirs(tmp_path)
        assert removed == 0
        assert f.exists()

    def test_removes_multiple_stale_dirs(self, tmp_path):
        for i in range(3):
            (tmp_path / f"georeel_session_{i}").mkdir()
        removed = tm._sweep_dirs(tmp_path)
        assert removed == 3

    def test_nonexistent_scan_dir(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        # Should not raise; cleanup_stale skips missing dirs
        # _sweep_dirs itself will be called after is_dir check, but here we call directly
        removed = tm._sweep_dirs(missing)
        assert removed == 0


# ---------------------------------------------------------------------------
# _sweep_files
# ---------------------------------------------------------------------------

class TestSweepFiles:
    def test_removes_preview_mp4(self, tmp_path):
        f = tmp_path / "georeel_preview_abc123.mp4"
        f.write_text("fake video")
        removed = tm._sweep_files(tmp_path)
        assert removed == 1
        assert not f.exists()

    def test_removes_settings_json(self, tmp_path):
        f = tmp_path / "session_georeel_settings.json"
        f.write_text("{}")
        removed = tm._sweep_files(tmp_path)
        assert removed == 1
        assert not f.exists()

    def test_ignores_other_files(self, tmp_path):
        f = tmp_path / "unrelated_file.txt"
        f.write_text("hello")
        removed = tm._sweep_files(tmp_path)
        assert removed == 0
        assert f.exists()

    def test_ignores_directories_for_file_globs(self, tmp_path):
        d = tmp_path / "georeel_preview_fake.mp4"
        d.mkdir()
        removed = tm._sweep_files(tmp_path)
        assert removed == 0


# ---------------------------------------------------------------------------
# cleanup_stale
# ---------------------------------------------------------------------------

class TestCleanupStale:
    def test_removes_stale_dirs_from_os_temp(self, tmp_path, monkeypatch):
        import tempfile
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        tm._base_dir = None

        stale = tmp_path / "georeel_crashed_session"
        stale.mkdir()
        n = tm.cleanup_stale()
        assert n >= 1
        assert not stale.exists()

    def test_scans_custom_base_dir(self, tmp_path, monkeypatch):
        import tempfile
        # Make gettempdir return something different so both are scanned
        other_tmp = tmp_path / "sys_tmp"
        other_tmp.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(other_tmp))

        custom = tmp_path / "custom"
        custom.mkdir()
        tm.set_base_dir(custom)

        stale = custom / "georeel_old"
        stale.mkdir()
        n = tm.cleanup_stale()
        assert n >= 1
        assert not stale.exists()

    def test_extra_dirs_scanned(self, tmp_path, monkeypatch):
        import tempfile
        other_tmp = tmp_path / "sys_tmp"
        other_tmp.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(other_tmp))
        tm._base_dir = None

        extra = tmp_path / "extra_dir"
        extra.mkdir()
        stale = extra / "georeel_leftover"
        stale.mkdir()
        n = tm.cleanup_stale(extra_dirs=[extra])
        assert n >= 1
        assert not stale.exists()

    def test_returns_zero_when_nothing_to_clean(self, tmp_path, monkeypatch):
        import tempfile
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        tm._base_dir = None
        n = tm.cleanup_stale()
        assert n == 0

    def test_scan_dir_not_existing_is_skipped(self, tmp_path, monkeypatch):
        import tempfile
        missing = tmp_path / "no_such_dir"
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(missing))
        tm._base_dir = None
        # Should not raise
        n = tm.cleanup_stale()
        assert n == 0
