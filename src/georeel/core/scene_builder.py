import atexit
import json
import logging
import math
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

import numpy as np
from scipy.interpolate import splev, splprep

from .blender_runtime import find_blender
from .elevation_grid import ElevationGrid
from .pil_lock import PIL_LOCK
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
    atexit.register(shutil.rmtree, work_dir, True)
    meta_path, data_path = _write_dem(pipeline.elevation_grid, work_dir)
    manifest_path = _write_texture_tiles(
        pipeline.satellite_texture, pipeline.elevation_grid, work_dir
    )
    settings = settings or {}
    fps = float(settings.get("render/fps", 30))
    speed_mps = float(settings.get("render/camera_speed_mps", 80.0))
    # Ribbon spacing must be at least speed_mps/fps so the Build modifier can
    # reveal exactly one face per camera frame — the camera, ribbon, and marker
    # all advance the same metres-per-frame regardless of the chosen speed.
    effective_ribbon_spacing = max(_RIBBON_SAMPLE_SPACING_M, speed_mps / fps)
    track_path, ribbon_points = _write_track(pipeline, work_dir,
                                             ribbon_spacing_m=effective_ribbon_spacing)
    pins_path = _write_pins(pipeline, work_dir, settings)
    pause_schedule = _compute_pause_schedule(pipeline, settings, ribbon_points)
    pauses_path = work_dir / "pauses.json"
    pauses_path.write_text(json.dumps(pause_schedule))
    blend_path = work_dir / "scene.blend"

    pin_color = _resolve_pin_color(settings)
    marker_color = _resolve_marker_color(settings)
    height_offset = float(settings.get("render/camera_height_offset", 200))

    cmd = [
        exe,
        "--background",
        "--python",
        str(_BLENDER_SCRIPT),
        "--",
        str(meta_path),
        str(data_path),
        str(manifest_path),
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


_RIBBON_SAMPLE_SPACING_M = 5.0  # minimum ribbon sample spacing (metres)


def _write_track(
    pipeline: "Pipeline",
    work_dir: Path,
    ribbon_spacing_m: float = _RIBBON_SAMPLE_SPACING_M,
) -> tuple[Path, list[dict]]:
    """Project trackpoints onto a B-spline, resample at *ribbon_spacing_m* intervals,
    sample elevation from the DEM, compute slope, and write JSON.

    *ribbon_spacing_m* is normally the module constant (5 m) but is widened when
    the flythrough speed requires more than 5 m per animation frame so that the
    ribbon Build modifier can advance exactly one face per frame.

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
    n_samples = max(2, int(total_length / ribbon_spacing_m) + 1)
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
        eps = ribbon_spacing_m / 2
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
    # Pin geometry (mirrors build_scene.py _build_pins)
    marker_r = max(1.5, 4.0 * scale)
    r_head   = marker_r * 0.8   # radius of the circular head
    # Gap between adjacent pins: small fixed margin so they almost touch
    PIN_GAP_M = max(2.0, marker_r * 0.1)

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
                # Place pins on a circle whose radius makes adjacent pins
                # almost touch (gap ≈ PIN_GAP_M between edges).
                # Chord between adjacent pins = 2*r_head + PIN_GAP_M
                # Chord = 2 * R * sin(π/n)  →  R = chord / (2*sin(π/n))
                chord    = 2 * r_head + PIN_GAP_M
                circle_r = chord / (2 * math.sin(math.pi / n))
                angle = 2 * math.pi * k / n
                dx = circle_r * math.cos(angle)
                dy = circle_r * math.sin(angle)
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
      pauses — list of {scene_start, duration, cumulative_before}, one entry
               per distinct waypoint; carousels (multiple photos at the same
               location) are merged into a single entry whose duration covers
               all photos in the cluster
    """
    fps = float(settings.get("render/fps", 30))
    speed_mps = float(settings.get("render/camera_speed_mps", 80.0))
    pause_dur = float(settings.get("render/photo_pause_duration", 3.0))
    pause_frames = max(1, round(pause_dur * fps))
    # Dynamic ribbon spacing (mirrors the value chosen in build_scene()):
    # widened so the Build modifier always reveals exactly 1 face per camera frame,
    # keeping the ribbon, marker, and camera speed-locked at any chosen speed.
    effective_ribbon_spacing = max(_RIBBON_SAMPLE_SPACING_M, speed_mps / fps)
    frames_per_ribbon_point = max(1.0, effective_ribbon_spacing * fps / speed_mps)

    n_ribbon = len(ribbon_points)
    fly_total = max(2, round((n_ribbon - 1) * frames_per_ribbon_point))

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
                0, round(nearest_idx * frames_per_ribbon_point)
            )  # consistent with _build_marker's round(i * frames_per_point)
            waypoints.append((fly_frame, r.photo_path or ""))

        waypoints.sort(key=lambda w: w[0])

        # Group photos that map to the same fly_frame (carousel / cluster).
        # Merging them into one pause entry with combined duration means the
        # schedule has exactly one entry per waypoint, which lets _build_marker
        # and _build_ribbon share the same timing data without key collisions.
        cumulative_pause = 0
        i = 0
        while i < len(waypoints):
            fly_frame = waypoints[i][0]
            j = i + 1
            while j < len(waypoints) and waypoints[j][0] == fly_frame:
                j += 1
            total_duration = pause_frames * (j - i)
            scene_start = pre_total + fly_frame + cumulative_pause + 1
            pauses.append(
                {
                    "scene_start": scene_start,
                    "duration": total_duration,
                    "cumulative_before": cumulative_pause,
                }
            )
            cumulative_pause += total_duration
            i = j

    total_scene_frames = (
        pre_total + fly_total + sum(p["duration"] for p in pauses) + post_total
    )
    return {
        "pre_total_frames": pre_total,
        "fly_total_frames": fly_total,
        "post_total_frames": post_total,
        "total_scene_frames": total_scene_frames,
        "pauses": pauses,
        # Communicated to build_scene.py so _build_ribbon/_build_marker use the
        # same spacing that _write_track used when sampling the ribbon geometry.
        "ribbon_spacing_m": effective_ribbon_spacing,
    }


def _resolve_pin_color(settings: dict) -> str:
    """Return a #rrggbb color string for the pin from settings."""
    from georeel.ui.color_picker_dialog import get_color_hex  # type: ignore[import]

    color_id = settings.get("pins/color", "ForestGreen")
    if color_id == "custom":
        return settings.get("pins/custom_color", "#228B22")
    return get_color_hex(color_id, "#228B22")


def _resolve_marker_color(settings: dict) -> str:
    """Return a #rrggbb color string for the track marker from settings."""
    from georeel.ui.color_picker_dialog import get_color_hex  # type: ignore[import]

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


_MAX_TILE_PIXELS = 400_000_000  # ~1.2 GB as RGB — safely below Blender's 2 GB pack limit


def _write_texture_tiles(
    texture: SatelliteTexture, grid: ElevationGrid, work_dir: Path
) -> Path:
    """Save the satellite texture as tiled PNG files and write a manifest JSON.

    Splits the image into an N×M grid so each tile stays under _MAX_TILE_PIXELS,
    working around Blender's 2 GB pack limit.  Adjacent tiles share their border
    row/column of DEM vertices so no seam appears in the rendered terrain.

    The satellite and DEM may cover slightly different extents when cached data
    from a previous run is reused; the image is cropped to the DEM extent first
    so UV [0,1] maps correctly.

    Returns the path to the manifest JSON consumed by build_scene.py.
    """
    import io
    import math as _math

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
        # Satellite image: row 0 = max_lat (north), col 0 = min_lon (west).
        left   = int(round((grid.min_lon - texture.min_lon) / lon_span * w))
        right  = int(round((grid.max_lon - texture.min_lon) / lon_span * w))
        top    = int(round((texture.max_lat - grid.max_lat) / lat_span * h))
        bottom = int(round((texture.max_lat - grid.min_lat) / lat_span * h))
        left, right = max(0, left), min(w, right)
        top, bottom = max(0, top), min(h, bottom)
        if right > left and bottom > top:
            with PIL_LOCK:
                img = img.crop((left, top, right, bottom))

    W, H = img.size
    total_pixels = W * H

    # Determine tile grid dimensions — aim for roughly square tiles
    n_tiles_needed = _math.ceil(total_pixels / _MAX_TILE_PIXELS)
    if n_tiles_needed <= 1:
        n_tile_cols, n_tile_rows = 1, 1
    else:
        n_tile_cols = max(1, _math.ceil(_math.sqrt(n_tiles_needed * W / H)))
        n_tile_rows = max(1, _math.ceil(n_tiles_needed / n_tile_cols))
        # Adjust until each tile fits
        while True:
            tile_w = _math.ceil(W / n_tile_cols)
            tile_h = _math.ceil(H / n_tile_rows)
            if tile_w * tile_h <= _MAX_TILE_PIXELS:
                break
            if n_tile_cols * H < n_tile_rows * W:
                n_tile_cols += 1
            else:
                n_tile_rows += 1

    _log.info(
        "[satellite] Splitting %dx%d px texture into %d×%d tiles",
        W, H, n_tile_rows, n_tile_cols,
    )

    tiles_dir = work_dir / "sat_tiles"
    tiles_dir.mkdir(exist_ok=True)

    dem_rows = grid.rows
    dem_cols = grid.cols

    tiles = []
    for ti in range(n_tile_rows):
        for tj in range(n_tile_cols):
            # Image pixel bounds (PIL crop: exclusive right/bottom)
            px_left   = tj * W // n_tile_cols
            px_right  = (tj + 1) * W // n_tile_cols
            px_top    = ti * H // n_tile_rows
            px_bottom = (ti + 1) * H // n_tile_rows

            # DEM row/col bounds (inclusive both ends so adjacent tiles share boundary)
            dem_c_start = round(px_left   / W * (dem_cols - 1))
            dem_c_end   = round(px_right  / W * (dem_cols - 1))
            dem_r_start = round(px_top    / H * (dem_rows - 1))
            dem_r_end   = round(px_bottom / H * (dem_rows - 1))

            tile_path = tiles_dir / f"{ti}_{tj}.png"
            buf = io.BytesIO()
            with PIL_LOCK:
                tile_img = img.crop((px_left, px_top, px_right, px_bottom))
                tile_img.convert("RGB").save(buf, format="PNG", optimize=False)
            tile_path.write_bytes(buf.getvalue())

            tiles.append({
                "ti": ti, "tj": tj,
                "path": str(tile_path),
                "dem_r_start": dem_r_start,
                "dem_r_end":   dem_r_end,
                "dem_c_start": dem_c_start,
                "dem_c_end":   dem_c_end,
            })

    manifest = {
        "n_tile_rows": n_tile_rows,
        "n_tile_cols": n_tile_cols,
        "tiles": tiles,
    }
    manifest_path = work_dir / "sat_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path
