import math

import numpy as np
import srtm

from .bounding_box import BoundingBox
from .elevation_grid import ElevationGrid

# Target horizontal spacing between grid points (metres).
# 90 m matches SRTM3 native resolution and keeps grids small.
_TARGET_SPACING_M = 90.0
_M_PER_DEG_LAT = 111_320.0


class DemFetchError(Exception):
    pass


def fetch_dem(bbox: BoundingBox) -> ElevationGrid:
    """Download SRTM elevation data for *bbox* and return a regular grid.

    Tiles are cached on disk by srtm.py (~/.cache/srtm.py/).  Subsequent
    calls for the same region are fast.  The returned ElevationGrid is the
    processed cache stored inside the .georeel project file.
    """
    mid_lat = (bbox.min_lat + bbox.max_lat) / 2
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(mid_lat))

    lat_span = bbox.max_lat - bbox.min_lat
    lon_span = bbox.max_lon - bbox.min_lon

    rows = max(2, round(lat_span * _M_PER_DEG_LAT / _TARGET_SPACING_M) + 1)
    cols = max(2, round(lon_span * m_per_deg_lon / _TARGET_SPACING_M) + 1)

    try:
        elevation_data = srtm.get_data()
    except Exception as e:
        raise DemFetchError(f"Failed to initialise SRTM data: {e}") from e

    grid = np.zeros((rows, cols), dtype=np.float32)
    for r in range(rows):
        lat = bbox.max_lat - r * lat_span / (rows - 1)
        for c in range(cols):
            lon = bbox.min_lon + c * lon_span / (cols - 1)
            elev = elevation_data.get_elevation(lat, lon)
            grid[r, c] = elev if elev is not None else 0.0

    return ElevationGrid(
        data=grid,
        min_lat=bbox.min_lat,
        max_lat=bbox.max_lat,
        min_lon=bbox.min_lon,
        max_lon=bbox.max_lon,
    )
