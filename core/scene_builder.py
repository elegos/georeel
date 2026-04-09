import json
import logging
import math
import shlex
import subprocess
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

import numpy as np
from scipy.interpolate import splev, splprep

from .blender_runtime import find_blender
from .elevation_grid import ElevationGrid
from .pipeline import Pipeline
from .satellite import SatelliteTexture
from .sun_position import sun_angles, sun_direction_vector

_BLENDER_SCRIPT = Path(__file__).parent / "blender_scripts" / "build_scene.py"
_TIMEOUT_SECONDS = 300  # 5 minutes


class SceneBuildError(Exception):
    pass


def build_scene(
    pipeline: Pipeline, blender_exe: str | None = None, settings: dict | None = None
) -> str:
    """Build a 3D terrain .blend from the pipeline's elevation grid and satellite texture.

    *blender_exe* overrides auto-detection (pass the value from QSettings).

    Returns the absolute path to the saved .blend file.
    The file lives in a temporary directory that persists for the OS session;
    the texture is packed inside the .blend so the directory can safely be
    discarded once stage 7 (rendering) is complete.
    """
    if pipeline.elevation_grid is None:
        raise SceneBuildError("Elevation grid is required (run DEM fetcher first).")
    if pipeline.satellite_texture is None:
        raise SceneBuildError(
            "Satellite texture is required (run satellite fetcher first)."
        )

    exe = find_blender(blender_exe)
    if exe is None:
        raise SceneBuildError(
            "Blender executable not found. "
            "Install Blender or download it via Options → Blender…"
        )

    work_dir = Path(tempfile.mkdtemp(prefix="georeel_scene_"))
    meta_path, data_path = _write_dem(pipeline.elevation_grid, work_dir)
    tex_path = _write_texture(
        pipeline.satellite_texture, pipeline.elevation_grid, work_dir
    )
    settings = settings or {}
    track_path, ribbon_points = _write_track(pipeline, work_dir)
    pins_path = _write_pins(pipeline, work_dir, settings)
    pause_schedule = _compute_pause_schedule(pipeline, settings, ribbon_points)
    pauses_path = work_dir / "pauses.json"
    pauses_path.write_text(json.dumps(pause_schedule))
    blend_path = work_dir / "scene.blend"

    pin_color = _resolve_pin_color(settings)
    marker_color = _resolve_marker_color(settings)
    height_offset = float(settings.get("render/camera_height_offset", 200))
    fps = float(settings.get("render/fps", 30))
    speed_mps = float(settings.get("render/camera_speed_mps", 80.0))

    cmd = [
        exe,
        "--background",
        "--python",
        str(_BLENDER_SCRIPT),
        "--",
        str(meta_path),
        str(data_path),
        str(tex_path),
        str(blend_path),
        str(track_path),
        str(pins_path),
        pin_color,
        str(height_offset),
        str(fps),
        str(speed_mps),
        str(pauses_path),
        marker_color,
    ] + _sun_args(pipeline)

    try:
        result = subprocess.run(
            shlex.join(cmd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            shell=True,
        )
    except subprocess.TimeoutExpired:
        raise SceneBuildError(
            f"Blender timed out after {_TIMEOUT_SECONDS // 60} minutes."
        )

    blender_output = (result.stderr or "") + (result.stdout or "")
    if blender_output:
        _log.debug("Blender output:\n%s", blender_output)

    # Surface DEM-quality diagnostics at INFO level so they reach the user
    for line in blender_output.splitlines():
        if line.startswith("[georeel]"):
            _log.info("%s", line)

    if result.returncode != 0 or not blend_path.is_file():
        _log.error(
            "Blender scene build failed (exit %d):\n%s",
            result.returncode,
            blender_output,
        )
        tail = blender_output[-2000:]
        raise SceneBuildError(
            f"Blender scene build failed (exit {result.returncode}).\n{tail}"
        )

    return str(blend_path)


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _sun_args(pipeline: "Pipeline") -> list[str]:
    """Return [sun_x, sun_y, sun_z] strings if a timestamp is available, else []."""
    ts = next(
        (tp.timestamp for tp in pipeline.trackpoints if tp.timestamp is not None),
        None,
    )
    if ts is None or pipeline.elevation_grid is None:
        return []
    g = pipeline.elevation_grid
    lat = (g.min_lat + g.max_lat) / 2
    lon = (g.min_lon + g.max_lon) / 2
    az, el = sun_angles(lat, lon, ts)
    sx, sy, sz = sun_direction_vector(az, el)
    return [str(sx), str(sy), str(sz)]


_RIBBON_SAMPLE_SPACING_M = 5.0  # resample every 5 m for smooth curves


def _write_track(pipeline: "Pipeline", work_dir: Path) -> tuple[Path, list[dict]]:
    """Project trackpoints onto a B-spline, resample at 5 m intervals,
    sample elevation from the DEM, compute slope, and write JSON.

    Returns (track_path, ribbon_points) where ribbon_points is the list of
    {x, y, z, slope} dicts — used by _compute_pause_schedule without re-parsing.
    """
    track_path = work_dir / "track.json"

    if not pipeline.trackpoints or pipeline.elevation_grid is None:
        track_path.write_text("[]")
        return track_path, []

    grid = pipeline.elevation_grid
    mean_lat_rad = math.radians((grid.min_lat + grid.max_lat) / 2)
    lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
    lon_m = (grid.max_lon - grid.min_lon) * 111_320.0 * math.cos(mean_lat_rad)

    # Project trackpoints to scene XY, removing duplicates
    raw: list[tuple[float, float]] = []
    for tp in pipeline.trackpoints:
        x = (tp.longitude - grid.min_lon) / (grid.max_lon - grid.min_lon) * lon_m
        y = (tp.latitude - grid.min_lat) / (grid.max_lat - grid.min_lat) * lat_m
        if raw and abs(x - raw[-1][0]) < 1e-4 and abs(y - raw[-1][1]) < 1e-4:
            continue
        raw.append((x, y))

    if len(raw) < 4:
        # Too few points for a spline: fall back to raw points with slope=0
        points = [
            {"x": x, "y": y, "z": _elev_at_xy(x, y, grid, lat_m, lon_m), "slope": 0.0}
            for x, y in raw
        ]
        track_path.write_text(json.dumps(points))
        return track_path, points

    pts = np.array(raw)

    # Fit parametric cubic B-spline through all trackpoints
    tck, _ = splprep([pts[:, 0], pts[:, 1]], s=0, k=3)

    # Compute total arc length on a dense evaluation
    t_fine = np.linspace(0, 1, max(10_000, len(pts) * 100))
    xs_fine, ys_fine = splev(t_fine, tck)
    dx = np.diff(xs_fine)
    dy = np.diff(ys_fine)
    cumlen = np.concatenate([[0.0], np.cumsum(np.sqrt(dx**2 + dy**2))])
    total_length = cumlen[-1]

    # Resample at equal spacing
    n_samples = max(2, int(total_length / _RIBBON_SAMPLE_SPACING_M) + 1)
    sample_dists = np.linspace(0, total_length, n_samples)
    sample_t = np.interp(sample_dists, cumlen, t_fine)

    xs, ys = splev(sample_t, tck)
    # Derivatives for slope computation
    dxs, dys = splev(sample_t, tck, der=1)

    points: list[dict] = []
    for i in range(n_samples):
        x, y = float(xs[i]), float(ys[i])
        z = _elev_at_xy(x, y, grid, lat_m, lon_m)
        # Slope: rise over run using spline tangent and DEM elevation difference
        # Sample elevation slightly ahead and behind for accurate grade
        eps = _RIBBON_SAMPLE_SPACING_M / 2
        horiz = math.sqrt(float(dxs[i]) ** 2 + float(dys[i]) ** 2)
        if horiz > 1e-6 and i > 0 and i < n_samples - 1:
            z_prev = _elev_at_xy(float(xs[i - 1]), float(ys[i - 1]), grid, lat_m, lon_m)
            z_next = _elev_at_xy(float(xs[i + 1]), float(ys[i + 1]), grid, lat_m, lon_m)
            seg_h = math.sqrt(
                (float(xs[i + 1]) - float(xs[i - 1])) ** 2
                + (float(ys[i + 1]) - float(ys[i - 1])) ** 2
            )
            slope = abs(z_next - z_prev) / seg_h if seg_h > 1e-6 else 0.0
        else:
            slope = 0.0
        points.append({"x": x, "y": y, "z": z, "slope": slope})

    track_path.write_text(json.dumps(points))
    return track_path, points


def _elev_at_xy(
    x: float, y: float, grid: "ElevationGrid", lat_m: float, lon_m: float
) -> float:
    lat = grid.min_lat + y / lat_m * (grid.max_lat - grid.min_lat)
    lon = grid.min_lon + x / lon_m * (grid.max_lon - grid.min_lon)
    return grid.elevation_at(lat, lon)


def _write_pins(
    pipeline: "Pipeline", work_dir: Path, settings: dict | None = None
) -> Path:
    """Write per-waypoint pin data (scene XY, elevation, photo path) as JSON.

    Pins sharing the same trackpoint are spread horizontally so they don't
    overlap and cause flickering.  Spread step scales with camera height so
    the separation is consistent across zoom levels.
    """
    pins_path = work_dir / "pins.json"

    if not pipeline.match_results or pipeline.elevation_grid is None:
        pins_path.write_text("[]")
        return pins_path

    grid = pipeline.elevation_grid
    mean_lat_rad = math.radians((grid.min_lat + grid.max_lat) / 2)
    lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
    lon_m = (grid.max_lon - grid.min_lon) * 111_320.0 * math.cos(mean_lat_rad)

    height_offset = float((settings or {}).get("render/camera_height_offset", 200))
    scale = height_offset / 200.0
    # Spread step = 1.5 × scaled pin width so adjacent pins have a small gap
    spread_step_m = 24.0 * scale * 1.5

    # Collect raw pins, keyed by trackpoint_index to detect collisions
    from collections import defaultdict

    groups: dict[int, list[dict]] = defaultdict(list)
    for r in pipeline.match_results:
        if not r.ok or r.trackpoint_index is None:
            continue
        tp = pipeline.trackpoints[r.trackpoint_index]
        x = (tp.longitude - grid.min_lon) / (grid.max_lon - grid.min_lon) * lon_m
        y = (tp.latitude - grid.min_lat) / (grid.max_lat - grid.min_lat) * lat_m
        z = grid.elevation_at(tp.latitude, tp.longitude)
        groups[r.trackpoint_index].append(
            {"x": x, "y": y, "z": z, "photo_path": r.photo_path}
        )

    pins: list[dict] = []
    for tp_idx in sorted(groups):
        group = sorted(groups[tp_idx], key=lambda p: p["photo_path"])
        n = len(group)
        for k, pin in enumerate(group):
            if n == 1:
                dx, dy = 0.0, 0.0
            else:
                # Arrange evenly on a circle around the trackpoint
                angle = 2 * math.pi * k / n
                dx = spread_step_m * math.cos(angle)
                dy = spread_step_m * math.sin(angle)
            pins.append(
                {
                    "x": pin["x"] + dx,
                    "y": pin["y"] + dy,
                    "z": pin["z"],
                    "photo_path": pin["photo_path"],
                }
            )

    pins_path.write_text(json.dumps(pins))
    return pins_path


def _compute_pause_schedule(
    pipeline: "Pipeline",
    settings: dict,
    ribbon_points: list[dict],
) -> dict:
    """Compute when each photo pause happens in the scene timeline.

    Uses the same logic as camera_path._insert_pauses but works from
    ribbon geometry rather than the full camera keyframe list, since
    scene building happens before camera path generation.

    Returns a dict suitable for JSON serialisation:
      fly_total_frames  — non-pause frames (ribbon travel only)
      total_scene_frames — fly + all pause frames
      pauses — list of {scene_start, duration, cumulative_before}
    """
    fps = float(settings.get("render/fps", 30))
    speed_mps = float(settings.get("render/camera_speed_mps", 80.0))
    pause_dur = float(settings.get("render/photo_pause_duration", 3.0))
    pause_frames = max(1, round(pause_dur * fps))
    dist_per_frame = speed_mps / fps

    n_ribbon = len(ribbon_points)
    total_ribbon_len = (
        (n_ribbon - 1) * _RIBBON_SAMPLE_SPACING_M if n_ribbon > 1 else 0.0
    )
    fly_total = max(2, int(total_ribbon_len / dist_per_frame))

    pre_total = 0
    post_total = 0
    pauses: list[dict] = []

    if pipeline.match_results and pipeline.elevation_grid is not None and n_ribbon >= 2:
        grid = pipeline.elevation_grid
        mean_lat_rad = math.radians((grid.min_lat + grid.max_lat) / 2)
        lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
        lon_m = (grid.max_lon - grid.min_lon) * 111_320.0 * math.cos(mean_lat_rad)

        ribbon_xy = np.array([(p["x"], p["y"]) for p in ribbon_points])

        # Count pre/post photos (position attribute added by timestamp matcher)
        pre_count = sum(
            1 for r in pipeline.match_results if r.ok and r.position == "pre"
        )
        post_count = sum(
            1 for r in pipeline.match_results if r.ok and r.position == "post"
        )
        pre_total = pre_count * pause_frames
        post_total = post_count * pause_frames

        # Collect in-track (fly_frame, photo_path) — matches _insert_pauses order
        waypoints: list[tuple[int, str]] = []
        for r in pipeline.match_results:
            if not r.ok or r.trackpoint_index is None or r.position != "track":
                continue
            tp = pipeline.trackpoints[r.trackpoint_index]
            x = (tp.longitude - grid.min_lon) / (grid.max_lon - grid.min_lon) * lon_m
            y = (tp.latitude - grid.min_lat) / (grid.max_lat - grid.min_lat) * lat_m
            dists = np.sqrt((ribbon_xy[:, 0] - x) ** 2 + (ribbon_xy[:, 1] - y) ** 2)
            nearest_idx = int(np.argmin(dists))
            fly_frame = max(
                0, round(nearest_idx * _RIBBON_SAMPLE_SPACING_M / dist_per_frame)
            )
            waypoints.append((fly_frame, r.photo_path or ""))

        waypoints.sort(key=lambda w: w[0])

        cumulative_pause = 0
        for fly_frame, _ in waypoints:
            # scene_start is offset by pre_total so it falls in the fly section
            scene_start = pre_total + fly_frame + cumulative_pause + 1
            pauses.append(
                {
                    "scene_start": scene_start,
                    "duration": pause_frames,
                    "cumulative_before": cumulative_pause,
                }
            )
            cumulative_pause += pause_frames

    total_scene_frames = (
        pre_total + fly_total + sum(p["duration"] for p in pauses) + post_total
    )
    return {
        "pre_total_frames": pre_total,
        "fly_total_frames": fly_total,
        "post_total_frames": post_total,
        "total_scene_frames": total_scene_frames,
        "pauses": pauses,
    }


def _resolve_pin_color(settings: dict) -> str:
    """Return a #rrggbb color string for the pin from settings."""
    from ui.color_picker_dialog import get_color_hex  # type: ignore[import]

    color_id = settings.get("pins/color", "ForestGreen")
    if color_id == "custom":
        return settings.get("pins/custom_color", "#228B22")
    return get_color_hex(color_id, "#228B22")


def _resolve_marker_color(settings: dict) -> str:
    """Return a #rrggbb color string for the track marker from settings."""
    from ui.color_picker_dialog import get_color_hex  # type: ignore[import]

    color_id = settings.get("marker/color", "Navy")
    if color_id == "custom":
        return settings.get("marker/custom_color", "#ADD8E6")
    return get_color_hex(color_id, "#ADD8E6")


def _write_dem(grid: ElevationGrid, work_dir: Path) -> tuple[Path, Path]:
    meta = {
        "rows": grid.rows,
        "cols": grid.cols,
        "min_lat": grid.min_lat,
        "max_lat": grid.max_lat,
        "min_lon": grid.min_lon,
        "max_lon": grid.max_lon,
    }
    meta_path = work_dir / "dem_meta.json"
    data_path = work_dir / "dem_data.bin"
    meta_path.write_text(json.dumps(meta))
    data_path.write_bytes(grid.to_bytes())
    return meta_path, data_path


def _write_texture(
    texture: SatelliteTexture, grid: ElevationGrid, work_dir: Path
) -> Path:
    """Save the satellite texture, cropped to the DEM grid's geographic bounds.

    The satellite and DEM may cover slightly different extents when cached data
    from a previous run (with a different frustum margin) is reused.  Without
    cropping, the terrain UV mapping [0,1] spans the DEM extent while the PNG
    covers a different extent, causing the imagery to appear spatially offset.
    """
    import io

    from PIL import Image as _PILImage

    img = texture.image

    bounds_match = (
        abs(texture.min_lat - grid.min_lat) < 1e-9
        and abs(texture.max_lat - grid.max_lat) < 1e-9
        and abs(texture.min_lon - grid.min_lon) < 1e-9
        and abs(texture.max_lon - grid.max_lon) < 1e-9
    )

    if not bounds_match:
        w, h = img.size
        lat_span = texture.max_lat - texture.min_lat
        lon_span = texture.max_lon - texture.min_lon
        # Convert DEM geographic bounds to pixel coordinates within the satellite image.
        # Satellite image: row 0 = max_lat (north), col 0 = min_lon (west).
        left = int(round((grid.min_lon - texture.min_lon) / lon_span * w))
        right = int(round((grid.max_lon - texture.min_lon) / lon_span * w))
        top = int(round((texture.max_lat - grid.max_lat) / lat_span * h))
        bottom = int(round((texture.max_lat - grid.min_lat) / lat_span * h))
        left, right = max(0, left), min(w, right)
        top, bottom = max(0, top), min(h, bottom)
        if right > left and bottom > top:
            img = img.crop((left, top, right, bottom))

    tex_path = work_dir / "satellite.png"
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=False)
    tex_path.write_bytes(buf.getvalue())
    return tex_path
