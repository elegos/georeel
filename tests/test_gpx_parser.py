"""Tests for gpx_parser.parse_gpx."""

import pytest
from datetime import timezone
from pathlib import Path
from georeel.core.gpx_parser import parse_gpx, GpxParseError


def _write_gpx(tmp_path: Path, content: str) -> str:
    p = tmp_path / "track.gpx"
    p.write_text(content, encoding="utf-8")
    return str(p)


_GPX_HEADER = '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
_GPX_FOOTER = "</gpx>"


def _gpx_with_points(points: list[dict]) -> str:
    trkpts = ""
    for p in points:
        ts = f'<time>{p["time"]}</time>' if "time" in p else ""
        elev = f'<ele>{p["ele"]}</ele>' if "ele" in p else ""
        trkpts += f'<trkpt lat="{p["lat"]}" lon="{p["lon"]}">{ts}{elev}</trkpt>'
    return f'{_GPX_HEADER}<trk><trkseg>{trkpts}</trkseg></trk>{_GPX_FOOTER}'


class TestParseGpxSuccess:
    def test_single_point(self, tmp_path):
        gpx = _gpx_with_points([{"lat": 48.8566, "lon": 2.3522}])
        tps, bbox = parse_gpx(_write_gpx(tmp_path, gpx))
        assert len(tps) == 1
        assert tps[0].latitude == pytest.approx(48.8566)
        assert tps[0].longitude == pytest.approx(2.3522)

    def test_multiple_points(self, tmp_path):
        points = [
            {"lat": 48.0, "lon": 2.0},
            {"lat": 48.1, "lon": 2.1},
            {"lat": 48.2, "lon": 2.2},
        ]
        tps, bbox = parse_gpx(_write_gpx(tmp_path, _gpx_with_points(points)))
        assert len(tps) == 3

    def test_elevation_parsed(self, tmp_path):
        gpx = _gpx_with_points([{"lat": 48.0, "lon": 2.0, "ele": 150.5}])
        tps, _ = parse_gpx(_write_gpx(tmp_path, gpx))
        assert tps[0].elevation == pytest.approx(150.5)

    def test_no_elevation_is_none(self, tmp_path):
        gpx = _gpx_with_points([{"lat": 48.0, "lon": 2.0}])
        tps, _ = parse_gpx(_write_gpx(tmp_path, gpx))
        assert tps[0].elevation is None

    def test_timestamp_parsed_as_utc(self, tmp_path):
        gpx = _gpx_with_points([{"lat": 48.0, "lon": 2.0, "time": "2023-06-01T10:30:00Z"}])
        tps, _ = parse_gpx(_write_gpx(tmp_path, gpx))
        assert tps[0].timestamp is not None
        assert tps[0].timestamp.tzinfo is not None
        assert tps[0].timestamp.tzinfo == timezone.utc

    def test_no_timestamp_is_none(self, tmp_path):
        gpx = _gpx_with_points([{"lat": 48.0, "lon": 2.0}])
        tps, _ = parse_gpx(_write_gpx(tmp_path, gpx))
        assert tps[0].timestamp is None


class TestParseGpxBoundingBox:
    def test_bbox_min_max(self, tmp_path):
        points = [
            {"lat": 48.0, "lon": 2.0},
            {"lat": 49.0, "lon": 3.0},
            {"lat": 47.5, "lon": 1.5},
        ]
        _, bbox = parse_gpx(_write_gpx(tmp_path, _gpx_with_points(points)))
        assert bbox.min_lat == pytest.approx(47.5)
        assert bbox.max_lat == pytest.approx(49.0)
        assert bbox.min_lon == pytest.approx(1.5)
        assert bbox.max_lon == pytest.approx(3.0)

    def test_single_point_bbox_is_point(self, tmp_path):
        gpx = _gpx_with_points([{"lat": 51.5, "lon": -0.12}])
        _, bbox = parse_gpx(_write_gpx(tmp_path, gpx))
        assert bbox.min_lat == bbox.max_lat
        assert bbox.min_lon == bbox.max_lon


class TestParseGpxErrors:
    def test_nonexistent_file_raises(self):
        with pytest.raises(GpxParseError):
            parse_gpx("/nonexistent/path/track.gpx")

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "empty.gpx"
        p.write_text("", encoding="utf-8")
        with pytest.raises(GpxParseError):
            parse_gpx(str(p))

    def test_invalid_xml_raises(self, tmp_path):
        p = tmp_path / "bad.gpx"
        p.write_text("not xml at all", encoding="utf-8")
        with pytest.raises(GpxParseError):
            parse_gpx(str(p))

    def test_gpx_no_trackpoints_raises(self, tmp_path):
        # Valid GPX but with no track points
        gpx = f"{_GPX_HEADER}<trk><trkseg></trkseg></trk>{_GPX_FOOTER}"
        with pytest.raises(GpxParseError, match="no trackpoints"):
            parse_gpx(_write_gpx(tmp_path, gpx))

    def test_gpx_no_track_element_raises(self, tmp_path):
        # Valid GPX but with no track at all
        gpx = f"{_GPX_HEADER}{_GPX_FOOTER}"
        with pytest.raises(GpxParseError):
            parse_gpx(_write_gpx(tmp_path, gpx))
