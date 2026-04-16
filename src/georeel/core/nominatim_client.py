"""Nominatim reverse geocoding client for GeoReel."""

import bisect
import json
import logging
import math
from typing import Any, Callable, NamedTuple

from .trackpoint import Trackpoint

_log = logging.getLogger(__name__)

_OSM_NOMINATIM_URL = "https://nominatim.openstreetmap.org"

# Nominatim zoom levels: https://nominatim.org/release-docs/latest/api/Reverse/
_DETAIL_ZOOM: dict[str, int] = {
    "village": 14,
    "town":    12,
    "city":    10,
    "state":    5,
    "country":  3,
}


class LocalityEntry(NamedTuple):
    """A locality name that becomes active at a given video frame."""
    frame_start: int
    name: str
    track_time_s: float = 0.0  # track-time offset (seconds) of this sample


def reverse_geocode(
    lat: float,
    lon: float,
    *,
    zoom: int = 10,
    base_url: str = _OSM_NOMINATIM_URL,
    timeout: float = 10.0,
) -> str | None:
    """Return the primary locality name from Nominatim for (lat, lon), or None on failure.

    Only the first comma-separated component of ``display_name`` is returned
    (e.g. ``"Nave"`` rather than ``"Nave, Comunità montana …"``).
    """
    import urllib.request
    import urllib.parse

    params = urllib.parse.urlencode({
        "lat": lat,
        "lon": lon,
        "zoom": zoom,
        "format": "json",
        "addressdetails": 0,
    })
    url = f"{base_url.rstrip('/')}/reverse?{params}"
    headers = {"User-Agent": "GeoReel/1.0 (+https://github.com/elegos/georeel)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode())
        display = data.get("display_name")
        if not display:
            return None
        # Keep only the first comma-separated component (e.g. "Nave" from
        # "Nave, Comunità montana della valle Trompia, Brescia, …").
        return str(display).split(",")[0].strip() or None
    except Exception as exc:
        _log.warning("Nominatim (%s) reverse geocode (%.5f, %.5f) failed: %s",
                     base_url, lat, lon, exc)
        return None


def _cumulative_times(trackpoints: list[Trackpoint]) -> list[float]:
    """Return seconds-since-start (or metres if no timestamps) per trackpoint."""
    if not trackpoints:
        return []
    if len(trackpoints) == 1:
        return [0.0]

    if all(tp.timestamp is not None for tp in trackpoints):
        t0 = trackpoints[0].timestamp
        assert t0 is not None
        return [
            (tp.timestamp - t0).total_seconds() if tp.timestamp else 0.0
            for tp in trackpoints
        ]

    # Fallback: cumulative great-circle distance in metres
    times: list[float] = [0.0]
    for i in range(1, len(trackpoints)):
        a, b = trackpoints[i - 1], trackpoints[i]
        mid_lat_rad = math.radians((a.latitude + b.latitude) / 2)
        dlat = (b.latitude  - a.latitude)  * 111_320.0
        dlon = (b.longitude - a.longitude) * 111_320.0 * math.cos(mid_lat_rad)
        times.append(times[-1] + math.hypot(dlat, dlon))
    return times


def _frame_at_track_time(t: float, track_times: list[float], total_frames: int) -> int:
    """Map a track-time value to a frame index (0-based, clamped)."""
    if not track_times or track_times[-1] <= 0:
        return 0
    frac = max(0.0, min(1.0, t / track_times[-1]))
    return min(total_frames - 1, round(frac * (total_frames - 1)))


def build_locality_timeline(
    trackpoints: list[Trackpoint],
    total_frames: int,
    settings: dict[str, Any],
    *,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[LocalityEntry]:
    """Build locality entries by querying Nominatim at intervals.

    Samples every ``locality_names/check_every_s`` seconds of track time.
    Consecutive identical names are de-duplicated.
    Returns empty list if locality names are disabled.
    """
    if not settings.get("locality_names/enabled", False):
        return []
    if not trackpoints or total_frames <= 0:
        return []

    check_every_s = float(settings.get("locality_names/check_every_s", 60.0))
    if check_every_s <= 0:
        check_every_s = 60.0

    detail  = str(settings.get("locality_names/detail_level", "city"))
    zoom    = _DETAIL_ZOOM.get(detail, _DETAIL_ZOOM["city"])
    service = str(settings.get("locality_names/service", "osm"))

    if service == "custom":
        base_url = str(settings.get("locality_names/custom_url", _OSM_NOMINATIM_URL)).strip()
        if not base_url:
            base_url = _OSM_NOMINATIM_URL
    else:  # "osm" or unknown
        base_url = _OSM_NOMINATIM_URL

    track_times = _cumulative_times(trackpoints)
    total_t     = track_times[-1] if track_times else 0.0
    if total_t <= 0:
        return []

    # Build sample times at multiples of check_every_s
    sample_times: list[float] = []
    t = 0.0
    while t <= total_t:
        sample_times.append(t)
        t += check_every_s
    if not sample_times or sample_times[-1] < total_t - 1e-6:
        sample_times.append(total_t)

    entries: list[LocalityEntry] = []
    last_name: str | None = None

    for i, st in enumerate(sample_times):
        tp_idx = bisect.bisect_left(track_times, st)
        tp_idx = max(0, min(tp_idx, len(trackpoints) - 1))
        tp = trackpoints[tp_idx]

        name = reverse_geocode(tp.latitude, tp.longitude, zoom=zoom, base_url=base_url)

        if progress_cb:
            progress_cb(i + 1, len(sample_times))

        if name and name != last_name:
            frame = _frame_at_track_time(st, track_times, total_frames)
            entries.append(LocalityEntry(frame_start=frame, name=name, track_time_s=st))
            last_name = name

    return entries
