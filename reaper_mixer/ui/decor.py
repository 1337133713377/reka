"""
Fonds de fenêtre et ornements des panneaux, selon le style d'interface
(styles.INTERFACE.background / .decor). Partagés par la console et les aperçus
des réglages.

Fonds : glow (halo d'origine), flat, brushed (métal), mica (Windows 11), grid
(quadrillage), wood (noyer), aurora (taches de couleur sous le verre),
scanlines (écran cathodique).
Ornements : screws (vis aux coins), brackets (coins de HUD).
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from ui import styles

SCREW_RADIUS = 3.6
SCREW_MARGIN = 9          # marge ajoutée aux panneaux vissés (les vis y logent)
BRACKET = 12              # longueur des coins de HUD


def paint_background(p: QPainter, w: int, h: int) -> None:
    """Fond de fenêtre du style en cours (le fond uni BG_WINDOW est déjà peint)."""
    kind = styles.INTERFACE.background
    painter = _BACKGROUNDS.get(kind)
    if painter is not None:
        p.save()
        painter(p, w, h)
        p.restore()


def _glow(p: QPainter, w: int, h: int) -> None:
    gradient = QRadialGradient(QPointF(w * 0.68, h * 0.42), max(w, h) * 0.62)
    gradient.setColorAt(0.0, styles.color(styles.BG_GLOW, 0.95))
    gradient.setColorAt(0.55, styles.color(styles.BG_GLOW, 0.35))
    gradient.setColorAt(1.0, styles.color(styles.BG_GLOW, 0.0))
    p.fillRect(0, 0, w, h, gradient)


def _brushed(p: QPainter, w: int, h: int) -> None:
    """Métal brossé : plus clair en haut, fines stries."""
    gradient = QLinearGradient(0, 0, 0, h)
    gradient.setColorAt(0.0, QColor(styles.BG_GLOW))
    gradient.setColorAt(0.5, QColor(styles.mix(styles.BG_GLOW, styles.BG_WINDOW, 0.6)))
    gradient.setColorAt(1.0, QColor(styles.BG_WINDOW))
    p.fillRect(0, 0, w, h, gradient)
    for y in range(0, h, 3):
        p.fillRect(0, y, w, 1, styles.color(styles.WHITE, 0.025 if (y // 3) % 2 else 0.0))


def _mica(p: QPainter, w: int, h: int) -> None:
    """Mica de Windows 11 : deux voiles de couleur très doux."""
    for cx, cy, tint, alpha in ((0.15, 0.1, "#3a6ea5", 0.22), (0.9, 0.95, "#6b4fa0", 0.16)):
        gradient = QRadialGradient(QPointF(w * cx, h * cy), max(w, h) * 0.75)
        gradient.setColorAt(0.0, styles.color(tint, alpha))
        gradient.setColorAt(1.0, styles.color(tint, 0.0))
        p.fillRect(0, 0, w, h, gradient)


def _grid(p: QPainter, w: int, h: int) -> None:
    """Quadrillage (plan technique, HUD) : traits fins, un trait marqué sur cinq."""
    _glow(p, w, h)
    for step, alpha in ((12, 0.06), (60, 0.13)):
        p.setPen(QPen(styles.color(styles.TEXT_PRIMARY, alpha), 1))
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)


def _wood(p: QPainter, w: int, h: int) -> None:
    """Coffret en noyer : dégradé brun et veines ondulées (toujours les mêmes)."""
    gradient = QLinearGradient(0, 0, w, h)
    gradient.setColorAt(0.0, QColor("#4a2c17"))
    gradient.setColorAt(0.5, QColor("#3a2112"))
    gradient.setColorAt(1.0, QColor("#2a170c"))
    p.fillRect(0, 0, w, h, gradient)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rng = random.Random(42)
    for n in range(0, h + 40, 7):
        path = QPainterPath()
        phase, amp, freq = rng.random() * 6.3, 2 + rng.random() * 5, 0.004 + rng.random() * 0.006
        path.moveTo(0, n)
        for x in range(0, w + 20, 20):
            path.lineTo(x, n + amp * math.sin(phase + x * freq))
        dark = rng.random() < 0.6
        p.setPen(QPen(styles.color("#140a04" if dark else "#7a4a26", 0.18 + rng.random() * 0.12),
                      1 + rng.random() * 1.5))
        p.drawPath(path)


def _aurora(p: QPainter, w: int, h: int) -> None:
    """Taches de couleur floues sous les panneaux de verre."""
    for cx, cy, tint, size in ((0.18, 0.2, styles.GREEN_NEON, 0.55), (0.75, 0.3, "#c13cff", 0.6),
                               (0.45, 0.95, "#3b5bff", 0.65), (0.95, 0.85, "#ff4fa3", 0.4)):
        gradient = QRadialGradient(QPointF(w * cx, h * cy), max(w, h) * size)
        gradient.setColorAt(0.0, styles.color(tint, 0.55))
        gradient.setColorAt(1.0, styles.color(tint, 0.0))
        p.fillRect(0, 0, w, h, gradient)


def _scanlines(p: QPainter, w: int, h: int) -> None:
    """Écran cathodique : lueur au centre, lignes de balayage."""
    gradient = QRadialGradient(QPointF(w * 0.5, h * 0.45), max(w, h) * 0.7)
    gradient.setColorAt(0.0, styles.color(styles.GREEN_NEON, 0.07))
    gradient.setColorAt(1.0, styles.color(styles.GREEN_NEON, 0.0))
    p.fillRect(0, 0, w, h, gradient)
    shade = styles.color(styles.BG_BLACK, 0.35)
    for y in range(0, h, 3):
        p.fillRect(0, y, w, 1, shade)


_BACKGROUNDS = {"glow": _glow, "brushed": _brushed, "mica": _mica, "grid": _grid, "wood": _wood,
                "aurora": _aurora, "scanlines": _scanlines}


# --------------------------------------------------------------------------- #
# Ornements des panneaux
# --------------------------------------------------------------------------- #
def decor_margins(horizontal: bool = False) -> tuple[int, int, int, int]:
    """Marges à ajouter à un panneau (gauche, haut, droite, bas) pour ses ornements."""
    if styles.INTERFACE.decor != "screws":
        return 0, 0, 0, 0
    if horizontal:                       # oreilles de rack à gauche et à droite
        return SCREW_MARGIN + 4, 0, SCREW_MARGIN + 4, 0
    return 0, SCREW_MARGIN, 0, SCREW_MARGIN


def draw_decor(p: QPainter, rect: QRectF, horizontal: bool = False) -> None:
    """Ornements du style en cours sur un panneau (strip, Master)."""
    kind = styles.INTERFACE.decor
    if not kind:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, styles.INTERFACE.antialias)
    if kind == "screws":
        if horizontal:
            x1, x2 = rect.left() + 8.5, rect.right() - 8.5
            spots = [(x, y) for x in (x1, x2) for y in (rect.top() + 12, rect.bottom() - 12)]
        else:
            y1, y2 = rect.top() + 8, rect.bottom() - 8
            spots = [(x, y) for y in (y1, y2) for x in (rect.left() + 10, rect.right() - 10)]
        for n, (x, y) in enumerate(spots):
            _screw(p, QPointF(x, y), 35 + n * 50)
    elif kind == "brackets":
        _brackets(p, rect.adjusted(1, 1, -1, -1))
    p.restore()


def _screw(p: QPainter, center: QPointF, angle: float) -> None:
    """Tête de vis cruciforme, éclairée par le haut."""
    r = SCREW_RADIUS
    gradient = QRadialGradient(center.x() - r * 0.4, center.y() - r * 0.5, r * 2)
    gradient.setColorAt(0.0, QColor("#e8eaee"))
    gradient.setColorAt(1.0, QColor("#5c626b"))
    p.setPen(QPen(styles.color(styles.BG_BLACK, 0.6), 0.8))
    p.setBrush(gradient)
    p.drawEllipse(center, r, r)
    p.setPen(QPen(styles.color("#2a2e34", 0.85), 1.1))
    for extra in (0, 90):
        a = math.radians(angle + extra)
        d = QPointF(math.cos(a), math.sin(a)) * (r * 0.7)
        p.drawLine(center - d, center + d)


def _brackets(p: QPainter, rect: QRectF) -> None:
    """Coins en crochets, à l'accent, avec un léger halo."""
    accent = styles.GREEN_NEON
    for width, alpha in ((4.0, 0.18), (1.6, 0.95)):
        p.setPen(QPen(styles.color(accent, alpha), width, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.SquareCap))
        for x, y, dx, dy in ((rect.left(), rect.top(), 1, 1), (rect.right(), rect.top(), -1, 1),
                             (rect.left(), rect.bottom(), 1, -1), (rect.right(), rect.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * BRACKET, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * BRACKET))
