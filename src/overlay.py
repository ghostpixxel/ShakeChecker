"""PyQt6 catch-rate overlay: frameless, translucent, click-through, always-on-top.

Layout (top to bottom): animated Pokemon sprite + name; base catch rate + turn;
one row per Poke Ball = ball sprite + catch %, the % coloured by likelihood
(<30% red, 30-60% yellow, >60% green). Hidden outside battles. Docks to the
top-right inside the game window's client area.

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

SPRITE_H = 24  # Pokemon sprite height (px) — kept compact so the panel stays small
BALL_H = 20  # ball icon height (px)
# Fixed panel width. Must exceed the content's natural width or Qt enforces a
# larger, content-dependent minimum and the dock position jitters frame to frame.
PANEL_W = 168
DOCK_MARGIN = 12  # gap from the game window's top-right corner

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


class Overlay(QWidget):
    def __init__(self, ball_names: list[str], loader: SpriteLoader | None = None) -> None:
        super().__init__()
        self._loader = loader or SpriteLoader()
        self._movie: QMovie | None = None
        self._current_dex: int | None = None  # avoid restarting the GIF every frame
        self._last_pos: tuple[int, int] | None = None  # avoid redundant moves
        self._pct_labels: dict[str, QLabel] = {}

        self.setFixedWidth(PANEL_W)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput  # click-through
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(objectName="panel")
        panel.setStyleSheet(
            "#panel { background: rgba(18,18,20,180); border-radius: 10px; }"
            " QLabel { color: #eeeeee; background: transparent; }"
        )
        root.addWidget(panel)
        col = QVBoxLayout(panel)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(4)

        # header: sprite + name
        header = QHBoxLayout()
        header.setSpacing(8)
        self._sprite = QLabel()
        self._sprite.setFixedHeight(SPRITE_H)
        self._sprite.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._name = QLabel("—")
        name_font = QFont(mono)
        name_font.setPixelSize(16)
        name_font.setBold(True)
        self._name.setFont(name_font)
        self._name.setTextFormat(Qt.TextFormat.RichText)  # bold name + small "Lv.N"
        # Ignored width: a long name clips instead of widening the panel (which
        # would make the right-anchored dock position jump).
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status = QLabel()
        status_font = QFont(mono)
        status_font.setPixelSize(10)
        status_font.setBold(True)
        self._status.setFont(status_font)
        self._status.setVisible(False)
        header.addWidget(self._sprite)
        header.addWidget(self._name, 1)
        header.addWidget(self._status)
        col.addLayout(header)

        # subheader: catch rate + turn
        self._sub = QLabel(subheader_text(0, 1))
        sub_font = QFont(mono)
        sub_font.setPixelSize(13)
        self._sub.setFont(sub_font)
        self._sub.setStyleSheet("color: #aaaaaa;")
        self._sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(self._sub)

        # HP line (its own row, above the balls)
        self._hp = QLabel("")
        self._hp.setFont(sub_font)
        self._hp.setStyleSheet("color: #cfd2d6;")
        self._hp.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(self._hp)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(255,255,255,40);")
        col.addWidget(line)

        # one row per ball: icon + percent
        pct_font = QFont(mono)
        pct_font.setPixelSize(13)
        for name in ball_names:
            row = QHBoxLayout()
            row.setSpacing(8)
            icon = QLabel()
            icon.setPixmap(self._loader.ball_pixmap(name, BALL_H))
            icon.setFixedHeight(BALL_H)
            pct = QLabel("—")
            pct.setFont(pct_font)
            pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pct.setMinimumWidth(52)
            row.addWidget(icon)
            row.addStretch(1)
            row.addWidget(pct)
            col.addLayout(row)
            self._pct_labels[name] = pct

    # --- public API ---

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
        lvl = f' <span style="font-size:11px; color:#9aa0aa;">Lv.{level}</span>' if level else ""
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
        self.show()

    def hide_battle(self) -> None:
        if self._movie is not None:
            self._movie.stop()
        self._current_dex = None  # so re-entering a battle restarts the sprite
        self.hide()

    def dock_to(self, left: int, top: int, width: int) -> None:
        """Place the overlay at the top-right inside a client rect (PHYSICAL screen
        coords from win32). Convert to Qt logical coords (DPI scaling), anchor by
        the constant PANEL_W, and only move on change so it cannot jitter."""
        lx, ly = phys_to_logical(left + width, top)
        pos = (lx - PANEL_W - DOCK_MARGIN, ly + DOCK_MARGIN)
        if pos != self._last_pos:
            self._last_pos = pos
            self.move(*pos)

    # --- internals ---

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
        movie = self._loader.species_movie(dex_id, SPRITE_H)
        if movie is not None:
            self._movie = movie
            self._sprite.setMovie(movie)
            movie.start()
        else:
            self._sprite.setPixmap(self._loader.species_pixmap(dex_id, SPRITE_H))


def _demo() -> None:
    import json
    import sys
    from pathlib import Path

    from PyQt6.QtWidgets import QApplication

    data = Path(__file__).resolve().parent / "data"
    balls = [b["name"] for b in json.loads((data / "balls.json").read_text("utf-8"))["balls"]]

    app = QApplication(sys.argv)
    ov = Overlay(balls)
    # sample: Floatzel (dex 419), some made-up probabilities
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
