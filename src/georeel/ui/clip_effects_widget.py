"""Clip effects settings widget — fade-in and fade-out controls."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)

_KEY_FI_ENABLED   = "clip_effects/fade_in_enabled"
_KEY_FI_BLACK_DUR = "clip_effects/fade_in_black_dur"
_KEY_FI_FADE_DUR  = "clip_effects/fade_in_fade_dur"
_KEY_FO_ENABLED   = "clip_effects/fade_out_enabled"
_KEY_FO_BLACK_DUR = "clip_effects/fade_out_black_dur"
_KEY_FO_FADE_DUR  = "clip_effects/fade_out_fade_dur"


class ClipEffectsWidget(QWidget):
    """Provides fade-in / fade-out settings backed by QSettings."""

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self._settings = settings

        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(16, 16, 16, 16)

        root.addWidget(self._build_fade_group(
            "Fade in (black → content)",
            _KEY_FI_ENABLED, _KEY_FI_BLACK_DUR, _KEY_FI_FADE_DUR,
            "_fi",
        ))
        root.addWidget(self._build_fade_group(
            "Fade out (content → black)",
            _KEY_FO_ENABLED, _KEY_FO_BLACK_DUR, _KEY_FO_FADE_DUR,
            "_fo",
        ))
        root.addStretch()

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------

    def _build_fade_group(
        self,
        title: str,
        key_enabled: str,
        key_black: str,
        key_fade: str,
        attr_prefix: str,
    ) -> QGroupBox:
        enabled = self._settings.value(key_enabled, False, type=bool)
        group = QGroupBox(title)
        group.setCheckable(True)
        group.setChecked(enabled)

        form = QFormLayout(group)
        form.setSpacing(8)

        black_spin = QDoubleSpinBox()
        black_spin.setRange(0.0, 300.0)
        black_spin.setSingleStep(0.5)
        black_spin.setDecimals(1)
        black_spin.setSuffix(" s")
        black_spin.setToolTip("Duration of the pure-black clip before/after the content.")
        black_spin.setValue(float(self._settings.value(key_black, 5.0)))

        fade_spin = QDoubleSpinBox()
        fade_spin.setRange(0.0, 60.0)
        fade_spin.setSingleStep(0.1)
        fade_spin.setDecimals(1)
        fade_spin.setSuffix(" s")
        fade_spin.setToolTip("Duration of the luminance transition between black and content.")
        fade_spin.setValue(float(self._settings.value(key_fade, 1.0)))

        form.addRow("Black clip duration:", black_spin)
        form.addRow("Fade duration:", fade_spin)

        # Persist immediately on any change
        group.toggled.connect(lambda v, k=key_enabled: self._settings.setValue(k, v))
        black_spin.valueChanged.connect(lambda v, k=key_black: self._settings.setValue(k, v))
        fade_spin.valueChanged.connect(lambda v, k=key_fade: self._settings.setValue(k, v))

        # Keep references for get_settings()
        setattr(self, attr_prefix + "_group", group)
        setattr(self, attr_prefix + "_black_spin", black_spin)
        setattr(self, attr_prefix + "_fade_spin", fade_spin)

        return group

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_settings(self) -> dict:
        """Return current clip effects settings as a flat dict."""
        return {
            _KEY_FI_ENABLED:   self._fi_group.isChecked(),
            _KEY_FI_BLACK_DUR: self._fi_black_spin.value(),
            _KEY_FI_FADE_DUR:  self._fi_fade_spin.value(),
            _KEY_FO_ENABLED:   self._fo_group.isChecked(),
            _KEY_FO_BLACK_DUR: self._fo_black_spin.value(),
            _KEY_FO_FADE_DUR:  self._fo_fade_spin.value(),
        }
