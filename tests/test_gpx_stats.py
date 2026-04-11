"""Tests for gpx_stats.compute_stats."""

import math
import pytest
from datetime import datetime, timezone, timedelta
from georeel.core.gpx_stats import compute_stats, _haversine, GpxStats
from georeel.core.trackpoint import Trackpoint


def _tp(lat, lon, elev=None, ts=None):
    return Trackpoint(latitude=lat, longitude=lon, elevation=elev, timestamp=ts)


def _utc(h, m=0, s=0):
    return datetime(2023, 6, 1, h, m, s, tzinfo=timezone.utc)


class TestHaversine:
    def test_same_point_is_zero(self):
        assert _haversine(48.0, 2.0, 48.0, 2.0) == pytest.approx(0.0)

    def test_known_distance(self):
        # Paris → London ≈ 340 km
        d = _haversine(48.8566, 2.3522, 51.5074, -0.1278)
        assert 330_000 < d < 350_000

    def test_symmetry(self):
        d1 = _haversine(0.0, 0.0, 1.0, 1.0)
        d2 = _haversine(1.0, 1.0, 0.0, 0.0)
        assert d1 == pytest.approx(d2)

    def test_equator_one_degree_lon(self):
        # At equator, 1° longitude ≈ 111,320 m
        d = _haversine(0.0, 0.0, 0.0, 1.0)
        assert 111_000 < d < 112_000


class TestComputeStatsEmpty:
    def test_empty_list_returns_zeros(self):
        stats = compute_stats([])
        assert stats.point_count == 0
        assert stats.total_distance_m == 0.0
        assert stats.elevation_gain_m == 0.0
        assert stats.elevation_loss_m == 0.0
        assert stats.start_time is None
        assert stats.end_time is None
        assert stats.duration is None
        assert stats.avg_speed_kmh is None
        assert stats.max_speed_kmh is None
        assert stats.min_elevation_m is None
        assert stats.max_elevation_m is None


class TestComputeStatsSinglePoint:
    def test_single_point(self):
        stats = compute_stats([_tp(48.0, 2.0, 100.0, _utc(10))])
        assert stats.point_count == 1
        assert stats.total_distance_m == pytest.approx(0.0)
        assert stats.elevation_gain_m == pytest.approx(0.0)
        assert stats.elevation_loss_m == pytest.approx(0.0)
        assert stats.min_elevation_m == 100.0
        assert stats.max_elevation_m == 100.0


class TestComputeStatsDistance:
    def test_two_points_distance(self):
        tp1 = _tp(48.0, 2.0)
        tp2 = _tp(48.0, 2.1)
        stats = compute_stats([tp1, tp2])
        expected = _haversine(48.0, 2.0, 48.0, 2.1)
        assert stats.total_distance_m == pytest.approx(expected)

    def test_three_points_accumulates(self):
        tp1 = _tp(0.0, 0.0)
        tp2 = _tp(1.0, 0.0)
        tp3 = _tp(2.0, 0.0)
        stats = compute_stats([tp1, tp2, tp3])
        d12 = _haversine(0, 0, 1, 0)
        d23 = _haversine(1, 0, 2, 0)
        assert stats.total_distance_m == pytest.approx(d12 + d23)


class TestComputeStatsTime:
    def test_timestamps_set_start_end(self):
        tp1 = _tp(0.0, 0.0, ts=_utc(8))
        tp2 = _tp(1.0, 0.0, ts=_utc(9))
        stats = compute_stats([tp1, tp2])
        assert stats.start_time == _utc(8)
        assert stats.end_time == _utc(9)
        assert stats.duration == timedelta(hours=1)

    def test_no_timestamps_gives_none(self):
        stats = compute_stats([_tp(0.0, 0.0), _tp(1.0, 0.0)])
        assert stats.start_time is None
        assert stats.duration is None
        assert stats.avg_speed_kmh is None

    def test_partial_timestamps_uses_timed_points(self):
        # Only first and last have timestamps
        tp1 = _tp(0.0, 0.0, ts=_utc(10))
        tp2 = _tp(0.5, 0.0)           # no timestamp
        tp3 = _tp(1.0, 0.0, ts=_utc(12))
        stats = compute_stats([tp1, tp2, tp3])
        assert stats.start_time == _utc(10)
        assert stats.end_time == _utc(12)


class TestComputeStatsSpeed:
    def test_avg_speed_computed(self):
        # 1° latitude ≈ 111,320 m in 1 hour = ~111.32 km/h
        tp1 = _tp(0.0, 0.0, ts=_utc(10, 0, 0))
        tp2 = _tp(1.0, 0.0, ts=_utc(11, 0, 0))
        stats = compute_stats([tp1, tp2])
        assert stats.avg_speed_kmh == pytest.approx(
            stats.total_distance_m / 3600 * 3.6, rel=1e-6
        )

    def test_max_speed_over_fastest_segment(self):
        tp1 = _tp(0.0, 0.0, ts=_utc(10, 0, 0))
        tp2 = _tp(0.01, 0.0, ts=_utc(10, 0, 30))  # fast
        tp3 = _tp(0.011, 0.0, ts=_utc(10, 5, 0))  # slow
        stats = compute_stats([tp1, tp2, tp3])
        assert stats.max_speed_kmh is not None
        # Max speed should be from first segment (short time, some distance)
        seg1_kmh = (_haversine(0, 0, 0.01, 0) / 30) * 3.6
        assert stats.max_speed_kmh == pytest.approx(seg1_kmh, rel=1e-5)

    def test_zero_time_delta_skipped_for_speed(self):
        # Two points with same timestamp: should not cause division by zero
        ts = _utc(10)
        tp1 = _tp(0.0, 0.0, ts=ts)
        tp2 = _tp(1.0, 0.0, ts=ts)
        stats = compute_stats([tp1, tp2])
        # max_speed_kmh should remain None (zero dt skipped)
        assert stats.max_speed_kmh is None


class TestComputeStatsElevation:
    def test_gain_and_loss(self):
        tps = [
            _tp(0, 0, elev=100.0),
            _tp(0, 0, elev=200.0),  # +100 gain
            _tp(0, 0, elev=150.0),  # -50  loss
            _tp(0, 0, elev=300.0),  # +150 gain
            _tp(0, 0, elev=50.0),   # -250 loss
        ]
        stats = compute_stats(tps)
        assert stats.elevation_gain_m == pytest.approx(250.0)
        assert stats.elevation_loss_m == pytest.approx(300.0)

    def test_no_elevation_gives_none(self):
        stats = compute_stats([_tp(0, 0), _tp(1, 0)])
        assert stats.min_elevation_m is None
        assert stats.max_elevation_m is None
        assert stats.elevation_gain_m == pytest.approx(0.0)

    def test_min_max_elevation(self):
        tps = [_tp(0, 0, elev=500.0), _tp(0, 0, elev=200.0), _tp(0, 0, elev=800.0)]
        stats = compute_stats(tps)
        assert stats.min_elevation_m == pytest.approx(200.0)
        assert stats.max_elevation_m == pytest.approx(800.0)

    def test_partial_elevation(self):
        # Some points missing elevation — only those with data contribute
        tps = [_tp(0, 0, elev=100.0), _tp(0, 0), _tp(0, 0, elev=300.0)]
        stats = compute_stats(tps)
        assert stats.min_elevation_m == pytest.approx(100.0)
        assert stats.max_elevation_m == pytest.approx(300.0)
