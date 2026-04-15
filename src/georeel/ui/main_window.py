# pyright: reportUninitializedInstanceVariable=false
from typing import Any
import logging
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from georeel.core.bounding_box import BoundingBox
from georeel.core.camera_path import CameraPathError, build_camera_path
from georeel.core.dem_fetcher import DemFetchError, fetch_dem
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.frustum import frustum_margin
from georeel.core.exif_reader import read_photo_metadata
from georeel.core.gpx_cleaner import REPAIR_LINEAR, REPAIR_NONE, detect_and_repair
from georeel.core.gpx_parser import GpxParseError, parse_gpx
from georeel.core.gpx_stats import compute_stats
from georeel.core.photo_matcher import match_photos
from georeel.core.photo_store import PhotoStore
from georeel.core.pipeline import Pipeline
from georeel.core.preview_map import PreviewMapError, render_preview_map
from georeel.core.project import ProjectState, autosave_tilde, load_project, save_project
from georeel.core.satellite import SatelliteTexture, build_source
from georeel.core.satellite.providers import QUALITY_ZOOM
from georeel.core.pipeline_memory import log_pipeline_memory
from georeel.core import temp_manager

from .blender_settings_dialog import BlenderSettingsDialog
from .clip_effects_widget import ClipEffectsWidget
from .compositor_progress_dialog import CompositorProgressDialog
from .gpx_drop_area import GpxDropArea
from .gpx_stats_widget import GpxStatsWidget
from .keyframe_calc_worker import KeyframeCalcWorker
from .output_file_selector import OutputFileSelector
from .photo_list_area import PhotoListArea
from .preview_map_dialog import PreviewMapDialog
from .preview_video_dialog import open_preview_video
from .preview_video_progress_dialog import PreviewVideoProgressDialog
from .render_progress_dialog import RenderProgressDialog
from .render_settings_dialog import (
    KEY_CACHE_BASE_DIR,
    KEY_CACHE_USE_CUSTOM_DIR,
    KEY_CAMERA_SPEED,
    KEY_FRUSTUM_MARGIN_KM,
    KEY_GPX_MAX_GAP_S,
    KEY_GPX_MAX_JUMP_KM,
    KEY_GPX_MAX_SPEED_KMH,
    KEY_GPX_OSRM_PROFILE,
    KEY_GPX_REPAIR_MODE,
    KEY_HEIGHT_OFFSET,
    KEY_MARKER_SHIFTING_PIN,
    KEY_RIBBON_COLOR_MODE,
    KEY_RIBBON_SELF_LIT,
    KEY_IMAGERY_API_KEY,
    KEY_IMAGERY_CUSTOM_URL,
    KEY_IMAGERY_PROVIDER,
    KEY_IMAGERY_QUALITY,
    KEY_PHOTO_TZ_OFFSET,
    KEY_TILT_DEG,
    RenderSettingsDialog,
    get_render_settings,
)
from .scene_build_dialog import SceneBuildDialog
from .scene_prep_worker import ScenePrepWorker
from .video_progress_dialog import VideoProgressDialog

_log = logging.getLogger(__name__)

_QUALITY_ORDER = {
    q: i for i, q in enumerate(QUALITY_ZOOM)
}  # standard=0, high=1, very_high=2


def _quality_rank(quality: str) -> int:
    return _QUALITY_ORDER.get(quality, 0)


_MATCH_MODES = [
    ("Timestamp", "timestamp"),
    ("GPS coordinates", "gps"),
    ("Both (GPS + timestamp)", "both"),
]

# Flythrough speed presets (label, m/s).  80 m/s ≈ right for a 25 km hike
# producing ~5 min video at 30 fps.  Cycling and driving cover more ground so
# a higher speed keeps the video a sensible length.
_SPEED_PRESETS = [
    ("Hiking",   80.0),
    ("Cycling", 120.0),
    ("Driving", 320.0),
]


class _SaveWorker(QObject):
    """Runs save_project in a background thread."""

    finished = Signal()
    failed = Signal(str)

    def __init__(self, state, path: str):
        super().__init__()
        self._state = state
        self._path = path

    def run(self):
        try:
            save_project(self._state, self._path)
            self.finished.emit()
        except Exception as e:
            self.failed.emit(str(e))


class _LoadResult:
    """All pre-computed data produced by _LoadWorker so the main thread only does UI."""
    __slots__ = ("state", "gpx_stats", "gpx_failed", "exif_cache")

    def __init__(self, state, gpx_stats, gpx_failed, exif_cache):
        self.state = state
        self.gpx_stats = gpx_stats      # GpxStats | None
        self.gpx_failed = gpx_failed
        self.exif_cache = exif_cache


class _LoadWorker(QObject):
    """Loads a project and pre-computes GPX + EXIF data in a background thread."""

    finished = Signal(object)   # _LoadResult
    failed   = Signal(str)
    progress = Signal(str)      # status-bar message

    def __init__(
        self,
        path: str,
        repair_mode: str,
        max_speed_mps: float,
        max_gap_s: float,
        max_jump_m: float,
    ):
        super().__init__()
        self._path = path
        self._repair_mode = repair_mode
        self._max_speed_mps = max_speed_mps
        self._max_gap_s = max_gap_s
        self._max_jump_m = max_jump_m

    def run(self):
        name = Path(self._path).name
        try:
            self.progress.emit(f"Loading project: {name}…")
            state = load_project(self._path)

            gpx_stats = None
            gpx_failed = False
            if state.gpx_path:
                self.progress.emit(f"Loading project: {name} — parsing GPX…")
                try:
                    trackpoints, _ = parse_gpx(state.gpx_path)
                    trackpoints, _ = detect_and_repair(
                        trackpoints,
                        self._repair_mode,
                        max_speed_mps=self._max_speed_mps,
                        max_gap_s=self._max_gap_s,
                        max_jump_m=self._max_jump_m,
                    )
                    gpx_stats = compute_stats(trackpoints)
                except Exception:
                    gpx_failed = True

            exif_cache: dict[str, Any] = {}
            photos = state.photos
            n = len(photos)
            for i, photo in enumerate(photos):
                if i % 10 == 0:
                    self.progress.emit(
                        f"Loading project: {name} — reading photo metadata ({i}/{n})…"
                    )
                try:
                    exif_cache[photo.path] = read_photo_metadata(photo.path)
                except Exception:
                    pass

            self.finished.emit(_LoadResult(state, gpx_stats, gpx_failed, exif_cache))
        except Exception as e:
            self.failed.emit(str(e))


class _InjectWorker(QObject):
    """Runs inject_camera_and_open headlessly in a background thread."""

    finished = Signal()
    failed   = Signal(str)

    def __init__(self, exe: str, blend_path: str, keyframes, resolution: str, fps: int = 30):
        super().__init__()
        self._exe        = exe
        self._blend_path = blend_path
        self._keyframes  = keyframes
        self._resolution = resolution
        self._fps        = fps

    def run(self):
        from georeel.core.open_in_blender import OpenInBlenderError, inject_camera_and_open
        try:
            inject_camera_and_open(
                self._exe, self._blend_path, self._keyframes, self._resolution,
                fps=self._fps,
            )
            self.finished.emit()
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GeoReel")
        self.setMinimumSize(640, 780)

        self._gpx_path: str | None = None
        self._project_path: str | None = None
        self._project_temp_dir: Path | None = None
        self._cached_elevation_grid: ElevationGrid | None = None
        self._cached_satellite_texture: SatelliteTexture | None = None
        self._dirty = False
        self._suppress_dirty = False
        self._tilde_fresh = False           # True when path~ is ready to rename on save
        self._autosave_thread: threading.Thread | None = None
        self._scene_stale = True  # True → stage 5 must rerun in _start()
        self._scene_prep_worker: ScenePrepWorker | None = None
        self._keyframe_calc_worker: KeyframeCalcWorker | None = None
        self._pipeline = Pipeline()
        self._pending_preview: str = "map"  # "map" or "video"
        self._pending_close: bool = False
        self._save_thread: QThread | None = None
        self._load_thread: QThread | None = None
        self._load_worker: _LoadWorker | None = None
        self._pending_load_path: str = ""
        self._pending_save_path: str = ""
        self._inject_thread: QThread | None = None
        self._inject_worker: _InjectWorker | None = None
        self._track_length_m: float | None = None
        self._store = PhotoStore.instance()
        self._settings = QSettings("GeoReel", "GeoReel")
        self._restore_window_geometry()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.setCentralWidget(central)

        tabs = QTabWidget()
        central_layout.addWidget(tabs, stretch=1)

        main_tab = QWidget()
        root = QVBoxLayout(main_tab)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)
        root.addWidget(self._build_gpx_group())
        root.addWidget(self._build_photos_group(), stretch=1)
        root.addWidget(self._build_match_group())
        root.addWidget(self._build_output_group())

        self._clip_effects_widget = ClipEffectsWidget(self._settings)

        tabs.addTab(main_tab, "Main")
        tabs.addTab(self._build_ribbon_tab(), "Ribbon")
        tabs.addTab(self._clip_effects_widget.fade_tab_widget(), "Fade")
        tabs.addTab(self._clip_effects_widget.title_tab_widget(), "Title")
        tabs.addTab(self._clip_effects_widget.music_tab_widget(), "Music")

        btn_container = QWidget()
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(16, 8, 16, 8)
        btn_layout.addLayout(self._build_action_buttons())
        central_layout.addWidget(btn_container)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self._fetch_progress_bar = QProgressBar()
        self._fetch_progress_bar.setRange(0, 0)  # indeterminate initially
        self._fetch_progress_bar.setMaximumWidth(200)
        self._fetch_progress_bar.hide()
        self._status.addPermanentWidget(self._fetch_progress_bar)

        self._save_progress_bar = QProgressBar()
        self._save_progress_bar.setRange(0, 0)  # indeterminate
        self._save_progress_bar.setMaximumWidth(160)
        self._save_progress_bar.setTextVisible(False)
        self._save_progress_bar.hide()
        self._status.addPermanentWidget(self._save_progress_bar)

        try:
            from importlib.metadata import version as _pkg_version

            _version = _pkg_version("georeel")
        except Exception:
            _version = "unknown"
        self._status.addPermanentWidget(QLabel(f"v{_version}"))

        self._status_show("Ready.")

        self._build_menu_bar()

        self._photo_area.set_tz_offset(
            float(str(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0)))
        )
        self._photo_area.photos_changed.connect(self._on_photos_changed)
        self._photo_area.photos_changed.connect(self._mark_dirty)
        self._photo_area.photos_changed.connect(self._invalidate_scene)
        self._photo_area.calculate_keyframes_requested.connect(
            self._calculate_keyframes
        )
        self._match_group.buttonClicked.connect(self._mark_dirty)
        self._match_group.buttonClicked.connect(self._invalidate_scene)
        self._output_selector.path_changed.connect(self._mark_dirty)

        self._apply_temp_dir_setting()
        self._cleanup_stale_temp()

    # ------------------------------------------------------------------
    # Temp-dir management
    # ------------------------------------------------------------------

    def _apply_temp_dir_setting(self) -> None:
        """Configure temp_manager from the current cache/dir settings."""
        use_custom = self._settings.value(KEY_CACHE_USE_CUSTOM_DIR, False)
        use_custom = bool(use_custom) and use_custom != "false"
        base_dir_str = str(self._settings.value(KEY_CACHE_BASE_DIR, "")).strip()
        if use_custom and base_dir_str:
            temp_manager.set_base_dir(Path(base_dir_str))
        else:
            temp_manager.set_base_dir(None)

    def _cleanup_stale_temp(self) -> None:
        """Remove temp dirs/files left over from previous crashed sessions."""
        n = temp_manager.cleanup_stale()
        if n:
            _log.info("[startup] Removed %d stale GeoReel temp entries", n)

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_menu_bar(self):
        # File menu
        file_menu = self.menuBar().addMenu("File")

        open_action = file_menu.addAction("Open…")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._load_project)

        self._recent_menu = file_menu.addMenu("Open Recent")
        self._recent_menu.aboutToShow.connect(self._populate_recent_menu)

        file_menu.addSeparator()

        save_action = file_menu.addAction("Save")
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save)

        save_as_action = file_menu.addAction("Save As…")
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_project)

        # Options menu
        options_menu = self.menuBar().addMenu("Options")
        blender_action = options_menu.addAction("Blender…")
        blender_action.triggered.connect(self._open_blender_settings)
        render_action = options_menu.addAction("Pipeline Settings…")
        render_action.triggered.connect(self._open_render_settings)

    def _open_blender_settings(self):
        dlg = BlenderSettingsDialog(self._settings, parent=self)
        dlg.exec()

    def _open_render_settings(self):
        # Snapshot settings that affect DEM coverage and satellite texture
        _dem_keys = (KEY_HEIGHT_OFFSET, KEY_TILT_DEG)
        _sat_keys = (
            KEY_IMAGERY_PROVIDER,
            KEY_IMAGERY_QUALITY,
            KEY_IMAGERY_API_KEY,
            KEY_IMAGERY_CUSTOM_URL,
        )
        dem_before = {k: self._settings.value(k) for k in _dem_keys}
        sat_before = {k: self._settings.value(k) for k in _sat_keys}

        dlg = RenderSettingsDialog(self._settings, parent=self)
        if dlg.exec():
            if any(self._settings.value(k) != dem_before[k] for k in _dem_keys):
                self._cached_elevation_grid = None
            if any(self._settings.value(k) != sat_before[k] for k in _sat_keys):
                self._cached_satellite_texture = None
            self._apply_temp_dir_setting()
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
        tz_offset = float(str(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0)))

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
        worker.progress.connect(self._on_worker_fetch_progress)
        self._keyframe_calc_worker = worker
        worker.start()

    def _on_keyframes_ready(self, keyframes, match_results, trackpoints):
        self._fetch_progress_bar.hide()
        self._photo_area.set_calc_kf_running(False)
        self._photo_area.update_match_statuses(match_results)
        self._photo_area.update_pipeline_info(
            trackpoints=trackpoints, keyframes=keyframes
        )
        self._status_show(f"Keyframes calculated: {len(keyframes)} frames total.")
        # Cache cleaned trackpoints so ScenePrepWorker can reuse them without
        # re-parsing and re-cleaning the GPX file.
        if trackpoints:
            self._pipeline.trackpoints = list(trackpoints)

    def _on_keyframe_calc_error(self, message: str):
        self._fetch_progress_bar.hide()
        self._photo_area.set_calc_kf_running(False)
        self._status_show(
            f"Keyframe calculation failed: {message}", level=logging.ERROR
        )

    def _build_gpx_group(self) -> QGroupBox:
        group = QGroupBox("GPX Track")
        layout = QVBoxLayout(group)
        self._gpx_area = GpxDropArea(self._on_gpx_selected)
        self._gpx_stats = GpxStatsWidget()
        layout.addWidget(self._gpx_area)
        layout.addWidget(self._gpx_stats)
        layout.addWidget(self._build_gpx_repair_row())
        return group

    def _build_gpx_repair_row(self) -> QWidget:
        """Compact per-project GPX hole-repair controls."""
        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)

        # ── Mode combo ────────────────────────────────────────────────
        self._gpx_repair_combo = QComboBox()
        self._gpx_repair_combo.addItem("No hole repair", "none")
        self._gpx_repair_combo.addItem("Linear interpolation", "linear")
        self._gpx_repair_combo.addItem("Street interpolation (OSRM)", "street")
        saved_mode = str(self._settings.value(KEY_GPX_REPAIR_MODE, "none"))
        # Remap legacy "ground" value to "linear".
        if saved_mode == "ground":
            saved_mode = "linear"
        idx = self._gpx_repair_combo.findData(saved_mode)
        if idx >= 0:
            self._gpx_repair_combo.setCurrentIndex(idx)
        self._gpx_repair_combo.setToolTip(
            "Linear: fills gaps with a straight line between the two endpoints.\n"
            "Street: routes via the OSRM public API (router.project-osrm.org);\n"
            "falls back to linear when the route is unavailable."
        )
        form.addRow("Hole repair:", self._gpx_repair_combo)

        # ── OSRM profile combo (shown only when mode == street) ───────
        self._gpx_osrm_profile_combo = QComboBox()
        self._gpx_osrm_profile_combo.addItem("Driving", "driving")
        self._gpx_osrm_profile_combo.addItem("Cycling", "cycling")
        self._gpx_osrm_profile_combo.addItem("Walking", "walking")
        saved_profile = str(self._settings.value(KEY_GPX_OSRM_PROFILE, "driving"))
        pidx = self._gpx_osrm_profile_combo.findData(saved_profile)
        if pidx >= 0:
            self._gpx_osrm_profile_combo.setCurrentIndex(pidx)
        self._gpx_osrm_profile_combo.setToolTip(
            "Routing profile sent to the OSRM API."
        )
        self._gpx_osrm_profile_widget = QWidget()
        profile_row = QHBoxLayout(self._gpx_osrm_profile_widget)
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.addWidget(self._gpx_osrm_profile_combo)
        profile_row.addStretch()
        form.addRow("OSRM profile:", self._gpx_osrm_profile_widget)

        # ── Threshold row (hidden when mode == none) ──────────────────
        thresh_row = QHBoxLayout()

        self._gpx_speed_spin = QSpinBox()
        self._gpx_speed_spin.setRange(10, 5000)
        self._gpx_speed_spin.setSingleStep(10)
        self._gpx_speed_spin.setSuffix(" km/h")
        self._gpx_speed_spin.setValue(
            int(str(self._settings.value(KEY_GPX_MAX_SPEED_KMH, 300)))
        )
        self._gpx_speed_spin.setToolTip(
            "Points implying a speed above this are treated as bad GPS readings "
            "and removed (requires timestamps)."
        )

        self._gpx_gap_spin = QDoubleSpinBox()
        self._gpx_gap_spin.setRange(1.0, 3600.0)
        self._gpx_gap_spin.setSingleStep(5.0)
        self._gpx_gap_spin.setDecimals(1)
        self._gpx_gap_spin.setSuffix(" s gap")
        self._gpx_gap_spin.setValue(
            float(str(self._settings.value(KEY_GPX_MAX_GAP_S, 30.0)))
        )
        self._gpx_gap_spin.setToolTip(
            "Time gaps longer than this between two valid points are filled "
            "with synthetic track points (requires timestamps)."
        )

        thresh_row.addWidget(QLabel("Max speed:"))
        thresh_row.addWidget(self._gpx_speed_spin)
        thresh_row.addSpacing(12)
        thresh_row.addWidget(QLabel("Fill above:"))
        thresh_row.addWidget(self._gpx_gap_spin)
        thresh_row.addStretch()

        self._gpx_thresh_widget = QWidget()
        self._gpx_thresh_widget.setLayout(thresh_row)
        form.addRow("", self._gpx_thresh_widget)

        # ── Shifting pin checkbox (visible only when mode != none) ────
        self._shifting_pin_check = QCheckBox("Shifting pin")
        self._shifting_pin_check.setToolTip(
            "When checked, the track marker gradually shifts from its chosen color\n"
            "to its complementary color over reconstructed (filled-gap) segments,\n"
            "then fades back once the recorded track resumes."
        )
        saved_shifting = self._settings.value(KEY_MARKER_SHIFTING_PIN, False)
        self._shifting_pin_check.setChecked(
            bool(saved_shifting) and saved_shifting != "false"
        )
        self._shifting_pin_widget = QWidget()
        shifting_row = QHBoxLayout(self._shifting_pin_widget)
        shifting_row.setContentsMargins(0, 0, 0, 0)
        shifting_row.addWidget(self._shifting_pin_check)
        shifting_row.addStretch()
        form.addRow("", self._shifting_pin_widget)

        # Show/hide thresholds, profile, and shifting-pin based on mode.
        def _update_thresh_visibility():
            mode = self._gpx_repair_combo.currentData()
            self._gpx_thresh_widget.setVisible(mode != "none")
            self._gpx_osrm_profile_widget.setVisible(mode == "street")
            self._shifting_pin_widget.setVisible(mode != "none")
            self._settings.setValue(KEY_GPX_REPAIR_MODE, mode)

        _update_thresh_visibility()
        self._gpx_repair_combo.currentIndexChanged.connect(_update_thresh_visibility)
        self._gpx_speed_spin.valueChanged.connect(
            lambda v: self._settings.setValue(KEY_GPX_MAX_SPEED_KMH, v)
        )
        self._gpx_gap_spin.valueChanged.connect(
            lambda v: self._settings.setValue(KEY_GPX_MAX_GAP_S, v)
        )
        self._gpx_osrm_profile_combo.currentIndexChanged.connect(
            lambda: self._settings.setValue(
                KEY_GPX_OSRM_PROFILE, self._gpx_osrm_profile_combo.currentData()
            )
        )
        self._shifting_pin_check.toggled.connect(
            lambda v: self._settings.setValue(KEY_MARKER_SHIFTING_PIN, v)
        )

        return container

    def _reload_gpx_repair_controls(self) -> None:
        """Sync GPX repair widgets from QSettings (called after project load)."""
        mode = str(self._settings.value(KEY_GPX_REPAIR_MODE, "none"))
        if mode == "ground":
            mode = "linear"  # remap legacy value
        idx = self._gpx_repair_combo.findData(mode)
        self._gpx_repair_combo.blockSignals(True)
        if idx >= 0:
            self._gpx_repair_combo.setCurrentIndex(idx)
        self._gpx_repair_combo.blockSignals(False)
        self._gpx_thresh_widget.setVisible(mode != "none")
        self._gpx_osrm_profile_widget.setVisible(mode == "street")
        self._shifting_pin_widget.setVisible(mode != "none")

        profile = str(self._settings.value(KEY_GPX_OSRM_PROFILE, "driving"))
        pidx = self._gpx_osrm_profile_combo.findData(profile)
        self._gpx_osrm_profile_combo.blockSignals(True)
        if pidx >= 0:
            self._gpx_osrm_profile_combo.setCurrentIndex(pidx)
        self._gpx_osrm_profile_combo.blockSignals(False)

        self._gpx_speed_spin.blockSignals(True)
        self._gpx_speed_spin.setValue(int(str(self._settings.value(KEY_GPX_MAX_SPEED_KMH, 300))))
        self._gpx_speed_spin.blockSignals(False)

        self._gpx_gap_spin.blockSignals(True)
        self._gpx_gap_spin.setValue(float(str(self._settings.value(KEY_GPX_MAX_GAP_S, 30.0))))
        self._gpx_gap_spin.blockSignals(False)

        saved_shifting = self._settings.value(KEY_MARKER_SHIFTING_PIN, False)
        self._shifting_pin_check.blockSignals(True)
        self._shifting_pin_check.setChecked(bool(saved_shifting) and saved_shifting != "false")
        self._shifting_pin_check.blockSignals(False)

    def _reload_speed_control(self) -> None:
        """Sync flythrough speed widgets from QSettings (called after project load)."""
        speed = float(str(self._settings.value(KEY_CAMERA_SPEED, 80.0)))
        self._speed_spin.blockSignals(True)
        self._speed_spin.setValue(speed)
        self._speed_spin.blockSignals(False)
        self._speed_preset_combo.blockSignals(True)
        self._speed_preset_combo.setCurrentIndex(self._speed_preset_index(speed))
        self._speed_preset_combo.blockSignals(False)

    def _build_photos_group(self) -> QGroupBox:
        group = QGroupBox("Photos")
        layout = QVBoxLayout(group)
        self._photo_area = PhotoListArea()
        layout.addWidget(self._photo_area)
        # Thumbnail rows are 52 px tall; keep at least 3 visible rows + header
        _row_h = 52
        _header_h = 26
        group.setMinimumHeight(
            _header_h + _row_h * 3 + 60
        )  # 60 for group title + buttons
        return group

    def _build_match_group(self) -> QGroupBox:
        group = QGroupBox("Photo matching mode")
        outer = QVBoxLayout(group)

        _match_tooltips = {
            "timestamp": (
                "Match photos to track points by comparing EXIF timestamps against\n"
                "GPX track timestamps. Requires both the GPX and the photos to have\n"
                "accurate timestamps and a correctly set camera timezone offset."
            ),
            "gps": (
                "Match photos to track points by nearest geographic distance using\n"
                "the GPS coordinates stored in the photo's EXIF data.\n"
                "Does not require timestamps — works even when clocks are wrong."
            ),
            "both": (
                "Use GPS coordinates as the primary match when EXIF GPS data is\n"
                "available; fall back to timestamp matching for photos that have\n"
                "no GPS coordinates. Warns when the two methods disagree by more\n"
                "than a configurable threshold. Recommended default."
            ),
        }
        btn_row = QHBoxLayout()
        self._match_buttons: dict[str, QRadioButton] = {}
        self._match_group = QButtonGroup(self)
        for label, value in _MATCH_MODES:
            rb = QRadioButton(label)
            rb.setProperty("match_value", value)
            rb.setToolTip(_match_tooltips.get(value, ""))
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
            float(str(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0)))
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

        # ── Flythrough speed ────────────────────────────────────────────
        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)

        self._speed_preset_combo = QComboBox()
        self._speed_preset_combo.setToolTip(
            "Quick-select a typical flythrough speed for the activity type.\n"
            "Hiking ~80 m/s · Cycling ~120 m/s · Driving ~320 m/s.\n"
            "Choose 'Custom' to type an exact value in the field to the right."
        )
        for label, _ in _SPEED_PRESETS:
            self._speed_preset_combo.addItem(label)
        self._speed_preset_combo.addItem("Custom")

        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(10.0, 1000.0)
        self._speed_spin.setSingleStep(10.0)
        self._speed_spin.setDecimals(0)
        self._speed_spin.setSuffix(" m/s")
        self._speed_spin.setToolTip(
            "How fast the camera flies through the 3D scene.\n"
            "Higher = shorter video.\n"
            "Hiking ~80 m/s · Cycling ~120 m/s · Driving ~320 m/s"
        )

        saved_speed = float(str(self._settings.value(KEY_CAMERA_SPEED, 80.0)))
        self._speed_spin.setValue(saved_speed)
        self._speed_preset_combo.setCurrentIndex(self._speed_preset_index(saved_speed))

        self._duration_label = QLabel()
        self._duration_label.setStyleSheet("color: gray;")

        def _on_preset_changed(idx):
            presets = [v for _, v in _SPEED_PRESETS]
            if idx < len(presets):
                self._speed_spin.blockSignals(True)
                self._speed_spin.setValue(presets[idx])
                self._speed_spin.blockSignals(False)
                self._settings.setValue(KEY_CAMERA_SPEED, presets[idx])
                self._update_duration_label()
                self._invalidate_scene()
                self._mark_dirty()

        def _on_speed_changed(value):
            self._speed_preset_combo.blockSignals(True)
            self._speed_preset_combo.setCurrentIndex(self._speed_preset_index(value))
            self._speed_preset_combo.blockSignals(False)
            self._settings.setValue(KEY_CAMERA_SPEED, value)
            self._update_duration_label()
            self._invalidate_scene()
            self._mark_dirty()

        self._speed_preset_combo.currentIndexChanged.connect(_on_preset_changed)
        self._speed_spin.valueChanged.connect(_on_speed_changed)

        speed_row.addWidget(QLabel("Flythrough speed:"))
        speed_row.addWidget(self._speed_preset_combo)
        speed_row.addWidget(self._speed_spin)
        speed_row.addWidget(self._duration_label)
        speed_row.addStretch()
        layout.addLayout(speed_row)

        return group

    def _speed_preset_index(self, value: float) -> int:
        """Return the combo index matching *value*, or the 'Custom' index."""
        for i, (_, v) in enumerate(_SPEED_PRESETS):
            if abs(value - v) < 0.5:
                return i
        return len(_SPEED_PRESETS)   # "Custom"

    def _update_duration_label(self) -> None:
        """Recompute and display the estimated video duration next to the speed control."""
        if self._track_length_m is None:
            self._duration_label.setText("")
            return
        speed = self._speed_spin.value()
        if speed <= 0:
            self._duration_label.setText("")
            return
        pause_duration_s = float(str(self._settings.value("render/photo_pause_duration", 3.0)))
        n_photos = len(self._store.all())
        total_s = self._track_length_m / speed + n_photos * pause_duration_s
        mins = int(total_s) // 60
        secs = int(total_s) % 60
        if mins > 0:
            text = f"≈ {mins} min {secs:02d} s"
        else:
            text = f"≈ {secs} s"
        self._duration_label.setText(text)

    def _build_ribbon_tab(self) -> QWidget:
        """Per-project ribbon options: color mode and emission style."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Color mode ────────────────────────────────────────────────
        color_group = QGroupBox("Color")
        color_layout = QVBoxLayout(color_group)
        color_layout.setSpacing(6)

        self._ribbon_slope_radio = QRadioButton("Slope gradient")
        self._ribbon_slope_radio.setToolTip(
            "Color the ribbon by terrain gradient:\n"
            "  flat → light blue  |  20% grade → yellow  |  ≥ 40% → red"
        )
        self._ribbon_speed_radio = QRadioButton("Speed gradient")
        self._ribbon_speed_radio.setToolTip(
            "Color the ribbon by recorded GPS speed, scaled between the\n"
            "5th and 95th percentile speeds of the track:\n"
            "  slow → cool blue  |  medium → cyan/green  |  fast → orange"
        )

        self._ribbon_color_group = QButtonGroup(self)
        self._ribbon_color_group.addButton(self._ribbon_slope_radio)
        self._ribbon_color_group.addButton(self._ribbon_speed_radio)

        saved_mode = str(self._settings.value(KEY_RIBBON_COLOR_MODE, "slope"))
        (self._ribbon_speed_radio if saved_mode == "speed"
         else self._ribbon_slope_radio).setChecked(True)

        color_layout.addWidget(self._ribbon_slope_radio)
        color_layout.addWidget(self._ribbon_speed_radio)
        layout.addWidget(color_group)

        # ── Appearance ────────────────────────────────────────────────
        appear_group = QGroupBox("Appearance")
        appear_layout = QVBoxLayout(appear_group)
        appear_layout.setSpacing(6)

        self._ribbon_self_lit_check = QCheckBox("Self-lit (vivid, sun-independent colors)")
        self._ribbon_self_lit_check.setToolTip(
            "When unchecked the ribbon emits at strength 2 — bright enough to stand\n"
            "out but still blends with scene bloom and exposure.\n"
            "When checked the emission strength is reduced to 1 so the vertex colors\n"
            "map linearly through Filmic tone-mapping, keeping hues fully saturated\n"
            "regardless of sun position or sky brightness."
        )
        saved_self_lit = self._settings.value(KEY_RIBBON_SELF_LIT, False)
        self._ribbon_self_lit_check.setChecked(
            bool(saved_self_lit) and saved_self_lit != "false"
        )

        appear_layout.addWidget(self._ribbon_self_lit_check)
        layout.addWidget(appear_group)

        layout.addStretch()

        # Wire up live-save to QSettings
        def _on_color_mode_changed():
            mode = "speed" if self._ribbon_speed_radio.isChecked() else "slope"
            self._settings.setValue(KEY_RIBBON_COLOR_MODE, mode)

        self._ribbon_slope_radio.toggled.connect(_on_color_mode_changed)
        self._ribbon_self_lit_check.toggled.connect(
            lambda v: self._settings.setValue(KEY_RIBBON_SELF_LIT, v)
        )

        return tab

    def _build_action_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._load_btn = QPushButton("Load project…")
        self._load_btn.setFixedHeight(36)
        self._load_btn.setToolTip(
            "Open a previously saved GeoReel project (.georeel).\n"
            "Restores the GPX track, photos, settings, and any cached DEM\n"
            "and satellite imagery without needing to re-download them."
        )
        self._load_btn.clicked.connect(self._load_project)

        self._save_btn = QPushButton("Save project…")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setToolTip(
            "Save the current GPX track, photos, settings, elevation data, and\n"
            "satellite texture into a single .georeel archive so the project can\n"
            "be resumed later without re-fetching any data."
        )
        self._save_btn.clicked.connect(self._save_project)

        self._preview_map_btn = QPushButton("Preview Map")
        self._preview_map_btn.setFixedHeight(36)
        self._preview_map_btn.setEnabled(False)
        self._preview_map_btn.setToolTip(
            "Render a quick 2D overhead map showing the GPX track and photo\n"
            "waypoint positions overlaid on the satellite imagery.\n"
            "Available after the DEM and satellite data have been fetched."
        )
        self._preview_map_btn.clicked.connect(self._show_preview_map)

        self._preview_video_btn = QPushButton("Preview Video")
        self._preview_video_btn.setFixedHeight(36)
        self._preview_video_btn.setEnabled(False)
        self._preview_video_btn.setToolTip(
            "Render a low-resolution preview of the full fly-through video\n"
            "so you can check camera path, timing, and photo placement before\n"
            "committing to a full-quality render. Requires a built 3D scene."
        )
        self._preview_video_btn.clicked.connect(self._show_preview_video)

        self._open_blender_btn = QPushButton("Open in Blender")
        self._open_blender_btn.setFixedHeight(36)
        self._open_blender_btn.setEnabled(False)
        self._open_blender_btn.setToolTip(
            "Open the generated .blend scene file in the Blender GUI so you\n"
            "can manually adjust materials, lighting, camera, or anything else\n"
            "before rendering. Requires a built 3D scene."
        )
        self._open_blender_btn.clicked.connect(self._open_in_blender)

        self._start_btn = QPushButton("Start")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setToolTip(
            "Fetch DEM elevation data and satellite imagery, build the 3D\n"
            "Blender scene, render all frames, and assemble the final video.\n"
            "Requires a GPX file and an output path to be set."
        )
        self._start_btn.clicked.connect(self._start)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(36)
        self._clear_btn.setToolTip(
            "Remove the loaded GPX track, all photos, and all cached data\n"
            "from the current session. Does not delete any files on disk."
        )
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

    def _camera_path_progress(self, current: int, total: int) -> None:
        self._fetch_progress_bar.setRange(0, total)
        self._fetch_progress_bar.setValue(current)
        QApplication.processEvents()

    def _status_show(self, message: str, level: int = logging.INFO) -> None:
        """Show *message* in the status bar and log it to the terminal."""
        _log.log(level, message)
        self._status.showMessage(message)

    def _mark_dirty(self, *_):
        if not self._suppress_dirty:
            self._dirty = True
            self._tilde_fresh = False

    def _autosave_tilde(self, *, update_dem: bool = False, update_sat: bool = False) -> None:
        """Fire-and-forget write of current DEM/satellite to project_path~.

        Skipped when no project has been saved/loaded yet.  Runs in a daemon
        thread so the UI stays responsive during the (potentially large) write.
        Sets _tilde_fresh=True on success so the next Save can just rename.
        """
        if not self._project_path:
            return
        path  = self._project_path
        state = self._current_state()

        # Wait for any previous autosave before starting a new one.
        if self._autosave_thread and self._autosave_thread.is_alive():
            self._autosave_thread.join()

        def _run() -> None:
            try:
                autosave_tilde(state, path, update_dem=update_dem, update_sat=update_sat)
                self._tilde_fresh = True
            except Exception:
                pass  # autosave failure is silent; user can always do a full save

        self._autosave_thread = threading.Thread(target=_run, daemon=True)
        self._autosave_thread.start()

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
        tz_offset = float(str(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0)))
        blender_exe = self._settings.value("blender/executable_path") or None

        worker = ScenePrepWorker(
            gpx_path=self._gpx_path,
            match_mode=self._match_mode(),
            tz_offset_hours=tz_offset,
            render_settings=render_settings,
            blender_exe=blender_exe,
            cached_elevation_grid=self._cached_elevation_grid,
            cached_satellite_texture=self._cached_satellite_texture,
            api_key=str(self._settings.value("imagery/api_key", "")),
            custom_url=str(self._settings.value("imagery/custom_url", "")),
            cleaned_trackpoints=self._pipeline.trackpoints or None,
        )
        worker.status.connect(self._status_show)
        worker.dem_fetched.connect(self._on_worker_dem_fetched)
        worker.satellite_fetched.connect(self._on_worker_satellite_fetched)
        worker.scene_ready.connect(self._on_worker_scene_ready)
        worker.error.connect(self._on_worker_error)
        worker.progress.connect(self._on_worker_fetch_progress)
        self._scene_prep_worker = worker
        worker.start()

    def _on_worker_fetch_progress(self, current: int, total: int) -> None:
        if total > 0:
            self._fetch_progress_bar.setRange(0, total)
            self._fetch_progress_bar.setValue(current)
        else:
            self._fetch_progress_bar.setRange(0, 0)
        self._fetch_progress_bar.show()

    def _on_worker_dem_fetched(self, grid):
        self._fetch_progress_bar.hide()
        self._cached_elevation_grid = grid
        self._mark_dirty()
        self._autosave_tilde(update_dem=True)

    def _on_worker_satellite_fetched(self, texture):
        self._fetch_progress_bar.hide()
        self._cached_satellite_texture = texture
        self._mark_dirty()
        self._autosave_tilde(update_sat=True)

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
        self._fetch_progress_bar.hide()
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
            clip_effects=self._clip_effects_widget.get_settings(),
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
            from georeel.core.gpx_parser import parse_gpx

            trackpoints, _ = parse_gpx(path)
            trackpoints, _ = detect_and_repair(
                trackpoints,
                str(self._settings.value(KEY_GPX_REPAIR_MODE, REPAIR_NONE)),
                max_speed_mps=float(str(self._settings.value(KEY_GPX_MAX_SPEED_KMH, 300))) / 3.6,
                max_gap_s=float(str(self._settings.value(KEY_GPX_MAX_GAP_S, 30.0))),
                max_jump_m=float(str(self._settings.value(KEY_GPX_MAX_JUMP_KM, 50.0))) * 1_000,
            )
            self._gpx_stats.update_stats(trackpoints)
            self._track_length_m = compute_stats(trackpoints).total_distance_m
        except Exception:
            self._gpx_stats.clear()
            self._track_length_m = None
        self._update_duration_label()

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

        self._update_duration_label()

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
        wh = {
            "720p": (1280, 720),
            "1080p": (1920, 1080),
            "1440p": (2560, 1440),
            "4k": (3840, 2160),
        }
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
        dlg = PreviewMapDialog(
            png_path, initial_dir=self._last_project_dir(), parent=self
        )
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
            self._fetch_progress_bar.setRange(0, 0)
            self._fetch_progress_bar.show()
            try:
                self._pipeline.camera_keyframes = build_camera_path(
                    self._pipeline, render_settings,
                    progress_callback=self._camera_path_progress,
                )
            except CameraPathError as e:
                self._fetch_progress_bar.hide()
                QMessageBox.critical(self, "Camera path error", str(e))
                self._status_show("Preview video failed: camera path error.")
                return
            self._fetch_progress_bar.hide()

        blender_exe = self._settings.value("blender/executable_path") or None
        self._status_show("Rendering preview video…")
        preview_settings = {
            **render_settings,
            **self._clip_effects_widget.get_settings(),
        }
        dlg = PreviewVideoProgressDialog(
            self._pipeline,
            preview_settings,
            blender_exe=blender_exe,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
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
        from georeel.core.blender_runtime import find_blender

        exe = find_blender(blender_exe)
        if exe is None:
            QMessageBox.critical(
                self,
                "Blender not found",
                "Blender executable not found.\nSet the path via Options → Blender…",
            )
            return

        render_settings = get_render_settings(self._settings)

        # Compute camera path if not yet done
        if not self._pipeline.camera_keyframes:
            self._status_show("Computing camera path…")
            self._fetch_progress_bar.setRange(0, 0)
            self._fetch_progress_bar.show()
            try:
                self._pipeline.camera_keyframes = build_camera_path(
                    self._pipeline, render_settings,
                    progress_callback=self._camera_path_progress,
                )
            except CameraPathError as e:
                self._fetch_progress_bar.hide()
                QMessageBox.critical(self, "Camera path error", str(e))
                self._status_show("Open in Blender failed: camera path error.")
                return
            self._fetch_progress_bar.hide()

        # Inject camera keyframes into a copy of the .blend (runs headlessly —
        # can take tens of seconds for long tracks; run in a background thread
        # so the UI stays responsive and the progress bar is visible).
        if self._inject_thread and self._inject_thread.isRunning():
            self._status_show("Already injecting camera — please wait.")
            return

        self._status_show("Injecting camera into scene…")
        self._fetch_progress_bar.setRange(0, 0)   # indeterminate spinner
        self._fetch_progress_bar.show()
        self.centralWidget().setEnabled(False)

        worker = _InjectWorker(
            exe,
            self._pipeline.scene,
            self._pipeline.camera_keyframes,
            render_settings.get("render/resolution", "1080p"),
            fps=int(render_settings.get("render/fps", 30)),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_inject_finished)
        worker.failed.connect(self._on_inject_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._inject_thread = thread
        self._inject_worker = worker   # keep reference alive
        thread.start()

    def _on_inject_finished(self):
        self._fetch_progress_bar.hide()
        self.centralWidget().setEnabled(True)
        self._status_show("Blender opened successfully.")

    def _on_inject_failed(self, message: str):
        self._fetch_progress_bar.hide()
        self.centralWidget().setEnabled(True)
        self._status_show("Open in Blender failed.", level=logging.ERROR)
        QMessageBox.critical(self, "Open in Blender failed", message)

    def _start(self):
        if not self._gpx_path:
            self._status_show("Please select a GPX file first.")
            return

        # Check for output file overwrite before doing any work
        output_path = self._output_selector.output_path()
        if not output_path:
            QMessageBox.warning(
                self,
                "No output path",
                "Please set an output video path before starting.",
            )
            self._status_show("Pipeline stopped: no output path set.")
            return
        if Path(output_path).exists():
            answer = QMessageBox.question(
                self,
                "Overwrite file?",
                f"The file already exists:\n{output_path}\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
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
            self._pipeline.cleanup()
            self._pipeline = Pipeline()

        # Stages 1–5: skip entirely if the background worker already built
        # a fresh scene for the current inputs.
        if not self._scene_stale and self._pipeline.scene is not None:
            self._status_show(f"Reusing existing scene: {self._pipeline.scene}")
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

        # GPX hole repair (optional, controlled by render settings)
        repair_mode = str(self._settings.value(KEY_GPX_REPAIR_MODE, REPAIR_NONE))
        if repair_mode != REPAIR_NONE:
            osrm_profile = str(self._settings.value(KEY_GPX_OSRM_PROFILE, "driving"))
            self._status_show(f"Repairing GPX holes ({repair_mode} mode)…")
            max_speed_mps = float(str(self._settings.value(KEY_GPX_MAX_SPEED_KMH, 300))) / 3.6
            max_gap_s     = float(str(self._settings.value(KEY_GPX_MAX_GAP_S, 30.0)))
            max_jump_m    = float(str(self._settings.value(KEY_GPX_MAX_JUMP_KM, 50.0))) * 1_000
            trackpoints, _cs = detect_and_repair(
                trackpoints,
                repair_mode,
                max_speed_mps=max_speed_mps,
                max_gap_s=max_gap_s,
                max_jump_m=max_jump_m,
                osrm_profile=osrm_profile,
            )
            _repair_msg = (
                f"GPX repaired: {_cs.nullified_removed} bad points removed, "
                f"{_cs.holes_filled} synthetic points added"
            )
            if _cs.street_fallbacks:
                _repair_msg += f" ({_cs.street_fallbacks} street→ground fallbacks)"
            _log.info(_repair_msg)

        # Recompute bbox from the (possibly cleaned) trackpoints so that
        # removed outliers (e.g. null-island artefacts) don't inflate the
        # DEM / satellite fetch region.
        if trackpoints:
            bbox = BoundingBox(
                min_lat=min(p.latitude  for p in trackpoints),
                max_lat=max(p.latitude  for p in trackpoints),
                min_lon=min(p.longitude for p in trackpoints),
                max_lon=max(p.longitude for p in trackpoints),
            )
        self._pipeline.trackpoints = trackpoints
        self._pipeline.bounding_box = bbox
        self._photo_area.update_pipeline_info(trackpoints=trackpoints)
        self._status_show(f"GPX parsed: {len(trackpoints)} trackpoints, bounds: {bbox}")

        # Stage 2 — Photo Matcher
        photos = self._store.all()
        if photos:
            self._status_show("Matching photos to trackpoints…")
            tz_offset = float(str(self._settings.value(KEY_PHOTO_TZ_OFFSET, 0.0)))
            results = match_photos(
                photos, trackpoints, self._match_mode(), tz_offset_hours=tz_offset
            )
            self._pipeline.match_results = results
            self._photo_area.update_match_statuses(results)

            failed = [r for r in results if not r.ok]
            if failed:
                lines = "\n".join(
                    f"• {Path(r.photo_path).name}: {r.error}" for r in failed
                )
                QMessageBox.warning(
                    self,
                    "Photo matching failed",
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
            max_view_m=float(render_settings.get(KEY_FRUSTUM_MARGIN_KM, 50)) * 1_000,
        )
        track_bbox = self._pipeline.bounding_box
        fetch_bbox = track_bbox.expand(margin_m)

        log_pipeline_memory(self._pipeline, "before DEM fetch")

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
                f"DEM: using cached grid ({cached.rows}×{cached.cols} points)."
            )
        else:
            self._status_show(f"Fetching DEM (SRTM, {margin_m / 1000:.1f} km margin)…")
            self._fetch_progress_bar.setRange(0, 0)
            self._fetch_progress_bar.show()

            def _dem_progress(current: int, total: int) -> None:
                self._fetch_progress_bar.setRange(0, total)
                self._fetch_progress_bar.setValue(current)
                QApplication.processEvents()

            try:
                grid = fetch_dem(fetch_bbox, progress_callback=_dem_progress)
            except DemFetchError as e:
                self._fetch_progress_bar.hide()
                QMessageBox.critical(self, "DEM error", str(e))
                self._status_show("Pipeline stopped: DEM fetch failed.")
                return
            self._fetch_progress_bar.hide()
            self._pipeline.elevation_grid = grid
            self._cached_elevation_grid = grid
            self._mark_dirty()
            self._autosave_tilde(update_dem=True)
            self._status_show(
                f"DEM fetched: {grid.rows}×{grid.cols} points "
                f"({grid.rows * grid.cols:,} total)."
            )

        log_pipeline_memory(self._pipeline, "after DEM fetch")

        # Stage 4 — Satellite Imagery Fetcher
        provider_id = str(self._settings.value("imagery/provider",    "esri_world"))
        img_quality = str(self._settings.value("imagery/quality",     "standard"))
        fetch_mode  = str(self._settings.value("imagery/fetch_mode",  "prefetch"))
        on_demand   = fetch_mode == "on_demand"
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
            try:
                dims = f"({cached_sat.width}×{cached_sat.height} px)"
            except RuntimeError:
                dims = "(size unknown)"
            self._status_show(f"Satellite: using cached texture {dims}.")
        else:
            self._status_show("Fetching satellite imagery…")
            self._fetch_progress_bar.setRange(0, 0)
            self._fetch_progress_bar.show()

            def _sat_progress(current: int, total: int) -> None:
                self._fetch_progress_bar.setRange(0, total)
                self._fetch_progress_bar.setValue(current)
                QApplication.processEvents()

            try:
                source = build_source(
                    provider_id=provider_id,
                    api_key=str(self._settings.value("imagery/api_key", "")),
                    custom_url=str(self._settings.value("imagery/custom_url", "")),
                    quality=img_quality,
                )
                texture = source.fetch(
                    fetch_bbox,
                    progress_callback=_sat_progress,
                    on_demand=on_demand,
                )
            except Exception as e:
                self._fetch_progress_bar.hide()
                QMessageBox.critical(self, "Satellite imagery error", str(e))
                self._status_show("Pipeline stopped: satellite fetch failed.")
                return
            self._fetch_progress_bar.hide()
            self._pipeline.satellite_texture = texture
            self._cached_satellite_texture = texture
            self._mark_dirty()
            self._autosave_tilde(update_sat=True)
            self._status_show(
                f"Satellite imagery fetched: {texture.width}×{texture.height} px."
            )

        log_pipeline_memory(self._pipeline, "after satellite fetch")

        # Stage 5 — 3D Scene Builder
        blender_exe = self._settings.value("blender/executable_path") or None
        dlg = SceneBuildDialog(
            self._pipeline,
            blender_exe=blender_exe,
            settings=render_settings,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.blend_path() is None:
            self._status_show("Pipeline stopped: scene build cancelled or failed.")
            return
        blend_path = dlg.blend_path()
        self._pipeline.scene = blend_path
        log_pipeline_memory(self._pipeline, "after scene build")
        self._scene_stale = False
        self._open_blender_btn.setEnabled(True)
        self._preview_map_btn.setEnabled(True)
        self._preview_video_btn.setEnabled(True)
        self._status_show(f"3D scene ready: {blend_path}")

        self._start_from_camera_path(render_settings)

    def _start_from_camera_path(self, render_settings: dict[str, Any]) -> None:
        """Run stages 6–9, assuming self._pipeline already has stages 1–5."""

        # Stage 6 — Camera Path Generator
        self._status_show("Computing camera path…")
        self._fetch_progress_bar.setRange(0, 0)
        self._fetch_progress_bar.show()
        try:
            keyframes = build_camera_path(self._pipeline, render_settings,
                                          progress_callback=self._camera_path_progress)
        except CameraPathError as e:
            self._fetch_progress_bar.hide()
            QMessageBox.critical(self, "Camera path error", str(e))
            self._status_show("Pipeline stopped: camera path failed.")
            return
        self._fetch_progress_bar.hide()
        self._pipeline.camera_keyframes = keyframes
        self._photo_area.update_pipeline_info(keyframes=keyframes)
        fps = render_settings.get("render/fps", 30)
        duration_s = len(keyframes) / fps
        self._status_show(
            f"Camera path: {len(keyframes)} frames ({duration_s:.1f} s at {fps} fps)"
        )

        # Stage 7 — Frame Renderer
        blender_exe = self._settings.value("blender/executable_path") or None
        dlg = RenderProgressDialog(
            self._pipeline,
            render_settings,
            blender_exe=blender_exe,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._pipeline.cleanup()
            self._status_show("Pipeline stopped: rendering cancelled or failed.")
            return
        self._pipeline.rendered_frames_dir = dlg.frames_dir()
        self._status_show(f"Frames rendered: {self._pipeline.rendered_frames_dir}")

        # Stage 8 — Photo Overlay Compositor
        dlg = CompositorProgressDialog(self._pipeline, render_settings, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._pipeline.cleanup()
            self._status_show("Pipeline stopped: compositing cancelled or failed.")
            return
        self._pipeline.composited_frames_dir = dlg.composited_frames_dir()
        self._status_show(f"Compositing done: {self._pipeline.composited_frames_dir}")

        # Stage 9 — Video Assembler
        output_path = self._output_selector.output_path()
        total_frames = len(self._pipeline.camera_keyframes or [])
        assemble_settings = {
            **render_settings,
            **self._clip_effects_widget.get_settings(),
        }
        dlg = VideoProgressDialog(
            self._pipeline.composited_frames_dir or "",
            output_path or "",
            assemble_settings,
            total_frames,
            gpx_path=self._gpx_path,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._pipeline.cleanup()
            self._status_show("Pipeline stopped: video encoding cancelled or failed.")
            return
        self._pipeline.output_video_path = output_path
        self._pipeline.cleanup()
        self._status_show(f"Done! Video saved: {output_path}")

    # ------------------------------------------------------------------
    # Project persistence
    # ------------------------------------------------------------------

    def _last_project_dir(self) -> str:
        return str(self._settings.value("project/last_dir", ""))

    def _save_last_project_dir(self, path: str):
        self._settings.setValue("project/last_dir", str(Path(path).parent))

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------

    _MAX_RECENT = 10

    def _recent_files(self) -> list[str]:
        """Return recent project paths that still exist, most-recent first."""
        raw = self._settings.value("project/recent_files", [])
        if isinstance(raw, str):  # QSettings may deserialise a single item as str
            entries: list[str] = [raw]
        elif isinstance(raw, list):
            entries = [str(x) for x in raw]
        else:
            entries = []
        return [p for p in entries if Path(p).is_file()]

    def _add_recent_file(self, path: str) -> None:
        raw = self._settings.value("project/recent_files", [])
        if isinstance(raw, str):
            entries: list[str] = [raw]
        elif isinstance(raw, list):
            entries = [str(x) for x in raw]
        else:
            entries = []
        paths = [p for p in entries if p != path]
        paths.insert(0, path)
        self._settings.setValue("project/recent_files", paths[: self._MAX_RECENT])

    def _populate_recent_menu(self) -> None:
        self._recent_menu.clear()
        recent = self._recent_files()
        if not recent:
            empty = self._recent_menu.addAction("No recent files")
            empty.setEnabled(False)
            return
        for p in recent:
            label = f"{Path(p).name}  —  {Path(p).parent}"
            action = self._recent_menu.addAction(label)
            action.setToolTip(p)
            action.triggered.connect(lambda checked, path=p: self._load_from_path(path))

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

    def _save_to_path(self, path: str) -> None:
        """Start a save to *path*.

        Fast path: if the tilde file is up-to-date (set after the last
        DEM/satellite fetch and no subsequent _mark_dirty), simply rename
        path~ → path.  Otherwise falls back to a full async rebuild.
        """
        # Clean up stale tilde from the previous project path on Save-As.
        if self._project_path and self._project_path != path:
            Path(self._project_path + "~").unlink(missing_ok=True)
            self._tilde_fresh = False

        # Wait for any in-progress autosave thread so the tilde is complete.
        if self._autosave_thread and self._autosave_thread.is_alive():
            self._autosave_thread.join()

        tilde = path + "~"
        if self._tilde_fresh and Path(tilde).is_file():
            # Fast path — tilde is ready; just rename it.
            try:
                shutil.move(tilde, path)
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e))
                self._status_show("Save failed.")
                return
            self._tilde_fresh = False
            self._save_last_project_dir(path)
            self._add_recent_file(path)
            self._project_path = path
            self._suggest_output_from_project(path)
            self._dirty = False
            self._status_show(f"Project saved: {path}")
            if self._pending_close:
                self._pending_close = False
                self._cleanup_temp_dir()
                self.close()
            return

        if self._save_thread and self._save_thread.isRunning():
            return  # already saving

        state = self._current_state()

        self.centralWidget().setEnabled(False)
        self.menuBar().setEnabled(False)
        self._save_progress_bar.show()
        self._status.showMessage("Saving project…")

        self._pending_save_path = path
        worker = _SaveWorker(state, path)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_save_worker_finished)
        worker.failed.connect(self._on_save_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._save_thread = thread
        self._save_worker = worker  # keep reference alive
        thread.start()

    def _on_save_worker_finished(self) -> None:
        self._on_save_complete(self._pending_save_path, None)

    def _on_save_worker_failed(self, msg: str) -> None:
        self._on_save_complete(self._pending_save_path, msg)

    def _on_save_complete(self, path: str, error: str | None) -> None:
        self._save_progress_bar.hide()
        self.centralWidget().setEnabled(True)
        self.menuBar().setEnabled(True)
        self._save_thread = None

        if error:
            self._status_show("Save failed.")
            QMessageBox.critical(self, "Save failed", error)
            self._pending_close = False
        else:
            # Full save completed — any tilde is now redundant.
            Path(path + "~").unlink(missing_ok=True)
            self._tilde_fresh = False
            self._save_last_project_dir(path)
            self._add_recent_file(path)
            self._project_path = path
            self._suggest_output_from_project(path)
            self._dirty = False
            self._status_show(f"Project saved: {path}")
            if self._pending_close:
                self._pending_close = False
                self._cleanup_temp_dir()
                self.close()  # _dirty is False now → accepted without re-prompting

    def _save(self) -> None:
        """Save to the current project path; open dialog if no path set yet."""
        if self._project_path:
            self._save_to_path(self._project_path)
        else:
            self._save_project()

    def _save_project(self) -> None:
        """Show save-as dialog, then start an async save."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save project",
            str(Path(self._last_project_dir()) / "project.georeel"),
            "GeoReel project (*.georeel)",
        )
        if not path:
            return
        if not path.endswith(".georeel"):
            path += ".georeel"
        self._save_to_path(path)

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load project",
            self._last_project_dir(),
            "GeoReel project (*.georeel)",
        )
        if not path:
            return
        self._load_from_path(path)

    def _load_from_path(self, path: str) -> None:
        """Load *path* in a background QThread so the UI stays responsive."""
        if self._load_thread and self._load_thread.isRunning():
            return

        self.centralWidget().setEnabled(False)
        self.menuBar().setEnabled(False)
        self._fetch_progress_bar.setRange(0, 0)
        self._fetch_progress_bar.show()
        self._status.showMessage(f"Loading project: {Path(path).name}…")

        self._pending_load_path = path
        worker = _LoadWorker(
            path,
            repair_mode=str(self._settings.value(KEY_GPX_REPAIR_MODE, REPAIR_NONE)),
            max_speed_mps=float(str(self._settings.value(KEY_GPX_MAX_SPEED_KMH, 300))) / 3.6,
            max_gap_s=float(str(self._settings.value(KEY_GPX_MAX_GAP_S, 30.0))),
            max_jump_m=float(str(self._settings.value(KEY_GPX_MAX_JUMP_KM, 50.0))) * 1_000,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Use bound methods (not lambdas) so Qt sees these as slots of a QObject
        # on the main thread and uses a queued connection automatically.
        worker.finished.connect(self._on_load_worker_finished)
        worker.failed.connect(self._on_load_worker_failed)
        worker.progress.connect(self._on_load_worker_progress)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        self._load_thread = thread
        self._load_worker = worker  # keep reference alive
        thread.start()

    def _on_load_worker_finished(self, result: object) -> None:
        self._on_load_complete(self._pending_load_path, result, None)

    def _on_load_worker_progress(self, msg: str) -> None:
        self._status.showMessage(msg)

    def _on_load_worker_failed(self, msg: str) -> None:
        self._on_load_complete(self._pending_load_path, None, msg)

    def _on_load_complete(self, path: str, result, error: str | None) -> None:
        self._fetch_progress_bar.hide()
        self.centralWidget().setEnabled(True)
        self.menuBar().setEnabled(True)
        self._load_thread = None
        self._load_worker = None

        if error:
            QMessageBox.critical(self, "Load failed", error)
            self._status_show("Load failed.")
            return
        self._apply_loaded_project(result, path)

    def _apply_loaded_project(self, result: _LoadResult, path: str) -> None:
        """Apply a loaded _LoadResult to the UI (main-thread UI work only)."""
        state = result.state
        self._suppress_dirty = True
        try:
            self._clear()
            if state.temp_dir:
                self._project_temp_dir = state.temp_dir
            if state.gpx_path:
                self._gpx_path = state.gpx_path
                self._gpx_area.set_file(state.gpx_path)
                if result.gpx_failed or result.gpx_stats is None:
                    self._gpx_stats.clear()
                    self._track_length_m = None
                else:
                    self._gpx_stats.apply_stats(result.gpx_stats)
                    self._track_length_m = result.gpx_stats.total_distance_m
            self._photo_area.preload_exif_cache(result.exif_cache)
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
            self._reload_gpx_repair_controls()
            self._reload_speed_control()
            self._update_duration_label()
        if state.clip_effects:
            for key, value in state.clip_effects.items():
                self._settings.setValue(key, value)
        self._clip_effects_widget.reload()
        self._save_last_project_dir(path)
        self._add_recent_file(path)
        self._project_path = path
        self._suggest_output_from_project(path)
        self._dirty = False
        self._status_show(f"Project loaded: {path}")
        if self._gpx_path:
            self._preview_map_btn.setEnabled(True)
            self._preview_video_btn.setEnabled(True)
            self._open_blender_btn.setEnabled(True)

    def _clear(self):
        # Wait for any autosave in flight and discard the tilde.
        if self._autosave_thread and self._autosave_thread.is_alive():
            self._autosave_thread.join()
        if self._project_path:
            Path(self._project_path + "~").unlink(missing_ok=True)
        self._tilde_fresh = False

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
        self._track_length_m = None
        self._project_path = None
        self._cleanup_temp_dir()
        self._pipeline = Pipeline()
        self._update_duration_label()
        self._scene_stale = True
        self._open_blender_btn.setEnabled(False)
        self._preview_map_btn.setEnabled(False)
        self._preview_video_btn.setEnabled(False)
        if self._scene_prep_worker and self._scene_prep_worker.isRunning():
            self._scene_prep_worker.quit()
        if self._keyframe_calc_worker and self._keyframe_calc_worker.isRunning():
            self._keyframe_calc_worker.quit()
        if self._load_thread and self._load_thread.isRunning():
            self._load_thread.quit()
        self._dirty = False
        self._status_show("Cleared.")

    # ------------------------------------------------------------------
    # Close / unsaved-changes guard
    # ------------------------------------------------------------------

    def _cleanup_temp_dir(self):
        if self._project_temp_dir and self._project_temp_dir.exists():
            shutil.rmtree(self._project_temp_dir, ignore_errors=True)
        self._project_temp_dir = None

    def _restore_window_geometry(self) -> None:
        geometry = self._settings.value("window/geometry")
        restored = bool(geometry and self.restoreGeometry(geometry))
        if restored:
            # Validate: center must land on a known screen and the window must
            # fit within that screen's available area (guards against lower-res
            # displays or disconnected monitors).
            screen = QApplication.screenAt(self.frameGeometry().center())
            if screen is None:
                restored = False
            else:
                avail = screen.availableGeometry()
                fg = self.frameGeometry()
                if fg.width() > avail.width() or fg.height() > avail.height():
                    restored = False
        if not restored:
            self.resize(1100, 820)
            screen = QApplication.primaryScreen()
            if screen:
                self.move(screen.availableGeometry().center() - self.rect().center())

    def _save_window_geometry(self) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())

    def closeEvent(self, event: QCloseEvent):
        if not self._dirty:
            self._save_window_geometry()
            self._cleanup_temp_dir()
            event.accept()
            return

        _SB = QMessageBox.StandardButton
        if self._project_path:
            name = Path(self._project_path).name
            answer = QMessageBox.question(
                self,
                "Unsaved changes",
                f'Save changes to "{name}"?',
                _SB.Save | _SB.Discard | _SB.Cancel,
            )
            if answer == _SB.Save:
                self._pending_close = True
                self._save_to_path(self._project_path)
                event.ignore()  # window stays open; close() fires in _on_save_complete
            elif answer == _SB.Discard:
                self._save_window_geometry()
                self._cleanup_temp_dir()
                event.accept()
            else:
                event.ignore()
        else:
            answer = QMessageBox.question(
                self,
                "Unsaved changes",
                "Do you want to save the project before closing?",
                _SB.Save | _SB.Discard | _SB.Cancel,
            )
            if answer == _SB.Save:
                path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save project",
                    str(Path(self._last_project_dir()) / "project.georeel"),
                    "GeoReel project (*.georeel)",
                )
                if path:
                    if not path.endswith(".georeel"):
                        path += ".georeel"
                    self._pending_close = True
                    self._save_to_path(path)
                    event.ignore()
                else:
                    event.ignore()  # user cancelled the save dialog
            elif answer == _SB.Discard:
                self._save_window_geometry()
                self._cleanup_temp_dir()
                event.accept()
            else:
                event.ignore()
