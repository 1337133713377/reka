"""Éditeur de thèmes : « Mes thèmes », aperçu, fichiers .rekatheme, sauvegarde."""

import json

import pytest


@pytest.fixture
def settings(qapp, mixer):
    from ui import styles
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    yield window, dialog
    for key in ("custom_themes", "theme", "colors", "fader_style", "vu_style"):
        window._cfg["ui"].pop(key, None)
    styles.register_user_themes([])
    window.apply_appearance()
    dialog.done(0)


def test_user_theme_checks_and_files():
    from ui import styles

    assert styles.clean_user_theme({"base": ["#000000", "#111111", "#ff0000"]}) is None
    assert styles.clean_user_theme({"base": ["#000000", "#111111", "#ff0000", "rouge"]}) is None
    theme = styles.clean_user_theme({"name": "  " * 3, "base": ["#000", "#111", "#f00", "#fff"],
                                     "colors": {"vu": "#00ff00", "inconnu": "#123456", "fader": "x"},
                                     "fader_style": "bizarre"}, key="user-a")
    assert theme["name"] == "Mon thème" and theme["base"][0] == "#000000"
    assert theme["colors"] == {"vu": "#00ff00"} and theme["fader_style"] == styles.DEFAULT_FADER_STYLE
    assert styles.user_theme_key("Nuit d'été !", set()) == "user-nuit-d-ete"
    assert styles.user_theme_key("Nuit", {"user-nuit", "user-nuit-2"}) == "user-nuit-3"

    data = styles.export_theme(theme)
    doc = json.loads(data)
    assert doc["format"] == styles.THEME_FILE_FORMAT and "key" not in doc["theme"]
    back = styles.import_theme(data)
    assert back["base"] == theme["base"] and back["colors"] == theme["colors"]
    for bad in (b"pas du json", b'{"theme": 3}', json.dumps({"format": 99, "theme": doc["theme"]}).encode(),
                json.dumps({"theme": {"base": []}}).encode(), b"x" * 300_000):
        with pytest.raises(ValueError):
            styles.import_theme(bad)


def test_create_edit_export_import_delete(qapp, settings, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui import styles

    window, dialog = settings
    ui_cfg = window._cfg["ui"]
    assert not dialog.edit_theme_button.isEnabled()      # aucun de mes thèmes appliqué

    def design(editor):
        editor.name_edit.setText("Lave")
        editor.set_base(0, "#0b0c10")
        editor.set_base(1, "#1a1416")
        editor.set_base(2, "#ff5a36")
        editor._set_element("vu", "#3ee6b0")
        editor.fader_combo.setCurrentIndex(editor.fader_combo.findData("rond"))
        assert not editor.warnings()
        editor.refresh_preview()
        assert not editor.last_preview.isNull()

    key = dialog.create_theme(editor_hook=design)
    assert key == "user-lave" and key in styles.PRESETS and key in dialog.preset_cards
    assert styles.PRESETS[key].category == styles.USER_CATEGORY
    assert ui_cfg["theme"] == key and ui_cfg["fader_style"] == "rond"
    assert styles.current_theme() == key and styles.GREEN_NEON == "#ff5a36"
    assert styles.BG_COLUMN == "#1a1416" and styles.element_color("vu") == "#3ee6b0"
    assert dialog.preset_cards[key].isChecked() and dialog.edit_theme_button.isEnabled()
    assert "user-brouillon" not in styles.PRESETS          # le brouillon de l'aperçu n'est pas resté
    window.grab()

    # Modifier : même clé, nouvelles couleurs
    assert dialog.edit_theme(key, editor_hook=lambda e: e.set_base(2, "#36a3ff"))
    assert styles.GREEN_NEON == "#36a3ff" and len(ui_cfg["custom_themes"]) == 1

    # Exporter puis réimporter : un second thème, clé distincte
    path = tmp_path / "Lave.rekatheme"
    assert dialog.export_theme_file(key, str(path))
    second = dialog.import_theme_file(str(path))
    assert second == "user-lave-2" and len(ui_cfg["custom_themes"]) == 2
    assert ui_cfg["theme"] == second
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    (tmp_path / "faux.rekatheme").write_text("rien")
    assert dialog.import_theme_file(str(tmp_path / "faux.rekatheme")) is None and warned

    # La sauvegarde .reka emporte mes thèmes
    from core import backup
    assert len(backup.portable_config(window._cfg)["ui"]["custom_themes"]) == 2

    # Supprimer le thème en cours : retour au thème par défaut
    assert dialog.delete_theme(second, confirm=False)
    assert ui_cfg["theme"] == styles.DEFAULT_THEME and second not in styles.PRESETS
    assert second not in dialog.preset_cards and key in dialog.preset_cards


def test_user_themes_after_restart(qapp):
    from ui import styles

    theme = styles.clean_user_theme({"name": "Givre", "base": ["#e8eef4", "#ffffff", "#1d4ed8",
                                                                 "#0f172a"]}, key="user-givre")
    ui_cfg = {"custom_themes": [theme], "theme": "user-givre"}
    try:
        assert styles.configure(ui_cfg)                      # thèmes enregistrés au démarrage
        styles.set_theme("user-givre")
        assert styles.current_theme() == "user-givre" and styles.is_light()
        assert styles.current_preset({**ui_cfg, "fader_style": styles.DEFAULT_FADER_STYLE}) == "user-givre"
        assert [c for c, _ in styles.presets_by_category()][0] == styles.USER_CATEGORY
    finally:
        styles.configure({})
        styles.set_theme(styles.DEFAULT_THEME)
    assert "user-givre" not in styles.PRESETS
