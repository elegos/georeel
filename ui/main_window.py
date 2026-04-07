from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
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
    KEY_HEIGHT_OFFSET, KEY_TILT_DEG,
)
from .video_progress_dialog import VideoProgressDialog

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

from .gpx_drop_area import GpxDropArea
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
        self.setMinimumSize(640, 560)

        self._gpx_path: str | None = None
        self._project_path: str | None = None
        self._cached_elevation_grid: ElevationGrid | None = None
        self._cached_satellite_texture: SatelliteTexture | None = None
        self._dirty = False
        self._suppress_dirty = False
        self._store = PhotoStore.instance()
        self._settings = QSettings("GeoReel", "GeoReel")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        root.addWidget(self._build_gpx_group())
        root.addWidget(self._build_photos_group())
        root.addWidget(self._build_match_group())
        root.addWidget(self._build_output_group())
        root.addLayout(self._build_action_buttons())

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready.")

        self._build_menu_bar()

        self._photo_area.photos_changed.connect(self._on_photos_changed)
        self._photo_area.photos_changed.connect(self._mark_dirty)
        self._match_group.buttonClicked.connect(self._mark_dirty)
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
        dlg = RenderSettingsDialog(self._settings, parent=self)
        dlg.exec()

    def _build_gpx_group(self) -> QGroupBox:
        group = QGroupBox("GPX Track")
        layout = QVBoxLayout(group)
        self._gpx_area = GpxDropArea(self._on_gpx_selected)
        layout.addWidget(self._gpx_area)
        return group

    def _build_photos_group(self) -> QGroupBox:
        group = QGroupBox("Photos")
        layout = QVBoxLayout(group)
        self._photo_area = PhotoListArea()
        layout.addWidget(self._photo_area)
        return group

    def _build_match_group(self) -> QGroupBox:
        group = QGroupBox("Photo matching mode")
        layout = QHBoxLayout(group)
        self._match_buttons: dict[str, QRadioButton] = {}
        self._match_group = QButtonGroup(self)
        for label, value in _MATCH_MODES:
            rb = QRadioButton(label)
            rb.setProperty("match_value", value)
            self._match_group.addButton(rb)
            self._match_buttons[value] = rb
            layout.addWidget(rb)
            if value == "timestamp":
                rb.setChecked(True)
        layout.addStretch()
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

        self._start_btn = QPushButton("Start")
        self._start_btn.setFixedHeight(36)
        self._start_btn.clicked.connect(self._start)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(36)
        self._clear_btn.clicked.connect(self._clear)

        row.addWidget(self._load_btn)
        row.addWidget(self._save_btn)
        row.addStretch()
        row.addWidget(self._start_btn)
        row.addWidget(self._clear_btn)
        return row

    # ------------------------------------------------------------------
    # Dirty tracking
    # ------------------------------------------------------------------

    def _mark_dirty(self, *_):
        if not self._suppress_dirty:
            self._dirty = True

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
        self._status.showMessage(f"GPX: {path}")

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

    def _match_mode(self) -> str:
        checked = self._match_group.checkedButton()
        return checked.property("match_value") if checked else "timestamp"

    def _start(self):
        if not self._gpx_path:
            self._status.showMessage("Please select a GPX file first.")
            return

        self._pipeline = Pipeline()

        # Stage 1 — GPX Parser
        self._status.showMessage("Parsing GPX…")
        try:
            trackpoints, bbox = parse_gpx(self._gpx_path)
        except GpxParseError as e:
            QMessageBox.critical(self, "GPX error", str(e))
            self._status.showMessage("GPX parsing failed.")
            return

        self._pipeline.trackpoints = trackpoints
        self._pipeline.bounding_box = bbox
        self._status.showMessage(
            f"GPX parsed: {len(trackpoints)} trackpoints, bounds: {bbox}"
        )

        # Stage 2 — Photo Matcher
        photos = self._store.all()
        if photos:
            self._status.showMessage("Matching photos to trackpoints…")
            results = match_photos(photos, trackpoints, self._match_mode())
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
                self._status.showMessage("Pipeline stopped: photo matching errors.")
                return

            warnings = [r for r in results if r.warning]
            self._status.showMessage(
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
            self._status.showMessage(
                f"DEM: using cached grid "
                f"({cached.rows}×{cached.cols} points)."
            )
        else:
            self._status.showMessage(
                f"Fetching DEM (SRTM, {margin_m/1000:.1f} km margin)…"
            )
            try:
                grid = fetch_dem(fetch_bbox)
            except DemFetchError as e:
                QMessageBox.critical(self, "DEM error", str(e))
                self._status.showMessage("Pipeline stopped: DEM fetch failed.")
                return
            self._pipeline.elevation_grid = grid
            self._cached_elevation_grid = grid
            self._mark_dirty()
            self._status.showMessage(
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
            self._status.showMessage(
                f"Satellite: using cached texture "
                f"({cached_sat.width}×{cached_sat.height} px)."
            )
        else:
            self._status.showMessage("Fetching satellite imagery…")
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
                self._status.showMessage("Pipeline stopped: satellite fetch failed.")
                return
            self._pipeline.satellite_texture = texture
            self._cached_satellite_texture = texture
            self._mark_dirty()
            self._status.showMessage(
                f"Satellite imagery fetched: {texture.width}×{texture.height} px."
            )

        # Stage 5 — 3D Scene Builder
        self._status.showMessage("Building 3D scene (Blender)…")
        blender_exe = self._settings.value("blender/executable_path") or None
        try:
            blend_path = build_scene(self._pipeline, blender_exe=blender_exe,
                                     settings=render_settings)
        except SceneBuildError as e:
            QMessageBox.critical(self, "Scene build error", str(e))
            self._status.showMessage("Pipeline stopped: scene build failed.")
            return
        self._pipeline.scene = blend_path
        self._status.showMessage(f"3D scene ready: {blend_path}")

        # Stage 6 — Camera Path Generator
        self._status.showMessage("Computing camera path…")
        try:
            keyframes = build_camera_path(self._pipeline, render_settings)
        except CameraPathError as e:
            QMessageBox.critical(self, "Camera path error", str(e))
            self._status.showMessage("Pipeline stopped: camera path failed.")
            return
        self._pipeline.camera_keyframes = keyframes
        fps = render_settings.get("render/fps", 30)
        duration_s = len(keyframes) / fps
        self._status.showMessage(
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
            self._status.showMessage("Pipeline stopped: rendering cancelled or failed.")
            return
        self._pipeline.rendered_frames_dir = dlg.frames_dir()
        self._status.showMessage(
            f"Frames rendered: {self._pipeline.rendered_frames_dir}"
        )

        # Stage 8 — Photo Overlay Compositor
        dlg = CompositorProgressDialog(self._pipeline, render_settings, parent=self)
        if dlg.exec() != CompositorProgressDialog.Accepted:
            self._status.showMessage("Pipeline stopped: compositing cancelled or failed.")
            return
        self._pipeline.composited_frames_dir = dlg.composited_frames_dir()
        self._status.showMessage(
            f"Compositing done: {self._pipeline.composited_frames_dir}"
        )

        # Stage 9 — Video Assembler
        output_path = self._output_selector.output_path()
        if not output_path:
            QMessageBox.warning(self, "No output path", "Please set an output video path before starting.")
            self._status.showMessage("Pipeline stopped: no output path set.")
            return
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
            self._status.showMessage("Pipeline stopped: video encoding cancelled or failed.")
            return
        self._pipeline.output_video_path = output_path
        self._status.showMessage(f"Done! Video saved: {output_path}")

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
            self._status.showMessage(f"Project saved: {path}")
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
        self._save_last_project_dir(path)
        self._project_path = path
        self._suggest_output_from_project(path)
        self._dirty = False
        self._status.showMessage(f"Project loaded: {path}")

    def _clear(self):
        self._suppress_dirty = True
        try:
            self._gpx_path = None
            self._gpx_area.clear()
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
        self._dirty = False
        self._status.showMessage("Cleared.")

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
