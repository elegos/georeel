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
