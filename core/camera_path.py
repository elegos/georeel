"""
Stage 6 — Camera Path Generator.

Converts the GPX trackpoints into a sequence of CameraKeyframes describing a
smooth fly-through animation.  All positions are in the same metre-based
scene coordinate system used by the 3D scene builder:

    X = (lon - min_lon) / (max_lon - min_lon) * lon_m    [east]
    Y = (lat - min_lat) / (max_lat - min_lat) * lat_m    [north]
    Z = elevation (metres)
"""

import math
from typing import Sequence

import numpy as np
from scipy.interpolate import splev, splprep
from scipy.ndimage import gaussian_filter1d

from .bounding_box import BoundingBox
from .camera_keyframe import CameraKeyframe
from .elevation_grid import ElevationGrid
from .pipeline import Pipeline
from .trackpoint import Trackpoint

# Douglas-Peucker tolerance (metres).  Points closer than this to the
# straight line between their neighbours are removed.
_DP_EPSILON_M = 20.0

# Look-ahead distance used when computing the look-at point (metres).
_LOOK_AHEAD_M = 100.0


class CameraPathError(Exception):
    pass


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def build_camera_path(pipeline: Pipeline, settings: dict) -> list[CameraKeyframe]:
    """Generate CameraKeyframes for the fly-through.

    *settings* is the dict returned by ``get_render_settings(QSettings)``
    from ``ui.render_settings_dialog``.
    """
    if not pipeline.trackpoints:
        raise CameraPathError("No trackpoints available.")
    if pipeline.elevation_grid is None:
        raise CameraPathError("Elevation grid is required for camera height.")

    grid  = pipeline.elevation_grid

    fps             = int(settings.get("render/fps", 30))
    speed_mps       = float(settings.get("render/camera_speed_mps", 80.0))
    path_method     = settings.get("render/path_smoothing", "spline")
    height_mode     = settings.get("render/camera_height_mode", "dem_fixed")
    height_offset   = float(settings.get("render/camera_height_offset", 200))
    orient_mode       = settings.get("render/camera_orientation", "tangent")
    tilt_deg          = float(settings.get("render/camera_tilt_deg", 45))
    lookahead_s       = float(settings.get("render/tangent_lookahead_s", 60.0))
    tangent_weight    = settings.get("render/tangent_weight", "linear")
    pause_mode      = settings.get("render/photo_pause_mode", "hold")
    pause_duration  = float(settings.get("render/photo_pause_duration", 3.0))

    # Scene coordinate system matches the elevation grid's extent (which may be
    # expanded beyond the track bbox by the frustum margin added in stage 3+4).
    bbox = BoundingBox(grid.min_lat, grid.max_lat, grid.min_lon, grid.max_lon)

    # Physical dimensions of the scene bbox in metres
    mean_lat_rad = math.radians((bbox.min_lat + bbox.max_lat) / 2)
    lat_m = (bbox.max_lat - bbox.min_lat) * 111_320.0
    lon_m = (bbox.max_lon - bbox.min_lon) * 111_320.0 * math.cos(mean_lat_rad)

    # ------------------------------------------------------------------ #
    # 1. Trackpoints → scene XY                                           #
    # ------------------------------------------------------------------ #

    pts = np.array([_tp_to_xy(tp, bbox, lat_m, lon_m) for tp in pipeline.trackpoints])
    pts = _remove_duplicates(pts)

    if len(pts) < 4:
        raise CameraPathError(
            "Track has fewer than 4 unique points; cannot fit a spline."
        )

    # ------------------------------------------------------------------ #
    # 2. Path smoothing                                                    #
    # ------------------------------------------------------------------ #

    if path_method == "dp_spline":
        simplified = _douglas_peucker(pts, _DP_EPSILON_M)
        # Fall back to full set if simplification removes too many points
        pts = simplified if len(simplified) >= 4 else pts

    # Fit parametric cubic B-spline (s=0 → passes through every point)
    tck, _ = splprep([pts[:, 0], pts[:, 1]], s=0, k=3)

    # ------------------------------------------------------------------ #
    # 3. Resample at equal arc-length (one sample per frame)              #
    # ------------------------------------------------------------------ #

    t_fine = np.linspace(0, 1, max(10_000, len(pts) * 100))
    xs_fine, ys_fine = splev(t_fine, tck)
    dx_fine = np.diff(xs_fine)
    dy_fine = np.diff(ys_fine)
    cumlen = np.concatenate([[0.0], np.cumsum(np.sqrt(dx_fine**2 + dy_fine**2))])
    total_length = cumlen[-1]

    dist_per_frame = speed_mps / fps
    n_frames = max(2, int(total_length / dist_per_frame))
    sample_dists = np.linspace(0, total_length, n_frames)
    sample_t = np.interp(sample_dists, cumlen, t_fine)

    xs, ys = splev(sample_t, tck)

    # ------------------------------------------------------------------ #
    # 4. Terrain heights and tilt                                         #
    # ------------------------------------------------------------------ #

    tilt_rad = math.radians(tilt_deg)

    terrain_zs = np.array([
        _height_at(xs[i], ys[i], grid, bbox, lat_m, lon_m, height_mode, 0.0)
        for i in range(n_frames)
    ])

    # ------------------------------------------------------------------ #
    # 5. Forward directions (horizontal heading at each frame)            #
    # ------------------------------------------------------------------ #

    lookahead_frames = max(1, round(lookahead_s * fps))

    if orient_mode == "tangent":
        forward_dirs = _compute_forward_dirs_tangent(
            xs, ys, lookahead_frames, tangent_weight,
        )
    else:
        dx_dt, dy_dt = splev(sample_t, tck, der=1)
        forward_dirs = _compute_forward_dirs_spline(
            xs, ys, dx_dt, dy_dt, orient_mode,
        )

    # ------------------------------------------------------------------ #
    # 6. Camera positions and look-at points                              #
    #                                                                     #
    # Camera is placed height_offset metres (slant distance) behind and  #
    # above the track marker at the given tilt angle:                     #
    #   horiz_back = d * cos(tilt)  — behind the marker                  #
    #   height     = d * sin(tilt)  — above the terrain at the marker    #
    # Look-at = the marker itself, so pitch == -tilt exactly.            #
    # ------------------------------------------------------------------ #

    horiz_back = height_offset * math.cos(tilt_rad)
    height_above = height_offset * math.sin(tilt_rad)

    # Smooth the forward direction components with a 2-second window before
    # computing camera offsets.  This removes end-of-track rotation jumps
    # (where the lookahead window shrinks to 0 frames and the direction
    # collapses to a single-point estimate) and any mid-track oscillations.
    nxs = np.array([forward_dirs[i][0] for i in range(n_frames)])
    nys = np.array([forward_dirs[i][1] for i in range(n_frames)])
    sigma_dir = max(1.0, fps * 2)
    nxs = gaussian_filter1d(nxs, sigma=sigma_dir, mode="nearest")
    nys = gaussian_filter1d(nys, sigma=sigma_dir, mode="nearest")
    mags = np.hypot(nxs, nys)
    mags = np.where(mags > 1e-10, mags, 1.0)
    nxs /= mags
    nys /= mags

    cam_xs_raw = xs - nxs * horiz_back
    cam_ys_raw = ys - nys * horiz_back
    cam_zs_raw = terrain_zs + height_above

    # Light positional smoothing to remove any remaining jitter from DEM noise.
    sigma = max(1.0, fps / 2)
    cam_xs = gaussian_filter1d(cam_xs_raw, sigma=sigma, mode="nearest")
    cam_ys = gaussian_filter1d(cam_ys_raw, sigma=sigma, mode="nearest")
    cam_zs = gaussian_filter1d(cam_zs_raw, sigma=sigma, mode="nearest")

    # Also smooth look-at Z to suppress DEM noise (look-at XY stays on the spline)
    look_at_zs = gaussian_filter1d(terrain_zs, sigma=sigma, mode="nearest")

    # Frame numbers start at 1 to match Blender's default timeline origin and
    # the Build modifier's frame_start=1 in build_scene.py.
    keyframes: list[CameraKeyframe] = [
        CameraKeyframe(
            frame=i + 1,
            x=float(cam_xs[i]),
            y=float(cam_ys[i]),
            z=float(cam_zs[i]),
            look_at_x=float(xs[i]),
            look_at_y=float(ys[i]),
            look_at_z=float(look_at_zs[i]),
        )
        for i in range(n_frames)
    ]

    # ------------------------------------------------------------------ #
    # 7. Insert pause keyframes at photo waypoints                         #
    # ------------------------------------------------------------------ #

    if pipeline.match_results:
        keyframes = _insert_pauses(
            keyframes, pipeline, bbox, lat_m, lon_m,
            grid, height_mode, height_offset,
            fps, pause_duration, pause_mode,
        )

    return keyframes


# ------------------------------------------------------------------
# Coordinate helpers
# ------------------------------------------------------------------

def _tp_to_xy(tp: Trackpoint, bbox: BoundingBox,
              lat_m: float, lon_m: float) -> np.ndarray:
    x = (tp.longitude - bbox.min_lon) / (bbox.max_lon - bbox.min_lon) * lon_m
    y = (tp.latitude  - bbox.min_lat) / (bbox.max_lat - bbox.min_lat) * lat_m
    return np.array([x, y])


def _remove_duplicates(pts: np.ndarray) -> np.ndarray:
    keep = np.concatenate([[True], np.any(np.diff(pts, axis=0) != 0, axis=1)])
    return pts[keep]


# ------------------------------------------------------------------
# Douglas-Peucker
# ------------------------------------------------------------------

def _douglas_peucker(pts: np.ndarray, epsilon: float) -> np.ndarray:
    if len(pts) <= 2:
        return pts

    start, end = pts[0], pts[-1]
    seg = end - start
    seg_len = np.linalg.norm(seg)

    if seg_len < 1e-10:
        dists = np.linalg.norm(pts - start, axis=1)
    else:
        t = np.clip(np.dot(pts - start, seg) / seg_len**2, 0.0, 1.0)
        proj = start + np.outer(t, seg)
        dists = np.linalg.norm(pts - proj, axis=1)

    max_idx = int(np.argmax(dists))

    if dists[max_idx] > epsilon:
        left  = _douglas_peucker(pts[: max_idx + 1], epsilon)
        right = _douglas_peucker(pts[max_idx:],       epsilon)
        return np.vstack([left[:-1], right])

    return np.array([pts[0], pts[-1]])


# ------------------------------------------------------------------
# Elevation helpers
# ------------------------------------------------------------------

def _height_at(x: float, y: float,
               grid: ElevationGrid, bbox: BoundingBox,
               lat_m: float, lon_m: float,
               height_mode: str, height_offset: float) -> float:
    lat = bbox.min_lat + y / lat_m * (bbox.max_lat - bbox.min_lat)
    lon = bbox.min_lon + x / lon_m * (bbox.max_lon - bbox.min_lon)
    if height_mode == "dem_smooth":
        elev = _smooth_elevation(grid, lat, lon)
    else:
        elev = grid.elevation_at(lat, lon)
    return elev + height_offset


def _smooth_elevation(grid: ElevationGrid, lat: float, lon: float) -> float:
    """Mean elevation over a 3×3 neighbourhood (1.5× grid spacing)."""
    dlat = (grid.max_lat - grid.min_lat) / (grid.rows - 1) * 1.5
    dlon = (grid.max_lon - grid.min_lon) / (grid.cols - 1) * 1.5
    samples = [
        grid.elevation_at(lat + r * dlat, lon + c * dlon)
        for r in (-1, 0, 1)
        for c in (-1, 0, 1)
    ]
    return sum(samples) / len(samples)


# ------------------------------------------------------------------
# Orientation helpers
# ------------------------------------------------------------------

def _compute_forward_dirs_tangent(
    xs: np.ndarray, ys: np.ndarray,
    lookahead_frames: int, weight_mode: str,
) -> list[tuple[float, float]]:
    """Weighted-average forward direction using upcoming track positions."""
    n = len(xs)
    dirs: list[tuple[float, float]] = []

    for i in range(n):
        j_start = i + 1
        j_end   = min(i + lookahead_frames + 1, n)

        if j_start >= n:
            dirs.append(dirs[-1] if dirs else (0.0, 1.0))
            continue

        window = np.arange(j_start, j_end)
        k = len(window)
        t = np.linspace(0.0, 1.0, k)
        if weight_mode == "uniform":
            w = np.ones(k)
        elif weight_mode == "exponential":
            w = np.exp(-3.0 * t)
        else:  # linear
            w = 1.0 - t
        w /= w.sum()

        tx = float(np.dot(w, xs[window]))
        ty = float(np.dot(w, ys[window]))
        dx = tx - float(xs[i])
        dy = ty - float(ys[i])
        norm = math.sqrt(dx * dx + dy * dy)
        if norm > 1e-6:
            dirs.append((dx / norm, dy / norm))
        else:
            dirs.append(dirs[-1] if dirs else (0.0, 1.0))

    return dirs


def _compute_forward_dirs_spline(
    xs: np.ndarray, ys: np.ndarray,
    dx_dt: np.ndarray, dy_dt: np.ndarray,
    orient_mode: str,
) -> list[tuple[float, float]]:
    """Forward direction from spline tangent or next-point vector."""
    n = len(xs)
    dirs: list[tuple[float, float]] = []

    for i in range(n):
        if orient_mode == "lookat" and i + 1 < n:
            dx = float(xs[i + 1] - xs[i])
            dy = float(ys[i + 1] - ys[i])
        else:
            dx, dy = float(dx_dt[i]), float(dy_dt[i])

        norm = math.sqrt(dx * dx + dy * dy)
        if norm > 1e-10:
            dirs.append((dx / norm, dy / norm))
        else:
            dirs.append(dirs[-1] if dirs else (0.0, 1.0))

    return dirs



# ------------------------------------------------------------------
# Photo pause insertion
# ------------------------------------------------------------------

def _make_pause_block(ref: CameraKeyframe, photo_path: str,
                      pause_frames: int) -> list[CameraKeyframe]:
    return [
        CameraKeyframe(
            frame=0,
            x=ref.x, y=ref.y, z=ref.z,
            look_at_x=ref.look_at_x,
            look_at_y=ref.look_at_y,
            look_at_z=ref.look_at_z,
            is_pause=True,
            photo_path=photo_path,
        )
        for _ in range(pause_frames)
    ]


def _insert_pauses(
    keyframes: list[CameraKeyframe],
    pipeline: Pipeline,
    bbox: BoundingBox,
    lat_m: float, lon_m: float,
    grid: ElevationGrid,
    height_mode: str, height_offset: float,
    fps: int, pause_duration: float, pause_mode: str,
) -> list[CameraKeyframe]:
    pause_frames = max(1, round(pause_duration * fps))

    waypoints_pre:   list[tuple[float, str]] = []  # (sort_key, photo_path)
    waypoints_track: list[tuple[float, float, str]] = []  # (wx, wy, photo_path)
    waypoints_post:  list[tuple[float, str]] = []  # (sort_key, photo_path)

    for r in pipeline.match_results:
        if not (r.ok and r.trackpoint_index is not None):
            continue
        pos = r.position
        if pos == "pre":
            waypoints_pre.append((r.sort_key, r.photo_path))
        elif pos == "post":
            waypoints_post.append((r.sort_key, r.photo_path))
        else:
            tp = pipeline.trackpoints[r.trackpoint_index]
            wx, wy = _tp_to_xy(tp, bbox, lat_m, lon_m)
            waypoints_track.append((float(wx), float(wy), r.photo_path))

    if not waypoints_pre and not waypoints_track and not waypoints_post:
        return keyframes

    # ── Pre-track: camera holds at the first fly frame ──────────────────
    pre_block: list[CameraKeyframe] = []
    if waypoints_pre and keyframes:
        waypoints_pre.sort(key=lambda w: w[0])   # chronological (most negative first)
        ref = keyframes[0]
        for _, photo_path in waypoints_pre:
            pre_block.extend(_make_pause_block(ref, photo_path, pause_frames))

    # ── In-track: insert after the nearest look-at keyframe ─────────────
    if waypoints_track:
        kf_xy = np.array([(kf.look_at_x, kf.look_at_y) for kf in keyframes])
        insertions: list[tuple[int, CameraKeyframe]] = []
        for (wx, wy, photo_path) in waypoints_track:
            dists = np.sqrt((kf_xy[:, 0] - wx)**2 + (kf_xy[:, 1] - wy)**2)
            idx   = int(np.argmin(dists))
            ref   = keyframes[idx]
            insertions.append((idx, CameraKeyframe(
                frame=0,
                x=ref.x, y=ref.y, z=ref.z,
                look_at_x=ref.look_at_x,
                look_at_y=ref.look_at_y,
                look_at_z=ref.look_at_z,
                is_pause=True,
                photo_path=photo_path,
            )))
        # Insert from last to first so earlier indices stay valid
        insertions.sort(key=lambda e: e[0], reverse=True)
        for idx, pause_kf in insertions:
            keyframes = (keyframes[: idx + 1]
                         + _make_pause_block(pause_kf, pause_kf.photo_path, pause_frames)
                         + keyframes[idx + 1:])

    # ── Post-track: camera holds at the last fly frame ───────────────────
    post_block: list[CameraKeyframe] = []
    if waypoints_post and keyframes:
        waypoints_post.sort(key=lambda w: w[0])   # chronological
        ref = keyframes[-1]
        for _, photo_path in waypoints_post:
            post_block.extend(_make_pause_block(ref, photo_path, pause_frames))

    keyframes = pre_block + keyframes + post_block

    # Renumber frames sequentially starting at 1 (Blender timeline origin)
    for i, kf in enumerate(keyframes):
        kf.frame = i + 1

    return keyframes
