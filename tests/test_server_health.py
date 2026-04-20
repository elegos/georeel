"""Tests for GET /api/v1/health."""

from importlib.metadata import version

from starlette.testclient import TestClient

from georeel.server.app import app


def test_health_status_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_version_matches_package() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.json()["version"] == version("georeel")


def test_health_response_schema() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    data = response.json()
    assert set(data.keys()) == {"status", "version"}


def test_health_wrong_method_returns_405() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/health")
    assert response.status_code == 405


def test_server_main_arg_parsing() -> None:
    """Ensure --host / --port are parsed without error."""
    from georeel.server.main import _parse_args

    args = _parse_args(["--host", "0.0.0.0", "--port", "9000", "--log-level", "debug"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.log_level == "debug"


def test_server_main_defaults() -> None:
    from georeel.server.main import DEFAULT_HOST, DEFAULT_PORT, _parse_args

    args = _parse_args([])
    assert args.host == DEFAULT_HOST
    assert args.port == DEFAULT_PORT
    assert args.log_level == "info"
