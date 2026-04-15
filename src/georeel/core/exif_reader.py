from datetime import datetime
from typing import Any

from PIL import Image, UnidentifiedImageError

from .photo_metadata import PhotoMetadata

_TAG_DATETIME_ORIGINAL = 0x9003  # 36867 — DateTimeOriginal (preferred)
_TAG_DATETIME_DIGITIZED = 0x9004  # 36868 — DateTimeDigitized
_TAG_DATETIME = 0x0132            #   306 — DateTime (fallback)
_TAG_GPS_IFD = 0x8825

_GPS_LAT_REF = 1
_GPS_LAT = 2
_GPS_LON_REF = 3
_GPS_LON = 4


def _dms_to_decimal(dms: tuple[Any, ...], ref: str) -> float:
    degrees, minutes, seconds = (float(v) for v in dms)
    decimal = degrees + minutes / 60 + seconds / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def _parse_gps(gps_ifd: dict[int, Any]) -> tuple[float, float] | None:
    try:
        lat = _dms_to_decimal(gps_ifd[_GPS_LAT], gps_ifd[_GPS_LAT_REF])
        lon = _dms_to_decimal(gps_ifd[_GPS_LON], gps_ifd[_GPS_LON_REF])
        return lat, lon
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def read_photo_metadata(path: str) -> PhotoMetadata:
    """Extract EXIF timestamp and GPS coordinates from an image file."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
    except (UnidentifiedImageError, OSError, Exception):
        return PhotoMetadata(path=path, timestamp=None, latitude=None, longitude=None)

    raw_ts = (
        exif.get(_TAG_DATETIME_ORIGINAL)
        or exif.get(_TAG_DATETIME_DIGITIZED)
        or exif.get(_TAG_DATETIME)
    )
    timestamp = _parse_timestamp(raw_ts)

    gps_ifd = exif.get_ifd(_TAG_GPS_IFD)
    coords = _parse_gps(gps_ifd) if gps_ifd else None
    latitude, longitude = coords if coords else (None, None)

    return PhotoMetadata(
        path=path,
        timestamp=timestamp,
        latitude=latitude,
        longitude=longitude,
    )
