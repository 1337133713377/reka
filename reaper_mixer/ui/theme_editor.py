"""
Éditeur de thèmes (réglages → Apparence → « Créer un thème… ») : quatre
couleurs de base (fond de la fenêtre, panneaux, accent, texte) dont REKA tire
toute la palette, les couleurs de quelques éléments (knobs d'envoi, fader,
VU, badge FX, casque, Muet, noms) et le style des faders et des VU-mètres.

À droite, une vraie mini-console dessinée avec le brouillon (dans le style
d'interface en cours, couleurs du thème par-dessus), refaite à chaque
retouche. Sous les couleurs, un avertissement si l'accent ou le texte se
lisent mal sur les panneaux.

Le thème enregistré rejoint « Mes thèmes » (config ui.custom_themes) et
s'échange en fichier .rekatheme (styles.export_theme / import_theme).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QColorDialog, QPushButton, QScrollArea, QFrame,
                               QVBoxLayout, QWidget)

from ui import interface_preview, styles
from ui.settings_dialog import ColorButton

BASE_LABELS = ("Fond de la fenêtre", "Panneaux", "Accent", "Texte")
ELEMENTS = ("send0", "send1", "send2", "send3", "fader", "vu", "fx_badge", "headphone", "mute",
            "name")
PREVIEW_W, PREVIEW_H = 330, 450
PREVIEW_DELAY_MS = 120
MIN_ACCENT = 3.0           # contraste accent / panneaux
MIN_TEXT = 4.5             # contraste texte / panneaux


class ThemeEditor(QDialog):
    def __init__(self, cfg: dict, theme: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        ui_cfg = cfg.get("ui", {})
        self._editing = theme["key"] if theme else None
        if theme is None:                  # nouveau : part de l'apparence en cours
            current = styles.current_preset(ui_cfg)
            preset = styles.PRESETS.get(current) if current else None
            theme = {"name": "Mon thème",
                     "base": styles.theme_base(ui_cfg.get("theme", styles.DEFAULT_THEME)),
                     "colors": {k: v for k, v in (preset.colors if preset else ui_cfg.get("colors", {})
                                                  or {}).items() if "@" not in k},
                     "fader_style": ui_cfg.get("fader_style") or styles.DEFAULT_FADER_STYLE,
                     "vu_style": ui_cfg.get("vu_style") or styles.DEFAULT_VU_STYLE}
        self.theme = styles.clean_user_theme(theme, key=self._editing or styles.USER_PREFIX + "brouillon")
        self.setWindowTitle("Modifier le thème" if self._editing else "Créer un thème")
        self.resize(820, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        body = QHBoxLayout()
        body.setSpacing(16)
        layout.addLayout(body, 1)

        left = QWidget()
        form_box = QVBoxLayout(left)
        form_box.setContentsMargins(0, 0, 6, 0)
        form = QFormLayout()
        self.name_edit = QLineEdit(self.theme["name"])
        self.name_edit.setMaxLength(40)
        form.addRow("Nom", self.name_edit)
        self.start_combo = QComboBox()
        self.start_combo.addItem("—", None)
        for category, presets in styles.presets_by_category():
            for preset in presets:
                if preset.key != self._editing:
                    self.start_combo.addItem(f"{preset.name} — {category}", preset.key)
        self.start_combo.activated.connect(self._start_from)
        form.addRow("Partir de", self.start_combo)
        form_box.addLayout(form)

        group = QGroupBox("Couleurs de base")
        base_form = QFormLayout(group)
        self.base_buttons: list[ColorButton] = []
        for n, label in enumerate(BASE_LABELS):
            button = ColorButton(self.theme["base"][n], True, label)
            button.clicked.connect(lambda _c=False, i=n: self._choose_base(i))
            self.base_buttons.append(button)
            base_form.addRow(label, button)
        base_form.addRow(_hint("REKA en tire toute la palette : sections, boutons, bordures, "
                               "textes secondaires. Un fond clair donne un thème clair."))
        form_box.addWidget(group)

        group = QGroupBox("Couleurs des éléments")
        element_form = QFormLayout(group)
        labels = dict(styles.ELEMENT_LABELS)
        routing = cfg.get("voicemeeter", {}).get("routing", [])
        for route in range(4):
            label = routing[route].get("label", f"Envoi {route + 1}") if route < len(routing) else ""
            labels[f"send{route}"] = f"Knob {label}" if label else f"Knob {route + 1}"
        self.element_buttons: dict[str, ColorButton] = {}
        self.element_resets: dict[str, QPushButton] = {}
        for element in ELEMENTS:
            button = ColorButton("#000000", False, labels[element])
            button.clicked.connect(lambda _c=False, e=element: self._choose_element(e))
            reset = QPushButton("Thème")
            reset.setToolTip("Couleur tirée de l'accent")
            reset.clicked.connect(lambda _c=False, e=element: self._set_element(e, None))
            row = QHBoxLayout()
            row.addWidget(button)
            row.addWidget(reset)
            row.addStretch(1)
            element_form.addRow(labels[element], row)
            self.element_buttons[element], self.element_resets[element] = button, reset
        form_box.addWidget(group)

        style_form = QFormLayout()
        self.fader_combo = QComboBox()
        for value, label in styles.FADER_STYLES.items():
            self.fader_combo.addItem(label, value)
        self.fader_combo.currentIndexChanged.connect(
            lambda _i: self._set("fader_style", self.fader_combo.currentData()))
        style_form.addRow("Faders", self.fader_combo)
        self.vu_combo = QComboBox()
        for value, label in styles.VU_STYLES.items():
            self.vu_combo.addItem(label, value)
        self.vu_combo.currentIndexChanged.connect(
            lambda _i: self._set("vu_style", self.vu_combo.currentData()))
        style_form.addRow("VU-mètres", self.vu_combo)
        form_box.addLayout(style_form)
        self.warning = QLabel()
        self.warning.setObjectName("warning")
        self.warning.setWordWrap(True)
        form_box.addWidget(self.warning)
        form_box.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("tabScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left)
        body.addWidget(scroll, 1)

        right = QVBoxLayout()
        caption = QLabel("Aperçu")
        caption.setObjectName("sectionTitle")
        right.addWidget(caption)
        self.preview = QLabel()
        self.preview.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.preview)
        right.addStretch(1)
        body.addLayout(right)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.save_button = QPushButton("Enregistrer et appliquer")
        self.save_button.setObjectName("primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self.refresh_preview)
        self._show()
        self.refresh_preview()

    # --------------------------------------------------------- brouillon
    def draft(self) -> dict:
        """Thème tel qu'il est dans l'éditeur (clé provisoire pour un nouveau thème)."""
        theme = dict(self.theme, name=self.name_edit.text().strip() or "Mon thème")
        return styles.clean_user_theme(theme, key=theme["key"])

    def result_theme(self) -> dict:
        """Thème à enregistrer : sa clé définitive (nouvelle si c'est un nouveau thème)."""
        theme = self.draft()
        if self._editing is None:
            taken = {t["key"] for t in styles.user_themes(self._cfg.get("ui", {}))}
            theme["key"] = styles.user_theme_key(theme["name"], taken)
        return theme

    def _set(self, key: str, value) -> None:
        if value is not None and self.theme.get(key) != value:
            self.theme[key] = value
            self._changed()

    def _set_element(self, element: str, value: str | None) -> None:
        colors = dict(self.theme["colors"])
        if value is None:
            colors.pop(element, None)
        else:
            colors[element] = QColor(value).name()
        self.theme["colors"] = colors
        self._changed()

    def _changed(self) -> None:
        self._show()
        self._preview_timer.start()

    def _start_from(self, row: int) -> None:
        key = self.start_combo.itemData(row)
        preset = styles.PRESETS.get(key) if key else None
        if preset is None:
            return
        self.theme["base"] = styles.theme_base(preset.theme)
        self.theme["colors"] = {k: v for k, v in preset.colors.items() if "@" not in k}
        self.theme["fader_style"], self.theme["vu_style"] = preset.fader_style, preset.vu_style
        self._changed()

    def _choose_base(self, index: int) -> None:
        color = QColorDialog.getColor(QColor(self.theme["base"][index]), self, BASE_LABELS[index])
        if color.isValid():
            self.set_base(index, color.name())

    def set_base(self, index: int, value: str) -> None:
        base = list(self.theme["base"])
        base[index] = QColor(value).name()
        self.theme["base"] = base
        self._changed()

    def _choose_element(self, element: str) -> None:
        current = self.element_buttons[element].color
        color = QColorDialog.getColor(QColor(current), self, self.element_buttons[element].toolTip())
        if color.isValid():
            self._set_element(element, color.name())

    # ---------------------------------------------------------- affichage
    def _palette(self) -> dict[str, str]:
        return styles.build_palette(*self.theme["base"])

    def _show(self) -> None:
        for button, value in zip(self.base_buttons, self.theme["base"]):
            button.color = value
            button.update()
        palette = self._palette()
        defaults = {"mute": styles.MUTE_ON, "name": palette["TEXT_PRIMARY"]}
        for element, button in self.element_buttons.items():
            custom = element in self.theme["colors"]
            button.color = self.theme["colors"].get(element) or defaults.get(element, palette["GREEN_NEON"])
            button.custom = custom
            button.update()
            self.element_resets[element].setEnabled(custom)
        for combo, value in ((self.fader_combo, self.theme["fader_style"]),
                             (self.vu_combo, self.theme["vu_style"])):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        self.warning.setText(" ".join(self.warnings()))

    def warnings(self) -> list[str]:
        """Ce qui se lirait mal sur les panneaux (accent, texte)."""
        _window, column, accent, text = (QColor(c) for c in self.theme["base"])
        out = []
        if styles.contrast(accent, column) < MIN_ACCENT:
            out.append("L'accent se voit mal sur les panneaux : éclaircis-le ou fonce les panneaux.")
        if styles.contrast(text, column) < MIN_TEXT:
            out.append("Le texte se lit mal sur les panneaux.")
        return out

    def refresh_preview(self) -> None:
        """Mini-console dessinée avec le brouillon (couleurs du thème par-dessus le style)."""
        draft = self.draft()
        ui_cfg = dict(self._cfg.get("ui", {}))
        others = [t for t in styles.user_themes(ui_cfg) if t["key"] != draft["key"]]
        ui_cfg.update({"custom_themes": others + [draft], "theme": draft["key"],
                       "colors": dict(draft["colors"]), "fader_style": draft["fader_style"],
                       "vu_style": draft["vu_style"]})
        pixmap = interface_preview.render(ui_cfg, ui_cfg.get("interface"), keep_colors=True)
        ratio = self.devicePixelRatioF()
        scaled = pixmap.scaled(int(PREVIEW_W * ratio), int(PREVIEW_H * ratio),
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.preview.setPixmap(scaled)
        self.last_preview: QPixmap = pixmap

    def accept(self) -> None:
        self._preview_timer.stop()
        super().accept()


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label
