"""
Preview Video Dialog.

Plays the rendered preview video in a resizable dialog window.
Uses PySide6.QtMultimedia (QMediaPlayer + QVideoWidget) when available;
falls back to opening the video in the system default player otherwise.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: F811
    from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: F811

    _has_multimedia = True
except ImportError:
    _has_multimedia = False


def open_preview_video(video_path: str, parent=None) -> None:
    """Open *video_path* for playback.

    If QtMultimedia is available, shows a player dialog.
    Otherwise launches the system default video player.
    """
    if _has_multimedia:
        dlg = _PlayerDialog(video_path, parent=parent)
        dlg.exec()
    else:
        _open_system_player(video_path)


# ------------------------------------------------------------------
# Qt player dialog (used when QtMultimedia is available)
# ------------------------------------------------------------------


class _PlayerDialog(QDialog):
    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preview Video")
        self.resize(1280, 760)
        self.setSizeGripEnabled(True)

        self._video_path = video_path

        # Video surface
        self._video_widget = QVideoWidget()
        self._video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Player
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_widget)
        self._player.setSource(QUrl.fromLocalFile(video_path))

        # ------------------------------------------------------------------ #
        # Controls                                                             #
        # ------------------------------------------------------------------ #
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(36)
        self._play_btn.clicked.connect(self._toggle_play)

        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedWidth(36)
        self._stop_btn.clicked.connect(self._player.stop)

        self._time_label = QLabel("0:00 / 0:00")

        self._seek_bar = QSlider(Qt.Orientation.Horizontal)
        self._seek_bar.setRange(0, 0)
        self._seek_bar.sliderMoved.connect(self._player.setPosition)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        ctrl.addWidget(self._play_btn)
        ctrl.addWidget(self._stop_btn)
        ctrl.addWidget(self._seek_bar, 1)
        ctrl.addWidget(self._time_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        ctrl.addWidget(buttons)

        # ------------------------------------------------------------------ #
        # Layout                                                               #
        # ------------------------------------------------------------------ #
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self._video_widget, 1)
        root.addLayout(ctrl)

        # ------------------------------------------------------------------ #
        # Player signals                                                       #
        # ------------------------------------------------------------------ #
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)

        self._player.play()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("⏸")
        else:
            self._play_btn.setText("▶")

    def _on_position_changed(self, pos_ms: int):
        self._seek_bar.setValue(pos_ms)
        self._time_label.setText(
            f"{_fmt_ms(pos_ms)} / {_fmt_ms(self._player.duration())}"
        )

    def _on_duration_changed(self, duration_ms: int):
        self._seek_bar.setRange(0, duration_ms)

    def closeEvent(self, event):
        self._player.stop()
        super().closeEvent(event)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _open_system_player(path: str) -> None:
    if sys.platform == "win32":
        import os

        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
