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

from typing import Any, Callable

from .camera_keyframe import CameraKeyframe
from .frame_renderer import FrameRenderError, render_frames
from .photo_compositor import CompositorError, composite_photos
from .pipeline import Pipeline
from .video_assembler import VideoAssembleError, assemble_video

_PREVIEW_FRACTION = 0.02  # render the first 2 % of total frames (minimum 2)
_PREVIEW_MIN_CONTENT_S = 3.0  # seconds of post-fade content always visible in preview


class PreviewVideoError(Exception):
    pass


# ------------------------------------------------------------------
# Public helpers
# ------------------------------------------------------------------


def build_preview_keyframes(
    keyframes: list[CameraKeyframe],
    settings: dict[str, Any] | None = None,
) -> list[CameraKeyframe]:
    """Return the first N keyframes as the preview clip.

    The base count is 2 % of the total (minimum 2).  When clip effects are
    active, extra frames are added so that the full fade transition is visible
    and at least *_PREVIEW_MIN_CONTENT_S* seconds of clean content follow it.

    - Fade-in: fi_black is added by ffmpeg's tpad (no extra rendered frames
      needed for the black part), but fi_fade overlaps the start of rendered
      content, so fi_fade + _PREVIEW_MIN_CONTENT_S extra seconds are rendered.
    - Fade-out: fo_fade overlaps the end of rendered content; fo_black is added
      by tpad. fo_fade extra seconds are rendered so the transition is visible.
    """
    n = len(keyframes)
    fps = int((settings or {}).get("render/fps", 30))
    base = max(2, round(n * _PREVIEW_FRACTION))

    extra = 0
    if settings:
        if settings.get("clip_effects/fade_in_enabled", False):
            fi_black = float(settings.get("clip_effects/fade_in_black_dur", 5.0))
            fi_fade = float(settings.get("clip_effects/fade_in_fade_dur", 1.0))
            # fi_black from tpad doesn't consume rendered frames, but we extend
            # the base by fi_black too so the preview is proportionally longer
            # and the user can see content well after the fade completes.
            extra += round((fi_black + fi_fade + _PREVIEW_MIN_CONTENT_S) * fps)
        # fade-out is suppressed in the preview (see assemble_settings below),
        # so no extra frames are needed for it here.

    count = min(base + extra, n)
    return keyframes[:count]


def render_preview_video(
    pipeline: Pipeline,
    settings: dict[str, Any],
    output_path: str,
    blender_exe: str | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    title_progress_cb: Callable[[int, int], None] | None = None,
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

    preview_kfs = build_preview_keyframes(pipeline.camera_keyframes, settings)
    is_full_video = len(preview_kfs) == len(pipeline.camera_keyframes)

    # Shallow pipeline clone with only the preview keyframes
    preview_pipeline = Pipeline()
    preview_pipeline.scene = pipeline.scene
    preview_pipeline.camera_keyframes = preview_kfs
    preview_pipeline.match_results = pipeline.match_results

    # Fast, low-res settings — pick the smallest resolution that matches the
    # user's chosen aspect ratio so portrait/square previews keep their shape.
    _PREVIEW_RESOLUTION = {
        "landscape": "720p",
        "portrait": "portrait_720p",
        "square": "square_720",
    }
    aspect = settings.get("render/aspect_ratio", "landscape")
    preview_settings = dict(settings)
    preview_settings["render/resolution"] = _PREVIEW_RESOLUTION.get(aspect, "720p")
    preview_settings["render/quality"] = "low"

    # ------------------------------------------------------------------ #
    # Stage 7: render preview frames                                       #
    # ------------------------------------------------------------------ #
    try:
        frames_dir = render_frames(
            preview_pipeline,
            preview_settings,
            blender_exe=blender_exe,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
        )
    except FrameRenderError as e:
        raise PreviewVideoError(f"Frame render failed: {e}") from e

    if cancel_check and cancel_check():
        return ""

    # ------------------------------------------------------------------ #
    # Stage 8: composite photo overlays                                    #
    # ------------------------------------------------------------------ #
    preview_pipeline.rendered_frames_dir = frames_dir
    try:
        frames_dir = composite_photos(
            preview_pipeline,
            preview_settings,
            cancel_check=cancel_check,
        )
    except CompositorError as e:
        raise PreviewVideoError(f"Photo compositing failed: {e}") from e

    if cancel_check and cancel_check():
        return ""

    total_frames = len(preview_pipeline.camera_keyframes)

    # ------------------------------------------------------------------ #
    # Stage 9: assemble using the user's chosen encoder                   #
    # ------------------------------------------------------------------ #
    assemble_settings = dict(preview_settings)
    assemble_settings["output/container"] = "mp4"
    # The preview is a leading clip — it never reaches the end of the full
    # video, so the fade-out must not fire (its timing is relative to the
    # full-video duration, not the preview duration).
    assemble_settings["clip_effects/fade_out_enabled"] = False
    # Music fade-out is relative to the full video end — suppress it unless
    # the preview happens to cover all frames (i.e. it IS the full video).
    if not is_full_video:
        assemble_settings["clip_effects/music_fade_out_enabled"] = False

    try:
        assemble_video(
            frames_dir=frames_dir,
            output_path=output_path,
            settings=assemble_settings,
            total_frames=total_frames,
            gpx_path=None,
            progress_cb=None,
            cancel_check=cancel_check,
            title_progress_cb=title_progress_cb,
        )
    except VideoAssembleError as e:
        raise PreviewVideoError(f"Video assembly failed: {e}") from e
    finally:
        preview_pipeline.cleanup()

    return output_path
