"""
Preview Video generator.

Renders a sparse sample of the camera path — 11 clips at 0%, 10%, 20%, … 100%
of the track, each covering approximately 2% of the total frame count — and
assembles them into a short MP4 preview video.

Original frame numbers are preserved so the Build modifier animation state
(ribbon revealed so far) is correct at each clip position.  After rendering,
the files are renamed to a sequential series for ffmpeg.

Reuses render_frames.py and assemble_video without modification.
"""

import tempfile
from pathlib import Path
from typing import Callable

from .camera_keyframe import CameraKeyframe
from .frame_renderer import FrameRenderError, render_frames
from .pipeline import Pipeline
from .video_assembler import VideoAssembleError, assemble_video

_PREVIEW_FRACTION = 0.02   # render the first 2 % of total frames (minimum 2)


class PreviewVideoError(Exception):
    pass


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------

def build_preview_keyframes(keyframes: list[CameraKeyframe]) -> list[CameraKeyframe]:
    """Return the first 2 % of keyframes (minimum 2) as the preview clip."""
    n = len(keyframes)
    count = max(2, round(n * _PREVIEW_FRACTION))
    return keyframes[:count]


def render_preview_video(
    pipeline: Pipeline,
    settings: dict,
    output_path: str,
    blender_exe: str | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Render a preview video to *output_path* and return the path.

    Uses a shallow copy of *pipeline* with only the preview keyframes so the
    existing render_frames / assemble_video machinery is reused unchanged.

    Settings overrides for speed:
      • resolution: 720p
      • quality:    low
      • encoder:    libx264 (universal; fast)
      • container:  mp4
    """
    if not pipeline.camera_keyframes:
        raise PreviewVideoError("Camera keyframes are required (run stage 6 first).")
    if not pipeline.scene:
        raise PreviewVideoError("Blender scene is required (run stages 1–5 first).")

    preview_kfs = build_preview_keyframes(pipeline.camera_keyframes)

    # Shallow pipeline clone with only the preview keyframes
    preview_pipeline = Pipeline()
    preview_pipeline.scene = pipeline.scene
    preview_pipeline.camera_keyframes = preview_kfs

    # Fast, low-res settings
    preview_settings = dict(settings)
    preview_settings["render/resolution"] = "720p"
    preview_settings["render/quality"]    = "low"

    # ------------------------------------------------------------------ #
    # Stage 7: render preview frames                                       #
    # ------------------------------------------------------------------ #
    try:
        frames_dir = render_frames(
            preview_pipeline, preview_settings,
            blender_exe=blender_exe,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
        )
    except FrameRenderError as e:
        raise PreviewVideoError(f"Frame render failed: {e}") from e

    if cancel_check and cancel_check():
        return ""

    total_frames = len(preview_pipeline.camera_keyframes)

    # ------------------------------------------------------------------ #
    # Stage 9: assemble using the user's chosen encoder                   #
    # ------------------------------------------------------------------ #
    assemble_settings = dict(preview_settings)
    assemble_settings["output/container"] = "mp4"

    try:
        assemble_video(
            frames_dir=frames_dir,
            output_path=output_path,
            settings=assemble_settings,
            total_frames=total_frames,
            gpx_path=None,
            progress_cb=None,
            cancel_check=cancel_check,
        )
    except VideoAssembleError as e:
        raise PreviewVideoError(f"Video assembly failed: {e}") from e

    return output_path
