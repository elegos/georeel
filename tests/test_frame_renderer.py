"""Tests for frame_renderer._write_keyframes and render_frames error paths."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

import georeel.core.frame_renderer as fr
from georeel.core.frame_renderer import (
    FrameRenderError,
    _write_keyframes,
    render_frames,
)
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.pipeline import Pipeline


def _kf(frame=1, is_pause=False):
    return CameraKeyframe(
        frame=frame, x=0.0, y=0.0, z=100.0,
        look_at_x=1.0, look_at_y=1.0, look_at_z=50.0,
        is_pause=is_pause, photo_path=None,
    )


# ── _write_keyframes ──────────────────────────────────────────────────

class TestWriteKeyframes:
    def test_creates_json_file(self, tmp_path):
        kfs = [_kf(1), _kf(2)]
        path = tmp_path / "keyframes.json"
        _write_keyframes(kfs, path)
        assert path.exists()

    def test_json_is_list(self, tmp_path):
        path = tmp_path / "keyframes.json"
        _write_keyframes([_kf(1)], path)
        data = json.loads(path.read_text())
        assert isinstance(data, list)

    def test_correct_number_of_entries(self, tmp_path):
        path = tmp_path / "keyframes.json"
        _write_keyframes([_kf(1), _kf(2), _kf(3)], path)
        data = json.loads(path.read_text())
        assert len(data) == 3

    def test_entry_fields_present(self, tmp_path):
        path = tmp_path / "keyframes.json"
        _write_keyframes([_kf(5)], path)
        data = json.loads(path.read_text())
        entry = data[0]
        for field in ("frame", "x", "y", "z", "look_at_x", "look_at_y", "look_at_z",
                      "is_pause", "photo_path"):
            assert field in entry

    def test_frame_number_preserved(self, tmp_path):
        path = tmp_path / "keyframes.json"
        _write_keyframes([_kf(42)], path)
        data = json.loads(path.read_text())
        assert data[0]["frame"] == 42

    def test_is_pause_preserved(self, tmp_path):
        path = tmp_path / "keyframes.json"
        _write_keyframes([_kf(1, is_pause=True)], path)
        data = json.loads(path.read_text())
        assert data[0]["is_pause"] is True

    def test_empty_list_writes_empty_array(self, tmp_path):
        path = tmp_path / "keyframes.json"
        _write_keyframes([], path)
        data = json.loads(path.read_text())
        assert data == []

    def test_coordinates_preserved(self, tmp_path):
        kf = CameraKeyframe(
            frame=1, x=100.5, y=200.5, z=300.5,
            look_at_x=110.0, look_at_y=210.0, look_at_z=250.0,
            is_pause=False, photo_path=None,
        )
        path = tmp_path / "keyframes.json"
        _write_keyframes([kf], path)
        data = json.loads(path.read_text())
        assert data[0]["x"] == pytest.approx(100.5)
        assert data[0]["look_at_z"] == pytest.approx(250.0)


# ── render_frames error paths ─────────────────────────────────────────

class TestRenderFramesErrors:
    def test_no_scene_raises(self):
        p = Pipeline()
        p.camera_keyframes = [_kf(1)]
        with pytest.raises(FrameRenderError, match="scene"):
            render_frames(p, {})

    def test_no_keyframes_raises(self):
        p = Pipeline()
        p.scene = "/fake/scene.blend"
        p.camera_keyframes = []
        with pytest.raises(FrameRenderError, match="keyframes"):
            render_frames(p, {})

    def test_none_keyframes_raises(self):
        p = Pipeline()
        p.scene = "/fake/scene.blend"
        p.camera_keyframes = None  # type: ignore[assignment]
        with pytest.raises(FrameRenderError, match="keyframes"):
            render_frames(p, {})

    def test_blender_not_found_raises(self):
        p = Pipeline()
        p.scene = "/fake/scene.blend"
        p.camera_keyframes = [_kf(1)]
        with patch("georeel.core.frame_renderer.find_blender", return_value=None):
            with pytest.raises(FrameRenderError, match="[Bb]lender"):
                render_frames(p, {})
