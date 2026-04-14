"""Tests for georeel.core.open_in_blender."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from georeel.core.open_in_blender import OpenInBlenderError, inject_camera_and_open
from georeel.core.camera_keyframe import CameraKeyframe


def _kf(frame=1, is_pause=False, x=0.0, y=0.0, z=100.0):
    return CameraKeyframe(
        frame=frame, x=x, y=y, z=z,
        look_at_x=x + 10.0, look_at_y=y + 5.0, look_at_z=z - 50.0,
        is_pause=is_pause, photo_path=None,
    )


class TestInjectCameraAndOpen:
    def test_success_writes_keyframes_json(self, tmp_path):
        blend = tmp_path / "scene.blend"
        blend.write_text("blend")
        out_blend = tmp_path / "scene_with_camera.blend"
        out_blend.write_text("blend_with_camera")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("georeel.core.open_in_blender.subprocess.run", mock_run):
            with patch("georeel.core.open_in_blender.subprocess.Popen") as mock_popen:
                inject_camera_and_open(
                    blender_exe="/usr/bin/blender",
                    blend_path=str(blend),
                    keyframes=[_kf(1), _kf(2)],
                )

        kf_path = tmp_path / "camera_keyframes.json"
        assert kf_path.is_file()
        data = json.loads(kf_path.read_text())
        assert len(data) == 2

    def test_success_launches_blender_interactively(self, tmp_path):
        blend = tmp_path / "scene.blend"
        blend.write_text("blend")
        out_blend = tmp_path / "scene_with_camera.blend"
        out_blend.write_text("blend_with_camera")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("georeel.core.open_in_blender.subprocess.run", mock_run):
            with patch("georeel.core.open_in_blender.subprocess.Popen") as mock_popen:
                inject_camera_and_open(
                    blender_exe="/usr/bin/blender",
                    blend_path=str(blend),
                    keyframes=[_kf(1)],
                )
                mock_popen.assert_called_once()

    def test_injection_failure_raises(self, tmp_path):
        blend = tmp_path / "scene.blend"
        blend.write_text("blend")
        # Do NOT create out_blend → failure

        mock_run = MagicMock()
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error"
        mock_run.return_value.stdout = ""

        with patch("georeel.core.open_in_blender.subprocess.run", mock_run):
            with pytest.raises(OpenInBlenderError, match="injection failed"):
                inject_camera_and_open(
                    blender_exe="/usr/bin/blender",
                    blend_path=str(blend),
                    keyframes=[_kf(1)],
                )

    def test_nonzero_returncode_raises_even_if_file_exists(self, tmp_path):
        blend = tmp_path / "scene.blend"
        blend.write_text("blend")
        out_blend = tmp_path / "scene_with_camera.blend"
        out_blend.write_text("blend_with_camera")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 2
        mock_run.return_value.stderr = "blender error"
        mock_run.return_value.stdout = ""

        with patch("georeel.core.open_in_blender.subprocess.run", mock_run):
            with pytest.raises(OpenInBlenderError):
                inject_camera_and_open(
                    blender_exe="/usr/bin/blender",
                    blend_path=str(blend),
                    keyframes=[_kf(1)],
                )

    def test_keyframe_fields_written(self, tmp_path):
        blend = tmp_path / "scene.blend"
        blend.write_text("blend")
        out_blend = tmp_path / "scene_with_camera.blend"
        out_blend.write_text("blend_with_camera")

        kf = _kf(42, is_pause=True, x=100.0, y=200.0, z=300.0)

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("georeel.core.open_in_blender.subprocess.run", mock_run):
            with patch("georeel.core.open_in_blender.subprocess.Popen"):
                inject_camera_and_open(
                    blender_exe="/usr/bin/blender",
                    blend_path=str(blend),
                    keyframes=[kf],
                )

        kf_path = tmp_path / "camera_keyframes.json"
        data = json.loads(kf_path.read_text())
        entry = data[0]
        assert entry["frame"] == 42
        assert entry["x"] == pytest.approx(100.0)
        assert entry["is_pause"] is True

    def test_empty_keyframes_writes_empty_json(self, tmp_path):
        blend = tmp_path / "scene.blend"
        blend.write_text("blend")
        out_blend = tmp_path / "scene_with_camera.blend"
        out_blend.write_text("blend_with_camera")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("georeel.core.open_in_blender.subprocess.run", mock_run):
            with patch("georeel.core.open_in_blender.subprocess.Popen"):
                inject_camera_and_open(
                    blender_exe="/usr/bin/blender",
                    blend_path=str(blend),
                    keyframes=[],
                )

        kf_path = tmp_path / "camera_keyframes.json"
        data = json.loads(kf_path.read_text())
        assert data == []
