"""Tests for satellite.texture.SatelliteTexture."""

import pytest
from PIL import Image
from georeel.core.satellite.texture import SatelliteTexture


def _make_texture(width=100, height=80, color=(128, 64, 32)):
    img = Image.new("RGB", (width, height), color)
    return SatelliteTexture(
        image=img,
        min_lat=10.0, max_lat=11.0,
        min_lon=20.0, max_lon=21.0,
        provider_id="esri_world",
        quality="standard",
    )


class TestSatelliteTextureProperties:
    def test_width(self):
        t = _make_texture(width=200, height=150)
        assert t.width == 200

    def test_height(self):
        t = _make_texture(width=200, height=150)
        assert t.height == 150

    def test_provider_id(self):
        t = _make_texture()
        assert t.provider_id == "esri_world"

    def test_quality(self):
        t = _make_texture()
        assert t.quality == "standard"

    def test_coordinates(self):
        t = _make_texture()
        assert t.min_lat == 10.0
        assert t.max_lat == 11.0
        assert t.min_lon == 20.0
        assert t.max_lon == 21.0


class TestSatelliteTextureSerialization:
    def test_round_trip_preserves_size(self):
        t = _make_texture(width=100, height=80)
        raw = t.to_png_bytes()
        t2 = SatelliteTexture.from_png_bytes(
            raw,
            min_lat=10.0, max_lat=11.0,
            min_lon=20.0, max_lon=21.0,
        )
        assert t2.width == 100
        assert t2.height == 80

    def test_round_trip_preserves_metadata(self):
        t = _make_texture()
        raw = t.to_png_bytes()
        t2 = SatelliteTexture.from_png_bytes(
            raw,
            min_lat=10.0, max_lat=11.0,
            min_lon=20.0, max_lon=21.0,
            provider_id="esri_clarity",
            quality="high",
        )
        assert t2.min_lat == 10.0
        assert t2.provider_id == "esri_clarity"
        assert t2.quality == "high"

    def test_to_png_bytes_returns_bytes(self):
        t = _make_texture()
        raw = t.to_png_bytes()
        assert isinstance(raw, bytes)
        assert len(raw) > 0

    def test_from_png_bytes_returns_rgb(self):
        t = _make_texture()
        raw = t.to_png_bytes()
        t2 = SatelliteTexture.from_png_bytes(raw, 0.0, 1.0, 0.0, 1.0)
        assert t2.image.mode == "RGB"

    def test_rgba_image_converted_to_rgb(self):
        img = Image.new("RGBA", (50, 50), (255, 0, 0, 128))
        t = SatelliteTexture(image=img, min_lat=0.0, max_lat=1.0,
                              min_lon=0.0, max_lon=1.0)
        raw = t.to_png_bytes()
        t2 = SatelliteTexture.from_png_bytes(raw, 0.0, 1.0, 0.0, 1.0)
        assert t2.image.mode == "RGB"

    def test_default_provider_and_quality(self):
        t = _make_texture()
        raw = t.to_png_bytes()
        t2 = SatelliteTexture.from_png_bytes(raw, 0.0, 1.0, 0.0, 1.0)
        assert t2.provider_id == ""
        assert t2.quality == "standard"
