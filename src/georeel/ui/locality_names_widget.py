# pyright: reportUninitializedInstanceVariable=false
"""Locality names settings widget — Nominatim reverse geocoding overlay."""

from datetime import datetime, timezone
from typing import Any, TypeVar, cast

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor

from georeel.core.nominatim_client import LocalityEntry

_T = TypeVar("_T")

_KEY_ENABLED          = "locality_names/enabled"
_KEY_SERVICE          = "locality_names/service"
_KEY_CUSTOM_URL       = "locality_names/custom_url"
_KEY_CHECK_EVERY_S    = "locality_names/check_every_s"
_KEY_DETAIL_LEVEL     = "locality_names/detail_level"
_KEY_POSITION         = "locality_names/position"
_KEY_DURATION         = "locality_names/duration"
_KEY_DURATION_FOREVER = "locality_names/duration_forever"
_KEY_TEXT_COLOR       = "locality_names/text_color"
_KEY_SHADOW           = "locality_names/shadow"

def _format_track_time(
    track_time_s: float,
    first_ts: datetime | None,
) -> str:
    """Format a track-time offset as a human-readable string.

    If the first trackpoint has a UTC timestamp, return the absolute UTC
    clock time (``HH:MM:SS UTC``).  Otherwise return an elapsed offset
    (``HH:MM:SS``).
    """
    from datetime import timedelta
    if first_ts is not None:
        abs_ts = first_ts.astimezone(timezone.utc) + timedelta(seconds=track_time_s)
        return abs_ts.strftime("%H:%M:%S UTC")
    total = int(track_time_s)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class _LocalityPreviewWorker(QThread):
    """Background worker that calls build_locality_timeline and emits results."""

    finished = Signal(object)   # list[LocalityEntry]
    error    = Signal(str)
    progress = Signal(int, int) # (current, total)

    def __init__(
        self,
        trackpoints: list[Any],
        total_frames: int,
        settings: dict[str, Any],
    ) -> None:
        super().__init__()
        self._trackpoints  = trackpoints
        self._total_frames = total_frames
        self._settings     = settings

    def run(self) -> None:
        try:
            from georeel.core.nominatim_client import build_locality_timeline
            result = build_locality_timeline(
                self._trackpoints,
                self._total_frames,
                self._settings,
                progress_cb=lambda cur, tot: self.progress.emit(cur, tot),
            )
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))




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

    # Emitted when the user triggers a preview but keyframes haven't been
    # computed yet.  main_window connects this to _calculate_keyframes().
    calculate_keyframes_requested = Signal()

    def _qsv(self, key: str, default: _T) -> _T:
        """Type-safe QSettings.value() wrapper — infers return type from default."""
        return cast(_T, self._settings.value(key, default, type=type(default)))

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._text_color: str = self._qsv(_KEY_TEXT_COLOR, "#ffffff")
        # Pipeline context — set by main_window after keyframes are computed.
        self._trackpoints: list[Any] = []
        self._total_frames: int = 0
        self._fps: int = 30
        self._preview_worker: _LocalityPreviewWorker | None = None
        # Set when preview was clicked but keyframes haven't been computed yet.
        self._preview_pending_after_keyframes: bool = False
        # Cached locality timeline — reused for final render to avoid re-querying Nominatim.
        # Invalidated when geocoding-relevant settings change or when the GPX track changes.
        self._cached_timeline: list[LocalityEntry] | None = None
        self._build_group()

    def set_pipeline_context(
        self,
        trackpoints: list[Any],
        total_frames: int,
        fps: int = 30,
    ) -> None:
        """Called by main_window once trackpoints and frame count are known."""
        self._trackpoints  = trackpoints
        self._total_frames = total_frames
        self._fps          = fps
        pending = self._preview_pending_after_keyframes
        if pending:
            self._preview_pending_after_keyframes = False
        self._update_preview_btn_state()
        if pending and trackpoints and total_frames > 0:
            self._run_preview_query()

    def notify_keyframe_calc_failed(self) -> None:
        """Called by main_window if keyframe calculation (triggered by preview) fails."""
        if self._preview_pending_after_keyframes:
            self._preview_pending_after_keyframes = False
            self._update_preview_btn_state()

    def _update_preview_btn_state(self) -> None:
        """Enable the preview button unless a query is in progress or custom URL is blank."""
        in_progress = (
            (self._preview_worker is not None and self._preview_worker.isRunning())
            or self._preview_pending_after_keyframes
        )
        custom_url_empty = (
            self._custom_radio.isChecked()
            and not self._custom_url_edit.text().strip()
        )
        self._preview_btn.setEnabled(not in_progress and not custom_url_empty)

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

        self._custom_radio = QRadioButton("Custom URL")
        self._custom_radio.setToolTip(
            "Use a custom Nominatim-compatible endpoint (e.g. a self-hosted server)."
        )

        self._service_group = QButtonGroup(self)
        self._service_group.addButton(self._osm_radio)
        self._service_group.addButton(self._custom_radio)

        saved_service = self._qsv(_KEY_SERVICE, "osm")
        if saved_service == "custom":
            self._custom_radio.setChecked(True)
        else:
            self._osm_radio.setChecked(True)

        service_row.addWidget(self._osm_radio)
        service_row.addWidget(self._custom_radio)
        form.addRow("Service:", service_row)

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
        duration_row = QHBoxLayout()
        duration_row.setContentsMargins(0, 0, 0, 0)
        duration_row.setSpacing(8)

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

        self._duration_forever_chk = QCheckBox("Forever")
        self._duration_forever_chk.setToolTip(
            "Keep each locality name visible until the next name appears\n"
            "(or until the end of the video), instead of hiding after the\n"
            "configured duration.  A cross-fade still occurs at transitions."
        )
        _forever_init = self._qsv(_KEY_DURATION_FOREVER, False)
        self._duration_forever_chk.setChecked(_forever_init)
        self._duration_spin.setEnabled(not _forever_init)

        def _on_forever_toggled(checked: bool) -> None:
            self._settings.setValue(_KEY_DURATION_FOREVER, checked)
            self._duration_spin.setEnabled(not checked)

        self._duration_forever_chk.toggled.connect(_on_forever_toggled)

        duration_row.addWidget(self._duration_spin)
        duration_row.addWidget(self._duration_forever_chk)
        duration_row.addStretch()
        form2.addRow("Duration:", duration_row)

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

        # ── Preview ───────────────────────────────────────────────────
        preview_row = QHBoxLayout()
        self._preview_btn = QPushButton("Compute locality names")
        self._preview_btn.setEnabled(False)
        self._preview_btn.setToolTip(
            "Query Nominatim with the current settings and populate the\n"
            "locality name timeline below (location, track time, frame range)."
        )
        self._preview_btn.clicked.connect(self._on_preview_clicked)
        self._preview_progress = QProgressBar()
        self._preview_progress.setRange(0, 0)
        self._preview_progress.setVisible(False)
        self._preview_progress.setMaximumWidth(120)
        preview_row.addWidget(self._preview_btn)
        preview_row.addWidget(self._preview_progress)
        preview_row.addStretch()
        outer.addLayout(preview_row)

        # ── Locality names timeline table ─────────────────────────────
        self._timeline_table = QTableWidget(0, 3)
        self._timeline_table.setHorizontalHeaderLabels(["Location", "Track time", "Frames"])
        _hdr = self._timeline_table.horizontalHeader()
        _hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        _hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        _hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._timeline_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._timeline_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._timeline_table.setAlternatingRowColors(True)
        self._timeline_table.setVisible(False)
        outer.addWidget(self._timeline_table)

        # ── Service visibility logic ──────────────────────────────────
        def _update_service_widgets() -> None:
            svc = "custom" if self._custom_radio.isChecked() else "osm"
            self._settings.setValue(_KEY_SERVICE, svc)
            self._custom_widget.setVisible(svc == "custom")

        _update_service_widgets()
        self._osm_radio.toggled.connect(lambda _: _update_service_widgets())
        self._custom_radio.toggled.connect(lambda _: _update_service_widgets())
        # Invalidate cached timeline when any Nominatim-query-affecting setting changes.
        # _invalidate_timeline always calls _update_preview_btn_state, so service/URL
        # changes (which affect the custom_url_empty check) are also covered here.
        self._osm_radio.toggled.connect(lambda _: self._invalidate_timeline())
        self._custom_radio.toggled.connect(lambda _: self._invalidate_timeline())
        self._custom_url_edit.textChanged.connect(lambda _: self._invalidate_timeline())
        self._check_every_spin.valueChanged.connect(lambda _: self._invalidate_timeline())
        self._detail_combo.currentIndexChanged.connect(lambda _: self._invalidate_timeline())

        # Set initial button state (e.g. disabled when custom URL is blank on load).
        self._update_preview_btn_state()

    # ------------------------------------------------------------------
    # Timeline cache
    # ------------------------------------------------------------------

    def _invalidate_timeline(self) -> None:
        """Clear the cached locality timeline (e.g. when settings change)."""
        self._cached_timeline = None
        self._update_preview_btn_state()

    def get_cached_timeline(self) -> list[LocalityEntry] | None:
        """Return the cached locality timeline, or None if not yet computed / invalidated."""
        return self._cached_timeline

    def set_cached_timeline(self, entries: list[LocalityEntry] | None) -> None:
        """Set (or clear) the cached locality timeline.

        Called by main_window when restoring a project that has a saved timeline.
        Populates the inline table immediately so the user sees the data without
        clicking anything.
        """
        self._cached_timeline = entries
        self._populate_timeline_table(entries or [])
        self._update_preview_btn_state()

    # ------------------------------------------------------------------
    # Inline timeline table
    # ------------------------------------------------------------------

    def _populate_timeline_table(self, entries: list[LocalityEntry]) -> None:
        """Fill the inline timeline table with *entries* (may be empty)."""
        fps = self._fps or 30
        forever = self._duration_forever_chk.isChecked()
        duration_frames = max(1, round(self._duration_spin.value() * fps))
        frames_known = self._total_frames > 0
        first_ts: datetime | None = None
        if self._trackpoints:
            first_ts = getattr(self._trackpoints[0], "timestamp", None)

        self._timeline_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            if frames_known:
                if forever:
                    # Name lasts until the next entry starts (or end of video)
                    if row + 1 < len(entries):
                        frame_end = entries[row + 1].frame_start - 1
                    else:
                        frame_end = self._total_frames - 1
                else:
                    frame_end = min(
                        entry.frame_start + duration_frames - 1, self._total_frames - 1
                    )
                frame_cell = f"{entry.frame_start}–{frame_end}"
            else:
                frame_cell = "—"
            self._timeline_table.setItem(row, 0, QTableWidgetItem(entry.name))
            self._timeline_table.setItem(
                row, 1,
                QTableWidgetItem(_format_track_time(entry.track_time_s, first_ts)),
            )
            self._timeline_table.setItem(row, 2, QTableWidgetItem(frame_cell))

        self._timeline_table.setVisible(len(entries) > 0)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        if self._preview_worker and self._preview_worker.isRunning():
            return
        if self._preview_pending_after_keyframes:
            return  # already waiting for keyframe calculation

        # No keyframe context yet — trigger calculation automatically; preview will
        # resume once set_pipeline_context() is called with the results.
        if not self._trackpoints or self._total_frames <= 0:
            self._preview_pending_after_keyframes = True
            self._update_preview_btn_state()
            self.calculate_keyframes_requested.emit()
            return

        self._run_preview_query()

    def _run_preview_query(self) -> None:
        """Start the background locality preview worker."""
        settings = self.get_settings()
        settings[_KEY_ENABLED] = True  # force enabled for preview regardless of checkbox

        self._preview_btn.setEnabled(False)
        self._preview_progress.setVisible(True)
        self._preview_progress.setRange(0, 0)

        worker = _LocalityPreviewWorker(
            self._trackpoints, self._total_frames, settings
        )
        worker.progress.connect(self._on_preview_progress)
        worker.finished.connect(self._on_preview_finished)
        worker.error.connect(self._on_preview_error)
        self._preview_worker = worker
        worker.start()

    def _on_preview_progress(self, current: int, total: int) -> None:
        self._preview_progress.setRange(0, total)
        self._preview_progress.setValue(current)

    def _on_preview_finished(self, entries: list[LocalityEntry]) -> None:
        self._preview_progress.setVisible(False)
        self._update_preview_btn_state()

        # Cache for reuse during final render and populate inline table.
        self._cached_timeline = entries if entries else None
        self._populate_timeline_table(entries)

        if not entries:
            QMessageBox.information(
                self,
                "Locality Names",
                "No locality names were returned.\n"
                "Check your Nominatim service settings and GPX coordinates.",
            )

    def _on_preview_error(self, msg: str) -> None:
        self._preview_progress.setVisible(False)
        self._update_preview_btn_state()
        QMessageBox.critical(self, "Preview failed", msg)

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

        if _sv(_KEY_SERVICE, "osm") == "custom":
            self._custom_radio.setChecked(True)
        else:
            self._osm_radio.setChecked(True)

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
        forever = _sv(_KEY_DURATION_FOREVER, False, bool)
        self._duration_forever_chk.setChecked(forever)
        self._duration_spin.setEnabled(not forever)

        self._text_color = _sv(_KEY_TEXT_COLOR, "#ffffff")
        self._update_color_btn(self._color_btn, self._text_color)
        self._shadow_chk.setChecked(_sv(_KEY_SHADOW, True, bool))

    def get_settings(self) -> dict[str, Any]:
        """Return current locality names settings as a flat dict."""
        return {
            _KEY_ENABLED:       self._group.isChecked(),
            _KEY_SERVICE:       "custom" if self._custom_radio.isChecked() else "osm",
            _KEY_CUSTOM_URL:    self._custom_url_edit.text(),
            _KEY_CHECK_EVERY_S: self._check_every_spin.value(),
            _KEY_DETAIL_LEVEL:  self._detail_combo.currentData(),
            _KEY_POSITION:          self._position_combo.currentData(),
            _KEY_DURATION:          self._duration_spin.value(),
            _KEY_DURATION_FOREVER:  self._duration_forever_chk.isChecked(),
            _KEY_TEXT_COLOR:        self._text_color,
            _KEY_SHADOW:        self._shadow_chk.isChecked(),
        }
