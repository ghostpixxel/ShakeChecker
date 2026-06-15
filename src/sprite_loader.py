"""Resolve and load the overlay sprites vendored under src/data/sprites/.

Two layers:
- Pure path resolution (ball_slug, species_sprite_path, ball_sprite_path): no Qt,
  fully unit-tested. Pokemon are keyed by National Dex id with a .gif (animated
  Gen-5) preferred over a .png (static fallback); balls by a name->slug map.
- SpriteLoader: turns those paths into cached QPixmap / QMovie at a target height
  (nearest-neighbour scaling to keep pixel art crisp), with a generated "?"
  placeholder for the few ids that have no sprite (PokeMMO event-custom mons).

Qt objects need a running QGuiApplication; the pure functions do not.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QFont, QMovie, QPainter, QPixmap

SPRITES_DIR = Path(__file__).resolve().parent / "data" / "sprites"


def ball_slug(name: str) -> str:
    """'Poké Ball' -> 'poke-ball' (matches the vendored item filenames)."""
    return name.lower().replace("é", "e").replace(" ", "-")


def species_sprite_path(dex_id: int, base: Path = SPRITES_DIR) -> Path | None:
    """Sprite file for a Dex id: animated .gif if present, else static .png."""
    for ext in ("gif", "png"):
        p = base / "pokemon" / f"{dex_id}.{ext}"
        if p.exists():
            return p
    return None


def ball_sprite_path(name: str, base: Path = SPRITES_DIR) -> Path | None:
    p = base / "items" / f"{ball_slug(name)}.png"
    return p if p.exists() else None


def _scaled(pix: QPixmap, height: int) -> QPixmap:
    if pix.isNull() or height <= 0 or pix.height() == height:
        return pix
    return pix.scaledToHeight(height, Qt.TransformationMode.FastTransformation)


class SpriteLoader:
    """Caches loaded sprites by (key, height). One instance per overlay."""

    def __init__(self, base: Path = SPRITES_DIR) -> None:
        self._base = base
        self._pix: dict[tuple, QPixmap] = {}
        self._movies: dict[tuple[int, int], QMovie] = {}

    def ball_pixmap(self, name: str, height: int) -> QPixmap:
        key = ("ball", name, height)
        if key not in self._pix:
            p = ball_sprite_path(name, self._base)
            pm = QPixmap(str(p)) if p else QPixmap()
            self._pix[key] = self._placeholder(height) if pm.isNull() else _scaled(pm, height)
        return self._pix[key]

    def species_movie(self, dex_id: int, height: int, max_width: int = 0) -> QMovie | None:
        """Animated sprite for a Dex id, scaled to `height`; None if the id has
        only a static sprite (or none) — call species_pixmap then. With max_width,
        scale to fit a height x max_width box (keeps aspect) so list sprites share
        a fixed column width and the names line up."""
        p = species_sprite_path(dex_id, self._base)
        if p is None or p.suffix != ".gif":
            return None
        key = (dex_id, height, max_width)
        if key not in self._movies:
            m = QMovie(str(p))
            m.jumpToFrame(0)
            fs = m.currentImage().size()
            if fs.height() > 0:
                w = max(1, round(fs.width() * height / fs.height()))
                h = height
                if max_width and w > max_width:  # too wide -> fit the box by width
                    h = max(1, round(fs.height() * max_width / fs.width()))
                    w = max_width
                m.setScaledSize(QSize(w, h))
            self._movies[key] = m
        return self._movies[key]

    def species_pixmap(self, dex_id: int, height: int, max_width: int = 0) -> QPixmap:
        """Static sprite for a Dex id (or the placeholder if none exists). With
        max_width, scale to fit a height x max_width box (keeps aspect)."""
        p = species_sprite_path(dex_id, self._base)
        key = ("species", dex_id, height, max_width)
        if key not in self._pix:
            pm = QPixmap(str(p)) if p else QPixmap()
            if pm.isNull():
                pm = self._placeholder(height)
            else:
                pm = _scaled(pm, height)
                if max_width and pm.width() > max_width:
                    pm = pm.scaledToWidth(max_width, Qt.TransformationMode.FastTransformation)
            self._pix[key] = pm
        return self._pix[key]

    def _placeholder(self, height: int) -> QPixmap:
        """A transparent square with a grey '?' for ids that have no sprite."""
        size = max(1, height)
        pm = QPixmap(size, size)
        pm.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pm)
        painter.setPen(QColor(170, 170, 170))
        font = QFont()
        font.setPixelSize(int(size * 0.7))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "?")
        painter.end()
        return pm
