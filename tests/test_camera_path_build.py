"""Tests for build_camera_path and _height_at_batch."""

import math
import numpy as np
import pytest

from georeel.core.camera_path import (
    CameraPathError,
    build_camera_path,
    _height_at_batch,
    _insert_pauses,
)
from georeel.core.bounding_box import BoundingBox
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.match_result import MatchResult
from georeel.core.pipeline import Pipeline
from georeel.core.trackpoint import Trackpoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_grid(rows=10, cols=10, fill=100.0):
    data = np.full((rows, cols), fill, dtype=np.float32)
    return ElevationGrid(
        data=data, min_lat=46.0, max_lat=47.0, min_lon=7.0, max_lon=8.0
    )


def _make_trackpoints(n=8):
    """Generate n trackpoints along a diagonal in [46..47, 7..8]."""
    tps = []
    for i in range(n):
        t = i / max(1, n - 1)
        tps.append(Trackpoint(
            latitude=46.0 + t * 0.8,
            longitude=7.0 + t * 0.8,
            elevation=100.0 + t * 50,
            timestamp=None,
        ))
    return tps


def _make_pipeline(n=8, with_grid=True):
    p = Pipeline()
    p.trackpoints = _make_trackpoints(n)
    if with_grid:
        p.elevation_grid = _make_grid()
    return p


_BASE_SETTINGS = {
    "render/fps": 30,
    "render/camera_speed_mps": 800.0,  # fast → fewer frames
    "render/path_smoothing": "spline",
    "render/camera_height_mode": "dem_fixed",
    "render/camera_height_offset": 200.0,
    "render/camera_orientation": "tangent",
    "render/camera_tilt_deg": 45.0,
    "render/tangent_lookahead_s": 2.0,
    "render/tangent_weight": "linear",
    "render/photo_pause_mode": "hold",
    "render/photo_pause_duration": 1.0,
}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestBuildCameraPathErrors:
    def test_no_trackpoints_raises(self):
        p = Pipeline()
        p.elevation_grid = _make_grid()
        with pytest.raises(CameraPathError, match="[Tt]rackpoints"):
            build_camera_path(p, _BASE_SETTINGS)

    def test_no_elevation_grid_raises(self):
        p = Pipeline()
        p.trackpoints = _make_trackpoints(8)
        with pytest.raises(CameraPathError, match="[Ee]levation"):
            build_camera_path(p, _BASE_SETTINGS)

    def test_too_few_points_raises(self):
        """Only 2 trackpoints → fewer than 4 unique → raises CameraPathError."""
        p = Pipeline()
        p.trackpoints = _make_trackpoints(2)
        p.elevation_grid = _make_grid()
        with pytest.raises(CameraPathError):
            build_camera_path(p, _BASE_SETTINGS)


# ---------------------------------------------------------------------------
# Successful builds
# ---------------------------------------------------------------------------

class TestBuildCameraPathSuccess:
    def test_returns_list_of_keyframes(self):
        p = _make_pipeline(n=10)
        kfs = build_camera_path(p, _BASE_SETTINGS)
        assert isinstance(kfs, list)
        assert len(kfs) >= 2
        assert all(isinstance(kf, CameraKeyframe) for kf in kfs)

    def test_frame_numbers_sequential(self):
        p = _make_pipeline(n=10)
        kfs = build_camera_path(p, _BASE_SETTINGS)
        for i, kf in enumerate(kfs):
            assert kf.frame == i + 1

    def test_camera_height_above_terrain(self):
        p = _make_pipeline(n=10)
        kfs = build_camera_path(p, _BASE_SETTINGS)
        fill = 100.0
        offset = 200.0
        for kf in kfs:
            # Camera should be substantially above terrain (fill + offset - some gaussian smoothing)
            assert kf.z > fill  # at minimum above terrain

    def test_progress_callback_called(self):
        p = _make_pipeline(n=10)
        calls = []
        build_camera_path(p, _BASE_SETTINGS,
                          progress_callback=lambda done, total: calls.append((done, total)))
        assert len(calls) > 0

    def test_dp_spline_path_method(self):
        p = _make_pipeline(n=12)
        settings = dict(_BASE_SETTINGS)
        settings["render/path_smoothing"] = "dp_spline"
        kfs = build_camera_path(p, settings)
        assert len(kfs) >= 2

    def test_dem_smooth_height_mode(self):
        p = _make_pipeline(n=10)
        settings = dict(_BASE_SETTINGS)
        settings["render/camera_height_mode"] = "dem_smooth"
        kfs = build_camera_path(p, settings)
        assert len(kfs) >= 2

    def test_tangent_spline_orient_mode(self):
        p = _make_pipeline(n=10)
        settings = dict(_BASE_SETTINGS)
        settings["render/camera_orientation"] = "spline_tangent"
        kfs = build_camera_path(p, settings)
        assert len(kfs) >= 2

    def test_lookat_orient_mode(self):
        p = _make_pipeline(n=10)
        settings = dict(_BASE_SETTINGS)
        settings["render/camera_orientation"] = "lookat"
        kfs = build_camera_path(p, settings)
        assert len(kfs) >= 2

    def test_uniform_weight(self):
        p = _make_pipeline(n=10)
        settings = dict(_BASE_SETTINGS)
        settings["render/tangent_weight"] = "uniform"
        kfs = build_camera_path(p, settings)
        assert len(kfs) >= 2

    def test_exponential_weight(self):
        p = _make_pipeline(n=10)
        settings = dict(_BASE_SETTINGS)
        settings["render/tangent_weight"] = "exponential"
        kfs = build_camera_path(p, settings)
        assert len(kfs) >= 2


# ---------------------------------------------------------------------------
# _height_at_batch
# ---------------------------------------------------------------------------

class TestHeightAtBatch:
    def test_flat_grid_returns_fill(self):
        grid = _make_grid(fill=200.0)
        bbox = BoundingBox(46.0, 47.0, 7.0, 8.0)
        lat_m = (bbox.max_lat - bbox.min_lat) * 111_320.0
        lon_m = (bbox.max_lon - bbox.min_lon) * 111_320.0

        xs = np.array([lon_m / 4, lon_m / 2, 3 * lon_m / 4])
        ys = np.array([lat_m / 4, lat_m / 2, 3 * lat_m / 4])
        result = _height_at_batch(xs, ys, grid, bbox, lat_m, lon_m, "dem_fixed")
        np.testing.assert_allclose(result, 200.0, atol=1.0)

    def test_dem_smooth_returns_average(self):
        grid = _make_grid(fill=300.0)
        bbox = BoundingBox(46.0, 47.0, 7.0, 8.0)
        lat_m = (bbox.max_lat - bbox.min_lat) * 111_320.0
        lon_m = (bbox.max_lon - bbox.min_lon) * 111_320.0

        xs = np.array([lon_m / 2])
        ys = np.array([lat_m / 2])
        result = _height_at_batch(xs, ys, grid, bbox, lat_m, lon_m, "dem_smooth")
        assert result[0] == pytest.approx(300.0)

    def test_output_shape_matches_input(self):
        grid = _make_grid(fill=100.0)
        bbox = BoundingBox(46.0, 47.0, 7.0, 8.0)
        lat_m = 111_320.0
        lon_m = 80_000.0

        xs = np.linspace(0, lon_m, 20)
        ys = np.linspace(0, lat_m, 20)
        result = _height_at_batch(xs, ys, grid, bbox, lat_m, lon_m, "dem_fixed")
        assert result.shape == (20,)


# ---------------------------------------------------------------------------
# _insert_pauses
# ---------------------------------------------------------------------------

class TestInsertPauses:
    def _kf(self, i):
        return CameraKeyframe(
            frame=i + 1,
            x=float(i * 100), y=float(i * 100), z=300.0,
            look_at_x=float(i * 100 + 50),
            look_at_y=float(i * 100 + 50),
            look_at_z=100.0,
        )

    def test_no_match_results_returns_unchanged(self):
        p = _make_pipeline(n=6)
        p.match_results = []
        grid = _make_grid()
        bbox = BoundingBox(46.0, 47.0, 7.0, 8.0)
        lat_m = 111_320.0
        lon_m = 80_000.0

        kfs = [self._kf(i) for i in range(6)]
        result = _insert_pauses(
            kfs, p, bbox, lat_m, lon_m, grid,
            "dem_fixed", 200.0, 30, 1.0, "hold",
        )
        assert len(result) == len(kfs)

    def test_track_match_inserts_pauses(self):
        p = _make_pipeline(n=8)
        grid = p.elevation_grid
        bbox = BoundingBox(grid.min_lat, grid.max_lat, grid.min_lon, grid.max_lon)
        lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
        lon_m = (grid.max_lon - grid.min_lon) * 111_320.0 * math.cos(math.radians((grid.min_lat + grid.max_lat) / 2))

        r = MatchResult(photo_path="/x.jpg", trackpoint_index=3, position="track")
        p.match_results = [r]

        kfs = [self._kf(i) for i in range(10)]
        result = _insert_pauses(
            kfs, p, bbox, lat_m, lon_m, grid,
            "dem_fixed", 200.0, 30, 1.0, "hold",
        )
        # Should have more keyframes than before (pause was inserted)
        assert len(result) > len(kfs)

    def test_pre_match_prepends_pauses(self):
        p = _make_pipeline(n=6)
        grid = p.elevation_grid
        bbox = BoundingBox(grid.min_lat, grid.max_lat, grid.min_lon, grid.max_lon)
        lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
        lon_m = 80_000.0

        r = MatchResult(photo_path="/pre.jpg", trackpoint_index=0, position="pre", sort_key=-10.0)
        p.match_results = [r]
        kfs = [self._kf(i) for i in range(6)]
        result = _insert_pauses(
            kfs, p, bbox, lat_m, lon_m, grid,
            "dem_fixed", 200.0, 30, 1.0, "hold",
        )
        assert len(result) > len(kfs)
        # Pre pauses should come first
        assert result[0].is_pause

    def test_post_match_appends_pauses(self):
        p = _make_pipeline(n=6)
        grid = p.elevation_grid
        bbox = BoundingBox(grid.min_lat, grid.max_lat, grid.min_lon, grid.max_lon)
        lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
        lon_m = 80_000.0

        r = MatchResult(photo_path="/post.jpg", trackpoint_index=5, position="post", sort_key=100.0)
        p.match_results = [r]
        kfs = [self._kf(i) for i in range(6)]
        result = _insert_pauses(
            kfs, p, bbox, lat_m, lon_m, grid,
            "dem_fixed", 200.0, 30, 1.0, "hold",
        )
        assert len(result) > len(kfs)
        # Post pauses should come last
        assert result[-1].is_pause

    def test_frames_renumbered_sequentially(self):
        p = _make_pipeline(n=6)
        grid = p.elevation_grid
        bbox = BoundingBox(grid.min_lat, grid.max_lat, grid.min_lon, grid.max_lon)
        lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
        lon_m = 80_000.0

        r = MatchResult(photo_path="/p.jpg", trackpoint_index=0, position="pre")
        p.match_results = [r]
        kfs = [self._kf(i) for i in range(6)]
        result = _insert_pauses(
            kfs, p, bbox, lat_m, lon_m, grid,
            "dem_fixed", 200.0, 30, 1.0, "hold",
        )
        for i, kf in enumerate(result):
            assert kf.frame == i + 1
