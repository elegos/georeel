import math

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
) -> list[MatchResult]:
    """Match each photo to its nearest trackpoint using the given strategy.

    mode: "timestamp" | "gps" | "both"
    """
    return [_match_one(photo, trackpoints, mode) for photo in photos]


# ------------------------------------------------------------------
# Strategy dispatch
# ------------------------------------------------------------------

def _match_one(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
    mode: str,
) -> MatchResult:
    if mode == "timestamp":
        return _match_by_timestamp(photo, trackpoints)
    if mode == "gps":
        return _match_by_gps(photo, trackpoints)
    return _match_by_both(photo, trackpoints)


# ------------------------------------------------------------------
# Individual strategies
# ------------------------------------------------------------------

def _match_by_timestamp(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
) -> MatchResult:
    if not photo.has_timestamp:
        return MatchResult(photo_path=photo.path, error="No timestamp in EXIF")

    timed = [(i, tp) for i, tp in enumerate(trackpoints) if tp.timestamp]
    if not timed:
        return MatchResult(photo_path=photo.path, error="No trackpoints have timestamps")

    best_i, _ = min(
        timed,
        key=lambda x: abs((x[1].timestamp - photo.timestamp).total_seconds()),
    )
    return MatchResult(photo_path=photo.path, trackpoint_index=best_i)


def _match_by_gps(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
) -> MatchResult:
    if not photo.has_gps:
        return MatchResult(photo_path=photo.path, error="No GPS coordinates in EXIF")

    best_i = min(
        range(len(trackpoints)),
        key=lambda i: _haversine(
            photo.latitude, photo.longitude,
            trackpoints[i].latitude, trackpoints[i].longitude,
        ),
    )
    return MatchResult(photo_path=photo.path, trackpoint_index=best_i)


def _match_by_both(
    photo: PhotoMetadata,
    trackpoints: list[Trackpoint],
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
        return _match_by_timestamp(photo, trackpoints)

    # Both available: GPS is primary; warn if the two methods disagree.
    gps_result = _match_by_gps(photo, trackpoints)
    ts_result = _match_by_timestamp(photo, trackpoints)

    warning = None
    if gps_result.trackpoint_index != ts_result.trackpoint_index:
        gps_tp = trackpoints[gps_result.trackpoint_index]
        ts_tp = trackpoints[ts_result.trackpoint_index]
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
