"""Tests for project.save_project / load_project round-trips."""

import shutil
import zipfile
import pytest
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

from georeel.core.elevation_grid import ElevationGrid
from georeel.core.photo_metadata import PhotoMetadata
from georeel.core.project import (
    ProjectState,
    save_project,
    load_project,
    _should_embed_font,
    _serialise_photo,
    _deserialise_photos,
)
from georeel.core.satellite.texture import SatelliteTexture

import numpy as np


def _state(
    tmp_path,
    gpx=None,
    photos=None,
    match_mode="timestamp",
    output_path=None,
    elevation_grid=None,
    satellite_texture=None,
    clip_effects=None,
    render_settings=None,
):
    return ProjectState(
        gpx_path=gpx,
        match_mode=match_mode,
        output_path=output_path,
        photos=photos or [],
        elevation_grid=elevation_grid,
        satellite_texture=satellite_texture,
        clip_effects=clip_effects,
        render_settings=render_settings,
    )


def _make_grid():
    data = np.array([[100.0, 200.0], [300.0, 400.0]], dtype=np.float32)
    return ElevationGrid(data=data, min_lat=10.0, max_lat=11.0, min_lon=20.0, max_lon=21.0)


def _make_texture():
    img = Image.new("RGB", (64, 64), (128, 128, 128))
    return SatelliteTexture(image=img, min_lat=10.0, max_lat=11.0,
                             min_lon=20.0, max_lon=21.0, provider_id="esri_world", quality="high")


class TestSaveLoadMinimal:
    def test_round_trip_basic_fields(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        state = _state(tmp_path, match_mode="gps", output_path="/out.mkv")
        save_project(state, path)
        loaded = load_project(path)
        assert loaded.match_mode == "gps"
        assert loaded.output_path == "/out.mkv"
        assert loaded.photos == []

    def test_output_is_zip(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path), path)
        assert zipfile.is_zipfile(path)

    def test_manifest_present(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path), path)
        with zipfile.ZipFile(path) as zf:
            assert "manifest.json" in zf.namelist()

    def test_project_json_present(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path), path)
        with zipfile.ZipFile(path) as zf:
            assert "project.json" in zf.namelist()


class TestSaveLoadElevationGrid:
    def test_round_trip_dem(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        g = _make_grid()
        state = _state(tmp_path, elevation_grid=g)
        save_project(state, path)
        loaded = load_project(path)
        assert loaded.elevation_grid is not None
        assert loaded.elevation_grid.rows == 2
        assert loaded.elevation_grid.cols == 2
        np.testing.assert_array_almost_equal(
            loaded.elevation_grid.data, g.data, decimal=3
        )

    def test_dem_metadata_preserved(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        state = _state(tmp_path, elevation_grid=_make_grid())
        save_project(state, path)
        loaded = load_project(path)
        g = loaded.elevation_grid
        assert g.min_lat == pytest.approx(10.0)
        assert g.max_lat == pytest.approx(11.0)
        assert g.min_lon == pytest.approx(20.0)
        assert g.max_lon == pytest.approx(21.0)

    def test_no_dem_loads_as_none(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path), path)
        loaded = load_project(path)
        assert loaded.elevation_grid is None


class TestSaveLoadSatelliteTexture:
    def test_round_trip_texture(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        t = _make_texture()
        save_project(_state(tmp_path, satellite_texture=t), path)
        loaded = load_project(path)
        assert loaded.satellite_texture is not None
        assert loaded.satellite_texture.width == 64
        assert loaded.satellite_texture.height == 64
        assert loaded.satellite_texture.provider_id == "esri_world"
        assert loaded.satellite_texture.quality == "high"

    def test_no_texture_loads_as_none(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path), path)
        loaded = load_project(path)
        assert loaded.satellite_texture is None


class TestSaveLoadPhotos:
    def _make_photo_file(self, tmp_path, name="photo.jpg"):
        p = tmp_path / name
        img = Image.new("RGB", (10, 10), (255, 0, 0))
        img.save(str(p), format="JPEG")
        return str(p)

    def test_photos_embedded_and_extracted(self, tmp_path):
        photo_path = self._make_photo_file(tmp_path, "IMG_1234.jpg")
        photo = PhotoMetadata(path=photo_path, timestamp=None, latitude=None, longitude=None)
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, photos=[photo]), path)

        with zipfile.ZipFile(path) as zf:
            entries = zf.namelist()
        # Photo should be stored under its original name
        assert any("IMG_1234.jpg" in e for e in entries)

    def test_original_filename_preserved_in_zip(self, tmp_path):
        photo_path = self._make_photo_file(tmp_path, "vacation_shot.jpg")
        photo = PhotoMetadata(path=photo_path, timestamp=None, latitude=None, longitude=None)
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, photos=[photo]), path)
        with zipfile.ZipFile(path) as zf:
            assert "photos/vacation_shot.jpg" in zf.namelist()

    def test_duplicate_filenames_get_suffix(self, tmp_path):
        # Two photos with the same filename in different dirs
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        for d in (d1, d2):
            img = Image.new("RGB", (10, 10))
            img.save(str(d / "photo.jpg"), format="JPEG")
        photos = [
            PhotoMetadata(path=str(d1 / "photo.jpg"), timestamp=None, latitude=None, longitude=None),
            PhotoMetadata(path=str(d2 / "photo.jpg"), timestamp=None, latitude=None, longitude=None),
        ]
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, photos=photos), path)
        with zipfile.ZipFile(path) as zf:
            entries = [e for e in zf.namelist() if e.startswith("photos/")]
        # Both photos present, with distinct names
        assert len(entries) == 2
        assert len(set(entries)) == 2

    def test_photo_timestamps_round_trip(self, tmp_path):
        photo_path = self._make_photo_file(tmp_path)
        ts = datetime(2023, 6, 1, 10, 30, tzinfo=timezone.utc)
        photo = PhotoMetadata(path=photo_path, timestamp=ts, latitude=48.0, longitude=2.0)
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, photos=[photo]), path)
        loaded = load_project(path)
        assert len(loaded.photos) == 1
        loaded_photo = loaded.photos[0]
        assert loaded_photo.timestamp is not None
        assert loaded_photo.latitude == pytest.approx(48.0)
        assert loaded_photo.longitude == pytest.approx(2.0)

    def test_nonexistent_photo_not_embedded(self, tmp_path):
        photo = PhotoMetadata(path="/nonexistent/photo.jpg", timestamp=None,
                               latitude=None, longitude=None)
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, photos=[photo]), path)  # should not raise
        with zipfile.ZipFile(path) as zf:
            entries = [e for e in zf.namelist() if e.startswith("photos/")]
        assert entries == []


class TestSaveLoadGpx:
    def test_gpx_embedded(self, tmp_path):
        gpx_path = tmp_path / "track.gpx"
        gpx_path.write_text("<gpx/>", encoding="utf-8")
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, gpx=str(gpx_path)), path)
        with zipfile.ZipFile(path) as zf:
            assert "gpx/track.gpx" in zf.namelist()

    def test_gpx_extracted_on_load(self, tmp_path):
        gpx_path = tmp_path / "track.gpx"
        gpx_path.write_text("<gpx>content</gpx>", encoding="utf-8")
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, gpx=str(gpx_path)), path)
        loaded = load_project(path)
        assert loaded.gpx_path is not None
        assert Path(loaded.gpx_path).read_text() == "<gpx>content</gpx>"
        # Cleanup temp dir
        if loaded.temp_dir:
            shutil.rmtree(loaded.temp_dir, ignore_errors=True)

    def test_no_gpx_stays_none(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, gpx=None), path)
        loaded = load_project(path)
        assert loaded.gpx_path is None


class TestSaveLoadClipEffects:
    def test_clip_effects_round_trip(self, tmp_path):
        ce = {
            "clip_effects/fade_in_enabled": True,
            "clip_effects/fade_in_black_dur": 5.0,
        }
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, clip_effects=ce), path)
        loaded = load_project(path)
        assert loaded.clip_effects is not None
        assert loaded.clip_effects.get("clip_effects/fade_in_enabled") is True
        assert loaded.clip_effects.get("clip_effects/fade_in_black_dur") == pytest.approx(5.0)

    def test_runtime_keys_stripped_from_json(self, tmp_path):
        import json
        ce = {
            "clip_effects/title_font_path": "/tmp/font.ttf",
            "clip_effects/music_paths": json.dumps(["/tmp/music.mp3"]),
            "clip_effects/fade_in_enabled": False,
        }
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, clip_effects=ce), path)
        with zipfile.ZipFile(path) as zf:
            project_data = json.loads(zf.read("project.json"))
        saved_ce = project_data.get("clip_effects", {})
        assert "clip_effects/title_font_path" not in saved_ce
        assert "clip_effects/music_paths" not in saved_ce
        # Non-runtime key preserved
        assert "clip_effects/fade_in_enabled" in saved_ce

    def test_none_clip_effects_stays_none(self, tmp_path):
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, clip_effects=None), path)
        loaded = load_project(path)
        assert loaded.clip_effects is None


class TestShouldEmbedFont:
    def test_false_when_no_clip_effects(self):
        assert _should_embed_font(None) is False
        assert _should_embed_font({}) is False

    def test_false_when_title_disabled(self):
        ce = {"clip_effects/title_enabled": False, "clip_effects/title_text": "Hello"}
        assert _should_embed_font(ce) is False

    def test_false_when_title_text_empty(self):
        ce = {"clip_effects/title_enabled": True, "clip_effects/title_text": "   "}
        assert _should_embed_font(ce) is False

    def test_true_when_title_enabled_with_text(self):
        ce = {"clip_effects/title_enabled": True, "clip_effects/title_text": "My Hike"}
        assert _should_embed_font(ce) is True


class TestSerialiseDeserialisePhoto:
    def test_serialise_all_fields(self):
        ts = datetime(2023, 6, 1, 10, 30)
        p = PhotoMetadata(path="/a.jpg", timestamp=ts, latitude=48.0, longitude=2.0)
        d = _serialise_photo(p)
        assert d["path"] == "/a.jpg"
        assert d["latitude"] == 48.0
        assert d["longitude"] == 2.0
        assert "2023-06-01" in d["timestamp"]

    def test_serialise_none_fields(self):
        p = PhotoMetadata(path="/a.jpg", timestamp=None, latitude=None, longitude=None)
        d = _serialise_photo(p)
        assert d["timestamp"] is None
        assert d["latitude"] is None

    def test_deserialise_round_trip(self):
        ts = datetime(2023, 6, 1, 10, 30)
        p = PhotoMetadata(path="/a.jpg", timestamp=ts, latitude=48.0, longitude=2.0)
        raw = [_serialise_photo(p)]
        photos = _deserialise_photos(raw)
        assert len(photos) == 1
        assert photos[0].path == "/a.jpg"
        assert photos[0].latitude == pytest.approx(48.0)

    def test_deserialise_no_timestamp(self):
        raw = [{"path": "/b.jpg", "timestamp": None, "latitude": None, "longitude": None}]
        photos = _deserialise_photos(raw)
        assert photos[0].timestamp is None

    def test_deserialise_empty_list(self):
        assert _deserialise_photos([]) == []


class TestMusicEmbedding:
    """Round-trip tests for multi-file music embedding in .georeel archives."""

    def test_single_music_file_embedded_and_restored(self, tmp_path):
        import json
        audio = tmp_path / "track.mp3"
        audio.write_bytes(b"\xff\xfb" * 50)  # fake mp3 bytes
        ce = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_paths": json.dumps([str(audio)]),
        }
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, clip_effects=ce), path)

        # ZIP must contain the file under music/ with original name.
        with zipfile.ZipFile(path) as zf:
            namelist = zf.namelist()
            assert "music/track.mp3" in namelist
            proj = json.loads(zf.read("project.json"))
            assert proj["music_embedded"] == ["music/track.mp3"]

        loaded = load_project(path)
        assert loaded.clip_effects is not None
        restored = json.loads(loaded.clip_effects["clip_effects/music_paths"])
        assert len(restored) == 1
        assert restored[0].endswith("track.mp3")
        assert Path(restored[0]).is_file()

    def test_multiple_music_files_embedded_and_restored(self, tmp_path):
        import json
        a1 = tmp_path / "alpha.mp3"
        a2 = tmp_path / "beta.mp3"
        a1.write_bytes(b"\x00" * 100)
        a2.write_bytes(b"\x00" * 100)
        ce = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_paths": json.dumps([str(a1), str(a2)]),
        }
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, clip_effects=ce), path)

        with zipfile.ZipFile(path) as zf:
            namelist = zf.namelist()
            assert "music/alpha.mp3" in namelist
            assert "music/beta.mp3" in namelist

        loaded = load_project(path)
        restored = json.loads(loaded.clip_effects["clip_effects/music_paths"])
        names = {Path(p).name for p in restored}
        assert names == {"alpha.mp3", "beta.mp3"}

    def test_duplicate_music_filenames_get_deduped(self, tmp_path):
        import json
        # Two files from different dirs with same name.
        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        d1.mkdir(); d2.mkdir()
        f1 = d1 / "song.mp3"
        f2 = d2 / "song.mp3"
        f1.write_bytes(b"\x00" * 100)
        f2.write_bytes(b"\x00" * 100)
        ce = {
            "clip_effects/music_enabled": True,
            "clip_effects/music_paths": json.dumps([str(f1), str(f2)]),
        }
        path = str(tmp_path / "project.georeel")
        save_project(_state(tmp_path, clip_effects=ce), path)

        with zipfile.ZipFile(path) as zf:
            namelist = zf.namelist()
        music_entries = [n for n in namelist if n.startswith("music/")]
        assert len(music_entries) == 2
        assert len(set(music_entries)) == 2  # no duplicates

    def test_legacy_bool_music_embedded_loads_single_file(self, tmp_path):
        """Backward compat: music_embedded=true (bool) from old format."""
        import json
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"\x00" * 100)

        # Build a legacy project ZIP by hand.
        proj_path = str(tmp_path / "legacy.georeel")
        with zipfile.ZipFile(proj_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"version": 2}))
            zf.write(str(audio), "music/audio.mp3")
            payload = {
                "gpx_path": None,
                "match_mode": "timestamp",
                "output_path": None,
                "photos": [],
                "music_embedded": True,
                "clip_effects": {"clip_effects/music_enabled": True},
            }
            zf.writestr("project.json", json.dumps(payload))

        loaded = load_project(proj_path)
        assert loaded.clip_effects is not None
        restored = json.loads(loaded.clip_effects["clip_effects/music_paths"])
        assert len(restored) == 1
        assert Path(restored[0]).is_file()
