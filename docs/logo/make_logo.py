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
CENTER = 503                     # axe de symétrie de l'emblème sur la grille


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
    """R et K soudés : la panse du R s'appuie sur le fût du K, la jambe du R et le fût
    du K se rejoignent dans la pointe centrale."""
    r_stem = poly((258, 222), (372, 300), (392, 650), (352, 808), (292, 640), (282, 334))
    r_bowl = poly((300, 300), (478, 300), (524, 346), (524, 468), (482, 510), (392, 510),
                  (392, 452), (444, 452), (460, 436), (460, 372), (444, 356), (372, 356))
    r_leg = poly((410, 510), (484, 510), (512, 640), (502, 915), (476, 700))
    k_stem = poly((512, 300), (586, 300), (584, 640), (502, 915), (516, 640))
    k_arm = poly((586, 448), (674, 306), (748, 222), (716, 330), (610, 528), (586, 528))
    k_leg = poly((602, 504), (652, 460), (720, 640), (678, 808), (650, 650))
    return union(r_stem, r_bowl, r_leg), union(k_stem, k_arm, k_leg)


def crown():
    """Onde de pouls posée sur les lettres : pic central très haut, deux plus petits."""
    path = QPainterPath(QPointF(296, 282))
    for x, y in ((430, 282), (452, 222), (476, 300), (502, 26), (528, 300), (552, 222), (574, 282),
                 (708, 282)):
        path.lineTo(x, y)
    s = QPainterPathStroker()
    s.setWidth(26)
    s.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    s.setMiterLimit(16)
    s.setCapStyle(Qt.PenCapStyle.FlatCap)
    return s.createStroke(path)


def wings():
    """Trois lames de chaque côté, qui sortent de derrière les lettres."""
    left = union(
        curved_blade((330, 330), (330, 462), (88, 116), (214, 248), (176, 350)),
        curved_blade((330, 482), (336, 596), (100, 352), (218, 442), (190, 520)),
        curved_blade((336, 612), (344, 716), (152, 578), (250, 616), (240, 690)),
    )
    return union(left, mirror(left))


def mirror(path):
    return QTransform(-1, 0, 0, 1, 2 * CENTER, 0).map(path)


def cut_lines(width: float) -> QPainterPath:
    """Fines entailles qui gardent les parties lisibles dans le bloc soudé."""
    cuts = QPainterPath()
    for points in (((282, 297), (466, 297)),            # couronne / R
                   ((538, 297), (730, 297)),            # couronne / K
                   ((518, 312), (518, 560)),            # panse du R / fût du K
                   ((514, 650), (504, 845))):           # jambe du R / fût du K, jusqu'à la pointe
        line = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            line.lineTo(*point)
        cuts.addPath(stroke_of(line, width))
    return cuts


def emblem(gap=8):
    r, k = letters()
    body = union(r, k, crown())
    wg = wings().subtracted(stroke_of(body, gap)).subtracted(body)
    return union(body, wg).subtracted(cut_lines(gap))


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
    """Couleurs de REKA sur du noir : lueur verte en bas à gauche, rouge en haut à
    droite, grain."""
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#050506"))
    p = QPainter(image)
    for cx, cy, color, radius, alpha in ((0.0, 1.04, "#2bd45f", 0.82, 0.92),
                                         (-0.05, 0.72, "#0e6b2c", 0.6, 0.5),
                                         (1.04, -0.06, "#ff3b3b", 0.74, 0.92),
                                         (1.0, 0.25, "#8f1414", 0.45, 0.35)):
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
