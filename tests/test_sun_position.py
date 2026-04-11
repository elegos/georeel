"""Tests for sun_position.sun_angles and sun_direction_vector."""

import math
import pytest
from datetime import datetime, timezone, timedelta
from georeel.core.sun_position import sun_angles, sun_direction_vector


def _utc(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestSunAnglesReturnShape:
    def test_returns_two_floats(self):
        az, el = sun_angles(48.8566, 2.3522, _utc(2023, 6, 21))
        assert isinstance(az, float)
        assert isinstance(el, float)

    def test_azimuth_in_range(self):
        az, _ = sun_angles(48.8566, 2.3522, _utc(2023, 6, 21))
        assert 0.0 <= az < 360.0

    def test_elevation_in_range(self):
        _, el = sun_angles(48.8566, 2.3522, _utc(2023, 6, 21))
        assert -90.0 <= el <= 90.0


class TestSunAnglesNaiveDatetime:
    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2023, 6, 21, 12, 0)
        aware = datetime(2023, 6, 21, 12, 0, tzinfo=timezone.utc)
        az1, el1 = sun_angles(0.0, 0.0, naive)
        az2, el2 = sun_angles(0.0, 0.0, aware)
        assert az1 == pytest.approx(az2, abs=1e-9)
        assert el1 == pytest.approx(el2, abs=1e-9)


class TestSunAnglesPhysics:
    def test_summer_solstice_noon_paris_elevation_high(self):
        # At solar noon on the solstice, elevation should be high for Paris
        az, el = sun_angles(48.8566, 2.3522, _utc(2023, 6, 21, 11, 0))
        assert el > 50.0

    def test_midnight_elevation_negative(self):
        # At midnight UTC at Greenwich, sun is below horizon
        az, el = sun_angles(51.5, 0.0, _utc(2023, 6, 21, 0, 0))
        assert el < 0.0

    def test_winter_solstice_elevation_lower_than_summer(self):
        lat, lon = 48.8566, 2.3522
        _, el_summer = sun_angles(lat, lon, _utc(2023, 6, 21, 11, 0))
        _, el_winter = sun_angles(lat, lon, _utc(2023, 12, 21, 11, 0))
        assert el_summer > el_winter

    def test_morning_sun_in_east(self):
        # Early morning the sun is roughly in the eastern half (az < 180°)
        az, el = sun_angles(48.8566, 2.3522, _utc(2023, 6, 21, 5, 0))
        if el > 0:  # only valid when sun is up
            assert az < 180.0

    def test_afternoon_sun_in_west(self):
        # Late afternoon the sun has passed south and moved west (az > 180°)
        az, el = sun_angles(48.8566, 2.3522, _utc(2023, 6, 21, 17, 0))
        if el > 0:
            assert az > 180.0


class TestSunDirectionVector:
    def test_unit_length(self):
        for az, el in [(0, 45), (90, 30), (180, 60), (270, 10)]:
            x, y, z = sun_direction_vector(az, el)
            length = math.sqrt(x * x + y * y + z * z)
            assert length == pytest.approx(1.0, abs=1e-9)

    def test_north_elevation_45(self):
        # az=0 (North), el=45 → x=0, y=cos45, z=sin45
        x, y, z = sun_direction_vector(0.0, 45.0)
        assert x == pytest.approx(0.0, abs=1e-9)
        assert y == pytest.approx(math.cos(math.radians(45.0)), abs=1e-9)
        assert z == pytest.approx(math.sin(math.radians(45.0)), abs=1e-9)

    def test_east_elevation_0(self):
        # az=90 (East), el=0 → x=1, y=0, z=0
        x, y, z = sun_direction_vector(90.0, 0.0)
        assert x == pytest.approx(1.0, abs=1e-9)
        assert y == pytest.approx(0.0, abs=1e-9)
        assert z == pytest.approx(0.0, abs=1e-9)

    def test_negative_elevation_clamped_to_zero(self):
        # Below-horizon elevation is clamped to 0 → z == 0
        x, y, z = sun_direction_vector(90.0, -30.0)
        assert z == pytest.approx(0.0, abs=1e-9)

    def test_zenith(self):
        # Elevation 90° → sun is directly overhead → z=1, x=y=0
        x, y, z = sun_direction_vector(0.0, 90.0)
        assert z == pytest.approx(1.0, abs=1e-9)
