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
import os
import shlex
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PIL import Image as _PILImage

from .blender_runtime import find_blender
from .camera_keyframe import CameraKeyframe
from .pipeline import Pipeline
from . import temp_manager

_BLENDER_SCRIPT = Path(__file__).parent / "blender_scripts" / "render_frames.py"


class FrameRenderError(Exception):
    pass


class _CompressionServer:
    """Listens on a localhost TCP port; Blender sends a PNG path (newline-
    terminated) after each frame is written.  A thread pool re-compresses
    each file in the background so Blender can start the next frame without
    waiting for zlib.

    Protocol: one persistent TCP connection from Blender for the duration of
    the render.  Each message is ``<absolute-path>\\n``.
    """

    def __init__(self, compress_level: int) -> None:
        self._level = compress_level
        self._futures: list = []
        self._errors: list[str] = []

        n_workers = max(1, min(4, (os.cpu_count() or 2) - 1))
        self._pool = ThreadPoolExecutor(
            max_workers=n_workers, thread_name_prefix="png_compress"
        )

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(60.0)  # wait up to 60 s for Blender to connect
        self.port: int = self._sock.getsockname()[1]

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return  # socket closed or Blender never connected
        finally:
            self._sock.close()

        with conn:
            buf = ""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk.decode()
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    path = line.strip()
                    if path:
                        self._futures.append(self._pool.submit(self._compress, path))

    def _compress(self, path: str) -> None:
        try:
            img = _PILImage.open(path)
            img.load()  # read pixels before overwriting the file
            img.save(path, format="PNG", compress_level=self._level)
        except Exception as exc:
            self._errors.append(f"{path}: {exc}")

    def finish(self) -> None:
        """Block until all queued compressions finish; log any errors."""
        # shutdown(SHUT_RDWR) interrupts a blocking accept() in _run on Linux;
        # close() alone does not.  Both are no-ops if the socket is already gone.
        for fn in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                fn()
            except OSError:
                pass
        self._thread.join(timeout=2)
        for f in self._futures:
            f.result()
        self._pool.shutdown(wait=True)
        for err in self._errors:
            print(f"[georeel] PNG compression warning: {err}")


# ------------------------------------------------------------------
# Tile geometry helpers
# ------------------------------------------------------------------

def _tile_world_bounds(
    tile: dict[str, Any], rows: int, cols: int, lat_m: float, lon_m: float
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
    tiles: list[dict[str, Any]],
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
    settings: dict[str, Any],
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

    work_dir = temp_manager.make_temp_dir("georeel_frames_")
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
        settings=settings,
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
    settings: dict[str, Any] | None = None,
) -> str:
    """Run one Blender render pass and stream progress."""
    # tex_scale < 1.0 for viewport/draft mode: downscales terrain textures in
    # VRAM to relieve GPU memory pressure on scenes with large satellite imagery.
    tex_scale = 0.5 if engine == "viewport" else 1.0
    # Viewport/draft renders frames in milliseconds — never compress PNGs in
    # that mode or the CPU write time dominates and the GPU starves (~4% usage).
    # For EEVEE/Cycles the per-frame time is long enough that compression is fine.
    if engine == "viewport":
        png_compression = 0
    else:
        png_compression = int(settings.get("render/png_compression", 1)) if settings else 1

    # When compression > 0, offload zlib to a background thread pool:
    # Blender writes frames uncompressed (fastest I/O), notifies the server
    # over a localhost socket, and the pool re-compresses each PNG while
    # Blender is already rendering the next frame.
    comp_server: _CompressionServer | None = None
    if png_compression > 0:
        comp_server = _CompressionServer(png_compression)

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
        tile_filter if tile_filter is not None else "",       # argv[7]; "" → None in script
        str(tex_scale),                                       # argv[8]
        "0" if comp_server else str(png_compression),        # argv[9]: 0 = Blender writes raw
        str(comp_server.port if comp_server else 0),         # argv[10]: compression server port
    ]

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

    if comp_server:
        comp_server.finish()

    return str(out_dir)


def _render_segmented(
    pipeline: Pipeline,
    settings: dict[str, Any],
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
    assert pipeline.scene is not None
    scene_dir = Path(pipeline.scene).parent  # type: ignore[arg-type]
    meta_path     = scene_dir / "dem_meta.json"
    manifest_path = scene_dir / "sat_manifest.json"

    # If scene metadata is unavailable, fall back to single-pass (no tile filtering)
    use_tile_filter = (
        meta_path.exists()
        and manifest_path.exists()
    )

    tiles: list[dict[str, Any]] = []
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
            settings=settings,
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
