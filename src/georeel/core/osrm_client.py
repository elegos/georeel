"""Thin OSRM routing client using the public demo server (no API key needed).

Public server: http://router.project-osrm.org
  Profiles available: driving, cycling, walking.
  Terms: demo/development use only.  For production deployments, self-host
  OSRM or consider one of the alternatives below.

Free alternatives (no API key):
  Valhalla (OpenStreetMap.de): https://valhalla1.openstreetmap.de/route
    (requires a POST JSON body with a different schema — not implemented here)

Free alternatives (API key required, free tier):
  OpenRouteService: https://openrouteservice.org/  — 2 000 requests / day
  GraphHopper:      https://www.graphhopper.com/   — 500 requests / day
"""
from __future__ import annotations

import json
import urllib.request

_OSRM_BASE = "http://router.project-osrm.org/route/v1"
_TIMEOUT = 5  # seconds


def route_waypoints(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    *,
    profile: str = "driving",
) -> list[tuple[float, float]] | None:
    """Return (lat, lon) waypoints along the shortest route, or None.

    Uses the OSRM public demo server with the given routing profile
    (``"driving"``, ``"cycling"``, or ``"walking"``).  Returns ``None`` on any
    error — network failure, no route found, etc. — so the caller can fall
    back to ground interpolation.
    """
    url = (
        f"{_OSRM_BASE}/{profile}"
        f"/{lon1:.7f},{lat1:.7f};{lon2:.7f},{lat2:.7f}"
        "?overview=full&geometries=geojson"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "georeel/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read())
        if data.get("code") != "Ok":
            return None
        coords = data["routes"][0]["geometry"]["coordinates"]
        # GeoJSON convention: [longitude, latitude] — swap to (lat, lon).
        return [(lat, lon) for lon, lat in coords]
    except Exception:
        return None
