"""
Moteur d'effets intégré (reka-host.exe) : protocole, chaînes d'effets,
relance après un arrêt, partage dans les .reka. L'hôte est remplacé par le
faux hôte Python (core/reka_engine_fake.py), qui parle le même protocole.
"""

import base64
import json
import zipfile
from pathlib import Path

import pytest

from conftest import pump


def _wait(app, predicate, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class Launcher:
    """Lanceur de faux hôtes qui garde la trace de chaque lancement."""

    def __init__(self, **options):
        self.engines = []
        self.options = options

    def __call__(self, port, token):
        from core.reka_engine_fake import FakeEngine

        engine = FakeEngine(port, token, **self.options)
        self.engines.append(engine)
        engine.start()
        return engine

    @property
    def last(self):
        return self.engines[-1]


@pytest.fixture
def host_factory(qapp, cfg, tmp_path):
    from core.reka_host import RekaHostController

    created = []

    def make(launcher=None, chains_file=None):
        cfg["effects"]["host"] = "reka"
        launcher = launcher or Launcher()
        host = RekaHostController(cfg, launcher=launcher,
                                  chains_file=chains_file or tmp_path / "chains.json")
        host.launcher = launcher
        tracks, errors = [], []
        host.tracks_changed.connect(tracks.append)
        host.command_failed.connect(errors.append)
        host.seen_tracks, host.errors = tracks, errors
        created.append(host)
        return host

    yield make
    for host in created:
        host.close(save_project=False)


def _plugin(tmp_path, name, fmt="VST3"):
    path = tmp_path / "plugins" / (name + (".vst3" if fmt == "VST3" else ".dll"))
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"x")
    return path


# ------------------------------------------------------------------ identité
def test_add_name_round_trip():
    from core.reka_host import PluginRef, parse_add_name

    ref = PluginRef("VST3", r"C:\Program Files\Common Files\VST3\Pro-Q 4.vst3", "ABCD", "Pro-Q 4")
    parsed = parse_add_name(ref.add_name)
    assert parsed == PluginRef("VST3", ref.path, "ABCD", "Pro-Q 4")
    # Chemin seul : format deviné par l'extension
    assert parse_add_name(r"D:\VST\Comp.dll").format == "VST2"
    assert parse_add_name(r"D:\VST3\Eq.vst3").format == "VST3"
    assert parse_add_name("vst2:D:\\VST\\Shell.dll|1234").uid == "1234"
    assert parse_add_name("") is None


def test_chains_are_checked(tmp_path):
    from core.reka_host import ChainsError, clean_chains, load_chains_file, parse_chains

    good = base64.b64encode(b"state").decode()
    doc = clean_chains({"strips": [
        {"enabled": False, "fx": [
            {"format": "vst3", "path": "C:/a.vst3", "state": good, "enabled": True},
            {"format": "AU", "path": "x"},                      # format inconnu
            {"format": "VST2", "path": 42},                     # chemin invalide
            {"format": "VST2", "path": "C:/b.dll", "state": "pas du base64 !"},
            "n'importe quoi"]},
        "pas une strip"]}, count=3)
    assert [len(s["fx"]) for s in doc["strips"]] == [2, 0, 0]
    first, second = doc["strips"][0]["fx"]
    assert first["format"] == "VST3" and first["state"] == good and first["name"] == "a"
    assert second["state"] == ""               # réglages illisibles : plugin gardé, réglages non
    assert doc["strips"][0]["enabled"] is False
    for bad in (b"{", b"[]", json.dumps({"format": 99, "strips": []}).encode()):
        with pytest.raises(ChainsError):
            parse_chains(bad)
    # Fichier abîmé : chaînes vides, fichier mis de côté
    path = tmp_path / "chains.json"
    path.write_text("{abîmé", encoding="utf-8")
    assert load_chains_file(path, 2)["strips"] == [{"enabled": True, "fx": []}] * 2
    assert (tmp_path / "chains.abime.json").is_file()


def test_scan_and_resolve_paths(tmp_path, monkeypatch):
    from core import reka_host
    from core.reka_host import resolve_paths, scan_plugins

    vst3 = tmp_path / "VST3"
    (vst3 / "FabFilter" / "Pro-Q 4.vst3" / "Contents").mkdir(parents=True)
    (vst3 / "Solo.vst3").write_bytes(b"x")
    monkeypatch.setattr(reka_host, "default_vst3_dirs", lambda extra=None: [vst3])
    monkeypatch.setattr(reka_host, "default_vst2_dirs", lambda extra=None: [])
    plugins = scan_plugins()
    assert [(p.name, p.kind) for p in plugins] == [("Pro-Q 4", "VST3"), ("Solo", "VST3")]
    assert plugins[0].add_name.startswith("VST3:") and plugins[0].add_name.endswith("Pro-Q 4.vst3")

    # Chaîne venue d'un autre PC : le plugin est retrouvé par son nom de fichier
    doc = {"strips": [{"fx": [
        {"format": "VST3", "path": r"E:\Autre PC\Pro-Q 4.vst3", "name": "Pro-Q 4"},
        {"format": "VST3", "path": r"E:\Autre PC\Absent.vst3", "name": "Absent"}]}]}
    assert resolve_paths(doc, plugins) == ["Absent"]
    assert doc["strips"][0]["fx"][0]["path"] == str(vst3 / "FabFilter" / "Pro-Q 4.vst3")


# ------------------------------------------------------------------ contrôleur
def test_factory_and_status(qapp, cfg):
    from core.effects import HOSTS, create_host
    from core.reka_host import RekaHostController

    cfg["effects"]["host"] = "reka"
    host = create_host(cfg)
    assert isinstance(host, RekaHostController) and host.kind == "reka" == HOSTS["reka"].key
    assert host.manages_fx and host.can_move_fx and not host.has_projects
    assert host.insert_strips([None] * 6, True) == set()       # pas d'insert ASIO
    assert host.status(False)[0] is False


def test_effects_are_driven_and_saved(qapp, host_factory, tmp_path):
    host = host_factory()
    host.start()
    assert _wait(qapp, lambda: host.is_connected)
    engine = host.launcher.last
    setup = next(m for m in engine.received if m["cmd"] == "setup")
    # Une strip est décrite par son premier canal d'insert et son nombre de canaux
    assert [(s["channel"], s["channels"]) for s in setup["strips"]] == \
        [(0, 2), (2, 2), (10, 8), (18, 8), (4, 2), (26, 8)]
    assert host.status(True)[0]

    eq, comp = _plugin(tmp_path, "Pro-Q 4"), _plugin(tmp_path, "Comp", "VST2")
    host.add_fx(0, f"VST3:{eq}")
    host.add_fx(0, f"VST2:{comp}")
    assert _wait(qapp, lambda: len(host.strip_tracks[0].fx) == 2)
    track = host.strip_tracks[0]
    assert [fx.display_name for fx in track.fx] == ["Pro-Q 4", "Comp"]
    assert track.fx[0].name.startswith("VST3:") and track.fx[1].name.startswith("VST2:")
    assert host.strip_latency_ms(0) == pytest.approx(2 * 64 / 48000 * 1000)
    assert host.fx_latency_ms(0, 1) == pytest.approx(64 / 48000 * 1000)

    host.move_fx(0, 0, 2, 0, copy=True)            # copie, réglages compris
    host.move_fx(0, 1, 0, 0)                        # Comp avant Pro-Q 4
    host.set_fx_enabled(0, 1, False)
    host.set_chain_enabled(2, False)
    host.toggle_fx_ui(2, 0)
    assert _wait(qapp, lambda: [fx.display_name for fx in host.strip_tracks[0].fx] == ["Comp", "Pro-Q 4"]
                 and len(host.strip_tracks[2].fx) == 1 and host.strip_tracks[2].fx[0].open)
    assert host.strip_tracks[0].fx[1].enabled is False and not host.strip_tracks[2].fx_enabled

    # Les chaînes (réglages des plugins compris) sont enregistrées après chaque changement
    assert _wait(qapp, lambda: host.chains_file.is_file()
                 and len(json.loads(host.chains_file.read_text())["strips"][2]["fx"]) == 1)
    saved = json.loads(host.chains_file.read_text())
    assert saved["strips"][2]["enabled"] is False
    assert saved["strips"][0]["fx"][1]["enabled"] is False
    assert saved["strips"][2]["fx"][0]["state"] == saved["strips"][0]["fx"][1]["state"] != ""

    # Réglage changé dans l'interface d'un plugin : relu tout seul
    engine.edit_parameter()
    assert _wait(qapp, lambda: json.loads(base64.b64decode(json.loads(
        host.chains_file.read_text())["strips"][0]["fx"][0]["state"]))["gain"] == 6, timeout=6)
    assert host.errors == []


def test_chains_reloaded_at_next_launch(qapp, host_factory, tmp_path):
    chains = tmp_path / "chains.json"
    eq = _plugin(tmp_path, "Eq")
    state = base64.b64encode(b"mes reglages").decode()
    chains.write_text(json.dumps({"format": 1, "strips": [
        {"enabled": True, "fx": [{"format": "VST3", "path": str(eq), "name": "Eq",
                                  "enabled": True, "state": state}]}]}))
    host = host_factory(chains_file=chains)
    # Avant la connexion, la console montre déjà les chaînes enregistrées
    assert [fx.display_name for fx in host.strip_tracks[0].fx] == ["Eq"]
    host.start()
    assert _wait(qapp, lambda: host.is_connected)
    load = next(m for m in host.launcher.last.received if m["cmd"] == "load")
    assert load["chains"]["strips"][0]["fx"][0]["state"] == state
    assert len(load["chains"]["strips"]) == 6

    host.close(save_project=True)                  # chaînes relues et enregistrées en partant
    assert json.loads(chains.read_text())["strips"][0]["fx"][0]["state"] == state
    assert any(m.get("cmd") == "quit" for m in host.launcher.last.received)


def test_missing_plugin_is_kept_and_reported(qapp, host_factory, tmp_path):
    chains = tmp_path / "chains.json"
    chains.write_text(json.dumps({"format": 1, "strips": [
        {"fx": [{"format": "VST3", "path": "Z:/nulle part/Absent.vst3", "name": "Absent",
                 "state": base64.b64encode(b"r").decode()}]}]}))
    launcher = Launcher()
    host = host_factory(launcher=launcher, chains_file=chains)
    original = launcher.__call__

    def launch(port, token):
        engine = original(port, token)
        engine.missing_paths.add("Z:/nulle part/Absent.vst3")
        return engine
    host._launcher = launch
    host.start()
    assert _wait(qapp, lambda: host.is_connected and host.strip_tracks[0].fx)
    fx = host.strip_tracks[0].fx[0]
    assert fx.display_name == "Absent (introuvable)" and not fx.enabled
    assert any("Absent" in e for e in host.errors)
    host.close(save_project=True)
    # Ses réglages ne sont pas perdus
    assert json.loads(chains.read_text())["strips"][0]["fx"][0]["state"]


def test_engine_restarted_after_crash(qapp, host_factory, tmp_path, monkeypatch):
    from core import reka_host

    monkeypatch.setattr(reka_host, "RESTART_DELAY_S", 0.05)
    host = host_factory()
    states = []
    host.connection_changed.connect(states.append)
    host.start()
    assert _wait(qapp, lambda: host.is_connected)
    host.add_fx(1, f"VST3:{_plugin(tmp_path, 'Reverb')}")
    assert _wait(qapp, lambda: host.chains_file.is_file()
                 and json.loads(host.chains_file.read_text())["strips"][1]["fx"])
    host.launcher.last.crash()
    assert _wait(qapp, lambda: len(host.launcher.engines) == 2 and host.is_connected)
    assert False in states
    # Le nouvel hôte reçoit les chaînes enregistrées
    load = next(m for m in host.launcher.last.received if m["cmd"] == "load")
    assert load["chains"]["strips"][1]["fx"][0]["name"] == "Reverb"

    # Arrêts à répétition : REKA abandonne et le dit
    for _ in range(reka_host.MAX_RESTARTS + 1):
        count = len(host.launcher.engines)
        assert _wait(qapp, lambda: host.is_connected or host.status(False)[1].endswith("arrêté"))
        host.launcher.last.crash()
        _wait(qapp, lambda: len(host.launcher.engines) > count or host._gave_up)
    assert _wait(qapp, lambda: host._gave_up)
    assert host.status(False)[1] == "REKA arrêté"


def test_wrong_token_is_refused(qapp, host_factory):
    from core.reka_engine_fake import FakeEngine

    engines = []

    def launch(port, token):
        engine = FakeEngine(port, "mauvais-jeton")
        engines.append(engine)
        engine.start()
        return engine
    host = host_factory(launcher=launch)
    host.start()
    pump(qapp, 0.5)
    assert not host.is_connected


def test_commands_refused_while_disconnected(qapp, host_factory, tmp_path):
    host = host_factory()
    host.add_fx(0, f"VST3:{_plugin(tmp_path, 'Eq')}")
    assert host.errors and "pas prêt" in host.errors[0]


# ------------------------------------------------------------------ partage .reka
def test_backup_carries_effect_chains(qapp, cfg, appdata, tmp_path):
    from core import backup, config
    from core.reka_host import CHAINS_FILENAME

    cfg["effects"]["host"] = "reka"
    chains = tmp_path / "chains.json"
    state = base64.b64encode(b"\x00\x01reglages").decode()
    chains.write_text(json.dumps({"format": 1, "strips": [
        {"enabled": True, "fx": [{"format": "VST2", "path": "C:/VST/Comp.dll", "name": "Comp",
                                  "enabled": True, "state": state},
                                 {"format": "VST3", "path": "C:/VST3/Eq.vst3", "name": "Eq"}]}]}))
    target = tmp_path / "partage.reka"
    saved = backup.export_backup(cfg, target, chains=chains)
    assert "2 effets REKA" in saved.describe()
    with zipfile.ZipFile(target) as archive:
        assert backup.CHAINS in archive.namelist()

    data = backup.read_backup(target)
    assert data.host == "reka" and data.effect_count == 2
    previous = config.app_data_dir() / CHAINS_FILENAME
    previous.write_text(json.dumps({"format": 1, "strips": []}))
    report = backup.import_backup(cfg, data)
    assert report.chains and any("2 effets" in n for n in report.notes)
    imported = json.loads((config.app_data_dir() / CHAINS_FILENAME).read_text())
    assert imported["strips"][0]["fx"][0]["state"] == state
    assert (config.app_data_dir() / f"avant-import-{CHAINS_FILENAME}").is_file()


def test_backup_with_hostile_chains_is_still_usable(tmp_path):
    from core import backup

    target = tmp_path / "bizarre.reka"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(backup.MANIFEST, json.dumps({"app": "REKA", "format": 1}))
        archive.writestr(backup.CONFIG, "{}")
        archive.writestr(backup.CHAINS, json.dumps({"strips": [
            {"fx": [{"format": "VST3", "path": "../../evil.vst3", "state": "%%%"}] * 500}] * 50}))
    data = backup.read_backup(target)
    # Taille bornée, réglages illisibles écartés
    assert len(data.chains["strips"]) <= 16
    assert all(len(s["fx"]) <= 32 for s in data.chains["strips"])
    assert all(fx["state"] == "" for s in data.chains["strips"] for fx in s["fx"])


# ------------------------------------------------------------------ console
def test_console_with_reka_engine(qapp, cfg, tmp_path):
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoVoicemeeter
    from core.media import DemoMedia
    from core.reka_host import RekaHostController
    from ui.main_window import MainWindow

    cfg["effects"]["host"] = "reka"
    launcher = Launcher()
    host = RekaHostController(cfg, launcher=launcher, chains_file=tmp_path / "chains.json")
    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=host, processes=DemoProcesses(),
                        persist=False, apps=DemoAppVolumes(), media=DemoMedia())
    try:
        host.start()
        window.show()
        assert _wait(qapp, lambda: host.is_connected)
        assert not window.master.project.isVisible()
        host.add_fx(1, f"VST3:{_plugin(tmp_path, 'Pro-Q 4')}")
        assert _wait(qapp, lambda: window.strips[1].fx_name(0) == "Pro-Q 4")
        window.move_effect(1, 0, 3, 0, copy=True)
        assert _wait(qapp, lambda: window.strips[3].fx_name(0) == "Pro-Q 4")
        assert "aucun tampon ASIO" in host.latency_report()
    finally:
        host.close(save_project=False)
        window.close()
