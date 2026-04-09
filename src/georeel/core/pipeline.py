import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .bounding_box import BoundingBox
from .camera_keyframe import CameraKeyframe
from .elevation_grid import ElevationGrid
from .match_result import MatchResult
from .satellite import SatelliteTexture
from .trackpoint import Trackpoint


@dataclass
class Pipeline:
    """Accumulates the output of each pipeline stage.

    Fields are populated in order as stages complete; downstream stages must
    check that their required inputs are not None before running.
    """

    # ------------------------------------------------------------------ #
    # Stage 1 — GPX Parser
    # ------------------------------------------------------------------ #
    trackpoints: list[Trackpoint] = field(default_factory=list)
    bounding_box: BoundingBox | None = None

    # ------------------------------------------------------------------ #
    # Stage 2 — Photo Matcher
    # ------------------------------------------------------------------ #
    match_results: list[MatchResult] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Stage 3 — DEM Fetcher
    # ------------------------------------------------------------------ #
    elevation_grid: ElevationGrid | None = None

    # ------------------------------------------------------------------ #
    # Stage 4 — Satellite Imagery Fetcher
    # ------------------------------------------------------------------ #
    satellite_texture: SatelliteTexture | None = None

    # ------------------------------------------------------------------ #
    # Stage 5 — 3D Scene Builder
    # ------------------------------------------------------------------ #
    scene: str | None = None   # path to the .blend file

    # ------------------------------------------------------------------ #
    # Stage 6 — Camera Path Generator
    # ------------------------------------------------------------------ #
    camera_keyframes: list[CameraKeyframe] | None = None

    # ------------------------------------------------------------------ #
    # Stage 7 — Frame Renderer
    # ------------------------------------------------------------------ #
    # Will hold the path to the rendered frame sequence once implemented
    rendered_frames_dir: str | None = None

    # ------------------------------------------------------------------ #
    # Stage 8 — Photo Overlay Compositor
    # ------------------------------------------------------------------ #
    # Will hold the path to the composited frame sequence once implemented
    composited_frames_dir: str | None = None

    # ------------------------------------------------------------------ #
    # Stage 9 — Video Assembler
    # ------------------------------------------------------------------ #
    output_video_path: str | None = None

    # ------------------------------------------------------------------ #
    # Temporary directory tracking
    # Large intermediate dirs (frame sequences) are registered here so
    # they can be removed as soon as the job finishes or is cancelled.
    # ------------------------------------------------------------------ #
    _temp_dirs: list[Path] = field(default_factory=list, repr=False)

    def cleanup(self) -> None:
        """Delete all registered temporary directories."""
        for d in list(self._temp_dirs):
            shutil.rmtree(d, ignore_errors=True)
        self._temp_dirs.clear()
