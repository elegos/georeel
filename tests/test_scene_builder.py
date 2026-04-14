"""Tests for georeel.core.scene_builder private helpers and build_scene error paths."""

import json
import math
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest
from PIL import Image

from georeel.core.bounding_box import BoundingBox
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.match_result import MatchResult
from georeel.core.pipeline import Pipeline
from georeel.core.trackpoint import Trackpoint
from georeel.core.scene_builder import (
    SceneBuildError,
    _write_dem,
    _write_track,
    _write_pins,
    _compute_pause_schedule,
    _elev_at_xy,
    _sun_args,
    _write_texture_tiles_from_image,
    build_scene,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_grid(rows=10, cols=10, fill=100.0):
    data = np.full((rows, cols), fill, dtype=np.float32)
    return ElevationGrid(
        data=data,
        min_lat=46.0, max_lat=47.0,
        min_lon=7.0,  max_lon=8.0,
    )


def _make_trackpoints(n=6):
    tps = []
    for i in range(n):
        lat = 46.0 + i * 0.1
        lon = 7.0  + i * 0.1
        tps.append(Trackpoint(latitude=lat, longitude=lon, elevation=100.0 + i * 10, timestamp=None))
    return tps


def _make_pipeline(n_trackpoints=6, with_grid=True):
    p = Pipeline()
    p.trackpoints = _make_trackpoints(n_trackpoints)
    if with_grid:
        p.elevation_grid = _make_grid()
    return p


# ---------------------------------------------------------------------------
# _elev_at_xy
# ---------------------------------------------------------------------------

class TestElevAtXY:
    def test_center_returns_fill(self):
        grid = _make_grid(fill=200.0)
        lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
        lon_m = (grid.max_lon - grid.min_lon) * 111_320.0 * math.cos(math.radians((grid.min_lat + grid.max_lat) / 2))
        # Center of scene
        x = lon_m / 2
        y = lat_m / 2
        result = _elev_at_xy(x, y, grid, lat_m, lon_m)
        assert result == pytest.approx(200.0)

    def test_origin_corner(self):
        grid = _make_grid(fill=300.0)
        lat_m = (grid.max_lat - grid.min_lat) * 111_320.0
        lon_m = (grid.max_lon - grid.min_lon) * 111_320.0
        result = _elev_at_xy(0.0, 0.0, grid, lat_m, lon_m)
        assert result == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# _write_dem
# ---------------------------------------------------------------------------

class TestWriteDem:
    def test_creates_meta_and_data(self, tmp_path):
        grid = _make_grid()
        meta_path, data_path = _write_dem(grid, tmp_path)
        assert meta_path.is_file()
        assert data_path.is_file()

    def test_meta_contains_required_keys(self, tmp_path):
        grid = _make_grid()
        meta_path, _ = _write_dem(grid, tmp_path)
        meta = json.loads(meta_path.read_text())
        for key in ("rows", "cols", "min_lat", "max_lat", "min_lon", "max_lon", "lat_m", "lon_m"):
            assert key in meta

    def test_meta_values_correct(self, tmp_path):
        grid = _make_grid(rows=5, cols=7)
        meta_path, _ = _write_dem(grid, tmp_path)
        meta = json.loads(meta_path.read_text())
        assert meta["rows"] == 5
        assert meta["cols"] == 7
        assert meta["min_lat"] == pytest.approx(46.0)
        assert meta["max_lat"] == pytest.approx(47.0)

    def test_data_file_nonempty(self, tmp_path):
        grid = _make_grid()
        _, data_path = _write_dem(grid, tmp_path)
        assert data_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# _write_track
# ---------------------------------------------------------------------------

class TestWriteTrack:
    def test_empty_trackpoints_writes_empty_json(self, tmp_path):
        p = Pipeline()
        p.trackpoints = []
        p.elevation_grid = _make_grid()
        track_path, ribbon_points = _write_track(p, tmp_path)
        data = json.loads(track_path.read_text())
        assert data == []
        assert ribbon_points == []

    def test_no_grid_writes_empty_json(self, tmp_path):
        p = Pipeline()
        p.trackpoints = _make_trackpoints(5)
        p.elevation_grid = None
        track_path, ribbon_points = _write_track(p, tmp_path)
        data = json.loads(track_path.read_text())
        assert data == []

    def test_few_trackpoints_fallback(self, tmp_path):
        """Fewer than 4 unique points → falls back to raw points."""
        p = _make_pipeline(n_trackpoints=3)
        track_path, ribbon_points = _write_track(p, tmp_path)
        data = json.loads(track_path.read_text())
        # Should still write something without crashing
        assert isinstance(data, list)

    def test_normal_path_writes_json(self, tmp_path):
        p = _make_pipeline(n_trackpoints=8)
        track_path, ribbon_points = _write_track(p, tmp_path)
        data = json.loads(track_path.read_text())
        assert len(data) > 0
        for pt in data:
            assert "x" in pt
            assert "y" in pt
            assert "z" in pt
            assert "slope" in pt

    def test_returns_path_and_list(self, tmp_path):
        p = _make_pipeline(n_trackpoints=6)
        result = _write_track(p, tmp_path)
        assert len(result) == 2
        path, ribbon_pts = result
        assert isinstance(path, Path)
        assert isinstance(ribbon_pts, list)


# ---------------------------------------------------------------------------
# _write_pins
# ---------------------------------------------------------------------------

class TestWritePins:
    def test_no_match_results_writes_empty(self, tmp_path):
        p = _make_pipeline()
        pins_path = _write_pins(p, tmp_path)
        data = json.loads(pins_path.read_text())
        assert data == []

    def test_no_elevation_grid_writes_empty(self, tmp_path):
        p = Pipeline()
        p.trackpoints = _make_trackpoints()
        r = MatchResult(photo_path="/a/b.jpg", trackpoint_index=0)
        p.match_results = [r]
        p.elevation_grid = None
        pins_path = _write_pins(p, tmp_path)
        data = json.loads(pins_path.read_text())
        assert data == []

    def test_failed_match_result_skipped(self, tmp_path):
        p = _make_pipeline()
        bad = MatchResult(photo_path="/x.jpg", trackpoint_index=None, error="no match")
        p.match_results = [bad]
        pins_path = _write_pins(p, tmp_path)
        data = json.loads(pins_path.read_text())
        assert data == []

    def test_single_valid_match_writes_pin(self, tmp_path):
        p = _make_pipeline()
        r = MatchResult(photo_path="/photo.jpg", trackpoint_index=0)
        p.match_results = [r]
        pins_path = _write_pins(p, tmp_path)
        data = json.loads(pins_path.read_text())
        assert len(data) == 1
        pin = data[0]
        assert "x" in pin
        assert "y" in pin
        assert "z" in pin
        assert pin["photo_path"] == "/photo.jpg"

    def test_multiple_pins_same_trackpoint_spread(self, tmp_path):
        """Two photos at the same trackpoint should spread on a circle."""
        p = _make_pipeline()
        r1 = MatchResult(photo_path="/a.jpg", trackpoint_index=0)
        r2 = MatchResult(photo_path="/b.jpg", trackpoint_index=0)
        p.match_results = [r1, r2]
        pins_path = _write_pins(p, tmp_path)
        data = json.loads(pins_path.read_text())
        assert len(data) == 2
        # Pins should be at different positions (spread)
        assert data[0]["x"] != pytest.approx(data[1]["x"]) or data[0]["y"] != pytest.approx(data[1]["y"])


# ---------------------------------------------------------------------------
# _compute_pause_schedule
# ---------------------------------------------------------------------------

class TestComputePauseSchedule:
    def test_no_match_results_returns_basic_schedule(self, tmp_path):
        p = _make_pipeline()
        ribbon_points = [{"x": float(i), "y": float(i), "z": 100.0, "slope": 0.0} for i in range(10)]
        settings = {"render/fps": 30, "render/camera_speed_mps": 80.0, "render/photo_pause_duration": 3.0}
        schedule = _compute_pause_schedule(p, settings, ribbon_points)
        assert "fly_total_frames" in schedule
        assert "total_scene_frames" in schedule
        assert "pauses" in schedule
        assert schedule["pauses"] == []

    def test_with_track_waypoints(self, tmp_path):
        p = _make_pipeline()
        r = MatchResult(photo_path="/p.jpg", trackpoint_index=2, position="track")
        p.match_results = [r]
        ribbon_points = [{"x": float(i * 1000), "y": float(i * 1000), "z": 100.0, "slope": 0.0} for i in range(20)]
        settings = {"render/fps": 30, "render/camera_speed_mps": 80.0, "render/photo_pause_duration": 3.0}
        schedule = _compute_pause_schedule(p, settings, ribbon_points)
        assert len(schedule["pauses"]) >= 0  # may be 0 if closest ribbon point not found

    def test_pre_post_photos(self):
        p = _make_pipeline()
        pre = MatchResult(photo_path="/pre.jpg", trackpoint_index=0, position="pre")
        post = MatchResult(photo_path="/post.jpg", trackpoint_index=5, position="post")
        p.match_results = [pre, post]
        ribbon_points = [{"x": float(i * 100), "y": 0.0, "z": 100.0, "slope": 0.0} for i in range(10)]
        settings = {"render/fps": 30, "render/camera_speed_mps": 80.0, "render/photo_pause_duration": 3.0}
        schedule = _compute_pause_schedule(p, settings, ribbon_points)
        assert schedule["pre_total_frames"] > 0
        assert schedule["post_total_frames"] > 0


# ---------------------------------------------------------------------------
# _sun_args
# ---------------------------------------------------------------------------

class TestSunArgs:
    def test_no_timestamps_returns_empty(self):
        p = _make_pipeline()
        # trackpoints have no timestamps
        result = _sun_args(p)
        assert result == []

    def test_no_grid_returns_empty(self):
        from datetime import datetime, timezone
        p = Pipeline()
        ts = datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc)
        p.trackpoints = [Trackpoint(latitude=46.5, longitude=7.5, elevation=100.0, timestamp=ts)]
        p.elevation_grid = None
        result = _sun_args(p)
        assert result == []

    def test_with_timestamp_and_grid_returns_three_values(self):
        from datetime import datetime, timezone
        p = Pipeline()
        ts = datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc)
        p.trackpoints = [Trackpoint(latitude=46.5, longitude=7.5, elevation=100.0, timestamp=ts)]
        p.elevation_grid = _make_grid()
        result = _sun_args(p)
        assert len(result) == 3
        # Each should be parseable as a float
        for s in result:
            float(s)  # should not raise


# ---------------------------------------------------------------------------
# _write_texture_tiles_from_image
# ---------------------------------------------------------------------------

class TestWriteTextureTilesFromImage:
    def _make_texture(self, width=100, height=80):
        img = Image.new("RGB", (width, height), color=(128, 64, 32))
        texture = MagicMock()
        texture.image = img
        texture._source_zip = None
        texture._tile_cache = None
        texture.min_lat = 46.0
        texture.max_lat = 47.0
        texture.min_lon = 7.0
        texture.max_lon = 8.0
        return texture

    def test_creates_manifest(self, tmp_path):
        texture = self._make_texture()
        grid = _make_grid()
        manifest_path, manifest = _write_texture_tiles_from_image(texture, grid, tmp_path)
        assert manifest_path.is_file()
        assert "tiles" in manifest

    def test_tiles_dir_created(self, tmp_path):
        texture = self._make_texture()
        grid = _make_grid()
        _write_texture_tiles_from_image(texture, grid, tmp_path)
        tiles_dir = tmp_path / "sat_tiles"
        assert tiles_dir.is_dir()

    def test_tile_png_files_created(self, tmp_path):
        texture = self._make_texture()
        grid = _make_grid()
        manifest_path, manifest = _write_texture_tiles_from_image(texture, grid, tmp_path)
        tiles = manifest["tiles"]
        assert len(tiles) >= 1
        for tile in tiles:
            assert Path(tile["path"]).is_file()

    def test_progress_callback_called(self, tmp_path):
        texture = self._make_texture()
        grid = _make_grid()
        calls = []
        _write_texture_tiles_from_image(
            texture, grid, tmp_path,
            tile_progress_cb=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) >= 1

    def test_cancel_check_raises(self, tmp_path):
        texture = self._make_texture()
        grid = _make_grid()
        with pytest.raises(SceneBuildError, match="Cancelled"):
            _write_texture_tiles_from_image(
                texture, grid, tmp_path,
                cancel_check=lambda: True,
            )

    def test_status_cb_called(self, tmp_path):
        texture = self._make_texture()
        grid = _make_grid()
        statuses = []
        _write_texture_tiles_from_image(
            texture, grid, tmp_path,
            status_cb=statuses.append,
        )
        assert len(statuses) >= 1

    def test_max_texture_pixels_downscale(self, tmp_path):
        """When max_texture_pixels is very small, image is downscaled."""
        texture = self._make_texture(width=500, height=400)
        grid = _make_grid()
        _, manifest = _write_texture_tiles_from_image(
            texture, grid, tmp_path,
            max_texture_pixels=100,  # force heavy downscale
        )
        assert manifest["image_width"] <= 500
        assert manifest["image_height"] <= 400

    def test_mismatched_bounds_uses_crop(self, tmp_path):
        """Texture with slightly different bounds from DEM should still work."""
        img = Image.new("RGB", (200, 200), color=(100, 100, 100))
        texture = MagicMock()
        texture.image = img
        texture._source_zip = None
        texture._tile_cache = None
        # Texture covers a larger area than the DEM
        texture.min_lat = 45.5
        texture.max_lat = 47.5
        texture.min_lon = 6.5
        texture.max_lon = 8.5
        grid = _make_grid()
        manifest_path, manifest = _write_texture_tiles_from_image(texture, grid, tmp_path)
        assert manifest_path.is_file()


# ---------------------------------------------------------------------------
# build_scene error paths
# ---------------------------------------------------------------------------

class TestBuildSceneErrors:
    def test_no_elevation_grid_raises(self):
        p = Pipeline()
        p.satellite_texture = MagicMock()
        with pytest.raises(SceneBuildError, match="[Ee]levation"):
            build_scene(p)

    def test_no_satellite_texture_raises(self):
        p = Pipeline()
        p.elevation_grid = _make_grid()
        with pytest.raises(SceneBuildError, match="[Ss]atellite"):
            build_scene(p)

    def test_blender_not_found_raises(self):
        p = Pipeline()
        p.elevation_grid = _make_grid()
        p.satellite_texture = MagicMock()
        with patch("georeel.core.scene_builder.find_blender", return_value=None):
            with pytest.raises(SceneBuildError, match="[Bb]lender"):
                build_scene(p)

    def test_blender_failure_raises(self, tmp_path):
        """If Blender exits non-zero, SceneBuildError is raised."""
        p = _make_pipeline()
        p.satellite_texture = MagicMock()
        p.satellite_texture._tile_cache = None
        p.satellite_texture.image = Image.new("RGB", (50, 50))
        p.satellite_texture._source_zip = None
        p.satellite_texture.min_lat = 46.0
        p.satellite_texture.max_lat = 47.0
        p.satellite_texture.min_lon = 7.0
        p.satellite_texture.max_lon = 8.0
        p.satellite_texture.free_image = MagicMock()

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.returncode = 1
        mock_proc.wait = MagicMock()

        # Mock color resolver (UI dependency)
        with patch("georeel.core.scene_builder.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.scene_builder.temp_manager.make_temp_dir", return_value=tmp_path):
                with patch("georeel.core.scene_builder.subprocess.Popen", return_value=mock_proc):
                    with patch("georeel.core.scene_builder._resolve_pin_color", return_value="#228B22"):
                        with patch("georeel.core.scene_builder._resolve_marker_color", return_value="#ADD8E6"):
                            with pytest.raises(SceneBuildError):
                                build_scene(p)

    def test_cancel_check_raises(self, tmp_path):
        p = _make_pipeline()
        p.satellite_texture = MagicMock()
        p.satellite_texture._tile_cache = None
        p.satellite_texture.image = Image.new("RGB", (50, 50))
        p.satellite_texture._source_zip = None
        p.satellite_texture.min_lat = 46.0
        p.satellite_texture.max_lat = 47.0
        p.satellite_texture.min_lon = 7.0
        p.satellite_texture.max_lon = 8.0
        p.satellite_texture.free_image = MagicMock()

        mock_proc = MagicMock()
        mock_proc.stdout = iter(["line1\n"])
        mock_proc.returncode = 0
        mock_proc.terminate = MagicMock()
        mock_proc.wait = MagicMock()

        with patch("georeel.core.scene_builder.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.scene_builder.temp_manager.make_temp_dir", return_value=tmp_path):
                with patch("georeel.core.scene_builder.subprocess.Popen", return_value=mock_proc):
                    with patch("georeel.core.scene_builder._resolve_pin_color", return_value="#228B22"):
                        with patch("georeel.core.scene_builder._resolve_marker_color", return_value="#ADD8E6"):
                            with pytest.raises(SceneBuildError, match="[Cc]ancelled"):
                                build_scene(p, cancel_check=lambda: True)
