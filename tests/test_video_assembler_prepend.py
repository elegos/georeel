"""Tests for video_assembler._prepend_black_frames and _resolve_fontfile."""

import os
import pytest
from pathlib import Path
from PIL import Image
from unittest.mock import patch, MagicMock

from georeel.core.video_assembler import _prepend_black_frames, _resolve_fontfile


def _write_frames(tmp_path: Path, count: int, size=(320, 240), color=(128, 64, 32)) -> Path:
    """Write *count* numbered PNG frames to *tmp_path*."""
    src = tmp_path / "src"
    src.mkdir()
    for i in range(count):
        Image.new("RGB", size, color).save(src / f"{i:06d}.png")
    return src


class TestPrependBlackFrames:
    def test_output_frame_count(self, tmp_path):
        src = _write_frames(tmp_path, count=5)
        dst = tmp_path / "dst"
        dst.mkdir()
        _prepend_black_frames(str(src), dst, n_black=3)
        frames = sorted(dst.glob("*.png"))
        assert len(frames) == 8  # 3 black + 5 original

    def test_black_frames_are_black(self, tmp_path):
        src = _write_frames(tmp_path, count=2)
        dst = tmp_path / "dst"
        dst.mkdir()
        _prepend_black_frames(str(src), dst, n_black=2)
        # First two frames should be black
        for i in range(2):
            img = Image.open(dst / f"{i:06d}.png").convert("RGB")
            r, g, b = img.getpixel((0, 0))
            assert r == 0 and g == 0 and b == 0

    def test_original_frames_follow_black(self, tmp_path):
        color = (200, 100, 50)
        src = _write_frames(tmp_path, count=3, color=color)
        dst = tmp_path / "dst"
        dst.mkdir()
        _prepend_black_frames(str(src), dst, n_black=2)
        # Frame index 2 should be the first original frame
        img = Image.open(dst / "000002.png").convert("RGB")
        r, g, b = img.getpixel((0, 0))
        assert r == color[0] and g == color[1] and b == color[2]

    def test_output_dimensions_match_source(self, tmp_path):
        src = _write_frames(tmp_path, count=2, size=(640, 480))
        dst = tmp_path / "dst"
        dst.mkdir()
        _prepend_black_frames(str(src), dst, n_black=1)
        img = Image.open(dst / "000000.png")
        assert img.size == (640, 480)

    def test_zero_black_frames(self, tmp_path):
        src = _write_frames(tmp_path, count=4)
        dst = tmp_path / "dst"
        dst.mkdir()
        _prepend_black_frames(str(src), dst, n_black=0)
        frames = sorted(dst.glob("*.png"))
        assert len(frames) == 4

    def test_empty_source_dir_uses_1x1_black(self, tmp_path):
        src = tmp_path / "empty"
        src.mkdir()
        dst = tmp_path / "dst"
        dst.mkdir()
        _prepend_black_frames(str(src), dst, n_black=2)
        frames = sorted(dst.glob("*.png"))
        assert len(frames) == 2
        img = Image.open(frames[0])
        assert img.size == (1, 1)

    def test_frames_sequentially_numbered(self, tmp_path):
        src = _write_frames(tmp_path, count=3)
        dst = tmp_path / "dst"
        dst.mkdir()
        _prepend_black_frames(str(src), dst, n_black=2)
        names = sorted(f.name for f in dst.glob("*.png"))
        expected = ["000000.png", "000001.png", "000002.png", "000003.png", "000004.png"]
        assert names == expected


class TestResolveFontfile:
    def test_returns_none_when_fc_match_fails(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("fc-match not found")):
            result = _resolve_fontfile("Noto Serif")
        assert result is None

    def test_returns_none_when_path_not_a_file(self):
        mock_result = MagicMock()
        mock_result.stdout = "/nonexistent/path/font.ttf"
        with patch("subprocess.run", return_value=mock_result):
            result = _resolve_fontfile("Noto Serif")
        assert result is None

    def test_returns_path_when_file_exists(self, tmp_path):
        font_file = tmp_path / "test.ttf"
        font_file.write_bytes(b"\x00" * 100)
        mock_result = MagicMock()
        mock_result.stdout = str(font_file)
        with patch("subprocess.run", return_value=mock_result):
            result = _resolve_fontfile("Noto Serif")
        assert result == str(font_file)
