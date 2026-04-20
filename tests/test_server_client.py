"""Tests for ServerClient — the GUI's typed HTTP wrapper for the REST API.

We spin up the FastAPI app in-process via Starlette's TestClient so no real
network or subprocess is involved.  The client is initialised with the
TestClient's transport so it can reach all endpoints.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image
from starlette.testclient import TestClient

from georeel.core.bounding_box import BoundingBox
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.match_result import MatchResult
from georeel.core.trackpoint import Trackpoint
from georeel.server.app import app
from georeel.server.jobs import get_registry
from georeel.server.routes.satellite import SatelliteJobResult
from georeel.ui.server_client import (
    ServerClient,
    ServerError,
    bbox_to_dict,
    elevation_grid_to_dict,
    keyframe_to_dict,
    match_result_to_dict,
    trackpoint_to_dict,
    _grid_from_dict,
    _keyframe_from_dict,
)


# ── Fixture: client backed by in-process ASGI app ─────────────────────────────

@pytest.fixture
def client() -> ServerClient:
    """ServerClient whose httpx.Client is wired to the in-process ASGI app."""
    tc = TestClient(app, raise_server_exceptions=False)
    sc = ServerClient.__new__(ServerClient)
    sc._base = ""
    sc._client = tc  # type: ignore[assignment]
    return sc


@pytest.fixture
def workspace_id(client: ServerClient) -> str:
    return client.create_workspace()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flat_grid(rows: int = 4, cols: int = 4) -> ElevationGrid:
    data = np.full((rows, cols), 300.0, dtype=np.float32)
    return ElevationGrid(
        data=data, min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1
    )


def _make_done_dem_job() -> str:
    job = get_registry().create()
    job.status = "done"
    job.progress = 100
    job.result = _flat_grid()
    return job.job_id


def _make_done_satellite_job(tmp_path: Path) -> tuple[str, Path]:
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
    return job.job_id, png


# ── Serialization helpers ─────────────────────────────────────────────────────

class TestSerializationHelpers:
    def test_trackpoint_to_dict_roundtrip(self) -> None:
        tp = Trackpoint(latitude=46.0, longitude=11.0, elevation=500.0, timestamp=None)
        d = trackpoint_to_dict(tp)
        assert d["latitude"] == 46.0
        assert d["elevation"] == 500.0
        assert d["timestamp"] is None

    def test_match_result_to_dict(self) -> None:
        mr = MatchResult(photo_path="/a/b.jpg", trackpoint_index=3)
        d = match_result_to_dict(mr)
        assert d["photo_path"] == "/a/b.jpg"
        assert d["trackpoint_index"] == 3

    def test_keyframe_to_dict(self) -> None:
        kf = CameraKeyframe(
            frame=10, x=1.0, y=2.0, z=3.0,
            look_at_x=4.0, look_at_y=5.0, look_at_z=6.0,
        )
        d = keyframe_to_dict(kf)
        assert d["frame"] == 10
        assert d["is_pause"] is False

    def test_bbox_to_dict(self) -> None:
        bb = BoundingBox(min_lat=1.0, max_lat=2.0, min_lon=3.0, max_lon=4.0)
        d = bbox_to_dict(bb)
        assert d == {"min_lat": 1.0, "max_lat": 2.0, "min_lon": 3.0, "max_lon": 4.0}

    def test_elevation_grid_roundtrip(self) -> None:
        grid = _flat_grid()
        d = elevation_grid_to_dict(grid)
        assert d["rows"] == 4
        assert d["cols"] == 4
        recovered = _grid_from_dict(d)
        assert recovered.rows == 4
        np.testing.assert_array_almost_equal(recovered.data, grid.data)

    def test_keyframe_from_dict(self) -> None:
        d = {
            "frame": 5, "x": 1.0, "y": 2.0, "z": 3.0,
            "look_at_x": 0.0, "look_at_y": 0.0, "look_at_z": 0.0,
            "is_pause": True, "photo_path": "/img.jpg",
        }
        kf = _keyframe_from_dict(d)
        assert kf.frame == 5
        assert kf.is_pause is True
        assert kf.photo_path == "/img.jpg"


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_is_alive_returns_true(self, client: ServerClient) -> None:
        assert client.is_alive() is True


# ── Workspace ─────────────────────────────────────────────────────────────────

class TestWorkspace:
    def test_create_workspace(self, client: ServerClient) -> None:
        ws_id = client.create_workspace()
        assert isinstance(ws_id, str)
        assert len(ws_id) > 0

    def test_delete_workspace(self, client: ServerClient) -> None:
        ws_id = client.create_workspace()
        # Should not raise
        client.delete_workspace(ws_id)


# ── Jobs ──────────────────────────────────────────────────────────────────────

class TestJobs:
    def test_get_job_done(self, client: ServerClient) -> None:
        job_id = _make_done_dem_job()
        job = client.get_job(job_id)
        assert job["status"] == "done"
        assert job["progress"] == 100

    def test_get_job_not_found_raises(self, client: ServerClient) -> None:
        with pytest.raises(ServerError, match="404"):
            client.get_job("no-such-job")

    def test_cancel_job(self, client: ServerClient) -> None:
        job = get_registry().create()
        job.status = "running"
        # Should not raise
        client.cancel_job(job.job_id)

    def test_result_path_exposed_for_string_result(
        self, client: ServerClient
    ) -> None:
        job = get_registry().create()
        job.status = "done"
        job.result = "/some/path/scene.blend"
        data = client.get_job(job.job_id)
        assert data["result_path"] == "/some/path/scene.blend"

    def test_result_path_none_for_non_string_result(
        self, client: ServerClient
    ) -> None:
        job_id = _make_done_dem_job()
        data = client.get_job(job_id)
        assert data["result_path"] is None

    def test_poll_job_done(self, client: ServerClient) -> None:
        job_id = _make_done_dem_job()
        result = client.poll_job(job_id)
        assert result["status"] == "done"

    def test_poll_job_error_raises(self, client: ServerClient) -> None:
        job = get_registry().create()
        job.status = "error"
        job.error = "something broke"
        with pytest.raises(ServerError, match="something broke"):
            client.poll_job(job.job_id)

    def test_poll_job_cancelled_on_cancel_check(
        self, client: ServerClient
    ) -> None:
        job = get_registry().create()
        job.status = "running"
        with pytest.raises(ServerError, match="Cancelled"):
            client.poll_job(job.job_id, cancel_check=lambda: True)


# ── DEM ───────────────────────────────────────────────────────────────────────

class TestDemClient:
    def test_start_dem_fetch_returns_job_id(
        self, client: ServerClient, workspace_id: str
    ) -> None:
        bbox = bbox_to_dict(
            BoundingBox(min_lat=46.0, max_lat=46.1, min_lon=11.0, max_lon=11.1)
        )
        job_id = client.start_dem_fetch(workspace_id, bbox)
        assert isinstance(job_id, str)

    def test_get_dem_result_returns_elevation_grid(
        self, client: ServerClient
    ) -> None:
        job_id = _make_done_dem_job()
        grid = client.get_dem_result(job_id)
        assert isinstance(grid, ElevationGrid)
        assert grid.rows == 4
        assert grid.cols == 4

    def test_get_dem_result_409_when_running(
        self, client: ServerClient
    ) -> None:
        job = get_registry().create()
        job.status = "running"
        with pytest.raises(ServerError, match="409"):
            client.get_dem_result(job.job_id)


# ── Satellite ─────────────────────────────────────────────────────────────────

class TestSatelliteClient:
    def test_get_satellite_metadata(
        self, client: ServerClient, tmp_path: Path
    ) -> None:
        job_id, _ = _make_done_satellite_job(tmp_path)
        meta = client.get_satellite_metadata(job_id)
        assert meta["width"] == 4
        assert meta["provider_id"] == "esri_world"

    def test_download_satellite_texture(
        self, client: ServerClient, tmp_path: Path
    ) -> None:
        job_id, _ = _make_done_satellite_job(tmp_path)
        texture = client.download_satellite_texture(job_id)
        assert texture.width == 4
        assert texture.height == 4
        assert texture.provider_id == "esri_world"
