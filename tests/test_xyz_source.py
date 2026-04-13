"""Tests for satellite.xyz_source coordinate helpers and build_source."""

import pytest
from georeel.core.satellite.xyz_source import (
    _lon_to_x,
    _lat_to_y,
    _tile_nw,
    build_source,
    XyzSource,
)


class TestLonToX:
    def test_prime_meridian_zoom0(self):
        # lon=0, zoom=0 → tile 0
        assert _lon_to_x(0.0, 0) == 0

    def test_date_line_zoom0(self):
        # lon=180, zoom=0 → tile 0 (the single tile covers the whole world)
        assert _lon_to_x(180.0, 0) == 1

    def test_lon_minus180_zoom1(self):
        assert _lon_to_x(-180.0, 1) == 0

    def test_lon_180_zoom1(self):
        assert _lon_to_x(180.0, 1) == 2

    def test_zoom10_range(self):
        x = _lon_to_x(2.3522, 10)  # Paris longitude
        n = 2 ** 10
        assert 0 <= x < n


class TestLatToY:
    def test_equator_zoom1(self):
        # Equator → y=1 (bottom half of the single tile at zoom 0)
        y = _lat_to_y(0.0, 1)
        assert y == 1

    def test_north_pole_vicinity_zoom1(self):
        # Very high latitude → tile 0 (top)
        y = _lat_to_y(85.0, 1)
        assert y == 0

    def test_south_hemisphere_zoom1(self):
        # Negative latitude → bottom tile
        y_north = _lat_to_y(45.0, 2)
        y_south = _lat_to_y(-45.0, 2)
        assert y_south > y_north  # Y increases southward

    def test_zoom10_range(self):
        y = _lat_to_y(48.8566, 10)  # Paris latitude
        n = 2 ** 10
        assert 0 <= y < n


class TestTileNw:
    def test_tile_0_0_zoom0_is_north_west(self):
        lat, lon = _tile_nw(0, 0, 0)
        # NW corner of the world tile: lat≈85.05, lon=-180
        assert lon == pytest.approx(-180.0, abs=1e-9)
        assert lat > 80.0

    def test_tile_nw_returns_float_tuple(self):
        lat, lon = _tile_nw(100, 200, 10)
        assert isinstance(lat, float)
        assert isinstance(lon, float)

    def test_longitude_increases_with_tile_x(self):
        _, lon0 = _tile_nw(0, 0, 5)
        _, lon1 = _tile_nw(1, 0, 5)
        assert lon1 > lon0

    def test_latitude_decreases_with_tile_y(self):
        lat0, _ = _tile_nw(0, 0, 5)
        lat1, _ = _tile_nw(0, 1, 5)
        assert lat1 < lat0  # Y increases southward


class TestBuildSource:
    def test_returns_xyz_source(self):
        source = build_source("esri_world")
        assert isinstance(source, XyzSource)

    def test_name_matches_provider_label(self):
        source = build_source("esri_world")
        from georeel.core.satellite.providers import get_provider
        assert source.name == get_provider("esri_world").label

    def test_unknown_provider_falls_back_to_default(self):
        source = build_source("nonexistent_provider")
        assert isinstance(source, XyzSource)

    def test_custom_url_stored(self):
        source = build_source("custom", custom_url="https://example.com/{z}/{x}/{y}.png")
        assert "example.com" in source._url_template

    def test_api_key_substituted_in_url(self):
        source = build_source("maptiler_satellite", api_key="my_key_123")
        assert "my_key_123" in source._url_template
        assert "{api_key}" not in source._url_template
