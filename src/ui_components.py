from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sprite_loader import SpriteLoader
from ui_theme import get_global_stylesheet, prob_color_hex, rarity_color_hex

ANIMATE_SPRITES = True  # Toggle animated GIFs for the Dex Panel (False saves CPU)


class BattleBallRow(QWidget):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self.icon = QLabel()
        self.label = QLabel(name)
        self.label.setObjectName("PrimaryText")

        self.pct = QLabel("-")
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._layout.addWidget(self.icon)
        self._layout.addWidget(self.label)
        self._layout.addStretch(1)
        self._layout.addWidget(self.pct)

    def apply_scale(
        self, font: QFont, icon_pixmap: QPixmap, height: int, pct_minw: int, spacing: int
    ) -> None:
        self._layout.setSpacing(spacing)
        self.icon.setPixmap(icon_pixmap)
        self.icon.setFixedHeight(height)
        self.label.setFont(font)
        self.pct.setFont(font)
        self.pct.setMinimumWidth(pct_minw)

    def set_prob(self, prob: float | None, unknown: bool) -> None:
        if unknown:
            self.pct.setText("??")
            self.pct.setObjectName("HiddenText")
            self.pct.setStyleSheet("")  # clear inline styles to let QSS take over
        elif prob is None:
            self.pct.setText("")
            self.pct.setObjectName("SecondaryTextDark")
            self.pct.setStyleSheet("")
        else:
            self.pct.setText(f"{100 * prob:5.1f}%")
            self.pct.setObjectName("")
            self.pct.setStyleSheet(f"color: {prob_color_hex(prob)};")


class DexSpeciesRow(QWidget):
    """A row that reports clicks (for per-species manual check-off) and manages its UI scale."""

    def __init__(self, index: int, on_click: Callable[[int], None]) -> None:
        super().__init__()
        self._index = index
        self._on_click = on_click
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.sprite = QLabel()
        self.sprite.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        self.name = QLabel("")
        self.name.setTextFormat(Qt.TextFormat.RichText)
        self.name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.way = QLabel("")
        self.way.setObjectName("SecondaryText")
        self.way.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(self.sprite)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.way)

        from PyQt6.QtGui import QMovie

        self.dex: int | None = None
        self.movie: QMovie | None = None

    def mousePressEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        self._on_click(self._index)

    def apply_scale(
        self, spacing: int, row_font: QFont, sub_font: QFont, col_w: int, sprite_h: int
    ) -> None:
        lay = self.layout()
        if lay:
            lay.setSpacing(spacing)
        self.name.setFont(row_font)
        self.way.setFont(sub_font)
        self.sprite.setFixedSize(col_w, sprite_h)

    def fill(
        self,
        entry,
        name_fm: QFontMetrics,
        way_fm: QFontMetrics,
        panel_w: int,
        margin_x: int,
        col_w: int,
        spacing: int,
        base_16: int,
    ) -> None:
        color = rarity_color_hex(entry.rarity)
        self.name.setText(f'<span style="color:{color};">{entry.name}</span>')
        way = "/".join(entry.ways)
        if entry.caught:
            way = (way + " ✓").strip()
        used = 2 * margin_x + col_w + name_fm.horizontalAdvance(entry.name) + 2 * spacing + base_16
        budget = panel_w - used
        if not way or budget < base_16:
            self.way.setText("")
        else:
            self.way.setText(way_fm.elidedText(way, Qt.TextElideMode.ElideRight, budget))

    def set_sprite(self, loader: SpriteLoader, dex_id: int, sprite_h: int, col_w: int) -> None:
        self.sprite.show()
        if dex_id == self.dex:
            if self.movie is not None:
                self.movie.start()
            return
        self.dex = dex_id
        if self.movie is not None:
            self.movie.stop()
            self.movie = None
        movie = loader.species_movie(dex_id, sprite_h, max_width=col_w) if ANIMATE_SPRITES else None
        if movie is not None:
            self.movie = movie
            self.sprite.setMovie(movie)
            movie.start()
        else:
            self.sprite.setPixmap(loader.species_pixmap(dex_id, sprite_h, max_width=col_w))

    def suspend_sprite(self) -> None:
        if self.movie is not None:
            self.movie.stop()

    def hide_sprite(self) -> None:
        if self.movie is not None:
            self.movie.stop()
        self.sprite.hide()

    def clear_sprite(self) -> None:
        if self.movie is not None:
            self.movie.stop()
            self.movie = None
        self.sprite.clear()
        self.sprite.show()
        self.dex = None


if TYPE_CHECKING:
    from dex_panel import DexPanel


def create_popup_window(
    obj_name: str, parent_widget: QWidget | None = None
) -> tuple[QWidget, QVBoxLayout]:
    """A frameless dark popup matching the panel; returns (window, content box)."""
    w = QWidget(parent_widget)
    w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    frame = QFrame(w)
    frame.setObjectName(obj_name)
    frame.setStyleSheet(get_global_stylesheet())
    outer = QVBoxLayout(w)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(frame)
    box = QVBoxLayout(frame)
    box.setContentsMargins(10, 8, 12, 8)
    box.setSpacing(3)
    return w, box


def build_legend(panel: DexPanel, parent_widget: QWidget | None = None) -> QWidget:
    from ui_theme import _RARITY_COLORS

    w, box = create_popup_window("legend", parent_widget)
    head = QLabel("Rarity")
    head.setFont(panel._font(12, bold=True))
    head.setObjectName("PrimaryText")
    box.addWidget(head)
    for rarity, color in _RARITY_COLORS:
        lab = QLabel(f'<span style="color:{color};">● {rarity}</span>')
        lab.setFont(panel._font(12))
        box.addWidget(lab)
    return w


def build_profiles(panel: DexPanel, parent_widget: QWidget | None = None) -> QWidget:
    active, accounts = panel.get_profiles() if panel.get_profiles else (None, [])
    w, box = create_popup_window("profiles", parent_widget)
    head = QLabel("Profiles")
    head.setFont(panel._font(12, bold=True))
    head.setObjectName("PrimaryText")
    box.addWidget(head)
    for name in accounts:
        row = QHBoxLayout()
        row.setSpacing(8)
        sw = QPushButton(("● " if name == active else "    ") + name)
        sw.setFont(panel._font(12))
        sw.setCursor(Qt.CursorShape.PointingHandCursor)
        sw.setObjectName("LeftAlignBtn")
        sw.clicked.connect(lambda _=False, n=name: panel._choose_profile(n))
        minus = QPushButton("−")
        minus.setFont(panel._font(14, bold=True))
        minus.setCursor(Qt.CursorShape.PointingHandCursor)
        minus.setToolTip(f"Delete profile '{name}'")
        minus.setFixedWidth(20)
        minus.clicked.connect(lambda _=False, n=name: panel._remove_profile(n))
        row.addWidget(sw, 1)
        row.addWidget(minus)
        cont = QWidget()
        cont.setLayout(row)
        box.addWidget(cont)
    new = QPushButton("+  New profile…")
    new.setFont(panel._font(12))
    new.setCursor(Qt.CursorShape.PointingHandCursor)
    new.setObjectName("LeftAlignBtnSecondary")
    new.clicked.connect(panel._create_profile)
    box.addWidget(new)

    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setObjectName("Divider")
    box.addWidget(sep)
    dex_head = QLabel("Dex")
    dex_head.setFont(panel._font(12, bold=True))
    dex_head.setObjectName("PrimaryText")
    box.addWidget(dex_head)
    keep = panel.get_keep_caught() if panel.get_keep_caught is not None else True
    toggle = QPushButton(("✓  " if keep else "    ") + "Show caught")
    toggle.setFont(panel._font(12))
    toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    toggle.setToolTip("Keep caught species in the list, checked, at the bottom")
    toggle.setObjectName("LeftAlignBtnChecked" if keep else "LeftAlignBtnUnchecked")
    toggle.clicked.connect(panel._toggle_keep_caught)
    box.addWidget(toggle)

    auto_switch = panel.get_auto_switch() if panel.get_auto_switch is not None else True
    auto_toggle = QPushButton(("✓  " if auto_switch else "    ") + "Auto-switch mode")
    auto_toggle.setFont(panel._font(12))
    auto_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    auto_toggle.setToolTip(
        "Automatically switch to Battle Mode when a battle starts, and Dex Mode in the overworld."
    )
    auto_toggle.setObjectName("LeftAlignBtnChecked" if auto_switch else "LeftAlignBtnUnchecked")
    auto_toggle.clicked.connect(panel._toggle_auto_switch)
    box.addWidget(auto_toggle)

    click_to_catch = panel.get_click_to_catch() if panel.get_click_to_catch is not None else True
    click_toggle = QPushButton(("✓  " if click_to_catch else "    ") + "Click to mark caught")
    click_toggle.setFont(panel._font(12))
    click_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    click_toggle.setToolTip("Click a species in the list to manually toggle its caught status.")
    click_toggle.setObjectName("LeftAlignBtnChecked" if click_to_catch else "LeftAlignBtnUnchecked")
    click_toggle.clicked.connect(panel._toggle_click_to_catch)
    box.addWidget(click_toggle)

    sep2 = QFrame()
    sep2.setFrameShape(QFrame.Shape.HLine)
    sep2.setObjectName("Divider")
    box.addWidget(sep2)
    reg_head = QLabel("Region Override")
    reg_head.setFont(panel._font(12, bold=True))
    reg_head.setObjectName("PrimaryText")
    box.addWidget(reg_head)

    reg_grid = QGridLayout()
    reg_grid.setContentsMargins(0, 0, 0, 0)
    reg_grid.setSpacing(4)
    curr = panel.get_current_region() if panel.get_current_region is not None else None
    for i, reg in enumerate(["Auto", "Kanto", "Johto", "Hoenn", "Sinnoh", "Unova"]):
        btn = QPushButton(reg)
        btn.setFont(panel._font(12))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        is_active = (curr == reg) or (curr is None and reg == "Auto")
        btn.setObjectName("RegionBtn" if is_active else "RegionBtnInactive")
        btn.clicked.connect(lambda _=False, r=reg: panel._region_changed(r))
        reg_grid.addWidget(btn, i // 3, i % 3)

    cont2 = QWidget()
    cont2.setLayout(reg_grid)
    box.addWidget(cont2)

    sep3 = QFrame()
    sep3.setFrameShape(QFrame.Shape.HLine)
    sep3.setObjectName("Divider")
    box.addWidget(sep3)

    scale_head_layout = QHBoxLayout()
    scale_head = QLabel("UI Scale")
    scale_head.setFont(panel._font(12, bold=True))
    scale_head.setObjectName("PrimaryText")

    panel._scale_val_label = QLabel("1.00x")
    panel._scale_val_label.setFixedWidth(40)
    panel._scale_val_label.setObjectName("PrimaryText")

    scale_head_layout.addWidget(scale_head)
    scale_head_layout.addStretch(1)
    scale_head_layout.addWidget(panel._scale_val_label)

    scale_head_w = QWidget()
    scale_head_w.setLayout(scale_head_layout)
    scale_head_layout.setContentsMargins(0, 0, 0, 0)
    box.addWidget(scale_head_w)

    scale_row = QHBoxLayout()

    import PyQt6.QtWidgets as QtWidgets

    panel._scale_auto_cb = QtWidgets.QCheckBox("Auto")
    panel._scale_auto_cb.setFont(panel._font(11))

    panel._scale_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
    panel._scale_slider.setRange(10, 200)  # 0.10 to 2.00
    panel._scale_slider.setCursor(Qt.CursorShape.PointingHandCursor)

    scale_row.addWidget(panel._scale_auto_cb)
    scale_row.addWidget(panel._scale_slider)
    box.addLayout(scale_row)

    curr_scale = panel.get_panel_scale() if panel.get_panel_scale else None
    if curr_scale is not None:
        panel._scale_auto_cb.setChecked(False)
        panel._scale_slider.setEnabled(True)
        panel._scale_slider.setValue(int(curr_scale * 100))
    else:
        panel._scale_auto_cb.setChecked(True)
        panel._scale_slider.setEnabled(False)
        panel._scale_slider.setValue(100)  # default value when auto is checked

    panel._scale_slider.valueChanged.connect(panel._scale_slider_changed)
    panel._scale_auto_cb.stateChanged.connect(panel._scale_auto_changed)

    return w
