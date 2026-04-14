from __future__ import annotations

import io
import logging
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, IO

if TYPE_CHECKING:
    from .tile_cache import TileCache

from PIL import Image

from ..pil_lock import PIL_LOCK

_log = logging.getLogger(__name__)


@dataclass
class SatelliteTexture:
    """A stitched, georeferenced RGB satellite image.

    The PIL Image may be freed after the scene tiles have been written to disk
    (call free_image()).  write_png() will reassemble from the tile files
    transparently, so project save still works without keeping the full image
    in RAM.

    Alternatively, when loaded from a project ZIP (from_zip_lazy), the image
    is never decoded into RAM at all — write_png() streams directly from the
    source ZIP entry on demand.
    """

    image: Image.Image | None
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    provider_id: str = ""
    quality: str = "standard"
    # Set by _write_texture_tiles / free_image so we can reassemble on demand.
    _tiles_dir: Path | None = field(default=None, repr=False)
    _tiles_manifest: dict | None = field(default=None, repr=False)
    # Set by from_zip_lazy — stream directly from the source ZIP without decoding.
    _source_zip: Path | None = field(default=None, repr=False)
    _source_entry: str | None = field(default=None, repr=False)
    # Set by XyzSource.fetch() — on-disk XYZ tiles, no global canvas ever built.
    _tile_cache: TileCache | None = field(default=None, repr=False)
    # Cached pixel dimensions — populated from image.size, tile manifest,
    # tile cache geometry, or the PNG IHDR header.
    _dim_width: int | None = field(default=None, repr=False)
    _dim_height: int | None = field(default=None, repr=False)

    @property
    def width(self) -> int:
        if self.image is not None:
            return self.image.width
        if self._dim_width is not None:
            return self._dim_width
        raise RuntimeError(
            "SatelliteTexture dimensions not available (image not loaded and no cached size)."
        )

    @property
    def height(self) -> int:
        if self.image is not None:
            return self.image.height
        if self._dim_height is not None:
            return self._dim_height
        raise RuntimeError(
            "SatelliteTexture dimensions not available (image not loaded and no cached size)."
        )

    def memory_bytes(self) -> int:
        """Approximate bytes used by the in-memory PIL image (0 if on disk / lazy)."""
        if self.image is None:
            return 0
        w, h = self.image.size
        bands = len(self.image.getbands())
        return w * h * bands

    def has_pixels(self) -> bool:
        """Return True if pixel data is available in RAM (image is decoded)."""
        return self.image is not None

    def free_image(
        self,
        tiles_dir: Path | None = None,
        tiles_manifest: dict | None = None,
    ) -> None:
        """Release the PIL Image from RAM.

        If *tiles_dir* and *tiles_manifest* are provided (from _write_texture_tiles),
        write_png() will reassemble the image from the tile PNGs on demand so
        project save still works.  Without them, write_png() will raise.
        """
        if tiles_dir is not None:
            self._tiles_dir = tiles_dir
        if tiles_manifest is not None:
            self._tiles_manifest = tiles_manifest
            self._dim_width  = tiles_manifest.get("image_width")
            self._dim_height = tiles_manifest.get("image_height")
        if self.image is not None:
            self._dim_width  = self.image.width
            self._dim_height = self.image.height
        mb = self.memory_bytes() / 1024 ** 2
        self.image = None
        # Blender tiles are now on disk; the XYZ tile cache is no longer needed.
        self._tile_cache = None
        _log.info("[memory] SatelliteTexture image freed (%.0f MB reclaimed)", mb)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def write_png(self, dest: IO[bytes]) -> None:
        """Stream the PNG directly into *dest* without an intermediate bytes copy.

        Priority order:
        1. Image in RAM — save directly.
        2. Lazy ZIP source (_source_zip) — copy raw bytes, zero decode.
        3. Tile cache (_tile_cache) — composite full bbox on demand.
        4. Blender tile manifest (_tiles_manifest) — reassemble from tile PNGs.
        """
        if self.image is not None:
            with PIL_LOCK:
                img = self.image if self.image.mode == "RGB" else self.image.convert("RGB")
                img.save(dest, format="PNG", optimize=False)
            return

        # Lazy ZIP source — stream the stored PNG bytes directly without decoding.
        if self._source_zip is not None and self._source_entry is not None:
            _log.info("[memory] Streaming satellite texture from source ZIP (no decode)")
            with zipfile.ZipFile(self._source_zip, "r") as zf:
                with zf.open(self._source_entry) as src:
                    while True:
                        chunk = src.read(1 << 20)  # 1 MiB at a time
                        if not chunk:
                            break
                        dest.write(chunk)
            return

        # Tile cache — composite the full bbox on demand (used when saving a
        # project before the scene has been built, so no Blender tiles exist yet).
        if self._tile_cache is not None:
            from ..bounding_box import BoundingBox
            bbox = BoundingBox(self.min_lat, self.max_lat, self.min_lon, self.max_lon)
            _log.info("[memory] Compositing full satellite texture from tile cache for save")
            with PIL_LOCK:
                img = self._tile_cache.composite(bbox)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(dest, format="PNG", optimize=False)
            return

        # Image was freed — reassemble from Blender tile PNGs.
        if self._tiles_dir is None or self._tiles_manifest is None:
            raise RuntimeError(
                "SatelliteTexture.image is None and no tile backing is available. "
                "Cannot serialise."
            )
        _log.info("[memory] Reassembling satellite texture from %d tile(s) for save",
                  len(self._tiles_manifest["tiles"]))
        img_w = self._tiles_manifest["image_width"]
        img_h = self._tiles_manifest["image_height"]
        with PIL_LOCK:
            canvas = Image.new("RGB", (img_w, img_h))
            for t in self._tiles_manifest["tiles"]:
                tile_path = Path(t["path"])
                if tile_path.exists():
                    tile = Image.open(tile_path).convert("RGB")
                    canvas.paste(tile, (t["px_left"], t["px_top"]))
                    del tile
            canvas.save(dest, format="PNG", optimize=False)

    def to_png_bytes(self) -> bytes:
        """Return the texture as a PNG-encoded bytes object (for small textures only)."""
        buf = io.BytesIO()
        self.write_png(buf)
        return buf.getvalue()

    @classmethod
    def from_png_stream(
        cls,
        stream: IO[bytes],
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        provider_id: str = "",
        quality: str = "standard",
    ) -> "SatelliteTexture":
        """Load from a file-like object (e.g. an open ZipExtFile).

        Avoids reading the entire compressed PNG into a Python bytes object
        first — the decompressed pixels are the only large allocation.
        """
        with PIL_LOCK:
            image = Image.open(stream)
            image.load()   # must load while stream is open
            if image.mode != "RGB":
                image = image.convert("RGB")
        return cls(
            image=image,
            min_lat=min_lat,
            max_lat=max_lat,
            min_lon=min_lon,
            max_lon=max_lon,
            provider_id=provider_id,
            quality=quality,
        )

    @classmethod
    def from_png_bytes(
        cls,
        data: bytes,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        provider_id: str = "",
        quality: str = "standard",
    ) -> "SatelliteTexture":
        """Load from a bytes object (legacy; prefer from_png_stream)."""
        return cls.from_png_stream(
            io.BytesIO(data),
            min_lat=min_lat, max_lat=max_lat,
            min_lon=min_lon, max_lon=max_lon,
            provider_id=provider_id, quality=quality,
        )

    @classmethod
    def from_zip_lazy(
        cls,
        zip_path: Path,
        entry: str,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        provider_id: str = "",
        quality: str = "standard",
    ) -> "SatelliteTexture":
        """Create a lazy reference to a PNG stored inside a ZIP archive.

        The PNG is NOT decoded.  No pixels are loaded into RAM.  The object
        carries the geo-metadata immediately; the image data is only streamed
        (without decoding) when write_png() is eventually called during save.

        Use this when loading a project — the satellite texture is only needed
        if the user re-saves the project without fetching a new texture.
        """
        # Peek at the PNG IHDR chunk to get pixel dimensions without decoding.
        # Layout: 8-byte signature + 4-byte length + 4-byte "IHDR" + 4W + 4H
        dim_w: int | None = None
        dim_h: int | None = None
        try:
            with zipfile.ZipFile(zip_path, "r") as _zf:
                with _zf.open(entry) as _f:
                    header = _f.read(24)
            if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
                dim_w, dim_h = struct.unpack(">II", header[16:24])
        except Exception:
            pass   # dimensions unavailable — width/height will raise if accessed

        _log.info(
            "[memory] SatelliteTexture lazy-loaded from %s::%s (no decode, %s)",
            zip_path.name, entry,
            f"{dim_w}×{dim_h} px" if dim_w is not None else "size unknown",
        )
        obj = cls(
            image=None,
            min_lat=min_lat,
            max_lat=max_lat,
            min_lon=min_lon,
            max_lon=max_lon,
            provider_id=provider_id,
            quality=quality,
        )
        obj._source_zip = zip_path
        obj._source_entry = entry
        obj._dim_width = dim_w
        obj._dim_height = dim_h
        return obj

    def load_image(self) -> Image.Image:
        """Decode and return the PIL Image, loading from the source ZIP if needed.

        The result is cached in self.image for subsequent calls.
        """
        if self.image is not None:
            return self.image
        if self._source_zip is not None and self._source_entry is not None:
            _log.info("[memory] Loading satellite texture image from source ZIP")
            with zipfile.ZipFile(self._source_zip, "r") as zf:
                with zf.open(self._source_entry) as src:
                    with PIL_LOCK:
                        image = Image.open(src)
                        image.load()
                        if image.mode != "RGB":
                            image = image.convert("RGB")
            self.image = image
            self._dim_width  = image.width
            self._dim_height = image.height
            return image
        raise RuntimeError(
            "SatelliteTexture has no image and no source ZIP to load from."
        )
