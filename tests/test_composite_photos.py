"""Tests for photo_compositor.composite_photos (integration-level with real frames)."""

import pytest
from pathlib import Path
from PIL import Image

from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.photo_compositor import composite_photos, CompositorError
from georeel.core.pipeline import Pipeline


def _make_kf(frame: int, is_pause: bool = False, photo_path=None):
    return CameraKeyframe(
        frame=frame, x=float(frame), y=0.0, z=100.0,
        look_at_x=float(frame) + 1, look_at_y=0.0, look_at_z=50.0,
        is_pause=is_pause,
        photo_path=photo_path,
    )


def _write_frames(path: Path, count: int, size=(64, 48)):
    path.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        Image.new("RGB", size, (100, 150, 200)).save(path / f"{i:06d}.png")


# ── error paths ───────────────────────────────────────────────────────

class TestCompositePhotosErrors:
    def test_no_rendered_dir_raises(self):
        p = Pipeline()
        p.camera_keyframes = [_make_kf(1)]
        with pytest.raises(CompositorError, match="[Rr]endered"):
            composite_photos(p, {})

    def test_no_keyframes_raises(self, tmp_path):
        p = Pipeline()
        frames = tmp_path / "frames"
        _write_frames(frames, 5)
        p.rendered_frames_dir = str(frames)
        p.camera_keyframes = []
        with pytest.raises(CompositorError, match="keyframes"):
            composite_photos(p, {})

    def test_empty_src_dir_raises(self, tmp_path):
        p = Pipeline()
        frames = tmp_path / "frames"
        frames.mkdir()
        p.rendered_frames_dir = str(frames)
        p.camera_keyframes = [_make_kf(1)]
        with pytest.raises(CompositorError, match="[Nn]o.*frame"):
            composite_photos(p, {})


# ── all-fly happy path (no photos needed) ─────────────────────────────

class TestCompositePhotosAllFly:
    def test_returns_path(self, tmp_path):
        p = Pipeline()
        frames = tmp_path / "frames"
        _write_frames(frames, 5)
        p.rendered_frames_dir = str(frames)
        p.camera_keyframes = [_make_kf(i + 1) for i in range(5)]
        result = composite_photos(p, {"render/resolution": "720p"})
        assert isinstance(result, str)
        p.cleanup()

    def test_output_has_same_frame_count(self, tmp_path):
        n = 8
        p = Pipeline()
        frames = tmp_path / "frames"
        _write_frames(frames, n)
        p.rendered_frames_dir = str(frames)
        p.camera_keyframes = [_make_kf(i + 1) for i in range(n)]
        out_dir = Path(composite_photos(p, {"render/resolution": "720p",
                                            "render/fps": 30}))
        assert len(list(out_dir.glob("*.png"))) == n
        p.cleanup()

    def test_progress_cb_called(self, tmp_path):
        calls = []
        p = Pipeline()
        frames = tmp_path / "frames"
        _write_frames(frames, 4)
        p.rendered_frames_dir = str(frames)
        p.camera_keyframes = [_make_kf(i + 1) for i in range(4)]
        composite_photos(
            p,
            {"render/resolution": "720p"},
            progress_cb=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) == 4
        p.cleanup()

    def test_cancel_check_stops_early(self, tmp_path):
        p = Pipeline()
        frames = tmp_path / "frames"
        _write_frames(frames, 10)
        p.rendered_frames_dir = str(frames)
        p.camera_keyframes = [_make_kf(i + 1) for i in range(10)]
        with pytest.raises(CompositorError, match="[Cc]ancelll?ed|[Cc]ancell?ing"):
            composite_photos(
                p,
                {"render/resolution": "720p"},
                cancel_check=lambda: True,
            )
        p.cleanup()

    def test_cut_transition_works(self, tmp_path):
        p = Pipeline()
        frames = tmp_path / "frames"
        _write_frames(frames, 6)
        p.rendered_frames_dir = str(frames)
        p.camera_keyframes = [_make_kf(i + 1) for i in range(6)]
        result = composite_photos(p, {
            "render/resolution": "720p",
            "render/photo_transition": "cut",
        })
        assert result
        p.cleanup()


# ── pause frames with a real photo ────────────────────────────────────

class TestCompositePhotosWithPhoto:
    def test_pause_frames_with_real_photo(self, tmp_path):
        photo = tmp_path / "photo.jpg"
        Image.new("RGB", (640, 480), (200, 100, 50)).save(str(photo))

        n_fly = 4
        n_pause = 3
        total = n_fly + n_pause

        p = Pipeline()
        frames = tmp_path / "frames"
        _write_frames(frames, total)
        p.rendered_frames_dir = str(frames)

        kfs = [_make_kf(i + 1) for i in range(n_fly)]
        kfs += [_make_kf(n_fly + i + 1, is_pause=True, photo_path=str(photo))
                for i in range(n_pause)]
        p.camera_keyframes = kfs

        out_dir = Path(composite_photos(p, {
            "render/resolution": "720p",
            "render/photo_transition": "cut",
        }))
        assert len(list(out_dir.glob("*.png"))) == total
        p.cleanup()
