"""Tests for video_assembler helper functions."""

import math
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from georeel.core.video_assembler import (
    _fade_filters,
    _title_alpha,
    _music_audio_cmd_parts,
    _quality_args,
    _pix_fmt_args,
    _container_args,
    _serialise_settings,
)
from georeel.core.encoder_registry import get_encoder


# ── _title_alpha ─────────────────────────────────────────────────────

class TestTitleAlpha:
    def test_before_start_is_zero(self):
        assert _title_alpha(-0.1, 10.0, False, 0.0, False, 0.0) == pytest.approx(0.0)

    def test_after_duration_is_zero(self):
        assert _title_alpha(10.1, 10.0, False, 0.0, False, 0.0) == pytest.approx(0.0)

    def test_no_fades_full_opacity(self):
        assert _title_alpha(5.0, 10.0, False, 0.0, False, 0.0) == pytest.approx(1.0)

    def test_fade_in_ramp(self):
        # fi_dur=2, at t=1 → alpha=0.5
        alpha = _title_alpha(1.0, 10.0, fi_on=True, fi_dur=2.0, fo_on=False, fo_dur=0.0)
        assert alpha == pytest.approx(0.5)

    def test_fade_in_complete_at_fi_dur(self):
        alpha = _title_alpha(2.0, 10.0, fi_on=True, fi_dur=2.0, fo_on=False, fo_dur=0.0)
        assert alpha == pytest.approx(1.0)

    def test_fade_out_ramp(self):
        # duration=10, fo_dur=2 → at t=9 (1s before end) alpha=0.5
        alpha = _title_alpha(9.0, 10.0, fi_on=False, fi_dur=0.0, fo_on=True, fo_dur=2.0)
        assert alpha == pytest.approx(0.5)

    def test_fade_out_complete_at_zero(self):
        alpha = _title_alpha(10.0, 10.0, fi_on=False, fi_dur=0.0, fo_on=True, fo_dur=2.0)
        assert alpha == pytest.approx(0.0)

    def test_both_fades_takes_minimum(self):
        # fi_dur=3, fo_dur=3, duration=10, at t=1 → fi=1/3, fo=1.0 → min=1/3
        alpha = _title_alpha(1.0, 10.0, fi_on=True, fi_dur=3.0, fo_on=True, fo_dur=3.0)
        assert alpha == pytest.approx(1 / 3, rel=1e-4)

    def test_fi_dur_zero_no_ramp(self):
        # With fi_dur=0, fade-in is effectively disabled
        alpha = _title_alpha(0.0, 10.0, fi_on=True, fi_dur=0.0, fo_on=False, fo_dur=0.0)
        assert alpha == pytest.approx(1.0)

    def test_at_zero_no_fades(self):
        assert _title_alpha(0.0, 10.0, False, 0.0, False, 0.0) == pytest.approx(1.0)

    def test_alpha_never_exceeds_one(self):
        for t in [0.0, 1.0, 5.0, 9.0, 10.0]:
            alpha = _title_alpha(t, 10.0, fi_on=True, fi_dur=2.0, fo_on=True, fo_dur=2.0)
            assert alpha <= 1.0

    def test_alpha_never_below_zero(self):
        for t in [-1.0, 0.0, 5.0, 10.0, 11.0]:
            alpha = _title_alpha(t, 10.0, fi_on=True, fi_dur=2.0, fo_on=True, fo_dur=2.0)
            assert alpha >= 0.0


# ── _fade_filters ─────────────────────────────────────────────────────

class TestFadeFilters:
    def test_no_effects_returns_empty(self):
        filters, frames = _fade_filters({}, total_frames=300, fps=30)
        assert filters == []
        assert frames == 300

    def test_fade_in_only_adds_tpad_and_fade(self):
        settings = {
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": 5.0,
            "clip_effects/fade_in_fade_dur": 1.0,
        }
        filters, frames = _fade_filters(settings, total_frames=300, fps=30)
        combined = " ".join(filters)
        assert "tpad" in combined
        assert "fade=t=in" in combined
        assert frames == 300 + 5 * 30  # fi_black*fps added

    def test_fade_out_only_adds_tpad_and_fade_out(self):
        settings = {
            "clip_effects/fade_out_enabled": True,
            "clip_effects/fade_out_black_dur": 3.0,
            "clip_effects/fade_out_fade_dur": 1.0,
        }
        filters, frames = _fade_filters(settings, total_frames=300, fps=30)
        combined = " ".join(filters)
        assert "fade=t=out" in combined
        assert frames == 300 + 3 * 30

    def test_both_effects_add_both_frames(self):
        settings = {
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": 2.0,
            "clip_effects/fade_in_fade_dur": 1.0,
            "clip_effects/fade_out_enabled": True,
            "clip_effects/fade_out_black_dur": 3.0,
            "clip_effects/fade_out_fade_dur": 1.0,
        }
        filters, frames = _fade_filters(settings, total_frames=300, fps=30)
        # Total: 300 + 2*30 + 3*30 = 450
        assert frames == 450

    def test_skip_prepend_omits_tpad_start_and_fade_in(self):
        settings = {
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": 5.0,
            "clip_effects/fade_in_fade_dur": 1.0,
        }
        filters, frames = _fade_filters(settings, total_frames=300, fps=30,
                                        skip_prepend=True)
        combined = " ".join(filters)
        assert "start_duration" not in combined
        assert "fade=t=in" not in combined
        # With skip_prepend, fi_black frames are already in total_frames → not added again
        assert frames == 300

    def test_skip_prepend_fo_start_correct(self):
        # The bug we fixed: with skip_prepend=True, fo_start = orig_dur - fo_fade
        settings = {
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": 5.0,
            "clip_effects/fade_in_fade_dur": 1.0,
            "clip_effects/fade_out_enabled": True,
            "clip_effects/fade_out_black_dur": 5.0,
            "clip_effects/fade_out_fade_dur": 1.0,
        }
        fps = 30
        # total_frames already includes 5s of black = 5*30=150 black + 300 content
        total_frames = 300 + 5 * 30
        filters, _ = _fade_filters(settings, total_frames=total_frames, fps=fps,
                                   skip_prepend=True)
        fade_out_filter = next(f for f in filters if "fade=t=out" in f)
        # orig_dur = total_frames / fps; fo_start = orig_dur - fo_fade
        orig_dur = total_frames / fps
        expected_fo_start = orig_dur - 1.0
        assert f"st={expected_fo_start:.6f}" in fade_out_filter

    def test_no_skip_prepend_fo_start_includes_fi_black(self):
        settings = {
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": 5.0,
            "clip_effects/fade_in_fade_dur": 1.0,
            "clip_effects/fade_out_enabled": True,
            "clip_effects/fade_out_black_dur": 5.0,
            "clip_effects/fade_out_fade_dur": 1.0,
        }
        fps = 30
        total_frames = 300
        filters, _ = _fade_filters(settings, total_frames=total_frames, fps=fps,
                                   skip_prepend=False)
        fade_out_filter = next(f for f in filters if "fade=t=out" in f)
        # orig_dur = 300/30 = 10s; fo_start = fi_black + orig_dur - fo_fade = 5+10-1=14
        assert "st=14.000000" in fade_out_filter

    def test_zero_fade_durations_no_fade_filter(self):
        settings = {
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": 5.0,
            "clip_effects/fade_in_fade_dur": 0.0,  # zero fade
        }
        filters, _ = _fade_filters(settings, total_frames=300, fps=30)
        assert not any("fade=t=in" in f for f in filters)

    def test_returns_tuple(self):
        result = _fade_filters({}, total_frames=100, fps=30)
        assert isinstance(result, tuple)
        assert isinstance(result[0], list)
        assert isinstance(result[1], int)


# ── _music_audio_cmd_parts ───────────────────────────────────────────

class TestMusicAudioCmdParts:
    def test_disabled_returns_empty(self):
        settings = {"clip_effects/music_enabled": False}
        r = _music_audio_cmd_parts(settings, 60.0)
        assert r == ([], [], [], [])

    def test_enabled_but_no_path_returns_empty(self):
        settings = {"clip_effects/music_enabled": True, "clip_effects/music_path": ""}
        r = _music_audio_cmd_parts(settings, 60.0)
        assert r == ([], [], [], [])

    def test_enabled_nonexistent_file_returns_empty(self):
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": "/nonexistent/audio.mp3",
        }
        r = _music_audio_cmd_parts(settings, 60.0)
        assert r == ([], [], [], [])

    def test_enabled_with_real_file(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
        }
        pre, inp, af, codec = _music_audio_cmd_parts(settings, 60.0)
        assert inp == ["-i", str(audio)]
        assert "-af" in af
        assert "-map" in codec

    def test_loop_adds_stream_loop(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
            "clip_effects/music_loop": True,
        }
        pre, inp, af, codec = _music_audio_cmd_parts(settings, 60.0)
        assert "-stream_loop" in pre

    def test_no_loop_no_stream_loop(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
            "clip_effects/music_loop": False,
        }
        pre, _, _, _ = _music_audio_cmd_parts(settings, 60.0)
        assert pre == []

    def test_delay_adds_adelay(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
            "clip_effects/music_delay": 3.0,
        }
        _, _, af, _ = _music_audio_cmd_parts(settings, 60.0)
        assert "adelay" in af[1]

    def test_fade_in_adds_afade_in(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
            "clip_effects/music_fade_in_enabled": True,
            "clip_effects/music_fade_in_dur": 2.0,
        }
        _, _, af, _ = _music_audio_cmd_parts(settings, 60.0)
        assert "afade=t=in" in af[1]

    def test_fade_out_adds_afade_out(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
            "clip_effects/music_fade_out_enabled": True,
            "clip_effects/music_fade_out_dur": 5.0,
        }
        _, _, af, _ = _music_audio_cmd_parts(settings, 60.0)
        assert "afade=t=out" in af[1]

    def test_atrim_uses_total_duration(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
        }
        _, _, af, _ = _music_audio_cmd_parts(settings, 45.0)
        assert "atrim=end=45.000000" in af[1]

    def test_codec_args_include_aac(self, tmp_path):
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\x00" * 100)
        settings = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_path": str(audio),
        }
        _, _, _, codec = _music_audio_cmd_parts(settings, 60.0)
        assert "-c:a" in codec
        assert "aac" in codec


# ── _quality_args ────────────────────────────────────────────────────

class TestQualityArgs:
    def test_libx264_crf(self):
        enc = get_encoder("libx264")
        args = _quality_args(enc, cq=23, preset="medium")
        assert "-crf" in args
        assert "23" in args
        assert "-preset" in args
        assert "medium" in args

    def test_libx265_crf(self):
        enc = get_encoder("libx265")
        args = _quality_args(enc, cq=28, preset="slow")
        assert "-crf" in args
        assert "28" in args

    def test_no_cq_flag_skips_cq(self):
        enc = get_encoder("h264_videotoolbox")
        args = _quality_args(enc, cq=65, preset="")
        # h264_videotoolbox has no preset_flag
        assert "-preset" not in args

    def test_nvenc_adds_rc_vbr(self):
        enc = get_encoder("h264_nvenc")
        args = _quality_args(enc, cq=28, preset="p4")
        assert "-rc" in args
        assert "vbr" in args
        assert "-cq" in args

    def test_svtav1_adds_zero_bitrate(self):
        enc = get_encoder("libsvtav1")
        args = _quality_args(enc, cq=35, preset="5")
        assert "-b:v" in args
        assert "0" in args


class TestPixFmtArgs:
    def test_returns_yuv420p(self):
        enc = get_encoder("libx264")
        args = _pix_fmt_args(enc)
        assert "-pix_fmt" in args
        assert "yuv420p" in args


class TestContainerArgs:
    def test_mp4_faststart(self):
        enc = get_encoder("libx264")
        args = _container_args(enc, "mp4")
        assert "-movflags" in args
        assert "+faststart" in args

    def test_mkv_no_movflags(self):
        enc = get_encoder("libx264")
        args = _container_args(enc, "mkv")
        assert "-movflags" not in args

    def test_mp4_h265_gets_hvc1_tag(self):
        enc = get_encoder("libx265")
        args = _container_args(enc, "mp4")
        assert "-tag:v" in args
        assert "hvc1" in args

    def test_mp4_h264_no_hvc1_tag(self):
        enc = get_encoder("libx264")
        args = _container_args(enc, "mp4")
        assert "hvc1" not in args


class TestSerialiseSettings:
    def test_excludes_api_key(self):
        settings = {"imagery/api_key": "secret", "render/fps": 30}
        result = _serialise_settings(settings)
        assert "secret" not in result
        assert "30" in result

    def test_valid_json(self):
        import json
        settings = {"render/fps": 30, "output/encoder": "libx264"}
        result = _serialise_settings(settings)
        parsed = json.loads(result)
        assert parsed["render/fps"] == 30
