import shutil

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from georeel.core.satellite.providers import PROVIDERS
from georeel.ui.color_picker_dialog import CSS3_COLORS, ColorPickerDialog, get_color_hex

from georeel.core.encoder_registry import (
    EncoderConfig,
    detect_available_encoders,
    encoders_for_codec,
    get_encoder,
)

# ------------------------------------------------------------------
# QSettings keys and defaults
# ------------------------------------------------------------------

KEY_PATH_SMOOTHING        = "render/path_smoothing"          # "spline" | "dp_spline"
KEY_HEIGHT_MODE           = "render/camera_height_mode"      # "dem_fixed" | "dem_smooth"
KEY_HEIGHT_OFFSET         = "render/camera_height_offset"    # slant distance to track point (metres)
KEY_ORIENTATION           = "render/camera_orientation"      # "tangent" | "lookat"
KEY_TILT_DEG              = "render/camera_tilt_deg"         # degrees below horizontal (int)
KEY_PHOTO_PAUSE_MODE      = "render/photo_pause_mode"        # "hold" | "ease"
KEY_PHOTO_PAUSE_DURATION  = "render/photo_pause_duration"    # seconds (float)
KEY_FPS                   = "render/fps"                     # int: 24 | 30 | 60
KEY_CAMERA_SPEED          = "render/camera_speed_mps"        # metres per second (float)
KEY_ENGINE                = "render/engine"                  # "eevee" | "cycles"
KEY_ASPECT_RATIO          = "render/aspect_ratio"            # "landscape" | "portrait" | "square"
KEY_RESOLUTION            = "render/resolution"              # see _ASPECT_RESOLUTIONS values
KEY_QUALITY               = "render/quality"                 # "low" | "medium" | "high"
KEY_PHOTO_TZ_OFFSET       = "render/photo_tz_offset_hours"   # float: UTC offset of camera clock
KEY_PHOTO_TRANSITION      = "render/photo_transition"        # "fade" | "cut"
KEY_PHOTO_FILL            = "render/photo_fill"              # "blurred" | "black"
KEY_PHOTO_FADE_DURATION   = "render/photo_fade_duration"     # seconds (float)
KEY_TANGENT_LOOKAHEAD_S   = "render/tangent_lookahead_s"     # seconds (float)
KEY_TANGENT_WEIGHT        = "render/tangent_weight"          # "uniform" | "linear" | "exponential"
KEY_PIN_COLOR             = "pins/color"                     # named color id or "custom"
KEY_PIN_CUSTOM_COLOR      = "pins/custom_color"              # "#rrggbb" when color=="custom"
KEY_MARKER_COLOR          = "marker/color"                   # named color id or "custom"
KEY_MARKER_CUSTOM_COLOR   = "marker/custom_color"            # "#rrggbb" when color=="custom"
KEY_IMAGERY_PROVIDER      = "imagery/provider"               # provider id
KEY_IMAGERY_QUALITY       = "imagery/quality"                # "standard" | "high" | "very_high"
KEY_IMAGERY_API_KEY       = "imagery/api_key"                # per-provider key (provider-prefixed)
KEY_IMAGERY_CUSTOM_URL    = "imagery/custom_url"
KEY_IMAGERY_FETCH_MODE    = "imagery/fetch_mode"             # "prefetch" | "on_demand"
KEY_CONTAINER             = "output/container"               # "mkv" | "mp4"
KEY_CODEC                 = "output/codec"                   # "h264" | "h265" | "av1"
KEY_ENCODER               = "output/encoder"                 # FFmpeg encoder name
KEY_OUTPUT_CQ             = "output/cq"                      # int
KEY_OUTPUT_PRESET         = "output/preset"                  # string
KEY_FRUSTUM_MARGIN_KM     = "render/frustum_margin_km"       # float km — max terrain view distance
KEY_RENDER_SEGMENTS       = "render/n_segments"              # int: render passes (1 = single pass)
KEY_GPX_REPAIR_MODE       = "gpx/repair_mode"                # "none" | "ground" | "street"
KEY_GPX_OSRM_PROFILE      = "gpx/osrm_profile"               # "driving" | "cycling" | "walking"
KEY_GPX_MAX_SPEED_KMH     = "gpx/max_speed_kmh"             # int km/h — above this is nullified
KEY_GPX_MAX_GAP_S         = "gpx/max_gap_s"                 # float s — gaps longer than this are filled
KEY_GPX_MAX_JUMP_KM       = "gpx/max_jump_km"               # float km — no-timestamp fallback distance
KEY_CACHE_USE_CUSTOM_DIR  = "cache/use_custom_dir"           # bool — use custom temp dir
KEY_CACHE_BASE_DIR        = "cache/base_dir"                 # str  — path to custom temp dir

_ASPECT_RESOLUTIONS: dict[str, list[tuple[str, str]]] = {
    "landscape": [
        ("720p   (1280 × 720)",   "720p"),
        ("1080p  (1920 × 1080)", "1080p"),
        ("1440p  (2560 × 1440)", "1440p"),
        ("4K     (3840 × 2160)", "4k"),
    ],
    "portrait": [
        ("720 × 1280",                    "portrait_720p"),
        ("1080 × 1920 (Instagram reel)", "portrait_1080p"),
        ("1440 × 2560",                  "portrait_1440p"),
        ("2160 × 3840",                  "portrait_4k"),
    ],
    "square": [
        ("720 × 720",   "square_720"),
        ("1080 × 1080", "square_1080"),
        ("1440 × 1440", "square_1440"),
        ("2160 × 2160", "square_2160"),
    ],
}

DEFAULTS = {
    KEY_PATH_SMOOTHING:       "spline",
    KEY_HEIGHT_MODE:          "dem_fixed",
    KEY_HEIGHT_OFFSET:        2000,
    KEY_ORIENTATION:          "tangent",
    KEY_TILT_DEG:             45,
    KEY_PHOTO_PAUSE_MODE:     "hold",
    KEY_PHOTO_PAUSE_DURATION: 3.0,
    KEY_FPS:                  30,
    KEY_CAMERA_SPEED:         80.0,
    KEY_ENGINE:               "eevee",
    KEY_ASPECT_RATIO:         "landscape",
    KEY_RESOLUTION:           "1080p",
    KEY_QUALITY:              "medium",
    KEY_PHOTO_TRANSITION:     "fade",
    KEY_PHOTO_FILL:           "blurred",
    KEY_PHOTO_FADE_DURATION:  0.5,
    KEY_CONTAINER:            "mkv",
    KEY_CODEC:                "h265",
    KEY_ENCODER:              "libx265",
    KEY_OUTPUT_CQ:            28,
    KEY_OUTPUT_PRESET:        "medium",
    KEY_PHOTO_TZ_OFFSET:      0.0,
    KEY_TANGENT_LOOKAHEAD_S:  60.0,
    KEY_TANGENT_WEIGHT:       "linear",
    KEY_FRUSTUM_MARGIN_KM:    50.0,
    KEY_IMAGERY_PROVIDER:     "esri_world",
    KEY_IMAGERY_QUALITY:      "standard",
    KEY_IMAGERY_API_KEY:      "",
    KEY_IMAGERY_CUSTOM_URL:   "",
    KEY_IMAGERY_FETCH_MODE:   "prefetch",
    KEY_PIN_COLOR:            "ForestGreen",
    KEY_PIN_CUSTOM_COLOR:     "#228B22",
    KEY_MARKER_COLOR:         "LightBlue",
    KEY_MARKER_CUSTOM_COLOR:  "#ADD8E6",
    KEY_RENDER_SEGMENTS:      1,
    KEY_GPX_REPAIR_MODE:      "none",
    KEY_GPX_MAX_SPEED_KMH:    300,
    KEY_GPX_MAX_GAP_S:        30.0,
    KEY_GPX_MAX_JUMP_KM:      50.0,
    KEY_CACHE_USE_CUSTOM_DIR: False,
    KEY_CACHE_BASE_DIR:       "",
}


def get_render_settings(settings: QSettings) -> dict:
    """Return all render settings as a plain dict, filled with defaults."""
    return {
        key: type(default)(settings.value(key, default))
        for key, default in DEFAULTS.items()
    }


# ------------------------------------------------------------------
# Dialog
# ------------------------------------------------------------------

class RenderSettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Render Settings")
        self.setMinimumSize(580, 320)
        self._settings = settings

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.West)
        tabs.addTab(self._build_playback_tab(),  "Playback")
        tabs.addTab(self._build_camera_tab(),    "Camera")
        tabs.addTab(self._build_rendering_tab(), "Rendering")
        tabs.addTab(self._build_photos_tab(),    "Photos")
        tabs.addTab(self._build_map_tab(),       "Map")
        tabs.addTab(self._build_pins_tab(),      "Pins")
        tabs.addTab(self._build_output_tab(),    "Output")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_playback_tab(self) -> QWidget:
        tab, layout = _make_tab()

        group = QGroupBox("Playback")
        form = QFormLayout(group)

        self._fps_combo = QComboBox()
        for fps in (24, 30, 60):
            self._fps_combo.addItem(f"{fps} fps", fps)
        _set_combo(self._fps_combo,
                   int(str(self._settings.value(KEY_FPS, DEFAULTS[KEY_FPS]))))
        form.addRow("Frame rate:", self._fps_combo)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _build_camera_tab(self) -> QWidget:
        tab, layout = _make_tab()

        # Path smoothing
        path_group = QGroupBox("Path smoothing")
        path_form = QFormLayout(path_group)
        self._path_combo = QComboBox()
        self._path_combo.addItem("B-spline through all trackpoints", "spline")
        self._path_combo.addItem("Douglas-Peucker simplification + B-spline", "dp_spline")
        _set_combo(self._path_combo,
                   str(self._settings.value(KEY_PATH_SMOOTHING, DEFAULTS[KEY_PATH_SMOOTHING])))
        path_form.addRow("Method:", self._path_combo)

        # Height
        height_group = QGroupBox("Height")
        height_form = QFormLayout(height_group)
        self._height_combo = QComboBox()
        self._height_combo.addItem("Fixed offset above DEM", "dem_fixed")
        self._height_combo.addItem("Smoothed DEM offset", "dem_smooth")
        _set_combo(self._height_combo,
                   self._settings.value(KEY_HEIGHT_MODE, DEFAULTS[KEY_HEIGHT_MODE]))
        height_form.addRow("Mode:", self._height_combo)
        self._height_spin = QSpinBox()
        self._height_spin.setRange(5, 5000)
        self._height_spin.setSingleStep(10)
        self._height_spin.setSuffix(" m")
        self._height_spin.setValue(
            int(str(self._settings.value(KEY_HEIGHT_OFFSET, DEFAULTS[KEY_HEIGHT_OFFSET])))
        )
        height_form.addRow("Distance to track:", self._height_spin)

        # Orientation
        orient_group = QGroupBox("Orientation")
        orient_form = QFormLayout(orient_group)
        self._orient_combo = QComboBox()
        self._orient_combo.addItem("Path tangent (look forward)", "tangent")
        self._orient_combo.addItem("Look at next waypoint", "lookat")
        _set_combo(self._orient_combo,
                   self._settings.value(KEY_ORIENTATION, DEFAULTS[KEY_ORIENTATION]))
        orient_form.addRow("Method:", self._orient_combo)
        self._tilt_spin = QSpinBox()
        self._tilt_spin.setRange(0, 89)
        self._tilt_spin.setSuffix("°")
        self._tilt_spin.setValue(
            int(str(self._settings.value(KEY_TILT_DEG, DEFAULTS[KEY_TILT_DEG])))
        )
        orient_form.addRow("Downward tilt:", self._tilt_spin)

        self._frustum_spin = QDoubleSpinBox()
        self._frustum_spin.setRange(1.0, 500.0)
        self._frustum_spin.setSingleStep(10.0)
        self._frustum_spin.setDecimals(0)
        self._frustum_spin.setSuffix(" km")
        self._frustum_spin.setValue(
            float(str(self._settings.value(KEY_FRUSTUM_MARGIN_KM,
                                       DEFAULTS[KEY_FRUSTUM_MARGIN_KM])))
        )
        self._frustum_spin.setToolTip(
            "Maximum terrain fetch distance around the track.\n"
            "Increase this if the terrain appears to end too close to the edges\n"
            "of the frame (visible at shallow tilt / near-horizontal views)."
        )
        orient_form.addRow("Terrain view distance:", self._frustum_spin)

        self._lookahead_spin = QDoubleSpinBox()
        self._lookahead_spin.setRange(1.0, 300.0)
        self._lookahead_spin.setSingleStep(5.0)
        self._lookahead_spin.setDecimals(0)
        self._lookahead_spin.setSuffix(" s")
        self._lookahead_spin.setValue(
            float(str(self._settings.value(KEY_TANGENT_LOOKAHEAD_S,
                                       DEFAULTS[KEY_TANGENT_LOOKAHEAD_S])))
        )
        orient_form.addRow("Look-ahead:", self._lookahead_spin)

        self._tangent_weight_combo = QComboBox()
        self._tangent_weight_combo.addItem("Linear falloff (recommended)", "linear")
        self._tangent_weight_combo.addItem("Uniform (equal weight)",        "uniform")
        self._tangent_weight_combo.addItem("Exponential (near-biased)",     "exponential")
        _set_combo(self._tangent_weight_combo,
                   self._settings.value(KEY_TANGENT_WEIGHT, DEFAULTS[KEY_TANGENT_WEIGHT]))
        orient_form.addRow("Weight distribution:", self._tangent_weight_combo)

        # Photo pause (camera movement)
        pause_group = QGroupBox("Photo pause")
        pause_form = QFormLayout(pause_group)
        self._pause_combo = QComboBox()
        self._pause_combo.addItem("Hold (freeze camera)", "hold")
        self._pause_combo.addItem("Ease in / hold / ease out", "ease")
        _set_combo(self._pause_combo,
                   self._settings.value(KEY_PHOTO_PAUSE_MODE, DEFAULTS[KEY_PHOTO_PAUSE_MODE]))
        pause_form.addRow("Camera movement:", self._pause_combo)
        self._pause_spin = QDoubleSpinBox()
        self._pause_spin.setRange(0.5, 30.0)
        self._pause_spin.setSingleStep(0.5)
        self._pause_spin.setSuffix(" s")
        self._pause_spin.setValue(
            float(str(self._settings.value(KEY_PHOTO_PAUSE_DURATION,
                                       DEFAULTS[KEY_PHOTO_PAUSE_DURATION])))
        )
        pause_form.addRow("Duration per photo:", self._pause_spin)

        layout.addWidget(path_group)
        layout.addWidget(height_group)
        layout.addWidget(orient_group)
        layout.addWidget(pause_group)
        layout.addStretch()
        return tab

    def _build_rendering_tab(self) -> QWidget:
        tab, layout = _make_tab()

        group = QGroupBox("3D Rendering")
        form = QFormLayout(group)

        self._engine_combo = QComboBox()
        self._engine_combo.addItem("EEVEE (fast, rasterisation)", "eevee")
        self._engine_combo.addItem("Cycles (slow, path tracing)", "cycles")
        _set_combo(self._engine_combo,
                   self._settings.value(KEY_ENGINE, DEFAULTS[KEY_ENGINE]))
        form.addRow("Engine:", self._engine_combo)

        self._aspect_combo = QComboBox()
        self._aspect_combo.addItem("Landscape (16:9)", "landscape")
        self._aspect_combo.addItem("Portrait (9:16)",  "portrait")
        self._aspect_combo.addItem("Square (1:1)",     "square")
        saved_aspect = str(self._settings.value(KEY_ASPECT_RATIO, DEFAULTS[KEY_ASPECT_RATIO]))
        _set_combo(self._aspect_combo, saved_aspect)
        form.addRow("Aspect ratio:", self._aspect_combo)

        self._resolution_combo = QComboBox()
        saved_resolution = str(self._settings.value(KEY_RESOLUTION, DEFAULTS[KEY_RESOLUTION]))
        self._populate_resolution_combo(saved_aspect, saved_resolution)
        form.addRow("Resolution:", self._resolution_combo)

        self._quality_combo = QComboBox()
        self._quality_combo.addItem("Low    (EEVEE 32  / Cycles 64 samples)",  "low")
        self._quality_combo.addItem("Medium (EEVEE 64  / Cycles 128 samples)", "medium")
        self._quality_combo.addItem("High   (EEVEE 128 / Cycles 256 samples)", "high")
        _set_combo(self._quality_combo,
                   self._settings.value(KEY_QUALITY, DEFAULTS[KEY_QUALITY]))
        form.addRow("Quality:", self._quality_combo)

        self._segments_spin = QSpinBox()
        self._segments_spin.setRange(1, 16)
        self._segments_spin.setValue(
            int(str(self._settings.value(KEY_RENDER_SEGMENTS, DEFAULTS[KEY_RENDER_SEGMENTS])))
        )
        self._segments_spin.setToolTip(
            "Split the render into N sequential passes.\n"
            "Each pass launches a fresh Blender process that loads only the\n"
            "terrain tiles visible during its frame range, reducing VRAM usage.\n"
            "Use 1 for a single pass (default). Increase to 4–8 for very large\n"
            "satellite textures that exceed your GPU memory."
        )
        form.addRow("Render segments:", self._segments_spin)

        layout.addWidget(group)
        layout.addStretch()

        self._aspect_combo.currentIndexChanged.connect(self._on_aspect_changed)
        return tab

    def _populate_resolution_combo(self, aspect: str, saved_resolution: str) -> None:
        self._resolution_combo.clear()
        for label, value in _ASPECT_RESOLUTIONS.get(aspect, _ASPECT_RESOLUTIONS["landscape"]):
            self._resolution_combo.addItem(label, value)
        _set_combo(self._resolution_combo, saved_resolution)
        if self._resolution_combo.currentIndex() < 0:
            self._resolution_combo.setCurrentIndex(0)

    def _on_aspect_changed(self) -> None:
        aspect = self._aspect_combo.currentData()
        current_res = self._resolution_combo.currentData() or ""
        self._populate_resolution_combo(aspect, current_res)
        # If the previous resolution doesn't exist in the new aspect, default to first
        if self._resolution_combo.currentIndex() < 0:
            self._resolution_combo.setCurrentIndex(0)

    def _build_photos_tab(self) -> QWidget:
        tab, layout = _make_tab()

        group = QGroupBox("Photo overlay")
        form = QFormLayout(group)

        self._transition_combo = QComboBox()
        self._transition_combo.addItem("Fade (cross-dissolve)", "fade")
        self._transition_combo.addItem("Cut (hard edit)", "cut")
        _set_combo(self._transition_combo,
                   self._settings.value(KEY_PHOTO_TRANSITION, DEFAULTS[KEY_PHOTO_TRANSITION]))
        form.addRow("Transition:", self._transition_combo)

        self._fill_combo = QComboBox()
        self._fill_combo.addItem("Blurred fill", "blurred")
        self._fill_combo.addItem("Black bars", "black")
        _set_combo(self._fill_combo,
                   self._settings.value(KEY_PHOTO_FILL, DEFAULTS[KEY_PHOTO_FILL]))
        form.addRow("Letterbox fill:", self._fill_combo)

        self._fade_dur_spin = QDoubleSpinBox()
        self._fade_dur_spin.setRange(0.1, 2.0)
        self._fade_dur_spin.setSingleStep(0.1)
        self._fade_dur_spin.setDecimals(1)
        self._fade_dur_spin.setSuffix(" s")
        self._fade_dur_spin.setValue(
            float(str(self._settings.value(KEY_PHOTO_FADE_DURATION,
                                       DEFAULTS[KEY_PHOTO_FADE_DURATION])))
        )
        form.addRow("Fade duration:", self._fade_dur_spin)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _build_map_tab(self) -> QWidget:
        tab, layout = _make_tab()

        group = QGroupBox("Satellite imagery")
        form = QFormLayout(group)

        self._provider_combo = QComboBox()
        for p in PROVIDERS:
            self._provider_combo.addItem(p.label, p.id)
        _set_combo(self._provider_combo,
                   self._settings.value(KEY_IMAGERY_PROVIDER, DEFAULTS[KEY_IMAGERY_PROVIDER]))
        form.addRow("Provider:", self._provider_combo)

        self._imagery_quality_combo = QComboBox()
        self._imagery_quality_combo.addItem("Standard  (zoom 13, ~19 m/pixel)", "standard")
        self._imagery_quality_combo.addItem("High      (zoom 15,  ~5 m/pixel)", "high")
        self._imagery_quality_combo.addItem("Very High (zoom 17, ~1.2 m/pixel, slow for large areas)", "very_high")
        _set_combo(self._imagery_quality_combo,
                   self._settings.value(KEY_IMAGERY_QUALITY, DEFAULTS[KEY_IMAGERY_QUALITY]))
        form.addRow("Detail level:", self._imagery_quality_combo)

        self._imagery_fetch_mode_combo = QComboBox()
        self._imagery_fetch_mode_combo.addItem(
            "Prefetch all  (download upfront, faster scene build)", "prefetch"
        )
        self._imagery_fetch_mode_combo.addItem(
            "On-demand  (download per terrain tile, lower peak RAM)", "on_demand"
        )
        self._imagery_fetch_mode_combo.setToolTip(
            "Prefetch: all satellite tiles for the route are downloaded before the scene is built.\n"
            "On-demand: tiles are fetched lazily as each terrain tile is processed — no upfront\n"
            "wait, lower peak memory, but individual scene tiles take slightly longer."
        )
        _set_combo(self._imagery_fetch_mode_combo,
                   self._settings.value(KEY_IMAGERY_FETCH_MODE, DEFAULTS[KEY_IMAGERY_FETCH_MODE]))
        form.addRow("Fetch mode:", self._imagery_fetch_mode_combo)

        self._api_key_label = QLabel("API key:")
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("Paste your API key here…")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        saved_key = str(self._settings.value(KEY_IMAGERY_API_KEY, ""))
        self._api_key_edit.setText(saved_key)
        form.addRow(self._api_key_label, self._api_key_edit)

        self._custom_url_label = QLabel("XYZ URL template:")
        self._custom_url_edit = QLineEdit()
        self._custom_url_edit.setPlaceholderText("https://…/{z}/{x}/{y}.png")
        self._custom_url_edit.setText(
            str(self._settings.value(KEY_IMAGERY_CUSTOM_URL, ""))
        )
        form.addRow(self._custom_url_label, self._custom_url_edit)

        layout.addWidget(group)

        # -- Temp & cache group --
        cache_group = QGroupBox("Temp & cache")
        cache_form = QFormLayout(cache_group)

        self._cache_custom_dir_check = QCheckBox("Use custom directory for temporary files")
        self._cache_custom_dir_check.setToolTip(
            "By default GeoReel writes temporary tile caches, scene files, rendered\n"
            "frames, etc. to the system temp directory (/tmp on Linux/macOS).\n"
            "Enable this to redirect all temp output to a directory you control\n"
            "(useful when /tmp is a RAM-disk or has limited space)."
        )
        use_custom = self._settings.value(KEY_CACHE_USE_CUSTOM_DIR, False)
        self._cache_custom_dir_check.setChecked(bool(use_custom) and use_custom != "false")
        cache_form.addRow(self._cache_custom_dir_check)

        dir_row = QWidget()
        dir_layout = QHBoxLayout(dir_row)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(6)
        self._cache_dir_edit = QLineEdit()
        self._cache_dir_edit.setPlaceholderText("e.g. /mnt/fast-disk/georeel-cache")
        self._cache_dir_edit.setReadOnly(True)
        self._cache_dir_edit.setText(str(self._settings.value(KEY_CACHE_BASE_DIR, "")))
        self._cache_dir_browse_btn = QPushButton("Browse…")
        self._cache_dir_browse_btn.setFixedWidth(80)
        self._cache_dir_browse_btn.clicked.connect(self._browse_cache_dir)
        dir_layout.addWidget(self._cache_dir_edit)
        dir_layout.addWidget(self._cache_dir_browse_btn)
        cache_form.addRow("Directory:", dir_row)

        # Sync enabled state with checkbox
        self._cache_custom_dir_check.toggled.connect(self._on_cache_custom_dir_toggled)
        self._on_cache_custom_dir_toggled(self._cache_custom_dir_check.isChecked())

        layout.addWidget(cache_group)
        layout.addStretch()

        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._on_provider_changed()   # set initial visibility

        return tab

    def _build_pins_tab(self) -> QWidget:
        tab, layout = _make_tab()

        # -- Track marker --
        marker_group = QGroupBox("Track marker")
        marker_form = QFormLayout(marker_group)

        self._marker_color_name = str(self._settings.value(KEY_MARKER_COLOR,        DEFAULTS[KEY_MARKER_COLOR]))
        self._marker_custom_color = str(self._settings.value(KEY_MARKER_CUSTOM_COLOR, DEFAULTS[KEY_MARKER_CUSTOM_COLOR]))

        marker_swatch_row = QWidget()
        marker_swatch_layout = QHBoxLayout(marker_swatch_row)
        marker_swatch_layout.setContentsMargins(0, 0, 0, 0)
        marker_swatch_layout.setSpacing(6)
        self._marker_swatch = QLabel()
        self._marker_swatch.setFixedSize(24, 24)
        self._marker_swatch.setAutoFillBackground(True)
        self._marker_color_label = QLabel()
        marker_change_btn = QPushButton("Change…")
        marker_change_btn.setFixedWidth(80)
        marker_change_btn.clicked.connect(self._change_marker_color)
        marker_swatch_layout.addWidget(self._marker_swatch)
        marker_swatch_layout.addWidget(self._marker_color_label)
        marker_swatch_layout.addWidget(marker_change_btn)
        marker_swatch_layout.addStretch()
        marker_form.addRow("Marker color:", marker_swatch_row)

        layout.addWidget(marker_group)

        # -- Photo waypoint pins --
        group = QGroupBox("Photo waypoint pins")
        form = QFormLayout(group)

        saved_name = str(self._settings.value(KEY_PIN_COLOR,        DEFAULTS[KEY_PIN_COLOR]))
        saved_custom = str(self._settings.value(KEY_PIN_CUSTOM_COLOR, DEFAULTS[KEY_PIN_CUSTOM_COLOR]))

        self._pin_color_name   = saved_name
        self._pin_custom_color = saved_custom

        # One row: swatch + resolved name + "Change…" button
        swatch_row = QWidget()
        swatch_layout = QHBoxLayout(swatch_row)
        swatch_layout.setContentsMargins(0, 0, 0, 0)
        swatch_layout.setSpacing(6)

        self._pin_swatch = QLabel()
        self._pin_swatch.setFixedSize(24, 24)
        self._pin_swatch.setAutoFillBackground(True)

        self._pin_color_label = QLabel()

        change_btn = QPushButton("Change…")
        change_btn.setFixedWidth(80)
        change_btn.clicked.connect(self._change_pin_color)

        swatch_layout.addWidget(self._pin_swatch)
        swatch_layout.addWidget(self._pin_color_label)
        swatch_layout.addWidget(change_btn)
        swatch_layout.addStretch()
        form.addRow("Pin color:", swatch_row)

        layout.addWidget(group)
        layout.addStretch()

        self._refresh_marker_swatch()
        self._refresh_pin_swatch()
        return tab

    def _refresh_marker_swatch(self) -> None:
        name = self._marker_color_name
        if name == "custom":
            hex_color = self._marker_custom_color
            label_text = f"Custom  {hex_color.upper()}"
        else:
            hex_color = get_color_hex(name, self._marker_custom_color)
            label_text = name
        _set_swatch(self._marker_swatch, hex_color)
        self._marker_color_label.setText(label_text)

    def _change_marker_color(self) -> None:
        dlg = ColorPickerDialog(
            current_name=self._marker_color_name,
            current_custom_hex=self._marker_custom_color,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._marker_color_name   = dlg.selected_name()
            self._marker_custom_color = dlg.custom_hex()
            self._refresh_marker_swatch()

    def _refresh_pin_swatch(self) -> None:
        name = self._pin_color_name
        if name == "custom":
            hex_color = self._pin_custom_color
            label_text = f"Custom  {hex_color.upper()}"
        else:
            hex_color = get_color_hex(name, self._pin_custom_color)
            label_text = name
        _set_swatch(self._pin_swatch, hex_color)
        self._pin_color_label.setText(label_text)

    def _change_pin_color(self) -> None:
        dlg = ColorPickerDialog(
            current_name=self._pin_color_name,
            current_custom_hex=self._pin_custom_color,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._pin_color_name   = dlg.selected_name()
            self._pin_custom_color = dlg.custom_hex()
            self._refresh_pin_swatch()

    def _on_cache_custom_dir_toggled(self, checked: bool) -> None:
        self._cache_dir_edit.setEnabled(checked)
        self._cache_dir_browse_btn.setEnabled(checked)

    def _browse_cache_dir(self) -> None:
        current = self._cache_dir_edit.text().strip() or ""
        chosen = QFileDialog.getExistingDirectory(
            self, "Select temp/cache directory", current
        )
        if chosen:
            self._cache_dir_edit.setText(chosen)

    def _on_provider_changed(self) -> None:
        from georeel.core.satellite.providers import get_provider
        p = get_provider(self._provider_combo.currentData() or "esri_world")
        show_key = p.requires_key
        show_url = p.id == "custom"
        for w in (self._api_key_label, self._api_key_edit):
            w.setVisible(show_key)
        for w in (self._custom_url_label, self._custom_url_edit):
            w.setVisible(show_url)
        if show_key:
            self._api_key_label.setText(p.key_label or "API key:")

    def _build_output_tab(self) -> QWidget:
        tab, layout = _make_tab()

        # Detect available encoders once at dialog open (fast, < 1 s)
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        self._available_encoders = detect_available_encoders(ffmpeg)
        self._ffmpeg_path = ffmpeg

        group = QGroupBox("Video output")
        form = QFormLayout(group)

        # Container
        self._container_combo = QComboBox()
        self._container_combo.addItem("Matroska (.mkv)", "mkv")
        self._container_combo.addItem("MPEG-4 (.mp4)",   "mp4")
        _set_combo(self._container_combo,
                   self._settings.value(KEY_CONTAINER, DEFAULTS[KEY_CONTAINER]))
        form.addRow("Container:", self._container_combo)

        # Codec
        self._out_codec_combo = QComboBox()
        self._out_codec_combo.addItem("H.264 (AVC)",  "h264")
        self._out_codec_combo.addItem("H.265 (HEVC)", "h265")
        self._out_codec_combo.addItem("AV1",           "av1")
        _set_combo(self._out_codec_combo,
                   self._settings.value(KEY_CODEC, DEFAULTS[KEY_CODEC]))
        form.addRow("Codec:", self._out_codec_combo)

        # Encoder (dynamic, populated by _refresh_encoders)
        self._encoder_combo = QComboBox()
        form.addRow("Encoder:", self._encoder_combo)

        # CQ
        self._out_cq_spin = QSpinBox()
        self._out_cq_spin.setRange(0, 63)
        form.addRow("Quality (CQ/CRF):", self._out_cq_spin)

        # Preset (dynamic)
        self._preset_combo = QComboBox()
        form.addRow("Preset:", self._preset_combo)

        # Suggestion label
        self._suggestion_label = QLabel()
        self._suggestion_label.setWordWrap(True)
        form.addRow("Suggestion:", self._suggestion_label)

        # Apply suggestion button
        self._apply_btn = QPushButton("Apply suggestion")
        self._apply_btn.clicked.connect(self._apply_suggestion)
        form.addRow("", self._apply_btn)

        layout.addWidget(group)

        # Detection status — shown only when something looks wrong
        self._detect_label = QLabel()
        self._detect_label.setWordWrap(True)
        if not self._available_encoders:
            self._detect_label.setText(
                f"⚠ Encoder detection returned no results.\n"
                f"FFmpeg path: {self._ffmpeg_path}\n"
                "Only software fallback encoders are listed."
            )
        layout.addWidget(self._detect_label)
        layout.addStretch()

        # Populate encoder combo for the current codec (blocks signals during init)
        self._refreshing = False
        self._refresh_encoders(
            saved_encoder = str(self._settings.value(KEY_ENCODER, DEFAULTS[KEY_ENCODER])),
            saved_cq=int(str(self._settings.value(KEY_OUTPUT_CQ, DEFAULTS[KEY_OUTPUT_CQ]))),
            saved_preset = str(self._settings.value(KEY_OUTPUT_PRESET, DEFAULTS[KEY_OUTPUT_PRESET])),
        )

        # Connect signals after init to avoid spurious resets
        self._out_codec_combo.currentIndexChanged.connect(self._on_codec_changed)
        self._encoder_combo.currentIndexChanged.connect(self._on_encoder_changed)

        return tab

    # ------------------------------------------------------------------
    # Output tab dynamics
    # ------------------------------------------------------------------

    def _refresh_encoders(
        self,
        saved_encoder: str = "",
        saved_cq: int = -1,
        saved_preset: str = "",
    ) -> None:
        """Rebuild encoder combo for the currently selected codec."""
        self._refreshing = True
        codec = self._out_codec_combo.currentData() or "h265"
        encoders = encoders_for_codec(codec, self._available_encoders)

        # Always include at least the software fallback even if not detected
        fallbacks = {"h264": "libx264", "h265": "libx265", "av1": "libsvtav1"}
        sw_name = fallbacks.get(codec, "libx265")
        fallback = get_encoder(sw_name)
        if fallback and sw_name not in {e.name for e in encoders}:
            encoders.append(fallback)

        self._encoder_combo.clear()
        for enc in encoders:
            self._encoder_combo.addItem(enc.label, enc.name)

        # Select saved encoder if available, else first in list
        idx = self._encoder_combo.findData(saved_encoder)
        self._encoder_combo.setCurrentIndex(max(idx, 0))

        # Populate preset/cq for the selected encoder
        enc = get_encoder(self._encoder_combo.currentData() or "")
        if enc:
            self._populate_encoder_details(enc, saved_cq, saved_preset)

        self._refreshing = False

    def _populate_encoder_details(
        self,
        enc: EncoderConfig,
        cq: int = -1,
        preset: str = "",
    ) -> None:
        """Update CQ range, preset combo, and suggestion for *enc*."""
        self._out_cq_spin.blockSignals(True)
        self._out_cq_spin.setRange(*enc.cq_range)
        self._out_cq_spin.setValue(cq if enc.cq_range[0] <= cq <= enc.cq_range[1]
                                   else enc.default_cq)
        self._out_cq_spin.blockSignals(False)

        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for value, label in enc.presets:
            self._preset_combo.addItem(label, value)
        idx = self._preset_combo.findData(preset if preset else enc.default_preset)
        self._preset_combo.setCurrentIndex(max(idx, 0))
        self._preset_combo.setEnabled(bool(enc.presets))
        self._preset_combo.blockSignals(False)

        self._suggestion_label.setText(enc.suggestion)

    def _on_codec_changed(self) -> None:
        if not self._refreshing:
            self._refresh_encoders()

    def _on_encoder_changed(self) -> None:
        if self._refreshing:
            return
        enc = get_encoder(self._encoder_combo.currentData() or "")
        if enc:
            self._populate_encoder_details(enc)

    def _apply_suggestion(self) -> None:
        enc = get_encoder(self._encoder_combo.currentData() or "")
        if enc:
            self._populate_encoder_details(enc)

    # ------------------------------------------------------------------

    def _save_and_accept(self):
        self._settings.setValue(KEY_FPS,                 self._fps_combo.currentData())
        self._settings.setValue(KEY_PATH_SMOOTHING,      self._path_combo.currentData())
        self._settings.setValue(KEY_HEIGHT_MODE,         self._height_combo.currentData())
        self._settings.setValue(KEY_HEIGHT_OFFSET,        self._height_spin.value())
        self._settings.setValue(KEY_ORIENTATION,          self._orient_combo.currentData())
        self._settings.setValue(KEY_TILT_DEG,             self._tilt_spin.value())
        self._settings.setValue(KEY_FRUSTUM_MARGIN_KM,    self._frustum_spin.value())
        self._settings.setValue(KEY_TANGENT_LOOKAHEAD_S,  self._lookahead_spin.value())
        self._settings.setValue(KEY_TANGENT_WEIGHT,       self._tangent_weight_combo.currentData())
        self._settings.setValue(KEY_PHOTO_PAUSE_MODE,    self._pause_combo.currentData())
        self._settings.setValue(KEY_PHOTO_PAUSE_DURATION, self._pause_spin.value())
        self._settings.setValue(KEY_ENGINE,              self._engine_combo.currentData())
        self._settings.setValue(KEY_ASPECT_RATIO,        self._aspect_combo.currentData())
        self._settings.setValue(KEY_RESOLUTION,          self._resolution_combo.currentData())
        self._settings.setValue(KEY_QUALITY,             self._quality_combo.currentData())
        self._settings.setValue(KEY_RENDER_SEGMENTS,     self._segments_spin.value())
        self._settings.setValue(KEY_PHOTO_TRANSITION,    self._transition_combo.currentData())
        self._settings.setValue(KEY_PHOTO_FILL,          self._fill_combo.currentData())
        self._settings.setValue(KEY_PHOTO_FADE_DURATION, self._fade_dur_spin.value())
        self._settings.setValue(KEY_CONTAINER,  self._container_combo.currentData())
        self._settings.setValue(KEY_CODEC,       self._out_codec_combo.currentData())
        self._settings.setValue(KEY_ENCODER,     self._encoder_combo.currentData())
        self._settings.setValue(KEY_OUTPUT_CQ,   self._out_cq_spin.value())
        self._settings.setValue(KEY_OUTPUT_PRESET, self._preset_combo.currentData() or "")
        self._settings.setValue(KEY_IMAGERY_PROVIDER,    self._provider_combo.currentData())
        self._settings.setValue(KEY_IMAGERY_QUALITY,     self._imagery_quality_combo.currentData())
        self._settings.setValue(KEY_IMAGERY_FETCH_MODE,  self._imagery_fetch_mode_combo.currentData())
        self._settings.setValue(KEY_IMAGERY_API_KEY,     self._api_key_edit.text().strip())
        self._settings.setValue(KEY_IMAGERY_CUSTOM_URL,  self._custom_url_edit.text().strip())
        self._settings.setValue(KEY_CACHE_USE_CUSTOM_DIR, self._cache_custom_dir_check.isChecked())
        self._settings.setValue(KEY_CACHE_BASE_DIR,       self._cache_dir_edit.text().strip())
        self._settings.setValue(KEY_MARKER_COLOR,        self._marker_color_name)
        self._settings.setValue(KEY_MARKER_CUSTOM_COLOR, self._marker_custom_color)
        self._settings.setValue(KEY_PIN_COLOR,           self._pin_color_name)
        self._settings.setValue(KEY_PIN_CUSTOM_COLOR,    self._pin_custom_color)
        self.accept()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_tab() -> tuple[QWidget, QVBoxLayout]:
    """Return a (widget, layout) pair for a tab page."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setSpacing(10)
    layout.setContentsMargins(12, 12, 12, 12)
    return tab, layout


def _set_combo(combo: QComboBox, value: object) -> None:  # type: ignore[misc]
    idx = combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)


def _set_swatch(label: QLabel, hex_color: str) -> None:
    palette = label.palette()
    palette.setColor(label.backgroundRole(), QColor(hex_color))
    label.setPalette(palette)
