"""
Écoute au casque : égalisation du casque (profils AutoEQ, fichiers importés)
et son surround virtuel. Le moteur est le faux hôte Python (même protocole
que reka-host.exe) ; le traitement du son lui-même (src/listen.cpp) est testé
par native/reka-host/test/listen_test.cpp, compilé ici s'il y a un compilateur.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from conftest import pump

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native" / "reka-host"

HD600 = """Preamp: -6.3 dB
Filter 1: ON LSC Fc 105 Hz Gain 6.5 dB Q 0.70
Filter 2: ON PK Fc 125 Hz Gain -2.7 dB Q 0.55
Filter 3: ON PK Fc 8445 Hz Gain 3.3 dB Q 1.61
Filter 4: ON PK Fc 522 Hz Gain 0.7 dB Q 1.02
Filter 5: ON PK Fc 1298 Hz Gain -1.2 dB Q 2.14
Filter 6: ON HSC Fc 10000 Hz Gain -3.1 dB Q 0.70
Filter 7: ON PK Fc 3158 Hz Gain -1.8 dB Q 3.67
Filter 8: ON PK Fc 2166 Hz Gain 0.9 dB Q 3.32
Filter 9: ON PK Fc 6639 Hz Gain 2.2 dB Q 5.82
Filter 10: ON PK Fc 5433 Hz Gain -1.2 dB Q 5.70
"""

INDEX = """# Index
This is a list of all equalization profiles.

- [Sennheiser HD 600](./crinacle/GRAS%2043AG-7%20over-ear/Sennheiser%20HD%20600) by crinacle on GRAS 43AG-7
- [Sennheiser HD 600](./oratory1990/over-ear/Sennheiser%20HD%20600) by oratory1990
- [Sennheiser HD 6XX](./oratory1990/over-ear/Sennheiser%20HD%206XX) by oratory1990
- [Sennheiser HD 600 (worn earpads)](./Innerfidelity/over-ear/Sennheiser%20HD%20600%20(worn%20earpads)) by Innerfidelity
- [1MORE Aero (ANC Off)](./HypetheSonics/GRAS%20RA0045%20in-ear/1MORE%20Aero%20(ANC%20Off)) by HypetheSonics on GRAS RA0045
- [1MORE Quad Driver](./HypetheSonics/Bruel%20&%20Kjaer%205128%20in-ear/1MORE%20Quad%20Driver) by HypetheSonics on Bruel & Kjaer 5128
- [Evil](./../../etc/passwd) by nobody
"""


def _wait(app, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --------------------------------------------------------------------------- #
# Fichiers d'égalisation
# --------------------------------------------------------------------------- #
def test_parse_autoeq_profile():
    from core.listening import parse_parametric_eq

    eq = parse_parametric_eq(HD600)
    assert eq.preamp == pytest.approx(-6.3)
    assert len(eq.filters) == 10
    assert [f.type for f in eq.filters[:3]] == ["LS", "PK", "PK"]
    assert eq.filters[5].type == "HS"
    first = eq.filters[0]
    assert (first.freq, first.gain, first.q) == (105, 6.5, 0.7)


def test_parse_equalizer_apo_config_without_preamp():
    from core.listening import parse_parametric_eq, response_db, log_frequencies

    text = """# config Equalizer APO
Device: Casque
Filter: ON PK Fc 3000 Hz Gain 4 dB Q 1.0
Filter: OFF PK Fc 100 Hz Gain 9 dB Q 1.0
Filter: ON LS Fc 80 Hz Gain -2 dB
Filter: ON HP Fc 20 Hz
Filter: ON XYZ Fc 20 Hz Gain 3 dB
Include: autre.txt
"""
    eq = parse_parametric_eq(text)
    assert [f.type for f in eq.filters] == ["PK", "LS", "HP"]   # OFF et inconnu ignorés
    assert eq.filters[1].q == pytest.approx(0.707)
    # Préampli absent : calculé pour ne jamais saturer
    peak = max(response_db(eq.preamp, eq.filters, log_frequencies(400)))
    assert -0.2 < peak <= 0.06
    assert eq.preamp == pytest.approx(-4.0, abs=0.1)


@pytest.mark.parametrize("text", ["", "Bonjour", "Preamp: -3 dB\n",
                                  "\n".join(f"Filter {i}: ON PK Fc 1000 Hz Gain 1 dB Q 1"
                                            for i in range(40))])
def test_parse_rejects_bad_files(text):
    from core.listening import EqParseError, parse_parametric_eq

    with pytest.raises(EqParseError):
        parse_parametric_eq(text)


def test_values_are_clamped_and_roundtrip():
    from core.listening import format_parametric_eq, parse_parametric_eq

    eq = parse_parametric_eq("Preamp: -99 dB\nFilter 1: ON PK Fc 90000 Hz Gain 80 dB Q 0,01\n")
    f = eq.filters[0]
    assert eq.preamp == -30 and f.freq == 24000 and f.gain == 30 and f.q == 0.05
    again = parse_parametric_eq(format_parametric_eq(parse_parametric_eq(HD600)))
    assert again == parse_parametric_eq(HD600)


def test_response_curve():
    from core.listening import EqFilter, response_db

    peak = EqFilter("PK", 1000, 6, 1.0)
    at, far = response_db(-2.0, [peak], [1000.0, 30.0])
    assert at == pytest.approx(4.0, abs=0.01)
    assert far == pytest.approx(-2.0, abs=0.05)
    low, high = response_db(0.0, [EqFilter("LS", 105, 6.5, 0.7), EqFilter("HS", 10000, -3.1, 0.7)],
                            [20.0, 20000.0])
    assert low == pytest.approx(6.5, abs=0.3) and high == pytest.approx(-3.1, abs=0.3)
    cut, passed = response_db(0.0, [EqFilter("HP", 100, 0, 0.707)], [20.0, 2000.0])
    assert cut < -20 and abs(passed) < 0.1


def test_import_file(tmp_path):
    from core.listening import EqParseError, load_file

    path = tmp_path / "Mon casque ParametricEQ.txt"
    path.write_text(HD600, encoding="utf-8-sig")
    assert len(load_file(path).filters) == 10
    big = tmp_path / "gros.txt"
    big.write_bytes(b"x" * (300 * 1024))
    with pytest.raises(EqParseError):
        load_file(big)
    with pytest.raises(EqParseError):
        load_file(tmp_path / "absent.txt")


# --------------------------------------------------------------------------- #
# Base AutoEQ
# --------------------------------------------------------------------------- #
def test_index_and_urls():
    from core.listening import parse_index

    entries = parse_index(INDEX)
    assert len(entries) == 6                      # le chemin « ../ » est écarté
    aero = next(e for e in entries if e.name == "1MORE Aero (ANC Off)")
    assert aero.source == "HypetheSonics" and aero.rig == "GRAS RA0045"
    assert aero.kind == "intra"
    assert aero.url == ("https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/"
                        "HypetheSonics/GRAS%20RA0045%20in-ear/1MORE%20Aero%20%28ANC%20Off%29/"
                        "1MORE%20Aero%20%28ANC%20Off%29%20ParametricEQ.txt")
    quad = next(e for e in entries if e.name == "1MORE Quad Driver")
    assert "Bruel%20%26%20Kjaer%205128%20in-ear" in quad.url
    worn = next(e for e in entries if "worn" in e.name)
    assert worn.path.endswith("(worn%20earpads)") and worn.kind == "casque"


def test_search_ranking():
    from core.listening import parse_index, search

    entries = parse_index(INDEX)
    found = search(entries, "hd 600")
    assert [(e.name, e.source) for e in found[:2]] == [("Sennheiser HD 600", "oratory1990"),
                                                        ("Sennheiser HD 600", "crinacle")]
    assert found[2].name == "Sennheiser HD 600 (worn earpads)"
    assert [e.name for e in search(entries, "HD600")][:1] == ["Sennheiser HD 600"]
    assert search(entries, "6xx")[0].name == "Sennheiser HD 6XX"
    assert search(entries, "airpods") == []
    lookalike = parse_index("- [Denon AH-D600](./x/over-ear/Denon%20AH-D600) by Rtings\n")
    assert [e.name for e in search(lookalike + entries, "hd 600")][-1] == "Denon AH-D600"
    assert search(entries, "   ") == []


def test_index_cache_and_offline(appdata):
    from core import listening

    calls = []

    def fetch(url, limit):
        calls.append(url)
        return INDEX.encode()

    assert len(listening.load_index(fetch=fetch)) == 6
    assert len(listening.load_index(fetch=fetch)) == 6
    assert len(calls) == 1                         # copie locale réutilisée

    def offline(url, limit):
        raise OSError("hors ligne")

    # Copie locale trop vieille et réseau coupé : l'ancienne copie sert encore
    assert len(listening.load_index(refresh=True, fetch=offline)) == 6
    (listening.cache_dir() / listening.INDEX_FILE).unlink()
    with pytest.raises(OSError):
        listening.load_index(fetch=offline)


def test_library_signals(qapp):
    from core import listening

    def fetch(url, limit):
        if url.endswith("INDEX.md"):
            return INDEX.encode()
        if "HD%20600/" in url:
            return HD600.encode()
        raise OSError("404")

    library = listening.AutoEqLibrary(fetch=fetch)
    got, failures = [], []
    library.index_ready.connect(got.append)
    library.profile_ready.connect(lambda entry, eq: got.append((entry.name, len(eq.filters))))
    library.failed.connect(failures.append)
    library.request_index()
    assert _wait(qapp, lambda: got)
    entries = got[0]
    hd600 = listening.search(entries, "hd 600")[0]
    library.request_profile(hd600)
    assert _wait(qapp, lambda: len(got) == 2)
    assert got[1] == ("Sennheiser HD 600", 10)
    library.request_profile(listening.search(entries, "6xx")[0])
    assert _wait(qapp, lambda: failures)
    assert "Sennheiser HD 6XX" in failures[0]


# --------------------------------------------------------------------------- #
# Réglages
# --------------------------------------------------------------------------- #
def test_settings_are_checked(cfg):
    from core import listening

    cfg["listen"] = {"eq": {"enabled": True, "preamp": "beaucoup", "filters": [
        {"type": "PK", "freq": 1000, "gain": 3, "q": 1}, {"type": "??"}, "texte",
        {"type": "PK", "freq": float("nan")}]},
        "surround": {"enabled": 1, "head": "énorme", "room": "grotte", "amount": 7}}
    current = listening.settings(cfg)
    assert len(current["eq"]["filters"]) == 1 and current["eq"]["preamp"] == 0.0
    assert current["surround"] == {"enabled": True, "head": "medium", "room": "living",
                                   "amount": 1.0}
    cfg["listen"]["eq"]["filters"] = []
    assert listening.settings(cfg)["eq"]["enabled"] is False     # pas de profil, pas d'EQ


def test_host_message_follows_headphone_bus(cfg):
    from core import listening

    message = listening.host_message(cfg)
    assert (message["eq"], message["surround"], message["channel"], message["channels"]) == \
        (False, False, 0, 8)
    cfg["voicemeeter"]["headphone_bus"] = "A2"
    listening.set_profile(cfg, "HD 600", "oratory1990", listening.parse_parametric_eq(HD600))
    listening.set_option(cfg, "surround", "enabled", True)
    listening.set_option(cfg, "surround", "room", "cinema")
    message = listening.host_message(cfg)
    assert message["channel"] == 8 and message["eq"] and message["surround"]
    assert message["room"] == 2 and message["head"] == 1
    assert message["bands"][0] == {"type": "LS", "freq": 105.0, "gain": 6.5, "q": 0.7}
    assert "HD 600" in listening.describe(cfg) and "cinéma" in listening.describe(cfg)
    cfg["voicemeeter"]["headphone_bus"] = "A5"      # absent sur Banana : A1
    cfg["voicemeeter"]["kind"] = "banana"
    assert listening.headphone_bus(cfg) == ("A1", 0)


def test_settings_survive_a_backup(cfg, tmp_path):
    from core import backup, listening

    listening.set_profile(cfg, "HD 600", "oratory1990", listening.parse_parametric_eq(HD600))
    listening.set_option(cfg, "surround", "enabled", True)
    target = tmp_path / "config.reka"
    backup.export_backup(cfg, target)
    from core import config

    restored = backup.merge_checked(config.default_config(), backup.read_backup(target).config)
    assert listening.settings(restored)["eq"]["name"] == "HD 600"
    assert len(listening.settings(restored)["eq"]["filters"]) == 10
    assert listening.settings(restored)["surround"]["enabled"] is True


# --------------------------------------------------------------------------- #
# Moteur
# --------------------------------------------------------------------------- #
class Launcher:
    def __init__(self):
        self.engines = []

    def __call__(self, port, token):
        from core.reka_engine_fake import FakeEngine

        engine = FakeEngine(port, token)
        self.engines.append(engine)
        engine.start()
        return engine


def test_listen_only_host_follows_the_settings(qapp, cfg):
    """Moteur REAPER (ou autre) : un hôte est lancé pour l'écoute seule, et
    arrêté quand plus rien n'est allumé."""
    from core import listening
    from core.demo import DemoReaper

    launcher = Launcher()
    controller = listening.ListenController(cfg, DemoReaper(cfg), launcher=launcher)
    statuses = []
    controller.status_changed.connect(statuses.append)
    controller.start()
    assert launcher.engines == []                  # rien d'allumé : pas d'hôte
    assert controller.status().text == "Désactivée."
    try:
        listening.set_option(cfg, "surround", "enabled", True)
        controller.apply()
        assert _wait(qapp, lambda: controller.status().ok)
        engine = launcher.engines[0]
        assert engine.strips == []                 # aucune strip, aucune chaîne d'effets
        assert engine.listen["surround"] is True and engine.listen["eq"] is False
        assert "Active sur le bus A1" in controller.status().text
        assert not any(m.get("cmd") in ("load", "get_chains") for m in engine.received)
        # Réglage modifié : envoyé au même hôte
        listening.set_option(cfg, "surround", "amount", 0.4)
        controller.apply()
        assert _wait(qapp, lambda: engine.listen.get("amount") == 0.4)
        assert len(launcher.engines) == 1
        # Tout éteint : l'hôte s'arrête
        listening.set_option(cfg, "surround", "enabled", False)
        controller.apply()
        assert _wait(qapp, lambda: engine.poll() is not None)
        assert controller.host is None
        assert statuses and statuses[-1].active is False
    finally:
        controller.close()
    from core import reka_host
    assert not reka_host.chains_path().exists()    # les chaînes d'effets ne sont pas touchées


def test_listen_settings_survive_a_host_restart(qapp, cfg):
    from core import listening
    from core.demo import DemoReaper

    launcher = Launcher()
    listening.set_profile(cfg, "HD 600", "oratory1990", listening.parse_parametric_eq(HD600))
    controller = listening.ListenController(cfg, DemoReaper(cfg), launcher=launcher)
    controller.start()
    try:
        assert _wait(qapp, lambda: launcher.engines and launcher.engines[0].listen)
        launcher.engines[0].crash()
        assert _wait(qapp, lambda: len(launcher.engines) == 2 and launcher.engines[1].listen, 8)
        assert launcher.engines[1].listen["eq"] is True
        assert len(launcher.engines[1].listen["bands"]) == 10
    finally:
        controller.close()


def test_reka_engine_shares_its_host(qapp, cfg, tmp_path):
    """Moteur d'effets REKA : l'écoute passe par le même hôte que les effets."""
    from core import listening
    from core.reka_host import RekaHostController

    launcher = Launcher()
    cfg["effects"]["host"] = "reka"
    host = RekaHostController(cfg, launcher=launcher, chains_file=tmp_path / "chains.json")
    listening.set_option(cfg, "surround", "enabled", True)
    controller = listening.ListenController(cfg, host)
    controller.start()
    host.start()
    try:
        assert _wait(qapp, lambda: host.is_connected and launcher.engines[0].listen)
        engine = launcher.engines[0]
        assert len(engine.strips) == 6 and engine.listen["surround"] is True
        assert controller.host is host and controller.status().ok
        listening.set_option(cfg, "surround", "enabled", False)
        controller.apply()
        assert _wait(qapp, lambda: engine.listen["surround"] is False)
        assert len(launcher.engines) == 1          # pas de second hôte
        assert host.is_connected                   # les effets continuent
    finally:
        controller.close()
        host.close()


def test_missing_engine_is_reported(qapp, cfg, monkeypatch):
    from core import listening, reka_host
    from core.demo import DemoReaper

    monkeypatch.setattr(reka_host, "engine_executable", lambda _cfg: None)
    listening.set_option(cfg, "surround", "enabled", True)
    controller = listening.ListenController(cfg, DemoReaper(cfg))
    controller.start()
    try:
        assert _wait(qapp, lambda: "introuvable" in controller.status().text)
        assert controller.status().ok is False
    finally:
        controller.close()


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
def test_listen_panel(qapp, cfg, tmp_path):
    from core import listening
    from ui.listen_panel import ListenPanel

    def fetch(url, limit):
        return INDEX.encode() if url.endswith("INDEX.md") else HD600.encode()

    applied = []

    class Controller:
        def __init__(self):
            self.status_changed = _Status()

        def apply(self):
            applied.append(listening.host_message(cfg))

        def status(self):
            return listening.ListenStatus(True, True, "Active.")

    panel = ListenPanel(cfg, Controller(), library=listening.AutoEqLibrary(fetch=fetch))
    assert not panel.eq_enabled.isEnabled()        # pas encore de profil
    assert panel.curve.points == []
    panel.search_edit.setText("hd 600")
    panel.search()
    assert _wait(qapp, lambda: panel.results.count() == 3)
    assert "oratory1990" in panel.results.item(0).text()
    panel.use_selected()
    assert _wait(qapp, lambda: listening.settings(cfg)["eq"]["enabled"])
    assert listening.settings(cfg)["eq"]["name"] == "Sennheiser HD 600"
    assert panel.eq_enabled.isChecked() and panel.eq_enabled.isEnabled()
    assert max(panel.curve.points) < 0.5           # préampli : jamais de saturation
    assert _wait(qapp, lambda: applied and applied[-1]["eq"])
    # Recherche au fil de la frappe une fois la base chargée
    panel.search_edit.setText("6xx")
    assert panel.results.count() == 1
    # Surround
    panel.surround_enabled.setChecked(True)
    panel.room.setCurrentIndex(panel.room.findData("cinema"))
    panel.amount.setValue(55)
    panel.flush()
    assert applied[-1]["surround"] and applied[-1]["room"] == 2 and applied[-1]["amount"] == 0.55
    # Import et retrait
    bad = tmp_path / "rien.txt"
    bad.write_text("rien", encoding="utf-8")
    assert panel.import_file(bad) is False
    assert panel.search_info.objectName() == "warning"
    good = tmp_path / "Casque X ParametricEQ.txt"
    good.write_text("Filter 1: ON PK Fc 1000 Hz Gain 3 dB Q 1\n", encoding="utf-8")
    assert panel.import_file(good)
    assert listening.settings(cfg)["eq"]["name"] == "Casque X"
    assert listening.settings(cfg)["eq"]["preamp"] == pytest.approx(-3.0, abs=0.1)
    panel.clear_profile()
    assert listening.settings(cfg)["eq"]["filters"] == []
    assert not panel.eq_enabled.isEnabled()
    panel.flush()
    panel.disconnect_all()


class _Status:
    """Signal minimal (connect / disconnect) pour un faux contrôleur."""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)


def test_master_pills_and_settings_tab(qapp, mixer, monkeypatch):
    from core import listening
    from ui.settings_dialog import SettingsDialog

    window, _vm, _reaper = mixer
    cfg = window._cfg
    pills = window.master.listen_buttons
    assert set(pills) == {"eq", "surround"}
    assert not pills["eq"].isChecked() and not pills["surround"].isChecked()
    # 3D : allumé d'un clic
    pills["surround"].click()
    pump(qapp, 0.05)
    assert listening.settings(cfg)["surround"]["enabled"] is True
    assert pills["surround"].isChecked()
    assert window.toast.text() == "Son surround virtuel allumé."
    # EQ sans casque choisi : les réglages s'ouvrent sur l'onglet Écoute
    opened = []
    monkeypatch.setattr(window, "open_settings", lambda tab=None: opened.append(tab))
    pills["eq"].click()
    pump(qapp, 0.05)
    assert opened == ["Écoute"] and not pills["eq"].isChecked()
    # Avec un profil : l'EQ s'allume et s'éteint
    listening.set_profile(cfg, "HD 600", "oratory1990", listening.parse_parametric_eq(HD600),
                          enable=False)
    window.show_listen()
    pills["eq"].click()
    pump(qapp, 0.05)
    assert listening.settings(cfg)["eq"]["enabled"] is True and pills["eq"].isChecked()
    assert window.toast.text() == "Égalisation du casque allumée."
    assert "HD 600" in pills["eq"].toolTip()
    # Onglet des réglages
    dialog = SettingsDialog(cfg, None, window._reaper, window._vm, window, persist=False)
    titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert "Écoute" in titles
    dialog.show_tab("Écoute")
    assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "Écoute"
    assert dialog.listen_panel.eq_enabled.isChecked()
    assert dialog.listen_panel.surround_enabled.isChecked()
    dialog.reject()


# --------------------------------------------------------------------------- #
# Traitement du son (C++), compilé pour la machine de test
# --------------------------------------------------------------------------- #
COMPILER = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")


@pytest.mark.skipif(COMPILER is None, reason="pas de compilateur C++")
def test_native_listen_processing(tmp_path):
    exe = tmp_path / "listen_test"
    build = subprocess.run([COMPILER, "-std=c++20", "-O2", "-I", str(NATIVE / "src"),
                            str(NATIVE / "test" / "listen_test.cpp"),
                            str(NATIVE / "src" / "listen.cpp"), "-o", str(exe)],
                           capture_output=True, text=True, timeout=180)
    if build.returncode != 0 and "c++20" in build.stderr:
        pytest.skip("compilateur sans C++20")
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "ok"
