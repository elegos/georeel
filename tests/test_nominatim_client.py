"""Tests for georeel.core.nominatim_client."""

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from georeel.core.nominatim_client import (
    LocalityEntry,
    _cumulative_times,
    _frame_at_track_time,
    build_locality_timeline,
    reverse_geocode,
)
from georeel.core.trackpoint import Trackpoint
from georeel.core.video_assembler import _locality_name_alpha


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _tp(lat: float, lon: float, ts: datetime | None = None) -> Trackpoint:
    return Trackpoint(latitude=lat, longitude=lon, elevation=None, timestamp=ts)


def _ts(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


# ------------------------------------------------------------------
# _cumulative_times
# ------------------------------------------------------------------

class TestCumulativeTimes:
    def test_empty(self):
        assert _cumulative_times([]) == []

    def test_single(self):
        result = _cumulative_times([_tp(0, 0)])
        assert result == [0.0]

    def test_with_timestamps(self):
        tps = [
            _tp(0.0, 0.0, _ts(1000.0)),
            _tp(0.0, 0.0, _ts(1010.0)),
            _tp(0.0, 0.0, _ts(1025.0)),
        ]
        result = _cumulative_times(tps)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(10.0)
        assert result[2] == pytest.approx(25.0)

    def test_fallback_to_distance(self):
        # No timestamps → cumulative metres
        tps = [
            _tp(0.0, 0.0),
            _tp(0.0, 0.001),   # ~111 m east
            _tp(0.0, 0.002),   # another ~111 m east
        ]
        result = _cumulative_times(tps)
        assert result[0] == pytest.approx(0.0)
        assert result[1] > 0.0
        assert result[2] > result[1]

    def test_partial_timestamps_falls_back_to_distance(self):
        # Mixed None/datetime → no-timestamp path
        tps = [
            _tp(0.0, 0.0, _ts(1000.0)),
            _tp(0.0, 0.001, None),
        ]
        result = _cumulative_times(tps)
        assert result[0] == pytest.approx(0.0)
        # Falls back to distance-based
        assert result[1] > 0.0

    def test_two_identical_points(self):
        tps = [_tp(10.0, 20.0, _ts(0.0)), _tp(10.0, 20.0, _ts(5.0))]
        result = _cumulative_times(tps)
        assert result == pytest.approx([0.0, 5.0])


# ------------------------------------------------------------------
# _frame_at_track_time
# ------------------------------------------------------------------

class TestFrameAtTrackTime:
    def test_zero_time(self):
        assert _frame_at_track_time(0.0, [0.0, 10.0, 20.0], 100) == 0

    def test_end_time(self):
        assert _frame_at_track_time(20.0, [0.0, 10.0, 20.0], 100) == 99

    def test_midpoint(self):
        # t=10 / total=20 → 50% → frame 49 or 50
        result = _frame_at_track_time(10.0, [0.0, 10.0, 20.0], 100)
        assert result == 50

    def test_empty_track_times(self):
        assert _frame_at_track_time(5.0, [], 100) == 0

    def test_zero_total(self):
        assert _frame_at_track_time(5.0, [0.0], 100) == 0

    def test_negative_clamped(self):
        assert _frame_at_track_time(-1.0, [0.0, 10.0], 100) == 0

    def test_over_end_clamped(self):
        assert _frame_at_track_time(100.0, [0.0, 10.0], 50) == 49

    def test_single_frame(self):
        assert _frame_at_track_time(5.0, [0.0, 10.0], 1) == 0


# ------------------------------------------------------------------
# reverse_geocode
# ------------------------------------------------------------------

def _make_urlopen_ctx(body: bytes, status: int = 200) -> MagicMock:
    """Return a mock context manager for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestReverseGeocode:
    def test_success(self):
        payload = {"display_name": "Paris, France"}
        ctx = _make_urlopen_ctx(json.dumps(payload).encode())
        with patch("urllib.request.urlopen", return_value=ctx):
            result = reverse_geocode(48.85, 2.35)
        # Only the first comma-separated component is returned.
        assert result == "Paris"

    def test_missing_display_name(self):
        payload: dict[str, Any] = {}
        ctx = _make_urlopen_ctx(json.dumps(payload).encode())
        with patch("urllib.request.urlopen", return_value=ctx):
            result = reverse_geocode(0.0, 0.0)
        assert result is None

    def test_http_error(self):
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="http://x", code=429, msg="Too Many Requests", hdrs=MagicMock(), fp=None
            ),
        ):
            result = reverse_geocode(0.0, 0.0)
        assert result is None

    def test_timeout(self):
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            result = reverse_geocode(0.0, 0.0)
        assert result is None

    def test_malformed_json(self):
        ctx = _make_urlopen_ctx(b"not-json")
        with patch("urllib.request.urlopen", return_value=ctx):
            result = reverse_geocode(0.0, 0.0)
        assert result is None

    def test_custom_zoom_and_url(self):
        payload = {"display_name": "SomeVillage"}
        ctx = _make_urlopen_ctx(json.dumps(payload).encode())
        captured_url: list[str] = []

        def _fake_urlopen(req: Any, timeout: float = 10.0) -> Any:
            captured_url.append(req.full_url)
            return ctx

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            result = reverse_geocode(1.0, 2.0, zoom=14, base_url="http://custom:8080")
        assert result == "SomeVillage"
        assert "zoom=14" in captured_url[0]
        assert "custom:8080" in captured_url[0]


# ------------------------------------------------------------------
# build_locality_timeline
# ------------------------------------------------------------------

class TestBuildLocalityTimeline:
    def _tps_with_ts(self, n: int = 5) -> list[Trackpoint]:
        return [_tp(float(i), 0.0, _ts(float(i * 60))) for i in range(n)]

    def test_disabled_returns_empty(self):
        tps = self._tps_with_ts()
        result = build_locality_timeline(tps, 300, {"locality_names/enabled": False})
        assert result == []

    def test_empty_trackpoints_returns_empty(self):
        result = build_locality_timeline([], 300, {"locality_names/enabled": True})
        assert result == []

    def test_zero_frames_returns_empty(self):
        tps = self._tps_with_ts()
        result = build_locality_timeline(tps, 0, {"locality_names/enabled": True})
        assert result == []

    def test_single_location_no_dedup(self):
        tps = [_tp(0.0, 0.0, _ts(0.0)), _tp(0.0, 0.0, _ts(60.0))]
        settings = {
            "locality_names/enabled": True,
            "locality_names/check_every_s": 60.0,
        }
        with patch("georeel.core.nominatim_client.reverse_geocode", return_value="London"):
            result = build_locality_timeline(tps, 100, settings)
        # Same name at every sample → only one entry
        assert len(result) == 1
        assert result[0].name == "London"

    def test_deduplication(self):
        tps = [
            _tp(0.0, 0.0, _ts(0.0)),
            _tp(0.0, 0.0, _ts(60.0)),
            _tp(0.0, 0.0, _ts(120.0)),
        ]
        settings = {
            "locality_names/enabled": True,
            "locality_names/check_every_s": 60.0,
        }
        names = ["Paris", "Paris", "Lyon"]
        call_count = 0

        def _rg(*a: Any, **kw: Any) -> str:
            nonlocal call_count
            name = names[min(call_count, len(names) - 1)]
            call_count += 1
            return name

        with patch("georeel.core.nominatim_client.reverse_geocode", side_effect=_rg):
            result = build_locality_timeline(tps, 100, settings)

        assert len(result) == 2
        assert result[0].name == "Paris"
        assert result[1].name == "Lyon"

    def test_progress_cb_called(self):
        tps = [_tp(0.0, 0.0, _ts(0.0)), _tp(0.0, 0.0, _ts(60.0))]
        settings = {
            "locality_names/enabled": True,
            "locality_names/check_every_s": 60.0,
        }
        calls: list[tuple[int, int]] = []

        def _cb(done: int, total: int) -> None:
            calls.append((done, total))

        with patch("georeel.core.nominatim_client.reverse_geocode", return_value="X"):
            build_locality_timeline(tps, 100, settings, progress_cb=_cb)

        assert len(calls) > 0
        # Last call should have done == total
        assert calls[-1][0] == calls[-1][1]

    def test_none_geocode_result_skipped(self):
        tps = [_tp(0.0, 0.0, _ts(0.0)), _tp(0.0, 0.0, _ts(60.0))]
        settings = {
            "locality_names/enabled": True,
            "locality_names/check_every_s": 60.0,
        }
        with patch("georeel.core.nominatim_client.reverse_geocode", return_value=None):
            result = build_locality_timeline(tps, 100, settings)
        assert result == []

    def test_custom_service_url(self):
        """Custom service should pass custom URL to reverse_geocode as base_url."""
        tps = [_tp(0.0, 0.0, _ts(0.0)), _tp(0.0, 0.0, _ts(3600.0))]
        settings: dict[str, Any] = {
            "locality_names/enabled": True,
            "locality_names/service": "custom",
            "locality_names/custom_url": "http://my-server:7777",
            "locality_names/check_every_s": 3600.0,
        }

        with patch("georeel.core.nominatim_client.reverse_geocode") as mock_rg:
            mock_rg.return_value = "Town"
            build_locality_timeline(tps, 100, settings)

        assert mock_rg.called
        _, kwargs = mock_rg.call_args
        assert "my-server:7777" in kwargs.get("base_url", "")

    def test_zero_check_every_s_defaults_to_60(self):
        """check_every_s <= 0 should be clamped to 60."""
        tps = [_tp(0.0, 0.0, _ts(0.0)), _tp(0.0, 0.0, _ts(60.0))]
        settings = {
            "locality_names/enabled": True,
            "locality_names/check_every_s": 0.0,
        }
        with patch("georeel.core.nominatim_client.reverse_geocode", return_value="X"):
            # Should not hang or error — just treat interval as 60s
            result = build_locality_timeline(tps, 100, settings)
        assert isinstance(result, list)

    def test_custom_url_empty_falls_back_to_osm(self):
        """Empty custom_url should fall back to the OSM endpoint."""
        tps = [_tp(0.0, 0.0, _ts(0.0)), _tp(0.0, 0.0, _ts(3600.0))]
        settings: dict = {
            "locality_names/enabled": True,
            "locality_names/service": "custom",
            "locality_names/custom_url": "",
            "locality_names/check_every_s": 3600.0,
        }
        with patch("georeel.core.nominatim_client.reverse_geocode") as mock_rg:
            mock_rg.return_value = "SomePlace"
            build_locality_timeline(tps, 100, settings)
        assert mock_rg.called
        _, kwargs = mock_rg.call_args
        assert "nominatim.openstreetmap.org" in kwargs.get("base_url", "")

    def test_total_t_zero_returns_empty(self):
        """Single trackpoint gives total_t=0.0 → returns empty list."""
        tps = [_tp(0.0, 0.0, _ts(0.0))]
        settings = {
            "locality_names/enabled": True,
            "locality_names/check_every_s": 60.0,
        }
        result = build_locality_timeline(tps, 100, settings)
        assert result == []

    def test_returns_locality_entries(self):
        tps = [_tp(0.0, 0.0, _ts(0.0)), _tp(0.0, 0.0, _ts(60.0))]
        settings = {
            "locality_names/enabled": True,
            "locality_names/check_every_s": 60.0,
        }
        names = ["Berlin", "Munich"]
        it = iter(names)

        with patch("georeel.core.nominatim_client.reverse_geocode", side_effect=lambda *a, **k: next(it, None)):
            result = build_locality_timeline(tps, 120, settings)

        assert all(isinstance(e, LocalityEntry) for e in result)
        assert result[0].frame_start == 0
        assert result[0].name == "Berlin"


# ------------------------------------------------------------------
# _locality_name_alpha (from video_assembler)
# ------------------------------------------------------------------

class TestLocalityNameAlpha:
    def test_before_start(self):
        assert _locality_name_alpha(-1, 30, 5) == 0.0

    def test_at_end(self):
        assert _locality_name_alpha(30, 30, 5) == 0.0

    def test_full_opacity_mid(self):
        assert _locality_name_alpha(15, 30, 5) == 1.0

    def test_fade_in_start(self):
        # frame_offset == 0 → alpha == 0 / fade_frames == 0.0
        result = _locality_name_alpha(0, 30, 5)
        assert result == pytest.approx(0.0)

    def test_fade_in_mid(self):
        result = _locality_name_alpha(3, 30, 5)
        assert result == pytest.approx(3 / 5)

    def test_fade_out(self):
        # At duration_frames - fade_frames → alpha starts dropping
        result = _locality_name_alpha(25, 30, 5)
        assert result == pytest.approx(5 / 5)  # (30 - 25) / 5 = 1.0

    def test_fade_out_falling(self):
        result = _locality_name_alpha(27, 30, 5)
        assert result == pytest.approx(3 / 5)

    def test_no_fade(self):
        assert _locality_name_alpha(10, 30, 0) == 1.0

    def test_boundary_last_frame(self):
        # frame_offset == duration_frames - 1 → last visible frame
        result = _locality_name_alpha(29, 30, 5)
        assert result == pytest.approx(1 / 5)
