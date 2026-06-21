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

import math
from collections.abc import Callable

import win32con
import win32gui
from PyQt6.QtCore import QPointF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
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

# Base (scale 1.0) sizes in logical px (mirrors overlay.py's approach). The way
# sits right after the name and overlong ways are elided, so this only needs to
# fit a typical name + short way; long names still show in full (the way elides).
BASE_PANEL_W = 236
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


def _icon_pixmap(kind: str, size: int, color: str) -> QPixmap:
    """A small monochrome header icon drawn in the overlay's own colour (so it
    matches the panel instead of an OS emoji): 'gear', 'ball' or 'info'."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    cx = cy = size / 2
    if kind == "gear":
        ring = QPen(c, size * 0.15)
        p.setPen(ring)
        p.drawEllipse(QPointF(cx, cy), size * 0.25, size * 0.25)
        teeth = QPen(c, size * 0.14)
        teeth.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(teeth)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(
                QPointF(cx + math.cos(a) * size * 0.33, cy + math.sin(a) * size * 0.33),
                QPointF(cx + math.cos(a) * size * 0.46, cy + math.sin(a) * size * 0.46),
            )
    elif kind == "ball":
        p.setPen(QPen(c, size * 0.10))
        p.drawEllipse(QPointF(cx, cy), size * 0.40, size * 0.40)  # ball outline
        p.drawLine(QPointF(cx - size * 0.40, cy), QPointF(cx + size * 0.40, cy))  # band
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy), size * 0.14, size * 0.14)  # centre button
    else:  # info
        p.setPen(QPen(c, size * 0.10))
        p.drawEllipse(QPointF(cx, cy), size * 0.42, size * 0.42)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx, cy - size * 0.19), size * 0.065, size * 0.065)  # dot
        stem = QPen(c, size * 0.13)
        stem.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(stem)
        p.drawLine(QPointF(cx, cy - size * 0.02), QPointF(cx, cy + size * 0.22))
    p.end()
    return pm


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
        self._profiles: QWidget | None = None  # profile management popup
        self._balls: QWidget | None = None  # ball-picker popup
        self._rows: list[dict] = []  # reused row-widget pool, grown as needed

        # Close the header popups when ShakeChecker stops being the active app,
        # i.e. the user clicked back into the game window.
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.applicationStateChanged.connect(self._on_app_state_changed)

        # callbacks the app wires in (no-ops until set)
        self.on_toggle_caught: Callable[[int], None] | None = None
        self.on_select_profile: Callable[[str], None] | None = None
        self.on_create_profile: Callable[[str], None] | None = None
        self.on_delete_profile: Callable[[str], None] | None = None
        self.get_profiles: Callable[[], tuple[str | None, list[str]]] | None = None
        # ball picker: toggle one, set all, and read (balls=[(id, name)], hidden ids)
        self.on_toggle_ball: Callable[[str], None] | None = None
        self.on_set_all_balls: Callable[[bool], None] | None = None
        self.get_ball_state: Callable[[], tuple[list[tuple[str, str]], set[str]]] | None = None
        # dex mode: read/flip whether caught species stay in the list (issue #16)
        self.get_keep_caught: Callable[[], bool] | None = None
        self.on_toggle_keep_caught: Callable[[], None] | None = None

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
        panel = QFrame(objectName="panel")  # type: ignore[call-arg]  # PyQt6 accepts QObject kwargs
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
        self._root = root  # kept so show_here can force a synchronous relayout
        self._col = QVBoxLayout(panel)

        # top icon bar: stretch + profile (gear) + info. Icons are drawn (not OS
        # emoji) in apply_scale so they match the panel's colour/style.
        self._bar = QHBoxLayout()
        self._bar.addStretch(1)
        self._profile_btn = QPushButton()
        self._profile_btn.setToolTip("Profiles: create / load / delete")
        self._profile_btn.clicked.connect(self._toggle_profiles)
        self._balls_btn = QPushButton()
        self._balls_btn.setToolTip("Choose which balls to show")
        self._balls_btn.clicked.connect(self._toggle_balls)
        self._info_btn = QPushButton()
        self._info_btn.setToolTip("Rarity colour legend")
        self._info_btn.clicked.connect(self._toggle_legend)
        for b in (self._profile_btn, self._balls_btn, self._info_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bar.addWidget(self._profile_btn)
        self._bar.addWidget(self._balls_btn)
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
        self._scroll.viewport().setStyleSheet("background: transparent;")  # type: ignore[union-attr]
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
        self._name_fm = QFontMetrics(self._font(self._px(BASE_ROW_PX)))
        self._way_fm = QFontMetrics(self._font(self._px(BASE_SUB_PX)))
        self._title.setFont(self._font(self._px(BASE_TITLE_PX), bold=True))
        self._subtitle.setFont(self._font(self._px(BASE_SUB_PX)))
        isz = self._px(BASE_ICON_PX)
        for btn, kind in (
            (self._profile_btn, "gear"),
            (self._balls_btn, "ball"),
            (self._info_btn, "info"),
        ):
            btn.setIcon(QIcon(_icon_pixmap(kind, isz, "#cfd2d6")))
            btn.setIconSize(QSize(isz, isz))
            btn.setFixedSize(isz + self._px(6), isz + self._px(6))
        self._col.setContentsMargins(
            self._px(BASE_MARGIN_X),
            self._px(BASE_MARGIN_Y),
            self._px(BASE_MARGIN_X),
            self._px(BASE_MARGIN_Y),
        )
        self._col.setSpacing(self._px(BASE_COL_SPACING))
        self._list_layout.setSpacing(self._px(BASE_ROW_SPACING))
        for r in self._rows:
            self._style_row(r)
            self._clear_row_sprite(r)  # force reload at the new sprite size
        self._last_pos = None

    def show_here(self, view: LocationView) -> None:
        """Populate from a location view and show the panel."""
        keep_caught = self.get_keep_caught() if self.get_keep_caught is not None else True
        entries = display_order(view.entries, keep_caught=keep_caught)
        needed = sum(1 for e in view.entries if not e.caught)
        self._title.setText(view.route.title())
        self._subtitle.setText(
            f"{view.region.title()} · {view.period.value.title()} · "
            f"{season_name(view.season)} · {needed} left"
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
        # Recompute the outer layouts synchronously too, so the window shrinks to
        # the new content immediately instead of lagging a refresh behind.
        self._col.invalidate()
        self._col.activate()
        self._root.invalidate()
        self._root.activate()
        self.adjustSize()
        self.show()
        self._apply_click_through(True)  # start passing input through
        if not self._hover.isActive():
            self._hover.start()

    def hide_panel(self) -> None:
        self._hover.stop()
        self._hide_popups()
        for r in self._rows:  # stop GIFs while hidden; they reload on re-show
            self._clear_row_sprite(r)
        self.hide()

    def _hide_popups(self) -> None:
        for popup in (self._legend, self._profiles, self._balls):
            if popup is not None and popup.isVisible():
                popup.hide()

    def _on_app_state_changed(self, state: Qt.ApplicationState) -> None:
        # Close the header popups when focus leaves ShakeChecker for the game.
        # Our own modal dialogs (new/delete profile) keep the app Active, so this
        # never fires while one is open.
        if state == Qt.ApplicationState.ApplicationInactive:
            self._hide_popups()

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
        for popup in (self._legend, self._profiles, self._balls):
            if popup is not None and popup.isVisible():
                over = over or popup.frameGeometry().contains(QCursor.pos())
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

    def _toggle_profiles(self) -> None:
        if self._profiles is not None and self._profiles.isVisible():
            self._profiles.hide()
            return
        self._open_profiles()

    def _open_profiles(self) -> None:
        # rebuilt each time so it reflects the current profile list
        if self._profiles is not None:
            self._profiles.close()
        self._profiles = self._build_profiles()
        self._profiles.move(self._profile_btn.mapToGlobal(self._profile_btn.rect().bottomLeft()))
        self._profiles.show()

    def _build_profiles(self) -> QWidget:
        active, accounts = self.get_profiles() if self.get_profiles else (None, [])
        w, box = self._popup_window("profiles")
        head = QLabel("Profiles")
        head.setFont(self._font(12, bold=True))
        head.setStyleSheet("color: #ffffff;")
        box.addWidget(head)
        for name in accounts:
            row = QHBoxLayout()
            row.setSpacing(8)
            sw = QPushButton(("● " if name == active else "    ") + name)
            sw.setFont(self._font(12))
            sw.setCursor(Qt.CursorShape.PointingHandCursor)
            sw.setStyleSheet("QPushButton { text-align: left; }")
            sw.clicked.connect(lambda _=False, n=name: self._choose_profile(n))
            minus = QPushButton("−")
            minus.setFont(self._font(14, bold=True))
            minus.setCursor(Qt.CursorShape.PointingHandCursor)
            minus.setToolTip(f"Delete profile '{name}'")
            minus.setFixedWidth(20)
            minus.clicked.connect(lambda _=False, n=name: self._remove_profile(n))
            row.addWidget(sw, 1)
            row.addWidget(minus)
            cont = QWidget()
            cont.setLayout(row)
            box.addWidget(cont)
        new = QPushButton("+  New profile…")
        new.setFont(self._font(12))
        new.setCursor(Qt.CursorShape.PointingHandCursor)
        new.setStyleSheet("QPushButton { text-align: left; color: #9aa0aa; }")
        new.clicked.connect(self._create_profile)
        box.addWidget(new)

        # Dex-mode section: keep caught species in the list (checked, at bottom)
        # or hide them. See issue #16.
        dex_head = QLabel("Dex")
        dex_head.setFont(self._font(12, bold=True))
        dex_head.setStyleSheet("color: #ffffff;")
        box.addWidget(dex_head)
        keep = self.get_keep_caught() if self.get_keep_caught is not None else True
        toggle = QPushButton(("✓  " if keep else "    ") + "Show caught")
        toggle.setFont(self._font(12))
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setToolTip("Keep caught species in the list, checked, at the bottom")
        shade = "#eeeeee" if keep else "#777777"
        toggle.setStyleSheet(f"QPushButton {{ text-align: left; color: {shade}; }}")
        toggle.clicked.connect(self._toggle_keep_caught)
        box.addWidget(toggle)
        return w

    def _toggle_keep_caught(self) -> None:
        if self.on_toggle_keep_caught is not None:
            self.on_toggle_keep_caught()
        self._open_profiles()  # rebuild so the check state updates, popup stays open

    def _choose_profile(self, name: str) -> None:
        if self.on_select_profile is not None:
            self.on_select_profile(name)
        if self._profiles is not None:
            self._profiles.hide()

    def _remove_profile(self, name: str) -> None:
        ok = QMessageBox.question(
            self, "Delete profile", f"Delete profile '{name}' and its caught list?"
        )
        if ok == QMessageBox.StandardButton.Yes and self.on_delete_profile is not None:
            self.on_delete_profile(name)
            self._open_profiles()  # rebuild with the updated list

    def _create_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Account name:")
        if ok and name.strip() and self.on_create_profile is not None:
            self.on_create_profile(name.strip())
        if self._profiles is not None:
            self._profiles.hide()

    def _toggle_legend(self) -> None:
        if self._legend is None:
            self._legend = self._build_legend()
        if self._legend.isVisible():
            self._legend.hide()
            return
        self._legend.move(self._info_btn.mapToGlobal(self._info_btn.rect().bottomRight()))
        self._legend.show()

    def _popup_window(self, obj_name: str) -> tuple[QWidget, QVBoxLayout]:
        """A frameless dark popup matching the panel; returns (window, content box)."""
        w = QWidget(None)
        w.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        frame = QFrame(w, objectName=obj_name)  # type: ignore[call-arg]  # PyQt6 accepts QObject kwargs
        frame.setStyleSheet(
            f"#{obj_name} {{ background: rgba(18,18,20,238); border-radius: 8px; }}"
            " QLabel { color: #eeeeee; background: transparent; }"
            " QPushButton { color: #cfd2d6; background: transparent; border: none; }"
            " QPushButton:hover { color: #ffffff; }"
        )
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        box = QVBoxLayout(frame)
        box.setContentsMargins(10, 8, 12, 8)
        box.setSpacing(3)
        return w, box

    def _build_legend(self) -> QWidget:
        w, box = self._popup_window("legend")
        head = QLabel("Rarity")
        head.setFont(self._font(12, bold=True))
        head.setStyleSheet("color: #ffffff;")
        box.addWidget(head)
        for rarity, color in _RARITY_COLORS:
            lab = QLabel(f'<span style="color:{color};">● {rarity}</span>')
            lab.setFont(self._font(12))
            box.addWidget(lab)
        return w

    # --- ball picker (which balls the catch overlay shows) ---

    def _toggle_balls(self) -> None:
        if self._balls is not None and self._balls.isVisible():
            self._balls.hide()
            return
        self._open_balls()

    def _open_balls(self) -> None:
        if self._balls is not None:
            self._balls.close()
        self._balls = self._build_balls()
        self._balls.move(self._balls_btn.mapToGlobal(self._balls_btn.rect().bottomLeft()))
        self._balls.show()

    def _build_balls(self) -> QWidget:
        balls, hidden = self.get_ball_state() if self.get_ball_state else ([], set())
        w, box = self._popup_window("balls")
        head = QLabel("Show balls")
        head.setFont(self._font(12, bold=True))
        head.setStyleSheet("color: #ffffff;")
        box.addWidget(head)
        icon_h = self._px(BASE_SPRITE_H)
        for ball_id, ball_name in balls:
            shown = ball_id not in hidden
            sw = QPushButton(("✓  " if shown else "    ") + ball_name)
            sw.setFont(self._font(12))
            sw.setCursor(Qt.CursorShape.PointingHandCursor)
            sw.setIcon(QIcon(self._loader.ball_pixmap(ball_name, icon_h)))
            sw.setIconSize(QSize(icon_h, icon_h))
            shade = "#eeeeee" if shown else "#777777"
            sw.setStyleSheet(f"QPushButton {{ text-align: left; color: {shade}; }}")
            sw.clicked.connect(lambda _=False, i=ball_id: self._toggle_ball(i))
            box.addWidget(sw)
        row = QHBoxLayout()
        for text, vis in (("All", True), ("None", False)):
            b = QPushButton(text)
            b.setFont(self._font(11))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton { color: #9aa0aa; }")
            b.clicked.connect(lambda _=False, v=vis: self._set_all_balls(v))
            row.addWidget(b)
        row.addStretch(1)
        cont = QWidget()
        cont.setLayout(row)
        box.addWidget(cont)
        return w

    def _toggle_ball(self, ball_id: str) -> None:
        if self.on_toggle_ball is not None:
            self.on_toggle_ball(ball_id)
        self._open_balls()  # rebuild so the check state updates, popup stays open

    def _set_all_balls(self, visible: bool) -> None:
        if self.on_set_all_balls is not None:
            self.on_set_all_balls(visible)
        self._open_balls()

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
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        way = QLabel("")
        way.setStyleSheet("color: #9aa0aa;")
        way.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # The way stays right-aligned (a tidy column); the stretch between name and
        # way is the gap, kept small by the narrow panel. The way is pre-elided in
        # _fill_row to whatever space is left, so the (full) name is never cut.
        row.addWidget(sprite)
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(way)
        # insert above the trailing stretch so rows stay top-aligned
        self._list_layout.insertWidget(self._list_layout.count() - 1, container)
        r = {
            "box": row,
            "w": container,
            "sprite": sprite,
            "name": name,
            "way": way,
            "dex": None,
            "movie": None,
        }
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
        # Force a synchronous relayout first: when rows were just hidden (e.g. the
        # list shrank after toggling "show caught" off) the layout's sizeHint is
        # otherwise stale until the next event loop, so the panel would keep the
        # old, taller height for a refresh cycle.
        self._list_layout.invalidate()
        self._list_layout.activate()
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
        # Elide the way to whatever space remains right of the (full) name, so the
        # name is never cut and the panel can stay narrow. Hidden if almost no room.
        used = (
            2 * self._px(BASE_MARGIN_X)
            + self._px(BASE_SPRITE_COL_W)
            + self._name_fm.horizontalAdvance(entry.name)
            + 2 * self._px(BASE_ROW_SPACING)
        )
        budget = self._panel_w - used
        if not way or budget < self._px(16):
            r["way"].setText("")
        else:
            r["way"].setText(self._way_fm.elidedText(way, Qt.TextElideMode.ElideRight, budget))

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
    panel.get_ball_state = lambda: ([("poke", "Poké Ball"), ("dusk", "Dusk Ball")], {"dusk"})
    panel.on_toggle_ball = lambda i: print("toggle ball", i)
    panel.on_set_all_balls = lambda v: print("set all balls", v)
    panel.on_toggle_caught = lambda d: print("toggle", d)
    panel.on_select_profile = lambda n: print("select", n)
    panel.on_create_profile = lambda n: print("create", n)
    panel.show_here(view)
    panel.move(200, 200)
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    _demo()
