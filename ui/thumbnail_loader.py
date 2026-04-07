from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtGui import QImage

from .image_loader import load_qimage


class ThumbnailSignals(QObject):
    loaded = Signal(str, QImage)


class ThumbnailLoader(QRunnable):
    """Loads and scales a single image to a thumbnail height off the UI thread."""

    def __init__(self, path: str, height: int):
        super().__init__()
        self.signals = ThumbnailSignals()
        self._path = path
        self._height = height

    def run(self):
        image = load_qimage(self._path, max_height=self._height)
        self.signals.loaded.emit(self._path, image)
