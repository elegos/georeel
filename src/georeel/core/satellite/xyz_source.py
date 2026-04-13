import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Callable

import requests
from PIL import Image

# Satellite tiles come from a known server — not arbitrary user files — so the
# decompression-bomb guard is not needed here.
Image.MAX_IMAGE_PIXELS = None

from ..bounding_box import BoundingBox
from ..pil_lock import PIL_LOCK
from .providers import PROVIDERS, ProviderConfig, QUALITY_ZOOM, get_provider
from .source import SatelliteSource
from .texture import SatelliteTexture

_log = logging.getLogger(__name__)
_TILE_SIZE = 256
_MAX_WORKERS = 8
_TIMEOUT = 10          # seconds per tile request
_USER_AGENT = "GeoReel/0.1 satellite-fetcher"


class XyzSource(SatelliteSource):
    """Fetches imagery by stitching XYZ/TMS slippy-map tiles."""

    def __init__(
        self,
        provider: ProviderConfig | None = None,
        api_key: str = "",
        custom_url: str = "",
        quality: str = "standard",
    ):
        if provider is None:
            provider = PROVIDERS[0]
        self._provider = provider
        self._quality = quality

        # Resolve the URL template
        if provider.id == "custom":
            self._url_template = custom_url
        elif provider.requires_key:
            self._url_template = provider.url_template.replace("{api_key}", api_key)
        else:
            self._url_template = provider.url_template

        self._target_zoom = QUALITY_ZOOM.get(quality, 13)
        self._max_zoom = provider.max_zoom

    @property
    def name(self) -> str:
        return self._provider.label

    def fetch(
        self,
        bbox: BoundingBox,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> SatelliteTexture:
        zoom = min(self._target_zoom, self._max_zoom)

        x_min = _lon_to_x(bbox.min_lon, zoom)
        x_max = _lon_to_x(bbox.max_lon, zoom)
        y_min = _lat_to_y(bbox.max_lat, zoom)   # y increases southward
        y_max = _lat_to_y(bbox.min_lat, zoom)

        cols = x_max - x_min + 1
        rows = y_max - y_min + 1
        total_tiles = cols * rows

        _log.info(
            "[satellite] zoom=%d  tiles=%d×%d=%d  quality=%s",
            zoom, cols, rows, total_tiles, self._quality,
        )
        if total_tiles > 2000:
            _log.warning(
                "[satellite] %d tiles to fetch — this may take a while. "
                "Lower the detail level in Render Settings if speed matters more than quality.",
                total_tiles,
            )

        canvas = Image.new("RGB", (cols * _TILE_SIZE, rows * _TILE_SIZE))

        session = requests.Session()
        session.headers["User-Agent"] = _USER_AGENT

        def _fetch_tile(tx: int, ty: int) -> tuple[int, int, Image.Image]:
            url = self._url_template.format(z=zoom, x=tx, y=ty)
            resp = session.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            # PIL_LOCK: PIL's C extension is not thread-safe; network fetch is
            # outside the lock so parallel downloading is preserved.
            with PIL_LOCK:
                tile = Image.open(BytesIO(resp.content)).convert("RGB")
            return tx, ty, tile

        completed = 0
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(_fetch_tile, tx, ty): (tx, ty)
                for ty in range(y_min, y_max + 1)
                for tx in range(x_min, x_max + 1)
            }
            for future in as_completed(futures):
                tx, ty, tile = future.result()
                px = (tx - x_min) * _TILE_SIZE
                py = (ty - y_min) * _TILE_SIZE
                canvas.paste(tile, (px, py))
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total_tiles)

        # Crop to the exact bounding box
        nw_lat, nw_lon = _tile_nw(x_min,     y_min,     zoom)
        se_lat, se_lon = _tile_nw(x_max + 1, y_max + 1, zoom)

        total_lat = nw_lat - se_lat
        total_lon = se_lon - nw_lon

        left   = round((bbox.min_lon - nw_lon) / total_lon * canvas.width)
        right  = round((bbox.max_lon - nw_lon) / total_lon * canvas.width)
        top    = round((nw_lat - bbox.max_lat) / total_lat * canvas.height)
        bottom = round((nw_lat - bbox.min_lat) / total_lat * canvas.height)

        cropped = canvas.crop((left, top, right, bottom))

        return SatelliteTexture(
            image=cropped,
            min_lat=bbox.min_lat,
            max_lat=bbox.max_lat,
            min_lon=bbox.min_lon,
            max_lon=bbox.max_lon,
            provider_id=self._provider.id,
            quality=self._quality,
        )


def build_source(
    provider_id: str = "esri_world",
    api_key: str = "",
    custom_url: str = "",
    quality: str = "standard",
) -> XyzSource:
    """Factory: build an XyzSource from plain config values (no Qt dependency)."""
    return XyzSource(
        provider=get_provider(provider_id),
        api_key=api_key,
        custom_url=custom_url,
        quality=quality,
    )


# ------------------------------------------------------------------
# Tile coordinate helpers
# ------------------------------------------------------------------

def _lon_to_x(lon: float, zoom: int) -> int:
    return int((lon + 180) / 360 * (2 ** zoom))


def _lat_to_y(lat: float, zoom: int) -> int:
    lat_r = math.radians(lat)
    return int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * (2 ** zoom))


def _tile_nw(tx: int, ty: int, zoom: int) -> tuple[float, float]:
    """Return (lat, lon) of the NW corner of tile (tx, ty)."""
    n = 2 ** zoom
    lon = tx / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    return lat, lon




