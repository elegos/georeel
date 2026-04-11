"""Tests for assemble_video early error paths and _composite_title_frames anchors."""

import pytest
from pathlib import Path
from PIL import Image
from unittest.mock import patch

from georeel.core.video_assembler import (
    VideoAssembleError,
    assemble_video,
    _composite_title_frames,
)


def _write_frames(path: Path, count: int, size=(80, 60)):
    path.mkdir(exist_ok=True)
    for i in range(count):
        Image.new("RGB", size, (100, 150, 200)).save(path / f"{i:06d}.png")


# ── assemble_video error paths ─────────────────────────────────────────

class TestAssembleVideoErrors:
    def test_ffmpeg_not_found_raises(self, tmp_path):
        with patch("shutil.which", return_value=None):
            with pytest.raises(VideoAssembleError, match="[Ff][Ff]mpeg"):
                assemble_video(
                    frames_dir=str(tmp_path),
                    output_path=str(tmp_path / "out.mkv"),
                    settings={},
                    total_frames=10,
                )

    def test_unknown_encoder_raises(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with pytest.raises(VideoAssembleError, match="encoder"):
                assemble_video(
                    frames_dir=str(tmp_path),
                    output_path=str(tmp_path / "out.mkv"),
                    settings={"output/encoder": "nonexistent_encoder_xyz"},
                    total_frames=10,
                )


# ── _composite_title_frames anchor / shadow / color coverage ──────────

class TestCompositeTitleAnchors:
    """Exercise the h_part/v_part branches so all positioning code is covered."""

    _SETTINGS_BASE = {
        "clip_effects/title_color": "#ffffff",
        "clip_effects/title_shadow": False,
        "clip_effects/title_duration": 999.0,
        "clip_effects/title_fade_in_enabled": False,
        "clip_effects/title_fade_in_dur": 0.0,
        "clip_effects/title_fade_out_enabled": False,
        "clip_effects/title_fade_out_dur": 0.0,
        "clip_effects/title_font_size": 12,
        "clip_effects/title_margin": 4,
        "clip_effects/title_font": "DejaVu Sans",
        "clip_effects/title_alignment": "left",
    }

    def _settings(self, anchor="top-left", **overrides):
        s = dict(self._SETTINGS_BASE)
        s["clip_effects/title_anchor"] = anchor
        s["clip_effects/title_text"] = "Hello"
        s.update(overrides)
        return s

    def _run(self, tmp_path, anchor, **extra):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_frames(src, count=3)
        _composite_title_frames(str(src), dst, self._settings(anchor, **extra), fps=30)
        return dst

    def test_top_left_anchor(self, tmp_path):
        dst = self._run(tmp_path, "top-left")
        assert len(list(dst.glob("*.png"))) == 3

    def test_top_right_anchor(self, tmp_path):
        dst = self._run(tmp_path, "top-right")
        assert len(list(dst.glob("*.png"))) == 3

    def test_bottom_left_anchor(self, tmp_path):
        dst = self._run(tmp_path, "bottom-left")
        assert len(list(dst.glob("*.png"))) == 3

    def test_bottom_right_anchor(self, tmp_path):
        dst = self._run(tmp_path, "bottom-right")
        assert len(list(dst.glob("*.png"))) == 3

    def test_center_anchor(self, tmp_path):
        dst = self._run(tmp_path, "center")
        assert len(list(dst.glob("*.png"))) == 3

    def test_shadow_enabled(self, tmp_path):
        dst = self._run(tmp_path, "top-left",
                        **{"clip_effects/title_shadow": True})
        assert len(list(dst.glob("*.png"))) == 3

    def test_invalid_color_fallback(self, tmp_path):
        """Invalid color string falls back to white — should not raise."""
        dst = self._run(tmp_path, "top-left",
                        **{"clip_effects/title_color": "not-a-color"})
        assert len(list(dst.glob("*.png"))) == 3

    def test_right_alignment(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_frames(src, count=2)
        settings = self._settings("center", **{"clip_effects/title_alignment": "right"})
        _composite_title_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 2

    def test_fade_in_partial(self, tmp_path):
        """Title fade-in partway through → generates composited frames."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_frames(src, count=30)
        settings = self._settings("top-left")
        settings["clip_effects/title_duration"] = 1.0  # 30 frames
        settings["clip_effects/title_fade_in_enabled"] = True
        settings["clip_effects/title_fade_in_dur"] = 0.5
        _composite_title_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 30
