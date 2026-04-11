"""Tests for assemble_video with mocked subprocess (no real FFmpeg needed)."""

import pytest
from pathlib import Path
from PIL import Image
from unittest.mock import patch, MagicMock

from georeel.core.video_assembler import VideoAssembleError, assemble_video


def _write_frames(path: Path, count: int, size=(80, 60)):
    path.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        Image.new("RGB", size, (100, 150, 200)).save(path / f"{i:06d}.png")


def _fake_popen(output_file: Path, returncode: int = 0, stderr_lines=None):
    """Build a mock subprocess.Popen context that (optionally) creates an output file."""
    mock_proc = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.stderr = iter(stderr_lines or [])
    mock_proc.wait.return_value = None

    if returncode == 0:
        output_file.write_bytes(b"fake video data")

    return mock_proc


SETTINGS_BASE = {
    "output/encoder": "libx264",
    "output/container": "mp4",
    "render/fps": 24,
    "output/cq": 23,
    "output/preset": "medium",
}


class TestAssembleVideoMocked:
    def test_success_runs_to_completion(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 5)
        out = tmp_path / "output.mp4"

        mock_proc = _fake_popen(out, returncode=0)
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", return_value=mock_proc):
            assemble_video(
                frames_dir=str(frames),
                output_path=str(out),
                settings=SETTINGS_BASE,
                total_frames=5,
            )
        assert out.exists()

    def test_ffmpeg_nonzero_exit_raises(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 5)
        out = tmp_path / "output.mp4"

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = iter(["Error: failed\n"])
        mock_proc.wait.return_value = None

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(VideoAssembleError, match="[Ff][Ff]mpeg"):
                assemble_video(
                    frames_dir=str(frames),
                    output_path=str(out),
                    settings=SETTINGS_BASE,
                    total_frames=5,
                )

    def test_output_not_created_raises(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 5)
        out = tmp_path / "output.mp4"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = iter([])
        mock_proc.wait.return_value = None
        # Do NOT create the output file → should raise

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(VideoAssembleError, match="output"):
                assemble_video(
                    frames_dir=str(frames),
                    output_path=str(out),
                    settings=SETTINGS_BASE,
                    total_frames=5,
                )

    def test_popen_exception_wrapped(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 5)
        out = tmp_path / "output.mp4"

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", side_effect=OSError("no such file")):
            with pytest.raises(VideoAssembleError, match="FFmpeg"):
                assemble_video(
                    frames_dir=str(frames),
                    output_path=str(out),
                    settings=SETTINGS_BASE,
                    total_frames=5,
                )

    def test_progress_cb_called_on_frame_lines(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 10)
        out = tmp_path / "output.mp4"
        out.write_bytes(b"fake")

        calls = []
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = iter(["frame=   5 fps=30 q=23.0 size=1kB\n",
                                  "frame=  10 fps=30 q=23.0 size=2kB\n"])
        mock_proc.wait.return_value = None

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", return_value=mock_proc):
            assemble_video(
                frames_dir=str(frames),
                output_path=str(out),
                settings=SETTINGS_BASE,
                total_frames=10,
                progress_cb=lambda done, total: calls.append(done),
            )
        assert 5 in calls and 10 in calls

    def test_cancel_during_encoding_raises(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 10)
        out = tmp_path / "output.mp4"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = iter(["frame=   1 fps=30\n"])
        mock_proc.wait.return_value = None
        mock_proc.terminate.return_value = None

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(VideoAssembleError, match="[Cc]ancell"):
                assemble_video(
                    frames_dir=str(frames),
                    output_path=str(out),
                    settings=SETTINGS_BASE,
                    total_frames=10,
                    cancel_check=lambda: True,
                )

    def test_container_extension_corrected(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 3)
        out = tmp_path / "output.avi"  # wrong extension for mp4 container
        expected_out = tmp_path / "output.mp4"

        mock_proc = _fake_popen(expected_out, returncode=0)
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", return_value=mock_proc):
            assemble_video(
                frames_dir=str(frames),
                output_path=str(out),
                settings=SETTINGS_BASE,
                total_frames=3,
            )
        assert expected_out.exists()

    def test_with_fade_in_settings(self, tmp_path):
        frames = tmp_path / "frames"
        _write_frames(frames, 20)
        out = tmp_path / "output.mp4"
        out.write_bytes(b"fake")

        settings = dict(SETTINGS_BASE)
        settings["clip_effects/fade_in_enabled"] = True
        settings["clip_effects/fade_in_black_dur"] = 0.5
        settings["clip_effects/fade_in_fade_dur"] = 0.3

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = iter([])
        mock_proc.wait.return_value = None

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.Popen", return_value=mock_proc):
            assemble_video(
                frames_dir=str(frames),
                output_path=str(out),
                settings=settings,
                total_frames=20,
            )
        assert out.exists()
