"""Tests for async job start endpoints (DEM, satellite, scene, render, compositor, video).

These tests verify the HTTP contract (202 + job_id returned, upstream validation,
result retrieval) without invoking real Blender, SRTM, or network calls.
The pipeline functions are replaced with lightweight stubs injected via the
job registry.
"""

import base64
import io
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from georeel.core.elevation_grid import ElevationGrid
from georeel.server.app import app
from georeel.server.jobs import get_registry
from georeel.server.routes.satellite import SatelliteJobResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flat_grid(rows: int = 5, cols: int = 5) -> ElevationGrid:
    data = np.full((rows, cols), 500.0, dtype=np.float32)
    return ElevationGrid(data=data, min_lat=46.0, max_lat=46.1,
                         min_lon=11.0, max_lon=11.1)


def _make_done_dem_job() -> str:
    job = get_registry().create()
    job.status = "done"
    job.progress = 100
    job.result = _flat_grid()
    return job.job_id


def _make_done_satellite_job(tmp_path: Path) -> str:
    from georeel.core.satellite import SatelliteTexture
    jpg = tmp_path / "satellite_preview.jpg"
    Image.new("RGB", (4, 4), (100, 150, 200)).save(str(jpg), format="JPEG")
    texture = SatelliteTexture(
        image=Image.new("RGB", (4, 4)),
        min_lat=46.0, max_lat=46.1,
        min_lon=11.0, max_lon=11.1,
        provider_id="esri_world",
        quality="standard",
    )
    job = get_registry().create()
    job.status = "done"
    job.progress = 100
    job.result = SatelliteJobResult(
        texture=texture,
        texture_path=str(jpg),
        width=4, height=4,
    )
    return job.job_id


def _make_done_scene_job(blend_path: str) -> str:
    job = get_registry().create()
    job.status = "done"
    job.progress = 100
    job.result = blend_path
    return job.job_id


def _make_done_render_job(frames_dir: str) -> str:
    job = get_registry().create()
    job.status = "done"
    job.progress = 100
    job.result = frames_dir
    return job.job_id


def _grid_b64() -> str:
    data = np.full((5, 5), 500.0, dtype=np.float32)
    return base64.b64encode(data.tobytes()).decode()


_TRACKPOINTS = [
    {"latitude": 46.0 + i * 0.01, "longitude": 11.0 + i * 0.01,
     "elevation": 500.0, "timestamp": None, "is_reconstructed": False}
    for i in range(5)
]


_BBOX = {"min_lat": 46.0, "max_lat": 46.1, "min_lon": 11.0, "max_lon": 11.1}


def _create_workspace(client: TestClient) -> str:
    return client.post("/api/v1/workspaces").json()["workspace_id"]


# ── DEM ───────────────────────────────────────────────────────────────────────

class TestDemFetch:
    def test_returns_202_and_job_id(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/dem/fetch",
                json={"workspace_id": ws_id, "bounding_box": _BBOX},
            )
        assert response.status_code == 202
        assert "job_id" in response.json()

    def test_unknown_workspace_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/dem/fetch",
                json={"workspace_id": "no-such-ws", "bounding_box": _BBOX},
            )
        assert response.status_code == 404

    def test_job_registered(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            job_id = client.post(
                "/api/v1/dem/fetch",
                json={"workspace_id": ws_id, "bounding_box": _BBOX},
            ).json()["job_id"]
            # Check inside the context — lifespan teardown removes jobs on exit.
            assert get_registry().get(job_id) is not None

    def test_result_endpoint_returns_409_when_not_done(self) -> None:
        job = get_registry().create()
        job.status = "running"
        with TestClient(app) as client:
            response = client.get(f"/api/v1/dem/{job.job_id}/result")
        assert response.status_code == 409

    def test_result_endpoint_returns_grid_when_done(self) -> None:
        job_id = _make_done_dem_job()
        with TestClient(app) as client:
            response = client.get(f"/api/v1/dem/{job_id}/result")
        assert response.status_code == 200
        data = response.json()
        assert data["rows"] == 5
        assert data["cols"] == 5
        assert "data_b64" in data

    def test_result_endpoint_404_unknown_job(self) -> None:
        with TestClient(app) as client:
            response = client.get("/api/v1/dem/no-such-job/result")
        assert response.status_code == 404


# ── Satellite ─────────────────────────────────────────────────────────────────

class TestSatelliteFetch:
    def test_returns_202_and_job_id(self, tmp_path: Path) -> None:
        with TestClient(app) as client:
            ws_id = client.post("/api/v1/workspaces").json()["workspace_id"]
            response = client.post(
                "/api/v1/satellite/fetch",
                json={
                    "workspace_id": ws_id,
                    "bounding_box": {
                        "min_lat": 46.0, "max_lat": 46.1,
                        "min_lon": 11.0, "max_lon": 11.1,
                    },
                },
            )
        assert response.status_code == 202
        assert "job_id" in response.json()

    def test_unknown_workspace_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/satellite/fetch",
                json={
                    "workspace_id": "does-not-exist",
                    "bounding_box": {
                        "min_lat": 46.0, "max_lat": 46.1,
                        "min_lon": 11.0, "max_lon": 11.1,
                    },
                },
            )
        assert response.status_code == 404

    def test_result_returns_metadata_when_done(self, tmp_path: Path) -> None:
        job_id = _make_done_satellite_job(tmp_path)
        with TestClient(app) as client:
            response = client.get(f"/api/v1/satellite/{job_id}/result")
        assert response.status_code == 200
        data = response.json()
        assert data["width"] == 4
        assert data["height"] == 4
        assert data["provider_id"] == "esri_world"

    def test_texture_png_download_when_done(self, tmp_path: Path) -> None:
        job_id = _make_done_satellite_job(tmp_path)
        with TestClient(app) as client:
            response = client.get(f"/api/v1/satellite/{job_id}/texture.png")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/jpeg")

    def test_result_409_when_not_done(self) -> None:
        job = get_registry().create()
        job.status = "running"
        with TestClient(app) as client:
            response = client.get(f"/api/v1/satellite/{job.job_id}/result")
        assert response.status_code == 409


# ── Scene ─────────────────────────────────────────────────────────────────────

class TestSceneBuild:
    def _body(self, ws_id: str, dem_job_id: str, sat_job_id: str) -> dict:
        return {
            "workspace_id": ws_id,
            "dem_job_id": dem_job_id,
            "satellite_job_id": sat_job_id,
            "trackpoints": _TRACKPOINTS,
            "match_results": [],
            "settings": {},
        }

    def test_returns_202_when_upstream_done(self, tmp_path: Path) -> None:
        dem_id = _make_done_dem_job()
        sat_id = _make_done_satellite_job(tmp_path)
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/scene/build", json=self._body(ws_id, dem_id, sat_id)
            )
        assert response.status_code == 202
        assert "job_id" in response.json()

    def test_returns_409_when_dem_not_done(self, tmp_path: Path) -> None:
        dem_job = get_registry().create()
        dem_job.status = "running"
        sat_id = _make_done_satellite_job(tmp_path)
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/scene/build",
                json=self._body(ws_id, dem_job.job_id, sat_id),
            )
        assert response.status_code == 409

    def test_returns_409_when_satellite_not_done(self) -> None:
        dem_id = _make_done_dem_job()
        sat_job = get_registry().create()
        sat_job.status = "running"
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/scene/build",
                json=self._body(ws_id, dem_id, sat_job.job_id),
            )
        assert response.status_code == 409

    def test_returns_404_for_unknown_dem_job(self, tmp_path: Path) -> None:
        sat_id = _make_done_satellite_job(tmp_path)
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/scene/build",
                json=self._body(ws_id, "no-such-job", sat_id),
            )
        assert response.status_code == 404

    def test_unknown_workspace_returns_404(self, tmp_path: Path) -> None:
        dem_id = _make_done_dem_job()
        sat_id = _make_done_satellite_job(tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/scene/build",
                json=self._body("no-such-ws", dem_id, sat_id),
            )
        assert response.status_code == 404

    def test_download_blend_when_done(self, tmp_path: Path) -> None:
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"BLENDER")
        job_id = _make_done_scene_job(str(blend))
        with TestClient(app) as client:
            response = client.get(f"/api/v1/scene/{job_id}/download")
        assert response.status_code == 200
        assert response.content == b"BLENDER"

    def test_download_blend_404_when_not_done(self) -> None:
        job = get_registry().create()
        job.status = "running"
        with TestClient(app) as client:
            response = client.get(f"/api/v1/scene/{job.job_id}/download")
        assert response.status_code == 409


# ── Render ────────────────────────────────────────────────────────────────────

class TestRenderFrames:
    def _keyframe(self, frame: int = 0) -> dict:
        return {
            "frame": frame, "x": 0.0, "y": 0.0, "z": 500.0,
            "look_at_x": 100.0, "look_at_y": 100.0, "look_at_z": 490.0,
            "is_pause": False, "photo_path": None,
        }

    def test_returns_202_when_scene_done(self, tmp_path: Path) -> None:
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"fake")
        scene_id = _make_done_scene_job(str(blend))
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/render/frames",
                json={"workspace_id": ws_id, "scene_job_id": scene_id,
                      "keyframes": [self._keyframe()], "settings": {}},
            )
        assert response.status_code == 202

    def test_returns_409_when_scene_not_done(self) -> None:
        job = get_registry().create()
        job.status = "running"
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/render/frames",
                json={"workspace_id": ws_id, "scene_job_id": job.job_id,
                      "keyframes": [], "settings": {}},
            )
        assert response.status_code == 409

    def test_returns_404_for_unknown_scene_job(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/render/frames",
                json={"workspace_id": ws_id, "scene_job_id": "no-such-job",
                      "keyframes": [], "settings": {}},
            )
        assert response.status_code == 404

    def test_unknown_workspace_returns_404(self, tmp_path: Path) -> None:
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"fake")
        scene_id = _make_done_scene_job(str(blend))
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/render/frames",
                json={"workspace_id": "no-such-ws", "scene_job_id": scene_id,
                      "keyframes": [], "settings": {}},
            )
        assert response.status_code == 404


# ── Compositor ────────────────────────────────────────────────────────────────

class TestCompositorRun:
    def test_returns_202_when_render_done(self, tmp_path: Path) -> None:
        render_id = _make_done_render_job(str(tmp_path))
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/compositor/run",
                json={
                    "workspace_id": ws_id,
                    "render_job_id": render_id,
                    "match_results": [],
                    "keyframes": [],
                    "settings": {},
                },
            )
        assert response.status_code == 202

    def test_returns_409_when_render_not_done(self) -> None:
        job = get_registry().create()
        job.status = "running"
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/compositor/run",
                json={
                    "workspace_id": ws_id,
                    "render_job_id": job.job_id,
                    "match_results": [],
                    "keyframes": [],
                    "settings": {},
                },
            )
        assert response.status_code == 409

    def test_returns_404_for_unknown_render_job(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/compositor/run",
                json={
                    "workspace_id": ws_id,
                    "render_job_id": "no-such-job",
                    "match_results": [],
                    "keyframes": [],
                    "settings": {},
                },
            )
        assert response.status_code == 404

    def test_unknown_workspace_returns_404(self, tmp_path: Path) -> None:
        render_id = _make_done_render_job(str(tmp_path))
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/compositor/run",
                json={
                    "workspace_id": "no-such-ws",
                    "render_job_id": render_id,
                    "match_results": [],
                    "keyframes": [],
                    "settings": {},
                },
            )
        assert response.status_code == 404


# ── Video ─────────────────────────────────────────────────────────────────────

class TestVideoAssemble:
    def test_returns_202_when_source_done(self, tmp_path: Path) -> None:
        source_id = _make_done_render_job(str(tmp_path))
        with TestClient(app) as client:
            ws_id = client.post("/api/v1/workspaces").json()["workspace_id"]
            response = client.post(
                "/api/v1/video/assemble",
                json={
                    "workspace_id": ws_id,
                    "source_job_id": source_id,
                    "total_frames": 100,
                    "settings": {},
                },
            )
        assert response.status_code == 202

    def test_unknown_workspace_returns_404(self, tmp_path: Path) -> None:
        source_id = _make_done_render_job(str(tmp_path))
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/video/assemble",
                json={
                    "workspace_id": "no-such-ws",
                    "source_job_id": source_id,
                    "total_frames": 100,
                    "settings": {},
                },
            )
        assert response.status_code == 404

    def test_returns_409_when_source_not_done(self) -> None:
        job = get_registry().create()
        job.status = "running"
        with TestClient(app) as client:
            ws_id = client.post("/api/v1/workspaces").json()["workspace_id"]
            response = client.post(
                "/api/v1/video/assemble",
                json={
                    "workspace_id": ws_id,
                    "source_job_id": job.job_id,
                    "total_frames": 100,
                    "settings": {},
                },
            )
        assert response.status_code == 409

    def test_download_video_when_done(self, tmp_path: Path) -> None:
        video_file = tmp_path / "output.mp4"
        video_file.write_bytes(b"FAKEMP4")
        job = get_registry().create()
        job.status = "done"
        job.result = str(video_file)
        with TestClient(app) as client:
            response = client.get(f"/api/v1/video/{job.job_id}/download")
        assert response.status_code == 200
        assert response.content == b"FAKEMP4"

    def test_download_video_409_when_not_done(self) -> None:
        job = get_registry().create()
        job.status = "running"
        with TestClient(app) as client:
            response = client.get(f"/api/v1/video/{job.job_id}/download")
        assert response.status_code == 409


# ── _run direct invocation helpers ────────────────────────────────────────────

def _run_async(coro) -> None:  # type: ignore[type-arg]
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    import asyncio
    asyncio.run(coro)


# ── DEM _run direct tests ─────────────────────────────────────────────────────

class TestDemRunDirect:
    """Cover lines 40-62 of server/routes/dem.py via direct _run() calls."""

    def test_run_returns_early_when_job_not_in_registry(self) -> None:
        """Line 41: _run returns silently when job_id is not found."""
        from georeel.server.routes.dem import _run, DemFetchRequest
        from georeel.server.models.bounding_box import BoundingBoxSchema
        body = DemFetchRequest(
            workspace_id="ws",
            bounding_box=BoundingBoxSchema(
                min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1
            ),
        )
        # A job_id that was never registered — _run must not raise
        _run_async(_run("nonexistent-job-id", body))

    def test_run_success_path_sets_done(self) -> None:
        """Lines 52-59: success path stores result and marks job done."""
        from unittest.mock import patch, MagicMock
        from georeel.server.routes.dem import _run, DemFetchRequest
        from georeel.server.models.bounding_box import BoundingBoxSchema

        job = get_registry().create()
        body = DemFetchRequest(
            workspace_id="ws",
            bounding_box=BoundingBoxSchema(
                min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1
            ),
        )
        fake_grid = _flat_grid()
        with patch("georeel.server.routes.dem.fetch_dem", return_value=fake_grid):
            _run_async(_run(job.job_id, body))

        assert job.status == "done"
        assert job.progress == 100
        assert job.result is fake_grid

    def test_run_exception_path_sets_error(self) -> None:
        """Lines 60-62: exception in _blocking sets error status."""
        from unittest.mock import patch
        from georeel.server.routes.dem import _run, DemFetchRequest
        from georeel.server.models.bounding_box import BoundingBoxSchema

        job = get_registry().create()
        body = DemFetchRequest(
            workspace_id="ws",
            bounding_box=BoundingBoxSchema(
                min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1
            ),
        )
        with patch("georeel.server.routes.dem.fetch_dem",
                   side_effect=RuntimeError("srtm down")):
            _run_async(_run(job.job_id, body))

        assert job.status == "error"
        assert "srtm down" in job.error  # type: ignore[operator]

    def test_result_endpoint_500_when_wrong_type(self) -> None:
        """Line 78: HTTPException(500) when result is not ElevationGrid."""
        job = get_registry().create()
        job.status = "done"
        job.result = "not a grid"
        with TestClient(app) as client:
            response = client.get(f"/api/v1/dem/{job.job_id}/result")
        assert response.status_code == 500


# ── Satellite _run direct tests ───────────────────────────────────────────────

class TestSatelliteRunDirect:
    """Cover lines 83-149 of server/routes/satellite.py via direct _run() calls."""

    def test_run_returns_early_when_job_not_in_registry(self, tmp_path: Path) -> None:
        """Line 84: _run returns silently when job_id is not found."""
        from georeel.server.routes.satellite import _run, SatelliteFetchRequest
        from georeel.server.models.bounding_box import BoundingBoxSchema
        body = SatelliteFetchRequest(
            workspace_id="ws",
            bounding_box=BoundingBoxSchema(
                min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1
            ),
        )
        _run_async(_run("nonexistent-job-id", body, tmp_path))

    def test_run_success_path_sets_done(self, tmp_path: Path) -> None:
        """Lines 99-144: success path fetches, composites, saves preview, marks done."""
        from unittest.mock import patch, MagicMock
        from PIL import Image
        from georeel.server.routes.satellite import _run, SatelliteFetchRequest, SatelliteJobResult
        from georeel.server.models.bounding_box import BoundingBoxSchema
        from georeel.core.satellite import SatelliteTexture

        job = get_registry().create()
        body = SatelliteFetchRequest(
            workspace_id="ws",
            bounding_box=BoundingBoxSchema(
                min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1
            ),
        )

        # Build a fake tile_cache whose composite_scaled returns a tiny image
        mock_tile_cache = MagicMock()
        mock_tile_cache.composite_scaled.return_value = (
            Image.new("RGB", (10, 10)), 256, 256
        )

        fake_texture = SatelliteTexture(
            image=None,
            min_lat=46.0, max_lat=46.1,
            min_lon=11.0, max_lon=11.1,
            provider_id="esri_world",
            quality="standard",
            tile_cache=mock_tile_cache,
        )

        mock_source = MagicMock()
        mock_source.fetch.return_value = fake_texture

        with patch("georeel.server.routes.satellite.build_source",
                   return_value=mock_source):
            _run_async(_run(job.job_id, body, tmp_path))

        assert job.status == "done"
        assert job.progress == 100
        assert isinstance(job.result, SatelliteJobResult)

    def test_run_exception_path_sets_error(self, tmp_path: Path) -> None:
        """Lines 146-149: exception in _blocking sets error status."""
        from unittest.mock import patch
        from georeel.server.routes.satellite import _run, SatelliteFetchRequest
        from georeel.server.models.bounding_box import BoundingBoxSchema

        job = get_registry().create()
        body = SatelliteFetchRequest(
            workspace_id="ws",
            bounding_box=BoundingBoxSchema(
                min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1
            ),
        )
        with patch("georeel.server.routes.satellite.build_source",
                   side_effect=RuntimeError("network error")):
            _run_async(_run(job.job_id, body, tmp_path))

        assert job.status == "error"
        assert "network error" in job.error  # type: ignore[operator]

    def test_get_result_404_unknown_job(self) -> None:
        """Line 155: _get_result raises 404 when job is missing."""
        with TestClient(app) as client:
            response = client.get("/api/v1/satellite/no-such-job/result")
        assert response.status_code == 404

    def test_get_result_500_wrong_type(self) -> None:
        """Line 162: _get_result raises 500 when result is not SatelliteJobResult."""
        job = get_registry().create()
        job.status = "done"
        job.result = "not a satellite result"
        with TestClient(app) as client:
            response = client.get(f"/api/v1/satellite/{job.job_id}/result")
        assert response.status_code == 500

    def test_register_tiles_404_unknown_workspace(self, tmp_path: Path) -> None:
        """Line 196: register_tiles returns 404 when workspace not found."""
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/satellite/register_tiles",
                json={
                    "workspace_id": "no-such-ws",
                    "tile_dir": str(tmp_path),
                    "zoom": 10,
                    "min_lat": 46.0, "max_lat": 46.1,
                    "min_lon": 11.0, "max_lon": 11.1,
                },
            )
        assert response.status_code == 404

    def test_texture_png_404_when_file_missing(self, tmp_path: Path) -> None:
        """Line 242: get_texture_png raises 404 when texture file doesn't exist."""
        from georeel.server.routes.satellite import SatelliteJobResult
        from georeel.core.satellite import SatelliteTexture
        texture = SatelliteTexture(
            image=None,
            min_lat=46.0, max_lat=46.1,
            min_lon=11.0, max_lon=11.1,
            provider_id="esri_world",
            quality="standard",
        )
        job = get_registry().create()
        job.status = "done"
        job.result = SatelliteJobResult(
            texture=texture,
            texture_path=str(tmp_path / "missing_preview.jpg"),
            width=4, height=4,
        )
        with TestClient(app) as client:
            response = client.get(f"/api/v1/satellite/{job.job_id}/texture.png")
        assert response.status_code == 404


# ── Compositor _run direct tests ──────────────────────────────────────────────

class TestCompositorRunDirect:
    """Cover lines 50-102 of server/routes/compositor.py via direct _run() calls."""

    def test_run_returns_early_when_job_not_in_registry(self) -> None:
        """Line 53: _run returns silently when job_id is not found."""
        from georeel.server.routes.compositor import _run, CompositorRunRequest
        body = CompositorRunRequest(
            workspace_id="ws",
            render_job_id="some-render",
            match_results=[],
            keyframes=[],
        )
        _run_async(_run("nonexistent-job-id", body))

    def test_run_error_when_render_job_no_longer_available(self) -> None:
        """Lines 58-61: render job removed from registry after _run starts."""
        from georeel.server.routes.compositor import _run, CompositorRunRequest

        comp_job = get_registry().create()
        body = CompositorRunRequest(
            workspace_id="ws",
            render_job_id="already-deleted-job",
            match_results=[],
            keyframes=[],
        )
        _run_async(_run(comp_job.job_id, body))

        assert comp_job.status == "error"
        assert "no longer available" in comp_job.error  # type: ignore[operator]

    def test_run_error_when_render_result_wrong_type(self, tmp_path: Path) -> None:
        """Lines 63-67: render job result is not a str."""
        from georeel.server.routes.compositor import _run, CompositorRunRequest

        render_job = get_registry().create()
        render_job.status = "done"
        render_job.result = 12345  # wrong type — not a str

        comp_job = get_registry().create()
        body = CompositorRunRequest(
            workspace_id="ws",
            render_job_id=render_job.job_id,
            match_results=[],
            keyframes=[],
        )
        _run_async(_run(comp_job.job_id, body))

        assert comp_job.status == "error"
        assert "unexpected type" in comp_job.error  # type: ignore[operator]

    def test_run_success_path_sets_done(self, tmp_path: Path) -> None:
        """Lines 90-99: success path stores comp_dir and marks done."""
        from unittest.mock import patch
        from georeel.server.routes.compositor import _run, CompositorRunRequest

        frames_dir = str(tmp_path / "frames")
        render_job = get_registry().create()
        render_job.status = "done"
        render_job.result = frames_dir

        comp_job = get_registry().create()
        body = CompositorRunRequest(
            workspace_id="ws",
            render_job_id=render_job.job_id,
            match_results=[],
            keyframes=[],
        )
        comp_out = str(tmp_path / "comp_out")
        with patch("georeel.server.routes.compositor.run_composite_stage",
                   return_value=comp_out):
            _run_async(_run(comp_job.job_id, body))

        assert comp_job.status == "done"
        assert comp_job.progress == 100
        assert comp_job.result == comp_out

    def test_run_exception_path_sets_error(self, tmp_path: Path) -> None:
        """Lines 100-102: exception in _blocking sets error status."""
        from unittest.mock import patch
        from georeel.server.routes.compositor import _run, CompositorRunRequest

        frames_dir = str(tmp_path / "frames")
        render_job = get_registry().create()
        render_job.status = "done"
        render_job.result = frames_dir

        comp_job = get_registry().create()
        body = CompositorRunRequest(
            workspace_id="ws",
            render_job_id=render_job.job_id,
            match_results=[],
            keyframes=[],
        )
        with patch("georeel.server.routes.compositor.run_composite_stage",
                   side_effect=RuntimeError("ffmpeg missing")):
            _run_async(_run(comp_job.job_id, body))

        assert comp_job.status == "error"
        assert "ffmpeg missing" in comp_job.error  # type: ignore[operator]


# ── Render _run direct tests ──────────────────────────────────────────────────

class TestRenderRunDirect:
    """Cover lines 51-102 of server/routes/render.py via direct _run() calls."""

    def _body(self, scene_job_id: str = "scene-id"):
        from georeel.server.routes.render import RenderFramesRequest
        return RenderFramesRequest(
            workspace_id="ws",
            scene_job_id=scene_job_id,
            keyframes=[],
        )

    def test_run_returns_early_when_job_not_in_registry(self) -> None:
        """Line 54: _run returns silently when job_id is not found."""
        from georeel.server.routes.render import _run
        _run_async(_run("nonexistent-job-id", self._body()))

    def test_run_error_when_scene_job_no_longer_available(self) -> None:
        """Lines 62-65: scene job removed from registry after _run starts."""
        from georeel.server.routes.render import _run

        render_job = get_registry().create()
        _run_async(_run(render_job.job_id, self._body("already-deleted")))

        assert render_job.status == "error"
        assert "no longer available" in render_job.error  # type: ignore[operator]

    def test_run_error_when_scene_result_wrong_type(self) -> None:
        """Lines 67-70: scene job result is not a str."""
        from georeel.server.routes.render import _run

        scene_job = get_registry().create()
        scene_job.status = "done"
        scene_job.result = 99  # not a str

        render_job = get_registry().create()
        _run_async(_run(render_job.job_id, self._body(scene_job.job_id)))

        assert render_job.status == "error"
        assert "unexpected type" in render_job.error  # type: ignore[operator]

    def test_run_success_path_sets_done(self, tmp_path: Path) -> None:
        """Lines 90-99: success path stores frames_dir and marks done."""
        from unittest.mock import patch
        from georeel.server.routes.render import _run

        scene_job = get_registry().create()
        scene_job.status = "done"
        scene_job.result = str(tmp_path / "scene.blend")

        render_job = get_registry().create()
        frames_out = str(tmp_path / "frames")
        with patch("georeel.server.routes.render.render_frames",
                   return_value=frames_out):
            _run_async(_run(render_job.job_id, self._body(scene_job.job_id)))

        assert render_job.status == "done"
        assert render_job.progress == 100
        assert render_job.result == frames_out

    def test_run_exception_path_sets_error(self, tmp_path: Path) -> None:
        """Lines 100-102: exception in _blocking sets error status."""
        from unittest.mock import patch
        from georeel.server.routes.render import _run

        scene_job = get_registry().create()
        scene_job.status = "done"
        scene_job.result = str(tmp_path / "scene.blend")

        render_job = get_registry().create()
        with patch("georeel.server.routes.render.render_frames",
                   side_effect=RuntimeError("blender not found")):
            _run_async(_run(render_job.job_id, self._body(scene_job.job_id)))

        assert render_job.status == "error"
        assert "blender not found" in render_job.error  # type: ignore[operator]


# ── Video _run direct tests ───────────────────────────────────────────────────

class TestVideoRunDirect:
    """Cover lines 53-102 of server/routes/video.py via direct _run() calls."""

    def _body(self, source_job_id: str = "source-id"):
        from georeel.server.routes.video import VideoAssembleRequest
        return VideoAssembleRequest(
            workspace_id="ws",
            source_job_id=source_job_id,
            total_frames=30,
        )

    def test_run_returns_early_when_job_not_in_registry(self, tmp_path: Path) -> None:
        """Line 55: _run returns silently when job_id is not found."""
        from georeel.server.routes.video import _run
        _run_async(_run("nonexistent-job-id", self._body(), tmp_path))

    def test_run_error_when_source_job_no_longer_available(self, tmp_path: Path) -> None:
        """Lines 60-64: source job removed from registry after _run starts."""
        from georeel.server.routes.video import _run

        video_job = get_registry().create()
        _run_async(_run(video_job.job_id, self._body("already-deleted"), tmp_path))

        assert video_job.status == "error"
        assert "no longer available" in video_job.error  # type: ignore[operator]

    def test_run_error_when_source_result_wrong_type(self, tmp_path: Path) -> None:
        """Lines 66-69: source job result is not a str."""
        from georeel.server.routes.video import _run

        source_job = get_registry().create()
        source_job.status = "done"
        source_job.result = 42  # not a str

        video_job = get_registry().create()
        _run_async(_run(video_job.job_id, self._body(source_job.job_id), tmp_path))

        assert video_job.status == "error"
        assert "unexpected type" in video_job.error  # type: ignore[operator]

    def test_run_success_path_sets_done(self, tmp_path: Path) -> None:
        """Lines 91-99: success path stores output path and marks done."""
        from unittest.mock import patch
        from georeel.server.routes.video import _run

        source_job = get_registry().create()
        source_job.status = "done"
        source_job.result = str(tmp_path / "frames")

        video_job = get_registry().create()
        video_out = str(tmp_path / "output.mp4")
        with patch("georeel.server.routes.video.assemble_video",
                   return_value=video_out):
            _run_async(_run(video_job.job_id, self._body(source_job.job_id), tmp_path))

        assert video_job.status == "done"
        assert video_job.progress == 100
        assert video_job.result == video_out

    def test_run_exception_path_sets_error(self, tmp_path: Path) -> None:
        """Lines 100-102: exception in _blocking sets error status."""
        from unittest.mock import patch
        from georeel.server.routes.video import _run

        source_job = get_registry().create()
        source_job.status = "done"
        source_job.result = str(tmp_path / "frames")

        video_job = get_registry().create()
        with patch("georeel.server.routes.video.assemble_video",
                   side_effect=RuntimeError("ffmpeg error")):
            _run_async(_run(video_job.job_id, self._body(source_job.job_id), tmp_path))

        assert video_job.status == "error"
        assert "ffmpeg error" in video_job.error  # type: ignore[operator]


# ── GPX parser additional coverage ────────────────────────────────────────────

class TestGpxParserAdditional:
    """Cover lines 47-65, 73-79, and 99 of core/gpx_parser.py."""

    def _write_gpx(self, tmp_path: Path, content: str) -> str:
        p = tmp_path / "track.gpx"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_fix_undeclared_namespaces_injection(self, tmp_path: Path) -> None:
        """Lines 47-65: _fix_undeclared_namespaces injects missing xmlns declarations."""
        from georeel.core.gpx_parser import _fix_undeclared_namespaces
        # ns3: prefix used but not declared
        content = (
            '<?xml version="1.0"?>'
            '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            '<trk><trkseg>'
            '<trkpt lat="48.0" lon="2.0"><extensions>'
            '<ns3:TrackPointExtension><ns3:ele>123</ns3:ele></ns3:TrackPointExtension>'
            '</extensions></trkpt>'
            '</trkseg></trk></gpx>'
        )
        fixed = _fix_undeclared_namespaces(content)
        assert "xmlns:ns3" in fixed
        assert fixed != content

    def test_fix_undeclared_namespaces_noop_when_all_declared(self) -> None:
        """_fix_undeclared_namespaces returns content unchanged when nothing is missing."""
        from georeel.core.gpx_parser import _fix_undeclared_namespaces
        content = (
            '<?xml version="1.0"?>'
            '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
            '<trk><trkseg>'
            '<trkpt lat="48.0" lon="2.0"><extensions>'
            '<ns3:TrackPointExtension><ns3:ele>123</ns3:ele></ns3:TrackPointExtension>'
            '</extensions></trkpt>'
            '</trkseg></trk></gpx>'
        )
        result = _fix_undeclared_namespaces(content)
        assert result == content

    def test_fix_undeclared_namespaces_unknown_prefix_uses_urn(self) -> None:
        """Unknown prefix gets an urn:unknown-ns: URI."""
        from georeel.core.gpx_parser import _fix_undeclared_namespaces
        content = (
            '<gpx xmlns="http://www.topografix.com/GPX/1/1">'
            '<trk><foobar:thing/></trk></gpx>'
        )
        fixed = _fix_undeclared_namespaces(content)
        assert 'xmlns:foobar="urn:unknown-ns:foobar"' in fixed

    def test_parse_gpx_recovers_from_undeclared_ns(self, tmp_path: Path) -> None:
        """Lines 92-99: parse_gpx retries with namespace injection when first parse fails."""
        from georeel.core.gpx_parser import parse_gpx
        # gpxpy will fail on the undeclared ns3 prefix; recovery should inject it
        content = (
            '<?xml version="1.0"?>'
            '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            '<trk><trkseg>'
            '<trkpt lat="48.0" lon="2.0">'
            '<extensions>'
            '<ns3:TrackPointExtension>'
            '<ns3:ele>350.0</ns3:ele>'
            '</ns3:TrackPointExtension>'
            '</extensions>'
            '</trkpt>'
            '</trkseg></trk></gpx>'
        )
        path = self._write_gpx(tmp_path, content)
        tps, bbox = parse_gpx(path)
        assert len(tps) == 1
        assert tps[0].latitude == 48.0

    def test_parse_gpx_recovery_raises_when_fix_doesnt_help(self, tmp_path: Path) -> None:
        """Line 99: GpxParseError raised when fixed content equals original (no ns to inject)."""
        from georeel.core.gpx_parser import GpxParseError, parse_gpx
        # Totally invalid XML that gpxpy cannot parse AND has no undeclared ns prefixes
        # so _fix_undeclared_namespaces returns content unchanged → GpxParseError raised.
        content = "<<<not xml and no namespace issues>>>"
        path = self._write_gpx(tmp_path, content)
        with pytest.raises(GpxParseError):
            parse_gpx(path)

    def test_elevation_from_extensions_finds_ele(self) -> None:
        """Lines 73-77: _elevation_from_extensions returns float from Garmin extension."""
        from unittest.mock import MagicMock
        from georeel.core.gpx_parser import _elevation_from_extensions, _GARMIN_NS

        ext = MagicMock()
        ele_elem = MagicMock()
        ele_elem.text = "425.5"
        ext.find.side_effect = lambda tag: ele_elem if tag == f"{{{_GARMIN_NS}}}ele" else None

        point = MagicMock()
        point.extensions = [ext]

        result = _elevation_from_extensions(point)
        assert result == pytest.approx(425.5)

    def test_elevation_from_extensions_returns_none_on_exception(self) -> None:
        """Lines 78-80: _elevation_from_extensions returns None when extension raises."""
        from georeel.core.gpx_parser import _elevation_from_extensions

        point_bad = type("P", (), {"extensions": None})()  # .extensions is not iterable
        result = _elevation_from_extensions(point_bad)  # type: ignore[arg-type]
        assert result is None

    def test_elevation_from_extensions_fallback_to_bare_ele(self) -> None:
        """Lines 74-77: falls back to bare 'ele' tag when Garmin NS tag not found."""
        from unittest.mock import MagicMock
        from georeel.core.gpx_parser import _elevation_from_extensions

        ext = MagicMock()
        ele_elem = MagicMock()
        ele_elem.text = "200.0"
        # First find (garmin NS) returns None, second find (bare) returns elem
        ext.find.side_effect = lambda tag: None if "{" in tag else ele_elem

        point = MagicMock()
        point.extensions = [ext]

        result = _elevation_from_extensions(point)
        assert result == pytest.approx(200.0)
