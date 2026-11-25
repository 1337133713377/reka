"""
Fader custom (paintEvent, antialiasé) avec échelle dB, vertical ou horizontal.

La loi de course (dB → position) est relevée sur la maquette ; elle est
partagée avec les VU-mètres (graduations alignées) et les knobs.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import styles

# Même plage que Voicemeeter (Strip.Gain, GainLayer, Bus.Gain : -60 à +12 dB)
FADER_MIN_DB = -60.0
FADER_MAX_DB = 12.0
SCALE_MARKS = (12, 6, 0, -6, -12, -24, -48)
H_SCALE_MARKS = (0, 12, -6, -12, -24, -48)   # à l'horizontale : 0 dB d'abord, puis la place libre
# (dB, position depuis le haut de la course, 0 → 1) : la loi relevée sur la
# maquette (0 → -60 dB) occupe les 85 % du bas, la réserve 0 → +12 dB le haut
_HEADROOM = 0.15
TAPER = ((12.0, 0.0), (6.0, _HEADROOM / 2),
         *((db, _HEADROOM + (1 - _HEADROOM) * pos)
           for db, pos in ((0.0, 0.0), (-6.0, 0.152), (-12.0, 0.278), (-24.0, 0.479),
                           (-48.0, 0.729), (-60.0, 1.0))))

THUMB_W, THUMB_H = 22, 22
TRACK_MARGIN = THUMB_H // 2 + 1   # marge haute/basse de la course (à reprendre dans les VU)
SCALE_W = 24                      # colonne des graduations
WIDTH = SCALE_W + 6 + THUMB_W + 2
DRAG_FINE = 0.25                  # Maj enfoncée : déplacement 4× plus fin
WHEEL_STEP_DB = 0.5
WHEEL_HOLD_S = 0.8                # après la molette, les relectures de Voicemeeter attendent


def db_to_ratio(db: float) -> float:
    """dB → position dans la course (0 = haut / +12 dB, 1 = bas / -60 dB)."""
    db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
    for (db_hi, pos_hi), (db_lo, pos_lo) in zip(TAPER, TAPER[1:]):
        if db >= db_lo:
            return pos_hi + (db_hi - db) / (db_hi - db_lo) * (pos_lo - pos_hi)
    return 1.0


def ratio_to_db(ratio: float) -> float:
    ratio = max(0.0, min(1.0, ratio))
    for (db_hi, pos_hi), (db_lo, pos_lo) in zip(TAPER, TAPER[1:]):
        if ratio <= pos_lo:
            return db_hi + (ratio - pos_hi) / (pos_lo - pos_hi) * (db_lo - db_hi)
    return FADER_MIN_DB


def format_db(db: float) -> str:
    """Valeur affichée comme dans Voicemeeter : une décimale, bornée à -60 / +12."""
    db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
    text = f"{db:.1f}"
    return "0.0" if text == "-0.0" else text


class Fader(QWidget):
    """Fader de gain. Glisser = régler (Maj = fin), molette = ±0,5 dB,
    double-clic = 0 dB. N'émet valueChanged que sur action de l'utilisateur.

    Vertical (+12 dB en haut) ou horizontal (+12 dB à droite) : le dessin est
    fait à la verticale, dans un repère tourné d'un quart de tour si besoin ;
    « le long » de la course = y vertical, ou largeur − x horizontal."""

    valueChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None,
                 orientation: Qt.Orientation = Qt.Orientation.Vertical) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._muted = False
        self._dragging = False
        self._last_y = 0.0
        self._drag_ratio = 0.0
        self._wheel_until = 0.0
        self._orientation = Qt.Orientation.Vertical
        self.color_scope: int | str | None = None   # canal (couleur personnalisée)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Glisser pour régler · Maj = réglage fin · double-clic = 0 dB")
        self.setOrientation(orientation)

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self._value

    def setValue(self, db: float) -> None:  # noqa: N802 (style Qt)
        """Valeur venant de Voicemeeter ; ignorée pendant que l'utilisateur glisse
        ou tourne la molette (une relecture antérieure ferait reculer le curseur)."""
        if self.isInteracting() or abs(db - self._value) < 0.005:
            return
        self._value = db
        self.update()

    def setMuted(self, muted: bool) -> None:  # noqa: N802
        self._muted = muted
        self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging or time.monotonic() < self._wheel_until

    def orientation(self) -> Qt.Orientation:
        return self._orientation

    def setOrientation(self, orientation: Qt.Orientation) -> None:  # noqa: N802
        self._orientation = orientation
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if self.horizontal:
            self.setFixedHeight(WIDTH)
            self.setMinimumWidth(120)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(WIDTH)
            self.setMinimumHeight(120)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.updateGeometry()
        self.update()

    @property
    def horizontal(self) -> bool:
        return self._orientation == Qt.Orientation.Horizontal

    # ------------------------------------------------------------- géométrie
    def _length(self) -> float:
        """Longueur de la course (hauteur, ou largeur à l'horizontale)."""
        return float(self.width() if self.horizontal else self.height())

    def _along(self, pos) -> float:
        """Position de la souris le long de la course (0 = +12 dB)."""
        return self.width() - pos.x() if self.horizontal else pos.y()

    def _track(self) -> tuple[float, float]:
        return float(TRACK_MARGIN), self._length() - TRACK_MARGIN

    def _rail_x(self) -> float:
        return SCALE_W + 6 + THUMB_W / 2

    def _y_for(self, db: float) -> float:
        top, bottom = self._track()
        return top + db_to_ratio(db) * (bottom - top)

    def _ratio_at(self, y: float) -> float:
        top, bottom = self._track()
        return (y - top) / max(1.0, bottom - top)

    # -------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_scale_labels(p)
        if self.horizontal:          # dessin vertical tourné : +12 dB à droite
            p.translate(self.width(), 0)
            p.rotate(90)
        top, bottom = self._track()
        rail_x = self._rail_x()
        shape = styles.fader_shape()

        # Graduations (traits ; les chiffres sont dessinés à part, à l'endroit)
        p.setPen(QPen(styles.color(styles.GREEN_SCALE), 1))
        for mark in SCALE_MARKS:
            if styles.INTERFACE.scale_labels or mark == 0:
                y = self._y_for(mark)
                p.drawLine(QPointF(SCALE_W - 3, y), QPointF(SCALE_W, y))

        accent = styles.accent("fader", self.color_scope)
        cy = self._y_for(self._value)
        if shape == "line":
            self._paint_line(p, rail_x, top, bottom, cy, accent)
            return
        if shape in ("flat", "chrome"):
            self._paint_slot(p, rail_x, top, bottom, cy, accent, shape)
        else:
            # Rail
            rail_color = "#4b5263" if self._muted else "#5b6577"
            p.setPen(QPen(styles.color(rail_color, 0.85), 2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(rail_x, top), QPointF(rail_x, bottom))
            # Remplissage accent sous le curseur (retour visuel de la position)
            if not self._muted:
                p.setPen(QPen(styles.color(accent, 0.9), 2, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(rail_x, cy), QPointF(rail_x, bottom))

        if shape == "flat":
            self._paint_flat_thumb(p, rail_x, cy, accent)
        elif shape == "chrome":
            self._paint_chrome_thumb(p, rail_x, cy, accent)
        else:
            self._paint_thumb(p, rail_x, cy, accent, shape)

    def _paint_scale_labels(self, p: QPainter) -> None:
        """Chiffres de l'échelle (toujours à l'endroit, même à l'horizontale)."""
        if not styles.INTERFACE.scale_labels:
            return
        p.setFont(styles.font(8, QFont.Weight.Medium))
        drawn: list[float] = []
        for mark in H_SCALE_MARKS if self.horizontal else SCALE_MARKS:
            along = self._y_for(mark)
            if self.horizontal:          # à l'horizontale, les chiffres ne doivent pas se toucher
                if any(abs(along - other) < 24 for other in drawn):
                    continue
                drawn.append(along)
            label_color = styles.TEXT_SECONDARY if mark == 0 else styles.TEXT_MUTED
            p.setPen(styles.color(label_color, 0.9 if mark == 0 else 0.75))
            text = f"+{mark}" if mark > 0 else str(mark)
            if self.horizontal:
                rect = QRectF(self.width() - along - 14, 0, 28, SCALE_W - 4)
                p.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, text)
            else:
                p.drawText(QRectF(0, along - 6, SCALE_W - 5, 12),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_thumb(self, p: QPainter, rail_x: float, cy: float, accent: str, style: str) -> None:
        """Curseurs d'origine : console, rond, fin, vintage (choix d'Apparence)."""
        if style == "rond":
            thumb, radius = QRectF(rail_x - 9, cy - 9, 18, 18), 9.0
        elif style == "fin":
            thumb, radius = QRectF(rail_x - THUMB_W / 2, cy - 4, THUMB_W, 8), 3.0
        elif style == "vintage":
            thumb, radius = QRectF(rail_x - THUMB_W / 2 + 1, cy - THUMB_H / 2, THUMB_W - 2, THUMB_H), 3.0
        else:
            thumb, radius = QRectF(rail_x - THUMB_W / 2, cy - THUMB_H / 2, THUMB_W, THUMB_H), 6.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.BG_BLACK, 0.45))
        p.drawRoundedRect(thumb.translated(0, 1.5), radius, radius)
        gradient = QLinearGradient(thumb.topLeft(), thumb.bottomLeft())
        if self._muted:
            gradient.setColorAt(0, QColor("#9aa19d"))
            gradient.setColorAt(1, QColor("#6f7672"))
        elif style == "vintage":
            gradient.setColorAt(0, QColor("#d9d4c7"))
            gradient.setColorAt(0.5, QColor("#a8a394"))
            gradient.setColorAt(1, QColor("#7d7869"))
        elif style == "fin":
            gradient.setColorAt(0, QColor(styles.accent_bright("fader", self.color_scope)))
            gradient.setColorAt(1, QColor(accent))
        else:
            gradient.setColorAt(0, QColor(styles.WHITE))
            gradient.setColorAt(1, QColor("#d3d8de"))
        p.setBrush(gradient)
        p.setPen(QPen(styles.color(styles.WHITE, 0.35), 1))
        p.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        if self._dragging and not self._muted and styles.INTERFACE.glow:   # halo pendant la saisie
            p.setPen(QPen(styles.color(accent, 0.8), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(thumb.adjusted(-1.5, -1.5, 1.5, 1.5), radius + 1, radius + 1)
        index = styles.TEXT_MUTED if self._muted else accent
        if style == "vintage":                   # stries + index central
            p.setPen(QPen(styles.color("#3b382f", 0.6), 1))
            for dy in (-6, -3, 3, 6):
                p.drawLine(QPointF(thumb.left() + 3, cy + dy), QPointF(thumb.right() - 3, cy + dy))
            p.setPen(QPen(QColor(index), 2))
            p.drawLine(QPointF(thumb.left() + 2, cy), QPointF(thumb.right() - 2, cy))
        elif style == "rond":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(index))
            p.drawEllipse(QPointF(rail_x, cy), 3, 3)
        elif style != "fin":
            p.setPen(QPen(QColor(index), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(rail_x - 6, cy), QPointF(rail_x + 6, cy))

    def _paint_slot(self, p: QPainter, rail_x: float, top: float, bottom: float, cy: float,
                    accent: str, shape: str) -> None:
        """Fente creusée (« Cantabile », « Lecteur 2000 »), remplie sous le curseur."""
        slot = QRectF(rail_x - 2.5, top - 2, 5, bottom - top + 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(styles.BG_DEEP))
        p.drawRect(slot)
        if shape == "chrome":
            styles.draw_bevel(p, slot, sunken=True)
        if not self._muted:
            p.setBrush(styles.color(accent, 0.85))
            p.drawRect(QRectF(slot.x() + 1, cy, slot.width() - 2, max(0.0, bottom - cy)))

    def _paint_flat_thumb(self, p: QPainter, rail_x: float, cy: float, accent: str) -> None:
        """« Cantabile » : curseur plat gris, trait d'index."""
        thumb = QRectF(rail_x - THUMB_W / 2, cy - 7, THUMB_W, 14)
        p.setPen(QPen(QColor(styles.BORDER_COLUMN if not self._dragging else accent), 1))
        p.setBrush(QColor("#6e6e74" if self._muted else "#9a9aa0" if not self._dragging else "#b4b4ba"))
        p.drawRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5))
        p.setPen(QPen(QColor(styles.TEXT_MUTED if self._muted else "#1c1c1e"), 2))
        p.drawLine(QPointF(thumb.left() + 3, cy), QPointF(thumb.right() - 3, cy))

    def _paint_chrome_thumb(self, p: QPainter, rail_x: float, cy: float, accent: str) -> None:
        """« Lecteur 2000 » : curseur chromé strié, en relief."""
        thumb = QRectF(rail_x - THUMB_W / 2, cy - 9, THUMB_W, 18)
        gradient = QLinearGradient(thumb.topLeft(), thumb.bottomLeft())
        if self._muted:
            gradient.setColorAt(0, QColor("#a3a9b1"))
            gradient.setColorAt(1, QColor("#5d636b"))
        else:
            gradient.setColorAt(0, QColor(styles.KNOB_CAP_TOP))
            gradient.setColorAt(0.45, QColor("#b9c1cc"))
            gradient.setColorAt(0.55, QColor("#98a2b0"))
            gradient.setColorAt(1, QColor(styles.KNOB_CAP_BOTTOM))
        p.setPen(QPen(styles.color(styles.BG_BLACK, 0.7), 1))
        p.setBrush(gradient)
        p.drawRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5))
        styles.draw_bevel(p, thumb.adjusted(1, 1, -1, -1))
        p.setPen(QPen(styles.color(styles.BG_BLACK, 0.45), 1))
        for dy in (-5, -3, 3, 5):
            p.drawLine(QPointF(thumb.left() + 4, cy + dy), QPointF(thumb.right() - 4, cy + dy))
        p.setPen(QPen(QColor(styles.TEXT_MUTED if self._muted else accent), 2))
        p.drawLine(QPointF(thumb.left() + 2, cy), QPointF(thumb.right() - 2, cy))

    def _paint_line(self, p: QPainter, rail_x: float, top: float, bottom: float, cy: float,
                    accent: str) -> None:
        """« Minimal » : une barre pleine sous la valeur, un petit trait pour curseur."""
        bar = QRectF(rail_x - 3, top, 6, bottom - top)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.TEXT_MUTED, 0.18))
        p.drawRoundedRect(bar, 3, 3)
        fill = QColor(styles.TEXT_MUTED if self._muted else accent)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(bar.x(), cy, bar.width(), max(0.0, bottom - cy)), 3, 3)
        p.setBrush(QColor(styles.TEXT_PRIMARY if not self._muted else styles.TEXT_MUTED))
        width = 18 if self._dragging else 14
        p.drawRoundedRect(QRectF(rail_x - width / 2, cy - 2, width, 4), 2, 2)

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        y = self._along(event.position())
        if abs(y - self._y_for(self._value)) > THUMB_H / 2 + 2:
            # Clic sur la course : le curseur saute sous la souris
            self._apply_ratio(self._ratio_at(y))
        self._drag_ratio = db_to_ratio(self._value)
        self._last_y = y
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        y = self._along(event.position())
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        top, bottom = self._track()
        self._drag_ratio += (y - self._last_y) / max(1.0, bottom - top) * factor
        self._drag_ratio = max(0.0, min(1.0, self._drag_ratio))
        self._last_y = y
        self._apply_ratio(self._drag_ratio)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_set(0.0)

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120
        step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else WHEEL_STEP_DB
        base = min(self._value, FADER_MAX_DB)
        if steps:
            self._wheel_until = time.monotonic() + WHEEL_HOLD_S
        self._user_set(max(FADER_MIN_DB, min(FADER_MAX_DB, base + steps * step)))
        event.accept()

    def _apply_ratio(self, ratio: float) -> None:
        self._user_set(round(ratio_to_db(ratio), 1))

    def _user_set(self, db: float) -> None:
        if abs(db - self._value) < 0.005:
            return
        self._value = db
        self.update()
        self.valueChanged.emit(db)
