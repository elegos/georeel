"""Tests for frustum.frustum_margin."""

import math
import pytest
from georeel.core.frustum import frustum_margin, _MIN_VIEW_M, _MAX_VIEW_M


class TestFrustumMarginClamping:
    def test_clamps_to_minimum(self):
        # Very high tilt (camera pointing straight down) → very short view dist
        result = frustum_margin(height_m=10.0, tilt_deg=89.0)
        assert result == pytest.approx(_MIN_VIEW_M)

    def test_clamps_to_maximum(self):
        # Shallow tilt (camera nearly horizontal) → top ray looks to horizon
        result = frustum_margin(height_m=1000.0, tilt_deg=5.0)
        assert result == pytest.approx(_MAX_VIEW_M)

    def test_top_ray_at_or_above_horizon(self):
        # tilt_deg very small → top_ray_down ≤ 0 → view_dist = _MAX_VIEW_M
        result = frustum_margin(height_m=100.0, tilt_deg=0.0)
        assert result == pytest.approx(_MAX_VIEW_M)

    def test_negative_tilt(self):
        # Negative tilt (camera looks upward) → top ray above horizon
        result = frustum_margin(height_m=500.0, tilt_deg=-10.0)
        assert result == pytest.approx(_MAX_VIEW_M)

    def test_result_within_bounds(self):
        for tilt in [10.0, 30.0, 45.0, 60.0, 80.0]:
            result = frustum_margin(height_m=500.0, tilt_deg=tilt)
            assert _MIN_VIEW_M <= result <= _MAX_VIEW_M


class TestFrustumMarginPhysics:
    def test_higher_camera_sees_further(self):
        # With same tilt, a higher camera sees a farther ground point
        r_low = frustum_margin(height_m=100.0, tilt_deg=45.0)
        r_high = frustum_margin(height_m=2000.0, tilt_deg=45.0)
        assert r_high >= r_low

    def test_steeper_tilt_sees_less(self):
        # Steeper downward tilt → top ray is more vertical → shorter view dist
        r_shallow = frustum_margin(height_m=500.0, tilt_deg=30.0)
        r_steep = frustum_margin(height_m=500.0, tilt_deg=70.0)
        assert r_shallow >= r_steep

    def test_returns_float(self):
        result = frustum_margin(height_m=200.0, tilt_deg=45.0)
        assert isinstance(result, float)
