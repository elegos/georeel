"""
Background worker: runs pipeline stages 1–5 (GPX → DEM → satellite → scene)
so the Preview Map button can be offered as soon as the GPX is loaded.

Signals
-------
status(str)           — progress message for the status bar
scene_ready(str)      — blend file path; stages 1–5 complete
dem_fetched           — elevation grid was fetched/reused (updates main window cache)
satellite_fetched     — satellite texture was fetched/reused
error(str)            — a stage failed; blend_path will be empty
"""

import math

from PySide6.QtCore import QThread, Signal

from core.bounding_box import BoundingBox
from core.dem_fetcher import DemFetchError, fetch_dem
from core.elevation_grid import ElevationGrid
from core.frustum import frustum_margin
from core.gpx_parser import GpxParseError, parse_gpx
from core.photo_matcher import match_photos
from core.photo_store import PhotoStore
from core.pipeline import Pipeline
from core.satellite import SatelliteTexture, build_source
from core.satellite.providers import QUALITY_MAX_TILES
from core.scene_builder import SceneBuildError, build_scene


def _quality_rank(q: str, order: dict) -> int:
    return order.get(q, 0)


class ScenePrepWorker(QThread):
    status         = Signal(str)
    scene_ready    = Signal(str, object)   # (blend_path, pipeline)
    dem_fetched    = Signal(object)        # ElevationGrid
    satellite_fetched = Signal(object)     # SatelliteTexture
    error          = Signal(str)

    def __init__(
        self,
        gpx_path: str,
        match_mode: str,
        tz_offset_hours: float,
        render_settings: dict,
        blender_exe: str | None,
        cached_elevation_grid: ElevationGrid | None,
        cached_satellite_texture: SatelliteTexture | None,
        api_key: str,
        custom_url: str,
    ):
        super().__init__()
        self._gpx_path              = gpx_path
        self._match_mode            = match_mode
        self._tz_offset             = tz_offset_hours
        self._settings              = render_settings
        self._blender_exe           = blender_exe
        self._cached_dem            = cached_elevation_grid
        self._cached_sat            = cached_satellite_texture
        self._api_key               = api_key
        self._custom_url            = custom_url
        self._quality_order         = {q: i for i, q in enumerate(QUALITY_MAX_TILES)}

    def run(self) -> None:
        pipeline = Pipeline()

        # Stage 1 — GPX
        self.status.emit("Auto-build: parsing GPX…")
        try:
            trackpoints, bbox = parse_gpx(self._gpx_path)
        except GpxParseError as e:
            self.error.emit(f"GPX parse error: {e}")
            return
        pipeline.trackpoints  = trackpoints
        pipeline.bounding_box = bbox

        # Stage 2 — Photo Matcher (best-effort; mismatches don't stop preview)
        photos = PhotoStore.instance().all()
        if photos:
            self.status.emit("Auto-build: matching photos…")
            try:
                results = match_photos(photos, trackpoints, self._match_mode,
                                       tz_offset_hours=self._tz_offset)
                pipeline.match_results = results
            except Exception:
                pass   # preview still works without pins

        # Expand bbox for DEM + imagery
        margin_m = frustum_margin(
            height_m=float(self._settings.get("render/camera_height_offset", 200)),
            tilt_deg=float(self._settings.get("render/camera_tilt_deg", 45)),
        )
        fetch_bbox = bbox.expand(margin_m)

        # Stage 3 — DEM
        cached = self._cached_dem
        if (
            cached is not None
            and cached.min_lat <= fetch_bbox.min_lat
            and cached.max_lat >= fetch_bbox.max_lat
            and cached.min_lon <= fetch_bbox.min_lon
            and cached.max_lon >= fetch_bbox.max_lon
        ):
            pipeline.elevation_grid = cached
            self.status.emit("Auto-build: DEM cached, reusing.")
        else:
            self.status.emit("Auto-build: fetching DEM…")
            try:
                grid = fetch_dem(fetch_bbox)
            except DemFetchError as e:
                self.error.emit(f"DEM fetch error: {e}")
                return
            pipeline.elevation_grid = grid
            self.dem_fetched.emit(grid)

        # Stage 4 — Satellite imagery
        provider_id = self._settings.get("imagery/provider", "esri_world")
        img_quality = self._settings.get("imagery/quality",  "standard")
        cached_sat  = self._cached_sat
        if (
            cached_sat is not None
            and cached_sat.min_lat <= fetch_bbox.min_lat
            and cached_sat.max_lat >= fetch_bbox.max_lat
            and cached_sat.min_lon <= fetch_bbox.min_lon
            and cached_sat.max_lon >= fetch_bbox.max_lon
            and cached_sat.provider_id == provider_id
            and _quality_rank(cached_sat.quality, self._quality_order)
                >= _quality_rank(img_quality, self._quality_order)
        ):
            pipeline.satellite_texture = cached_sat
            self.status.emit("Auto-build: satellite texture cached, reusing.")
        else:
            self.status.emit("Auto-build: fetching satellite imagery…")
            try:
                source = build_source(
                    provider_id=provider_id,
                    api_key=self._api_key,
                    custom_url=self._custom_url,
                    quality=img_quality,
                )
                texture = source.fetch(fetch_bbox)
            except Exception as e:
                self.error.emit(f"Satellite fetch error: {e}")
                return
            pipeline.satellite_texture = texture
            self.satellite_fetched.emit(texture)

        # Stage 5 — 3D Scene Builder
        self.status.emit("Auto-build: building 3D scene (Blender)…")
        try:
            blend_path = build_scene(pipeline, blender_exe=self._blender_exe,
                                     settings=self._settings)
        except SceneBuildError as e:
            self.error.emit(f"Scene build error: {e}")
            return

        pipeline.scene = blend_path
        self.scene_ready.emit(blend_path, pipeline)
