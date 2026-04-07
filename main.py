import logging
import sys

from PySide6.QtWidgets import QApplication

from ui import MainWindow


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def main():
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("GeoReel")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
