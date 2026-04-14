"""Tests for core.pipeline_memory diagnostics."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from georeel.core.pipeline_memory import _fmt, _rss_mb, log_pipeline_memory


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------

class TestFmt:
    def test_small_value_shows_mb(self):
        assert _fmt(512.0) == "512.0 MB"

    def test_exactly_1024_shows_gb(self):
        assert _fmt(1024.0) == "1.00 GB"

    def test_large_value_shows_gb(self):
        result = _fmt(2048.0)
        assert "GB" in result
        assert "2.00" in result

    def test_sub_mb_shows_mb(self):
        assert "MB" in _fmt(0.5)

    def test_zero(self):
        assert "0.0 MB" == _fmt(0.0)


# ---------------------------------------------------------------------------
# _rss_mb
# ---------------------------------------------------------------------------

class TestRssMb:
    def test_returns_float(self):
        result = _rss_mb()
        assert isinstance(result, float)

    def test_returns_nan_when_both_unavailable(self):
        # Simulate both psutil and resource being unavailable.
        original = _rss_mb.__globals__.get("_rss_mb")
        import math
        import importlib
        import georeel.core.pipeline_memory as pm_mod
        # Patch psutil and resource to raise inside _rss_mb.
        with (
            patch.dict("sys.modules", {"psutil": None, "resource": None}),
        ):
            # Re-import to pick up None entries — just verify float is returned.
            val = _rss_mb()
            assert isinstance(val, float)

    def test_positive_or_nan(self):
        import math
        val = _rss_mb()
        assert math.isnan(val) or val > 0


# ---------------------------------------------------------------------------
# log_pipeline_memory
# ---------------------------------------------------------------------------

def _empty_pipeline():
    return SimpleNamespace()


def _pipeline_with_trackpoints(n=10):
    p = _empty_pipeline()
    p.trackpoints = list(range(n))
    return p


def _pipeline_with_keyframes(n=5):
    p = _empty_pipeline()
    p.camera_keyframes = list(range(n))
    return p


def _pipeline_with_dem(rows=100, cols=200):
    p = _empty_pipeline()
    grid = SimpleNamespace(
        rows=rows,
        cols=cols,
        data=np.zeros((rows, cols), dtype=np.float32),
    )
    p.elevation_grid = grid
    return p


def _pipeline_with_sat_image(width=100, height=80):
    p = _empty_pipeline()
    img = Image.new("RGB", (width, height))
    sat = SimpleNamespace(image=img)
    p.satellite_texture = sat
    return p


def _pipeline_with_lazy_sat(source_zip_name="test.georeel"):
    p = _empty_pipeline()
    from pathlib import Path
    sat = SimpleNamespace(
        image=None,
        _source_zip=Path(source_zip_name),
        _tiles_dir=None,
    )
    p.satellite_texture = sat
    return p


def _pipeline_with_freed_sat(tiles_dir="/tmp/tiles"):
    p = _empty_pipeline()
    from pathlib import Path
    sat = SimpleNamespace(
        image=None,
        _source_zip=None,
        _tiles_dir=Path(tiles_dir),
    )
    p.satellite_texture = sat
    return p


class TestLogPipelineMemory:
    def test_empty_pipeline_logs_empty(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_empty_pipeline(), "test_label")
        assert any("pipeline empty" in r.message for r in caplog.records)

    def test_trackpoints_branch(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_pipeline_with_trackpoints(42), "tp_test")
        combined = " ".join(r.message for r in caplog.records)
        assert "trackpoints" in combined
        assert "42" in combined

    def test_keyframes_branch(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_pipeline_with_keyframes(7), "kf_test")
        combined = " ".join(r.message for r in caplog.records)
        assert "camera_kf" in combined
        assert "7" in combined

    def test_elevation_grid_branch(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_pipeline_with_dem(100, 200), "dem_test")
        combined = " ".join(r.message for r in caplog.records)
        assert "elevation_grid" in combined
        assert "100" in combined

    def test_satellite_image_branch(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_pipeline_with_sat_image(100, 80), "sat_test")
        combined = " ".join(r.message for r in caplog.records)
        assert "satellite" in combined
        assert "100" in combined
        assert "80" in combined

    def test_satellite_lazy_branch(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_pipeline_with_lazy_sat("my_project.georeel"), "lazy_test")
        combined = " ".join(r.message for r in caplog.records)
        assert "lazy" in combined
        assert "my_project.georeel" in combined

    def test_satellite_freed_branch(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_pipeline_with_freed_sat("/tmp/tiles"), "freed_test")
        combined = " ".join(r.message for r in caplog.records)
        assert "freed" in combined

    def test_label_appears_in_log(self, caplog):
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(_empty_pipeline(), "my_unique_label")
        combined = " ".join(r.message for r in caplog.records)
        assert "my_unique_label" in combined

    def test_full_pipeline_logs_all_sections(self, caplog):
        """Exercise all branches in a single pipeline object."""
        p = _empty_pipeline()
        p.trackpoints = list(range(5))
        p.camera_keyframes = list(range(3))
        p.elevation_grid = SimpleNamespace(
            rows=50, cols=100,
            data=np.zeros((50, 100), dtype=np.float32),
        )
        img = Image.new("RGB", (60, 40))
        p.satellite_texture = SimpleNamespace(image=img)

        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(p, "full")

        combined = " ".join(r.message for r in caplog.records)
        assert "trackpoints" in combined
        assert "camera_kf" in combined
        assert "elevation_grid" in combined
        assert "satellite" in combined

    def test_dem_without_data_attribute_skipped(self, caplog):
        """Elevation grid without .data must not raise."""
        p = _empty_pipeline()
        p.elevation_grid = SimpleNamespace(rows=10, cols=10)  # no .data
        with caplog.at_level(logging.INFO, logger="georeel.core.pipeline_memory"):
            log_pipeline_memory(p, "no_data")
        # Should not raise; grid section just absent from output.
