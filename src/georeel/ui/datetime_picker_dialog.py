from datetime import datetime

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)


class DateTimePickerDialog(QDialog):
    def __init__(self, filename: str, current: datetime | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit timestamp")
        self.setMinimumWidth(280)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"Timestamp for <b>{filename}</b>:"))

        self._editor = QDateTimeEdit()
        self._editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._editor.setCalendarPopup(True)
        if current:
            self._editor.setDateTime(
                QDateTime(current.year, current.month, current.day,
                          current.hour, current.minute, current.second)
            )
        else:
            self._editor.setDateTime(QDateTime.currentDateTime())

        layout.addWidget(self._editor)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_datetime(self) -> datetime:
        qdt = self._editor.dateTime()
        return datetime(
            qdt.date().year(), qdt.date().month(), qdt.date().day(),
            qdt.time().hour(), qdt.time().minute(), qdt.time().second(),
        )
