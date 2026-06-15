"""PyQt6 dex "missing here" panel: frameless, translucent, always-on-top, shown
while walking the overworld (hidden in battle, where the catch overlay takes
over). Docks below the game's HUD like the catch overlay.

Layout: a top icon bar (profile + info), a route header (name + region/time/
season + still-needed count), then a VERTICALLY SCROLLABLE list of sprite + name
+ way rows -- every uncaught species (dex order), then the already-caught
Lure/Rare/Very Rare ones (✓), via dex_tracker.display_order. The list height is
capped (BASE_MAX_LIST_H); longer lists scroll. Name colour = rarity (WoW-style).

Interaction is HOVER-TO-INTERACT: the window is click-through (input passes to
the game) until the cursor is over it, then it accepts clicks (icons, per-row
check-off) and the wheel scrolls. Click-through is toggled via the Win32
WS_EX_TRANSPARENT extended style; a short timer polls the cursor.

The app wires four callbacks: on_toggle_caught(dex_id), on_select_profile(name),
on_create_profile(name), get_profiles()->(active, [names]).

Preview without the game:  python src/dex_panel.py
"""

from __future__ import annotations

from collections.abc import Callable

import win32con
import win32gui
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dex_session import LocationView
from dex_tracker import display_order
from game_time import season_name
from overlay import DOCK_MARGIN, DOCK_SIDE, DOCK_TOP_OFFSET, MIN_SCALE, phys_to_logical
from sprite_loader import SpriteLoader

HOVER_POLL_MS = 120  # how often to check if the cursor is over the panel

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

# Base (scale 1.0) sizes in logical px (mirrors overlay.py's approach). Wide enough
# that long names incl. forms ("Gastrodon-East") + a way ("Good Rod/Old Rod") and
# the region/time/season subtitle all fit; they never show with the catch overlay.
BASE_PANEL_W = 292
BASE_SPRITE_H = 22
BASE_SPRITE_COL_W = 30  # fixed sprite-column width so names start flush
BASE_TITLE_PX = 15
BASE_SUB_PX = 11
BASE_ROW_PX = 13
BASE_ICON_PX = 15
BASE_MARGIN_X = 12
BASE_MARGIN_Y = 10
BASE_COL_SPACING = 3
BASE_ROW_SPACING = 6
DEX_MAX_VISIBLE_ROWS = 6  # show at most this many rows; the rest scroll


def rarity_color_hex(rarity: str) -> str:
    """Name colour for a rarity (WoW-style)."""
    return _RARITY_COLOR.get(rarity, _DEFAULT_COLOR)


class _ClickRow(QWidget):
    """A row that reports clicks (for per-species manual check-off)."""

    def __init__(self, index: int, on_click: Callable[[int], None]) -> None:
        super().__init__()
        self._index = index
        self._on_click = on_click
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        self._on_click(self._index)


class DexPanel(QWidget):
    def __init__(self, loader: SpriteLoader | None = None) -> None:
        super().__init__()
        self._loader = loader or SpriteLoader()
        self._scale = 0.0
        self._panel_w = BASE_PANEL_W
        self._sprite_h = BASE_SPRITE_H
        self._last_pos: tuple[int, int] | None = None
        self._click_through: bool | None = None  # current WS_EX_TRANSPARENT state
        self._legend: QWidget | None = None
        self._rows: list[dict] = []  # reused row-widget pool, grown as needed

        # callbacks the app wires in (no-ops until set)
        self.on_toggle_caught: Callable[[int], None] | None = None
        self.on_select_profile: Callable[[str], None] | None = None
        self.on_create_profile: Callable[[str], None] | None = None
        self.get_profiles: Callable[[], tuple[str | None, list[str]]] | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # click-through handled via Win32 (hover toggle)
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._mono = QFont("Consolas")
        self._mono.setStyleHint(QFont.StyleHint.Monospace)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(objectName="panel")
        panel.setStyleSheet(
            "#panel { background: rgba(18,18,20,180); border-radius: 10px; }"
            " QLabel { color: #eeeeee; background: transparent; }"
            " QPushButton { color: #cfd2d6; background: transparent; border: none; }"
            " QPushButton:hover { color: #ffffff; }"
            " QScrollArea { background: transparent; border: none; }"
            " QScrollBar:vertical { width: 6px; background: transparent; margin: 0; }"
            " QScrollBar::handle:vertical { background: rgba(255,255,255,70);"
            " border-radius: 3px; min-height: 20px; }"
            " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            " QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
            " background: transparent; }"
        )
        root.addWidget(panel)
        self._col = QVBoxLayout(panel)

        # top icon bar: stretch + profile (gear) + info
        self._bar = QHBoxLayout()
        self._bar.addStretch(1)
        self._profile_btn = QPushButton("⚙")  # gear
        self._profile_btn.setToolTip("Profile: create / load")
        self._profile_btn.clicked.connect(self._open_profile_menu)
        self._info_btn = QPushButton("ℹ")  # info
        self._info_btn.setToolTip("Rarity colour legend")
        self._info_btn.clicked.connect(self._toggle_legend)
        self._bar.addWidget(self._profile_btn)
        self._bar.addWidget(self._info_btn)
        self._col.addLayout(self._bar)

        self._title = QLabel("—")
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._col.addWidget(self._title)
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet("color: #aaaaaa;")
        self._subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._col.addWidget(self._subtitle)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(255,255,255,40);")
        self._col.addWidget(line)

        # scrollable species list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._list = QWidget()
        self._list.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 8, 0)  # gap so the scrollbar clears the way text
        self._list_layout.addStretch(1)  # keep rows top-aligned
        self._scroll.setWidget(self._list)
        self._col.addWidget(self._scroll)

        self._hover = QTimer(self)
        self._hover.setInterval(HOVER_POLL_MS)
        self._hover.timeout.connect(self._check_hover)

        self.apply_scale(1.0)

    # --- public API ---

    def apply_scale(self, scale: float) -> None:
        scale = max(MIN_SCALE, min(1.0, scale))
        if abs(scale - self._scale) < 0.02:
            return
        self._scale = scale
        self._panel_w = self._px(BASE_PANEL_W)
        self.setFixedWidth(self._panel_w)
        self._sprite_h = self._px(BASE_SPRITE_H)
        self._title.setFont(self._font(self._px(BASE_TITLE_PX), bold=True))
        self._subtitle.setFont(self._font(self._px(BASE_SUB_PX)))
        icon_font = self._font(self._px(BASE_ICON_PX))
        self._profile_btn.setFont(icon_font)
        self._info_btn.setFont(icon_font)
        self._col.setContentsMargins(
            self._px(BASE_MARGIN_X), self._px(BASE_MARGIN_Y),
            self._px(BASE_MARGIN_X), self._px(BASE_MARGIN_Y),
        )
        self._col.setSpacing(self._px(BASE_COL_SPACING))
        self._list_layout.setSpacing(self._px(BASE_ROW_SPACING))
        for r in self._rows:
            self._style_row(r)
            self._clear_row_sprite(r)  # force reload at the new sprite size
        self._last_pos = None

    def show_here(self, view: LocationView) -> None:
        """Populate from a location view and show the panel."""
        entries = display_order(view.entries)
        needed = sum(1 for e in view.entries if not e.caught)
        self._title.setText(view.route.title())
        self._subtitle.setText(
            f"{view.region.title()} · {view.period.value.title()} · "
            f"{season_name(view.season)} — {needed} needed"
        )
        self._ensure_rows(max(1, len(entries)))
        for i, r in enumerate(self._rows):
            if i < len(entries):
                self._fill_row(r, entries[i])
                r["w"].setVisible(True)
            else:
                self._clear_row_sprite(r)
                r["w"].setVisible(False)
        if not entries:  # nothing left here
            r0 = self._rows[0]
            self._clear_row_sprite(r0)
            r0["w"].setVisible(True)
            r0["name"].setText('<span style="color:#9aa0aa;">all caught here!</span>')
            r0["way"].setText("")
        self._fit_list_height()
        self.adjustSize()
        self.show()
        self._apply_click_through(True)  # start passing input through
        if not self._hover.isActive():
            self._hover.start()

    def hide_panel(self) -> None:
        self._hover.stop()
        if self._legend is not None:
            self._legend.hide()
        for r in self._rows:  # stop GIFs while hidden; they reload on re-show
            self._clear_row_sprite(r)
        self.hide()

    def dock_to(self, left: int, top: int, width: int) -> None:
        """Dock below the HUD on the configured side (same spot as the catch
        overlay, which is hidden while this shows). PHYSICAL coords in."""
        top += DOCK_TOP_OFFSET
        if DOCK_SIDE == "left":
            lx, ly = phys_to_logical(left, top)
            x = lx + DOCK_MARGIN
        else:
            lx, ly = phys_to_logical(left + width, top)
            x = lx - self._panel_w - DOCK_MARGIN
        pos = (x, ly)
        if pos != self._last_pos:
            self._last_pos = pos
            self.move(*pos)

    # --- interaction (hover -> click-through toggle) ---

    def _check_hover(self) -> None:
        if not self.isVisible():
            return
        over = self.frameGeometry().contains(QCursor.pos())
        if self._legend is not None and self._legend.isVisible():
            over = over or self._legend.frameGeometry().contains(QCursor.pos())
        self._apply_click_through(not over)

    def _apply_click_through(self, on: bool) -> None:
        """Toggle the Win32 WS_EX_TRANSPARENT bit so input passes to the game
        (on=True) or is received by the panel (on=False). Only on change."""
        if on == self._click_through:
            return
        self._click_through = on
        hwnd = int(self.winId())
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        ex = (ex | win32con.WS_EX_TRANSPARENT) if on else (ex & ~win32con.WS_EX_TRANSPARENT)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    def _row_clicked(self, index: int) -> None:
        dex = self._rows[index]["dex"] if index < len(self._rows) else None
        if dex is not None and self.on_toggle_caught is not None:
            self.on_toggle_caught(dex)

    def _open_profile_menu(self) -> None:
        active, accounts = self.get_profiles() if self.get_profiles else (None, [])
        menu = QMenu(self)
        for name in accounts:
            act = menu.addAction(("● " if name == active else "   ") + name)
            act.triggered.connect(lambda _checked=False, n=name: self._select_profile(n))
        if accounts:
            menu.addSeparator()
        menu.addAction("New profile…").triggered.connect(self._create_profile)
        menu.exec(self._profile_btn.mapToGlobal(self._profile_btn.rect().bottomLeft()))

    def _select_profile(self, name: str) -> None:
        if self.on_select_profile is not None:
            self.on_select_profile(name)

    def _create_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Account name:")
        if ok and name.strip() and self.on_create_profile is not None:
            self.on_create_profile(name.strip())

    def _toggle_legend(self) -> None:
        if self._legend is None:
            self._legend = self._build_legend()
        if self._legend.isVisible():
            self._legend.hide()
            return
        self._legend.move(self._info_btn.mapToGlobal(self._info_btn.rect().bottomRight()))
        self._legend.show()

    def _build_legend(self) -> QWidget:
        w = QWidget(None)
        w.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        frame = QFrame(w, objectName="legend")
        frame.setStyleSheet("#legend { background: rgba(18,18,20,225); border-radius: 8px; }")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        box = QVBoxLayout(frame)
        box.setContentsMargins(10, 8, 12, 8)
        head = QLabel("Rarity")
        head.setFont(self._font(12, bold=True))
        head.setStyleSheet("color: #ffffff;")
        box.addWidget(head)
        for rarity, color in _RARITY_COLORS:
            lab = QLabel(f'<span style="color:{color};">● {rarity}</span>')
            lab.setFont(self._font(12))
            box.addWidget(lab)
        return w

    # --- internals ---

    def _ensure_rows(self, n: int) -> None:
        while len(self._rows) < n:
            self._make_row()

    def _make_row(self) -> None:
        index = len(self._rows)
        container = _ClickRow(index, self._row_clicked)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        sprite = QLabel()
        sprite.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        name = QLabel("")
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        way = QLabel("")
        way.setStyleSheet("color: #9aa0aa;")
        way.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # name fills the gap up to the (right-aligned) way, so it clips much later
        row.addWidget(sprite)
        row.addWidget(name, 1)
        row.addWidget(way)
        # insert above the trailing stretch so rows stay top-aligned
        self._list_layout.insertWidget(self._list_layout.count() - 1, container)
        r = {"box": row, "w": container, "sprite": sprite, "name": name, "way": way,
             "dex": None, "movie": None}
        self._style_row(r)
        self._rows.append(r)

    def _style_row(self, r: dict) -> None:
        r["box"].setSpacing(self._px(BASE_ROW_SPACING))
        r["name"].setFont(self._font(self._px(BASE_ROW_PX)))
        r["way"].setFont(self._font(self._px(BASE_SUB_PX)))
        r["sprite"].setFixedSize(self._px(BASE_SPRITE_COL_W), self._sprite_h)

    def _fit_list_height(self) -> None:
        """Size the scroll viewport to the content, capped at DEX_MAX_VISIBLE_ROWS
        rows (the rest scroll)."""
        self._list.adjustSize()
        content = self._list.sizeHint().height()
        row_h = self._rows[0]["w"].sizeHint().height() if self._rows else self._sprite_h
        spacing = self._px(BASE_ROW_SPACING)
        cap = DEX_MAX_VISIBLE_ROWS * row_h + (DEX_MAX_VISIBLE_ROWS - 1) * spacing
        self._scroll.setFixedHeight(min(content, cap))

    def _fill_row(self, r: dict, entry) -> None:
        self._set_row_sprite(r, entry.id)
        color = rarity_color_hex(entry.rarity)
        r["name"].setText(f'<span style="color:{color};">{entry.name}</span>')
        way = "/".join(entry.ways)
        if entry.caught:
            way = (way + " ✓").strip()
        r["way"].setText(way)

    def _set_row_sprite(self, r: dict, dex_id: int) -> None:
        # Reload only on a species change so an animated GIF isn't restarted to
        # frame 0 each refresh (the panel re-renders every couple of seconds).
        if dex_id == r["dex"]:
            return
        r["dex"] = dex_id
        col_w = self._px(BASE_SPRITE_COL_W)
        if r["movie"] is not None:
            r["movie"].stop()
            r["movie"] = None
        movie = self._loader.species_movie(dex_id, self._sprite_h, max_width=col_w)
        if movie is not None:
            r["movie"] = movie
            r["sprite"].setMovie(movie)
            movie.start()
        else:
            r["sprite"].setPixmap(
                self._loader.species_pixmap(dex_id, self._sprite_h, max_width=col_w)
            )

    def _clear_row_sprite(self, r: dict) -> None:
        if r["movie"] is not None:
            r["movie"].stop()
            r["movie"] = None
        r["sprite"].clear()
        r["dex"] = None

    def _px(self, base: float) -> int:
        return max(1, round(base * self._scale))

    def _font(self, size_px: int, bold: bool = False) -> QFont:
        f = QFont(self._mono)
        f.setPixelSize(size_px)
        f.setBold(bold)
        return f


def _demo() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    from dex_tracker import DexEntry
    from game_time import Period

    entries = [
        DexEntry(1, "Bulbasaur", (), "Lure", False),
        DexEntry(10, "Caterpie", (), "Common", False),
        DexEntry(72, "Tentacool", ("Water",), "Uncommon", False),
        DexEntry(73, "Tentacruel", ("Water",), "Rare", False),
        DexEntry(129, "Magikarp", ("Good Rod", "Old Rod"), "Very Common", False),
        DexEntry(130, "Gyarados", ("Super Rod",), "Very Rare", False),
        DexEntry(16, "Pidgey", (), "Common", False),
        DexEntry(19, "Rattata", (), "Common", False),
        DexEntry(21, "Spearow", (), "Uncommon", False),
        DexEntry(143, "Snorlax", (), "Rare", True),
    ]
    view = LocationView("ROUTE 218", "SINNOH", Period.NIGHT, 1, entries)

    app = QApplication(sys.argv)
    panel = DexPanel()
    panel.get_profiles = lambda: ("Red", ["Red", "Blue"])
    panel.on_toggle_caught = lambda d: print("toggle", d)
    panel.on_select_profile = lambda n: print("select", n)
    panel.on_create_profile = lambda n: print("create", n)
    panel.show_here(view)
    panel.move(200, 200)
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    _demo()
