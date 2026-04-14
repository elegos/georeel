"""Tests for georeel.core.dem_fetcher."""

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from georeel.core.dem_fetcher import (
    DemFetchError,
    _fill_voids,
    _parse_tile,
    fetch_dem,
)
from georeel.core.bounding_box import BoundingBox


# ---------------------------------------------------------------------------
# _fill_voids
# ---------------------------------------------------------------------------

class TestFillVoids:
    def test_all_valid_returns_unchanged(self):
        grid = np.array([[100.0, 200.0], [150.0, 250.0]], dtype=np.float32)
        result = _fill_voids(grid)
        np.testing.assert_array_equal(result, grid)

    def test_all_void_returns_zeros(self):
        grid = np.full((3, 3), -32768.0, dtype=np.float32)
        result = _fill_voids(grid)
        np.testing.assert_array_equal(result, np.zeros((3, 3), dtype=np.float32))

    def test_partial_voids_filled(self):
        grid = np.array([[100.0, -32768.0], [200.0, 300.0]], dtype=np.float32)
        result = _fill_voids(grid)
        # The void cell should be filled with a valid neighbour value
        assert result[0, 1] > -500.0  # not void anymore
        assert np.isfinite(result[0, 1])

    def test_out_of_range_high_treated_as_void(self):
        grid = np.array([[100.0, 9500.0], [200.0, 150.0]], dtype=np.float32)
        result = _fill_voids(grid)
        # 9500 > _ELEV_MAX_M → void → filled
        assert result[0, 1] != 9500.0

    def test_out_of_range_low_treated_as_void(self):
        grid = np.array([[100.0, -600.0], [200.0, 150.0]], dtype=np.float32)
        result = _fill_voids(grid)
        # -600 < _ELEV_MIN_M → void → filled
        assert result[0, 1] != -600.0

    def test_no_voids_shape_preserved(self):
        grid = np.ones((5, 7), dtype=np.float32) * 500.0
        result = _fill_voids(grid)
        assert result.shape == (5, 7)


# ---------------------------------------------------------------------------
# _parse_tile
# ---------------------------------------------------------------------------

class TestParseTile:
    def test_none_geo_file_returns_none(self):
        assert _parse_tile(None) is None

    def test_empty_data_returns_none(self):
        geo_file = MagicMock()
        geo_file.data = b""  # falsy
        assert _parse_tile(geo_file) is None

    def test_valid_geo_file_returns_tuple(self):
        N = 4
        geo_file = MagicMock()
        geo_file.square_side = N
        # Build raw big-endian int16 data: 4×4 grid of 100s
        data = np.full((N, N), 100, dtype=">i2").tobytes()
        geo_file.data = data
        geo_file.latitude = 46.0
        geo_file.longitude = 7.0

        result = _parse_tile(geo_file)
        assert result is not None
        tile_arr, n_out, f_lat, f_lon = result
        assert n_out == N
        assert f_lat == 46.0
        assert f_lon == 7.0
        assert tile_arr.shape == (N, N)
        assert tile_arr.dtype == np.float32

    def test_void_values_masked(self):
        N = 2
        geo_file = MagicMock()
        geo_file.square_side = N
        # One valid cell (500) and one srtm-raw-max void (11000)
        arr = np.array([[500, 11000], [200, -2000]], dtype=">i2")
        geo_file.data = arr.tobytes()
        geo_file.latitude = 0.0
        geo_file.longitude = 0.0

        result = _parse_tile(geo_file)
        assert result is not None
        tile_arr, *_ = result
        # 11000 > _SRTM_RAW_MAX (10000) → -32768
        assert tile_arr[0, 1] == -32768.0
        # -2000 < _SRTM_RAW_MIN (-1000) → -32768
        assert tile_arr[1, 1] == -32768.0
        # Valid cell stays
        assert tile_arr[0, 0] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# fetch_dem — mocked srtm.get_data()
# ---------------------------------------------------------------------------

def _make_bbox(size=0.1):
    return BoundingBox(
        min_lat=46.0, max_lat=46.0 + size,
        min_lon=7.0,  max_lon=7.0 + size,
    )


def _make_mock_elevation_data(elev_value=500):
    """Return a mock srtm elevation_data that yields a trivial GeoElevationFile."""
    N = 4
    raw = np.full((N, N), elev_value, dtype=">i2").tobytes()

    geo_file = MagicMock()
    geo_file.square_side = N
    geo_file.data = raw
    geo_file.latitude = 46.0
    geo_file.longitude = 7.0

    elevation_data = MagicMock()
    elevation_data.get_file.return_value = geo_file
    return elevation_data


class TestFetchDem:
    def test_returns_elevation_grid(self):
        from georeel.core.elevation_grid import ElevationGrid
        bbox = _make_bbox()
        mock_data = _make_mock_elevation_data(500)

        with patch("georeel.core.dem_fetcher.srtm.get_data", return_value=mock_data):
            grid = fetch_dem(bbox)

        assert isinstance(grid, ElevationGrid)

    def test_grid_bbox_matches_input(self):
        bbox = _make_bbox()
        mock_data = _make_mock_elevation_data(100)

        with patch("georeel.core.dem_fetcher.srtm.get_data", return_value=mock_data):
            grid = fetch_dem(bbox)

        assert grid.min_lat == pytest.approx(bbox.min_lat)
        assert grid.max_lat == pytest.approx(bbox.max_lat)
        assert grid.min_lon == pytest.approx(bbox.min_lon)
        assert grid.max_lon == pytest.approx(bbox.max_lon)

    def test_grid_data_is_float32(self):
        bbox = _make_bbox()
        mock_data = _make_mock_elevation_data(250)

        with patch("georeel.core.dem_fetcher.srtm.get_data", return_value=mock_data):
            grid = fetch_dem(bbox)

        assert grid.data.dtype == np.float32

    def test_progress_callback_called(self):
        bbox = _make_bbox()
        mock_data = _make_mock_elevation_data(100)
        calls = []

        with patch("georeel.core.dem_fetcher.srtm.get_data", return_value=mock_data):
            fetch_dem(bbox, progress_callback=lambda done, total: calls.append((done, total)))

        assert len(calls) > 0
        # Each call: done <= total
        for done, total in calls:
            assert done <= total

    def test_srtm_init_failure_raises_dem_fetch_error(self):
        bbox = _make_bbox()
        with patch("georeel.core.dem_fetcher.srtm.get_data", side_effect=RuntimeError("network")):
            with pytest.raises(DemFetchError, match="SRTM"):
                fetch_dem(bbox)

    def test_none_geo_file_skipped(self):
        """A tile with None geo_file should be silently skipped."""
        bbox = _make_bbox()
        elevation_data = MagicMock()
        elevation_data.get_file.return_value = None

        with patch("georeel.core.dem_fetcher.srtm.get_data", return_value=elevation_data):
            # Should not raise; void-fill will handle the empty grid
            grid = fetch_dem(bbox)
        assert grid is not None

    def test_grid_rows_cols_positive(self):
        bbox = _make_bbox(size=0.5)
        mock_data = _make_mock_elevation_data(300)

        with patch("georeel.core.dem_fetcher.srtm.get_data", return_value=mock_data):
            grid = fetch_dem(bbox)

        assert grid.rows >= 2
        assert grid.cols >= 2
