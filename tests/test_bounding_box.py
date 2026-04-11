"""Tests for BoundingBox."""

import math
import pytest
from georeel.core.bounding_box import BoundingBox, _M_PER_DEG_LAT


class TestBoundingBoxConstruction:
    def test_basic_fields(self):
        bb = BoundingBox(min_lat=10.0, max_lat=20.0, min_lon=5.0, max_lon=15.0)
        assert bb.min_lat == 10.0
        assert bb.max_lat == 20.0
        assert bb.min_lon == 5.0
        assert bb.max_lon == 15.0

    def test_frozen(self):
        bb = BoundingBox(1.0, 2.0, 3.0, 4.0)
        with pytest.raises((AttributeError, TypeError)):
            bb.min_lat = 99.0  # type: ignore[misc]

    def test_negative_coordinates(self):
        bb = BoundingBox(min_lat=-45.0, max_lat=-10.0, min_lon=-80.0, max_lon=-50.0)
        assert bb.min_lat == -45.0
        assert bb.min_lon == -80.0

    def test_single_point_bbox(self):
        bb = BoundingBox(min_lat=48.8566, max_lat=48.8566, min_lon=2.3522, max_lon=2.3522)
        assert bb.min_lat == bb.max_lat


class TestBoundingBoxExpand:
    def test_expand_increases_all_sides(self):
        bb = BoundingBox(min_lat=10.0, max_lat=20.0, min_lon=5.0, max_lon=15.0)
        expanded = bb.expand(1000.0)
        assert expanded.min_lat < bb.min_lat
        assert expanded.max_lat > bb.max_lat
        assert expanded.min_lon < bb.min_lon
        assert expanded.max_lon > bb.max_lon

    def test_expand_zero_is_identity(self):
        bb = BoundingBox(min_lat=10.0, max_lat=20.0, min_lon=5.0, max_lon=15.0)
        expanded = bb.expand(0.0)
        assert expanded.min_lat == pytest.approx(bb.min_lat)
        assert expanded.max_lat == pytest.approx(bb.max_lat)
        assert expanded.min_lon == pytest.approx(bb.min_lon)
        assert expanded.max_lon == pytest.approx(bb.max_lon)

    def test_expand_lat_delta_correct(self):
        bb = BoundingBox(min_lat=10.0, max_lat=20.0, min_lon=5.0, max_lon=15.0)
        margin_m = 1000.0
        expanded = bb.expand(margin_m)
        expected_lat_delta = margin_m / _M_PER_DEG_LAT
        assert expanded.min_lat == pytest.approx(bb.min_lat - expected_lat_delta, abs=1e-10)
        assert expanded.max_lat == pytest.approx(bb.max_lat + expected_lat_delta, abs=1e-10)

    def test_expand_lon_delta_depends_on_latitude(self):
        # At the equator, lon_delta == lat_delta; at high latitudes, it is larger.
        bb_equator = BoundingBox(min_lat=-1.0, max_lat=1.0, min_lon=-1.0, max_lon=1.0)
        bb_high = BoundingBox(min_lat=60.0, max_lat=61.0, min_lon=-1.0, max_lon=1.0)
        margin_m = 10_000.0
        expanded_eq = bb_equator.expand(margin_m)
        expanded_hi = bb_high.expand(margin_m)
        lon_delta_eq = expanded_eq.max_lon - bb_equator.max_lon
        lon_delta_hi = expanded_hi.max_lon - bb_high.max_lon
        # At high latitude the cosine is smaller, so lon_delta is larger.
        assert lon_delta_hi > lon_delta_eq

    def test_expand_returns_new_instance(self):
        bb = BoundingBox(1.0, 2.0, 3.0, 4.0)
        expanded = bb.expand(500.0)
        assert expanded is not bb

    def test_expand_large_margin(self):
        bb = BoundingBox(min_lat=0.0, max_lat=1.0, min_lon=0.0, max_lon=1.0)
        expanded = bb.expand(100_000.0)
        assert expanded.min_lat < 0.0
        assert expanded.min_lon < 0.0


class TestBoundingBoxStr:
    def test_str_contains_coordinates(self):
        bb = BoundingBox(min_lat=1.23456, max_lat=2.34567, min_lon=3.45678, max_lon=4.56789)
        s = str(bb)
        assert "1.23456" in s
        assert "2.34567" in s
        assert "3.45678" in s
        assert "4.56789" in s

    def test_str_format_arrow(self):
        bb = BoundingBox(0.0, 1.0, 0.0, 1.0)
        assert "→" in str(bb)
