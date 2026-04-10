"""Clip effects settings widget — fade-in/fade-out and title controls."""

from PySide6.QtCore import QRect, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

_KEY_FI_ENABLED   = "clip_effects/fade_in_enabled"
_KEY_FI_BLACK_DUR = "clip_effects/fade_in_black_dur"
_KEY_FI_FADE_DUR  = "clip_effects/fade_in_fade_dur"
_KEY_FO_ENABLED   = "clip_effects/fade_out_enabled"
_KEY_FO_BLACK_DUR = "clip_effects/fade_out_black_dur"
_KEY_FO_FADE_DUR  = "clip_effects/fade_out_fade_dur"

_KEY_TITLE_ENABLED   = "clip_effects/title_enabled"
_KEY_TITLE_TEXT      = "clip_effects/title_text"
_KEY_TITLE_FONT      = "clip_effects/title_font"
_KEY_TITLE_FONT_SIZE = "clip_effects/title_font_size"
_KEY_TITLE_ANCHOR    = "clip_effects/title_anchor"
_KEY_TITLE_MARGIN    = "clip_effects/title_margin"
_KEY_TITLE_ALIGNMENT = "clip_effects/title_alignment"
_KEY_TITLE_COLOR     = "clip_effects/title_color"
_KEY_TITLE_SHADOW    = "clip_effects/title_shadow"
_KEY_TITLE_DURATION  = "clip_effects/title_duration"
_KEY_TITLE_FI_ENABLED = "clip_effects/title_fade_in_enabled"
_KEY_TITLE_FI_DUR     = "clip_effects/title_fade_in_dur"
_KEY_TITLE_FO_ENABLED = "clip_effects/title_fade_out_enabled"
_KEY_TITLE_FO_DUR     = "clip_effects/title_fade_out_dur"

_ANCHORS = [
    ("Top left",     "top-left"),
    ("Top",          "top"),
    ("Top right",    "top-right"),
    ("Center left",  "center-left"),
    ("Center",       "center"),
    ("Center right", "center-right"),
    ("Bottom left",  "bottom-left"),
    ("Bottom",       "bottom"),
    ("Bottom right", "bottom-right"),
]

_ALIGNMENTS = [("Left", "left"), ("Center", "center"), ("Right", "right")]

_RESOLUTION_WIDTHS = {
    "720p": 1280, "1080p": 1920, "1440p": 2560, "4k": 3840,
    "portrait_720p": 720, "portrait_1080p": 1080,
    "portrait_1440p": 1440, "portrait_4k": 2160,
    "square_720": 720, "square_1080": 1080,
    "square_1440": 1440, "square_2160": 2160,
}

_PREVIEW_SIZES = {
    "landscape": (320, 180),
    "portrait":  (180, 320),
    "square":    (280, 280),
}


class _TitlePreviewWidget(QWidget):
    """Miniature live preview of the title overlay."""

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._apply_size()

    def _preview_dims(self) -> tuple[int, int]:
        aspect = self._settings.value("render/aspect_ratio", "landscape")
        return _PREVIEW_SIZES.get(aspect, (320, 180))

    def _apply_size(self):
        w, h = self._preview_dims()
        self.setFixedSize(w, h)

    def refresh(self):
        self._apply_size()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)

        text = self._settings.value(_KEY_TITLE_TEXT, "")
        if not text.strip():
            return

        pw, ph = self.width(), self.height()
        font_name  = self._settings.value(_KEY_TITLE_FONT, "Noto Serif")
        font_size  = int(self._settings.value(_KEY_TITLE_FONT_SIZE, 95))
        anchor     = self._settings.value(_KEY_TITLE_ANCHOR, "bottom-right")
        margin     = int(self._settings.value(_KEY_TITLE_MARGIN, 40))
        alignment  = self._settings.value(_KEY_TITLE_ALIGNMENT, "right")
        color_str  = self._settings.value(_KEY_TITLE_COLOR, "#ffffff")
        shadow     = self._settings.value(_KEY_TITLE_SHADOW, True, type=bool)

        # Scale font size proportionally to preview vs. actual resolution
        res   = self._settings.value("render/resolution", "1080p")
        ref_w = _RESOLUTION_WIDTHS.get(res, 1920)
        scale = pw / ref_w
        scaled_size   = max(6, round(font_size * scale))
        scaled_margin = max(1, round(margin * scale))

        font = QFont(font_name, scaled_size)
        painter.setFont(font)
        fm = QFontMetrics(font)

        lines  = text.split("\n") if text else [""]
        line_h = fm.height()
        text_h = line_h * len(lines)

        # Determine block position from anchor.
        # The anchor is the *extreme corner* the text originates from:
        #   top-right  → text grows downward and leftward from the top-right corner
        #   bottom-right → text grows upward and leftward from the bottom-right corner
        #
        # draw_rect always spans from the opposite edge to the anchor edge so
        # Qt's alignment flag is the sole authority on where each line lands.
        # This prevents any line from escaping the anchor boundary regardless
        # of line length.
        parts  = anchor.split("-") if anchor != "center" else ["center", "center"]
        v_part = parts[0]
        h_part = parts[1] if len(parts) > 1 else "center"

        # Horizontal: rect spans from the far edge to the anchor edge
        if h_part == "left":
            rect_x, rect_w = scaled_margin, pw - scaled_margin
        elif h_part == "right":
            rect_x, rect_w = 0, pw - scaled_margin
        else:  # center
            rect_x, rect_w = 0, pw

        # Vertical: position the rect so the anchor edge is at the margin
        if v_part == "top":
            rect_y = scaled_margin
        elif v_part == "bottom":
            rect_y = max(0, ph - text_h - scaled_margin)
        else:  # center
            rect_y = max(0, (ph - text_h) // 2)

        draw_rect = QRect(rect_x, rect_y, rect_w, text_h + 4)

        align_flag = {
            "left":   Qt.AlignLeft,
            "center": Qt.AlignHCenter,
            "right":  Qt.AlignRight,
        }.get(alignment, Qt.AlignLeft)

        if shadow:
            off = max(1, round(3 * scale))
            painter.setPen(QColor(0, 0, 0, 180))
            painter.drawText(draw_rect.translated(off, off), align_flag, text)

        painter.setPen(QColor(color_str))
        painter.drawText(draw_rect, align_flag, text)


class ClipEffectsWidget(QWidget):
    """Provides fade-in/fade-out and title settings backed by QSettings."""

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
        root.addWidget(self._build_title_group())
        root.addStretch()

    # ------------------------------------------------------------------
    # Fade group builder
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

        group.toggled.connect(lambda v, k=key_enabled: self._settings.setValue(k, v))
        black_spin.valueChanged.connect(lambda v, k=key_black: self._settings.setValue(k, v))
        fade_spin.valueChanged.connect(lambda v, k=key_fade: self._settings.setValue(k, v))

        setattr(self, attr_prefix + "_group", group)
        setattr(self, attr_prefix + "_black_spin", black_spin)
        setattr(self, attr_prefix + "_fade_spin", fade_spin)

        return group

    # ------------------------------------------------------------------
    # Title group builder
    # ------------------------------------------------------------------

    def _build_title_group(self) -> QGroupBox:
        enabled = self._settings.value(_KEY_TITLE_ENABLED, False, type=bool)
        group = QGroupBox("Title")
        group.setCheckable(True)
        group.setChecked(enabled)
        group.toggled.connect(lambda v: self._settings.setValue(_KEY_TITLE_ENABLED, v))
        self._title_group = group

        outer = QVBoxLayout(group)
        outer.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(8)
        outer.addLayout(form)

        # Text
        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("Enter title text…")
        text_edit.setFixedHeight(72)
        text_edit.setPlainText(self._settings.value(_KEY_TITLE_TEXT, ""))
        self._title_text = text_edit
        form.addRow("Text:", text_edit)

        # Font + size
        font_row = QHBoxLayout()
        font_combo = QFontComboBox()
        saved_font = self._settings.value(_KEY_TITLE_FONT, "")
        font_combo.setCurrentFont(QFont(saved_font or "Noto Serif"))
        self._title_font = font_combo
        size_spin = QSpinBox()
        size_spin.setRange(6, 500)
        size_spin.setValue(int(self._settings.value(_KEY_TITLE_FONT_SIZE, 95)))
        size_spin.setSuffix(" pt")
        size_spin.setFixedWidth(70)
        self._title_font_size = size_spin
        font_row.addWidget(font_combo, stretch=1)
        font_row.addWidget(size_spin)
        form.addRow("Font:", font_row)

        # Anchor + margin
        anchor_row = QHBoxLayout()
        anchor_combo = QComboBox()
        saved_anchor = self._settings.value(_KEY_TITLE_ANCHOR, "bottom-right")
        for label, value in _ANCHORS:
            anchor_combo.addItem(label, value)
            if value == saved_anchor:
                anchor_combo.setCurrentIndex(anchor_combo.count() - 1)
        self._title_anchor = anchor_combo
        margin_spin = QSpinBox()
        margin_spin.setRange(0, 500)
        margin_spin.setValue(int(self._settings.value(_KEY_TITLE_MARGIN, 40)))
        margin_spin.setSuffix(" px")
        margin_spin.setFixedWidth(75)
        margin_spin.setEnabled(saved_anchor != "center")
        self._title_margin = margin_spin
        anchor_row.addWidget(anchor_combo, stretch=1)
        anchor_row.addWidget(QLabel("Margin:"))
        anchor_row.addWidget(margin_spin)
        form.addRow("Anchor:", anchor_row)

        # Alignment
        align_combo = QComboBox()
        saved_align = self._settings.value(_KEY_TITLE_ALIGNMENT, "right")
        for label, value in _ALIGNMENTS:
            align_combo.addItem(label, value)
            if value == saved_align:
                align_combo.setCurrentIndex(align_combo.count() - 1)
        self._title_alignment = align_combo
        form.addRow("Alignment:", align_combo)

        # Color + shadow
        color_row = QHBoxLayout()
        saved_color = self._settings.value(_KEY_TITLE_COLOR, "#ffffff")
        color_btn = QPushButton()
        color_btn.setFixedWidth(80)
        self._title_color = saved_color
        self._title_color_btn = color_btn
        self._update_color_btn(color_btn, saved_color)
        shadow_chk = QCheckBox("Shadow")
        shadow_chk.setChecked(self._settings.value(_KEY_TITLE_SHADOW, True, type=bool))
        self._title_shadow = shadow_chk
        color_row.addWidget(color_btn)
        color_row.addWidget(shadow_chk)
        color_row.addStretch()
        form.addRow("Color:", color_row)

        # Duration
        dur_spin = QDoubleSpinBox()
        dur_spin.setRange(0.0, 3600.0)
        dur_spin.setSingleStep(0.5)
        dur_spin.setDecimals(1)
        dur_spin.setSuffix(" s")
        dur_spin.setValue(float(self._settings.value(_KEY_TITLE_DURATION, 10.0)))
        self._title_duration = dur_spin
        form.addRow("Duration:", dur_spin)

        # Title fade-in
        fi_row = QHBoxLayout()
        fi_chk = QCheckBox("Fade in")
        fi_chk.setChecked(self._settings.value(_KEY_TITLE_FI_ENABLED, True, type=bool))
        self._title_fi_chk = fi_chk
        fi_dur = QDoubleSpinBox()
        fi_dur.setRange(0.0, 60.0)
        fi_dur.setSingleStep(0.5)
        fi_dur.setDecimals(1)
        fi_dur.setSuffix(" s")
        fi_dur.setValue(float(self._settings.value(_KEY_TITLE_FI_DUR, 3.0)))
        fi_dur.setEnabled(fi_chk.isChecked())
        fi_dur.setFixedWidth(80)
        self._title_fi_dur = fi_dur
        fi_chk.toggled.connect(fi_dur.setEnabled)
        fi_row.addWidget(fi_chk)
        fi_row.addWidget(fi_dur)
        fi_row.addStretch()

        # Title fade-out
        fo_chk = QCheckBox("Fade out")
        fo_chk.setChecked(self._settings.value(_KEY_TITLE_FO_ENABLED, True, type=bool))
        self._title_fo_chk = fo_chk
        fo_dur = QDoubleSpinBox()
        fo_dur.setRange(0.0, 60.0)
        fo_dur.setSingleStep(0.5)
        fo_dur.setDecimals(1)
        fo_dur.setSuffix(" s")
        fo_dur.setValue(float(self._settings.value(_KEY_TITLE_FO_DUR, 3.0)))
        fo_dur.setEnabled(fo_chk.isChecked())
        fo_dur.setFixedWidth(80)
        self._title_fo_dur = fo_dur
        fo_chk.toggled.connect(fo_dur.setEnabled)
        fi_row.addWidget(fo_chk)
        fi_row.addWidget(fo_dur)
        form.addRow("Title fades:", fi_row)

        # Preview
        preview = _TitlePreviewWidget(self._settings)
        self._title_preview = preview
        preview_row = QHBoxLayout()
        preview_row.addStretch()
        preview_row.addWidget(preview)
        preview_row.addStretch()
        outer.addLayout(preview_row)

        # Wire signals → persist + update preview
        def _refresh():
            self._title_preview.refresh()

        text_edit.textChanged.connect(
            lambda: (self._settings.setValue(_KEY_TITLE_TEXT, text_edit.toPlainText()), _refresh())
        )
        font_combo.currentFontChanged.connect(
            lambda f: (self._settings.setValue(_KEY_TITLE_FONT, f.family()), _refresh())
        )
        size_spin.valueChanged.connect(
            lambda v: (self._settings.setValue(_KEY_TITLE_FONT_SIZE, v), _refresh())
        )
        anchor_combo.currentIndexChanged.connect(self._on_anchor_changed)
        margin_spin.valueChanged.connect(
            lambda v: (self._settings.setValue(_KEY_TITLE_MARGIN, v), _refresh())
        )
        align_combo.currentIndexChanged.connect(
            lambda _: (self._settings.setValue(_KEY_TITLE_ALIGNMENT, align_combo.currentData()), _refresh())
        )
        color_btn.clicked.connect(self._pick_color)
        shadow_chk.toggled.connect(
            lambda v: (self._settings.setValue(_KEY_TITLE_SHADOW, v), _refresh())
        )
        dur_spin.valueChanged.connect(
            lambda v: self._settings.setValue(_KEY_TITLE_DURATION, v)
        )
        fi_chk.toggled.connect(lambda v: self._settings.setValue(_KEY_TITLE_FI_ENABLED, v))
        fi_dur.valueChanged.connect(lambda v: self._settings.setValue(_KEY_TITLE_FI_DUR, v))
        fo_chk.toggled.connect(lambda v: self._settings.setValue(_KEY_TITLE_FO_ENABLED, v))
        fo_dur.valueChanged.connect(lambda v: self._settings.setValue(_KEY_TITLE_FO_DUR, v))
        group.toggled.connect(lambda _: _refresh())

        return group

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_anchor_changed(self, _index: int):
        value = self._title_anchor.currentData()
        self._settings.setValue(_KEY_TITLE_ANCHOR, value)
        self._title_margin.setEnabled(value != "center")
        self._title_preview.refresh()

    def _update_color_btn(self, btn: QPushButton, color_hex: str):
        c = QColor(color_hex)
        luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        text_color = "#000000" if luma > 128 else "#ffffff"
        btn.setText(color_hex)
        btn.setStyleSheet(
            f"background-color: {color_hex}; color: {text_color}; border: 1px solid #888;"
        )

    def _pick_color(self):
        current = QColor(self._title_color)
        chosen = QColorDialog.getColor(current, self, "Title color")
        if chosen.isValid():
            self._title_color = chosen.name()
            self._settings.setValue(_KEY_TITLE_COLOR, self._title_color)
            self._update_color_btn(self._title_color_btn, self._title_color)
            self._title_preview.refresh()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-read all controls from the current QSettings values.

        Called after the project loader writes new values to QSettings so the
        widget reflects the loaded project without destroying and recreating it
        (which would cause tab-index churn and spurious Qt layout events).
        """
        def _sv(key, default, t=None):
            return self._settings.value(key, default) if t is None \
                else self._settings.value(key, default, type=t)

        # Fade-in
        self._fi_group.setChecked(_sv(_KEY_FI_ENABLED, False, bool))
        self._fi_black_spin.setValue(float(_sv(_KEY_FI_BLACK_DUR, 5.0)))
        self._fi_fade_spin.setValue(float(_sv(_KEY_FI_FADE_DUR, 1.0)))

        # Fade-out
        self._fo_group.setChecked(_sv(_KEY_FO_ENABLED, False, bool))
        self._fo_black_spin.setValue(float(_sv(_KEY_FO_BLACK_DUR, 5.0)))
        self._fo_fade_spin.setValue(float(_sv(_KEY_FO_FADE_DUR, 1.0)))

        # Title
        self._title_group.setChecked(_sv(_KEY_TITLE_ENABLED, False, bool))
        self._title_text.setPlainText(_sv(_KEY_TITLE_TEXT, ""))
        self._title_font.setCurrentFont(QFont(_sv(_KEY_TITLE_FONT, "Noto Serif")))
        self._title_font_size.setValue(int(_sv(_KEY_TITLE_FONT_SIZE, 95)))

        saved_anchor = _sv(_KEY_TITLE_ANCHOR, "bottom-right")
        for i in range(self._title_anchor.count()):
            if self._title_anchor.itemData(i) == saved_anchor:
                self._title_anchor.setCurrentIndex(i)
                break
        self._title_margin.setValue(int(_sv(_KEY_TITLE_MARGIN, 40)))
        self._title_margin.setEnabled(saved_anchor != "center")

        saved_align = _sv(_KEY_TITLE_ALIGNMENT, "right")
        for i in range(self._title_alignment.count()):
            if self._title_alignment.itemData(i) == saved_align:
                self._title_alignment.setCurrentIndex(i)
                break

        self._title_color = _sv(_KEY_TITLE_COLOR, "#ffffff")
        self._update_color_btn(self._title_color_btn, self._title_color)
        self._title_shadow.setChecked(_sv(_KEY_TITLE_SHADOW, True, bool))
        self._title_duration.setValue(float(_sv(_KEY_TITLE_DURATION, 10.0)))
        self._title_fi_chk.setChecked(_sv(_KEY_TITLE_FI_ENABLED, True, bool))
        self._title_fi_dur.setValue(float(_sv(_KEY_TITLE_FI_DUR, 3.0)))
        self._title_fi_dur.setEnabled(self._title_fi_chk.isChecked())
        self._title_fo_chk.setChecked(_sv(_KEY_TITLE_FO_ENABLED, True, bool))
        self._title_fo_dur.setValue(float(_sv(_KEY_TITLE_FO_DUR, 3.0)))
        self._title_fo_dur.setEnabled(self._title_fo_chk.isChecked())

        self._title_preview.refresh()

    def get_settings(self) -> dict:
        """Return current clip effects settings as a flat dict."""
        return {
            _KEY_FI_ENABLED:   self._fi_group.isChecked(),
            _KEY_FI_BLACK_DUR: self._fi_black_spin.value(),
            _KEY_FI_FADE_DUR:  self._fi_fade_spin.value(),
            _KEY_FO_ENABLED:   self._fo_group.isChecked(),
            _KEY_FO_BLACK_DUR: self._fo_black_spin.value(),
            _KEY_FO_FADE_DUR:  self._fo_fade_spin.value(),
            _KEY_TITLE_ENABLED:   self._title_group.isChecked(),
            _KEY_TITLE_TEXT:      self._title_text.toPlainText(),
            _KEY_TITLE_FONT:      self._title_font.currentFont().family(),
            _KEY_TITLE_FONT_SIZE: self._title_font_size.value(),
            _KEY_TITLE_ANCHOR:    self._title_anchor.currentData(),
            _KEY_TITLE_MARGIN:    self._title_margin.value(),
            _KEY_TITLE_ALIGNMENT: self._title_alignment.currentData(),
            _KEY_TITLE_COLOR:     self._title_color,
            _KEY_TITLE_SHADOW:    self._title_shadow.isChecked(),
            _KEY_TITLE_DURATION:  self._title_duration.value(),
            _KEY_TITLE_FI_ENABLED: self._title_fi_chk.isChecked(),
            _KEY_TITLE_FI_DUR:     self._title_fi_dur.value(),
            _KEY_TITLE_FO_ENABLED: self._title_fo_chk.isChecked(),
            _KEY_TITLE_FO_DUR:     self._title_fo_dur.value(),
        }
