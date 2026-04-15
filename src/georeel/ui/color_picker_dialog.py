"""
CSS3/X11 named color picker dialog.

Shows all CSS3 named colors as swatches sorted by hue then lightness,
with name, hex, and HSL values displayed below each swatch.
"""

import colorsys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# ------------------------------------------------------------------
# Full CSS3 / X11 color list (147 entries; grey/gray duplicates kept)
# Deduplicated by hex when displayed so only one swatch per unique color.
# ------------------------------------------------------------------

_CSS3_COLORS_RAW: tuple[tuple[str, str], ...] = (
    ("AliceBlue",            "#F0F8FF"),
    ("AntiqueWhite",         "#FAEBD7"),
    ("Aqua",                 "#00FFFF"),
    ("Aquamarine",           "#7FFFD4"),
    ("Azure",                "#F0FFFF"),
    ("Beige",                "#F5F5DC"),
    ("Bisque",               "#FFE4C4"),
    ("Black",                "#000000"),
    ("BlanchedAlmond",       "#FFEBCD"),
    ("Blue",                 "#0000FF"),
    ("BlueViolet",           "#8A2BE2"),
    ("Brown",                "#A52A2A"),
    ("BurlyWood",            "#DEB887"),
    ("CadetBlue",            "#5F9EA0"),
    ("Chartreuse",           "#7FFF00"),
    ("Chocolate",            "#D2691E"),
    ("Coral",                "#FF7F50"),
    ("CornflowerBlue",       "#6495ED"),
    ("Cornsilk",             "#FFF8DC"),
    ("Crimson",              "#DC143C"),
    ("Cyan",                 "#00FFFF"),
    ("DarkBlue",             "#00008B"),
    ("DarkCyan",             "#008B8B"),
    ("DarkGoldenRod",        "#B8860B"),
    ("DarkGray",             "#A9A9A9"),
    ("DarkGreen",            "#006400"),
    ("DarkGrey",             "#A9A9A9"),
    ("DarkKhaki",            "#BDB76B"),
    ("DarkMagenta",          "#8B008B"),
    ("DarkOliveGreen",       "#556B2F"),
    ("DarkOrange",           "#FF8C00"),
    ("DarkOrchid",           "#9932CC"),
    ("DarkRed",              "#8B0000"),
    ("DarkSalmon",           "#E9967A"),
    ("DarkSeaGreen",         "#8FBC8F"),
    ("DarkSlateBlue",        "#483D8B"),
    ("DarkSlateGray",        "#2F4F4F"),
    ("DarkSlateGrey",        "#2F4F4F"),
    ("DarkTurquoise",        "#00CED1"),
    ("DarkViolet",           "#9400D3"),
    ("DeepPink",             "#FF1493"),
    ("DeepSkyBlue",          "#00BFFF"),
    ("DimGray",              "#696969"),
    ("DimGrey",              "#696969"),
    ("DodgerBlue",           "#1E90FF"),
    ("FireBrick",            "#B22222"),
    ("FloralWhite",          "#FFFAF0"),
    ("ForestGreen",          "#228B22"),
    ("Fuchsia",              "#FF00FF"),
    ("Gainsboro",            "#DCDCDC"),
    ("GhostWhite",           "#F8F8FF"),
    ("Gold",                 "#FFD700"),
    ("GoldenRod",            "#DAA520"),
    ("Gray",                 "#808080"),
    ("Green",                "#008000"),
    ("GreenYellow",          "#ADFF2F"),
    ("Grey",                 "#808080"),
    ("HoneyDew",             "#F0FFF0"),
    ("HotPink",              "#FF69B4"),
    ("IndianRed",            "#CD5C5C"),
    ("Indigo",               "#4B0082"),
    ("Ivory",                "#FFFFF0"),
    ("Khaki",                "#F0E68C"),
    ("Lavender",             "#E6E6FA"),
    ("LavenderBlush",        "#FFF0F5"),
    ("LawnGreen",            "#7CFC00"),
    ("LemonChiffon",         "#FFFACD"),
    ("LightBlue",            "#ADD8E6"),
    ("LightCoral",           "#F08080"),
    ("LightCyan",            "#E0FFFF"),
    ("LightGoldenRodYellow", "#FAFAD2"),
    ("LightGray",            "#D3D3D3"),
    ("LightGreen",           "#90EE90"),
    ("LightGrey",            "#D3D3D3"),
    ("LightPink",            "#FFB6C1"),
    ("LightSalmon",          "#FFA07A"),
    ("LightSeaGreen",        "#20B2AA"),
    ("LightSkyBlue",         "#87CEFA"),
    ("LightSlateGray",       "#778899"),
    ("LightSlateGrey",       "#778899"),
    ("LightSteelBlue",       "#B0C4DE"),
    ("LightYellow",          "#FFFFE0"),
    ("Lime",                 "#00FF00"),
    ("LimeGreen",            "#32CD32"),
    ("Linen",                "#FAF0E6"),
    ("Magenta",              "#FF00FF"),
    ("Maroon",               "#800000"),
    ("MediumAquaMarine",     "#66CDAA"),
    ("MediumBlue",           "#0000CD"),
    ("MediumOrchid",         "#BA55D3"),
    ("MediumPurple",         "#9370DB"),
    ("MediumSeaGreen",       "#3CB371"),
    ("MediumSlateBlue",      "#7B68EE"),
    ("MediumSpringGreen",    "#00FA9A"),
    ("MediumTurquoise",      "#48D1CC"),
    ("MediumVioletRed",      "#C71585"),
    ("MidnightBlue",         "#191970"),
    ("MintCream",            "#F5FFFA"),
    ("MistyRose",            "#FFE4E1"),
    ("Moccasin",             "#FFE4B5"),
    ("NavajoWhite",          "#FFDEAD"),
    ("Navy",                 "#000080"),
    ("OldLace",              "#FDF5E6"),
    ("Olive",                "#808000"),
    ("OliveDrab",            "#6B8E23"),
    ("Orange",               "#FFA500"),
    ("OrangeRed",            "#FF4500"),
    ("Orchid",               "#DA70D6"),
    ("PaleGoldenRod",        "#EEE8AA"),
    ("PaleGreen",            "#98FB98"),
    ("PaleTurquoise",        "#AFEEEE"),
    ("PaleVioletRed",        "#DB7093"),
    ("PapayaWhip",           "#FFEFD5"),
    ("PeachPuff",            "#FFDAB9"),
    ("Peru",                 "#CD853F"),
    ("Pink",                 "#FFC0CB"),
    ("Plum",                 "#DDA0DD"),
    ("PowderBlue",           "#B0E0E6"),
    ("Purple",               "#800080"),
    ("Red",                  "#FF0000"),
    ("RosyBrown",            "#BC8F8F"),
    ("RoyalBlue",            "#4169E1"),
    ("SaddleBrown",          "#8B4513"),
    ("Salmon",               "#FA8072"),
    ("SandyBrown",           "#F4A460"),
    ("SeaGreen",             "#2E8B57"),
    ("SeaShell",             "#FFF5EE"),
    ("Sienna",               "#A0522D"),
    ("Silver",               "#C0C0C0"),
    ("SkyBlue",              "#87CEEB"),
    ("SlateBlue",            "#6A5ACD"),
    ("SlateGray",            "#708090"),
    ("SlateGrey",            "#708090"),
    ("Snow",                 "#FFFAFA"),
    ("SpringGreen",          "#00FF7F"),
    ("SteelBlue",            "#4682B4"),
    ("Tan",                  "#D2B48C"),
    ("Teal",                 "#008080"),
    ("Thistle",              "#D8BFD8"),
    ("Tomato",               "#FF6347"),
    ("Turquoise",            "#40E0D0"),
    ("Violet",               "#EE82EE"),
    ("Wheat",                "#F5DEB3"),
    ("White",                "#FFFFFF"),
    ("WhiteSmoke",           "#F5F5F5"),
    ("Yellow",               "#FFFF00"),
    ("YellowGreen",          "#9ACD32"),
)

# ------------------------------------------------------------------
# Build deduplicated, sorted color list
# ------------------------------------------------------------------

def _hex_to_hsl(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    hf, lf, sf = colorsys.rgb_to_hls(r, g, b)
    return round(hf * 360), round(sf * 100), round(lf * 100)


_HUE_BUCKET_DEG = 15   # coarse hue quantization — keeps visually similar colors together
                        # and avoids the 0°/360° wrap splitting reds

def _sort_key(entry: tuple[str, str]) -> tuple[float, float, float]:
    _, hex_color = entry
    hue, sat, lit = _hex_to_hsl(hex_color)
    if sat < 8:                           # near-achromatic → group at the end by lightness
        return (999.0, 0.0, lit)
    bucket = float(hue // _HUE_BUCKET_DEG)      # 0…23 coarse hue band
    return (bucket, 0.0, lit)


def _hsl_label(hex_color: str) -> str:
    h, s, l = _hex_to_hsl(hex_color)
    return f"hsl({h}°, {s}%, {l}%)"


def _build_color_list() -> list[tuple[str, str]]:
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for name, hex_color in _CSS3_COLORS_RAW:
        key = hex_color.upper()
        if key not in seen:
            seen.add(key)
            unique.append((name, hex_color))
    unique.sort(key=_sort_key)
    return unique


CSS3_COLORS: list[tuple[str, str]] = _build_color_list()

_BY_NAME: dict[str, str] = {name.lower(): hex_color for name, hex_color in CSS3_COLORS}
_DEFAULT_COLOR_NAME = "ForestGreen"


def get_color_hex(name: str, fallback: str = "#228B22") -> str:
    """Return the hex value for a CSS3 color name (case-insensitive), or fallback."""
    return _BY_NAME.get(name.lower(), fallback)


# ------------------------------------------------------------------
# Swatch widget
# ------------------------------------------------------------------

_SWATCH_W   = 88
_SWATCH_H   = 48
_CELL_W     = _SWATCH_W + 4
_COLS       = 10


class _ColorSwatch(QFrame):
    def __init__(self, name: str, hex_color: str, on_select, parent=None):
        super().__init__(parent)
        self.color_name = name
        self.hex_color  = hex_color
        self._selected  = False
        self._on_select = on_select

        self.setFixedWidth(_CELL_W)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{name}\n{hex_color}\n{_hsl_label(hex_color)}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 4)
        layout.setSpacing(2)

        # Color block
        self._block = QLabel()
        self._block.setFixedHeight(_SWATCH_H)
        self._block.setAutoFillBackground(True)
        palette = self._block.palette()
        palette.setColor(self._block.backgroundRole(), QColor(hex_color))
        self._block.setPalette(palette)
        layout.addWidget(self._block)

        small = QFont()
        small.setPointSize(7)

        for text in (name, hex_color.upper(), _hsl_label(hex_color)):
            lbl = QLabel(text)
            lbl.setFont(small)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        self._update_border()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_border()

    def _update_border(self) -> None:
        from PySide6.QtWidgets import QApplication
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("")  # never let stylesheets cascade to children
        p = self.palette()
        if self._selected:
            # Compute an absolute contrasting background regardless of palette role names
            window_lum = QApplication.palette().color(QPalette.ColorRole.Window).lightness()
            target_lum = 220 if window_lum < 128 else 60  # light on dark, dark on light
            bg = QColor.fromHsl(0, 0, target_lum)
            fg = QColor.fromHsl(0, 0, 30 if target_lum > 128 else 220)
            p.setColor(QPalette.ColorRole.Window, bg)
            p.setColor(QPalette.ColorRole.WindowText, fg)
            self.setAutoFillBackground(True)
        else:
            app_palette = QApplication.palette()
            p.setColor(QPalette.ColorRole.Window,     app_palette.color(QPalette.ColorRole.Window))
            p.setColor(QPalette.ColorRole.WindowText, app_palette.color(QPalette.ColorRole.WindowText))
            self.setAutoFillBackground(False)
        self.setPalette(p)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_select(self)


# ------------------------------------------------------------------
# Dialog
# ------------------------------------------------------------------

class ColorPickerDialog(QDialog):
    """Grid color picker showing all CSS3/X11 named colors."""

    def __init__(self, current_name: str = _DEFAULT_COLOR_NAME,
                 current_custom_hex: str = "#228B22", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select pin color")
        self.setMinimumSize(940, 540)

        self._selected_name = current_name
        self._selected_hex  = get_color_hex(current_name, current_custom_hex)
        self._custom_hex    = current_custom_hex
        self._swatches: list[_ColorSwatch] = []
        self._active: _ColorSwatch | None = None

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # Search bar
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter colors…")
        self._search.textChanged.connect(self._filter)
        search_row.addWidget(self._search)
        root.addLayout(search_row)

        # Scroll area with grid
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(4)
        self._grid.setContentsMargins(4, 4, 4, 4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._grid_widget)
        root.addWidget(scroll, 1)

        # Bottom row: custom color + buttons
        bottom = QHBoxLayout()
        custom_btn = QPushButton("Custom color…")
        custom_btn.clicked.connect(self._pick_custom)
        bottom.addWidget(custom_btn)
        bottom.addStretch()

        self._preview_swatch = QLabel()
        self._preview_swatch.setFixedSize(28, 28)
        self._preview_swatch.setAutoFillBackground(True)
        self._preview_label = QLabel()
        bottom.addWidget(QLabel("Selected:"))
        bottom.addWidget(self._preview_swatch)
        bottom.addWidget(self._preview_label)
        bottom.addSpacing(12)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        root.addLayout(bottom)

        self._populate()
        self._update_preview()

    # ------------------------------------------------------------------

    def _populate(self) -> None:
        for i in reversed(range(self._grid.count())):
            item = self._grid.itemAt(i)
            w = item.widget() if item is not None else None
            if w:
                w.setParent(None)
        self._swatches.clear()
        self._active = None

        query = self._search.text().strip().lower()
        filtered = [(n, h) for n, h in CSS3_COLORS
                    if not query or query in n.lower() or query in h.lower()]

        for idx, (name, hex_color) in enumerate(filtered):
            sw = _ColorSwatch(name, hex_color, self._select_swatch, self._grid_widget)
            self._grid.addWidget(sw, idx // _COLS, idx % _COLS)
            self._swatches.append(sw)
            if name.lower() == self._selected_name.lower():
                sw.set_selected(True)
                self._active = sw

    def _filter(self) -> None:
        self._populate()

    def _select_swatch(self, swatch: _ColorSwatch) -> None:
        if self._active:
            self._active.set_selected(False)
        swatch.set_selected(True)
        self._active = swatch
        self._selected_name = swatch.color_name
        self._selected_hex  = swatch.hex_color
        self._update_preview()

    def _pick_custom(self) -> None:
        color = QColorDialog.getColor(QColor(self._custom_hex), self, "Custom color")
        if color.isValid():
            self._custom_hex    = color.name()
            self._selected_name = "custom"
            self._selected_hex  = self._custom_hex
            if self._active:
                self._active.set_selected(False)
                self._active = None
            self._update_preview()

    def _update_preview(self) -> None:
        palette = self._preview_swatch.palette()
        palette.setColor(self._preview_swatch.backgroundRole(),
                         QColor(self._selected_hex))
        self._preview_swatch.setPalette(palette)
        label = self._selected_name
        if self._selected_name != "custom":
            label += f"  {self._selected_hex.upper()}"
        self._preview_label.setText(label)

    # ------------------------------------------------------------------
    # Result accessors
    # ------------------------------------------------------------------

    def selected_name(self) -> str:
        return self._selected_name

    def selected_hex(self) -> str:
        return self._selected_hex

    def custom_hex(self) -> str:
        return self._custom_hex
