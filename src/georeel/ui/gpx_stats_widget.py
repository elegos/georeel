"""
Widget that displays summary statistics for a loaded GPX track.
Call update_stats(trackpoints) after parsing; call clear() on reset.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from georeel.core.gpx_stats import GpxStats, compute_stats
from georeel.core.trackpoint import Trackpoint

_ROWS = [
    "Start",
    "End",
    "Duration",
    "Distance",
    "Avg speed",
    "Max speed",
    "Min elevation",
    "Max elevation",
    "Elevation gain",
    "Elevation loss",
    "Track points",
]


def _fmt_duration(td) -> str:
    total_s = int(td.total_seconds())
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _fmt_dist(m: float) -> str:
    if m >= 1000:
        return f"{m / 1000:.2f} km"
    return f"{m:.0f} m"


class GpxStatsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(len(_ROWS), 2)
        self._table.setHorizontalHeaderLabels(["Stat", "Value"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)

        # Populate stat-name column (never changes)
        for i, name in enumerate(_ROWS):
            item = QTableWidgetItem(name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(i, 0, item)
            val = QTableWidgetItem("—")
            val.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(i, 1, val)

        # Compact row height; cap the widget so it doesn't push other widgets away
        _ROW_H = 22
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        # Maximum height: header (~26 px) + all rows + a little breathing room
        self.setMaximumHeight(30 + len(_ROWS) * _ROW_H + 8)

        layout.addWidget(self._table)
        self.setVisible(False)

    # ------------------------------------------------------------------

    def update_stats(self, trackpoints: list[Trackpoint]) -> None:
        stats = compute_stats(trackpoints)
        self._apply(stats)
        self.setVisible(True)

    def clear(self) -> None:
        self.setVisible(False)
        for i in range(len(_ROWS)):
            item = self._table.item(i, 1)
            if item is not None:
                item.setText("—")

    # ------------------------------------------------------------------

    def _set(self, row: int, text: str) -> None:
        item = self._table.item(row, 1)
        if item is not None:
            item.setText(text)

    def _apply(self, s: GpxStats) -> None:
        fmt_ts = lambda ts: ts.strftime("%Y-%m-%d %H:%M:%S UTC") if ts else "—"

        self._set(0,  fmt_ts(s.start_time))
        self._set(1,  fmt_ts(s.end_time))
        self._set(2,  _fmt_duration(s.duration) if s.duration else "—")
        self._set(3,  _fmt_dist(s.total_distance_m))
        self._set(4,  f"{s.avg_speed_kmh:.1f} km/h" if s.avg_speed_kmh is not None else "—")
        self._set(5,  f"{s.max_speed_kmh:.1f} km/h" if s.max_speed_kmh is not None else "—")
        self._set(6,  f"{s.min_elevation_m:.0f} m"  if s.min_elevation_m is not None else "—")
        self._set(7,  f"{s.max_elevation_m:.0f} m"  if s.max_elevation_m is not None else "—")
        self._set(8,  f"+{s.elevation_gain_m:.0f} m")
        self._set(9,  f"−{s.elevation_loss_m:.0f} m")
        self._set(10, str(s.point_count))
