"""Export / import de toute la config dans un fichier .reka."""

import json
import zipfile

import pytest

from conftest import pump
from core import backup, config
from core.backup import BackupError


def make_cfg(cfg, appdata):
    """Une config personnalisée, avec image d'arrière-plan et réglages propres au PC."""
    cfg["ui"].update(theme="synthwave", colors={"mute": "#ff00aa"}, background_image="background.png",
                     strip_names=["Voix", "Zik", "Appels", "Bureau", "Jeu", "Navigateur"],
                     geometry="AAAA", minimize_to_tray=True)
    cfg["voicemeeter"].update(path=r"C:\VB\voicemeeter8x64.exe", routing=[
        {"label": "Casque", "bus": "A1"}, {"label": "Stream", "bus": "B1"}])
    cfg["reaper"].update(path=r"C:\REAPER\reaper.exe", project=r"C:\Users\moi\Live.rpp",
                         recent_projects=[r"C:\Users\moi\Live.rpp"])
    cfg["discord"].update(client_id="123", client_secret="s3cret", access_token="jeton",
                          refresh_token="suivant", token_expires=99, mute_sync=True)
    cfg["state"] = {"strips": [{"gain": -6.0, "mute": True}], "headphone_db": -3.0}
    (appdata / "REKA").mkdir(exist_ok=True)
    (appdata / "REKA" / "background.png").write_bytes(b"\x89PNG image")
    return cfg


def fresh_cfg():
    cfg = config.default_config()
    cfg["reaper"]["path"] = r"D:\Apps\reaper.exe"
    cfg["discord"]["client_secret"] = "le-mien"
    cfg["ui"]["geometry"] = "BBBB"
    cfg["update"]["skipped_build"] = 12
    return cfg


def test_export_contains_everything_but_local_settings(cfg, appdata, tmp_path):
    make_cfg(cfg, appdata)
    project = tmp_path / "Live.rpp"
    project.write_text('<REAPER_PROJECT 0.1 "7.0/win64" 0\n>\n', encoding="utf-8")
    target = tmp_path / "ma-config.reka"
    saved = backup.export_backup(cfg, target, project)
    assert saved.kind == "potato" and saved.host == "reaper" and saved.project_name == "Live"
    assert "Voicemeeter Potato + REAPER" in saved.describe()
    assert "image d'arrière-plan et projet REAPER" in saved.describe()
    assert not list(tmp_path.glob("*.tmp"))

    with zipfile.ZipFile(target) as archive:
        assert sorted(archive.namelist()) == ["background.png", "config.json", "manifest.json",
                                              "project.rpp"]
        exported = json.loads(archive.read("config.json"))
        text = archive.read("config.json").decode("utf-8") + archive.read("manifest.json").decode("utf-8")
    assert exported["ui"]["theme"] == "synthwave" and exported["ui"]["colors"] == {"mute": "#ff00aa"}
    assert exported["state"]["strips"][0]["mute"] is True
    assert exported["discord"] == {"rich_presence": True, "details": "Vst", "state": "talking",
                                   "mute_sync": True, "mute_strip": 0,
                                   "mute_direction": "both", "client_id": "123"}
    # Ni secrets, ni chemins du PC (donc pas le nom du compte Windows) : partageable
    for private in ("s3cret", "jeton", "suivant", "C:\\\\", "moi", "AAAA"):
        assert private not in text
    assert cfg["discord"]["client_secret"] == "s3cret"       # la config en cours n'est pas touchée


def test_import_round_trip_keeps_local_settings(cfg, appdata, tmp_path):
    make_cfg(cfg, appdata)
    project = tmp_path / "Live.rpp"
    project.write_text('<REAPER_PROJECT 0.1 "7.0/win64" 0\n>\n', encoding="utf-8")
    target = tmp_path / "ma-config.reka"
    backup.export_backup(cfg, target, project)
    (appdata / "REKA" / "background.png").unlink()            # « autre PC »
    (appdata / "REKA" / "background.jpg").write_bytes(b"ancienne image")

    other = fresh_cfg()
    same_dict = other
    report = backup.import_backup(other, backup.read_backup(target), ["potato", "banana"])
    assert other is same_dict                                  # modifiée sur place
    assert other["ui"]["theme"] == "synthwave" and other["ui"]["colors"] == {"mute": "#ff00aa"}
    assert other["ui"]["strip_names"][0] == "Voix" and other["ui"]["minimize_to_tray"] is True
    assert other["voicemeeter"]["routing"][1] == {"label": "Stream", "bus": "B1"}
    assert other["state"] == {"strips": [{"gain": -6.0, "mute": True}], "headphone_db": -3.0}
    assert other["discord"]["mute_sync"] is True and other["discord"]["client_id"] == "123"
    # Propre à ce PC : inchangé
    assert other["reaper"]["path"] == r"D:\Apps\reaper.exe"
    assert other["discord"]["client_secret"] == "le-mien" and other["discord"]["access_token"] == ""
    assert other["ui"]["geometry"] == "BBBB" and other["update"]["skipped_build"] == 12
    assert other["voicemeeter"]["path"] == ""
    # Image et projet REAPER copiés dans le dossier de REKA
    folder = appdata / "REKA"
    assert (folder / "background.png").read_bytes() == b"\x89PNG image"
    assert not (folder / "background.jpg").exists()
    assert report.project == "Live (importé).rpp" and other["reaper"]["project"] == report.project
    assert (folder / report.project).read_text(encoding="utf-8").startswith("<REAPER_PROJECT")
    assert any("Live" in note for note in report.notes)
    # La config d'avant est gardée, et réimportable
    assert report.previous == folder / "avant-import.reka"
    assert backup.read_backup(report.previous).config["ui"]["theme"] == "studio"

    # Second import : le premier projet importé n'est pas écrasé (REAPER l'a peut-être ouvert)
    again = backup.import_backup(other, backup.read_backup(target), ["potato"])
    assert again.project == "Live (importé 2).rpp"


def test_import_without_extras_and_blank_session_name(cfg, appdata, tmp_path):
    cfg["ui"]["theme"] = "neon"
    project = tmp_path / "session.rpp"
    project.write_text("<REAPER_PROJECT 0.1\n>\n", encoding="utf-8")
    target = tmp_path / "simple.reka"
    saved = backup.export_backup(cfg, target, project)
    assert saved.background is None and saved.project is not None

    other = fresh_cfg()
    other["ui"]["background_image"] = "background.png"
    (appdata / "REKA" / "background.png").write_bytes(b"x")
    report = backup.import_backup(other, saved, keep_previous=False)
    assert other["ui"]["background_image"] == "" and not (appdata / "REKA" / "background.png").exists()
    assert report.project == "Projet (importé).rpp" and report.previous is None

    # Sans projet dans la sauvegarde : le projet de ce PC reste
    backup.export_backup(cfg, target)
    other = fresh_cfg()
    other["reaper"]["project"] = "Mien.rpp"
    report = backup.import_backup(other, backup.read_backup(target), keep_previous=False)
    assert report.project == "" and other["reaper"]["project"] == "Mien.rpp" and report.notes == []


def test_import_keeps_edition_when_not_installed(cfg, appdata, tmp_path):
    cfg["voicemeeter"]["kind"] = "banana"
    project = tmp_path / "Live.rpp"
    project.write_text("<REAPER_PROJECT 0.1\n>\n", encoding="utf-8")
    target = tmp_path / "banana.reka"
    backup.export_backup(cfg, target, project)

    other = fresh_cfg()                                        # Potato, Banana pas installé
    report = backup.import_backup(other, backup.read_backup(target), ["potato"], keep_previous=False)
    assert other["voicemeeter"]["kind"] == "potato" and report.project == ""
    assert other["reaper"]["project"] == ""
    assert len(report.notes) == 2 and "Banana n'est pas installé" in report.notes[0]

    other = fresh_cfg()                                        # Banana installé : on y passe
    report = backup.import_backup(other, backup.read_backup(target), ["potato", "banana"],
                                  keep_previous=False)
    assert other["voicemeeter"]["kind"] == "banana" and report.project


def write_archive(path, manifest=None, cfg=None, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        if manifest is not None:
            archive.writestr("manifest.json", json.dumps(manifest))
        if cfg is not None:
            archive.writestr("config.json", cfg if isinstance(cfg, str) else json.dumps(cfg))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


GOOD = {"app": "REKA", "format": 1, "version": "0.8.0"}


def test_unusable_files_are_refused(tmp_path):
    not_zip = tmp_path / "faux.reka"
    not_zip.write_text("pas une archive", encoding="utf-8")
    for path, message in (
            (not_zip, "pas une sauvegarde REKA lisible"),
            (tmp_path / "absent.reka", "pas une sauvegarde REKA lisible"),
            (write_archive(tmp_path / "vide.reka"), "pas une sauvegarde REKA"),
            (write_archive(tmp_path / "autre.reka", {"app": "Autre", "format": 1}, {}),
             "pas une sauvegarde REKA"),
            (write_archive(tmp_path / "futur.reka", {**GOOD, "format": 99}, {}), "plus récente"),
            (write_archive(tmp_path / "sans.reka", GOOD), "pas une sauvegarde REKA"),
            (write_archive(tmp_path / "casse.reka", GOOD, "{pas du json"), "illisible"),
            (write_archive(tmp_path / "liste.reka", GOOD, "[1, 2]"), "illisible")):
        with pytest.raises(BackupError, match=message):
            backup.read_backup(path)


def test_oversized_members_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "MAX_CONFIG_BYTES", 64)
    big = write_archive(tmp_path / "gros.reka", GOOD, {"ui": {"theme": "x" * 500}})
    with pytest.raises(BackupError, match="trop gros"):
        backup.read_backup(big)
    monkeypatch.setattr(backup, "MAX_CONFIG_BYTES", 4096)
    monkeypatch.setattr(backup, "MAX_PROJECT_BYTES", 16)
    big = write_archive(tmp_path / "projet.reka", GOOD, {}, {"project.rpp": "<REAPER_PROJECT" + "x" * 99})
    with pytest.raises(BackupError, match="trop gros"):
        backup.read_backup(big)


def test_hostile_archive_cannot_write_elsewhere_or_break_config(appdata, tmp_path):
    hostile = write_archive(
        tmp_path / "piege.reka",
        {**GOOD, "background": "../../evil.exe", "project": "../../../Démarrage/virus"},
        {"ui": "pas un dictionnaire", "voicemeeter": {"poll_hz": "vite", "routing": {"a": 1},
                                                      "kind": "potato", "manage_insert": 0},
         "update": {"interval_hours": 2.5}, "inconnu": {"x": 1}, "log_level": 5,
         "discord": {"client_secret": "volé", "access_token": "volé", "mute_sync": True}},
        {"../../evil.exe": b"MZ", "background../../evil.exe": b"MZ", "autre.txt": b"x",
         "project.rpp": b"<REAPER_PROJECT 0.1\n>\n"})
    data = backup.read_backup(hostile)
    assert data.background is None                 # extension inconnue : pas d'image
    other = fresh_cfg()
    report = backup.import_backup(other, data, keep_previous=False)

    folder = appdata / "REKA"
    assert sorted(p.name for p in folder.iterdir()) == ["virus (importé).rpp"]
    assert not list(tmp_path.rglob("evil.exe")) and not list(tmp_path.rglob("autre.txt"))
    assert report.project == "virus (importé).rpp"
    # Valeurs du mauvais type ignorées : REKA démarre encore
    defaults = config.default_config()
    assert other["ui"]["theme"] == defaults["ui"]["theme"]
    assert other["voicemeeter"]["poll_hz"] == 30 and other["voicemeeter"]["manage_insert"] is True
    assert other["voicemeeter"]["routing"] == defaults["voicemeeter"]["routing"]
    assert other["update"]["interval_hours"] == 2.5 and other["log_level"] == "INFO"
    assert other["inconnu"] == {"x": 1}
    # Les secrets d'une sauvegarde ne remplacent jamais ceux du PC
    assert other["discord"]["client_secret"] == "le-mien" and other["discord"]["access_token"] == ""
    assert other["discord"]["mute_sync"] is True


def test_project_that_is_not_reaper_is_ignored(tmp_path):
    path = write_archive(tmp_path / "x.reka", GOOD, {}, {"project.rpp": b"MZ\x90 binaire"})
    assert backup.read_backup(path).project is None


# --------------------------------------------------------------------------- #
# Depuis la console
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def no_save_delay(monkeypatch):
    """Export : pas d'attente de l'enregistrement du projet par REAPER (simulé)."""
    from ui import main_window

    monkeypatch.setattr(main_window, "EXPORT_SAVE_MS", 0)


def test_console_export_includes_current_state(qapp, mixer, tmp_path):
    window, vm, _ = mixer
    vm.set_gain(2, -12.5)
    vm.set_mute(3, True)
    pump(qapp, 0.05)
    messages = []
    window.export_config(tmp_path / "depuis-la-console", lambda ok, text: messages.append((ok, text)))
    pump(qapp, 0.1)
    target = tmp_path / "depuis-la-console.reka"            # extension ajoutée
    assert messages and messages[0][0] and "depuis-la-console.reka" in messages[0][1]
    state = backup.read_backup(target).config["state"]
    assert state["strips"][2]["gain"] == -12.5 and state["strips"][3]["mute"] is True

    ok, text = window.import_config(backup.read_backup(target))
    assert not ok and "démo" in text                        # la démo ne touche pas à la config


def test_console_import_replaces_config_and_keeps_it_on_close(qapp, cfg, appdata, tmp_path, monkeypatch):
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.media import DemoMedia
    from ui import main_window
    from ui.main_window import MainWindow

    source = config.default_config()
    source["ui"]["theme"] = "matrix"
    source["state"] = {"strips": [{"gain": -20.0, "mute": False}], "headphone_db": -9.0}
    target = tmp_path / "ami.reka"
    backup.export_backup(source, target)

    monkeypatch.setattr(main_window, "installed_voicemeeter_kinds", lambda path=None: ["potato"])
    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=DemoReaper(cfg),
                        processes=DemoProcesses(), persist=True, apps=DemoAppVolumes(),
                        media=DemoMedia())
    window.show()
    pump(qapp, 0.1)
    ok, text = window.import_config(backup.read_backup(target))
    assert ok and text.startswith("Config importée.")
    assert cfg["ui"]["theme"] == "matrix"
    assert json.loads(config.user_config_path().read_text(encoding="utf-8"))["ui"]["theme"] == "matrix"
    window.close()                                 # la fermeture n'y remet pas l'état de la console
    saved = json.loads(config.user_config_path().read_text(encoding="utf-8"))
    assert saved["state"] == source["state"] and saved["ui"]["geometry"]
    assert (appdata / "REKA" / "avant-import.reka").is_file()


def test_settings_backup_buttons(qapp, mixer, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    done = []
    dialog.exportRequested.connect(lambda path: window.export_config(path, dialog.show_backup_status))
    dialog.importRequested.connect(done.append)
    dialog.export_config(str(tmp_path / "reglages.reka"))
    assert not dialog.export_button.isEnabled()
    pump(qapp, 0.1)
    assert dialog.export_button.isEnabled() and "reglages.reka" in dialog.backup_status.text()
    assert dialog.backup_status.objectName() == "hint"

    dialog.import_config(str(tmp_path / "reglages.reka"))     # démo : refusé, sans rien lire
    assert "démo" in dialog.backup_status.text() and dialog.backup_status.objectName() == "warning"

    dialog._persist = True
    broken = tmp_path / "abime.reka"
    broken.write_bytes(b"\x00\x01")
    dialog.import_config(str(broken))
    assert "pas une sauvegarde" in dialog.backup_status.text() and done == []

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args: asked.append(args[2]) or QMessageBox.StandardButton.No)
    dialog.import_config(str(tmp_path / "reglages.reka"))
    assert done == [] and "avant-import.reka" in asked[0] and "Voicemeeter Potato" in asked[0]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.import_config(str(tmp_path / "reglages.reka"))
    assert len(done) == 1 and isinstance(done[0], backup.Backup)

    # Après un import, le dialogue n'écrit plus ses champs dans la nouvelle config
    dialog.config_replaced()
    window._cfg["voicemeeter"]["terminate_on_exit"] = "importé"
    dialog.done(0)
    assert window._cfg["voicemeeter"]["terminate_on_exit"] == "importé"


def test_import_from_settings_restarts_reka(qapp, cfg, appdata, tmp_path, monkeypatch):
    """Parcours complet : ⚙ → Importer… → confirmation → config remplacée → redémarrage."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.media import DemoMedia
    from ui import main_window
    from ui.main_window import MainWindow

    source = config.default_config()
    source["ui"].update(theme="sakura", strip_names=["A", "B", "C", "D", "E", "F"])
    source["voicemeeter"]["terminate_on_exit"] = False
    source["state"] = {"strips": [{"gain": -30.0, "mute": True}], "headphone_db": -4.0}
    target = tmp_path / "ami.reka"
    backup.export_backup(source, target)

    restarts, shown = [], []
    monkeypatch.setattr(main_window, "installed_voicemeeter_kinds", lambda path=None: ["potato"])
    monkeypatch.setattr(main_window, "launch_restart", lambda: restarts.append(1) or True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: shown.append(args[2]))
    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=DemoReaper(cfg),
                        processes=DemoProcesses(), persist=True, apps=DemoAppVolumes(),
                        media=DemoMedia())
    window.show()
    pump(qapp, 0.1)

    def in_settings() -> None:
        dialog = QApplication.activeModalWidget()
        assert dialog.close_vm.isChecked()            # réglage de ce PC, affiché dans le dialogue
        dialog.import_config(str(target))

    QTimer.singleShot(150, in_settings)
    window.open_settings()
    pump(qapp, 0.1)
    assert restarts == [1] and not window.isVisible()         # relancée, fenêtre fermée
    assert "Config importée." in shown[0] and "redémarre" in shown[0]
    saved = json.loads(config.user_config_path().read_text(encoding="utf-8"))
    assert saved["ui"]["theme"] == "sakura" and saved["ui"]["strip_names"][0] == "A"
    assert saved["state"] == source["state"]                  # pas l'état de la console en cours
    assert saved["voicemeeter"]["terminate_on_exit"] is False  # pas la case du dialogue
    assert saved is not cfg and cfg["ui"]["theme"] == "sakura"
