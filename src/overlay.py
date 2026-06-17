"""PyQt6 catch-rate overlay: frameless, translucent, click-through, always-on-top.

Layout (top to bottom): Pokemon sprite + name (+ level / status badge); base
catch rate + turn; HP; one row per Poke Ball = sprite + name + catch %, the %
coloured by likelihood (<35% red, 35-66% yellow, >=66% green). Hidden outside
battles. Docks to the top corner inside the game window's client area.

Sizes are expressed at scale 1.0 (the maximum) and shrunk by apply_scale() when
the game window is small, so the overlay never overflows a small battle view and
never grows larger than its design size.

Read-only: the overlay only displays. Click-through means input passes straight
to the game underneath; the overlay never receives or sends any input.

Run standalone to preview the look without the game:
    python src/overlay.py
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont, QGuiApplication, QMovie
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from sprite_loader import SpriteLoader

# Base (scale 1.0) sizes in logical px. apply_scale() multiplies these; 1.0 is the
# cap, so the overlay is never larger than this.
BASE_PANEL_W = 210  # fits the widest obtainable name + " Lv.100" at 16px bold
BASE_SPRITE_H = 24
BASE_BALL_H = 20
BASE_NAME_PX = 16
BASE_SUB_PX = 13
BASE_ROW_PX = 13
BASE_STATUS_PX = 10
BASE_LEVEL_PX = 11
BASE_MARGIN_X = 12
BASE_MARGIN_Y = 10
BASE_COL_SPACING = 4
BASE_HEADER_SPACING = 8
BASE_ROW_SPACING = 6
BASE_PCT_MINW = 48
DOCK_MARGIN = 12  # gap from the game window's side edge
DOCK_SIDE = "left"  # "left" or "right"
# Push the overlay down past the game's fixed top-left HUD (location, money,
# time, ability, and a possible donator line) plus ~0.5 cm of gap. The HUD is a
# fixed pixel size regardless of window size, so this is a constant PHYSICAL
# offset added to the client top before conversion.
DOCK_TOP_OFFSET = 160

# Window-height (physical px) at/above which the overlay is at full size, and the
# smallest scale it will shrink to. Below REF the overlay scales down with the
# window so it stays inside a small battle view.
REF_WINDOW_HEIGHT = 1400
MIN_SCALE = 0.6

# probability colour thresholds (fraction 0-1) -> hex
_RED, _YELLOW, _GREEN = "#ff5555", "#ffcc44", "#55dd66"


def prob_color_hex(prob: float) -> str:
    """Colour hint for a catch probability (0-1): <35% red, 35-66% yellow, >=66% green."""
    if prob < 0.35:
        return _RED
    if prob < 0.66:
        return _YELLOW
    return _GREEN


def subheader_text(catch_rate: int, turn: int) -> str:
    return f"Rate: {catch_rate}  ·  Turn {turn}"


def scale_for_window(height_px: int) -> float:
    """Overlay scale for a game-window client height: 1.0 (the cap) down to MIN_SCALE."""
    return max(MIN_SCALE, min(1.0, height_px / REF_WINDOW_HEIGHT))


# Status code -> (label, badge background) following the in-game colour scheme.
_STATUS_BADGE = {
    "slp": ("SLP", "#7a7a7a"),
    "par": ("PAR", "#b59a00"),
    "psn": ("PSN", "#9b4dca"),
    "brn": ("BRN", "#d4602f"),
    "frz": ("FRZ", "#3f9fd4"),
}


def status_badge(status: str | None) -> tuple[str, str] | None:
    """(label, background colour) for a status, or None for no status (-> hidden)."""
    return _STATUS_BADGE.get(status.lower()) if status else None


def phys_to_logical(px: int, py: int) -> tuple[int, int]:
    """Convert a physical-pixel screen point (from win32) to Qt's logical-pixel
    coordinates, which move() expects. They differ when Windows display scaling
    is not 100%; without this the overlay lands on the wrong monitor. Uses the
    target screen's device pixel ratio (refined from the primary's first guess),
    which is exact for uniform scaling and close for mixed-DPI setups."""
    primary = QGuiApplication.primaryScreen()
    dpr = primary.devicePixelRatio() if primary else 1.0
    lx, ly = px / dpr, py / dpr
    screen = QGuiApplication.screenAt(QPoint(round(lx), round(ly)))
    if screen is not None and screen.devicePixelRatio() != dpr:
        dpr = screen.devicePixelRatio()
        lx, ly = px / dpr, py / dpr
    return round(lx), round(ly)


def visible_ball_order(
    ball_names: list[str], probs: dict[str, float], hidden: set[str]
) -> list[str]:
    """Balls to show in the overlay, best catch rate first. Drops hidden balls and
    any without a probability; ties keep the original ball order (stable sort)."""
    shown = [n for n in ball_names if n not in hidden and probs.get(n) is not None]
    return sorted(shown, key=lambda n: probs[n], reverse=True)


class Overlay(QWidget):
    def __init__(self, ball_names: list[str], loader: SpriteLoader | None = None) -> None:
        super().__init__()
        self._loader = loader or SpriteLoader()
        self._movie: QMovie | None = None
        self._current_dex: int | None = None  # avoid restarting the GIF every frame
        self._last_pos: tuple[int, int] | None = None  # avoid redundant moves
        self._ball_names = list(ball_names)
        self._ball_icons: dict[str, QLabel] = {}
        self._ball_name_labels: dict[str, QLabel] = {}
        self._pct_labels: dict[str, QLabel] = {}
        self._ball_rows: dict[str, QWidget] = {}  # one reorderable row widget per ball
        self._row_layouts: list[QHBoxLayout] = []
        self._hidden_names: set[str] = set()  # balls the user chose to hide
        self._last_order: list[str] | None = None  # skip reordering when unchanged
        self._scale = 0.0  # forces the first apply_scale to size everything
        self._panel_w = BASE_PANEL_W
        self._sprite_h = BASE_SPRITE_H
        self._level_px = BASE_LEVEL_PX

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput  # click-through
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
        )
        root.addWidget(panel)
        self._col = QVBoxLayout(panel)

        # header: sprite + name (+ status badge)
        self._header = QHBoxLayout()
        self._sprite = QLabel()
        self._sprite.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._name = QLabel("—")
        self._name.setTextFormat(Qt.TextFormat.RichText)  # bold name + small "Lv.N"
        # Ignored width: a long name clips instead of widening the panel.
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status = QLabel()
        self._status.setVisible(False)
        self._header.addWidget(self._sprite)
        self._header.addWidget(self._name, 1)
        self._header.addWidget(self._status)
        self._col.addLayout(self._header)

        self._sub = QLabel(subheader_text(0, 1))
        self._sub.setStyleSheet("color: #aaaaaa;")
        self._sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._col.addWidget(self._sub)

        self._hp = QLabel("")
        self._hp.setStyleSheet("color: #cfd2d6;")
        self._hp.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._col.addWidget(self._hp)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(255,255,255,40);")
        self._col.addWidget(line)

        # one row per ball: icon + name (left) + percent (right). Each row is its own
        # widget so show_battle() can reorder (best % on top) and hide filtered balls.
        for name in self._ball_names:
            roww = QWidget()
            row = QHBoxLayout(roww)
            row.setContentsMargins(0, 0, 0, 0)
            icon = QLabel()
            label = QLabel(name)
            label.setStyleSheet("color: #cfd2d6;")
            pct = QLabel("—")
            pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(icon)
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(pct)
            self._col.addWidget(roww)
            self._ball_rows[name] = roww
            self._row_layouts.append(row)
            self._ball_icons[name] = icon
            self._ball_name_labels[name] = label
            self._pct_labels[name] = pct
        # excess vertical space collapses into this stretch, so with fewer balls the
        # rows stay tightly spaced (the window shrinks) instead of spreading apart.
        self._col.addStretch(1)

        self.apply_scale(1.0)  # set fonts, sprites, widths at full size

    # --- public API ---

    def apply_scale(self, scale: float) -> None:
        """Resize the whole overlay by `scale` (<=1.0). Cheap no-op if unchanged."""
        scale = max(MIN_SCALE, min(1.0, scale))
        if abs(scale - self._scale) < 0.02:
            return
        self._scale = scale

        def px(base: float) -> int:
            return max(1, round(base * scale))

        self._panel_w = px(BASE_PANEL_W)
        self.setFixedWidth(self._panel_w)
        self._sprite_h = px(BASE_SPRITE_H)
        self._level_px = px(BASE_LEVEL_PX)

        name_font = self._font(px(BASE_NAME_PX), bold=True)
        row_font = self._font(px(BASE_ROW_PX))
        sub_font = self._font(px(BASE_SUB_PX))
        status_font = self._font(px(BASE_STATUS_PX), bold=True)
        self._name.setFont(name_font)
        self._sub.setFont(sub_font)
        self._hp.setFont(sub_font)
        self._status.setFont(status_font)

        self._sprite.setFixedHeight(self._sprite_h)
        ball_h = px(BASE_BALL_H)
        for ball, icon in self._ball_icons.items():
            icon.setPixmap(self._loader.ball_pixmap(ball, ball_h))
            icon.setFixedHeight(ball_h)
        for label in self._ball_name_labels.values():
            label.setFont(row_font)
        for pct in self._pct_labels.values():
            pct.setFont(row_font)
            pct.setMinimumWidth(px(BASE_PCT_MINW))

        self._col.setContentsMargins(
            px(BASE_MARGIN_X), px(BASE_MARGIN_Y), px(BASE_MARGIN_X), px(BASE_MARGIN_Y)
        )
        self._col.setSpacing(px(BASE_COL_SPACING))
        self._header.setSpacing(px(BASE_HEADER_SPACING))
        for row in self._row_layouts:
            row.setSpacing(px(BASE_ROW_SPACING))

        # reload the current sprite at the new size and force a re-dock
        if self._current_dex is not None:
            dex = self._current_dex
            self._current_dex = None
            self._set_sprite(dex)
        self.adjustSize()
        self._last_pos = None

    def show_battle(
        self,
        dex_id: int,
        name: str,
        catch_rate: int,
        turn: int,
        probs: dict[str, float],
        level: int | None = None,
        status: str | None = None,
        hp_pct: float | None = None,
    ) -> None:
        """Update the overlay for the current enemy and show it."""
        self._set_sprite(dex_id)
        lvl = (
            f' <span style="font-size:{self._level_px}px; color:#9aa0aa;">Lv.{level}</span>'
            if level
            else ""
        )
        self._name.setText(f"{name}{lvl}")
        self._sub.setText(subheader_text(catch_rate, turn))
        self._hp.setText(f"HP: {hp_pct:.0f}%" if hp_pct is not None else "")
        self._set_status(status)
        for ball, label in self._pct_labels.items():
            prob = probs.get(ball)
            if prob is None:
                label.setText("—")
                label.setStyleSheet("color: #888888;")
            else:
                label.setText(f"{100 * prob:5.1f}%")
                label.setStyleSheet(f"color: {prob_color_hex(prob)};")
        self._reorder(visible_ball_order(self._ball_names, probs, self._hidden_names))
        self.show()

    def set_hidden_names(self, names: set[str]) -> None:
        """Choose which balls the overlay shows (by ball NAME). Hidden balls drop
        out; the rest are sorted by catch rate on the next update."""
        self._hidden_names = set(names)
        self._last_order = None  # force a re-layout on the next show_battle

    def _reorder(self, order: list[str]) -> None:
        """Lay the ball rows out in `order` (best % first), hiding the rest. Skips
        the layout work when the order hasn't changed."""
        if order == self._last_order:
            return
        self._last_order = order
        for roww in self._ball_rows.values():
            self._col.removeWidget(roww)
            roww.setVisible(False)
        for name in order:
            roww = self._ball_rows[name]
            # insert before the trailing stretch (the layout's last item) so rows
            # stay top-aligned and tightly spaced
            self._col.insertWidget(self._col.count() - 1, roww)
            roww.setVisible(True)
        self._col.invalidate()  # drop the stale size hint so the window shrinks to fit
        self.adjustSize()

    def hide_battle(self) -> None:
        if self._movie is not None:
            self._movie.stop()
        self._current_dex = None  # so re-entering a battle restarts the sprite
        self.hide()

    def dock_to(self, left: int, top: int, width: int) -> None:
        """Dock below the game's top-left HUD, on the configured side, inside a
        client rect (PHYSICAL screen coords from win32). Convert to Qt logical
        coords (DPI), anchor by the constant panel width, and only move on change
        so it cannot jitter."""
        top += DOCK_TOP_OFFSET  # clear the location/money/time/ability HUD
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

    # --- internals ---

    def _font(self, size_px: int, bold: bool = False) -> QFont:
        f = QFont(self._mono)
        f.setPixelSize(size_px)
        f.setBold(bold)
        return f

    def _set_status(self, status: str | None) -> None:
        badge = status_badge(status)
        if badge is None:
            self._status.setVisible(False)
            return
        label, bg = badge
        self._status.setText(label)
        self._status.setStyleSheet(
            f"color: #ffffff; background: {bg}; border-radius: 3px; padding: 1px 3px;"
        )
        self._status.setVisible(True)

    def _set_sprite(self, dex_id: int) -> None:
        # Only (re)load on a species change; otherwise an animated GIF would be
        # restarted to frame 0 every tick and look frozen.
        if dex_id == self._current_dex:
            return
        self._current_dex = dex_id
        if self._movie is not None:
            self._movie.stop()
            self._movie = None
        movie = self._loader.species_movie(dex_id, self._sprite_h)
        if movie is not None:
            self._movie = movie
            self._sprite.setMovie(movie)
            movie.start()
        else:
            self._sprite.setPixmap(self._loader.species_pixmap(dex_id, self._sprite_h))


def _demo() -> None:
    import json
    import sys

    from PyQt6.QtWidgets import QApplication

    import paths

    data = paths.DATA_DIR
    balls = [b["name"] for b in json.loads((data / "balls.json").read_text("utf-8"))["balls"]]

    app = QApplication(sys.argv)
    ov = Overlay(balls)
    sample = {
        "Poké Ball": 0.098,
        "Great Ball": 0.147,
        "Ultra Ball": 0.196,
        "Net Ball": 0.343,
        "Quick Ball": 0.49,
        "Dusk Ball": 0.245,
    }
    ov.show_battle(419, "Floatzel", 75, 2, sample, level=24, status="psn", hp_pct=58)
    ov.move(200, 200)  # standalone: no game window to dock to
    ov.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    _demo()
