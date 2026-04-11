"""Tests for satellite.providers."""

import pytest
from georeel.core.satellite.providers import (
    PROVIDERS,
    ProviderConfig,
    get_provider,
    QUALITY_MAX_TILES,
)


class TestProviders:
    def test_providers_not_empty(self):
        assert len(PROVIDERS) > 0

    def test_all_have_id_and_label(self):
        for p in PROVIDERS:
            assert p.id
            assert p.label

    def test_known_providers_present(self):
        ids = {p.id for p in PROVIDERS}
        assert "esri_world" in ids
        assert "custom" in ids

    def test_custom_provider_empty_url(self):
        custom = next(p for p in PROVIDERS if p.id == "custom")
        assert custom.url_template == ""
        assert custom.requires_key is False

    def test_maptiler_requires_key(self):
        maptiler = next((p for p in PROVIDERS if p.id == "maptiler_satellite"), None)
        if maptiler:
            assert maptiler.requires_key is True

    def test_esri_no_key_required(self):
        esri = next(p for p in PROVIDERS if p.id == "esri_world")
        assert esri.requires_key is False

    def test_all_max_zoom_positive(self):
        for p in PROVIDERS:
            assert p.max_zoom > 0

    def test_frozen_dataclass(self):
        p = PROVIDERS[0]
        with pytest.raises((AttributeError, TypeError)):
            p.id = "new_id"  # type: ignore[misc]


class TestGetProvider:
    def test_known_id_returns_correct(self):
        p = get_provider("esri_world")
        assert p.id == "esri_world"

    def test_unknown_id_returns_default(self):
        p = get_provider("does_not_exist")
        assert p is PROVIDERS[0]

    def test_empty_id_returns_default(self):
        p = get_provider("")
        assert p is PROVIDERS[0]

    def test_all_providers_retrievable(self):
        for provider in PROVIDERS:
            result = get_provider(provider.id)
            assert result.id == provider.id


class TestQualityMaxTiles:
    def test_standard_present(self):
        assert "standard" in QUALITY_MAX_TILES

    def test_high_present(self):
        assert "high" in QUALITY_MAX_TILES

    def test_very_high_present(self):
        assert "very_high" in QUALITY_MAX_TILES

    def test_increasing_quality_more_tiles(self):
        assert QUALITY_MAX_TILES["standard"] < QUALITY_MAX_TILES["high"]
        assert QUALITY_MAX_TILES["high"] < QUALITY_MAX_TILES["very_high"]

    def test_all_positive(self):
        for v in QUALITY_MAX_TILES.values():
            assert v > 0
