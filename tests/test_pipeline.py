"""Tests for pipeline.Pipeline."""

import shutil
import pytest
from pathlib import Path
from georeel.core.pipeline import Pipeline


class TestPipeline:
    def test_initial_state(self):
        p = Pipeline()
        assert p.trackpoints == []
        assert p.bounding_box is None
        assert p.match_results == []
        assert p.elevation_grid is None
        assert p.satellite_texture is None
        assert p.scene is None
        assert p.camera_keyframes is None
        assert p.rendered_frames_dir is None
        assert p.composited_frames_dir is None
        assert p.output_video_path is None

    def test_cleanup_deletes_temp_dirs(self, tmp_path):
        p = Pipeline()
        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        d1.mkdir()
        d2.mkdir()
        p._temp_dirs.append(d1)
        p._temp_dirs.append(d2)
        p.cleanup()
        assert not d1.exists()
        assert not d2.exists()

    def test_cleanup_tolerates_missing_dirs(self, tmp_path):
        p = Pipeline()
        p._temp_dirs.append(tmp_path / "nonexistent")
        p.cleanup()  # should not raise

    def test_cleanup_clears_temp_dirs_list(self, tmp_path):
        p = Pipeline()
        d = tmp_path / "d"
        d.mkdir()
        p._temp_dirs.append(d)
        p.cleanup()
        assert p._temp_dirs == []

    def test_fields_assignable(self):
        p = Pipeline()
        p.scene = "/path/to/scene.blend"
        assert p.scene == "/path/to/scene.blend"
