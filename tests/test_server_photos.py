"""Tests for POST /api/v1/photos/upload and /api/v1/photos/match."""

import io

from PIL import Image
from starlette.testclient import TestClient

from georeel.server.app import app


def _minimal_jpeg() -> bytes:
    """Create a minimal 2x2 RGB JPEG with no EXIF data."""
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color=(128, 64, 32)).save(buf, format="JPEG")
    return buf.getvalue()


_TRACKPOINTS = [
    {
        "latitude": 46.0, "longitude": 11.0, "elevation": 100.0,
        "timestamp": "2024-06-01T08:00:00Z", "is_reconstructed": False,
    },
    {
        "latitude": 46.1, "longitude": 11.1, "elevation": 200.0,
        "timestamp": "2024-06-01T08:10:00Z", "is_reconstructed": False,
    },
]


def _create_workspace(client: TestClient) -> str:
    return client.post("/api/v1/workspaces").json()["workspace_id"]


class TestPhotosUpload:
    def test_upload_single_photo(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                f"/api/v1/photos/upload?workspace_id={ws_id}",
                files={"files": ("photo.jpg", _minimal_jpeg(), "image/jpeg")},
            )
        assert response.status_code == 201
        photos = response.json()["photos"]
        assert len(photos) == 1
        assert "photo_id" in photos[0]

    def test_upload_returns_server_path(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                f"/api/v1/photos/upload?workspace_id={ws_id}",
                files={"files": ("photo.jpg", _minimal_jpeg(), "image/jpeg")},
            )
        photo = response.json()["photos"][0]
        assert photo["path"].endswith(".jpg")

    def test_upload_multiple_photos(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                f"/api/v1/photos/upload?workspace_id={ws_id}",
                files=[
                    ("files", ("a.jpg", _minimal_jpeg(), "image/jpeg")),
                    ("files", ("b.jpg", _minimal_jpeg(), "image/jpeg")),
                ],
            )
        assert response.status_code == 201
        assert len(response.json()["photos"]) == 2

    def test_upload_unique_photo_ids(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                f"/api/v1/photos/upload?workspace_id={ws_id}",
                files=[
                    ("files", ("a.jpg", _minimal_jpeg(), "image/jpeg")),
                    ("files", ("b.jpg", _minimal_jpeg(), "image/jpeg")),
                ],
            )
        ids = [p["photo_id"] for p in response.json()["photos"]]
        assert ids[0] != ids[1]

    def test_upload_unknown_workspace_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/photos/upload?workspace_id=does-not-exist",
                files={"files": ("photo.jpg", _minimal_jpeg(), "image/jpeg")},
            )
        assert response.status_code == 404

    def test_upload_no_exif_yields_null_metadata(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                f"/api/v1/photos/upload?workspace_id={ws_id}",
                files={"files": ("photo.jpg", _minimal_jpeg(), "image/jpeg")},
            )
        photo = response.json()["photos"][0]
        assert photo["timestamp"] is None
        assert photo["latitude"] is None
        assert photo["longitude"] is None


class TestPhotosMatch:
    def _upload_photo(self, client: TestClient, ws_id: str) -> str:
        resp = client.post(
            f"/api/v1/photos/upload?workspace_id={ws_id}",
            files={"files": ("photo.jpg", _minimal_jpeg(), "image/jpeg")},
        )
        return resp.json()["photos"][0]["photo_id"]

    def test_match_no_exif_returns_error_result(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            photo_id = self._upload_photo(client, ws_id)
            response = client.post(
                "/api/v1/photos/match",
                json={
                    "workspace_id": ws_id,
                    "photo_ids": [photo_id],
                    "trackpoints": _TRACKPOINTS,
                    "mode": "both",
                },
            )
        assert response.status_code == 200
        result = response.json()["match_results"][0]
        # No EXIF → cannot match → error field set
        assert result["error"] is not None

    def test_match_unknown_workspace_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/photos/match",
                json={
                    "workspace_id": "does-not-exist",
                    "photo_ids": [],
                    "trackpoints": _TRACKPOINTS,
                    "mode": "both",
                },
            )
        assert response.status_code == 404

    def test_match_unknown_photo_id_returns_422(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/photos/match",
                json={
                    "workspace_id": ws_id,
                    "photo_ids": ["non-existent-id"],
                    "trackpoints": _TRACKPOINTS,
                    "mode": "both",
                },
            )
        assert response.status_code == 422

    def test_match_empty_photo_list_returns_empty(self) -> None:
        with TestClient(app) as client:
            ws_id = _create_workspace(client)
            response = client.post(
                "/api/v1/photos/match",
                json={
                    "workspace_id": ws_id,
                    "photo_ids": [],
                    "trackpoints": _TRACKPOINTS,
                    "mode": "both",
                },
            )
        assert response.status_code == 200
        assert response.json()["match_results"] == []
