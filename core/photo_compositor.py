"""
Stage 8 — Photo Overlay Compositor.

Reads the rendered fly-through frame sequence, replaces pause-frame blocks
with composited full-screen photo frames, and writes the result to a new
directory.

Pause blocks are identified from pipeline.camera_keyframes (is_pause=True).
Each consecutive run of pause keyframes sharing the same photo_path is one
block; fade-in / fade-out are applied at its boundaries when transition="fade".
"""

import shutil
import tempfile
from itertools import groupby
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter, ImageOps

from .camera_keyframe import CameraKeyframe
from .pipeline import Pipeline

_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p":  (1280,  720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k":    (3840, 2160),
}


class CompositorError(Exception):
    pass


def composite_photos(
    pipeline: Pipeline,
    settings: dict,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Composite photo overlays onto the rendered frame sequence.

    Returns the path to the output directory containing composited PNGs.
    """
    if pipeline.rendered_frames_dir is None:
        raise CompositorError("Rendered frames directory is required (run frame renderer first).")
    if not pipeline.camera_keyframes:
        raise CompositorError("Camera keyframes are required.")

    src_dir = Path(pipeline.rendered_frames_dir)

    resolution   = settings.get("render/resolution",       "1080p")
    transition   = settings.get("render/photo_transition", "fade")
    fill         = settings.get("render/photo_fill",       "blurred")
    fps          = int(settings.get("render/fps",          30))
    fade_dur     = float(settings.get("render/photo_fade_duration", 0.5))
    fade_frames  = max(1, round(fade_dur * fps))

    out_w, out_h = _RESOLUTIONS.get(resolution, (1920, 1080))

    out_dir = Path(tempfile.mkdtemp(prefix="georeel_comp_")) / "frames"
    out_dir.mkdir()

    # Build frame-number → keyframe map
    kf_map: dict[int, CameraKeyframe] = {kf.frame: kf for kf in pipeline.camera_keyframes}

    # Collect sorted source frames
    src_frames = sorted(src_dir.glob("*.png"), key=lambda p: int(p.stem))
    total = len(src_frames)
    if total == 0:
        raise CompositorError("No rendered frames found in source directory.")

    # Group keyframes into pause / non-pause blocks, then collapse short
    # fly-through gaps between consecutive photo blocks so photos are shown
    # back-to-back without the terrain briefly reappearing in between.
    blocks = _build_blocks(pipeline.camera_keyframes)
    blocks = _absorb_photo_gaps(blocks, max_gap=max(1, fade_frames * 2))

    # Preload photo frames for each unique photo_path (resized once per photo)
    photo_cache: dict[str, Image.Image] = {}

    done = 0
    for block in blocks:
        if cancel_check and cancel_check():
            raise CompositorError("Compositing cancelled.")

        if not block["is_pause"]:
            # Regular fly-through frames — copy as-is
            for frame_num in block["frames"]:
                src = src_dir / f"{frame_num - 1:06d}.png"
                if src.exists():
                    shutil.copy(src, out_dir / src.name)
                done += 1
                if progress_cb:
                    progress_cb(done, total)
        else:
            photo_path = block["photo_path"]
            frame_nums = block["frames"]
            n = len(frame_nums)

            if photo_path is None or n == 0:
                # No photo associated — copy terrain frames
                for frame_num in frame_nums:
                    src = src_dir / f"{frame_num - 1:06d}.png"
                    if src.exists():
                        shutil.copy(src, out_dir / src.name)
                    done += 1
                    if progress_cb:
                        progress_cb(done, total)
                continue

            # Load and fit the photo
            if photo_path not in photo_cache:
                photo_cache[photo_path] = _fit_photo(
                    Image.open(photo_path), out_w, out_h, fill
                )
            photo_img = photo_cache[photo_path]

            # Determine fade range within this block
            if transition == "fade":
                actual_fade = min(fade_frames, n // 2)
            else:
                actual_fade = 0

            for i, frame_num in enumerate(frame_nums):
                src_path = src_dir / f"{frame_num - 1:06d}.png"

                if actual_fade > 0 and i < actual_fade:
                    # Fade in: blend terrain → photo
                    alpha = (i + 1) / (actual_fade + 1)
                    terrain = _load_rgb(src_path, out_w, out_h)
                    frame_img = Image.blend(terrain, photo_img, alpha)
                elif actual_fade > 0 and i >= n - actual_fade:
                    # Fade out: blend photo → terrain
                    steps_from_end = n - i
                    alpha = steps_from_end / (actual_fade + 1)
                    terrain = _load_rgb(src_path, out_w, out_h)
                    frame_img = Image.blend(terrain, photo_img, alpha)
                else:
                    # Full photo
                    frame_img = photo_img

                frame_img.save(out_dir / f"{frame_num - 1:06d}.png")
                done += 1
                if progress_cb:
                    progress_cb(done, total)

    return str(out_dir)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_blocks(keyframes: list[CameraKeyframe]) -> list[dict]:
    """Group consecutive keyframes into pause / non-pause blocks."""
    blocks = []
    for (is_pause, photo_path), group in groupby(
        keyframes,
        key=lambda kf: (kf.is_pause, kf.photo_path if kf.is_pause else None),
    ):
        frames = [kf.frame for kf in group]
        blocks.append({"is_pause": is_pause, "photo_path": photo_path, "frames": frames})
    return blocks


def _absorb_photo_gaps(blocks: list[dict], max_gap: int) -> list[dict]:
    """Absorb short fly-through gaps between consecutive photo pause blocks.

    When two photo blocks are separated by ≤ *max_gap* fly frames, those
    terrain frames are folded into the preceding photo block so the map
    never flashes between photos.  The compositor then shows photo A for
    the gap frames (fading into photo B as its block starts), giving a
    clean photo-to-photo cross-fade with no terrain in between.
    """
    result: list[dict] = []
    i = 0
    while i < len(blocks):
        current = blocks[i]
        # Repeatedly try to absorb the next fly gap into this pause block
        while (
            current["is_pause"]
            and i + 2 < len(blocks)
            and not blocks[i + 1]["is_pause"]
            and blocks[i + 2]["is_pause"]
            and len(blocks[i + 1]["frames"]) <= max_gap
        ):
            # Extend this pause block to cover the gap frames
            current = dict(current)
            current["frames"] = current["frames"] + blocks[i + 1]["frames"]
            i += 2  # consume current + fly gap; next iteration sees blocks[i+2]
        result.append(current)
        i += 1
    return result


def _fit_photo(photo: Image.Image, out_w: int, out_h: int, fill: str) -> Image.Image:
    """Return a correctly sized composite of the photo on its background."""
    photo = ImageOps.exif_transpose(photo).convert("RGB")

    if fill == "blurred":
        bg = ImageOps.fit(photo.copy(), (out_w, out_h), method=Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=out_h // 20))
    else:
        bg = Image.new("RGB", (out_w, out_h), (0, 0, 0))

    scaled = ImageOps.contain(photo, (out_w, out_h), method=Image.LANCZOS)
    x = (out_w - scaled.width)  // 2
    y = (out_h - scaled.height) // 2
    result = bg.copy()
    result.paste(scaled, (x, y))
    return result


def _load_rgb(path: Path, out_w: int, out_h: int) -> Image.Image:
    """Load a rendered frame, resizing only if dimensions mismatch."""
    img = Image.open(path).convert("RGB")
    if img.size != (out_w, out_h):
        img = img.resize((out_w, out_h), Image.LANCZOS)
    return img
