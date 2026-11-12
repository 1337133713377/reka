"""
Le vrai reka-host.exe, piloté par REKA, avec un vrai plugin VST2
(reka-test-gain.dll, construit avec l'hôte : native/reka-host/test).

Sans Voicemeeter (machine de build), l'hôte ne traite pas de son mais tout le
reste est réel : chargement du plugin, réglages, copie, chaînes, interface
refusée proprement. Lancé sous Windows, ou sous Linux avec Wine.

Emplacement des binaires : variables REKA_HOST_EXE et REKA_TEST_PLUGIN, sinon
engine/reka-host.exe et engine/reka-test-gain.dll.
"""

import base64
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXE = Path(os.environ.get("REKA_HOST_EXE") or ROOT / "engine" / "reka-host.exe")
PLUGIN = Path(os.environ.get("REKA_TEST_PLUGIN") or ROOT / "engine" / "reka-test-gain.dll")
PLUGIN3 = Path(os.environ.get("REKA_TEST_PLUGIN3") or ROOT / "engine" / "reka-test-gain3.vst3")
FAKE_VM = Path(os.environ.get("REKA_FAKE_VOICEMEETER")
               or ROOT / "engine" / "fake-voicemeeter" / "VoicemeeterRemote64.dll")
WINE = None if sys.platform == "win32" else shutil.which("wine")

pytestmark = pytest.mark.skipif(
    not (EXE.is_file() and PLUGIN.is_file() and (sys.platform == "win32" or WINE)),
    reason="reka-host.exe / plugin de test absents (ou ni Windows ni Wine)")


def windows_path(path: Path) -> str:
    return str(path) if sys.platform == "win32" else "Z:" + str(path).replace("/", "\\")


def launcher(log_dir: Path, env: dict | None = None):
    def launch(port, token):
        args = [str(EXE), "--port", str(port), "--token", token, "--client", "REKA-tests",
                "--log", windows_path(log_dir / "reka-host.log")]
        environment = dict(os.environ, **(env or {}))
        environment.pop("REKA_VOICEMEETER_DLL", None) if env is None else None
        if WINE:
            environment["WINEDEBUG"] = "-all"
            return subprocess.Popen([WINE, *args], env=environment, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        return subprocess.Popen(args, env=environment)
    return launch


def _wait(app, predicate, timeout=30.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _saved_gain(path: Path, strip: int) -> float | None:
    """Réglage du premier plugin d'une strip dans chains.json (None pendant une écriture)."""
    try:
        return _gain(json.loads(path.read_text())["strips"][strip]["fx"][0]["state"])
    except (OSError, ValueError, KeyError, IndexError):
        return None


def _gain(state_b64: str) -> float:
    data = base64.b64decode(state_b64)
    assert data[:4] == b"RKP2"           # réglages = valeurs des paramètres
    _program, count = struct.unpack_from("<ii", data, 4)
    assert count == 1
    return struct.unpack_from("<f", data, 12)[0]


def test_real_host_with_real_plugin(qapp, cfg, tmp_path):
    from core.reka_host import RekaHostController

    cfg["effects"]["host"] = "reka"
    host = RekaHostController(cfg, launcher=launcher(tmp_path), chains_file=tmp_path / "chains.json")
    errors = []
    host.command_failed.connect(errors.append)
    try:
        host.start()
        assert _wait(qapp, lambda: host.is_connected, 60), "l'hôte ne s'est pas connecté"
        assert host.version.startswith("REKA ")
        # Pas de Voicemeeter ici : l'hôte attend, et le dit
        assert not host.engine_status.running and host.engine_status.message

        plugin = windows_path(PLUGIN)
        host.add_fx(0, f"VST2:{plugin}")
        assert _wait(qapp, lambda: len(host.strip_tracks[0].fx) == 1)
        fx = host.strip_tracks[0].fx[0]
        assert fx.display_name == "REKA Test Gain" and fx.enabled and fx.latency == 32

        host.move_fx(0, 0, 3, 0, copy=True)
        host.set_fx_enabled(3, 0, False)
        assert _wait(qapp, lambda: len(host.strip_tracks[3].fx) == 1
                     and not host.strip_tracks[3].fx[0].enabled)

        def saved_copy_disabled():
            try:
                fx = json.loads(host.chains_file.read_text())["strips"][3]["fx"]
            except (OSError, ValueError):
                return False
            return bool(fx) and fx[0]["enabled"] is False
        host.save_project()
        assert _wait(qapp, saved_copy_disabled)
        saved = json.loads(host.chains_file.read_text())
        first, copy = saved["strips"][0]["fx"][0], saved["strips"][3]["fx"][0]
        assert first["name"] == "REKA Test Gain" and first["vendor"] == "REKA"
        assert first["uid"] == str(0x524B4731)
        assert _gain(first["state"]) == pytest.approx(0.5) == _gain(copy["state"])
        assert copy["enabled"] is False

        # Plugin introuvable : erreur claire, rien d'ajouté
        host.add_fx(1, "VST2:Z:\\nulle\\part\\absent.dll")
        assert _wait(qapp, lambda: any("absent" in e for e in errors))
        assert host.strip_tracks[1].fx == ()

        # Plugin sans interface : refusé proprement
        errors.clear()
        host.toggle_fx_ui(0, 0)
        assert _wait(qapp, lambda: any("interface" in e for e in errors))

        host.remove_fx(0, 0)
        assert _wait(qapp, lambda: host.strip_tracks[0].fx == ())
    finally:
        host.close(save_project=True)

    # Relance : la chaîne est rechargée avec ses réglages (ici, modifiés à la main)
    saved = json.loads((tmp_path / "chains.json").read_text())
    state = bytearray(base64.b64decode(saved["strips"][3]["fx"][0]["state"]))
    struct.pack_into("<f", state, 12, 0.25)
    saved["strips"][3]["fx"][0]["state"] = base64.b64encode(bytes(state)).decode()
    (tmp_path / "chains.json").write_text(json.dumps(saved))
    host = RekaHostController(cfg, launcher=launcher(tmp_path), chains_file=tmp_path / "chains.json")
    try:
        host.start()
        assert _wait(qapp, lambda: host.is_connected and host.strip_tracks[3].fx, 60)
        host.save_project()
        assert _wait(qapp, lambda: _saved_gain(tmp_path / "chains.json", 3) == pytest.approx(0.25))
    finally:
        host.close(save_project=False)
    log = (tmp_path / "reka-host.log").read_text(encoding="utf-8", errors="replace")
    assert "reka-host" in log


def _gain_state(gain: float) -> str:
    return base64.b64encode(b"RKP2" + struct.pack("<iif", 0, 1, gain)).decode()


def _read_output(path: Path) -> dict[int, float]:
    try:
        lines = path.read_text().split("\n")
    except OSError:
        return {}
    values = {}
    for line in lines:
        parts = line.split()
        if len(parts) == 2:
            values[int(parts[0])] = float(parts[1])
    return values


@pytest.mark.skipif(not FAKE_VM.is_file(), reason="faux Voicemeeter absent")
def test_real_host_processes_voicemeeter_audio(qapp, cfg, tmp_path):
    """Son de Voicemeeter (simulé) traité par la chaîne de chaque strip :
    entrée du canal c = (c + 1) / 100, gain du plugin de test = 2 × réglage."""
    from core.reka_host import RekaHostController

    cfg["effects"]["host"] = "reka"
    plugin = windows_path(PLUGIN)
    fx = {"format": "VST2", "path": plugin, "name": "Gain", "enabled": True}
    chains = {"format": 1, "strips": [
        {"enabled": True, "fx": [dict(fx, state=_gain_state(0.25))]},          # canaux 0-1 : ×0,5
        {"enabled": True, "fx": []},
        {"enabled": True, "fx": [dict(fx, enabled=False, state=_gain_state(0))]},  # 10-17 : coupé
        {"enabled": True, "fx": [dict(fx, state=_gain_state(1.0))]},           # 18-25 : ×2 (L/R)
        {"enabled": False, "fx": [dict(fx, state=_gain_state(0))]},            # 4-5 : chaîne off
        {"enabled": True, "fx": []}]}
    (tmp_path / "chains.json").write_text(json.dumps(chains))
    out = tmp_path / "vm-out.txt"
    env = {"REKA_VOICEMEETER_DLL": windows_path(FAKE_VM), "FAKE_VOICEMEETER_OUT": windows_path(out)}
    host = RekaHostController(cfg, launcher=launcher(tmp_path, env),
                              chains_file=tmp_path / "chains.json")
    try:
        host.start()
        assert _wait(qapp, lambda: host.is_connected and host.engine_status.running, 60)
        assert host.status(True)[0]
        assert (host.engine_status.samplerate, host.engine_status.blocksize) == (48000, 256)
        assert host.strip_latency_ms(0) == pytest.approx(32 / 48)
        assert host.strip_latency_ms(2) == 0          # plugin coupé : pas de latence

        def expected(values):
            want = {c: (c + 1) / 100 for c in range(34)}
            want.update({0: 0.005, 1: 0.01, 18: 0.38, 19: 0.40})
            return all(abs(values.get(c, -9) - v) < 1e-5 for c, v in want.items())
        assert _wait(qapp, lambda: expected(_read_output(out)), 20), _read_output(out)

        # Plugin coupé depuis REKA : le son repasse tel quel
        host.set_fx_enabled(0, 0, False)
        assert _wait(qapp, lambda: abs(_read_output(out).get(0, -9) - 0.01) < 1e-5, 20)
    finally:
        host.close(save_project=False)


def test_real_host_applies_settings_taken_from_reaper(qapp, cfg, tmp_path):
    """Chaîne venue de REAPER : pas encore de réglages au format de REKA, seulement
    le chunk (ignoré : ce plugin n'en a pas) et les paramètres."""
    from core.reka_host import RekaHostController

    cfg["effects"]["host"] = "reka"
    plugin = windows_path(PLUGIN)
    (tmp_path / "chains.json").write_text(json.dumps({"format": 1, "strips": [{"fx": [
        {"format": "VST2", "path": plugin, "name": "Gain", "chunk": "AAEC",
         "params": [["Gain", 0.125], ["Bypass", 1.0]]},
        {"format": "VST3", "path": "Z:\\nulle\\part\\Absent.vst3", "name": "Absent",
         "params": [["Band 1 Gain", 0.75]]}]}]}))
    host = RekaHostController(cfg, launcher=launcher(tmp_path), chains_file=tmp_path / "chains.json")
    try:
        host.start()
        assert _wait(qapp, lambda: host.is_connected and len(host.strip_tracks[0].fx) == 2, 60)
        host.save_project()
        assert _wait(qapp, lambda: _saved_gain(tmp_path / "chains.json", 0) == pytest.approx(0.125))
        saved = json.loads((tmp_path / "chains.json").read_text())["strips"][0]["fx"]
        # Réglages désormais au format de REKA ; ceux du plugin absent attendent toujours
        assert "params" not in saved[0] and "chunk" not in saved[0]
        assert saved[1]["state"] == "" and saved[1]["params"] == [["Band 1 Gain", 0.75]]
    finally:
        host.close(save_project=False)


def _vst3_gain(state_b64: str) -> float:
    data = base64.b64decode(state_b64)
    assert data[:4] == b"RK31"           # état du composant, puis du contrôleur
    size = struct.unpack_from("<I", data, 4)[0]
    assert size == 4
    return struct.unpack_from("<f", data, 8)[0]


@pytest.mark.skipif(not (PLUGIN3.is_file() and FAKE_VM.is_file()),
                    reason="plugin VST3 de test ou faux Voicemeeter absent")
def test_real_host_with_real_vst3_plugin(qapp, cfg, tmp_path):
    """Chemin VST3 complet : chargement, paramètre repris de REAPER (par son
    nom), état enregistré, et son traité par le plugin."""
    from core.reka_host import RekaHostController

    cfg["effects"]["host"] = "reka"
    plugin = windows_path(PLUGIN3)
    (tmp_path / "chains.json").write_text(json.dumps({"format": 1, "strips": [
        {"fx": [{"format": "VST3", "path": plugin, "name": "REKA Test Gain 3",
                 "params": [["Gain", 1.0]]}]}]}))
    out = tmp_path / "vm-out.txt"
    env = {"REKA_VOICEMEETER_DLL": windows_path(FAKE_VM), "FAKE_VOICEMEETER_OUT": windows_path(out)}
    host = RekaHostController(cfg, launcher=launcher(tmp_path, env),
                              chains_file=tmp_path / "chains.json")
    errors = []
    host.command_failed.connect(errors.append)
    try:
        host.start()
        assert _wait(qapp, lambda: host.is_connected and host.engine_status.running
                     and host.strip_tracks[0].fx, 60)
        fx = host.strip_tracks[0].fx[0]
        assert fx.display_name == "REKA Test Gain 3" and fx.enabled and fx.latency == 16
        # Gain repris de REAPER (1,0 → ×2) : appliqué au son de la strip
        assert _wait(qapp, lambda: abs(_read_output(out).get(0, -9) - 0.02) < 1e-5
                     and abs(_read_output(out).get(1, -9) - 0.04) < 1e-5, 20), _read_output(out)
        host.save_project()

        def saved_gain():
            try:
                fx = json.loads((tmp_path / "chains.json").read_text())["strips"][0]["fx"][0]
                return _vst3_gain(fx["state"]) if fx.get("state") else None
            except (OSError, ValueError, KeyError, IndexError):
                return None
        assert _wait(qapp, lambda: saved_gain() == pytest.approx(1.0))

        # Ajout depuis REKA, puis copie (réglages compris) sur une autre strip
        host.add_fx(1, f"VST3:{plugin}")
        assert _wait(qapp, lambda: len(host.strip_tracks[1].fx) == 1)
        host.move_fx(0, 0, 4, 0, copy=True)
        # Strip 4 = canaux 4-5 : ×2 aussi, grâce aux réglages copiés
        assert _wait(qapp, lambda: abs(_read_output(out).get(4, -9) - 0.10) < 1e-5, 20)
        # Sous Wine, REKA ne peut pas vérifier un chemin « Z:\… » : faux « introuvable »
        assert [e for e in errors if not (WINE and "introuvable" in e)] == []
    finally:
        host.close(save_project=False)
