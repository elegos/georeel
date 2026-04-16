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
    _composite_locality_frames,
    _locality_name_alpha,
    _resolve_overlay,
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


# ── _locality_name_alpha ──────────────────────────────────────────

class TestLocalityNameAlphaInAssembler:
    """Tests for _locality_name_alpha in video_assembler."""

    def test_before_start(self):
        assert _locality_name_alpha(-1, 30, 5) == 0.0

    def test_at_duration(self):
        assert _locality_name_alpha(30, 30, 5) == 0.0

    def test_full_opacity(self):
        assert _locality_name_alpha(15, 30, 5) == 1.0

    def test_fade_in_partial(self):
        result = _locality_name_alpha(2, 30, 5)
        assert result == pytest.approx(2 / 5)

    def test_fade_out_partial(self):
        result = _locality_name_alpha(27, 30, 5)
        assert result == pytest.approx(3 / 5)

    def test_no_fade(self):
        assert _locality_name_alpha(0, 30, 0) == 1.0


# ── _composite_locality_frames ────────────────────────────────────

def _write_locality_frames(path: Path, count: int, size: tuple[int, int] = (320, 240)) -> None:
    path.mkdir(exist_ok=True)
    for i in range(count):
        Image.new("RGB", size, (80, 120, 160)).save(path / f"{i:06d}.png")


class TestCompositeLocalityFrames:
    def test_empty_source_no_error(self, tmp_path):
        src = tmp_path / "empty_src"
        src.mkdir()
        dst = tmp_path / "dst"
        dst.mkdir()
        settings = {"locality_names/timeline_json": "[]"}
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 0

    def test_no_timeline_hard_links_all(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=5)
        settings: dict = {"locality_names/timeline_json": "[]"}
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 5

    def test_with_timeline_produces_same_count(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=10)
        timeline = json.dumps([{"frame_start": 0, "name": "Paris"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/position": "bottom-right",
            "locality_names/duration": 5.0,
            "locality_names/text_color": "#ffffff",
            "locality_names/shadow": False,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 10

    def test_output_frames_are_valid_images(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=5, size=(320, 240))
        timeline = json.dumps([{"frame_start": 0, "name": "Berlin"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 10.0,
            "locality_names/shadow": True,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        for f in sorted(dst.glob("*.png")):
            img = Image.open(f)
            assert img.size == (320, 240)

    def test_progress_cb_called(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=4)
        timeline = json.dumps([{"frame_start": 0, "name": "Rome"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 10.0,
        }
        calls: list[tuple[int, int]] = []
        _composite_locality_frames(str(src), dst, settings, fps=30,
                                    progress_cb=lambda d, t: calls.append((d, t)))
        assert len(calls) == 4
        assert calls[-1][0] == calls[-1][1]

    def test_malformed_timeline_json_falls_back(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=3)
        settings = {"locality_names/timeline_json": "NOT_JSON"}
        _composite_locality_frames(str(src), dst, settings, fps=30)
        # Should not raise; falls back to hard-linking
        assert len(list(dst.glob("*.png"))) == 3

    def test_positions_top_left(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=5)
        timeline = json.dumps([{"frame_start": 0, "name": "London"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/position": "top-left",
            "locality_names/duration": 10.0,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 5

    def test_center_position(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=3)
        timeline = json.dumps([{"frame_start": 0, "name": "Madrid"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/position": "center",
            "locality_names/duration": 10.0,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 3

    def test_no_active_frames_hard_linked(self, tmp_path):
        """Frames outside the locality duration should be hard-linked."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=5)
        # Start at frame 1000 → none of the 5 frames are in range
        timeline = json.dumps([{"frame_start": 1000, "name": "Tokyo"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 1.0,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 5

    def test_prepended_black_frames_have_no_overlay(self, tmp_path):
        """Frames 0..n_prepended_black-1 must be hard-linked (no text)."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        n_black = 3
        _write_locality_frames(src, count=6)  # frames 0-5; 0-2 are "black"
        # Entry starts at original frame 0 — without offset correction it would
        # put text on the prepended black frames too.
        timeline = json.dumps([{"frame_start": 0, "name": "Paris"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 60.0,  # long enough to cover all frames
        }
        _composite_locality_frames(str(src), dst, settings, fps=30,
                                    n_prepended_black=n_black)
        # All 6 output frames produced
        assert len(list(dst.glob("*.png"))) == 6
        # Frames 0-2 must be identical to source (hard-linked, no compositing)
        for i in range(n_black):
            src_px = Image.open(src / f"{i:06d}.png").getpixel((160, 120))
            dst_px = Image.open(dst / f"{i:06d}.png").getpixel((160, 120))
            assert src_px == dst_px, f"Frame {i} should not have an overlay"

    def test_pause_frames_have_no_overlay(self, tmp_path):
        """Frames listed in pause_frames_json must be hard-linked (no text)."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=6)
        pause_frames = [2, 3]  # these are photo-pause frames
        timeline = json.dumps([{"frame_start": 0, "name": "Rome"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 60.0,
            "locality_names/pause_frames_json": json.dumps(pause_frames),
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 6
        for i in pause_frames:
            src_px = Image.open(src / f"{i:06d}.png").getpixel((160, 120))
            dst_px = Image.open(dst / f"{i:06d}.png").getpixel((160, 120))
            assert src_px == dst_px, f"Pause frame {i} should not have an overlay"

    def test_malformed_pause_json_is_ignored(self, tmp_path):
        """Bad pause_frames_json should not raise; falls back to no suppression."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=3)
        timeline = json.dumps([{"frame_start": 0, "name": "Madrid"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 60.0,
            "locality_names/pause_frames_json": "NOT_VALID_JSON",
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 3

    def test_suppress_end_frames_have_no_overlay(self, tmp_path):
        """Last n_suppress_end frames must be hard-linked (no text) — fade-out black clip."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        n_suppress = 2
        _write_locality_frames(src, count=6)
        # Entry covers all frames; without end suppression all would get text.
        timeline = json.dumps([{"frame_start": 0, "name": "Vienna"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 60.0,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30,
                                    n_suppress_end=n_suppress)
        assert len(list(dst.glob("*.png"))) == 6
        # Last n_suppress frames must be pixel-identical to source (no overlay).
        for i in range(6 - n_suppress, 6):
            src_px = Image.open(src / f"{i:06d}.png").getpixel((160, 120))
            dst_px = Image.open(dst / f"{i:06d}.png").getpixel((160, 120))
            assert src_px == dst_px, f"Frame {i} should not have an overlay"

    def test_suppress_end_with_prepended_black(self, tmp_path):
        """End suppression applies on top of start suppression without conflict."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        n_black = 2
        n_suppress = 2
        _write_locality_frames(src, count=8)
        timeline = json.dumps([{"frame_start": 0, "name": "Zurich"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 60.0,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30,
                                    n_prepended_black=n_black,
                                    n_suppress_end=n_suppress)
        assert len(list(dst.glob("*.png"))) == 8
        # First n_black frames: suppressed (start)
        for i in range(n_black):
            src_px = Image.open(src / f"{i:06d}.png").getpixel((160, 120))
            dst_px = Image.open(dst / f"{i:06d}.png").getpixel((160, 120))
            assert src_px == dst_px, f"Start frame {i} should not have an overlay"
        # Last n_suppress frames: suppressed (end)
        for i in range(8 - n_suppress, 8):
            src_px = Image.open(src / f"{i:06d}.png").getpixel((160, 120))
            dst_px = Image.open(dst / f"{i:06d}.png").getpixel((160, 120))
            assert src_px == dst_px, f"End frame {i} should not have an overlay"


# ── _resolve_overlay ──────────────────────────────────────────────

class TestResolveOverlay:
    def _tl(self, *starts: int) -> list[dict]:
        """Build a timeline with named entries 'Name0', 'Name1', … at the given starts."""
        return [{"frame_start": s, "name": f"Name{i}"} for i, s in enumerate(starts)]

    def test_empty_timeline(self):
        assert _resolve_overlay(0, [], 60, 5) == []

    def test_before_first_entry(self):
        assert _resolve_overlay(0, self._tl(10), 60, 5) == []

    def test_single_entry_fade_in(self):
        result = _resolve_overlay(3, self._tl(0), 60, 6)
        assert len(result) == 1
        name, alpha = result[0]
        assert name == "Name0"
        assert alpha == pytest.approx(3 / 6)

    def test_single_entry_full_opacity(self):
        result = _resolve_overlay(30, self._tl(0), 60, 6)
        assert len(result) == 1
        assert result[0][1] == pytest.approx(1.0)

    def test_single_entry_fade_out(self):
        # Fade-out starts at offset 60-6=54; at offset 57: (60-57)/6=0.5
        result = _resolve_overlay(57, self._tl(0), 60, 6)
        assert len(result) == 1
        assert result[0][1] == pytest.approx(3 / 6)

    def test_single_entry_expired(self):
        # offset == duration_frames → no overlay
        assert _resolve_overlay(60, self._tl(0), 60, 6) == []

    def test_gap_between_entries_returns_empty(self):
        # Entry0 ends at 60, entry1 starts at 120; at frame 90 — gap
        result = _resolve_overlay(90, self._tl(0, 120), 60, 6)
        assert result == []

    def test_no_overlap_two_entries_only_one_active(self):
        # Entry0: 0-60, Entry1: 120-180 (with fade). At frame 30 only Name0 visible.
        result = _resolve_overlay(30, self._tl(0, 120), 60, 6)
        assert len(result) == 1
        assert result[0][0] == "Name0"

    def test_cross_fade_in_progress(self):
        # Entry0 starts at 0, Entry1 starts at 40 (within Entry0's duration of 60).
        # At frame 43 (3 frames into cross-fade of 6): old=0.5, new=0.5 → sum=1.0
        timeline = self._tl(0, 40)
        result = _resolve_overlay(43, timeline, 60, 6)
        assert len(result) == 2
        names  = {r[0] for r in result}
        alphas = {r[0]: r[1] for r in result}
        assert "Name0" in names
        assert "Name1" in names
        assert alphas["Name0"] == pytest.approx(3 / 6)
        assert alphas["Name1"] == pytest.approx(3 / 6)
        # Alphas must sum to 1.0 during cross-fade
        assert sum(r[1] for r in result) == pytest.approx(1.0)

    def test_cross_fade_complete_only_new_visible(self):
        timeline = self._tl(0, 40)
        # After fade_frames=6 have elapsed since entry1 start (frame 46)
        result = _resolve_overlay(46, timeline, 60, 6)
        assert len(result) == 1
        assert result[0][0] == "Name1"

    def test_cross_fade_alphas_sum_to_one_throughout(self):
        """Verify alpha sum == 1.0 for every frame during a cross-fade."""
        timeline = self._tl(0, 30)
        fade_frames = 10
        for f in range(30, 40):
            result = _resolve_overlay(f, timeline, 60, fade_frames)
            total = sum(r[1] for r in result)
            assert total == pytest.approx(1.0), f"frame {f}: alphas sum {total}"

    def test_instant_cut_fade_frames_zero(self):
        # With fade_frames=0: no cross-fade, hard cut at entry1's start
        timeline = self._tl(0, 30)
        # At frame 29 (just before): only Name0
        result = _resolve_overlay(29, timeline, 60, 0)
        assert len(result) == 1
        assert result[0][0] == "Name0"
        # At frame 30 (entry1 starts): only Name1
        result = _resolve_overlay(30, timeline, 60, 0)
        assert len(result) == 1
        assert result[0][0] == "Name1"


# ── _composite_locality_frames — "forever" mode ───────────────────

class TestCompositeLocalityFramesForever:
    """_composite_locality_frames with duration_forever=True."""

    def test_forever_entry_stays_until_next(self, tmp_path):
        """Name stays visible between its start and the next entry's start."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        # 10 frames; entry0 at 0, entry1 at 7
        _write_locality_frames(src, count=10)
        timeline = json.dumps([
            {"frame_start": 0, "name": "Paris"},
            {"frame_start": 7, "name": "Lyon"},
        ])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 2.0,       # would normally expire early
            "locality_names/duration_forever": True,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 10

    def test_forever_single_entry_stays_to_end(self, tmp_path):
        """With a single entry and forever=True, name persists until the last frame."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=8)
        timeline = json.dumps([{"frame_start": 0, "name": "Berlin"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 0.5,       # short — would expire at frame 15 at 30fps
            "locality_names/duration_forever": True,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        # Every frame after the fade-in should have been composited (not hard-linked)
        # — we verify the output exists and has the right count.
        assert len(list(dst.glob("*.png"))) == 8

    def test_forever_false_entry_expires(self, tmp_path):
        """Sanity check: duration_forever=False still expires after duration_frames."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        dst.mkdir()
        _write_locality_frames(src, count=5)
        # duration=0.1s at fps=30 → 3 frames; frame 4 should be hard-linked
        timeline = json.dumps([{"frame_start": 0, "name": "Rome"}])
        settings = {
            "locality_names/timeline_json": timeline,
            "locality_names/duration": 0.1,
            "locality_names/duration_forever": False,
        }
        _composite_locality_frames(str(src), dst, settings, fps=30)
        assert len(list(dst.glob("*.png"))) == 5
        # Frame 4 must be pixel-identical to source (hard-linked, no overlay)
        src_px = Image.open(src / "000004.png").getpixel((160, 120))
        dst_px = Image.open(dst / "000004.png").getpixel((160, 120))
        assert src_px == dst_px
