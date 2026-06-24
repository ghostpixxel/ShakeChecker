"""Shared UI overlay logic: docking constants, window scaling, and Z-order management."""

from __future__ import annotations

from collections.abc import Callable

import win32api
import win32con
import win32gui
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QCursor, QFont, QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

# Base (scale 1.0) sizes in logical px. apply_scale() multiplies these; 1.0 is the
# cap, so the overlay is never larger than this.
DOCK_MARGIN = 12  # gap from the game window's side edge
DOCK_SIDE = "left"  # "left" or "right"
# Push the overlay down past the game's fixed top-left HUD (location, money,
# time, ability, and a possible donator line) AND the enemy HP meter block.
# The HUD and HP meters are fixed pixel sizes regardless of window size.
# The HP meter block (including the trainer party icons) extends to ~260px,
# so 270 ensures we clear it without being unnecessarily low.
DOCK_TOP_OFFSET = 180

# Window-height (physical px) at/above which the overlay is at full size, and the
# smallest scale it will shrink to. Below REF the overlay scales down with the
# window so it stays inside a small battle view.
REF_WINDOW_HEIGHT = 1000
MIN_SCALE = 0.6
UI_SCALE_MULTIPLIER = 1.0  # Manual override multiplier to increase UI size


def scale_for_window(height_px: int) -> float:
    """Overlay scale for a game-window client height."""
    return max(MIN_SCALE, min(1.0, height_px / REF_WINDOW_HEIGHT)) * UI_SCALE_MULTIPLIER


def phys_to_logical(px: int, py: int) -> tuple[int, int]:
    """Convert a physical-pixel screen point (from win32) to Qt's logical-pixel
    coordinates, which move() expects. They differ when Windows display scaling
    is not 100%; without this the overlay lands on the wrong monitor."""
    screens = QGuiApplication.screens()
    if not screens:
        return px, py

    monitors = win32api.EnumDisplayMonitors()
    monitors.sort(key=lambda m: m[2][0])
    screens_sorted = sorted(screens, key=lambda s: s.geometry().x())

    target_idx = 0
    for i, m in enumerate(monitors):
        left, top, right, bottom = m[2]
        if left <= px < right and top <= py < bottom:
            target_idx = i
            break

    if target_idx >= len(screens_sorted):
        target_idx = 0

    phys_left, phys_top, _, _ = monitors[target_idx][2]
    qt_screen = screens_sorted[target_idx]
    dpr = qt_screen.devicePixelRatio()

    lx = qt_screen.geometry().x() + (px - phys_left) / dpr
    ly = qt_screen.geometry().y() + (py - phys_top) / dpr

    return round(lx), round(ly)


def bring_overlay_above_game(widget: QWidget) -> None:
    """Insert the overlay just below the game window in the Z-order,
    without stealing focus or changing its state."""
    try:
        handle = widget.windowHandle()
        if not handle:
            return
        parent = handle.transientParent()
        if not parent:
            return
        game_hwnd = int(parent.winId())
        if not game_hwnd:
            return

        hwnd = int(widget.winId())
        prev_hwnd = win32gui.GetWindow(game_hwnd, win32con.GW_HWNDPREV)
        insert_after = prev_hwnd if prev_hwnd else win32con.HWND_TOP

        flags = (
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOACTIVATE
            | win32con.SWP_NOOWNERZORDER
        )
        win32gui.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags)
    except Exception:
        pass


class ResizeHandle(QWidget):
    """A small drag-handle pip at the bottom of the overlay for manual vertical resizing."""
    def __init__(self, on_drag: Callable[[int], None]):
        super().__init__()
        self.on_drag = on_drag
        self._drag_start_y: int | None = None
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setObjectName("ResizeHandle")
        # Keep a comfortably large hit target, but slightly smaller than before
        self.setFixedSize(100, 20)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.pip = QFrame()
        self.pip.setFixedSize(32, 4)
        self.pip.setObjectName("Pip")
        self.pip.setStyleSheet("""
            #Pip {
                background: rgba(255, 255, 255, 0.4);
                border-radius: 2px;
            }
            #ResizeHandle:hover #Pip {
                background: rgba(255, 255, 255, 0.8);
            }
        """)
        # Explicitly set the wrapper background transparent so Qt handles hit testing properly
        self.setStyleSheet("#ResizeHandle { background: transparent; }")
        layout.addWidget(self.pip)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_y = event.globalPosition().y()
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_start_y is not None:
            current_y = event.globalPosition().y()
            dy = current_y - self._drag_start_y
            if dy != 0:
                self.on_drag(dy)
                self._drag_start_y = current_y
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_y = None
            self.releaseMouse()
            event.accept()


class BaseOverlay(QWidget):
    """Shared parent class for DexPanel and BattlePanel overlays.
    Handles the transparent click-through window frame, generic top-bar layout,
    CSS initialization, scaling helpers, and Win32 docking logic."""

    def __init__(
        self, mode_name: str, mode_tooltip: str, base_panel_w: int, extra_css: str = ""
    ) -> None:
        super().__init__()
        self._scale = 0.0
        self._panel_w = base_panel_w
        self._last_pos: tuple[int, int] | tuple[int, int, int] | None = None
        self._click_through: bool | None = None
        self._manual_height: int | None = None

        self.on_mode_toggle: Callable[[], None] | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._mono = QFont("Consolas")
        self._mono.setStyleHint(QFont.StyleHint.Monospace)

        from ui_theme import get_global_stylesheet

        self.setStyleSheet(get_global_stylesheet() + extra_css)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("panel")
        self._root.addWidget(panel)
        
        self._main_layout = QVBoxLayout(panel)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)
        
        self._col = QVBoxLayout()
        self._main_layout.addLayout(self._col)

        self._bar = QHBoxLayout()
        self._mode_btn = QPushButton()
        self._mode_btn.setToolTip(mode_tooltip)
        self._mode_btn.clicked.connect(
            lambda: self.on_mode_toggle() if self.on_mode_toggle else None
        )
        self._bar.addWidget(self._mode_btn)

        self._mode_label = QLabel(mode_name)
        self._mode_label.setObjectName("PrimaryText")
        self._bar.addWidget(self._mode_label)
        self._bar.addStretch(1)

        self._settings_btn = QPushButton()
        self._settings_btn.setToolTip("Profiles: create / load / delete")

        self.setup_middle_btn()
        self._bar.addWidget(self._settings_btn)

        for b in (self._mode_btn, self._settings_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        self._col.addLayout(self._bar)

        self._hover = QTimer(self)
        self._hover.setInterval(40)
        self._hover.timeout.connect(self._check_hover)

        self._resize_handle = ResizeHandle(self._on_drag_resize)
        self._main_layout.addWidget(self._resize_handle, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)

    def _on_drag_resize(self, dy: int) -> None:
        new_h = max(100, self.height() + int(dy))
        self._manual_height = new_h
        self.setFixedHeight(new_h)
        # Re-apply docking to stay clamped if we grew downwards
        if self._last_pos is not None:
            if len(self._last_pos) == 3:
                self.dock_to(*self._last_pos)
            else:
                self.move(*self._last_pos)

    def setup_middle_btn(self) -> None:
        """Override in subclasses to insert a button before the profile button
        (e.g. Info or Balls)"""
        pass

    def _px(self, base: float) -> int:
        return max(1, round(base * self._scale))

    def _font(self, size_px: int, bold: bool = False) -> QFont:
        f = QFont(self._mono)
        f.setPixelSize(size_px)
        f.setBold(bold)
        return f

    def dock_to(self, left: int, top: int, client_w: int) -> None:
        """Move the overlay to its docked position."""
        if self._last_pos == (left, top, client_w):
            return
        self._last_pos = (left, top, client_w)
        x = (
            left + client_w - self.width() - DOCK_MARGIN
            if DOCK_SIDE == "right"
            else left + DOCK_MARGIN
        )
        lx, ly = phys_to_logical(x, top + DOCK_TOP_OFFSET)
        self.move(lx, ly)

    def _check_hover(self) -> None:
        if not self.isVisible():
            return
            
        # If the user is currently holding the left mouse button, they might be dragging
        # the resize handle or a scrollbar. Do not abruptly toggle click-through!
        from PyQt6.QtGui import Qt, QGuiApplication
        if QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            return
            
        over = self.frameGeometry().adjusted(-30, -30, 30, 30).contains(QCursor.pos())
        self._apply_click_through(not over)

    def _apply_click_through(self, on: bool) -> None:
        if on == self._click_through:
            return
        self._click_through = on
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if on:
            style |= win32con.WS_EX_TRANSPARENT
        else:
            style &= ~win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
        win32gui.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE
        )
