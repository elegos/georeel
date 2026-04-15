"""
Injects camera keyframes into the scene .blend and opens the result in
Blender interactively.

The original .blend is never modified; the camera is written into a sibling
file  scene_preview.blend  in the same temp directory.
"""

import json
import shlex
import subprocess
from pathlib import Path

from .camera_keyframe import CameraKeyframe

_INJECT_SCRIPT = Path(__file__).parent / "blender_scripts" / "inject_camera.py"
_SETUP_VIEWPORT_SCRIPT = Path(__file__).parent / "blender_scripts" / "setup_viewport.py"


class OpenInBlenderError(Exception):
    pass


def inject_camera_and_open(
    blender_exe: str,
    blend_path: str,
    keyframes: list[CameraKeyframe],
    resolution: str = "1080p",
    fps: int = 30,
) -> None:
    """Write keyframes into a sibling .blend and open it in Blender.

    The injection step runs headlessly (fast — no rendering).  Once the
    preview .blend is saved, Blender is launched interactively (no
    --background) so the user sees the full UI.

    Keyframes are subsampled at one keyframe per second (stride=fps) and
    Blender interpolates between them, which is far faster than inserting
    a keyframe for every single frame.
    """
    blend = Path(blend_path)
    out_blend = blend.with_name("scene_with_camera.blend")
    kf_path = blend.with_name("camera_keyframes.json")

    # Write keyframes JSON (include is_pause so inject_camera.py can apply
    # CONSTANT interpolation during photo-pause segments)
    kf_data = [
        {
            "frame": kf.frame,
            "x": kf.x,
            "y": kf.y,
            "z": kf.z,
            "look_at_x": kf.look_at_x,
            "look_at_y": kf.look_at_y,
            "look_at_z": kf.look_at_z,
            "is_pause": kf.is_pause,
        }
        for kf in keyframes
    ]
    kf_path.write_text(json.dumps(kf_data))

    # Run inject_camera.py headlessly to produce scene_with_camera.blend.
    # No timeout: the async _InjectWorker in main_window.py already runs this
    # in a background thread, and injection is now fast thanks to subsampling.
    cmd = [
        blender_exe,
        "--background",
        str(blend),
        "--python",
        str(_INJECT_SCRIPT),
        "--",
        str(kf_path),
        str(out_blend),
        resolution,
        str(fps),
    ]

    result = subprocess.run(
        shlex.join(cmd),
        capture_output=True,
        text=True,
        shell=True,
    )

    if result.returncode != 0 or not out_blend.is_file():
        tail = (result.stderr + result.stdout)[-2000:]
        raise OpenInBlenderError(
            f"Camera injection failed (exit {result.returncode}).\n{tail}"
        )

    # Open the result interactively (non-blocking).
    # setup_viewport.py fires via a timer once the UI event loop is ready,
    # switching every 3D viewport to Material Preview / camera view and
    # pre-selecting FlyCamera in the scene collection.
    subprocess.Popen(
        [
            blender_exe,
            str(out_blend),
            "--python",
            str(_SETUP_VIEWPORT_SCRIPT),
        ]
    )
