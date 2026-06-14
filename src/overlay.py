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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QMovie
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from sprite_loader import SpriteLoader

SPRITE_H = 28  # Pokemon sprite height (px) — kept compact so the panel stays small
BALL_H = 24  # ball icon height (px)
PANEL_W = 190  # fixed panel width: stops the dock position jumping as text widths change
DOCK_MARGIN = 12  # gap from the game window's top-right corner

# probability colour thresholds (fraction 0-1) -> hex
_RED, _YELLOW, _GREEN = "#ff5555", "#ffcc44", "#55dd66"


def prob_color_hex(prob: float) -> str:
    """Colour hint for a catch probability (0-1): <30% red, 30-60% yellow, else green."""
    if prob < 0.30:
        return _RED
    if prob < 0.60:
        return _YELLOW
    return _GREEN


def subheader_text(catch_rate: int, turn: int) -> str:
    return f"Rate: {catch_rate}   ·   Turn {turn}"


class Overlay(QWidget):
    def __init__(self, ball_names: list[str], loader: SpriteLoader | None = None) -> None:
        super().__init__()
        self._loader = loader or SpriteLoader()
        self._movie: QMovie | None = None
        self._current_dex: int | None = None  # avoid restarting the GIF every frame
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
        name_font.setPixelSize(20)
        name_font.setBold(True)
        self._name.setFont(name_font)
        header.addWidget(self._sprite)
        header.addWidget(self._name, 1)
        col.addLayout(header)

        # subheader: catch rate + turn
        self._sub = QLabel(subheader_text(0, 1))
        sub_font = QFont(mono)
        sub_font.setPixelSize(13)
        self._sub.setFont(sub_font)
        self._sub.setStyleSheet("color: #aaaaaa;")
        col.addWidget(self._sub)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(255,255,255,40);")
        col.addWidget(line)

        # one row per ball: icon + percent
        pct_font = QFont(mono)
        pct_font.setPixelSize(16)
        for name in ball_names:
            row = QHBoxLayout()
            row.setSpacing(8)
            icon = QLabel()
            icon.setPixmap(self._loader.ball_pixmap(name, BALL_H))
            icon.setFixedHeight(BALL_H)
            pct = QLabel("—")
            pct.setFont(pct_font)
            pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pct.setMinimumWidth(64)
            row.addWidget(icon)
            row.addStretch(1)
            row.addWidget(pct)
            col.addLayout(row)
            self._pct_labels[name] = pct

    # --- public API ---

    def show_battle(
        self, dex_id: int, name: str, catch_rate: int, turn: int, probs: dict[str, float]
    ) -> None:
        """Update the overlay for the current enemy and show it."""
        self._set_sprite(dex_id)
        self._name.setText(name)
        self._sub.setText(subheader_text(catch_rate, turn))
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
        """Place the overlay at the top-right inside a client rect (screen coords).
        Width is fixed, so this is a stable position (no per-frame jitter)."""
        self.move(left + width - self.width() - DOCK_MARGIN, top + DOCK_MARGIN)

    # --- internals ---

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
    ov.show_battle(419, "Floatzel", 75, 2, sample)
    ov.move(200, 200)  # standalone: no game window to dock to
    ov.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    _demo()
