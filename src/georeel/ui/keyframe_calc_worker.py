"""
Lightweight background worker: runs stages 1–3 + camera path (stage 6)
without satellite imagery or Blender scene building.  Used by the
"Calculate keyframe" button to show which frame each photo lands on
before committing to a full pipeline run.

Signals
-------
status(str)                            — progress message
keyframes_ready(object, object, object) — (list[CameraKeyframe], list[MatchResult],
                                           list[Trackpoint])
dem_fetched(object)                    — ElevationGrid (update main-window cache)
error(str)                             — failure message
"""

import math

from PySide6.QtCore import QThread, Signal

from georeel.core.camera_path import CameraPathError, build_camera_path
from georeel.core.dem_fetcher import DemFetchError, fetch_dem
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.frustum import frustum_margin
from georeel.core.gpx_parser import GpxParseError, parse_gpx
from georeel.core.photo_matcher import match_photos
from georeel.core.photo_store import PhotoStore
from georeel.core.pipeline import Pipeline


class KeyframeCalcWorker(QThread):
    status           = Signal(str)
    keyframes_ready  = Signal(object, object, object)  # keyframes, match_results, trackpoints
    dem_fetched      = Signal(object)                  # ElevationGrid
    error            = Signal(str)

    def __init__(
        self,
        gpx_path: str,
        match_mode: str,
        tz_offset_hours: float,
        render_settings: dict,
        cached_elevation_grid: ElevationGrid | None,
    ):
        super().__init__()
        self._gpx_path    = gpx_path
        self._match_mode  = match_mode
        self._tz_offset   = tz_offset_hours
        self._settings    = render_settings
        self._cached_dem  = cached_elevation_grid

    def run(self) -> None:
        pipeline = Pipeline()

        # Stage 1 — GPX
        self.status.emit("Keyframe preview: parsing GPX…")
        try:
            trackpoints, bbox = parse_gpx(self._gpx_path)
        except GpxParseError as e:
            self.error.emit(f"GPX parse error: {e}")
            return
        pipeline.trackpoints  = trackpoints
        pipeline.bounding_box = bbox

        # Stage 2 — Photo matcher
        photos = PhotoStore.instance().all()
        match_results = []
        if photos:
            self.status.emit("Keyframe preview: matching photos…")
            try:
                match_results = match_photos(
                    photos, trackpoints, self._match_mode,
                    tz_offset_hours=self._tz_offset,
                )
                pipeline.match_results = match_results
            except Exception as e:
                self.error.emit(f"Photo match error: {e}")
                return

        # Stage 3 — DEM (required by camera path for terrain heights)
        _distance_m = float(self._settings.get("render/camera_height_offset", 200))
        _tilt_deg   = float(self._settings.get("render/camera_tilt_deg", 45))
        margin_m = frustum_margin(
            height_m=_distance_m * math.sin(math.radians(_tilt_deg)),
            tilt_deg=_tilt_deg,
        )
        fetch_bbox = bbox.expand(margin_m)

        cached = self._cached_dem
        if (
            cached is not None
            and cached.min_lat <= fetch_bbox.min_lat
            and cached.max_lat >= fetch_bbox.max_lat
            and cached.min_lon <= fetch_bbox.min_lon
            and cached.max_lon >= fetch_bbox.max_lon
        ):
            pipeline.elevation_grid = cached
            self.status.emit("Keyframe preview: DEM cached, reusing.")
        else:
            self.status.emit("Keyframe preview: fetching DEM…")
            try:
                grid = fetch_dem(fetch_bbox)
            except DemFetchError as e:
                self.error.emit(f"DEM fetch error: {e}")
                return
            pipeline.elevation_grid = grid
            self.dem_fetched.emit(grid)

        # Stage 6 — Camera path (no scene needed, just needs trackpoints + DEM)
        self.status.emit("Keyframe preview: computing camera path…")
        try:
            keyframes = build_camera_path(pipeline, self._settings)
        except CameraPathError as e:
            self.error.emit(f"Camera path error: {e}")
            return

        self.keyframes_ready.emit(keyframes, match_results, trackpoints)
