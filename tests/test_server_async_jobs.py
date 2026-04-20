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
    png = tmp_path / "texture.png"
    Image.new("RGB", (4, 4), (100, 150, 200)).save(str(png))
    job = get_registry().create()
    job.status = "done"
    job.progress = 100
    job.result = SatelliteJobResult(
        png_path=str(png),
        min_lat=46.0, max_lat=46.1,
        min_lon=11.0, max_lon=11.1,
        provider_id="esri_world",
        quality="standard",
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
        assert response.headers["content-type"].startswith("image/png")

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
