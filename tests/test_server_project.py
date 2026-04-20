"""Tests for POST /api/v1/project/save and /api/v1/project/load."""

from __future__ import annotations

import base64
import io
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from starlette.testclient import TestClient

from georeel.core.elevation_grid import ElevationGrid
from georeel.core.photo_metadata import PhotoMetadata
from georeel.core.project import ProjectState, save_project
from georeel.core.satellite.texture import SatelliteTexture
from georeel.server.app import app


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _minimal_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(80, 120, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _dem_dict() -> dict[str, object]:
    data = np.ones((3, 3), dtype=np.float32) * 100.0
    data_b64 = base64.b64encode(data.tobytes()).decode()
    return {
        "rows": 3, "cols": 3,
        "min_lat": 46.0, "max_lat": 46.1,
        "min_lon": 11.0, "max_lon": 11.1,
        "data_b64": data_b64,
    }


def _satellite_dict() -> dict[str, object]:
    return {
        "min_lat": 46.0, "max_lat": 46.1,
        "min_lon": 11.0, "max_lon": 11.1,
        "provider_id": "esri_world",
        "quality": "standard",
        "png_b64": base64.b64encode(_minimal_png()).decode(),
    }


def _create_workspace(client: TestClient) -> str:
    return client.post("/api/v1/workspaces").json()["workspace_id"]


def _upload_photo(client: TestClient, ws_id: str) -> str:
    r = client.post(
        f"/api/v1/photos/upload?workspace_id={ws_id}",
        files={"files": ("photo.jpg", _minimal_jpeg(), "image/jpeg")},
    )
    assert r.status_code == 201
    return str(r.json()["photos"][0]["photo_id"])


def _make_georeel_bytes(with_dem: bool = False, with_sat: bool = False) -> bytes:
    """Create a minimal in-memory .georeel ZIP using the core save_project helper."""
    dem: ElevationGrid | None = None
    if with_dem:
        dem = ElevationGrid(
            data=np.ones((3, 3), dtype=np.float32),
            min_lat=46.0, max_lat=46.1,
            min_lon=11.0, max_lon=11.1,
        )
    sat: SatelliteTexture | None = None
    if with_sat:
        sat = SatelliteTexture(
            image=Image.new("RGB", (4, 4)),
            min_lat=46.0, max_lat=46.1,
            min_lon=11.0, max_lon=11.1,
            provider_id="esri_world",
            quality="standard",
        )
    state = ProjectState(
        gpx_path=None,
        match_mode="both",
        output_path=None,
        photos=[],
        elevation_grid=dem,
        satellite_texture=sat,
        render_settings={"render/fps": 30},
    )
    with tempfile.NamedTemporaryFile(suffix=".georeel", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        save_project(state, tmp_path)
        return Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Save tests ────────────────────────────────────────────────────────────────

class TestProjectSave:
    def test_save_minimal_returns_zip(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            r = client.post("/api/v1/project/save", json={
                "workspace_id": ws_id,
                "match_mode": "both",
            })
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        # ZIP magic bytes
        assert r.content[:2] == b"PK"

    def test_save_with_dem_and_satellite(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            r = client.post("/api/v1/project/save", json={
                "workspace_id": ws_id,
                "match_mode": "both",
                "elevation_grid": _dem_dict(),
                "satellite": _satellite_dict(),
            })
        assert r.status_code == 200
        assert r.content[:2] == b"PK"

    def test_save_with_render_settings(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            r = client.post("/api/v1/project/save", json={
                "workspace_id": ws_id,
                "match_mode": "gps",
                "render_settings": {"render/fps": 30, "render/width": 1920},
            })
        assert r.status_code == 200

    def test_save_with_photo(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            photo_id = _upload_photo(client, ws_id)
            r = client.post("/api/v1/project/save", json={
                "workspace_id": ws_id,
                "match_mode": "both",
                "photos": [{"photo_id": photo_id}],
            })
        assert r.status_code == 200
        assert r.content[:2] == b"PK"

    def test_save_unknown_workspace_returns_404(self) -> None:
        with TestClient(app) as client:
            r = client.post("/api/v1/project/save", json={
                "workspace_id": "does-not-exist",
                "match_mode": "both",
            })
        assert r.status_code == 404

    def test_save_unknown_photo_id_returns_400(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            r = client.post("/api/v1/project/save", json={
                "workspace_id": ws_id,
                "match_mode": "both",
                "photos": [{"photo_id": "no-such-photo"}],
            })
        assert r.status_code == 400

    def test_save_content_disposition_header(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            r = client.post("/api/v1/project/save", json={
                "workspace_id": ws_id, "match_mode": "both",
            })
        assert "georeel" in r.headers.get("content-disposition", "")


# ── Load tests ────────────────────────────────────────────────────────────────

class TestProjectLoad:
    def test_load_minimal_returns_workspace_id(self) -> None:
        georeel_bytes = _make_georeel_bytes()
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/project/load",
                files={"file": ("project.georeel", georeel_bytes, "application/zip")},
            )
        assert r.status_code == 200
        data = r.json()
        assert "workspace_id" in data
        assert len(data["workspace_id"]) == 36  # UUID

    def test_load_preserves_match_mode(self) -> None:
        state = ProjectState(
            gpx_path=None, match_mode="timestamp", output_path=None, photos=[],
        )
        with tempfile.NamedTemporaryFile(suffix=".georeel", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            save_project(state, tmp_path)
            georeel_bytes = Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/project/load",
                files={"file": ("p.georeel", georeel_bytes, "application/zip")},
            )
        assert r.json()["match_mode"] == "timestamp"

    def test_load_with_dem_returns_elevation_grid(self) -> None:
        georeel_bytes = _make_georeel_bytes(with_dem=True)
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/project/load",
                files={"file": ("p.georeel", georeel_bytes, "application/zip")},
            )
        data = r.json()
        assert data["elevation_grid"] is not None
        assert data["elevation_grid"]["rows"] == 3
        assert data["elevation_grid"]["cols"] == 3

    def test_load_with_satellite_returns_png_b64(self) -> None:
        georeel_bytes = _make_georeel_bytes(with_sat=True)
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/project/load",
                files={"file": ("p.georeel", georeel_bytes, "application/zip")},
            )
        data = r.json()
        assert data["satellite"] is not None
        png_bytes = base64.b64decode(data["satellite"]["png_b64"])
        img = Image.open(io.BytesIO(png_bytes))
        assert img.width > 0

    def test_load_creates_new_workspace_each_time(self) -> None:
        georeel_bytes = _make_georeel_bytes()
        with TestClient(app) as client:
            r1 = client.post(
                "/api/v1/project/load",
                files={"file": ("p.georeel", georeel_bytes, "application/zip")},
            )
            r2 = client.post(
                "/api/v1/project/load",
                files={"file": ("p.georeel", georeel_bytes, "application/zip")},
            )
        assert r1.json()["workspace_id"] != r2.json()["workspace_id"]

    def test_load_corrupt_file_returns_422(self) -> None:
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/project/load",
                files={"file": ("bad.georeel", b"not a zip file", "application/zip")},
            )
        assert r.status_code == 422

    def test_load_no_photos_returns_empty_list(self) -> None:
        georeel_bytes = _make_georeel_bytes()
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/project/load",
                files={"file": ("p.georeel", georeel_bytes, "application/zip")},
            )
        assert r.json()["photos"] == []


# ── Roundtrip ─────────────────────────────────────────────────────────────────

class TestProjectRoundtrip:
    def test_save_then_load_roundtrip(self) -> None:
        """Save via server, load via server, verify key fields survive."""
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            save_r = client.post("/api/v1/project/save", json={
                "workspace_id": ws_id,
                "match_mode": "gps",
                "output_path": "/tmp/out.mp4",
                "elevation_grid": _dem_dict(),
                "render_settings": {"render/fps": 24},
            })
            assert save_r.status_code == 200

            load_r = client.post(
                "/api/v1/project/load",
                files={"file": ("p.georeel", save_r.content, "application/zip")},
            )
        assert load_r.status_code == 200
        data = load_r.json()
        assert data["match_mode"] == "gps"
        assert data["render_settings"] == {"render/fps": 24}
        assert data["elevation_grid"] is not None
        assert data["elevation_grid"]["rows"] == 3
