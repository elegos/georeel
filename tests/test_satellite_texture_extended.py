"""Extended tests for satellite.texture.SatelliteTexture — lazy loading, free_image,
write_png paths, from_zip_lazy, load_image, memory_bytes, has_pixels."""

from __future__ import annotations

import io
import struct
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from georeel.core.satellite.texture import SatelliteTexture

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(width=64, height=48, color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_texture(width=100, height=80):
    img = Image.new("RGB", (width, height), (128, 64, 32))
    return SatelliteTexture(
        image=img,
        min_lat=10.0,
        max_lat=11.0,
        min_lon=20.0,
        max_lon=21.0,
        provider_id="esri_world",
        quality="standard",
    )


def _make_zip_with_png(width=64, height=48) -> tuple[Path, str]:
    """Write a temp ZIP containing a PNG at 'satellite.png'; return (zip_path, entry)."""
    png_bytes = _make_png_bytes(width, height)
    tmp = tempfile.NamedTemporaryFile(suffix=".georeel", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w") as zf:
        zf.writestr("satellite.png", png_bytes)
    return Path(tmp.name), "satellite.png"


# ---------------------------------------------------------------------------
# has_pixels / memory_bytes
# ---------------------------------------------------------------------------


class TestHasPixels:
    def test_true_when_image_loaded(self):
        t = _make_texture()
        assert t.has_pixels() is True

    def test_false_when_image_none(self):
        t = SatelliteTexture(image=None, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        assert t.has_pixels() is False


class TestMemoryBytes:
    def test_returns_nonzero_for_loaded_image(self):
        t = _make_texture(100, 80)
        assert t.memory_bytes() == 100 * 80 * 3

    def test_returns_zero_when_no_image(self):
        t = SatelliteTexture(image=None, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        assert t.memory_bytes() == 0


# ---------------------------------------------------------------------------
# width / height with cached dims
# ---------------------------------------------------------------------------


class TestDimProperties:
    def test_width_from_dim_width_when_image_none(self):
        t = SatelliteTexture(
            image=None,
            min_lat=0,
            max_lat=1,
            min_lon=0,
            max_lon=1,
            _dim_width=300,
            _dim_height=200,
        )
        assert t.width == 300
        assert t.height == 200

    def test_width_raises_without_image_or_dim(self):
        t = SatelliteTexture(image=None, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        with pytest.raises(RuntimeError, match="dimensions not available"):
            t.width

    def test_height_raises_without_image_or_dim(self):
        t = SatelliteTexture(image=None, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        with pytest.raises(RuntimeError, match="dimensions not available"):
            t.height


# ---------------------------------------------------------------------------
# free_image
# ---------------------------------------------------------------------------


class TestFreeImage:
    def test_image_is_none_after_free(self):
        t = _make_texture()
        t.free_image()
        assert t.image is None

    def test_dims_cached_from_image_before_free(self):
        t = _make_texture(200, 150)
        t.free_image()
        assert t._dim_width == 200
        assert t._dim_height == 150

    def test_tile_cache_cleared_on_free(self):
        t = _make_texture()
        t._tile_cache = MagicMock()
        t.free_image()
        assert t._tile_cache is None

    def test_tiles_dir_set_on_free(self):
        t = _make_texture()
        tiles_dir = Path("/tmp/fake_tiles")
        manifest = {"image_width": 100, "image_height": 80, "tiles": []}
        t.free_image(tiles_dir=tiles_dir, tiles_manifest=manifest)
        assert t._tiles_dir == tiles_dir

    def test_manifest_dims_set_before_image_dims_override(self):
        # When both manifest and image are present, image dims win (they are
        # the authoritative dimensions at free time).
        t = _make_texture(100, 80)
        manifest = {"image_width": 400, "image_height": 300, "tiles": []}
        t.free_image(tiles_manifest=manifest)
        # Image dims (100×80) override the manifest values (400×300).
        assert t._dim_width == 100
        assert t._dim_height == 80
        assert t._tiles_manifest is manifest


# ---------------------------------------------------------------------------
# write_png — RAM path
# ---------------------------------------------------------------------------


class TestWritePngFromImage:
    def test_write_png_produces_valid_png(self):
        t = _make_texture(32, 24)
        buf = io.BytesIO()
        t.write_png(buf)
        buf.seek(0)
        img = Image.open(buf)
        assert img.size == (32, 24)

    def test_write_png_rgba_converted_to_rgb(self):
        img = Image.new("RGBA", (20, 20), (255, 0, 0, 128))
        t = SatelliteTexture(image=img, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        buf = io.BytesIO()
        t.write_png(buf)
        buf.seek(0)
        out = Image.open(buf)
        assert out.mode == "RGB"


# ---------------------------------------------------------------------------
# write_png — lazy ZIP path (streams raw bytes)
# ---------------------------------------------------------------------------


class TestWritePngFromZip:
    def test_streams_raw_bytes_without_decoding(self):
        zip_path, entry = _make_zip_with_png(64, 48)
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path, entry, min_lat=0, max_lat=1, min_lon=0, max_lon=1
            )
            buf = io.BytesIO()
            t.write_png(buf)
            buf.seek(0)
            img = Image.open(buf)
            assert img.size == (64, 48)
        finally:
            zip_path.unlink(missing_ok=True)

    def test_no_image_loaded_during_stream(self):
        zip_path, entry = _make_zip_with_png()
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path, entry, min_lat=0, max_lat=1, min_lon=0, max_lon=1
            )
            assert t.image is None
            buf = io.BytesIO()
            t.write_png(buf)
            # Image must still be None after streaming
            assert t.image is None
        finally:
            zip_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# write_png — tile cache path
# ---------------------------------------------------------------------------


class TestWritePngFromTileCache:
    def test_delegates_to_tile_cache_composite(self):
        t = SatelliteTexture(
            image=None,
            min_lat=47.0,
            max_lat=47.1,
            min_lon=8.0,
            max_lon=8.1,
        )
        fake_img = Image.new("RGB", (128, 96), (50, 100, 150))
        mock_cache = MagicMock()
        mock_cache.composite.return_value = fake_img
        t._tile_cache = mock_cache

        buf = io.BytesIO()
        t.write_png(buf)

        mock_cache.composite.assert_called_once()
        buf.seek(0)
        out = Image.open(buf)
        assert out.size == (128, 96)

    def test_tile_cache_composite_rgba_converted(self):
        t = SatelliteTexture(
            image=None,
            min_lat=0,
            max_lat=1,
            min_lon=0,
            max_lon=1,
        )
        fake_img = Image.new("RGBA", (10, 10), (255, 0, 0, 200))
        mock_cache = MagicMock()
        mock_cache.composite.return_value = fake_img
        t._tile_cache = mock_cache

        buf = io.BytesIO()
        t.write_png(buf)
        buf.seek(0)
        out = Image.open(buf)
        assert out.mode == "RGB"


# ---------------------------------------------------------------------------
# write_png — tiles manifest path (reassembly from Blender tile PNGs)
# ---------------------------------------------------------------------------


class TestWritePngFromManifest:
    def test_reassembles_from_tiles(self, tmp_path):
        # Write two small tile PNGs.
        tile_w, tile_h = 32, 32
        tile_a_path = tmp_path / "tile_0_0.png"
        tile_b_path = tmp_path / "tile_1_0.png"
        Image.new("RGB", (tile_w, tile_h), (255, 0, 0)).save(tile_a_path)
        Image.new("RGB", (tile_w, tile_h), (0, 255, 0)).save(tile_b_path)

        manifest = {
            "image_width": 64,
            "image_height": 32,
            "tiles": [
                {"path": str(tile_a_path), "px_left": 0, "px_top": 0},
                {"path": str(tile_b_path), "px_left": 32, "px_top": 0},
            ],
        }

        t = SatelliteTexture(image=None, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        t._tiles_dir = tmp_path
        t._tiles_manifest = manifest
        t._dim_width = 64
        t._dim_height = 32

        buf = io.BytesIO()
        t.write_png(buf)
        buf.seek(0)
        out = Image.open(buf)
        assert out.size == (64, 32)
        # Left half should be red, right half green.
        assert out.getpixel((16, 16)) == (255, 0, 0)
        assert out.getpixel((48, 16)) == (0, 255, 0)

    def test_raises_when_no_backing(self):
        t = SatelliteTexture(image=None, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        buf = io.BytesIO()
        with pytest.raises(RuntimeError, match="Cannot serialise"):
            t.write_png(buf)


# ---------------------------------------------------------------------------
# from_zip_lazy
# ---------------------------------------------------------------------------


class TestFromZipLazy:
    def test_no_image_in_ram(self):
        zip_path, entry = _make_zip_with_png()
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path, entry, min_lat=0, max_lat=1, min_lon=0, max_lon=1
            )
            assert t.image is None
        finally:
            zip_path.unlink(missing_ok=True)

    def test_dimensions_peeked_from_ihdr(self):
        zip_path, entry = _make_zip_with_png(width=64, height=48)
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path, entry, min_lat=0, max_lat=1, min_lon=0, max_lon=1
            )
            assert t.width == 64
            assert t.height == 48
        finally:
            zip_path.unlink(missing_ok=True)

    def test_source_zip_and_entry_set(self):
        zip_path, entry = _make_zip_with_png()
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path, entry, min_lat=0, max_lat=1, min_lon=0, max_lon=1
            )
            assert t._source_zip == zip_path
            assert t._source_entry == entry
        finally:
            zip_path.unlink(missing_ok=True)

    def test_metadata_preserved(self):
        zip_path, entry = _make_zip_with_png()
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path,
                entry,
                min_lat=10.0,
                max_lat=11.0,
                min_lon=20.0,
                max_lon=21.0,
                provider_id="esri_world",
                quality="high",
            )
            assert t.min_lat == 10.0
            assert t.provider_id == "esri_world"
            assert t.quality == "high"
        finally:
            zip_path.unlink(missing_ok=True)

    def test_corrupt_zip_dims_unavailable(self, tmp_path):
        """A corrupt ZIP entry must not raise; dims remain None."""
        bad_zip = tmp_path / "bad.georeel"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("satellite.png", b"\x00\x01\x02bad not a png")
        t = SatelliteTexture.from_zip_lazy(
            bad_zip, "satellite.png", min_lat=0, max_lat=1, min_lon=0, max_lon=1
        )
        assert t._dim_width is None
        assert t._dim_height is None


# ---------------------------------------------------------------------------
# load_image
# ---------------------------------------------------------------------------


class TestLoadImage:
    def test_returns_cached_image_if_present(self):
        t = _make_texture()
        original = t.image
        img = t.load_image()
        assert img is original

    def test_loads_from_zip(self):
        zip_path, entry = _make_zip_with_png(width=64, height=48)
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path, entry, min_lat=0, max_lat=1, min_lon=0, max_lon=1
            )
            img = t.load_image()
            assert img.size == (64, 48)
            assert img.mode == "RGB"
            # Cached on the object now.
            assert t.image is img
        finally:
            zip_path.unlink(missing_ok=True)

    def test_load_image_caches_dims(self):
        zip_path, entry = _make_zip_with_png(width=64, height=48)
        try:
            t = SatelliteTexture.from_zip_lazy(
                zip_path, entry, min_lat=0, max_lat=1, min_lon=0, max_lon=1
            )
            t.load_image()
            assert t._dim_width == 64
            assert t._dim_height == 48
        finally:
            zip_path.unlink(missing_ok=True)

    def test_raises_without_source(self):
        t = SatelliteTexture(image=None, min_lat=0, max_lat=1, min_lon=0, max_lon=1)
        with pytest.raises(RuntimeError, match="no source ZIP"):
            t.load_image()


# ---------------------------------------------------------------------------
# from_png_stream — RGBA conversion
# ---------------------------------------------------------------------------


class TestFromPngStream:
    def test_rgba_converted_to_rgb(self):
        buf = io.BytesIO()
        Image.new("RGBA", (10, 10), (255, 0, 0, 128)).save(buf, format="PNG")
        buf.seek(0)
        t = SatelliteTexture.from_png_stream(buf, 0.0, 1.0, 0.0, 1.0)
        assert t.image.mode == "RGB"
