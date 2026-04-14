"""Tests for georeel.core.preview_map."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from georeel.core.preview_map import PreviewMapError, render_preview_map


class TestRenderPreviewMap:
    def test_blender_not_found_raises(self):
        with patch("georeel.core.preview_map.find_blender", return_value=None):
            with pytest.raises(PreviewMapError, match="[Bb]lender"):
                render_preview_map("/fake/scene.blend")

    def test_successful_render_returns_path(self, tmp_path):
        blend_path = str(tmp_path / "scene.blend")
        out_png = tmp_path / "preview_map.png"
        out_png.write_bytes(b"PNG")  # simulate Blender creating the file

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("georeel.core.preview_map.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.preview_map.subprocess.run", return_value=mock_result):
                result = render_preview_map(blend_path)

        assert result == str(out_png)

    def test_nonzero_returncode_raises(self, tmp_path):
        blend_path = str(tmp_path / "scene.blend")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "error"
        mock_result.stderr = "blender crash"

        with patch("georeel.core.preview_map.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.preview_map.subprocess.run", return_value=mock_result):
                with pytest.raises(PreviewMapError, match="failed"):
                    render_preview_map(blend_path)

    def test_output_file_missing_raises(self, tmp_path):
        """Even if returncode==0, missing output file should raise."""
        blend_path = str(tmp_path / "scene.blend")
        # Do NOT create the expected output PNG

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("georeel.core.preview_map.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.preview_map.subprocess.run", return_value=mock_result):
                with pytest.raises(PreviewMapError):
                    render_preview_map(blend_path)

    def test_timeout_raises(self, tmp_path):
        blend_path = str(tmp_path / "scene.blend")

        with patch("georeel.core.preview_map.find_blender", return_value="/usr/bin/blender"):
            with patch(
                "georeel.core.preview_map.subprocess.run",
                side_effect=subprocess.TimeoutExpired("blender", 120),
            ):
                with pytest.raises(PreviewMapError, match="timed out"):
                    render_preview_map(blend_path)

    def test_custom_resolution_passed(self, tmp_path):
        blend_path = str(tmp_path / "scene.blend")
        out_png = tmp_path / "preview_map.png"
        out_png.write_bytes(b"PNG")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            return mock_result

        with patch("georeel.core.preview_map.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.preview_map.subprocess.run", side_effect=fake_run):
                render_preview_map(blend_path, width=1280, height=720)

        # Verify the command string contains the scaled resolution values
        assert captured_cmd
        cmd_str = captured_cmd[0]
        assert "3840" in cmd_str  # 1280 * 3
        assert "2160" in cmd_str  # 720 * 3
