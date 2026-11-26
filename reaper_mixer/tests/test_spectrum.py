"""
Analyseur de spectre des strips : FFT de l'hôte (native/reka-host/src/spectrum.h,
compilée ici s'il y a un compilateur), protocole avec le moteur REKA (faux hôte
Python), affichage dans chaque strip, démo et réglage.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import pump

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native" / "reka-host"
COMPILER = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")


def _wait(app, predicate, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.mark.skipif(COMPILER is None, reason="pas de compilateur C++")
def test_native_spectrum_analyzer(tmp_path):
    exe = tmp_path / "spectrum_test"
    build = subprocess.run([COMPILER, "-std=c++20", "-O2", "-I", str(NATIVE / "src"),
                            str(NATIVE / "test" / "spectrum_test.cpp"), "-o", str(exe)],
                           capture_output=True, text=True, timeout=180)
    if build.returncode != 0 and "c++20" in build.stderr:
        pytest.skip("compilateur sans C++20")
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "ok"


def test_parse_spectrum_and_bands():
    from core.effects import (SPECTRUM_BANDS, SPECTRUM_FLOOR_DB, parse_spectrum,
                              spectrum_band_hz)

    bands = parse_spectrum([[-10, "x", 5, -200], None, "bof"], 4)
    assert bands[0] == [-10.0, SPECTRUM_FLOOR_DB, 0.0, SPECTRUM_FLOOR_DB]   # bornés
    assert bands[1:] == [None, None, None]                  # strip muette, entrée invalide, absente
    assert parse_spectrum("rien", 2) == [None, None]
    assert parse_spectrum([[0] * 500], 1)[0] == [0.0] * 64  # taille bornée
    assert 30 < spectrum_band_hz(0) < 40 and 13000 < spectrum_band_hz(SPECTRUM_BANDS - 1) < 16000


def test_demo_spectrum_moves_and_keeps_a_silent_strip():
    from core.demo import demo_spectrum
    from core.effects import SPECTRUM_BANDS, SPECTRUM_FLOOR_DB

    a, b = demo_spectrum(1.0, 6), demo_spectrum(1.4, 6)
    assert len(a) == 6 and a[5] is None
    assert all(len(x) == SPECTRUM_BANDS for x in a[:5])
    assert all(SPECTRUM_FLOOR_DB <= v <= 0 for x in a[:5] for v in x)
    assert a[0] != b[0]                                     # ça bouge


def test_view_falls_slowly_and_holds_peaks(qapp):
    from core.effects import SPECTRUM_BANDS, SPECTRUM_FLOOR_DB
    from ui.spectrum import SpectrumView

    view = SpectrumView()
    view.resize(120, 46)
    view.set_bands([-6.0] * SPECTRUM_BANDS)
    assert view.bands()[0] == -6.0 and view.caps()[0] == -6.0
    view._t -= 0.1                                          # 100 ms plus tard : du silence
    view.set_bands(None)
    assert -12.0 < view.bands()[0] < -6.0                   # retombée progressive
    assert view.caps()[0] == -6.0                           # chapeau tenu sur la crête
    view.grab()
    view.reset()
    assert view.bands() == [SPECTRUM_FLOOR_DB] * SPECTRUM_BANDS


def test_reka_engine_sends_spectra_only_when_asked(qapp, cfg, tmp_path):
    from core.reka_host import RekaHostController
    from test_reka_host import Launcher

    cfg["effects"]["host"] = "reka"
    launcher = Launcher()
    host = RekaHostController(cfg, launcher=launcher, chains_file=tmp_path / "chains.json")
    received = []
    host.spectrum_changed.connect(received.append)
    try:
        assert host.has_spectrum
        host.start()
        assert _wait(qapp, lambda: host.is_connected)
        pump(qapp, 0.2)
        assert received == []                               # rien sans demande
        host.set_spectrum(True)
        assert _wait(qapp, lambda: len(received) >= 2)
        bands = received[-1]
        assert len(bands) == len(host.strip_tracks) and bands[5] is None
        assert len(bands[0]) == 24
        assert {"cmd": "spectrum", "on": True}.items() <= next(
            m for m in launcher.last.received if m.get("cmd") == "spectrum").items()

        # Relance du moteur : l'analyseur est redemandé tout seul
        launcher.last.crash()
        assert _wait(qapp, lambda: len(launcher.engines) == 2 and launcher.last.spectrum, 10)
        host.set_spectrum(False)
        assert _wait(qapp, lambda: not launcher.last.spectrum)
        count = len(received)
        pump(qapp, 0.3)
        assert len(received) <= count + 1                   # plus rien (au plus un en route)
    finally:
        host.close(save_project=False)


def test_listen_only_engine_has_no_spectrum(qapp, cfg):
    from core.reka_host import RekaHostController

    assert not RekaHostController(cfg, listen_only=True).has_spectrum


def test_spectrum_in_the_console(qapp, mixer):
    from core.effects import SPECTRUM_FLOOR_DB
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    strip = window.strips[0]
    assert not strip.spectrum_shown() and not reaper._spectrum_timer.isActive()
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    try:
        assert dialog.spectrum_box.isEnabled()              # la démo a un analyseur
        dialog.spectrum_box.setChecked(True)
        assert window._cfg["ui"]["spectrum"]
        assert all(s.spectrum_shown() and s.spectrum_section.isVisible() for s in window.strips)
        assert reaper._spectrum_timer.isActive()
        assert _wait(qapp, lambda: max(strip.spectrum.bands()) > -60)
        assert max(window.strips[5].spectrum.bands()) <= SPECTRUM_FLOOR_DB   # strip muette
        window.grab()

        window.hide()                                       # fenêtre cachée : plus d'analyse
        assert not reaper._spectrum_timer.isActive()
        window.show()
        assert reaper._spectrum_timer.isActive()

        # À l'horizontale aussi, à côté du fader
        window._cfg["ui"]["orientation"] = "horizontal"
        window.apply_appearance()
        pump(qapp, 0.05)
        assert strip.spectrum_section.isVisible()
        assert strip.spectrum_section.x() < strip.fader_section.x()
        window.grab()

        dialog.spectrum_box.setChecked(False)
        assert not strip.spectrum_shown() and not strip.spectrum_section.isVisible()
        assert not reaper._spectrum_timer.isActive()
    finally:
        window._cfg["ui"].pop("orientation", None)
        window._cfg["ui"].pop("spectrum", None)
        window.apply_appearance()
        dialog.done(0)


def test_no_spectrum_option_without_reka_engine(qapp, mixer, monkeypatch):
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    monkeypatch.setattr(type(reaper), "has_spectrum", False)
    window._cfg["ui"]["spectrum"] = True
    try:
        window.apply_appearance()
        assert not window.strips[0].spectrum_shown()        # réglage gardé, mais sans effet
        dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
        assert not dialog.spectrum_box.isEnabled()
        dialog.done(0)
    finally:
        window._cfg["ui"].pop("spectrum", None)
        window.apply_appearance()
