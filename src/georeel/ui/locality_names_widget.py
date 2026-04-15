# pyright: reportUninitializedInstanceVariable=false
"""Locality names settings widget — Nominatim reverse geocoding overlay."""

from typing import Any, TypeVar, cast

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor

from georeel.core.nominatim_client import get_container_runtime

_T = TypeVar("_T")

_KEY_ENABLED        = "locality_names/enabled"
_KEY_SERVICE        = "locality_names/service"
_KEY_CUSTOM_URL     = "locality_names/custom_url"
_KEY_DOCKER_PBF_URL = "locality_names/docker_pbf_url"
_KEY_DOCKER_PORT    = "locality_names/docker_port"
_KEY_DOCKER_KEEP    = "locality_names/docker_keep"
_KEY_CHECK_EVERY_S  = "locality_names/check_every_s"
_KEY_DETAIL_LEVEL   = "locality_names/detail_level"
_KEY_POSITION       = "locality_names/position"
_KEY_DURATION       = "locality_names/duration"
_KEY_TEXT_COLOR     = "locality_names/text_color"
_KEY_SHADOW         = "locality_names/shadow"

_DETAIL_LEVELS = [
    ("Village", "village"),
    ("Town",    "town"),
    ("City",    "city"),
    ("State",   "state"),
    ("Country", "country"),
]

_POSITIONS = [
    ("Top left",     "top-left"),
    ("Top",          "top"),
    ("Top right",    "top-right"),
    ("Center left",  "center-left"),
    ("Center",       "center"),
    ("Center right", "center-right"),
    ("Bottom left",  "bottom-left"),
    ("Bottom",       "bottom"),
    ("Bottom right", "bottom-right"),
]


class LocalityNamesWidget(QWidget):
    """Provides locality names overlay settings backed by QSettings."""

    def _qsv(self, key: str, default: _T) -> _T:
        """Type-safe QSettings.value() wrapper — infers return type from default."""
        return cast(_T, self._settings.value(key, default, type=type(default)))

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._text_color: str = self._qsv(_KEY_TEXT_COLOR, "#ffffff")
        self._build_group()

    # ------------------------------------------------------------------
    # Tab widget factory
    # ------------------------------------------------------------------

    def locality_tab_widget(self) -> QWidget:
        """Return a widget containing the locality names group."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._group)
        layout.addStretch()
        return w

    # ------------------------------------------------------------------
    # Group builder
    # ------------------------------------------------------------------

    def _build_group(self) -> None:
        group = QGroupBox("Locality names")
        group.setCheckable(True)
        group.setChecked(self._qsv(_KEY_ENABLED, False))
        group.toggled.connect(lambda v: self._settings.setValue(_KEY_ENABLED, v))
        self._group = group

        outer = QVBoxLayout(group)
        outer.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)
        outer.addLayout(form)

        # ── Service selection ─────────────────────────────────────────
        service_row = QVBoxLayout()

        self._osm_radio = QRadioButton("OSM (official Nominatim)")
        self._osm_radio.setToolTip(
            "Use the public OSM Nominatim service at nominatim.openstreetmap.org.\n"
            "Subject to the OSM usage policy (max 1 request/second)."
        )

        docker_available = get_container_runtime() is not None
        self._docker_radio = QRadioButton("Docker / Podman (local)")
        self._docker_radio.setEnabled(docker_available)
        if not docker_available:
            self._docker_radio.setToolTip(
                "Docker and Podman are not available on this system.\n"
                "Install Docker or Podman to use a local Nominatim container."
            )
        else:
            self._docker_radio.setToolTip(
                "Run a local Nominatim container using Docker or Podman.\n"
                "Requires downloading a PBF extract for your region."
            )

        self._custom_radio = QRadioButton("Custom URL")
        self._custom_radio.setToolTip(
            "Use a custom Nominatim-compatible endpoint (e.g. a private server)."
        )

        self._service_group = QButtonGroup(self)
        self._service_group.addButton(self._osm_radio)
        self._service_group.addButton(self._docker_radio)
        self._service_group.addButton(self._custom_radio)

        saved_service = self._qsv(_KEY_SERVICE, "osm")
        if saved_service == "docker" and docker_available:
            self._docker_radio.setChecked(True)
        elif saved_service == "custom":
            self._custom_radio.setChecked(True)
        else:
            self._osm_radio.setChecked(True)

        service_row.addWidget(self._osm_radio)
        service_row.addWidget(self._docker_radio)
        service_row.addWidget(self._custom_radio)
        form.addRow("Service:", service_row)

        # ── Docker section ────────────────────────────────────────────
        self._docker_widget = QWidget()
        docker_form = QFormLayout(self._docker_widget)
        docker_form.setContentsMargins(20, 0, 0, 0)
        docker_form.setSpacing(6)

        self._docker_pbf_edit = QLineEdit()
        self._docker_pbf_edit.setPlaceholderText(
            "https://download.geofabrik.de/europe/…-latest.osm.pbf"
        )
        self._docker_pbf_edit.setText(self._qsv(_KEY_DOCKER_PBF_URL, ""))
        self._docker_pbf_edit.textChanged.connect(
            lambda v: self._settings.setValue(_KEY_DOCKER_PBF_URL, v)
        )
        docker_form.addRow("PBF URL:", self._docker_pbf_edit)

        self._docker_port_spin = QSpinBox()
        self._docker_port_spin.setRange(1024, 65535)
        self._docker_port_spin.setValue(self._qsv(_KEY_DOCKER_PORT, 8080))
        self._docker_port_spin.setReadOnly(True)
        self._docker_port_spin.setToolTip(
            "Actual host port assigned to the container after Start.\n"
            "The OS picks a free port automatically."
        )
        self._docker_port_spin.valueChanged.connect(
            lambda v: self._settings.setValue(_KEY_DOCKER_PORT, v)
        )
        docker_form.addRow("Port (auto):", self._docker_port_spin)

        self._docker_keep_chk = QCheckBox("Keep volume between runs")
        self._docker_keep_chk.setChecked(self._qsv(_KEY_DOCKER_KEEP, False))
        self._docker_keep_chk.setToolTip(
            "Preserve the PostgreSQL data volume so the PBF import survives\n"
            "container restarts. Requires more disk space."
        )
        self._docker_keep_chk.toggled.connect(
            lambda v: self._settings.setValue(_KEY_DOCKER_KEEP, v)
        )
        docker_form.addRow("", self._docker_keep_chk)

        docker_btn_row = QHBoxLayout()
        self._docker_start_btn = QPushButton("Start container")
        self._docker_stop_btn  = QPushButton("Stop container")
        self._docker_clean_btn = QPushButton("Clean volumes")
        docker_btn_row.addWidget(self._docker_start_btn)
        docker_btn_row.addWidget(self._docker_stop_btn)
        docker_btn_row.addWidget(self._docker_clean_btn)
        docker_btn_row.addStretch()
        docker_form.addRow("", docker_btn_row)

        self._docker_status_lbl = QLabel()
        self._docker_status_lbl.setWordWrap(True)
        docker_form.addRow("Status:", self._docker_status_lbl)

        self._docker_start_btn.clicked.connect(self._on_docker_start)
        self._docker_stop_btn.clicked.connect(self._on_docker_stop)
        self._docker_clean_btn.clicked.connect(self._on_docker_clean)

        outer.addWidget(self._docker_widget)

        # ── Custom URL section ────────────────────────────────────────
        self._custom_widget = QWidget()
        custom_row = QHBoxLayout(self._custom_widget)
        custom_row.setContentsMargins(0, 0, 0, 0)
        self._custom_url_edit = QLineEdit()
        self._custom_url_edit.setPlaceholderText("http://localhost:8080")
        self._custom_url_edit.setText(self._qsv(_KEY_CUSTOM_URL, ""))
        self._custom_url_edit.textChanged.connect(
            lambda v: self._settings.setValue(_KEY_CUSTOM_URL, v)
        )
        custom_row.addWidget(QLabel("URL:"))
        custom_row.addWidget(self._custom_url_edit)

        outer.addWidget(self._custom_widget)

        # ── Check interval ────────────────────────────────────────────
        form2 = QFormLayout()
        form2.setSpacing(8)
        outer.addLayout(form2)

        self._check_every_spin = QDoubleSpinBox()
        self._check_every_spin.setRange(1.0, 3600.0)
        self._check_every_spin.setSingleStep(10.0)
        self._check_every_spin.setDecimals(1)
        self._check_every_spin.setSuffix(" s")
        self._check_every_spin.setValue(self._qsv(_KEY_CHECK_EVERY_S, 60.0))
        self._check_every_spin.setToolTip(
            "How often (in track time) to query Nominatim for the current location.\n"
            "Lower values give finer-grained name changes but more API calls."
        )
        self._check_every_spin.valueChanged.connect(
            lambda v: self._settings.setValue(_KEY_CHECK_EVERY_S, v)
        )
        form2.addRow("Check every:", self._check_every_spin)

        # ── Detail level ──────────────────────────────────────────────
        self._detail_combo = QComboBox()
        saved_detail = self._qsv(_KEY_DETAIL_LEVEL, "city")
        for label, value in _DETAIL_LEVELS:
            self._detail_combo.addItem(label, value)
            if value == saved_detail:
                self._detail_combo.setCurrentIndex(self._detail_combo.count() - 1)
        self._detail_combo.setToolTip(
            "Nominatim zoom level controlling the granularity of the returned name."
        )
        self._detail_combo.currentIndexChanged.connect(
            lambda _: self._settings.setValue(
                _KEY_DETAIL_LEVEL, self._detail_combo.currentData()
            )
        )
        form2.addRow("Detail level:", self._detail_combo)

        # ── Position ──────────────────────────────────────────────────
        self._position_combo = QComboBox()
        saved_pos = self._qsv(_KEY_POSITION, "bottom-right")
        for label, value in _POSITIONS:
            self._position_combo.addItem(label, value)
            if value == saved_pos:
                self._position_combo.setCurrentIndex(self._position_combo.count() - 1)
        self._position_combo.setToolTip("Where on the frame to render the locality name.")
        self._position_combo.currentIndexChanged.connect(
            lambda _: self._settings.setValue(
                _KEY_POSITION, self._position_combo.currentData()
            )
        )
        form2.addRow("Position:", self._position_combo)

        # ── Duration ──────────────────────────────────────────────────
        self._duration_spin = QDoubleSpinBox()
        self._duration_spin.setRange(0.5, 60.0)
        self._duration_spin.setSingleStep(0.5)
        self._duration_spin.setDecimals(1)
        self._duration_spin.setSuffix(" s")
        self._duration_spin.setValue(self._qsv(_KEY_DURATION, 5.0))
        self._duration_spin.setToolTip(
            "How long each locality name label stays visible (with 1 s fade each side)."
        )
        self._duration_spin.valueChanged.connect(
            lambda v: self._settings.setValue(_KEY_DURATION, v)
        )
        form2.addRow("Duration:", self._duration_spin)

        # ── Text color + shadow ───────────────────────────────────────
        color_row = QHBoxLayout()
        self._color_btn = QPushButton()
        self._color_btn.setFixedWidth(80)
        self._update_color_btn(self._color_btn, self._text_color)
        self._shadow_chk = QCheckBox("Shadow")
        self._shadow_chk.setChecked(self._qsv(_KEY_SHADOW, True))
        self._shadow_chk.toggled.connect(
            lambda v: self._settings.setValue(_KEY_SHADOW, v)
        )
        color_row.addWidget(self._color_btn)
        color_row.addWidget(self._shadow_chk)
        color_row.addStretch()
        form2.addRow("Text color:", color_row)

        self._color_btn.clicked.connect(self._pick_color)

        # ── Service visibility logic ──────────────────────────────────
        def _update_service_widgets() -> None:
            if self._docker_radio.isChecked():
                svc = "docker"
            elif self._custom_radio.isChecked():
                svc = "custom"
            else:
                svc = "osm"
            self._settings.setValue(_KEY_SERVICE, svc)
            self._docker_widget.setVisible(svc == "docker")
            self._custom_widget.setVisible(svc == "custom")

        _update_service_widgets()
        self._osm_radio.toggled.connect(lambda _: _update_service_widgets())
        self._docker_radio.toggled.connect(lambda _: _update_service_widgets())
        self._custom_radio.toggled.connect(lambda _: _update_service_widgets())

    # ------------------------------------------------------------------
    # Docker button handlers
    # ------------------------------------------------------------------

    def _on_docker_start(self) -> None:
        from georeel.core.nominatim_client import start_nominatim_container
        pbf = self._docker_pbf_edit.text().strip()
        keep = self._docker_keep_chk.isChecked()
        ok, msg, actual_port = start_nominatim_container(pbf, keep_volume=keep)
        if ok and actual_port:
            self._docker_port_spin.setValue(actual_port)
        self._docker_status_lbl.setText(msg)

    def _on_docker_stop(self) -> None:
        from georeel.core.nominatim_client import stop_nominatim_container
        ok, msg = stop_nominatim_container()
        self._docker_status_lbl.setText(msg)

    def _on_docker_clean(self) -> None:
        from georeel.core.nominatim_client import clean_nominatim_volumes
        ok, msg = clean_nominatim_volumes()
        self._docker_status_lbl.setText(msg)

    # ------------------------------------------------------------------
    # Color helpers
    # ------------------------------------------------------------------

    def _update_color_btn(self, btn: QPushButton, color_hex: str) -> None:
        c = QColor(color_hex)
        luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        text_color = "#000000" if luma > 128 else "#ffffff"
        btn.setText(color_hex)
        btn.setStyleSheet(
            f"background-color: {color_hex}; color: {text_color}; border: 1px solid #888;"
        )

    def _pick_color(self) -> None:
        current = QColor(self._text_color)
        chosen = QColorDialog.getColor(current, self, "Locality name text color")
        if chosen.isValid():
            self._text_color = chosen.name()
            self._settings.setValue(_KEY_TEXT_COLOR, self._text_color)
            self._update_color_btn(self._color_btn, self._text_color)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-read all controls from the current QSettings values."""
        def _sv(key: str, default: _T, t: type[_T] | None = None) -> _T:
            return cast(Any, self._settings.value(
                key, default, type=t or type(default)
            ))

        self._group.setChecked(_sv(_KEY_ENABLED, False, bool))

        saved_service = _sv(_KEY_SERVICE, "osm")
        if saved_service == "docker" and self._docker_radio.isEnabled():
            self._docker_radio.setChecked(True)
        elif saved_service == "custom":
            self._custom_radio.setChecked(True)
        else:
            self._osm_radio.setChecked(True)

        self._docker_pbf_edit.setText(_sv(_KEY_DOCKER_PBF_URL, ""))
        self._docker_port_spin.setValue(int(_sv(_KEY_DOCKER_PORT, 8080)))
        self._docker_keep_chk.setChecked(_sv(_KEY_DOCKER_KEEP, False, bool))
        self._custom_url_edit.setText(_sv(_KEY_CUSTOM_URL, ""))
        self._check_every_spin.setValue(float(_sv(_KEY_CHECK_EVERY_S, 60.0)))

        saved_detail = _sv(_KEY_DETAIL_LEVEL, "city")
        for i in range(self._detail_combo.count()):
            if self._detail_combo.itemData(i) == saved_detail:
                self._detail_combo.setCurrentIndex(i)
                break

        saved_pos = _sv(_KEY_POSITION, "bottom-right")
        for i in range(self._position_combo.count()):
            if self._position_combo.itemData(i) == saved_pos:
                self._position_combo.setCurrentIndex(i)
                break

        self._duration_spin.setValue(float(_sv(_KEY_DURATION, 5.0)))

        self._text_color = _sv(_KEY_TEXT_COLOR, "#ffffff")
        self._update_color_btn(self._color_btn, self._text_color)
        self._shadow_chk.setChecked(_sv(_KEY_SHADOW, True, bool))

    def get_settings(self) -> dict[str, Any]:
        """Return current locality names settings as a flat dict."""
        if self._docker_radio.isChecked():
            svc = "docker"
        elif self._custom_radio.isChecked():
            svc = "custom"
        else:
            svc = "osm"

        return {
            _KEY_ENABLED:        self._group.isChecked(),
            _KEY_SERVICE:        svc,
            _KEY_CUSTOM_URL:     self._custom_url_edit.text(),
            _KEY_DOCKER_PBF_URL: self._docker_pbf_edit.text(),
            _KEY_DOCKER_PORT:    self._docker_port_spin.value(),
            _KEY_DOCKER_KEEP:    self._docker_keep_chk.isChecked(),
            _KEY_CHECK_EVERY_S:  self._check_every_spin.value(),
            _KEY_DETAIL_LEVEL:   self._detail_combo.currentData(),
            _KEY_POSITION:       self._position_combo.currentData(),
            _KEY_DURATION:       self._duration_spin.value(),
            _KEY_TEXT_COLOR:     self._text_color,
            _KEY_SHADOW:         self._shadow_chk.isChecked(),
        }
