"""Preview frame-limit helpers.

``build_preview_keyframes`` computes how many keyframes to include in a
preview render (the leading slice of the full keyframe list that covers
roughly 2 % of the total duration plus any clip-effects padding).

The actual rendering is done by the server pipeline (render_frames →
compositor → video_assemble) via ``PreviewPipelineDialog``, using
``render/frame_limit`` in settings so that preview and final render go
through exactly the same code path.
"""

from typing import Any

from .camera_keyframe import CameraKeyframe

_PREVIEW_FRACTION = 0.02          # render the first 2 % of total frames (minimum 2)
_PREVIEW_MIN_CONTENT_S = 3.0      # seconds of post-fade content always visible in preview


class PreviewVideoError(Exception):
    pass


def build_preview_keyframes(
    keyframes: list[CameraKeyframe],
    settings: dict[str, Any] | None = None,
) -> list[CameraKeyframe]:
    """Return the first N keyframes that form the preview clip.

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
            extra += round((fi_black + fi_fade + _PREVIEW_MIN_CONTENT_S) * fps)
        # fade-out is suppressed in the preview (see _show_preview_video),
        # so no extra frames are needed for it here.

    count = min(base + extra, n)
    return keyframes[:count]
