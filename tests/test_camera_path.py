"""Tests for camera_path pure helper functions."""

import math
import numpy as np
import pytest
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.camera_path import (
    _douglas_peucker,
    _remove_duplicates,
    _tp_to_xy,
    _height_at,
    _smooth_elevation,
    _compute_forward_dirs_tangent,
    _compute_forward_dirs_spline,
    _make_pause_block,
    _smooth_orientation_spikes,
)
from georeel.core.bounding_box import BoundingBox
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.trackpoint import Trackpoint
import numpy.testing as npt


def _make_bbox():
    return BoundingBox(min_lat=0.0, max_lat=1.0, min_lon=0.0, max_lon=1.0)


def _make_grid(rows=4, cols=4, fill=100.0):
    data = np.full((rows, cols), fill, dtype=np.float32)
    return ElevationGrid(data=data, min_lat=0.0, max_lat=1.0, min_lon=0.0, max_lon=1.0)


def _kf_ref():
    return CameraKeyframe(
        frame=1, x=10.0, y=20.0, z=300.0,
        look_at_x=15.0, look_at_y=25.0, look_at_z=200.0,
        is_pause=False, photo_path=None,
    )


# ── _tp_to_xy ─────────────────────────────────────────────────────

class TestTpToXy:
    def test_min_corner_maps_to_origin(self):
        tp = Trackpoint(latitude=0.0, longitude=0.0, elevation=None, timestamp=None)
        bbox = _make_bbox()
        xy = _tp_to_xy(tp, bbox, lat_m=1000.0, lon_m=1000.0)
        assert xy[0] == pytest.approx(0.0)
        assert xy[1] == pytest.approx(0.0)

    def test_max_corner_maps_to_lat_lon_m(self):
        tp = Trackpoint(latitude=1.0, longitude=1.0, elevation=None, timestamp=None)
        bbox = _make_bbox()
        xy = _tp_to_xy(tp, bbox, lat_m=2000.0, lon_m=3000.0)
        assert xy[0] == pytest.approx(3000.0)  # x = lon_m
        assert xy[1] == pytest.approx(2000.0)  # y = lat_m

    def test_center_maps_to_half(self):
        tp = Trackpoint(latitude=0.5, longitude=0.5, elevation=None, timestamp=None)
        xy = _tp_to_xy(tp, _make_bbox(), lat_m=1000.0, lon_m=1000.0)
        assert xy[0] == pytest.approx(500.0)
        assert xy[1] == pytest.approx(500.0)

    def test_returns_ndarray(self):
        tp = Trackpoint(latitude=0.0, longitude=0.0, elevation=None, timestamp=None)
        result = _tp_to_xy(tp, _make_bbox(), lat_m=1.0, lon_m=1.0)
        assert isinstance(result, np.ndarray)


# ── _remove_duplicates ────────────────────────────────────────────

class TestRemoveDuplicates:
    def test_no_duplicates_unchanged(self):
        pts = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
        result = _remove_duplicates(pts)
        assert len(result) == 3

    def test_consecutive_duplicates_removed(self):
        pts = np.array([[0.0, 0.0], [0.0, 0.0], [1.0, 0.0]])
        result = _remove_duplicates(pts)
        assert len(result) == 2

    def test_multiple_duplicates(self):
        pts = np.array([[1.0, 2.0], [1.0, 2.0], [1.0, 2.0], [3.0, 4.0]])
        result = _remove_duplicates(pts)
        assert len(result) == 2

    def test_all_same_keeps_first(self):
        pts = np.array([[5.0, 5.0], [5.0, 5.0], [5.0, 5.0]])
        result = _remove_duplicates(pts)
        assert len(result) == 1

    def test_single_point_unchanged(self):
        pts = np.array([[3.0, 4.0]])
        result = _remove_duplicates(pts)
        assert len(result) == 1

    def test_non_consecutive_duplicates_kept(self):
        pts = np.array([[1.0, 2.0], [3.0, 4.0], [1.0, 2.0]])
        result = _remove_duplicates(pts)
        assert len(result) == 3  # Not consecutive → all kept


# ── _douglas_peucker ──────────────────────────────────────────────

class TestDouglasPeucker:
    def test_two_points_unchanged(self):
        pts = np.array([[0.0, 0.0], [10.0, 0.0]])
        result = _douglas_peucker(pts, epsilon=1.0)
        assert len(result) == 2

    def test_collinear_points_simplified(self):
        # 5 collinear points → only endpoints kept
        pts = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]])
        result = _douglas_peucker(pts, epsilon=0.1)
        assert len(result) == 2
        npt.assert_array_almost_equal(result[0], [0.0, 0.0])
        npt.assert_array_almost_equal(result[-1], [4.0, 0.0])

    def test_far_deviant_point_kept(self):
        # Middle point deviates 10m from the line → kept
        pts = np.array([[0.0, 0.0], [5.0, 10.0], [10.0, 0.0]])
        result = _douglas_peucker(pts, epsilon=1.0)
        assert len(result) == 3  # All 3 kept because middle deviates 10m

    def test_small_epsilon_keeps_more(self):
        pts = np.array([
            [0.0, 0.0], [1.0, 0.5], [2.0, 0.0], [3.0, 0.5], [4.0, 0.0]
        ])
        result_small = _douglas_peucker(pts, epsilon=0.1)
        result_large = _douglas_peucker(pts, epsilon=2.0)
        assert len(result_small) >= len(result_large)

    def test_preserves_endpoints(self):
        pts = np.array([[0.0, 0.0], [5.0, 2.0], [10.0, 0.0]])
        result = _douglas_peucker(pts, epsilon=1.0)
        npt.assert_array_almost_equal(result[0], pts[0])
        npt.assert_array_almost_equal(result[-1], pts[-1])

    def test_degenerate_segment_zero_length(self):
        # start == end: all points map to 0 distance
        pts = np.array([[0.0, 0.0], [0.1, 0.1], [0.0, 0.0]])
        result = _douglas_peucker(pts, epsilon=1.0)
        assert len(result) >= 2


# ── _height_at ────────────────────────────────────────────────────

class TestHeightAt:
    def test_dem_fixed_adds_offset(self):
        grid = _make_grid(fill=200.0)
        bbox = _make_bbox()
        h = _height_at(500.0, 500.0, grid, bbox, lat_m=1000.0, lon_m=1000.0,
                       height_mode="dem_fixed", height_offset=150.0)
        assert h == pytest.approx(200.0 + 150.0)

    def test_dem_smooth_adds_offset(self):
        grid = _make_grid(fill=300.0)
        bbox = _make_bbox()
        h = _height_at(500.0, 500.0, grid, bbox, lat_m=1000.0, lon_m=1000.0,
                       height_mode="dem_smooth", height_offset=50.0)
        assert h == pytest.approx(300.0 + 50.0)

    def test_zero_offset(self):
        grid = _make_grid(fill=100.0)
        bbox = _make_bbox()
        h = _height_at(0.0, 0.0, grid, bbox, lat_m=1.0, lon_m=1.0,
                       height_mode="dem_fixed", height_offset=0.0)
        assert h == pytest.approx(100.0)


# ── _smooth_elevation ─────────────────────────────────────────────

class TestSmoothElevation:
    def test_uniform_grid_returns_fill(self):
        grid = _make_grid(fill=500.0)
        result = _smooth_elevation(grid, lat=0.5, lon=0.5)
        assert result == pytest.approx(500.0)

    def test_returns_float(self):
        grid = _make_grid(fill=100.0)
        result = _smooth_elevation(grid, lat=0.5, lon=0.5)
        assert isinstance(result, float)

    def test_averaging_effect(self):
        # Grid with varying elevations: mean should be between min and max
        data = np.arange(16, dtype=np.float32).reshape(4, 4)
        grid = ElevationGrid(data=data, min_lat=0.0, max_lat=3.0, min_lon=0.0, max_lon=3.0)
        result = _smooth_elevation(grid, lat=1.5, lon=1.5)
        assert result > 0.0
        assert result < 15.0


# ── _compute_forward_dirs_tangent ────────────────────────────────

class TestComputeForwardDirsTangent:
    def _straight_east(self, n=10):
        xs = np.arange(n, dtype=float)
        ys = np.zeros(n)
        return xs, ys

    def test_straight_east_dirs_are_east(self):
        xs, ys = self._straight_east(10)
        dirs_x, dirs_y = _compute_forward_dirs_tangent(xs, ys, lookahead_frames=3, weight_mode="linear")
        for dx, dy in zip(dirs_x[:-1], dirs_y[:-1]):  # last may be special-cased
            assert dx > 0.9  # mostly east
            assert abs(dy) < 0.1

    def test_output_length_matches_input(self):
        xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        ys = np.zeros(5)
        dirs_x, dirs_y = _compute_forward_dirs_tangent(xs, ys, lookahead_frames=2, weight_mode="uniform")
        assert len(dirs_x) == 5

    def test_unit_vectors(self):
        xs = np.array([0.0, 1.0, 2.0, 3.0])
        ys = np.array([0.0, 1.0, 2.0, 3.0])
        dirs_x, dirs_y = _compute_forward_dirs_tangent(xs, ys, lookahead_frames=2, weight_mode="linear")
        for dx, dy in zip(dirs_x, dirs_y):
            length = math.sqrt(dx * dx + dy * dy)
            assert length == pytest.approx(1.0, abs=1e-9)

    def test_weight_modes(self):
        xs = np.arange(10, dtype=float)
        ys = np.zeros(10)
        for mode in ("linear", "uniform", "exponential"):
            dirs_x, dirs_y = _compute_forward_dirs_tangent(xs, ys, lookahead_frames=3, weight_mode=mode)
            assert len(dirs_x) == 10

    def test_hairpin_does_not_point_inward(self):
        # U-turn: approach east, apex at (5,0), return west.
        # The OLD centroid approach pointed the camera toward the hairpin interior
        # (roughly north/south) at the apex.  The unit-step approach must keep the
        # forward direction aligned with the road (east component positive on
        # approach, west component positive on return).
        approach = np.linspace(0, 5, 20)
        # Semicircle apex (radius=1) curving from east to west via north
        theta = np.linspace(0, np.pi, 20)
        apex_x = 5 + np.cos(theta)
        apex_y = np.sin(theta)
        retreat = np.linspace(4, 0, 20)
        xs = np.concatenate([approach, apex_x[1:], retreat[1:]])
        ys = np.concatenate([np.zeros(20), apex_y[1:], np.zeros(20 - 1)])

        dirs_x, dirs_y = _compute_forward_dirs_tangent(xs, ys, lookahead_frames=10, weight_mode="linear")

        # Approach region: forward direction must have positive x (eastward)
        for i in range(5):
            assert dirs_x[i] > 0.0, f"approach frame {i}: dirs_x={dirs_x[i]:.3f} should be east"

        # Return region: forward direction must have negative x (westward)
        for i in range(len(xs) - 5, len(xs)):
            assert dirs_x[i] < 0.0, f"return frame {i}: dirs_x={dirs_x[i]:.3f} should be west"


# ── _compute_forward_dirs_spline ─────────────────────────────────

class TestComputeForwardDirsSpline:
    def _straight_data(self, n=8):
        xs = np.arange(n, dtype=float)
        ys = np.zeros(n)
        dx_dt = np.ones(n)
        dy_dt = np.zeros(n)
        return xs, ys, dx_dt, dy_dt

    def test_output_length_matches_input(self):
        xs, ys, dx_dt, dy_dt = self._straight_data(8)
        dirs_x, dirs_y = _compute_forward_dirs_spline(xs, ys, dx_dt, dy_dt, orient_mode="tangent")
        assert len(dirs_x) == 8

    def test_straight_east_tangent_mode(self):
        xs, ys, dx_dt, dy_dt = self._straight_data(8)
        dirs_x, dirs_y = _compute_forward_dirs_spline(xs, ys, dx_dt, dy_dt, orient_mode="tangent")
        for dx, dy in zip(dirs_x, dirs_y):
            assert dx == pytest.approx(1.0, abs=1e-9)
            assert dy == pytest.approx(0.0, abs=1e-9)

    def test_lookat_mode_uses_next_point(self):
        xs = np.array([0.0, 1.0, 2.0, 3.0])
        ys = np.array([0.0, 1.0, 0.0, 1.0])
        dx_dt = np.ones(4)
        dy_dt = np.zeros(4)
        dirs_x, dirs_y = _compute_forward_dirs_spline(xs, ys, dx_dt, dy_dt, orient_mode="lookat")
        # First direction should point toward (1,1) from (0,0)
        assert dirs_x[0] > 0  # east component
        assert dirs_y[0] > 0  # north component

    def test_unit_vectors(self):
        xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        ys = np.array([0.0, 0.5, 1.0, 0.5, 0.0])
        dx_dt = np.diff(xs, append=xs[-1])
        dy_dt = np.diff(ys, append=ys[-1])
        dirs_x, dirs_y = _compute_forward_dirs_spline(xs, ys, dx_dt, dy_dt, orient_mode="tangent")
        for dx, dy in zip(dirs_x, dirs_y):
            length = math.sqrt(dx * dx + dy * dy)
            assert length == pytest.approx(1.0, abs=1e-6)


# ── _make_pause_block ─────────────────────────────────────────────

class TestMakePauseBlock:
    def test_returns_correct_count(self):
        ref = _kf_ref()
        block = _make_pause_block(ref, "/photo.jpg", pause_frames=10)
        assert len(block) == 10

    def test_all_are_pause_keyframes(self):
        ref = _kf_ref()
        block = _make_pause_block(ref, "/photo.jpg", pause_frames=5)
        assert all(kf.is_pause for kf in block)

    def test_photo_path_set(self):
        ref = _kf_ref()
        block = _make_pause_block(ref, "/photo.jpg", pause_frames=3)
        assert all(kf.photo_path == "/photo.jpg" for kf in block)

    def test_position_copied_from_ref(self):
        ref = _kf_ref()
        block = _make_pause_block(ref, "/photo.jpg", pause_frames=2)
        for kf in block:
            assert kf.x == pytest.approx(ref.x)
            assert kf.y == pytest.approx(ref.y)
            assert kf.z == pytest.approx(ref.z)
            assert kf.look_at_x == pytest.approx(ref.look_at_x)

    def test_frame_number_is_zero(self):
        ref = _kf_ref()
        block = _make_pause_block(ref, "/a.jpg", pause_frames=4)
        assert all(kf.frame == 0 for kf in block)

    def test_one_pause_frame(self):
        ref = _kf_ref()
        block = _make_pause_block(ref, "/a.jpg", pause_frames=1)
        assert len(block) == 1

    def test_zero_pause_frames(self):
        ref = _kf_ref()
        block = _make_pause_block(ref, "/a.jpg", pause_frames=0)
        assert block == []


# ── _smooth_orientation_spikes ────────────────────────────────────

class TestSmoothOrientationSpikes:
    def test_no_spikes_returned_unchanged(self):
        angles = np.linspace(0.0, 1.0, 20)
        out = _smooth_orientation_spikes(angles)
        npt.assert_allclose(out, angles, atol=1e-12)

    def test_short_array_returned_unchanged(self):
        angles = np.array([0.0, 1.0])
        out = _smooth_orientation_spikes(angles)
        npt.assert_allclose(out, angles)

    def test_single_element(self):
        angles = np.array([0.5])
        out = _smooth_orientation_spikes(angles)
        npt.assert_allclose(out, angles)

    def test_constant_array(self):
        angles = np.full(10, 1.23)
        out = _smooth_orientation_spikes(angles)
        npt.assert_allclose(out, angles)

    def test_single_spike_removed(self):
        # Smooth ramp with one huge jump at index 5
        angles = np.linspace(0.0, 0.1, 20)
        angles[5] += 3.0  # spike: ~30× larger than typical step
        out = _smooth_orientation_spikes(angles)
        # After repair the spike value must be closer to the linear ramp
        ramp_value = np.interp(5, [0, 19], [0.0, 0.1])
        assert abs(out[5] - ramp_value) < 0.05

    def test_spike_at_start(self):
        # Ramp with non-zero median step so the spike is detectable
        angles = np.linspace(0.0, 0.1, 20)
        angles[0] += 5.0  # large spike at first index
        out = _smooth_orientation_spikes(angles)
        # Index 0 should be pulled toward the ramp value
        ramp_value = np.interp(0, [0, 19], [0.0, 0.1]) + 5.0  # original spiked value
        # At minimum the spike must be reduced
        assert abs(out[0] - 0.0) < abs(ramp_value - 0.0)

    def test_spike_at_end(self):
        angles = np.linspace(0.0, 0.1, 20)
        angles[-1] += 5.0
        out = _smooth_orientation_spikes(angles)
        ramp_end = 0.1 + 5.0
        assert abs(out[-1] - 0.1) < abs(ramp_end - 0.1)

    def test_output_length_unchanged(self):
        for n in (3, 10, 100):
            angles = np.random.default_rng(42).uniform(-3, 3, n)
            out = _smooth_orientation_spikes(angles)
            assert len(out) == n

    def test_input_not_mutated(self):
        angles = np.linspace(0.0, 1.0, 15)
        angles[7] += 10.0
        original = angles.copy()
        _smooth_orientation_spikes(angles)
        npt.assert_array_equal(angles, original)

    def test_high_mad_factor_skips_removal(self):
        # With a very high threshold nothing should be removed
        angles = np.linspace(0.0, 0.1, 20)
        angles[5] += 3.0
        out = _smooth_orientation_spikes(angles, mad_factor=1000.0)
        npt.assert_allclose(out, angles, atol=1e-12)

    def test_low_mad_factor_removes_more(self):
        angles = np.linspace(0.0, 0.1, 30)
        angles[10] += 0.5  # moderate bump
        out_tight = _smooth_orientation_spikes(angles, mad_factor=2.0)
        out_loose = _smooth_orientation_spikes(angles, mad_factor=10.0)
        # Tight threshold should change index 10 more than loose
        assert abs(out_tight[10] - angles[10]) >= abs(out_loose[10] - angles[10])

    def test_all_spikes_no_good_points_fallback(self):
        # Alternating large jumps so every point is "bad" — should return copy
        angles = np.array([0.0, 10.0, 0.0, 10.0, 0.0])
        out = _smooth_orientation_spikes(angles, mad_factor=0.5)
        # Either unchanged or interpolated — must not raise and must be same length
        assert len(out) == len(angles)
