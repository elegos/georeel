"""Tests for POST/DELETE /api/v1/workspaces."""

from starlette.testclient import TestClient

from georeel.server.app import app


def test_create_workspace_returns_id() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/workspaces")
    assert response.status_code == 201
    data = response.json()
    assert "workspace_id" in data
    assert len(data["workspace_id"]) == 36  # UUID format


def test_create_two_workspaces_unique_ids() -> None:
    with TestClient(app) as client:
        r1 = client.post("/api/v1/workspaces")
        r2 = client.post("/api/v1/workspaces")
    assert r1.json()["workspace_id"] != r2.json()["workspace_id"]


def test_delete_workspace_success() -> None:
    with TestClient(app) as client:
        ws_id = client.post("/api/v1/workspaces").json()["workspace_id"]
        response = client.delete(f"/api/v1/workspaces/{ws_id}")
    assert response.status_code == 204


def test_delete_unknown_workspace_returns_404() -> None:
    with TestClient(app) as client:
        response = client.delete("/api/v1/workspaces/does-not-exist")
    assert response.status_code == 404


def test_delete_same_workspace_twice_returns_404() -> None:
    with TestClient(app) as client:
        ws_id = client.post("/api/v1/workspaces").json()["workspace_id"]
        client.delete(f"/api/v1/workspaces/{ws_id}")
        response = client.delete(f"/api/v1/workspaces/{ws_id}")
    assert response.status_code == 404
