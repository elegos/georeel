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
    # Landscape (16:9)
    "720p":  (1280,  720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k":    (3840, 2160),
    # Portrait (9:16)
    "portrait_720p":  ( 720, 1280),
    "portrait_1080p": (1080, 1920),
    "portrait_1440p": (1440, 2560),
    "portrait_4k":    (2160, 3840),
    # Square (1:1)
    "square_720":  ( 720,  720),
    "square_1080": (1080, 1080),
    "square_1440": (1440, 1440),
    "square_2160": (2160, 2160),
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

    # Consecutive pause blocks form a carousel: each photo is shown for its
    # own duration and photos cross-fade into each other.  Terrain fades only
    # appear at the very first and very last boundary of a run.
    runs = _group_into_runs(blocks)

    photo_cache: dict[str, Image.Image] = {}

    done = 0
    for run in runs:
        if cancel_check and cancel_check():
            raise CompositorError("Compositing cancelled.")

        if not run[0]["is_pause"]:
            # Regular fly-through frames — copy as-is
            for block in run:
                for frame_num in block["frames"]:
                    src = src_dir / f"{frame_num - 1:06d}.png"
                    if src.exists():
                        shutil.copy(src, out_dir / src.name)
                    done += 1
                    if progress_cb:
                        progress_cb(done, total)
        else:
            done = _composite_pause_run(
                run, src_dir, out_dir, out_w, out_h, fill,
                transition, fade_frames, photo_cache,
                done, total, progress_cb,
            )

    return str(out_dir)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _group_into_runs(blocks: list[dict]) -> list[list[dict]]:
    """Group consecutive pause blocks into carousel runs.

    Non-pause blocks are each their own single-element run.
    """
    runs: list[list[dict]] = []
    i = 0
    while i < len(blocks):
        if blocks[i]["is_pause"]:
            run = [blocks[i]]
            while i + 1 < len(blocks) and blocks[i + 1]["is_pause"]:
                i += 1
                run.append(blocks[i])
            runs.append(run)
        else:
            runs.append([blocks[i]])
        i += 1
    return runs


def _composite_pause_run(
    run: list[dict],
    src_dir: Path, out_dir: Path,
    out_w: int, out_h: int, fill: str,
    transition: str, fade_frames: int,
    photo_cache: dict[str, Image.Image],
    done: int, total: int,
    progress_cb,
) -> int:
    """Composite a run of consecutive pause blocks as a photo carousel.

    - Terrain fade-in applies only at the start of the first photo.
    - Terrain fade-out applies only at the end of the last photo.
    - Between photos a cross-fade is used: the last *fade_frames* of photo A
      blend A→B so that B is already fully visible at the start of its block.
    """
    # Preload all photos in the run
    for block in run:
        photo_path = block["photo_path"]
        if photo_path and photo_path not in photo_cache:
            photo_cache[photo_path] = _fit_photo(
                Image.open(photo_path), out_w, out_h, fill
            )

    n_photos = len(run)

    for p_idx, block in enumerate(run):
        photo_path = block["photo_path"]
        frame_nums = block["frames"]
        n = len(frame_nums)
        is_first = p_idx == 0
        is_last  = p_idx == n_photos - 1

        photo_img = photo_cache.get(photo_path) if photo_path else None

        if photo_img is None or n == 0:
            for frame_num in frame_nums:
                src = src_dir / f"{frame_num - 1:06d}.png"
                if src.exists():
                    shutil.copy(src, out_dir / src.name)
                done += 1
                if progress_cb:
                    progress_cb(done, total)
            continue

        actual_fade = min(fade_frames, n // 2) if transition == "fade" else 0

        next_photo_img: Image.Image | None = None
        if not is_last and actual_fade > 0:
            next_path = run[p_idx + 1]["photo_path"]
            next_photo_img = photo_cache.get(next_path) if next_path else None

        for j, frame_num in enumerate(frame_nums):
            src_path = src_dir / f"{frame_num - 1:06d}.png"

            if is_first and actual_fade > 0 and j < actual_fade:
                # Terrain → first photo fade-in
                alpha = (j + 1) / (actual_fade + 1)
                terrain = _load_rgb(src_path, out_w, out_h)
                frame_img = Image.blend(terrain, photo_img, alpha)

            elif not is_last and actual_fade > 0 and j >= n - actual_fade and next_photo_img is not None:
                # Cross-fade current photo → next photo
                alpha = (j - (n - actual_fade) + 1) / (actual_fade + 1)
                frame_img = Image.blend(photo_img, next_photo_img, alpha)

            elif is_last and actual_fade > 0 and j >= n - actual_fade:
                # Last photo → terrain fade-out
                steps_from_end = n - j
                alpha = steps_from_end / (actual_fade + 1)
                terrain = _load_rgb(src_path, out_w, out_h)
                frame_img = Image.blend(terrain, photo_img, alpha)

            else:
                frame_img = photo_img

            frame_img.save(out_dir / f"{frame_num - 1:06d}.png")
            done += 1
            if progress_cb:
                progress_cb(done, total)

    return done


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
