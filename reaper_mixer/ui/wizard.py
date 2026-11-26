"""
Assistant de premier lancement : six étapes courtes, la console en fond qui
change en direct.

  1. Bienvenue
  2. Micro       : périphérique de la première entrée matérielle, avec un
                   VU-mètre pour vérifier qu'il capte (« parle »)
  3. Casque      : périphérique du bus du casque ; proposer d'égaliser le casque
  4. Canaux      : noms des strips
  5. Discord     : Rich Presence, micro synchronisé avec le Muet d'un canal
  6. Style       : style d'interface, thème, analyseur de spectre — puis c'est prêt

Ouvert tout seul après le premier lancement (config wizard_done à False),
avec `REKA.exe --wizard`, et depuis Réglages → Général. Chaque choix
s'applique tout de suite ; « Passer » garde ce qui a déjà été choisi.
Les moteurs (édition de Voicemeeter, effets) se choisissent avant, dans
setup_dialog : ils doivent être connus avant le démarrage.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from core import config
from ui import interfaces, styles
from ui.vu_meter import VuMeter

log = logging.getLogger(__name__)

WIZARD_WIDTH, WIZARD_HEIGHT = 560, 470
STEPS = ("Bienvenue", "Micro", "Casque", "Canaux", "Discord", "Style")


def _text(text: str, name: str = "hint") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


class StepDots(QWidget):
    """Avancement : un point par étape, l'étape en cours en accent."""

    def __init__(self, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count, self._current = count, 0
        self.setFixedSize(count * 18, 12)

    def set_current(self, index: int) -> None:
        self._current = index
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for n in range(self._count):
            current = n == self._current
            p.setBrush(QColor(styles.GREEN_NEON if n <= self._current else styles.BUTTON_HOVER))
            width = 14 if current else 8
            p.drawRoundedRect(QRectF(n * 18 + (14 - width) / 2, 2, width, 8), 4, 4)


class FirstRunWizard(QDialog):
    appearanceChanged = Signal()
    stripRenamed = Signal(int, str)
    discordChanged = Signal()

    def __init__(self, cfg: dict, vm, reaper=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._vm = vm
        self._reaper = reaper
        self.open_listen = False          # « Égaliser mon casque » : l'onglet Écoute s'ouvre après
        self._mic_strip = next((i for i in range(vm.strip_count) if vm.is_physical(i)), None)
        self.setWindowTitle(f"{config.APP_NAME} — premiers réglages")
        self.setFixedSize(WIZARD_WIDTH, WIZARD_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        self.step_label = QLabel()
        self.step_label.setObjectName("hint")
        header.addWidget(self.step_label)
        header.addStretch(1)
        self.dots = StepDots(len(STEPS))
        header.addWidget(self.dots)
        layout.addLayout(header)

        self.pages = QStackedWidget()
        for build in (self._welcome_page, self._mic_page, self._headphone_page, self._names_page,
                      self._discord_page, self._style_page):
            self.pages.addWidget(build())
        layout.addWidget(self.pages, 1)

        footer = QHBoxLayout()
        self.skip_button = QPushButton("Passer l'assistant")
        self.skip_button.clicked.connect(self.skip)
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        self.back_button = QPushButton("Précédent")
        self.back_button.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        footer.addWidget(self.back_button)
        self.next_button = QPushButton("Suivant")
        self.next_button.setObjectName("primary")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self.next)
        footer.addWidget(self.next_button)
        layout.addLayout(footer)

        if hasattr(vm, "devices_changed"):
            vm.devices_changed.connect(self._show_devices)
        if hasattr(vm, "levels_changed"):
            vm.levels_changed.connect(self._on_levels)
        if hasattr(vm, "request_devices"):
            vm.request_devices()
        self.go(0)

    # ------------------------------------------------------------- pages
    def _page(self, title: str, intro: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        box.addWidget(heading)
        box.addWidget(_text(intro))
        return page, box

    def _welcome_page(self) -> QWidget:
        page, box = self._page(f"Bienvenue dans {config.APP_NAME}",
                               "Cinq petites questions pour régler ta console : ton micro, ton "
                               "casque, le nom de tes canaux, Discord et ton style. Chaque choix "
                               "s'applique tout de suite, et tout se change ensuite dans ⚙.")
        logo = QLabel()
        pixmap = QPixmap(str(config.resource_path("assets/reka_mark.png")))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToHeight(110, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addStretch(1)
        box.addWidget(logo)
        box.addStretch(1)
        engine = getattr(self._reaper, "label", "") if self._reaper is not None else ""
        if engine:
            box.addWidget(_text(f"Effets : {engine}. Pour changer de moteur : ⚙ → Général."))
        return page

    def _mic_page(self) -> QWidget:
        page, box = self._page("Ton micro", "Choisis le micro de la première entrée, puis parle : "
                                            "la barre doit bouger.")
        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Chargement…", None)
        self.mic_combo.activated.connect(self._choose_mic)
        form = QFormLayout()
        name = self._strip_name(self._mic_strip) if self._mic_strip is not None else ""
        form.addRow(f"Entrée « {name} »" if name else "Micro", self.mic_combo)
        box.addLayout(form)
        self.mic_meter = VuMeter(channels=2, bar_width=8, gap=3, margin_top=0, margin_bottom=0,
                                 orientation=Qt.Orientation.Horizontal)
        self.mic_meter.setMinimumWidth(300)
        box.addWidget(self.mic_meter)
        self.mic_hint = _text("WDM : le plus rapide ; MME : le plus compatible. Le changement "
                              "coupe le son une seconde.")
        box.addWidget(self.mic_hint)
        if self._mic_strip is None:
            self.mic_combo.setEnabled(False)
            self.mic_hint.setText("Aucune entrée matérielle : règle ton micro dans Voicemeeter.")
        box.addStretch(1)
        return page

    def _headphone_page(self) -> QWidget:
        page, box = self._page("Ton casque", "La sortie où tu écoutes : le knob Casque du Master "
                                             "règle son volume.")
        self.headphone_bus = str(self._cfg.get("voicemeeter", {}).get("headphone_bus", "A1")).upper()
        self.headphone_combo = QComboBox()
        self.headphone_combo.addItem("Chargement…", None)
        self.headphone_combo.activated.connect(self._choose_headphone)
        form = QFormLayout()
        form.addRow(f"Sortie {self.headphone_bus}", self.headphone_combo)
        box.addLayout(form)
        self.listen_box = QCheckBox("Égaliser mon casque avec son profil mesuré")
        box.addWidget(self.listen_box)
        box.addWidget(_text("Des milliers de casques et d'écouteurs : l'onglet Écoute s'ouvrira à "
                            "la fin pour choisir le tien."))
        box.addStretch(1)
        return page

    def _names_page(self) -> QWidget:
        page, box = self._page("Tes canaux", "Un nom par entrée : il s'affiche sous chaque strip "
                                             "et dans Voicemeeter.")
        form = QFormLayout()
        self.name_edits: list[QLineEdit] = []
        for index in range(self._vm.strip_count):
            edit = QLineEdit(self._strip_name(index))
            edit.setMaxLength(30)
            edit.editingFinished.connect(lambda i=index, e=edit: self._rename(i, e.text()))
            kind = "matérielle" if self._vm.is_physical(index) else "virtuelle"
            form.addRow(f"Entrée {index + 1} ({kind})", edit)
            self.name_edits.append(edit)
        box.addLayout(form)
        box.addStretch(1)
        return page

    def _discord_page(self) -> QWidget:
        page, box = self._page("Discord", "REKA peut s'afficher sur ton profil Discord, et couper "
                                          "ton micro dans Discord quand tu le coupes ici.")
        section = self._cfg.setdefault("discord", {})
        self.presence_box = QCheckBox(f"Afficher « Joue à {config.APP_NAME} » sur mon profil")
        self.presence_box.setChecked(bool(section.get("rich_presence", True)))
        self.presence_box.toggled.connect(lambda on: self._set_discord("rich_presence", on))
        box.addWidget(self.presence_box)
        self.mute_box = QCheckBox("Synchroniser le Muet d'un canal avec mon micro Discord")
        self.mute_box.setChecked(bool(section.get("mute_sync", False)))
        self.mute_box.toggled.connect(lambda on: self._set_discord("mute_sync", on))
        box.addWidget(self.mute_box)
        self.mute_strip = QComboBox()
        for index in range(self._vm.strip_count):
            self.mute_strip.addItem(self._strip_name(index), index)
        self.mute_strip.setCurrentIndex(max(0, self.mute_strip.findData(
            int(section.get("mute_strip", 0) or 0))))
        self.mute_strip.currentIndexChanged.connect(
            lambda _row: self._set_discord("mute_strip", self.mute_strip.currentData()))
        form = QFormLayout()
        form.addRow("Canal", self.mute_strip)
        box.addLayout(form)
        box.addWidget(_text("La synchronisation du micro demande ta propre application Discord "
                            "(2 minutes) : ⚙ → Discord explique comment."))
        box.addStretch(1)
        return page

    def _style_page(self) -> QWidget:
        page, box = self._page("Ton style", "Regarde la console derrière : elle change en direct. "
                                            "Quinze styles et 43 thèmes t'attendent dans ⚙.")
        ui_cfg = self._cfg.setdefault("ui", {})
        form = QFormLayout()
        self.interface_combo = QComboBox()
        for iface in interfaces.INTERFACES.values():
            self.interface_combo.addItem(iface.name, iface.key)
        self.interface_combo.setCurrentIndex(max(0, self.interface_combo.findData(
            interfaces.interface(ui_cfg.get("interface")).key)))
        self.interface_combo.currentIndexChanged.connect(self._choose_interface)
        form.addRow("Interface", self.interface_combo)
        self.theme_combo = QComboBox()
        current = styles.current_preset(ui_cfg)
        self.theme_combo.addItem("(mes couleurs actuelles)", None)
        for category, presets in styles.presets_by_category():
            for preset in presets:
                self.theme_combo.addItem(f"{preset.name} — {category}", preset.key)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(current)))
        self.theme_combo.currentIndexChanged.connect(self._choose_theme)
        form.addRow("Thème", self.theme_combo)
        box.addLayout(form)
        self.spectrum_box = QCheckBox("Analyseur de spectre sur chaque strip")
        self.spectrum_box.setChecked(bool(ui_cfg.get("spectrum", False)))
        self.spectrum_box.setEnabled(bool(getattr(self._reaper, "has_spectrum", False)))
        if not self.spectrum_box.isEnabled():
            self.spectrum_box.setToolTip("Avec le moteur d'effets REKA (intégré) seulement")
        self.spectrum_box.toggled.connect(lambda on: self._set_ui("spectrum", on))
        box.addWidget(self.spectrum_box)
        box.addStretch(1)
        box.addWidget(_text("Astuces : double-clic sur un nom pour le renommer, clic droit sur un "
                            "effet pour ses options, molette sur un fader pour le régler finement. "
                            "Cet assistant se relance depuis ⚙ → Général.", "ok"))
        return page

    # -------------------------------------------------------- navigation
    def go(self, index: int) -> None:
        index = max(0, min(len(STEPS) - 1, index))
        self.pages.setCurrentIndex(index)
        self.dots.set_current(index)
        self.step_label.setText(f"Étape {index + 1} sur {len(STEPS)} · {STEPS[index]}")
        self.back_button.setEnabled(index > 0)
        last = index == len(STEPS) - 1
        self.next_button.setText("Terminer" if last else "Suivant")
        self.skip_button.setVisible(not last)

    def next(self) -> None:
        if self.pages.currentIndex() >= len(STEPS) - 1:
            self.finish()
        else:
            self.go(self.pages.currentIndex() + 1)

    def finish(self) -> None:
        for index, edit in enumerate(self.name_edits):   # nom tapé sans quitter le champ
            self._rename(index, edit.text())
        self.open_listen = self.listen_box.isChecked()
        self._cfg["wizard_done"] = True
        self.accept()

    def skip(self) -> None:
        self._cfg["wizard_done"] = True
        self.accept()

    def done(self, result: int) -> None:
        for signal, slot in (("devices_changed", self._show_devices),
                             ("levels_changed", self._on_levels)):
            try:
                getattr(self._vm, signal).disconnect(slot)
            except (AttributeError, RuntimeError, TypeError):
                pass
        self._cfg["wizard_done"] = True        # fermé par la croix : pas reproposé
        super().done(result)

    # --------------------------------------------------------- réglages
    def _strip_name(self, index: int) -> str:
        names = list(self._cfg.get("ui", {}).get("strip_names", []))
        return names[index] if index < len(names) else f"Strip {index + 1}"

    def _rename(self, index: int, text: str) -> None:
        text = text.strip()
        if text and text != self._strip_name(index):
            self.stripRenamed.emit(index, text)

    def _set_discord(self, key: str, value) -> None:
        self._cfg.setdefault("discord", {})[key] = value
        self.discordChanged.emit()

    def _set_ui(self, key: str, value) -> None:
        self._cfg.setdefault("ui", {})[key] = value
        self.appearanceChanged.emit()

    def _choose_interface(self, _row: int) -> None:
        if interfaces.apply_interface(self._cfg.setdefault("ui", {}), self.interface_combo.currentData()):
            self.appearanceChanged.emit()

    def _choose_theme(self, _row: int) -> None:
        key = self.theme_combo.currentData()
        if key and styles.apply_preset(self._cfg.setdefault("ui", {}), key):
            self.appearanceChanged.emit()

    def _choose_mic(self, row: int) -> None:
        data = self.mic_combo.itemData(row)
        if data and self._mic_strip is not None:
            self._vm.set_strip_device(self._mic_strip, data[0], data[1])

    def _choose_headphone(self, row: int) -> None:
        data = self.headphone_combo.itemData(row)
        if data:
            self._vm.set_bus_device(self.headphone_bus, data[0], data[1])

    def _show_devices(self, report) -> None:
        def fill(combo: QComboBox, endpoints, current: str) -> None:
            combo.clear()
            for endpoint in endpoints:
                combo.addItem(endpoint.label, (endpoint.driver, endpoint.name))
            if not endpoints:
                combo.addItem("Aucun périphérique trouvé", None)
            row = next((r for r in range(combo.count())
                        if combo.itemData(r) and combo.itemData(r)[1] == current), -1)
            if current and row < 0:
                combo.addItem(f"{current} (indisponible)", None)
                row = combo.count() - 1
            combo.setCurrentIndex(max(0, row))

        if self._mic_strip is not None:
            devices = report.strip_devices
            current = devices[self._mic_strip] if self._mic_strip < len(devices) else ""
            fill(self.mic_combo, report.inputs, current or "")
        fill(self.headphone_combo, [e for e in report.outputs
                                    if e.driver != "asio" or self.headphone_bus == "A1"],
             dict(report.bus_devices).get(self.headphone_bus, ""))

    def _on_levels(self, levels) -> None:
        if self._mic_strip is not None and self._mic_strip < len(levels.strips):
            self.mic_meter.set_levels(levels.strips[self._mic_strip])

    def mic_level(self) -> float:
        return max(self.mic_meter.levels_shown())
