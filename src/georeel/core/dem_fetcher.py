import math
from typing import Callable

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

# srtm library void thresholds (from GeoElevationFile.get_elevation_from_row_and_column)
_SRTM_RAW_MAX =  10_000
_SRTM_RAW_MIN =  -1_000


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
    nearest = np.asarray(distance_transform_edt(~valid, return_distances=False,
                                                return_indices=True))
    filled = grid.copy()
    filled[~valid] = grid[tuple(nearest[:, ~valid])]
    return filled


def fetch_dem(
    bbox: BoundingBox,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ElevationGrid:
    """Download SRTM elevation data for *bbox* and return a regular grid.

    Tiles are cached on disk by srtm.py (~/.cache/srtm/).  Subsequent
    calls for the same region are fast.  The returned ElevationGrid is the
    processed cache stored inside the .georeel project file.

    If *progress_callback* is provided it is called as
    ``progress_callback(tiles_done, total_tiles)`` after each tile is loaded.
    """
    mid_lat = (bbox.min_lat + bbox.max_lat) / 2
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(mid_lat))

    lat_span = bbox.max_lat - bbox.min_lat
    lon_span = bbox.max_lon - bbox.min_lon

    rows = max(2, round(lat_span * _M_PER_DEG_LAT / _TARGET_SPACING_M) + 1)
    cols = max(2, round(lon_span * m_per_deg_lon / _TARGET_SPACING_M) + 1)

    # Build coordinate meshgrid (lat decreasing north→south, lon increasing west→east)
    lats = np.linspace(bbox.max_lat, bbox.min_lat, rows)
    lons = np.linspace(bbox.min_lon, bbox.max_lon, cols)
    lon_grid, lat_grid = np.meshgrid(lons, lats)   # both shape (rows, cols)

    # Determine which integer-degree tiles are needed
    lat_tiles = range(math.floor(bbox.min_lat), math.floor(bbox.max_lat) + 1)
    lon_tiles = range(math.floor(bbox.min_lon), math.floor(bbox.max_lon) + 1)
    total_tiles = len(lat_tiles) * len(lon_tiles)

    grid = np.full((rows, cols), _SRTM_VOID, dtype=np.float32)

    try:
        elevation_data = srtm.get_data()
    except Exception as e:
        raise DemFetchError(f"Failed to initialise SRTM data: {e}") from e

    tiles_done = 0
    for tile_lat in lat_tiles:
        for tile_lon in lon_tiles:
            # get_file triggers download + cache if the tile isn't on disk yet.
            # Pass a point inside the tile (floor lat/lon + 0.5) so the filename
            # calculation inside srtm produces the correct tile name.
            geo_file = elevation_data.get_file(tile_lat + 0.5, tile_lon + 0.5)

            tiles_done += 1
            if progress_callback is not None:
                progress_callback(tiles_done, total_tiles)

            if geo_file is None or not geo_file.data:
                continue   # ocean / no coverage

            # Load raw big-endian int16 bytes directly into a numpy array —
            # this is the key vectorisation step that avoids the per-point
            # struct.unpack loop inside srtm's get_elevation().
            N = geo_file.square_side
            tile_arr = (
                np.frombuffer(geo_file.data, dtype=">i2")
                .reshape(N, N)
                .astype(np.float32)
            )
            # Mask srtm library void values (same thresholds as the library uses)
            tile_arr[(tile_arr > _SRTM_RAW_MAX) | (tile_arr < _SRTM_RAW_MIN)] = _SRTM_VOID

            f_lat = float(geo_file.latitude)   # integer SW-corner latitude
            f_lon = float(geo_file.longitude)  # integer SW-corner longitude

            # Select grid points that fall inside this 1°×1° tile
            mask = (
                (lat_grid >= f_lat) & (lat_grid < f_lat + 1.0) &
                (lon_grid >= f_lon) & (lon_grid < f_lon + 1.0)
            )
            if not mask.any():
                continue

            # Vectorised coordinate → tile-array index mapping.
            # Formula mirrors GeoElevationFile.get_row_and_column():
            #   row = floor((f_lat + 1 - lat) * (N - 1))
            #   col = floor((lon - f_lon)     * (N - 1))
            q_lats = lat_grid[mask]
            q_lons = lon_grid[mask]
            r_idx = np.floor((f_lat + 1.0 - q_lats) * (N - 1)).astype(np.intp)
            c_idx = np.floor((q_lons - f_lon)        * (N - 1)).astype(np.intp)
            np.clip(r_idx, 0, N - 1, out=r_idx)
            np.clip(c_idx, 0, N - 1, out=c_idx)

            grid[mask] = tile_arr[r_idx, c_idx]

    grid = _fill_voids(grid).astype(np.float32)

    return ElevationGrid(
        data=grid,
        min_lat=bbox.min_lat,
        max_lat=bbox.max_lat,
        min_lon=bbox.min_lon,
        max_lon=bbox.max_lon,
    )
