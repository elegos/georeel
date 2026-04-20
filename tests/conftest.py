"""Shared fixtures for the GeoReel test suite."""

import pytest

from georeel.core.photo_store import PhotoStore


@pytest.fixture(autouse=True)
def reset_photo_store():
    """Reset the PhotoStore singleton before every test."""
    store = PhotoStore.instance()
    store.clear()
    yield
    store.clear()


@pytest.fixture(autouse=True)
def reset_workspace_manager():
    """Reset the WorkspaceManager singleton before every test."""
    import georeel.server.workspace as ws_mod
    ws_mod.reset_manager()
    yield
    ws_mod.reset_manager()


@pytest.fixture(autouse=True)
def reset_job_registry():
    """Reset the JobRegistry singleton before every test."""
    import georeel.server.jobs as jobs_mod
    jobs_mod.reset_registry()
    yield
    jobs_mod.reset_registry()
