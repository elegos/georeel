import io
import logging
import re
from datetime import timezone

import gpxpy
import gpxpy.gpx

from .bounding_box import BoundingBox
from .trackpoint import Trackpoint

_log = logging.getLogger(__name__)

# Garmin TrackPoint Extension namespace used by devices like the Zumo / Edge.
# Elevation is stored as <gpxtpx:ele> inside <extensions> when the standard
# <ele> element is absent (rare, but happens with some export tools).
_GARMIN_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"


class GpxParseError(Exception):
    pass


# Well-known namespace URIs for common GPX extension prefixes.
_KNOWN_NS: dict[str, str] = {
    "ns3":    "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "gpxtpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "gpxx":   "http://www.garmin.com/xmlschemas/GpxExtensions/v3",
    "wptx1":  "http://www.garmin.com/xmlschemas/WaypointExtension/v1",
}


def _fix_undeclared_namespaces(content: str) -> str:
    """Inject missing xmlns:prefix declarations into the GPX root element.

    Some tools produce files that reference namespace prefixes (e.g. ``ns3:``)
    without declaring them, which makes the file invalid XML.  This commonly
    happens when two GPX files are merged and only the first file's root
    element is kept.
    """
    declared: set[str] = set(re.findall(r'xmlns:(\w+)\s*=', content))
    used: set[str] = set(re.findall(r'</?(\w+):', content))
    missing = used - declared
    if not missing:
        return content

    injections = []
    for prefix in sorted(missing):
        uri = _KNOWN_NS.get(prefix, f"urn:unknown-ns:{prefix}")
        injections.append(f'xmlns:{prefix}="{uri}"')
        _log.warning(
            "[gpx_parser] GPX file uses undeclared namespace prefix '%s'; "
            "injecting declaration (URI: %s)",
            prefix,
            uri,
        )

    injection_str = "\n    " + "\n    ".join(injections)

    # Inject into the root <gpx ...> opening tag, just before its closing >.
    def _inject(m: re.Match[str]) -> str:
        return m.group(0)[:-1] + injection_str + ">"

    fixed = re.sub(r"<gpx\b[^>]*>", _inject, content, count=1, flags=re.DOTALL)
    return fixed if fixed != content else content


def _elevation_from_extensions(point: gpxpy.gpx.GPXTrackPoint) -> float | None:
    """Try to read elevation from Garmin TrackPoint extensions as a fallback."""
    try:
        for ext in point.extensions:
            # ext may be an lxml or stdlib Element; try both APIs.
            ele = ext.find(f"{{{_GARMIN_NS}}}ele")
            if ele is None:
                ele = ext.find("ele")
            if ele is not None and ele.text:
                return float(ele.text)
    except Exception:
        pass
    return None


def parse_gpx(path: str) -> tuple[list[Trackpoint], BoundingBox]:
    """Parse a GPX file and return its trackpoints and bounding box.

    Raises GpxParseError if the file cannot be read or contains no points.
    """
    try:
        with open(path, encoding="utf-8") as f:
            gpx = gpxpy.parse(f)
    except Exception as first_err:
        # Attempt recovery: inject any undeclared namespace prefixes, then retry.
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            fixed = _fix_undeclared_namespaces(content)
            if fixed == content:
                raise GpxParseError(f"Cannot read GPX file: {first_err}") from first_err
            gpx = gpxpy.parse(io.StringIO(fixed))
        except GpxParseError:
            raise
        except Exception as e:
            raise GpxParseError(f"Cannot read GPX file: {first_err}") from e

    trackpoints: list[Trackpoint] = []
    missing_ele = 0
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                elevation = point.elevation
                if elevation is None:
                    elevation = _elevation_from_extensions(point)
                    if elevation is None:
                        missing_ele += 1
                trackpoints.append(
                    Trackpoint(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        elevation=elevation,
                        timestamp=point.time.astimezone(timezone.utc)
                        if point.time
                        else None,
                    )
                )

    if missing_ele:
        total = len(trackpoints)
        _log.warning(
            "[gpx_parser] %d/%d trackpoints have no elevation data%s",
            missing_ele,
            total,
            " — elevation stats will be unavailable." if missing_ele == total else ".",
        )

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
