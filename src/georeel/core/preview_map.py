"""
Preview Map renderer.

Renders a single top-down frame from the existing scene .blend using a
dedicated Blender script.  The result is a PNG file in the same temp
directory as the .blend.
"""

import logging
import shlex
import subprocess
from pathlib import Path

from .blender_runtime import find_blender

_log = logging.getLogger(__name__)

_PREVIEW_SCRIPT = Path(__file__).parent / "blender_scripts" / "render_preview.py"
_TIMEOUT_SECONDS = 120


class PreviewMapError(Exception):
    pass


_PREVIEW_SCALE = 3  # render at 3× the display resolution for sharpness


def render_preview_map(
    blend_path: str,
    blender_exe: str | None = None,
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Render a top-down preview frame from *blend_path*.

    Returns the absolute path to the output PNG.
    """
    exe = find_blender(blender_exe)
    if exe is None:
        raise PreviewMapError(
            "Blender executable not found. "
            "Install Blender or set the path via Options → Blender…"
        )

    out_path = str(Path(blend_path).parent / "preview_map.png")

    cmd = [
        exe,
        "--background",
        blend_path,
        "--python",
        str(_PREVIEW_SCRIPT),
        "--",
        out_path,
        str(width * _PREVIEW_SCALE),
        str(height * _PREVIEW_SCALE),
    ]

    try:
        result = subprocess.run(
            shlex.join(cmd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            shell=True,
        )
    except subprocess.TimeoutExpired:
        raise PreviewMapError(f"Blender timed out after {_TIMEOUT_SECONDS} seconds.")

    blender_output = (result.stderr or "") + (result.stdout or "")
    if blender_output:
        _log.debug("Blender preview output:\n%s", blender_output)

    if result.returncode != 0 or not Path(out_path).is_file():
        _log.error(
            "Preview render failed (exit %d):\n%s", result.returncode, blender_output
        )
        tail = blender_output[-2000:]
        raise PreviewMapError(
            f"Preview render failed (exit {result.returncode}).\n{tail}"
        )

    return out_path
