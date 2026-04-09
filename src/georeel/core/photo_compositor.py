"""
Stage 8 — Photo Overlay Compositor.

Reads the rendered fly-through frame sequence, replaces pause-frame blocks
with composited full-screen photo frames, and writes the result to a new
directory.

Pause blocks are identified from pipeline.camera_keyframes (is_pause=True).
Each consecutive run of pause keyframes sharing the same photo_path is one
block; fade-in / fade-out are applied at its boundaries when transition="fade".
Photo clusters at the same waypoint are shown as a carousel with cross-fades
between photos.

Frame processing is parallelised across os.cpu_count() worker processes.
"""

import io
import logging
import os
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import groupby
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter, ImageOps

from .camera_keyframe import CameraKeyframe
from .pipeline import Pipeline

_log = logging.getLogger(__name__)

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


# ══════════════════════════════════════════════════════════════════════
# Worker-side globals (populated once per worker process by the
# pool initialiser; never touched by the main process after fork/spawn)
# ══════════════════════════════════════════════════════════════════════

_WORKER_CACHE: dict[str, Image.Image] = {}


def _init_worker_cache(photo_bytes: dict[str, bytes]) -> None:
    """Deserialise pre-fitted photos into each worker process once."""
    global _WORKER_CACHE
    _WORKER_CACHE = {
        key: Image.open(io.BytesIO(data)).copy()
        for key, data in photo_bytes.items()
    }


def _process_frame_task(task: dict) -> int | None:
    """Process one frame.

    Returns frame_num if a required source file was missing, else None.

    Operations (task["op"]):
      "copy"      — copy terrain frame as-is
      "photo"     — write the fitted photo directly (no terrain)
      "fade_in"   — blend terrain → photo at alpha
      "fade_out"  — blend photo → terrain at alpha
      "crossfade" — blend current photo → next photo at alpha
    """
    frame_num = task["frame_num"]
    src_path  = Path(task["src_path"])
    out_path  = Path(task["out_path"])
    op        = task["op"]

    if op == "copy":
        if src_path.exists():
            shutil.copy(src_path, out_path)
            return None
        return frame_num

    photo_key = task.get("photo_key")
    photo_img = _WORKER_CACHE.get(photo_key) if photo_key else None

    if photo_img is None:
        # No photo available — fall back to copying the terrain frame
        if src_path.exists():
            shutil.copy(src_path, out_path)
            return None
        return frame_num

    if op == "photo":
        photo_img.save(str(out_path))
        return None

    if op == "crossfade":
        next_key = task.get("next_photo_key")
        next_img = _WORKER_CACHE.get(next_key) if next_key else None
        if next_img is not None:
            Image.blend(photo_img, next_img, task["alpha"]).save(str(out_path))
        else:
            photo_img.save(str(out_path))
        return None

    # fade_in / fade_out both require the terrain frame
    if not src_path.exists():
        photo_img.save(str(out_path))
        return frame_num

    out_w, out_h = task["out_w"], task["out_h"]
    terrain = Image.open(str(src_path)).convert("RGB")
    if terrain.size != (out_w, out_h):
        terrain = terrain.resize((out_w, out_h), Image.LANCZOS)
    Image.blend(terrain, photo_img, task["alpha"]).save(str(out_path))
    return None


# ══════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════

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

    resolution  = settings.get("render/resolution",       "1080p")
    transition  = settings.get("render/photo_transition", "fade")
    fill        = settings.get("render/photo_fill",       "blurred")
    fps         = int(settings.get("render/fps",          30))
    fade_dur    = float(settings.get("render/photo_fade_duration", 0.5))
    fade_frames = max(1, round(fade_dur * fps))

    out_w, out_h = _RESOLUTIONS.get(resolution, (1920, 1080))

    comp_work_dir = Path(tempfile.mkdtemp(prefix="georeel_comp_"))
    pipeline._temp_dirs.append(comp_work_dir)
    out_dir = comp_work_dir / "frames"
    out_dir.mkdir()

    src_frames = sorted(src_dir.glob("*.png"), key=lambda p: int(p.stem))
    total = len(src_frames)
    if total == 0:
        raise CompositorError("No rendered frames found in source directory.")

    # ── Build block / run structure ──────────────────────────────────
    blocks = _build_blocks(pipeline.camera_keyframes)
    n_pause_blocks     = sum(1 for b in blocks if b["is_pause"])
    n_fly_blocks       = sum(1 for b in blocks if not b["is_pause"])
    pause_frames_total = sum(len(b["frames"]) for b in blocks if b["is_pause"])
    _log.debug(
        "Compositor: %d keyframes → %d blocks (%d pause, %d fly-through); "
        "%d pause frames, %d fly frames",
        total, len(blocks), n_pause_blocks, n_fly_blocks,
        pause_frames_total, total - pause_frames_total,
    )

    max_gap = max(1, fade_frames * 2)
    blocks, n_gaps_absorbed = _absorb_photo_gaps(blocks, max_gap=max_gap)
    if n_gaps_absorbed:
        _log.debug(
            "Compositor: absorbed %d short fly-through gap(s) (≤%d frames each) "
            "between consecutive photo blocks",
            n_gaps_absorbed, max_gap,
        )

    runs = _group_into_runs(blocks)
    carousel_runs = [r for r in runs if r[0]["is_pause"] and len(r) > 1]
    if carousel_runs:
        _log.debug(
            "Compositor: %d carousel run(s) with multiple photos (%s photos each)",
            len(carousel_runs),
            ", ".join(str(len(r)) for r in carousel_runs),
        )

    # ── Pre-fit all unique photos in the main process ────────────────
    # Serialise to PNG bytes so they can be sent once to each worker
    # via the pool initialiser without re-fitting per frame.
    unique_photo_paths: set[str] = {
        block["photo_path"]
        for run in runs
        for block in run
        if run[0]["is_pause"] and block["photo_path"]
    }
    photo_bytes: dict[str, bytes] = {}
    for photo_path in unique_photo_paths:
        fitted = _fit_photo(Image.open(photo_path), out_w, out_h, fill)
        buf = io.BytesIO()
        fitted.save(buf, format="PNG")
        photo_bytes[photo_path] = buf.getvalue()
    if photo_bytes:
        _log.debug("Compositor: pre-fitted %d unique photo(s)", len(photo_bytes))

    # ── Build flat task list (one entry per output frame) ────────────
    tasks = _build_frame_tasks(runs, src_dir, out_dir, out_w, out_h, transition, fade_frames)

    # ── Dispatch to worker pool ──────────────────────────────────────
    n_workers = max(1, os.cpu_count() or 1)
    _log.debug(
        "Compositor: processing %d frames with %d worker process(es)",
        len(tasks), n_workers,
    )

    missing_frames: list[int] = []
    done = 0

    with ProcessPoolExecutor(
        max_workers=n_workers,
        initializer=_init_worker_cache,
        initargs=(photo_bytes,),
    ) as executor:
        futures = [executor.submit(_process_frame_task, task) for task in tasks]
        for future in as_completed(futures):
            if cancel_check and cancel_check():
                for f in futures:
                    f.cancel()
                raise CompositorError("Compositing cancelled.")
            missing = future.result()
            if missing is not None:
                missing_frames.append(missing)
            done += 1
            if progress_cb:
                progress_cb(done, total)

    # ── Final summary ────────────────────────────────────────────────
    out_frames = len(list(out_dir.glob("*.png")))
    if missing_frames:
        missing_frames.sort()
        _log.warning(
            "Compositor: %d source frame(s) were missing and skipped "
            "(first missing: frame %d). Output has %d/%d frames — "
            "the video may be shorter than expected.",
            len(missing_frames), missing_frames[0], out_frames, total,
        )
    elif out_frames == total:
        _log.info(
            "Compositor: complete — %d/%d frames written with no gaps.",
            out_frames, total,
        )
    else:
        _log.warning(
            "Compositor: output has %d frames but %d were expected. "
            "The video may be shorter than expected.",
            out_frames, total,
        )

    return str(out_dir)


# ══════════════════════════════════════════════════════════════════════
# Task builder
# ══════════════════════════════════════════════════════════════════════

def _build_frame_tasks(
    runs: list[list[dict]],
    src_dir: Path,
    out_dir: Path,
    out_w: int, out_h: int,
    transition: str,
    fade_frames: int,
) -> list[dict]:
    """Return a flat, ordered list of per-frame task dicts."""
    tasks: list[dict] = []

    for run in runs:
        if not run[0]["is_pause"]:
            for block in run:
                for frame_num in block["frames"]:
                    tasks.append({
                        "frame_num":      frame_num,
                        "src_path":       str(src_dir / f"{frame_num - 1:06d}.png"),
                        "out_path":       str(out_dir / f"{frame_num - 1:06d}.png"),
                        "op":             "copy",
                        "photo_key":      None,
                        "next_photo_key": None,
                        "alpha":          0.0,
                        "out_w":          out_w,
                        "out_h":          out_h,
                    })
            continue

        n_photos = len(run)
        for p_idx, block in enumerate(run):
            photo_path = block["photo_path"]
            frame_nums = block["frames"]
            n          = len(frame_nums)
            is_first   = p_idx == 0
            is_last    = p_idx == n_photos - 1

            if not photo_path or n == 0:
                for frame_num in frame_nums:
                    tasks.append({
                        "frame_num":      frame_num,
                        "src_path":       str(src_dir / f"{frame_num - 1:06d}.png"),
                        "out_path":       str(out_dir / f"{frame_num - 1:06d}.png"),
                        "op":             "copy",
                        "photo_key":      None,
                        "next_photo_key": None,
                        "alpha":          0.0,
                        "out_w":          out_w,
                        "out_h":          out_h,
                    })
                continue

            actual_fade    = min(fade_frames, n // 2) if transition == "fade" else 0
            next_photo_key = run[p_idx + 1]["photo_path"] if not is_last else None

            for j, frame_num in enumerate(frame_nums):
                if is_first and actual_fade > 0 and j < actual_fade:
                    op    = "fade_in"
                    alpha = (j + 1) / (actual_fade + 1)
                    nk    = None
                elif not is_last and actual_fade > 0 and j >= n - actual_fade and next_photo_key:
                    op    = "crossfade"
                    alpha = (j - (n - actual_fade) + 1) / (actual_fade + 1)
                    nk    = next_photo_key
                elif is_last and actual_fade > 0 and j >= n - actual_fade:
                    op    = "fade_out"
                    alpha = (n - j) / (actual_fade + 1)
                    nk    = None
                else:
                    op    = "photo"
                    alpha = 0.0
                    nk    = None

                tasks.append({
                    "frame_num":      frame_num,
                    "src_path":       str(src_dir / f"{frame_num - 1:06d}.png"),
                    "out_path":       str(out_dir / f"{frame_num - 1:06d}.png"),
                    "op":             op,
                    "photo_key":      photo_path,
                    "next_photo_key": nk,
                    "alpha":          alpha,
                    "out_w":          out_w,
                    "out_h":          out_h,
                })

    return tasks


# ══════════════════════════════════════════════════════════════════════
# Block / run helpers
# ══════════════════════════════════════════════════════════════════════

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


def _absorb_photo_gaps(blocks: list[dict], max_gap: int) -> tuple[list[dict], int]:
    """Absorb short fly-through gaps between consecutive photo pause blocks.

    When two photo blocks are separated by ≤ *max_gap* fly frames, those
    terrain frames are folded into the preceding photo block so the map
    never flashes between photos.

    Returns (updated blocks list, number of gaps absorbed).
    """
    result: list[dict] = []
    absorbed = 0
    i = 0
    while i < len(blocks):
        current = blocks[i]
        while (
            current["is_pause"]
            and i + 2 < len(blocks)
            and not blocks[i + 1]["is_pause"]
            and blocks[i + 2]["is_pause"]
            and len(blocks[i + 1]["frames"]) <= max_gap
        ):
            current = dict(current)
            current["frames"] = current["frames"] + blocks[i + 1]["frames"]
            i += 1
            absorbed += 1
        result.append(current)
        i += 1
    return result, absorbed


# ══════════════════════════════════════════════════════════════════════
# Image helpers (main-process side only)
# ══════════════════════════════════════════════════════════════════════

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
