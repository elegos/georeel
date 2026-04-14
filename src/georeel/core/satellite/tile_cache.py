"""On-disk cache of XYZ slippy-map tiles.

Tiles are downloaded in parallel and stored as raw image bytes (JPEG or PNG,
whatever the server returns) in a temporary directory.  PIL detects the
format from the file's magic bytes, so no extension is needed.

Usage
-----
    cache = TileCache(url_template, zoom)
    cache.prefetch(x_min, x_max, y_min, y_max, progress_callback=...)
    img = cache.composite(bbox)          # builds a PIL Image for a sub-region
    W, H = cache.canvas_size(bbox)       # native pixel dimensions, no decode
    cache.cleanup()                      # remove temp dir (also called by atexit)
"""

from __future__ import annotations

import atexit
import logging
import math
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import requests
from PIL import Image

from ..bounding_box import BoundingBox
from ..pil_lock import PIL_LOCK
from .. import temp_manager

_log = logging.getLogger(__name__)

_TILE_SIZE = 256
_TIMEOUT   = 10          # seconds per tile request
_USER_AGENT = "GeoReel/0.1 satellite-fetcher"

# Per-thread requests.Session — Session is not thread-safe for concurrent use.
_thread_local = threading.local()


def _session(user_agent: str) -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = user_agent
        _thread_local.session = s
    return s


# ------------------------------------------------------------------
# XYZ / Mercator coordinate helpers
# ------------------------------------------------------------------

def lon_to_x(lon: float, zoom: int) -> int:
    return int((lon + 180) / 360 * (2 ** zoom))


def lat_to_y(lat: float, zoom: int) -> int:
    lat_r = math.radians(lat)
    return int(
        (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi)
        / 2
        * (2 ** zoom)
    )


def tile_nw(tx: int, ty: int, zoom: int) -> tuple[float, float]:
    """Return (lat, lon) of the north-west corner of tile (tx, ty)."""
    n = 2 ** zoom
    lon = tx / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    return lat, lon


def _crop_bounds(
    bbox: BoundingBox, zoom: int
) -> tuple[int, int, int, int, int, int, int, int]:
    """Return (x_min, x_max, y_min, y_max, crop_left, crop_top, canvas_w, canvas_h)."""
    x_min = lon_to_x(bbox.min_lon, zoom)
    x_max = lon_to_x(bbox.max_lon, zoom)
    y_min = lat_to_y(bbox.max_lat, zoom)   # y increases southward
    y_max = lat_to_y(bbox.min_lat, zoom)

    nw_lat, nw_lon = tile_nw(x_min,     y_min,     zoom)
    se_lat, se_lon = tile_nw(x_max + 1, y_max + 1, zoom)
    full_w = (x_max - x_min + 1) * _TILE_SIZE
    full_h = (y_max - y_min + 1) * _TILE_SIZE
    total_lat = nw_lat - se_lat
    total_lon = se_lon - nw_lon

    crop_left   = max(0, round((bbox.min_lon - nw_lon) / total_lon * full_w))
    crop_right  = min(full_w, round((bbox.max_lon - nw_lon) / total_lon * full_w))
    crop_top    = max(0, round((nw_lat - bbox.max_lat) / total_lat * full_h))
    crop_bottom = min(full_h, round((nw_lat - bbox.min_lat) / total_lat * full_h))

    canvas_w = max(1, crop_right  - crop_left)
    canvas_h = max(1, crop_bottom - crop_top)
    return x_min, x_max, y_min, y_max, crop_left, crop_top, canvas_w, canvas_h


# ------------------------------------------------------------------
# TileCache
# ------------------------------------------------------------------

class TileCache:
    """Parallel downloader and on-demand compositor for XYZ satellite tiles.

    The constructor registers a cleanup handler with ``atexit`` so the
    temporary directory is removed when the process exits even if
    ``cleanup()`` is never explicitly called.

    When *on_demand* is True, ``prefetch()`` is a no-op and tiles are
    downloaded lazily inside ``composite()`` — only the tiles that
    overlap the requested bbox are fetched, right when they are needed.
    This avoids any upfront download wait and keeps peak disk/RAM usage
    proportional to a single terrain tile rather than the whole track.
    """

    def __init__(
        self,
        url_template: str,
        zoom: int,
        max_workers: int = 8,
        timeout: int = _TIMEOUT,
        user_agent: str = _USER_AGENT,
        on_demand: bool = False,
    ) -> None:
        self._url_template = url_template
        self._zoom         = zoom
        self._max_workers  = max_workers
        self._timeout      = timeout
        self._user_agent   = user_agent
        self._on_demand    = on_demand
        self._dir          = temp_manager.make_temp_dir("georeel_xyz_")
        # Track tiles that permanently failed so we never retry them.
        self._failed: set[tuple[int, int]] = set()
        atexit.register(self.cleanup)

    # ----------------------------------------------------------------
    # Properties
    # ----------------------------------------------------------------

    @property
    def zoom(self) -> int:
        return self._zoom

    @property
    def dir(self) -> Path:
        return self._dir

    # ----------------------------------------------------------------
    # Tile path
    # ----------------------------------------------------------------

    def _tile_path(self, tx: int, ty: int) -> Path:
        """Return the on-disk path for tile (tx, ty).  May not exist yet."""
        return self._dir / f"{tx}_{ty}.img"

    # ----------------------------------------------------------------
    # Download
    # ----------------------------------------------------------------

    def _download_tile(self, tx: int, ty: int) -> None:
        """Download a single tile to disk; mark it failed on any error.

        Safe to call from multiple threads concurrently — each thread uses
        its own ``requests.Session`` via ``_thread_local``.
        """
        if (tx, ty) in self._failed:
            return
        path = self._tile_path(tx, ty)
        if path.exists():
            return
        url = self._url_template.format(z=self._zoom, x=tx, y=ty)
        try:
            resp = _session(self._user_agent).get(url, timeout=self._timeout)
            resp.raise_for_status()
            path.write_bytes(resp.content)
        except Exception as exc:
            _log.debug("[tile_cache] tile (%d,%d) unavailable: %s", tx, ty, exc)
            self._failed.add((tx, ty))

    def prefetch(
        self,
        x_min: int,
        x_max: int,
        y_min: int,
        y_max: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """Download all tiles in the given XYZ range in parallel.

        Already-cached tiles are skipped.  Individual tile failures (HTTP
        errors, timeouts, ocean tiles returning 404) are logged as warnings
        and skipped — the compositor will leave those regions black.

        When the cache was created with *on_demand=True* this method is a
        no-op; tiles are fetched lazily inside ``composite()`` instead.
        """
        if self._on_demand:
            return

        tiles = [
            (tx, ty)
            for ty in range(y_min, y_max + 1)
            for tx in range(x_min, x_max + 1)
        ]
        total = len(tiles)
        completed = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {pool.submit(self._download_tile, tx, ty): (tx, ty) for tx, ty in tiles}
            for future in as_completed(futures):
                future.result()
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total)

    # ----------------------------------------------------------------
    # Geometry helpers
    # ----------------------------------------------------------------

    def canvas_size(self, bbox: BoundingBox) -> tuple[int, int]:
        """Return the native pixel dimensions ``(width, height)`` for *bbox*
        at this cache's zoom level — no image I/O performed."""
        _, _, _, _, _, _, w, h = _crop_bounds(bbox, self._zoom)
        return w, h

    # ----------------------------------------------------------------
    # Compositor
    # ----------------------------------------------------------------

    def composite(self, bbox: BoundingBox) -> Image.Image:
        """Return a PIL RGB image covering *bbox* from cached tiles.

        Only the tiles that overlap *bbox* are read.  Missing tiles
        (ocean, no-data) remain black in the output canvas.  The returned
        image is at native XYZ-tile resolution — resize in the caller if
        a different size is needed.

        When the cache was created with *on_demand=True*, any tiles that
        have not been downloaded yet are fetched in parallel here before
        the compositor runs — so only the tiles needed for this bbox are
        ever retrieved from the server.
        """
        x_min, x_max, y_min, y_max, crop_left, crop_top, canvas_w, canvas_h = (
            _crop_bounds(bbox, self._zoom)
        )

        if self._on_demand:
            needed = [
                (tx, ty)
                for ty in range(y_min, y_max + 1)
                for tx in range(x_min, x_max + 1)
                if (tx, ty) not in self._failed
                and not self._tile_path(tx, ty).exists()
            ]
            if needed:
                _log.debug(
                    "[tile_cache] on-demand: fetching %d tile(s) for bbox %s",
                    len(needed), bbox,
                )
                with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                    futures = {
                        pool.submit(self._download_tile, tx, ty)
                        for tx, ty in needed
                    }
                    for f in as_completed(futures):
                        f.result()

        with PIL_LOCK:
            canvas = Image.new("RGB", (canvas_w, canvas_h))
            for ty in range(y_min, y_max + 1):
                for tx in range(x_min, x_max + 1):
                    path = self._tile_path(tx, ty)
                    if not path.exists():
                        continue
                    try:
                        tile = Image.open(path).convert("RGB")
                    except Exception as exc:
                        _log.debug("[tile_cache] bad tile file (%d,%d): %s", tx, ty, exc)
                        continue
                    px = (tx - x_min) * _TILE_SIZE - crop_left
                    py = (ty - y_min) * _TILE_SIZE - crop_top
                    canvas.paste(tile, (px, py))
                    del tile

        return canvas

    # ----------------------------------------------------------------
    # Cleanup
    # ----------------------------------------------------------------

    def cleanup(self) -> None:
        """Remove the temporary tile directory from disk."""
        shutil.rmtree(self._dir, ignore_errors=True)
