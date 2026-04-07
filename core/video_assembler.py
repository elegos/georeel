"""
Stage 9 — Video Assembler.

Encodes the composited frame sequence into the final output video using FFmpeg.
"""

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .encoder_registry import EncoderConfig, get_encoder


class VideoAssembleError(Exception):
    pass


def assemble_video(
    frames_dir: str,
    output_path: str,
    settings: dict,
    total_frames: int,
    gpx_path: str | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Encode *frames_dir*/%06d.png → *output_path* using settings from QSettings dict."""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VideoAssembleError(
            "FFmpeg not found. Install FFmpeg and ensure it is in your PATH."
        )

    encoder_name = settings.get("output/encoder", "libx265")
    enc = get_encoder(encoder_name)
    if enc is None:
        raise VideoAssembleError(f"Unknown encoder '{encoder_name}'.")

    fps       = int(settings.get("render/fps", 30))
    cq        = int(settings.get("output/cq",  enc.default_cq))
    preset    = settings.get("output/preset",  enc.default_preset)
    container = settings.get("output/container", "mkv")

    # Ensure output extension matches container
    out = Path(output_path)
    expected_ext = f".{container}"
    if out.suffix.lower() != expected_ext:
        out = out.with_suffix(expected_ext)

    # Write settings JSON to a temp file so it can be attached for MKV
    settings_json = _serialise_settings(settings)
    tmp_settings = Path(tempfile.mktemp(suffix="_georeel_settings.json"))
    tmp_settings.write_text(settings_json, encoding="utf-8")

    cmd = (
        [ffmpeg, "-y",
         "-framerate", str(fps),
         "-i", str(Path(frames_dir) / "%06d.png"),
         "-c:v", enc.name]
        + _quality_args(enc, cq, preset)
        + _pix_fmt_args(enc)
        + _container_args(enc, container)
        + _attach_args(gpx_path, container)
        + _attach_settings_args(str(tmp_settings), container)
        + [str(out)]
    )

    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.debug("FFmpeg command: %s", shlex.join(cmd))

    stderr_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            shlex.join(cmd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )
        for line in proc.stderr:
            stderr_lines.append(line)
            # FFmpeg progress: "frame=  123 fps=..."
            m = re.search(r"frame=\s*(\d+)", line)
            if m and progress_cb:
                progress_cb(int(m.group(1)), total_frames)

            if cancel_check and cancel_check():
                proc.terminate()
                proc.wait()
                out.unlink(missing_ok=True)
                raise VideoAssembleError("Encoding cancelled.")

        proc.wait()
    except VideoAssembleError:
        raise
    except Exception as e:
        raise VideoAssembleError(f"FFmpeg error: {e}") from e
    finally:
        tmp_settings.unlink(missing_ok=True)

    stderr_tail = "".join(stderr_lines[-40:])
    _log.debug("FFmpeg output:\n%s", "".join(stderr_lines))

    if proc.returncode != 0:
        raise VideoAssembleError(
            f"FFmpeg exited with code {proc.returncode}.\n{stderr_tail}"
        )

    if not out.is_file():
        raise VideoAssembleError("FFmpeg finished but output file was not created.")

    _copy_gpx_alongside(gpx_path, out, container)
    _write_settings(settings, out, container)


# ------------------------------------------------------------------
# Command-line argument helpers
# ------------------------------------------------------------------

def _quality_args(enc: EncoderConfig, cq: int, preset: str) -> list[str]:
    args: list[str] = []

    # NVENC H.264/H.265 require VBR RC mode for CQ to work correctly
    if enc.hw_type == "nvenc" and enc.codec in ("h264", "h265"):
        args += ["-rc", "vbr", "-cq", str(cq)]
    elif enc.cq_flag:
        args += [enc.cq_flag, str(cq)]

    # SVT-AV1 / libaom need explicit bitrate=0 to engage CRF mode
    if enc.name in ("libsvtav1", "libaom-av1"):
        args += ["-b:v", "0"]

    if preset and enc.preset_flag:
        args += [enc.preset_flag, preset]

    return args


def _pix_fmt_args(enc: EncoderConfig) -> list[str]:
    # yuv420p is required for broad player compatibility on H.264/H.265;
    # AV1 software encoders also default to it for 8-bit output.
    return ["-pix_fmt", "yuv420p"]


_ATTACHMENT_CONTAINERS = {"mkv", "mp4"}


def _attach_args(gpx_path: str | None, container: str) -> list[str]:
    if not gpx_path or not Path(gpx_path).is_file():
        return []
    if container not in _ATTACHMENT_CONTAINERS:
        return []
    return [
        "-attach", gpx_path,
        "-metadata:s:t:0", "mimetype=application/gpx+xml",
        "-metadata:s:t:0", f"filename={Path(gpx_path).name}",
    ]


_SETTINGS_ATTACHMENT_CONTAINERS = {"mkv"}   # MP4 attachment support is unreliable for JSON


def _serialise_settings(settings: dict) -> str:
    """Return a pretty-printed JSON string of settings, excluding the API key."""
    safe = {k: v for k, v in settings.items() if k != "imagery/api_key"}
    return json.dumps(safe, indent=2, sort_keys=True, default=str)


def _attach_settings_args(settings_path: str, container: str) -> list[str]:
    if container not in _SETTINGS_ATTACHMENT_CONTAINERS:
        return []
    return [
        "-attach", settings_path,
        "-metadata:s:t:1", "mimetype=application/json",
        "-metadata:s:t:1", "filename=georeel_settings.json",
    ]


def _write_settings(settings: dict, out: Path, container: str) -> None:
    """For non-MKV containers write <stem>_settings.json next to the video."""
    if container in _SETTINGS_ATTACHMENT_CONTAINERS:
        return
    dest = out.with_name(out.stem + "_settings.json")
    dest.write_text(_serialise_settings(settings), encoding="utf-8")


def _copy_gpx_alongside(gpx_path: str | None, out: Path, container: str) -> None:
    """For containers that don't support attachments, copy the GPX next to the video."""
    if not gpx_path or not Path(gpx_path).is_file():
        return
    if container in _ATTACHMENT_CONTAINERS:
        return
    dest = out.with_suffix(".gpx")
    shutil.copy2(gpx_path, dest)


def _container_args(enc: EncoderConfig, container: str) -> list[str]:
    args: list[str] = []
    if container == "mp4":
        args += ["-movflags", "+faststart"]
        # Apple devices need hvc1 tag for H.265 in MP4
        if enc.codec == "h265":
            args += ["-tag:v", "hvc1"]
    return args
