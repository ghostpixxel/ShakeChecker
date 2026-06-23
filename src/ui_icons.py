import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap


def icon_pixmap(kind: str, size: int, color: str) -> QPixmap:
    """A small monochrome header icon drawn in the overlay's own colour (so it
    matches the panel instead of an OS emoji): 'gear', 'ball', 'swords', 'book', or 'info'."""
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
    elif kind == "swords":
        p.setPen(QPen(c, size * 0.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(
            QPointF(cx - size * 0.3, cy + size * 0.3), QPointF(cx + size * 0.3, cy - size * 0.3)
        )
        p.drawLine(
            QPointF(cx - size * 0.25, cy + size * 0.1), QPointF(cx - size * 0.1, cy + size * 0.25)
        )
        p.drawLine(
            QPointF(cx + size * 0.3, cy + size * 0.3), QPointF(cx - size * 0.3, cy - size * 0.3)
        )
        p.drawLine(
            QPointF(cx + size * 0.25, cy + size * 0.1), QPointF(cx + size * 0.1, cy + size * 0.25)
        )
    elif kind == "book":
        p.setPen(QPen(c, size * 0.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - size * 0.35, cy - size * 0.2), QPointF(cx, cy - size * 0.1))
        p.drawLine(QPointF(cx - size * 0.35, cy + size * 0.2), QPointF(cx, cy + size * 0.3))
        p.drawLine(
            QPointF(cx - size * 0.35, cy - size * 0.2), QPointF(cx - size * 0.35, cy + size * 0.2)
        )
        p.drawLine(QPointF(cx + size * 0.35, cy - size * 0.2), QPointF(cx, cy - size * 0.1))
        p.drawLine(QPointF(cx + size * 0.35, cy + size * 0.2), QPointF(cx, cy + size * 0.3))
        p.drawLine(
            QPointF(cx + size * 0.35, cy - size * 0.2), QPointF(cx + size * 0.35, cy + size * 0.2)
        )
        p.drawLine(QPointF(cx, cy - size * 0.1), QPointF(cx, cy + size * 0.3))
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
