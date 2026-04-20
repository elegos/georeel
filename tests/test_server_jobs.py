"""Tests for /api/v1/jobs/* endpoints."""

import asyncio

import pytest
from starlette.testclient import TestClient

from georeel.server.app import app
from georeel.server.jobs import get_registry


def _make_done_job(result: object = "result") -> str:
    """Create a completed job directly in the registry and return its job_id."""
    job = get_registry().create()
    job.status = "done"
    job.progress = 100
    job.message = "finished"
    job.result = result
    return job.job_id


def _make_running_job() -> str:
    job = get_registry().create()
    job.status = "running"
    job.progress = 42
    job.message = "working"
    return job.job_id


def _make_error_job() -> str:
    job = get_registry().create()
    job.status = "error"
    job.error = "something went wrong"
    return job.job_id


class TestGetJob:
    def test_get_done_job(self) -> None:
        job_id = _make_done_job()
        with TestClient(app) as client:
            response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "done"
        assert data["progress"] == 100

    def test_get_running_job(self) -> None:
        job_id = _make_running_job()
        with TestClient(app) as client:
            response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "running"
        assert response.json()["progress"] == 42

    def test_get_error_job(self) -> None:
        job_id = _make_error_job()
        with TestClient(app) as client:
            response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["error"] == "something went wrong"

    def test_get_unknown_job_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.get("/api/v1/jobs/does-not-exist")
        assert response.status_code == 404

    def test_response_schema_has_required_fields(self) -> None:
        job_id = _make_done_job()
        with TestClient(app) as client:
            data = client.get(f"/api/v1/jobs/{job_id}").json()
        for field in ("job_id", "status", "progress", "message", "error"):
            assert field in data


class TestCancelJob:
    def test_cancel_running_job(self) -> None:
        job_id = _make_running_job()
        with TestClient(app) as client:
            response = client.delete(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 204

    def test_cancel_sets_cancel_flag(self) -> None:
        job_id = _make_running_job()
        job = get_registry().get(job_id)
        assert job is not None
        assert not job.is_cancelled()
        with TestClient(app) as client:
            client.delete(f"/api/v1/jobs/{job_id}")
        assert job.is_cancelled()

    def test_cancel_unknown_job_returns_404(self) -> None:
        with TestClient(app) as client:
            response = client.delete("/api/v1/jobs/does-not-exist")
        assert response.status_code == 404


class TestJobEvents:
    def test_sse_done_job_yields_final_event(self) -> None:
        job_id = _make_done_job()
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/jobs/{job_id}/events",
                headers={"Accept": "text/event-stream"},
            )
        assert response.status_code == 200
        body = response.text
        assert "done" in body

    def test_sse_unknown_job_yields_error(self) -> None:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/jobs/does-not-exist/events",
                headers={"Accept": "text/event-stream"},
            )
        assert response.status_code == 200
        assert "error" in response.text

    def test_sse_error_job_yields_error_status(self) -> None:
        job_id = _make_error_job()
        with TestClient(app) as client:
            response = client.get(f"/api/v1/jobs/{job_id}/events")
        assert "error" in response.text
