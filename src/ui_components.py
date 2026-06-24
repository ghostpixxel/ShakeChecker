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
    w.setWindowFlags(
        Qt.WindowType.FramelessWindowHint 
        | Qt.WindowType.Tool 
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
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



