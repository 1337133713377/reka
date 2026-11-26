"""
VU-mètre stéréo custom (paintEvent, antialiasé) : vert → jaune → rouge.
Vertical, ou horizontal (barres empilées, niveau de gauche à droite).

Même loi de course que le fader (graduations alignées). Balistique simple :
montée instantanée, retombée progressive, maintien du pic, LED de saturation.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import styles
from ui.fader import FADER_MIN_DB, TRACK_MARGIN, db_to_ratio

RELEASE_DB_PER_S = 26.0
PEAK_HOLD_S = 1.2
PEAK_FALL_DB_PER_S = 20.0
CLIP_DB = -0.1
CLIP_HOLD_S = 1.5
SEGMENT_PX = 3          # aspect « LED » : une ligne sombre tous les 3 px


def _gradient_stop(db: float) -> float:
    """Position d'un niveau dans un dégradé orienté du bas (0) vers le haut (1)."""
    return 1.0 - db_to_ratio(db)


class VuMeter(QWidget):
    def __init__(self, channels: int = 2, bar_width: int = 3, gap: int = 2,
                 margin_top: int = TRACK_MARGIN, margin_bottom: int = TRACK_MARGIN,
                 clip_leds: bool = False, parent: QWidget | None = None,
                 orientation: Qt.Orientation = Qt.Orientation.Vertical) -> None:
        super().__init__(parent)
        self._channels = channels
        self._bar_w = bar_width
        self._gap = gap
        self._clip_leds = clip_leds
        self._margin_top = margin_top + (14 if clip_leds else 0)
        self._margin_bottom = margin_bottom
        self._shown = [FADER_MIN_DB] * channels
        self._peaks = [FADER_MIN_DB] * channels
        self._peak_t = [0.0] * channels
        self._clip_until = [0.0] * channels
        self._t = time.monotonic()
        self._orientation = Qt.Orientation.Vertical
        self.color_scope: int | str | None = None   # canal ou Master (couleur personnalisée)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setOrientation(orientation)

    def orientation(self) -> Qt.Orientation:
        return self._orientation

    def setOrientation(self, orientation: Qt.Orientation) -> None:  # noqa: N802
        """Vertical (+12 dB en haut) ou horizontal (+12 dB à droite)."""
        self._orientation = orientation
        thickness = (self._channels * self._bar_w + (self._channels - 1) * self._gap
                     + (6 if self._clip_leds else 0))
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(thickness)
            self.setMinimumWidth(60)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(thickness)
            self.setMinimumHeight(60)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.updateGeometry()
        self.update()

    def set_levels(self, levels: tuple[float, ...]) -> None:
        now = time.monotonic()
        dt = min(0.25, now - self._t)
        self._t = now
        for i in range(self._channels):
            db = levels[i] if i < len(levels) else FADER_MIN_DB
            self._shown[i] = max(db, self._shown[i] - RELEASE_DB_PER_S * dt, FADER_MIN_DB)
            if db >= self._peaks[i]:
                self._peaks[i], self._peak_t[i] = db, now
            elif now - self._peak_t[i] > PEAK_HOLD_S:
                self._peaks[i] = max(self._shown[i], self._peaks[i] - PEAK_FALL_DB_PER_S * dt)
            if db >= CLIP_DB:
                self._clip_until[i] = now + CLIP_HOLD_S
        self.update()

    def is_clipping(self) -> bool:
        now = time.monotonic()
        return any(until > now for until in self._clip_until)

    def levels_shown(self) -> list[float]:
        """Niveaux affichés (avec la retombée), en dB."""
        return list(self._shown)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        styles.smooth(p)
        length = float(self.height())
        if self._orientation == Qt.Orientation.Horizontal:
            # Dessin vertical dans un repère tourné : +12 dB à droite
            length = float(self.width())
            p.translate(self.width(), 0)
            p.rotate(90)
        top, bottom = float(self._margin_top), length - self._margin_bottom
        height = max(1.0, bottom - top)
        shape = styles.vu_shape()

        gradient = QLinearGradient(0, bottom, 0, top)
        # Couleur du thème (niveaux normaux) → jaune (zone haute) → rouge (crêtes)
        accent = styles.accent("vu", self.color_scope)
        gradient.setColorAt(0.0, QColor(accent).darker(115))
        gradient.setColorAt(_gradient_stop(-18), QColor(accent))
        gradient.setColorAt(_gradient_stop(-9), QColor(styles.VU_YELLOW))
        gradient.setColorAt(_gradient_stop(-3), QColor(styles.VU_YELLOW))
        gradient.setColorAt(_gradient_stop(-1.5), QColor(styles.VU_RED))
        gradient.setColorAt(1.0, QColor(styles.VU_RED))

        offset = 3 if self._clip_leds else 0
        now = time.monotonic()
        off_opacity = {"flat": 0.0, "line": 0.0, "lcd": 0.22, "outline": 0.0,
                       "pixel": 0.25}.get(shape, 0.16)
        for i in range(self._channels):
            x = offset + i * (self._bar_w + self._gap)
            bar = QRectF(x, top, self._bar_w, height)
            # Fond de la barre selon le style
            if shape == "flat":
                p.fillRect(bar, QColor(styles.BG_DEEP))
            elif shape == "line":
                p.fillRect(bar, styles.color(styles.TEXT_MUTED, 0.14))
            elif shape == "lcd":
                p.fillRect(bar.adjusted(-1, -1, 1, 1), QColor(styles.LCD_BG))
            # LED éteintes : le dégradé en très sombre
            if off_opacity:
                p.setOpacity(off_opacity)
                p.fillRect(bar, gradient)
            # LED allumées
            p.setOpacity(1.0)
            level_y = top + db_to_ratio(self._shown[i]) * height
            if self._shown[i] > FADER_MIN_DB:
                p.fillRect(QRectF(x, level_y, self._bar_w, bottom - level_y), gradient)
            # Pic maintenu
            if self._peaks[i] > FADER_MIN_DB + 1:
                peak_y = top + db_to_ratio(self._peaks[i]) * height
                # Peak hold : trait coloré selon la zone du pic
                if self._peaks[i] >= -3:
                    peak_color = styles.VU_RED
                elif self._peaks[i] >= -9:
                    peak_color = styles.VU_YELLOW
                else:
                    peak_color = styles.TEXT_PRIMARY
                p.fillRect(QRectF(x, peak_y - 1, self._bar_w, 2 if shape != "line" else 1.5),
                           QColor(peak_color))
            # Segmentation, selon le style choisi (styles.VU_STYLE ou celui de l'interface)
            p.setPen(Qt.PenStyle.NoPen)
            if shape in ("segments", "led", "lcd", "pixel"):
                step, gap = {"led": (6, 2), "lcd": (4, 1.5), "pixel": (5, 2)}.get(shape, (SEGMENT_PX, 1))
                back = styles.LCD_BG if shape == "lcd" else styles.BG_SECTION
                shade = styles.color(back, 0.85 if gap > 1 else 0.55)
                y = top + step
                while y < bottom:
                    p.fillRect(QRectF(x, y, self._bar_w, gap), shade)
                    y += step
            if shape == "outline":            # contour au trait
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(styles.color(styles.TEXT_MUTED, 0.8), 1))
                p.drawRect(bar.adjusted(-0.5, -0.5, 0.5, 0.5))
                p.setPen(Qt.PenStyle.NoPen)
            # LED de saturation au-dessus de chaque barre
            if self._clip_leds:
                lit = self._clip_until[i] > now
                p.setBrush(QColor(styles.VU_RED if lit else styles.RED_DIM))
                cx = x + self._bar_w / 2
                p.drawEllipse(QRectF(cx - 3.5, top - 13, 7, 7))


class ClipLed(QWidget):
    """Petite LED rouge (en-tête du fader) : s'allume quand le VU sature."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lit = False
        self.setFixedSize(10, 10)
        self.setToolTip("Saturation")

    def set_lit(self, lit: bool) -> None:
        if lit != self._lit:
            self._lit = lit
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        styles.smooth(p)
        p.setPen(Qt.PenStyle.NoPen)
        if self._lit:
            p.setBrush(styles.color(styles.RED_MUTE, 0.35))
            p.drawEllipse(QRectF(0, 0, 10, 10))
        p.setBrush(QColor(styles.RED_MUTE if self._lit else styles.RED_DIM))
        p.drawEllipse(QRectF(2, 2, 6, 6))
