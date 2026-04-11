"""Tests for gpx_cleaner — hole detection and repair."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from georeel.core.gpx_cleaner import (
    REPAIR_GROUND,
    REPAIR_NONE,
    REPAIR_STREET,
    CleanStats,
    _haversine,
    _interp_latlon,
    _is_nullified,
    _resample_route,
    detect_and_repair,
)
from georeel.core.trackpoint import Trackpoint


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _ts(offset_s: float = 0.0) -> datetime:
    base = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=offset_s)


def _pt(lat: float, lon: float, t: float | None = None, elev: float | None = None) -> Trackpoint:
    return Trackpoint(
        latitude=lat,
        longitude=lon,
        elevation=elev,
        timestamp=_ts(t) if t is not None else None,
    )


# ── _haversine ────────────────────────────────────────────────────────────────

class TestHaversine:
    def test_same_point_is_zero(self):
        assert _haversine(48.0, 2.0, 48.0, 2.0) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_paris_london(self):
        # Paris (48.8566 N, 2.3522 E) → London (51.5074 N, 0.1278 W)
        d = _haversine(48.8566, 2.3522, 51.5074, -0.1278)
        assert 330_000 < d < 345_000  # ≈ 340 km

    def test_symmetry(self):
        d1 = _haversine(10.0, 20.0, 11.0, 21.0)
        d2 = _haversine(11.0, 21.0, 10.0, 20.0)
        assert d1 == pytest.approx(d2)


# ── _is_nullified ─────────────────────────────────────────────────────────────

class TestIsNullified:
    def test_zero_zero_is_nullified(self):
        pt = _pt(0.0, 0.0, t=0)
        assert _is_nullified(pt, [], 83.3, 50_000, max_gap_s=30.0)

    def test_first_non_null_point_is_valid(self):
        pt = _pt(48.0, 2.0, t=0)
        assert not _is_nullified(pt, [], 83.3, 50_000, max_gap_s=30.0)

    def test_normal_speed_is_valid(self):
        a = _pt(48.0, 2.0, t=0)
        b = _pt(48.001, 2.001, t=10)  # ~130 m in 10 s ≈ 13 m/s
        assert not _is_nullified(b, [a], 83.3, 50_000, max_gap_s=30.0)

    def test_too_fast_with_timestamps(self):
        a = _pt(48.0, 2.0, t=0)
        # 1 degree of lat ≈ 111 km in 1 second = 111 000 m/s >> 83.3; gap=1 < max_gap_s=30
        b = _pt(49.0, 2.0, t=1)
        assert _is_nullified(b, [a], 83.3, 50_000, max_gap_s=30.0)

    def test_large_jump_no_timestamps(self):
        a = _pt(48.0, 2.0)
        b = _pt(48.0, 12.0)  # ~800 km away
        assert _is_nullified(b, [a], 83.3, 50_000, max_gap_s=30.0)

    def test_reasonable_jump_no_timestamps(self):
        a = _pt(48.0, 2.0)
        b = _pt(48.1, 2.1)  # ≈ 13 km
        assert not _is_nullified(b, [a], 83.3, 50_000, max_gap_s=30.0)

    def test_large_time_gap_not_nullified(self):
        """A point reachable only via large time gap should not be discarded."""
        a = _pt(48.0, 2.0, t=0)
        b = _pt(48.1, 2.0, t=60)  # 11 km in 60 s → speed > 83 m/s, but gap >= max_gap_s
        assert not _is_nullified(b, [a], 83.3, 50_000, max_gap_s=30.0)


# ── _interp_latlon ────────────────────────────────────────────────────────────

class TestInterpLatlon:
    def test_midpoint(self):
        a = _pt(0.0, 0.0)
        b = _pt(2.0, 2.0)
        pts = _interp_latlon(a, b, 1)
        assert len(pts) == 1
        assert pts[0] == pytest.approx((1.0, 1.0))

    def test_n_zero_returns_empty(self):
        a = _pt(0.0, 0.0)
        b = _pt(1.0, 1.0)
        assert _interp_latlon(a, b, 0) == []

    def test_three_points_evenly_spaced(self):
        a = _pt(0.0, 0.0)
        b = _pt(4.0, 0.0)
        pts = _interp_latlon(a, b, 3)
        assert len(pts) == 3
        lats = [p[0] for p in pts]
        assert lats == pytest.approx([1.0, 2.0, 3.0])


# ── _resample_route ───────────────────────────────────────────────────────────

class TestResampleRoute:
    def test_single_segment_midpoint(self):
        route = [(0.0, 0.0), (2.0, 0.0)]
        pts = _resample_route(route, 1)
        assert len(pts) == 1
        lat, lon = pts[0]
        assert lat == pytest.approx(1.0, abs=0.01)

    def test_n_zero_returns_empty(self):
        route = [(0.0, 0.0), (1.0, 0.0)]
        assert _resample_route(route, 0) == []

    def test_three_points_on_two_segment_route(self):
        # Route: (0,0) → (1,0) → (3,0) — total length ≈ 3 degrees lat
        route = [(0.0, 0.0), (1.0, 0.0), (3.0, 0.0)]
        pts = _resample_route(route, 3)
        assert len(pts) == 3
        lats = [p[0] for p in pts]
        # Evenly spaced at 0.75, 1.5, 2.25 of total
        assert lats[0] < lats[1] < lats[2]

    def test_degenerate_zero_length_route(self):
        route = [(1.0, 1.0), (1.0, 1.0)]
        pts = _resample_route(route, 2)
        assert len(pts) == 2
        assert all(p == pytest.approx((1.0, 1.0)) for p in pts)


# ── detect_and_repair — mode=none ────────────────────────────────────────────

class TestDetectAndRepairNone:
    def test_no_changes_in_none_mode(self):
        pts = [_pt(1.0, 1.0, t=0), _pt(1.001, 1.001, t=5)]
        result, stats = detect_and_repair(pts, REPAIR_NONE)
        assert len(result) == 2
        assert stats.nullified_removed == 0
        assert stats.holes_filled == 0

    def test_removes_zero_zero_even_in_none_mode(self):
        pts = [_pt(1.0, 1.0, t=0), _pt(0.0, 0.0, t=1), _pt(1.001, 1.001, t=2)]
        result, stats = detect_and_repair(pts, REPAIR_NONE)
        assert len(result) == 2
        assert stats.nullified_removed == 1

    def test_removes_teleporting_point(self):
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(58.0, 2.0, t=1),   # 1100 km in 1 s → nullified
            _pt(48.001, 2.001, t=2),
        ]
        result, stats = detect_and_repair(pts, REPAIR_NONE)
        assert len(result) == 2
        assert stats.nullified_removed == 1

    def test_empty_input(self):
        result, stats = detect_and_repair([], REPAIR_NONE)
        assert result == []
        assert stats.nullified_removed == 0


# ── detect_and_repair — mode=ground ─────────────────────────────────────────

class TestDetectAndRepairGround:
    def test_fills_time_gap(self):
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(48.1, 2.0, t=60),   # 60 s gap → above default 30 s
        ]
        result, stats = detect_and_repair(pts, REPAIR_GROUND, max_gap_s=30.0)
        assert len(result) > 2
        assert stats.holes_filled > 0

    def test_no_fill_below_gap_threshold(self):
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(48.001, 2.0, t=10),  # 10 s < 30 s threshold
        ]
        result, stats = detect_and_repair(pts, REPAIR_GROUND, max_gap_s=30.0)
        assert len(result) == 2
        assert stats.holes_filled == 0

    def test_synthetic_points_between_endpoints(self):
        pts = [
            _pt(48.0, 2.0, t=0, elev=100.0),
            _pt(49.0, 2.0, t=100, elev=200.0),
        ]
        result, stats = detect_and_repair(pts, REPAIR_GROUND, max_gap_s=30.0)
        assert result[0] == pts[0]
        assert result[-1] == pts[1]
        mid = result[len(result) // 2]
        assert 48.0 < mid.latitude < 49.0

    def test_timestamps_interpolated(self):
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(49.0, 2.0, t=100),
        ]
        result, _ = detect_and_repair(pts, REPAIR_GROUND, max_gap_s=30.0)
        ts_values = [p.timestamp for p in result if p.timestamp]
        assert ts_values == sorted(ts_values)
        assert ts_values[0] == pts[0].timestamp
        assert ts_values[-1] == pts[1].timestamp

    def test_elevation_interpolated(self):
        pts = [
            _pt(48.0, 2.0, t=0, elev=0.0),
            _pt(48.1, 2.0, t=100, elev=100.0),
        ]
        result, _ = detect_and_repair(pts, REPAIR_GROUND, max_gap_s=30.0)
        elevations = [p.elevation for p in result if p.elevation is not None]
        assert elevations == sorted(elevations)

    def test_no_timestamps_no_gap_fill(self):
        """Without timestamps, gap filling is skipped (can't measure gap)."""
        # 0.01 degree ≈ 1 km — below the 50 km jump threshold so not nullified.
        pts = [_pt(48.0, 2.0), _pt(48.01, 2.0)]
        result, stats = detect_and_repair(pts, REPAIR_GROUND)
        assert len(result) == 2
        assert stats.holes_filled == 0

    def test_nullified_followed_by_gap_fill(self):
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(0.0, 0.0, t=1),        # nullified (0,0)
            _pt(48.1, 2.0, t=100),     # large gap after removal → gets filled
        ]
        result, stats = detect_and_repair(pts, REPAIR_GROUND, max_gap_s=30.0)
        assert stats.nullified_removed == 1
        assert stats.holes_filled > 0

    def test_single_point_returns_unchanged(self):
        pts = [_pt(48.0, 2.0, t=0)]
        result, stats = detect_and_repair(pts, REPAIR_GROUND)
        assert result == pts
        assert stats.holes_filled == 0


# ── detect_and_repair — mode=street (OSRM mocked) ───────────────────────────

class TestDetectAndRepairStreet:
    def test_falls_back_to_ground_when_osrm_unavailable(self, monkeypatch):
        import georeel.core.gpx_cleaner as _mod
        monkeypatch.setattr(_mod, "route_waypoints", lambda *_, **__: None)
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(48.1, 2.0, t=100),
        ]
        result, stats = detect_and_repair(pts, REPAIR_STREET, max_gap_s=30.0)
        assert len(result) > 2
        assert stats.street_fallbacks > 0

    def test_uses_osrm_route_when_available(self, monkeypatch):
        import georeel.core.gpx_cleaner as _mod
        monkeypatch.setattr(
            _mod,
            "route_waypoints",
            lambda lat1, lon1, lat2, lon2, **kw: [
                (lat1, lon1),
                ((lat1 + lat2) / 2, (lon1 + lon2) / 2),
                (lat2, lon2),
            ],
        )
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(48.2, 2.0, t=100),
        ]
        result, stats = detect_and_repair(pts, REPAIR_STREET, max_gap_s=30.0)
        assert len(result) > 2
        assert stats.street_fallbacks == 0

    def test_empty_osrm_response_falls_back(self, monkeypatch):
        import georeel.core.gpx_cleaner as _mod
        monkeypatch.setattr(_mod, "route_waypoints", lambda *_, **__: [])
        pts = [
            _pt(48.0, 2.0, t=0),
            _pt(48.1, 2.0, t=100),
        ]
        result, stats = detect_and_repair(pts, REPAIR_STREET, max_gap_s=30.0)
        assert stats.street_fallbacks > 0


# ── osrm_client (import-level smoke test, no network) ────────────────────────

class TestOsrmClientModule:
    def test_route_waypoints_returns_none_on_network_error(self, monkeypatch):
        import urllib.request

        def _bad_urlopen(*a, **kw):
            raise OSError("no network")

        monkeypatch.setattr(urllib.request, "urlopen", _bad_urlopen)
        from georeel.core.osrm_client import route_waypoints

        result = route_waypoints(48.0, 2.0, 51.5, 0.0)
        assert result is None

    def test_route_waypoints_returns_none_on_bad_json(self, monkeypatch):
        import io
        import urllib.request

        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b"not json"

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _FakeResponse())
        from georeel.core.osrm_client import route_waypoints

        result = route_waypoints(48.0, 2.0, 51.5, 0.0)
        assert result is None

    def test_route_waypoints_returns_none_on_osrm_error_code(self, monkeypatch):
        import json
        import urllib.request

        payload = json.dumps({"code": "NoRoute", "message": "No route found"}).encode()

        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return payload

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _FakeResponse())
        from georeel.core.osrm_client import route_waypoints

        result = route_waypoints(48.0, 2.0, 51.5, 0.0)
        assert result is None

    def test_route_waypoints_parses_valid_response(self, monkeypatch):
        import json
        import urllib.request

        payload = json.dumps({
            "code": "Ok",
            "routes": [{
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [2.0, 48.0],
                        [1.0, 50.0],
                        [0.0, 51.5],
                    ],
                }
            }],
        }).encode()

        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return payload

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _FakeResponse())
        from georeel.core.osrm_client import route_waypoints

        result = route_waypoints(48.0, 2.0, 51.5, 0.0)
        assert result is not None
        assert len(result) == 3
        # GeoJSON [lon, lat] → should be returned as (lat, lon)
        assert result[0] == pytest.approx((48.0, 2.0))
        assert result[2] == pytest.approx((51.5, 0.0))
