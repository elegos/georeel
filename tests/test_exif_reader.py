"""Tests for exif_reader."""

import pytest
from datetime import datetime
from pathlib import Path
from PIL import Image
from georeel.core.exif_reader import (
    read_photo_metadata,
    _dms_to_decimal,
    _parse_gps,
    _parse_timestamp,
)


class TestDmsToDecimal:
    def test_positive_north(self):
        # 48°51'30" N = 48 + 51/60 + 30/3600 = 48.858333...
        result = _dms_to_decimal((48, 51, 30), "N")
        assert result == pytest.approx(48.858333, abs=1e-4)

    def test_south_is_negative(self):
        result = _dms_to_decimal((33, 52, 0), "S")
        assert result < 0

    def test_west_is_negative(self):
        result = _dms_to_decimal((2, 21, 0), "W")
        assert result < 0

    def test_east_is_positive(self):
        result = _dms_to_decimal((2, 21, 8.4), "E")
        assert result > 0

    def test_zero_coords(self):
        result = _dms_to_decimal((0, 0, 0), "N")
        assert result == pytest.approx(0.0)

    def test_float_values_accepted(self):
        result = _dms_to_decimal((48.0, 51.0, 30.0), "N")
        assert result > 48.0


class TestParseGps:
    def _gps_ifd(self, lat, lat_ref, lon, lon_ref):
        return {1: lat_ref, 2: lat, 3: lon_ref, 4: lon}

    def test_valid_north_east(self):
        ifd = self._gps_ifd((48, 51, 30), "N", (2, 21, 8), "E")
        result = _parse_gps(ifd)
        assert result is not None
        lat, lon = result
        assert lat > 0
        assert lon > 0

    def test_valid_south_west(self):
        ifd = self._gps_ifd((33, 52, 0), "S", (151, 12, 0), "W")
        result = _parse_gps(ifd)
        assert result is not None
        lat, lon = result
        assert lat < 0
        assert lon < 0

    def test_missing_lat_returns_none(self):
        # GPS IFD without latitude key
        result = _parse_gps({3: "E", 4: (2, 21, 0)})
        assert result is None

    def test_empty_ifd_returns_none(self):
        result = _parse_gps({})
        assert result is None

    def test_invalid_type_returns_none(self):
        result = _parse_gps({1: "N", 2: "not_a_tuple", 3: "E", 4: (2, 0, 0)})
        assert result is None


class TestParseTimestamp:
    def test_valid_exif_timestamp(self):
        result = _parse_timestamp("2023:06:01 10:30:00")
        assert result is not None
        assert result.year == 2023
        assert result.month == 6
        assert result.day == 1
        assert result.hour == 10

    def test_none_input_returns_none(self):
        assert _parse_timestamp(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_timestamp("") is None

    def test_invalid_format_returns_none(self):
        assert _parse_timestamp("2023-06-01 10:30:00") is None  # wrong separators

    def test_whitespace_stripped(self):
        result = _parse_timestamp("  2023:06:01 10:30:00  ")
        assert result is not None

    def test_returns_naive_datetime(self):
        result = _parse_timestamp("2023:06:01 10:30:00")
        assert result.tzinfo is None


class TestReadPhotoMetadata:
    def test_nonexistent_file_returns_empty_metadata(self):
        meta = read_photo_metadata("/nonexistent/photo.jpg")
        assert meta.path == "/nonexistent/photo.jpg"
        assert meta.timestamp is None
        assert meta.latitude is None
        assert meta.longitude is None

    def test_plain_jpeg_no_exif_returns_path(self, tmp_path):
        img_path = tmp_path / "no_exif.jpg"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(str(img_path), format="JPEG")
        meta = read_photo_metadata(str(img_path))
        assert meta.path == str(img_path)
        assert meta.timestamp is None
        assert meta.latitude is None

    def test_text_file_returns_empty_metadata(self, tmp_path):
        bad = tmp_path / "not_an_image.jpg"
        bad.write_text("this is not an image")
        meta = read_photo_metadata(str(bad))
        assert meta.timestamp is None
        assert meta.latitude is None

    def test_png_file_returns_metadata(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (20, 20)).save(str(img_path), format="PNG")
        meta = read_photo_metadata(str(img_path))
        assert meta.path == str(img_path)
        # PNG without EXIF: no timestamp or GPS
        assert meta.timestamp is None
