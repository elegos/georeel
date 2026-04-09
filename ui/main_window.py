import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import QSettings

_log = logging.getLogger(__name__)
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .blender_settings_dialog import BlenderSettingsDialog
from .compositor_progress_dialog import CompositorProgressDialog
from .render_progress_dialog import RenderProgressDialog
from .render_settings_dialog import (
    RenderSettingsDialog, get_render_settings,
    KEY_HEIGHT_OFFSET, KEY_TILT_DEG, KEY_PHOTO_TZ_OFFSET,
    KEY_IMAGERY_PROVIDER, KEY_IMAGERY_QUALITY, KEY_IMAGERY_API_KEY, KEY_IMAGERY_CUSTOM_URL,
)
from .video_progress_dialog import VideoProgressDialog
from .preview_map_dialog import PreviewMapDialog
from .preview_video_dialog import open_preview_video
from .preview_video_progress_dialog import PreviewVideoProgressDialog
from .keyframe_calc_worker import KeyframeCalcWorker
from .scene_prep_worker import ScenePrepWorker

from core.dem_fetcher import DemFetchError, fetch_dem
from core.frustum import frustum_margin
from core.elevation_grid import ElevationGrid
from core.satellite import SatelliteTexture, build_source
from core.satellite.providers import QUALITY_MAX_TILES
from core.gpx_parser import GpxParseError, parse_gpx
from core.photo_matcher import match_photos
from core.photo_store import PhotoStore
from core.pipeline import Pipeline
from core.project import ProjectState, load_project, save_project
from core.camera_path import CameraPathError, build_camera_path
from core.scene_builder import SceneBuildError, build_scene
from core.preview_map import PreviewMapError, render_preview_map

from .gpx_drop_area import GpxDropArea
from .gpx_stats_widget import GpxStatsWidget
from .output_file_selector import OutputFileSelector
from .photo_list_area import PhotoListArea

_QUALITY_ORDER = {q: i for i, q in enumerate(QUALITY_MAX_TILES)}  # standard=0, high=1, very_high=2


def _quality_rank(quality: str) -> int:
    return _QUALITY_ORDER.get(quality, 0)


_MATCH_MODES = [
    ("Timestamp", "timestamp"),
    ("GPS coordinates", "gps"),
    ("Both (GPS + timestamp)", "both"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GeoReel")
        self.setMinimumSize(640, 780)

        self._gpx_path: str | None = None
        self._project_path: str | None = None
        self._cached_elevation_grid: ElevationGrid | None = None
        self._cached_satellite_texture: SatelliteTexture | None = None
        self._dirty = False
        self._suppress_dirty = False
        self._scene_stale = True        # True → stage 5 must rerun in _start()
        self._scene_prep_worker: ScenePrepWorker | None = None
        self._keyframe_calc_worker: KeyframeCalcWorker | None = None
        self._pipeline = Pipeline()
        self._pending_preview: str = "map"  # "map" or "video"
        self._store = PhotoStore.instance()
        self._settings = QSettings("GeoReel", "GeoReel")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        root.addWidget(self._build_gpx_group())
        root.addWidget(self._build_photos_group(), stretch=1)
        root.addWidget(self._build_match_group())
        root.addWidget(self._build_output_group())
        root.addLayout(self._build_action_buttons())

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_show("Ready.")

        self._build_menu_bar()

        self._photo_area.set_tz_offset(float(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0)))
        self._photo_area.photos_changed.connect(self._on_photos_changed)
        self._photo_area.photos_changed.connect(self._mark_dirty)
        self._photo_area.photos_changed.connect(self._invalidate_scene)
        self._photo_area.calculate_keyframes_requested.connect(self._calculate_keyframes)
        self._match_group.buttonClicked.connect(self._mark_dirty)
        self._match_group.buttonClicked.connect(self._invalidate_scene)
        self._output_selector.path_changed.connect(self._mark_dirty)

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_menu_bar(self):
        options_menu = self.menuBar().addMenu("Options")
        blender_action = options_menu.addAction("Blender…")
        blender_action.triggered.connect(self._open_blender_settings)
        render_action = options_menu.addAction("Render Settings…")
        render_action.triggered.connect(self._open_render_settings)

    def _open_blender_settings(self):
        dlg = BlenderSettingsDialog(self._settings, parent=self)
        dlg.exec()

    def _open_render_settings(self):
        # Snapshot settings that affect DEM coverage and satellite texture
        _dem_keys = (KEY_HEIGHT_OFFSET, KEY_TILT_DEG)
        _sat_keys = (KEY_IMAGERY_PROVIDER, KEY_IMAGERY_QUALITY,
                     KEY_IMAGERY_API_KEY, KEY_IMAGERY_CUSTOM_URL)
        dem_before = {k: self._settings.value(k) for k in _dem_keys}
        sat_before = {k: self._settings.value(k) for k in _sat_keys}

        dlg = RenderSettingsDialog(self._settings, parent=self)
        if dlg.exec():
            if any(self._settings.value(k) != dem_before[k] for k in _dem_keys):
                self._cached_elevation_grid = None
            if any(self._settings.value(k) != sat_before[k] for k in _sat_keys):
                self._cached_satellite_texture = None
            self._invalidate_scene()

    def _calculate_keyframes(self):
        if not self._gpx_path:
            QMessageBox.warning(self, "No GPX", "Please load a GPX file first.")
            return
        if self._keyframe_calc_worker and self._keyframe_calc_worker.isRunning():
            return

        self._photo_area.set_calc_kf_running(True)
        self._status_show("Calculating keyframes…")

        render_settings = get_render_settings(self._settings)
        tz_offset = float(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0))

        worker = KeyframeCalcWorker(
            gpx_path=self._gpx_path,
            match_mode=self._match_mode(),
            tz_offset_hours=tz_offset,
            render_settings=render_settings,
            cached_elevation_grid=self._cached_elevation_grid,
        )
        worker.status.connect(self._status_show)
        worker.dem_fetched.connect(self._on_worker_dem_fetched)
        worker.keyframes_ready.connect(self._on_keyframes_ready)
        worker.error.connect(self._on_keyframe_calc_error)
        self._keyframe_calc_worker = worker
        worker.start()

    def _on_keyframes_ready(self, keyframes, match_results, trackpoints):
        self._photo_area.set_calc_kf_running(False)
        self._photo_area.update_match_statuses(match_results)
        self._photo_area.update_pipeline_info(trackpoints=trackpoints, keyframes=keyframes)
        self._status_show(
            f"Keyframes calculated: {len(keyframes)} frames total."
        )

    def _on_keyframe_calc_error(self, message: str):
        self._photo_area.set_calc_kf_running(False)
        self._status_show(f"Keyframe calculation failed: {message}", level=logging.ERROR)

    def _build_gpx_group(self) -> QGroupBox:
        group = QGroupBox("GPX Track")
        layout = QVBoxLayout(group)
        self._gpx_area = GpxDropArea(self._on_gpx_selected)
        self._gpx_stats = GpxStatsWidget()
        layout.addWidget(self._gpx_area)
        layout.addWidget(self._gpx_stats)
        return group

    def _build_photos_group(self) -> QGroupBox:
        group = QGroupBox("Photos")
        layout = QVBoxLayout(group)
        self._photo_area = PhotoListArea()
        layout.addWidget(self._photo_area)
        # Thumbnail rows are 52 px tall; keep at least 3 visible rows + header
        _row_h = 52
        _header_h = 26
        group.setMinimumHeight(_header_h + _row_h * 3 + 60)  # 60 for group title + buttons
        return group

    def _build_match_group(self) -> QGroupBox:
        group = QGroupBox("Photo matching mode")
        outer = QVBoxLayout(group)

        btn_row = QHBoxLayout()
        self._match_buttons: dict[str, QRadioButton] = {}
        self._match_group = QButtonGroup(self)
        for label, value in _MATCH_MODES:
            rb = QRadioButton(label)
            rb.setProperty("match_value", value)
            self._match_group.addButton(rb)
            self._match_buttons[value] = rb
            btn_row.addWidget(rb)
            if value == "timestamp":
                rb.setChecked(True)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        tz_row = QHBoxLayout()
        tz_label = QLabel("Camera clock timezone:")
        self._tz_offset_spin = QDoubleSpinBox()
        self._tz_offset_spin.setRange(-14.0, 14.0)
        self._tz_offset_spin.setSingleStep(0.5)
        self._tz_offset_spin.setDecimals(1)
        self._tz_offset_spin.setPrefix("UTC")
        self._tz_offset_spin.setSuffix(" h")
        self._tz_offset_spin.setToolTip(
            "EXIF timestamps are local time (no timezone). "
            "Set this to the UTC offset of the camera clock "
            "(e.g. +2.0 for UTC+2 / CEST)."
        )
        self._tz_offset_spin.setValue(
            float(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0))
        )
        self._tz_offset_spin.valueChanged.connect(self._on_tz_offset_changed)
        tz_row.addWidget(tz_label)
        tz_row.addWidget(self._tz_offset_spin)
        tz_row.addStretch()
        outer.addLayout(tz_row)

        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("Output video")
        layout = QVBoxLayout(group)
        self._output_selector = OutputFileSelector()
        layout.addWidget(self._output_selector)
        return group

    def _build_action_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._load_btn = QPushButton("Load project…")
        self._load_btn.setFixedHeight(36)
        self._load_btn.clicked.connect(self._load_project)

        self._save_btn = QPushButton("Save project…")
        self._save_btn.setFixedHeight(36)
        self._save_btn.clicked.connect(self._save_project)

        self._preview_map_btn = QPushButton("Preview Map")
        self._preview_map_btn.setFixedHeight(36)
        self._preview_map_btn.setEnabled(False)
        self._preview_map_btn.clicked.connect(self._show_preview_map)

        self._preview_video_btn = QPushButton("Preview Video")
        self._preview_video_btn.setFixedHeight(36)
        self._preview_video_btn.setEnabled(False)
        self._preview_video_btn.clicked.connect(self._show_preview_video)

        self._open_blender_btn = QPushButton("Open in Blender")
        self._open_blender_btn.setFixedHeight(36)
        self._open_blender_btn.setEnabled(False)
        self._open_blender_btn.clicked.connect(self._open_in_blender)

        self._start_btn = QPushButton("Start")
        self._start_btn.setFixedHeight(36)
        self._start_btn.clicked.connect(self._start)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(36)
        self._clear_btn.clicked.connect(self._clear)

        row.addWidget(self._load_btn)
        row.addWidget(self._save_btn)
        row.addStretch()
        row.addWidget(self._preview_map_btn)
        row.addWidget(self._preview_video_btn)
        row.addWidget(self._open_blender_btn)
        row.addWidget(self._start_btn)
        row.addWidget(self._clear_btn)
        return row

    # ------------------------------------------------------------------
    # Dirty tracking
    # ------------------------------------------------------------------

    def _status_show(self, message: str, level: int = logging.INFO) -> None:
        """Show *message* in the status bar and log it to the terminal."""
        _log.log(level, message)
        self._status.showMessage(message)

    def _mark_dirty(self, *_):
        if not self._suppress_dirty:
            self._dirty = True

    def _invalidate_scene(self, *_):
        """Mark the cached scene as stale so _start() will rebuild it."""
        self._scene_stale = True
        self._pipeline.scene = None

    def _trigger_scene_prep(self):
        """Start a background worker to run stages 1–5 for the preview map."""
        if not self._gpx_path:
            return
        # Cancel any in-flight worker
        if self._scene_prep_worker and self._scene_prep_worker.isRunning():
            self._scene_prep_worker.scene_ready.disconnect()
            self._scene_prep_worker.error.disconnect()
            self._scene_prep_worker.status.disconnect()
            self._scene_prep_worker.dem_fetched.disconnect()
            self._scene_prep_worker.satellite_fetched.disconnect()
            self._scene_prep_worker.quit()

        render_settings = get_render_settings(self._settings)
        tz_offset = float(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0))
        blender_exe = self._settings.value("blender/executable_path") or None

        worker = ScenePrepWorker(
            gpx_path=self._gpx_path,
            match_mode=self._match_mode(),
            tz_offset_hours=tz_offset,
            render_settings=render_settings,
            blender_exe=blender_exe,
            cached_elevation_grid=self._cached_elevation_grid,
            cached_satellite_texture=self._cached_satellite_texture,
            api_key=self._settings.value("imagery/api_key", ""),
            custom_url=self._settings.value("imagery/custom_url", ""),
        )
        worker.status.connect(self._status_show)
        worker.dem_fetched.connect(self._on_worker_dem_fetched)
        worker.satellite_fetched.connect(self._on_worker_satellite_fetched)
        worker.scene_ready.connect(self._on_worker_scene_ready)
        worker.error.connect(self._on_worker_error)
        self._scene_prep_worker = worker
        worker.start()

    def _on_worker_dem_fetched(self, grid):
        self._cached_elevation_grid = grid
        self._mark_dirty()

    def _on_worker_satellite_fetched(self, texture):
        self._cached_satellite_texture = texture
        self._mark_dirty()

    def _on_worker_scene_ready(self, blend_path: str, pipeline):
        self._pipeline = pipeline
        if pipeline.trackpoints:
            self._photo_area.update_pipeline_info(trackpoints=pipeline.trackpoints)
        self._scene_stale = False
        self._open_blender_btn.setEnabled(True)
        self._preview_map_btn.setEnabled(True)
        self._preview_video_btn.setEnabled(True)
        self._status_show("Scene ready.")
        if self._pending_preview == "video":
            self._show_preview_video()
        elif self._pending_preview == "blender":
            self._open_in_blender()
        else:
            self._show_preview_map()

    def _on_worker_error(self, message: str):
        self._status_show(f"Auto-build failed: {message}", level=logging.ERROR)

    def _current_state(self) -> ProjectState:
        return ProjectState(
            gpx_path=self._gpx_path,
            match_mode=self._match_mode(),
            output_path=self._output_selector.output_path(),
            photos=self._store.all(),
            elevation_grid=self._cached_elevation_grid,
            satellite_texture=self._cached_satellite_texture,
            render_settings=get_render_settings(self._settings),
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_gpx_selected(self, path: str):
        self._gpx_path = path
        self._mark_dirty()
        self._status_show(f"GPX: {path}")
        self._invalidate_scene()
        self._preview_map_btn.setEnabled(True)
        self._preview_video_btn.setEnabled(True)
        self._open_blender_btn.setEnabled(True)
        try:
            from core.gpx_parser import parse_gpx
            trackpoints, _ = parse_gpx(path)
            self._gpx_stats.update_stats(trackpoints)
        except Exception:
            self._gpx_stats.clear()

    def _on_photos_changed(self):
        ts_ok = self._store.all_have_timestamp
        gps_ok = self._store.all_have_gps

        self._match_buttons["timestamp"].setEnabled(ts_ok)
        self._match_buttons["gps"].setEnabled(gps_ok)
        self._match_buttons["both"].setEnabled(ts_ok and gps_ok)

        current = self._match_group.checkedButton()
        if current and not current.isEnabled():
            for value in ("timestamp", "gps", "both"):
                btn = self._match_buttons[value]
                if btn.isEnabled():
                    btn.setChecked(True)
                    break
            else:
                self._match_group.setExclusive(False)
                current.setChecked(False)
                self._match_group.setExclusive(True)

    def _on_tz_offset_changed(self, value: float):
        self._settings.setValue(KEY_PHOTO_TZ_OFFSET, value)
        self._photo_area.set_tz_offset(value)
        self._mark_dirty()
        self._invalidate_scene()

    def _match_mode(self) -> str:
        checked = self._match_group.checkedButton()
        return checked.property("match_value") if checked else "timestamp"

    def _show_preview_map(self):
        if not self._gpx_path:
            QMessageBox.warning(self, "No GPX", "Please load a GPX file first.")
            return

        # Scene not ready: build it first, then come back here via the worker signal
        if self._scene_stale or self._pipeline.scene is None:
            if self._scene_prep_worker and self._scene_prep_worker.isRunning():
                self._status_show("Building scene… please wait.")
                return
            self._pending_preview = "map"
            self._preview_map_btn.setEnabled(False)
            self._preview_video_btn.setEnabled(False)
            self._status_show("Building scene for preview map…")
            self._trigger_scene_prep()
            return

        blender_exe = self._settings.value("blender/executable_path") or None
        render_settings = get_render_settings(self._settings)
        res = render_settings.get("render/resolution", "1080p")
        wh = {"720p": (1280, 720), "1080p": (1920, 1080),
              "1440p": (2560, 1440), "4k": (3840, 2160)}
        width, height = wh.get(res, (1920, 1080))

        self._status_show("Rendering preview map…")
        try:
            png_path = render_preview_map(
                self._pipeline.scene,
                blender_exe=blender_exe,
                width=width,
                height=height,
            )
        except PreviewMapError as e:
            QMessageBox.critical(self, "Preview map error", str(e))
            self._status_show("Preview map rendering failed.")
            return

        self._status_show(f"Preview map ready: {png_path}")
        dlg = PreviewMapDialog(png_path, initial_dir=self._last_project_dir(), parent=self)
        dlg.exec()

    def _show_preview_video(self):
        if not self._gpx_path:
            QMessageBox.warning(self, "No GPX", "Please load a GPX file first.")
            return

        # Scene not ready: trigger auto-build first, then come back via worker signal
        if self._scene_stale or self._pipeline.scene is None:
            if self._scene_prep_worker and self._scene_prep_worker.isRunning():
                self._status_show("Building scene… please wait.")
                return
            self._pending_preview = "video"
            self._preview_map_btn.setEnabled(False)
            self._preview_video_btn.setEnabled(False)
            self._status_show("Building scene for preview video…")
            self._trigger_scene_prep()
            return

        render_settings = get_render_settings(self._settings)

        # Build camera path if not already done (fast, synchronous)
        if not self._pipeline.camera_keyframes:
            self._status_show("Computing camera path…")
            try:
                from core.camera_path import CameraPathError, build_camera_path
                self._pipeline.camera_keyframes = build_camera_path(
                    self._pipeline, render_settings
                )
            except CameraPathError as e:
                QMessageBox.critical(self, "Camera path error", str(e))
                self._status_show("Preview video failed: camera path error.")
                return

        blender_exe = self._settings.value("blender/executable_path") or None
        self._status_show("Rendering preview video…")
        dlg = PreviewVideoProgressDialog(
            self._pipeline, render_settings,
            blender_exe=blender_exe, parent=self,
        )
        if dlg.exec() != PreviewVideoProgressDialog.Accepted:
            self._status_show("Preview video cancelled or failed.")
            return

        video_path = dlg.output_path()
        if not video_path:
            return
        self._status_show(f"Preview video ready: {video_path}")
        open_preview_video(video_path, parent=self)

    def _open_in_blender(self):
        if not self._gpx_path:
            QMessageBox.warning(self, "No GPX", "Please load a GPX file first.")
            return

        if self._scene_stale or self._pipeline.scene is None:
            if self._scene_prep_worker and self._scene_prep_worker.isRunning():
                self._status_show("Building scene… please wait.")
                return
            self._pending_preview = "blender"
            self._preview_map_btn.setEnabled(False)
            self._preview_video_btn.setEnabled(False)
            self._open_blender_btn.setEnabled(False)
            self._status_show("Building scene for Blender…")
            self._trigger_scene_prep()
            return

        blender_exe = self._settings.value("blender/executable_path") or None
        from core.blender_runtime import find_blender
        exe = find_blender(blender_exe)
        if exe is None:
            QMessageBox.critical(self, "Blender not found",
                                 "Blender executable not found.\n"
                                 "Set the path via Options → Blender…")
            return

        render_settings = get_render_settings(self._settings)

        # Compute camera path if not yet done
        if not self._pipeline.camera_keyframes:
            self._status_show("Computing camera path…")
            try:
                from core.camera_path import CameraPathError, build_camera_path
                self._pipeline.camera_keyframes = build_camera_path(
                    self._pipeline, render_settings
                )
            except CameraPathError as e:
                QMessageBox.critical(self, "Camera path error", str(e))
                self._status_show("Open in Blender failed: camera path error.")
                return

        # Inject camera keyframes into a copy of the .blend
        self._status_show("Injecting camera into scene…")
        try:
            from core.open_in_blender import inject_camera_and_open
            inject_camera_and_open(
                exe,
                self._pipeline.scene,
                self._pipeline.camera_keyframes,
            )
        except Exception as e:
            QMessageBox.critical(self, "Open in Blender failed", str(e))
            self._status_show("Open in Blender failed.")
            return

        self._status_show(f"Opened in Blender: {self._pipeline.scene}")

    def _start(self):
        if not self._gpx_path:
            self._status_show("Please select a GPX file first.")
            return

        # Check for output file overwrite before doing any work
        output_path = self._output_selector.output_path()
        if not output_path:
            QMessageBox.warning(self, "No output path", "Please set an output video path before starting.")
            self._status_show("Pipeline stopped: no output path set.")
            return
        if Path(output_path).exists():
            answer = QMessageBox.question(
                self, "Overwrite file?",
                f"The file already exists:\n{output_path}\n\nOverwrite it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._status_show("Pipeline stopped: output file not overwritten.")
                return

        # If the background worker is still building the scene, wait for it
        # to finish rather than starting a duplicate Blender process.
        if self._scene_prep_worker and self._scene_prep_worker.isRunning():
            self._status_show(
                "Scene is still being built in the background — please wait."
            )
            return

        # Preserve the background-built pipeline if it is fresh (not stale).
        # Otherwise start a clean pipeline so all stages run from scratch.
        if self._scene_stale or self._pipeline.scene is None:
            self._pipeline = Pipeline()

        # Stages 1–5: skip entirely if the background worker already built
        # a fresh scene for the current inputs.
        if not self._scene_stale and self._pipeline.scene is not None:
            self._status_show(
                f"Reusing existing scene: {self._pipeline.scene}"
            )
            render_settings = get_render_settings(self._settings)
            # Jump straight to stage 6
            self._start_from_camera_path(render_settings)
            return

        # Stage 1 — GPX Parser
        self._status_show("Parsing GPX…")
        try:
            trackpoints, bbox = parse_gpx(self._gpx_path)
        except GpxParseError as e:
            QMessageBox.critical(self, "GPX error", str(e))
            self._status_show("GPX parsing failed.")
            return

        self._pipeline.trackpoints = trackpoints
        self._pipeline.bounding_box = bbox
        self._photo_area.update_pipeline_info(trackpoints=trackpoints)
        self._status_show(
            f"GPX parsed: {len(trackpoints)} trackpoints, bounds: {bbox}"
        )

        # Stage 2 — Photo Matcher
        photos = self._store.all()
        if photos:
            self._status_show("Matching photos to trackpoints…")
            tz_offset = float(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0))
            results = match_photos(photos, trackpoints, self._match_mode(),
                                   tz_offset_hours=tz_offset)
            self._pipeline.match_results = results
            self._photo_area.update_match_statuses(results)

            failed = [r for r in results if not r.ok]
            if failed:
                lines = "\n".join(
                    f"• {Path(r.photo_path).name}: {r.error}"
                    for r in failed
                )
                QMessageBox.warning(
                    self, "Photo matching failed",
                    f"{len(failed)} photo(s) could not be matched:\n\n{lines}",
                )
                self._status_show("Pipeline stopped: photo matching errors.")
                return

            warnings = [r for r in results if r.warning]
            self._status_show(
                f"Photos matched: {len(results)} ok"
                + (f", {len(warnings)} warning(s)" if warnings else "")
            )

        # Compute expanded bbox for DEM + imagery (covers camera's visible ground)
        render_settings = get_render_settings(self._settings)
        margin_m = frustum_margin(
            height_m=render_settings.get(KEY_HEIGHT_OFFSET, 200),
            tilt_deg=render_settings.get(KEY_TILT_DEG, 45),
        )
        track_bbox = self._pipeline.bounding_box
        fetch_bbox = track_bbox.expand(margin_m)

        # Stage 3 — DEM Fetcher
        cached = self._cached_elevation_grid
        if (
            cached is not None
            and cached.min_lat <= fetch_bbox.min_lat
            and cached.max_lat >= fetch_bbox.max_lat
            and cached.min_lon <= fetch_bbox.min_lon
            and cached.max_lon >= fetch_bbox.max_lon
        ):
            self._pipeline.elevation_grid = cached
            self._status_show(
                f"DEM: using cached grid "
                f"({cached.rows}×{cached.cols} points)."
            )
        else:
            self._status_show(
                f"Fetching DEM (SRTM, {margin_m/1000:.1f} km margin)…"
            )
            try:
                grid = fetch_dem(fetch_bbox)
            except DemFetchError as e:
                QMessageBox.critical(self, "DEM error", str(e))
                self._status_show("Pipeline stopped: DEM fetch failed.")
                return
            self._pipeline.elevation_grid = grid
            self._cached_elevation_grid = grid
            self._mark_dirty()
            self._status_show(
                f"DEM fetched: {grid.rows}×{grid.cols} points "
                f"({grid.rows * grid.cols:,} total)."
            )

        # Stage 4 — Satellite Imagery Fetcher
        provider_id = self._settings.value("imagery/provider", "esri_world")
        img_quality = self._settings.value("imagery/quality",  "standard")
        cached_sat = self._cached_satellite_texture
        if (
            cached_sat is not None
            and cached_sat.min_lat <= fetch_bbox.min_lat
            and cached_sat.max_lat >= fetch_bbox.max_lat
            and cached_sat.min_lon <= fetch_bbox.min_lon
            and cached_sat.max_lon >= fetch_bbox.max_lon
            and cached_sat.provider_id == provider_id
            and _quality_rank(cached_sat.quality) >= _quality_rank(img_quality)
        ):
            self._pipeline.satellite_texture = cached_sat
            self._status_show(
                f"Satellite: using cached texture "
                f"({cached_sat.width}×{cached_sat.height} px)."
            )
        else:
            self._status_show("Fetching satellite imagery…")
            try:
                source = build_source(
                    provider_id=provider_id,
                    api_key=self._settings.value("imagery/api_key", ""),
                    custom_url=self._settings.value("imagery/custom_url", ""),
                    quality=img_quality,
                )
                texture = source.fetch(fetch_bbox)
            except Exception as e:
                QMessageBox.critical(self, "Satellite imagery error", str(e))
                self._status_show("Pipeline stopped: satellite fetch failed.")
                return
            self._pipeline.satellite_texture = texture
            self._cached_satellite_texture = texture
            self._mark_dirty()
            self._status_show(
                f"Satellite imagery fetched: {texture.width}×{texture.height} px."
            )

        # Stage 5 — 3D Scene Builder
        self._status_show("Building 3D scene (Blender)…")
        blender_exe = self._settings.value("blender/executable_path") or None
        try:
            blend_path = build_scene(self._pipeline, blender_exe=blender_exe,
                                     settings=render_settings)
        except SceneBuildError as e:
            QMessageBox.critical(self, "Scene build error", str(e))
            self._status_show("Pipeline stopped: scene build failed.")
            return
        self._pipeline.scene = blend_path
        self._scene_stale = False
        self._open_blender_btn.setEnabled(True)
        self._preview_map_btn.setEnabled(True)
        self._preview_video_btn.setEnabled(True)
        self._status_show(f"3D scene ready: {blend_path}")

        self._start_from_camera_path(render_settings)

    def _start_from_camera_path(self, render_settings: dict) -> None:
        """Run stages 6–9, assuming self._pipeline already has stages 1–5."""

        # Stage 6 — Camera Path Generator
        self._status_show("Computing camera path…")
        try:
            keyframes = build_camera_path(self._pipeline, render_settings)
        except CameraPathError as e:
            QMessageBox.critical(self, "Camera path error", str(e))
            self._status_show("Pipeline stopped: camera path failed.")
            return
        self._pipeline.camera_keyframes = keyframes
        self._photo_area.update_pipeline_info(keyframes=keyframes)
        fps = render_settings.get("render/fps", 30)
        duration_s = len(keyframes) / fps
        self._status_show(
            f"Camera path: {len(keyframes)} frames "
            f"({duration_s:.1f} s at {fps} fps)"
        )

        # Stage 7 — Frame Renderer
        blender_exe = self._settings.value("blender/executable_path") or None
        dlg = RenderProgressDialog(
            self._pipeline, render_settings,
            blender_exe=blender_exe, parent=self,
        )
        if dlg.exec() != RenderProgressDialog.Accepted:
            self._status_show("Pipeline stopped: rendering cancelled or failed.")
            return
        self._pipeline.rendered_frames_dir = dlg.frames_dir()
        self._status_show(
            f"Frames rendered: {self._pipeline.rendered_frames_dir}"
        )

        # Stage 8 — Photo Overlay Compositor
        dlg = CompositorProgressDialog(self._pipeline, render_settings, parent=self)
        if dlg.exec() != CompositorProgressDialog.Accepted:
            self._status_show("Pipeline stopped: compositing cancelled or failed.")
            return
        self._pipeline.composited_frames_dir = dlg.composited_frames_dir()
        self._status_show(
            f"Compositing done: {self._pipeline.composited_frames_dir}"
        )

        # Stage 9 — Video Assembler
        output_path = self._output_selector.output_path()
        total_frames = len(self._pipeline.camera_keyframes or [])
        dlg = VideoProgressDialog(
            self._pipeline.composited_frames_dir,
            output_path,
            render_settings,
            total_frames,
            gpx_path=self._gpx_path,
            parent=self,
        )
        if dlg.exec() != VideoProgressDialog.Accepted:
            self._status_show("Pipeline stopped: video encoding cancelled or failed.")
            return
        self._pipeline.output_video_path = output_path
        self._status_show(f"Done! Video saved: {output_path}")

    # ------------------------------------------------------------------
    # Project persistence
    # ------------------------------------------------------------------

    def _last_project_dir(self) -> str:
        return self._settings.value("project/last_dir", "")

    def _save_last_project_dir(self, path: str):
        self._settings.setValue("project/last_dir", str(Path(path).parent))

    def _suggest_output_from_project(self, project_path: str):
        """Auto-fill output path from the project filename if not already set."""
        if self._output_selector.output_path():
            return
        render_settings = get_render_settings(self._settings)
        container = render_settings.get("output/container", "mkv")
        stem = Path(project_path).stem
        suggested = str(Path(project_path).parent / f"{stem}.{container}")
        self._suppress_dirty = True
        try:
            self._output_selector.set_path(suggested)
        finally:
            self._suppress_dirty = False

    def _save_to_path(self, path: str) -> bool:
        """Save current state to *path*. Returns True on success."""
        try:
            save_project(self._current_state(), path)
            self._save_last_project_dir(path)
            self._project_path = path
            self._suggest_output_from_project(path)
            self._dirty = False
            self._status_show(f"Project saved: {path}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False

    def _save_project(self) -> bool:
        """Show save dialog, then save. Returns True if actually saved."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project",
            str(Path(self._last_project_dir()) / "project.georeel"),
            "GeoReel project (*.georeel)",
        )
        if not path:
            return False
        if not path.endswith(".georeel"):
            path += ".georeel"
        return self._save_to_path(path)

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load project", self._last_project_dir(),
            "GeoReel project (*.georeel)",
        )
        if not path:
            return
        try:
            state = load_project(path)
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return

        self._suppress_dirty = True
        try:
            self._clear()
            if state.gpx_path:
                self._gpx_path = state.gpx_path
                self._gpx_area.set_file(state.gpx_path)
                try:
                    trackpoints, _ = parse_gpx(state.gpx_path)
                    self._gpx_stats.update_stats(trackpoints)
                except Exception:
                    self._gpx_stats.clear()
            self._photo_area.set_photos(state.photos)
            if state.match_mode in self._match_buttons:
                self._match_buttons[state.match_mode].setChecked(True)
            if state.output_path:
                self._output_selector.set_path(state.output_path)
        finally:
            self._suppress_dirty = False

        self._cached_elevation_grid = state.elevation_grid
        self._cached_satellite_texture = state.satellite_texture
        if state.render_settings:
            for key, value in state.render_settings.items():
                self._settings.setValue(key, value)
            tz = float(state.render_settings.get(KEY_PHOTO_TZ_OFFSET, 0.0))
            self._tz_offset_spin.blockSignals(True)
            self._tz_offset_spin.setValue(tz)
            self._tz_offset_spin.blockSignals(False)
            self._photo_area.set_tz_offset(tz)
        self._save_last_project_dir(path)
        self._project_path = path
        self._suggest_output_from_project(path)
        self._dirty = False
        self._status_show(f"Project loaded: {path}")
        if self._gpx_path:
            self._preview_map_btn.setEnabled(True)
            self._preview_video_btn.setEnabled(True)
            self._open_blender_btn.setEnabled(True)

    def _clear(self):
        self._suppress_dirty = True
        try:
            self._gpx_path = None
            self._gpx_area.clear()
            self._gpx_stats.clear()
            self._photo_area.clear()
            self._output_selector.clear()
            for btn in self._match_buttons.values():
                btn.setEnabled(True)
            self._match_buttons["timestamp"].setChecked(True)
        finally:
            self._suppress_dirty = False

        self._cached_elevation_grid = None
        self._cached_satellite_texture = None
        self._project_path = None
        self._pipeline = Pipeline()
        self._scene_stale = True
        self._open_blender_btn.setEnabled(False)
        self._preview_map_btn.setEnabled(False)
        self._preview_video_btn.setEnabled(False)
        if self._scene_prep_worker and self._scene_prep_worker.isRunning():
            self._scene_prep_worker.quit()
        if self._keyframe_calc_worker and self._keyframe_calc_worker.isRunning():
            self._keyframe_calc_worker.quit()
        self._dirty = False
        self._status_show("Cleared.")

    # ------------------------------------------------------------------
    # Close / unsaved-changes guard
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent):
        if not self._dirty:
            event.accept()
            return

        if self._project_path:
            name = Path(self._project_path).name
            answer = QMessageBox.question(
                self, "Unsaved changes",
                f'Save changes to "{name}"?',
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Save:
                if self._save_to_path(self._project_path):
                    event.accept()
                else:
                    event.ignore()
            elif answer == QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            answer = QMessageBox.question(
                self, "Unsaved changes",
                "Do you want to save the project before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Save:
                if self._save_project():
                    event.accept()
                else:
                    event.ignore()
            elif answer == QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
