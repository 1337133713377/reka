"""
Analyseur de spectre d'une strip : le son après ses effets, des graves (à
gauche) aux aigus (à droite), en barres qui montent tout de suite et
retombent doucement, avec un chapeau qui garde la crête un instant.

Les bandes viennent du moteur d'effets REKA (FFT dans reka-host, voir
native/reka-host/src/spectrum.h) ; une strip muette (None) retombe à plat.
Le dessin suit le style d'interface : segments sur un afficheur LCD,
barres pleines ailleurs, couleur de l'élément « VU » de la strip.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from core.effects import SPECTRUM_BANDS, SPECTRUM_FLOOR_DB, spectrum_band_hz
from ui import styles

SPECTRUM_HEIGHT = 46
RANGE_DB = 72.0               # hauteur de l'affichage : de -72 dB à 0 dB
FALL_DB_PER_S = 48.0          # retombée des barres
CAP_HOLD_S = 0.6              # le chapeau reste sur la crête…
CAP_FALL_DB_PER_S = 24.0      # … puis retombe
GRID_DB = (-24.0, -48.0)
LABELS_HZ = ((100, "100"), (1000, "1k"), (10000, "10k"))


def _band_x(hz: float) -> float:
    """Position (0 → 1) d'une fréquence sur l'axe, à l'échelle des bandes."""
    first, last = spectrum_band_hz(0), spectrum_band_hz(SPECTRUM_BANDS - 1)
    import math
    return (math.log(hz) - math.log(first)) / (math.log(last) - math.log(first))


class SpectrumView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shown = [SPECTRUM_FLOOR_DB] * SPECTRUM_BANDS
        self._caps = [SPECTRUM_FLOOR_DB] * SPECTRUM_BANDS
        self._cap_t = [0.0] * SPECTRUM_BANDS
        self._t = time.monotonic()
        self._silent = True
        self.color_scope: int | str | None = None   # canal (couleur de l'élément « VU »)
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip("Spectre de la strip après ses effets : graves à gauche, aigus à droite")

    def bands(self) -> list[float]:
        """Niveaux affichés (avec la retombée), en dB."""
        return list(self._shown)

    def caps(self) -> list[float]:
        return list(self._caps)

    def set_bands(self, bands: list[float] | None) -> None:
        """Nouvelle mesure (None : strip muette, tout retombe)."""
        now = time.monotonic()
        dt = min(0.25, now - self._t)
        self._t = now
        self._silent = bands is None
        values = list(bands or [])[:SPECTRUM_BANDS]
        values += [SPECTRUM_FLOOR_DB] * (SPECTRUM_BANDS - len(values))
        for i, db in enumerate(values):
            self._shown[i] = max(db, self._shown[i] - FALL_DB_PER_S * dt, SPECTRUM_FLOOR_DB)
            if db >= self._caps[i]:
                self._caps[i], self._cap_t[i] = db, now
            elif now - self._cap_t[i] > CAP_HOLD_S:
                self._caps[i] = max(self._shown[i], self._caps[i] - CAP_FALL_DB_PER_S * dt)
        if self.isVisible():
            self.update()

    def reset(self) -> None:
        self._shown = [SPECTRUM_FLOOR_DB] * SPECTRUM_BANDS
        self._caps = [SPECTRUM_FLOOR_DB] * SPECTRUM_BANDS
        self._silent = True
        self.update()

    # -------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        styles.smooth(p)
        lcd = styles.INTERFACE.lcd or styles.vu_shape() in ("lcd", "pixel")
        rect = QRectF(0, 0, self.width(), self.height())
        if lcd:
            p.fillRect(rect, QColor(styles.LCD_BG))
        area = rect.adjusted(2, 2, -2, -11 if self.height() >= 40 else -2)
        # Grille : -24 et -48 dB
        p.setPen(QPen(styles.color(styles.TEXT_MUTED, 0.18), 1, Qt.PenStyle.DotLine))
        for db in GRID_DB:
            y = area.top() + area.height() * (-db / RANGE_DB)
            p.drawLine(int(area.left()), int(y), int(area.right()), int(y))
        if area.bottom() < rect.bottom() - 4:      # repères de fréquence
            p.setFont(styles.font(7, QFont.Weight.Medium))
            p.setPen(styles.color(styles.TEXT_MUTED, 0.8))
            for hz, label in LABELS_HZ:
                x = area.left() + area.width() * _band_x(hz)
                p.drawText(QRectF(x - 12, area.bottom() + 1, 24, 9), Qt.AlignmentFlag.AlignCenter, label)

        accent = QColor(styles.LCD_TEXT if lcd and not styles.uses_theme_colors()
                        else styles.accent("vu", self.color_scope))
        gradient = QLinearGradient(0, area.bottom(), 0, area.top())
        gradient.setColorAt(0.0, accent.darker(130))
        gradient.setColorAt(0.65, accent)
        gradient.setColorAt(0.85, QColor(styles.VU_YELLOW))
        gradient.setColorAt(1.0, QColor(styles.VU_RED))
        gap = 1.0 if self.width() < 120 else 2.0
        width = (area.width() - gap * (SPECTRUM_BANDS - 1)) / SPECTRUM_BANDS
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(SPECTRUM_BANDS):
            x = area.left() + i * (width + gap)
            level = max(0.0, min(1.0, 1 + self._shown[i] / RANGE_DB))
            height = area.height() * level
            if height > 0.5:
                p.fillRect(QRectF(x, area.bottom() - height, width, height), gradient)
            cap = max(0.0, min(1.0, 1 + self._caps[i] / RANGE_DB))
            if cap > 0.02:
                y = area.bottom() - area.height() * cap
                p.fillRect(QRectF(x, y - 1, width, 1.5), styles.color(styles.TEXT_PRIMARY, 0.8))
        if lcd:                                    # segments d'afficheur
            shade = styles.color(styles.LCD_BG, 0.85)
            y = area.bottom() - 3
            while y > area.top():
                p.fillRect(QRectF(area.left(), y, area.width(), 1), shade)
                y -= 3
        if self._silent and max(self._shown) <= SPECTRUM_FLOOR_DB + 0.5:
            p.setPen(styles.color(styles.TEXT_MUTED, 0.7))
            p.setFont(styles.font(8, QFont.Weight.Medium))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "silence")
