"""Tests for PhotoMetadata."""

import pytest
from datetime import datetime
from georeel.core.photo_metadata import PhotoMetadata


class TestPhotoMetadataProperties:
    def test_has_gps_both_present(self):
        p = PhotoMetadata(path="/img.jpg", timestamp=None, latitude=48.0, longitude=2.0)
        assert p.has_gps is True

    def test_has_gps_lat_none(self):
        p = PhotoMetadata(path="/img.jpg", timestamp=None, latitude=None, longitude=2.0)
        assert p.has_gps is False

    def test_has_gps_lon_none(self):
        p = PhotoMetadata(path="/img.jpg", timestamp=None, latitude=48.0, longitude=None)
        assert p.has_gps is False

    def test_has_gps_both_none(self):
        p = PhotoMetadata(path="/img.jpg", timestamp=None, latitude=None, longitude=None)
        assert p.has_gps is False

    def test_has_timestamp_present(self):
        ts = datetime(2023, 6, 1, 10, 30)
        p = PhotoMetadata(path="/img.jpg", timestamp=ts, latitude=None, longitude=None)
        assert p.has_timestamp is True

    def test_has_timestamp_none(self):
        p = PhotoMetadata(path="/img.jpg", timestamp=None, latitude=None, longitude=None)
        assert p.has_timestamp is False

    def test_frozen_immutable(self):
        p = PhotoMetadata(path="/img.jpg", timestamp=None, latitude=1.0, longitude=2.0)
        with pytest.raises((AttributeError, TypeError)):
            p.path = "/other.jpg"  # type: ignore[misc]

    def test_equality_by_value(self):
        ts = datetime(2023, 1, 1)
        p1 = PhotoMetadata(path="/a.jpg", timestamp=ts, latitude=1.0, longitude=2.0)
        p2 = PhotoMetadata(path="/a.jpg", timestamp=ts, latitude=1.0, longitude=2.0)
        assert p1 == p2

    def test_inequality_different_path(self):
        p1 = PhotoMetadata(path="/a.jpg", timestamp=None, latitude=None, longitude=None)
        p2 = PhotoMetadata(path="/b.jpg", timestamp=None, latitude=None, longitude=None)
        assert p1 != p2
