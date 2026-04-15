import atexit
import json
import logging
import math
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from georeel.core import temp_manager

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


_PREVIEW_MAX_TEXTURE_PIXELS = 8_000_000  # ~4K×2K — keeps Blender preview under ~100 MB


def build_scene(
    pipeline: Pipeline,
    blender_exe: str | None = None,
    settings: dict[str, Any] | None = None,
    max_texture_pixels: int | None = None,
    tile_progress_cb: Callable[[int, int], None] | None = None,
    status_cb: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
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

    work_dir = temp_manager.make_temp_dir("georeel_scene_")
    atexit.register(shutil.rmtree, work_dir, True)
    meta_path, data_path = _write_dem(pipeline.elevation_grid, work_dir)
    manifest_path, tiles_manifest = _write_texture_tiles(
        pipeline.satellite_texture,
        pipeline.elevation_grid,
        work_dir,
        max_texture_pixels=max_texture_pixels,
        tile_progress_cb=tile_progress_cb,
        status_cb=status_cb,
        cancel_check=cancel_check,
    )
    # Release the PIL image now that tile PNGs are on disk — the Blender script
    # reads from the files directly.  write_png() (used by project save) will
    # reassemble from tiles on demand so no RAM is wasted between now and save.
    tiles_dir = work_dir / "sat_tiles"
    pipeline.satellite_texture.free_image(
        tiles_dir=tiles_dir, tiles_manifest=tiles_manifest
    )
    settings = settings or {}
    fps = float(settings.get("render/fps", 30))
    speed_mps = float(settings.get("render/camera_speed_mps", 80.0))
    # Ribbon spacing must be at least speed_mps/fps so the Build modifier can
    # reveal exactly one face per camera frame — the camera, ribbon, and marker
    # all advance the same metres-per-frame regardless of the chosen speed.
    effective_ribbon_spacing = max(_RIBBON_SAMPLE_SPACING_M, speed_mps / fps)
    track_path, ribbon_points, min_spd_mps, max_spd_mps = _write_track(
        pipeline, work_dir, ribbon_spacing_m=effective_ribbon_spacing
    )
    pins_path = _write_pins(pipeline, work_dir, settings)
    pause_schedule = _compute_pause_schedule(pipeline, settings, ribbon_points)
    pauses_path = work_dir / "pauses.json"
    pauses_path.write_text(json.dumps(pause_schedule))
    blend_path = work_dir / "scene.blend"

    pin_color = _resolve_pin_color(settings)
    marker_color = _resolve_marker_color(settings)
    height_offset = float(settings.get("render/camera_height_offset", 200))
    shifting_pin = bool(settings.get("marker/shifting_pin", False))
    marker_comp_color = _complementary_color(marker_color)
    ribbon_color_mode = str(settings.get("ribbon/color_mode", "slope"))
    ribbon_self_lit = bool(settings.get("ribbon/self_lit", False))

    cmd = [
        exe,
        "--background",
        "--python",
        str(_BLENDER_SCRIPT),
        "--",
        str(meta_path),  # argv[0]
        str(data_path),  # argv[1]
        str(manifest_path),  # argv[2]
        str(blend_path),  # argv[3]
        str(track_path),  # argv[4]
        str(pins_path),  # argv[5]
        pin_color,  # argv[6]
        str(height_offset),  # argv[7]
        str(fps),  # argv[8]
        str(speed_mps),  # argv[9]
        str(pauses_path),  # argv[10]
        marker_color,  # argv[11]
        "1" if shifting_pin else "0",  # argv[12]
        marker_comp_color,  # argv[13]
        ribbon_color_mode,  # argv[14]
        str(min_spd_mps),  # argv[15]
        str(max_spd_mps),  # argv[16]
        "1" if ribbon_self_lit else "0",  # argv[17]
    ] + _sun_args(pipeline)  # argv[18..20] (optional)

    if status_cb:
        status_cb("Running Blender to assemble 3D scene…")

    proc = subprocess.Popen(
        shlex.join(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True,
    )
    output_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output_lines.append(line)
        if cancel_check and cancel_check():
            proc.terminate()
            proc.wait()
            raise SceneBuildError("Cancelled.")
    proc.wait()

    blender_output = "".join(output_lines)
    if blender_output:
        _log.debug("Blender output:\n%s", blender_output)

    # Surface DEM-quality diagnostics at INFO level so they reach the user
    for line in blender_output.splitlines():
        if line.startswith("[georeel]"):
            _log.info("%s", line)

    if proc.returncode != 0 or not blend_path.is_file():
        _log.error(
            "Blender scene build failed (exit %d):\n%s",
            proc.returncode,
            blender_output,
        )
        tail = blender_output[-2000:]
        raise SceneBuildError(
            f"Blender scene build failed (exit {proc.returncode}).\n{tail}"
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
) -> tuple[Path, list[dict[str, Any]], float, float]:
    """Project trackpoints onto a B-spline, resample at *ribbon_spacing_m* intervals,
    sample elevation from the DEM, compute slope and speed, and write JSON.

    *ribbon_spacing_m* is normally the module constant (5 m) but is widened when
    the flythrough speed requires more than 5 m per animation frame so that the
    ribbon Build modifier can advance exactly one face per frame.

    Returns (track_path, ribbon_points, min_speed_mps, max_speed_mps) where
    ribbon_points is the list of {x, y, z, slope, speed, is_reconstructed} dicts —
    used by _compute_pause_schedule without re-parsing.  min/max_speed_mps are the
    5th/95th-percentile speeds across all trackpoints (0.0 when no timestamps).
    """
    track_path = work_dir / "track.json"

    if not pipeline.trackpoints or pipeline.elevation_grid is None:
        track_path.write_text("[]")
        return track_path, [], 0.0, 0.0

    grid = pipeline.elevation_grid
    mean_lat_rad = math.radians((grid.min_lat + grid.max_lat) / 2)
    lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
    lon_m = (grid.max_lon - grid.min_lon) * 111_320.0 * math.cos(mean_lat_rad)

    # Per-trackpoint speed (m/s): mean of the speeds of the adjacent segments.
    # When timestamps are absent the speed stays 0.0.
    tps = pipeline.trackpoints
    tp_speeds: list[float] = []
    for i in range(len(tps)):
        adj: list[float] = []
        for pair in (
            ((tps[i - 1], tps[i]) if i > 0 else None),
            ((tps[i], tps[i + 1]) if i < len(tps) - 1 else None),
        ):
            if pair is None:
                continue
            a, b = pair
            if a.timestamp and b.timestamp:
                dt_s = (b.timestamp - a.timestamp).total_seconds()
                if dt_s > 0:
                    d_m = _haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
                    adj.append(d_m / dt_s)
        tp_speeds.append(sum(adj) / len(adj) if adj else 0.0)

    # Percentile range for the speed colour scale (robust against GPS outliers).
    valid_spd = sorted(s for s in tp_speeds if s > 0.0)
    if valid_spd:
        p05 = valid_spd[max(0, int(0.05 * len(valid_spd)))]
        p95 = valid_spd[min(len(valid_spd) - 1, int(0.95 * len(valid_spd)))]
        min_speed_mps = p05
        max_speed_mps = max(p95, p05 + 1e-3)  # ensure range > 0
    else:
        min_speed_mps = 0.0
        max_speed_mps = 1.0

    # Project trackpoints to scene XY, removing duplicates.
    # Carry is_reconstructed and per-point speed alongside each projected point.
    raw: list[tuple[float, float, bool, float]] = []
    for i, tp in enumerate(tps):
        x = (tp.longitude - grid.min_lon) / (grid.max_lon - grid.min_lon) * lon_m
        y = (tp.latitude - grid.min_lat) / (grid.max_lat - grid.min_lat) * lat_m
        if raw and abs(x - raw[-1][0]) < 1e-4 and abs(y - raw[-1][1]) < 1e-4:
            continue
        raw.append((x, y, tp.is_reconstructed, tp_speeds[i]))

    if len(raw) < 4:
        # Too few points for a spline: fall back to raw points with slope=0
        points = [
            {
                "x": x,
                "y": y,
                "z": _elev_at_xy(x, y, grid, lat_m, lon_m),
                "slope": 0.0,
                "speed": spd,
                "is_reconstructed": rec,
            }
            for x, y, rec, spd in raw
        ]
        track_path.write_text(json.dumps(points))
        return track_path, points, min_speed_mps, max_speed_mps

    pts = np.array([(x, y) for x, y, _, _ in raw])
    raw_flags: list[bool] = [rec for _, _, rec, _ in raw]
    raw_speeds: list[float] = [spd for _, _, _, spd in raw]

    # Fit parametric cubic B-spline through all trackpoints.
    # splprep returns u: the parameter values corresponding to each input point.
    tck, u = splprep([pts[:, 0], pts[:, 1]], s=0, k=3)

    # Compute total arc length on a dense evaluation
    t_fine = np.linspace(0, 1, max(10_000, len(pts) * 100))
    _ev_fine = splev(t_fine, tck)
    xs_fine, ys_fine = (
        np.asarray(_ev_fine[0], dtype=float),
        np.asarray(_ev_fine[1], dtype=float),
    )
    dx = np.diff(xs_fine)
    dy = np.diff(ys_fine)
    cumlen = np.concatenate([[0.0], np.cumsum(np.sqrt(dx**2 + dy**2))])
    total_length = cumlen[-1]

    # Resample at equal spacing
    n_samples = max(2, int(total_length / ribbon_spacing_m) + 1)
    sample_dists = np.linspace(0, total_length, n_samples)
    sample_t = np.interp(sample_dists, cumlen, t_fine)

    _ev = splev(sample_t, tck)
    xs, ys = np.asarray(_ev[0], dtype=float), np.asarray(_ev[1], dtype=float)
    # Derivatives for slope computation
    _dev = splev(sample_t, tck, der=1)
    dxs, dys = np.asarray(_dev[0], dtype=float), np.asarray(_dev[1], dtype=float)

    # For each ribbon sample, determine whether it lies in a reconstructed segment.
    # A sample at parameter t falls in segment [u[j], u[j+1]]; it is reconstructed
    # if either bounding input point is reconstructed.
    u_arr = np.asarray(u)

    points: list[dict[str, Any]] = []
    for i in range(n_samples):
        x, y = float(xs[i]), float(ys[i])
        z = _elev_at_xy(x, y, grid, lat_m, lon_m)
        # Slope: rise over run using spline tangent and DEM elevation difference
        # Sample elevation slightly ahead and behind for accurate grade
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
        # Map sample parameter back to its input segment; propagate
        # is_reconstructed and linearly interpolate speed.
        j = int(np.searchsorted(u_arr, sample_t[i], side="right")) - 1
        j = max(0, min(j, len(raw_flags) - 2))
        is_rec = raw_flags[j] or raw_flags[j + 1]
        seg_len = float(u_arr[j + 1]) - float(u_arr[j])
        frac = (
            (float(sample_t[i]) - float(u_arr[j])) / seg_len if seg_len > 1e-12 else 0.0
        )
        spd = raw_speeds[j] + frac * (raw_speeds[j + 1] - raw_speeds[j])
        points.append(
            {
                "x": x,
                "y": y,
                "z": z,
                "slope": slope,
                "speed": spd,
                "is_reconstructed": is_rec,
            }
        )

    track_path.write_text(json.dumps(points))
    return track_path, points, min_speed_mps, max_speed_mps


def _elev_at_xy(
    x: float, y: float, grid: "ElevationGrid", lat_m: float, lon_m: float
) -> float:
    lat = grid.min_lat + y / lat_m * (grid.max_lat - grid.min_lat)
    lon = grid.min_lon + x / lon_m * (grid.max_lon - grid.min_lon)
    return grid.elevation_at(lat, lon)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def _write_pins(
    pipeline: "Pipeline", work_dir: Path, settings: dict[str, Any] | None = None
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
    r_head = marker_r * 0.8  # radius of the circular head
    # Gap between adjacent pins: small fixed margin so they almost touch
    PIN_GAP_M = max(2.0, marker_r * 0.1)

    # Collect raw pins, keyed by trackpoint_index to detect collisions
    from collections import defaultdict

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
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

    pins: list[dict[str, Any]] = []
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
                chord = 2 * r_head + PIN_GAP_M
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
    settings: dict[str, Any],
    ribbon_points: list[dict[str, Any]],
) -> dict[str, Any]:
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
    pauses: list[dict[str, Any]] = []

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


def _resolve_pin_color(settings: dict[str, Any]) -> str:
    """Return a #rrggbb color string for the pin from settings."""
    from georeel.ui.color_picker_dialog import get_color_hex  # type: ignore[import]

    color_id = settings.get("pins/color", "ForestGreen")
    if color_id == "custom":
        return settings.get("pins/custom_color", "#228B22")
    return get_color_hex(color_id, "#228B22")


def _resolve_marker_color(settings: dict[str, Any]) -> str:
    """Return a #rrggbb color string for the track marker from settings."""
    from georeel.ui.color_picker_dialog import get_color_hex  # type: ignore[import]

    color_id = settings.get("marker/color", "Navy")
    if color_id == "custom":
        return settings.get("marker/custom_color", "#ADD8E6")
    return get_color_hex(color_id, "#ADD8E6")


def _complementary_color(hex_color: str) -> str:
    """Return the complementary (hue-opposite) color as #rrggbb.

    Complement is computed by rotating hue 180° in HSV space, keeping
    saturation and value intact so the two colors are equally vivid.
    """
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0

    cmax = max(r, g, b)
    cmin = min(r, g, b)
    delta = cmax - cmin

    # Hue (0–360)
    if delta == 0:
        h = 0.0
    elif cmax == r:
        h = 60.0 * (((g - b) / delta) % 6)
    elif cmax == g:
        h = 60.0 * ((b - r) / delta + 2)
    else:
        h = 60.0 * ((r - g) / delta + 4)

    s = 0.0 if cmax == 0 else delta / cmax
    v = cmax

    # Rotate hue by 180°
    h = (h + 180.0) % 360.0

    # HSV → RGB
    c = v * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = v - c
    sector = int(h / 60) % 6
    if sector == 0:
        r2, g2, b2 = c, x, 0.0
    elif sector == 1:
        r2, g2, b2 = x, c, 0.0
    elif sector == 2:
        r2, g2, b2 = 0.0, c, x
    elif sector == 3:
        r2, g2, b2 = 0.0, x, c
    elif sector == 4:
        r2, g2, b2 = x, 0.0, c
    else:
        r2, g2, b2 = c, 0.0, x

    ri, gi, bi = round((r2 + m) * 255), round((g2 + m) * 255), round((b2 + m) * 255)
    return f"#{ri:02x}{gi:02x}{bi:02x}"


def _write_dem(grid: ElevationGrid, work_dir: Path) -> tuple[Path, Path]:
    mean_lat_rad = math.radians((grid.min_lat + grid.max_lat) / 2)
    lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
    lon_m = (grid.max_lon - grid.min_lon) * 111_320.0 * math.cos(mean_lat_rad)
    meta = {
        "rows": grid.rows,
        "cols": grid.cols,
        "min_lat": grid.min_lat,
        "max_lat": grid.max_lat,
        "min_lon": grid.min_lon,
        "max_lon": grid.max_lon,
        "lat_m": lat_m,
        "lon_m": lon_m,
    }
    meta_path = work_dir / "dem_meta.json"
    data_path = work_dir / "dem_data.bin"
    meta_path.write_text(json.dumps(meta))
    data_path.write_bytes(grid.to_bytes())
    return meta_path, data_path


_MAX_TILE_PIXELS = (
    400_000_000  # ~1.2 GB as RGB — safely below Blender's 2 GB pack limit
)


def _write_texture_tiles(
    texture: SatelliteTexture,
    grid: ElevationGrid,
    work_dir: Path,
    max_texture_pixels: int | None = None,
    tile_progress_cb: Callable[[int, int], None] | None = None,
    status_cb: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Dispatch to the tile-cache or PIL-image tiling path."""
    if texture._tile_cache is not None:
        return _write_texture_tiles_from_cache(
            texture,
            grid,
            work_dir,
            max_texture_pixels=max_texture_pixels,
            tile_progress_cb=tile_progress_cb,
            status_cb=status_cb,
            cancel_check=cancel_check,
        )
    return _write_texture_tiles_from_image(
        texture,
        grid,
        work_dir,
        max_texture_pixels=max_texture_pixels,
        tile_progress_cb=tile_progress_cb,
        status_cb=status_cb,
        cancel_check=cancel_check,
    )


def _write_texture_tiles_from_cache(
    texture: SatelliteTexture,
    grid: ElevationGrid,
    work_dir: Path,
    max_texture_pixels: int | None = None,
    tile_progress_cb: Callable[[int, int], None] | None = None,
    status_cb: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Build Blender terrain tiles by compositing from the XYZ tile cache.

    For each terrain tile the compositor reads only the XYZ source tiles that
    overlap its geographic extent, so the full-resolution satellite canvas is
    never held in RAM.  Peak memory per call is one composited terrain tile
    (bounded by _MAX_TILE_PIXELS) plus the working XYZ tiles (~few MB).

    The manifest format is identical to _write_texture_tiles_from_image, so
    write_png() reassembly and the Blender script are unchanged.
    """
    import math as _math

    from PIL.Image import Resampling

    from .bounding_box import BoundingBox

    cache: Any = texture._tile_cache  # type: ignore[assignment]

    lat_span = grid.max_lat - grid.min_lat
    lon_span = grid.max_lon - grid.min_lon
    dem_rows = grid.rows
    dem_cols = grid.cols

    # Native pixel dimensions for the DEM-extent region at the XYZ zoom level.
    dem_bbox = BoundingBox(grid.min_lat, grid.max_lat, grid.min_lon, grid.max_lon)
    tex_w, tex_h = cache.canvas_size(dem_bbox)
    total_pixels = tex_w * tex_h

    # Apply preview downscale if requested.
    if max_texture_pixels is not None and total_pixels > max_texture_pixels:
        scale = _math.sqrt(max_texture_pixels / total_pixels)
        tex_w = max(1, int(tex_w * scale))
        tex_h = max(1, int(tex_h * scale))
        total_pixels = tex_w * tex_h
        _log.info("[satellite] Preview downscale: virtual %d×%d px", tex_w, tex_h)

    # Determine Blender terrain tile grid (same logic as the image path).
    n_tiles_needed = _math.ceil(total_pixels / _MAX_TILE_PIXELS)
    if n_tiles_needed <= 1:
        n_tile_cols, n_tile_rows = 1, 1
    else:
        n_tile_cols = max(1, _math.ceil(_math.sqrt(n_tiles_needed * tex_w / tex_h)))
        n_tile_rows = max(1, _math.ceil(n_tiles_needed / n_tile_cols))
        while True:
            tile_w = _math.ceil(tex_w / n_tile_cols)
            tile_h = _math.ceil(tex_h / n_tile_rows)
            if tile_w * tile_h <= _MAX_TILE_PIXELS:
                break
            if n_tile_cols * tex_h < n_tile_rows * tex_w:
                n_tile_cols += 1
            else:
                n_tile_rows += 1

    _log.info(
        "[satellite] Compositing %d×%d px texture from tile cache → %d×%d Blender tiles",
        tex_w,
        tex_h,
        n_tile_rows,
        n_tile_cols,
    )
    if status_cb:
        status_cb(f"Compositing satellite texture ({n_tile_rows * n_tile_cols} tiles)…")

    tiles_dir = work_dir / "sat_tiles"
    tiles_dir.mkdir(exist_ok=True)

    total_tiles = n_tile_rows * n_tile_cols
    tile_idx = 0
    tiles = []
    for ti in range(n_tile_rows):
        for tj in range(n_tile_cols):
            # Pixel bounds within the virtual (tex_w×tex_h) canvas — linear fractions
            # so adjacent tiles share their boundary pixel exactly.
            px_left = tj * tex_w // n_tile_cols
            px_right = (tj + 1) * tex_w // n_tile_cols
            px_top = ti * tex_h // n_tile_rows
            px_bottom = (ti + 1) * tex_h // n_tile_rows
            tile_px_w = max(1, px_right - px_left)
            tile_px_h = max(1, px_bottom - px_top)

            # DEM row/col bounds (shared boundary so terrain seams are seamless).
            dem_r_start = round(ti * (dem_rows - 1) / n_tile_rows)
            dem_r_end = round((ti + 1) * (dem_rows - 1) / n_tile_rows)
            dem_c_start = round(tj * (dem_cols - 1) / n_tile_cols)
            dem_c_end = round((tj + 1) * (dem_cols - 1) / n_tile_cols)

            # Geographic bounds from DEM fractions — works regardless of
            # whether satellite and DEM extents differ.
            tile_max_lat = grid.max_lat - dem_r_start / (dem_rows - 1) * lat_span
            tile_min_lat = grid.max_lat - dem_r_end / (dem_rows - 1) * lat_span
            tile_min_lon = grid.min_lon + dem_c_start / (dem_cols - 1) * lon_span
            tile_max_lon = grid.min_lon + dem_c_end / (dem_cols - 1) * lon_span
            tile_bbox = BoundingBox(
                tile_min_lat, tile_max_lat, tile_min_lon, tile_max_lon
            )

            if cancel_check and cancel_check():
                raise SceneBuildError("Cancelled.")

            tile_path = tiles_dir / f"{ti}_{tj}.png"
            with PIL_LOCK:
                tile_img = cache.composite(tile_bbox)
                # Resize to the target grid size for consistent write_png reassembly.
                if tile_img.size != (tile_px_w, tile_px_h):
                    tile_img = tile_img.resize(
                        (tile_px_w, tile_px_h), resample=Resampling.LANCZOS
                    )
                if tile_img.mode != "RGB":
                    tile_img = tile_img.convert("RGB")
                tile_img.save(str(tile_path), format="PNG", optimize=False)
            del tile_img

            tile_idx += 1
            if tile_progress_cb:
                tile_progress_cb(tile_idx, total_tiles)

            tiles.append(
                {
                    "ti": ti,
                    "tj": tj,
                    "path": str(tile_path),
                    "px_left": px_left,
                    "px_top": px_top,
                    "px_right": px_right,
                    "px_bottom": px_bottom,
                    "dem_r_start": dem_r_start,
                    "dem_r_end": dem_r_end,
                    "dem_c_start": dem_c_start,
                    "dem_c_end": dem_c_end,
                }
            )

    manifest = {
        "n_tile_rows": n_tile_rows,
        "n_tile_cols": n_tile_cols,
        "image_width": tex_w,
        "image_height": tex_h,
        "tiles": tiles,
    }
    manifest_path = work_dir / "sat_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path, manifest


def _write_texture_tiles_from_image(
    texture: SatelliteTexture,
    grid: ElevationGrid,
    work_dir: Path,
    max_texture_pixels: int | None = None,
    tile_progress_cb: Callable[[int, int], None] | None = None,
    status_cb: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Save the satellite texture as tiled PNG files and write a manifest JSON.

    Splits the image into an N×M grid so each tile stays under _MAX_TILE_PIXELS,
    working around Blender's 2 GB pack limit.  Adjacent tiles share their border
    row/column of DEM vertices so no seam appears in the rendered terrain.

    The satellite and DEM may cover slightly different extents when cached data
    from a previous run is reused; the crop offset is applied per-tile so no
    intermediate full-image copy is created.

    Returns (manifest_path, manifest_dict).  The manifest dict includes pixel
    bounds for each tile so the image can be reassembled from tiles on demand
    (e.g. for project save after the PIL image has been freed).
    """
    import math as _math

    img = texture.image
    if img is None:
        # Lazy-loaded from a project ZIP — decode now (only happens on first
        # scene build after loading a project without re-fetching the texture).
        if texture._source_zip is not None:
            if status_cb:
                status_cb("Loading satellite texture from project file…")
            img = texture.load_image()
        else:
            raise SceneBuildError(
                "Satellite texture image is not available (was it freed before build_scene?)."
            )

    # Compute crop offsets for DEM-extent alignment without allocating a
    # second full-size image.  The offsets are applied per-tile below so the
    # large intermediate copy is never created.
    src_left = src_top = 0
    src_w, src_h = img.size

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
        c_left = int(round((grid.min_lon - texture.min_lon) / lon_span * w))
        c_right = int(round((grid.max_lon - texture.min_lon) / lon_span * w))
        c_top = int(round((texture.max_lat - grid.max_lat) / lat_span * h))
        c_bottom = int(round((texture.max_lat - grid.min_lat) / lat_span * h))
        c_left, c_right = max(0, c_left), min(w, c_right)
        c_top, c_bottom = max(0, c_top), min(h, c_bottom)
        if c_right > c_left and c_bottom > c_top:
            src_left, src_top = c_left, c_top
            src_w = c_right - c_left
            src_h = c_bottom - c_top

    tex_w, tex_h = src_w, src_h
    total_pixels = tex_w * tex_h

    # Downscale for preview if requested — this IS a new allocation but the
    # result is small (max_texture_pixels, e.g. 8 MP), so it is fine.
    if max_texture_pixels is not None and total_pixels > max_texture_pixels:
        scale = _math.sqrt(max_texture_pixels / total_pixels)
        new_w = max(1, int(tex_w * scale))
        new_h = max(1, int(tex_h * scale))
        _log.info(
            "[satellite] Preview downscale: %dx%d → %dx%d px",
            tex_w,
            tex_h,
            new_w,
            new_h,
        )
        from PIL.Image import Resampling

        with PIL_LOCK:
            # We must materialise the crop first when there is an offset.
            region = img.crop((src_left, src_top, src_left + tex_w, src_top + tex_h))
            img = region.resize((new_w, new_h), resample=Resampling.BICUBIC)
            del region
        src_left = src_top = 0
        tex_w, tex_h = new_w, new_h
        total_pixels = tex_w * tex_h

    # Determine tile grid dimensions — aim for roughly square tiles
    n_tiles_needed = _math.ceil(total_pixels / _MAX_TILE_PIXELS)
    if n_tiles_needed <= 1:
        n_tile_cols, n_tile_rows = 1, 1
    else:
        n_tile_cols = max(1, _math.ceil(_math.sqrt(n_tiles_needed * tex_w / tex_h)))
        n_tile_rows = max(1, _math.ceil(n_tiles_needed / n_tile_cols))
        # Adjust until each tile fits
        while True:
            tile_w = _math.ceil(tex_w / n_tile_cols)
            tile_h = _math.ceil(tex_h / n_tile_rows)
            if tile_w * tile_h <= _MAX_TILE_PIXELS:
                break
            if n_tile_cols * tex_h < n_tile_rows * tex_w:
                n_tile_cols += 1
            else:
                n_tile_rows += 1

    _log.info(
        "[satellite] Splitting %dx%d px texture into %d×%d tiles",
        tex_w,
        tex_h,
        n_tile_rows,
        n_tile_cols,
    )
    if status_cb:
        status_cb(
            f"Splitting satellite texture into {n_tile_rows * n_tile_cols} tiles…"
        )

    tiles_dir = work_dir / "sat_tiles"
    tiles_dir.mkdir(exist_ok=True)

    dem_rows = grid.rows
    dem_cols = grid.cols

    total_tiles = n_tile_rows * n_tile_cols
    tile_idx = 0
    tiles = []
    for ti in range(n_tile_rows):
        for tj in range(n_tile_cols):
            # Image pixel bounds within the (possibly offset) source region
            px_left = src_left + tj * tex_w // n_tile_cols
            px_right = src_left + (tj + 1) * tex_w // n_tile_cols
            px_top = src_top + ti * tex_h // n_tile_rows
            px_bottom = src_top + (ti + 1) * tex_h // n_tile_rows

            # DEM row/col bounds (inclusive both ends so adjacent tiles share boundary)
            dem_c_start = round((px_left - src_left) / tex_w * (dem_cols - 1))
            dem_c_end = round((px_right - src_left) / tex_w * (dem_cols - 1))
            dem_r_start = round((px_top - src_top) / tex_h * (dem_rows - 1))
            dem_r_end = round((px_bottom - src_top) / tex_h * (dem_rows - 1))

            if cancel_check and cancel_check():
                raise SceneBuildError("Cancelled.")

            tile_path = tiles_dir / f"{ti}_{tj}.png"
            # Write directly to disk — no BytesIO round-trip, no convert("RGB")
            # copy (img is guaranteed RGB from the fetch pipeline).
            with PIL_LOCK:
                tile_img = img.crop((px_left, px_top, px_right, px_bottom))
                if tile_img.mode != "RGB":
                    tile_img = tile_img.convert("RGB")
                tile_img.save(str(tile_path), format="PNG", optimize=False)
            del tile_img

            tile_idx += 1
            if tile_progress_cb:
                tile_progress_cb(tile_idx, total_tiles)

            tiles.append(
                {
                    "ti": ti,
                    "tj": tj,
                    "path": str(tile_path),
                    # Pixel bounds in the output image coordinate space (origin = src_left, src_top)
                    # stored so the image can be reassembled from tiles on demand.
                    "px_left": px_left - src_left,
                    "px_top": px_top - src_top,
                    "px_right": px_right - src_left,
                    "px_bottom": px_bottom - src_top,
                    "dem_r_start": dem_r_start,
                    "dem_r_end": dem_r_end,
                    "dem_c_start": dem_c_start,
                    "dem_c_end": dem_c_end,
                }
            )

    manifest = {
        "n_tile_rows": n_tile_rows,
        "n_tile_cols": n_tile_cols,
        "image_width": tex_w,
        "image_height": tex_h,
        "tiles": tiles,
    }
    manifest_path = work_dir / "sat_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path, manifest
