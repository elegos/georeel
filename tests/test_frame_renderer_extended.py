"""Extended tests for georeel.core.frame_renderer."""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from georeel.core.frame_renderer import (
    FrameRenderError,
    _filter_tiles,
    _tile_world_bounds,
    _render_single,
    _render_segmented,
    render_frames,
)
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.pipeline import Pipeline


def _kf(frame=1, x=0.0, y=0.0, z=100.0, is_pause=False):
    return CameraKeyframe(
        frame=frame, x=x, y=y, z=z,
        look_at_x=x + 10, look_at_y=y + 5, look_at_z=z - 20,
        is_pause=is_pause, photo_path=None,
    )


# ---------------------------------------------------------------------------
# _tile_world_bounds
# ---------------------------------------------------------------------------

class TestTileWorldBounds:
    def test_single_tile_full_extent(self):
        tile = {"dem_c_start": 0, "dem_c_end": 9, "dem_r_start": 0, "dem_r_end": 9}
        x_min, x_max, y_min, y_max = _tile_world_bounds(tile, rows=10, cols=10, lat_m=1000.0, lon_m=1000.0)
        assert x_min == pytest.approx(0.0)
        assert x_max == pytest.approx(1000.0)
        assert y_min == pytest.approx(0.0)
        assert y_max == pytest.approx(1000.0)

    def test_top_tile_north_extent(self):
        # dem_r_start=0, dem_r_end=4 → top half
        tile = {"dem_c_start": 0, "dem_c_end": 9, "dem_r_start": 0, "dem_r_end": 4}
        x_min, x_max, y_min, y_max = _tile_world_bounds(tile, rows=10, cols=10, lat_m=1000.0, lon_m=1000.0)
        # y increases northward so top rows → higher y
        assert y_max > y_min

    def test_left_tile(self):
        tile = {"dem_c_start": 0, "dem_c_end": 4, "dem_r_start": 0, "dem_r_end": 9}
        x_min, x_max, y_min, y_max = _tile_world_bounds(tile, rows=10, cols=10, lat_m=1000.0, lon_m=1000.0)
        assert x_min < x_max
        assert x_max <= 500.0  # left half


# ---------------------------------------------------------------------------
# _filter_tiles
# ---------------------------------------------------------------------------

class TestFilterTiles:
    def _tiles(self, n_rows=2, n_cols=2):
        tiles = []
        for ti in range(n_rows):
            for tj in range(n_cols):
                tiles.append({
                    "ti": ti, "tj": tj,
                    "dem_r_start": ti * 5,
                    "dem_r_end":   (ti + 1) * 5,
                    "dem_c_start": tj * 5,
                    "dem_c_end":   (tj + 1) * 5,
                })
        return tiles

    def test_empty_cam_list_returns_all_tiles(self):
        tiles = self._tiles()
        result = _filter_tiles(tiles, [], [], 0.0, 10, 10, 1000.0, 1000.0)
        assert len(result) == len(tiles)

    def test_empty_tile_list_returns_empty(self):
        result = _filter_tiles([], [500.0], [500.0], 0.0, 10, 10, 1000.0, 1000.0)
        assert result == []

    def test_camera_in_top_left_selects_nearby_tiles(self):
        tiles = self._tiles(2, 2)
        # Camera near origin → should include at least one tile
        result = _filter_tiles(tiles, [50.0], [50.0], 100.0, 10, 10, 1000.0, 1000.0)
        assert len(result) >= 1

    def test_camera_far_from_all_tiles_returns_all(self):
        tiles = self._tiles(2, 2)
        # Camera WAY outside — filter returns all as fallback
        result = _filter_tiles(tiles, [999_999.0], [999_999.0], 0.0, 10, 10, 1000.0, 1000.0)
        # All tiles returned as fallback when no match
        assert len(result) == 4

    def test_large_margin_includes_all(self):
        tiles = self._tiles(2, 2)
        result = _filter_tiles(tiles, [500.0], [500.0], 1_000_000.0, 10, 10, 1000.0, 1000.0)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# _render_single — mocked Popen
# ---------------------------------------------------------------------------

def _mock_proc(lines=None, returncode=0):
    proc = MagicMock()
    proc.stdout = iter(lines or [])
    proc.returncode = returncode
    proc.wait = MagicMock()
    proc.terminate = MagicMock()
    return proc


class TestRenderSingle:
    def test_success_returns_out_dir(self, tmp_path):
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        kf_path = tmp_path / "kf.json"
        kf_path.write_text("[]")

        with patch("georeel.core.frame_renderer.subprocess.Popen", return_value=_mock_proc(returncode=0)):
            result = _render_single(
                exe="/usr/bin/blender",
                scene="/scene.blend",
                kf_path=kf_path,
                out_dir=out_dir,
                engine="eevee",
                resolution="1080p",
                quality="medium",
                total=10,
                frame_start=0,
                frame_end=9,
                tile_filter=None,
                progress_cb=None,
                cancel_check=None,
            )
        assert result == str(out_dir)

    def test_nonzero_returncode_raises(self, tmp_path):
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        kf_path = tmp_path / "kf.json"
        kf_path.write_text("[]")

        with patch("georeel.core.frame_renderer.subprocess.Popen", return_value=_mock_proc(returncode=1)):
            with pytest.raises(FrameRenderError, match="[Bb]lender"):
                _render_single(
                    exe="/usr/bin/blender",
                    scene="/scene.blend",
                    kf_path=kf_path,
                    out_dir=out_dir,
                    engine="eevee",
                    resolution="1080p",
                    quality="medium",
                    total=10,
                    frame_start=0,
                    frame_end=9,
                    tile_filter=None,
                    progress_cb=None,
                    cancel_check=None,
                )

    def test_progress_cb_called_on_fra_line(self, tmp_path):
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        kf_path = tmp_path / "kf.json"
        kf_path.write_text("[]")
        calls = []

        with patch("georeel.core.frame_renderer.subprocess.Popen",
                   return_value=_mock_proc(["Fra:0 Mem:100\n", "Fra:1 Mem:100\n"], returncode=0)):
            _render_single(
                exe="/usr/bin/blender",
                scene="/scene.blend",
                kf_path=kf_path,
                out_dir=out_dir,
                engine="eevee",
                resolution="1080p",
                quality="medium",
                total=10,
                frame_start=0,
                frame_end=9,
                tile_filter=None,
                progress_cb=lambda cur, tot: calls.append((cur, tot)),
                cancel_check=None,
            )
        assert len(calls) >= 1

    def test_cancel_check_aborts(self, tmp_path):
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        kf_path = tmp_path / "kf.json"
        kf_path.write_text("[]")

        proc = _mock_proc(["Fra:0 Mem:100\n"], returncode=0)

        with patch("georeel.core.frame_renderer.subprocess.Popen", return_value=proc):
            with pytest.raises(FrameRenderError, match="[Cc]ancelled"):
                _render_single(
                    exe="/usr/bin/blender",
                    scene="/scene.blend",
                    kf_path=kf_path,
                    out_dir=out_dir,
                    engine="eevee",
                    resolution="1080p",
                    quality="medium",
                    total=10,
                    frame_start=0,
                    frame_end=9,
                    tile_filter=None,
                    progress_cb=None,
                    cancel_check=lambda: True,
                )
        proc.terminate.assert_called_once()

    def test_tile_filter_passed_in_cmd(self, tmp_path):
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        kf_path = tmp_path / "kf.json"
        kf_path.write_text("[]")
        captured = []

        def fake_popen(cmd, **kwargs):
            captured.append(cmd)
            return _mock_proc(returncode=0)

        with patch("georeel.core.frame_renderer.subprocess.Popen", side_effect=fake_popen):
            _render_single(
                exe="/usr/bin/blender",
                scene="/scene.blend",
                kf_path=kf_path,
                out_dir=out_dir,
                engine="eevee",
                resolution="1080p",
                quality="medium",
                total=10,
                frame_start=0,
                frame_end=9,
                tile_filter="0_0,0_1",
                progress_cb=None,
                cancel_check=None,
            )
        assert "0_0,0_1" in captured[0]

    def test_viewport_engine_uses_half_tex_scale(self, tmp_path):
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        kf_path = tmp_path / "kf.json"
        kf_path.write_text("[]")
        captured = []

        def fake_popen(cmd, **kwargs):
            captured.append(cmd)
            return _mock_proc(returncode=0)

        with patch("georeel.core.frame_renderer.subprocess.Popen", side_effect=fake_popen):
            _render_single(
                exe="/usr/bin/blender",
                scene="/scene.blend",
                kf_path=kf_path,
                out_dir=out_dir,
                engine="viewport",
                resolution="1080p",
                quality="medium",
                total=5,
                frame_start=0,
                frame_end=4,
                tile_filter=None,
                progress_cb=None,
                cancel_check=None,
            )
        assert "0.5" in captured[0]


# ---------------------------------------------------------------------------
# render_frames with segmented rendering
# ---------------------------------------------------------------------------

class TestRenderSegmented:
    def _pipeline_with_kfs(self, tmp_path, n_kfs=10):
        p = Pipeline()
        p.scene = str(tmp_path / "scene.blend")
        (tmp_path / "scene.blend").write_text("blend")
        p.camera_keyframes = [_kf(i, x=float(i * 100)) for i in range(n_kfs)]
        return p

    def test_segmented_no_manifest_falls_back(self, tmp_path):
        """Without manifest files, tile filtering is skipped (all tiles loaded)."""
        p = self._pipeline_with_kfs(tmp_path)
        settings = {"render/n_segments": 2}

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        # Note: render_frames will create out_dir itself — don't pre-create it

        def fake_make_temp_dir(prefix):
            return work_dir

        out_dir = work_dir / "frames"

        def fake_render_single(**kwargs):
            # Simulate Blender writing frames
            out_dir.mkdir(exist_ok=True)
            (out_dir / "frame_0000.png").write_bytes(b"PNG")
            return str(out_dir)

        with patch("georeel.core.frame_renderer.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.frame_renderer.temp_manager.make_temp_dir", side_effect=fake_make_temp_dir):
                with patch("georeel.core.frame_renderer._render_single", side_effect=fake_render_single):
                    result = render_frames(p, settings)
        assert result == str(out_dir)

    def test_no_frames_written_raises(self, tmp_path):
        """If Blender writes no PNGs, FrameRenderError is raised."""
        p = self._pipeline_with_kfs(tmp_path)
        settings = {"render/n_segments": 2}

        work_dir = tmp_path / "work2"
        work_dir.mkdir()

        def fake_make_temp_dir(prefix):
            return work_dir

        out_dir = work_dir / "frames"

        def fake_render_single(**kwargs):
            # Do NOT write any frames
            out_dir.mkdir(exist_ok=True)
            return str(out_dir)

        with patch("georeel.core.frame_renderer.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.frame_renderer.temp_manager.make_temp_dir", side_effect=fake_make_temp_dir):
                with patch("georeel.core.frame_renderer._render_single", side_effect=fake_render_single):
                    with pytest.raises(FrameRenderError, match="[Nn]o frames"):
                        render_frames(p, settings)

    def test_single_segment_calls_render_once(self, tmp_path):
        p = self._pipeline_with_kfs(tmp_path, n_kfs=5)
        settings = {}

        work_dir = tmp_path / "work3"
        work_dir.mkdir()

        def fake_make_temp_dir(prefix):
            return work_dir

        out_dir = work_dir / "frames"
        called = []

        def fake_render_single(**kwargs):
            called.append(kwargs)
            out_dir.mkdir(exist_ok=True)
            (out_dir / "frame_0000.png").write_bytes(b"PNG")
            return str(out_dir)

        with patch("georeel.core.frame_renderer.find_blender", return_value="/usr/bin/blender"):
            with patch("georeel.core.frame_renderer.temp_manager.make_temp_dir", side_effect=fake_make_temp_dir):
                with patch("georeel.core.frame_renderer._render_single", side_effect=fake_render_single):
                    render_frames(p, settings)
        assert len(called) == 1
