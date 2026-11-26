"""
Logo de REKA : emblème « RK » en lames, couronne en onde de pouls, ailes.

Génère, à côté de ce script :
  reka-emblem.svg   emblème vectoriel blanc, fond transparent
  reka-emblem.png   emblème blanc 1024 × 1024, fond transparent
  reka-avatar.png   1080 × 1080 sur fond dégradé (profil Discord, réseaux)
  reka-banner.png   1920 × 1080 sur fond dégradé (bannière, miniature)

    python docs/logo/make_logo.py

La forme est décrite sur une grille de 1000 × 1000 (fonctions ci-dessous) :
la retoucher ici, puis relancer le script.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QPainter, QPainterPath,
                           QPainterPathStroker, QPolygonF, QRadialGradient, QTransform)
from PySide6.QtSvg import QSvgGenerator

OUT = Path(__file__).resolve().parent
GRID = 1000
CENTER = 505                     # axe de symétrie de l'emblème sur la grille


def poly(*pts):
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    path.closeSubpath()
    return path


def union(*paths):
    out = QPainterPath()
    for p in paths:
        out = out.united(p)
    return out


def stroke_of(path, width):
    s = QPainterPathStroker()
    s.setWidth(width)
    s.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    s.setMiterLimit(8)
    return s.createStroke(path)


def curved_blade(a, b, tip, ca, cb):
    """Lame : de la base a vers la pointe (courbe ca), retour vers la base b (courbe cb)."""
    path = QPainterPath(QPointF(*a))
    path.quadTo(QPointF(*ca), QPointF(*tip))
    path.quadTo(QPointF(*cb), QPointF(*b))
    path.closeSubpath()
    return path


def letters():
    r_stem = poly((252, 226), (366, 300), (388, 660), (346, 815), (286, 640), (274, 336))
    r_bowl = poly((300, 300), (474, 300), (520, 348), (520, 470), (476, 514), (388, 514),
                  (388, 454), (440, 454), (456, 438), (456, 372), (440, 358), (366, 358))
    r_leg = poly((408, 514), (482, 514), (526, 700), (506, 915), (480, 706))
    k_stem = poly((534, 300), (608, 300), (606, 650), (536, 870), (540, 650))
    k_arm = poly((608, 450), (694, 305), (758, 226), (734, 330), (632, 532), (608, 532))
    k_leg = poly((624, 506), (674, 462), (742, 640), (696, 815), (668, 650))
    return union(r_stem, r_bowl, r_leg), union(k_stem, k_arm, k_leg)


def crown():
    """Onde de pouls : pic central très haut, deux plus petits, posée sur les lettres."""
    path = QPainterPath(QPointF(318, 262))
    for x, y in ((430, 262), (452, 208), (478, 266), (506, 34), (534, 266), (560, 208), (582, 262),
                 (694, 262)):
        path.lineTo(x, y)
    s = QPainterPathStroker()
    s.setWidth(24)
    s.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    s.setMiterLimit(16)
    s.setCapStyle(Qt.PenCapStyle.FlatCap)
    return s.createStroke(path)


def wings():
    left = union(
        curved_blade((280, 330), (290, 452), (52, 96), (196, 246), (150, 336)),
        curved_blade((294, 482), (304, 580), (70, 336), (204, 430), (168, 508)),
        curved_blade((310, 608), (318, 688), (122, 566), (232, 604), (214, 664)),
    )
    return union(left, mirror(left))


def mirror(path):
    return QTransform(-1, 0, 0, 1, 2 * CENTER, 0).map(path)


def emblem(gap=15):
    r, k = letters()
    lk = union(r, k)
    cr = crown()
    wg = wings()
    cr = cr.subtracted(stroke_of(lk, gap)).subtracted(lk)
    wg = wg.subtracted(stroke_of(union(lk, cr), gap)).subtracted(lk)
    return union(lk, cr, wg)


def bounds(path: QPainterPath) -> QRectF:
    return path.boundingRect()


def place(painter: QPainter, path: QPainterPath, w: int, h: int, fill: float) -> None:
    """Centre l'emblème : il occupe `fill` de la plus petite dimension."""
    box = bounds(path)
    scale = min(w, h) * fill / max(box.width(), box.height())
    painter.translate(w / 2 - box.center().x() * scale, h / 2 - box.center().y() * scale)
    painter.scale(scale, scale)


def emblem_image(path: QPainterPath, w: int, h: int, fill: float) -> QImage:
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    place(p, path, w, h, fill)
    p.fillPath(path, QColor("white"))
    p.end()
    return image


def background(w: int, h: int) -> Image.Image:
    """Nuit bleutée : lueur cyan en bas à gauche, lumière blanche en haut à droite, grain."""
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#04070b"))
    p = QPainter(image)
    for cx, cy, color, radius, alpha in ((0.02, 1.02, "#23b8ff", 0.95, 0.95),
                                         (0.0, 0.7, "#0b4f9c", 0.75, 0.55),
                                         (1.02, -0.04, "#f2f5f8", 0.8, 0.85),
                                         (0.85, 0.2, "#7f8b97", 0.6, 0.25)):
        gradient = QRadialGradient(QPointF(w * cx, h * cy), min(w, h) * radius)
        start, end = QColor(color), QColor(color)
        start.setAlphaF(alpha)
        end.setAlphaF(0.0)
        gradient.setColorAt(0, start)
        gradient.setColorAt(1, end)
        p.fillRect(0, 0, w, h, gradient)
    p.end()
    pixels = qimage_to_pil(image).convert("RGB")
    rng = np.random.default_rng(7)
    grain = rng.normal(0, 7, (h, w, 1))
    array = np.clip(np.asarray(pixels, dtype=np.float32) + grain, 0, 255).astype(np.uint8)
    return Image.fromarray(array, "RGB")


def qimage_to_pil(image: QImage) -> Image.Image:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(image.constBits())
    return Image.frombuffer("RGBA", (image.width(), image.height()), data, "raw", "RGBA",
                            image.bytesPerLine(), 1)


def compose(path: QPainterPath, w: int, h: int, fill: float) -> Image.Image:
    base = background(w, h).convert("RGBA")
    base.alpha_composite(qimage_to_pil(emblem_image(path, w, h, fill)))
    return base.convert("RGB")


def save_svg(path: QPainterPath, target: Path) -> None:
    box = bounds(path)
    margin = 0.06 * max(box.width(), box.height())
    view = box.adjusted(-margin, -margin, margin, margin)
    generator = QSvgGenerator()
    generator.setFileName(str(target))
    generator.setSize(QSize(round(view.width()), round(view.height())))
    generator.setViewBox(view)
    generator.setTitle("REKA")
    generator.setDescription("Emblème de REKA")
    p = QPainter(generator)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillPath(path, QColor("white"))
    p.end()


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv)     # Qt en route (dessin, polices)
    path = emblem()
    save_svg(path, OUT / "reka-emblem.svg")
    qimage_to_pil(emblem_image(path, 1024, 1024, 0.9)).save(OUT / "reka-emblem.png", optimize=True)
    compose(path, 1080, 1080, 0.74).save(OUT / "reka-avatar.png", optimize=True)
    compose(path, 1920, 1080, 0.62).save(OUT / "reka-banner.png", optimize=True)
    for name in ("reka-emblem.svg", "reka-emblem.png", "reka-avatar.png", "reka-banner.png"):
        print(OUT / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
