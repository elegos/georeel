from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QDropEvent, QIcon, QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.exif_reader import read_photo_metadata
from georeel.core.match_result import MatchResult
from georeel.core.photo_store import PhotoStore
from georeel.core.trackpoint import Trackpoint

from .datetime_picker_dialog import DateTimePickerDialog
from .drop_area import DropArea
from .photo_preview_window import PhotoPreviewWindow
from .thumbnail_loader import ThumbnailLoader

_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic"}
_COLUMNS = ["File", "Timestamp", "GPS", "Status"]
_COL_NAME = 0
_COL_TS = 1
_COL_GPS = 2
_COL_STATUS = 3
_THUMBNAIL_HEIGHT = 48
_COLOR_WARNING = QColor(255, 165, 0)


class PhotoListArea(DropArea):
    photos_changed = Signal()
    calculate_keyframes_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = PhotoStore.instance()
        self._tz_offset_hours: float = 0.0
        # clipboard holds (timestamp | None, latitude | None, longitude | None)
        self._clipboard: tuple[datetime | None, float | None, float | None] | None = None
        self._preview_windows: list[PhotoPreviewWindow] = []
        self._thread_pool = QThreadPool.globalInstance()
        self._match_results: dict[str, MatchResult] = {}
        self._trackpoints: list[Trackpoint] = []
        self._pause_frames: dict[str, list[int]] = {}  # photo_path → [first_frame, last_frame]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(_COL_NAME, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(_COL_TS, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_GPS, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_STATUS, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setIconSize(QSize(_THUMBNAIL_HEIGHT * 2, _THUMBNAIL_HEIGHT))
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self._table.keyPressEvent = self._table_key_press

        drop_hint = QLabel("or drag & drop images here")
        drop_hint.setAlignment(Qt.AlignCenter)
        drop_hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("Add photos…")
        self._remove_btn = QPushButton("Remove selected")
        self._calc_kf_btn = QPushButton("Calculate keyframes")
        self._clear_btn = QPushButton("Clear")
        self._add_btn.clicked.connect(self._browse)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._calc_kf_btn.clicked.connect(self.calculate_keyframes_requested)
        self._calc_kf_btn.setEnabled(False)
        self._clear_btn.clicked.connect(self.clear)
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._remove_btn)
        btn_row.addWidget(self._calc_kf_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()

        layout.addWidget(self._table)
        layout.addWidget(drop_hint)
        layout.addLayout(btn_row)

        self.setStyleSheet(
            "PhotoListArea { border: 2px dashed palette(mid); border-radius: 6px; }"
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def photo_paths(self) -> list[str]:
        return [p.path for p in self._store.all()]

    def update_match_statuses(self, results: list[MatchResult]) -> None:
        self._match_results = {r.photo_path: r for r in results}
        for row in range(self._table.rowCount()):
            path = self._table.item(row, _COL_NAME).data(Qt.UserRole)
            result = self._match_results.get(path)
            if result is None:
                continue
            item = QTableWidgetItem(result.status_text)
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if result.error:
                item.setForeground(Qt.red)
            elif result.warning:
                item.setForeground(_COLOR_WARNING)
            self._table.setItem(row, _COL_STATUS, item)

    def update_pipeline_info(
        self,
        trackpoints: list[Trackpoint] | None = None,
        keyframes: list[CameraKeyframe] | None = None,
    ) -> None:
        """Feed trackpoint list and/or camera keyframes to update the Status column."""
        if trackpoints is not None:
            self._trackpoints = trackpoints
        if keyframes is not None:
            # Build a map: photo_path → [first_pause_frame, last_pause_frame]
            self._pause_frames = {}
            for kf in keyframes:
                if kf.is_pause and kf.photo_path:
                    entry = self._pause_frames.setdefault(kf.photo_path, [kf.frame, kf.frame])
                    entry[0] = min(entry[0], kf.frame)
                    entry[1] = max(entry[1], kf.frame)
            self._update_keyframe_statuses()

    def set_tz_offset(self, hours: float) -> None:
        if self._tz_offset_hours != hours:
            self._tz_offset_hours = hours
            self._rebuild_table()

    def set_photos(self, photos):
        self._store.clear()
        for metadata in photos:
            self._store.add(metadata)
        self._rebuild_table()
        self.photos_changed.emit()

    def clear(self):
        self._store.clear()
        self._table.setRowCount(0)
        self._clipboard = None
        self._match_results = {}
        self._trackpoints = []
        self._pause_frames = {}
        self._calc_kf_btn.setEnabled(False)
        for w in self._preview_windows:
            w.close()
        self._preview_windows.clear()
        self.photos_changed.emit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _browse(self):
        exts = " ".join(f"*{e}" for e in _SUPPORTED_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select photo files", "", f"Images ({exts})"
        )
        for p in paths:
            self._add_path(p)

    def dropEvent(self, event: QDropEvent):
        added = False
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in _SUPPORTED_EXTENSIONS:
                self._add_path(path)
                added = True
        if added:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _add_path(self, path: str):
        existing_paths = {p.path for p in self._store.all()}
        if path in existing_paths:
            return
        metadata = read_photo_metadata(path)
        self._store.add(metadata)
        self._rebuild_table()
        self.photos_changed.emit()

    def _remove_selected(self):
        selected_rows = {idx.row() for idx in self._table.selectedIndexes()}
        if not selected_rows:
            return
        for row in selected_rows:
            path = self._table.item(row, 0).data(Qt.UserRole)
            self._store.remove(path)
        self._rebuild_table()
        self.photos_changed.emit()

    def _update_keyframe_statuses(self) -> None:
        """Overwrite the Status column with keyframe-range info for each photo."""
        for row in range(self._table.rowCount()):
            path = self._table.item(row, _COL_NAME).data(Qt.UserRole)
            frames = self._pause_frames.get(path)
            result = self._match_results.get(path)

            if frames:
                first, last = frames
                pos_label = ""
                if result:
                    if result.position == "pre":
                        pos_label = " (pre-track)"
                    elif result.position == "post":
                        pos_label = " (post-track)"
                if first == last:
                    text = f"frame {first}{pos_label}"
                else:
                    text = f"frames {first}–{last}{pos_label}"
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self._table.setItem(row, _COL_STATUS, item)

    def _table_key_press(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._remove_selected()
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_A:
            self._table.selectAll()
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_C:
            self._copy_metadata()
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_V:
            self._paste_metadata()
        else:
            QTableWidget.keyPressEvent(self._table, event)

    def _copy_metadata(self):
        selected_rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not selected_rows:
            return
        path = self._table.item(selected_rows[0], _COL_NAME).data(Qt.UserRole)
        metadata = next((p for p in self._store.all() if p.path == path), None)
        if metadata:
            self._clipboard = (metadata.timestamp, metadata.latitude, metadata.longitude)

    def _paste_metadata(self):
        if self._clipboard is None:
            return
        ts, lat, lon = self._clipboard
        selected_rows = {idx.row() for idx in self._table.selectedIndexes()}
        if not selected_rows:
            return
        for row in selected_rows:
            path = self._table.item(row, _COL_NAME).data(Qt.UserRole)
            if ts is not None:
                self._store.update_timestamp(path, ts)
            if lat is not None and lon is not None:
                self._store.update_gps(path, lat, lon)
        self._rebuild_table()
        self.photos_changed.emit()

    def _on_cell_double_clicked(self, row: int, col: int):
        path = self._table.item(row, _COL_NAME).data(Qt.UserRole)
        self._show_exif_dialog(path)

    def _show_exif_dialog(self, path: str):
        stored = next((p for p in self._store.all() if p.path == path), None)
        if stored is None:
            return
        original = read_photo_metadata(path)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Photo — {Path(path).name}")
        dlg.setMinimumWidth(440)
        root = QVBoxLayout(dlg)

        group = QGroupBox("EXIF data")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)

        _COLOR_OVERRIDE = "#E07800"

        def _val_label(text: str, overridden: bool = False) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            if overridden:
                lbl.setStyleSheet(f"color: {_COLOR_OVERRIDE};")
            return lbl

        # Timestamp
        orig_ts = original.timestamp
        cur_ts  = stored.timestamp
        ts_overridden = (cur_ts != orig_ts)
        if orig_ts:
            orig_ts_str = orig_ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            orig_ts_str = "—"
        if cur_ts:
            cur_ts_str = cur_ts.strftime("%Y-%m-%d %H:%M:%S")
            if ts_overridden:
                form.addRow("Timestamp (EXIF):", _val_label(orig_ts_str))
                form.addRow("Timestamp (current):", _val_label(
                    f"{cur_ts_str}  ← overridden", overridden=True
                ))
            else:
                form.addRow("Timestamp:", _val_label(cur_ts_str))
        else:
            form.addRow("Timestamp:", _val_label("—"))

        # GPS
        def _fmt_gps(lat, lon):
            if lat is None or lon is None:
                return "—"
            return f"{lat:.6f}°, {lon:.6f}°"

        orig_gps_str = _fmt_gps(original.latitude, original.longitude)
        cur_gps_str  = _fmt_gps(stored.latitude,   stored.longitude)
        gps_overridden = (
            stored.latitude  != original.latitude or
            stored.longitude != original.longitude
        )
        if gps_overridden:
            form.addRow("GPS (EXIF):", _val_label(orig_gps_str))
            form.addRow("GPS (current):", _val_label(
                f"{cur_gps_str}  ← overridden", overridden=True
            ))
        else:
            form.addRow("GPS:", _val_label(cur_gps_str))

        root.addWidget(group)

        # Buttons
        btn_box = QDialogButtonBox()
        edit_ts_btn  = btn_box.addButton("Edit timestamp…", QDialogButtonBox.ActionRole)
        view_btn     = btn_box.addButton("View photo",      QDialogButtonBox.ActionRole)
        close_btn    = btn_box.addButton(QDialogButtonBox.Close)

        def _edit_ts():
            dlg.accept()
            self._open_timestamp_editor(path)

        def _view():
            dlg.accept()
            self._open_preview(path)

        edit_ts_btn.clicked.connect(_edit_ts)
        view_btn.clicked.connect(_view)
        close_btn.clicked.connect(dlg.reject)

        root.addWidget(btn_box)
        dlg.exec()

    def _open_timestamp_editor(self, path: str):
        metadata = next((p for p in self._store.all() if p.path == path), None)
        if metadata is None:
            return
        dlg = DateTimePickerDialog(Path(path).name, metadata.timestamp, parent=self)
        if dlg.exec() != DateTimePickerDialog.Accepted:
            return
        self._store.update_timestamp(path, dlg.selected_datetime())
        self._rebuild_table()
        self.photos_changed.emit()

    def _open_preview(self, path: str):
        paths = [
            self._table.item(r, _COL_NAME).data(Qt.UserRole)
            for r in range(self._table.rowCount())
        ]
        index = paths.index(path) if path in paths else 0
        window = PhotoPreviewWindow(paths, index, parent=None)
        self._preview_windows.append(window)
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.destroyed.connect(
            lambda: self._preview_windows.remove(window)
            if window in self._preview_windows else None
        )
        window.photo_removed.connect(self._on_preview_photo_removed)
        window.show()

    def _on_preview_photo_removed(self, path: str):
        self._store.remove(path)
        self._rebuild_table()
        self.photos_changed.emit()

    def _submit_thumbnail(self, path: str):
        loader = ThumbnailLoader(path, _THUMBNAIL_HEIGHT)
        loader.signals.loaded.connect(self._on_thumbnail_ready)
        self._thread_pool.start(loader)

    def _on_thumbnail_ready(self, path: str, image: QImage):
        if image.isNull():
            return
        icon = QIcon(QPixmap.fromImage(image))
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_NAME)
            if item and item.data(Qt.UserRole) == path:
                item.setIcon(icon)
                break

    def set_calc_kf_running(self, running: bool) -> None:
        """Disable/enable the Calculate keyframes button during a background run."""
        self._calc_kf_btn.setEnabled(not running and self._table.rowCount() > 0)
        self._calc_kf_btn.setText(
            "Calculating…" if running else "Calculate keyframes"
        )

    def _rebuild_table(self):
        def sort_key(m):
            return (m.timestamp is None, m.timestamp, Path(m.path).name.lower())

        photos = sorted(self._store.all(), key=sort_key)
        # Cache original EXIF for override detection (keyed by path)
        originals: dict[str, object] = {}

        self._table.setRowCount(0)
        for metadata in photos:
            row = self._table.rowCount()
            self._table.insertRow(row)

            name_item = QTableWidgetItem(Path(metadata.path).name)
            name_item.setToolTip(metadata.path)
            name_item.setData(Qt.UserRole, metadata.path)
            self._table.setRowHeight(row, _THUMBNAIL_HEIGHT + 4)
            self._submit_thumbnail(metadata.path)

            # Lazy-read original EXIF for override detection
            if metadata.path not in originals:
                originals[metadata.path] = read_photo_metadata(metadata.path)
            original = originals[metadata.path]

            ts = metadata.timestamp
            ts_overridden = ts != original.timestamp
            if ts:
                offset = self._tz_offset_hours
                if offset != 0.0:
                    utc_ts = ts - timedelta(hours=offset)
                    display = f"{ts.strftime('%H:%M:%S')} → {utc_ts.strftime('%H:%M:%S')} UTC"
                    tooltip = (
                        f"EXIF local: {ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"UTC:        {utc_ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Offset: UTC{offset:+.1f}"
                    )
                else:
                    display = ts.strftime("%Y-%m-%d %H:%M:%S")
                    tooltip = ""
            else:
                display = "—"
                tooltip = ""
            ts_item = QTableWidgetItem(display)
            ts_item.setTextAlignment(Qt.AlignCenter)
            if tooltip:
                ts_item.setToolTip(tooltip)
            if not ts:
                ts_item.setForeground(Qt.red)
            elif ts_overridden:
                ts_item.setForeground(_COLOR_WARNING)

            gps_overridden = (
                metadata.latitude  != original.latitude or
                metadata.longitude != original.longitude
            )
            if metadata.has_gps:
                gps_text = f"{metadata.latitude:.4f}°, {metadata.longitude:.4f}°"
            else:
                gps_text = "—"
            gps_item = QTableWidgetItem(gps_text)
            gps_item.setTextAlignment(Qt.AlignCenter)
            if not metadata.has_gps:
                gps_item.setForeground(Qt.red)
            elif gps_overridden:
                gps_item.setForeground(_COLOR_WARNING)

            status_item = QTableWidgetItem("—")
            status_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            self._table.setItem(row, _COL_NAME, name_item)
            self._table.setItem(row, _COL_TS, ts_item)
            self._table.setItem(row, _COL_GPS, gps_item)
            self._table.setItem(row, _COL_STATUS, status_item)

        has_photos = self._table.rowCount() > 0
        self._calc_kf_btn.setEnabled(has_photos)
        # Re-apply keyframe statuses if already computed
        if self._pause_frames:
            self._update_keyframe_statuses()
