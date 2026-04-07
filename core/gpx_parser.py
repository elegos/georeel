import gpxpy

from .bounding_box import BoundingBox
from .trackpoint import Trackpoint


class GpxParseError(Exception):
    pass


def parse_gpx(path: str) -> tuple[list[Trackpoint], BoundingBox]:
    """Parse a GPX file and return its trackpoints and bounding box.

    Raises GpxParseError if the file cannot be read or contains no points.
    """
    try:
        with open(path, encoding="utf-8") as f:
            gpx = gpxpy.parse(f)
    except Exception as e:
        raise GpxParseError(f"Cannot read GPX file: {e}") from e

    trackpoints: list[Trackpoint] = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                trackpoints.append(Trackpoint(
                    latitude=point.latitude,
                    longitude=point.longitude,
                    elevation=point.elevation,
                    timestamp=point.time.replace(tzinfo=None) if point.time else None,
                ))

    if not trackpoints:
        raise GpxParseError("GPX file contains no trackpoints.")

    lats = [p.latitude for p in trackpoints]
    lons = [p.longitude for p in trackpoints]
    bbox = BoundingBox(
        min_lat=min(lats),
        max_lat=max(lats),
        min_lon=min(lons),
        max_lon=max(lons),
    )

    return trackpoints, bbox
