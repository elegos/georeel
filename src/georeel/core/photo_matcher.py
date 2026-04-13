import math
from datetime import timedelta, timezone

from .match_result import MatchResult
from .photo_metadata import PhotoMetadata
from .trackpoint import Trackpoint

# Warn when GPS-matched and timestamp-matched trackpoints are further apart
# than this distance (metres).
_DISAGREEMENT_THRESHOLD_M = 100.0


def match_photos(
    photos: list[PhotoMetadata],
    trackpoints: list[Trackpoint],
    mode: str,
    tz_offset_hours: float = 0.0,
) -> list[MatchResult]:
    """Match each photo to its nearest trackpoint using the given strategy.

    mode: "timestamp" | "gps" | "both"

    tz_offset_hours: UTC offset of the camera clock, e.g. +2.0 for UTC+2.
      EXIF DateTimeOriginal is local time with no embedded timezone; this
      offset converts it to UTC so it can be compared against GPX timestamps
      (which are always stored in UTC).
    """
    tz = timezone(timedelta(hours=tz_offset_hours))
    return [_match_one(photo, trackpoints, mode, tz) for photo in photos]


# ------------------------------------------------------------------
# Strategy dispatch
# ------------------------------------------------------------------

def _match_one(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
    mode: str,
    tz: timezone,
) -> MatchResult:
    if mode == "timestamp":
        return _match_by_timestamp(photo, trackpoints, tz)
    if mode == "gps":
        return _match_by_gps(photo, trackpoints)
    return _match_by_both(photo, trackpoints, tz)


# ------------------------------------------------------------------
# Individual strategies
# ------------------------------------------------------------------

def _match_by_timestamp(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
    tz: timezone,
) -> MatchResult:
    if not photo.has_timestamp or photo.timestamp is None:
        return MatchResult(photo_path=photo.path, error="No timestamp in EXIF")

    timed = [(i, tp) for i, tp in enumerate(trackpoints) if tp.timestamp is not None]
    if not timed:
        return MatchResult(photo_path=photo.path, error="No trackpoints have timestamps")

    # Attach the user-supplied UTC offset to the naive EXIF timestamp so that
    # subtraction against the UTC-aware GPX timestamps is unambiguous.
    photo_utc = photo.timestamp.replace(tzinfo=tz)

    timed_sorted = sorted(timed, key=lambda x: x[1].timestamp)  # type: ignore[arg-type]
    first_time = timed_sorted[0][1].timestamp
    last_time  = timed_sorted[-1][1].timestamp
    assert first_time is not None and last_time is not None
    sort_key   = (photo_utc - first_time).total_seconds()

    if photo_utc < first_time:
        return MatchResult(
            photo_path=photo.path,
            trackpoint_index=timed_sorted[0][0],
            position="pre",
            sort_key=sort_key,   # negative: further before = smaller value
        )
    if photo_utc > last_time:
        return MatchResult(
            photo_path=photo.path,
            trackpoint_index=timed_sorted[-1][0],
            position="post",
            sort_key=sort_key,
        )

    best_i, _ = min(
        timed,
        key=lambda x: abs((x[1].timestamp - photo_utc).total_seconds()),  # type: ignore[operator]
    )
    return MatchResult(photo_path=photo.path, trackpoint_index=best_i, sort_key=sort_key)


def _match_by_gps(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
) -> MatchResult:
    if not photo.has_gps:
        return MatchResult(photo_path=photo.path, error="No GPS coordinates in EXIF")

    assert photo.latitude is not None and photo.longitude is not None
    best_i = min(
        range(len(trackpoints)),
        key=lambda i: _haversine(
            photo.latitude,  # type: ignore[arg-type]
            photo.longitude,  # type: ignore[arg-type]
            trackpoints[i].latitude, trackpoints[i].longitude,
        ),
    )
    return MatchResult(photo_path=photo.path, trackpoint_index=best_i)


def _match_by_both(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
    tz: timezone,
) -> MatchResult:
    has_gps = photo.has_gps
    has_ts = photo.has_timestamp

    if not has_gps and not has_ts:
        return MatchResult(
            photo_path=photo.path,
            error="No GPS coordinates or timestamp in EXIF",
        )

    if has_gps and not has_ts:
        return _match_by_gps(photo, trackpoints)

    if has_ts and not has_gps:
        return _match_by_timestamp(photo, trackpoints, tz)

    # Both available: GPS is primary; warn if the two methods disagree.
    gps_result = _match_by_gps(photo, trackpoints)
    ts_result = _match_by_timestamp(photo, trackpoints, tz)

    warning = None
    if gps_result.trackpoint_index != ts_result.trackpoint_index:
        gps_idx = gps_result.trackpoint_index
        ts_idx = ts_result.trackpoint_index
        if gps_idx is not None and ts_idx is not None:
            gps_tp = trackpoints[gps_idx]
            ts_tp = trackpoints[ts_idx]
            dist = _haversine(
                gps_tp.latitude, gps_tp.longitude,
                ts_tp.latitude, ts_tp.longitude,
            )
            if dist > _DISAGREEMENT_THRESHOLD_M:
                warning = f"GPS and timestamp matches disagree by {dist:.0f} m"

    return MatchResult(
        photo_path=photo.path,
        trackpoint_index=gps_result.trackpoint_index,
        warning=warning,
    )


# ------------------------------------------------------------------
# Geometry
# ------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two (lat, lon) points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
