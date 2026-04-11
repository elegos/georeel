"""Tests for photo_compositor._process_frame_task worker function."""

import shutil
import pytest
from pathlib import Path
from PIL import Image

import georeel.core.photo_compositor as pc
from georeel.core.photo_compositor import _process_frame_task


def _write_rgb_png(path: Path, color=(100, 150, 200), size=(64, 64)):
    img = Image.new("RGB", size, color)
    img.save(str(path))
    return img


def _make_task(
    frame_num: int,
    src_path: str,
    out_path: str,
    op: str = "copy",
    photo_key: str | None = None,
    next_photo_key: str | None = None,
    alpha: float = 0.0,
    out_w: int = 64,
    out_h: int = 64,
) -> dict:
    return {
        "frame_num": frame_num,
        "src_path": src_path,
        "out_path": out_path,
        "op": op,
        "photo_key": photo_key,
        "next_photo_key": next_photo_key,
        "alpha": alpha,
        "out_w": out_w,
        "out_h": out_h,
    }


@pytest.fixture(autouse=True)
def clear_worker_cache():
    """Reset _WORKER_CACHE before each test."""
    original = pc._WORKER_CACHE.copy()
    pc._WORKER_CACHE.clear()
    yield
    pc._WORKER_CACHE.clear()
    pc._WORKER_CACHE.update(original)


# ── "copy" op ─────────────────────────────────────────────────────────

class TestCopyOp:
    def test_copy_existing_src(self, tmp_path):
        src = tmp_path / "000001.png"
        out = tmp_path / "out.png"
        _write_rgb_png(src)
        task = _make_task(2, str(src), str(out), op="copy")
        result = _process_frame_task(task)
        assert result is None
        assert out.exists()

    def test_copy_missing_src_returns_frame_num(self, tmp_path):
        src = tmp_path / "missing.png"
        out = tmp_path / "out.png"
        task = _make_task(7, str(src), str(out), op="copy")
        result = _process_frame_task(task)
        assert result == 7

    def test_copy_produces_same_image(self, tmp_path):
        src = tmp_path / "src.png"
        out = tmp_path / "out.png"
        _write_rgb_png(src, color=(200, 100, 50))
        task = _make_task(1, str(src), str(out), op="copy")
        _process_frame_task(task)
        r, g, b = Image.open(out).getpixel((0, 0))
        assert r == 200 and g == 100 and b == 50


# ── "photo" op ────────────────────────────────────────────────────────

class TestPhotoOp:
    def test_photo_no_cache_falls_back_to_copy(self, tmp_path):
        src = tmp_path / "src.png"
        out = tmp_path / "out.png"
        _write_rgb_png(src, color=(0, 255, 0))
        task = _make_task(1, str(src), str(out), op="photo", photo_key="/missing.jpg")
        result = _process_frame_task(task)
        assert result is None
        # Should have copied the terrain frame
        assert out.exists()

    def test_photo_no_cache_missing_src_returns_frame_num(self, tmp_path):
        out = tmp_path / "out.png"
        task = _make_task(3, str(tmp_path / "ghost.png"), str(out),
                          op="photo", photo_key="/missing.jpg")
        result = _process_frame_task(task)
        assert result == 3

    def test_photo_with_cache_writes_photo(self, tmp_path):
        src = tmp_path / "terrain.png"
        out = tmp_path / "out.png"
        _write_rgb_png(src, color=(0, 0, 0))
        photo = Image.new("RGB", (64, 64), (255, 0, 0))
        pc._WORKER_CACHE["/photo.jpg"] = photo

        task = _make_task(1, str(src), str(out), op="photo", photo_key="/photo.jpg")
        result = _process_frame_task(task)
        assert result is None
        r, g, b = Image.open(out).getpixel((0, 0))
        assert r == 255 and g == 0 and b == 0

    def test_photo_none_photo_key_falls_back_to_copy(self, tmp_path):
        src = tmp_path / "src.png"
        out = tmp_path / "out.png"
        _write_rgb_png(src, color=(128, 64, 32))
        task = _make_task(1, str(src), str(out), op="photo", photo_key=None)
        _process_frame_task(task)
        r, g, b = Image.open(out).getpixel((0, 0))
        assert r == 128 and g == 64 and b == 32


# ── "crossfade" op ────────────────────────────────────────────────────

class TestCrossfadeOp:
    def test_crossfade_both_cached_blends(self, tmp_path):
        out = tmp_path / "out.png"
        photo_a = Image.new("RGB", (64, 64), (0, 0, 0))
        photo_b = Image.new("RGB", (64, 64), (200, 200, 200))
        pc._WORKER_CACHE["/a.jpg"] = photo_a
        pc._WORKER_CACHE["/b.jpg"] = photo_b

        task = _make_task(1, str(tmp_path / "t.png"), str(out),
                          op="crossfade", photo_key="/a.jpg",
                          next_photo_key="/b.jpg", alpha=0.5)
        result = _process_frame_task(task)
        assert result is None
        assert out.exists()
        # Blend(0, 200, 0.5) = 100
        r, g, b = Image.open(out).getpixel((0, 0))
        assert 90 <= r <= 110

    def test_crossfade_missing_next_writes_current_photo(self, tmp_path):
        out = tmp_path / "out.png"
        photo_a = Image.new("RGB", (64, 64), (255, 0, 0))
        pc._WORKER_CACHE["/a.jpg"] = photo_a

        task = _make_task(1, str(tmp_path / "t.png"), str(out),
                          op="crossfade", photo_key="/a.jpg",
                          next_photo_key=None, alpha=0.5)
        result = _process_frame_task(task)
        assert result is None
        r, g, b = Image.open(out).getpixel((0, 0))
        assert r == 255

    def test_crossfade_no_photo_cache_returns_frame_num_if_no_src(self, tmp_path):
        out = tmp_path / "out.png"
        task = _make_task(5, str(tmp_path / "ghost.png"), str(out),
                          op="crossfade", photo_key="/missing.jpg",
                          next_photo_key="/also.jpg", alpha=0.5)
        result = _process_frame_task(task)
        assert result == 5


# ── "fade_in" / "fade_out" ops ────────────────────────────────────────

class TestFadeInOutOps:
    def test_fade_in_with_terrain_blends(self, tmp_path):
        src = tmp_path / "terrain.png"
        out = tmp_path / "out.png"
        _write_rgb_png(src, color=(0, 0, 0))
        photo = Image.new("RGB", (64, 64), (200, 200, 200))
        pc._WORKER_CACHE["/p.jpg"] = photo

        task = _make_task(1, str(src), str(out), op="fade_in",
                          photo_key="/p.jpg", alpha=0.5, out_w=64, out_h=64)
        result = _process_frame_task(task)
        assert result is None
        assert out.exists()

    def test_fade_out_with_terrain_blends(self, tmp_path):
        src = tmp_path / "terrain.png"
        out = tmp_path / "out.png"
        _write_rgb_png(src, color=(0, 0, 0))
        photo = Image.new("RGB", (64, 64), (200, 200, 200))
        pc._WORKER_CACHE["/p.jpg"] = photo

        task = _make_task(1, str(src), str(out), op="fade_out",
                          photo_key="/p.jpg", alpha=0.3, out_w=64, out_h=64)
        result = _process_frame_task(task)
        assert result is None
        assert out.exists()

    def test_fade_in_missing_src_writes_photo_and_returns_frame_num(self, tmp_path):
        out = tmp_path / "out.png"
        photo = Image.new("RGB", (64, 64), (255, 0, 0))
        pc._WORKER_CACHE["/p.jpg"] = photo

        task = _make_task(9, str(tmp_path / "ghost.png"), str(out),
                          op="fade_in", photo_key="/p.jpg", alpha=0.5,
                          out_w=64, out_h=64)
        result = _process_frame_task(task)
        assert result == 9
        # Should still write the photo
        assert out.exists()

    def test_fade_terrain_resize_if_needed(self, tmp_path):
        """Terrain frame is resized to out_w×out_h when sizes differ."""
        src = tmp_path / "terrain.png"
        out = tmp_path / "out.png"
        # Write a 32×32 terrain, request 64×64 output
        Image.new("RGB", (32, 32), (0, 0, 0)).save(str(src))
        photo = Image.new("RGB", (64, 64), (200, 200, 200))
        pc._WORKER_CACHE["/p.jpg"] = photo

        task = _make_task(1, str(src), str(out), op="fade_in",
                          photo_key="/p.jpg", alpha=0.5, out_w=64, out_h=64)
        _process_frame_task(task)
        img = Image.open(out)
        assert img.size == (64, 64)
