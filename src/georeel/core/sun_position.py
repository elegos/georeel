"""
Computes solar azimuth and elevation from geographic coordinates and UTC time.

Uses the NOAA simplified solar position algorithm, accurate to within ~0.5°
for years 2000–2100. No external dependencies.
"""

import math
from datetime import datetime, timezone


def sun_angles(lat_deg: float, lon_deg: float, dt: datetime) -> tuple[float, float]:
    """Return *(azimuth_deg, elevation_deg)* of the sun.

    - azimuth: degrees clockwise from North (0=N, 90=E, 180=S, 270=W)
    - elevation: degrees above the horizon (negative means below horizon)

    *dt* is treated as UTC when it carries no timezone info.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)

    # Julian day relative to J2000.0
    j2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
    jd = (dt_utc - j2000).total_seconds() / 86400.0
    T = jd / 36525.0  # Julian centuries

    # Geometric mean longitude of the sun (deg)
    L0 = (280.46646 + 36000.76983 * T) % 360.0

    # Mean anomaly (deg → rad)
    M = math.radians((357.52911 + 35999.05029 * T - 0.0001537 * T * T) % 360.0)

    # Equation of centre (deg)
    C = (
        (1.914602 - 0.004817 * T - 0.000014 * T * T) * math.sin(M)
        + (0.019993 - 0.000101 * T) * math.sin(2 * M)
        + 0.000289 * math.sin(3 * M)
    )

    # Sun's true longitude (rad)
    sun_lon = math.radians(L0 + C)

    # Mean obliquity of ecliptic (rad)
    obliquity = math.radians(23.439291111 - 0.013004167 * T)

    # Solar declination (rad)
    dec = math.asin(math.sin(obliquity) * math.sin(sun_lon))

    # Equation of time (minutes)
    e = 0.016708634 - 0.000042037 * T
    y = math.tan(obliquity / 2) ** 2
    eot = 4.0 * math.degrees(
        y * math.sin(2 * math.radians(L0))
        - 2 * e * math.sin(M)
        + 4 * e * y * math.sin(M) * math.cos(2 * math.radians(L0))
        - 0.5 * y * y * math.sin(4 * math.radians(L0))
        - 1.25 * e * e * math.sin(2 * M)
    )

    # True solar time (minutes from midnight UTC) and hour angle
    minutes_utc = dt_utc.hour * 60 + dt_utc.minute + dt_utc.second / 60.0
    tst = minutes_utc + eot + 4.0 * lon_deg
    ha = math.radians((tst - 720.0) / 4.0)

    lat = math.radians(lat_deg)

    # Solar elevation
    sin_el = max(-1.0, min(1.0,
        math.sin(lat) * math.sin(dec)
        + math.cos(lat) * math.cos(dec) * math.cos(ha)
    ))
    elevation = math.degrees(math.asin(sin_el))

    # Solar azimuth (clockwise from North)
    cos_el = math.cos(math.asin(sin_el))
    if abs(cos_el) < 1e-10:
        azimuth = 0.0
    else:
        cos_az = max(-1.0, min(1.0,
            (math.sin(dec) - math.sin(lat) * sin_el) / (math.cos(lat) * cos_el)
        ))
        azimuth = math.degrees(math.acos(cos_az))
        if math.sin(ha) > 0:  # afternoon: sun has passed south and moved west
            azimuth = 360.0 - azimuth

    return azimuth, elevation


def sun_direction_vector(az_deg: float, el_deg: float) -> tuple[float, float, float]:
    """Convert azimuth/elevation to a unit vector pointing FROM the ground TOWARD the sun.

    Coordinate system: X=East, Y=North, Z=Up (matches Blender's default world axes).
    Elevation is clamped to 0° so the vector never points below the horizon.
    """
    az = math.radians(az_deg)
    el = math.radians(max(el_deg, 0.0))
    x = math.sin(az) * math.cos(el)
    y = math.cos(az) * math.cos(el)
    z = math.sin(el)
    return x, y, z
