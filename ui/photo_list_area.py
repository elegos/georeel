from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QDropEvent, QIcon, QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.exif_reader import read_photo_metadata
from core.match_result import MatchResult
from core.photo_store import PhotoStore

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = PhotoStore.instance()
        self._ts_clipboard: datetime | None = None
        self._preview_windows: list[PhotoPreviewWindow] = []
        self._thread_pool = QThreadPool.globalInstance()

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
        self._add_btn.clicked.connect(self._browse)
        self._remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._remove_btn)
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
        by_path = {r.photo_path: r for r in results}
        for row in range(self._table.rowCount()):
            path = self._table.item(row, _COL_NAME).data(Qt.UserRole)
            result = by_path.get(path)
            if result is None:
                continue
            item = QTableWidgetItem(result.status_text)
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if result.error:
                item.setForeground(Qt.red)
            elif result.warning:
                item.setForeground(_COLOR_WARNING)
            self._table.setItem(row, _COL_STATUS, item)

    def set_photos(self, photos):
        self._store.clear()
        for metadata in photos:
            self._store.add(metadata)
        self._rebuild_table()
        self.photos_changed.emit()

    def clear(self):
        self._store.clear()
        self._table.setRowCount(0)
        self._ts_clipboard = None
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

    def _table_key_press(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._remove_selected()
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_C:
            self._copy_timestamp()
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_V:
            self._paste_timestamp()
        else:
            QTableWidget.keyPressEvent(self._table, event)

    def _copy_timestamp(self):
        selected_rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not selected_rows:
            return
        path = self._table.item(selected_rows[0], _COL_NAME).data(Qt.UserRole)
        metadata = next((p for p in self._store.all() if p.path == path), None)
        if metadata and metadata.timestamp:
            self._ts_clipboard = metadata.timestamp

    def _paste_timestamp(self):
        if self._ts_clipboard is None:
            return
        selected_rows = {idx.row() for idx in self._table.selectedIndexes()}
        if not selected_rows:
            return
        for row in selected_rows:
            path = self._table.item(row, _COL_NAME).data(Qt.UserRole)
            self._store.update_timestamp(path, self._ts_clipboard)
        self._rebuild_table()
        self.photos_changed.emit()

    def _on_cell_double_clicked(self, row: int, col: int):
        path = self._table.item(row, _COL_NAME).data(Qt.UserRole)
        if col == _COL_TS:
            self._open_timestamp_editor(path)
        else:
            self._open_preview(path)

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

    def _rebuild_table(self):
        def sort_key(m):
            return (m.timestamp is None, m.timestamp, Path(m.path).name.lower())

        photos = sorted(self._store.all(), key=sort_key)

        self._table.setRowCount(0)
        for metadata in photos:
            row = self._table.rowCount()
            self._table.insertRow(row)

            name_item = QTableWidgetItem(Path(metadata.path).name)
            name_item.setToolTip(metadata.path)
            name_item.setData(Qt.UserRole, metadata.path)
            self._table.setRowHeight(row, _THUMBNAIL_HEIGHT + 4)
            self._submit_thumbnail(metadata.path)

            ts = metadata.timestamp
            ts_item = QTableWidgetItem(ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—")
            ts_item.setTextAlignment(Qt.AlignCenter)
            if not ts:
                ts_item.setForeground(Qt.red)

            gps_item = QTableWidgetItem("Yes" if metadata.has_gps else "No")
            gps_item.setTextAlignment(Qt.AlignCenter)
            if not metadata.has_gps:
                gps_item.setForeground(Qt.red)

            status_item = QTableWidgetItem("—")
            status_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            self._table.setItem(row, _COL_NAME, name_item)
            self._table.setItem(row, _COL_TS, ts_item)
            self._table.setItem(row, _COL_GPS, gps_item)
            self._table.setItem(row, _COL_STATUS, status_item)
