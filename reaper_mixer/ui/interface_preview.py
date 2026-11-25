"""
Aperçu d'un style d'interface (réglages → Interface) : une vraie mini-console
(deux ou trois strips et le Master, ou des lignes de rack), construite et
dessinée avec ce style le temps d'une capture, puis détruite.

L'aperçu suit la disposition conseillée du style (orientation, densité), celle
que l'on obtient en le choisissant ; les couleurs sont celles que la console
aurait (palette du style, ou thème et couleurs de l'utilisateur en mode mixte).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QWidget

from ui import interfaces, styles

PREVIEW_STRIPS = (("Micro", -14.0, True), ("Musique", -9.0, False), ("Discord", -22.0, False))
PREVIEW_EFFECTS = (("Pro-Q 4", True), ("Compresseur", True), ("Limiteur", False))
PREVIEW_ROUTES = ("A1", "B1", "B2", "A2")
VERTICAL_SIZE = QSize(470, 640)
HORIZONTAL_SIZE = QSize(940, 420)


class _Backdrop(QWidget):
    """Fond de fenêtre du style (le même dessin que la console, sans image)."""

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(styles.BG_WINDOW))
        if styles.INTERFACE.background == "brushed":
            gradient = QLinearGradient(0, 0, 0, h)
            gradient.setColorAt(0.0, QColor(styles.BG_GLOW))
            gradient.setColorAt(1.0, QColor(styles.BG_WINDOW))
            p.fillRect(self.rect(), gradient)
        elif styles.INTERFACE.background == "glow":
            gradient = QRadialGradient(QPointF(w * 0.68, h * 0.42), max(w, h) * 0.62)
            gradient.setColorAt(0.0, styles.color(styles.BG_GLOW, 0.95))
            gradient.setColorAt(1.0, styles.color(styles.BG_GLOW, 0.0))
            p.fillRect(self.rect(), gradient)


def _build(orientation: str, density: str) -> QWidget:
    from ui.master_widget import MasterWidget
    from ui.strip_widget import StripWidget

    horizontal = orientation == interfaces.HORIZONTAL
    root = _Backdrop()
    root.setStyleSheet(styles.build_stylesheet(1.0))
    outer = QHBoxLayout(root)
    gap = round(10 * interfaces.density_factor(density))
    outer.setContentsMargins(gap, gap, gap, gap)
    outer.setSpacing(gap)
    strips = QBoxLayout(QBoxLayout.Direction.TopToBottom if horizontal
                        else QBoxLayout.Direction.LeftToRight)
    strips.setSpacing(gap)
    outer.addLayout(strips, 1)
    count = 3 if horizontal else 2
    for index, (name, level, muted) in enumerate(PREVIEW_STRIPS[:count]):
        strip = StripWidget(index, name, list(PREVIEW_ROUTES), apps_button=index == 1)
        strip.set_layout(orientation, density)
        strip.fx_list.set_effects([(fx, on, False) for fx, on in PREVIEW_EFFECTS[:3 - index]])
        strip.fader.setValue(-6.0 if index else 0.0)
        strip._show_value(strip.fader.value())
        strip.mute.setChecked(muted)
        strip.vu.set_levels((level, level - 2))
        for route, knob in enumerate(strip.knobs):
            knob.setActive(route <= index + 1)
            knob.setValue(-8.0 * route)
        strips.addWidget(strip)
    strips.addStretch(1)
    master = MasterWidget()
    master.set_status(True, "REAPER connecté")
    master.set_project("Démo", "")
    master.set_engines([("voicemeeter", "Voicemeeter"), ("reaper", "REAPER")])
    master.set_headphone(-6.0)
    master.apply_interface()
    master.visualizer.setVisible(styles.INTERFACE.visualizer)     # figé : sans animation
    master.visualizer.set_levels((-8.0, -10.0))
    for _ in range(12):
        master.visualizer.step()
    master.set_levels((-8.0, -10.0))
    outer.addWidget(master)
    root.resize(HORIZONTAL_SIZE if horizontal else VERTICAL_SIZE)
    return root


def render(ui_cfg: dict, key: str, keep_colors: bool | None = None) -> QPixmap:
    """Capture d'une mini-console dans le style `key`, à sa disposition conseillée."""
    chosen = interfaces.interface(key)
    cfg = {**ui_cfg, "orientation": chosen.orientation, "density": chosen.density,
           "animations": "off"}
    with styles.previewing(cfg, key, keep_colors):
        root = _build(chosen.orientation, chosen.density)
        root.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        root.show()
        root.layout().activate()
        pixmap = root.grab()
        root.close()
        root.deleteLater()
    return pixmap
