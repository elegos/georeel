from typing import Any
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

from georeel.core.bounding_box import BoundingBox
from georeel.core.camera_path import CameraPathError, build_camera_path
from georeel.core.dem_fetcher import DemFetchError, fetch_dem
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.frustum import frustum_margin
from georeel.core.gpx_cleaner import detect_and_repair
from georeel.core.gpx_parser import GpxParseError, parse_gpx
from georeel.core.photo_matcher import match_photos
from georeel.core.photo_store import PhotoStore
from georeel.core.pipeline import Pipeline
from georeel.core.trackpoint import Trackpoint


class KeyframeCalcWorker(QThread):
    status           = Signal(str)
    keyframes_ready  = Signal(object, object, object)  # keyframes, match_results, trackpoints
    dem_fetched      = Signal(object)                  # ElevationGrid
    error            = Signal(str)
    progress         = Signal(int, int)                # (current, total)

    def __init__(
        self,
        gpx_path: str,
        match_mode: str,
        tz_offset_hours: float,
        render_settings: dict[str, Any],
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

        # Apply the same GPX cleaning that the main pipeline uses, so that
        # (0,0) holes and implausible-speed points don't inflate the track
        # length and produce millions of keyframes.
        repair_mode    = self._settings.get("gpx/repair_mode", "none")
        max_speed_mps  = float(self._settings.get("gpx/max_speed_kmh", 300)) / 3.6
        max_gap_s      = float(self._settings.get("gpx/max_gap_s", 30.0))
        max_jump_m     = float(self._settings.get("gpx/max_jump_km", 50.0)) * 1_000
        osrm_profile   = self._settings.get("gpx/osrm_profile", "driving")
        trackpoints, _ = detect_and_repair(
            trackpoints, repair_mode,
            max_speed_mps=max_speed_mps,
            max_gap_s=max_gap_s,
            max_jump_m=max_jump_m,
            osrm_profile=osrm_profile,
        )
        # Recompute bbox from cleaned points only.
        if trackpoints:
            bbox = BoundingBox(
                min_lat=min(tp.latitude  for tp in trackpoints),
                max_lat=max(tp.latitude  for tp in trackpoints),
                min_lon=min(tp.longitude for tp in trackpoints),
                max_lon=max(tp.longitude for tp in trackpoints),
            )

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
        _distance_m   = float(self._settings.get("render/camera_height_offset", 200))
        _tilt_deg     = float(self._settings.get("render/camera_tilt_deg", 45))
        _max_view_m   = float(self._settings.get("render/frustum_margin_km", 50)) * 1_000
        margin_m = frustum_margin(
            height_m=_distance_m,
            tilt_deg=_tilt_deg,
            max_view_m=_max_view_m,
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
                grid = fetch_dem(fetch_bbox,
                                 progress_callback=lambda c, t: self.progress.emit(c, t))
            except DemFetchError as e:
                self.error.emit(f"DEM fetch error: {e}")
                return
            pipeline.elevation_grid = grid
            self.dem_fetched.emit(grid)

        # Backfill missing elevations from DEM (if GPX had no elevation data)
        filled_trackpoints = []
        for pt in pipeline.trackpoints:
            if pt.elevation is None:
                elev = pipeline.elevation_grid.elevation_at(pt.latitude, pt.longitude)
                filled_trackpoints.append(Trackpoint(
                    latitude=pt.latitude,
                    longitude=pt.longitude,
                    elevation=elev,
                    timestamp=pt.timestamp
                ))
            else:
                filled_trackpoints.append(pt)
        trackpoints = filled_trackpoints
        pipeline.trackpoints = trackpoints

        # Stage 6 — Camera path (no scene needed, just needs trackpoints + DEM)
        self.status.emit("Keyframe preview: computing camera path…")
        try:
            keyframes = build_camera_path(
                pipeline, self._settings,
                progress_callback=lambda c, t: self.progress.emit(c, t),
            )
        except CameraPathError as e:
            self.error.emit(f"Camera path error: {e}")
            return

        self.keyframes_ready.emit(keyframes, match_results, trackpoints)
