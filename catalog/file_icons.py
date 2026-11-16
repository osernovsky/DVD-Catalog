"""Локальные пиктограммы типов с вариантами для масштабирования Windows."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

from .file_types import GROUP_NAMES

COLORS = ("#94631a", "#6740a3", "#156c81", "#a24f2d", "#367246", "#8a3f6b", "#365f96", "#505b69")


def polygon(painter, points):
    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))


def draw_icon(painter, group):
    color = QColor(COLORS[group])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    if group == 0:
        polygon(painter, ((1, 3), (7, 3), (8.5, 5), (15, 5), (14, 13.5), (1, 13.5)))
        painter.setBrush(QColor("#efd297"))
        polygon(painter, ((2.4, 7), (14, 7), (13.3, 12.4), (2.4, 12.4)))
        return
    if group <= 4:
        painter.drawRoundedRect(QRectF(1, 2, 14, 12), 2, 2)
    else:
        polygon(painter, ((3, 1), (10, 1), (14, 5), (14, 15), (3, 15)))
        painter.setBrush(QColor("#ffffff"))
        polygon(painter, ((9.5, 2), (9.5, 5.5), (13, 5.5)))
    painter.setBrush(QColor("#ffffff"))
    painter.setPen(QPen(QColor("#ffffff"), 1.25, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if group == 1:
        painter.setPen(Qt.PenStyle.NoPen)
        polygon(painter, ((6, 4.5), (11, 8), (6, 11.5)))
    elif group == 2:
        painter.drawLine(QPointF(8, 10), QPointF(8, 5))
        painter.drawLine(QPointF(8, 5), QPointF(11.5, 6.5))
        painter.drawEllipse(QRectF(4.8, 9.4, 3.2, 2.1))
    elif group == 3:
        for y in (4, 6, 8):
            painter.drawLine(QPointF(6.5, y), QPointF(9.5, y))
        painter.drawRoundedRect(QRectF(7, 10, 2, 1.5), 0.5, 0.5)
    elif group == 4:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(10, 4.5, 2, 2))
        polygon(painter, ((3, 11.5), (6.8, 6.8), (9, 9.5), (10.5, 8.2), (13, 11.5)))
    elif group == 5:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(5.5, 7.5, 5.5, 4.5))
    elif group == 6:
        for y in (8, 10, 12):
            painter.drawLine(QPointF(5.5, y), QPointF(11, y))
    else:
        painter.drawLine(QPointF(6, 10), QPointF(10.5, 10))


def file_icons():
    icons = []
    for group in range(len(GROUP_NAMES)):
        icon = QIcon()
        for pixels in (16, 20, 24, 28, 32, 40, 48, 64):
            pixmap = QPixmap(pixels, pixels)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(pixels / 16, pixels / 16)
            draw_icon(painter, group)
            painter.end()
            pixmap.setDevicePixelRatio(pixels / 16)
            icon.addPixmap(pixmap)
        icons.append(icon)
    return tuple(icons)
