"""Tests for remaining video_assembler helper functions."""

import json
import pytest
from pathlib import Path
from PIL import Image

from georeel.core.video_assembler import (
    _attach_args,
    _attach_settings_args,
    _write_settings,
    _copy_gpx_alongside,
    _composite_title_frames,
)


# ── _attach_args ──────────────────────────────────────────────────

class TestAttachArgs:
    def test_none_gpx_returns_empty(self):
        assert _attach_args(None, "mkv") == []

    def test_nonexistent_file_returns_empty(self):
        assert _attach_args("/nonexistent/track.gpx", "mkv") == []

    def test_unsupported_container_returns_empty(self, tmp_path):
        gpx = tmp_path / "track.gpx"
        gpx.write_text("<gpx/>")
        assert _attach_args(str(gpx), "avi") == []

    def test_mkv_returns_attach_args(self, tmp_path):
        gpx = tmp_path / "track.gpx"
        gpx.write_text("<gpx/>")
        args = _attach_args(str(gpx), "mkv")
        assert "-attach" in args
        assert str(gpx) in args
        assert "mimetype=application/gpx+xml" in " ".join(args)

    def test_mp4_returns_attach_args(self, tmp_path):
        gpx = tmp_path / "track.gpx"
        gpx.write_text("<gpx/>")
        args = _attach_args(str(gpx), "mp4")
        assert "-attach" in args

    def test_filename_metadata_present(self, tmp_path):
        gpx = tmp_path / "my_track.gpx"
        gpx.write_text("<gpx/>")
        args = _attach_args(str(gpx), "mkv")
        combined = " ".join(args)
        assert "my_track.gpx" in combined


# ── _attach_settings_args ─────────────────────────────────────────

class TestAttachSettingsArgs:
    def test_mkv_returns_args(self, tmp_path):
        settings_file = str(tmp_path / "s.json")
        args = _attach_settings_args(settings_file, "mkv")
        assert "-attach" in args
        assert settings_file in args

    def test_mp4_returns_empty(self, tmp_path):
        settings_file = str(tmp_path / "s.json")
        args = _attach_settings_args(settings_file, "mp4")
        assert args == []

    def test_unknown_container_returns_empty(self, tmp_path):
        args = _attach_settings_args("/tmp/s.json", "webm")
        assert args == []

    def test_mimetype_present(self, tmp_path):
        args = _attach_settings_args("/tmp/s.json", "mkv")
        assert "mimetype=application/json" in " ".join(args)


# ── _write_settings ───────────────────────────────────────────────

class TestWriteSettings:
    def test_non_mkv_writes_json_file(self, tmp_path):
        out = tmp_path / "output.mp4"
        out.write_bytes(b"fake")
        settings = {"render/fps": 30, "output/encoder": "libx264"}
        _write_settings(settings, out, "mp4")
        json_file = tmp_path / "output_settings.json"
        assert json_file.exists()
        parsed = json.loads(json_file.read_text())
        assert parsed["render/fps"] == 30

    def test_mkv_does_not_write_file(self, tmp_path):
        out = tmp_path / "output.mkv"
        out.write_bytes(b"fake")
        _write_settings({"render/fps": 30}, out, "mkv")
        assert not (tmp_path / "output_settings.json").exists()

    def test_excludes_api_key(self, tmp_path):
        out = tmp_path / "output.mp4"
        out.write_bytes(b"fake")
        _write_settings({"imagery/api_key": "secret", "x": 1}, out, "mp4")
        parsed = json.loads((tmp_path / "output_settings.json").read_text())
        assert "imagery/api_key" not in parsed


# ── _copy_gpx_alongside ───────────────────────────────────────────

class TestCopyGpxAlongside:
    def test_none_gpx_is_noop(self, tmp_path):
        out = tmp_path / "video.mp4"
        _copy_gpx_alongside(None, out, "mp4")  # should not raise

    def test_nonexistent_gpx_is_noop(self, tmp_path):
        out = tmp_path / "video.mp4"
        _copy_gpx_alongside("/nonexistent.gpx", out, "mp4")  # should not raise

    def test_mkv_does_not_copy(self, tmp_path):
        gpx = tmp_path / "track.gpx"
        gpx.write_text("<gpx/>")
        out = tmp_path / "video.mkv"
        _copy_gpx_alongside(str(gpx), out, "mkv")
        assert not (tmp_path / "video.gpx").exists()

    def test_mp4_does_not_copy_because_it_is_in_attachment_containers(self, tmp_path):
        gpx = tmp_path / "track.gpx"
        gpx.write_text("<gpx/>")
        out = tmp_path / "video.mp4"
        _copy_gpx_alongside(str(gpx), out, "mp4")
        assert not (tmp_path / "video.gpx").exists()

    def test_non_attachment_container_copies(self, tmp_path):
        gpx = tmp_path / "track.gpx"
        gpx.write_text("<gpx content/>")
        out = tmp_path / "video.avi"
        _copy_gpx_alongside(str(gpx), out, "avi")
        dest = tmp_path / "video.gpx"
        assert dest.exists()
        assert dest.read_text() == "<gpx content/>"


# ── _composite_title_frames ───────────────────────────────────────

def _write_test_frames(path: Path, count: int, size=(320, 240)):
    path.mkdir(exist_ok=True)
    for i in range(count):
        Image.new("RGB", size, (100, 150, 200)).save(path / f"{i:06d}.png")


class TestCompositeTitleFrames:
    def test_no_text_hard_links_frames(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_test_frames(src, count=5)
        settings = {"clip_effects/title_text": ""}
        _composite_title_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 5

    def test_with_text_produces_same_frame_count(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_test_frames(src, count=10)
        settings = {
            "clip_effects/title_text": "Test Title",
            "clip_effects/title_font": "DejaVu Sans",
            "clip_effects/title_font_size": 24,
            "clip_effects/title_anchor": "bottom-right",
            "clip_effects/title_margin": 10,
            "clip_effects/title_alignment": "right",
            "clip_effects/title_color": "#ffffff",
            "clip_effects/title_shadow": False,
            "clip_effects/title_duration": 1.0,
            "clip_effects/title_fade_in_enabled": False,
            "clip_effects/title_fade_in_dur": 0.0,
            "clip_effects/title_fade_out_enabled": False,
            "clip_effects/title_fade_out_dur": 0.0,
        }
        _composite_title_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 10

    def test_output_frames_are_valid_images(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_test_frames(src, count=3, size=(320, 240))
        _composite_title_frames(str(src), dst, {"clip_effects/title_text": ""}, fps=30)
        for f in sorted(dst.glob("*.png")):
            img = Image.open(f)
            assert img.size == (320, 240)

    def test_empty_source_dir_no_error(self, tmp_path):
        src = tmp_path / "empty_src"
        src.mkdir()
        dst = tmp_path / "dst"
        dst.mkdir()
        # No frames in source — should not raise
        _composite_title_frames(str(src), dst, {"clip_effects/title_text": "Hi"}, fps=30)
        assert len(list(dst.glob("*.png"))) == 0
