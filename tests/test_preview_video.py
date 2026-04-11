"""Tests for preview_video.build_preview_keyframes."""

import pytest
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.preview_video import build_preview_keyframes, _PREVIEW_FRACTION, _PREVIEW_MIN_CONTENT_S


def _kf(frame):
    return CameraKeyframe(
        frame=frame, x=0.0, y=0.0, z=0.0,
        look_at_x=0.0, look_at_y=0.0, look_at_z=0.0,
        is_pause=False, photo_path=None,
    )


def _make_kfs(n):
    return [_kf(i) for i in range(n)]


class TestBuildPreviewKeyframesBase:
    def test_returns_subset(self):
        kfs = _make_kfs(1000)
        result = build_preview_keyframes(kfs)
        assert len(result) < len(kfs)

    def test_returns_first_keyframes(self):
        kfs = _make_kfs(1000)
        result = build_preview_keyframes(kfs)
        assert result == kfs[:len(result)]

    def test_minimum_two_frames(self):
        # Even for very small tracks, at least 2 frames returned
        kfs = _make_kfs(10)
        result = build_preview_keyframes(kfs)
        assert len(result) >= 2

    def test_two_percent_of_total(self):
        kfs = _make_kfs(500)
        result = build_preview_keyframes(kfs, settings={"render/fps": 30})
        expected = max(2, round(500 * _PREVIEW_FRACTION))
        assert len(result) == expected

    def test_empty_keyframes_returns_empty(self):
        result = build_preview_keyframes([])
        assert result == []

    def test_single_keyframe_returns_it(self):
        kfs = _make_kfs(1)
        result = build_preview_keyframes(kfs)
        assert len(result) == 1

    def test_never_exceeds_total(self):
        kfs = _make_kfs(5)
        result = build_preview_keyframes(kfs)
        assert len(result) <= len(kfs)


class TestBuildPreviewKeyframesWithFadeIn:
    def _settings_fade_in(self, black=5.0, fade=1.0, fps=30):
        return {
            "render/fps": fps,
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": black,
            "clip_effects/fade_in_fade_dur": fade,
        }

    def test_fade_in_adds_extra_frames(self):
        kfs = _make_kfs(3000)
        settings_plain = {"render/fps": 30}
        settings_fade = self._settings_fade_in(black=5.0, fade=1.0, fps=30)
        base = build_preview_keyframes(kfs, settings_plain)
        with_fade = build_preview_keyframes(kfs, settings_fade)
        assert len(with_fade) > len(base)

    def test_extra_includes_content_minimum(self):
        kfs = _make_kfs(3000)
        fps = 30
        black = 5.0
        fade = 1.0
        settings = self._settings_fade_in(black=black, fade=fade, fps=fps)
        result = build_preview_keyframes(kfs, settings)
        base = max(2, round(3000 * _PREVIEW_FRACTION))
        extra = round((black + fade + _PREVIEW_MIN_CONTENT_S) * fps)
        expected = min(base + extra, 3000)
        assert len(result) == expected

    def test_fade_out_disabled_no_extra_frames(self):
        # Fade-out is suppressed in preview; no extra frames should come from it
        kfs = _make_kfs(3000)
        settings_no_effects = {"render/fps": 30}
        settings_fo_only = {
            "render/fps": 30,
            "clip_effects/fade_out_enabled": True,
            "clip_effects/fade_out_black_dur": 10.0,
            "clip_effects/fade_out_fade_dur": 2.0,
        }
        base = build_preview_keyframes(kfs, settings_no_effects)
        with_fo = build_preview_keyframes(kfs, settings_fo_only)
        # Fade-out is not in build_preview_keyframes, so same result
        assert len(base) == len(with_fo)

    def test_caps_at_total_keyframe_count(self):
        # With a very small track + big fade settings, must not exceed total
        kfs = _make_kfs(10)
        settings = self._settings_fade_in(black=100.0, fade=100.0, fps=30)
        result = build_preview_keyframes(kfs, settings)
        assert len(result) == len(kfs)
