# WoW-style rarity -> name colour (user's scheme). Very Common/Horde fall back to
# the Common grey; unknown rarities too. Ordered for the info legend.
_RARITY_COLORS = [
    ("Common", "#9d9d9d"),
    ("Uncommon", "#ffffff"),
    ("Rare", "#3fcf5f"),
    ("Very Rare", "#4aa3ff"),
    ("Lure", "#b86bff"),
    ("Special", "#ffd633"),
]
_RARITY_COLOR = dict(_RARITY_COLORS) | {"Very Common": "#9d9d9d", "Horde": "#9d9d9d"}
_DEFAULT_COLOR = "#9d9d9d"


def rarity_color_hex(rarity: str) -> str:
    """Name colour for a rarity (WoW-style)."""
    return _RARITY_COLOR.get(rarity, _DEFAULT_COLOR)


_RED, _YELLOW, _GREEN = "#ff5555", "#ffcc44", "#55dd66"


def prob_color_hex(prob: float) -> str:
    """Colour hint for a catch probability (0-1): <35% red, 35-66% yellow, >=66% green."""
    if prob < 0.35:
        return _RED
    if prob < 0.66:
        return _YELLOW
    return _GREEN


def get_global_stylesheet() -> str:
    """Returns the central stylesheet (QSS) for the application."""
    return """
        #panel {
            background: rgba(18,18,20,180);
            border-radius: 10px;
        }

        #legend, #profiles {
            background: rgba(18,18,20,238);
            border-radius: 8px;
        }

        QLabel {
            color: #eeeeee;
            background: transparent;
        }

        QPushButton {
            color: #cfd2d6;
            background: transparent;
            border: none;
        }

        QPushButton:hover {
            color: #ffffff;
        }

        QLabel#PrimaryText {
            color: #cfd2d6;
        }

        QLabel#SecondaryText {
            color: #9aa0aa;
        }

        QLabel#SecondaryTextDark {
            color: #888888;
        }

        QLabel#HiddenText {
            color: #cccccc;
        }

        QFrame#Divider {
            color: rgba(255,255,255,40);
        }

        QPushButton#LeftAlignBtn {
            text-align: left;
        }

        QPushButton#LeftAlignBtnSecondary {
            text-align: left;
            color: #9aa0aa;
        }

        QPushButton#LeftAlignBtnChecked {
            text-align: left;
            color: #eeeeee;
        }

        QPushButton#LeftAlignBtnUnchecked {
            text-align: left;
            color: #777777;
        }

        QPushButton#RegionBtn {
            color: #ffffff;
            background: rgba(255,255,255,40);
            border-radius: 4px;
            padding: 4px 0px;
        }

        QPushButton#RegionBtn:hover {
            background: rgba(255,255,255,60);
        }

        QPushButton#RegionBtnInactive {
            color: #aaaaaa;
            background: rgba(255,255,255,10);
            border-radius: 4px;
            padding: 4px 0px;
        }

        QPushButton#RegionBtnInactive:hover {
            background: rgba(255,255,255,60);
            color: #ffffff;
        }

        QScrollArea {
            background: transparent;
            border: none;
        }

        QScrollBar:vertical {
            width: 6px;
            background: transparent;
            margin: 0;
        }

        QScrollBar::handle:vertical {
            background: rgba(255,255,255,30);
            min-height: 20px;
            border-radius: 3px;
        }

        QScrollBar::handle:vertical:hover {
            background: rgba(255,255,255,60);
        }

        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
            background: none;
        }

        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QSlider::groove:horizontal {
            border: 1px solid #444;
            height: 4px;
            background: #222;
            border-radius: 2px;
        }

        QSlider::handle:horizontal {
            background: #cfd2d6;
            width: 14px;
            margin: -5px 0;
            border-radius: 7px;
        }

        QCheckBox {
            color: #eeeeee;
        }
    """
