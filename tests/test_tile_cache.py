"""Tests for satellite.tile_cache — TileCache and coordinate helpers."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from georeel.core.bounding_box import BoundingBox
from georeel.core.satellite.tile_cache import (
    TileCache,
    _crop_bounds,
    lat_to_y,
    lon_to_x,
    tile_nw,
    _session,
    _thread_local,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SMALL_BBOX = BoundingBox(min_lat=47.0, max_lat=47.1, min_lon=8.0, max_lon=8.1)
_ZOOM = 10
_URL = "https://example.com/{z}/{x}/{y}.img"


def _make_png_bytes(color=(0, 128, 255), size=(256, 256)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_cache(on_demand: bool = False) -> TileCache:
    return TileCache(url_template=_URL, zoom=_ZOOM, on_demand=on_demand)


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

class TestCropBounds:
    def test_returns_eight_values(self):
        result = _crop_bounds(_SMALL_BBOX, _ZOOM)
        assert len(result) == 8

    def test_canvas_size_positive(self):
        *_, canvas_w, canvas_h = _crop_bounds(_SMALL_BBOX, _ZOOM)
        assert canvas_w >= 1
        assert canvas_h >= 1

    def test_x_min_le_x_max(self):
        x_min, x_max, *_ = _crop_bounds(_SMALL_BBOX, _ZOOM)
        assert x_min <= x_max

    def test_y_min_le_y_max(self):
        _, _, y_min, y_max, *_ = _crop_bounds(_SMALL_BBOX, _ZOOM)
        assert y_min <= y_max

    def test_crop_left_non_negative(self):
        *_, crop_left, crop_top, canvas_w, canvas_h = _crop_bounds(_SMALL_BBOX, _ZOOM)
        # unpack properly
        x_min, x_max, y_min, y_max, crop_left, crop_top, canvas_w, canvas_h = (
            _crop_bounds(_SMALL_BBOX, _ZOOM)
        )
        assert crop_left >= 0
        assert crop_top >= 0


class TestSessionHelper:
    def test_returns_same_session_in_same_thread(self):
        # Clear any existing session on this thread.
        if hasattr(_thread_local, "session"):
            del _thread_local.session
        s1 = _session("TestAgent/1.0")
        s2 = _session("TestAgent/1.0")
        assert s1 is s2

    def test_session_has_user_agent(self):
        if hasattr(_thread_local, "session"):
            del _thread_local.session
        s = _session("MyUA/2.0")
        assert s.headers["User-Agent"] == "MyUA/2.0"


# ---------------------------------------------------------------------------
# TileCache construction
# ---------------------------------------------------------------------------

class TestTileCacheInit:
    def test_creates_temp_dir(self):
        cache = _make_cache()
        assert cache.dir.exists()
        cache.cleanup()

    def test_zoom_property(self):
        cache = _make_cache()
        assert cache.zoom == _ZOOM
        cache.cleanup()

    def test_dir_property_is_path(self):
        cache = _make_cache()
        assert isinstance(cache.dir, Path)
        cache.cleanup()

    def test_on_demand_default_false(self):
        cache = _make_cache()
        assert cache._on_demand is False
        cache.cleanup()

    def test_on_demand_true(self):
        cache = _make_cache(on_demand=True)
        assert cache._on_demand is True
        cache.cleanup()

    def test_failed_set_empty_initially(self):
        cache = _make_cache()
        assert len(cache._failed) == 0
        cache.cleanup()

    def test_tile_path_naming(self):
        cache = _make_cache()
        p = cache._tile_path(5, 7)
        assert p.name == "5_7.img"
        assert p.parent == cache.dir
        cache.cleanup()


# ---------------------------------------------------------------------------
# TileCache cleanup
# ---------------------------------------------------------------------------

class TestTileCacheCleanup:
    def test_cleanup_removes_dir(self):
        cache = _make_cache()
        d = cache.dir
        assert d.exists()
        cache.cleanup()
        assert not d.exists()

    def test_cleanup_idempotent(self):
        cache = _make_cache()
        cache.cleanup()
        # Second call must not raise
        cache.cleanup()


# ---------------------------------------------------------------------------
# _download_tile
# ---------------------------------------------------------------------------

class TestDownloadTile:
    def test_skips_already_failed(self):
        cache = _make_cache()
        cache._failed.add((1, 2))
        # Even if session raises, we should silently skip without touching disk
        cache._download_tile(1, 2)
        assert not cache._tile_path(1, 2).exists()
        cache.cleanup()

    def test_skips_if_file_already_exists(self):
        cache = _make_cache()
        path = cache._tile_path(3, 4)
        path.write_bytes(b"existing")
        # Should not make any HTTP call
        with patch("georeel.core.satellite.tile_cache._session") as mock_s:
            cache._download_tile(3, 4)
            mock_s.assert_not_called()
        assert path.read_bytes() == b"existing"
        cache.cleanup()

    def test_writes_response_bytes_on_success(self):
        cache = _make_cache()
        fake_bytes = _make_png_bytes()
        mock_resp = MagicMock()
        mock_resp.content = fake_bytes
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("georeel.core.satellite.tile_cache._session", return_value=mock_session):
            cache._download_tile(5, 6)
        assert cache._tile_path(5, 6).read_bytes() == fake_bytes
        assert (5, 6) not in cache._failed
        cache.cleanup()

    def test_marks_failed_on_http_error(self):
        cache = _make_cache()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("404 Not Found")
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        with patch("georeel.core.satellite.tile_cache._session", return_value=mock_session):
            cache._download_tile(7, 8)
        assert (7, 8) in cache._failed
        assert not cache._tile_path(7, 8).exists()
        cache.cleanup()

    def test_marks_failed_on_connection_error(self):
        cache = _make_cache()
        mock_session = MagicMock()
        mock_session.get.side_effect = ConnectionError("timeout")
        with patch("georeel.core.satellite.tile_cache._session", return_value=mock_session):
            cache._download_tile(9, 10)
        assert (9, 10) in cache._failed
        cache.cleanup()


# ---------------------------------------------------------------------------
# prefetch
# ---------------------------------------------------------------------------

class TestPrefetch:
    def test_on_demand_prefetch_is_noop(self):
        cache = _make_cache(on_demand=True)
        with patch.object(cache, "_download_tile") as mock_dl:
            cache.prefetch(0, 1, 0, 1)
            mock_dl.assert_not_called()
        cache.cleanup()

    def test_prefetch_calls_download_for_each_tile(self):
        cache = _make_cache()
        downloaded: list[tuple[int, int]] = []

        def fake_download(tx, ty):
            downloaded.append((tx, ty))

        with patch.object(cache, "_download_tile", side_effect=fake_download):
            cache.prefetch(x_min=0, x_max=1, y_min=0, y_max=1)

        # 2×2 = 4 tiles
        assert len(downloaded) == 4
        assert set(downloaded) == {(0, 0), (1, 0), (0, 1), (1, 1)}
        cache.cleanup()

    def test_prefetch_calls_progress_callback(self):
        cache = _make_cache()
        calls: list[tuple[int, int]] = []

        def fake_download(tx, ty):
            pass

        with patch.object(cache, "_download_tile", side_effect=fake_download):
            cache.prefetch(0, 0, 0, 0, progress_callback=lambda c, t: calls.append((c, t)))

        assert len(calls) == 1
        assert calls[0] == (1, 1)  # completed=1, total=1
        cache.cleanup()

    def test_prefetch_no_callback_does_not_raise(self):
        cache = _make_cache()
        with patch.object(cache, "_download_tile"):
            cache.prefetch(0, 0, 0, 0, progress_callback=None)
        cache.cleanup()


# ---------------------------------------------------------------------------
# canvas_size
# ---------------------------------------------------------------------------

class TestCanvasSize:
    def test_returns_positive_dimensions(self):
        cache = _make_cache()
        w, h = cache.canvas_size(_SMALL_BBOX)
        assert w >= 1
        assert h >= 1
        cache.cleanup()

    def test_larger_bbox_gives_larger_canvas(self):
        cache = _make_cache()
        small = BoundingBox(47.0, 47.01, 8.0, 8.01)
        large = BoundingBox(47.0, 47.5, 8.0, 8.5)
        sw, sh = cache.canvas_size(small)
        lw, lh = cache.canvas_size(large)
        assert lw >= sw
        assert lh >= sh
        cache.cleanup()


# ---------------------------------------------------------------------------
# composite — prefetch mode (files already on disk)
# ---------------------------------------------------------------------------

class TestComposite:
    def _write_tiles(self, cache: TileCache, bbox: BoundingBox) -> None:
        """Write solid-color PNG tiles for every tile that overlaps bbox."""
        from georeel.core.satellite.tile_cache import _crop_bounds
        bounds = _crop_bounds(bbox, cache.zoom)
        x_min, x_max, y_min, y_max = bounds[0], bounds[1], bounds[2], bounds[3]
        tile_bytes = _make_png_bytes(color=(200, 100, 50))
        for ty in range(y_min, y_max + 1):
            for tx in range(x_min, x_max + 1):
                cache._tile_path(tx, ty).write_bytes(tile_bytes)

    def test_returns_pil_image(self):
        cache = _make_cache()
        self._write_tiles(cache, _SMALL_BBOX)
        img = cache.composite(_SMALL_BBOX)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        cache.cleanup()

    def test_image_size_matches_canvas_size(self):
        cache = _make_cache()
        self._write_tiles(cache, _SMALL_BBOX)
        w, h = cache.canvas_size(_SMALL_BBOX)
        img = cache.composite(_SMALL_BBOX)
        assert img.size == (w, h)
        cache.cleanup()

    def test_missing_tiles_produce_black_pixels(self):
        cache = _make_cache()
        # Don't write any tiles — canvas should be all black.
        img = cache.composite(_SMALL_BBOX)
        extrema = img.getextrema()  # ((r_min, r_max), (g_min, g_max), (b_min, b_max))
        assert all(lo == 0 and hi == 0 for lo, hi in extrema)
        cache.cleanup()

    def test_corrupt_tile_file_skipped(self):
        cache = _make_cache()
        from georeel.core.satellite.tile_cache import _crop_bounds
        bounds = _crop_bounds(_SMALL_BBOX, cache.zoom)
        x_min, y_min = bounds[0], bounds[2]
        # Write garbage that PIL cannot decode.
        cache._tile_path(x_min, y_min).write_bytes(b"\x00\x01\x02\x03")
        # Should not raise; corrupt tile is skipped.
        img = cache.composite(_SMALL_BBOX)
        assert isinstance(img, Image.Image)
        cache.cleanup()


# ---------------------------------------------------------------------------
# composite — on-demand mode
# ---------------------------------------------------------------------------

class TestCompositeOnDemand:
    def test_on_demand_composite_downloads_needed_tiles(self):
        cache = _make_cache(on_demand=True)
        downloaded: list[tuple[int, int]] = []

        def fake_download(tx, ty):
            # Write a real tile so PIL can open it.
            cache._tile_path(tx, ty).write_bytes(_make_png_bytes())
            downloaded.append((tx, ty))

        with patch.object(cache, "_download_tile", side_effect=fake_download):
            img = cache.composite(_SMALL_BBOX)

        assert len(downloaded) > 0
        assert isinstance(img, Image.Image)
        cache.cleanup()

    def test_on_demand_skips_already_present_tiles(self):
        cache = _make_cache(on_demand=True)
        from georeel.core.satellite.tile_cache import _crop_bounds
        bounds = _crop_bounds(_SMALL_BBOX, cache.zoom)
        x_min, x_max, y_min, y_max = bounds[0], bounds[1], bounds[2], bounds[3]
        # Pre-write all tiles.
        for ty in range(y_min, y_max + 1):
            for tx in range(x_min, x_max + 1):
                cache._tile_path(tx, ty).write_bytes(_make_png_bytes())

        with patch.object(cache, "_download_tile") as mock_dl:
            cache.composite(_SMALL_BBOX)
            mock_dl.assert_not_called()

        cache.cleanup()

    def test_on_demand_skips_failed_tiles(self):
        cache = _make_cache(on_demand=True)
        from georeel.core.satellite.tile_cache import _crop_bounds
        bounds = _crop_bounds(_SMALL_BBOX, cache.zoom)
        x_min, x_max, y_min, y_max = bounds[0], bounds[1], bounds[2], bounds[3]
        # Mark every tile as failed.
        for ty in range(y_min, y_max + 1):
            for tx in range(x_min, x_max + 1):
                cache._failed.add((tx, ty))

        with patch.object(cache, "_download_tile") as mock_dl:
            cache.composite(_SMALL_BBOX)
            mock_dl.assert_not_called()

        cache.cleanup()
