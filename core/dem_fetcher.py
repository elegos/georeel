import math

import numpy as np
from scipy.ndimage import distance_transform_edt
import srtm

from .bounding_box import BoundingBox
from .elevation_grid import ElevationGrid

# Target horizontal spacing between grid points (metres).
# 90 m matches SRTM3 native resolution and keeps grids small.
_TARGET_SPACING_M = 90.0
_M_PER_DEG_LAT = 111_320.0

# SRTM void sentinel values and plausible elevation range.
_SRTM_VOID    = -32768.0
_ELEV_MIN_M   =  -500.0   # Dead Sea ~-430 m
_ELEV_MAX_M   = 9_000.0   # Everest  ~8849 m


class DemFetchError(Exception):
    pass


def _fill_voids(grid: np.ndarray) -> np.ndarray:
    """Replace void / out-of-range cells with nearest-neighbour interpolation.

    SRTM tiles have data voids (radar shadow, water) stored as -32768 or
    returned as None (→ 0.0 by the fetcher).  Rather than leaving flat
    patches at sea level, we propagate the nearest valid elevation value.
    """
    valid = (
        np.isfinite(grid)
        & (grid > _ELEV_MIN_M)
        & (grid < _ELEV_MAX_M)
        & (grid != _SRTM_VOID)
    )

    if valid.all():
        return grid   # nothing to fix

    if not valid.any():
        # Entire grid is void — return a flat 0 m surface
        return np.zeros_like(grid)

    # distance_transform_edt returns, for each void cell, the index of the
    # nearest valid cell — cheapest correct nearest-neighbour fill.
    _, nearest = distance_transform_edt(~valid, return_distances=True,
                                        return_indices=True)
    filled = grid.copy()
    filled[~valid] = grid[tuple(nearest[:, ~valid])]
    return filled


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
            grid[r, c] = elev if elev is not None else _SRTM_VOID

    grid = _fill_voids(grid).astype(np.float32)

    return ElevationGrid(
        data=grid,
        min_lat=bbox.min_lat,
        max_lat=bbox.max_lat,
        min_lon=bbox.min_lon,
        max_lon=bbox.max_lon,
    )
