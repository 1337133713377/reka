"""
Onglet « Écoute » des réglages : égalisation du casque (profil AutoEQ ou
fichier importé, avec sa courbe) et son surround virtuel (pièce, taille de
tête, intensité). Les changements partent tout de suite vers le moteur
d'écoute (core/listening.py).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from core import listening
from ui import styles

log = logging.getLogger(__name__)

APPLY_DELAY_MS = 60          # curseur déplacé : réglages envoyés au plus toutes les 60 ms
CURVE_RANGE_DB = 12.0


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _tone(label: QLabel, name: str) -> None:
    """Style d'un texte : « hint », « ok » ou « warning »."""
    if label.objectName() != name:
        label.setObjectName(name)
        label.style().unpolish(label)
        label.style().polish(label)


class EqCurve(QWidget):
    """Courbe de la correction (20 Hz – 20 kHz, ±12 dB), préampli compris."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._freqs = listening.log_frequencies(160)
        self._points: list[float] = []
        self._enabled = False

    def set_eq(self, preamp: float, filters, enabled: bool) -> None:
        self._points = listening.response_db(preamp, filters, self._freqs) if filters else []
        self._enabled = enabled
        self.update()

    @property
    def points(self) -> list[float]:
        return list(self._points)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(styles.BORDER_SECTION), 1))
        p.setBrush(QColor(styles.BG_SECTION))
        p.drawRoundedRect(rect, 8, 8)
        plot = rect.adjusted(30, 8, -8, -18)
        low, high = math.log10(20.0), math.log10(20000.0)

        def x_of(freq: float) -> float:
            return plot.left() + (math.log10(freq) - low) / (high - low) * plot.width()

        def y_of(db: float) -> float:
            db = max(-CURVE_RANGE_DB, min(CURVE_RANGE_DB, db))
            return plot.center().y() - db / CURVE_RANGE_DB * plot.height() / 2

        p.setFont(styles.font(8))
        for db in (-12, -6, 0, 6, 12):
            y = y_of(db)
            p.setPen(QPen(QColor(styles.TEXT_MUTED if db == 0 else styles.BORDER_COLUMN), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(styles.TEXT_MUTED))
            p.drawText(QRectF(rect.left(), y - 7, 26, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{db:+d}"
                       if db else "0")
        for freq, text in ((100, "100"), (1000, "1k"), (10000, "10k")):
            x = x_of(freq)
            p.setPen(QPen(QColor(styles.BORDER_COLUMN), 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(QColor(styles.TEXT_MUTED))
            p.drawText(QRectF(x - 20, plot.bottom() + 2, 40, 14), Qt.AlignmentFlag.AlignCenter,
                       text)
        if not self._points:
            p.setPen(QColor(styles.TEXT_SECONDARY))
            p.setFont(styles.font(9))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Aucun profil : son du casque d'origine")
            return
        path = QPainterPath()
        for i, (freq, db) in enumerate(zip(self._freqs, self._points)):
            point = QPointF(x_of(freq), y_of(db))
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        ink = QColor(styles.GREEN_NEON if self._enabled else styles.TEXT_MUTED)
        p.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)


class ListenPanel(QWidget):
    """Contenu de l'onglet « Écoute »."""

    changed = Signal()          # réglages modifiés (déjà écrits dans la config)

    def __init__(self, cfg: dict, controller=None, library: listening.AutoEqLibrary | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._controller = controller
        self._library = library or listening.AutoEqLibrary(self)
        self._pending_search = False
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(APPLY_DELAY_MS)
        self._apply_timer.timeout.connect(self._apply)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        self.status = _hint("")
        box.addWidget(self.status)
        box.addWidget(self._eq_group())
        box.addWidget(self._surround_group())

        self._library.index_ready.connect(self._on_index)
        self._library.profile_ready.connect(self._on_profile)
        self._library.failed.connect(self._on_failed)
        if controller is not None:
            controller.status_changed.connect(self._show_status)
        self._show_eq()
        self._show_status(controller.status() if controller is not None else None)

    # ------------------------------------------------------------ égalisation
    def _eq_group(self) -> QGroupBox:
        group = QGroupBox("Égalisation du casque")
        box = QVBoxLayout(group)
        self.eq_enabled = QCheckBox("Corriger la courbe de mon casque")
        self.eq_enabled.toggled.connect(self._on_eq_toggled)
        box.addWidget(self.eq_enabled)
        self.profile_label = QLabel()
        self.profile_label.setWordWrap(True)
        box.addWidget(self.profile_label)
        self.curve = EqCurve()
        box.addWidget(self.curve)

        row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Modèle de casque : HD 600, DT 770, Arctis Nova…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self.search)
        self.search_edit.textChanged.connect(self._on_query)
        row.addWidget(self.search_edit, 1)
        self.search_button = QPushButton("Rechercher")
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        box.addLayout(row)
        self.results = QListWidget()
        self.results.setMinimumHeight(140)
        self.results.itemDoubleClicked.connect(lambda _item: self.use_selected())
        self.results.currentItemChanged.connect(lambda *_: self._show_buttons())
        self.results.hide()
        box.addWidget(self.results)
        self.search_info = _hint("")
        self.search_info.hide()
        box.addWidget(self.search_info)

        buttons = QHBoxLayout()
        self.use_button = QPushButton("Utiliser ce profil")
        self.use_button.setObjectName("primary")
        self.use_button.clicked.connect(self.use_selected)
        self.use_button.hide()
        buttons.addWidget(self.use_button)
        self.import_button = QPushButton("Importer un fichier…")
        self.import_button.clicked.connect(self._import)
        buttons.addWidget(self.import_button)
        self.clear_button = QPushButton("Retirer le profil")
        self.clear_button.clicked.connect(self.clear_profile)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        box.addLayout(buttons)
        box.addWidget(_hint(
            "Les profils viennent d'AutoEQ : des milliers de casques et d'écouteurs mesurés, "
            "corrigés vers une courbe neutre. Le préampli baisse un peu le volume pour que la "
            "correction ne sature jamais : remonte le knob Casque si besoin. Un fichier "
            "« ParametricEQ.txt » ou une config Equalizer APO s'importent aussi."))
        return group

    def _eq_settings(self) -> dict:
        return listening.settings(self._cfg)["eq"]

    def _show_eq(self) -> None:
        eq = self._eq_settings()
        has_profile = bool(eq["filters"])
        self.eq_enabled.blockSignals(True)
        self.eq_enabled.setChecked(eq["enabled"])
        self.eq_enabled.setEnabled(has_profile)
        self.eq_enabled.blockSignals(False)
        if has_profile:
            source = f" ({eq['source']})" if eq["source"] else ""
            self.profile_label.setText(
                f"Profil : <b>{_escape(eq['name'] or 'personnalisé')}</b>{_escape(source)} · "
                f"{len(eq['filters'])} filtres · préampli {eq['preamp']:+.1f} dB")
        else:
            self.profile_label.setText("Aucun profil : cherche ton casque ci-dessous.")
        self.curve.set_eq(eq["preamp"], eq["filters"], eq["enabled"])
        self.clear_button.setEnabled(has_profile)
        self._show_buttons()

    def _show_buttons(self) -> None:
        self.use_button.setVisible(self.results.isVisible() and self.results.currentItem() is not None)

    def _on_eq_toggled(self, on: bool) -> None:
        listening.set_option(self._cfg, "eq", "enabled", bool(on))
        self._show_eq()
        self._schedule()

    def search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            self.search_edit.setFocus()
            return
        if self._library.entries is not None:
            self._fill(self._library.entries)
            return
        self._pending_search = True
        self.search_button.setEnabled(False)
        self._info("Chargement de la base AutoEQ…")
        self._library.request_index()

    def _on_query(self, _text: str) -> None:
        # Base déjà chargée : résultats au fil de la frappe
        if self._library.entries is not None and self.search_edit.text().strip():
            self._fill(self._library.entries)

    def _on_index(self, entries) -> None:
        self.search_button.setEnabled(True)
        if self._pending_search:
            self._pending_search = False
            self._fill(entries)

    def _fill(self, entries) -> None:
        query = self.search_edit.text().strip()
        found = listening.search(entries, query)
        self.results.clear()
        for entry in found:
            item = QListWidgetItem(f"{entry.label} · {entry.kind}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.results.addItem(item)
        self.results.setVisible(bool(found))
        if found:
            self.results.setCurrentRow(0)
            self._info(f"{len(found)} profil(s) pour « {query} ». Les mesures d'oratory1990 et "
                       "de crinacle sont en tête : double-clic pour l'utiliser.")
        else:
            self._info(f"Aucun casque ne correspond à « {query} » : essaie juste la marque ou "
                       "le numéro du modèle.")
        self._show_buttons()

    def use_selected(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        self.use_button.setEnabled(False)
        self._info(f"Téléchargement du profil « {entry.name} »…")
        self._library.request_profile(entry)

    def _on_profile(self, entry, eq) -> None:
        self.use_button.setEnabled(True)
        self.set_profile(entry.name, entry.source, eq)
        self._info(f"Profil « {entry.name} » ({entry.source}) appliqué.")

    def set_profile(self, name: str, source: str, eq: listening.HeadphoneEq) -> None:
        listening.set_profile(self._cfg, name, source, eq)
        self._show_eq()
        self._schedule()

    def clear_profile(self) -> None:
        listening.clear_profile(self._cfg)
        self._show_eq()
        self._schedule()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importer une égalisation", "",
                                              "Égalisation (*.txt);;Tous les fichiers (*)")
        if path:
            self.import_file(Path(path))

    def import_file(self, path: Path) -> bool:
        try:
            eq = listening.load_file(path)
        except listening.EqParseError as exc:
            self._info(str(exc), error=True)
            return False
        name = path.stem.replace(" ParametricEQ", "").strip() or path.stem
        self.set_profile(name, "fichier importé", eq)
        self._info(f"« {path.name} » importé : {len(eq.filters)} filtres.")
        return True

    def _on_failed(self, message: str) -> None:
        self.search_button.setEnabled(True)
        self.use_button.setEnabled(True)
        self._pending_search = False
        self._info(message, error=True)

    def _info(self, text: str, error: bool = False) -> None:
        self.search_info.setText(text)
        _tone(self.search_info, "warning" if error else "hint")
        self.search_info.setVisible(bool(text))

    # ------------------------------------------------------------ surround
    def _surround_group(self) -> QGroupBox:
        group = QGroupBox("Son surround virtuel")
        box = QVBoxLayout(group)
        sur = listening.settings(self._cfg)["surround"]
        self.surround_enabled = QCheckBox("Entendre le son autour de moi (7.1 virtuel)")
        self.surround_enabled.setChecked(sur["enabled"])
        self.surround_enabled.toggled.connect(
            lambda on: self._set_surround("enabled", bool(on)))
        box.addWidget(self.surround_enabled)
        form = QFormLayout()
        self.room = QComboBox()
        for key, label in listening.ROOMS.items():
            self.room.addItem(label, key)
        self.room.setCurrentIndex(max(0, self.room.findData(sur["room"])))
        self.room.currentIndexChanged.connect(
            lambda _row: self._set_surround("room", self.room.currentData()))
        form.addRow("Pièce", self.room)
        self.head = QComboBox()
        for key, label in listening.HEADS.items():
            self.head.addItem(label, key)
        self.head.setCurrentIndex(max(0, self.head.findData(sur["head"])))
        self.head.currentIndexChanged.connect(
            lambda _row: self._set_surround("head", self.head.currentData()))
        form.addRow("Taille de tête", self.head)
        amount_row = QHBoxLayout()
        self.amount = QSlider(Qt.Orientation.Horizontal)
        self.amount.setRange(0, 100)
        self.amount.setValue(round(sur["amount"] * 100))
        self.amount_label = QLabel(f"{self.amount.value()} %")
        self.amount_label.setMinimumWidth(42)
        self.amount.valueChanged.connect(self._on_amount)
        amount_row.addWidget(self.amount, 1)
        amount_row.addWidget(self.amount_label)
        form.addRow("Intensité", amount_row)
        box.addLayout(form)
        box.addWidget(_hint(
            "Chaque canal du son devient une enceinte placée autour de toi : les pas derrière "
            "toi s'entendent derrière, les dialogues devant. En stéréo, le son sort de deux "
            "enceintes face à toi au lieu du milieu de ta tête. Pour le vrai 7.1 des jeux : "
            "Paramètres son de Windows → « Voicemeeter Input » → Configurer → 7.1 Surround. "
            "Si l'effet ne te convainc pas, essaie une autre taille de tête : chacun entend "
            "différemment."))
        self._show_surround_fields()
        return group

    def _show_surround_fields(self) -> None:
        on = self.surround_enabled.isChecked()
        for widget in (self.room, self.head, self.amount):
            widget.setEnabled(on)

    def _set_surround(self, key: str, value) -> None:
        listening.set_option(self._cfg, "surround", key, value)
        self._show_surround_fields()
        self._schedule()

    def _on_amount(self, value: int) -> None:
        self.amount_label.setText(f"{value} %")
        self._set_surround("amount", value / 100.0)

    # ------------------------------------------------------------ moteur
    def _schedule(self) -> None:
        self._apply_timer.start()

    def _apply(self) -> None:
        if self._controller is not None:
            self._controller.apply()
        self.changed.emit()

    def flush(self) -> None:
        """Réglages en attente envoyés tout de suite (fermeture des réglages)."""
        if self._apply_timer.isActive():
            self._apply_timer.stop()
            self._apply()

    def refresh(self) -> None:
        """Config changée ailleurs (pastilles EQ / 3D du Master)."""
        self._show_eq()
        sur = listening.settings(self._cfg)["surround"]
        self.surround_enabled.blockSignals(True)
        self.surround_enabled.setChecked(sur["enabled"])
        self.surround_enabled.blockSignals(False)
        self._show_surround_fields()

    def _show_status(self, status) -> None:
        if status is None:
            self.status.setText("Écoute au casque : moteur indisponible.")
            _tone(self.status, "hint")
            return
        self.status.setText(f"Écoute au casque : {status.text}")
        _tone(self.status, "hint" if not status.active else "ok" if status.ok else "warning")

    def disconnect_all(self) -> None:
        for signal, slot in ((self._library.index_ready, self._on_index),
                             (self._library.profile_ready, self._on_profile),
                             (self._library.failed, self._on_failed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if self._controller is not None:
            try:
                self._controller.status_changed.disconnect(self._show_status)
            except (RuntimeError, TypeError):
                pass


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
