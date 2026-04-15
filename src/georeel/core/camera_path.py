"""
Stage 6 — Camera Path Generator.

Converts the GPX trackpoints into a sequence of CameraKeyframes describing a
smooth fly-through animation.  All positions are in the same metre-based
scene coordinate system used by the 3D scene builder:

    X = (lon - min_lon) / (max_lon - min_lon) * lon_m    [east]
    Y = (lat - min_lat) / (max_lat - min_lat) * lat_m    [north]
    Z = elevation (metres)
"""

import gc
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import numpy as np
from scipy.interpolate import splev, splprep
from scipy.ndimage import gaussian_filter1d
from scipy.signal import fftconvolve

_log = logging.getLogger(__name__)


def _rss_mb() -> float:
    """Resident memory of this process in MB (psutil optional)."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024 / 1024
    except Exception:
        return float("nan")


def _mem(label: str) -> None:
    _log.debug("[camera_path mem] %s — RSS %.0f MB", label, _rss_mb())


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


def build_camera_path(
    pipeline: Pipeline,
    settings: dict[str, Any],
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[CameraKeyframe]:
    """Generate CameraKeyframes for the fly-through.

    *settings* is the dict returned by ``get_render_settings(QSettings)``
    from ``ui.render_settings_dialog``.
    """
    if not pipeline.trackpoints:
        raise CameraPathError("No trackpoints available.")
    if pipeline.elevation_grid is None:
        raise CameraPathError("Elevation grid is required for camera height.")

    grid = pipeline.elevation_grid

    fps = int(settings.get("render/fps", 30))
    speed_mps = float(settings.get("render/camera_speed_mps", 80.0))
    path_method = settings.get("render/path_smoothing", "spline")
    height_mode = settings.get("render/camera_height_mode", "dem_fixed")
    height_offset = float(settings.get("render/camera_height_offset", 200))
    orient_mode = settings.get("render/camera_orientation", "tangent")
    tilt_deg = float(settings.get("render/camera_tilt_deg", 45))
    lookahead_s = float(settings.get("render/tangent_lookahead_s", 60.0))
    tangent_weight = settings.get("render/tangent_weight", "linear")
    pause_mode = settings.get("render/photo_pause_mode", "hold")
    pause_duration = float(settings.get("render/photo_pause_duration", 3.0))

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

    def _step(n: int, label: str) -> None:
        _log.info("[camera_path] step %d/7 — %s  (RSS %.0f MB)", n, label, _rss_mb())
        if progress_callback is not None:
            progress_callback(n, 7)

    _log.info(
        "[camera_path] starting  trackpoints=%d  fps=%d  speed=%.1f m/s  (RSS %.0f MB)",
        len(pipeline.trackpoints),
        fps,
        speed_mps,
        _rss_mb(),
    )

    # Log the geographic bounding box so bad coordinates (e.g. residual 0,0
    # holes) are immediately visible as an implausible extent.
    lats = [tp.latitude for tp in pipeline.trackpoints]
    lons = [tp.longitude for tp in pipeline.trackpoints]
    _log.info(
        "[camera_path] trackpoint bbox  lat [%.6f, %.6f]  lon [%.6f, %.6f]"
        "  scene %.1f km × %.1f km",
        min(lats),
        max(lats),
        min(lons),
        max(lons),
        lat_m / 1_000,
        lon_m / 1_000,
    )
    # Count any residual (0,0) points that the cleaner missed.
    zero_pts = sum(
        1 for tp in pipeline.trackpoints if tp.latitude == 0.0 and tp.longitude == 0.0
    )
    if zero_pts:
        _log.warning(
            "[camera_path] %d trackpoint(s) still have (0,0) coordinates — "
            "re-run GPX cleaning to remove them.",
            zero_pts,
        )
    del lats, lons

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
    n_pts = len(pts)
    tck, _ = splprep([pts[:, 0], pts[:, 1]], s=0, k=3)
    del pts

    _step(1, f"spline fitted  n_pts={n_pts}")

    # ------------------------------------------------------------------ #
    # 3. Resample at equal arc-length (one sample per frame)              #
    # ------------------------------------------------------------------ #

    # Cap the fine-grid size: 2 M points is more than enough arc-length
    # accuracy for any track length, and avoids gigabytes of temporaries.
    n_fine = min(max(10_000, n_pts * 100), 2_000_000)
    t_fine = np.linspace(0, 1, n_fine)
    xs_fine, ys_fine = splev(t_fine, tck)
    dx_fine = np.diff(xs_fine)
    dy_fine = np.diff(ys_fine)
    cumlen = np.concatenate([[0.0], np.cumsum(np.sqrt(dx_fine**2 + dy_fine**2))])
    total_length = cumlen[-1]
    del dx_fine, dy_fine, xs_fine, ys_fine  # free ~4 × n_fine floats

    # Sanity-check the computed track length before using it to size arrays.
    # A track longer than 2,000 km almost certainly contains uncleaned bad GPS
    # points (teleportation jumps).  Warn but continue — the user can re-clean
    # the GPX with a stricter jump threshold.
    _TRACK_WARN_KM = 2_000.0
    if total_length > _TRACK_WARN_KM * 1_000:
        _log.warning(
            "[camera_path] Track arc-length is %.0f km — this is almost certainly "
            "caused by bad GPS points that were not removed by the GPX cleaner.  "
            "Re-run GPX cleaning with a stricter max-jump or max-speed threshold.",
            total_length / 1_000,
        )

    dist_per_frame = speed_mps / fps
    n_frames_raw = max(2, int(total_length / dist_per_frame))

    # Hard cap: no more than 2 hours of video at the chosen fps.
    # Beyond this the memory cost of Python keyframe objects becomes extreme
    # (each CameraKeyframe is ~350 bytes; 100 M objects = ~35 GB).
    _MAX_VIDEO_S = 7_200  # 2 hours
    n_frames_max = fps * _MAX_VIDEO_S
    if n_frames_raw > n_frames_max:
        _log.warning(
            "[camera_path] Clamping n_frames from %d to %d (%.1f h cap). "
            "The track length (%.0f km) is likely inflated by bad GPS data.",
            n_frames_raw,
            n_frames_max,
            _MAX_VIDEO_S / 3600,
            total_length / 1_000,
        )
    n_frames = min(n_frames_raw, n_frames_max)
    sample_dists = np.linspace(0, total_length, n_frames)
    sample_t = np.interp(sample_dists, cumlen, t_fine)
    del t_fine, cumlen, sample_dists  # no longer needed

    _ev = splev(sample_t, tck)
    xs, ys = np.asarray(_ev[0], dtype=float), np.asarray(_ev[1], dtype=float)

    _step(
        2,
        f"resampled  n_fine={n_fine}  n_frames={n_frames}  length={total_length / 1000:.1f} km",
    )

    # ------------------------------------------------------------------ #
    # 4. Terrain heights and tilt                                         #
    # ------------------------------------------------------------------ #

    tilt_rad = math.radians(tilt_deg)

    # Vectorised: convert all frame positions to lat/lon, then batch-interpolate.
    terrain_zs = _height_at_batch(xs, ys, grid, bbox, lat_m, lon_m, height_mode)

    _step(3, "terrain heights done")

    # ------------------------------------------------------------------ #
    # 5. Forward directions (horizontal heading at each frame)            #
    # ------------------------------------------------------------------ #

    lookahead_frames = max(1, round(lookahead_s * fps))

    if orient_mode == "tangent":
        del sample_t  # not needed for tangent mode; free before large array ops
        _log.info(
            "[camera_path] computing tangent directions  lookahead_frames=%d  (RSS %.0f MB)",
            lookahead_frames,
            _rss_mb(),
        )
        nxs, nys = _compute_forward_dirs_tangent(
            xs,
            ys,
            lookahead_frames,
            tangent_weight,
            progress_callback=progress_callback,
            progress_base=3,
            progress_total=7,
        )
    else:
        _dev = splev(sample_t, tck, der=1)
        dx_dt, dy_dt = (
            np.asarray(_dev[0], dtype=float),
            np.asarray(_dev[1], dtype=float),
        )
        del sample_t
        if progress_callback is not None:
            progress_callback(4, 7)
        nxs, nys = _compute_forward_dirs_spline(
            xs,
            ys,
            dx_dt,
            dy_dt,
            orient_mode,
        )
        del dx_dt, dy_dt

    _step(5, "forward directions done")

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

    # Smooth the heading in angle space *before* computing the camera offset.
    # Component-wise Gaussian averaging of unit vectors produces near-zero
    # magnitudes at ~180° reversals (e.g. averaging (1,0) and (-1,0) gives
    # (0,0)), which causes undefined headings and the visible direction spikes
    # on tight curves.  Converting to an unwrapped angle signal first keeps it
    # continuous, so the Gaussian filter is always well-defined.
    sigma_dir = max(1.0, fps * 2)
    sigma = max(1.0, fps / 2)

    _log.info(
        "[camera_path] smoothing heading in angle space  sigma_dir=%.1f  (RSS %.0f MB)",
        sigma_dir,
        _rss_mb(),
    )
    angles_smooth = gaussian_filter1d(
        np.unwrap(np.arctan2(nys, nxs)), sigma_dir, mode="nearest"
    )
    nxs = np.cos(angles_smooth)
    nys = np.sin(angles_smooth)
    del angles_smooth

    cam_xs_raw = xs - nxs * horiz_back
    cam_ys_raw = ys - nys * horiz_back
    cam_zs_raw = terrain_zs + height_above
    del nxs, nys

    _log.info(
        "[camera_path] smoothing 4 position arrays in parallel  (RSS %.0f MB)",
        _rss_mb(),
    )
    with ThreadPoolExecutor(max_workers=4) as _gpool:
        f_cam_xs = _gpool.submit(gaussian_filter1d, cam_xs_raw, sigma, mode="nearest")
        f_cam_ys = _gpool.submit(gaussian_filter1d, cam_ys_raw, sigma, mode="nearest")
        f_cam_zs = _gpool.submit(gaussian_filter1d, cam_zs_raw, sigma, mode="nearest")
        f_look_at_z = _gpool.submit(
            gaussian_filter1d, terrain_zs, sigma, mode="nearest"
        )
        cam_xs = f_cam_xs.result()
        cam_ys = f_cam_ys.result()
        cam_zs = f_cam_zs.result()
        look_at_zs = f_look_at_z.result()

    del cam_xs_raw, cam_ys_raw, cam_zs_raw

    # Frame numbers start at 1 to match Blender's default timeline origin and
    # the Build modifier's frame_start=1 in build_scene.py.
    # Convert to plain Python arrays first so the list comprehension does
    # cheap indexed lookups rather than per-element numpy scalar boxing.
    _log.info(
        "[camera_path] building %d keyframe objects  (RSS %.0f MB)", n_frames, _rss_mb()
    )
    _cam_xs = cam_xs.tolist()
    del cam_xs
    _cam_ys = cam_ys.tolist()
    del cam_ys
    _cam_zs = cam_zs.tolist()
    del cam_zs
    _xs = xs.tolist()
    del xs
    _ys = ys.tolist()
    del ys
    _look_at_zs = look_at_zs.tolist()
    del look_at_zs
    del terrain_zs

    keyframes: list[CameraKeyframe] = [
        CameraKeyframe(
            frame=i + 1,
            x=_cam_xs[i],
            y=_cam_ys[i],
            z=_cam_zs[i],
            look_at_x=_xs[i],
            look_at_y=_ys[i],
            look_at_z=_look_at_zs[i],
        )
        for i in range(n_frames)
    ]
    del _cam_xs, _cam_ys, _cam_zs, _xs, _ys, _look_at_zs
    gc.collect()

    _step(6, f"keyframes built  count={len(keyframes)}  (after gc.collect)")

    # ------------------------------------------------------------------ #
    # 7. Insert pause keyframes at photo waypoints                         #
    # ------------------------------------------------------------------ #

    if pipeline.match_results:
        keyframes = _insert_pauses(
            keyframes,
            pipeline,
            bbox,
            lat_m,
            lon_m,
            grid,
            height_mode,
            height_offset,
            fps,
            pause_duration,
            pause_mode,
        )

    _step(7, f"done  total_keyframes={len(keyframes)}")

    return keyframes


# ------------------------------------------------------------------
# Coordinate helpers
# ------------------------------------------------------------------


def _tp_to_xy(
    tp: Trackpoint, bbox: BoundingBox, lat_m: float, lon_m: float
) -> np.ndarray:
    x = (tp.longitude - bbox.min_lon) / (bbox.max_lon - bbox.min_lon) * lon_m
    y = (tp.latitude - bbox.min_lat) / (bbox.max_lat - bbox.min_lat) * lat_m
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
        left = _douglas_peucker(pts[: max_idx + 1], epsilon)
        right = _douglas_peucker(pts[max_idx:], epsilon)
        return np.vstack([left[:-1], right])

    return np.array([pts[0], pts[-1]])


# ------------------------------------------------------------------
# Elevation helpers
# ------------------------------------------------------------------


def _height_at(
    x: float,
    y: float,
    grid: ElevationGrid,
    bbox: BoundingBox,
    lat_m: float,
    lon_m: float,
    height_mode: str,
    height_offset: float,
) -> float:
    lat = bbox.min_lat + y / lat_m * (bbox.max_lat - bbox.min_lat)
    lon = bbox.min_lon + x / lon_m * (bbox.max_lon - bbox.min_lon)
    if height_mode == "dem_smooth":
        elev = _smooth_elevation(grid, lat, lon)
    else:
        elev = grid.elevation_at(lat, lon)
    return elev + height_offset


def _height_at_batch(
    xs: np.ndarray,
    ys: np.ndarray,
    grid: ElevationGrid,
    bbox: BoundingBox,
    lat_m: float,
    lon_m: float,
    height_mode: str,
) -> np.ndarray:
    """Vectorised terrain-height lookup for all frames at once."""
    lats = bbox.min_lat + ys / lat_m * (bbox.max_lat - bbox.min_lat)
    lons = bbox.min_lon + xs / lon_m * (bbox.max_lon - bbox.min_lon)
    if height_mode == "dem_smooth":
        dlat = (grid.max_lat - grid.min_lat) / (grid.rows - 1) * 1.5
        dlon = (grid.max_lon - grid.min_lon) / (grid.cols - 1) * 1.5
        # 9 offset grids, each evaluated in one vectorised call.
        samples = [
            grid.elevation_at_batch(lats + r * dlat, lons + c * dlon)
            for r in (-1, 0, 1)
            for c in (-1, 0, 1)
        ]
        return np.mean(samples, axis=0)
    return grid.elevation_at_batch(lats, lons)


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
    xs: np.ndarray,
    ys: np.ndarray,
    lookahead_frames: int,
    weight_mode: str,
    progress_callback: Callable[[int, int], None] | None = None,
    progress_base: int = 0,
    progress_total: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted-average forward direction using upcoming track positions.

    Bulk frames (where the full lookahead window fits) are handled with a
    single vectorised matrix multiply via ``sliding_window_view``.  Only the
    tail region — where the window shrinks because we are near the end of the
    track — is computed in a small Python loop.
    """
    n = len(xs)
    L = min(lookahead_frames, n - 1)  # can't look past end of track

    # Precompute normalised weights for a full-size window (constant across bulk).
    t_full = np.linspace(0.0, 1.0, L) if L > 1 else np.zeros(1)
    if weight_mode == "uniform":
        w_full = np.ones(L) / L
    elif weight_mode == "exponential":
        w_full = np.exp(-3.0 * t_full)
        w_full /= w_full.sum()
    else:  # linear (default)
        w_full = 1.0 - t_full
        w_full /= w_full.sum()

    # Number of frames whose lookahead window is fully populated.
    # Frame i uses xs[i+1 : i+L+1]; that slice fits iff i+L+1 <= n, i.e. i < n-L.
    n_bulk = max(0, n - L)

    dirs_x = np.empty(n)
    dirs_y = np.empty(n)

    # ── Bulk region ──────────────────────────────────────────────────────────
    if n_bulk > 0 and L > 0:
        # The weighted window sum is a correlation: tx[i] = dot(xs[i+1:i+L+1], w_full).
        # Replacing the O(n×L) sliding-window matrix multiply with an O(n log n)
        # FFT-based convolution. x and y are independent so we run them in parallel.
        seg_x = xs[1 : n_bulk + L]
        seg_y = ys[1 : n_bulk + L]
        kernel = w_full[
            ::-1
        ]  # fftconvolve computes convolution; kernel flip = correlation

        with ThreadPoolExecutor(max_workers=2) as _pool:
            fut_x = _pool.submit(fftconvolve, seg_x, kernel, "valid")
            fut_y = _pool.submit(fftconvolve, seg_y, kernel, "valid")
            tx = fut_x.result()
            ty = fut_y.result()

        dx = tx - xs[:n_bulk]
        dy = ty - ys[:n_bulk]
        norms = np.hypot(dx, dy)
        valid = norms > 1e-6
        safe_norms = np.where(valid, norms, 1.0)
        dirs_x[:n_bulk] = np.where(valid, dx / safe_norms, np.nan)
        dirs_y[:n_bulk] = np.where(valid, dy / safe_norms, np.nan)

    if progress_callback is not None:
        progress_callback(progress_base + 1, progress_total)  # bulk done

    # ── Tail region (shrinking window) ───────────────────────────────────────
    last_x, last_y = 0.0, 1.0  # fallback direction
    # Carry forward the last valid bulk direction if available.
    for i in range(n_bulk - 1, -1, -1):
        if not math.isnan(dirs_x[i]):
            last_x, last_y = dirs_x[i], dirs_y[i]
            break

    for i in range(n_bulk, n):
        k = n - i - 1
        if k <= 0:
            dirs_x[i], dirs_y[i] = last_x, last_y
            continue
        t = np.linspace(0.0, 1.0, k)
        if weight_mode == "uniform":
            w = np.ones(k) / k
        elif weight_mode == "exponential":
            w = np.exp(-3.0 * t)
            w /= w.sum()
        else:
            w = 1.0 - t
            w /= w.sum()
        tx = float(np.dot(w, xs[i + 1 :]))
        ty = float(np.dot(w, ys[i + 1 :]))
        dx, dy = tx - float(xs[i]), ty - float(ys[i])
        norm = math.sqrt(dx * dx + dy * dy)
        if norm > 1e-6:
            last_x, last_y = dx / norm, dy / norm
        dirs_x[i], dirs_y[i] = last_x, last_y

    # Fix any NaN bulk entries (zero-length segment) by forward-filling.
    # Vectorised: build an index array where each NaN position gets the index
    # of the last preceding non-NaN value, then index-select in one shot.
    nan_mask = np.isnan(dirs_x)
    if nan_mask.any():
        valid_mask = ~nan_mask
        if not valid_mask.any():
            dirs_x[:] = 0.0
            dirs_y[:] = 1.0
        else:
            # Replace NaN indices with 0 so accumulate works, then propagate.
            idx = np.where(valid_mask, np.arange(n), 0)
            np.maximum.accumulate(idx, out=idx)
            dirs_x = dirs_x[idx]
            dirs_y = dirs_y[idx]

    return dirs_x, dirs_y


def _compute_forward_dirs_spline(
    xs: np.ndarray,
    ys: np.ndarray,
    dx_dt: np.ndarray,
    dy_dt: np.ndarray,
    orient_mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Forward direction from spline tangent or next-point vector."""
    if orient_mode == "lookat":
        dx = np.empty(len(xs))
        dy = np.empty(len(xs))
        dx[:-1] = np.diff(xs)
        dy[:-1] = np.diff(ys)
        dx[-1] = dx_dt[-1]
        dy[-1] = dy_dt[-1]
    else:
        dx = dx_dt.copy()
        dy = dy_dt.copy()

    norms = np.hypot(dx, dy)
    valid = norms > 1e-10
    safe_norms = np.where(valid, norms, 1.0)
    dirs_x = np.where(valid, dx / safe_norms, np.nan)
    dirs_y = np.where(valid, dy / safe_norms, np.nan)

    # Forward-fill any degenerate (zero-length) segments
    nan_mask = np.isnan(dirs_x)
    if nan_mask.any():
        idx = np.where(~nan_mask, np.arange(len(xs)), 0)
        np.maximum.accumulate(idx, out=idx)
        dirs_x = dirs_x[idx]
        dirs_y = dirs_y[idx]
        if np.isnan(dirs_x[0]):
            dirs_x[0], dirs_y[0] = 0.0, 1.0

    return dirs_x, dirs_y


# ------------------------------------------------------------------
# Photo pause insertion
# ------------------------------------------------------------------


def _make_pause_block(
    ref: CameraKeyframe, photo_path: str, pause_frames: int
) -> list[CameraKeyframe]:
    return [
        CameraKeyframe(
            frame=0,
            x=ref.x,
            y=ref.y,
            z=ref.z,
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
    lat_m: float,
    lon_m: float,
    grid: ElevationGrid,
    height_mode: str,
    height_offset: float,
    fps: int,
    pause_duration: float,
    pause_mode: str,
) -> list[CameraKeyframe]:
    pause_frames = max(1, round(pause_duration * fps))

    waypoints_pre: list[tuple[float, str]] = []  # (sort_key, photo_path)
    waypoints_track: list[tuple[float, float, str]] = []  # (wx, wy, photo_path)
    waypoints_post: list[tuple[float, str]] = []  # (sort_key, photo_path)

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
        waypoints_pre.sort(key=lambda w: w[0])  # chronological (most negative first)
        ref = keyframes[0]
        for _, photo_path in waypoints_pre:
            pre_block.extend(_make_pause_block(ref, photo_path, pause_frames))

    # ── In-track: insert after the nearest look-at keyframe ─────────────
    if waypoints_track:
        kf_xy = np.array([(kf.look_at_x, kf.look_at_y) for kf in keyframes])
        insertions: list[tuple[int, CameraKeyframe]] = []
        for wx, wy, photo_path in waypoints_track:
            dists = np.sqrt((kf_xy[:, 0] - wx) ** 2 + (kf_xy[:, 1] - wy) ** 2)
            idx = int(np.argmin(dists))
            ref = keyframes[idx]
            insertions.append(
                (
                    idx,
                    CameraKeyframe(
                        frame=0,
                        x=ref.x,
                        y=ref.y,
                        z=ref.z,
                        look_at_x=ref.look_at_x,
                        look_at_y=ref.look_at_y,
                        look_at_z=ref.look_at_z,
                        is_pause=True,
                        photo_path=photo_path,
                    ),
                )
            )
        # Insert from last to first so earlier indices stay valid
        insertions.sort(key=lambda e: e[0], reverse=True)
        for idx, pause_kf in insertions:
            keyframes = (
                keyframes[: idx + 1]
                + _make_pause_block(pause_kf, pause_kf.photo_path or "", pause_frames)
                + keyframes[idx + 1 :]
            )

    # ── Post-track: camera holds at the last fly frame ───────────────────
    post_block: list[CameraKeyframe] = []
    if waypoints_post and keyframes:
        waypoints_post.sort(key=lambda w: w[0])  # chronological
        ref = keyframes[-1]
        for _, photo_path in waypoints_post:
            post_block.extend(_make_pause_block(ref, photo_path, pause_frames))

    keyframes = pre_block + keyframes + post_block

    # Renumber frames sequentially starting at 1 (Blender timeline origin)
    for i, kf in enumerate(keyframes):
        kf.frame = i + 1

    return keyframes
