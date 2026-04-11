"""Tests for photo_matcher.match_photos."""

import pytest
from datetime import datetime, timezone, timedelta
from georeel.core.photo_matcher import match_photos, _haversine
from georeel.core.photo_metadata import PhotoMetadata
from georeel.core.trackpoint import Trackpoint


def _utc(h, m=0, s=0):
    return datetime(2023, 6, 1, h, m, s, tzinfo=timezone.utc)


def _tp(lat, lon, ts=None):
    return Trackpoint(latitude=lat, longitude=lon, elevation=None, timestamp=ts)


def _photo(path, ts=None, lat=None, lon=None):
    return PhotoMetadata(path=path, timestamp=ts, latitude=lat, longitude=lon)


# Simple track: 3 points spaced 1 km apart along a line
_TRACK = [
    _tp(48.000, 2.000, ts=_utc(10, 0, 0)),
    _tp(48.009, 2.000, ts=_utc(10, 30, 0)),  # ~1 km north
    _tp(48.018, 2.000, ts=_utc(11, 0, 0)),   # ~2 km north
]


class TestHaversine:
    def test_zero_distance(self):
        assert _haversine(0, 0, 0, 0) == pytest.approx(0.0)

    def test_known_distance(self):
        # 1° latitude ≈ 111 km
        d = _haversine(0.0, 0.0, 1.0, 0.0)
        assert 110_000 < d < 112_000


class TestMatchByTimestamp:
    def test_matches_nearest_trackpoint(self):
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 10, 30, 0))
        results = match_photos([photo], _TRACK, mode="timestamp", tz_offset_hours=0.0)
        assert results[0].ok
        assert results[0].trackpoint_index == 1  # nearest in time

    def test_photo_before_track_is_pre(self):
        # Photo 1 hour before first trackpoint
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 9, 0, 0))
        results = match_photos([photo], _TRACK, mode="timestamp", tz_offset_hours=0.0)
        assert results[0].position == "pre"
        assert results[0].trackpoint_index == 0

    def test_photo_after_track_is_post(self):
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 12, 0, 0))
        results = match_photos([photo], _TRACK, mode="timestamp", tz_offset_hours=0.0)
        assert results[0].position == "post"
        assert results[0].trackpoint_index == 2

    def test_no_timestamp_returns_error(self):
        photo = _photo("/a.jpg")
        results = match_photos([photo], _TRACK, mode="timestamp")
        assert not results[0].ok
        assert results[0].error is not None

    def test_no_trackpoint_timestamps_returns_error(self):
        track = [_tp(0.0, 0.0), _tp(1.0, 0.0)]  # no timestamps
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 10, 0))
        results = match_photos([photo], track, mode="timestamp")
        assert not results[0].ok
        assert results[0].error is not None

    def test_tz_offset_applied(self):
        # Camera clock is UTC+2; EXIF shows 12:30 local = 10:30 UTC
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 12, 30, 0))
        results = match_photos([photo], _TRACK, mode="timestamp", tz_offset_hours=2.0)
        assert results[0].ok
        assert results[0].trackpoint_index == 1  # matches 10:30 UTC

    def test_sort_key_pre_is_negative(self):
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 9, 0, 0))
        results = match_photos([photo], _TRACK, mode="timestamp")
        assert results[0].sort_key < 0.0

    def test_sort_key_post_is_positive(self):
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 12, 0, 0))
        results = match_photos([photo], _TRACK, mode="timestamp")
        assert results[0].sort_key > 0.0


class TestMatchByGps:
    def test_matches_nearest_trackpoint(self):
        # Photo GPS very close to trackpoint 1
        photo = _photo("/a.jpg", lat=48.0085, lon=2.0)
        results = match_photos([photo], _TRACK, mode="gps")
        assert results[0].ok
        assert results[0].trackpoint_index == 1

    def test_exact_match(self):
        photo = _photo("/a.jpg", lat=48.000, lon=2.000)
        results = match_photos([photo], _TRACK, mode="gps")
        assert results[0].trackpoint_index == 0

    def test_no_gps_returns_error(self):
        photo = _photo("/a.jpg")
        results = match_photos([photo], _TRACK, mode="gps")
        assert not results[0].ok
        assert results[0].error is not None

    def test_matches_last_point(self):
        photo = _photo("/a.jpg", lat=48.020, lon=2.000)
        results = match_photos([photo], _TRACK, mode="gps")
        assert results[0].trackpoint_index == 2


class TestMatchByBoth:
    def test_gps_primary_when_both_available(self):
        # Photo GPS is near trackpoint 2, timestamp is near trackpoint 0
        # GPS should win
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 10, 0, 0),
                       lat=48.018, lon=2.000)
        results = match_photos([photo], _TRACK, mode="both")
        assert results[0].trackpoint_index == 2  # GPS → last point

    def test_falls_back_to_gps_when_no_timestamp(self):
        photo = _photo("/a.jpg", lat=48.000, lon=2.000)
        results = match_photos([photo], _TRACK, mode="both")
        assert results[0].ok
        assert results[0].trackpoint_index == 0

    def test_falls_back_to_timestamp_when_no_gps(self):
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 10, 30, 0))
        results = match_photos([photo], _TRACK, mode="both")
        assert results[0].ok
        assert results[0].trackpoint_index == 1

    def test_error_when_neither_available(self):
        photo = _photo("/a.jpg")
        results = match_photos([photo], _TRACK, mode="both")
        assert not results[0].ok
        assert results[0].error is not None

    def test_no_warning_when_gps_ts_agree(self):
        # Photo GPS and timestamp both point to the same trackpoint
        photo = _photo("/a.jpg", ts=datetime(2023, 6, 1, 10, 0, 0),
                       lat=48.000, lon=2.000)
        results = match_photos([photo], _TRACK, mode="both")
        assert results[0].warning is None

    def test_warning_when_disagreement_exceeds_threshold(self):
        # GPS near point 0; timestamp near point 2 (far apart)
        photo = _photo("/a.jpg",
                       ts=datetime(2023, 6, 1, 11, 0, 0),  # → trackpoint 2
                       lat=48.000, lon=2.000)              # → trackpoint 0
        results = match_photos([photo], _TRACK, mode="both")
        # Distance between tp0 and tp2 >> 100 m → warning
        assert results[0].warning is not None
        assert "disagree" in results[0].warning.lower()


class TestMatchPhotosMultiple:
    def test_multiple_photos_all_matched(self):
        photos = [
            _photo("/a.jpg", ts=datetime(2023, 6, 1, 10, 0, 0)),
            _photo("/b.jpg", ts=datetime(2023, 6, 1, 10, 30, 0)),
            _photo("/c.jpg", ts=datetime(2023, 6, 1, 11, 0, 0)),
        ]
        results = match_photos(photos, _TRACK, mode="timestamp")
        assert len(results) == 3
        assert all(r.ok for r in results)

    def test_empty_photos_list(self):
        results = match_photos([], _TRACK, mode="timestamp")
        assert results == []

    def test_single_trackpoint_gps(self):
        track = [_tp(48.0, 2.0)]
        photo = _photo("/a.jpg", lat=48.5, lon=2.5)
        results = match_photos([photo], track, mode="gps")
        assert results[0].trackpoint_index == 0
