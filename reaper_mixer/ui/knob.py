"""
Knob circulaire custom (paintEvent, antialiasé).

- Strips : clic = routing on/off vers le bus, glisser verticalement = niveau
  d'envoi vers ce bus (Maj = réglage fin), molette = idem.
- Master (casque) : pas de bascule ; le dessin suit la valeur avec une
  interpolation douce, y compris pendant le glisser.

Valeurs par pas de 0,5 dB (glisser comme molette : un cran = 0,5 dB). Course
de 270° (vide en bas), même loi dB que les faders : 0 dB = butée haute.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import styles
from ui.fader import FADER_MAX_DB, FADER_MIN_DB, db_to_ratio, format_db, ratio_to_db

START_ANGLE = 225.0      # degrés, sens trigo (225° = en bas à gauche)
SPAN = 270.0
DRAG_PX = 160.0          # pixels de glisser pour toute la course
DRAG_FINE = 0.25
CLICK_SLOP = 3.0         # au-delà, un appui devient un glisser (et plus un clic)
STEP_DB = 0.5            # pas de réglage (glisser et molette)
WHEEL_NOTCH = 120        # un cran de molette ; les molettes « haute résolution » envoient moins
# Après un cran de molette, les valeurs relues dans Voicemeeter sont ignorées un
# instant : celles déjà en route vers l'UI sont antérieures au réglage et
# ramèneraient le knob en arrière (la molette « collait » sur les PC rapides)
WHEEL_HOLD_S = 0.8
SMOOTH_MS = 16
SMOOTH_FACTOR = 0.28


def _position(db: float) -> float:
    """dB → position sur la course (0 = -60 dB, 1 = +12 dB)."""
    return 1.0 - db_to_ratio(db)


def snap_db(db: float) -> float:
    """Valeur ramenée sur la grille de 0,5 dB, dans la plage des faders."""
    db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
    return round(db / STEP_DB) * STEP_DB + 0.0     # + 0.0 : pas de « -0.0 »


class Knob(QWidget):
    valueChanged = Signal(float)
    toggled = Signal(bool)

    def __init__(self, diameter: int = 30, checkable: bool = True, smooth: bool = False,
                 default_db: float = 0.0, adjustable: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._checkable = checkable
        self._adjustable = adjustable    # False : simple interrupteur (Banana)
        self._smooth = smooth
        self._default = default_db
        self._value = default_db
        self._active = True
        self._shown = _position(default_db)   # position dessinée (interpolée si smooth)
        self._dragging = False
        self._moved = False
        self._press_y = 0.0
        self._press_pos = 0.0
        self._wheel_delta = 0           # crans de molette pas encore appliqués (1/120)
        self._wheel_until = 0.0
        # Couleur personnalisée (styles.element_color) : élément et canal du knob
        self.color_element = "knob"
        self.color_scope: int | str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(SMOOTH_MS)
        self._timer.timeout.connect(self._animate)
        size = diameter + 8
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_tooltip()

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self._value

    def setValue(self, db: float) -> None:  # noqa: N802
        """Valeur venant de Voicemeeter ; ignorée pendant un glisser ou juste
        après un cran de molette."""
        if self.isInteracting() or abs(db - self._value) < 0.005:
            return
        self._value = db
        self._retarget()

    def isActive(self) -> bool:  # noqa: N802
        return self._active

    def setActive(self, active: bool) -> None:  # noqa: N802
        if active != self._active:
            self._active = active
            self._update_tooltip()
            self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging or time.monotonic() < self._wheel_until

    # -------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = float(self._diameter)
        ring = max(2.6, d * 0.12)
        center = QPointF(self.width() / 2, self.height() / 2)
        arc_rect = QRectF(center.x() - (d - ring) / 2, center.y() - (d - ring) / 2,
                          d - ring, d - ring)
        pos = max(0.0, min(1.0, self._shown))
        accent = styles.accent(self.color_element, self.color_scope)
        dim = styles.accent_dim(self.color_element, self.color_scope)
        shape = styles.INTERFACE.knob
        if shape == "flat":
            self._paint_flat(p, center, arc_rect, d, ring, pos, accent, dim)
        elif shape == "chrome":
            self._paint_chrome(p, center, d, ring, pos, accent, dim)
        elif shape == "arc":
            self._paint_arc(p, center, arc_rect, ring, pos, accent, dim)
        else:
            self._paint_reka(p, center, arc_rect, d, ring, pos, accent, dim)

    @staticmethod
    def _direction(pos: float) -> QPointF:
        angle = math.radians(START_ANGLE - SPAN * pos)
        return QPointF(math.cos(angle), -math.sin(angle))

    def _paint_reka(self, p, center, arc_rect, d, ring, pos, accent, dim) -> None:
        """Style d'origine : halo, arc épais, bouton en relief, index blanc."""
        arc = int(START_ANGLE * 16)
        span_value = -SPAN * pos
        if self._active:
            # Halo, piste, puis arc de valeur
            p.setPen(QPen(styles.color(accent, 0.16), ring * 2.0,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(span_value * 16))
            p.setPen(QPen(styles.color(dim, 0.7), ring,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(-SPAN * 16))
            if pos > 0.002:
                p.setPen(QPen(QColor(accent), ring,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(arc_rect, arc, int(span_value * 16))
        else:
            self._paint_off_track(p, center, arc_rect, ring, accent, dim)

        # Bouton central
        cap_r = d / 2 - ring - max(2.0, ring * 0.55)
        gradient = QRadialGradient(center.x() - cap_r * 0.3, center.y() - cap_r * 0.4, cap_r * 1.6)
        gradient.setColorAt(0, QColor(styles.KNOB_CAP_TOP))
        gradient.setColorAt(1, QColor(styles.KNOB_CAP_BOTTOM))
        p.setBrush(gradient)
        p.setPen(QPen(styles.color(styles.WHITE, 0.08), 1))
        p.drawEllipse(center, cap_r, cap_r)

        # Indicateur blanc du centre vers le bord
        direction = self._direction(pos)
        width = max(2.0, d * 0.055)
        p.setPen(QPen(styles.color(styles.WHITE, 0.95 if self._active else 0.75), width,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(center + direction * (cap_r * 0.25), center + direction * (cap_r * 0.78))

    def _paint_off_track(self, p, center, arc_rect, ring, accent, dim) -> None:
        """Éteint : piste à peine visible + point au départ de la course."""
        p.setPen(QPen(styles.color(dim, 0.25), ring * 0.6,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, int(START_ANGLE * 16), int(-SPAN * 16))
        start = math.radians(START_ANGLE)
        r = arc_rect.width() / 2
        dot = QPointF(center.x() + r * math.cos(start), center.y() - r * math.sin(start))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(accent, 0.85))
        p.drawEllipse(dot, ring * 0.45, ring * 0.45)

    def _paint_flat(self, p, center, arc_rect, d, ring, pos, accent, dim) -> None:
        """« Cantabile » : arc fin sans halo, bouton plat, index clair."""
        arc = int(START_ANGLE * 16)
        thin = max(2.0, ring * 0.7)
        p.setPen(QPen(QColor(styles.BUTTON_BG if self._active else styles.BG_SECTION), thin,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(arc_rect, arc, int(-SPAN * 16))
        if self._active and pos > 0.002:
            p.setPen(QPen(QColor(accent), thin, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(arc_rect, arc, int(-SPAN * pos * 16))
        cap_r = d / 2 - ring - 1.5
        p.setBrush(QColor(styles.KNOB_CAP_TOP))
        p.setPen(QPen(QColor(styles.BORDER_COLUMN), 1))
        p.drawEllipse(center, cap_r, cap_r)
        direction = self._direction(pos)
        p.setPen(QPen(QColor(styles.TEXT_PRIMARY if self._active else styles.TEXT_MUTED),
                      max(1.6, d * 0.05), Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawLine(center + direction * (cap_r * 0.2), center + direction * cap_r)

    def _paint_chrome(self, p, center, d, ring, pos, accent, dim) -> None:
        """« Lecteur 2000 » : couronne de LED, bouton chromé, encoche sombre."""
        ticks = 11
        outer = d / 2
        for n in range(ticks):
            t = n / (ticks - 1)
            direction = self._direction(t)
            lit = self._active and pos > 0.002 and t <= pos + 0.001
            p.setPen(QPen(QColor(accent) if lit else styles.color(dim, 0.55 if self._active else 0.3),
                          max(1.6, d * 0.05), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(center + direction * (outer - ring * 0.9), center + direction * (outer - 0.5))
        cap_r = outer - ring * 1.25
        gradient = QRadialGradient(center.x() - cap_r * 0.35, center.y() - cap_r * 0.45, cap_r * 1.7)
        gradient.setColorAt(0, QColor(styles.KNOB_CAP_TOP))
        gradient.setColorAt(0.55, QColor(styles.mix(styles.KNOB_CAP_TOP, styles.KNOB_CAP_BOTTOM, 0.5)))
        gradient.setColorAt(1, QColor(styles.KNOB_CAP_BOTTOM))
        p.setBrush(gradient)
        p.setPen(QPen(styles.color(styles.BG_BLACK, 0.6), 1))
        p.drawEllipse(center, cap_r, cap_r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(styles.color(styles.WHITE, 0.5), 1))
        p.drawEllipse(center, cap_r - 1.5, cap_r - 1.5)
        direction = self._direction(pos)
        p.setPen(QPen(QColor("#1d2228" if self._active else "#5a626d"), max(1.8, d * 0.06),
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(center + direction * (cap_r * 0.3), center + direction * (cap_r * 0.85))

    def _paint_arc(self, p, center, arc_rect, ring, pos, accent, dim) -> None:
        """« Minimal » : un arc fin et un point, sans bouton."""
        arc = int(START_ANGLE * 16)
        thin = max(1.8, ring * 0.55)
        p.setPen(QPen(styles.color(styles.TEXT_MUTED, 0.3), thin,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, arc, int(-SPAN * 16))
        ink = QColor(accent) if self._active else styles.color(styles.TEXT_MUTED, 0.8)
        if self._active and pos > 0.002:
            p.setPen(QPen(ink, thin, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(-SPAN * pos * 16))
        r = arc_rect.width() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawEllipse(center + self._direction(pos) * r, thin * 1.1, thin * 1.1)

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging, self._moved = True, False
        self._press_y = event.position().y()
        self._press_pos = _position(self._value)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        dy = self._press_y - event.position().y()
        if not self._moved and abs(dy) < CLICK_SLOP:
            return
        self._moved = True
        if not self._adjustable:
            return
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        self._user_set_position(self._press_pos + dy / DRAG_PX * factor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        if not self._moved and self._checkable and self.rect().contains(event.position().toPoint()):
            self._active = not self._active
            self._update_tooltip()
            self.update()
            self.toggled.emit(self._active)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Sur un knob de routing, le double-clic serait aussi deux bascules : réservé au master
        if not self._checkable and event.button() == Qt.MouseButton.LeftButton:
            self._user_set(self._default)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self._adjustable:
            event.ignore()
            return
        event.accept()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            return
        now = time.monotonic()
        if now > self._wheel_until or (delta > 0) != (self._wheel_delta > 0):
            self._wheel_delta = 0           # nouveau geste ou changement de sens : de zéro
        self._wheel_until = now + WHEEL_HOLD_S
        # Molettes haute résolution (crans fractionnés) : on cumule jusqu'au cran entier
        self._wheel_delta += delta
        steps = int(self._wheel_delta / WHEEL_NOTCH)
        if steps:
            self._wheel_delta -= steps * WHEEL_NOTCH
            self._user_set(snap_db(min(self._value, FADER_MAX_DB) + steps * STEP_DB))

    def _user_set_position(self, pos: float) -> None:
        self._user_set(snap_db(ratio_to_db(1.0 - max(0.0, min(1.0, pos)))))

    def _user_set(self, db: float) -> None:
        db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
        if abs(db - self._value) < 0.005:
            return
        self._value = db
        self._retarget()
        self.valueChanged.emit(db)

    # ----------------------------------------------------------- animation
    def _retarget(self) -> None:
        self._update_tooltip()
        if self._smooth and styles.animations("reduced"):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._shown = _position(self._value)
            self.update()

    def _animate(self) -> None:
        target = _position(self._value)
        self._shown += (target - self._shown) * SMOOTH_FACTOR
        if abs(target - self._shown) < 0.001:
            self._shown = target
            self._timer.stop()
        self.update()

    def _update_tooltip(self) -> None:
        state = "" if not self._checkable else (" · activé" if self._active else " · coupé")
        if not self._adjustable:
            self.setToolTip(("Envoi activé" if self._active else "Envoi coupé") + " · clic pour basculer")
            return
        self.setToolTip(f"{format_db(self._value)} dB{state}")
