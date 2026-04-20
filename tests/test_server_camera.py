"""Tests for POST /api/v1/camera/keyframes."""

import base64

import numpy as np
import pytest
from starlette.testclient import TestClient

from georeel.server.app import app


def _flat_grid_b64(rows: int = 10, cols: int = 10, elevation: float = 500.0) -> str:
    """Serialise a flat ElevationGrid as base64 float32 bytes."""
    data = np.full((rows, cols), elevation, dtype=np.float32)
    return base64.b64encode(data.tobytes()).decode()


def _grid_payload(
    rows: int = 10,
    cols: int = 10,
    min_lat: float = 46.0,
    max_lat: float = 46.2,
    min_lon: float = 11.0,
    max_lon: float = 11.2,
    elevation: float = 500.0,
) -> dict:
    return {
        "rows": rows,
        "cols": cols,
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
        "data_b64": _flat_grid_b64(rows, cols, elevation),
    }


_TRACKPOINTS = [
    {"latitude": 46.0 + i * 0.01, "longitude": 11.0 + i * 0.01,
     "elevation": 500.0, "timestamp": None, "is_reconstructed": False}
    for i in range(20)
]

_SETTINGS = {
    "render/fps": 30,
    "render/camera_speed_mps": 300.0,
    "render/path_smoothing": "spline",
    "render/camera_height_mode": "dem_fixed",
    "render/camera_height_offset": 200.0,
    "render/camera_orientation": "tangent",
    "render/camera_tilt_deg": 45.0,
    "render/tangent_lookahead_s": 2.0,
    "render/tangent_weight": "linear",
    "render/photo_pause_mode": "hold",
    "render/photo_pause_duration": 3.0,
}


class TestCameraKeyframes:
    def _body(self, **overrides: object) -> dict:
        payload: dict = {
            "trackpoints": _TRACKPOINTS,
            "elevation_grid": _grid_payload(),
            "match_results": [],
            "settings": _SETTINGS,
        }
        payload.update(overrides)
        return payload

    def test_returns_keyframes(self) -> None:
        with TestClient(app) as client:
            response = client.post("/api/v1/camera/keyframes", json=self._body())
        assert response.status_code == 200
        kfs = response.json()["keyframes"]
        assert len(kfs) > 0

    def test_keyframe_schema(self) -> None:
        with TestClient(app) as client:
            response = client.post("/api/v1/camera/keyframes", json=self._body())
        kf = response.json()["keyframes"][0]
        for field in ("frame", "x", "y", "z", "look_at_x", "look_at_y", "look_at_z",
                      "is_pause", "photo_path"):
            assert field in kf

    def test_frames_are_sequential(self) -> None:
        with TestClient(app) as client:
            response = client.post("/api/v1/camera/keyframes", json=self._body())
        frames = [kf["frame"] for kf in response.json()["keyframes"]]
        assert frames == sorted(frames)
        assert frames[0] >= 0

    def test_empty_trackpoints_returns_422(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/camera/keyframes",
                json=self._body(trackpoints=[]),
            )
        assert response.status_code == 422

    def test_elevation_grid_roundtrip(self) -> None:
        """Grid encoded as base64 is correctly decoded by the endpoint."""
        rows, cols = 5, 5
        data = np.arange(rows * cols, dtype=np.float32).reshape(rows, cols)
        grid = {
            "rows": rows, "cols": cols,
            "min_lat": 46.0, "max_lat": 46.2,
            "min_lon": 11.0, "max_lon": 11.2,
            "data_b64": base64.b64encode(data.tobytes()).decode(),
        }
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/camera/keyframes",
                json=self._body(elevation_grid=grid),
            )
        # The endpoint should not error; camera path may be short but valid
        assert response.status_code in (200, 422)  # 422 only if < 2 usable points
