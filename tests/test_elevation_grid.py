"""Tests for ElevationGrid."""

import numpy as np
import pytest
from georeel.core.elevation_grid import ElevationGrid


def _make_grid(rows=4, cols=4, fill=0.0):
    data = np.full((rows, cols), fill, dtype=np.float32)
    return ElevationGrid(data=data, min_lat=0.0, max_lat=1.0, min_lon=0.0, max_lon=1.0)


def _ramp_grid():
    """4x4 grid where data[r,c] = r*10 + c (increasing south→north, west→east)."""
    data = np.array([[r * 10.0 + c for c in range(4)] for r in range(4)], dtype=np.float32)
    # Row 0 = max_lat (north), row 3 = min_lat (south)
    return ElevationGrid(data=data, min_lat=0.0, max_lat=3.0, min_lon=0.0, max_lon=3.0)


class TestElevationGridProperties:
    def test_rows_cols(self):
        g = _make_grid(rows=5, cols=7)
        assert g.rows == 5
        assert g.cols == 7

    def test_data_stored_correctly(self):
        data = np.ones((3, 3), dtype=np.float32) * 42.0
        g = ElevationGrid(data=data, min_lat=0.0, max_lat=1.0, min_lon=0.0, max_lon=1.0)
        np.testing.assert_array_equal(g.data, data)


class TestElevationAt:
    def test_corner_nw(self):
        # max_lat, min_lon = row 0, col 0
        g = _ramp_grid()
        result = g.elevation_at(lat=3.0, lon=0.0)
        assert result == pytest.approx(0.0)  # data[0,0] = 0

    def test_corner_se(self):
        # min_lat, max_lon = last row, last col
        g = _ramp_grid()
        result = g.elevation_at(lat=0.0, lon=3.0)
        assert result == pytest.approx(33.0)  # data[3,3] = 3*10+3

    def test_corner_ne(self):
        g = _ramp_grid()
        result = g.elevation_at(lat=3.0, lon=3.0)
        assert result == pytest.approx(3.0)  # data[0,3] = 0*10+3

    def test_corner_sw(self):
        g = _ramp_grid()
        result = g.elevation_at(lat=0.0, lon=0.0)
        assert result == pytest.approx(30.0)  # data[3,0] = 3*10+0

    def test_uniform_grid_returns_fill(self):
        g = _make_grid(fill=250.0)
        assert g.elevation_at(0.5, 0.5) == pytest.approx(250.0)

    def test_center_interpolation(self):
        # 2×2 grid: NW=0, NE=10, SW=20, SE=30
        data = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)
        g = ElevationGrid(data=data, min_lat=0.0, max_lat=1.0, min_lon=0.0, max_lon=1.0)
        # Center (lat=0.5, lon=0.5) should bilinearly interpolate to 15.0
        result = g.elevation_at(0.5, 0.5)
        assert result == pytest.approx(15.0)

    def test_clamps_lat_below_min(self):
        g = _make_grid(fill=100.0)
        # Should not raise; clamped to min_lat boundary
        result = g.elevation_at(-1.0, 0.5)
        assert result == pytest.approx(100.0)

    def test_clamps_lat_above_max(self):
        g = _make_grid(fill=100.0)
        result = g.elevation_at(2.0, 0.5)
        assert result == pytest.approx(100.0)

    def test_clamps_lon_out_of_range(self):
        g = _make_grid(fill=55.0)
        result = g.elevation_at(0.5, 5.0)
        assert result == pytest.approx(55.0)


class TestElevationGridSerialization:
    def test_round_trip_bytes(self):
        data = np.array([[100.0, 200.0], [300.0, 400.0]], dtype=np.float32)
        g = ElevationGrid(data=data, min_lat=10.0, max_lat=11.0, min_lon=20.0, max_lon=21.0)
        raw = g.to_bytes()
        g2 = ElevationGrid.from_bytes(raw, rows=2, cols=2,
                                      min_lat=10.0, max_lat=11.0,
                                      min_lon=20.0, max_lon=21.0)
        np.testing.assert_array_almost_equal(g.data, g2.data, decimal=5)
        assert g2.min_lat == 10.0
        assert g2.max_lat == 11.0

    def test_from_bytes_returns_copy(self):
        data = np.ones((2, 2), dtype=np.float32)
        g = ElevationGrid(data=data, min_lat=0.0, max_lat=1.0, min_lon=0.0, max_lon=1.0)
        raw = g.to_bytes()
        g2 = ElevationGrid.from_bytes(raw, 2, 2, 0.0, 1.0, 0.0, 1.0)
        # Mutating g2.data should not affect original bytes
        g2.data[0, 0] = 999.0
        g3 = ElevationGrid.from_bytes(raw, 2, 2, 0.0, 1.0, 0.0, 1.0)
        assert g3.data[0, 0] == pytest.approx(1.0)

    def test_to_bytes_is_float32(self):
        g = _make_grid(rows=3, cols=3, fill=1.0)
        raw = g.to_bytes()
        assert len(raw) == 3 * 3 * 4  # 4 bytes per float32
