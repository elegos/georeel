import logging
from typing import Callable

from PIL import Image

# Satellite tiles come from a known server — not arbitrary user files — so the
# decompression-bomb guard is not needed here.
Image.MAX_IMAGE_PIXELS = None

from ..bounding_box import BoundingBox
from .providers import PROVIDERS, ProviderConfig, QUALITY_ZOOM, get_provider
from .source import SatelliteSource
from .texture import SatelliteTexture
from .tile_cache import TileCache, lon_to_x, lat_to_y, tile_nw

# Legacy private-name aliases kept for any code that imported them directly.
_lon_to_x = lon_to_x
_lat_to_y = lat_to_y
_tile_nw  = tile_nw

_log = logging.getLogger(__name__)
_MAX_WORKERS = 8
_TIMEOUT     = 10   # seconds per tile request


class XyzSource(SatelliteSource):
    """Fetches imagery by downloading XYZ/TMS slippy-map tiles to a TileCache.

    Tiles are stored on disk as raw server bytes (JPEG/PNG) rather than being
    composited into a single in-memory canvas.  The scene builder reads from
    the cache one Blender terrain tile at a time, so peak RAM during fetch is
    proportional to the number of concurrent workers (a few MB) rather than
    the total texture area.
    """

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
        self._quality  = quality

        # Resolve the URL template
        if provider.id == "custom":
            self._url_template = custom_url
        elif provider.requires_key:
            self._url_template = provider.url_template.replace("{api_key}", api_key)
        else:
            self._url_template = provider.url_template

        self._target_zoom = QUALITY_ZOOM.get(quality, 13)
        self._max_zoom    = provider.max_zoom

    @property
    def name(self) -> str:
        return self._provider.label

    def fetch(
        self,
        bbox: BoundingBox,
        progress_callback: Callable[[int, int], None] | None = None,
        on_demand: bool = False,
    ) -> SatelliteTexture:
        zoom = min(self._target_zoom, self._max_zoom)

        x_min = lon_to_x(bbox.min_lon, zoom)
        x_max = lon_to_x(bbox.max_lon, zoom)
        y_min = lat_to_y(bbox.max_lat, zoom)   # y increases southward
        y_max = lat_to_y(bbox.min_lat, zoom)

        cols        = x_max - x_min + 1
        rows        = y_max - y_min + 1
        total_tiles = cols * rows

        _log.info(
            "[satellite] zoom=%d  tiles=%d×%d=%d  quality=%s  fetch_mode=%s",
            zoom, cols, rows, total_tiles, self._quality,
            "on_demand" if on_demand else "prefetch",
        )
        if not on_demand and total_tiles > 2000:
            _log.warning(
                "[satellite] %d tiles to fetch — this may take a while. "
                "Lower the detail level in Render Settings if speed matters more than quality.",
                total_tiles,
            )

        cache = TileCache(
            url_template=self._url_template,
            zoom=zoom,
            max_workers=_MAX_WORKERS,
            timeout=_TIMEOUT,
            on_demand=on_demand,
        )
        if not on_demand:
            cache.prefetch(x_min, x_max, y_min, y_max, progress_callback=progress_callback)
        else:
            _log.info("[satellite] On-demand mode: tiles will be fetched per terrain tile")

        # Compute the native pixel dimensions so width/height are available
        # without decoding any image.
        W, H = cache.canvas_size(bbox)

        return SatelliteTexture(
            image=None,
            min_lat=bbox.min_lat,
            max_lat=bbox.max_lat,
            min_lon=bbox.min_lon,
            max_lon=bbox.max_lon,
            provider_id=self._provider.id,
            quality=self._quality,
            _tile_cache=cache,
            _dim_width=W,
            _dim_height=H,
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
