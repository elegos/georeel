"""GPX track hole detection and repair.

Holes come in two flavours:

* **Nullified points** — ``(lat, lon) == (0, 0)`` or a point whose implied
  speed relative to the last valid point exceeds *max_speed_mps*.  When
  timestamps are absent, the *max_jump_m* fallback is used instead (any
  single jump beyond that distance is treated as a nullified point).

* **Time gaps** — two consecutive valid points whose timestamp delta exceeds
  *max_gap_s* (the recorder paused or lost satellite signal).

Both types are repaired by inserting synthetic ``Trackpoint`` objects between
the bounding valid points.  Three repair modes are supported:

``"none"``
    No repair — return the cleaned (bad-points-removed) list as-is.

``"ground"``
    Linear great-circle interpolation of latitude/longitude; elevation is
    linearly interpolated between the two endpoint elevations (or set to
    ``None`` when either is absent); timestamps are linearly interpolated.

``"street"``
    The OSRM public routing API is queried for the shortest driving route
    between the two endpoints.  The returned polyline is uniformly resampled
    to the desired number of points.  Elevation is still interpolated from
    the DEM endpoints (OSRM does not supply elevation data, so bridges and
    tunnels are not modelled differently from ground level).  Falls back
    silently to ground interpolation when OSRM is unavailable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from .osrm_client import route_waypoints
from .trackpoint import Trackpoint

# ── Public constants ──────────────────────────────────────────────────────────

REPAIR_NONE   = "none"
REPAIR_GROUND = "ground"
REPAIR_STREET = "street"


@dataclass
class CleanStats:
    """Summary of what ``detect_and_repair`` did to the track."""
    nullified_removed: int = 0   # points discarded (0,0 or implausible speed)
    holes_filled: int = 0        # synthetic points inserted
    street_fallbacks: int = 0    # OSRM unavailable → fell back to ground


# ── Earth radius ──────────────────────────────────────────────────────────────

_R_EARTH = 6_371_000.0  # metres


# ── Public API ────────────────────────────────────────────────────────────────

def detect_and_repair(
    points: list[Trackpoint],
    mode: str = REPAIR_NONE,
    *,
    max_speed_mps: float = 83.3,   # 300 km/h — above this is almost certainly bad data
    max_gap_s: float = 30.0,       # gaps longer than this get synthetic points
    max_jump_m: float = 50_000.0,  # fallback for timestamp-less tracks (50 km)
) -> tuple[list[Trackpoint], CleanStats]:
    """Return a cleaned trackpoint list and statistics about what changed.

    Parameters
    ----------
    points:
        Raw trackpoints from the GPX parser.
    mode:
        ``REPAIR_NONE``, ``REPAIR_GROUND``, or ``REPAIR_STREET``.
    max_speed_mps:
        Implied speed above which a point is considered nullified.
        Only used when timestamps are present.
    max_gap_s:
        Time gap (seconds) above which synthetic points are inserted.
        Only used when timestamps are present on both endpoints.
    max_jump_m:
        Maximum allowed distance (metres) between consecutive points
        when timestamps are absent.  Points beyond this are removed.
    """
    stats = CleanStats()

    # ── Step 1: remove nullified points ──────────────────────────────────────
    clean: list[Trackpoint] = []
    for pt in points:
        if _is_nullified(pt, clean, max_speed_mps, max_jump_m, max_gap_s):
            stats.nullified_removed += 1
        else:
            clean.append(pt)

    if mode == REPAIR_NONE or len(clean) < 2:
        return clean, stats

    # ── Step 2: estimate the typical inter-point interval (median) ───────────
    pair_gaps = [
        _time_gap_s(clean[i], clean[i + 1])
        for i in range(len(clean) - 1)
    ]
    valid_gaps = sorted(g for g in pair_gaps if g is not None and 0 < g <= max_gap_s)
    typical_s = valid_gaps[len(valid_gaps) // 2] if valid_gaps else 1.0

    # ── Step 3: scan consecutive pairs and fill gaps ──────────────────────────
    result: list[Trackpoint] = [clean[0]]
    for i in range(len(clean) - 1):
        a, b = clean[i], clean[i + 1]
        gap_s = _time_gap_s(a, b)
        if gap_s is not None and gap_s > max_gap_s:
            # Number of synthetic points to insert (≥1).
            n = max(1, round(gap_s / typical_s) - 1)
            synthetic, fell_back = _fill_hole(a, b, n, mode)
            result.extend(synthetic)
            stats.holes_filled += len(synthetic)
            if fell_back:
                stats.street_fallbacks += 1
        result.append(b)

    return result, stats


# ── Internal helpers ──────────────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return _R_EARTH * 2 * math.asin(math.sqrt(min(a, 1.0)))


def _time_gap_s(a: Trackpoint, b: Trackpoint) -> float | None:
    if a.timestamp and b.timestamp:
        return (b.timestamp - a.timestamp).total_seconds()
    return None


def _is_nullified(
    pt: Trackpoint,
    previous_good: list[Trackpoint],
    max_speed_mps: float,
    max_jump_m: float,
    max_gap_s: float,
) -> bool:
    """Return True if *pt* should be discarded.

    A point is *nullified* (a bad GPS reading) only when:
      - its coordinates are (0, 0), or
      - the implied speed from the previous valid point is above *max_speed_mps*
        **and** the time gap is below *max_gap_s* (large-gap transitions are
        treated as legitimate recording pauses, not bad readings).

    When timestamps are absent, any single jump exceeding *max_jump_m* is
    treated as a bad reading.
    """
    if pt.latitude == 0.0 and pt.longitude == 0.0:
        return True
    if not previous_good:
        return False
    last = previous_good[-1]
    dist = _haversine(last.latitude, last.longitude, pt.latitude, pt.longitude)
    gap_s = _time_gap_s(last, pt)
    if gap_s is not None:
        if gap_s >= max_gap_s:
            # Treat as a legitimate recording gap — don't discard the point.
            return False
        if gap_s > 0:
            return dist / gap_s > max_speed_mps
        # gap_s == 0 but different position → duplicate timestamp with bad coords
        return dist > 0
    # No timestamps: fall back to pure distance check.
    return dist > max_jump_m


def _fill_hole(
    a: Trackpoint,
    b: Trackpoint,
    n: int,
    mode: str,
) -> tuple[list[Trackpoint], bool]:
    """Synthesise *n* Trackpoints between *a* and *b* (exclusive).

    Returns ``(synthetic_points, fell_back_to_ground)``.
    """
    fell_back = False

    if mode == REPAIR_STREET:
        route = route_waypoints(a.latitude, a.longitude, b.latitude, b.longitude)
        if route and len(route) >= 2:
            latlon_pts = _resample_route(route, n)
        else:
            fell_back = True
            latlon_pts = _interp_latlon(a, b, n)
    else:
        latlon_pts = _interp_latlon(a, b, n)

    # Elevation: linearly interpolate between the two valid endpoints.
    el_a, el_b = a.elevation, b.elevation
    have_el = el_a is not None and el_b is not None

    # Timestamps: linearly interpolate (None when either endpoint has none).
    ts_a, ts_b = a.timestamp, b.timestamp
    have_ts = ts_a is not None and ts_b is not None
    total_s = (ts_b - ts_a).total_seconds() if have_ts else 0.0  # type: ignore[operator]

    result: list[Trackpoint] = []
    for i, (lat, lon) in enumerate(latlon_pts):
        frac = (i + 1) / (n + 1)
        elev = el_a + frac * (el_b - el_a) if have_el else None  # type: ignore[operator]
        ts   = ts_a + timedelta(seconds=frac * total_s) if have_ts else None  # type: ignore[operator]
        result.append(Trackpoint(latitude=lat, longitude=lon, elevation=elev, timestamp=ts))
    return result, fell_back


def _interp_latlon(a: Trackpoint, b: Trackpoint, n: int) -> list[tuple[float, float]]:
    """Return n linearly-interpolated (lat, lon) pairs between a and b."""
    return [
        (
            a.latitude  + (i + 1) / (n + 1) * (b.latitude  - a.latitude),
            a.longitude + (i + 1) / (n + 1) * (b.longitude - a.longitude),
        )
        for i in range(n)
    ]


def _resample_route(
    route: list[tuple[float, float]],
    n: int,
) -> list[tuple[float, float]]:
    """Uniformly resample *n* interior points from a polyline (lat, lon list)."""
    if len(route) < 2 or n < 1:
        return []

    # Cumulative arc lengths along the polyline.
    cum = [0.0]
    for i in range(1, len(route)):
        d = _haversine(route[i - 1][0], route[i - 1][1], route[i][0], route[i][1])
        cum.append(cum[-1] + d)

    total = cum[-1]
    if total == 0.0:
        return [(route[0][0], route[0][1])] * n

    result: list[tuple[float, float]] = []
    seg = 1  # current segment index (into `route` and `cum`)
    for k in range(1, n + 1):
        target = total * k / (n + 1)
        # Advance to the segment that contains `target`.
        while seg < len(cum) - 1 and cum[seg] < target:
            seg += 1
        seg_len = cum[seg] - cum[seg - 1]
        frac = (target - cum[seg - 1]) / seg_len if seg_len > 0 else 0.0
        lat = route[seg - 1][0] + frac * (route[seg][0] - route[seg - 1][0])
        lon = route[seg - 1][1] + frac * (route[seg][1] - route[seg - 1][1])
        result.append((lat, lon))
    return result
