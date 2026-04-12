from PySide6.QtCore import QObject, QRunnable, QSize, Signal
from PySide6.QtGui import QImage, QImageReader


class ThumbnailSignals(QObject):
    loaded = Signal(str, QImage)


class ThumbnailLoader(QRunnable):
    """Loads and scales a single image to a thumbnail height off the UI thread.

    Uses QImageReader (thread-safe) rather than PIL to avoid libjpeg's
    non-reentrant error handler which causes SIGSEGV under concurrent use.
    """

    def __init__(self, path: str, height: int):
        super().__init__()
        self.signals = ThumbnailSignals()
        self._path = path
        self._height = height

    def run(self):
        reader = QImageReader(self._path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.height() > self._height:
            ratio = self._height / size.height()
            new_w = max(1, round(size.width() * ratio))
            reader.setScaledSize(QSize(new_w, self._height))
        image = reader.read()
        self.signals.loaded.emit(self._path, image if not image.isNull() else QImage())
