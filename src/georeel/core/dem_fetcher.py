import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import numpy as np
from scipy.ndimage import distance_transform_edt
import srtm

from .bounding_box import BoundingBox
from .elevation_grid import ElevationGrid

_log = logging.getLogger(__name__)

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

# Parallel tile download workers.  More workers = faster on first download;
# the srtm library writes each tile to a separate cache file so concurrent
# downloads of *different* tiles are safe.
_DEM_WORKERS = 8


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


def _parse_tile(geo_file: object) -> tuple[np.ndarray, int, float, float] | None:
    """Convert a cached srtm GeoElevationFile to a float32 tile array.

    Returns (tile_arr, N, f_lat, f_lon) or None if the tile has no data.
    """
    if geo_file is None or not geo_file.data:  # type: ignore[union-attr]
        return None
    N: int = geo_file.square_side  # type: ignore[union-attr]
    tile_arr = (
        np.frombuffer(geo_file.data, dtype=">i2")  # type: ignore[union-attr]
        .reshape(N, N)
        .astype(np.float32)
    )
    # Mask srtm library void values (same thresholds as the library uses)
    tile_arr[(tile_arr > _SRTM_RAW_MAX) | (tile_arr < _SRTM_RAW_MIN)] = _SRTM_VOID
    return tile_arr, N, float(geo_file.latitude), float(geo_file.longitude)  # type: ignore[union-attr]


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

    Design notes
    ------------
    - We avoid building a full (rows, cols) meshgrid — that would allocate
      two float64 arrays the size of the output grid (up to 10+ GB for large
      bboxes).  Instead we keep 1D lat/lon arrays and use np.searchsorted to
      find the row/col slice for each 1°×1° SRTM tile, then build a small
      per-tile meshgrid only for that slice.

    - Tile downloads are parallelised with a ThreadPoolExecutor.  Each SRTM
      tile is a separate file in the on-disk cache, so concurrent downloads of
      different tiles are safe.
    """
    mid_lat = (bbox.min_lat + bbox.max_lat) / 2
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(mid_lat))

    lat_span = bbox.max_lat - bbox.min_lat
    lon_span = bbox.max_lon - bbox.min_lon

    rows = max(2, round(lat_span * _M_PER_DEG_LAT / _TARGET_SPACING_M) + 1)
    cols = max(2, round(lon_span * m_per_deg_lon / _TARGET_SPACING_M) + 1)

    # 1D coordinate arrays — lats decreases N→S, lons increases W→E.
    # No meshgrid here: the full (rows×cols) grids would be 2×rows×cols×8 bytes
    # of float64 (e.g. 10 GB for a 56 k×12 k grid).  We build per-tile slices
    # via searchsorted instead.
    lats = np.linspace(bbox.max_lat, bbox.min_lat, rows)   # float64 1-D (rows,)
    lons = np.linspace(bbox.min_lon, bbox.max_lon, cols)   # float64 1-D (cols,)

    lat_tiles = list(range(math.floor(bbox.min_lat), math.floor(bbox.max_lat) + 1))
    lon_tiles = list(range(math.floor(bbox.min_lon), math.floor(bbox.max_lon) + 1))
    all_tiles = [(tlat, tlon) for tlat in lat_tiles for tlon in lon_tiles]
    total_tiles = len(all_tiles)

    _log.info("[dem] %d×%d grid, %d SRTM tiles to load", rows, cols, total_tiles)

    grid = np.full((rows, cols), _SRTM_VOID, dtype=np.float32)

    try:
        elevation_data = srtm.get_data()
    except Exception as e:
        raise DemFetchError(f"Failed to initialise SRTM data: {e}") from e

    # ------------------------------------------------------------------ #
    # Parallel tile download                                               #
    # Each tile downloads to a separate file in ~/.cache/srtm/ so there  #
    # are no write-write conflicts between workers.                        #
    # ------------------------------------------------------------------ #
    tiles_done = 0

    def _load(tile_lat: int, tile_lon: int) -> tuple[int, int, object]:
        geo_file = elevation_data.get_file(tile_lat + 0.5, tile_lon + 0.5)
        return tile_lat, tile_lon, geo_file

    with ThreadPoolExecutor(max_workers=_DEM_WORKERS) as pool:
        futures = {pool.submit(_load, tlat, tlon): (tlat, tlon)
                   for tlat, tlon in all_tiles}
        for future in as_completed(futures):
            tile_lat, tile_lon, geo_file = future.result()

            tiles_done += 1
            if progress_callback is not None:
                progress_callback(tiles_done, total_tiles)

            parsed = _parse_tile(geo_file)
            if parsed is None:
                continue
            tile_arr, N, f_lat, f_lon = parsed

            # ---------------------------------------------------------- #
            # Find which rows/cols of the output grid fall in this tile.  #
            # lats is *decreasing*, so we negate for searchsorted.        #
            # ---------------------------------------------------------- #
            r_start = int(np.searchsorted(-lats, -(f_lat + 1.0), side="left"))
            r_end   = int(np.searchsorted(-lats, -f_lat,         side="right"))
            c_start = int(np.searchsorted( lons,   f_lon,         side="left"))
            c_end   = int(np.searchsorted( lons,   f_lon + 1.0,   side="right"))

            if r_start >= r_end or c_start >= c_end:
                continue

            sub_lats = lats[r_start:r_end]   # shape (nr,)
            sub_lons = lons[c_start:c_end]   # shape (nc,)

            # Small meshgrid only for this tile's slice.
            sub_lon_g, sub_lat_g = np.meshgrid(sub_lons, sub_lats)  # (nr, nc)

            r_idx = np.floor((f_lat + 1.0 - sub_lat_g) * (N - 1)).astype(np.intp)
            c_idx = np.floor((sub_lon_g - f_lon)        * (N - 1)).astype(np.intp)
            np.clip(r_idx, 0, N - 1, out=r_idx)
            np.clip(c_idx, 0, N - 1, out=c_idx)

            grid[r_start:r_end, c_start:c_end] = tile_arr[r_idx, c_idx]

    grid = _fill_voids(grid).astype(np.float32)

    return ElevationGrid(
        data=grid,
        min_lat=bbox.min_lat,
        max_lat=bbox.max_lat,
        min_lon=bbox.min_lon,
        max_lon=bbox.max_lon,
    )
