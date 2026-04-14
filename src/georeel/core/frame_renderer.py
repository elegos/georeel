"""
Stage 7 — Frame Renderer.

Launches Blender headlessly to render each camera keyframe into a PNG
image sequence stored in a temporary directory.

When render/n_segments > 1, the render is split into N temporal passes.
Each pass launches a fresh Blender process that loads only the terrain
tiles visible from the camera during its frame range (camera AABB +
render/frustum_margin_km radius).  This keeps per-pass VRAM usage
proportional to the fraction of the terrain the camera actually sees,
which is critical for large satellite textures.
"""

import json
import math
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


# ------------------------------------------------------------------
# Tile geometry helpers
# ------------------------------------------------------------------

def _tile_world_bounds(
    tile: dict, rows: int, cols: int, lat_m: float, lon_m: float
) -> tuple[float, float, float, float]:
    """Return (x_min, x_max, y_min, y_max) in world metres for a manifest tile.

    Coordinate convention matches build_scene.py:
        x = east  (0 → lon_m)
        y = north (0 → lat_m)   [row 0 = max_lat, so y increases southward in
                                  pixel space but northward in world space]
    """
    x_min = tile["dem_c_start"] / (cols - 1) * lon_m
    x_max = tile["dem_c_end"]   / (cols - 1) * lon_m
    # dem_r_start is the northernmost row index (smaller r → larger y)
    y_min = (1.0 - tile["dem_r_end"]   / (rows - 1)) * lat_m
    y_max = (1.0 - tile["dem_r_start"] / (rows - 1)) * lat_m
    return x_min, x_max, y_min, y_max


def _filter_tiles(
    tiles: list[dict],
    cam_xs: list[float],
    cam_ys: list[float],
    margin_m: float,
    rows: int,
    cols: int,
    lat_m: float,
    lon_m: float,
) -> list[str]:
    """Return tile IDs ('ti_tj') that intersect the camera AABB expanded by margin_m.

    If the camera list is empty or only one tile exists, all tiles are returned.
    """
    if not cam_xs or not tiles:
        return [f"{t['ti']}_{t['tj']}" for t in tiles]

    q_xmin = min(cam_xs) - margin_m
    q_xmax = max(cam_xs) + margin_m
    q_ymin = min(cam_ys) - margin_m
    q_ymax = max(cam_ys) + margin_m

    result = []
    for t in tiles:
        tx_min, tx_max, ty_min, ty_max = _tile_world_bounds(t, rows, cols, lat_m, lon_m)
        if tx_max >= q_xmin and tx_min <= q_xmax and ty_max >= q_ymin and ty_min <= q_ymax:
            result.append(f"{t['ti']}_{t['tj']}")
    # Always include at least one tile (shouldn't happen, but guard against empty filter)
    if not result:
        result = [f"{t['ti']}_{t['tj']}" for t in tiles]
    return result


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

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
    n_segments = int(settings.get("render/n_segments", 1))

    work_dir = Path(tempfile.mkdtemp(prefix="georeel_frames_"))
    pipeline._temp_dirs.append(work_dir)
    kf_path  = work_dir / "keyframes.json"
    out_dir  = work_dir / "frames"
    out_dir.mkdir()

    _write_keyframes(pipeline.camera_keyframes, kf_path)

    total = len(pipeline.camera_keyframes)

    if n_segments > 1:
        return _render_segmented(
            pipeline=pipeline,
            settings=settings,
            exe=exe,
            kf_path=kf_path,
            out_dir=out_dir,
            engine=engine,
            resolution=resolution,
            quality=quality,
            total=total,
            n_segments=n_segments,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
        )

    return _render_single(
        exe=exe,
        scene=str(pipeline.scene),
        kf_path=kf_path,
        out_dir=out_dir,
        engine=engine,
        resolution=resolution,
        quality=quality,
        total=total,
        frame_start=0,
        frame_end=total - 1,
        tile_filter=None,
        progress_cb=progress_cb,
        cancel_check=cancel_check,
    )


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _render_single(
    exe: str,
    scene: str,
    kf_path: Path,
    out_dir: Path,
    engine: str,
    resolution: str,
    quality: str,
    total: int,
    frame_start: int,
    frame_end: int,
    tile_filter: str | None,
    progress_cb: Callable[[int, int], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> str:
    """Run one Blender render pass and stream progress."""
    cmd = [
        exe,
        "--background", scene,
        "--python", str(_BLENDER_SCRIPT),
        "--",
        str(kf_path),
        str(out_dir),
        engine,
        resolution,
        quality,
        str(frame_start),
        str(frame_end),
    ]
    if tile_filter is not None:
        cmd.append(tile_filter)

    try:
        proc = subprocess.Popen(
            shlex.join(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=True,
        )

        last_reported_fra = -1
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("Fra:"):
                try:
                    fra_num = int(line[4:].split()[0])
                    if fra_num != last_reported_fra:
                        last_reported_fra = fra_num
                        if progress_cb:
                            progress_cb(fra_num + 1, total)
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
        raise FrameRenderError(f"Blender exited with code {proc.returncode}.")

    return str(out_dir)


def _render_segmented(
    pipeline: Pipeline,
    settings: dict,
    exe: str,
    kf_path: Path,
    out_dir: Path,
    engine: str,
    resolution: str,
    quality: str,
    total: int,
    n_segments: int,
    progress_cb: Callable[[int, int], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> str:
    """Render in N temporal passes, each loading only the tiles the camera needs.

    Tile filtering is based on the camera AABB for the segment expanded by
    render/frustum_margin_km (default 50 km).  Each Blender process exits
    cleanly between segments, fully releasing VRAM before the next segment
    starts — this is the key benefit for large satellite textures.
    """
    scene_dir = Path(pipeline.scene).parent  # type: ignore[arg-type]
    meta_path     = scene_dir / "dem_meta.json"
    manifest_path = scene_dir / "sat_manifest.json"

    # If scene metadata is unavailable, fall back to single-pass (no tile filtering)
    use_tile_filter = (
        meta_path.exists()
        and manifest_path.exists()
    )

    tiles: list[dict] = []
    rows = cols = 1
    lat_m = lon_m = 1.0

    if use_tile_filter:
        meta     = json.loads(meta_path.read_text())
        manifest = json.loads(manifest_path.read_text())
        rows  = meta["rows"]
        cols  = meta["cols"]
        lat_m = float(meta.get("lat_m", 1.0))
        lon_m = float(meta.get("lon_m", 1.0))
        tiles = manifest.get("tiles", [])
        # If lat_m/lon_m weren't baked in (old scene), skip tile filtering
        if lat_m == 1.0 and lon_m == 1.0:
            use_tile_filter = False

    margin_m = float(settings.get("render/frustum_margin_km", 50.0)) * 1000.0
    keyframes = pipeline.camera_keyframes
    assert keyframes is not None  # guaranteed by caller
    seg_size  = math.ceil(total / n_segments)

    for seg_idx in range(n_segments):
        seg_start = seg_idx * seg_size
        seg_end   = min(seg_start + seg_size - 1, total - 1)

        if use_tile_filter and len(tiles) > 1:
            seg_kfs = keyframes[seg_start : seg_end + 1]
            cam_xs  = [kf.x for kf in seg_kfs]
            cam_ys  = [kf.y for kf in seg_kfs]
            tile_ids = _filter_tiles(
                tiles, cam_xs, cam_ys, margin_m, rows, cols, lat_m, lon_m
            )
            tile_filter: str | None = ",".join(sorted(tile_ids))
            print(
                f"[georeel] Render segment {seg_idx + 1}/{n_segments}: "
                f"frames {seg_start}–{seg_end}, "
                f"tiles {tile_filter} ({len(tile_ids)}/{len(tiles)} loaded)"
            )
        else:
            tile_filter = None
            print(
                f"[georeel] Render segment {seg_idx + 1}/{n_segments}: "
                f"frames {seg_start}–{seg_end}, all tiles"
            )

        _render_single(
            exe=exe,
            scene=str(pipeline.scene),
            kf_path=kf_path,
            out_dir=out_dir,
            engine=engine,
            resolution=resolution,
            quality=quality,
            total=total,
            frame_start=seg_start,
            frame_end=seg_end,
            tile_filter=tile_filter,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
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
