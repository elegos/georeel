from typing import override

from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent
from PySide6.QtWidgets import QWidget


class DropArea(QWidget):
    """Base class for drag-and-drop enabled areas."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @override
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
