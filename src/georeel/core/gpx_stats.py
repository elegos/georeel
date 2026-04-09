"""
Compute summary statistics from a list of Trackpoints.
No external dependencies beyond the standard library and numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .trackpoint import Trackpoint

_R_EARTH_M = 6_371_000.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _R_EARTH_M * math.asin(math.sqrt(a))


@dataclass
class GpxStats:
    point_count: int

    # Time
    start_time: datetime | None
    end_time: datetime | None
    duration: timedelta | None

    # Distance / speed
    total_distance_m: float         # 2-D great-circle sum
    avg_speed_kmh: float | None     # total_distance / duration
    max_speed_kmh: float | None     # peak between consecutive points

    # Elevation
    min_elevation_m: float | None
    max_elevation_m: float | None
    elevation_gain_m: float         # cumulative ascent
    elevation_loss_m: float         # cumulative descent (positive value)


def compute_stats(trackpoints: list[Trackpoint]) -> GpxStats:
    n = len(trackpoints)
    if n == 0:
        return GpxStats(
            point_count=0,
            start_time=None, end_time=None, duration=None,
            total_distance_m=0.0,
            avg_speed_kmh=None, max_speed_kmh=None,
            min_elevation_m=None, max_elevation_m=None,
            elevation_gain_m=0.0, elevation_loss_m=0.0,
        )

    # Time
    timed = [tp for tp in trackpoints if tp.timestamp is not None]
    start_time = timed[0].timestamp  if timed else None
    end_time   = timed[-1].timestamp if timed else None
    duration   = (end_time - start_time) if (start_time and end_time) else None

    # Distance & speed
    total_dist = 0.0
    max_speed_kmh: float | None = None

    for i in range(1, n):
        a, b = trackpoints[i - 1], trackpoints[i]
        seg_m = _haversine(a.latitude, a.longitude, b.latitude, b.longitude)
        total_dist += seg_m

        if a.timestamp and b.timestamp:
            dt_s = (b.timestamp - a.timestamp).total_seconds()
            if dt_s > 0:
                seg_kmh = (seg_m / dt_s) * 3.6
                if max_speed_kmh is None or seg_kmh > max_speed_kmh:
                    max_speed_kmh = seg_kmh

    avg_speed_kmh: float | None = None
    if duration and duration.total_seconds() > 0:
        avg_speed_kmh = (total_dist / duration.total_seconds()) * 3.6

    # Elevation
    elevs = [tp.elevation for tp in trackpoints if tp.elevation is not None]
    min_elevation_m = min(elevs) if elevs else None
    max_elevation_m = max(elevs) if elevs else None

    gain = 0.0
    loss = 0.0
    for i in range(1, n):
        a_e = trackpoints[i - 1].elevation
        b_e = trackpoints[i].elevation
        if a_e is not None and b_e is not None:
            delta = b_e - a_e
            if delta > 0:
                gain += delta
            else:
                loss += -delta

    return GpxStats(
        point_count=n,
        start_time=start_time,
        end_time=end_time,
        duration=duration,
        total_distance_m=total_dist,
        avg_speed_kmh=avg_speed_kmh,
        max_speed_kmh=max_speed_kmh,
        min_elevation_m=min_elevation_m,
        max_elevation_m=max_elevation_m,
        elevation_gain_m=gain,
        elevation_loss_m=loss,
    )
