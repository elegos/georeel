"""
Stage 7 — Frame Renderer.

Launches Blender headlessly to render each camera keyframe into a PNG
image sequence stored in a temporary directory.
"""

import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .blender_runtime import find_blender
from .camera_keyframe import CameraKeyframe
from .pipeline import Pipeline

_BLENDER_SCRIPT = Path(__file__).parent / "blender_scripts" / "render_frames.py"


class FrameRenderError(Exception):
    pass


def render_frames(
    pipeline: Pipeline,
    settings: dict,
    blender_exe: str | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Render the fly-through frame sequence.

    Returns the path to the directory containing the rendered PNG files.
    *progress_cb(current_frame, total_frames)* is called after each frame.
    *cancel_check()* is polled after each frame; returning True aborts.
    """
    if pipeline.scene is None:
        raise FrameRenderError("3D scene (.blend) is required (run scene builder first).")
    if not pipeline.camera_keyframes:
        raise FrameRenderError("Camera keyframes are required (run camera path generator first).")

    exe = find_blender(blender_exe)
    if exe is None:
        raise FrameRenderError(
            "Blender executable not found. "
            "Install Blender or download it via Options → Blender…"
        )

    engine     = settings.get("render/engine",     "eevee")
    resolution = settings.get("render/resolution", "1080p")
    quality    = settings.get("render/quality",    "medium")

    work_dir = Path(tempfile.mkdtemp(prefix="georeel_frames_"))
    pipeline._temp_dirs.append(work_dir)
    kf_path  = work_dir / "keyframes.json"
    out_dir  = work_dir / "frames"
    out_dir.mkdir()

    _write_keyframes(pipeline.camera_keyframes, kf_path)

    cmd = [
        exe,
        "--background", str(pipeline.scene),
        "--python", str(_BLENDER_SCRIPT),
        "--",
        str(kf_path),
        str(out_dir),
        engine,
        resolution,
        quality,
    ]

    total = len(pipeline.camera_keyframes)

    try:
        proc = subprocess.Popen(
            shlex.join(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=True,
        )

        # Blender's animation renderer prints "Fra:N Mem:…" once per render
        # pass per frame (rendering, compositing, saving).  We deduplicate
        # by frame number so the progress bar advances once per frame.
        last_reported_fra = -1
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("Fra:"):
                # Format: "Fra:N Mem:… | Time:… | …"
                try:
                    fra_num = int(line[4:].split()[0])
                    if fra_num != last_reported_fra:
                        last_reported_fra = fra_num
                        if progress_cb:
                            progress_cb(fra_num + 1, total)  # 0-based → 1-based count
                except (ValueError, IndexError):
                    pass

            if cancel_check and cancel_check():
                proc.terminate()
                proc.wait()
                raise FrameRenderError("Rendering cancelled.")

        proc.wait()
    except FrameRenderError:
        raise
    except Exception as e:
        raise FrameRenderError(f"Unexpected error: {e}") from e

    if proc.returncode != 0:
        raise FrameRenderError(
            f"Blender exited with code {proc.returncode}."
        )

    rendered = list(out_dir.glob("*.png"))
    if not rendered:
        raise FrameRenderError("Blender finished but no frames were written.")

    return str(out_dir)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_keyframes(keyframes: list[CameraKeyframe], path: Path) -> None:
    data = [
        {
            "frame":      kf.frame,
            "x":          kf.x,
            "y":          kf.y,
            "z":          kf.z,
            "look_at_x":  kf.look_at_x,
            "look_at_y":  kf.look_at_y,
            "look_at_z":  kf.look_at_z,
            "is_pause":   kf.is_pause,
            "photo_path": kf.photo_path,
        }
        for kf in keyframes
    ]
    path.write_text(json.dumps(data))
