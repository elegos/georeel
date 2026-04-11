"""Tests for photo_compositor pure helper functions."""

import pytest
from PIL import Image
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.photo_compositor import (
    _build_blocks,
    _group_into_runs,
    _absorb_photo_gaps,
    _fit_photo,
)


def _kf(frame, is_pause=False, photo_path=None):
    return CameraKeyframe(
        frame=frame, x=0.0, y=0.0, z=0.0,
        look_at_x=0.0, look_at_y=0.0, look_at_z=0.0,
        is_pause=is_pause,
        photo_path=photo_path,
    )


# ── _build_blocks ──────────────────────────────────────────────────

class TestBuildBlocks:
    def test_all_fly_frames_single_block(self):
        kfs = [_kf(i) for i in range(10)]
        blocks = _build_blocks(kfs)
        assert len(blocks) == 1
        assert blocks[0]["is_pause"] is False
        assert blocks[0]["frames"] == list(range(10))

    def test_all_pause_frames(self):
        kfs = [_kf(i, is_pause=True, photo_path="/a.jpg") for i in range(5)]
        blocks = _build_blocks(kfs)
        assert len(blocks) == 1
        assert blocks[0]["is_pause"] is True
        assert blocks[0]["photo_path"] == "/a.jpg"

    def test_fly_then_pause_two_blocks(self):
        kfs = [_kf(0), _kf(1), _kf(2, is_pause=True, photo_path="/a.jpg")]
        blocks = _build_blocks(kfs)
        assert len(blocks) == 2
        assert blocks[0]["is_pause"] is False
        assert blocks[1]["is_pause"] is True

    def test_pause_then_fly_two_blocks(self):
        kfs = [_kf(0, is_pause=True, photo_path="/a.jpg"), _kf(1), _kf(2)]
        blocks = _build_blocks(kfs)
        assert len(blocks) == 2
        assert blocks[0]["is_pause"] is True
        assert blocks[1]["is_pause"] is False

    def test_fly_pause_fly_three_blocks(self):
        kfs = [
            _kf(0), _kf(1),
            _kf(2, is_pause=True, photo_path="/a.jpg"),
            _kf(3), _kf(4),
        ]
        blocks = _build_blocks(kfs)
        assert len(blocks) == 3

    def test_different_photos_separate_blocks(self):
        kfs = [
            _kf(0, is_pause=True, photo_path="/a.jpg"),
            _kf(1, is_pause=True, photo_path="/b.jpg"),
        ]
        blocks = _build_blocks(kfs)
        assert len(blocks) == 2

    def test_same_photo_merged_into_one_block(self):
        kfs = [
            _kf(0, is_pause=True, photo_path="/a.jpg"),
            _kf(1, is_pause=True, photo_path="/a.jpg"),
        ]
        blocks = _build_blocks(kfs)
        assert len(blocks) == 1
        assert blocks[0]["frames"] == [0, 1]

    def test_empty_returns_empty(self):
        assert _build_blocks([]) == []


# ── _group_into_runs ───────────────────────────────────────────────

class TestGroupIntoRuns:
    def _fly(self, frames):
        return {"is_pause": False, "photo_path": None, "frames": frames}

    def _pause(self, frames, path="/a.jpg"):
        return {"is_pause": True, "photo_path": path, "frames": frames}

    def test_single_fly_block(self):
        blocks = [self._fly([0, 1, 2])]
        runs = _group_into_runs(blocks)
        assert len(runs) == 1
        assert runs[0][0]["is_pause"] is False

    def test_consecutive_pause_blocks_merged(self):
        blocks = [self._pause([0]), self._pause([1]), self._pause([2])]
        runs = _group_into_runs(blocks)
        assert len(runs) == 1
        assert len(runs[0]) == 3

    def test_fly_between_pauses_separates(self):
        blocks = [self._pause([0]), self._fly([1]), self._pause([2])]
        runs = _group_into_runs(blocks)
        assert len(runs) == 3

    def test_fly_pause_fly(self):
        blocks = [self._fly([0, 1]), self._pause([2, 3]), self._fly([4])]
        runs = _group_into_runs(blocks)
        assert len(runs) == 3

    def test_carousel_run_has_multiple_pauses(self):
        blocks = [
            self._pause([0, 1], "/a.jpg"),
            self._pause([2, 3], "/b.jpg"),
        ]
        runs = _group_into_runs(blocks)
        assert len(runs) == 1
        assert len(runs[0]) == 2

    def test_empty_returns_empty(self):
        assert _group_into_runs([]) == []


# ── _absorb_photo_gaps ─────────────────────────────────────────────

class TestAbsorbPhotoGaps:
    def _fly(self, frames):
        return {"is_pause": False, "photo_path": None, "frames": frames}

    def _pause(self, frames, path="/a.jpg"):
        return {"is_pause": True, "photo_path": path, "frames": frames}

    def test_no_gaps_unchanged(self):
        blocks = [self._pause([0, 1]), self._fly([2, 3, 4, 5]), self._pause([6, 7])]
        result, absorbed = _absorb_photo_gaps(blocks, max_gap=1)
        assert absorbed == 0
        assert len(result) == 3

    def test_short_gap_absorbed(self):
        blocks = [self._pause([0, 1]), self._fly([2]), self._pause([3, 4])]
        result, absorbed = _absorb_photo_gaps(blocks, max_gap=1)
        assert absorbed == 1
        # Gap absorbed into first pause block
        assert len(result) == 2
        assert result[0]["frames"] == [0, 1, 2]

    def test_long_gap_not_absorbed(self):
        blocks = [
            self._pause([0, 1]),
            self._fly([2, 3, 4, 5, 6]),
            self._pause([7, 8]),
        ]
        result, absorbed = _absorb_photo_gaps(blocks, max_gap=3)
        assert absorbed == 0
        assert len(result) == 3

    def test_all_fly_no_change(self):
        blocks = [self._fly([i]) for i in range(5)]
        result, absorbed = _absorb_photo_gaps(blocks, max_gap=10)
        assert absorbed == 0
        assert len(result) == 5

    def test_empty_input(self):
        result, absorbed = _absorb_photo_gaps([], max_gap=5)
        assert result == []
        assert absorbed == 0

    def test_multiple_gaps_absorbed(self):
        blocks = [
            self._pause([0]),
            self._fly([1]),       # gap 1 frame
            self._pause([2]),
            self._fly([3]),       # gap 1 frame
            self._pause([4]),
        ]
        result, absorbed = _absorb_photo_gaps(blocks, max_gap=1)
        assert absorbed == 2

    def test_leading_fly_not_absorbed(self):
        blocks = [self._fly([0, 1]), self._pause([2]), self._fly([3]), self._pause([4])]
        result, absorbed = _absorb_photo_gaps(blocks, max_gap=5)
        # Only the gap between pause[2] and pause[4] is absorbed
        assert absorbed == 1


# ── _fit_photo ─────────────────────────────────────────────────────

class TestFitPhoto:
    def _landscape_photo(self):
        return Image.new("RGB", (1920, 1080), (200, 100, 50))

    def _portrait_photo(self):
        return Image.new("RGB", (1080, 1920), (50, 100, 200))

    def _square_photo(self):
        return Image.new("RGB", (500, 500), (128, 128, 128))

    def test_output_size_correct_landscape(self):
        photo = self._landscape_photo()
        result = _fit_photo(photo, out_w=1920, out_h=1080, fill="black")
        assert result.size == (1920, 1080)

    def test_output_size_correct_portrait(self):
        photo = self._portrait_photo()
        result = _fit_photo(photo, out_w=1080, out_h=1920, fill="black")
        assert result.size == (1080, 1920)

    def test_output_size_arbitrary(self):
        photo = self._landscape_photo()
        result = _fit_photo(photo, out_w=640, out_h=480, fill="black")
        assert result.size == (640, 480)

    def test_fill_black_gives_rgb(self):
        photo = self._portrait_photo()
        result = _fit_photo(photo, out_w=1920, out_h=1080, fill="black")
        assert result.mode == "RGB"

    def test_fill_blurred_gives_rgb(self):
        photo = self._landscape_photo()
        result = _fit_photo(photo, out_w=1920, out_h=1080, fill="blurred")
        assert result.mode == "RGB"

    def test_rgba_photo_accepted(self):
        photo = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
        result = _fit_photo(photo, out_w=200, out_h=200, fill="black")
        assert result.size == (200, 200)
        assert result.mode == "RGB"

    def test_small_photo_scaled_up(self):
        photo = Image.new("RGB", (10, 10))
        result = _fit_photo(photo, out_w=1920, out_h=1080, fill="black")
        assert result.size == (1920, 1080)
