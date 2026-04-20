"""Tests for POST /api/v1/gpx/parse and /api/v1/gpx/clean."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from georeel.server.app import app

_GPX_HEADER = '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
_GPX_FOOTER = "</gpx>"


def _gpx_bytes(points: list[dict]) -> bytes:
    trkpts = ""
    for p in points:
        ts = f'<time>{p["time"]}</time>' if "time" in p else ""
        ele = f'<ele>{p["ele"]}</ele>' if "ele" in p else ""
        trkpts += f'<trkpt lat="{p["lat"]}" lon="{p["lon"]}">{ts}{ele}</trkpt>'
    return (
        f'{_GPX_HEADER}<trk><trkseg>{trkpts}</trkseg></trk>{_GPX_FOOTER}'
    ).encode()


_POINTS = [
    {"lat": 46.0, "lon": 11.0, "ele": 100.0, "time": "2024-06-01T08:00:00Z"},
    {"lat": 46.1, "lon": 11.1, "ele": 200.0, "time": "2024-06-01T08:10:00Z"},
    {"lat": 46.2, "lon": 11.2, "ele": 150.0, "time": "2024-06-01T08:20:00Z"},
]


class TestGpxParse:
    def test_parse_valid_gpx(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/parse",
                files={"file": ("track.gpx", _gpx_bytes(_POINTS), "application/gpx+xml")},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data["trackpoints"]) == 3
        assert data["trackpoints"][0]["latitude"] == pytest.approx(46.0)
        assert data["trackpoints"][0]["elevation"] == pytest.approx(100.0)

    def test_parse_returns_bounding_box(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/parse",
                files={"file": ("track.gpx", _gpx_bytes(_POINTS), "application/gpx+xml")},
            )
        bbox = response.json()["bounding_box"]
        assert bbox["min_lat"] == pytest.approx(46.0)
        assert bbox["max_lat"] == pytest.approx(46.2)
        assert bbox["min_lon"] == pytest.approx(11.0)
        assert bbox["max_lon"] == pytest.approx(11.2)

    def test_parse_returns_stats(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/parse",
                files={"file": ("track.gpx", _gpx_bytes(_POINTS), "application/gpx+xml")},
            )
        stats = response.json()["stats"]
        assert stats["point_count"] == 3
        assert stats["total_distance_m"] > 0
        assert stats["elevation_gain_m"] == pytest.approx(100.0)

    def test_parse_empty_gpx_returns_422(self) -> None:
        empty = f"{_GPX_HEADER}{_GPX_FOOTER}".encode()
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/parse",
                files={"file": ("empty.gpx", empty, "application/gpx+xml")},
            )
        assert response.status_code == 422

    def test_parse_invalid_file_returns_422(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/parse",
                files={"file": ("bad.gpx", b"not xml at all", "application/gpx+xml")},
            )
        assert response.status_code == 422

    def test_parse_no_elevation(self) -> None:
        points = [{"lat": 46.0, "lon": 11.0}, {"lat": 46.1, "lon": 11.1}]
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/parse",
                files={"file": ("track.gpx", _gpx_bytes(points), "application/gpx+xml")},
            )
        assert response.status_code == 200
        assert response.json()["trackpoints"][0]["elevation"] is None


class TestGpxClean:
    def _trackpoints(self) -> list[dict]:
        return [
            {"latitude": 46.0, "longitude": 11.0, "elevation": 100.0,
             "timestamp": "2024-06-01T08:00:00Z", "is_reconstructed": False},
            {"latitude": 46.1, "longitude": 11.1, "elevation": 200.0,
             "timestamp": "2024-06-01T08:10:00Z", "is_reconstructed": False},
            {"latitude": 46.2, "longitude": 11.2, "elevation": 150.0,
             "timestamp": "2024-06-01T08:20:00Z", "is_reconstructed": False},
        ]

    def test_clean_mode_none_returns_same_points(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/clean",
                json={"trackpoints": self._trackpoints(), "mode": "none"},
            )
        assert response.status_code == 200
        assert len(response.json()["trackpoints"]) == 3

    def test_clean_stats_no_nullified(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/clean",
                json={"trackpoints": self._trackpoints(), "mode": "none"},
            )
        stats = response.json()["stats"]
        assert stats["nullified_removed"] == 0
        assert stats["holes_filled"] == 0

    def test_clean_removes_null_island(self) -> None:
        points = [
            {"latitude": 0.0, "longitude": 0.0, "elevation": None,
             "timestamp": "2024-06-01T08:00:00Z", "is_reconstructed": False},
            {"latitude": 46.0, "longitude": 11.0, "elevation": 100.0,
             "timestamp": "2024-06-01T08:01:00Z", "is_reconstructed": False},
            {"latitude": 46.1, "longitude": 11.1, "elevation": 200.0,
             "timestamp": "2024-06-01T08:02:00Z", "is_reconstructed": False},
        ]
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/clean",
                json={"trackpoints": points, "mode": "none"},
            )
        data = response.json()
        assert data["stats"]["nullified_removed"] == 1
        assert len(data["trackpoints"]) == 2

    def test_clean_empty_points_returns_empty(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/gpx/clean",
                json={"trackpoints": [], "mode": "none"},
            )
        assert response.status_code == 200
        assert response.json()["trackpoints"] == []
